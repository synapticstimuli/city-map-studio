"""Offline tests: python -m unittest discover -s backend/tests -v"""
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cache_reuse import CACHE_SCHEMA, RENDER_VERSION, request_identity, reusable_map, svg_sha256, source_identity, geographic_cache_folder


class CacheReuseTest(unittest.TestCase):
    def request(self, **kwargs):
        fields = dict(city="Somerville", county="Middlesex", state="Massachusetts",
                      country="USA", style="fine", road_mode="outline",
                      include_paths=True, show_boundary=False,
                      regional_context=False, regional_buffer_km=3.0)
        fields.update(kwargs)
        return SimpleNamespace(**fields)

    def test_normalized_location_key(self):
        self.assertEqual(request_identity(self.request()),
                         request_identity(self.request(city="  SOMERVILLE  ")))

    def test_geography_cache_folder_preserves_legacy_overpass_and_separates_extracts(self):
        root = Path("/data")
        old = geographic_cache_folder(root, "Somerville_Middlesex_MA_USA", "overpass")
        first = geographic_cache_folder(root, "Somerville_Middlesex_MA_USA", "extract-aaa")
        second = geographic_cache_folder(root, "Somerville_Middlesex_MA_USA", "extract-bbb")
        self.assertEqual(old, root / "Somerville_Middlesex_MA_USA")
        self.assertNotEqual(first, old)
        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, old)

    def test_distinct_data_sources_have_distinct_render_keys(self):
        overpass = request_identity(self.request(), "generator", "overpass")
        extract = request_identity(self.request(), "generator", "extract-abc")
        self.assertNotEqual(overpass[0], extract[0])
        self.assertNotEqual(overpass[1], extract[1])

    def test_extract_revision_change_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "region.osm.pbf"
            path.write_bytes(b"fixture-v1")
            first = source_identity(path)
            path.write_bytes(b"fixture-v2-with-new-size")
            second = source_identity(path)
            self.assertNotEqual(first, second)
            self.assertEqual(source_identity(None), "overpass")
            self.assertEqual(source_identity(""), "overpass")

    def test_missing_extract_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(OSError):
                source_identity(Path(folder) / "missing.osm.pbf")

    def test_generator_change_invalidates_render_cache(self):
        self.assertNotEqual(request_identity(self.request(), "old")[1],
                            request_identity(self.request(), "new")[1])

    def test_options_change_render_key(self):
        original = request_identity(self.request())[1]
        for field, value in [("style", "bold"), ("road_mode", "centerline"),
                             ("include_paths", False), ("show_boundary", True),
                             ("regional_context", True), ("regional_buffer_km", 5.0)]:
            self.assertNotEqual(original, request_identity(self.request(**{field: value}))[1])

    def test_match_and_unsafe_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            identifier = str(uuid.uuid4())
            render_key = request_identity(self.request())[1]
            (root / "map.svg").write_text("<svg/>", encoding="utf-8")
            metadata = dict(id=identifier, filename="map.svg", cache_schema=CACHE_SCHEMA,
                            renderer_version=RENDER_VERSION, render_key=render_key,
                            svg_sha256=svg_sha256(root / "map.svg"))
            (root / f"{identifier}.json").write_text(json.dumps(metadata), encoding="utf-8")
            self.assertEqual(reusable_map(root, render_key)["id"], identifier)
            metadata["filename"] = "../outside.svg"
            (root / f"{identifier}.json").write_text(json.dumps(metadata), encoding="utf-8")
            self.assertIsNone(reusable_map(root, render_key))
            metadata["filename"] = "map.svg"
            (root / f"{identifier}.json").write_text(json.dumps(metadata), encoding="utf-8")
            (root / "map.svg").write_text("<svg>corrupted</svg>", encoding="utf-8")
            self.assertIsNone(reusable_map(root, render_key))

    def test_legacy_record_not_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "old.json").write_text('{"id":"old","filename":"map.svg"}')
            self.assertIsNone(reusable_map(root, "anything"))


if __name__ == "__main__":
    unittest.main()
