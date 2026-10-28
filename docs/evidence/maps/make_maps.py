"""Regenerate the two evidence maps in this folder.

Not part of the harvester: needs matplotlib in addition to the pinned
runtime set, plus network access to fetch the official state boundaries.

    python docs/evidence/maps/make_maps.py

Background: BKG VG250 state boundaries (vg250:vg250_lan) from the public
WFS https://sgx.geodatenzentrum.de/wfs_vg250, licence Datenlizenz
Deutschland - Namensnennung 2.0 ("© BKG (2026) dl-de/by-2-0"). Tile
basemaps are not used: CartoDB requires an API key and the OpenStreetMap
tile servers' usage policy does not cover this kind of client.

Inputs:
  output/live-control/sample_sites_25832.gpkg   (oek-harvester harvest --config config.live-control.toml --no-db)
  docs/evidence/example_run/sample_sites_25832.gpkg, issues.jsonl   (fixture run)
  tests/fixtures/make_fixtures.py   (positions of fixture features that were rejected)

All maps are drawn in EPSG:25832, the harvester's working CRS.
"""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import requests
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from shapely.geometry import Point, box

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
RP_BBOX = (6.05, 48.90, 8.55, 51.00)  # quality.state_bbox_wgs84
VG250_URL = ("https://sgx.geodatenzentrum.de/wfs_vg250?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"
             "&TYPENAMES=vg250:vg250_lan&OUTPUTFORMAT=application/json&COUNT=40")
CREDIT = "State boundaries: © BKG (2026) dl-de/by-2-0, VG250"


def states() -> gpd.GeoDataFrame:
    response = requests.get(VG250_URL, timeout=300,
                            headers={"User-Agent": "iris-oek-rp evidence maps"})
    response.raise_for_status()
    # The service returns EPSG:25832 coordinates without a CRS member.
    gdf = gpd.read_file(io.BytesIO(response.content)).set_crs(25832, allow_override=True)
    gdf = gdf.dissolve(by="gen", as_index=False)  # land + water parts per state
    gdf["geometry"] = gdf.geometry.simplify(100)
    return gdf


def draw_states(ax, gdf):
    others = gdf[gdf.gen != "Rheinland-Pfalz"]
    rlp = gdf[gdf.gen == "Rheinland-Pfalz"]
    others.plot(ax=ax, color="#f2f2f0", edgecolor="#b8b8b8", linewidth=0.6)
    rlp.plot(ax=ax, color="#fbf6e9", edgecolor="#6b5b3e", linewidth=1.4)
    for _, s in others.iterrows():
        p = s.geometry.representative_point()
        ax.text(p.x, p.y, s.gen, fontsize=8, color="#9a9a9a", ha="center", clip_on=True)


def state_box():
    return gpd.GeoSeries([box(*RP_BBOX)], crs=4326).to_crs(25832)


def live_control_map(gdf):
    zones = gpd.read_file(ROOT / "output/live-control/sample_sites_25832.gpkg")
    repaired = zones[zones.geometry_outcome == "repaired"]
    fig, ax = plt.subplots(figsize=(10, 11), dpi=110)
    draw_states(ax, gdf)
    zones.plot(ax=ax, color="#2b7bba", alpha=0.6, edgecolor="#1b4f78", linewidth=0.3)
    repaired.plot(ax=ax, color="#f28e2b", edgecolor="#b35806", linewidth=1.2)
    state_box().boundary.plot(ax=ax, color="#555", linestyle="--", linewidth=1)
    for _, row in repaired.iterrows():
        p = row.geometry.representative_point()
        ax.annotate(f"repaired: {row.site_name}", (p.x, p.y), xytext=(-230, -45),
                    textcoords="offset points", fontsize=9,
                    bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#b35806", alpha=0.9),
                    arrowprops=dict(arrowstyle="->", color="#b35806"), color="#7a3d00")
    xmin, ymin, xmax, ymax = state_box().total_bounds
    ax.set_xlim(xmin - 15_000, xmax + 15_000)
    ax.set_ylim(ymin - 15_000, ymax + 15_000)
    ax.set_axis_off()
    ax.set_title("Live control run — RLP water WFS, layer wsg:twsg_rvo (2026-10-28)\n"
                 f"{len(zones)} real polygons retrieved in 6 requests · "
                 f"{(zones.geometry_outcome == 'valid').sum()} valid · {len(repaired)} repaired · 0 rejected",
                 fontsize=11, loc="left")
    ax.legend(handles=[
        Patch(color="#2b7bba", alpha=0.6, label="drinking-water protection zone (valid)"),
        Patch(color="#f28e2b", label="repaired geometry"),
        Patch(facecolor="#fbf6e9", edgecolor="#6b5b3e", label="Rhineland-Palatinate (VG250)"),
        Line2D([], [], ls="--", color="#555", label="bounding box used by the state check"),
    ], loc="lower left", fontsize=8, framealpha=0.95)
    fig.text(0.01, 0.005, "Not compensation data: used only to validate the live WFS path. Requested in "
             f"EPSG:31466, reprojected to EPSG:25832 by the harvester. {CREDIT}",
             fontsize=7.5, color="#444")
    fig.tight_layout()
    fig.savefig(HERE / "live_control_zones.png")
    plt.close(fig)


