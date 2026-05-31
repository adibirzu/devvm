#!/usr/bin/env python3
"""guardrail — policy engine for agent tool calls.

Decides allow / ask / deny for a tool invocation (Bash command, file write, etc.)
given a declarative policy. Used by the Claude Code PreToolUse hook to block
destructive actions and require confirmation for risky ones — the enforcement
point for the agentic OS, applied per-user before any tool runs.

The policy is data-driven (rules with first-match-wins). `decide()` is pure and
unit-tested; policy loading from disk is the only IO.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

POLICY_FILE = os.environ.get("GUARDRAIL_POLICY", "/etc/agent-os/policy.json")

# Default policy — conservative but practical. Ordered; first match wins.
# action: deny (block), ask (require user confirmation), allow.
DEFAULT_POLICY: Dict[str, Any] = {
    "allowed_write_roots": ["~", "/opt/shared-dev", "/tmp"],
    "rules": [
        # --- Catastrophic shell: hard deny ---
        {"id": "rm-rf-root", "action": "deny", "tool": "Bash",
         "command_regex": r"rm\s+-[a-z]*r[a-z]*f?\s+(/|~|\$HOME|/\*|--no-preserve-root)",
         "reason": "Recursive force-delete of a root/home path is blocked."},
        {"id": "disk-destroyer", "action": "deny", "tool": "Bash",
         "command_regex": r"\b(mkfs|fdisk|wipefs)\b|dd\s+if=\S+\s+of=/dev/|>\s*/dev/sd",
         "reason": "Direct disk/partition writes are blocked."},
        {"id": "fork-bomb", "action": "deny", "tool": "Bash",
         "command_regex": r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:",
         "reason": "Fork bomb pattern blocked."},
        {"id": "power", "action": "deny", "tool": "Bash",
         "command_regex": r"\b(shutdown|reboot|halt|poweroff)\b",
         "reason": "Power-state commands are blocked on the shared VM."},
        {"id": "force-push-protected", "action": "deny", "tool": "Bash",
         "command_regex": r"git\s+push\b.*(--force\b|--force-with-lease=?\s*$|-f\b).*\b(main|master|origin\s+main)\b|git\s+push\s+.*-f\s+\w+\s+(main|master)\b",
         "reason": "Force-pushing a protected branch is blocked."},
        # --- Cloud / cluster mutations: ask for confirmation ---
        {"id": "cloud-destroy", "action": "ask", "tool": "Bash",
         "command_regex": r"\b(oci|aws|gcloud|az)\b.*\b(delete|terminate|destroy|remove|rm)\b|terraform\s+destroy|kubectl\s+(delete|drain|cordon)\b|helm\s+(uninstall|delete)\b",
         "reason": "Cloud/cluster resource mutation — confirm before running."},
        {"id": "db-destructive", "action": "ask", "tool": "Bash",
         "command_regex": r"\b(DROP\s+(TABLE|DATABASE)|TRUNCATE\s+TABLE|DELETE\s+FROM)\b",
         "reason": "Destructive SQL — confirm before running."},
        {"id": "system-install", "action": "ask", "tool": "Bash",
         "command_regex": r"\bsudo\b.*\b(apt|apt-get|dnf|yum)\b\s+(install|remove|purge)|\bpip\d?\s+install\b.*\s-g\b|npm\s+install\s+-g\b",
         "reason": "System-wide install/removal — confirm before running."},
        # --- Secret access: ask ---
        {"id": "secret-read", "action": "ask", "tool": "*",
         "path_regex": r"(/\.ssh/id_|/\.ssh/.*_rsa$|\.pem$|\.key$|(^|/)\.env$|/keys\.txt$|/\.aws/credentials|/\.oci/.*\.pem)",
         "reason": "Access to a private key / credentials file — confirm."},
        # --- Writes outside allowed roots: ask ---
        {"id": "write-outside-roots", "action": "ask", "tool": "Write,Edit,MultiEdit,NotebookEdit",
         "path_outside_roots": True,
         "reason": "Writing outside your home / shared workspace / tmp — confirm."},
    ],
}

ACTION_RANK = {"allow": 0, "ask": 1, "deny": 2}


def load_policy(path: str = POLICY_FILE) -> Dict[str, Any]:
    p = Path(path)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "rules" in data:
                data.setdefault("allowed_write_roots", DEFAULT_POLICY["allowed_write_roots"])
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return DEFAULT_POLICY


def _expand_roots(roots: List[str], home: str) -> List[str]:
    out = []
    for r in roots:
        out.append(home if r == "~" else r.replace("~", home))
    return out


def _tool_matches(rule_tool: str, tool: str) -> bool:
    if rule_tool == "*":
        return True
    return tool in {t.strip() for t in rule_tool.split(",")}


def _extract_command(tool_input: Dict[str, Any]) -> str:
    return str(tool_input.get("command", "") or "")


def _extract_path(tool_input: Dict[str, Any]) -> str:
    return str(tool_input.get("file_path") or tool_input.get("path") or tool_input.get("notebook_path") or "")


def _is_outside_roots(path: str, roots: List[str]) -> bool:
    if not path:
        return False
    ap = os.path.normpath(path if os.path.isabs(path) else os.path.join(os.getcwd(), path))
    return not any(ap == r or ap.startswith(r.rstrip("/") + "/") for r in roots)


def decide(tool: str, tool_input: Dict[str, Any], ctx: Dict[str, Any],
           policy: Optional[Dict[str, Any]] = None) -> Tuple[str, str, str]:
    """Return (action, reason, rule_id). Default allow when no rule matches."""
    policy = policy or DEFAULT_POLICY
    home = ctx.get("home", os.path.expanduser("~"))
    roots = _expand_roots(policy.get("allowed_write_roots", []), home)
    command = _extract_command(tool_input)
    path = _extract_path(tool_input)
    # Some Bash commands embed paths too; scan the command for secret patterns.
    haystack_path = path or command

    for rule in policy.get("rules", []):
        if not _tool_matches(rule.get("tool", "*"), tool):
            continue
        if "command_regex" in rule:
            if not command or not re.search(rule["command_regex"], command, re.IGNORECASE):
                continue
        if "path_regex" in rule:
            if not haystack_path or not re.search(rule["path_regex"], haystack_path, re.IGNORECASE):
                continue
        if rule.get("path_outside_roots"):
            if not _is_outside_roots(path, roots):
                continue
        return rule.get("action", "allow"), rule.get("reason", ""), rule.get("id", "")
    return "allow", "", ""


def main(argv: Optional[List[str]] = None) -> int:
    """CLI for testing: echo a tool call as JSON on stdin, print the decision."""
    p = argparse.ArgumentParser(description="Evaluate a tool call against the guardrail policy.")
    p.add_argument("--tool", default="Bash")
    p.add_argument("--input", default="{}", help="tool_input as JSON")
    p.add_argument("--dump-policy", action="store_true", help="Print the default policy as JSON (for /etc/agent-os/policy.json)")
    args = p.parse_args(argv)

    if args.dump_policy:
        print(json.dumps(DEFAULT_POLICY, indent=2))
        return 0
    try:
        ti = json.loads(args.input)
    except json.JSONDecodeError:
        ti = {"command": args.input}
    action, reason, rid = decide(args.tool, ti, {"home": os.path.expanduser("~")}, load_policy())
    print(json.dumps({"action": action, "reason": reason, "rule": rid}))
    return 0 if action == "allow" else (1 if action == "ask" else 2)


if __name__ == "__main__":
    sys.exit(main())
