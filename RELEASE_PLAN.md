# City Map Studio — isolated release preparation branch

This branch is based on `feature/on-demand-maps`. It deliberately does **not**
overwrite changes that may still exist only on the developer's Windows machine.

## Implemented
- Fixed Pydantic place validation indentation.
- Read backend paths, CORS origins and generation timeouts from environment.
- Stream generator logs to the backend console.
- Cache completed SVGs using normalized request settings, with schema versioning.
- Coalesce identical jobs while one is queued or running.
- Fail closed on legacy or malformed cache records.
- Disable generation on hosted frontend until a secure production API is configured.
- Add source syntax checks and unit tests.

## Local checkout and testing
```powershell
git fetch origin
git checkout agent/cache-first-release
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -v
.\.venv\Scripts\python.exe -m py_compile backend/server.py backend/cache_reuse.py
.\.venv\Scripts\python.exe -m uvicorn server:app --app-dir backend --host 127.0.0.1 --port 8001
```

If local work is uncommitted, create a backup or stash it before switching branches.
Do not merge this branch into the developer's active feature branch without reconciling
uncommitted changes.

## Not production-ready
The Python API is intentionally local-only. Before public deployment:
- Implement authentication, per-user quotas, abuse controls, and true cancellation.
- Validate geographic boundaries and prevent overly large Overpass requests.
- Deploy durable persistent storage; in-memory job status will not survive restarts.
- Implement configurable externally hosted API URL and restrict CORS to the deployed UI.
- Test complete end-to-end generation on real geographic caches and large regions.
- Verify OpenStreetMap/Overpass usage requirements and attribution.

Current cache identity uses normalized location text, not a verified OSM relation ID.
