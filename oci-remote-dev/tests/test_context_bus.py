import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from scripts.context_bus import (
    SHARED_SCOPE,
    build_put_payload,
    format_memory_rows,
    main,
    scope_for,
    tenant_for,
)


class TestTenant(unittest.TestCase):
    def test_private_tenant_is_user(self) -> None:
        self.assertEqual(tenant_for("adi", shared=False), "adi")

    def test_shared_tenant(self) -> None:
        self.assertEqual(tenant_for("adi", shared=True), SHARED_SCOPE)

    def test_all_scope_no_tenant(self) -> None:
        self.assertEqual(tenant_for("adi", shared=False, all_scopes=True), "")

    def test_tenant_distinct_per_user(self) -> None:
        self.assertNotEqual(tenant_for("adi", False), tenant_for("royce", False))


class TestScoping(unittest.TestCase):
    def test_default_scope_is_per_user(self) -> None:
        self.assertEqual(scope_for("adi", shared=False), "user-adi")

    def test_shared_scope(self) -> None:
        self.assertEqual(scope_for("adi", shared=True), SHARED_SCOPE)

    def test_shared_is_same_for_all_users(self) -> None:
        self.assertEqual(scope_for("adi", True), scope_for("royce", True))

    def test_user_namespaces_are_distinct(self) -> None:
        self.assertNotEqual(scope_for("adi", False), scope_for("royce", False))


class TestPayload(unittest.TestCase):
    def test_build_put_payload_fields(self) -> None:
        p = build_put_payload("t", "c", "decision", "user-adi")
        self.assertEqual(p["title"], "t")
        self.assertEqual(p["content"], "c")
        self.assertEqual(p["category"], "decision")
        self.assertEqual(p["project"], "user-adi")
        self.assertIn("source_llm", p)


class TestFormatting(unittest.TestCase):
    def test_empty_rows(self) -> None:
        self.assertIn("no matching memories", format_memory_rows([]))

    def test_rows_render_id_project_title(self) -> None:
        rows = [{"id": "abcd1234ef", "project": "shared", "title": "Decision X",
                 "content": "we chose   split tunnel   for routing"}]
        out = format_memory_rows(rows)
        self.assertIn("[abcd1234]", out)       # id truncated to 8
        self.assertIn("(shared)", out)
        self.assertIn("Decision X", out)
        self.assertIn("split tunnel", out)      # whitespace-collapsed snippet

    def test_rows_tolerate_missing_fields(self) -> None:
        out = format_memory_rows([{}])
        self.assertIn("(untitled)", out)


class TestArgRouting(unittest.TestCase):
    def test_unknown_command_errors(self) -> None:
        # argparse exits non-zero for an invalid subcommand
        with self.assertRaises(SystemExit):
            main(["bogus"])

    def test_missing_subcommand_errors(self) -> None:
        with self.assertRaises(SystemExit):
            main([])


if __name__ == "__main__":
    unittest.main()
