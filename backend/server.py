"""Local-only City Map Studio v2 API (prototype, not public-deployment hardened)."""

from __future__ import annotations

import os
import logging
import hashlib
import ipaddress
import re
import json
import subprocess
import sys
import threading
import time
import uuid
from xml.etree import ElementTree
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from cache_reuse import CACHE_SCHEMA, RENDER_VERSION, request_identity, reusable_map, svg_sha256, source_identity


# ==========================================
# APPLICATION CONFIGURATION
# ==========================================

ROOT = Path(__file__).resolve().parent

APP_ENV = os.getenv("APP_ENV", "development")

DATA_DIR = Path(os.getenv("MAP_DATA_DIR", str(ROOT))).resolve()

OUTPUT_DIR = DATA_DIR / "generated_maps"
CACHE_DIR = DATA_DIR / "map_data_cache"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

GENERATOR = ROOT / "generate_city_map_v2_1.py"
GENERATOR_SHA256 = hashlib.sha256(GENERATOR.read_bytes()).hexdigest()

MAX_JOBS = int(os.getenv("MAX_JOBS", "20"))
JOB_TIMEOUT = int(os.getenv("JOB_TIMEOUT", "1200"))
OVERPASS_TIMEOUT = int(os.getenv("OVERPASS_TIMEOUT", "300"))
OSM_EXTRACT_FILE = os.getenv("OSM_EXTRACT_FILE", "").strip()

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
    ).split(",")
    if origin.strip()
]

# ==========================================
# API INITIALIZATION
# ==========================================

