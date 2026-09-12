import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEVPORT = ROOT / "scripts" / "devport.py"


class TestDevportCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name)
        self.config = self.home / ".config/devport/config.json"
        self.config.parent.mkdir(parents=True)
        self.write_config(23000, 23002)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_config(self, start: int, end: int) -> None:
        self.config.write_text(
            json.dumps(
                {"range_start": start, "range_end": end, "url_host": "10.0.0.1"}
            ),
            encoding="utf-8",
        )

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "HOME": str(self.home),
            "DEVPORT_CONFIG": str(self.config),
            "XDG_STATE_HOME": str(self.home / "state"),
        }
        return subprocess.run(
            [sys.executable, str(DEVPORT), *args],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

    def test_claim_is_stable_and_listed(self) -> None:
        first = self.run_cli("claim", "tree-a")
        second = self.run_cli("claim", "tree-a")
        listing = self.run_cli("list")

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, "http://10.0.0.1:23000\n")
        self.assertEqual(second.stdout, first.stdout)
        self.assertEqual(listing.stdout, "tree-a\t23000\thttp://10.0.0.1:23000\n")

    def test_release_makes_the_port_reusable(self) -> None:
        self.assertEqual(self.run_cli("claim", "tree-a").returncode, 0)
        released = self.run_cli("release", "tree-a")
        replacement = self.run_cli("claim", "tree-b")

        self.assertEqual(released.stdout, "released tree-a\n")
        self.assertEqual(replacement.stdout, "http://10.0.0.1:23000\n")

    def test_claim_skips_a_port_occupied_outside_the_broker(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 23000))
            listener.listen()
            claim = self.run_cli("claim", "tree-a")

        self.assertEqual(claim.returncode, 0, claim.stderr)
        self.assertEqual(claim.stdout, "http://10.0.0.1:23001\n")

    def test_a_claim_outside_a_reconfigured_range_is_reallocated(self) -> None:
        first = self.run_cli("claim", "tree-a")
        self.assertEqual(first.stdout, "http://10.0.0.1:23000\n")

        self.write_config(24000, 24002)
        second = self.run_cli("claim", "tree-a")

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout, "http://10.0.0.1:24000\n")
        self.assertIn("re-allocating", second.stderr)

    def test_env_exports_common_server_variables(self) -> None:
        result = self.run_cli("env", "tree-a")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("export PORT=23000\n", result.stdout)
        self.assertIn("export DEV_URL=http://10.0.0.1:23000\n", result.stdout)
        self.assertIn("export UVICORN_PORT=23000\n", result.stdout)
        self.assertIn("export FLASK_RUN_PORT=23000\n", result.stdout)


if __name__ == "__main__":
    unittest.main()
