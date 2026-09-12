"""Harness-dimension cost tracking (LiteLLM User-Agent equivalent).

Pure-function tests for the per-coding-harness breakdown in usage_report and
agent_status. No network, no Ansible — the HTTP fetches are tested with a
mocked urlopen.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parent.parent))

from scripts import agent_status, usage_report


class FakeResp:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestNormalizeHarness(unittest.TestCase):
    CASES = {
        "claude-cli/1.0": "claude",
        "Claude Code": "claude",
        "codex": "codex",
        "cursor-agent": "cursor-agent",
        "cursor": "cursor",
        "pi-signed": "pi-signed",
        "pi-coding-agent": "pi",
        "copilot": "copilot",  # must not collapse to pi
        "Gemini CLI": "gemini",
        "kimi-code": "kimi",
        "muse": "muse",
        "agy": "agy",
        "cline": "cline",
        "aider": "aider",
        "opencode-ai": "opencode",
        "grok": "grok",
        "openrouter": "other",
        "": "other",
        None: "other",
    }

    def test_usage_report_normalizes(self) -> None:
        for raw, want in self.CASES.items():
            with self.subTest(raw=raw):
                self.assertEqual(usage_report.normalize_harness(raw), want)

    def test_agent_status_agrees(self) -> None:
        for raw, want in self.CASES.items():
            with self.subTest(raw=raw):
                self.assertEqual(agent_status.normalize_harness(raw), want)


class TestHarnessTable(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertIn("no per-harness", usage_report.format_harness_table([]))

    def test_rows_use_canonical_names(self) -> None:
        out = usage_report.format_harness_table(
            [
                {
                    "harness": "claude-cli/2.0",
                    "requests": 5,
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cost_usd": 0.1,
                }
            ]
        )
        self.assertIn("claude", out)
        self.assertNotIn("claude-cli", out)
        self.assertIn("$0.1000", out)

    def test_team_report_shows_harness_section_when_served(self) -> None:
        out = usage_report.render_team_report(
            {
                "by_user": [],
                "by_harness": [
                    {
                        "harness": "codex",
                        "requests": 1,
                        "input_tokens": 2,
                        "output_tokens": 3,
                        "cost_usd": 0.01,
                    }
                ],
                "totals": {},
            },
            24,
        )
        self.assertIn("By coding harness", out)
        self.assertIn("codex", out)

    def test_team_report_silent_on_old_gateways(self) -> None:
        out = usage_report.render_team_report({"by_user": [], "totals": {}}, 24)
        self.assertNotIn("By coding harness", out)


class TestBoardHarness(unittest.TestCase):
    def _per_user(self):
        return {
            "adi": [
                {"name": "a1", "agent": "claude", "project": "p", "dir": "", "started_at": ""},
                {"name": "a2", "agent": "codex", "project": "p", "dir": "", "started_at": ""},
            ]
        }

    def test_merge_board_aggregates_by_harness(self) -> None:
        b = agent_status.merge_board(
            self._per_user(), {"adi": {"a1": "1"}}, {"adi": 1.0}
        )
        self.assertEqual(
            b["by_harness"],
            {"claude": {"sessions": 1, "running": 1}, "codex": {"sessions": 1, "running": 0}},
        )

    def test_build_with_joins_harness_cost(self) -> None:
        b = agent_status.build_with(
            self._per_user(),
            {"adi": {"a1": "1"}},
            {"adi": 1.0},
            harness_costs={"claude": 0.5},
        )
        self.assertEqual(b["by_harness"]["claude"]["cost_usd"], 0.5)
        self.assertNotIn("cost_usd", b["by_harness"]["codex"])
        self.assertEqual(b["harness_costs"], {"claude": 0.5})

    def test_build_with_defaults_to_no_harness_cost(self) -> None:
        b = agent_status.build_with(self._per_user(), {}, {})
        self.assertEqual(b["harness_costs"], {})

    def test_fetch_harness_costs(self) -> None:
        payload = {
            "by_harness": [
                {"harness": "claude-cli/1.0", "cost_usd": 0.3},
                {"harness": "claude", "cost_usd": 0.2},
            ]
        }
        with patch.object(
            agent_status.urllib.request, "urlopen", return_value=FakeResp(payload)
        ):
            self.assertEqual(
                agent_status.fetch_harness_costs("http://x:8080"),
                {"claude": 0.5},
            )

    def test_fetch_harness_costs_empty_on_old_gateway(self) -> None:
        with patch.object(
            agent_status.urllib.request, "urlopen", return_value=FakeResp({})
        ):
            self.assertEqual(agent_status.fetch_harness_costs("http://x:8080"), {})

    def test_fetch_harness_costs_empty_on_error(self) -> None:
        with patch.object(
            agent_status.urllib.request,
            "urlopen",
            side_effect=OSError("down"),
        ):
            self.assertEqual(agent_status.fetch_harness_costs("http://x:8080"), {})


if __name__ == "__main__":
    unittest.main()
