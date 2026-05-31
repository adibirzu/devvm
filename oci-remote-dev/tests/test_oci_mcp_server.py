import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from scripts.oci_mcp_server import (
    TOOL_NAMES,
    build_oci_command,
    handle_message,
)


class TestCommandBuilder(unittest.TestCase):
    def test_list_compartments_is_readonly(self) -> None:
        cmd = build_oci_command("oci_list_compartments", {}, "cap")
        self.assertEqual(cmd[:2], ["oci", "--profile"])
        self.assertIn("compartment", cmd)
        self.assertIn("list", cmd)
        # never a mutating verb
        for bad in ("delete", "terminate", "create", "update"):
            self.assertNotIn(bad, cmd)

    def test_list_instances_requires_compartment(self) -> None:
        with self.assertRaises(ValueError):
            build_oci_command("oci_list_instances", {}, "DEFAULT")
        cmd = build_oci_command("oci_list_instances", {"compartment_id": "ocid1.compartment.oc1..x"}, "DEFAULT")
        self.assertIn("--compartment-id", cmd)

    def test_get_instance_requires_id(self) -> None:
        with self.assertRaises(ValueError):
            build_oci_command("oci_get_instance", {}, "")
        cmd = build_oci_command("oci_get_instance", {"instance_id": "ocid1.instance.oc1..y"}, "")
        self.assertIn("get", cmd)
        self.assertNotIn("--profile", cmd)  # empty profile omitted

    def test_unknown_tool_raises(self) -> None:
        with self.assertRaises(ValueError):
            build_oci_command("oci_delete_everything", {}, "cap")

    def test_json_output_forced(self) -> None:
        self.assertIn("--output", build_oci_command("oci_list_regions", {}, "cap"))


def _fake_runner(name, args):
    return True, f'{{"ran": "{name}"}}'


class TestProtocol(unittest.TestCase):
    def test_initialize(self) -> None:
        r = handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, _fake_runner)
        self.assertEqual(r["result"]["serverInfo"]["name"], "oci-readonly")
        self.assertIn("tools", r["result"]["capabilities"])

    def test_tools_list_exposes_only_readonly(self) -> None:
        r = handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, _fake_runner)
        names = {t["name"] for t in r["result"]["tools"]}
        self.assertEqual(names, TOOL_NAMES)
        self.assertTrue(all(n.startswith("oci_") for n in names))

    def test_tools_call_dispatches(self) -> None:
        r = handle_message({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                            "params": {"name": "oci_list_regions", "arguments": {}}}, _fake_runner)
        self.assertFalse(r["result"]["isError"])
        self.assertIn("oci_list_regions", r["result"]["content"][0]["text"])

    def test_tools_call_unknown_tool_errors(self) -> None:
        r = handle_message({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                            "params": {"name": "nope"}}, _fake_runner)
        self.assertIn("error", r)

    def test_notification_returns_none(self) -> None:
        self.assertIsNone(handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}, _fake_runner))

    def test_unknown_method_errors(self) -> None:
        r = handle_message({"jsonrpc": "2.0", "id": 5, "method": "frobnicate"}, _fake_runner)
        self.assertEqual(r["error"]["code"], -32601)

    def test_runner_failure_marks_iserror(self) -> None:
        r = handle_message({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                            "params": {"name": "oci_list_regions"}}, lambda n, a: (False, "boom"))
        self.assertTrue(r["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
