"""End-to-end harvest: discover -> fetch pages -> build records -> load."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import db
from .config import Config
from .geometry import transformation_info
from .gml import parse_feature_collection, srid_from_srs_name
from .records import BuildResult, Issue, build_records
from .wfs import Capabilities, FeatureSchema, Transport, WfsClient, WfsError

log = logging.getLogger(__name__)


@dataclass
class Discovery:
    capabilities: Capabilities
    schema: FeatureSchema | None
    request_crs: str
    expected_count: int | None
    mapping_report: dict[str, str]
    issues: list[Issue] = field(default_factory=list)


def discover(client: WfsClient, cfg: Config) -> Discovery:
    caps = client.capabilities()
    ft = caps.find_feature_type(cfg.source.type_name)
    if ft is None:
        raise WfsError(
            f"Layer {cfg.source.type_name!r} is not advertised; available: "
            f"{sorted(caps.feature_types)}")
    issues: list[Issue] = []
    request_crs = cfg.source.request_crs or ft.default_crs
    if not request_crs:
        raise WfsError(f"No CRS configured and none advertised for {ft.name}")
    advertised = {c for c in [ft.default_crs, *ft.other_crs] if c}
    if cfg.source.request_crs and cfg.source.request_crs not in advertised:
        issues.append(Issue("fetch", "warning", "request_crs_not_advertised",
                            message=f"{request_crs} not in {sorted(advertised)}"))

    schema = None
    try:
        schema = client.describe(ft.name)
    except WfsError as exc:
        issues.append(Issue("fetch", "warning", "describe_feature_type_failed", message=str(exc)))

    mapping_report: dict[str, str] = {}
    for target, source_name in cfg.mapping.fields.items():
        if schema is None:
            mapping_report[target] = f"{source_name} (unverified: no schema)"
        elif source_name in schema.attributes:
            mapping_report[target] = f"{source_name} ({schema.attributes[source_name]})"
        else:
            mapping_report[target] = f"{source_name} (ABSENT from schema)"
            issues.append(Issue("fetch", "warning", "mapped_attribute_absent",
                                message=f"{target} -> {source_name} not in DescribeFeatureType"))

    expected = None
    try:
        expected = client.hits(ft.name)
    except WfsError as exc:
        issues.append(Issue("fetch", "warning", "hits_request_failed", message=str(exc)))

    return Discovery(caps, schema, request_crs, expected, mapping_report, issues)


@dataclass
class RunResult:
    run_id: str | None
    mode: str
    started_at: str
    finished_at: str
    status: str
    counts: dict[str, Any]
    discovery: dict[str, Any]
    issues: list[Issue]
    build: BuildResult | None
    requests: list[dict[str, Any]]
    transformation: dict[str, Any] | None
    error: str | None = None


def run_harvest(cfg: Config, transport: Transport, *, mode: str,
                load: bool = True, conn=None) -> RunResult:
    started = datetime.now(timezone.utc)
    client = WfsClient(transport, cfg.source.version)
    prov = db.Provenance(cfg.source.name, cfg.source.endpoint, cfg.source.type_name,
                         cfg.source.licence)
    run_id = None
    if load:
        db.apply_schema(conn, cfg.database.schema)
        run_id = db.start_run(conn, cfg.database.schema, prov, cfg.state_code, mode)

    issues: list[Issue] = []
    discovery_info: dict[str, Any] = {}
    build: BuildResult | None = None
    transformation = None
    counts: dict[str, Any] = {}
    try:
        disc = discover(client, cfg)
        issues.extend(disc.issues)
        caps = disc.capabilities
        discovery_info = {
            "service_title": caps.title, "provider": caps.provider,
            "fees": caps.fees, "access_constraints": caps.access_constraints,
            "wfs_version": caps.version, "implements_result_paging": caps.implements_paging,
            "count_default": caps.count_default, "layer": cfg.source.type_name,
            "request_crs": disc.request_crs, "number_matched": disc.expected_count,
            "schema_attributes": disc.schema.attributes if disc.schema else None,
            "geometry_property": disc.schema.geometry_property if disc.schema else None,
            "attribute_mapping": disc.mapping_report,
        }

        features = []
        pages = 0
        for page in client.pages(
            cfg.source.type_name, disc.request_crs,
            page_size=cfg.source.page_size, max_features=cfg.source.max_features,
            paging=caps.implements_paging, expected=disc.expected_count,
        ):
            pages += 1
            features.extend(parse_feature_collection(page.content, disc.request_crs))
        if not caps.implements_paging and caps.count_default and len(features) >= caps.count_default:
            issues.append(Issue("fetch", "warning", "possible_truncation",
                                message=f"No paging and {len(features)} >= CountDefault"))
        if disc.expected_count is not None and len(features) != disc.expected_count:
            issues.append(Issue("fetch", "warning", "count_mismatch",
                                message=f"numberMatched={disc.expected_count}, "
                                        f"received={len(features)}"))

        build = build_records(features, cfg)
        issues.extend(build.issues)
        counts = {"pages": pages, "number_matched": disc.expected_count, **build.counts}
        transformation = transformation_info(srid_from_srs_name(disc.request_crs),
                                             cfg.quality.state_bbox_wgs84)
        if transformation["better_operations_unavailable"]:
            issues.append(Issue(
                "geometry", "warning", "datum_grid_unavailable",
                message=f"Using '{transformation['operation']}' "
                        f"(~{transformation['accuracy_m']} m); install the PROJ grid "
                        "for a more accurate DHDN->ETRS89 transformation",
                details=transformation))

        if load:
            load_counts = db.load_sites(conn, cfg.database.schema, run_id, cfg.state_code,
                                        build.records, issues, prov)
            counts |= load_counts.as_dict()
            db.finish_run(conn, cfg.database.schema, run_id, "succeeded", counts)
        status, error = "succeeded", None
    except Exception as exc:  # recorded on the run, then re-raised by the CLI
        log.exception("Harvest failed")
        status, error = "failed", f"{type(exc).__name__}: {exc}"
        issues.append(Issue("run", "error", "run_failed", message=error))
        if load and run_id is not None:
            # load_sites' transaction has already rolled back; record the
            # failure in a fresh one.
            with conn.transaction():
                db.log_issues(conn, cfg.database.schema, run_id, issues)
            db.finish_run(conn, cfg.database.schema, run_id, "failed", counts or None, error)

    return RunResult(
        run_id=str(run_id) if run_id else None,
        mode=mode,
        started_at=started.isoformat(timespec="seconds"),
        finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        status=status,
        counts=counts,
        discovery=discovery_info,
        issues=issues,
        build=build,
        requests=[vars(r) for r in transport.requests_made],
        transformation=transformation,
        error=error,
    )
