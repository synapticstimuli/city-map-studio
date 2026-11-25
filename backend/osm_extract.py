"""Optional offline import of road/path/railway ways from a regional OSM extract.

This deliberately does not download an extract or change the administrative
boundary. Pyosmium is imported lazily so normal Overpass-based installs work.

v2.3 (see DECISIONS.md, ADR-002):

* The scan filters ways by tag key in C++ and builds way geometry with
  pyosmium's WKB factory, instead of a Python callback per way and per node.
  Ways are handled in fixed-size chunks so memory stays bounded.
* With ``index_dir`` set, the first scan of an extract also writes a
  reusable *road index*: every matching way, unclipped, in spatially indexed
  FlatGeobuf part files. Later cities, modes and regional halos from the same
  unchanged extract read only the ways near them from the index instead of
  rescanning the whole extract.

Both paths return the same features, in the same (file) order, with the same
geometries and tags as the v2.2 importer.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely


FIELDS = (
    "highway", "railway", "disused", "abandoned", "tunnel",
    "service", "area", "access", "bridge", "width",
)
# Bump when the index content or layout changes; old indexes are then ignored.
INDEX_VERSION = 1
INDEX_CRS = "EPSG:4326"
COMPLETE_MARKER = "complete.json"
CHUNK_WAYS = 250_000


def _require_osmium():
    try:
        import osmium
    except ImportError as exc:
        raise RuntimeError(
            "Offline PBF support requires the optional 'osmium' Python package. "
            "Install with: python -m pip install osmium"
        ) from exc
    return osmium


def _check_inputs(extract_path, boundary) -> Path:
    extract_path = Path(extract_path).expanduser().resolve()
    if not extract_path.is_file() or extract_path.stat().st_size == 0:
        raise FileNotFoundError(f"OSM extract is missing or empty: {extract_path}")
    if not boundary.is_valid or boundary.is_empty:
        raise ValueError("Cannot import against an empty or invalid city boundary")
    return extract_path


def scan_extract(extract_path: Path, highway_types: set[str], railway_types: set[str],
                 chunk_ways: int = CHUNK_WAYS):
    """Yield every matching way of the extract, in file order, in chunks.

    Each chunk is ``(wkb, columns)``: a list of binary WKB LineStrings
    (unclipped, WGS84) and a dict of tag lists keyed by ``FIELDS``. Ways with
    an invalid node location or fewer than two nodes are skipped, as in v2.2:
    a partial geometry would create a false road segment.
    """
    osmium = _require_osmium()
    from osmium.geom import WKBFactory, use_nodes

    # ALL keeps repeated consecutive nodes, exactly like building the
    # LineString from every node location.
    make_line = WKBFactory().create_linestring
    every_node = use_nodes.ALL
    invalid_location = osmium.InvalidLocationError
    from_hex = bytes.fromhex
    intern = sys.intern

    def empty_chunk():
        return [], {field: [] for field in FIELDS}

    wkb, columns = empty_chunk()
    column_lists = [columns[field] for field in FIELDS]
    processor = (
        osmium.FileProcessor(str(extract_path))
        .with_locations()
        .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
        .with_filter(osmium.filter.KeyFilter("highway", "railway"))
    )
    try:
        for way in processor:
            tags = way.tags
            if (tags.get("highway") not in highway_types
                    and tags.get("railway") not in railway_types):
                continue
            if len(way.nodes) < 2:
                continue
            try:
                wkb.append(from_hex(make_line(way, every_node)))
            except invalid_location:
                continue
            for values, field in zip(column_lists, FIELDS):
                value = tags.get(field)
                values.append(None if value is None else intern(value))
            if len(wkb) >= chunk_ways:
                yield wkb, columns
                wkb, columns = empty_chunk()
                column_lists = [columns[field] for field in FIELDS]
    except Exception as exc:
        raise RuntimeError(
            "Failed to read OSM extract. Check that the file is a complete "
            "regional .osm.pbf or valid .osm and that it contains node locations."
        ) from exc
    if wkb:
        yield wkb, columns


class ClippedWays:
    """Collects clipped ways across chunks, preserving input order."""

    def __init__(self, boundary):
        self.boundary = boundary
        shapely.prepare(boundary)
        self.geometries = []
        self.columns = {field: [] for field in FIELDS}

    def add(self, geometries, columns) -> None:
        """Vectorized equivalent of the v2.2 per-way logic.

        Envelope check, prepared ``intersects``, ``intersection``, keeping only
        (Multi)LineString results. Ways strictly inside the boundary are
        clipped against a small rectangle around them, which gives the
        identical result far more cheaply (see ADR-001).
        """
        geometries = np.asarray(geometries, dtype=object)
        if not len(geometries):
            return
        boundary = self.boundary
        west, south, east, north = boundary.bounds
        bounds = shapely.bounds(geometries)
        order = np.flatnonzero(~(
            (bounds[:, 2] < west) | (bounds[:, 0] > east)
            | (bounds[:, 3] < south) | (bounds[:, 1] > north)
        ))
        geometries = geometries[order]
        hits = ~shapely.is_empty(geometries) & shapely.intersects(boundary, geometries)
        order, geometries = order[hits], geometries[hits]
        inside = shapely.contains_properly(boundary, geometries)
        clipped = np.empty(len(geometries), dtype=object)
        if inside.any():
            b = shapely.bounds(geometries[inside])
            frames = shapely.box(b[:, 0] - 1, b[:, 1] - 1, b[:, 2] + 1, b[:, 3] + 1)
            clipped[inside] = shapely.intersection(geometries[inside], frames)
        if (~inside).any():
            clipped[~inside] = shapely.intersection(geometries[~inside], boundary)
        kind = shapely.get_type_id(clipped)
        keep = ~shapely.is_empty(clipped) & np.isin(kind, (1, 5))  # Line, MultiLine
        for i, geom in zip(order[keep], clipped[keep]):
            self.geometries.append(geom)
            for field in FIELDS:
                self.columns[field].append(columns[field][i])

    def frame(self) -> gpd.GeoDataFrame:
        if not self.geometries:
            raise RuntimeError("Regional extract contains no matching mapped ways inside this boundary")
        # Built from per-way dicts exactly like v2.2, so pandas infers the same
        # column dtypes and missing-value markers (NaN in mixed columns).
        rows = [dict(zip(FIELDS, values)) for values in zip(*(self.columns[f] for f in FIELDS))]
        return gpd.GeoDataFrame(rows, geometry=self.geometries, crs=INDEX_CRS)


# ==========================================
# REUSABLE ROAD INDEX (one per unchanged extract)
# ==========================================


def index_path(index_dir: Path, extract_path: Path, highway_types, railway_types) -> Path:
    """Index folder for this extract revision and rendered tag set."""
    stat = extract_path.stat()
    key = hashlib.sha256(json.dumps({
        "version": INDEX_VERSION,
        "path": str(extract_path),
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "highway": sorted(highway_types),
        "railway": sorted(railway_types),
    }, sort_keys=True).encode()).hexdigest()[:24]
    return Path(index_dir) / f"roads-v{INDEX_VERSION}-{key}"


def write_index_part(folder: Path, part: int, first_seq: int, wkb, columns) -> None:
    """Write one chunk of ways to a spatially indexed FlatGeobuf file."""
    from pyogrio.raw import write

    field_data = [np.array(columns[field], dtype=object) for field in FIELDS]
    field_data.append(np.arange(first_seq, first_seq + len(wkb), dtype=np.int64))
    write(
        str(folder / f"part-{part:05d}.fgb"),
        geometry=np.array(wkb, dtype=object),
        field_data=field_data,
        fields=list(FIELDS) + ["seq"],
        geometry_type="LineString",
        crs=INDEX_CRS,
        driver="FlatGeobuf",
        layer_options={"SPATIAL_INDEX": "YES"},
    )


def build_index(path: Path, extract_path: Path, highway_types, railway_types) -> dict:
    """Scan the extract once and write its road index, atomically.

    Parts go to a temporary folder that is renamed into place only after
    every part and the completion marker are written, so an interrupted
    build is never mistaken for a usable index.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir()
    ways = parts = 0
    try:
        for wkb, columns in scan_extract(extract_path, highway_types, railway_types):
            write_index_part(temporary, parts, ways, wkb, columns)
            ways += len(wkb)
            parts += 1
        summary = {
            "version": INDEX_VERSION,
            "extract": str(extract_path),
            "extract_bytes": extract_path.stat().st_size,
            "extract_mtime_ns": extract_path.stat().st_mtime_ns,
            "ways": ways,
            "parts": parts,
        }
        (temporary / COMPLETE_MARKER).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        if (path / COMPLETE_MARKER).is_file():  # Another job finished it first.
            shutil.rmtree(temporary, ignore_errors=True)
        else:
            shutil.rmtree(path, ignore_errors=True)  # Leftover without a marker.
            os.replace(temporary, path)
        return summary
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def read_index(path: Path, boundary, clipped: ClippedWays) -> int:
    """Clip every indexed way whose envelope meets the boundary's bounding box.

    Returns the number of candidate ways read. Ways are fed in original file
    order (by ``seq``) so the result matches a full scan exactly.
    """
    from pyogrio import read_dataframe

    summary = json.loads((path / COMPLETE_MARKER).read_text(encoding="utf-8"))
    frames = []
    for part in range(summary["parts"]):
        frame = read_dataframe(str(path / f"part-{part:05d}.fgb"), bbox=tuple(boundary.bounds))
        if len(frame):
            frames.append(frame)
    if not frames:
        return 0
    frame = gpd.pd.concat(frames, ignore_index=True).sort_values("seq", kind="stable")
    columns = {
        field: [None if value is None or value != value else value
                for value in frame[field].tolist()]
        for field in FIELDS
    }
    clipped.add(frame.geometry.to_numpy(), columns)
    return len(frame)


