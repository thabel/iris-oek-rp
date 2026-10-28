# Source discovery — 2026-10-27

The brief asked for the WFS capabilities, layer names, permissions and schema to be checked before use. This is what those checks found. Raw outputs are in `docs/evidence/`.

## 1. Endpoint and layer named in the brief

| Brief | Result |
|---|---|
| `https://geodaten-wasser.rlp.de/wfs/kompensation` | Host has **no DNS address record** (`getaddrinfo failed`; `nslookup` against the local resolver and 8.8.8.8 returns the name without an address). Evidence: `evidence/brief_endpoint_discover_2026-10-27.json`. |
| Layer `rp_oekokonto` | Not advertised by any RLP WFS found in the state geoportal catalogue. |

## 2. Service actually registered for RLP compensation/eco-account data

The RLP geoportal catalogue (`geoportal.rlp.de`, search API `mod_callMetadata.php`, keyword "Kompensation", resource type `wfs`) lists one matching service:

| Field | Value |
|---|---|
| Service | **Kompensationsverzeichnis WFS**, geoportal id 536 |
| Abstract | "Ebenen aus dem Landeskompensationsverzeichnis: KOM EIV OEK EMA MAE" |
| Responsible | Struktur- und Genehmigungsdirektion Nord – LANIS-Zentrale (technical contact: KSP-Servicestelle, SGD Nord, Koblenz) |
| Licence | Open Data Commons Open Database License (ODbL 1.0) |
| Registered URL | `https://geodaten.naturschutz.rlp.de/kartendienste_naturschutz/mod_ogc/wfs_getmap.php?mapfile=komon` (MapServer) |
| Feature types | `ms:eiv_f/l/p` (Eingriffe), `ms:kom_f/l/p` (Kompensationsmaßnahmen), `ms:ema_f` (Ersatzzahlung), **`ms:oek_f` — "Oekokonto OEK Flaechen"** (geoportal feature type id 3008) |
| CRS advertised for `ms:oek_f` | EPSG **31466** (DHDN / GK zone 2), 31467 (GK zone 3), 3857, 4326. **EPSG:25832 is not offered**, so the harvester must reproject. |
| WGS84 extent | 6.0597, 48.9344 – 8.5129, 50.9475 |
| Registry status | last capabilities update 2023-11-28 (1064 days before this check); monitored availability 88.17 % |

`ms:oek_f` is the eco-account layer, so it is the correct match for the brief's domain ("registered offsetting sites"). The harvester is configured for it.

A newer dataset record, **"Ökokonten OEK"** (uuid `d8c45a2a-5bd0-5c66-94e3-1fbc7f262ed9`, dated 2026-09-17, ODbL, daily update, period 2012-07-01 to today), points only to the KSP web application `https://ksp.naturschutz.rlp.de`. It names no download service.

## 3. Access test

