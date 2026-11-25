#!/usr/bin/env python3
"""Write a deterministic synthetic regional OSM PBF for offline import benchmarks.

The file imitates the shape of a regional Geofabrik extract: a large area of
nodes and ways, most of which are NOT roads (buildings, landuse, untagged
geometry), with a street grid and rail lines spread over it. A small target
city sits in one corner, so an importer that scans the whole extract pays for
everything while only a fraction of ways matters.

Example:
  python backend/synthetic_pbf.py --grid 400 --buildings 300000 \
      --output benchmark_results/synthetic-region.osm.pbf
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path

REGION_WEST, REGION_SOUTH = -119.0, 33.5
REGION_SIZE = 1.5  # degrees: a regional extract, much larger than one city
HIGHWAYS = ["residential"] * 6 + [
    "tertiary", "secondary", "primary", "service", "footway", "cycleway", "track",
]


def target_city_bounds() -> tuple[float, float, float, float]:
    """A city-sized box inside the region (west, south, east, north)."""
    west, south = REGION_WEST + 0.2, REGION_SOUTH + 0.2
    return west, south, west + 0.2, south + 0.17


def write_synthetic_pbf(path: Path, grid: int = 400, buildings: int = 300_000,
                        seed: int = 7, header_box=None) -> dict:
    """``header_box`` (west, south, east, north) is the coverage the file
    declares in its header, as Geofabrik extracts do; default: the region."""
    import osmium

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    rng = random.Random(seed)
    step = REGION_SIZE / grid
    node_id = 0
    way_id = 0
    counts = {"nodes": 0, "ways": 0, "highway_ways": 0, "rail_ways": 0}

    header = osmium.io.Header()
    west, south, east, north = header_box or (
        REGION_WEST, REGION_SOUTH, REGION_WEST + REGION_SIZE, REGION_SOUTH + REGION_SIZE)
    header.add_box(osmium.osm.Box(osmium.osm.Location(west, south),
                                  osmium.osm.Location(east, north)))
    writer = osmium.SimpleWriter(str(path), header=header)
    try:
        # Grid intersections plus one mid-block node per block, so each block
        # way has three nodes. Node ids are laid out so ways can refer to them.
        def grid_id(i, j):
            return 1 + i * (grid + 1) + j

        for i in range(grid + 1):
            for j in range(grid + 1):
                writer.add_node(osmium.osm.mutable.Node(
                    id=grid_id(i, j),
                    location=(REGION_WEST + j * step, REGION_SOUTH + i * step),
                ))
        node_id = grid_id(grid, grid)
        counts["nodes"] = node_id

        mid_nodes = []  # (way node list, tags) created after all nodes
        def mid(lon, lat):
            nonlocal node_id
            node_id += 1
            writer.add_node(osmium.osm.mutable.Node(id=node_id, location=(lon, lat)))
            return node_id

        for i in range(grid + 1):
            for j in range(grid):
                tag = HIGHWAYS[(i * 7 + j) % len(HIGHWAYS)]
                lat = REGION_SOUTH + i * step
                m = mid(REGION_WEST + (j + 0.5) * step, lat + step * 0.03)
                mid_nodes.append(([grid_id(i, j), m, grid_id(i, j + 1)], {"highway": tag}))
                lon = REGION_WEST + i * step
                m = mid(lon + step * 0.03, REGION_SOUTH + (j + 0.5) * step)
                mid_nodes.append(([grid_id(j, i), m, grid_id(j + 1, i)], {"highway": tag}))

        # Buildings: small closed squares with their own nodes; never rendered.
        building_ways = []
        size = step * 0.1
        for _ in range(buildings):
            lon = REGION_WEST + rng.random() * REGION_SIZE
            lat = REGION_SOUTH + rng.random() * REGION_SIZE
            ids = [mid(lon, lat), mid(lon + size, lat), mid(lon + size, lat + size),
                   mid(lon, lat + size)]
            building_ways.append((ids + [ids[0]], {"building": "yes"}))
        counts["nodes"] = node_id

        for nodes, tags in mid_nodes:
            way_id += 1
            writer.add_way(osmium.osm.mutable.Way(id=way_id, nodes=nodes, tags=tags))
            counts["highway_ways"] += 1
        # Rail lines running across the whole region along grid rows.
        for k in range(1, grid, max(1, grid // 8)):
            way_id += 1
            writer.add_way(osmium.osm.mutable.Way(
                id=way_id,
                nodes=[grid_id(k, j) for j in range(grid + 1)],
                tags={"railway": "rail"},
            ))
            counts["rail_ways"] += 1
        for nodes, tags in building_ways:
            way_id += 1
            writer.add_way(osmium.osm.mutable.Way(id=way_id, nodes=nodes, tags=tags))
        counts["ways"] = way_id
    finally:
        writer.close()
    counts["bytes"] = path.stat().st_size
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--grid", type=int, default=400)
    parser.add_argument("--buildings", type=int, default=300_000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(write_synthetic_pbf(args.output, args.grid, args.buildings))


if __name__ == "__main__":
    main()
