"""Regression fence for the resilience layer + memory palace assets.

Pure file/syntax checks — no tmux or network needed, so it runs anywhere.
"""

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PALACE = ROOT / ".memory-palace"
SCRIPTS = ROOT / "scripts"

EXPECTED_ROOMS = {
    "README.md", "00-INDEX.md", "ARCHITECTURE.md", "DECISIONS.md",
    "SESSION-LOG.md", "OPEN-THREADS.md", "GLOSSARY.md",
}


class TestMemoryPalace(unittest.TestCase):
    def test_palace_exists(self) -> None:
        self.assertTrue(PALACE.is_dir(), "memory palace directory missing")

    def test_all_expected_rooms_present(self) -> None:
        present = {p.name for p in PALACE.glob("*.md")}
        missing = EXPECTED_ROOMS - present
        self.assertFalse(missing, f"missing palace rooms: {missing}")

    def test_each_room_has_a_title(self) -> None:
        for room in PALACE.glob("*.md"):
            first = room.read_text(encoding="utf-8").lstrip().splitlines()[0]
            self.assertTrue(first.startswith("# "), f"{room.name} lacks an H1 title")

    def test_open_threads_is_actionable(self) -> None:
        text = (PALACE / "OPEN-THREADS.md").read_text(encoding="utf-8")
        self.assertIn("[", text)  # contains checklist items


class TestScriptSyntax(unittest.TestCase):
    def _bash_n(self, name: str) -> None:
        script = SCRIPTS / name
        self.assertTrue(script.exists(), f"{name} missing")
        result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f"{name} syntax error: {result.stderr}")

    def test_agentctl_syntax(self) -> None:
        self._bash_n("agentctl.sh")

    def test_palace_syntax(self) -> None:
        self._bash_n("palace.sh")

    def test_connect_syntax(self) -> None:
        self._bash_n("connect.sh")

    def test_no_bash4_only_lowercase_in_palace(self) -> None:
        # `${var,,}` / `${var^^}` break on macOS bash 3.2 — guard against regressions.
        text = (SCRIPTS / "palace.sh").read_text(encoding="utf-8")
        self.assertNotIn(",,}", text)
        self.assertNotIn("^^}", text)


if __name__ == "__main__":
    unittest.main()
