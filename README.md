# City Map Studio v2.2 — Local release candidate

Create, style, view, and export editable street maps. The frontend is a static
website; city generation uses a Python API bound to your local computer.

## Run locally on Windows

1. Install Python 3.12 (if not already installed).
2. Double-click **Run City Map Studio.cmd**, or run `py -3.12 launch_local.py`
   in PowerShell.
3. The launcher creates a private `.venv` if needed, installs the core
   dependencies on first run, starts both localhost servers, and opens
   **http://127.0.0.1:8000**.
4. To stop the app, press Ctrl+C in the launcher terminal.

The startup script binds the API to **127.0.0.1:8001** and the viewer to
**127.0.0.1:8000**. It does not expose generation on the public internet.
If you have an older local server running, stop that first to avoid port conflicts.

## Features

- City generation via OpenStreetMap/Nominatim and bounded Overpass requests.
- Optional local Geofabrik regional OSM extract import (requires `osmium`).
- Reuse geographic GeoJSON and previously generated SVGs, with source-scoped
  caching, checksum verification, and duplicate-request coalescing.
- Editable road, footpath, rail and boundary layers; centered and outline modes.
- Style presets, paper sizes, portrait/landscape preview, and editable SVG export.
- Local saved-map library, queue positions, live generator stage details,
  and diagnostic logs.
- City-only and Regional print modes: 78% focus-city fill, with context
  clipped to a 95% page window on a separate SVG layer. Regional fetching
  is reduced to the print-visible area when the geographic cache is cold.

## Optional large-city data

Large locations such as **Los Angeles** may exceed the safe public Overpass
request budget. Obtain a regional PBF and follow
[backend/REGIONAL_EXTRACTS.md](backend/REGIONAL_EXTRACTS.md). The importer does
not automatically download multi-hundred-megabyte files. It uses the local
file configured by `OSM_EXTRACT_FILE` when present.

## Tests and troubleshooting

- [Windows testing guide](TESTING_WINDOWS.md)
- [Real PBF validation evidence](REAL_DATA_VALIDATION.md)
- [Release gates](RELEASE_PLAN.md)
- Backend/API offline tests: `python -m unittest discover -s backend/tests -v`
- Local smoke test: `.venv\\Scripts\\python.exe backend\\smoke_local.py`
- GitHub Actions runs Python tests, JavaScript syntax checks and a headless
  Chromium test covering map load, styling and SVG download.
- Real network-dependent Monaco PBF test is available by manually dispatching
  the GitHub Actions **Backend checks** workflow; not a required push gate.
- Job logs: `backend/generated_maps/<job-id>.log`

## Public website / limitations

A static Cloudflare Pages deployment can host the viewer and bundled SVG
examples. It cannot run the local FastAPI/OSMnx generator by itself. The
hosted generator is intentionally disabled until a secured persistent API
is provisioned with authentication, quotas, abuse controls, and storage.
Do not expose development port 8001 to the internet.

OpenStreetMap data © OpenStreetMap contributors, under the ODbL.
Roadway outlines are estimated buffers of mapped centerlines, not surveyed
curb/road boundaries.
