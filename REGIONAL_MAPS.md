# Regional map mode (v2.2 preview)

**City-only** remains the default. **Regional** adds a 3 km outer context
around the selected municipality. The focused city keeps dark primary lines,
and surrounding roads/paths/railways are drawn in a muted grey.

## How to use

1. Start the application locally using `Run City Map Studio.cmd`.
2. Under **Generate a new city → Map extent**, choose **Regional (3 km surroundings)**.
3. Enter city, state, optional county, and country; select a road mode.
4. Generate the map. The surrounding network appears on a separate editable
   SVG group named `regional-context`.
5. Under **Map layers**, uncheck **Regional surroundings** to compare with
   city-only appearance without regenerating the map.
6. Export the styled SVG. The focus-city features remain in distinct groups:
   `road-edges` or `road-centerlines`, `path-edges` or
   `path-centerlines`, `railways`, and `city-boundary`.
   The subdued surroundings are exported as an independent
   `regional-context` group, which can be restyled or removed in Illustrator
   and Inkscape.

## Print framing — focus first

Regional mode now uses two separate extents:

- **Acquisition extent:** the existing 3 km buffer determines which outside
  streets are loaded (and retains the same bounded Overpass safeguards).
- **Display extent:** the *city boundary bounding box* sets the page scale,
  centered exactly on the paper. The city fills 78% of the limiting page
  dimension without stretching; all visible surroundings are clipped to the
  centered 95%-of-page window.

For a **cold** geographic cache, the Overpass query is further trimmed
to the intersection of the configured 3 km buffer and the visible print
frame. Streets that cannot appear on the page are not requested. This
reduces unnecessary downloaded geography, especially for small cities with
large buffered surroundings. Previously cached full-region data remains
valid and may still be reused. Download speed is affected by Overpass server
load and network latency; no guaranteed speedup is claimed.

The API now reports coarse stages (boundary lookup, street retrieval,
rendering, export) during long-running jobs, with exact timings in each
job's log under `backend/generated_maps`.

The page frame is intentionally distinct from the data buffer: neither
the size nor shape of the surrounding query can shrink the focus city.
At extreme city aspect ratios the city may occupy less than 78% in the
other dimension (preserving geographic proportions). If the 3 km buffer
does not supply streets all the way to the print-window edge, the context
will naturally stop before 95%, rather than fabricate streets.

The City-only layout and its original paper margins remain unchanged.
Regional exports still preserve the independent `regional-context` group.

## Design and safety

- The city boundary is made visible by default in Regional mode and styled
  as a stronger contrasting outline; city roads remain dark.
- Outer context is clipped **outside** the focus city boundary, avoiding
  duplicated feature paths inside the focus city.
- Regional context uses thin centerlines even when the focus city uses buffered
  road outlines. This avoids expensive polygon-union operations outside the
  target city.
- Buffer distance is 3 km in the UI, with an API/CLI allowed range of
  0.5–10 km. The context geometry is buffered in a local projected coordinate
  system, not latitude/longitude degrees.
- Each source and mode/distance receives its own geographic cache directory;
  finished SVG identity also distinguishes City-only from Regional.
- The same geographic preflight limits apply to Overpass regional requests,
  so large boundaries may be refused instead of overwhelming the shared
  service. A local regional `.osm.pbf` extract can handle larger requests.
- No regional data is bundled with the static website, and this does not
  enable remote map generation.

## CLI example

```powershell
.\.venv\Scripts\python.exe backend\generate_city_map_v2_1.py `
  --city Watertown --county Middlesex --state Massachusetts --country USA `
  --regional-context --regional-buffer-km 3 --road-mode centerline `
  --output watertown_regional.svg
```

An initial Regional render fetches a larger geographic area than City-only,
so the first map may take longer. Repeat requests with the same mode and
distance reuse the source-scoped caches.

## Testing

An offline integration test seeds focus and surrounding features and confirms
that the output SVG has a nonempty `regional-context` layer, prominent
focus-city boundary, and independently editable focus-city groups. The
City-only fixture confirms that the regional context group is absent.

## Test the regional preview safely on Windows

Stop any running local frontend/backend servers, then open PowerShell in
your existing `city-map-studio-v21-test` working directory:

```powershell
git fetch origin
git worktree add ..\city-map-studio-regional-test origin/agent/regional-context-v2-2
cd ..\city-map-studio-regional-test
py -3.12 launch_local.py
```

The new folder and its local map cache are separate from the validated v2.1
checkout. The launcher starts the frontend and backend and opens the editor.
Choose **Watertown / Middlesex / Massachusetts / USA**, set **Map extent**
to **Regional**, then generate and inspect:

1. Dark Watertown streets and a prominent focus-city border
2. Muted streets and railways outside the boundary
3. Regional surroundings checkbox independently hides the outer context
4. Downloaded SVG contains a `regional-context` group in addition to the
   normal focus-city road/path/boundary groups
5. Repeating an identical Regional request reuses the completed map cache

The default City-only option should continue to omit the regional SVG group.
If Overpass refuses the wider request, the backend should explain the area
restriction; a local Massachusetts extract is the alternative.

When reporting a defect, capture the city inputs, selected road mode,
error text, and whether it occurred on first generation or cache reuse.

## Troubleshooting: Regional selected but surroundings absent

If **Regional surroundings** remains disabled after generating a Regional map,
the loaded SVG does not contain the expected `regional-context` group.
An important cause is an older local backend on port 8001: a v2.1 API
silently ignores the new regional request fields and generates a City-only
map. The updated v2.2 preview now rejects that condition.

Check the running backend in a PowerShell window:

```powershell
Invoke-RestMethod http://127.0.0.1:8001/api/health |
  ConvertTo-Json -Depth 5
```

Expected: `version: "2.2.0"` and `capabilities.regional_context: true`.
If you see v2.1.0 or no `capabilities`, stop the old Python frontend and
backend terminals with Ctrl+C. Then, from the separate regional test folder:

```powershell
git fetch origin
git switch --detach origin/agent/regional-context-v2-2
py -3.12 launch_local.py
```

The launcher now refuses to start when ports 8000 or 8001 are already
occupied, to prevent frontend/backend cross-version mixing. Do not kill
unrelated services indiscriminately. If the page was open during updates,
reload it with **Ctrl+Shift+R**.

The backend explicitly checks that new Regional SVGs have a **populated**
`regional-context` group before saving their metadata; it will report a
generation error instead of registering an incorrectly tagged City-only map.
The frontend also checks backend capabilities and SVG content before
reporting success.

If the API reports 2.2.0 but the problem remains, capture the **exact error
message**, recent job log in `backend/generated_maps`, and a screenshot.
A configured regional PBF is not required for small cities; the default
Overpass retrieval may still be refused by geographic area safeguards.
