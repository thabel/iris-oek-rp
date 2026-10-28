"""Parse WFS GetFeature responses (GML 3.1/3.2) into raw features.

Geometry is parsed directly with lxml rather than through GDAL so that axis
order is explicit: WFS 2.0 servers write coordinates in the axis order of
the CRS as defined by EPSG when the srsName is a URN/URI
(``urn:ogc:def:crs:EPSG::31466`` is northing, easting), but in x/y order for
the legacy ``EPSG:31466`` form. All geometries returned here are in
easting/northing (x, y) order, which is what Shapely and GeoPandas expect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from lxml import etree
from pyproj import CRS
from shapely.geometry import (
    LinearRing,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

GML_NAMESPACES = {"http://www.opengis.net/gml/3.2", "http://www.opengis.net/gml"}
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"


class GmlError(ValueError):
    pass


@dataclass
class RawFeature:
    feature_id: str | None  # gml:id
    properties: dict[str, str | None]
    geometry: BaseGeometry | None
    srs_name: str | None
    geometry_error: str | None = None
    had_z: bool = False
    raw_xml_snippet: str = field(default="", repr=False)


def _local(el: etree._Element) -> str:
    return etree.QName(el).localname


def _is_gml(el: etree._Element) -> bool:
    return etree.QName(el).namespace in GML_NAMESPACES


def srid_from_srs_name(srs_name: str) -> int:
    crs = CRS.from_user_input(_normalise_srs(srs_name))
    epsg = crs.to_epsg()
    if epsg is None:
        raise GmlError(f"Cannot determine EPSG code for {srs_name!r}")
    return epsg


def _normalise_srs(srs_name: str) -> str:
    # http://www.opengis.net/gml/srs/epsg.xml#31466 (GML2 style)
    if "epsg.xml#" in srs_name:
        return "EPSG:" + srs_name.split("#")[-1]
    return srs_name


@lru_cache(maxsize=32)
def axis_order_is_yx(srs_name: str) -> bool:
    """True when coordinates in this srsName are written northing/lat first."""
    lowered = srs_name.lower()
    authority_order = lowered.startswith("urn:ogc:def:crs") or "opengis.net/def/crs" in lowered
    if not authority_order:
        return False  # EPSG:xxxx and epsg.xml#xxxx use traditional x/y order
    crs = CRS.from_user_input(srs_name)
    first = crs.axis_info[0].direction.lower() if crs.axis_info else "east"
    return first in ("north", "south")


def _coords(el: etree._Element, srs_name: str | None, dim_hint: int | None) -> tuple[list[tuple[float, float]], bool]:
    """Return 2D (x, y) coordinate tuples for a ring/line element and
    whether a Z ordinate was dropped."""
    pos_list = el.find("{*}posList")
    values: list[float]
    dim = dim_hint
    if pos_list is not None:
        dim = int(pos_list.get("srsDimension") or el.get("srsDimension") or dim or 2)
        values = [float(v) for v in (pos_list.text or "").split()]
    else:
        pos = el.findall("{*}pos")
        if pos:
            dim = int(pos[0].get("srsDimension") or dim or len((pos[0].text or "").split()) or 2)
            values = [float(v) for p in pos for v in (p.text or "").split()]
        else:
            coordinates = el.find("{*}coordinates")
            if coordinates is None:
                raise GmlError(f"No coordinates in <{_local(el)}>")
            tuples = [c.split(",") for c in (coordinates.text or "").split()]
            dim = len(tuples[0]) if tuples else 2
            values = [float(v) for t in tuples for v in t]
    if dim not in (2, 3):
        raise GmlError(f"Unsupported srsDimension {dim}")
    if len(values) % dim:
        raise GmlError(f"Coordinate count {len(values)} not divisible by dimension {dim}")
    pairs = [(values[i], values[i + 1]) for i in range(0, len(values), dim)]
    if srs_name and axis_order_is_yx(srs_name):
        pairs = [(b, a) for a, b in pairs]
    return pairs, dim == 3


def _polygon(el: etree._Element, srs: str | None, dim: int | None) -> tuple[Polygon, bool]:
    had_z = False
    rings: list[list[tuple[float, float]]] = []
    for boundary in ("exterior", "outerBoundaryIs", "interior", "innerBoundaryIs"):
        for b in el.findall(f"{{*}}{boundary}"):
            ring = b.find("{*}LinearRing")
            if ring is None:
                raise GmlError(f"<{boundary}> without LinearRing")
            pts, z = _coords(ring, srs, dim)
            had_z |= z
            if boundary in ("exterior", "outerBoundaryIs"):
                rings.insert(0, pts)
            else:
                rings.append(pts)
    if not rings:
        raise GmlError("Polygon without exterior ring")
    for pts in rings:
        if len(pts) < 4:
            raise GmlError(f"Ring with {len(pts)} points (need >= 4)")
    return Polygon(rings[0], rings[1:]), had_z


def parse_geometry(el: etree._Element, inherited_srs: str | None = None) -> tuple[BaseGeometry, str | None, bool]:
    """Parse one GML geometry element -> (geometry, srsName, had_z)."""
    srs = el.get("srsName") or inherited_srs
    dim = int(el.get("srsDimension")) if el.get("srsDimension") else None
    name = _local(el)

    if name == "Polygon":
        poly, z = _polygon(el, srs, dim)
        return poly, srs, z
    if name == "Surface":
        polys, had_z = [], False
        for patch in el.iter("{*}PolygonPatch"):
            p, z = _polygon(patch, srs, dim)
            polys.append(p)
            had_z |= z
        if len(polys) == 1:
            return polys[0], srs, had_z
        return MultiPolygon(polys), srs, had_z
    if name in ("MultiSurface", "MultiPolygon", "CompositeSurface"):
        polys, had_z = [], False
        for member in el:
            if _local(member) not in ("surfaceMember", "surfaceMembers", "polygonMember", "polygonMembers"):
                continue
            for child in member:
                g, _, z = parse_geometry(child, srs)
                had_z |= z
                polys.extend(g.geoms if isinstance(g, MultiPolygon) else [g])
        return MultiPolygon(polys), srs, had_z
    if name == "Point":
        pts, z = _coords(el, srs, dim)
        return Point(pts[0]), srs, z
    if name in ("LineString", "Curve"):
        target = el if name == "LineString" else next(el.iter("{*}LineStringSegment"), el)
        pts, z = _coords(target, srs, dim)
        return LineString(pts), srs, z
    if name == "LinearRing":
        pts, z = _coords(el, srs, dim)
        return LinearRing(pts), srs, z
    if name in ("MultiPoint", "MultiCurve", "MultiLineString"):
        parts, had_z = [], False
        for member in el:
            for child in member:
                g, _, z = parse_geometry(child, srs)
                parts.append(g)
                had_z |= z
        cls = MultiPoint if name == "MultiPoint" else MultiLineString
        return cls(parts), srs, had_z
    raise GmlError(f"Unsupported GML geometry <{name}>")


def _feature_elements(root: etree._Element) -> list[etree._Element]:
    features = []
    for member in root:
        if _local(member) in ("member", "featureMember"):
            features.extend(child for child in member if isinstance(child.tag, str))
        elif _local(member) == "featureMembers":
            features.extend(child for child in member if isinstance(child.tag, str))
    return features


def parse_feature_collection(content: bytes, default_srs: str | None = None) -> list[RawFeature]:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    root = etree.fromstring(content, parser)
    features: list[RawFeature] = []
    for feature in _feature_elements(root):
        fid = (feature.get("{http://www.opengis.net/gml/3.2}id")
               or feature.get("{http://www.opengis.net/gml}id")
               or feature.get("fid"))
        properties: dict[str, str | None] = {}
        geometry = None
        srs_name = None
        geometry_error = None
        had_z = False
        for prop in feature:
            if not isinstance(prop.tag, str):
                continue
            pname = _local(prop)
            if _is_gml(prop) and pname in ("boundedBy", "name", "description", "identifier"):
                continue
            geom_children = [c for c in prop if isinstance(c.tag, str) and _is_gml(c)]
            if geom_children:
                if geometry is not None:
                    geometry_error = f"multiple geometry properties (extra: {pname})"
                    continue
                try:
                    geometry, srs_name, had_z = parse_geometry(geom_children[0], default_srs)
                except (GmlError, ValueError) as exc:
                    geometry_error = f"{pname}: {exc}"
                continue
            if prop.get(XSI_NIL) in ("true", "1"):
                properties[pname] = None
            else:
                text = (prop.text or "").strip()
                properties[pname] = text if text else None
        features.append(RawFeature(
            feature_id=fid,
            properties=properties,
            geometry=geometry,
            srs_name=srs_name or default_srs,
            geometry_error=geometry_error,
            had_z=had_z,
        ))
    return features
