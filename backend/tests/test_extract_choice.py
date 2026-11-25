"""Choosing the local OSM extract that covers the city (ADR-007).

Rod's San Francisco maps failed because the only extract configured was
Southern California. The generator now checks the area each extract declares
in its header, uses another extract in the same folder when that one covers
the city, tries Overpass when none does, and otherwise says exactly what to
download.
"""
import shutil
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
from benchmark_render import render_args  # noqa: E402
from cache_reuse import extract_candidates, extract_set_identity, source_identity  # noqa: E402
from query_planner import GeographicQueryTooLarge  # noqa: E402
from synthetic_pbf import target_city_bounds, write_synthetic_pbf  # noqa: E402

ELSEWHERE = (-125.0, 36.0, -121.0, 42.0)  # a "Northern California" header box


class ExtractChoiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.covering = cls.root / "src" / "covering.osm.pbf"
        cls.other = cls.root / "src" / "other.osm.pbf"
        write_synthetic_pbf(cls.covering, grid=40, buildings=0)
        write_synthetic_pbf(cls.other, grid=40, buildings=0, header_box=ELSEWHERE)
        west, south, east, north = target_city_bounds()
        cls.city = box(west + 0.05, south + 0.05, east - 0.05, north - 0.05)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(dir=self.root))
        self.addCleanup(shutil.rmtree, self.work, True)
        self.folder = self.work / "MapData"
        self.folder.mkdir()

    def place(self, *names):
        for name in names:
            shutil.copy(getattr(self, name), self.folder / f"{name}.osm.pbf")
        return [self.folder / f"{name}.osm.pbf" for name in names]

    def args(self, extract, regional=False):
        args = render_args(self.work / "cache", self.work / "map.svg", regional, "centerline")
        args.extract_file = str(extract)
        return args

    def geocode(self):
        return patch.object(ox, "geocode_to_gdf", return_value=gpd.GeoDataFrame(
            geometry=[self.city], crs="EPSG:4326"))

    def test_declared_coverage_is_read_from_the_header(self):
        self.assertEqual(osm_extract.extract_bounds(self.covering), (-119.0, 33.5, -117.5, 35.0))
        self.assertEqual(osm_extract.extract_bounds(self.other), ELSEWHERE)

    def test_candidates_and_cache_identity(self):
        (single,) = self.place("other")
        self.assertEqual(extract_candidates(single), [single.resolve()])
        self.assertEqual(extract_set_identity(single), source_identity(single))
        self.place("covering")
        self.assertEqual(extract_candidates(single),
                         [single.resolve(), (self.folder / "covering.osm.pbf").resolve()])
        self.assertNotEqual(extract_set_identity(single), source_identity(single))
        self.assertEqual(extract_set_identity(None), "overpass")

    def test_configured_extract_that_covers_is_kept(self):
        covering, _ = self.place("covering", "other")
        with self.geocode(), redirect_stdout(StringIO()):
            chosen, message = generator.choose_extract(self.args(covering), "Key")
        self.assertEqual(chosen, covering.resolve())
        self.assertIsNone(message)

    def test_sibling_extract_that_covers_is_used(self):
        other, covering = self.place("other", "covering")
        args = self.args(other)
        log = StringIO()
        with self.geocode(), redirect_stdout(log):
            generator.create_svg(args)
        self.assertIn("Using OSM extract covering.osm.pbf", log.getvalue())
        self.assertGreater(args.output.read_text(encoding="utf-8").count("<path"), 10)
        place = next((self.work / "cache").glob("Synthetic_*"))
        self.assertTrue((place / source_identity(covering)).is_dir())

    def test_no_covering_extract_tries_overpass(self):
        (other,) = self.place("other")
        features = gpd.GeoDataFrame(
            {"highway": ["residential"], "railway": [None]},
            geometry=[LineString([self.city.centroid.coords[0], self.city.bounds[2:]])],
            crs="EPSG:4326")
        log = StringIO()
        with self.geocode(), redirect_stdout(log), \
                patch.object(generator, "fetch_features_with_retry", return_value=features) as fetch:
            generator.load_geodata(self.args(other))
        self.assertTrue(fetch.called)
        self.assertIn("Trying OpenStreetMap Overpass instead", log.getvalue())

    def test_no_covering_extract_and_area_too_large_explains_download(self):
        (other,) = self.place("other")
        with self.geocode(), redirect_stdout(StringIO()), \
                patch.object(generator, "plan_polygon_queries",
                             side_effect=GeographicQueryTooLarge("too large")):
            with self.assertRaises(generator.ExtractDoesNotCover) as caught:
                generator.load_geodata(self.args(other, regional=True))
        text = str(caught.exception)
        self.assertIn("other.osm.pbf covers longitude -125.00 to -121.00", text)
        self.assertIn("download.geofabrik.de", text)
        self.assertIn(str(self.folder.resolve()), text)

    def test_extract_without_declared_area_is_trusted(self):
        xml = self.folder / "plain.osm"
        xml.write_text('<?xml version="1.0"?><osm version="0.6"></osm>', encoding="utf-8")
        self.assertIsNone(osm_extract.extract_bounds(xml))
        with patch.object(generator, "city_boundary_for_extract_choice",
                          side_effect=AssertionError("not needed")):
            chosen, message = generator.choose_extract(self.args(xml), "Key")
        self.assertEqual(chosen, xml.resolve())
        self.assertIsNone(message)


if __name__ == "__main__":
    unittest.main()
