#!/usr/bin/env python3
"""Claude Code PreToolUse hook → guardrail policy decision.

Claude Code runs this BEFORE executing any tool, passing a JSON payload on stdin
({tool_name, tool_input, cwd, ...}). We evaluate it against the guardrail policy
and emit Claude Code's permission decision:
  * deny  -> block the call;
  * ask   -> force a confirmation prompt (the user sees it on attach; the
             notification ring also fires so they know to look);
  * allow -> stay silent and let Claude's normal permission flow proceed (the
             guardrail only intervenes on risky calls; it never loosens anything).

Every decision is appended to an audit log (~/.agentctl/guardrail.jsonl).
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

# Resolve the policy engine from the install lib dir (VM) or the sibling script (dev).
for cand in (
    os.environ.get("GUARDRAIL_LIB"),
    "/usr/local/lib/agent-os",
    str(Path(__file__).resolve().parent),
):
    if cand and (Path(cand) / "guardrail.py").exists():
        sys.path.insert(0, cand)
        break
try:
    from guardrail import decide, load_policy
except ImportError:
    # Fail OPEN on a broken install would be unsafe; fail CLOSED-silent instead:
    # emit nothing (allow) so we never wedge the agent, but log to stderr.
    print("guardrail: policy engine not found; allowing", file=sys.stderr)
    sys.exit(0)


def _summary(tool: str, tool_input: dict) -> str:
    if tool == "Bash":
        return ("$ " + str(tool_input.get("command", "")))[:160]
    p = tool_input.get("file_path") or tool_input.get("path") or ""
    return f"{tool} {p}"[:160]


def _audit(entry: dict) -> None:
    try:
        feed = (
            Path(os.environ.get("AGENTCTL_HOME", str(Path.home() / ".agentctl")))
            / "guardrail.jsonl"
        )
        feed.parent.mkdir(parents=True, exist_ok=True)
        with feed.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # nothing to evaluate
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}
    ctx = {"home": os.path.expanduser("~"), "cwd": payload.get("cwd", "")}

    action, reason, rule_id = decide(tool, tool_input, ctx, load_policy())

    _audit(
        {
            "ts": datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "tool": tool,
            "action": action,
            "rule": rule_id,
            "reason": reason,
            "summary": _summary(tool, tool_input),
            "session": os.environ.get("AGENTCTL_SESSION", ""),
        }
    )

    if action in ("deny", "ask"):
        # Ring the board so the user knows an action is blocked / awaiting confirm.
        try:
            subprocess.run(
                ["agent-notify", f"guardrail {action}: {reason}"],
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        decision = "deny" if action == "deny" else "ask"
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": decision,
                        "permissionDecisionReason": f"[guardrail:{rule_id}] {reason}",
                    }
                }
            )
        )
    # allow → silent (exit 0): do not override Claude's normal permission flow.
    return 0


if __name__ == "__main__":
    sys.exit(main())
