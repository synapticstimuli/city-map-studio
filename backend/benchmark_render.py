#!/usr/bin/env python3
"""Offline, repeatable SVG-rendering benchmark on a synthetic large city.

No network, PBF or real OSM data is needed. The script builds a deterministic
city with a detailed (many-vertex) boundary and a dense street grid, seeds a
private geographic cache with it, then times ``create_svg`` for each requested
mode. It reports per-mode wall time and a SHA-256 of each SVG so renderer
changes can be checked for byte-identical output.

Example:
  python backend/benchmark_render.py --grid 120 --boundary-vertices 4000 \
      --output benchmark_results/render.json

The synthetic city only stresses the renderer. It does not replace timing a
real city log (STAGE lines) or a real-data acceptance test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sys
import tempfile
import time
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

import geopandas as gpd  # noqa: E402
from shapely.geometry import LineString, Polygon  # noqa: E402

from cache_reuse import geographic_cache_folder  # noqa: E402
from generate_city_map_v2_1 import create_svg, regional_cache_name  # noqa: E402

CITY = "Synthetic"
STATE = "Benchmark"
COUNTRY = "USA"
PLACE_KEY = f"{CITY}_Location_{STATE}_{COUNTRY}"  # empty county slugs to "Location"
CENTER_LON, CENTER_LAT = -118.25, 34.05
PAGE_WIDTH, PAGE_HEIGHT = 610, 914  # 24 x 36 inch portrait
RADIUS_DEG = 0.12  # roughly 22 km across: a large city, not LA's full extent


def synthetic_boundary(vertices: int) -> Polygon:
    """A wiggly, star-like municipal border with many vertices."""
    points = []
    for i in range(vertices):
        angle = 2 * math.pi * i / vertices
        radius = RADIUS_DEG * (
            1 + 0.18 * math.sin(7 * angle) + 0.06 * math.sin(53 * angle)
        )
        points.append((
            CENTER_LON + radius * math.cos(angle),
            CENTER_LAT + radius * math.sin(angle) * 0.83,
        ))
    return Polygon(points)


def synthetic_features(grid: int, extent: float) -> gpd.GeoDataFrame:
    """A street grid (split into blocks) plus paths and rail lines.

    ``extent`` > 1 extends streets past the city so regional context exists.
    """
    half = RADIUS_DEG * 1.3 * extent
    step = 2 * half / grid
    highway_cycle = ["residential"] * 6 + ["tertiary", "secondary", "primary", "service", "footway"]
    rows, geoms = [], []
    for i in range(grid + 1):
        offset = -half + i * step
        for j in range(grid):
            start, end = -half + j * step, -half + (j + 1) * step
            tag = highway_cycle[(i * 7 + j) % len(highway_cycle)]
            # East-west block, slightly bent so it is not a 2-point line.
            geoms.append(LineString([
                (CENTER_LON + start, CENTER_LAT + offset),
                (CENTER_LON + (start + end) / 2, CENTER_LAT + offset + step * 0.03),
                (CENTER_LON + end, CENTER_LAT + offset),
            ]))
            rows.append({"highway": tag, "railway": None})
            # North-south block.
            geoms.append(LineString([
                (CENTER_LON + offset, CENTER_LAT + start),
                (CENTER_LON + offset + step * 0.03, CENTER_LAT + (start + end) / 2),
                (CENTER_LON + offset, CENTER_LAT + end),
            ]))
            rows.append({"highway": tag, "railway": None})
    for k in range(6):
        y = CENTER_LAT - half + (k + 0.5) * 2 * half / 6
        geoms.append(LineString([(CENTER_LON - half, y), (CENTER_LON + half, y + 0.01)]))
        rows.append({"highway": None, "railway": "rail"})
    return gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")


def seed_cache(cache_dir: Path, regional: bool, boundary: Polygon, grid: int) -> int:
    folder = geographic_cache_folder(cache_dir, PLACE_KEY, "overpass")
    if regional:
        folder = folder / regional_cache_name(PAGE_WIDTH, PAGE_HEIGHT)
    folder.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(geometry=[boundary], crs="EPSG:4326").to_file(
        folder / "boundary.geojson", driver="GeoJSON"
    )
    features = synthetic_features(grid, 1.6 if regional else 1.0)
    features.to_file(folder / "osm_features.geojson", driver="GeoJSON")
    return len(features)


def render_args(cache_dir: Path, output: Path, regional: bool, road_mode: str):
    return SimpleNamespace(
        city=CITY, county="", state=STATE, country=COUNTRY,
        style="detailed", road_mode=road_mode, include_paths=True,
        show_boundary=True, regional_context=regional, regional_buffer_km=3.0,
        show_rail=True, include_service=True, include_tracks=False,
        include_underground_rail=False, width=PAGE_WIDTH, height=PAGE_HEIGHT, margin=None,
        road_stroke=None, boundary_stroke=None, refresh_data=False,
        overpass_timeout=300, cache_dir=cache_dir, extract_file=None,
        output=output,
    )


def run(grid: int, vertices: int, modes: list[str], keep_svgs: Path | None) -> dict:
    boundary = synthetic_boundary(vertices)
    results = []
    with tempfile.TemporaryDirectory() as temp:
        cache_dir = Path(temp) / "cache"
        counts = {}
        for regional in (False, True):
            counts[regional] = seed_cache(cache_dir, regional, boundary, grid)
        for mode in modes:
            regional = mode.startswith("regional")
            road_mode = mode.split("-", 1)[1]
            output = (keep_svgs or Path(temp)) / f"{mode}.svg"
            output.parent.mkdir(parents=True, exist_ok=True)
            log = StringIO()
            started = time.perf_counter()
            with redirect_stdout(log):
                create_svg(render_args(cache_dir, output, regional, road_mode))
            elapsed = time.perf_counter() - started
            stages = {
                line.split()[1].split("=")[0]: float(line.split("=")[1])
                for line in log.getvalue().splitlines() if line.startswith("STAGE ")
            }
            data = output.read_bytes()
            results.append({
                "mode": mode,
                "features": counts[regional],
                "wall_seconds": round(elapsed, 3),
                "stages": stages,
                "svg_bytes": len(data),
                "svg_sha256": hashlib.sha256(data).hexdigest(),
            })
            print(f"{mode:22s} {elapsed:8.2f} s  {len(data):>11,d} bytes  "
                  f"{results[-1]['svg_sha256'][:12]}", file=sys.stderr)
    return {
        "grid": grid,
        "boundary_vertices": vertices,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--grid", type=int, default=120,
                        help="Streets per side of the synthetic grid (default 120)")
    parser.add_argument("--boundary-vertices", type=int, default=4000)
    parser.add_argument(
        "--modes", nargs="+",
        default=["city-centerline", "city-outline", "regional-centerline", "regional-outline"],
        choices=["city-centerline", "city-outline", "regional-centerline", "regional-outline"],
    )
    parser.add_argument("--keep-svgs", type=Path, help="Directory to keep rendered SVGs")
    parser.add_argument("--output", type=Path, help="Write JSON results here")
    args = parser.parse_args()
    report = run(args.grid, args.boundary_vertices, args.modes, args.keep_svgs)
    text = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
