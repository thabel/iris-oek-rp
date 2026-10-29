"""Regenerate the WFS fixture set in tests/fixtures/wfs/.

The fixtures imitate a MapServer WFS 2.0.0 endpoint for the RLP
Kompensationsverzeichnis (layer ``ms:oek_f``). What is modelled on the real
service (from the RLP geoportal registry, checked 2026-10-27):

* service title and abstract, provider SGD-Nord LANIS-Zentrale, licence ODbL
* layer names ms:eiv_f ... ms:oek_f, title "Oekokonto OEK Flaechen"
* advertised CRS: EPSG 31466, 31467, 3857, 4326, and the WGS84 extent
* MapServer as server software (inferred from the `mapfile=` parameter and
  the `ms:` prefix, not stated by the provider)

ASSUMED (plausible for MapServer WFS 2.0, not verified on the real service):
* 31466 as the DefaultCRS (the registry lists it first)
* ImplementsResultPaging TRUE and CountDefault 5000
* the Fees and AccessConstraints wording, and the outputFormat list

SYNTHETIC: the attribute names/types in DescribeFeatureType and all feature
values. The real schema could not be retrieved (origin returns 404), so these
must not be read as the real data model.

Coordinates are written in EPSG axis order for the URN srsName
(northing, easting), as WFS 2.0 servers do for urn:ogc:def:crs:EPSG::31466.

Run:  python tests/fixtures/make_fixtures.py
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from pyproj import Transformer

OUT = Path(__file__).parent / "wfs"
SRS = "urn:ogc:def:crs:EPSG::31466"
TO_GK2 = Transformer.from_crs(4326, 31466, always_xy=True)

# Attribute order = DescribeFeatureType sequence
ATTRS = [
    ("oek_id", "string"), ("oek_name", "string"), ("gemeinde", "string"),
    ("gemeinde_schluessel", "string"), ("gemarkung", "string"), ("gemarkung_nr", "string"),
    ("flurstuecke", "string"), ("status", "string"), ("flaeche_m2", "string"),
    ("oekopunkte_gesamt", "string"), ("oekopunkte_abgebucht", "string"),
    ("oekopunkte_verfuegbar", "string"),
]

SQUARE = [[(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)]]
# Small self-crossing lobe on the top edge: invalid, make_valid changes
# area by < 0.01 % -> defensible repair.
LOBE = [[(0, 0), (100, 0), (100, 100), (50, 100), (50, 99), (50.5, 100.5),
         (49.5, 100.5), (49, 100), (0, 100), (0, 0)]]
# Bowtie: digitised ring area is 0, repair would invent geometry -> reject.
BOWTIE = [[(0, 0), (100, 100), (100, 0), (0, 100), (0, 0)]]
HOLE = [[(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)],
        [(40, 40), (60, 40), (60, 60), (40, 60), (40, 40)]]
TINY = [[(0, 0), (0.7071, 0), (0.7071, 0.7071), (0, 0.7071), (0, 0)]]


def square_at(dx: float, dy: float, size: float = 60):
    return [[(dx, dy), (dx + size, dy), (dx + size, dy + size), (dx, dy + size), (dx, dy)]]


# (oek_id, lon, lat, geometry kind, rings/parts, attributes)
FEATURES = [
    # --- page 0 -----------------------------------------------------------
    ("1001", 6.66, 49.76, "polygon", SQUARE, dict(
        oek_name="Ökokonto Kürenzer Hang", gemeinde="Trier", gemeinde_schluessel="07211000",
        # decomposed umlaut (u + U+0308) -> NFC normalisation
        gemarkung="Kürenz", gemarkung_nr="2414", flurstuecke="Flur 3: 112/4, 113",
        status="eingerichtet", flaeche_m2="10.000", oekopunkte_gesamt="120.000",
        oekopunkte_abgebucht="20.000", oekopunkte_verfuegbar="100.000")),
    ("1002", 7.58, 50.35, "multipolygon", [square_at(0, 0), square_at(200, 0)], dict(
        oek_name="Streuobstwiese Moselweiß", gemeinde="Koblenz", gemeinde_schluessel="07111000",
        gemarkung="Moselweiß", gemarkung_nr="1305", flurstuecke="Flur 7: 45, 46",
        status="Teilweise abgebucht", flaeche_m2="7.200,0", oekopunkte_gesamt="45.000,5",
        oekopunkte_abgebucht="12.000", oekopunkte_verfuegbar="33.000,5")),
    ("1003", 8.25, 49.98, "polygon", LOBE, dict(
        oek_name="Auwald Laubenheim", gemeinde="Mainz", gemeinde_schluessel="07315000",
        gemarkung="Laubenheim", gemarkung_nr="3108", flurstuecke="Flur 12: 9",
        status="eingerichtet", flaeche_m2="10.000", oekopunkte_gesamt="80.000",
        oekopunkte_abgebucht="0", oekopunkte_verfuegbar="80.000")),
    ("1004", 7.77, 49.44, "polygon", BOWTIE, dict(
        oek_name="Bowtie-Fläche", gemeinde="Kaiserslautern", gemeinde_schluessel="07312000",
        status="eingerichtet", flaeche_m2="10.000")),
    ("1005", 8.43, 49.48, "none", None, dict(
        oek_name="Ohne Geometrie", gemeinde="Ludwigshafen am Rhein",
        gemeinde_schluessel="07314000", status="geplant")),
    # --- page 5 -----------------------------------------------------------
    ("1006", 8.36, 49.63, "point", None, dict(
        oek_name="Punktobjekt", gemeinde="Worms", gemeinde_schluessel="07319000",
        status="eingerichtet")),
    ("1007", 11.08, 49.45, "polygon", SQUARE, dict(
        oek_name="Fläche in Bayern", gemeinde="Nürnberg", gemeinde_schluessel="09564000",
        status="eingerichtet", flaeche_m2="10.000")),
    ("1008", 7.87, 49.84, "polygon", SQUARE, dict(
        oek_name="  Magerrasen   am  Kuhberg ", gemeinde="  Bad  Kreuznach ",
        gemeinde_schluessel="07133 006", gemarkung="BAD KREUZNACH", gemarkung_nr="0712",
        status="in Prüfung", flaeche_m2="10.000", oekopunkte_gesamt="unbekannt",
        oekopunkte_abgebucht="0", oekopunkte_verfuegbar="")),
    ("1009", 7.30, 49.58, "polygon", SQUARE, dict(
        oek_name="Feuchtwiese ohne Gemeinde", gemeinde=None, gemeinde_schluessel=None,
        status=None, flaeche_m2="1 ha")),
    ("1010", 7.05, 50.10, "polygon", TINY, dict(
        oek_name="Splitterfläche", gemeinde="Bernkastel-Kues", gemeinde_schluessel="07231009",
        status="eingerichtet", flaeche_m2="0,5")),
    # --- page 10 ----------------------------------------------------------
    ("1002", 7.58, 50.35, "multipolygon", [square_at(0, 0), square_at(200, 0)], dict(
        oek_name="Streuobstwiese Moselweiß", gemeinde="Koblenz", gemeinde_schluessel="07111000",
        gemarkung="Moselweiß", gemarkung_nr="1305", flurstuecke="Flur 7: 45, 46",
        status="Teilweise abgebucht", flaeche_m2="7.200,0", oekopunkte_gesamt="45.000,5",
        oekopunkte_abgebucht="12.000", oekopunkte_verfuegbar="33.000,5")),
    ("1011", 8.43, 49.32, "polygon3d", HOLE, dict(
        oek_name="Rheinauen mit Insel", gemeinde="Speyer", gemeinde_schluessel="07318000",
        gemarkung="Speyer", gemarkung_nr="3815", status="vollständig abgebucht",
        flaeche_m2="12.000", oekopunkte_gesamt="50.000", oekopunkte_abgebucht="50.000",
        oekopunkte_verfuegbar="0")),
    ("", 8.12, 49.20, "polygon", SQUARE, dict(
        oek_name="Ohne Kennung", gemeinde="Landau in der Pfalz", gemeinde_schluessel="07313000",
        status="eingerichtet", flaeche_m2="10.000")),
]

PAGES = {0: FEATURES[0:5], 5: FEATURES[5:10], 10: FEATURES[10:13]}

NS = ('xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:gml="http://www.opengis.net/gml/3.2" '
      'xmlns:ms="http://mapserver.gis.umn.edu/mapserver" '
      'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"')


def pos_list(ring, lon, lat, z=False):
    e0, n0 = TO_GK2.transform(lon, lat)
    out = []
    for dx, dy in ring:
        # EPSG axis order for 31466: northing first
        out += [f"{n0 + dy:.3f}", f"{e0 + dx:.3f}"] + (["250.0"] if z else [])
    return " ".join(out)


def polygon_xml(rings, lon, lat, gid, z=False):
    dim = ' srsDimension="3"' if z else ""
    ext = (f'<gml:exterior><gml:LinearRing><gml:posList{dim}>'
           f'{pos_list(rings[0], lon, lat, z)}</gml:posList></gml:LinearRing></gml:exterior>')
    ints = "".join(
        f'<gml:interior><gml:LinearRing><gml:posList{dim}>{pos_list(r, lon, lat, z)}'
        f'</gml:posList></gml:LinearRing></gml:interior>' for r in rings[1:])
    return f'<gml:Polygon gml:id="{gid}" srsName="{SRS}"{dim}>{ext}{ints}</gml:Polygon>'


def geometry_xml(kind, rings, lon, lat, gid):
    if kind == "none":
        return ""
    if kind == "point":
        e, n = TO_GK2.transform(lon, lat)
        g = f'<gml:Point gml:id="{gid}.g" srsName="{SRS}"><gml:pos>{n:.3f} {e:.3f}</gml:pos></gml:Point>'
    elif kind == "multipolygon":
        members = "".join(f"<gml:surfaceMember>{polygon_xml(p, lon, lat, f'{gid}.g{i}')}"
                          f"</gml:surfaceMember>" for i, p in enumerate(rings))
        g = f'<gml:MultiSurface gml:id="{gid}.g" srsName="{SRS}">{members}</gml:MultiSurface>'
    else:
        g = polygon_xml(rings, lon, lat, f"{gid}.g", z=(kind == "polygon3d"))
    return f"<ms:geom>{g}</ms:geom>"


def feature_xml(oek_id, lon, lat, kind, rings, attrs, n):
    gid = f"oek_f.{oek_id or f'noid{n}'}"
    props = [f"<ms:oek_id>{oek_id}</ms:oek_id>"]
    for name, _ in ATTRS[1:]:
        if name not in attrs:
            continue
        value = attrs[name]
        props.append(f'<ms:{name} xsi:nil="true"/>' if value is None
                     else f"<ms:{name}>{escape(value)}</ms:{name}>")
    return (f'<wfs:member><ms:oek_f gml:id="{gid}">'
            f'{geometry_xml(kind, rings, lon, lat, gid)}{"".join(props)}</ms:oek_f></wfs:member>')


def write_pages():
    for start, feats in PAGES.items():
        members = "".join(feature_xml(*f, n=start + i) for i, f in enumerate(feats))
        (OUT / f"getfeature_{start}.xml").write_text(
            f'<?xml version="1.0" encoding="UTF-8"?>\n<wfs:FeatureCollection {NS} '
            f'timeStamp="2026-10-27T12:00:00" numberMatched="{len(FEATURES)}" '
            f'numberReturned="{len(feats)}">{members}</wfs:FeatureCollection>\n',
            encoding="utf-8")
    (OUT / "hits.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<wfs:FeatureCollection {NS} '
        f'timeStamp="2026-10-27T12:00:00" numberMatched="{len(FEATURES)}" numberReturned="0"/>\n',
        encoding="utf-8")


def write_describe():
    elements = "\n".join(
        f'        <element name="{n}" minOccurs="0" maxOccurs="1" type="{t}" nillable="true"/>'
        for n, t in ATTRS)
    (OUT / "describefeaturetype.xsd").write_text(f'''<?xml version="1.0" encoding="UTF-8"?>
<!-- SYNTHETIC schema for tests; the real ms:oek_f schema was not retrievable. -->
<schema targetNamespace="http://mapserver.gis.umn.edu/mapserver"
        xmlns:ms="http://mapserver.gis.umn.edu/mapserver"
        xmlns="http://www.w3.org/2001/XMLSchema"
        xmlns:gml="http://www.opengis.net/gml/3.2"
        elementFormDefault="qualified" version="0.1">
  <import namespace="http://www.opengis.net/gml/3.2"
          schemaLocation="http://schemas.opengis.net/gml/3.2.1/gml.xsd"/>
  <element name="oek_f" type="ms:oek_fType" substitutionGroup="gml:AbstractFeature"/>
  <complexType name="oek_fType">
    <complexContent>
      <extension base="gml:AbstractFeatureType">
        <sequence>
        <element name="geom" type="gml:GeometryPropertyType" minOccurs="0" maxOccurs="1"/>
{elements}
        </sequence>
      </extension>
    </complexContent>
  </complexType>
</schema>
''', encoding="utf-8")


def write_capabilities():
    layers = [("eiv_f", "Eingriffsvorgang EIV Flaechen"), ("kom_f", "Kompensationsmassnahmen KOM Flaechen"),
              ("ema_f", "Massnahmen aus Ersatzzahlung EMA Flaechen nach altem Recht"),
              ("oek_f", "Oekokonto OEK Flaechen")]
    fts = "\n".join(f'''    <FeatureType>
      <Name>ms:{n}</Name>
      <Title>{t}</Title>
      <DefaultCRS>urn:ogc:def:crs:EPSG::31466</DefaultCRS>
      <OtherCRS>urn:ogc:def:crs:EPSG::31467</OtherCRS>
      <OtherCRS>urn:ogc:def:crs:EPSG::3857</OtherCRS>
      <OtherCRS>urn:ogc:def:crs:EPSG::4326</OtherCRS>
      <ows:WGS84BoundingBox dimensions="2">
        <ows:LowerCorner>6.0597466992742 48.9343735165295</ows:LowerCorner>
        <ows:UpperCorner>8.51290772937881 50.9474521355781</ows:UpperCorner>
      </ows:WGS84BoundingBox>
    </FeatureType>''' for n, t in layers)
    (OUT / "capabilities.xml").write_text(f'''<?xml version="1.0" encoding="UTF-8"?>
<!-- Test fixture modelled on the registered RLP Kompensationsverzeichnis WFS (geoportal.rlp.de id 536).
     Verified from the registry: service title/abstract, provider, layer names and titles, CRS list, extent.
     ASSUMED, not verified: DefaultCRS choice, ImplementsResultPaging, CountDefault, Fees and
     AccessConstraints wording, outputFormat list. See tests/fixtures/make_fixtures.py. -->
<wfs:WFS_Capabilities xmlns:wfs="http://www.opengis.net/wfs/2.0"
    xmlns:ows="http://www.opengis.net/ows/1.1" xmlns:fes="http://www.opengis.net/fes/2.0"
    xmlns:xlink="http://www.w3.org/1999/xlink" xmlns="http://www.opengis.net/wfs/2.0" version="2.0.0">
  <ows:ServiceIdentification>
    <ows:Title>Kompensationsverzeichnis WFS</ows:Title>
    <ows:Abstract>Ebenen aus dem Landeskompensationsverzeichnis: KOM EIV OEK EMA MAE</ows:Abstract>
    <ows:ServiceType codeSpace="OGC">WFS</ows:ServiceType>
    <ows:ServiceTypeVersion>2.0.0</ows:ServiceTypeVersion>
    <ows:Fees>keine</ows:Fees>
    <ows:AccessConstraints>Open Data Commons Open Database License (ODbL)</ows:AccessConstraints>
  </ows:ServiceIdentification>
  <ows:ServiceProvider>
    <ows:ProviderName>Struktur- und Genehmigungsdirektion Nord - LANIS-Zentrale</ows:ProviderName>
  </ows:ServiceProvider>
  <ows:OperationsMetadata>
    <ows:Operation name="GetCapabilities"/>
    <ows:Operation name="DescribeFeatureType"/>
    <ows:Operation name="GetFeature">
      <ows:Parameter name="resultType"><ows:AllowedValues><ows:Value>results</ows:Value><ows:Value>hits</ows:Value></ows:AllowedValues></ows:Parameter>
      <ows:Parameter name="outputFormat"><ows:AllowedValues>
        <ows:Value>application/gml+xml; version=3.2</ows:Value>
        <ows:Value>text/xml; subtype=gml/3.2.1</ows:Value>
      </ows:AllowedValues></ows:Parameter>
    </ows:Operation>
    <ows:Constraint name="ImplementsBasicWFS"><ows:NoValues/><ows:DefaultValue>TRUE</ows:DefaultValue></ows:Constraint>
    <ows:Constraint name="ImplementsResultPaging"><ows:NoValues/><ows:DefaultValue>TRUE</ows:DefaultValue></ows:Constraint>
    <ows:Constraint name="CountDefault"><ows:NoValues/><ows:DefaultValue>5000</ows:DefaultValue></ows:Constraint>
  </ows:OperationsMetadata>
  <FeatureTypeList>
{fts}
  </FeatureTypeList>
</wfs:WFS_Capabilities>
''', encoding="utf-8")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    write_capabilities()
    write_describe()
    write_pages()
    print(f"Wrote fixtures to {OUT}")