app = FastAPI(title="City Map Studio API", version="2.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

executor = ThreadPoolExecutor(max_workers=1)

lock = threading.Lock()
jobs: dict[str, dict] = {}
logger = logging.getLogger('city-map-studio')


class MapRequest(BaseModel):
    city: str = Field(min_length=2, max_length=80)
    state: str = Field(min_length=2, max_length=80)
    county: str = Field(default="", max_length=80)
    country: str = Field(default="USA", min_length=2, max_length=80)

    style: str = Field(default="fine", pattern="^(fine|detailed|bold)$")

    road_mode: str = Field(default="outline", pattern="^(centerline|outline)$")

    include_paths: bool = True
    show_boundary: bool = False
    regional_context: bool = False
    regional_buffer_km: float = Field(default=3.0, ge=0.5, le=10.0)

    @field_validator("city", "state", "county", "country")
    @classmethod
    def safe_place(cls, value, info):
        value = value.strip()

        if info.field_name == "county" and not value:
            return ""

        if len(value) < 2:
            raise ValueError("Please enter a valid place name")

        if not re.fullmatch(r"[\w\s.,'()\-]+", value, flags=re.UNICODE):
            raise ValueError("Only ordinary place-name characters are allowed")

        return value


def public(job):
    return {k: v for k, v in job.items() if k not in ("path", "_queued_monotonic")}


def svg_contains_nonempty_group(path: Path, group_id: str) -> bool:
    """Stream-validate a populated SVG layer without loading the entire map."""
    depth = 0
    found_path = False
    for event, node in ElementTree.iterparse(path, events=("start", "end")):
        if event == "start":
            if depth:
                depth += 1
                if node.tag.rsplit("}", 1)[-1] == "path":
                    found_path = True
            elif node.get("id") == group_id:
                depth = 1
        elif depth:
            depth -= 1
            if depth == 0:
                return found_path
        if event == "end":
            node.clear()
    return False


def run_job(job_id: str, req: MapRequest):
    with lock:
        job = jobs[job_id]
        started_monotonic = time.monotonic()
        job["status"] = "running"
        job["started_at"] = datetime.now(timezone.utc).isoformat()
        job["queue_seconds"] = round(started_monotonic - job["_queued_monotonic"], 2)
        expected_source_key = job["source_key"]
    filename = re.sub("[^A-Za-z0-9_-]+", "_", req.city).strip("_") or "Map"
    destination = OUTPUT_DIR / f"{filename}_{job_id[:8]}.svg"
    command = [
        sys.executable,
        "-u",
        str(GENERATOR),
        "--city",
        req.city,
        "--county",
        req.county,
        "--state",
        req.state,
        "--country",
        req.country,
        "--style",
        req.style,
        "--road-mode",
        req.road_mode,
        "--output",
        str(destination),
        "--cache-dir",
        str(CACHE_DIR),
        "--overpass-timeout",
        str(OVERPASS_TIMEOUT),
        "--include-paths" if req.include_paths else "--no-include-paths",
        "--show-boundary" if req.show_boundary else "--no-show-boundary",
        "--regional-context" if req.regional_context else "--no-regional-context",
        "--regional-buffer-km",
        str(req.regional_buffer_km),
    ]
    if OSM_EXTRACT_FILE:
        command.extend(["--extract-file", OSM_EXTRACT_FILE])
    try:
        if source_identity(OSM_EXTRACT_FILE) != expected_source_key:
            raise RuntimeError("OSM extract changed while job was queued. Submit a fresh request.")
        # Persist subprocess diagnostics per job. On failure show a bounded,
        # actionable excerpt instead of hiding the generator's exception.
        log_path = OUTPUT_DIR / f"{job_id}.log"
        with log_path.open("w", encoding="utf-8") as log_stream:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=JOB_TIMEOUT,
                check=False,
            )

        if completed.returncode != 0 or not destination.is_file():
            excerpt = log_path.read_text(encoding="utf-8", errors="replace")
            lines = [line.strip() for line in excerpt.splitlines() if line.strip()]
            if "GeographicQueryTooLarge" in excerpt:
                raise RuntimeError(
                    "Geographic request exceeds the safe Overpass area limit. "
                    "For large cities such as Los Angeles, configure a regional "
                    "California OSM extract (OSM_EXTRACT_FILE). "
                    + (lines[-1][:350] if lines else "")
                )
            if any(word in excerpt.lower() for word in ("no boundary found", "geocode", "nominatim")):
                raise RuntimeError(
                    "Could not resolve the municipal boundary. Verify city, state, "
                    "country and county (Los Angeles city is in Los Angeles County). "
                    + (lines[-1][:350] if lines else "")
                )
            raise RuntimeError(
                "Map generation failed; see backend/generated_maps/"
                f"{job_id}.log. Last diagnostic: "
                + (lines[-1][:450] if lines else "Generator did not produce an SVG.")
            )

        if req.regional_context and not svg_contains_nonempty_group(
            destination, "regional-context"
        ):
            raise RuntimeError(
                "The generator did not produce the required populated regional-context "
                "SVG layer. The result was discarded; check the job log and backend version."
            )
        if source_identity(OSM_EXTRACT_FILE) != expected_source_key:
            raise RuntimeError("OSM extract changed during generation; result discarded.")
        geography_key, render_key = request_identity(req, GENERATOR_SHA256, expected_source_key)
        metadata = {
            "source_key": expected_source_key,
            "svg_sha256": svg_sha256(destination),
            "cache_schema": CACHE_SCHEMA,
            "renderer_version": RENDER_VERSION,
            "geography_key": geography_key,
            "render_key": render_key,
            "include_paths": req.include_paths,
            "show_boundary": req.show_boundary,
            "regional_context": req.regional_context,
            "regional_buffer_km": req.regional_buffer_km,
            "id": job_id,
            "city": req.city,
            "county": req.county,
            "state": req.state,
            "country": req.country,
            "road_mode": req.road_mode,
            "style": req.style,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "filename": destination.name,
            "download_url": f"/api/library/{job_id}/download",
        }

        metadata_file = OUTPUT_DIR / f"{job_id}.json"

        # Write metadata atomically.
        temporary_file = metadata_file.with_suffix(".tmp")
        temporary_file.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        temporary_file.replace(metadata_file)

        with lock:
            jobs[job_id].update(
                status="complete",
                path=destination,
                download_url=metadata["download_url"],
                finished_at=datetime.now(timezone.utc).isoformat(),
                generation_seconds=round(time.monotonic() - started_monotonic, 2),
                total_seconds=round(time.monotonic() - jobs[job_id]["_queued_monotonic"], 2),
            )

    except subprocess.TimeoutExpired as exc:
        logger.exception("Generation timed out for job %s", job_id)
        destination.unlink(missing_ok=True)
        with lock:
            jobs[job_id].update(
                status="error",
                error=f"Generation exceeded the {JOB_TIMEOUT}s backend timeout. "
                      "For large cities try a regional OSM extract or a smaller location.",
                finished_at=datetime.now(timezone.utc).isoformat(),
                generation_seconds=round(time.monotonic() - started_monotonic, 2),
                total_seconds=round(time.monotonic() - jobs[job_id]["_queued_monotonic"], 2),
            )
    except Exception as exc:
        logger.exception("Generation failed for job %s", job_id)
        destination.unlink(missing_ok=True)
        with lock:
            jobs[job_id].update(
                status="error",
                error=str(exc)[:1200],
                finished_at=datetime.now(timezone.utc).isoformat(),
                generation_seconds=round(time.monotonic() - started_monotonic, 2),
                total_seconds=round(time.monotonic() - jobs[job_id]["_queued_monotonic"], 2),
            )


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "application": "City Map Studio",
        "version": "2.2.0",
        "environment": APP_ENV,
        "capabilities": {"regional_context": True, "regional_context_layer": "regional-context"},
    }


