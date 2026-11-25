"""Offline end-to-end checks for City-only and Regional SVG exports."""
import sys
import re
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
from shapely.ops import transform
from pyproj import Transformer
from generate_city_map_v2_1 import create_svg, load_geodata, regional_cache_name, regional_display_frame


def group_coordinate_bounds(group):
    """Read the SVG path coordinates emitted by City Map Studio."""
    points = []
    for path in group.findall(".//{http://www.w3.org/2000/svg}path"):
        points.extend(
            (float(x), float(y))
            for x, y in re.findall(
                r"[ML](-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)",
                path.get("d", ""),
            )
        )
    if not points:
        raise AssertionError("SVG group contains no path coordinates")
    return (
        min(x for x, _ in points), min(y for _, y in points),
        max(x for x, _ in points), max(y for _, y in points),
    )


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
            width=420, height=297, margin=None, road_stroke=None,
            boundary_stroke=None, refresh_data=False, overpass_timeout=300,
            cache_dir=self.root / "cache", extract_file=None,
            output=self.root / ("regional.svg" if regional else "city.svg"),
        )

    def seed(self, args):
        place_dir = args.cache_dir / self.place
        folder = (place_dir / regional_cache_name(args.width, args.height)) if args.regional_context else place_dir
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

    def test_regional_fetch_covers_whole_printed_page(self):
        """A first-time regional download covers the full page (ADR-004)."""
        args = self.args(True)
        test_boundary = gpd.GeoDataFrame(
            geometry=[self.boundary], crs="EPSG:4326"
        )
        fixture_features = gpd.GeoDataFrame(
            {"highway": ["residential"], "railway": [None]},
            geometry=[LineString([(-71.01, 42.01), (-70.99, 42.01)])],
            crs="EPSG:4326",
        )
        with (
            patch.object(ox, "geocode_to_gdf", return_value=test_boundary),
            patch("generate_city_map_v2_1.fetch_features_with_retry",
                  return_value=fixture_features) as fetch,
        ):
            load_geodata(args)
        queried = fetch.call_args.args[0]
        crs = test_boundary.estimate_utm_crs()
        project = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform
        city_metric = transform(project, self.boundary)
        queried_metric = transform(project, queried)
        _, page = regional_display_frame(city_metric, args.width, args.height)
        # The whole page is fetched, plus no more than a small edge margin.
        self.assertTrue(queried_metric.buffer(0.01).covers(page))
        self.assertTrue(queried_metric.buffer(0.01).covers(city_metric))
        self.assertLess(queried_metric.area, page.area * 1.05)
        # The page is far larger than the old 3 km halo would reach along
        # the page's long side for this square city on landscape paper.
        self.assertGreater(page.bounds[2] - page.bounds[0], (page.bounds[3] - page.bounds[1]) * 1.4)

    def test_city_only_and_regional_export_layers(self):
        for regional in (False, True):
            for road_mode in ("centerline", "outline"):
                with self.subTest(regional=regional, road_mode=road_mode):
                    args = self.args(regional)
                    args.road_mode = road_mode
                    args.output = self.root / f"{road_mode}_{'regional' if regional else 'city'}.svg"
                    self.seed(args)
                    with patch.object(ox, "geocode_to_gdf", side_effect=AssertionError("Network geocoding")), \
                         patch.object(ox.features, "features_from_polygon", side_effect=AssertionError("Overpass")):
                        create_svg(args)
                    root = ET.parse(args.output).getroot()
                    nodes = {node.get("id"): node for node in root.iter() if node.get("id")}
                    self.assertIn("road-centerlines" if road_mode == "centerline" else "road-edges", nodes)
                    self.assertIn("city-boundary", nodes)
                    self.assertGreater(len(list(nodes["road-centerlines" if road_mode == "centerline" else "road-edges"])), 0)
                    if regional:
                        self.assertIn("regional-context", nodes)
                        self.assertGreater(len(list(nodes["regional-context"])), 0)
                        # The focus-city BBOX is centered and fills 95% of
                        # the limiting page dimension (ADR-004).
                        x0, y0, x1, y1 = group_coordinate_bounds(nodes["city-boundary"])
                        city_fraction = max((x1 - x0) / args.width, (y1 - y0) / args.height)
                        self.assertAlmostEqual(city_fraction, 0.95, places=3)
                        self.assertAlmostEqual((x0 + x1) / 2, args.width / 2, places=2)
                        self.assertAlmostEqual((y0 + y1) / 2, args.height / 2, places=2)

                        # Surrounding paths fill the page up to its edges and
                        # never beyond, although source data extends further.
                        # Context remains an independent SVG layer.
                        cx0, cy0, cx1, cy1 = group_coordinate_bounds(nodes["regional-context"])
                        self.assertGreaterEqual(cx0, -0.01)
                        self.assertLessEqual(cx1, args.width + 0.01)
                        self.assertGreaterEqual(cy0, -0.01)
                        self.assertLessEqual(cy1, args.height + 0.01)
                        self.assertAlmostEqual(cx0, 0.0, places=2)
                        # Focus boundary is visible and emphasized for regional output.
                        self.assertNotIn("display:none", nodes["city-boundary"].get("style", ""))
                    else:
                        self.assertNotIn("regional-context", nodes)
                        self.assertIn("display:none", nodes["city-boundary"].get("style", ""))
                        # City-only maps also fill 95% of the page, centered.
                        x0, y0, x1, y1 = group_coordinate_bounds(nodes["city-boundary"])
                        city_fraction = max((x1 - x0) / args.width, (y1 - y0) / args.height)
                        self.assertAlmostEqual(city_fraction, 0.95, places=3)
                        self.assertAlmostEqual((x0 + x1) / 2, args.width / 2, places=2)
                        self.assertAlmostEqual((y0 + y1) / 2, args.height / 2, places=2)

    def test_explicit_margin_keeps_v22_city_only_layout(self):
        args = self.args(False)
        args.margin = 18
        self.seed(args)
        with patch.object(ox, "geocode_to_gdf", side_effect=AssertionError("Network geocoding")):
            create_svg(args)
        root = ET.parse(args.output).getroot()
        boundary = next(n for n in root.iter() if n.get("id") == "city-boundary")
        x0, y0, x1, y1 = group_coordinate_bounds(boundary)
        # Limiting dimension spans the page minus both 18 mm margins.
        self.assertAlmostEqual(max((x1 - x0) / (420 - 36), (y1 - y0) / (297 - 36)), 1.0, places=3)



class PageBorderTests(unittest.TestCase):
    def test_default_page_is_a3_inside_a_1cm_border(self):
        """ADR-006: the app adds a 10 mm border; the artwork fills the rest."""
        import generate_city_map_v2_1 as generator
        self.assertEqual(generator.PAGE_BORDER_MM, 10)
        self.assertEqual((generator.PAGE_WIDTH_MM, generator.PAGE_HEIGHT_MM), (400, 277))
        app = (Path(__file__).resolve().parents[2] / "app.js").read_text(encoding="utf-8")
        self.assertIn("const PAGE_BORDER_MM = 10;", app)

if __name__ == "__main__":
    unittest.main()
