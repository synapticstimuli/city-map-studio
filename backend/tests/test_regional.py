"""Offline end-to-end checks for City-only and Regional SVG exports."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import geopandas as gpd
import osmnx as ox
from shapely.geometry import Polygon, LineString
from generate_city_map_v2_1 import create_svg


class RegionalRenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.place = "Fixture_Location_Massachusetts_USA"
        self.boundary = Polygon([
            (-71.01, 42.0), (-70.99, 42.0),
            (-70.99, 42.02), (-71.01, 42.02),
        ])

    def args(self, regional):
        return SimpleNamespace(
            city="Fixture", county="", state="Massachusetts", country="USA",
            style="fine", road_mode="centerline", include_paths=True,
            show_boundary=False, regional_context=regional,
            regional_buffer_km=3.0, show_rail=True, include_service=True,
            include_tracks=True, include_underground_rail=False,
            width=420, height=297, margin=18, road_stroke=None,
            boundary_stroke=None, refresh_data=False, overpass_timeout=300,
            cache_dir=self.root / "cache", extract_file=None,
            output=self.root / ("regional.svg" if regional else "city.svg"),
        )

    def seed(self, args):
        place_dir = args.cache_dir / self.place
        folder = (place_dir / "regional_3000m") if args.regional_context else place_dir
        folder.mkdir(parents=True, exist_ok=True)
        gpd.GeoDataFrame(
            geometry=[self.boundary], crs="EPSG:4326"
        ).to_file(folder / "boundary.geojson", driver="GeoJSON")
        features = gpd.GeoDataFrame(
            {"highway": ["residential", "residential", "footway"],
             "railway": [None, None, None]},
            geometry=[
                LineString([(-71.012, 42.01), (-70.988, 42.01)]),
                LineString([(-71.03, 42.015), (-71.012, 42.015)]),
                LineString([(-71.005, 42.025), (-71.005, 42.005)]),
            ],
            crs="EPSG:4326",
        )
        features.to_file(folder / "osm_features.geojson", driver="GeoJSON")

    def test_city_only_and_regional_export_layers(self):
        for regional in (False, True):
            with self.subTest(regional=regional):
                args = self.args(regional)
                self.seed(args)
                with patch.object(ox, "geocode_to_gdf", side_effect=AssertionError("Network geocoding")), \
                     patch.object(ox.features, "features_from_polygon", side_effect=AssertionError("Overpass")):
                    create_svg(args)
                root = ET.parse(args.output).getroot()
                nodes = {node.get("id"): node for node in root.iter() if node.get("id")}
                self.assertIn("road-centerlines", nodes)
                self.assertIn("city-boundary", nodes)
                self.assertGreater(len(list(nodes["road-centerlines"])), 0)
                if regional:
                    self.assertIn("regional-context", nodes)
                    self.assertGreater(len(list(nodes["regional-context"])), 0)
                    # Focus boundary is visible and emphasized for regional output.
                    self.assertNotIn("display:none", nodes["city-boundary"].get("style", ""))
                else:
                    self.assertNotIn("regional-context", nodes)
                    self.assertIn("display:none", nodes["city-boundary"].get("style", ""))


if __name__ == "__main__":
    unittest.main()
