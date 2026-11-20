"""Request-keyed finished SVG cache. Legacy records fail closed."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

CACHE_SCHEMA = 1
RENDER_VERSION = "city-map-studio-svg-v2.1"
PLACE_FIELDS = ("city", "county", "state", "country")
OPTIONS = ("style", "road_mode", "include_paths", "show_boundary")


def request_identity(req, generator_sha256: str = "") -> tuple[str, str]:
    location = {
        key: " ".join(getattr(req, key).strip().casefold().split())
        for key in PLACE_FIELDS
    }
    geography_key = digest({"location": location})
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
