import datetime
import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from scripts.agent_job import build_agent_argv, due_jobs, is_due


def _ts(now, secs_ago):
    return datetime.datetime.fromtimestamp(now - secs_ago, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TestSchedule(unittest.TestCase):
    NOW = 1_700_000_000.0

    def test_due_when_interval_elapsed(self) -> None:
        job = {"enabled": True, "interval_minutes": 60, "last_run": _ts(self.NOW, 4000)}
        self.assertTrue(is_due(job, self.NOW))   # 4000s > 3600s

    def test_not_due_within_interval(self) -> None:
        job = {"enabled": True, "interval_minutes": 60, "last_run": _ts(self.NOW, 1000)}
        self.assertFalse(is_due(job, self.NOW))

    def test_never_run_is_due(self) -> None:
        job = {"enabled": True, "interval_minutes": 60, "last_run": ""}
        self.assertTrue(is_due(job, self.NOW))

    def test_disabled_never_due(self) -> None:
        job = {"enabled": False, "interval_minutes": 1, "last_run": ""}
        self.assertFalse(is_due(job, self.NOW))

    def test_zero_interval_never_due(self) -> None:
        job = {"enabled": True, "interval_minutes": 0, "last_run": ""}
        self.assertFalse(is_due(job, self.NOW))

    def test_due_jobs_filters(self) -> None:
        jobs = [
            {"name": "a", "enabled": True, "interval_minutes": 60, "last_run": ""},
            {"name": "b", "enabled": True, "interval_minutes": 60, "last_run": _ts(self.NOW, 100)},
            {"name": "c", "enabled": False, "interval_minutes": 1, "last_run": ""},
        ]
        self.assertEqual([j["name"] for j in due_jobs(jobs, self.NOW)], ["a"])


class TestArgv(unittest.TestCase):
    def test_claude_headless(self) -> None:
        self.assertEqual(build_agent_argv("claude", "do x"), ["claude", "-p", "do x"])

    def test_codex_exec(self) -> None:
        self.assertEqual(build_agent_argv("codex", "do x"), ["codex", "exec", "do x"])

    def test_unknown_agent_fallback(self) -> None:
        self.assertEqual(build_agent_argv("aider", "do x"), ["aider", "-p", "do x"])


if __name__ == "__main__":
    unittest.main()
