#!/usr/bin/env python3
"""Local City Map Studio smoke test (Python standard library only).

Run without --generate first; it checks /api/health and /api/library.
Use --generate only when you want to start a real, potentially lengthy map job.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree


def request_json(base: str, path: str, payload: dict | None = None) -> dict | list:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = Request(base.rstrip("/") + path, data=data, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8001")
    parser.add_argument("--generate", action="store_true", help="Opt in to a real geographic job")
    parser.add_argument("--city", default="Somerville")
    parser.add_argument("--state", default="Massachusetts")
    parser.add_argument("--county", default="Middlesex")
    parser.add_argument("--country", default="USA")
    parser.add_argument("--mode", choices=("centerline", "outline"), default="centerline")
    parser.add_argument("--max-wait", type=int, default=180, help="Maximum wait in seconds (default: 180)")
    args = parser.parse_args()
    try:
        health = request_json(args.base, "/api/health")
        assert health.get("status") == "ok", health
        print("PASS: API health", health.get("version", "unknown"))
        library = request_json(args.base, "/api/library")
        assert isinstance(library, list), type(library)
        print(f"PASS: saved library endpoint ({len(library)} records)")
        if not args.generate:
            print("PASS: safe checks complete. Add --generate to test real map generation.")
            return 0
        if args.max_wait < 1:
            parser.error("--max-wait must be positive")
        body = {
            "city": args.city, "state": args.state, "county": args.county,
            "country": args.country, "road_mode": args.mode,
            "style": "fine", "include_paths": True, "show_boundary": False,
        }
        job = request_json(args.base, "/api/maps", body)
        assert "id" in job, job
        print("Map job", job["id"], "initial status", job.get("status"),
              "cache hit", job.get("cache_hit", False))
        started = time.monotonic()
        deadline = started + args.max_wait
        last_status = None
        while job.get("status") not in ("complete", "error") and time.monotonic() < deadline:
            time.sleep(3)
            job = request_json(args.base, "/api/maps/" + job["id"])
            label = job.get("status")
            position = job.get("queue_position")
            message = label + (f" (queue position {position})" if position else "")
            if message != last_status:
                print(f"Status: {message} · elapsed {time.monotonic()-started:.1f}s")
                last_status = message
        if job.get("status") == "error":
            print("FAIL: generation:", job.get("error"))
            return 1
        if job.get("status") != "complete":
            print("INCOMPLETE: still queued/running; check the backend terminal. "
                  "The job may continue after this script exits.")
            return 2
        link = job.get("download_url")
        if not link or not link.startswith("/api/"):
            raise ValueError(f"Invalid download URL: {link!r}")
        with urlopen(args.base.rstrip("/") + link, timeout=30) as resp:
            svg_bytes = resp.read()
        root = ElementTree.fromstring(svg_bytes)
        if root.tag != "{http://www.w3.org/2000/svg}svg":
            raise ValueError("Download was not a valid SVG document")
        ids = {node.attrib.get("id") for node in root.iter()}
        group = "road-centerlines" if args.mode == "centerline" else "road-edges"
        if group not in ids:
            raise ValueError("Missing expected road group: " + group)
        print("PASS: downloaded valid SVG", len(svg_bytes), "bytes")
        print(
            "TIMING:",
            f"queue={job.get('queue_seconds', 'n/a')}s",
            f"generation={job.get('generation_seconds', 'n/a')}s",
            f"total={job.get('total_seconds', 'n/a')}s",
            f"client_elapsed={time.monotonic()-started:.1f}s",
        )
        second = request_json(args.base, "/api/maps", body)
        if second["id"] != job["id"] or second.get("status") != "complete":
            raise ValueError(f"Second identical request was not reused: {second}")
        print("PASS: identical request reused existing finished map")
        print("ALL LOCAL SMOKE CHECKS PASSED")
        return 0
    except (HTTPError, URLError, OSError, ValueError, AssertionError) as exc:
        print("FAIL:", exc, file=sys.stderr)
        print("Verify both servers are running and the API is reachable.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
