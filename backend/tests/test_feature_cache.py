"""FlatGeobuf geographic feature cache (ADR-005).

New caches are written as ``osm_features.fgb``. A map rendered from the cache
must be byte-identical to the map rendered on the run that built the cache,
and caches written by earlier versions as ``osm_features.geojson`` must keep
working.
"""
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
import osmnx as ox
from shapely.geometry import LineString, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import generate_city_map_v2_1 as generator  # noqa: E402
import osm_extract  # noqa: E402
from benchmark_render import render_args, seed_cache, synthetic_boundary  # noqa: E402
from synthetic_pbf import target_city_bounds, write_synthetic_pbf  # noqa: E402


def render(args):
    with redirect_stdout(StringIO()):
        generator.create_svg(args)
    return args.output.read_bytes()


class ColdAndCachedRunsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.pbf = cls.root / "region.osm.pbf"
        write_synthetic_pbf(cls.pbf, grid=120, buildings=500)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_cached_run_matches_the_run_that_built_the_cache(self):
        west, south, east, north = target_city_bounds()
        cx, cy = (west + east) / 2, (south + north) / 2
        city = box(cx - (east - west) * 0.3, cy - (north - south) * 0.3,
                   cx + (east - west) * 0.3, cy + (north - south) * 0.3)
        boundary = gpd.GeoDataFrame(geometry=[city], crs="EPSG:4326")
        for regional in (False, True):
            for road_mode in ("centerline", "outline"):
                with self.subTest(regional=regional, road_mode=road_mode):
                    cache = self.root / f"cache-{regional}-{road_mode}"
                    args = render_args(cache, self.root / "cold.svg", regional, road_mode)
                    args.extract_file = str(self.pbf)
                    with patch.object(ox, "geocode_to_gdf", return_value=boundary):
                        cold = render(args)
                    written = sorted(p.name for p in cache.rglob("osm_features*"))
                    self.assertEqual(written, ["osm_features.fgb"])
                    args.output = self.root / "warm.svg"
                    with patch.object(osm_extract, "extract_features",
                                      side_effect=AssertionError("cache not used")):
                        warm = render(args)
                    self.assertGreater(len(cold), 10_000)
                    self.assertEqual(cold, warm)


class LegacyGeoJsonCacheTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.cache = self.root / "cache"

    def test_old_cache_still_renders_and_is_converted_once(self):
        boundary = synthetic_boundary(400)
        for regional in (False, True):
            seed_cache(self.cache, regional, boundary, grid=20)
            for road_mode in ("centerline", "outline"):
                with self.subTest(regional=regional, road_mode=road_mode):
                    args = render_args(self.cache, self.root / "a.svg", regional, road_mode)
                    folder = next(p.parent for p in self.cache.rglob("osm_features.geojson")
                                  if (p.parent.name.startswith("regional")) == regional)
                    (folder / "osm_features.fgb").unlink(missing_ok=True)
                    # v2.3 behaviour: read the GeoJSON cache directly.
                    with patch.object(generator, "read_cached_features",
                                      lambda f: gpd.read_file(f / "osm_features.geojson")):
                        reference = render(args)
                    args.output = self.root / "b.svg"
                    from_legacy = render(args)          # reads GeoJSON, writes .fgb
                    self.assertTrue((folder / "osm_features.fgb").is_file())
                    self.assertTrue((folder / "osm_features.geojson").is_file())
                    args.output = self.root / "c.svg"
                    with patch.object(gpd, "read_file", wraps=gpd.read_file) as reads:
                        from_fast = render(args)
                    read_paths = [Path(c.args[0]).name for c in reads.call_args_list]
                    self.assertIn("osm_features.fgb", read_paths)
                    self.assertNotIn("osm_features.geojson", read_paths)
                    self.assertEqual(reference, from_legacy)
                    self.assertEqual(reference, from_fast)


class CacheFileSafetyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.features = gpd.GeoDataFrame(
            {"highway": ["residential", None], "railway": [None, "rail"],
             "bridge": ["yes", None], "width": [None, None]},
            geometry=[LineString([(0, 0), (1, 1)]), LineString([(0, 1), (1, 0.123456789012345678)])],
            crs="EPSG:4326",
        )

    def test_round_trip_keeps_values_and_exact_coordinates(self):
        generator.write_cached_features(self.features, self.folder / "osm_features.fgb")
        back = generator.read_cached_features(self.folder)
        self.assertEqual(list(back.columns), list(self.features.columns))
        for field in ("highway", "railway", "bridge", "width"):
            def plain(values):
                return [v if isinstance(v, str) else None for v in values]
            self.assertEqual(plain(back[field]), plain(self.features[field]))
        for a, b in zip(self.features.geometry, back.geometry):
            self.assertTrue(a.equals_exact(b, tolerance=0))

    def test_interrupted_write_leaves_no_cache(self):
        with patch.object(generator.os, "replace", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                generator.write_cached_features(self.features, self.folder / "osm_features.fgb")
        self.assertEqual(list(self.folder.iterdir()), [])
        self.assertIsNone(generator.read_cached_features(self.folder))

    def test_damaged_fast_cache_falls_back(self):
        damaged = self.folder / "osm_features.fgb"
        damaged.write_bytes(b"not a flatgeobuf")
        with redirect_stdout(StringIO()):
            self.assertIsNone(generator.read_cached_features(self.folder))
        self.assertFalse(damaged.exists())
        self.features.to_file(self.folder / "osm_features.geojson", driver="GeoJSON")
        damaged.write_bytes(b"not a flatgeobuf")
        with redirect_stdout(StringIO()):
            back = generator.read_cached_features(self.folder)
        self.assertEqual(len(back), 2)
        self.assertEqual(len(generator.read_cached_features(self.folder)), 2)

    def test_unwritable_conversion_still_uses_legacy_cache(self):
        self.features.to_file(self.folder / "osm_features.geojson", driver="GeoJSON")
        with patch.object(generator, "write_cached_features", side_effect=OSError("read-only")):
            with redirect_stdout(StringIO()):
                back = generator.read_cached_features(self.folder)
        self.assertEqual(len(back), 2)


if __name__ == "__main__":
    unittest.main()
