"""Bound and split administrative geometries before contacting Overpass.

Never combine disconnected polygons into a single OSMnx convex hull.
This module does not silently discard islands or alter the output boundary.
"""
from __future__ import annotations

from dataclasses import dataclass

from pyproj import Transformer
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.ops import transform


class GeographicQueryTooLarge(ValueError):
    """Administrative geometry requires a different acquisition strategy."""


@dataclass(frozen=True)
class QueryPlan:
    polygons: tuple[Polygon, ...]
    projected_hull_km2: float


def _polygon_parts(geom):
    if isinstance(geom, Polygon):
        if not geom.is_empty:
            yield geom
    elif isinstance(geom, (MultiPolygon, GeometryCollection)):
        for part in geom.geoms:
            yield from _polygon_parts(part)


def plan_polygon_queries(
    geometry,
    *,
    max_component_km2: float = 500.0,
    max_total_km2: float = 1000.0,
    max_components: int = 24,
) -> QueryPlan:
    """Split disconnected polygons and enforce approximate projected hull budgets.

    Input is geographic EPSG:4326 geometry. Areas are projected to equal-area
    EPSG:6933; these are *preflight estimates* of OSMnx request coverage, not
    guarantees about provider load.
    """
    if max_component_km2 <= 0 or max_total_km2 <= 0 or max_components < 1:
        raise ValueError("Query budget limits must be positive")

    polygons = tuple(_polygon_parts(geometry))
    if not polygons:
        raise ValueError("The geographic boundary has no polygon components")
    if len(polygons) > max_components:
        raise GeographicQueryTooLarge(
            f"Boundary consists of {len(polygons)} disconnected areas "
            f"(limit {max_components}); use a regional OSM extract."
        )

    project = Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True).transform
    total_km2 = 0.0
    for index, polygon in enumerate(polygons, start=1):
        if not polygon.is_valid:
            raise ValueError(f"Boundary component {index} is invalid")
        km2 = transform(project, polygon).convex_hull.area / 1_000_000
        if km2 > max_component_km2:
            raise GeographicQueryTooLarge(
                f"Boundary component {index} needs roughly {km2:.0f} km² "
                f"of query coverage (limit {max_component_km2:.0f} km²). "
                "Use a regional OSM extract or a smaller map area."
            )
        total_km2 += km2

    if total_km2 > max_total_km2:
        raise GeographicQueryTooLarge(
            f"Boundary needs roughly {total_km2:.0f} km² across "
            f"{len(polygons)} queries (limit {max_total_km2:.0f} km²). "
            "Use a regional OSM extract or a smaller map area."
        )
    return QueryPlan(polygons, total_km2)
