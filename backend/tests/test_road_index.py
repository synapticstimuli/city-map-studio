"""v2.3 PBF importer and reusable road index (ADR-002).

The importer must return exactly what the v2.2 importer returned (same ways,
order, tags and geometry), whether it scans the extract, builds the road
index, or reuses it. The index must never be trusted when it is incomplete,
damaged, or belongs to a different extract revision.
"""
import math
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import shapely
from shapely.geometry import Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import osm_extract  # noqa: E402
import reference_osm_extract_v22 as reference  # noqa: E402
from benchmark_extract import HIGHWAYS, RAILWAYS  # noqa: E402
from synthetic_pbf import target_city_bounds, write_synthetic_pbf  # noqa: E402

EDGE_CASES_OSM = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6" generator="City Map Studio tests">
  <node id="1" lat="42.000" lon="-71.015" version="1"/>
  <node id="2" lat="42.000" lon="-70.985" version="1"/>
  <node id="3" lat="42.005" lon="-71.005" version="1"/>
  <node id="4" lat="42.006" lon="-70.995" version="1"/>
  <node id="5" lat="42.004" lon="-71.000" version="1">
    <tag k="highway" v="traffic_signals"/>
  </node>
  <way id="100" version="1">
    <nd ref="1"/><nd ref="999"/><nd ref="2"/><tag k="highway" v="residential"/>
  </way>
  <way id="101" version="1">
    <nd ref="1"/><nd ref="1"/><nd ref="3"/><nd ref="4"/>
    <tag k="highway" v="residential"/><tag k="bridge" v="yes"/>
    <tag k="width" v="7.5"/><tag k="name" v="Señora Street"/>
  </way>
  <way id="102" version="1">
    <nd ref="3"/><tag k="highway" v="residential"/>
  </way>
  <way id="103" version="1">
    <nd ref="3"/><nd ref="4"/><tag k="highway" v="proposed"/>
  </way>
  <way id="104" version="1">
    <nd ref="3"/><nd ref="4"/><nd ref="5"/><nd ref="3"/>
    <tag k="railway" v="rail"/><tag k="disused" v="yes"/>
  </way>
  <way id="105" version="1">
    <nd ref="2"/><nd ref="4"/><tag k="building" v="yes"/>
  </way>
