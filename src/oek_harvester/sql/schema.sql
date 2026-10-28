-- Staging schema for compensation / eco-account sites.
-- {schema} is substituted with a quoted identifier by oek_harvester.db.
-- Every statement is idempotent so the file can be applied on every run.

CREATE SCHEMA IF NOT EXISTS {schema};

CREATE TABLE IF NOT EXISTS {schema}.harvest_runs (
    run_id            uuid PRIMARY KEY,
    source_name       text        NOT NULL,
    state_code        char(2)     NOT NULL,
    source_endpoint   text        NOT NULL,
    source_layer      text        NOT NULL,
    mode              text        NOT NULL CHECK (mode IN ('live', 'fixture')),
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,
    status            text        NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running', 'succeeded', 'failed')),
    counts            jsonb,
    error             text
);

CREATE TABLE IF NOT EXISTS {schema}.compensation_sites (
    state_code               char(2)  NOT NULL CHECK (state_code = upper(state_code)),
    site_id                  text     NOT NULL,
    source_feature_id        text     NOT NULL,
    gml_id                   text,
    source_name              text     NOT NULL,
    source_endpoint          text     NOT NULL,
    source_layer             text     NOT NULL,
    source_licence           text,
    source_srid              integer  NOT NULL,
    site_name                text,
    municipality_name        text,
    municipality_key         text,
    municipality_code        text,
    cadastral_district_name  text,
    cadastral_district_key   text,
    cadastral_district_code  text,
    cadastral_parcel_refs    text,
    compensation_status      text     NOT NULL CHECK (compensation_status IN (
                                 'planned', 'active', 'partially_debited',
                                 'fully_debited', 'closed', 'unknown', 'unmapped')),
    compensation_status_raw  text,
    area_m2                  double precision NOT NULL CHECK (area_m2 > 0),
    source_area_raw          text,
    source_area_m2           double precision,
    credits_total            numeric,
    credits_debited          numeric,
    credits_available        numeric,
    credit_unit              text,
    geometry_outcome         text     NOT NULL CHECK (geometry_outcome IN ('valid', 'repaired')),
    quality_flags            text[]   NOT NULL DEFAULT ARRAY[]::text[],
    source_attributes        jsonb    NOT NULL,
    geom                     geometry(MultiPolygon, 25832) NOT NULL,
    geom_source              geometry NOT NULL,
    content_hash             text     NOT NULL,
    first_seen_run_id        uuid     NOT NULL REFERENCES {schema}.harvest_runs (run_id),
    last_seen_run_id         uuid     NOT NULL REFERENCES {schema}.harvest_runs (run_id),
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (state_code, site_id),
    CONSTRAINT compensation_sites_geom_valid CHECK (ST_IsValid(geom)),
    CONSTRAINT compensation_sites_geom_source_srid CHECK (ST_SRID(geom_source) = source_srid)
);

CREATE INDEX IF NOT EXISTS compensation_sites_geom_gist
    ON {schema}.compensation_sites USING gist (geom);
CREATE INDEX IF NOT EXISTS compensation_sites_status_idx
    ON {schema}.compensation_sites (state_code, compensation_status);

CREATE TABLE IF NOT EXISTS {schema}.harvest_errors (
    id                 bigserial PRIMARY KEY,
    run_id             uuid NOT NULL REFERENCES {schema}.harvest_runs (run_id) ON DELETE CASCADE,
    stage              text NOT NULL,
    severity           text NOT NULL CHECK (severity IN ('reject', 'warning', 'error')),
    code               text NOT NULL,
    source_feature_id  text,
    site_id            text,
    message            text,
    details            jsonb,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS harvest_errors_run_idx
    ON {schema}.harvest_errors (run_id, severity, code);