| Request | Result |
|---|---|
| Registered URL `…?mapfile=komon&SERVICE=WFS&REQUEST=GetCapabilities&VERSION=2.0.0` | `301` → `https://geoportal.naturschutz.rlp.de/kartendienste_naturschutz/mod_ogc/wfs_getmap.php?...` → **HTTP 404** (Apache). Evidence: `evidence/live_discover_2026-10-27.json`. |
| Geoportal proxy `https://www.geoportal.rlp.de/registry/wfs/536?REQUEST=GetCapabilities` | Routed through the geoportal's `http_auth` gateway; returns `<error>http_auth/index.php: Cannot parse Metadata XML!</error>`. The catalogue marks the service `hasConstraints: true`. |
| WMS of the same register (geoportal id 3082, updated 2026-09-17) | Registered at the same `geodaten.naturschutz.rlp.de` origin, so it lands on the same 404. |
| Geoportal **OGC API – Features** proxy `https://www.geoportal.rlp.de/spatial-objects/536` (labelled "experimental"; checked 2026-10-28) | Service and collection listings work (HTTP 200) and list all 8 layers, including `ms:oek_f`. Every request for a layer's details or **features returns HTTP 500 with an empty body**: `ms:oek_f/items?f=json&limit=1` and `ms:kom_f/items` alike, from the candidate's network and from an independent server. The proxy's own description names the dead origin as its source (`"accessUrl": "https://geodaten.naturschutz.rlp.de/...?mapfile=komon&"`), so it fails for the same reason. Evidence: `evidence/ogcapi_spatial_objects_536_2026-10-28.txt`, `evidence/ogcapi_536_collections_2026-10-28.json`. |
| Every request documented in that API's OpenAPI description (`/spatial-objects/536/api`, OpenAPI 3.0.1), for `ms:oek_f` | `resultType=hits`, `f=html` and a `bbox` filter all return **HTTP 500, empty**. `/items/{id}` returns HTTP 200 but only an **empty shell** (title and links, no geometry, no properties), identically for real-looking ids (`1`, `2`, `100`) and an invented one (`oek_f.1`). `/items/1?f=xml` passes through the origin's own Apache **"404 Not Found"** page. `/conformance`, although listed in the description, answers "URI not valid!". Evidence: `evidence/ogcapi_536_api_checks_2026-10-28.txt`, `evidence/ogcapi_536_openapi_2026-10-28.json`. |

The geoportal's address without `www` (`https://geoportal.rlp.de/`) fails with a TLS certificate name mismatch; use `https://www.geoportal.rlp.de/`.

**Conclusion.** There is no working, openly published WFS or OGC API endpoint for the RLP eco-account layer today. In line with the brief ("do not bypass restrictions"), the harvester does not try undocumented URLs, the authenticated proxy or the KSP web app. Instead:

* The live code path is fully implemented and configured for the registered endpoint and layer. `oek-harvester discover --config config.example.toml` reports the 404 and makes no further requests.
* A fixture-based demonstrator exercises the same discovery, paging, parsing and loading code. Where the real service could be checked, the fixtures copy it (layer names, CRS list, licence, provider, MapServer-style WFS 2.0 responses with northing-first coordinates for the GK URN).

## 4. What could not be verified

* **The attribute schema of `ms:oek_f`.** DescribeFeatureType is unreachable and the geoportal does not store it. The OGC API's OpenAPI description does not fill the gap: its feature schema is generic GeoJSON (`type`, `id`, `geometry` of type Polygon/MultiPolygon/…, and `properties` declared only as `{"type": "object", "nullable": true}`), with no attribute names for any layer. Its examples are the OGC template's "Buildings in the city of Bonn", and the response descriptions of `/items` and `/items/{featureId}` are swapped, so it is an auto-generated description, not a data model. The fixture schema (`oek_id`, `gemeinde`, `status`, `oekopunkte_*`, …) is **synthetic**. The live config therefore maps no attributes: every source attribute is kept verbatim in `source_attributes` until the real schema has been checked.
* The service's real status vocabulary, area units and credit (Ökopunkte) fields.
* Whether MapServer's `gml:id` values for this layer come from a stable database key. The harvester prefers a configured identifier attribute and falls back to `gml:id`.
* Paging support (`ImplementsResultPaging`) and `CountDefault` on the live service. The harvester reads both from capabilities and also handles services without paging.

## 5. How to unblock live access

If SGD Nord restores the origin, the OGC API proxy above (GeoJSON, `limit` restricted to 1, 5, 10, 20, 50, 100, 200, 500, 1000–4000) would likely work again and could be added as a second transport. The WFS path below remains the primary one.

Ask the KSP-Servicestelle (SGD Nord) for the current WFS URL of the Kompensationsverzeichnis, or for access to the protected geoportal proxy. Then:

```bash
oek-harvester discover --config config.toml     # verify layer, CRS, paging, schema
# fill [mapping.fields] and [mapping.status_values] from the discovered schema
oek-harvester harvest --config config.toml
```
