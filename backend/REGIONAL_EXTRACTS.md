# Regional OSM extracts — experimental local fallback

City Map Studio can import **road, path and railway ways** from a local OSM
extract without querying Overpass. This is an opt-in feature for development,
large cities, and offline data processing.

## Example: Massachusetts on Windows

1. Download `massachusetts-latest.osm.pbf` from the official Geofabrik page:
   https://download.geofabrik.de/north-america/us/massachusetts.html
2. Store the file outside your Git repository, for example
   `C:\\MapData\\massachusetts-latest.osm.pbf`.
3. Install the extra dependencies using your existing Python virtual environment:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r backend\requirements-extract.txt
   ```

4. Set the source file for the terminal session before launching the backend:

   ```powershell
   $env:OSM_EXTRACT_FILE = "C:\MapData\massachusetts-latest.osm.pbf"
   .\.venv\Scripts\python.exe -m uvicorn server:app --app-dir backend --host 127.0.0.1 --port 8001
   ```

5. Choose a Massachusetts city not yet in the geographic cache, or test the
   generator directly with `--extract-file` and a **separate** cache folder:

   ```powershell
   .\.venv\Scripts\python.exe backend\generate_city_map_v2_1.py --city Cambridge --county Middlesex --state Massachusetts --country USA --extract-file "C:\MapData\massachusetts-latest.osm.pbf" --cache-dir ".\backend\extract_test_cache" --output "extract_test.svg"
   ```

The original generator still geocodes a city's municipal boundary through
Nominatim when a boundary is not already cached. The local extract replaces
**Overpass feature retrieval**, not geocoding. A cached boundary can be reused
when the associated GeoJSON boundary file exists.

If `OSM_EXTRACT_FILE` is unset, City Map Studio retains its existing
bounded Overpass retrieval behavior. If an extract is configured but
missing/corrupted, generation fails visibly rather than silently switching
to Overpass.

## Current limitations

- This first importer supports tagged OSM **ways** (not relation-assembled
  geometries or tagged standalone nodes). Railway and roadway centerline
  geometry is retained and clipped within the municipal boundary. Some
  complex multipolygon features may differ from live OSMnx feature results.
- Local extract processing still scans the regional input file, so an initial
  generation is not instantaneous and memory use can be significant.
- Use reference-complete extracts and enough disk space for PBF plus cached
  geographic data. Do not commit these large files.
- The existing cache is checked **before** the extract. To benchmark extracts,
  use a fresh cache directory; do not overwrite existing maps inadvertently.
- Changes to an extract don't automatically replace existing geographic
  GeoJSON cache entries. A controlled refresh is needed to retrieve new data.
- This does not introduce external API authentication, hosted durable storage,
  or multiuser service quotas. Public generation remains disabled.

## Data licensing

OpenStreetMap data is governed by the ODbL and requires attribution.
Check the Geofabrik download terms and OpenStreetMap attribution guidance
before distribution or commercialization.
