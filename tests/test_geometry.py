import pytest
from pyproj import Transformer
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box

from oek_harvester.geometry import ellipsoidal_area_m2, process_geometry

RP_BBOX = (6.05, 48.90, 8.55, 51.00)
TO_GK2 = Transformer.from_crs(4326, 31466, always_xy=True)
E0, N0 = TO_GK2.transform(7.0, 50.0)  # inside RLP


def run(geom, srid=31466, **kw):
    params = dict(max_repair_area_change=0.01, min_area_m2=1.0, state_bbox_wgs84=RP_BBOX)
    return process_geometry(geom, srid, **(params | kw))


def at(coords):
    return [(E0 + x, N0 + y) for x, y in coords]


def test_valid_polygon_projected_to_25832():
    result = run(Polygon(at([(0, 0), (100, 0), (100, 100), (0, 100)])))
    assert result.outcome == "valid"
    assert isinstance(result.geom_target, MultiPolygon)
    assert result.geom_target.is_valid
    # Same lon/lat expressed directly in 25832 must match the reprojected corner.
    ex, ny = Transformer.from_crs(4326, 25832, always_xy=True).transform(7.0, 50.0)
    x0, y0 = result.geom_target.geoms[0].exterior.coords[0]
    assert abs(x0 - ex) < 5 and abs(y0 - ny) < 5   # DHDN->ETRS89 Helmert: metre level
    assert result.area_m2 == pytest.approx(10_000, rel=0.002)


def test_small_self_intersection_is_repaired():
    lobe = at([(0, 0), (100, 0), (100, 100), (50, 100), (50, 99), (50.5, 100.5),
               (49.5, 100.5), (49, 100), (0, 100), (0, 0)])
    result = run(Polygon(lobe))
    assert result.outcome == "repaired"
    assert result.geom_target.is_valid
    assert any(f.startswith("repaired:") for f in result.flags)


def test_bowtie_is_rejected_not_invented():
    result = run(Polygon(at([(0, 0), (100, 100), (100, 0), (0, 100), (0, 0)])))
    assert (result.outcome, result.reject_code) == ("rejected", "repair_changes_area")


def test_repair_threshold_is_configurable():
    lobe = Polygon(at([(0, 0), (100, 0), (100, 100), (50, 100), (50, 99), (50.5, 100.5),
                       (49.5, 100.5), (49, 100), (0, 100), (0, 0)]))
    assert run(lobe, max_repair_area_change=0.0).reject_code == "repair_changes_area"


@pytest.mark.parametrize("geom,code", [
    (None, "missing_geometry"),
    (Polygon(), "missing_geometry"),
    (Point(E0, N0), "non_polygon_geometry"),
    (LineString([(E0, N0), (E0 + 5, N0)]), "non_polygon_geometry"),
])
def test_rejections(geom, code):
    assert run(geom).reject_code == code


def test_tiny_area_rejected():
    assert run(box(E0, N0, E0 + 0.5, N0 + 0.5)).reject_code == "tiny_area"


def test_outside_state_rejected():
    e, n = TO_GK2.transform(11.08, 49.45)  # Nuremberg
    assert run(box(e, n, e + 100, n + 100)).reject_code == "outside_state_extent"


def test_z_is_dropped_and_flagged():
    poly = Polygon([(x, y, 250.0) for x, y in at([(0, 0), (50, 0), (50, 50), (0, 50)])])
    result = run(poly)
    assert result.outcome == "valid"
    assert "dropped_z" in result.flags
    assert not result.geom_target.has_z


@pytest.mark.parametrize("hole", [
    [(40, 40), (60, 40), (60, 60), (40, 60)],   # counter-clockwise, same as shell
    [(40, 40), (40, 60), (60, 60), (60, 40)],   # clockwise
])
def test_holes_are_subtracted_whatever_their_winding(hole):
    x, y = Transformer.from_crs(4326, 25832, always_xy=True).transform(9.0, 50.0)
    shell = [(x, y), (x + 100, y), (x + 100, y + 100), (x, y + 100)]
    poly = Polygon(shell, [[(x + a, y + b) for a, b in hole]])
    assert ellipsoidal_area_m2(poly) == pytest.approx(9_600 / 0.9996**2, rel=1e-4)


def test_regional_grids_are_never_applied_to_the_whole_state():
    from oek_harvester.geometry import transformation_info, transformer_for

    aou = transformer_for(31466, RP_BBOX).area_of_use
    assert aou.west <= RP_BBOX[0] and aou.south <= RP_BBOX[1]
    assert aou.east >= RP_BBOX[2] and aou.north >= RP_BBOX[3]
    info = transformation_info(31466, RP_BBOX)
    # The Saarland-only SeTa2016 grid must not be reported as a missing upgrade
    assert not any("(10)" in op for op in info["better_operations_unavailable"])


def test_ellipsoidal_area_close_to_planar_utm():
    x, y = Transformer.from_crs(4326, 25832, always_xy=True).transform(9.0, 50.0)
    square = box(x, y, x + 100, y + 100)
    # On the central meridian UTM scale is 0.9996 -> ground area is 1/0.9996^2 larger
    assert ellipsoidal_area_m2(square) == pytest.approx(10_000 / 0.9996**2, rel=1e-4)
