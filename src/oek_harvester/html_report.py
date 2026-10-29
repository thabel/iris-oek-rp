"""Self-contained HTML report for one harvest run.

One file, opened by double-click: summary counts, the requests the harvester
made in order, a map of accepted sites, and tables of rejections, warnings
and accepted sites. All source values are escaped (they come from an
external service). The map loads Leaflet and OpenStreetMap tiles in the
browser, so it needs internet access; everything else works offline.
"""

from __future__ import annotations

import json
from collections import Counter
from html import escape
from urllib.parse import parse_qs, urlsplit

import shapely
from pyproj import Transformer
from shapely.geometry import mapping
from shapely.ops import transform as transform_geometry

from .config import Config
from .pipeline import RunResult

LEAFLET = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4"

EXPLAIN = {
    "missing_source_id": "No identifier in the source, so the site could not get a stable key.",
    "missing_geometry": "The feature has no shape.",
    "geometry_parse_error": "The shape could not be read.",
    "missing_srs": "The shape does not say which coordinate system it uses.",
    "unknown_srs": "The coordinate system is not recognised.",
    "non_polygon_geometry": "A point or line in a layer of areas.",
    "repair_changes_area": "The shape is broken and repairing it would change its area by more than "
                           "the allowed limit, so it would invent geometry.",
    "unrepairable_geometry": "The shape is broken and cannot be repaired into an area.",
    "invalid_after_projection": "The shape became invalid after conversion and could not be fixed.",
    "tiny_area": "Smaller than the minimum area (a digitising splinter).",
    "outside_state_extent": "Located outside Rhineland-Palatinate.",
    "municipality_code_outside_state": "The official municipality key belongs to another state.",
    "geometry_repaired": "A small drawing error was repaired (area change within the limit).",
    "unmapped_status": "The status value has no configured mapping; kept as 'unmapped'.",
    "missing_status": "The status is empty; kept as 'unknown'.",
    "missing_municipality": "The municipality is empty.",
    "unparseable_number": "A number field contains text; left empty instead of guessing.",
    "area_mismatch": "The area stated by the source differs from the measured area by more than 5 %.",
    "duplicate_in_source": "The same site was sent twice; the first copy was kept.",
    "conflicting_duplicate_in_source": "The same identifier was sent twice with different content.",
    "datum_grid_unavailable": "A more accurate coordinate transformation grid is not installed.",
    "mapped_attribute_absent": "A configured field does not exist in the service's schema.",
    "count_mismatch": "The number of features received differs from the number announced.",
    "possible_truncation": "The service may have cut the result short.",
    "run_failed": "The run failed; nothing was written.",
}

_TO_LONLAT = Transformer.from_crs(25832, 4326, always_xy=True)


