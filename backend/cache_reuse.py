"""Request-keyed finished SVG cache. Legacy records fail closed."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

CACHE_SCHEMA = 1
RENDER_VERSION = "city-map-studio-svg-v2.3-border10"
PLACE_FIELDS = ("city", "county", "state", "country")
OPTIONS = ("style", "road_mode", "include_paths", "show_boundary", "regional_context", "regional_buffer_km")


def source_identity(extract_file: str | Path | None = None) -> str:
    """Stable provenance key for an unchanged source file.

    Uses resolved path, byte size, and nanosecond mtime. This avoids hashing
    multi-gigabyte extracts on every request. Replacing a file while preserving
    all three attributes is outside the guarantee; a full content digest would
    be required for cryptographic source identity.
    """
    if not extract_file:
        return "overpass"
    path = Path(extract_file).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"Configured OSM extract is not a file: {path}")
    stat = path.stat()
    if stat.st_size == 0:
        raise ValueError(f"Configured OSM extract is empty: {path}")
    return "extract-" + digest({
        "path": str(path),
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    })[:24]


def extract_candidates(extract_file: str | Path | None) -> list[Path]:
    """The configured extract first, then any other ``*.osm.pbf`` beside it.

    Dropping another region's extract (for example Northern California next
    to Southern California) into the same folder makes it available without
    changing OSM_EXTRACT_FILE (ADR-007).
    """
    if not extract_file:
        return []
    configured = Path(extract_file).expanduser().resolve(strict=True)
    siblings = sorted(
        p.resolve() for p in configured.parent.glob("*.osm.pbf")
        if p.is_file() and p.resolve() != configured
    )
    return [configured] + siblings


def extract_set_identity(extract_file: str | Path | None = None) -> str:
    """Provenance key for every extract the generator may choose from.

    With a single extract this equals ``source_identity`` (unchanged keys);
    adding, replacing or removing a sibling extract gives a new key.
    """
    candidates = extract_candidates(extract_file)
    if len(candidates) <= 1:
        return source_identity(extract_file)
    return "extracts-" + digest([source_identity(p) for p in candidates])[:24]


def geographic_cache_folder(root: Path, place_key: str, source_key: str) -> Path:
    """Preserve legacy Overpass layout; isolate all offline-extract revisions."""
    base = Path(root) / place_key
    return base if source_key == "overpass" else base / source_key


def request_identity(req, generator_sha256: str = "", source_key: str = "overpass") -> tuple[str, str]:
    location = {
        key: " ".join(getattr(req, key).strip().casefold().split())
        for key in PLACE_FIELDS
    }
    geography_key = digest({"location": location, "source": source_key})
    render_key = digest({
        "schema": CACHE_SCHEMA,
        "renderer": RENDER_VERSION,
        "generator_sha256": generator_sha256,
        "geography_key": geography_key,
        "options": {key: getattr(req, key) for key in OPTIONS},
    })
    return geography_key, render_key


def digest(value: dict) -> str:
    packed = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()


def svg_sha256(path: Path) -> str:
    """Hash SVG content to reject partial or corrupted cached exports."""
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def reusable_map(output_dir: Path, render_key: str) -> dict | None:
    """Find matching completed map only if metadata and SVG are valid."""
    for record in output_dir.glob("*.json"):
        try:
            metadata = json.loads(record.read_text(encoding="utf-8"))
            if (metadata.get("cache_schema") != CACHE_SCHEMA or
                    metadata.get("renderer_version") != RENDER_VERSION or
                    metadata.get("render_key") != render_key):
                continue
            identifier = str(uuid.UUID(metadata["id"]))
            if identifier != record.stem:
                continue
            filename = metadata["filename"]
            if not isinstance(filename, str) or not filename.endswith(".svg"):
                continue
            if filename != Path(filename).name:
                continue
            svg = output_dir / filename
            if (svg.is_file() and svg.stat().st_size > 0 and
                    metadata.get("svg_sha256") and
                    metadata["svg_sha256"] == svg_sha256(svg)):
                return metadata
        except (KeyError, OSError, ValueError, TypeError, AttributeError):
            continue
    return None
