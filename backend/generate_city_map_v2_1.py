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
import os
import re
from pathlib import Path
import time
import random
import requests
import pandas as pd

from osmnx._errors import ResponseStatusCodeError, InsufficientResponseError
from query_planner import plan_polygon_queries, GeographicQueryTooLarge
from cache_reuse import source_identity, geographic_cache_folder

import geopandas as gpd
import numpy as np
import osmnx as ox
import shapely
import shapely.affinity
import svgwrite
from pyproj import Transformer
from shapely.geometry import (
    GeometryCollection,
    LineString,
    box,
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
# v2.3 print composition (ADR-004, approved by Rod 2026-11-25): the city's
# bounding box fills 95% of the page in its limiting dimension, centered, in
# both modes. Regional surroundings fill the whole page. (v2.2: 78% / 95%.)
CITY_FILL = 0.95
REGIONAL_FOCUS_FILL = CITY_FILL
REGIONAL_OUTER_FILL = 1.00
# Optional fixed margin in mm for City-only maps (v2.2 behaviour, --margin).
MARGIN_MM = None

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


def regional_display_frame(boundary_xy, page_width, page_height):
    """Return city-based scale and the projected visible page window.

    The city's bounding box is exactly centered; its limiting dimension
    occupies REGIONAL_FOCUS_FILL (95%) of the paper, and the window covers
    REGIONAL_OUTER_FILL (100%) of the page. Geographic fetching and rendering
    use the identical frame (ADR-004).
    """
    xmin, ymin, xmax, ymax = boundary_xy.bounds
    city_width, city_height = xmax - xmin, ymax - ymin
    if city_width <= 0 or city_height <= 0:
        raise ValueError("Focus city has a degenerate projected boundary")
    scale = min(
        page_width * REGIONAL_FOCUS_FILL / city_width,
        page_height * REGIONAL_FOCUS_FILL / city_height,
    )
    cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2
    half_w = page_width * REGIONAL_OUTER_FILL / (2 * scale)
    half_h = page_height * REGIONAL_OUTER_FILL / (2 * scale)
    return scale, box(cx - half_w, cy - half_h, cx + half_w, cy + half_h)


EMPTY_GEOMETRY = GeometryCollection()


def clip_to(geom, area):
    """Return the same geometry as ``geom.intersection(area)``, faster.

    ``area`` should be prepared with ``shapely.prepare``. Most ways of a large
    city lie entirely inside or entirely outside the clipping area, and a full
    overlay against a detailed municipal boundary (thousands of vertices) is
    the renderer's most expensive per-way step. So:

    * disjoint -> empty (the overlay result would be empty too);
    * strictly interior -> overlay against a small rectangle enclosing the
      geometry. GEOS builds the result from the geometry's own edges in both
      cases, so this reproduces the original output (vertex order, ring
      orientation, noding of self-crossing ways) at a fraction of the cost;
    * crossing or touching the edge -> the original overlay.

    ``tests/test_render_fast_paths.py`` and ``benchmark_render.py`` check that
    SVG output stays byte-identical to clipping every way against ``area``.
    """
    if shapely.disjoint(area, geom):
        return EMPTY_GEOMETRY
    if shapely.contains_properly(area, geom):
        xmin, ymin, xmax, ymax = geom.bounds
        return geom.intersection(box(xmin - 1, ymin - 1, xmax + 1, ymax + 1))
    return geom.intersection(area)


def clip_many(geometries, area):
    """Vectorized ``clip_to``: element ``i`` equals ``clip_to(geometries[i], area)``.

    Classifies every geometry against the prepared ``area`` in one call per
    predicate and runs each kind of overlay as a single batch, instead of
    several Python-level shapely calls (and a new rectangle) per way.
    """
    geometries = np.asarray(geometries, dtype=object)
    clipped = np.full(len(geometries), EMPTY_GEOMETRY, dtype=object)
    if not len(geometries):
        return clipped
    disjoint = shapely.disjoint(area, geometries)
    inside = ~disjoint & shapely.contains_properly(area, geometries)
    crossing = ~disjoint & ~inside
    if inside.any():
        b = shapely.bounds(geometries[inside])
        frames = shapely.box(b[:, 0] - 1, b[:, 1] - 1, b[:, 2] + 1, b[:, 3] + 1)
        clipped[inside] = shapely.intersection(geometries[inside], frames)
    if crossing.any():
        clipped[crossing] = shapely.intersection(geometries[crossing], area)
    return clipped


def project_geometries(geometries, transformer):
    """Vectorized equivalent of ``shapely.ops.transform`` for many geometries.

    Uses the same pyproj transformer on all coordinates in a single call, so
    projected coordinates match the previous per-geometry projection exactly.
    """
    def reproject(coords):
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])

    return shapely.transform(np.asarray(geometries, dtype=object), reproject)


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


