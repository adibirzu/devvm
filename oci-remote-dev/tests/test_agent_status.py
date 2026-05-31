import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from scripts.agent_status import (
    apply_notifications,
    evaluate_budget_status,
    merge_board,
    parse_budgets,
    parse_meta_dir,
    recent_guardrail,
    recent_notifications,
    state_from_live,
    summarize_guardrail,
)


class TestParseMeta(unittest.TestCase):
    def test_parses_env_files_with_spaces_in_values(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            meta = Path(d)
            (meta / "a.env").write_text(
                "name=agent:proj:claude\nagent=claude\nproject=proj\n"
                "dir=/home/adi/x\ncmd=sleep 120\nstarted_at=2026-05-30T10:00:00Z\n",
                encoding="utf-8",
            )
            rows = parse_meta_dir(meta)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["name"], "agent:proj:claude")
            self.assertEqual(rows[0]["cmd"], "sleep 120")  # value with space preserved

    def test_missing_dir_returns_empty(self) -> None:
        self.assertEqual(parse_meta_dir(Path("/nonexistent/xyz")), [])

    def test_skips_files_without_name(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "bad.env").write_text("agent=claude\n", encoding="utf-8")
            self.assertEqual(parse_meta_dir(Path(d)), [])


class TestState(unittest.TestCase):
    def test_dead_when_not_live(self) -> None:
        self.assertEqual(state_from_live("agent:p:claude", {}), "dead")

    def test_running_when_unattached(self) -> None:
        live = {"agent_p_claude": "0"}
        self.assertEqual(state_from_live("agent:p:claude", live), "running")

    def test_attached_when_attached(self) -> None:
        live = {"agent_p_claude": "1"}
        self.assertEqual(state_from_live("agent:p:claude", live), "attached")


class TestMergeBoard(unittest.TestCase):
    def _fixture(self):
        per_user = {
            "adi": [
                {"name": "agent:web:claude", "agent": "claude", "project": "web",
                 "dir": "/home/adi/web", "started_at": "t1"},
                {"name": "agent:api:codex", "agent": "codex", "project": "api",
                 "dir": "/home/adi/api", "started_at": "t2"},
            ],
            "royce": [],
        }
        live = {"adi": {"agent_web_claude": "1"}}  # claude attached; codex dead
        costs = {"adi": 1.25, "royce": 0.0}
        return merge_board(per_user, live, costs)

    def test_totals(self) -> None:
        b = self._fixture()
        self.assertEqual(b["totals"]["sessions"], 2)
        self.assertEqual(b["totals"]["running"], 1)         # only attached/running count
        self.assertAlmostEqual(b["totals"]["cost_usd"], 1.25)

    def test_developer_states(self) -> None:
        b = self._fixture()
        adi = next(d for d in b["developers"] if d["name"] == "adi")
        states = {s["agent"]: s["state"] for s in adi["sessions"]}
        self.assertEqual(states["claude"], "attached")
        self.assertEqual(states["codex"], "dead")
        self.assertEqual(adi["cost_usd_24h"], 1.25)

    def test_developers_sorted_and_empty_handled(self) -> None:
        b = self._fixture()
        self.assertEqual([d["name"] for d in b["developers"]], ["adi", "royce"])
        royce = next(d for d in b["developers"] if d["name"] == "royce")
        self.assertEqual(royce["sessions"], [])


