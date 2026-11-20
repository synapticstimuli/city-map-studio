#!/usr/bin/env python3
"""Create configurable print-ready city street maps as editable SVG.

Examples:
  py generate_city_map_v2_1.py --style detailed --road-mode outline --include-paths --show-boundary
  py generate_city_map_v2_1.py --city Cambridge --style fine --road-mode centerline
  py generate_city_map_v2_1.py --style bold --hide-rail --refresh-data

Data: OpenStreetMap contributors (ODbL). Road edges are ESTIMATED from
centerlines, NOT surveyed curb/roadway edges. Cached GeoJSON is local data.
"""

import argparse
import re
from pathlib import Path
import time
import random
import requests
import pandas as pd

from osmnx._errors import ResponseStatusCodeError, InsufficientResponseError
from query_planner import plan_polygon_queries, GeographicQueryTooLarge

import geopandas as gpd
import osmnx as ox
import svgwrite
from pyproj import Transformer
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPolygon,
    Polygon,
)
from shapely.ops import transform, unary_union

# ===== LOCATION (change these for your next city) =====
CITY = "Somerville"
COUNTY = "Middlesex"  # Optional: "" to omit
STATE = "Massachusetts"
COUNTRY = "USA"

# ===== ARTWORK DEFAULTS (also adjustable on command line) =====
STYLE = "detailed"  # fine, detailed, bold
ROAD_MODE = "outline"  # outline or centerline
INCLUDE_PATHS = True
SHOW_BOUNDARY = False  # Keep boundary in SVG, hidden by default
SHOW_RAIL = True
INCLUDE_SERVICE = True
INCLUDE_TRACKS = True
INCLUDE_UNDERGROUND_RAIL = False
OVERPASS_TIMEOUT_SECONDS = 300
PAGE_WIDTH_MM = 420
PAGE_HEIGHT_MM = 297
MARGIN_MM = 18

# Line widths are SVG output units, i.e. millimeters given our SVG viewBox.
STYLES = {
    "fine": {
        "roads": 0.10,
        "paths": 0.07,
        "rail": 0.12,
        "boundary": 0.17,
        "color": "#171717",
        "boundary_color": "#888888",
    },
    "detailed": {
        "roads": 0.16,
        "paths": 0.10,
        "rail": 0.18,
        "boundary": 0.24,
        "color": "#101010",
        "boundary_color": "#777777",
    },
    "bold": {
        "roads": 0.24,
        "paths": 0.14,
        "rail": 0.27,
        "boundary": 0.35,
        "color": "#090909",
        "boundary_color": "#555555",
    },
}

STREET_TYPES = {
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "unclassified",
    "residential",
    "living_street",
    "service",
    "road",
    "motorway_link",
    "trunk_link",
    "primary_link",
    "secondary_link",
    "tertiary_link",
    "busway",
    "track",
}
PATH_TYPES = {
    "pedestrian",
    "footway",
    "path",
    "steps",
    "cycleway",
    "bridleway",
    "corridor",
}
RAIL_TYPES = {"rail", "light_rail", "subway", "tram", "narrow_gauge", "monorail"}
ROAD_HALF_WIDTH_M = {
    "motorway": 8.0,
    "trunk": 7.0,
    "primary": 6.0,
    "secondary": 5.2,
    "tertiary": 4.6,
    "unclassified": 3.8,
    "residential": 3.6,
    "living_street": 3.4,
    "service": 2.6,
    "road": 3.6,
    "motorway_link": 4.8,
    "trunk_link": 4.4,
    "primary_link": 4.0,
    "secondary_link": 3.6,
    "tertiary_link": 3.3,
    "busway": 3.3,
    "track": 2.4,
}
PATH_HALF_WIDTH_M = {
    "pedestrian": 1.8,
    "footway": 1.0,
    "path": 0.9,
    "steps": 0.8,
    "cycleway": 1.2,
    "bridleway": 1.0,
    "corridor": 1.0,
}


def slug(text):
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_") or "Location"


def location_query(args):
    parts = [args.city.strip()]
    if args.county.strip():
        parts.append(f"{args.county.strip()} County")
    parts.extend([args.state.strip(), args.country.strip()])
    return ", ".join(p for p in parts if p)


def tag_values(value):
    if isinstance(value, str):
        return {v.strip() for v in value.split(";") if v.strip()}
    if isinstance(value, (tuple, list, set)):
        return {v for v in value if isinstance(v, str)}
    return set()


def iter_lines(geom):
    if geom.is_empty:
        return
    if isinstance(geom, LineString):
        yield geom
    elif isinstance(geom, (MultiLineString, GeometryCollection)):
        for part in geom.geoms:
            yield from iter_lines(part)


