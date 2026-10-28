"""Run outputs: JSON run report, Markdown quality report, rejected-feature
log and a sample export of accepted sites (GeoPackage, EPSG:25832)."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import geopandas as gpd
import shapely

from .config import Config
from .pipeline import RunResult


def write_outputs(result: RunResult, cfg: Config, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "run_report": out_dir / "run_report.json",
        "quality_report": out_dir / "quality_report.md",
        "issues": out_dir / "issues.jsonl",
    }
    report = {
        "run_id": result.run_id, "mode": result.mode, "status": result.status,
        "started_at": result.started_at, "finished_at": result.finished_at,
        "error": result.error,
        "source": {"name": cfg.source.name, "endpoint": cfg.source.endpoint,
                   "layer": cfg.source.type_name, "licence": cfg.source.licence,
                   "provider": cfg.source.provider},
        "target": f"{cfg.database.schema}.compensation_sites",
        "state_code": cfg.state_code,
        "discovery": result.discovery,
        "transformation": result.transformation,
        "counts": result.counts,
        "issue_counts": dict(Counter(f"{i.severity}:{i.code}" for i in result.issues)),
        "requests": result.requests,
    }
    paths["run_report"].write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str),
                                   encoding="utf-8")
    with paths["issues"].open("w", encoding="utf-8") as fh:
        for issue in result.issues:
            fh.write(json.dumps(asdict(issue), ensure_ascii=False, default=str) + "\n")

    if result.build and result.build.records:
        paths["sample_export"] = out_dir / "sample_sites_25832.gpkg"
        rows = []
        for r in result.build.records:
            rows.append({
                "state_code": r.state_code, "site_id": r.site_id,
                "source_feature_id": r.source_feature_id, "site_name": r.site_name,
                "municipality_name": r.municipality_name, "municipality_code": r.municipality_code,
                "cadastral_district_name": r.cadastral_district_name,
                "compensation_status": r.compensation_status,
                "compensation_status_raw": r.compensation_status_raw,
                "area_m2": r.area_m2, "credits_total": r.credits_total,
                "credits_debited": r.credits_debited, "credits_available": r.credits_available,
                "geometry_outcome": r.geometry_outcome,
                "quality_flags": ",".join(r.quality_flags),
                "source_srid": r.source_srid,
                "geometry": shapely.from_wkb(r.geom_wkb),
            })
        gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:25832").to_file(
            paths["sample_export"], layer="compensation_sites", driver="GPKG")

    paths["quality_report"].write_text(render_quality_report(result, cfg), encoding="utf-8")
    return paths


def render_quality_report(result: RunResult, cfg: Config) -> str:
    c = result.counts
    d = result.discovery
    lines = [
        f"# Quality report — {cfg.source.type_name} ({cfg.state_code})",
        "",
        f"- Run: `{result.run_id or 'not loaded'}` · mode **{result.mode}** · status **{result.status}**",
        f"- Window: {result.started_at} → {result.finished_at}",
        f"- Source: {cfg.source.name} — `{cfg.source.endpoint}` (layer `{cfg.source.type_name}`)",
        f"- Licence: {cfg.source.licence}",
        f"- Request CRS: `{d.get('request_crs')}` → stored as `geom_source`; working geometry EPSG:25832",
    ]
    t = result.transformation
    if t:
        lines.append(f"- Transformation: {t['operation']} (accuracy ~{t['accuracy_m']} m)")
        if t["better_operations_unavailable"]:
            lines.append("  - More accurate operations not installed: "
                         + "; ".join(t["better_operations_unavailable"]))
    if result.error:
        lines += ["", f"**Run failed:** `{result.error}`"]
    lines += ["", "## Counts by processing outcome", "", "| Outcome | Count |", "|---|---:|"]
    for key in sorted(c):
        lines.append(f"| {key} | {c[key]} |")

    rejects = [i for i in result.issues if i.severity == "reject"]
    lines += ["", f"## Rejected features ({len(rejects)})", ""]
    if rejects:
        lines += ["| Source id | Code | Detail |", "|---|---|---|"]
        for i in rejects:
            lines.append(f"| {i.source_feature_id or '—'} | `{i.code}` | {i.message or ''} |")
    else:
        lines.append("None.")

    warnings = Counter(i.code for i in result.issues if i.severity == "warning")
    lines += ["", "## Warnings (accepted with flags)", ""]
    if warnings:
        lines += ["| Code | Count |", "|---|---:|"]
        lines += [f"| `{k}` | {v} |" for k, v in sorted(warnings.items())]
    else:
        lines.append("None.")

    lines += ["", "## Attribute mapping as verified against DescribeFeatureType", ""]
    mapping = d.get("attribute_mapping") or {}
    if mapping:
        lines += ["| Target column | Source attribute |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in mapping.items()]
    else:
        lines.append("Not available (discovery did not complete).")
    lines.append("")
    return "\n".join(lines)
