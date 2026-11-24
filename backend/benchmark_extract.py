#!/usr/bin/env python3
"""Benchmark one existing city against a regional OSM extract with no Overpass calls.

Example:
 python backend/benchmark_extract.py --extract C:/MapData/massachusetts-latest.osm.pbf \
   --boundary backend/map_data_cache/Somerville_Middlesex_Massachusetts_USA/boundary.geojson \
   --name Somerville --output benchmark_results/somerville.json

The only inputs are a local PBF and local boundary GeoJSON. No internet access
is required. Writes results as JSON; does not mutate the normal project cache.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd

from osm_extract import extract_features

HIGHWAYS = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
    "residential", "living_street", "service", "road", "motorway_link",
    "trunk_link", "primary_link", "secondary_link", "tertiary_link",
    "busway", "track", "pedestrian", "footway", "path", "steps",
    "cycleway", "bridleway", "corridor",
}
RAILWAYS = {"rail", "light_rail", "subway", "tram", "narrow_gauge", "monorail"}


def benchmark(extract: Path, boundary_file: Path, name: str) -> dict:
    extract = extract.expanduser().resolve()
    boundary_file = boundary_file.expanduser().resolve()
    if not boundary_file.is_file():
        raise FileNotFoundError(f"Boundary GeoJSON not found: {boundary_file}")
    if not extract.is_file():
        raise FileNotFoundError(f"Regional extract not found: {extract}")
    boundary_gdf = gpd.read_file(boundary_file).to_crs("EPSG:4326")
    if boundary_gdf.empty:
        raise ValueError("Boundary GeoJSON contains no geometry")
    boundary = boundary_gdf.geometry.union_all()

    # Measure importer time and Python-managed allocations; RSS / process
    # memory is platform-dependent and not represented by tracemalloc.
    tracemalloc.start()
    start = time.perf_counter()
    features = extract_features(extract, boundary, HIGHWAYS, RAILWAYS)
    elapsed = time.perf_counter() - start
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    roads = int(features.highway.isin(HIGHWAYS).sum())
    railway = int(features.railway.isin(RAILWAYS).sum())
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "city": name,
        "source_type": "regional_osm_extract",
        "extract_name": extract.name,
        "extract_bytes": extract.stat().st_size,
        "boundary_file": str(boundary_file),
        "elapsed_seconds": round(elapsed, 3),
        "feature_rows": len(features),
        "road_or_path_rows": roads,
        "railway_rows": railway,
        "python_allocation_peak_bytes": peak,
        "python_allocation_note": (
            "tracemalloc counts Python allocations, not full native "
            "memory/RSS used by pyosmium, GEOS, or GeoPandas."
        ),
        "no_network_requests": True,
        "complete_rendering_measured": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract", required=True, type=Path)
    parser.add_argument("--boundary", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = benchmark(args.extract, args.boundary, args.name)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(".tmp")
    temp.write_text(json.dumps(result, indent=2), encoding="utf-8")
    temp.replace(args.output)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
