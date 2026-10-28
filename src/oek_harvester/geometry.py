"""Geometry validation, conservative repair and projection to EPSG:25832."""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from functools import lru_cache

import shapely
from pyproj import Geod, Transformer
from pyproj.crs import CRS
from pyproj.transformer import TransformerGroup
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.geometry.polygon import orient
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as transform_geometry
from shapely.validation import explain_validity

TARGET_SRID = 25832
# ETRS89 geographic; same datum as EPSG:25832, so no datum shift involved.
_TO_LONLAT = Transformer.from_crs(TARGET_SRID, 4258, always_xy=True)


@dataclass
class GeometryResult:
    outcome: str  # "valid" | "repaired" | "rejected"
    geom_source: MultiPolygon | None = None  # source CRS, x/y order, 2D
    geom_target: MultiPolygon | None = None  # EPSG:25832
    area_m2: float | None = None
    reject_code: str | None = None
    detail: str | None = None
    flags: list[str] = field(default_factory=list)


@lru_cache(maxsize=16)
def _transformer_group(source_srid: int) -> TransformerGroup:
    # pyproj warns when a better grid-based operation is missing; that fact
    # is reported through transformation_info() instead.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return TransformerGroup(CRS.from_epsg(source_srid), CRS.from_epsg(TARGET_SRID),
                                always_xy=True)


def _covers(area_of_use, bbox: tuple[float, float, float, float]) -> bool:
    if area_of_use is None:
        return True  # e.g. Ballpark/world-wide operations
    w, s, e, n = bbox
    return (area_of_use.west <= w and area_of_use.south <= s
            and area_of_use.east >= e and area_of_use.north >= n)


def transformer_for(source_srid: int, region: tuple[float, float, float, float]) -> Transformer:
    """The most accurate installed transformation valid for the whole region.

    Pinning one operation (instead of letting PROJ choose per coordinate)
    makes runs reproducible on the same PROJ data and lets the report state
    exactly which operation and accuracy were used. Operations whose area of
    use does not cover the whole region (e.g. a Saarland-only grid inside the
    RLP bounding box) are never chosen, as they would be applied outside
    their area.
    """
    group = _transformer_group(source_srid)
    candidates = [t for t in group.transformers if _covers(t.area_of_use, region)]
    if not candidates:
        raise ValueError(f"No transformation from EPSG:{source_srid} to {TARGET_SRID} "
                         f"covers {region}")
    return candidates[0]


def transformation_info(source_srid: int, region: tuple[float, float, float, float]) -> dict:
    group = _transformer_group(source_srid)
    chosen = transformer_for(source_srid, region)
    return {
        "source_srid": source_srid,
        "target_srid": TARGET_SRID,
        "operation": chosen.description,
        "accuracy_m": chosen.accuracy if chosen.accuracy >= 0 else None,
        "better_operations_unavailable": [
            op.name for op in group.unavailable_operations
            if _covers(op.area_of_use, region) and op.accuracy >= 0
            and (chosen.accuracy < 0 or op.accuracy < chosen.accuracy)
        ],
    }


_GEOD = Geod(ellps="GRS80")


def ellipsoidal_area_m2(geom_25832: BaseGeometry) -> float:
    """Ground area on the GRS80 ellipsoid. Planar EPSG:25832 area differs
    from it by the UTM scale factor (up to ~0.1 % across RLP)."""
    lonlat = transform_geometry(_TO_LONLAT.transform, geom_25832)
    # pyproj sums signed ring areas, so holes only subtract when they wind
    # opposite to the shell; orient() guarantees that.
    polys = [lonlat] if isinstance(lonlat, Polygon) else list(lonlat.geoms)
    return sum(_GEOD.geometry_area_perimeter(orient(p, sign=1.0))[0] for p in polys)


@lru_cache(maxsize=4)
def state_extent_25832(bbox_wgs84: tuple[float, float, float, float]) -> Polygon:
    t = Transformer.from_crs(4326, TARGET_SRID, always_xy=True)
    return box(*t.transform_bounds(*bbox_wgs84, densify_pts=21))


