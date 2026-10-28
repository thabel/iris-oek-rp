import pytest
from shapely.geometry import MultiPolygon, Point, Polygon

from oek_harvester.gml import axis_order_is_yx, parse_feature_collection, srid_from_srs_name

GK2_URN = "urn:ogc:def:crs:EPSG::31466"


def fc(member_xml: str) -> bytes:
    return (
        '<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" '
        'xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:ms="http://ms" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<wfs:member>{member_xml}</wfs:member></wfs:FeatureCollection>"
    ).encode()


@pytest.mark.parametrize("srs,expected", [
    ("urn:ogc:def:crs:EPSG::31466", True),     # Gauss-Krueger: northing first
    ("http://www.opengis.net/def/crs/EPSG/0/4326", True),  # lat first
    ("urn:ogc:def:crs:EPSG::25832", False),    # UTM: easting first
    ("EPSG:31466", False),                     # legacy form: always x/y
    ("EPSG:4326", False),
])
def test_axis_order(srs, expected):
    assert axis_order_is_yx(srs) is expected


def test_srid_from_srs_name_forms():
    assert srid_from_srs_name(GK2_URN) == 31466
    assert srid_from_srs_name("EPSG:31467") == 31467
    assert srid_from_srs_name("http://www.opengis.net/gml/srs/epsg.xml#31466") == 31466


def test_urn_gk_coordinates_are_swapped_to_easting_northing():
    xml = fc(f'<ms:f gml:id="f.1"><ms:geom><gml:Polygon srsName="{GK2_URN}">'
             '<gml:exterior><gml:LinearRing><gml:posList>'
             '5513000 2547000 5513000 2547100 5513100 2547100 5513000 2547000'
             '</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></ms:geom></ms:f>')
    [feature] = parse_feature_collection(xml)
    x, y = feature.geometry.exterior.coords[0]
    assert (x, y) == (2547000, 5513000)
    assert feature.srs_name == GK2_URN


def test_legacy_epsg_form_is_not_swapped():
    xml = fc('<ms:f gml:id="f.1"><ms:geom><gml:Point srsName="EPSG:31466">'
             '<gml:pos>2547000 5513000</gml:pos></gml:Point></ms:geom></ms:f>')
    [feature] = parse_feature_collection(xml)
    assert isinstance(feature.geometry, Point)
    assert (feature.geometry.x, feature.geometry.y) == (2547000, 5513000)


def test_nil_and_empty_properties_become_none():
    xml = fc('<ms:f gml:id="f.1"><ms:a xsi:nil="true"/><ms:b></ms:b><ms:c> x </ms:c></ms:f>')
    [feature] = parse_feature_collection(xml)
    assert feature.properties == {"a": None, "b": None, "c": "x"}
    assert feature.geometry is None


def test_3d_poslist_drops_z_and_reports_it():
    xml = fc(f'<ms:f gml:id="f.1"><ms:geom><gml:Polygon srsName="{GK2_URN}" srsDimension="3">'
             '<gml:exterior><gml:LinearRing><gml:posList srsDimension="3">'
             '0 0 9 0 10 9 10 10 9 0 0 9</gml:posList></gml:LinearRing></gml:exterior>'
             '</gml:Polygon></ms:geom></ms:f>')
    [feature] = parse_feature_collection(xml)
    assert feature.had_z
    assert not feature.geometry.has_z
    assert feature.geometry.area == pytest.approx(50)


def test_gml2_coordinates_and_unsupported_dimension():
    ok = fc('<ms:f gml:id="f.1"><ms:geom><gml:Polygon srsName="EPSG:25832"><gml:outerBoundaryIs>'
            '<gml:LinearRing><gml:coordinates>0,0 10,0 10,10 0,0</gml:coordinates>'
            '</gml:LinearRing></gml:outerBoundaryIs></gml:Polygon></ms:geom></ms:f>')
    assert isinstance(parse_feature_collection(ok)[0].geometry, Polygon)
    bad = fc('<ms:f gml:id="f.1"><ms:geom><gml:Polygon srsName="EPSG:25832"><gml:exterior>'
             '<gml:LinearRing><gml:posList>0 0 10 0 10</gml:posList></gml:LinearRing>'
             '</gml:exterior></gml:Polygon></ms:geom></ms:f>')
    assert "not divisible" in parse_feature_collection(bad)[0].geometry_error


def test_fixture_pages_parse(fixture_dir):
    first = parse_feature_collection((fixture_dir / "getfeature_0.xml").read_bytes())
    assert [f.properties["oek_id"] for f in first] == ["1001", "1002", "1003", "1004", "1005"]
    assert isinstance(first[1].geometry, MultiPolygon)
    assert first[4].geometry is None
