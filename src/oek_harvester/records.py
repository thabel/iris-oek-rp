"""Turn parsed WFS features into staging records plus quality issues."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

import shapely

from .config import Config
from .geometry import process_geometry
from .gml import GmlError, RawFeature, srid_from_srs_name
from .normalize import (
    clean_text,
    name_key,
    normalize_code,
    normalize_status,
    parse_decimal,
)


@dataclass
class Issue:
    stage: str  # fetch | parse | identity | geometry | attributes | load
    severity: str  # reject | warning | error
    code: str
    source_feature_id: str | None = None
    site_id: str | None = None
    message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SiteRecord:
    state_code: str
    site_id: str
    source_feature_id: str
    gml_id: str | None
    source_srid: int
    site_name: str | None
    municipality_name: str | None
    municipality_key: str | None
    municipality_code: str | None
    cadastral_district_name: str | None
    cadastral_district_key: str | None
    cadastral_district_code: str | None
    cadastral_parcel_refs: str | None
    compensation_status: str
    compensation_status_raw: str | None
    area_m2: float
    source_area_raw: str | None
    source_area_m2: float | None
    credits_total: float | None
    credits_debited: float | None
    credits_available: float | None
    credit_unit: str | None
    geometry_outcome: str
    quality_flags: list[str]
    source_attributes: dict[str, str | None]
    geom_wkb: bytes
    geom_source_wkb: bytes
    content_hash: str = ""

    def compute_hash(self) -> str:
        payload = {k: v for k, v in asdict(self).items()
                   if k not in ("content_hash", "geom_wkb", "geom_source_wkb")}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode())
        digest.update(self.geom_wkb)
        digest.update(self.geom_source_wkb)
        return digest.hexdigest()


def _get(feature: RawFeature, cfg: Config, target: str) -> str | None:
    source_name = cfg.mapping.fields.get(target)
    if not source_name:
        return None
    return feature.properties.get(source_name)


def source_identifier(feature: RawFeature, cfg: Config) -> str | None:
    if "source_id" in cfg.mapping.fields:
        return clean_text(_get(feature, cfg, "source_id"))
    return clean_text(feature.feature_id)


def build_record(feature: RawFeature, cfg: Config) -> tuple[SiteRecord | None, list[Issue]]:
    issues: list[Issue] = []
    source_id = source_identifier(feature, cfg)
    fid_for_log = source_id or feature.feature_id

    def reject(stage: str, code: str, message: str | None = None, **details) -> tuple[None, list[Issue]]:
        issues.append(Issue(stage, "reject", code, fid_for_log, None, message,
                            details | {"properties": feature.properties}))
        return None, issues

    if not source_id:
        return reject("identity", "missing_source_id",
                      "No stable source identifier; a key derived from geometry or "
                      "attributes would not survive source edits, so the feature is "
                      "not loaded")
    site_id = f"{cfg.site_id_prefix}{source_id}"

    if feature.geometry_error:
        return reject("parse", "geometry_parse_error", feature.geometry_error)
    if feature.srs_name is None:
        return reject("parse", "missing_srs", "Geometry has no srsName")
    try:
        source_srid = srid_from_srs_name(feature.srs_name)
    except (GmlError, Exception) as exc:  # pyproj raises CRSError
        return reject("parse", "unknown_srs", str(exc))

    q = cfg.quality
    geo = process_geometry(
        feature.geometry, source_srid,
        max_repair_area_change=q.max_repair_area_change,
        min_area_m2=q.min_area_m2,
        state_bbox_wgs84=q.state_bbox_wgs84,
    )
    if geo.outcome == "rejected":
        return reject("geometry", geo.reject_code or "rejected_geometry", geo.detail,
                      flags=geo.flags)
    flags = list(geo.flags)
    if feature.had_z and "dropped_z" not in flags:
        flags.append("dropped_z")

    municipality_code = normalize_code(_get(feature, cfg, "municipality_code"))
    prefix = q.municipality_code_prefix
    if municipality_code and prefix and not municipality_code.startswith(prefix):
        return reject("attributes", "municipality_code_outside_state",
                      f"Municipality key {municipality_code} does not start with {prefix}")

    status, status_issue = normalize_status(_get(feature, cfg, "compensation_status"),
                                            cfg.mapping.status_values)
    if status_issue:
        flags.append(status_issue)
        issues.append(Issue("attributes", "warning", status_issue, source_id, site_id,
                            details={"value": _get(feature, cfg, "compensation_status")}))

    municipality_name = clean_text(_get(feature, cfg, "municipality_name"))
    if "municipality_name" in cfg.mapping.fields and municipality_name is None:
        flags.append("missing_municipality")
        issues.append(Issue("attributes", "warning", "missing_municipality", source_id, site_id))

    def number(target: str) -> float | None:
        raw = _get(feature, cfg, target)
        value, problem = parse_decimal(raw, cfg.mapping.decimal_style)
        if problem:
            flags.append(f"{problem}:{target}")
            issues.append(Issue("attributes", "warning", problem, source_id, site_id,
                                details={"field": target, "value": raw}))
        return value

    source_area_raw = clean_text(_get(feature, cfg, "source_area"))
    source_area_m2 = None
    if source_area_raw is not None and cfg.mapping.source_area_unit:
        value = number("source_area")
        if value is not None:
            source_area_m2 = value * (10_000 if cfg.mapping.source_area_unit == "ha" else 1)
            assert geo.area_m2 is not None
            rel = abs(source_area_m2 - geo.area_m2) / geo.area_m2
            if rel > q.area_mismatch_tolerance:
                flags.append("area_mismatch")
                issues.append(Issue("attributes", "warning", "area_mismatch", source_id, site_id,
                                    details={"source_area_m2": source_area_m2,
                                             "computed_area_m2": round(geo.area_m2, 2),
                                             "relative_difference": round(rel, 4)}))

    for target in ("credits_total", "credits_debited", "credits_available"):
        if target in cfg.mapping.fields and _get(feature, cfg, target) is None:
            flags.append(f"missing_{target}")

    record = SiteRecord(
        state_code=cfg.state_code,
        site_id=site_id,
        source_feature_id=source_id,
        gml_id=feature.feature_id,
        source_srid=source_srid,
        site_name=clean_text(_get(feature, cfg, "site_name")),
        municipality_name=municipality_name,
        municipality_key=name_key(municipality_name),
        municipality_code=municipality_code,
        cadastral_district_name=clean_text(_get(feature, cfg, "cadastral_district_name")),
        cadastral_district_key=name_key(_get(feature, cfg, "cadastral_district_name")),
        cadastral_district_code=normalize_code(_get(feature, cfg, "cadastral_district_code")),
        cadastral_parcel_refs=clean_text(_get(feature, cfg, "cadastral_parcel_refs")),
        compensation_status=status,
        compensation_status_raw=clean_text(_get(feature, cfg, "compensation_status")),
        area_m2=round(geo.area_m2 or 0.0, 2),
        source_area_raw=source_area_raw,
        source_area_m2=source_area_m2,
        credits_total=number("credits_total"),
        credits_debited=number("credits_debited"),
        credits_available=number("credits_available"),
        credit_unit=cfg.mapping.credit_unit,
        geometry_outcome=geo.outcome,
        quality_flags=sorted(set(flags)),
        source_attributes=dict(feature.properties),
        # Round to millimetres so float noise from reprojection does not
        # register as a change between otherwise identical runs.
        geom_wkb=shapely.to_wkb(shapely.set_precision(geo.geom_target, 0.001), output_dimension=2),
        geom_source_wkb=shapely.to_wkb(geo.geom_source, output_dimension=2),
    )
    if geo.outcome == "repaired":
        issues.append(Issue("geometry", "warning", "geometry_repaired", source_id, site_id,
                            details={"flags": geo.flags}))
    record.content_hash = record.compute_hash()
    return record, issues


@dataclass
class BuildResult:
    records: list[SiteRecord]
    issues: list[Issue]
    counts: dict[str, int]


def build_records(features: list[RawFeature], cfg: Config) -> BuildResult:
    records: dict[str, SiteRecord] = {}
    issues: list[Issue] = []
    counts: dict[str, int] = {"features_received": len(features),
                              "duplicates_in_source": 0,
                              "accepted_valid": 0, "accepted_repaired": 0}
    for feature in features:
        record, feature_issues = build_record(feature, cfg)
        issues.extend(feature_issues)
        if record is None:
            code = next(i.code for i in feature_issues if i.severity == "reject")
            counts[f"rejected_{code}"] = counts.get(f"rejected_{code}", 0) + 1
            continue
        existing = records.get(record.site_id)
        if existing is not None:
            counts["duplicates_in_source"] += 1
            same = existing.content_hash == record.content_hash
            issues.append(Issue(
                "identity", "warning",
                "duplicate_in_source" if same else "conflicting_duplicate_in_source",
                record.source_feature_id, record.site_id,
                "Same identifier returned more than once (e.g. across pages); "
                "first occurrence kept"))
            continue
        records[record.site_id] = record
        counts[f"accepted_{record.geometry_outcome}"] += 1
    counts["accepted_total"] = counts["accepted_valid"] + counts["accepted_repaired"]
    counts["rejected_total"] = sum(v for k, v in counts.items() if k.startswith("rejected_")
                                   and k != "rejected_total")
    return BuildResult(list(records.values()), issues, counts)
