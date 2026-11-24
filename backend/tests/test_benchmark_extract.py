"""End-to-end smoke test of benchmark output with a synthetic OSM XML file."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmark_extract import benchmark
from test_osm_extract import OSM


class BenchmarkExtractTests(unittest.TestCase):
    def test_local_benchmark_never_requires_overpass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            extract = root / "tiny.osm"
            extract.write_text(OSM, encoding="utf-8")
            boundary_file = root / "boundary.geojson"
            boundary = Polygon([
                (-71.01, 41.99), (-70.99, 41.99),
                (-70.99, 42.01), (-71.01, 42.01),
            ])
            gpd.GeoDataFrame(
                [{"name": "test"}], geometry=[boundary], crs="EPSG:4326"
            ).to_file(boundary_file, driver="GeoJSON")
            result = benchmark(extract, boundary_file, "Fixture City")
            self.assertEqual(result["feature_rows"], 2)
            self.assertEqual(result["road_or_path_rows"], 1)
            self.assertEqual(result["railway_rows"], 1)
            self.assertTrue(result["no_network_requests"])
            self.assertFalse(result["complete_rendering_measured"])
            self.assertGreaterEqual(result["elapsed_seconds"], 0)
            self.assertGreater(result["extract_bytes"], 0)
            json.dumps(result)


if __name__ == "__main__":
    unittest.main()
