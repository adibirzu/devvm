import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from scripts.control_plane import (
    HANDLERS,
    budgets_to_spec,
    dispatch,
    parse_budgets_request,
    validate_developer_request,
)

GOOD_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAExample adi@mac"


def base_deps():
    recorded = {"queue": [], "audit": [], "budgets": None}
    return {
        "fleet_status": lambda: {"developers": [{"name": "adi"}], "totals": {"running": 1}},
        "developers": lambda: [{"name": "adi", "home": "/home/adi"}],
        "services": lambda: [{"unit": "multillm-gateway.service", "state": "active"}],
        "pending": lambda: list(recorded["queue"]),
        "admin_token": lambda: "secret123",
        "enqueue": lambda c: recorded["queue"].append(c),
        "audit": lambda e: recorded["audit"].append(e),
        "set_budgets": lambda spec: recorded.__setitem__("budgets", spec),
    }, recorded


def ctx(headers=None, body=None):
    return {"headers": headers or {}, "body": body or {}}


class TestValidators(unittest.TestCase):
    def test_developer_ok(self) -> None:
        ok, errs = validate_developer_request({"name": "carlos", "ssh_key": GOOD_KEY})
        self.assertTrue(ok); self.assertEqual(errs, [])

    def test_developer_bad_name(self) -> None:
        ok, errs = validate_developer_request({"name": "Bad Name!", "ssh_key": GOOD_KEY})
        self.assertFalse(ok)

    def test_developer_bad_key(self) -> None:
        ok, errs = validate_developer_request({"name": "carlos", "ssh_key": "not-a-key"})
        self.assertFalse(ok)

    def test_budgets_parse(self) -> None:
        ok, b, errs = parse_budgets_request({"budgets": {"adi": 5, "royce": "10"}})
        self.assertTrue(ok); self.assertEqual(b, {"adi": 5.0, "royce": 10.0})

    def test_budgets_flat(self) -> None:
        ok, b, _ = parse_budgets_request({"adi": 3})
        self.assertTrue(ok); self.assertEqual(b, {"adi": 3.0})

    def test_budgets_bad(self) -> None:
        ok, _, errs = parse_budgets_request({"adi": "lots"})
        self.assertFalse(ok)

    def test_budgets_to_spec(self) -> None:
        self.assertEqual(budgets_to_spec({"royce": 10.0, "adi": 5.0}), "adi=5,royce=10")


class TestReadRoutes(unittest.TestCase):
    def test_healthz(self) -> None:
        deps, _ = base_deps()
        self.assertEqual(dispatch("GET", "/healthz", HANDLERS, deps)[0], 200)

    def test_fleet_status(self) -> None:
        deps, _ = base_deps()
        s, b = dispatch("GET", "/fleet/status", HANDLERS, deps)
        self.assertEqual(b["totals"]["running"], 1)

    def test_pending(self) -> None:
        deps, _ = base_deps()
        self.assertEqual(dispatch("GET", "/pending", HANDLERS, deps)[1]["pending"], [])

    def test_404(self) -> None:
        deps, _ = base_deps()
        self.assertEqual(dispatch("GET", "/nope", HANDLERS, deps)[0], 404)

    def test_405_known_path_wrong_method(self) -> None:
        deps, _ = base_deps()
        self.assertEqual(dispatch("PUT", "/healthz", HANDLERS, deps)[0], 405)


class TestWriteRoutes(unittest.TestCase):
    def test_post_developer_requires_token(self) -> None:
        deps, rec = base_deps()
        s, b = dispatch("POST", "/developers", HANDLERS, deps, ctx(body={"name": "carlos", "ssh_key": GOOD_KEY}))
        self.assertEqual(s, 401)
        self.assertEqual(rec["queue"], [])

    def test_post_developer_queues_with_token(self) -> None:
        deps, rec = base_deps()
        s, b = dispatch("POST", "/developers", HANDLERS, deps,
                        ctx(headers={"x-admin-token": "secret123"}, body={"name": "carlos", "ssh_key": GOOD_KEY}))
        self.assertEqual(s, 202)
        self.assertEqual(rec["queue"][0]["op"], "add")
        self.assertEqual(rec["audit"][0]["action"], "queue_add_developer")

    def test_post_developer_validation(self) -> None:
        deps, rec = base_deps()
        s, b = dispatch("POST", "/developers", HANDLERS, deps,
                        ctx(headers={"x-admin-token": "secret123"}, body={"name": "X!", "ssh_key": "no"}))
        self.assertEqual(s, 422)
        self.assertEqual(rec["queue"], [])

    def test_delete_developer_queues(self) -> None:
        deps, rec = base_deps()
        s, b = dispatch("DELETE", "/developers/royce", HANDLERS, deps, ctx(headers={"x-admin-token": "secret123"}))
        self.assertEqual(s, 202)
        self.assertEqual(rec["queue"][0], {"op": "remove", "name": "royce"})

    def test_delete_developer_requires_token(self) -> None:
        deps, rec = base_deps()
        self.assertEqual(dispatch("DELETE", "/developers/royce", HANDLERS, deps, ctx())[0], 401)

    def test_post_budgets_applies_live(self) -> None:
        deps, rec = base_deps()
        s, b = dispatch("POST", "/budgets", HANDLERS, deps,
                        ctx(headers={"x-admin-token": "secret123"}, body={"budgets": {"adi": 5, "royce": 10}}))
        self.assertEqual(s, 200)
        self.assertEqual(rec["budgets"], "adi=5,royce=10")
        self.assertEqual(rec["audit"][0]["action"], "set_budgets")

    def test_post_budgets_requires_token(self) -> None:
        deps, rec = base_deps()
        self.assertEqual(dispatch("POST", "/budgets", HANDLERS, deps, ctx(body={"adi": 5}))[0], 401)
        self.assertIsNone(rec["budgets"])

    def test_empty_admin_token_denies(self) -> None:
        deps, rec = base_deps()
        deps["admin_token"] = lambda: ""   # not configured → deny even an empty header match
        s, _ = dispatch("POST", "/budgets", HANDLERS, deps, ctx(headers={"x-admin-token": ""}, body={"adi": 5}))
        self.assertEqual(s, 401)


if __name__ == "__main__":
    unittest.main()
