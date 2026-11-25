"""The v2.3 renderer speedups must not change any exported SVG byte.

``clip_to`` and ``project_geometries`` replace per-way GEOS overlays against
the full city boundary and per-way pyproj calls. These tests compare them with
the original operations on awkward geometries, then render complete SVGs both
ways and require identical files.
"""
import math
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import generate_city_map_v2_1 as generator  # noqa: E402
from benchmark_render import render_args, seed_cache, synthetic_boundary  # noqa: E402


def wiggly_area():
    points = []
    for i in range(800):
        angle = 2 * math.pi * i / 800
        radius = 1000 * (1 + 0.2 * math.sin(9 * angle) + 0.05 * math.sin(61 * angle))
        points.append((radius * math.cos(angle), radius * math.sin(angle)))
    return Polygon(points)


def assert_same_geometry(test, expected, actual):
    """Same type, same parts, same vertices in the same order."""
    test.assertEqual(expected.is_empty, actual.is_empty)
    if expected.is_empty:
        return
    test.assertEqual(expected.geom_type, actual.geom_type)
    test.assertTrue(
        np.array_equal(shapely.get_coordinates(expected), shapely.get_coordinates(actual)),
        f"coordinates differ for {expected.geom_type}",
    )
    test.assertTrue(shapely.equals_exact(expected, actual, tolerance=0))


class ClipToTests(unittest.TestCase):
    def cases(self):
        inside_line = LineString([(-100, -50), (0, 10), (120, 40)])
        return {
            "line inside": inside_line,
            "line crossing edge": LineString([(0, 0), (2000, 30)]),
            "line outside": LineString([(3000, 0), (3100, 50)]),
            "line touching edge": LineString([(0, 0), (1200, 0)]),
            "self-crossing line inside": LineString(
                [(0, 0), (100, 100), (100, 0), (0, 100)]
            ),
            "closed way inside": LineString(
                [(10, 10), (60, 10), (60, 60), (10, 60), (10, 10)]
            ),
            "multiline inside": MultiLineString(
                [[(0, 0), (50, 0)], [(0, 20), (50, 25)]]
            ),
            "buffered road inside": inside_line.buffer(4, cap_style=2, join_style=2),
            "buffered road crossing": LineString([(0, 0), (2000, 30)]).buffer(
                4, cap_style=2, join_style=2
            ),
            "plaza with courtyard": Polygon(
                [(0, 0), (80, 0), (80, 80), (0, 80)],
                [[(20, 20), (20, 60), (60, 60), (60, 20)]],
            ),
            "roundabout ring": Point(200, 200).buffer(15).exterior,
        }

    def test_matches_plain_intersection(self):
        area = wiggly_area()
        # A regional-style clip area: a page window minus the focus city.
        ring = box(-2500, -2500, 2500, 2500).difference(area)
        for area_name, clip_area in (("city", area), ("context ring", ring)):
            prepared = shapely.from_wkb(shapely.to_wkb(clip_area))
            shapely.prepare(prepared)
            for name, geom in self.cases().items():
                with self.subTest(area=area_name, case=name):
                    expected = geom.intersection(clip_area)
                    actual = generator.clip_to(geom, prepared)
                    assert_same_geometry(self, expected, actual)

    def test_fast_paths_skip_full_overlay(self):
        area = wiggly_area()
        shapely.prepare(area)
        calls = []
        original = BaseGeometry.intersection

        def spy(self, other, *args, **kwargs):
            calls.append(other)
            return original(self, other, *args, **kwargs)

        with patch.object(BaseGeometry, "intersection", spy):
            generator.clip_to(LineString([(3000, 0), (3100, 0)]), area)
            generator.clip_to(LineString([(0, 0), (50, 0)]), area)
        self.assertTrue(all(other is not area for other in calls))


class ProjectionTests(unittest.TestCase):
    def test_vectorized_projection_matches_per_geometry_transform(self):
        transformer = Transformer.from_crs("EPSG:4326", "EPSG:32611", always_xy=True)
        geoms = [
            LineString([(-118.3, 34.0), (-118.2, 34.1), (-118.25, 34.05)]),
            MultiLineString([[(-118.3, 34.0), (-118.29, 34.0)], [(-118.1, 34.2), (-118.0, 34.3)]]),
            Point(-118.25, 34.05).buffer(0.01),
            synthetic_boundary(300),
        ]
        projected = generator.project_geometries(geoms, transformer)
        for original, fast in zip(geoms, projected):
            assert_same_geometry(self, transform(transformer.transform, original), fast)


_Drawing = generator.svgwrite.Drawing


def validating_drawing(*args, **kwargs):
    kwargs["debug"] = True
    return _Drawing(*args, **kwargs)


class ClipManyTests(unittest.TestCase):
    def test_matches_clip_to_element_by_element(self):
        area = wiggly_area()
        ring = box(-2500, -2500, 2500, 2500).difference(area)
        cases = list(ClipToTests().cases().values())
        for clip_area in (area, ring):
            shapely.prepare(clip_area)
            batch = generator.clip_many(cases, clip_area)
            for geom, fast in zip(cases, batch):
                assert_same_geometry(self, geom.intersection(clip_area), fast)


class ByteIdenticalSvgTests(unittest.TestCase):
    def test_all_modes_match_clipping_every_way(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache = root / "cache"
            boundary = synthetic_boundary(600)
            for regional in (False, True):
                seed_cache(cache, regional, boundary, grid=24)
            for regional in (False, True):
                for road_mode in ("centerline", "outline"):
                    with self.subTest(regional=regional, road_mode=road_mode):
                        outputs = []
                        for label in ("fast", "reference"):
                            out = root / f"{label}-{regional}-{road_mode}.svg"
                            with ExitStack() as stack:
                                stack.enter_context(redirect_stdout(StringIO()))
                                if label == "reference":
                                    # v2.2 behaviour: a full overlay per way and
                                    # svgwrite attribute validation switched on.
                                    stack.enter_context(patch.object(
                                        generator, "clip_to",
                                        lambda geom, area: geom.intersection(area)))
                                    stack.enter_context(patch.object(
                                        generator, "clip_many",
                                        lambda geoms, area: np.array(
                                            [g.intersection(area) for g in geoms], dtype=object)))
                                    stack.enter_context(patch.object(
                                        generator.svgwrite, "Drawing", validating_drawing))
                                generator.create_svg(render_args(cache, out, regional, road_mode))
                            outputs.append(out.read_bytes())
                        self.assertGreater(len(outputs[0]), 10_000)
                        self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()