def _polygonal_parts(geom: BaseGeometry) -> list[Polygon]:
    if isinstance(geom, Polygon):
        return [geom] if not geom.is_empty else []
    if isinstance(geom, MultiPolygon):
        return [g for g in geom.geoms if not g.is_empty]
    if hasattr(geom, "geoms"):  # GeometryCollection from make_valid
        parts: list[Polygon] = []
        for g in geom.geoms:
            parts.extend(_polygonal_parts(g))
        return parts
    return []


def _ring_area(geom: BaseGeometry) -> float:
    """Shoelace area of the rings as digitised (for invalid input this is
    what the source 'meant', and what repair is compared against)."""
    polys = [geom] if isinstance(geom, Polygon) else list(geom.geoms)
    total = 0.0
    for p in polys:
        total += abs(Polygon(p.exterior).area) - sum(abs(Polygon(i).area) for i in p.interiors)
    return total


def process_geometry(
    geom: BaseGeometry | None,
    source_srid: int,
    *,
    max_repair_area_change: float,
    min_area_m2: float,
    state_bbox_wgs84: tuple[float, float, float, float],
) -> GeometryResult:
    if geom is None or geom.is_empty:
        return GeometryResult("rejected", reject_code="missing_geometry")

    flags: list[str] = []
    if shapely.has_z(geom):
        geom = shapely.force_2d(geom)
        flags.append("dropped_z")

    if not isinstance(geom, (Polygon, MultiPolygon)):
        return GeometryResult("rejected", reject_code="non_polygon_geometry",
                              detail=geom.geom_type, flags=flags)

    outcome = "valid"
    if not geom.is_valid:
        reason = explain_validity(geom)
        original_area = _ring_area(geom)
        parts = _polygonal_parts(shapely.make_valid(geom))
        if not parts:
            return GeometryResult("rejected", reject_code="unrepairable_geometry",
                                  detail=reason, flags=flags)
        repaired = MultiPolygon(parts)
        if original_area <= 0:
            return GeometryResult("rejected", reject_code="repair_changes_area",
                                  detail=f"{reason}; digitised ring area is 0", flags=flags)
        change = abs(repaired.area - original_area) / original_area
        if change > max_repair_area_change:
            return GeometryResult(
                "rejected", reject_code="repair_changes_area",
                detail=f"{reason}; repair changes area by {change:.2%}", flags=flags)
        geom = repaired
        outcome = "repaired"
        flags.append(f"repaired:{reason.split('[')[0].strip()}")

    source = geom if isinstance(geom, MultiPolygon) else MultiPolygon([geom])
    if len(source.geoms) > 1:
        flags.append("multipart")

    target = transform_geometry(transformer_for(source_srid, state_bbox_wgs84).transform, source)
    if not target.is_valid:
        # Reprojection can make near-degenerate input invalid; one more
        # make_valid is accepted only if it keeps the geometry polygonal.
        parts = _polygonal_parts(shapely.make_valid(target))
        if not parts:
            return GeometryResult("rejected", reject_code="invalid_after_projection",
                                  detail=explain_validity(target), flags=flags)
        target = MultiPolygon(parts)
        flags.append("repaired_after_projection")

    area = ellipsoidal_area_m2(target)
    if area < min_area_m2:
        return GeometryResult("rejected", reject_code="tiny_area",
                              detail=f"{area:.3f} m2 < {min_area_m2} m2", flags=flags)

    extent = state_extent_25832(state_bbox_wgs84)
    if not extent.contains(target.representative_point()):
        return GeometryResult("rejected", reject_code="outside_state_extent",
                              detail="representative point outside state bounding box",
                              flags=flags)
    if not extent.contains(target):
        flags.append("extends_outside_state_extent")

    return GeometryResult(outcome, geom_source=source, geom_target=target,
                          area_m2=area, flags=flags)
