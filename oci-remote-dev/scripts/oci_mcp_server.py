#!/usr/bin/env python3
"""oci-readonly — a stdio MCP server exposing READ-ONLY OCI operations to agents.

Lets agents inspect tenancy/compartment/instance state without shell access to OCI
credentials, and WITHOUT the ability to mutate anything: every tool maps to a
hardcoded read-only `oci ... list|get` command (build_oci_command refuses anything
else). This is defense in depth on top of the PreToolUse guardrail.

Implements the minimal MCP stdio protocol (newline-delimited JSON-RPC 2.0):
initialize, tools/list, tools/call, ping. No third-party deps — uses the `oci` CLI.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "oci-readonly", "version": "1.0.0"}

# Tool name -> (oci argv builder fields). Each tool maps to a fixed read-only verb.
TOOLS: List[Dict[str, Any]] = [
    {
        "name": "oci_list_compartments",
        "description": "List compartments in the tenancy (read-only).",
        "inputSchema": {"type": "object", "properties": {
            "compartment_id": {"type": "string", "description": "Parent compartment OCID (default: tenancy root)"}}},
    },
    {
        "name": "oci_list_instances",
        "description": "List compute instances in a compartment (read-only).",
        "inputSchema": {"type": "object", "required": ["compartment_id"], "properties": {
            "compartment_id": {"type": "string", "description": "Compartment OCID"}}},
    },
    {
        "name": "oci_get_instance",
        "description": "Get a compute instance by OCID (read-only).",
        "inputSchema": {"type": "object", "required": ["instance_id"], "properties": {
            "instance_id": {"type": "string", "description": "Instance OCID"}}},
    },
    {
        "name": "oci_list_regions",
        "description": "List subscribed regions for the tenancy (read-only).",
        "inputSchema": {"type": "object", "properties": {}},
    },
]
TOOL_NAMES = {t["name"] for t in TOOLS}

# Read-only verb allowlist — build_oci_command will only ever emit these.
_READONLY_VERBS = {("iam", "compartment", "list"), ("compute", "instance", "list"),
                   ("compute", "instance", "get"), ("iam", "region-subscription", "list")}


def build_oci_command(tool: str, args: Dict[str, Any], profile: str) -> List[str]:
    """Build the oci CLI argv for a tool. Raises ValueError for anything not read-only."""
    base = ["oci"]
    if profile:
        base += ["--profile", profile]
    base += ["--output", "json"]
    if tool == "oci_list_compartments":
        verb = ("iam", "compartment", "list")
        cmd = base + list(verb) + ["--compartment-id-in-subtree", "true", "--all"]
        cid = args.get("compartment_id")
        if cid:
            cmd += ["--compartment-id", cid]
        else:
            cmd += ["--compartment-id-in-subtree", "true"]
    elif tool == "oci_list_instances":
        verb = ("compute", "instance", "list")
        cid = args.get("compartment_id")
        if not cid:
            raise ValueError("compartment_id is required")
        cmd = base + list(verb) + ["--compartment-id", cid, "--all"]
    elif tool == "oci_get_instance":
        verb = ("compute", "instance", "get")
        iid = args.get("instance_id")
        if not iid:
            raise ValueError("instance_id is required")
        cmd = base + list(verb) + ["--instance-id", iid]
    elif tool == "oci_list_regions":
        verb = ("iam", "region-subscription", "list")
        cmd = base + list(verb)
    else:
        raise ValueError(f"unknown tool: {tool}")
    if verb not in _READONLY_VERBS:
        raise ValueError(f"refusing non-read-only verb: {verb}")
    return cmd


def run_oci(tool: str, args: Dict[str, Any], profile: str, timeout: int = 30) -> Tuple[bool, str]:
    """Execute a read-only OCI tool. Returns (ok, text). IO; not unit-tested."""
    try:
        cmd = build_oci_command(tool, args, profile)
    except ValueError as e:
        return False, f"error: {e}"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"error running oci: {e}"
    if r.returncode != 0:
        return False, f"oci error (exit {r.returncode}): {r.stderr.strip()[:500]}"
    return True, r.stdout.strip() or "{}"


def handle_message(msg: Dict[str, Any], runner: Callable[[str, Dict[str, Any]], Tuple[bool, str]]) -> Optional[Dict[str, Any]]:
    """Process one JSON-RPC message; return a response dict, or None for notifications."""
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params", {}) or {}
        name = params.get("name", "")
        if name not in TOOL_NAMES:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {name}"}}
        ok, text = runner(name, params.get("arguments", {}) or {})
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": text}], "isError": not ok}}
    if method and method.startswith("notifications/"):
        return None  # notifications get no response
    if mid is not None:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return None


def main() -> int:
    profile = os.environ.get("OCI_CLI_PROFILE", os.environ.get("OCI_PROFILE", "DEFAULT"))

    def runner(name: str, args: Dict[str, Any]) -> Tuple[bool, str]:
        return run_oci(name, args, profile)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle_message(msg, runner)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
