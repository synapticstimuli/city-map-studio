"""Optional offline import of road/path/railway ways from a regional OSM extract.

This deliberately does not download an extract or change the administrative
boundary. Pyosmium is imported lazily so normal Overpass-based installs work.
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString
from shapely.prepared import prep


FIELDS = (
    "highway", "railway", "disused", "abandoned", "tunnel",
    "service", "area", "access", "bridge", "width",
)


def extract_features(
    extract_path: Path,
    boundary,
    highway_types: set[str],
    railway_types: set[str],
) -> gpd.GeoDataFrame:
    """Parse tagged OSM ways and clip to a WGS84 administrative boundary.

    Reference-complete PBF extracts are preferred. Polygon highways are
    intentionally represented by their constituent way centerlines in this
    first version; relation-derived features are not assembled.
    """
    try:
        import osmium
    except ImportError as exc:
        raise RuntimeError(
            "Offline PBF support requires the optional 'osmium' Python package. "
            "Install with: python -m pip install osmium"
        ) from exc

    extract_path = Path(extract_path).expanduser().resolve()
    if not extract_path.is_file() or extract_path.stat().st_size == 0:
        raise FileNotFoundError(f"OSM extract is missing or empty: {extract_path}")
    if not boundary.is_valid or boundary.is_empty:
        raise ValueError("Cannot import against an empty or invalid city boundary")

    match_boundary = prep(boundary)
    west, south, east, north = boundary.bounds
    rows = []
    geometries = []

    class WayCollector(osmium.SimpleHandler):
        def way(self, way):
            # Only use tags rendered by our SVG pipeline.
            highway = way.tags.get("highway")
            railway = way.tags.get("railway")
            if highway not in highway_types and railway not in railway_types:
                return

            coordinates = []
            for node in way.nodes:
                if not node.location.valid():
                    # A partial geometry would create a false road segment.
                    return
                coordinates.append((node.lon, node.lat))

            if len(coordinates) < 2:
                return
            if not any(west <= lon <= east and south <= lat <= north
                       for lon, lat in coordinates):
                # A line can cross an area despite both endpoints being outside;
                # check a cheap way-envelope overlap below instead.
                if (max(lon for lon, _ in coordinates) < west or
                    min(lon for lon, _ in coordinates) > east or
                    max(lat for _, lat in coordinates) < south or
                    min(lat for _, lat in coordinates) > north):
                    return
            line = LineString(coordinates)
            if line.is_empty or not match_boundary.intersects(line):
                return
            clipped = line.intersection(boundary)
            if clipped.is_empty:
                return
            # Preserve OSM way tags for downstream filtering. Renderer can
            # handle LineString and MultiLineString directly.
            if clipped.geom_type not in ("LineString", "MultiLineString"):
                return
            rows.append({field: way.tags.get(field) for field in FIELDS})
            geometries.append(clipped)

    print(f"Importing local OSM extract: {extract_path}", flush=True)
    collector = WayCollector()
    try:
        collector.apply_file(str(extract_path), locations=True, idx="flex_mem")
    except Exception as exc:
        raise RuntimeError(
            "Failed to read OSM extract. Check that the file is a complete "
            "regional .osm.pbf or valid .osm and that it contains node locations."
        ) from exc

    if not geometries:
        raise RuntimeError("Regional extract contains no matching mapped ways inside this boundary")
    features = gpd.GeoDataFrame(rows, geometry=geometries, crs="EPSG:4326")
    print(f"Imported {len(features)} clipped OSM ways from extract", flush=True)
    return features