def road_index_dir(cache_dir):
    """Where reusable regional-extract road indexes live (ADR-002).

    Set OSM_ROAD_INDEX=0 to disable them and scan the extract every time,
    as v2.2 did. The leading underscore cannot collide with a place folder,
    because place keys are slugs.
    """
    if os.getenv("OSM_ROAD_INDEX", "1").strip().lower() in {"0", "false", "no", "off"}:
        return None
    return Path(cache_dir) / "_extract_index"


FEATURES_CACHE_NAME = "osm_features.fgb"
LEGACY_FEATURES_CACHE_NAME = "osm_features.geojson"


def write_cached_features(features, path):
    """Write the feature cache as FlatGeobuf (ADR-005).

    FlatGeobuf stores coordinates as exact doubles and writes/reads several
    times faster than GeoJSON. The file is written under a temporary name and
    renamed, so an interrupted run never leaves a half-written cache behind.
    """
    path = Path(path)
    partial = path.with_name(path.stem + ".partial.fgb")
    try:
        features.to_file(partial, driver="FlatGeobuf", index=False,
                         engine="pyogrio", SPATIAL_INDEX="NO")
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


def read_cached_features(folder):
    """Cached features for ``folder``, or None when there is no usable cache.

    Prefers ``osm_features.fgb``. A cache written by v2.2-v2.3 as
    ``osm_features.geojson`` is still read, then also saved as FlatGeobuf so
    later runs read it fast; the GeoJSON is kept for older app versions. A
    damaged FlatGeobuf is removed and the next best source is used.
    """
    folder = Path(folder)
    fast = folder / FEATURES_CACHE_NAME
    legacy = folder / LEGACY_FEATURES_CACHE_NAME
    if fast.is_file():
        try:
            return gpd.read_file(fast, engine="pyogrio")
        except Exception as exc:  # damaged or truncated file
            print(f"Ignoring unreadable feature cache {fast}: {exc}", flush=True)
            fast.unlink(missing_ok=True)
    if legacy.is_file():
        features = gpd.read_file(legacy)
        try:
            write_cached_features(features, fast)
        except Exception as exc:  # read-only or full disk: legacy cache still works
            print(f"Could not convert feature cache to FlatGeobuf: {exc}", flush=True)
        return features
    return None


def regional_cache_name(width, height):
    """Geographic cache subfolder for full-page Regional maps (ADR-004)."""
    return f"regional_fullpage_{width:g}x{height:g}mm"


