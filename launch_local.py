#!/usr/bin/env python3
"""Run City Map Studio locally with one command; Python 3.12 recommended.

Creates a local virtual environment and installs core requirements only on
first launch. Both services bind to 127.0.0.1, not the public network.
Ctrl+C terminates both processes.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time
import venv
import webbrowser
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PYTHON = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
FRONTEND_URL = "http://127.0.0.1:8000"
BACKEND_URL = "http://127.0.0.1:8001/api/health"


def ensure_environment():
    if not PYTHON.exists():
        print("Creating Python environment...")
        venv.EnvBuilder(with_pip=True).create(str(VENV))
        subprocess.run(
            [str(PYTHON), "-m", "pip", "install", "-r", str(ROOT / "backend/requirements.txt")],
            check=True,
        )
    else:
        check = subprocess.run(
            [str(PYTHON), "-c", "import uvicorn, fastapi, osmnx, svgwrite"],
            capture_output=True,
        )
        if check.returncode != 0:
            subprocess.run(
                [str(PYTHON), "-m", "pip", "install", "-r", str(ROOT / "backend/requirements.txt")],
                check=True,
            )
    if os.getenv("OSM_EXTRACT_FILE"):
        extra = subprocess.run(
            [str(PYTHON), "-c", "import osmium"], capture_output=True
        )
        if extra.returncode:
            print("Local regional extract enabled: installing optional importer...")
            subprocess.run(
                [str(PYTHON), "-m", "pip", "install", "osmium>=4,<5"], check=True
            )


def await_ready(children, limit=35):
    until = time.monotonic() + limit
    while time.monotonic() < until:
        for name, process in children:
            if process.poll() is not None:
                raise RuntimeError(f"{name} server exited (code {process.returncode})")
        try:
            with urlopen(FRONTEND_URL, timeout=1) as response:
                if response.status == 200:
                    with urlopen(BACKEND_URL, timeout=1) as api:
                        if api.status == 200:
                            return
        except (URLError, OSError):
            pass
        time.sleep(0.3)
    raise RuntimeError("Local server health checks timed out; check the terminal output.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        parser.error("Python 3.10+ required; Python 3.12 recommended")
    os.chdir(ROOT)
    children = []
    try:
        ensure_environment()
        backend = subprocess.Popen([
            str(PYTHON), "-m", "uvicorn", "server:app", "--app-dir", "backend",
            "--host", "127.0.0.1", "--port", "8001",
        ], cwd=ROOT)
        children.append(("Backend", backend))
        frontend = subprocess.Popen([
            str(PYTHON), "-m", "http.server", "8000", "--bind", "127.0.0.1"
        ], cwd=ROOT)
        children.append(("Frontend", frontend))
        await_ready(children)
        print("\nCity Map Studio is ready at", FRONTEND_URL)
        print("Use Ctrl+C in this terminal to stop both local servers.\n")
        if not args.no_browser:
            webbrowser.open(FRONTEND_URL)
        while all(process.poll() is None for _, process in children):
            time.sleep(0.5)
        raise RuntimeError("A server stopped unexpectedly; see output above.")
    except KeyboardInterrupt:
        print("\nStopping City Map Studio...")
    except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    finally:
        for _, process in children:
            if process.poll() is None:
                process.terminate()
        for _, process in children:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
