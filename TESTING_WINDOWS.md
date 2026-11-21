# City Map Studio v2.1 — Windows hands-on test

**Purpose:** Test the latest draft without replacing your stable project checkout,
merging code, or deploying the site. The test directory is a separate Git worktree.

## 1. Create an isolated test checkout

Open PowerShell in your existing `city-map-studio` project directory.

```powershell
git status --short
git fetch origin
git worktree add ..\city-map-studio-v21-test origin/agent/reconciled-v2-1
cd ..\city-map-studio-v21-test
git log -1 --oneline
```

This checks out a detached working tree based on the remote test branch.
It does **not** change the branch in your original project folder.
If the test folder already exists, do not overwrite it; inspect it first.

## 2. Set up Python

Use Python 3.12 where available (Windows Python launcher):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s backend\tests -v
```

The optional regional PBF importer additionally requires:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-extract.txt
```

You do **not** need a regional PBF to test the viewer, original maps,
Overpass generation, or finished-SVG caching.

## 3. Start both local servers (separate PowerShell terminals)

Run these from the new `city-map-studio-v21-test` folder.

**Terminal A — backend**

```powershell
$env:APP_ENV = "development"
Remove-Item Env:OSM_EXTRACT_FILE -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m uvicorn server:app --app-dir backend --host 127.0.0.1 --port 8001
```

**Terminal B — frontend**

```powershell
.\.venv\Scripts\python.exe -m http.server 8000 --bind 127.0.0.1
```

Open **http://127.0.0.1:8000/** in your browser. The backend health endpoint
is **http://127.0.0.1:8001/api/health**.

If a port is already in use, stop your previously running local app
before starting the test servers. Keep the two PowerShell windows open.

## 4. Run a safe API smoke test

In **Terminal C**, from the test folder:

```powershell
.\.venv\Scripts\python.exe backend\smoke_local.py
```

This checks the health and persistent library endpoints, without starting
a map-generation request. It should print two PASS lines.

## 5. Browser acceptance checks

- Switch among the three shipped cities; no loading errors.
- Zoom in/out/reset; drag to pan.
- Toggle roads, paths, rail and boundary layers.
- Apply Fine, Detailed and Bold presets; adjust widths.
- Switch paper dimensions and portrait/landscape orientation.
- Export a styled SVG; open it in a vector editor or browser and confirm
  that paper orientation, layer visibility, and road widths are retained.
- The generator panel should be available when the localhost API is running.

## 6. Optional real generation test

A city not yet cached may contact Nominatim/Overpass and take significantly
longer or fail due to provider load. Start with a small known city. If your
old checkout has trusted geographic cache files, they can be copied into
`backend\map_data_cache` in the test checkout to avoid re-downloading.

```powershell
.\.venv\Scripts\python.exe backend\smoke_local.py --generate --city Somerville --county Middlesex --state Massachusetts --mode centerline --max-wait 900
```

Expected: a complete job, valid SVG download, then immediate reuse of the
same finished map on an identical second request. If it exceeds the timeout,
check Terminal A. Timing out the test **does not cancel the backend job**.

For an optional **regional PBF** test, first install the optional requirements
and set `OSM_EXTRACT_FILE` in **Terminal A** to an existing local PBF path,
then restart the backend. A regional extract is never downloaded by the UI.

## 7. Report results

Send the output of `backend\smoke_local.py` plus any backend error messages
and whether the SVG export looks correct. Screenshots are helpful for visual
issues. Do not include personal paths or confidential configuration values.

**Known limitations:** public cloud generation remains disabled; SF-scale
real-boundary performance and full browser automation have not been approved
as release-ready. The real Monaco PBF CI test used a rectangular test area.

**Avoid:** merging draft PR #3, pushing to `main`, or deleting your current
working project folder during this test.