def load_geodata(args):
    """Download once per place, or reuse human-readable GeoJSON files."""
    started_at = time.perf_counter()
    # Geographic download settings
    ox.settings.log_console = True
    ox.settings.use_cache = True

    # Subdivide large geographic queries.
    # Area is expressed in square meters.
    ox.settings.max_query_area_size = 500_000_000

    key = "_".join(
        slug(part) for part in (args.city, args.county, args.state, args.country)
    )
    # Never mix Overpass and regional-extract geographic features.
    # Changing the extract path/size/mtime selects a fresh cache namespace.
    source_key = source_identity(args.extract_file)
    folder = geographic_cache_folder(args.cache_dir, key, source_key)
    if args.regional_context:
        # Full-page regional geography (ADR-004) depends on the page shape,
        # and never reuses v2.2 "regional_3000m" halo caches, which do not
        # reach the page edges.
        folder = folder / regional_cache_name(args.width, args.height)
    boundary_file = folder / "boundary.geojson"
    features_file = folder / FEATURES_CACHE_NAME
    if not args.refresh_data and boundary_file.exists():
        cached_features = read_cached_features(folder)
        if cached_features is not None:
            print(f"Using cached geographic data: {folder}")
            cached_boundary = gpd.read_file(boundary_file)
            print(f"STAGE geographic_cache_read_seconds={time.perf_counter()-started_at:.2f}", flush=True)
            return cached_boundary, cached_features

    query = location_query(args)
    boundary_started_at = time.perf_counter()
    # Existing Overpass boundaries remain usable when importing a different
    # dataset; the feature GeoJSON itself is never shared across sources.
    prior_boundary = args.cache_dir / key / "boundary.geojson"
    source_boundary = geographic_cache_folder(args.cache_dir, key, source_key) / "boundary.geojson"
    candidate_boundary = next(
        (candidate for candidate in (boundary_file, source_boundary, prior_boundary) if candidate.is_file()),
        boundary_file,
    )
    if candidate_boundary.is_file() and not args.refresh_data:
        print(f"Reusing cached boundary: {candidate_boundary}", flush=True)
        boundary_gdf = gpd.read_file(candidate_boundary).to_crs("EPSG:4326")
    else:
        print(f"Fetching boundary: {query}", flush=True)
        boundary_gdf = ox.geocode_to_gdf(query).to_crs("EPSG:4326")
    if boundary_gdf.empty:
        raise RuntimeError(f"No boundary found: {query}")
    boundary = boundary_gdf.geometry.union_all()
    print(f"STAGE boundary_lookup_seconds={time.perf_counter()-boundary_started_at:.2f}", flush=True)
    features_started_at = time.perf_counter()
    query_geometry = boundary
    if args.regional_context:
        local_crs = boundary_gdf.estimate_utm_crs()
        if local_crs is None:
            raise RuntimeError("Could not estimate projection for surrounding region")
        project_local = Transformer.from_crs("EPSG:4326", local_crs, always_xy=True).transform
        project_geographic = Transformer.from_crs(local_crs, "EPSG:4326", always_xy=True).transform
        # Fetch exactly the streets that can appear on the page: the whole
        # printed page frame around the centered city (ADR-004). The
        # --regional-buffer-km option no longer limits this; it is kept for
        # API and cache-key compatibility.
        city_metric = transform(project_local, boundary)
        _, print_frame = regional_display_frame(city_metric, args.width, args.height)
        # 2% extra so streets still reach the page edge after the frame is
        # re-projected to longitude/latitude; rendering clips to the page.
        useful_metric = shapely.affinity.scale(print_frame, 1.02, 1.02).union(city_metric)
        query_geometry = transform(project_geographic, useful_metric)
        print(
            f"Regional context: full page frame, "
            f"query {useful_metric.area / 1_000_000:.1f} km2",
            flush=True,
        )

    if args.extract_file is not None:
        # Explicit opt-in; no Overpass queries when a local extract is selected.
        from osm_extract import extract_features
        features = extract_features(
            args.extract_file, query_geometry, STREET_TYPES | PATH_TYPES, RAIL_TYPES,
            index_dir=road_index_dir(args.cache_dir),
        )
    else:
        ox.settings.requests_timeout = args.overpass_timeout
        print(
            f"Fetching roads, paths, and railways from OpenStreetMap (timeout: {args.overpass_timeout}s)...",
            flush=True,
        )
        tags = {
            "highway": sorted(STREET_TYPES | PATH_TYPES),
            "railway": sorted(RAIL_TYPES),
        }
        plan = plan_polygon_queries(query_geometry)
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
                print(f"Component {position} has no matching OSM features", flush=True)
        if not components:
            raise RuntimeError("No matching OpenStreetMap features found in any component")
        features = gpd.GeoDataFrame(
            pd.concat(components).loc[lambda frame: ~frame.index.duplicated(keep="first")],
            crs=components[0].crs,
        )
    if features.empty:
        raise RuntimeError("No matching OpenStreetMap features found")
    print(f"STAGE feature_retrieval_seconds={time.perf_counter()-features_started_at:.2f}", flush=True)
    features = features.to_crs("EPSG:4326")
    folder.mkdir(parents=True, exist_ok=True)
    boundary_gdf[["geometry"]].to_file(boundary_file, driver="GeoJSON", index=False)
    # Draw with the boundary exactly as cached runs will read it back
    # (GeoJSON rounds coordinates), so the first map matches later ones.
    boundary_gdf = gpd.read_file(boundary_file)
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
    write_cached_features(clean, features_file)
    print(f"Cached geographic data: {folder}")
    print(f"STAGE geographic_total_seconds={time.perf_counter()-started_at:.2f}", flush=True)
    return boundary_gdf, clean


