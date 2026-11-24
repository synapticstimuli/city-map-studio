"""Offline tests for bounded, separated administrative query planning."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shapely.geometry import MultiPolygon, Polygon
from query_planner import GeographicQueryTooLarge, plan_polygon_queries


class QueryPlannerTests(unittest.TestCase):
    def test_multipart_boundary_avoids_combined_convex_hull(self):
        # Simulates distant islands. Combined hull would be much larger.
        west = Polygon([(-123.5, 37.5), (-123.49, 37.5),
                        (-123.49, 37.51), (-123.5, 37.51)])
        east = Polygon([(-122.5, 37.5), (-122.49, 37.5),
                        (-122.49, 37.51), (-122.5, 37.51)])
        plan = plan_polygon_queries(MultiPolygon([west, east]))
        self.assertEqual(len(plan.polygons), 2)
        self.assertLess(plan.projected_hull_km2, 10.0)

    def test_large_polygon_is_rejected_before_request(self):
        giant = Polygon([(-124, 37), (-122, 37), (-122, 39), (-124, 39)])
        with self.assertRaises(GeographicQueryTooLarge):
            plan_polygon_queries(giant)

    def test_no_silent_component_dropping(self):
        polygons = []
        for i in range(4):
            x = -123 + i * 0.03
            polygons.append(Polygon([(x, 37), (x + 0.01, 37),
                                     (x + 0.01, 37.01), (x, 37.01)]))
        plan = plan_polygon_queries(MultiPolygon(polygons))
        self.assertEqual(len(plan.polygons), 4)
        with self.assertRaises(GeographicQueryTooLarge):
            plan_polygon_queries(MultiPolygon(polygons), max_components=3)

    def test_invalid_boundary_rejected(self):
        invalid = Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])
        with self.assertRaises(ValueError):
            plan_polygon_queries(invalid)

    def test_nonpositive_budget_rejected(self):
        geometry = Polygon([(0, 0), (0.01, 0), (0.01, 0.01), (0, 0.01)])
        with self.assertRaises(ValueError):
            plan_polygon_queries(geometry, max_total_km2=0)


if __name__ == "__main__":
    unittest.main()
