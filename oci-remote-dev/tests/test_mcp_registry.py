import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from scripts.mcp_registry import merge_mcp, render_server

REG = {
    "servers": [
        {"name": "multillm", "enabled": True, "command": "python3",
         "args": ["-m", "multillm.mcp_server"], "env": {"LLM_GATEWAY_URL": "http://localhost:${PORT}"}},
        {"name": "oci-readonly", "enabled": True, "command": "python3",
         "args": ["/usr/local/lib/agent-os/oci_mcp_server.py"]},
        {"name": "legacy", "enabled": False, "command": "python3", "args": ["x"]},
    ]
}
SUBS = {"PORT": "8080"}


class TestSubst(unittest.TestCase):
    def test_render_substitutes_env(self) -> None:
        out = render_server(REG["servers"][0], SUBS)
        self.assertEqual(out["env"]["LLM_GATEWAY_URL"], "http://localhost:8080")
        self.assertEqual(out["args"], ["-m", "multillm.mcp_server"])

    def test_render_no_env(self) -> None:
        out = render_server(REG["servers"][1], SUBS)
        self.assertNotIn("env", out)


class TestMerge(unittest.TestCase):
    def test_adds_enabled_servers(self) -> None:
        merged = merge_mcp({}, REG, SUBS)
        self.assertIn("multillm", merged["mcpServers"])
        self.assertIn("oci-readonly", merged["mcpServers"])

    def test_disabled_server_removed(self) -> None:
        existing = {"mcpServers": {"legacy": {"command": "old"}}}
        merged = merge_mcp(existing, REG, SUBS)
        self.assertNotIn("legacy", merged["mcpServers"])

    def test_preserves_unmanaged_personal_server(self) -> None:
        existing = {"mcpServers": {"my-experiment": {"command": "node", "args": ["s.js"]}}}
        merged = merge_mcp(existing, REG, SUBS)
        self.assertIn("my-experiment", merged["mcpServers"])   # untouched
        self.assertIn("multillm", merged["mcpServers"])         # added

    def test_updates_existing_managed_server(self) -> None:
        existing = {"mcpServers": {"multillm": {"command": "STALE", "args": []}}}
        merged = merge_mcp(existing, REG, SUBS)
        self.assertEqual(merged["mcpServers"]["multillm"]["command"], "python3")

    def test_idempotent(self) -> None:
        once = merge_mcp({}, REG, SUBS)
        twice = merge_mcp(once, REG, SUBS)
        self.assertEqual(once, twice)


if __name__ == "__main__":
    unittest.main()
