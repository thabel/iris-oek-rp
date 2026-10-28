"""PostGIS integration tests: idempotency, change detection, rollback.

Run with IRIS_TEST_DATABASE_URL pointing at a PostGIS database; each test
works in its own throwaway schema.
"""

import shutil
from dataclasses import replace

import pytest
from psycopg import sql

from oek_harvester import db
from oek_harvester.pipeline import run_harvest
from oek_harvester.wfs import FixtureTransport


def harvest(cfg, fixture_dir, conn):
    return run_harvest(cfg, FixtureTransport(fixture_dir), mode="fixture", load=True, conn=conn)


def q(conn, schema, query, *args):
    return conn.execute(sql.SQL(query).format(s=sql.Identifier(schema)), args).fetchall()


@pytest.fixture
def scfg(cfg, pg):
    conn, schema = pg
    return replace(cfg, database=replace(cfg.database, schema=schema)), conn, schema


def test_rerun_is_idempotent(scfg, fixture_dir):
    cfg, conn, schema = scfg
    first = harvest(cfg, fixture_dir, conn)
    assert first.status == "succeeded", first.error
    assert (first.counts["db_inserted"], first.counts["db_updated"]) == (6, 0)

    second = harvest(cfg, fixture_dir, conn)
    assert second.counts["db_inserted"] == 0
    assert second.counts["db_updated"] == 0
    assert second.counts["db_unchanged"] == 6

    [(rows, distinct)] = q(conn, schema,
                           "SELECT count(*), count(DISTINCT (state_code, site_id)) "
                           "FROM {s}.compensation_sites")
    assert rows == distinct == 6
    [(first_seen, last_seen)] = q(conn, schema,
                                  "SELECT count(DISTINCT first_seen_run_id), "
                                  "count(DISTINCT last_seen_run_id) FROM {s}.compensation_sites")
    assert (first_seen, last_seen) == (1, 1)
    runs = q(conn, schema, "SELECT status FROM {s}.harvest_runs")
    assert [r[0] for r in runs] == ["succeeded", "succeeded"]


def test_geometry_and_provenance_stored(scfg, fixture_dir):
    cfg, conn, schema = scfg
    harvest(cfg, fixture_dir, conn)
    rows = q(conn, schema,
             "SELECT bool_and(ST_IsValid(geom)), min(ST_SRID(geom)), "
             "min(ST_SRID(geom_source)), bool_and(source_layer = 'ms:oek_f'), "
             "bool_and(state_code = 'RP'), "
             "max(abs(ST_Area(geography(ST_Transform(geom, 4326))) - area_m2) / area_m2) "
             "FROM {s}.compensation_sites")
    valid, srid, src_srid, layer_ok, state_ok, area_diff = rows[0]
    assert (valid, srid, src_srid, layer_ok, state_ok) == (True, 25832, 31466, True, True)
    assert area_diff < 0.001  # PostGIS geography area agrees with stored area_m2

    errors = dict(q(conn, schema,
                    "SELECT severity, count(*) FROM {s}.harvest_errors GROUP BY severity"))
    assert errors["reject"] == 6
    assert errors["warning"] >= 1


def test_changed_source_updates_only_that_site(scfg, fixture_dir, tmp_path):
    cfg, conn, schema = scfg
    harvest(cfg, fixture_dir, conn)

    changed = tmp_path / "wfs"
    shutil.copytree(fixture_dir, changed)
    page = changed / "getfeature_0.xml"
    page.write_text(page.read_text("utf-8").replace(
        "<ms:status>Teilweise abgebucht</ms:status>",
        "<ms:status>vollständig abgebucht</ms:status>", 1), "utf-8")

    result = harvest(cfg, changed, conn)
    assert (result.counts["db_inserted"], result.counts["db_updated"],
            result.counts["db_unchanged"]) == (0, 1, 5)
    [(status,)] = q(conn, schema, "SELECT compensation_status FROM {s}.compensation_sites "
                                  "WHERE site_id = 'OEK-1002'")
    assert status == "fully_debited"


def test_failed_load_rolls_back_and_is_logged(scfg, fixture_dir, monkeypatch):
    cfg, conn, schema = scfg
    harvest(cfg, fixture_dir, conn)
    before = q(conn, schema, "SELECT site_id, content_hash FROM {s}.compensation_sites ORDER BY 1")

    def broken(schema_name):  # fails mid-transaction, after the advisory lock
        return sql.SQL("INSERT INTO {}.no_such_table VALUES (%(site_id)s)").format(
            sql.Identifier(schema_name))

    monkeypatch.setattr(db, "_upsert_statement", broken)
    changed = harvest(cfg, fixture_dir, conn)
    assert changed.status == "failed"

    after = q(conn, schema, "SELECT site_id, content_hash FROM {s}.compensation_sites ORDER BY 1")
    assert after == before
    [(status, error)] = q(conn, schema, "SELECT status, error FROM {s}.harvest_runs "
                                        "ORDER BY started_at DESC LIMIT 1")
    assert status == "failed" and "no_such_table" in error
    [(logged,)] = q(conn, schema, "SELECT count(*) FROM {s}.harvest_errors e "
                                  "JOIN {s}.harvest_runs r USING (run_id) "
                                  "WHERE r.status = 'failed' AND e.code = 'run_failed'")
    assert logged == 1
