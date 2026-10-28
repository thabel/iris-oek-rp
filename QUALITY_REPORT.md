# Quality and error report — IRIS-OEK-RP

**Date:** 2026-10-27 · **Mode:** fixture demonstrator (no live access) · **Target:** `iris_staging.compensation_sites`

## 1. Source access

| Finding | Severity | Evidence |
|---|---|---|
| The brief's endpoint `geodaten-wasser.rlp.de` has no DNS address record | blocking | `docs/evidence/brief_endpoint_discover_2026-10-27.json` |
| The brief's layer `rp_oekokonto` is not advertised anywhere; the eco-account layer is `ms:oek_f` | corrected in config | geoportal.rlp.de feature type 3008 |
| The registered WFS (geoportal id 536) redirects to a host that returns HTTP 404 | blocking | `docs/evidence/live_discover_2026-10-27.json` |
| The geoportal proxy for the WFS sits behind `http_auth` (`hasConstraints: true`) | not bypassed | `docs/discovery.md` §3 |
| The geoportal's OGC API – Features proxy (`/spatial-objects/536`) lists `ms:oek_f`, but no documented request returns data: lists, counts and area filters return HTTP 500, single features return empty shells, and the XML variant passes through the origin's 404 page | blocking | `docs/evidence/ogcapi_spatial_objects_536_2026-10-28.txt`, `docs/evidence/ogcapi_536_api_checks_2026-10-28.txt` |
| The OGC API's OpenAPI description defines `properties` only as a generic object, so it does not reveal the real attribute schema | open | `docs/evidence/ogcapi_536_openapi_2026-10-28.json` |
| The service offers no EPSG:25832 (31466/31467/3857/4326 only) | handled: reprojection | `docs/discovery.md` §2 |
| Without the BETA2007 grid, DHDN→ETRS89 uses Helmert with ~3 m accuracy | warning `datum_grid_unavailable`; the Docker image installs the grid | `run_report.json` → `transformation` |

## 2. Counts by processing outcome (fixture run)

| Outcome | Count |
|---|---:|
| numberMatched (hits) | 13 |
| features received (3 pages of 5/5/3) | 13 |
| accepted — valid | 5 |
| accepted — repaired | 1 |
| rejected | 6 |
| duplicate in source (dropped) | 1 |
| **Check:** 5 + 1 + 6 + 1 | **13** ✔ |
| DB run 1: inserted / updated / unchanged | 6 / 0 / 0 |
| DB run 2 (same input): inserted / updated / unchanged | 0 / 0 / 6 |

## 3. Rejected data

| Source id | Code | Why it is not loaded |
|---|---|---|
| 1004 | `repair_changes_area` | Bowtie ring with zero digitised area. `make_valid` would invent two triangles, so it is not repaired. |
| 1005 | `missing_geometry` | No geometry property |
| 1006 | `non_polygon_geometry` | Point in a polygon layer |
| 1007 | `outside_state_extent` | Located in Nuremberg (Bavaria); its AGS 09564000 is not RLP either |
| 1010 | `tiny_area` | 0.5 m², below the 1 m² floor (digitising splinter) |
| `oek_f.noid12` | `missing_source_id` | Empty `oek_id`. A geometry-derived key would not survive edits, so the feature is not keyed. |

Every reject is stored in `iris_staging.harvest_errors` with the full source properties in `details`.

## 4. Accepted with flags (edge cases)

| Site | Flags | Handling |
|---|---|---|
| OEK-1003 | `repaired:Self-intersection` | Small self-crossing lobe. Repair changes area by < 0.01 %. |
| OEK-1002 | `multipart` | MultiSurface with two parts. A second, identical copy on page 3 was counted as a duplicate. |
| OEK-1008 | `unmapped_status`, `unparseable_number:credits_total`, `missing_credits_available` | Status "in Prüfung" has no configured mapping, so it is loaded as `unmapped` with the raw value kept. Credits "unbekannt" become NULL, not 0. NBSP/extra spaces removed from "  Bad  Kreuznach ". Key "07133 006" becomes "07133006". |
| OEK-1009 | `missing_municipality`, `missing_status`, `unparseable_number:source_area`, `missing_credits_*` | `xsi:nil` attributes stay NULL. Status becomes `unknown`. "1 ha" is not converted because the configured unit is m². |
| OEK-1011 | `area_mismatch`, `dropped_z` | Source says 12,000 m², geometry is 9,593 m² (a polygon with a hole): reported, not overwritten. 3D posList reduced to 2D. |
| OEK-1001 | — | Decomposed umlaut "Kürenz" normalised to NFC "Kürenz" |

