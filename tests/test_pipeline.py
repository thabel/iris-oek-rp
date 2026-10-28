from dataclasses import replace

import pytest

from oek_harvester.pipeline import run_harvest
from oek_harvester.wfs import FixtureTransport


@pytest.fixture
def result(cfg, fixture_dir):
    return run_harvest(cfg, FixtureTransport(fixture_dir), mode="fixture", load=False)


def test_counts_by_outcome(result):
    assert result.status == "succeeded"
    c = result.counts
    assert c["features_received"] == c["number_matched"] == 13
    assert c["pages"] == 3
    assert c["accepted_valid"] == 5
    assert c["accepted_repaired"] == 1
    assert c["duplicates_in_source"] == 1
    assert c["rejected_total"] == 6
    assert c["accepted_total"] + c["rejected_total"] + c["duplicates_in_source"] == 13


def test_reject_reasons(result):
    rejects = {i.source_feature_id: i.code for i in result.issues if i.severity == "reject"}
    assert rejects == {
        "1004": "repair_changes_area",
        "1005": "missing_geometry",
        "1006": "non_polygon_geometry",
        "1007": "outside_state_extent",
        "1010": "tiny_area",
        "oek_f.noid12": "missing_source_id",  # falls back to gml:id for the log only
    }


def test_records_are_normalised(result):
    by_id = {r.site_id: r for r in result.build.records}
    assert set(by_id) == {"OEK-1001", "OEK-1002", "OEK-1003", "OEK-1008", "OEK-1009", "OEK-1011"}

    trier = by_id["OEK-1001"]
    assert trier.state_code == "RP"
    assert trier.cadastral_district_name == "Kürenz"            # NFC
    assert trier.credits_total == 120_000
    assert trier.source_srid == 31466
    assert trier.compensation_status == "active"

    kh = by_id["OEK-1008"]
    assert kh.municipality_name == "Bad Kreuznach"
    assert kh.municipality_code == "07133006"
    assert kh.site_name == "Magerrasen am Kuhberg"
    assert kh.compensation_status == "unmapped"
    assert kh.compensation_status_raw == "in Prüfung"
    assert kh.credits_total is None                             # "unbekannt" not guessed
    assert "unparseable_number:credits_total" in kh.quality_flags

    no_muni = by_id["OEK-1009"]
    assert no_muni.municipality_name is None
    assert no_muni.compensation_status == "unknown"
    assert {"missing_municipality", "missing_status"} <= set(no_muni.quality_flags)

    speyer = by_id["OEK-1011"]
    assert {"area_mismatch", "dropped_z"} <= set(speyer.quality_flags)
    assert speyer.compensation_status == "fully_debited"

    assert by_id["OEK-1003"].geometry_outcome == "repaired"
    assert by_id["OEK-1002"].source_attributes["gemarkung"] == "Moselweiß"


def test_provenance_in_report(result):
    d = result.discovery
    assert d["layer"] == "ms:oek_f"
    assert d["request_crs"] == "urn:ogc:def:crs:EPSG::31466"
    assert all("ABSENT" not in v for v in d["attribute_mapping"].values())
    t = result.transformation
    assert t["source_srid"] == 31466 and t["target_srid"] == 25832
    assert "DHDN to ETRS89" in t["operation"]
    assert t["accuracy_m"] is not None


def test_unadvertised_layer_fails_cleanly(cfg, fixture_dir):
    bad = replace(cfg, source=replace(cfg.source, type_name="rp_oekokonto"))
    result = run_harvest(bad, FixtureTransport(fixture_dir), mode="fixture", load=False)
    assert result.status == "failed"
    assert "not advertised" in result.error


def test_mapping_to_absent_attribute_is_reported(cfg, fixture_dir):
    fields = dict(cfg.mapping.fields, site_name="name_that_does_not_exist")
    bad = replace(cfg, mapping=replace(cfg.mapping, fields=fields))
    result = run_harvest(bad, FixtureTransport(fixture_dir), mode="fixture", load=False)
    assert any(i.code == "mapped_attribute_absent" for i in result.issues)


def test_unconfigured_status_is_unknown_without_per_site_warnings(cfg, fixture_dir):
    fields = {k: v for k, v in cfg.mapping.fields.items() if k != "compensation_status"}
    bare = replace(cfg, mapping=replace(cfg.mapping, fields=fields))
    result = run_harvest(bare, FixtureTransport(fixture_dir), mode="fixture", load=False)
    assert {r.compensation_status for r in result.build.records} == {"unknown"}
    assert not any(i.code in ("missing_status", "unmapped_status") for i in result.issues)


def test_rerun_produces_identical_hashes(cfg, fixture_dir):
    a = run_harvest(cfg, FixtureTransport(fixture_dir), mode="fixture", load=False)
    b = run_harvest(cfg, FixtureTransport(fixture_dir), mode="fixture", load=False)
    assert ({r.site_id: r.content_hash for r in a.build.records}
            == {r.site_id: r.content_hash for r in b.build.records})
