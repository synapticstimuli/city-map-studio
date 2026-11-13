# City Map Studio — Cloudflare Pages MVP

A static interactive SVG viewer and styling editor for three existing generated maps: Somerville, Cambridge, Boston.

## Features
- Select city, show/hide road/path/rail/boundary layers
- Fine / Detailed / Bold line weights, manual stroke-weight sliders
- Zoom/pan; export styled, editable SVG to your computer
- No account, map server, API token, or database required

## Preview locally
From this directory run `py -m http.server 8000` (Windows with Python installed), then visit `http://localhost:8000`. Do not double-click `index.html`, because browsers restrict `fetch()` on `file://` URLs.

## Deploy with Cloudflare Pages
1. Create a dedicated GitHub repository (suggested: `city-map-studio`). Push **all files and the maps folder**.
2. In the Cloudflare dashboard, go to **Workers & Pages → Create application → Pages → Connect to Git**.
3. Select the GitHub repository; use no framework, leave build command blank, output directory `/` (repository root) for a plain static site. Set production branch to `main`.
4. Deploy. Future GitHub pushes automatically redeploy the site.

## Limitations
- Uses pre-generated SVGs: the city list does **not** run OSMnx/Overpass in the browser. Add new cities by generating an SVG with your Python script, copying it into `maps/`, and updating `files` and the dropdown in `app.js`/`index.html`.
- Centerline/outline switching is not built in because these source SVGs contain roadway outlines only. Generate or prepackage a centerline SVG for each city to enable mode switching.
- Outlines represent estimated buffered road widths, not actual surveyed curb edges.
- Large SVG files can take time to load on mobile devices. Optimizing path data and using compressed content can help.
- OSM attribution and applicable ODbL requirements must be preserved in any public use and downloads.
