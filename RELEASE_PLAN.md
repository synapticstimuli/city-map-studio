# City Map Studio — isolated release preparation branch

This branch is based on `feature/on-demand-maps`. It deliberately does **not**
overwrite changes that may still exist only on the developer's Windows machine.

## Implemented
- Fixed Pydantic place validation indentation.
- Read backend paths, CORS origins and generation timeouts from environment.
- Stream generator logs to the backend console.
- Cache completed SVGs using normalized request settings, with schema versioning.
- Coalesce identical jobs while one is queued or running.
- Fail closed on legacy or malformed cache records, and verify SVG SHA-256 integrity.
- Invalidate finished-map cache entries if the generator source changes.
- Limit Overpass tag requests to feature categories actually rendered.
- Split disconnected municipal polygons before submitting Overpass queries, and reject excessive query-area coverage before network access.
- Deny map-generation requests from non-loopback clients; production remains disabled.
- Disable generation on hosted frontend until a secure production API is configured.
- Add Python compilation, unit/API lifecycle tests, frontend JS syntax checks and script-order tests.

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
- Implement authenticated remote generation, per-user quotas, abuse controls, and true cancellation. The current localhost-only restriction must not simply be removed.
- Validate live large-city retrieval and introduce a regional OSM-extract fallback when area preflight refuses an oversized request.
- Deploy durable persistent storage; in-memory job status will not survive restarts.
- Implement configurable externally hosted API URL and restrict CORS to the deployed UI.
- Test complete end-to-end generation on real geographic caches and large regions.
- Verify OpenStreetMap/Overpass usage requirements and attribution.

Current cache identity uses normalized location text, not a verified OSM relation ID.
Finished SVG integrity is checked, but dataset provenance and geographic cache health checks still need design.

## Geographic query preflight (development)

The generator now plans each disconnected polygon separately instead of passing a
multi-part municipal boundary to OSMnx, which may otherwise request its overall
convex hull. Preflight estimates coverage in EPSG:6933 and enforces a maximum
of 24 components, 500 km² per component, and 1,000 km² total. These limits
are conservative defaults, not a substitute for provider-side rate limiting.

The complete administrative boundary remains the map's rendering boundary.
The planner does not quietly drop islands or remote territory. When a large
single polygon exceeds limits, generation refuses to make an expensive
request and requires a regional extract or deliberate smaller-area selection.
A real San Francisco success test and performance measurements remain pending.
