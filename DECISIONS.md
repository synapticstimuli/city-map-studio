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

These are synthetic numbers. The real LA gain still has to be measured on
Rod's machine; it is not claimed here.

### Not addressed (next candidates)
- PBF import (`feature_retrieval_seconds`, 179.9 s for LA, 53% of the run):
  full scan of the regional extract with an in-memory location index.
- Outline mode's single `unary_union` of all road polygons (now the largest
  remaining cost in outline mode on the benchmark).
- GeoJSON geographic cache read/write (~13 s inferred for LA).
