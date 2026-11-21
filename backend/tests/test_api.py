"""API integration tests with a synthetic generator (no Overpass requests)."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class APIFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.output_patch = patch.object(server, "OUTPUT_DIR", self.output)
        self.output_patch.start()
        self.addCleanup(self.output_patch.stop)
        server.jobs.clear()
        self.addCleanup(server.jobs.clear)

    def req(self, **kwargs):
        fields = dict(city="Somerville", state="Massachusetts", county="Middlesex",
                      country="USA")
        fields.update(kwargs)
        return server.MapRequest(**fields)

    def submit_without_running(self, request):
        with patch.object(server.executor, "submit") as submit:
            response = server.create_map(request, SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")))
        return response, submit

    def test_location_validation(self):
        with self.assertRaises(ValueError):
            self.req(city="Invalid@city")
        with self.assertRaises(ValueError):
            server.MapRequest(city="A", state="MA")
        self.assertEqual(server.MapRequest(city="Medford", state="MA", county="").county, "")

    def test_active_duplicate_job(self):
        request = self.req()
        first, submit = self.submit_without_running(request)
        self.assertEqual(first["status"], "queued")
        self.assertEqual(submit.call_count, 1)
        second, submit = self.submit_without_running(request)
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(submit.call_count, 0)
        self.assertEqual(len(server.jobs), 1)

    def test_queue_position_and_private_timing_not_exposed(self):
        first, _ = self.submit_without_running(self.req())
        second, _ = self.submit_without_running(self.req(road_mode="centerline"))
        first_status = server.map_status(first["id"])
        second_status = server.map_status(second["id"])
        self.assertEqual(first_status["queue_position"], 1)
        self.assertEqual(second_status["queue_position"], 2)
        self.assertNotIn("_queued_monotonic", first_status)

    def test_completion_and_persistent_cache_hit(self):
        request = self.req()
        created, _ = self.submit_without_running(request)

        def fake_run(command, **kwargs):
            destination = Path(command[command.index("--output") + 1])
            destination.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
            class Result:
                returncode = 0
            return Result()

        with patch.object(server.subprocess, "run", side_effect=fake_run):
            server.run_job(created["id"], request)
        completed_job = server.map_status(created["id"])
        self.assertEqual(completed_job["status"], "complete")
        self.assertGreaterEqual(completed_job["generation_seconds"], 0)
        self.assertGreaterEqual(completed_job["queue_seconds"], 0)
        self.assertGreaterEqual(completed_job["total_seconds"], completed_job["generation_seconds"])
        self.assertNotIn("_queued_monotonic", completed_job)
        self.assertEqual(len(list(self.output.glob("*.json"))), 1)
        self.assertEqual(len(list(self.output.glob("*.svg"))), 1)

        server.jobs.clear()  # Simulate backend restart.
        cached, submit = self.submit_without_running(request)
        self.assertEqual(cached["status"], "complete")
        self.assertTrue(cached["cache_hit"])
        self.assertEqual(cached["id"], created["id"])
        self.assertEqual(submit.call_count, 0)

        # Corrupt a completed file: identical requests must regenerate.
        saved_svg = next(self.output.glob("*.svg"))
        saved_svg.write_text("<svg>damaged</svg>", encoding="utf-8")
        server.jobs.clear()
        recovery, submit = self.submit_without_running(request)
        self.assertEqual(recovery["status"], "queued")
        self.assertNotEqual(recovery["id"], created["id"])
        self.assertEqual(submit.call_count, 1)

        other, submit = self.submit_without_running(self.req(road_mode="centerline"))
        self.assertEqual(other["status"], "queued")
        self.assertNotEqual(other["id"], cached["id"])
        self.assertEqual(submit.call_count, 1)

    def test_large_city_failure_provides_actionable_error_and_log(self):
        request = self.req(city="Los Angeles", county="Los Angeles", state="California")
        created, _ = self.submit_without_running(request)

        def fail_large_boundary(command, **kwargs):
            kwargs["stdout"].write(
                "Traceback (most recent call last):\\n"
                "GeographicQueryTooLarge: Boundary component 1 needs 1200 km2\\n"
            )
            kwargs["stdout"].flush()
            class Result:
                returncode = 1
            return Result()

        with patch.object(server.subprocess, "run", side_effect=fail_large_boundary):
            server.run_job(created["id"], request)
        status = server.map_status(created["id"])
        self.assertEqual(status["status"], "error")
        self.assertIn("safe Overpass area limit", status["error"])
        self.assertIn("OSM_EXTRACT_FILE", status["error"])
        self.assertTrue((self.output / f"{created['id']}.log").is_file())

    def test_remote_client_cannot_generate(self):
        with self.assertRaises(server.HTTPException) as caught:
            server.create_map(self.req(), SimpleNamespace(client=SimpleNamespace(host="198.51.100.2")))
        self.assertEqual(caught.exception.status_code, 403)

    def test_production_does_not_generate(self):
        with patch.object(server, "APP_ENV", "production"):
            with self.assertRaises(server.HTTPException) as caught:
                self.submit_without_running(self.req())
        self.assertEqual(caught.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