## 5. Evidence for acceptance

| Criterion | Evidence |
|---|---|
| Correct state | All rows `state_code = 'RP'`; bounding-box and AGS-prefix checks; Bavarian site rejected (validation query 2, test `test_outside_state_rejected`) |
| Correct layer/domain | `source_layer = 'ms:oek_f'` on all rows; an unadvertised layer fails the run (`test_unadvertised_layer_fails_cleanly`) |
| Provenance kept | `source_*` columns, `gml_id`, verbatim `source_attributes`, run lineage |
| Valid projected geometry | `CHECK (ST_IsValid(geom))`, type `MultiPolygon(25832)`; query 2: `all_valid = t`, `working_srid = 25832` |
| Original CRS kept | `geom_source` with `source_srid = 31466`, enforced by `CHECK (ST_SRID(geom_source) = source_srid)` |
| Correct positions | Reprojected corner within 5 m of an independent lon/lat→25832 transform (`test_valid_polygon_projected_to_25832`); the sample export lands on the generating coordinates (Trier 6.66 E / 49.76 N, …) |
| Correct areas | `area_m2` agrees with PostGIS `ST_Area(geography)` to 0.1 m² on all sites (query 3; `test_geometry_and_provenance_stored`) |
| No duplicates on re-run | Run 2: 0 inserted / 0 updated / 6 unchanged; query 1 returns 0 rows (`test_rerun_is_idempotent`) |
| Changes detected | One changed status leads to exactly 1 update (`test_changed_source_updates_only_that_site`) |
| Transactional | A forced mid-load failure leaves the table unchanged and logs `run_failed` (`test_failed_load_rolls_back_and_is_logged`) |
| Quality issues reported | `harvest_errors` (6 rejects, warnings), `quality_report.md`, `issues.jsonl` per run |

Test suite: **67 passed** (63 unit + 4 PostGIS integration tests against PostGIS 17 / 3.5).

**Live control run (2026-10-28).** The unchanged harvester ran against a working RLP WFS: the water GeoServer, layer `wsg:twsg_rvo`, run with `--no-db` because it is not compensation data.
- It discovered paging and read the real schema.
- It retrieved 1,412 polygons in 6 requests: 1,411 valid, 1 repaired, 0 rejected.
- Requested in EPSG:31466 and reprojected, the zones match the server's own EPSG:25832 output within 0.61–1.31 m, with 0.0002 % area difference.
- Evidence: `docs/evidence/live_control_water_2026-10-28/`. The `docker compose` stack was also run end to end from an empty volume: run 1 inserted 6 / run 2 left 6 unchanged, using the BETA2007 grid (operation "DHDN to ETRS89 (8)", 0.9 m). The validation output in `docs/evidence/` comes from that stack.

## 6. Defects found and fixed during development

| Defect | Detected by | Fix |
|---|---|---|
| Polygon holes were *added* to the area when wound the same way as the shell (pyproj sums signed ring areas) | Cross-check against PostGIS geography area (7.7 % off on OEK-1011) | Orient rings before measuring; regression test for both windings |
| Area was planar UTM area (off by up to 0.1 % from ground area) | Comparison with source areas | `area_m2` is now GRS80 ellipsoidal area |
| PROJ chose the DHDN→ETRS89 operation per call and could not report which one | Run report showed "unavailable until proj_trans is called" | Pin the best *available* operation, report it with accuracy and list better ones that are missing |
| With non-autocommit connections a prior query turned `transaction()` into a savepoint, so the failure-path rollback erased the run record | Rollback integration test | Autocommit connection; every write block is its own transaction |
| "Most accurate operation" would pick a Saarland-only grid (SeTa2016, 0.01 m) once installed and apply it to all of RLP | Inspecting unavailable operations inside the Docker image | Only operations whose area of use covers the whole state extent are eligible; regression test |
| Compose healthcheck passed during the image's socket-only init phase, so the harvester got "connection refused" | First `docker compose up` on an empty volume | `pg_isready -h 127.0.0.1` (TCP) |
| A missing status was flagged on every site even when no status field was configured (1412 warnings in one run) | Live control run | Status stays `unknown` without per-site warnings unless the status field is mapped; regression test |

## 7. Open items before production use

1. Get the current WFS URL or proxy access from KSP-Servicestelle, SGD Nord.
2. Run `discover`, then set `[mapping.fields]`, `[mapping.status_values]`, the area unit and credit fields from the real schema and values.
3. Confirm that the source identifier is stable across service updates.
4. Optionally replace the bounding-box state check with the official RLP boundary (VG250).
