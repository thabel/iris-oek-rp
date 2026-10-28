# Quality report — wsg:twsg_rvo (RP)

- Run: `not loaded` · mode **live** · status **succeeded**
- Window: 2026-10-28T21:47:11+00:00 → 2026-10-28T21:47:24+00:00
- Source: Geoportal Wasser RLP - Trinkwasserschutzgebiete (live control run) — `https://geodienste-wasser.rlp-umwelt.de/geoserver/wfs` (layer `wsg:twsg_rvo`)
- Licence: CC BY 4.0
- Request CRS: `urn:ogc:def:crs:EPSG::31466` → stored as `geom_source`; working geometry EPSG:25832
- Transformation: axis order change (2D) + Inverse of 3-degree Gauss-Kruger zone 2 + DHDN to ETRS89 (2) + UTM zone 32N (accuracy ~3.0 m)
  - More accurate operations not installed: Inverse of 3-degree Gauss-Kruger zone 2 + DHDN to ETRS89 (8) + UTM zone 32N

## Counts by processing outcome

| Outcome | Count |
|---|---:|
| accepted_repaired | 1 |
| accepted_total | 1412 |
| accepted_valid | 1411 |
| duplicates_in_source | 0 |
| features_received | 1412 |
| number_matched | 1412 |
| pages | 3 |
| rejected_total | 0 |

## Rejected features (0)

None.

## Warnings (accepted with flags)

| Code | Count |
|---|---:|
| `datum_grid_unavailable` | 1 |
| `geometry_repaired` | 1 |

## Attribute mapping as verified against DescribeFeatureType

| Target column | Source attribute |
|---|---|
| source_id | ID (xsd:decimal) |
| site_name | WSG_BEZ (xsd:string) |
