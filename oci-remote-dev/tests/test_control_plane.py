import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from scripts.control_plane import HANDLERS, dispatch

DEPS = {
    "fleet_status": lambda: {"developers": [{"name": "adi"}], "totals": {"running": 1}},
    "developers": lambda: [{"name": "adi", "home": "/home/adi"}],
    "services": lambda: [{"unit": "multillm-gateway.service", "state": "active"}],
}


class TestDispatch(unittest.TestCase):
    def test_healthz(self) -> None:
        status, body = dispatch("GET", "/healthz", HANDLERS, DEPS)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_fleet_status(self) -> None:
        status, body = dispatch("GET", "/fleet/status", HANDLERS, DEPS)
        self.assertEqual(status, 200)
        self.assertEqual(body["totals"]["running"], 1)

    def test_developers(self) -> None:
        status, body = dispatch("GET", "/developers", HANDLERS, DEPS)
        self.assertEqual(status, 200)
        self.assertEqual(body["developers"][0]["name"], "adi")

    def test_services(self) -> None:
        status, body = dispatch("GET", "/fleet/services", HANDLERS, DEPS)
        self.assertEqual(body["services"][0]["state"], "active")

    def test_404(self) -> None:
        status, body = dispatch("GET", "/nope", HANDLERS, DEPS)
        self.assertEqual(status, 404)

    def test_405_on_write(self) -> None:
        # read-only API: any non-GET is rejected (no mutations here)
        status, body = dispatch("POST", "/developers", HANDLERS, DEPS)
        self.assertEqual(status, 405)

    def test_handler_exception_is_500_not_crash(self) -> None:
        bad = {"fleet_status": lambda: (_ for _ in ()).throw(RuntimeError("boom"))}
        status, body = dispatch("GET", "/fleet/status", HANDLERS, bad)
        self.assertEqual(status, 500)
        self.assertIn("detail", body)


if __name__ == "__main__":
    unittest.main()
