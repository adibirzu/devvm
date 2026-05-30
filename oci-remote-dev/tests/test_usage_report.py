import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from scripts.usage_report import (
    evaluate_budgets,
    format_model_table,
    format_project_table,
    format_user_table,
    parse_budgets,
    render_budget_report,
    render_report,
    render_team_report,
    summarize_totals,
)


TEAM_SAMPLE = {
    "window_hours": 168,
    "totals": {"users": 2, "accounts": 3, "requests": 40,
               "input_tokens": 90000, "output_tokens": 25000, "cost_usd": 1.23},
    "by_user": [
        {"bucket": "adi", "requests": 30, "input_tokens": 80000,
         "output_tokens": 20000, "cache_tokens": 5000, "cost_usd": 1.10},
        {"bucket": "royce", "requests": 10, "input_tokens": 10000,
         "output_tokens": 5000, "cache_tokens": 0, "cost_usd": 0.13},
    ],
}


SAMPLE = {
    "by_model": [
        {
            "model_alias": "claude-sonnet-4-6",
            "backend": "anthropic",
            "request_count": 12,
            "total_input": 34000,
            "total_output": 8000,
            "total_cost_usd": 0.42,
            "error_count": 1,
        },
        {
            "model_alias": "llama3",
            "backend": "ollama",
            "request_count": 5,
            "total_input": 1000,
            "total_output": 2000,
            "total_cost_usd": 0.0,
            "error_count": 0,
        },
    ],
    "by_project": [
        {"project": "devvm", "requests": 17, "input_tokens": 35000, "output_tokens": 10000, "cost_usd": 0.42},
        {"project": None, "requests": 3, "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.0},
    ],
}


class TestUsageReport(unittest.TestCase):
    def test_summarize_totals(self) -> None:
        totals = summarize_totals(SAMPLE["by_model"])
        self.assertEqual(totals["requests"], 17)
        self.assertEqual(totals["input"], 35000)
        self.assertEqual(totals["output"], 10000)
        self.assertAlmostEqual(totals["cost"], 0.42)
        self.assertEqual(totals["errors"], 1)

    def test_totals_handle_none_aggregates(self) -> None:
        # SQL SUM over an empty group returns NULL/None — must not crash.
        rows = [{"request_count": None, "total_input": None, "total_output": None,
                 "total_cost_usd": None, "error_count": None}]
        totals = summarize_totals(rows)
        self.assertEqual(totals["cost"], 0.0)
        self.assertEqual(totals["requests"], 0.0)

    def test_model_table_contains_rows(self) -> None:
        out = format_model_table(SAMPLE["by_model"])
        self.assertIn("claude-sonnet-4-6", out)
        self.assertIn("ollama", out)
        self.assertIn("$0.4200", out)

    def test_empty_model_table(self) -> None:
        self.assertIn("no model usage", format_model_table([]))

    def test_project_table_renders_untagged(self) -> None:
        out = format_project_table(SAMPLE["by_project"])
        self.assertIn("devvm", out)
        self.assertIn("(untagged)", out)  # None project label

    def test_render_report_has_sections_and_totals(self) -> None:
        out = render_report(SAMPLE, hours=24)
        self.assertIn("last 24h", out)
        self.assertIn("By model:", out)
        self.assertIn("By project:", out)
        self.assertIn("Totals:", out)
        self.assertIn("cost=$0.4200", out)

    def test_render_report_empty_payload(self) -> None:
        out = render_report({}, hours=12)
        self.assertIn("no model usage", out)
        self.assertIn("no project usage", out)
        self.assertIn("cost=$0.0000", out)


class TestTeamUsageReport(unittest.TestCase):
    def test_user_table_lists_developers(self) -> None:
        out = format_user_table(TEAM_SAMPLE["by_user"])
        self.assertIn("adi", out)
        self.assertIn("royce", out)
        self.assertIn("$1.1000", out)

    def test_user_table_handles_missing_bucket(self) -> None:
        out = format_user_table([{"requests": 1}])
        self.assertIn("(unknown)", out)

    def test_render_team_report_sections(self) -> None:
        out = render_team_report(TEAM_SAMPLE, hours=168)
        self.assertIn("team usage — last 168h", out)
        self.assertIn("By developer (tenant):", out)
        self.assertIn("developers=2", out)
        self.assertIn("cost=$1.2300", out)

    def test_render_team_report_empty(self) -> None:
        out = render_team_report({}, hours=24)
        self.assertIn("no developer usage", out)
        self.assertIn("cost=$0.0000", out)


class TestBudgets(unittest.TestCase):
    def test_parse_budgets_basic(self) -> None:
        self.assertEqual(parse_budgets("adi=5,royce=10"), {"adi": 5.0, "royce": 10.0})

    def test_parse_budgets_tolerant(self) -> None:
        # whitespace, blanks, and malformed pairs are skipped, not fatal
        self.assertEqual(parse_budgets(" adi = 5 , ,bad,royce=x,carlos=2.5"),
                         {"adi": 5.0, "carlos": 2.5})

    def test_parse_budgets_empty(self) -> None:
        self.assertEqual(parse_budgets(""), {})

    def test_evaluate_flags_over_and_under(self) -> None:
        rows = evaluate_budgets(TEAM_SAMPLE["by_user"], {"adi": 1.0, "royce": 1.0})
        by_user = {r["user"]: r for r in rows}
        self.assertTrue(by_user["adi"]["over"])      # spend 1.10 > cap 1.00
        self.assertFalse(by_user["royce"]["over"])   # spend 0.13 < cap 1.00

    def test_evaluate_includes_zero_usage_developer(self) -> None:
        rows = evaluate_budgets(TEAM_SAMPLE["by_user"], {"newdev": 5.0})
        self.assertEqual(rows[0]["spend"], 0.0)
        self.assertFalse(rows[0]["over"])

    def test_render_budget_report_breach_banner(self) -> None:
        out = render_budget_report(TEAM_SAMPLE["by_user"], {"adi": 1.0}, hours=24)
        self.assertIn("OVER", out)
        self.assertIn("over budget: adi", out)

    def test_render_budget_report_all_ok(self) -> None:
        out = render_budget_report(TEAM_SAMPLE["by_user"], {"adi": 99.0}, hours=24)
        self.assertIn("within budget", out)

    def test_render_budget_report_no_budgets(self) -> None:
        out = render_budget_report(TEAM_SAMPLE["by_user"], {}, hours=24)
        self.assertIn("no budgets configured", out)


if __name__ == "__main__":
    unittest.main()
