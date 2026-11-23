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