def extract_features(
    extract_path: Path,
    boundary,
    highway_types: set[str],
    railway_types: set[str],
    index_dir: Path | None = None,
) -> gpd.GeoDataFrame:
    """Parse tagged OSM ways and clip to a WGS84 administrative boundary.

    Reference-complete PBF extracts are preferred. Polygon highways are
    intentionally represented by their constituent way centerlines in this
    first version; relation-derived features are not assembled.

    With ``index_dir``, the road index for this extract is reused or, on the
    first call, built. Without it, the whole extract is scanned every time.
    """
    _require_osmium()
    extract_path = _check_inputs(extract_path, boundary)
    clipped = ClippedWays(boundary)

    if index_dir is not None:
        path = index_path(Path(index_dir), extract_path, highway_types, railway_types)
        if not (path / COMPLETE_MARKER).is_file():
            print(f"Importing local OSM extract: {extract_path}", flush=True)
            print("Building a road index for this extract; later maps from it "
                  "will skip the full scan", flush=True)
            started = time.perf_counter()
            try:
                summary = build_index(path, extract_path, highway_types, railway_types)
            except RuntimeError:
                raise
            except Exception as exc:
                # Disk full, permissions, ...: the map can still be made.
                print(f"Could not write road index ({exc}); scanning without it", flush=True)
                index_dir = None
            else:
                print(f"STAGE road_index_build_seconds={time.perf_counter()-started:.2f}", flush=True)
                print(f"Saved road index ({summary['ways']} ways): {path}", flush=True)
        if index_dir is not None:
            started = time.perf_counter()
            print(f"Using road index for local OSM extract: {path.name}", flush=True)
            try:
                read_index(path, boundary, clipped)
            except Exception as exc:  # A damaged index is never trusted.
                print(f"Road index unreadable ({exc}); removing it and scanning", flush=True)
                shutil.rmtree(path, ignore_errors=True)
                clipped = ClippedWays(boundary)
                index_dir = None
            else:
                print(f"STAGE road_index_read_seconds={time.perf_counter()-started:.2f}", flush=True)

    if index_dir is None:
        print(f"Importing local OSM extract: {extract_path}", flush=True)
        started = time.perf_counter()
        for wkb, columns in scan_extract(extract_path, highway_types, railway_types):
            clipped.add(shapely.from_wkb(np.array(wkb, dtype=object)), columns)
        print(f"STAGE extract_scan_seconds={time.perf_counter()-started:.2f}", flush=True)

    features = clipped.frame()
    print(f"Imported {len(features)} clipped OSM ways from extract", flush=True)
    return features