def _num(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def _request_steps(requests: list[dict]) -> list[dict]:
    steps, page = [], 0
    for req in requests:
        params = {k.upper(): v[0] for k, v in parse_qs(urlsplit(req["url"]).query).items()}
        kind = params.get("REQUEST", "").lower()
        if kind == "getcapabilities":
            what = "Read the service capabilities (layers, coordinate systems, paging options)"
        elif kind == "describefeaturetype":
            what = "Read the layer's attribute schema"
        elif kind == "getfeature" and params.get("RESULTTYPE", "").lower() == "hits":
            what = "Asked how many features the layer holds"
        elif kind == "getfeature":
            page += 1
            what = (f"Downloaded page {page} (start at feature {params.get('STARTINDEX', '0')}, "
                    f"up to {params.get('COUNT', 'all')})")
        else:
            what = "Request"
        ok = req.get("status") == 200 and not req.get("error")
        steps.append({"what": what, "ok": ok, "status": req.get("status"),
                      "bytes": req.get("bytes", 0), "seconds": req.get("elapsed_s", 0.0),
                      "error": req.get("error")})
    return steps


def _features(result: RunResult) -> list[dict]:
    features = []
    for r in (result.build.records if result.build else []):
        geom = shapely.from_wkb(r.geom_wkb).simplify(1.0, preserve_topology=True)
        geom = shapely.set_precision(transform_geometry(_TO_LONLAT.transform, geom), 1e-6)
        features.append({
            "type": "Feature",
            "geometry": mapping(geom),
            "properties": {
                "Site": r.site_id, "Name": r.site_name, "Municipality": r.municipality_name,
                "Municipality key": r.municipality_code,
                "Cadastral district": r.cadastral_district_name,
                "Status": f"{r.compensation_status} (source: {r.compensation_status_raw})"
                          if r.compensation_status_raw else r.compensation_status,
                "Area m²": f"{r.area_m2:,.0f}",
                "Credits total / available":
                    f"{_num(r.credits_total) or '—'} / {_num(r.credits_available) or '—'}"
                    if r.credits_total is not None or r.credits_available is not None else None,
                "Geometry": r.geometry_outcome,
                "Flags": ", ".join(r.quality_flags) or None,
                "_outcome": r.geometry_outcome,
            },
        })
    return features


def _table(headers: list[str], rows: list[list], empty: str) -> str:
    if not rows:
        return f'<p class="muted">{escape(empty)}</p>'
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{escape('' if v is None else str(v))}</td>" for v in row)
                   + "</tr>" for row in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def render_html_report(result: RunResult, cfg: Config) -> str:
    c = result.counts
    d = result.discovery
    rejects = [i for i in result.issues if i.severity == "reject"]
    warnings = Counter(i.code for i in result.issues if i.severity == "warning")
    errors = [i for i in result.issues if i.severity == "error"]

    cards = [
        ("Features received", c.get("features_received", 0), ""),
        ("Accepted", c.get("accepted_total", 0),
         f"{c.get('accepted_valid', 0)} valid · {c.get('accepted_repaired', 0)} repaired"),
        ("Rejected", c.get("rejected_total", 0), "see the table below"),
        ("Duplicates in source", c.get("duplicates_in_source", 0), "first copy kept"),
    ]
    if "db_inserted" in c:
        cards.append(("Database", c["db_inserted"] + c["db_updated"] + c["db_unchanged"],
                      f"{c['db_inserted']} added · {c['db_updated']} updated · "
                      f"{c['db_unchanged']} unchanged"))
    else:
        cards.append(("Database", "—", "not loaded (--no-db)"))
    cards_html = "".join(
        f'<div class="card"><div class="label">{escape(t)}</div><div class="value">{escape(str(v))}</div>'
        f'<div class="sub">{escape(s)}</div></div>' for t, v, s in cards)

    steps = _request_steps(result.requests)
    step_items = [
        f'<li class="{"ok" if s["ok"] else "bad"}"><span class="tick">{"✔" if s["ok"] else "✖"}</span>'
        f'{escape(s["what"])} <span class="muted">— HTTP {escape(str(s["status"]))}, '
        f'{s["bytes"]:,} bytes, {s["seconds"]:.1f} s'
        + (f', {escape(s["error"])}' if s["error"] else "") + "</span></li>"
        for s in steps]
    t = result.transformation
    if result.build:
        step_items.append(
            f'<li class="ok"><span class="tick">✔</span>Checked every feature: identifier, geometry '
            f'(validate, repair if defensible), state membership, attribute normalisation'
            + (f' <span class="muted">— projected with “{escape(t["operation"])}”, '
               f'~{escape(str(t["accuracy_m"]))} m</span>' if t else "") + "</li>")
    if "db_inserted" in c:
        step_items.append(
            f'<li class="ok"><span class="tick">✔</span>Saved to '
            f'<code>{escape(cfg.database.schema)}.compensation_sites</code> in one transaction '
            f'<span class="muted">— {c["db_inserted"]} added, {c["db_updated"]} updated, '
            f'{c["db_unchanged"]} unchanged</span></li>')
    for e in errors:
        step_items.append(f'<li class="bad"><span class="tick">✖</span>{escape(e.message or e.code)}</li>')

    reject_rows = [[i.source_feature_id, i.code, EXPLAIN.get(i.code, ""), i.message] for i in rejects]
    warning_rows = [[code, n, EXPLAIN.get(code, "")] for code, n in sorted(warnings.items())]
    site_rows = [[r.site_id, r.site_name, r.municipality_name, r.compensation_status,
                  f"{r.area_m2:,.0f}", _num(r.credits_available), r.geometry_outcome,
                  ", ".join(r.quality_flags)] for r in (result.build.records if result.build else [])]
    mapping_rows = [[k, v] for k, v in (d.get("attribute_mapping") or {}).items()]
    provenance_rows = [
        ["Source", cfg.source.name], ["Provider", cfg.source.provider],
        ["Endpoint", cfg.source.endpoint], ["Layer", cfg.source.type_name],
        ["Licence", cfg.source.licence], ["Requested CRS (kept as geom_source)", d.get("request_crs")],
        ["Working CRS", "EPSG:25832"],
        ["Transformation", f'{t["operation"]} (~{t["accuracy_m"]} m)' if t else None],
        ["Paging advertised", d.get("implements_result_paging")],
        ["Features announced (numberMatched)", d.get("number_matched")],
    ]

    data = json.dumps({"type": "FeatureCollection", "features": _features(result)},
                      ensure_ascii=False).replace("</", "<\\/")
    status_class = "ok" if result.status == "succeeded" else "bad"
    title = f"Harvest report — {cfg.source.type_name} ({cfg.state_code})"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<link rel="stylesheet" href="{LEAFLET}/leaflet.min.css">
<style>
  :root {{ --ink:#1f2328; --muted:#59636e; --line:#d1d9e0; --bg:#f6f8fa; --ok:#1a7f37; --bad:#cf222e;
          --valid:#2ca02c; --repaired:#1f77b4; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; color:var(--ink); background:#fff; }}
  main {{ max-width:1100px; margin:0 auto; padding:24px 16px 48px; }}
  h1 {{ font-size:24px; margin:0 0 4px; }} h2 {{ font-size:18px; margin:32px 0 10px; }}
  .muted {{ color:var(--muted); }} code {{ background:var(--bg); padding:1px 5px; border-radius:4px; }}
  .badge {{ display:inline-block; padding:2px 10px; border-radius:12px; color:#fff; font-weight:600; font-size:13px; }}
  .badge.ok {{ background:var(--ok); }} .badge.bad {{ background:var(--bad); }}
  .notice {{ background:#fff8c5; border:1px solid #e6d36b; padding:10px 14px; border-radius:6px; margin:14px 0; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; margin-top:16px; }}
  .card {{ border:1px solid var(--line); border-radius:8px; padding:12px 14px; background:var(--bg); }}
  .card .label {{ color:var(--muted); font-size:13px; }} .card .value {{ font-size:28px; font-weight:700; }}
  .card .sub {{ color:var(--muted); font-size:12px; }}
  ol.steps {{ list-style:none; padding:0; margin:0; }}
  ol.steps li {{ padding:6px 0; border-bottom:1px solid var(--line); }}
  .tick {{ display:inline-block; width:22px; font-weight:700; }} li.ok .tick {{ color:var(--ok); }} li.bad .tick {{ color:var(--bad); }}
  #map {{ height:460px; border:1px solid var(--line); border-radius:8px; }}
  .legend span {{ display:inline-block; width:12px; height:12px; border-radius:2px; margin:0 4px 0 12px; vertical-align:-1px; }}
  .scroll {{ overflow-x:auto; max-height:420px; overflow-y:auto; border:1px solid var(--line); border-radius:8px; }}
  table {{ border-collapse:collapse; width:100%; font-size:14px; }}
  th, td {{ text-align:left; padding:6px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ background:var(--bg); position:sticky; top:0; }}
  footer {{ margin-top:40px; color:var(--muted); font-size:13px; }}
</style>
</head>
<body>
<main>
  <h1>{escape(title)}</h1>
  <div class="muted">{escape(cfg.source.name)} · mode <b>{escape(result.mode)}</b> ·
    {escape(result.started_at)} → {escape(result.finished_at)} UTC ·
    <span class="badge {status_class}">{escape(result.status)}</span></div>
  {f'<div class="notice"><b>Run failed:</b> {escape(result.error)}</div>' if result.error else ''}
  {'<div class="notice">Mode <b>fixture</b>: the features are synthetic test data served by a local fake WFS. See tests/fixtures/make_fixtures.py.</div>' if result.mode == 'fixture' else ''}

  <div class="cards">{cards_html}</div>

  <h2>What the harvester did</h2>
  <ol class="steps">{''.join(step_items)}</ol>

  <h2>Map of accepted sites</h2>
  <p class="legend muted"><span style="background:var(--valid)"></span>valid<span style="background:var(--repaired)"></span>repaired
    — click a site for its details. Rejected features are not drawn; they are listed below.</p>
  <div id="map"></div>

  <h2>Rejected features ({len(rejects)})</h2>
  {_table(["Source id", "Code", "Meaning", "Detail"], reject_rows, "No feature was rejected.")}

  <h2>Warnings (accepted with a flag)</h2>
  {_table(["Code", "Count", "Meaning"], warning_rows, "No warnings.")}

  <h2>Accepted sites ({len(site_rows)})</h2>
  {_table(["Site", "Name", "Municipality", "Status", "Area m²", "Credits available", "Geometry", "Flags"],
          site_rows, "No site accepted.")}

  <h2>Attribute mapping</h2>
  {_table(["Target column", "Source attribute (checked against DescribeFeatureType)"], mapping_rows,
          "No attribute mapped: every source attribute is kept verbatim in source_attributes.")}

  <h2>Provenance</h2>
  {_table(["", ""], provenance_rows, "")}

  <footer>Generated by oek-harvester · run {escape(result.run_id or "not loaded")} ·
    map data © OpenStreetMap contributors</footer>
</main>
<script type="application/json" id="sites">{data}</script>
<script src="{LEAFLET}/leaflet.min.js"></script>
<script>
  const data = JSON.parse(document.getElementById("sites").textContent);
  const el = document.getElementById("map");
  if (typeof L === "undefined") {{
    el.textContent = "The map needs internet access (Leaflet and OpenStreetMap tiles). The tables below work offline.";
  }} else {{
    const map = L.map(el);
    L.tileLayer("https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
      maxZoom: 19, attribution: "&copy; OpenStreetMap contributors" }}).addTo(map);
    const colors = {{ valid: "#2ca02c", repaired: "#1f77b4" }};
    const layer = L.geoJSON(data, {{
      style: f => ({{ color: colors[f.properties._outcome] || "#555", weight: 2, fillOpacity: 0.35 }}),
      onEachFeature: (f, lyr) => {{
        const box = document.createElement("div");
        for (const [k, v] of Object.entries(f.properties)) {{
          if (k.startsWith("_") || v === null) continue;
          const row = document.createElement("div");
          const key = document.createElement("b"); key.textContent = k + ": ";
          row.append(key, document.createTextNode(String(v)));
          box.append(row);
        }}
        lyr.bindPopup(box);
        // Small sites vanish at regional zoom: add a marker at each centre.
        L.circleMarker(lyr.getBounds().getCenter(), {{ radius: 6, weight: 2, color: "#fff",
          fillColor: colors[f.properties._outcome] || "#555", fillOpacity: 0.9 }})
          .bindPopup(box).addTo(map);
      }},
    }}).addTo(map);
    if (data.features.length) map.fitBounds(layer.getBounds(), {{ padding: [20, 20], maxZoom: 13 }});
    else map.setView([49.9, 7.4], 7);
  }}
</script>
</body>
</html>
"""
