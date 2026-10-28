# iris-oek-rp — Rhineland-Palatinate eco-account WFS harvester

IRIS task **056 / IRIS-OEK-RP**. A reproducible harvester that reads registered eco-account (Ökokonto) offsetting sites for Rhineland-Palatinate from an OGC WFS and loads them into `iris_staging.compensation_sites` in PostGIS.

> **Status: fixture-based demonstrator.** The endpoint and layer named in the brief do not exist (no DNS record; no `rp_oekokonto` layer). The service that is registered for this data (SGD Nord LANIS *Kompensationsverzeichnis WFS*, layer `ms:oek_f`) returned HTTP 404 on 2026-10-27, and its geoportal proxy is access-controlled. The live code path is implemented and configured, but every demonstrated result comes from fixtures. See [docs/discovery.md](docs/discovery.md) for the evidence.

## Contents

- [Data provenance](#data-provenance)
- [How it works](#how-it-works)
- [Run it](#run-it)
- [Schema mapping](#schema-mapping)
- [Quality rules](#quality-rules)
- [Tests](#tests)
- [Assumptions and limitations](#assumptions-and-limitations)
- [Repository layout](#repository-layout)

## Data provenance

| | |
|---|---|
| Register | Landeskompensationsverzeichnis Rheinland-Pfalz (LKompVzVO), KSP/KomOn system |
| Publisher | Struktur- und Genehmigungsdirektion Nord – LANIS-Zentrale, Koblenz |
| Service | Kompensationsverzeichnis WFS (geoportal.rlp.de id 536), MapServer, WFS 2.0.0 |
| Endpoint (registered) | `https://geodaten.naturschutz.rlp.de/kartendienste_naturschutz/mod_ogc/wfs_getmap.php?mapfile=komon` |
| Layer | `ms:oek_f` — *Oekokonto OEK Flaechen* (polygons) |
| Native CRS | EPSG:31466 (DHDN / Gauss-Krüger zone 2), also 31467, 3857, 4326 |
| Licence | ODbL 1.0. Downstream use must attribute the source and share derived databases alike. |

Every stored row carries `source_name`, `source_endpoint`, `source_layer`, `source_licence`, `source_feature_id`, `gml_id`, the original geometry and CRS (`geom_source`, `source_srid`), all original attributes (`source_attributes`), and the run that first and last saw it.

## How it works

```
GetCapabilities ─► verify layer, CRS list, paging support, fees/constraints
DescribeFeatureType ─► verify every configured attribute mapping exists
GetFeature resultType=hits ─► expected count
GetFeature STARTINDEX/COUNT pages ─► GML 3.2 parser (explicit axis order)
        │
        ▼
per feature: stable key ─► geometry validate / repair / project ─► state checks
             ─► attribute normalisation ─► content hash
        │
        ▼
one transaction (advisory lock per state): upsert sites + log every issue
        │
        ▼
run_report.json · quality_report.md · issues.jsonl · sample_sites_25832.gpkg
```

- **Controlled requests.** There is a fixed delay between requests, a timeout, and bounded retries with exponential backoff on 429/5xx (honouring `Retry-After`). Other 4xx errors are **not** retried, so access restrictions are reported rather than worked around. A `max_features` guard stops runaway paging, and every request is logged in the run report.
- **Paging.** STARTINDEX/COUNT is used when the capabilities advertise `ImplementsResultPaging`. Paging stops on a short page or at `numberMatched`. Without paging support it makes one request and flags possible truncation at `CountDefault`.
- **Axis order.** WFS 2.0 servers write coordinates for `urn:ogc:def:crs:EPSG::31466` as northing, easting. The parser reads the axis order from the CRS definition, so geometries are never silently swapped. The legacy `EPSG:xxxx` form stays x/y.
- **Projection.** The most accurate installed transformation whose area of use covers the whole state is chosen explicitly. Regional grids such as the Saarland-only SeTa2016 are never applied state-wide. The run report records the choice with its accuracy. With the AdV **BETA2007** NTv2 grid (fetched in the Docker image) DHDN→ETRS89 is sub-metre. Without it PROJ uses a 7-parameter Helmert (~3 m), and the run reports `datum_grid_unavailable`.

## Run it

### Docker (PostGIS + harvester)

```bash
cp .env.example .env          # set POSTGRES_PASSWORD
docker compose up --build harvester
```

This starts PostGIS 17 / PostGIS 3.5, builds the image (pinned dependencies plus the BETA2007 grid) and runs the fixture harvest into `iris_staging`. Reports are written to `./output/fixture/`. Other commands:

```bash
docker compose run --rm harvester discover --config config.example.toml   # live capability check
docker compose run --rm harvester harvest --config tests/fixtures/config.fixture.toml --fixtures tests/fixtures/wfs
psql "postgresql://iris:<password>@localhost:5434/iris" -f sql/validation_queries.sql
```

### Local (Python 3.11+)

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/pip install --no-deps -e .
export IRIS_DATABASE_URL=postgresql://user:password@localhost:5432/iris

oek-harvester discover --config config.example.toml                       # live endpoint
oek-harvester harvest  --config tests/fixtures/config.fixture.toml \
                       --fixtures tests/fixtures/wfs                      # fixture demonstrator
oek-harvester harvest  --config tests/fixtures/config.fixture.toml \
                       --fixtures tests/fixtures/wfs --no-db              # reports only, no database
```

`harvest` creates the schema if needed (or run `oek-harvester init-db`). It exits with code 1 if the run failed. A failed run leaves the table untouched and is still recorded in `harvest_runs` and `harvest_errors`.

### Configuration

`config.example.toml` is the live configuration and `tests/fixtures/config.fixture.toml` is the demonstrator's. Secrets never live in config: the database URL is read from the environment variable named in `[database].url_env`.

## Schema mapping

Target table `iris_staging.compensation_sites`. DDL: [src/oek_harvester/sql/schema.sql](src/oek_harvester/sql/schema.sql). Primary key **(state_code, site_id)**.

| Target column | Source (fixture config) | Rule |
|---|---|---|
| `state_code` | config `harvest.state_code` | `RP` |
| `site_id` | `oek_id` → `OEK-<oek_id>` | Stable key. Falls back to `gml:id` only if no id attribute is configured. Features without an id are **rejected**, never keyed on geometry. |
| `source_feature_id`, `gml_id` | `oek_id`, `@gml:id` | kept for traceability |
| `site_name` | `oek_name` | whitespace/Unicode cleaned |
| `municipality_name` / `_key` | `gemeinde` | cleaned display name / casefolded match key (`ß`→`ss`); display name never rewritten |
| `municipality_code` | `gemeinde_schluessel` | official key (AGS), leading zeros kept; must start with `07` (RLP) or the site is rejected |
| `cadastral_district_name` / `_key` / `_code` | `gemarkung`, `gemarkung_nr` | as for municipality |
| `cadastral_parcel_refs` | `flurstuecke` | cleaned text, not parsed |
| `compensation_status` | `status` | mapped via `[mapping.status_values]` to `planned · active · partially_debited · fully_debited · closed`. Missing values become `unknown`, unlisted values become `unmapped`; the raw value is kept in `compensation_status_raw` |
| `area_m2` | geometry | ellipsoidal (GRS80) ground area of the accepted geometry |
| `source_area_raw` / `source_area_m2` | `flaeche_m2` | converted only if `source_area_unit` is configured; >5 % deviation from `area_m2` is flagged `area_mismatch` |
| `credits_total` / `_debited` / `_available`, `credit_unit` | `oekopunkte_*` | German/English number formats parsed per `decimal_style`; text such as "unbekannt" becomes NULL and is flagged; nothing is derived from other fields |
| `geom` | geometry | `MultiPolygon`, EPSG:25832, valid (enforced by a CHECK constraint) |
| `geom_source`, `source_srid` | geometry | original geometry in the requested source CRS (2D) |
| `geometry_outcome`, `quality_flags` | — | `valid`/`repaired`; per-site flags |
| `source_attributes` | all properties | verbatim JSON |
| `content_hash`, `first_seen_run_id`, `last_seen_run_id`, `created_at`, `updated_at` | — | change detection and lineage |

Supporting tables: `iris_staging.harvest_runs` (one row per run, with counts) and `iris_staging.harvest_errors` (every reject, warning and run error, with details).

## Quality rules

| Check | Outcome |
|---|---|
| No source id | reject `missing_source_id` |
| No geometry / unparseable GML / unknown CRS | reject `missing_geometry` / `geometry_parse_error` / `unknown_srs` |
| Point or line geometry | reject `non_polygon_geometry` |
| Invalid polygon, `make_valid` result polygonal and area change ≤ 1 % of the digitised ring area | accept as `repaired` (flag records the GEOS reason) |
| Invalid polygon where repair changes area > 1 % (e.g. a bowtie with zero ring area) | reject `repair_changes_area`: repair would invent geometry |
| Area < 1 m² | reject `tiny_area` |
| Representative point outside the RLP bounding box | reject `outside_state_extent`; partly outside gets flag `extends_outside_state_extent` |
| Municipality key not starting with `07` | reject `municipality_code_outside_state` |
| Z coordinates | dropped, flag `dropped_z` |
| Same id returned twice (e.g. across pages) | first kept, `duplicate_in_source` (or `conflicting_duplicate_in_source` if content differs) |
| Mapped attribute absent from DescribeFeatureType | run warning `mapped_attribute_absent` |
| `numberMatched` ≠ received | run warning `count_mismatch` |

Thresholds are configurable in `[quality]`.

**Idempotency.** Re-running the same input inserts nothing, updates nothing and only moves `last_seen_run_id`. A changed site is rewritten and gets a new `updated_at`. Sites missing from a run are counted (`db_existing_not_seen_this_run`) but never deleted, because a WFS outage must not wipe staging.

## Tests

```bash
pytest                                            # 62 unit tests; 4 PostGIS tests skip
IRIS_TEST_DATABASE_URL=postgresql://... pytest    # all 66; each DB test uses a throwaway schema
```

| Area | What is tested |
|---|---|
| `test_wfs.py` | capabilities parsing, layer lookup (the brief's `rp_oekokonto` is absent), DescribeFeatureType, paging stop conditions, `max_features` guard, OGC exception reports, retry on 503/429 with `Retry-After`, **no retry on 401/403/404**, DNS failure |
| `test_gml.py` | axis order for URN vs legacy CRS names, GK coordinate swap, `xsi:nil`, 3D posList, GML2 coordinates, malformed coordinate lists |
| `test_geometry.py` | projection against an independent 25832 transform, repair vs reject, threshold, tiny/outside/non-polygon, Z, holes of either winding, ellipsoidal area |
| `test_normalize.py` | NFC/whitespace, casefold keys, German/English numbers, refusal to parse text, status mapping, code formatting |
| `test_pipeline.py` | end-to-end counts by outcome, exact reject reasons, normalised values, provenance and transformation in report, unadvertised layer, absent mapping, deterministic hashes |
| `test_db.py` | re-run inserts 0 / updates 0 / unchanged 6; one changed attribute updates exactly 1 site; valid 25832 geometry, original SRID kept, stored area equals PostGIS geography area; failed load rolls back and is logged |

The fixtures are regenerated by `python tests/fixtures/make_fixtures.py`. Their attribute schema is **synthetic** (see the file header).

## Example results

From the fixture run on 2026-10-27 with `docker compose` (fresh volume, BETA2007 grid), run twice:

| | Run 1 | Run 2 |
|---|---:|---:|
| features received / numberMatched | 13 / 13 | 13 / 13 |
| accepted (valid + repaired) | 6 (5 + 1) | 6 (5 + 1) |
| rejected | 6 | 6 |
| duplicates in source | 1 | 1 |
| db inserted / updated / unchanged | 6 / 0 / 0 | 0 / 0 / 6 |

Output of `sql/validation_queries.sql`: [docs/evidence/validation_queries_output.txt](docs/evidence/validation_queries_output.txt). Full run report: [docs/evidence/example_run/](docs/evidence/example_run/). Quality/error report: [QUALITY_REPORT.md](QUALITY_REPORT.md).

## Assumptions and limitations

- **No live data.** All loaded records are fixtures. The real endpoint is unreachable (404) and the protected proxy was deliberately not bypassed.
- **Unknown real schema.** Attribute mapping, status vocabulary, area units and credit fields are unverified. The live config maps nothing until `discover` has shown the real DescribeFeatureType; raw attributes are always kept.
- **Identifier stability.** `site_id` is only as stable as the source identifier. If the real service only offers MapServer `gml:id`s, confirm that they come from a database key and not from row order.
- **State membership** uses a padded RLP bounding box plus the AGS prefix, not the exact state boundary. Sites in the bounding-box corners belonging to neighbouring states would only be caught by the AGS check.
- **Datum accuracy** depends on the BETA2007 grid (present in the Docker image; ~3 m Helmert otherwise). Switching between the two changes stored coordinates by up to a few metres, which shows up as `updated` rows in the next run.
- **Repairs** are limited to `make_valid` with an area-change guard. Slivers and overlaps *between* sites are not checked.
- **Deletions** in the source are reported, not propagated.
- Only the polygon layer `ms:oek_f` is harvested. Points/lines (`*_p`, `*_l`) belong to other registers (KOM/EIV).

## Repository layout

```
src/oek_harvester/   cli · config · wfs (transports, capabilities, paging) · gml (parser)
                     geometry · normalize · records · db · pipeline · report · sql/schema.sql
tests/               unit + PostGIS integration tests; fixtures/ (generator, WFS responses, config)
sql/                 validation_queries.sql
docs/                discovery.md, evidence/ (live checks, validation output, example run)
config.example.toml  live configuration (no secrets)
Dockerfile, docker-compose.yml, .env.example
requirements.txt     fully pinned runtime set · requirements-dev.txt adds pytest
```