def iter_outlines(geom):
    if geom.is_empty:
        return
    if isinstance(geom, Polygon):
        yield LineString(geom.exterior.coords)
        for ring in geom.interiors:
            yield LineString(ring.coords)
    elif isinstance(geom, (MultiPolygon, GeometryCollection)):
        for part in geom.geoms:
            yield from iter_outlines(part)
    else:
        yield from iter_lines(geom)


def projected_polygon(geom, types, width_map, fallback):
    if isinstance(geom, (Polygon, MultiPolygon)):
        return geom
    if isinstance(geom, (LineString, MultiLineString)):
        half_width = max([width_map[t] for t in types if t in width_map] or [fallback])
        return geom.buffer(half_width, cap_style=2, join_style=2)
    return None


# ==========================================
# GEOGRAPHIC DOWNLOAD RETRY HANDLING
# ==========================================


def fetch_features_with_retry(boundary, tags, max_attempts=4):
    for attempt in range(1, max_attempts + 1):
        try:
            print(f"Geographic download attempt {attempt}/{max_attempts}")

            return ox.features.features_from_polygon(boundary, tags)

        except (
            ResponseStatusCodeError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as exc:
            if isinstance(exc, ResponseStatusCodeError):
                retryable = any(str(code) in str(exc) for code in (429, 502, 503, 504))

                if not retryable:
                    raise

            if attempt == max_attempts:
                raise RuntimeError(
                    "Geographic data provider is unavailable. Please try again later."
                ) from exc

            delay = 30 * (2 ** (attempt - 1))
            delay += random.uniform(0, 5)

            print(f"Temporary geographic download failure: {exc}")

            print(f"Retrying in {delay:.0f} seconds...")

            time.sleep(delay)


def load_geodata(args):
    """Download once per place, or reuse human-readable GeoJSON files."""
    # Geographic download settings
    ox.settings.log_console = True
    ox.settings.use_cache = True

    # Subdivide large geographic queries.
    # Area is expressed in square meters.
    ox.settings.max_query_area_size = 500_000_000

    key = "_".join(
        slug(part) for part in (args.city, args.county, args.state, args.country)
    )
    folder = args.cache_dir / key
    boundary_file = folder / "boundary.geojson"
    features_file = folder / "osm_features.geojson"
    if not args.refresh_data and boundary_file.exists() and features_file.exists():
        print(f"Using cached geographic data: {folder}")
        return gpd.read_file(boundary_file), gpd.read_file(features_file)

    query = location_query(args)
    print(f"Fetching boundary: {query}")
    boundary_gdf = ox.geocode_to_gdf(query).to_crs("EPSG:4326")
    if boundary_gdf.empty:
        raise RuntimeError(f"No boundary found: {query}")
    boundary = boundary_gdf.geometry.union_all()
    ox.settings.requests_timeout = args.overpass_timeout
    print(
        f"Fetching roads, paths, and railways from OpenStreetMap (timeout: {args.overpass_timeout}s)...",
        flush=True,
    )
    tags = {
        "highway": sorted(STREET_TYPES | PATH_TYPES),
        "railway": sorted(RAIL_TYPES),
    }
    # Query disconnected components individually: OSMnx would otherwise
    # consolidate a MultiPolygon into a potentially enormous convex hull.
    # Preflight checks reject unreasonably broad geometry *before* network I/O.
    plan = plan_polygon_queries(boundary)
    print(
        f"Query plan: {len(plan.polygons)} separate areas; "
        f"~{plan.projected_hull_km2:.1f} km2 total coverage",
        flush=True,
    )
    components = []
    for position, polygon in enumerate(plan.polygons, start=1):
        print(f"Fetching geographic component {position}/{len(plan.polygons)}", flush=True)
        try:
            components.append(fetch_features_with_retry(polygon, tags))
        except InsufficientResponseError:
            # Some remote island components legitimately have no mapped roads.
            print(f"Component {position} has no matching OSM features", flush=True)
    if not components:
        raise RuntimeError("No matching OpenStreetMap features found in any component")
    features = gpd.GeoDataFrame(
        pd.concat(components).loc[lambda frame: ~frame.index.duplicated(keep="first")],
        crs=components[0].crs,
    )
    if features.empty:
        raise RuntimeError("No matching OpenStreetMap features found")
    features = features.to_crs("EPSG:4326")
    folder.mkdir(parents=True, exist_ok=True)
    boundary_gdf[["geometry"]].to_file(boundary_file, driver="GeoJSON", index=False)
    # Keep only relevant tags to avoid fragile OSM object/list field serialization.
    fields = [
        c
        for c in [
            "highway",
            "railway",
            "disused",
            "abandoned",
            "tunnel",
            "service",
            "area",
            "access",
            "bridge",
            "width",
        ]
        if c in features.columns
    ]
    clean = features[fields + ["geometry"]].copy()
    for field in fields:
        clean[field] = clean[field].map(
            lambda v: (
                ";".join(str(x) for x in v)
                if isinstance(v, (list, tuple, set))
                else str(v)
                if isinstance(v, str)
                else None
            )
        )
    clean.to_file(features_file, driver="GeoJSON", index=False)
    print(f"Cached geographic data: {folder}")
    return boundary_gdf, clean


def create_svg(args):
    boundary_gdf, features = load_geodata(args)
    boundary = boundary_gdf.geometry.union_all()
    local_crs = boundary_gdf.estimate_utm_crs()
    if local_crs is None:
        raise RuntimeError("Could not estimate a local metric coordinate system")
    project = Transformer.from_crs(
        boundary_gdf.crs, local_crs, always_xy=True
    ).transform
    boundary_xy = transform(project, boundary)
    xmin, ymin, xmax, ymax = boundary_xy.bounds
    width, height = args.width, args.height
    scale = min(
        (width - 2 * args.margin) / (xmax - xmin),
        (height - 2 * args.margin) / (ymax - ymin),
    )
    left = (width - (xmax - xmin) * scale) / 2
    top = (height - (ymax - ymin) * scale) / 2
    style = STYLES[args.style]

    drawing = svgwrite.Drawing(
        str(args.output),
        size=(f"{width}mm", f"{height}mm"),
        viewBox=f"0 0 {width} {height}",
        profile="full",
    )
    drawing.set_desc(
        title=f"{args.city} — {args.style} {args.road_mode} street map",
        desc="Road edges approximated from OpenStreetMap centerlines; "
        "not surveyed curb locations. © OpenStreetMap contributors (ODbL).",
    )
    drawing.add(
        drawing.rect(insert=(0, 0), size=(width, height), fill="white", id="background")
    )
    groups = {
        "roads": drawing.g(
            id="road-edges" if args.road_mode == "outline" else "road-centerlines",
            stroke=style["color"],
            stroke_width=args.road_stroke or style["roads"],
            fill="none",
        ),
        "paths": drawing.g(
            id="path-edges" if args.road_mode == "outline" else "path-centerlines",
            stroke=style["color"],
            stroke_width=style["paths"],
            fill="none",
        ),
        "rail": drawing.g(
            id="railways",
            stroke=style["color"],
            stroke_width=style["rail"],
            fill="none",
        ),
        "boundary": drawing.g(
            id="city-boundary",
            stroke=style["boundary_color"],
            stroke_width=args.boundary_stroke or style["boundary"],
            fill="none",
        ),
    }
    # Hide boundary only at presentation level; its vector paths remain editable.
    if not args.show_boundary:
        groups["boundary"].update({"style": "display:none"})
    counts = {key: 0 for key in groups}

    def draw_geometry(geom_xy, category, outlines=False):
        clipped = geom_xy.intersection(boundary_xy)
        iterator = iter_outlines(clipped) if outlines else iter_lines(clipped)
        for piece in iterator:
            if len(piece.coords) < 2:
                continue
            points = [
                (left + (x - xmin) * scale, top + (ymax - y) * scale)
                for x, y in piece.coords
            ]
            path = f"M{points[0][0]:.4f},{points[0][1]:.4f} " + " ".join(
                f"L{x:.4f},{y:.4f}" for x, y in points[1:]
            )
            groups[category].add(
                drawing.path(d=path, stroke_linecap="round", stroke_linejoin="round")
            )
            counts[category] += 1

    road_shapes, path_shapes = [], []
    for _, item in features.iterrows():
        geom = item.geometry
        if geom is None or geom.is_empty:
            continue
        highway = tag_values(item.get("highway"))
        railway = tag_values(item.get("railway"))
        geom_xy = transform(project, geom)
        if highway & STREET_TYPES:
            if ("service" not in highway or args.include_service) and (
                "track" not in highway or args.include_tracks
            ):
                if args.road_mode == "outline":
                    poly = projected_polygon(geom_xy, highway, ROAD_HALF_WIDTH_M, 3.5)
                    if poly is not None:
                        road_shapes.append(poly)
                elif isinstance(geom_xy, (LineString, MultiLineString)):
                    draw_geometry(geom_xy, "roads")
                elif isinstance(geom_xy, (Polygon, MultiPolygon)):
                    draw_geometry(geom_xy, "roads", outlines=True)
        elif args.include_paths and highway & PATH_TYPES:
            if args.road_mode == "outline":
                poly = projected_polygon(geom_xy, highway, PATH_HALF_WIDTH_M, 1.0)
                if poly is not None:
                    path_shapes.append(poly)
            elif isinstance(geom_xy, (LineString, MultiLineString)):
                draw_geometry(geom_xy, "paths")
            elif isinstance(geom_xy, (Polygon, MultiPolygon)):
                draw_geometry(geom_xy, "paths", outlines=True)

        if args.show_rail and railway & RAIL_TYPES:
            if (tag_values(item.get("disused")) | tag_values(item.get("abandoned"))) & {
                "yes"
            }:
                continue
            if not args.include_underground_rail and "subway" in railway:
                continue
            rail_geom = (
                geom_xy.boundary
                if isinstance(geom_xy, (Polygon, MultiPolygon))
                else geom_xy
            )
            draw_geometry(rail_geom, "rail")

    if args.road_mode == "outline":
        if road_shapes:
            print(f"Merging {len(road_shapes)} road shapes...")
            draw_geometry(unary_union(road_shapes), "roads", outlines=True)
        if path_shapes:
            print(f"Merging {len(path_shapes)} path shapes...")
            draw_geometry(unary_union(path_shapes), "paths", outlines=True)
    # Always preserve geographic boundary geometry in the file for later toggling.
    draw_geometry(boundary_xy, "boundary", outlines=True)

    for key in ("paths", "roads", "rail", "boundary"):
        drawing.add(groups[key])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    drawing.save(pretty=False)
    print(f"Saved: {args.output.resolve()}")
    print(
        f"Style: {args.style}; road mode: {args.road_mode}; paths per layer: {counts}"
    )
    print("Print/source attribution: © OpenStreetMap contributors")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", default=CITY)
    parser.add_argument("--county", default=COUNTY)
    parser.add_argument("--state", default=STATE)
    parser.add_argument("--country", default=COUNTRY)
    parser.add_argument("--style", choices=sorted(STYLES), default=STYLE)
    parser.add_argument(
        "--road-mode", choices=["centerline", "outline"], default=ROAD_MODE
    )
    parser.add_argument(
        "--include-paths",
        dest="include_paths",
        action=argparse.BooleanOptionalAction,
        default=INCLUDE_PATHS,
    )
    parser.add_argument(
        "--show-boundary",
        dest="show_boundary",
        action=argparse.BooleanOptionalAction,
        default=SHOW_BOUNDARY,
    )
    parser.add_argument(
        "--show-rail",
        dest="show_rail",
        action=argparse.BooleanOptionalAction,
        default=SHOW_RAIL,
    )
    parser.add_argument(
        "--include-service",
        dest="include_service",
        action=argparse.BooleanOptionalAction,
        default=INCLUDE_SERVICE,
    )
    parser.add_argument(
        "--include-tracks",
        dest="include_tracks",
        action=argparse.BooleanOptionalAction,
        default=INCLUDE_TRACKS,
    )
    parser.add_argument(
        "--include-underground-rail",
        action="store_true",
        default=INCLUDE_UNDERGROUND_RAIL,
    )
    parser.add_argument(
        "--road-stroke",
        type=float,
        default=None,
        help="Override road line weight in mm",
    )
    parser.add_argument(
        "--boundary-stroke",
        type=float,
        default=None,
        help="Override boundary line weight in mm",
    )
    parser.add_argument("--width", type=float, default=PAGE_WIDTH_MM)
    parser.add_argument("--height", type=float, default=PAGE_HEIGHT_MM)
    parser.add_argument("--margin", type=float, default=MARGIN_MM)
    parser.add_argument("--cache-dir", type=Path, default=Path("map_data_cache"))
    parser.add_argument(
        "--overpass-timeout",
        type=int,
        default=OVERPASS_TIMEOUT_SECONDS,
        help="OSM download timeout in seconds (default 300)",
    )
    parser.add_argument(
        "--refresh-data", action="store_true", help="Re-download OpenStreetMap data"
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if not args.city.strip() or not args.state.strip() or not args.country.strip():
        parser.error("City, state, and country must be nonempty")
    if args.overpass_timeout <= 0:
        parser.error("--overpass-timeout must be positive")
    if (
        args.margin < 0
        or args.width <= 2 * args.margin
        or args.height <= 2 * args.margin
    ):
        parser.error("Invalid paper dimensions or margins")
    if args.road_stroke is not None and args.road_stroke <= 0:
        parser.error("--road-stroke must be positive")
    if args.boundary_stroke is not None and args.boundary_stroke <= 0:
        parser.error("--boundary-stroke must be positive")
    if args.output is None:
        args.output = Path(
            f"{slug(args.city)}_{slug(args.state)}_{args.style}_{args.road_mode}.svg"
        )
    create_svg(args)


if __name__ == "__main__":
    main()