@app.post("/api/maps", status_code=202)
def create_map(req: MapRequest, request: Request):
    if APP_ENV.casefold() == "production":
        raise HTTPException(503, "Public map generation is not enabled.")
    try:
        local_request = ipaddress.ip_address(request.client.host).is_loopback
    except (ValueError, AttributeError, TypeError):
        local_request = False
    if not local_request:
        raise HTTPException(403, "Map generation is available only on localhost.")

    try:
        source_key = source_identity(OSM_EXTRACT_FILE)
    except (OSError, ValueError) as exc:
        raise HTTPException(422, f"Configured regional extract unavailable: {exc}") from exc
    geography_key, render_key = request_identity(req, GENERATOR_SHA256, source_key)
    with lock:
        for existing in jobs.values():
            if existing.get("render_key") == render_key and existing["status"] in ("queued", "running"):
                return public(existing)

        cached = reusable_map(OUTPUT_DIR, render_key)
        if cached is not None and req.regional_context:
            try:
                if not svg_contains_nonempty_group(
                    OUTPUT_DIR / cached["filename"], "regional-context"
                ):
                    cached = None
            except (ElementTree.ParseError, OSError):
                cached = None
        if cached is not None:
            jid = cached["id"]
            jobs[jid] = {
                "id": jid,
                "status": "complete",
                "path": OUTPUT_DIR / cached["filename"],
                "city": cached["city"],
                "state": cached["state"],
                "created_at": cached.get("created_at"),
                "download_url": f"/api/library/{jid}/download",
                "render_key": render_key,
                "cache_hit": True,
                "regional_context": bool(cached.get("regional_context", False)),
                "regional_buffer_km": cached.get("regional_buffer_km", 3.0),
            }
            return public(jobs[jid])

        active_jobs = sum(
            job["status"] in ("queued", "running") for job in jobs.values()
        )
        if active_jobs >= MAX_JOBS:
            raise HTTPException(
                429, "Map generation queue is full. Please try again later."
            )
        jid = str(uuid.uuid4())
        jobs[jid] = {
            "id": jid,
            "status": "queued",
            "render_key": render_key,
            "source_key": source_key,
            "cache_hit": False,
            "regional_context": req.regional_context,
            "regional_buffer_km": req.regional_buffer_km,
            "_queued_monotonic": time.monotonic(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "city": req.city,
            "state": req.state,
        }
    executor.submit(run_job, jid, req)
    return public(jobs[jid])


@app.get("/api/maps/{job_id}")
def map_status(job_id: str):
    with lock:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Unknown job")
        result = public(job)
        if job["status"] == "queued":
            result["queue_position"] = sum(
                j["status"] == "queued" and j["_queued_monotonic"] < job["_queued_monotonic"]
                for j in jobs.values()
            ) + 1
        return result


@app.get("/api/maps/{job_id}/download")
def download(job_id: str):
    with lock:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Unknown job")
        if job["status"] != "complete":
            raise HTTPException(409, "Map not ready")
        filepath = job["path"]
    if not filepath.is_file():
        raise HTTPException(404, "SVG file not found")
    return FileResponse(filepath, media_type="image/svg+xml", filename=filepath.name)


# ==========================================
# PERSISTENT MAP LIBRARY
# ==========================================


@app.get("/api/library")
def list_saved_maps():
    saved_maps = []

    for metadata_file in OUTPUT_DIR.glob("*.json"):
        try:
            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))

            filename = metadata["filename"]

            if Path(filename).name != filename:
                continue

            if (OUTPUT_DIR / filename).is_file():
                saved_maps.append(metadata)

        except (OSError, ValueError, KeyError, TypeError):
            continue

    return sorted(saved_maps, key=lambda item: item.get("created_at", ""), reverse=True)


@app.get("/api/library/{map_id}/download")
def download_saved_map(map_id: uuid.UUID):
    metadata_file = OUTPUT_DIR / f"{map_id}.json"

    if not metadata_file.is_file():
        raise HTTPException(404, "Saved map not found")

    metadata = json.loads(metadata_file.read_text(encoding="utf-8"))

    filename = metadata["filename"]

    if Path(filename).name != filename:
        raise HTTPException(400, "Invalid filename")

    filepath = OUTPUT_DIR / filename

    if not filepath.is_file():
        raise HTTPException(404, "SVG file not found")

    return FileResponse(filepath, media_type="image/svg+xml", filename=filepath.name)
