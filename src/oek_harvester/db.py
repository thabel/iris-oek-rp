"""PostGIS loading: schema setup, run bookkeeping and idempotent upserts.

Loading rules:

* Only the configured staging schema is written (default ``iris_staging``).
* All site upserts and quality issues of a run are written in one
  transaction, serialised per state by an advisory lock, so a failed run
  leaves the table exactly as before and concurrent runs cannot interleave.
* A site is keyed by (state_code, site_id). Re-running identical input
  updates only ``last_seen_run_id``; rows whose content hash changed are
  rewritten and get a new ``updated_at``. Sites missing from a run are
  reported as not seen, never deleted.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from importlib import resources

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from .records import Issue, SiteRecord

SITE_COLUMNS = (
    "state_code", "site_id", "source_feature_id", "gml_id", "source_name",
    "source_endpoint", "source_layer", "source_licence", "source_srid", "site_name",
    "municipality_name", "municipality_key", "municipality_code",
    "cadastral_district_name", "cadastral_district_key", "cadastral_district_code",
    "cadastral_parcel_refs", "compensation_status", "compensation_status_raw",
    "area_m2", "source_area_raw", "source_area_m2", "credits_total", "credits_debited",
    "credits_available", "credit_unit", "geometry_outcome", "quality_flags",
    "source_attributes", "geom", "geom_source", "content_hash",
    "first_seen_run_id", "last_seen_run_id",
)


@dataclass
class Provenance:
    source_name: str
    endpoint: str
    layer: str
    licence: str | None


@dataclass
class LoadCounts:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    not_seen_this_run: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"db_inserted": self.inserted, "db_updated": self.updated,
                "db_unchanged": self.unchanged,
                "db_existing_not_seen_this_run": self.not_seen_this_run}


def connect(url: str) -> psycopg.Connection:
    # Autocommit so every ``conn.transaction()`` block below is a real
    # transaction (committed or rolled back on its own), never a savepoint
    # nested in an implicitly opened outer transaction.
    return psycopg.connect(url, autocommit=True)


def apply_schema(conn: psycopg.Connection, schema: str) -> None:
    ddl = resources.files("oek_harvester").joinpath("sql/schema.sql").read_text("utf-8")
    statement = sql.SQL(ddl).format(schema=sql.Identifier(schema))
    with conn.transaction():
        conn.execute(statement)


def start_run(conn: psycopg.Connection, schema: str, prov: Provenance,
              state_code: str, mode: str) -> uuid.UUID:
    run_id = uuid.uuid4()
    with conn.transaction():
        conn.execute(
            sql.SQL("INSERT INTO {}.harvest_runs (run_id, source_name, state_code, "
                    "source_endpoint, source_layer, mode) VALUES (%s, %s, %s, %s, %s, %s)"
                    ).format(sql.Identifier(schema)),
            (run_id, prov.source_name, state_code, prov.endpoint, prov.layer, mode),
        )
    return run_id


def finish_run(conn: psycopg.Connection, schema: str, run_id: uuid.UUID, status: str,
               counts: dict | None, error: str | None = None) -> None:
    with conn.transaction():
        conn.execute(
            sql.SQL("UPDATE {}.harvest_runs SET status = %s, finished_at = now(), "
                    "counts = %s, error = %s WHERE run_id = %s").format(sql.Identifier(schema)),
            (status, Jsonb(counts) if counts is not None else None, error, run_id),
        )


def log_issues(conn: psycopg.Connection, schema: str, run_id: uuid.UUID,
               issues: list[Issue]) -> None:
    if not issues:
        return
    with conn.cursor() as cur:
        cur.executemany(
            sql.SQL("INSERT INTO {}.harvest_errors (run_id, stage, severity, code, "
                    "source_feature_id, site_id, message, details) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)").format(sql.Identifier(schema)),
            [(run_id, i.stage, i.severity, i.code, i.source_feature_id, i.site_id,
              i.message, Jsonb(json.loads(json.dumps(i.details, default=str))))
             for i in issues],
        )


def _upsert_statement(schema: str) -> sql.Composed:
    placeholders = []
    for col in SITE_COLUMNS:
        if col == "geom":
            placeholders.append(sql.SQL("ST_SetSRID(ST_GeomFromWKB(%(geom)s), 25832)"))
        elif col == "geom_source":
            placeholders.append(sql.SQL("ST_SetSRID(ST_GeomFromWKB(%(geom_source)s), %(source_srid)s)"))
        else:
            placeholders.append(sql.Placeholder(col))
    updatable = [c for c in SITE_COLUMNS
                 if c not in ("state_code", "site_id", "first_seen_run_id")]
    assignments = sql.SQL(", ").join(
        sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(c)) for c in updatable)
    return sql.SQL(
        "INSERT INTO {table} ({cols}) VALUES ({vals}) "
        "ON CONFLICT (state_code, site_id) DO UPDATE SET {assignments}, "
        "updated_at = CASE WHEN {table}.content_hash IS DISTINCT FROM EXCLUDED.content_hash "
        "THEN now() ELSE {table}.updated_at END"
    ).format(
        table=sql.Identifier(schema, "compensation_sites"),
        cols=sql.SQL(", ").join(map(sql.Identifier, SITE_COLUMNS)),
        vals=sql.SQL(", ").join(placeholders),
        assignments=assignments,
    )


def load_sites(conn: psycopg.Connection, schema: str, run_id: uuid.UUID, state_code: str,
               records: list[SiteRecord], issues: list[Issue], prov: Provenance) -> LoadCounts:
    """Upsert all records and log all issues in a single transaction."""
    counts = LoadCounts()
    table = sql.Identifier(schema, "compensation_sites")
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                     (f"{schema}.compensation_sites:{state_code}",))
        existing = dict(conn.execute(
            sql.SQL("SELECT site_id, content_hash FROM {} WHERE state_code = %s").format(table),
            (state_code,)).fetchall())
        rows = []
        for r in records:
            before = existing.get(r.site_id)
            if before is None:
                counts.inserted += 1
            elif before != r.content_hash:
                counts.updated += 1
            else:
                counts.unchanged += 1
            rows.append({
                "state_code": r.state_code, "site_id": r.site_id,
                "source_feature_id": r.source_feature_id, "gml_id": r.gml_id,
                "source_name": prov.source_name, "source_endpoint": prov.endpoint,
                "source_layer": prov.layer, "source_licence": prov.licence,
                "source_srid": r.source_srid, "site_name": r.site_name,
                "municipality_name": r.municipality_name, "municipality_key": r.municipality_key,
                "municipality_code": r.municipality_code,
                "cadastral_district_name": r.cadastral_district_name,
                "cadastral_district_key": r.cadastral_district_key,
                "cadastral_district_code": r.cadastral_district_code,
                "cadastral_parcel_refs": r.cadastral_parcel_refs,
                "compensation_status": r.compensation_status,
                "compensation_status_raw": r.compensation_status_raw,
                "area_m2": r.area_m2, "source_area_raw": r.source_area_raw,
                "source_area_m2": r.source_area_m2, "credits_total": r.credits_total,
                "credits_debited": r.credits_debited, "credits_available": r.credits_available,
                "credit_unit": r.credit_unit, "geometry_outcome": r.geometry_outcome,
                "quality_flags": r.quality_flags, "source_attributes": Jsonb(r.source_attributes),
                "geom": r.geom_wkb, "geom_source": r.geom_source_wkb,
                "content_hash": r.content_hash,
                "first_seen_run_id": run_id, "last_seen_run_id": run_id,
            })
        if rows:
            with conn.cursor() as cur:
                cur.executemany(_upsert_statement(schema), rows)
        seen = {r.site_id for r in records}
        counts.not_seen_this_run = len(set(existing) - seen)
        log_issues(conn, schema, run_id, issues)
    return counts
