# Quality report — ms:oek_f (RP)

- Run: `80049fcd-8434-4501-af73-24c87a797ce0` · mode **fixture** · status **succeeded**
- Window: 2026-10-29T11:40:30+00:00 → 2026-10-29T11:40:30+00:00
- Source: LANIS Kompensationsverzeichnis RLP - Oekokonto OEK (fixture) — `https://geodaten.naturschutz.rlp.de/kartendienste_naturschutz/mod_ogc/wfs_getmap.php?mapfile=komon` (layer `ms:oek_f`)
- Licence: ODbL-1.0 (Open Data Commons Open Database License)
- Request CRS: `urn:ogc:def:crs:EPSG::31466` → stored as `geom_source`; working geometry EPSG:25832
- Transformation: axis order change (2D) + Inverse of 3-degree Gauss-Kruger zone 2 + DHDN to ETRS89 (8) + UTM zone 32N (accuracy ~0.9 m)

## Counts by processing outcome

| Outcome | Count |
|---|---:|
| accepted_repaired | 1 |
| accepted_total | 6 |
| accepted_valid | 5 |
| db_existing_not_seen_this_run | 0 |
| db_inserted | 0 |
| db_unchanged | 6 |
| db_updated | 0 |
| duplicates_in_source | 1 |
| features_received | 13 |
| number_matched | 13 |
| pages | 3 |
| rejected_missing_geometry | 1 |
| rejected_missing_source_id | 1 |
| rejected_non_polygon_geometry | 1 |
| rejected_outside_state_extent | 1 |
| rejected_repair_changes_area | 1 |
| rejected_tiny_area | 1 |
| rejected_total | 6 |

## Rejected features (6)

| Source id | Code | Detail |
|---|---|---|
| 1004 | `repair_changes_area` | Self-intersection[2628459.896 5479680.002]; digitised ring area is 0 |
| 1005 | `missing_geometry` |  |
| 1006 | `non_polygon_geometry` | Point |
| 1007 | `outside_state_extent` | representative point outside state bounding box |
| 1010 | `tiny_area` | 0.500 m2 < 1.0 m2 |
| oek_f.noid12 | `missing_source_id` | No stable source identifier; a key derived from geometry or attributes would not survive source edits, so the feature is not loaded |

## Warnings (accepted with flags)

| Code | Count |
|---|---:|
| `area_mismatch` | 1 |
| `duplicate_in_source` | 1 |
| `geometry_repaired` | 1 |
| `missing_municipality` | 1 |
| `missing_status` | 1 |
| `unmapped_status` | 1 |
| `unparseable_number` | 2 |

## Attribute mapping as verified against DescribeFeatureType

| Target column | Source attribute |
|---|---|
| source_id | oek_id (string) |
| site_name | oek_name (string) |
| municipality_name | gemeinde (string) |
| municipality_code | gemeinde_schluessel (string) |
| cadastral_district_name | gemarkung (string) |
| cadastral_district_code | gemarkung_nr (string) |
| cadastral_parcel_refs | flurstuecke (string) |
| compensation_status | status (string) |
| source_area | flaeche_m2 (string) |
| credits_total | oekopunkte_gesamt (string) |
| credits_debited | oekopunkte_abgebucht (string) |
| credits_available | oekopunkte_verfuegbar (string) |
