"""Local-only City Map Studio v2 API (prototype, not public-deployment hardened)."""

from __future__ import annotations

import re
import json
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "generated_maps"
CACHE_DIR = ROOT / "map_data_cache"
OUTPUT_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)
GENERATOR = ROOT / "generate_city_map_v2_1.py"

app = FastAPI(title="City Map Studio local generation API", version="0.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
executor = ThreadPoolExecutor(max_workers=1)
lock = threading.Lock()
jobs: dict[str, dict] = {}
MAX_JOBS = 20


class MapRequest(BaseModel):
    city: str = Field(min_length=2, max_length=80)
    state: str = Field(min_length=2, max_length=80)
    county: str = Field(default="", max_length=80)
    country: str = Field(default="USA", min_length=2, max_length=80)
    style: str = Field(default="fine", pattern="^(fine|detailed|bold)$")
    road_mode: str = Field(default="outline", pattern="^(centerline|outline)$")
    include_paths: bool = True
    show_boundary: bool = False


@field_validator("city", "state", "county", "country")
@classmethod
def safe_place(cls, value, info):
    value = value.strip()

    # County is optional.
    if info.field_name == "county" and not value:
        return ""

    # All other location fields remain required.
    if len(value) < 2:
        raise ValueError("Please enter a valid place name")

    if not re.fullmatch(r"[\w\s.,'()\-]+", value, flags=re.UNICODE):
        raise ValueError("Only ordinary place-name characters are allowed")

    return value


def public(job):
    return {k: v for k, v in job.items() if k not in ("path",)}


def run_job(job_id: str, req: MapRequest):
    with lock:
        jobs[job_id]["status"] = "running"
    filename = re.sub("[^A-Za-z0-9_-]+", "_", req.city).strip("_") or "Map"
    destination = OUTPUT_DIR / f"{filename}_{job_id[:8]}.svg"
    command = [
        sys.executable,
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
        "300",
        "--include-paths" if req.include_paths else "--no-include-paths",
        "--show-boundary" if req.show_boundary else "--no-show-boundary",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=1200,
            check=False,
        )
        if completed.returncode != 0 or not destination.is_file():
            raise RuntimeError((completed.stdout or "Map generation failed")[-2500:])

        metadata = {
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
            )

    except Exception as exc:
        with lock:
            jobs[job_id].update(status="error", error=str(exc)[:1200])


@app.get("/api/health")
def health():
    return {"status": "ok", "mode": "local-prototype"}


@app.post("/api/maps", status_code=202)
def create_map(req: MapRequest):
    with lock:
        active_jobs = sum(
            job["status"] in ("queued", "running") for job in jobs.values()
        )
        if active_jobs >= MAX_JOBS:
            raise HTTPException(
                429, "Local test queue is full; restart server before more jobs"
            )
        jid = str(uuid.uuid4())
        jobs[jid] = {
            "id": jid,
            "status": "queued",
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
        return public(job)


@app.get("/api/maps/{job_id}/download")
def download(job_id: str):
    with lock:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Unknown job")
        if job["status"] != "complete":
            raise HTTPException(409, "Map not ready")
        filepath = job["path"]
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