</osm>"""


def wiggly_city():
    west, south, east, north = target_city_bounds()
    cx, cy = (west + east) / 2, (south + north) / 2
    rx, ry = (east - west) / 2, (north - south) / 2
    points = []
    for i in range(600):
        angle = 2 * math.pi * i / 600
        r = 0.8 + 0.15 * math.sin(7 * angle)
        points.append((cx + rx * r * math.cos(angle), cy + ry * r * math.sin(angle)))
    return Polygon(points)


class ImporterTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.pbf = cls.root / "region.osm.pbf"
        write_synthetic_pbf(cls.pbf, grid=200, buildings=4000)
        cls.xml = cls.root / "edge-cases.osm"
        cls.xml.write_text(EDGE_CASES_OSM, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.index_dir = Path(tempfile.mkdtemp(dir=self.root))
        self.addCleanup(shutil.rmtree, self.index_dir, True)

    def assert_same_features(self, expected, actual):
        self.assertEqual(len(expected), len(actual))
        self.assertEqual(list(expected.columns), list(actual.columns))
        for field in osm_extract.FIELDS:
            # Series.equals: same dtype and values, NaN equal to NaN.
            self.assertTrue(expected[field].equals(actual[field]), field)
        for a, b in zip(expected.geometry, actual.geometry):
            self.assertEqual(a.geom_type, b.geom_type)
            self.assertTrue(np.array_equal(shapely.get_coordinates(a), shapely.get_coordinates(b)))
        self.assertEqual(expected.crs, actual.crs)

    def import_all_ways(self, path, boundary, highways=HIGHWAYS, railways=RAILWAYS):
        expected = reference.extract_features(path, boundary, highways, railways)
        scanned = osm_extract.extract_features(path, boundary, highways, railways)
        built = osm_extract.extract_features(
            path, boundary, highways, railways, index_dir=self.index_dir)
        reused = osm_extract.extract_features(
            path, boundary, highways, railways, index_dir=self.index_dir)
        return expected, scanned, built, reused


class MatchesV22Tests(ImporterTestCase):
    def test_synthetic_region_all_paths_match_v22(self):
        for name, boundary in (("box city", box(*target_city_bounds())),
                               ("wiggly city", wiggly_city())):
            with self.subTest(boundary=name):
                expected, *candidates = self.import_all_ways(self.pbf, boundary)
                self.assertGreater(len(expected), 300)
                for candidate in candidates:
                    self.assert_same_features(expected, candidate)
                shutil.rmtree(self.index_dir)

    def test_edge_cases_match_v22(self):
        """Missing node, repeated node, one-node way, unrendered types, tags."""
        boundary = box(-71.02, 41.99, -70.98, 42.01)
        expected, *candidates = self.import_all_ways(
            self.xml, boundary, {"residential"}, {"rail"})
        self.assertEqual(len(expected), 2)
        self.assertEqual(expected["width"].iloc[0], "7.5")
        self.assertTrue(expected["width"].isna().iloc[1])
        for candidate in candidates:
            self.assert_same_features(expected, candidate)

    def test_small_chunks_match_one_chunk(self):
        boundary = wiggly_city()
        expected = osm_extract.extract_features(self.pbf, boundary, HIGHWAYS, RAILWAYS)
        with patch.object(osm_extract, "CHUNK_WAYS", 997):
            # Default argument binds at definition time; patch the scanner too.
            original = osm_extract.scan_extract

            def small(*args, **kwargs):
                kwargs["chunk_ways"] = 997
                return original(*args, **kwargs)

            with patch.object(osm_extract, "scan_extract", small):
                built = osm_extract.extract_features(
                    self.pbf, boundary, HIGHWAYS, RAILWAYS, index_dir=self.index_dir)
        parts = list(next(self.index_dir.iterdir()).glob("part-*.fgb"))
        self.assertGreater(len(parts), 3)
        self.assert_same_features(expected, built)


class IndexLifecycleTests(ImporterTestCase):
    def test_reuse_does_not_rescan_extract(self):
        boundary = box(*target_city_bounds())
        osm_extract.extract_features(self.pbf, boundary, HIGHWAYS, RAILWAYS, index_dir=self.index_dir)
        with patch.object(osm_extract, "scan_extract", side_effect=AssertionError("rescanned")):
            features = osm_extract.extract_features(
                self.pbf, boundary, HIGHWAYS, RAILWAYS, index_dir=self.index_dir)
        self.assertGreater(len(features), 0)

    def test_changed_extract_or_tag_set_gets_a_new_index(self):
        copy = self.root / "copy.osm.pbf"
        shutil.copy(self.pbf, copy)
        first = osm_extract.index_path(self.index_dir, copy.resolve(), HIGHWAYS, RAILWAYS)
        stat = copy.stat()
        os.utime(copy, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        self.assertNotEqual(first, osm_extract.index_path(self.index_dir, copy.resolve(), HIGHWAYS, RAILWAYS))
        self.assertNotEqual(first, osm_extract.index_path(
            self.index_dir, copy.resolve(), HIGHWAYS - {"track"}, RAILWAYS))

    def test_interrupted_build_leaves_no_usable_index(self):
        boundary = box(*target_city_bounds())
        calls = {"n": 0}
        original = osm_extract.write_index_part

        def fail_second(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise KeyboardInterrupt("power cut")
            return original(*args, **kwargs)

        scan = osm_extract.scan_extract

        def small(*args, **kwargs):
            kwargs["chunk_ways"] = 2000
            return scan(*args, **kwargs)

        with patch.object(osm_extract, "write_index_part", fail_second), \
                patch.object(osm_extract, "scan_extract", small):
            with self.assertRaises(KeyboardInterrupt):
                osm_extract.extract_features(
                    self.pbf, boundary, HIGHWAYS, RAILWAYS, index_dir=self.index_dir)
        self.assertEqual(list(self.index_dir.iterdir()), [])
        expected = reference.extract_features(self.pbf, boundary, HIGHWAYS, RAILWAYS)
        rebuilt = osm_extract.extract_features(
            self.pbf, boundary, HIGHWAYS, RAILWAYS, index_dir=self.index_dir)
        self.assert_same_features(expected, rebuilt)

    def test_damaged_index_is_removed_and_extract_scanned(self):
        boundary = box(*target_city_bounds())
        osm_extract.extract_features(self.pbf, boundary, HIGHWAYS, RAILWAYS, index_dir=self.index_dir)
        index = next(self.index_dir.iterdir())
        (index / "part-00000.fgb").write_bytes(b"not a flatgeobuf")
        expected = reference.extract_features(self.pbf, boundary, HIGHWAYS, RAILWAYS)
        features = osm_extract.extract_features(
            self.pbf, boundary, HIGHWAYS, RAILWAYS, index_dir=self.index_dir)
        self.assert_same_features(expected, features)
        self.assertFalse(index.exists())

    def test_unwritable_index_falls_back_to_scan(self):
        boundary = box(*target_city_bounds())
        expected = reference.extract_features(self.pbf, boundary, HIGHWAYS, RAILWAYS)
        with patch.object(osm_extract, "write_index_part", side_effect=OSError("disk full")):
            features = osm_extract.extract_features(
                self.pbf, boundary, HIGHWAYS, RAILWAYS, index_dir=self.index_dir)
        self.assert_same_features(expected, features)
        self.assertEqual(list(self.index_dir.iterdir()), [])

    def test_no_matching_ways_is_explicit_error(self):
        far_away = box(10.0, 10.0, 10.1, 10.1)
        for index_dir in (None, self.index_dir):
            with self.subTest(index=index_dir is not None):
                with self.assertRaises(RuntimeError):
                    osm_extract.extract_features(
                        self.pbf, far_away, HIGHWAYS, RAILWAYS, index_dir=index_dir)


class GeneratorWiringTests(unittest.TestCase):
    def test_road_index_can_be_disabled(self):
        import generate_city_map_v2_1 as generator

        with patch.dict(os.environ, {"OSM_ROAD_INDEX": "0"}):
            self.assertIsNone(generator.road_index_dir(Path("cache")))
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OSM_ROAD_INDEX", None)
            self.assertEqual(generator.road_index_dir(Path("cache")), Path("cache") / "_extract_index")


if __name__ == "__main__":
    unittest.main()