def fixture_outcomes_map(gdf):
    spec = importlib.util.spec_from_file_location("mf", ROOT / "tests/fixtures/make_fixtures.py")
    mf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mf)

    accepted = gpd.read_file(ROOT / "docs/evidence/example_run/sample_sites_25832.gpkg")
    status = {r.source_feature_id: r.geometry_outcome for r in accepted.itertuples()}
    rejects = {}
    for line in (ROOT / "docs/evidence/example_run/issues.jsonl").read_text("utf-8").splitlines():
        issue = json.loads(line)
        if issue["severity"] == "reject":
            rejects[issue["source_feature_id"]] = issue["code"]

    rows, seen = [], set()
    for n, (oek_id, lon, lat, kind, _rings, _attrs) in enumerate(mf.FEATURES):
        key = oek_id or f"oek_f.noid{n}"
        if key in seen:
            outcome, label = "duplicate", f"{oek_id} duplicate (dropped)"
        elif key in status:
            outcome, label = status[key], f"{oek_id} {status[key]}"
        else:
            outcome, label = "rejected", f"{oek_id or '(no id)'} rejected: {rejects.get(key, '?')}"
        seen.add(key)
        rows.append({"label": label, "outcome": outcome, "has_geometry": kind != "none",
                     "geometry": Point(lon, lat)})
    pts = gpd.GeoDataFrame(rows, crs=4326).to_crs(25832)

    style = {"valid": ("o", "#2ca02c"), "repaired": ("o", "#1f77b4"),
             "rejected": ("X", "#d62728"), "duplicate": ("o", "#7f7f7f")}
    # Label placement (points offset, horizontal alignment) where defaults collide.
    placement = {"1004": ((-8, -14), "right"), "1005": ((-8, 4), "right"),
                 "1002 duplicate (dropped)": ((7, -11), "left")}
    fig, ax = plt.subplots(figsize=(13, 7.2), dpi=110)
    draw_states(ax, gdf)
    state_box().boundary.plot(ax=ax, color="#555", linestyle="--", linewidth=1)
    for _, r in pts.iterrows():
        marker, color = style[r.outcome]
        ax.scatter(r.geometry.x, r.geometry.y, marker=marker, s=80,
                   c=color if r.has_geometry else "white", edgecolors=color, linewidths=1.5, zorder=3)
        key = r.label if r.outcome == "duplicate" else r.label.split()[0]
        offset, align = placement.get(key, ((7, 3), "left"))
        text = r.label + ("" if r.has_geometry else " (no geometry)")
        ax.annotate(text, (r.geometry.x, r.geometry.y), xytext=offset, textcoords="offset points",
                    ha=align, fontsize=8.5, color=color, zorder=4,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85))
    rlp = gdf[gdf.gen == "Rheinland-Pfalz"].total_bounds
    px = pts.total_bounds
    ax.set_xlim(min(rlp[0], px[0]) - 40_000, max(rlp[2], px[2]) + 230_000)
    ax.set_ylim(min(rlp[1], px[1]) - 25_000, max(rlp[3], px[3]) + 25_000)
    ax.set_axis_off()
    counts = pts.outcome.value_counts()
    ax.set_title(f"Fixture demonstrator — 13 synthetic features: {counts.get('valid', 0)} valid, "
                 f"{counts.get('repaired', 0)} repaired, {counts.get('rejected', 0)} rejected, "
                 f"{counts.get('duplicate', 0)} duplicate", fontsize=11, loc="left")
    ax.legend(handles=[
        Line2D([], [], marker="o", ls="", color="#2ca02c", label="accepted (valid)"),
        Line2D([], [], marker="o", ls="", color="#1f77b4", label="accepted (repaired)"),
        Line2D([], [], marker="X", ls="", color="#d62728", label="rejected (reason in label)"),
        Line2D([], [], marker="o", ls="", color="#7f7f7f", label="duplicate in source"),
        Patch(facecolor="#fbf6e9", edgecolor="#6b5b3e", label="Rhineland-Palatinate (VG250)"),
        Line2D([], [], ls="--", color="#555", label="bounding box used by the state check"),
    ], loc="lower right", fontsize=8, framealpha=0.95)
    fig.text(0.01, 0.005, "Synthetic test data (tests/fixtures). Sites are 100 m squares, shown as points "
             f"at this scale. {CREDIT}", fontsize=7.5, color="#444")
    fig.tight_layout()
    fig.savefig(HERE / "fixture_outcomes.png")
    plt.close(fig)


if __name__ == "__main__":
    boundaries = states()
    live_control_map(boundaries)
    fixture_outcomes_map(boundaries)
    print("maps written to", HERE)
