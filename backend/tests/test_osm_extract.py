"""Synthetic regional OSM XML test: no Overpass connection necessary."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shapely.geometry import Polygon
from osm_extract import extract_features

OSM = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6" generator="City Map Studio tests">
  <node id="1" lat="42.000" lon="-71.015" version="1"/>
  <node id="2" lat="42.000" lon="-70.985" version="1"/>
  <node id="3" lat="42.005" lon="-71.005" version="1"/>
  <node id="4" lat="42.006" lon="-70.995" version="1"/>
  <node id="5" lat="43.000" lon="-72.000" version="1"/>
  <node id="6" lat="43.001" lon="-72.001" version="1"/>
  <way id="100" version="1">
    <nd ref="1"/><nd ref="2"/><tag k="highway" v="residential"/>
  </way>
  <way id="101" version="1">
    <nd ref="3"/><nd ref="4"/><tag k="railway" v="rail"/>
  </way>
  <way id="102" version="1">
    <nd ref="5"/><nd ref="6"/><tag k="highway" v="residential"/>
  </way>
  <way id="103" version="1">
    <nd ref="3"/><nd ref="4"/><tag k="building" v="yes"/>
  </way>
</osm>"""


class RegionalExtractTests(unittest.TestCase):
    def test_extract_clips_crossing_road_and_rail(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "city-test.osm"
            path.write_text(OSM, encoding="utf-8")
            boundary = Polygon([
                (-71.01, 41.99), (-70.99, 41.99),
                (-70.99, 42.01), (-71.01, 42.01),
            ])
            features = extract_features(path, boundary, {"residential"}, {"rail"})
            self.assertEqual(len(features), 2)
            self.assertEqual(set(features["highway"].dropna()), {"residential"})
            self.assertEqual(set(features["railway"].dropna()), {"rail"})
            self.assertTrue(all(boundary.covers(g) for g in features.geometry))
            road = features[features["highway"] == "residential"].geometry.iloc[0]
            self.assertAlmostEqual(road.bounds[0], -71.01, places=6)
            self.assertAlmostEqual(road.bounds[2], -70.99, places=6)

    def test_binary_pbf_roundtrip(self):
        """Convert the fixture to actual OSM PBF and import it offline."""
        import osmium

        with TemporaryDirectory() as folder:
            input_file = Path(folder) / "fixture.osm"
            output_file = Path(folder) / "fixture.osm.pbf"
            input_file.write_text(OSM, encoding="utf-8")

            class CopyHandler(osmium.SimpleHandler):
                def __init__(self, writer):
                    super().__init__()
                    self.writer = writer

                def node(self, node):
                    self.writer.add_node(node)

                def way(self, way):
                    self.writer.add_way(way)

            with osmium.SimpleWriter(str(output_file)) as writer:
                CopyHandler(writer).apply_file(str(input_file))

            boundary = Polygon([
                (-71.01, 41.99), (-70.99, 41.99),
                (-70.99, 42.01), (-71.01, 42.01),
            ])
            features = extract_features(
                output_file, boundary, {"residential"}, {"rail"}
            )
            self.assertEqual(len(features), 2)

    def test_missing_extract_is_explicit_error(self):
        with TemporaryDirectory() as folder:
            with self.assertRaises(FileNotFoundError):
                extract_features(
                    Path(folder) / "missing.osm.pbf",
                    Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
                    {"residential"}, {"rail"},
                )


if __name__ == "__main__":
    unittest.main()