def create_svg(args):
    generation_started_at = time.perf_counter()
    boundary_gdf, features = load_geodata(args)
    print(f"STAGE geographic_loading_seconds={time.perf_counter()-generation_started_at:.2f}", flush=True)
    render_started_at = time.perf_counter()
    boundary = boundary_gdf.geometry.union_all()
    local_crs = boundary_gdf.estimate_utm_crs()
    if local_crs is None:
        raise RuntimeError("Could not estimate a local metric coordinate system")
    transformer = Transformer.from_crs(boundary_gdf.crs, local_crs, always_xy=True)
    boundary_xy = transform(transformer.transform, boundary)
    width, height = args.width, args.height
    xmin, ymin, xmax, ymax = boundary_xy.bounds
    if args.regional_context:
        # The exact same centered frame used by geographic acquisition.
        scale, display_window_xy = regional_display_frame(boundary_xy, width, height)
        focus_cx, focus_cy = (
            (boundary_xy.bounds[0] + boundary_xy.bounds[2]) / 2,
            (boundary_xy.bounds[1] + boundary_xy.bounds[3]) / 2,
        )
        context_ring_xy = display_window_xy.difference(boundary_xy)
        # Frame conversion: the focus-city bounding box center maps to the
        # exact page center; surroundings fill the page.
        xmin = focus_cx - width / (2 * scale)
        ymax = focus_cy + height / (2 * scale)
        left = top = 0.0
        print(
            f"Regional framing: focus {REGIONAL_FOCUS_FILL:.0%} max fill; "
            f"outer context clipped to {REGIONAL_OUTER_FILL:.0%} of page",
            flush=True,
        )
    elif args.margin is None:
        # City fills 95% of the page in its limiting dimension (ADR-004).
        scale = CITY_FILL * min(width / (xmax - xmin), height / (ymax - ymin))
        left = (width - (xmax - xmin) * scale) / 2
        top = (height - (ymax - ymin) * scale) / 2
        context_ring_xy = None
    else:
        # Explicit --margin: the v2.2 City-only layout.
        scale = min(
            (width - 2 * args.margin) / (xmax - xmin),
            (height - 2 * args.margin) / (ymax - ymin),
        )
        left = (width - (xmax - xmin) * scale) / 2
        top = (height - (ymax - ymin) * scale) / 2
        context_ring_xy = None
    style = STYLES[args.style]

    drawing = svgwrite.Drawing(
        str(args.output),
        size=(f"{width}mm", f"{height}mm"),
        viewBox=f"0 0 {width} {height}",
        profile="full",
        # Skip svgwrite's per-attribute validation (about a fifth of render
        # time for large cities). It only checks values; the written SVG is
        # byte-identical. All attributes here are fixed or numeric.
        debug=False,
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
        "context": drawing.g(
            id="regional-context",
            stroke="#bac1ca",
            stroke_width=0.10,
            opacity=0.7,
            fill="none",
        ),
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
    # Keep focus-city borders prominent in regional mode.
    if args.regional_context:
        groups["boundary"]["stroke"] = "#35617e"
        groups["boundary"]["stroke-width"] = max(
            args.boundary_stroke or style["boundary"], 0.32
        )
    # Hide boundary only at presentation level; its vector paths remain editable.
    if not args.show_boundary and not args.regional_context:
        groups["boundary"].update({"style": "display:none"})
    counts = {key: 0 for key in groups}

    # Prepared clip areas make the per-way disjoint/interior tests cheap.
    shapely.prepare(boundary_xy)
    if context_ring_xy is not None:
        shapely.prepare(context_ring_xy)

    def draw_geometry(geom_xy, category, outlines=False, clipped=None):
        # ``clipped`` is the precomputed clip of ``geom_xy`` (see below).
        if clipped is None:
            clipped = clip_to(
                geom_xy, context_ring_xy if category == "context" else boundary_xy
            )
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

    def column(name):
        # Same result as ``item.get(name)`` on a missing column: None.
        if name in features.columns:
            return features[name].tolist()
        return [None] * len(features)

    # Project every feature in one vectorized call instead of once per row.
    # Empty or missing geometries are skipped exactly as before.
    source_geoms = features.geometry.to_numpy()
    valid = ~(shapely.is_missing(source_geoms) | shapely.is_empty(source_geoms))
    projected = np.full(len(source_geoms), None, dtype=object)
    if valid.any():
        projected[valid] = project_geometries(source_geoms[valid], transformer)

    # Clip every projected way against the city (and the regional context
    # ring) in batches up front; the loop below then only looks results up.
    # Derived geometries (polygon boundaries, outline buffers, unions) are
    # still clipped one by one with clip_to.
    city_clips = np.full(len(projected), None, dtype=object)
    context_clips = np.full(len(projected), None, dtype=object)
    if valid.any():
        city_clips[valid] = clip_many(projected[valid], boundary_xy)
        if context_ring_xy is not None:
            context_clips[valid] = clip_many(projected[valid], context_ring_xy)

    for geom_xy, city_clip, context_clip, highway_raw, railway_raw, disused_raw, abandoned_raw in zip(
        projected, city_clips, context_clips, column("highway"), column("railway"),
        column("disused"), column("abandoned"),
    ):
        if geom_xy is None:
            continue
        highway = tag_values(highway_raw)
        railway = tag_values(railway_raw)
        if args.regional_context and (highway & (STREET_TYPES | PATH_TYPES) or railway & RAIL_TYPES):
            # Muted out-of-city lines are drawn as centerlines even in
            # outline mode, avoiding expensive outer-area buffered unions.
            # The focus-city geometry is rendered independently afterward.
            if isinstance(geom_xy, (LineString, MultiLineString)):
                draw_geometry(geom_xy, "context", clipped=context_clip)
            elif isinstance(geom_xy, (Polygon, MultiPolygon)):
                draw_geometry(geom_xy.boundary, "context")
        if highway & STREET_TYPES:
            if ("service" not in highway or args.include_service) and (
                "track" not in highway or args.include_tracks
            ):
                if args.road_mode == "outline":
                    poly = projected_polygon(geom_xy, highway, ROAD_HALF_WIDTH_M, 3.5)
                    if poly is not None:
                        focused_poly = clip_to(poly, boundary_xy)
                        if not focused_poly.is_empty:
                            road_shapes.append(focused_poly)
                elif isinstance(geom_xy, (LineString, MultiLineString)):
                    draw_geometry(geom_xy, "roads", clipped=city_clip)
                elif isinstance(geom_xy, (Polygon, MultiPolygon)):
                    draw_geometry(geom_xy, "roads", outlines=True, clipped=city_clip)
        elif args.include_paths and highway & PATH_TYPES:
            if args.road_mode == "outline":
                poly = projected_polygon(geom_xy, highway, PATH_HALF_WIDTH_M, 1.0)
                if poly is not None:
                    focused_poly = clip_to(poly, boundary_xy)
                    if not focused_poly.is_empty:
                        path_shapes.append(focused_poly)
            elif isinstance(geom_xy, (LineString, MultiLineString)):
                draw_geometry(geom_xy, "paths", clipped=city_clip)
            elif isinstance(geom_xy, (Polygon, MultiPolygon)):
                draw_geometry(geom_xy, "paths", outlines=True, clipped=city_clip)

        if args.show_rail and railway & RAIL_TYPES:
            if (tag_values(disused_raw) | tag_values(abandoned_raw)) & {
                "yes"
            }:
                continue
            if not args.include_underground_rail and "subway" in railway:
                continue
            if isinstance(geom_xy, (Polygon, MultiPolygon)):
                draw_geometry(geom_xy.boundary, "rail")
            else:
                draw_geometry(geom_xy, "rail", clipped=city_clip)

    if args.road_mode == "outline":
        if road_shapes:
            print(f"Merging {len(road_shapes)} road shapes...")
            draw_geometry(unary_union(road_shapes), "roads", outlines=True)
        if path_shapes:
            print(f"Merging {len(path_shapes)} path shapes...")
            draw_geometry(unary_union(path_shapes), "paths", outlines=True)
    # Always preserve geographic boundary geometry in the file for later toggling.
    draw_geometry(boundary_xy, "boundary", outlines=True)

    if args.regional_context:
        drawing.add(groups["context"])
    for key in ("paths", "roads", "rail", "boundary"):
        drawing.add(groups[key])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    drawing.save(pretty=False)
    print(f"STAGE svg_rendering_seconds={time.perf_counter()-render_started_at:.2f}", flush=True)
    print(f"STAGE generator_total_seconds={time.perf_counter()-generation_started_at:.2f}", flush=True)
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
        "--regional-context",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Show muted surroundings beyond the focus city's municipal boundary",
    )
    parser.add_argument(
        "--regional-buffer-km",
        type=float,
        default=3.0,
        help="Regional context distance from municipal boundary in kilometers (0.5–10)",
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
    parser.add_argument("--extract-file", type=Path, default=None, help="Optional regional .osm.pbf file; bypasses Overpass")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if not args.city.strip() or not args.state.strip() or not args.country.strip():
        parser.error("City, state, and country must be nonempty")
    if not (0.5 <= args.regional_buffer_km <= 10):
        parser.error("--regional-buffer-km must be between 0.5 and 10 km")
    if args.overpass_timeout <= 0:
        parser.error("--overpass-timeout must be positive")
    if args.width <= 0 or args.height <= 0 or (args.margin is not None and (
        args.margin < 0
        or args.width <= 2 * args.margin
        or args.height <= 2 * args.margin
    )):
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