class TestNotifications(unittest.TestCase):
    NOW = 1_700_000_600.0  # fixed "now"; events stamped relative to it

    def _line(self, session, secs_ago, msg="needs input"):
        import datetime
        ts = datetime.datetime.fromtimestamp(self.NOW - secs_ago, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f'{{"ts":"{ts}","session":"{session}","user":"adi","message":"{msg}"}}'

    def test_recent_keeps_in_window_drops_old(self) -> None:
        lines = [self._line("agent:web:claude", 60), self._line("agent:api:codex", 5000)]
        out = recent_notifications(lines, self.NOW, window_sec=600)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["session"], "agent:web:claude")

    def test_recent_ignores_malformed_lines(self) -> None:
        lines = ["not json", "", self._line("agent:web:claude", 10)]
        self.assertEqual(len(recent_notifications(lines, self.NOW)), 1)

    def test_apply_sets_needs_input_on_matching_session(self) -> None:
        devs = [{"name": "adi", "sessions": [
            {"name": "agent:web:claude"}, {"name": "agent:api:codex"}]}]
        notifs = {"adi": [{"session": "agent:web:claude", "message": "input?"}]}
        ringing = apply_notifications(devs, notifs)
        self.assertEqual(ringing, 1)
        states = {s["name"]: s["needs_input"] for s in devs[0]["sessions"]}
        self.assertTrue(states["agent:web:claude"])
        self.assertFalse(states["agent:api:codex"])
        self.assertTrue(devs[0]["needs_input"])

    def test_apply_no_notifications_means_quiet(self) -> None:
        devs = [{"name": "royce", "sessions": [{"name": "agent:x:claude"}]}]
        self.assertEqual(apply_notifications(devs, {}), 0)
        self.assertFalse(devs[0]["needs_input"])
        self.assertFalse(devs[0]["sessions"][0]["needs_input"])


class TestGuardrailAudit(unittest.TestCase):
    NOW = 1_700_000_600.0

    def _line(self, action, secs_ago, summary="$ rm -rf /"):
        import datetime
        ts = datetime.datetime.fromtimestamp(self.NOW - secs_ago, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f'{{"ts":"{ts}","tool":"Bash","action":"{action}","rule":"x","reason":"r","summary":"{summary}"}}'

    def test_recent_keeps_deny_ask_drops_allow_and_old(self) -> None:
        lines = [self._line("deny", 60), self._line("ask", 120),
                 self._line("allow", 30), self._line("deny", 99999)]
        out = recent_guardrail(lines, self.NOW, window_sec=3600)
        actions = sorted(e["action"] for e in out)
        self.assertEqual(actions, ["ask", "deny"])   # allow excluded, old deny excluded

    def test_summarize_counts_and_flattens(self) -> None:
        events = {
            "adi": [{"action": "deny", "ts": "2026-05-31T10:00:00Z"}],
            "royce": [{"action": "ask", "ts": "2026-05-31T11:00:00Z"},
                      {"action": "deny", "ts": "2026-05-31T09:00:00Z"}],
        }
        s = summarize_guardrail(events)
        self.assertEqual(s["denied"], 2)
        self.assertEqual(s["asked"], 1)
        self.assertEqual(s["recent"][0]["ts"], "2026-05-31T11:00:00Z")  # newest first
        self.assertEqual(s["recent"][0]["user"], "royce")

    def test_summarize_empty(self) -> None:
        s = summarize_guardrail({})
        self.assertEqual((s["denied"], s["asked"], s["recent"]), (0, 0, []))


class TestBudgetStatus(unittest.TestCase):
    def test_parse_budgets(self) -> None:
        self.assertEqual(parse_budgets("adi=5, royce=10"), {"adi": 5.0, "royce": 10.0})

    def test_flags_over_budget(self) -> None:
        devs = [{"name": "adi", "cost_usd_24h": 6.5}, {"name": "royce", "cost_usd_24h": 1.0}]
        over = evaluate_budget_status(devs, "adi=5,royce=10")
        self.assertEqual(over, 1)
        self.assertTrue(devs[0]["budget"]["over"])
        self.assertFalse(devs[1]["budget"]["over"])

    def test_no_budget_means_none(self) -> None:
        devs = [{"name": "carlos", "cost_usd_24h": 99}]
        self.assertEqual(evaluate_budget_status(devs, "adi=5"), 0)
        self.assertIsNone(devs[0]["budget"])

    def test_empty_spec(self) -> None:
        devs = [{"name": "adi", "cost_usd_24h": 1}]
        self.assertEqual(evaluate_budget_status(devs, ""), 0)


if __name__ == "__main__":
    unittest.main()
