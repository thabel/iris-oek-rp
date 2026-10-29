import json
import re

from oek_harvester.html_report import render_html_report
from oek_harvester.pipeline import run_harvest
from oek_harvester.report import write_outputs
from oek_harvester.wfs import FixtureTransport


def test_all_outputs_are_written(cfg, fixture_dir, tmp_path):
    result = run_harvest(cfg, FixtureTransport(fixture_dir), mode="fixture", load=False)
    paths = write_outputs(result, cfg, tmp_path)
    for key in ("run_report", "quality_report", "issues", "sample_export", "html_report"):
        assert paths[key].exists(), key


def test_html_report_shows_counts_steps_and_reasons(cfg, fixture_dir):
    result = run_harvest(cfg, FixtureTransport(fixture_dir), mode="fixture", load=False)
    html = render_html_report(result, cfg)

    assert "Features received" in html and ">13<" in html
    assert "5 valid · 1 repaired" in html
    assert html.count("Downloaded page") == 3
    for code in ("repair_changes_area", "missing_geometry", "non_polygon_geometry",
                 "outside_state_extent", "tiny_area", "missing_source_id"):
        assert code in html
    data = json.loads(re.search(r'<script type="application/json" id="sites">(.*?)</script>',
                                html, re.S).group(1).replace("<\\/", "</"))
    assert len(data["features"]) == 6
    for feature in data["features"]:
        point = feature["geometry"]["coordinates"]
        while isinstance(point[0], list):  # Polygon or MultiPolygon nesting
            point = point[0]
        lon, lat = point
        assert 6.0 < lon < 8.6 and 48.9 < lat < 51.0  # WGS84 for the web map, inside RLP


def test_html_report_escapes_source_values(cfg, fixture_dir):
    result = run_harvest(cfg, FixtureTransport(fixture_dir), mode="fixture", load=False)
    result.build.records[0].site_name = "<script>alert(1)</script>"
    html = render_html_report(result, cfg)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html  # table cell
    assert "<\\/script>" in html                             # embedded map data
