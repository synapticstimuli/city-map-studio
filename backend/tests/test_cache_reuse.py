"""Offline tests: python -m unittest discover -s backend/tests -v"""
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cache_reuse import CACHE_SCHEMA, RENDER_VERSION, request_identity, reusable_map, svg_sha256


class CacheReuseTest(unittest.TestCase):
    def request(self, **kwargs):
        fields = dict(city="Somerville", county="Middlesex", state="Massachusetts",
                      country="USA", style="fine", road_mode="outline",
                      include_paths=True, show_boundary=False)
        fields.update(kwargs)
        return SimpleNamespace(**fields)

    def test_normalized_location_key(self):
        self.assertEqual(request_identity(self.request()),
                         request_identity(self.request(city="  SOMERVILLE  ")))

    def test_options_change_render_key(self):
        original = request_identity(self.request())[1]
        for field, value in [("style", "bold"), ("road_mode", "centerline"),
                             ("include_paths", False), ("show_boundary", True)]:
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
