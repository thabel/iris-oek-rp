-- Reproducible validation queries for iris_staging.compensation_sites.
-- Run with: psql "$IRIS_DATABASE_URL" -f sql/validation_queries.sql

-- 1. Key uniqueness: must return 0 rows.
SELECT state_code, site_id, count(*)
FROM iris_staging.compensation_sites
GROUP BY 1, 2
HAVING count(*) > 1;

-- 2. Domain and provenance: one row per state/layer/licence, all geometries
--    valid MultiPolygons in EPSG:25832 with the original CRS retained.
SELECT state_code,
       source_layer,
       source_licence,
       count(*)                                    AS sites,
       bool_and(ST_IsValid(geom))                  AS all_valid,
       min(ST_SRID(geom))                          AS working_srid,
       array_agg(DISTINCT source_srid)             AS source_srids,
       bool_and(ST_SRID(geom_source) = source_srid) AS source_crs_kept,
       array_agg(DISTINCT GeometryType(geom))      AS geom_types
FROM iris_staging.compensation_sites
GROUP BY 1, 2, 3;

-- 3. Stored ground area vs PostGIS geography area (should agree < 0.1 %).
SELECT site_id,
       round(area_m2::numeric, 1)                                   AS area_m2,
       round(ST_Area(geography(ST_Transform(geom, 4326)))::numeric, 1) AS postgis_geography_m2,
       round(ST_Area(geom)::numeric, 1)                             AS planar_25832_m2
FROM iris_staging.compensation_sites
ORDER BY site_id;

-- 4. Status distribution and quality flags.
SELECT compensation_status, count(*) AS sites,
       array_agg(DISTINCT compensation_status_raw) AS raw_values
FROM iris_staging.compensation_sites
GROUP BY 1 ORDER BY 1;

SELECT flag, count(*) AS sites
FROM iris_staging.compensation_sites, unnest(quality_flags) AS flag
GROUP BY 1 ORDER BY 2 DESC, 1;

-- 5. Runs and outcome counts, newest first.
SELECT run_id, mode, status, started_at,
       counts ->> 'features_received'  AS received,
       counts ->> 'accepted_total'     AS accepted,
       counts ->> 'rejected_total'     AS rejected,
       counts ->> 'db_inserted'        AS inserted,
       counts ->> 'db_updated'         AS updated,
       counts ->> 'db_unchanged'       AS unchanged
FROM iris_staging.harvest_runs
ORDER BY started_at DESC;

-- 6. Rejections of the latest run.
SELECT e.code, e.source_feature_id, e.message
FROM iris_staging.harvest_errors e
WHERE e.severity = 'reject'
  AND e.run_id = (SELECT run_id FROM iris_staging.harvest_runs ORDER BY started_at DESC LIMIT 1)
ORDER BY e.code, e.source_feature_id;

-- 7. Spatial query: eco-account sites with available credits within 25 km
--    of Koblenz city centre (7.5886 E, 50.3569 N), nearest first.
SELECT site_id, site_name, municipality_name, compensation_status,
       credits_available, round(area_m2::numeric) AS area_m2,
       round((ST_Distance(geom, ST_Transform(ST_SetSRID(ST_MakePoint(7.5886, 50.3569), 4326), 25832))
              / 1000)::numeric, 2) AS distance_km
FROM iris_staging.compensation_sites
WHERE ST_DWithin(geom, ST_Transform(ST_SetSRID(ST_MakePoint(7.5886, 50.3569), 4326), 25832), 25000)
  AND coalesce(credits_available, 0) > 0
ORDER BY distance_km;
