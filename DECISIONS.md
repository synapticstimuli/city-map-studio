# Architecture decisions

Short records of significant technical decisions. Newest first. Each entry
says what was decided, why, and how it is verified.

## ADR-001: Faster per-way clipping and projection in the SVG renderer (v2.3)

**Date:** 2026-11-24 · **Status:** accepted · **Branch:** `claude/project-thread-l341wt` (off `feature/on-demand-maps`)

### Context
Los Angeles from the Southern California PBF took 340 s for a City-only
centerline map (job 5dfd433e). `svg_rendering_seconds` was 146.9 s of that
for 269,130 ways. In `create_svg`, every way was:

- projected separately with `shapely.ops.transform` inside `DataFrame.iterrows()`;
- clipped with a full GEOS overlay against the whole municipal boundary
  (thousands of vertices), once per layer it is drawn in, and again for
  each buffered polygon in outline mode.

Most ways in a large city lie entirely inside or entirely outside the
clipping area, so nearly all of that overlay work produced either the way
unchanged or nothing.

### Decision
1. Project all feature geometries in one vectorized call
   (`project_geometries`, same pyproj transformer, same coordinates).
2. Iterate plain column lists instead of `iterrows()`.
3. Clip through `clip_to(geom, area)` with the boundary and the regional
   context ring prepared (`shapely.prepare`):
   - disjoint from the area: return empty, no overlay;
   - strictly inside the area: overlay against a small rectangle around the
     geometry instead of the detailed boundary;
   - crossing or touching the edge: the original overlay.

The "strictly inside" case deliberately still runs a (cheap) overlay rather
than returning the geometry unchanged. Returning it unchanged was tried and
changed ring start points and orientation in outline mode, which would make
SVGs differ from v2.2. The small-rectangle overlay reproduces the v2.2 output
exactly.

### Compatibility
- Exported SVGs are byte-identical to v2.2 for City-only and Regional,
  centerline and outline (checked on the synthetic benchmark at two sizes and
  in `backend/tests/test_render_fast_paths.py`).
- No change to the API, CLI, cache formats, framing (78% / 95%) or SVG groups.
- Changing the generator source invalidates finished-SVG cache entries by
  design, so each previously generated map is re-rendered once from its
  cached geography on its next request.

### Evidence (synthetic benchmark, Linux, 4 vCPU, Python 3.13)
`python backend/benchmark_render.py --grid 160` (51,526 ways, 4,000-vertex boundary):

| Mode | v2.2 wall | v2.3 wall | Speedup |
|---|---|---|---|
| City-only centerline | 13.7 s | 2.3 s | 6.0x |
| City-only outline | 17.4 s | 5.2 s | 3.3x |
| Regional centerline | 18.4 s | 3.7 s | 5.0x |
| Regional outline | 23.1 s | 5.2 s | 4.4x |

These are synthetic numbers. Real-data LA numbers follow.

### Evidence (real LA data, Rod's Windows PC, Python 3.12, 2026-11-24)
Los Angeles from `socal-latest.osm.pbf`: City-only, Fine, centerline, paths
on, boundary hidden; 269,130 ways. `77b4979` (v2.2) versus `f9ffbd3`. Cached
runs read existing cached geography, so there is no PBF import.

| Stage | v2.2 full (job 5dfd433e) | v2.2 cached (2 runs) | `f9ffbd3` cached (2 runs) | `f9ffbd3` full (1 run) |
|---|---|---|---|---|
| PBF import | 179.9 s | skipped | skipped | 215.8 s |
| Cache write / read | about 13 s (write) | 7.1 / 7.0 s (read) | 7.8 / 7.2 s (read) | about 12 s (write) |
| SVG rendering | 146.9 s | 145.0 / 141.9 s | 35.7 / 35.5 s | 37.9 s |
| Generator total | 340.1 s | 152.1 / 148.9 s | 43.5 / 42.7 s | 265.7 s |

- Rendering is about 4x faster; the cached total is about 3.5x faster.
- Rod's own run in the app (job 5a2ab87a, cached geography): cache read 7.1 s,
  rendering 33.8 s, generator total 40.9 s; about 45 s as Rod timed it. Its SVG
  has the same SHA-256 as below.
- All five benchmark SVGs and the original job SVG share SHA-256
  `8ab52b0fdf118cd884cb70616f20ab65c90e3732f20a6bbdfdd479e31ec22765`.
- This change does not touch the import code. The slower import in the single
  full run was not investigated, so the 340.1 s versus 265.7 s totals are not
  a controlled comparison.
- Not measured on real data: outline mode, Regional mode, other cities, memory.

### Not addressed (next candidates)
- PBF import (`feature_retrieval_seconds`, 179.9 s for LA, 53% of the run):
  full scan of the regional extract with an in-memory location index.
- Outline mode's single `unary_union` of all road polygons (now the largest
  remaining cost in outline mode on the benchmark).
- GeoJSON geographic cache read/write (~13 s inferred for LA).
