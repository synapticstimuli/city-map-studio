# Real regional-data integration validation

Test date: 2026-11-21 (GitHub Actions, Ubuntu, Python 3.12)

Workflow: [Backend checks run 44943704005](https://github.com/synapticstimuli/city-map-studio/actions/runs/44943704005)

## Verified
- Real Geofabrik [Monaco OSM extract](https://download.geofabrik.de/europe/monaco.html)
  downloaded and decoded successfully (704,767 bytes in this run).
- A **Monaco-area rectangular bounding box**, *not an official city boundary*,
  was used to clip the extract.
- The PBF importer returned 3,656 clipped feature rows: 3,645 roads/paths,
  11 railways, and completed in 0.615 s on the CI runner.
- Python-managed allocation peak measured by tracemalloc was 2,731,652 bytes,
  excluding memory allocated through native libraries and the rest of the
  process. This is NOT the peak working-set/RSS figure.
- The generator produced a valid **centerline** SVG with 3,663 paths and
  600,891 bytes, plus a valid **outline** SVG with 1,275 paths and 765,326
  bytes. Both had road, railway and boundary groups.
- The outline run reused geographic features cached by the centerline run.

## Limits of this result
- Import and clipping time (0.615 s) excludes full SVG generation.
- No reliable total generation wall-clock time or peak native RSS collected.
- The test region is small and cannot predict full California performance.
- City boundary precision is not validated by this rectangular test.
- Differences in relation-based geometry versus OSMnx remain unquantified.
- Geofabrik data changes; exact feature counts are a snapshot, not invariant.

## Next release gates
1. Repeat on a Massachusetts municipality and compare line/rail counts against
   the existing Overpass geographic cache.
2. Benchmark full wall-clock and process RSS (not just tracemalloc).
3. Validate city boundaries and disjoint municipal islands, especially SF.
4. Run browser interactions and print/export regression tests.
5. Authenticate and quota any publicly accessible generation backend.

This report documents test evidence; it is not a production acceptance.
