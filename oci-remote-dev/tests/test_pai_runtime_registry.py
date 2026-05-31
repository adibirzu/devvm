import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from scripts.pai_runtime_registry import (
    RegistryError,
    enabled_runtimes,
    gateway_env_for,
    get_runtime,
    load_registry,
    resolve_command,
    validate_registry,
)

REPO_REGISTRY = Path(__file__).resolve().parent.parent / "agent-os" / "runtimes.json"


def _sample() -> dict:
    return {
        "version": 1,
        "runtimes": [
            {"name": "claude", "enabled": True, "exec_template": ["claude", "-p", "{prompt}"],
             "interactive_template": ["claude"], "gateway_routed": True, "gateway_env": "ANTHROPIC_BASE_URL"},
            {"name": "hermes", "aliases": ["herm"], "enabled": True, "exec_template": ["hermes", "--task", "{prompt}"],
             "gateway_routed": True, "gateway_env": "HERMES_BASE_URL"},
            {"name": "legacy", "enabled": False, "exec_template": ["legacy"]},
        ],
    }


class TestValidate(unittest.TestCase):
    def test_valid_returns_names(self) -> None:
        self.assertEqual(validate_registry(_sample()), ["claude", "hermes", "legacy"])

    def test_root_must_be_object(self) -> None:
        with self.assertRaises(RegistryError):
            validate_registry([])  # type: ignore[arg-type]

    def test_requires_nonempty_runtimes(self) -> None:
        with self.assertRaises(RegistryError):
            validate_registry({"runtimes": []})

    def test_missing_required_field(self) -> None:
        with self.assertRaises(RegistryError):
            validate_registry({"runtimes": [{"name": "x", "enabled": True}]})  # no exec_template

    def test_duplicate_name_rejected(self) -> None:
        dup = {"runtimes": [
            {"name": "a", "enabled": True, "exec_template": ["a"]},
            {"name": "a", "enabled": True, "exec_template": ["a"]},
        ]}
        with self.assertRaises(RegistryError):
            validate_registry(dup)

    def test_alias_collision_rejected(self) -> None:
        bad = {"runtimes": [
            {"name": "a", "enabled": True, "exec_template": ["a"], "aliases": ["b"]},
            {"name": "b", "enabled": True, "exec_template": ["b"]},
        ]}
        with self.assertRaises(RegistryError):
            validate_registry(bad)


class TestEnabled(unittest.TestCase):
    def test_enabled_excludes_disabled(self) -> None:
        names = [r["name"] for r in enabled_runtimes(_sample())]
        self.assertEqual(names, ["claude", "hermes"])


class TestResolve(unittest.TestCase):
    def test_resolve_substitutes_prompt(self) -> None:
        cmd = resolve_command(_sample(), "claude", prompt="do the thing")
        self.assertEqual(cmd, ["claude", "-p", "do the thing"])

    def test_resolve_by_alias(self) -> None:
        cmd = resolve_command(_sample(), "herm", prompt="x")
        self.assertEqual(cmd, ["hermes", "--task", "x"])

    def test_interactive_template(self) -> None:
        cmd = resolve_command(_sample(), "claude", interactive=True)
        self.assertEqual(cmd, ["claude"])

    def test_unknown_runtime_raises(self) -> None:
        with self.assertRaises(RegistryError):
            resolve_command(_sample(), "doesnotexist")

    def test_disabled_runtime_raises(self) -> None:
        with self.assertRaises(RegistryError):
            get_runtime(_sample(), "legacy")


class TestGatewayEnv(unittest.TestCase):
    def test_gateway_routed_env(self) -> None:
        env = gateway_env_for(_sample(), "claude", "http://10.200.200.1:8080")
        self.assertEqual(env, {"ANTHROPIC_BASE_URL": "http://10.200.200.1:8080"})

    def test_non_routed_returns_empty(self) -> None:
        data = {"runtimes": [{"name": "x", "enabled": True, "exec_template": ["x"], "gateway_routed": False}]}
        self.assertEqual(gateway_env_for(data, "x", "http://gw"), {})


class TestShippedRegistry(unittest.TestCase):
    """The actual agent-os/runtimes.json must be valid and contain the new runtimes."""

    def setUp(self) -> None:
        self.data = load_registry(str(REPO_REGISTRY))

    def test_repo_registry_is_valid(self) -> None:
        self.assertIn("claude", [r["name"] for r in self.data["runtimes"]])

    def test_new_runtimes_present_and_gateway_routed(self) -> None:
        idx = {r["name"]: r for r in self.data["runtimes"]}
        for name in ("antigravity", "hermes", "nanoclaw"):
            self.assertIn(name, idx, f"{name} missing from shipped registry")
            self.assertTrue(idx[name]["gateway_routed"], f"{name} should route via the gateway")

    def test_agy_alias_resolves_to_antigravity(self) -> None:
        self.assertEqual(get_runtime(self.data, "agy")["name"], "antigravity")

    def test_nano_claw_alias_resolves(self) -> None:
        self.assertEqual(get_runtime(self.data, "nano-claw")["name"], "nanoclaw")

    def test_load_bad_json_raises(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write("{ not json")
            bad = fh.name
        with self.assertRaises(RegistryError):
            load_registry(bad)


if __name__ == "__main__":
    unittest.main()
