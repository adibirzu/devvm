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
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

POLICY_FILE = os.environ.get("GUARDRAIL_POLICY", "/etc/agent-os/policy.json")

# --- Content-aware secret detection (independent of write-target path) ---
# A path being "safe" (home/shared/tmp) says nothing about whether the bytes
# written there are a secret. These patterns classify write *content* into a
# small set of pattern classes; the value itself is never surfaced in a
# decision reason or audit entry — only the class name and the target path.
_PLACEHOLDER_VALUE_RE = re.compile(
    r"^(<.*>|\.\.\.|changeme|change_me|change-me|dummy|example|test|sample|"
    r"todo|fixme|replace[_-]?me|your[_-].*|xxx+|placeholder|insert[_-].*|"
    r"redacted|fake|none|null|n/?a|\$\{.*\}|\$\w+)$",
    re.IGNORECASE,
)

_SECRET_KEY_NAME = (
    r"[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE_KEY)[A-Z0-9_]*"
)

#  Anchored on a line/word boundary rather than a whole line, so this matches
#  both real dotenv-file lines (KEY=VALUE) and a KEY=VALUE assignment embedded
#  in a larger string (e.g. `echo "KEY=VALUE" > file`, JSON, YAML).
_DOTENV_ASSIGNMENT_RE = re.compile(
    rf"(?:^|[\s'\"])(?:export[ \t]+)?({_SECRET_KEY_NAME})[ \t]*=[ \t]*['\"]?([^\s'\";]+)",
    re.IGNORECASE | re.MULTILINE,
)

_JSON_SECRET_FIELD_RE = re.compile(
    r'"(private_key|client_secret)"\s*:\s*"([^"]*)"', re.IGNORECASE
)

_PEM_BLOCK_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")

# A write via Bash: redirection (`>`, `>>`), a heredoc (`<<EOF`, `<<-EOF`, `<<'EOF'`),
# or a pipe into `tee`.
_BASH_WRITE_INDICATOR_RE = re.compile(r"(?:>{1,2}(?!>)|<<[-~]?|\btee\b)")
_BASH_REDIRECT_TARGET_RE = re.compile(
    r"(?:>{1,2}(?!>)|\btee\b(?:\s+-a)?)\s+(['\"]?)([^\s'\"|;&]+)\1"
)

HIGH_ENTROPY_MIN_LEN = 20
HIGH_ENTROPY_MIN_BITS = 3.5


def _shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


def _is_placeholder_value(value: str) -> bool:
    v = value.strip().strip("'\"")
    if not v:
        return True
    return bool(_PLACEHOLDER_VALUE_RE.match(v))


def classify_secret_content(text: str, target_path: str = "") -> Optional[str]:
    """Classify write `text` into a secret pattern class, or None if it's clean.

    Never returns the matched value — only the pattern class name — so callers
    can log/report the classification without leaking the secret itself.
    """
    if not text:
        return None
    if target_path and os.path.basename(target_path) == ".env.example":
        return None
    if _PEM_BLOCK_RE.search(text):
        return "pem-block"
    for m in _JSON_SECRET_FIELD_RE.finditer(text):
        if not _is_placeholder_value(m.group(2)):
            return "json-secret-field"
    for m in _DOTENV_ASSIGNMENT_RE.finditer(text):
        value = m.group(2)
        if _is_placeholder_value(value):
            continue
        if (
            len(value) >= HIGH_ENTROPY_MIN_LEN
            and _shannon_entropy(value) >= HIGH_ENTROPY_MIN_BITS
        ):
            return "high-entropy-key-value"
        return "dotenv-assignment"
    return None


def _extract_write_content(tool: str, tool_input: Dict[str, Any]) -> Tuple[str, str]:
    """Return (content_being_written, target_path) for a tool call, or ("", "")."""
    if tool == "Write":
        return str(tool_input.get("content", "") or ""), _extract_path(tool_input)
    if tool == "Edit":
        return str(tool_input.get("new_string", "") or ""), _extract_path(tool_input)
    if tool == "MultiEdit":
        edits = tool_input.get("edits", []) or []
        content = "\n".join(
            str(e.get("new_string", "") or "") for e in edits if isinstance(e, dict)
        )
        return content, _extract_path(tool_input)
    if tool == "NotebookEdit":
        return str(tool_input.get("new_source", "") or ""), _extract_path(tool_input)
    if tool == "Bash":
        command = _extract_command(tool_input)
        if not command or not _BASH_WRITE_INDICATOR_RE.search(command):
            return "", ""
        target = ""
        for m in _BASH_REDIRECT_TARGET_RE.finditer(command):
            target = m.group(2)  # last redirection target wins (e.g. a pipe to tee)
        return command, target
    return "", ""


# Default policy — conservative but practical. Ordered; first match wins.
# action: deny (block), ask (require user confirmation), allow.
DEFAULT_POLICY: Dict[str, Any] = {
    "allowed_write_roots": ["~", "/opt/shared-dev", "/tmp"],
    # secret_writes: "deny" | "ask" — verdict when write content/target looks
    # secret-shaped (see classify_secret_content). Default "ask" preserves the
    # existing behavior of confirming rather than silently blocking.
    "secret_writes": "ask",
    "rules": [
        # --- Catastrophic shell: hard deny ---
        {
            "id": "rm-rf-root",
            "action": "deny",
            "tool": "Bash",
            "command_regex": r"rm\s+-[a-z]*r[a-z]*f?\s+(/|~|\$HOME|/\*|--no-preserve-root)",
            "reason": "Recursive force-delete of a root/home path is blocked.",
        },
        {
            "id": "disk-destroyer",
            "action": "deny",
            "tool": "Bash",
            "command_regex": r"\b(mkfs|fdisk|wipefs)\b|dd\s+if=\S+\s+of=/dev/|>\s*/dev/sd",
            "reason": "Direct disk/partition writes are blocked.",
        },
        {
            "id": "fork-bomb",
            "action": "deny",
            "tool": "Bash",
            "command_regex": r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:",
            "reason": "Fork bomb pattern blocked.",
        },
        {
            "id": "power",
            "action": "deny",
            "tool": "Bash",
            "command_regex": r"\b(shutdown|reboot|halt|poweroff)\b",
            "reason": "Power-state commands are blocked on the shared VM.",
        },
        {
            "id": "force-push-protected",
            "action": "deny",
            "tool": "Bash",
            "command_regex": r"git\s+push\b.*(--force\b|--force-with-lease=?\s*$|-f\b).*\b(main|master|origin\s+main)\b|git\s+push\s+.*-f\s+\w+\s+(main|master)\b",
            "reason": "Force-pushing a protected branch is blocked.",
        },
        # --- Cloud / cluster mutations: ask for confirmation ---
        {
            "id": "cloud-destroy",
            "action": "ask",
            "tool": "Bash",
            "command_regex": r"\b(oci|aws|gcloud|az)\b.*\b(delete|terminate|destroy|remove|rm)\b|terraform\s+destroy|kubectl\s+(delete|drain|cordon)\b|helm\s+(uninstall|delete)\b",
            "reason": "Cloud/cluster resource mutation — confirm before running.",
        },
        {
            "id": "db-destructive",
            "action": "ask",
            "tool": "Bash",
            "command_regex": r"\b(DROP\s+(TABLE|DATABASE)|TRUNCATE\s+TABLE|DELETE\s+FROM)\b",
            "reason": "Destructive SQL — confirm before running.",
        },
        {
            "id": "system-install",
            "action": "ask",
            "tool": "Bash",
            "command_regex": r"\bsudo\b.*\b(apt|apt-get|dnf|yum)\b\s+(install|remove|purge)|\bpip\d?\s+install\b.*\s-g\b|npm\s+install\s+-g\b",
            "reason": "System-wide install/removal — confirm before running.",
        },
        # --- Secret-shaped write content: verdict from the secret_writes knob ---
        # Path-based rules (below/secret-read) treat home/shared/tmp — and even a
        # path that merely isn't named `.env` — as safe by path alone; this rule
        # catches secret VALUES landing anywhere, including those roots, and takes
        # precedence over secret-read so a write gets the more specific classification.
        {
            "id": "secret-write-detected",
            "tool": "Write,Edit,MultiEdit,NotebookEdit,Bash",
            "secret_write_check": True,
            "reason": (
                "Write content looks secret-shaped ({pattern_class}) — refusing to "
                "write a plaintext secret. Tune with the 'secret_writes' policy knob."
            ),
        },
        # --- Secret access: ask ---
        {
            "id": "secret-read",
            "action": "ask",
            "tool": "*",
            "path_regex": r"(/\.ssh/id_|/\.ssh/.*_rsa$|\.pem$|\.key$|(^|/)\.env$|/keys\.txt$|/\.aws/credentials|/\.oci/.*\.pem)",
            "reason": "Access to a private key / credentials file — confirm.",
        },
        # --- Writes outside allowed roots: ask ---
        {
            "id": "write-outside-roots",
            "action": "ask",
            "tool": "Write,Edit,MultiEdit,NotebookEdit",
            "path_outside_roots": True,
            "reason": "Writing outside your home / shared workspace / tmp — confirm.",
        },
    ],
}

ACTION_RANK = {"allow": 0, "ask": 1, "deny": 2}


def load_policy(path: str = POLICY_FILE) -> Dict[str, Any]:
    p = Path(path)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "rules" in data:
                data.setdefault(
                    "allowed_write_roots", DEFAULT_POLICY["allowed_write_roots"]
                )
                data.setdefault("secret_writes", DEFAULT_POLICY["secret_writes"])
                existing_ids = {
                    rule.get("id")
                    for rule in data["rules"]
                    if isinstance(rule, dict)
                }
                for rule in DEFAULT_POLICY["rules"]:
                    if rule.get("id") not in existing_ids:
                        data["rules"].append(rule)
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
    return str(
        tool_input.get("file_path")
        or tool_input.get("path")
        or tool_input.get("notebook_path")
        or ""
    )


def _is_outside_roots(path: str, roots: List[str]) -> bool:
    if not path:
        return False
    ap = os.path.normpath(
        path if os.path.isabs(path) else os.path.join(os.getcwd(), path)
    )
    return not any(ap == r or ap.startswith(r.rstrip("/") + "/") for r in roots)


def decide(
    tool: str,
    tool_input: Dict[str, Any],
    ctx: Dict[str, Any],
    policy: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str, str]:
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
        if rule.get("secret_write_check"):
            content, target = _extract_write_content(tool, tool_input)
            pattern_class = classify_secret_content(content, target)
            if not pattern_class:
                continue
            action = policy.get("secret_writes", "ask")
            if action not in ("ask", "deny"):
                continue
            reason = rule.get("reason", "Secret-shaped write content detected.").format(
                pattern_class=pattern_class
            )
            return (
                action,
                reason,
                f"{rule.get('id', 'secret-write-detected')}:{pattern_class}:{target}",
            )
        if "command_regex" in rule:
            if not command or not re.search(
                rule["command_regex"], command, re.IGNORECASE
            ):
                continue
        if "path_regex" in rule:
            if not haystack_path or not re.search(
                rule["path_regex"], haystack_path, re.IGNORECASE
            ):
                continue
        if rule.get("path_outside_roots"):
            if not _is_outside_roots(path, roots):
                continue
        return rule.get("action", "allow"), rule.get("reason", ""), rule.get("id", "")
    return "allow", "", ""


def main(argv: Optional[List[str]] = None) -> int:
    """CLI for testing: echo a tool call as JSON on stdin, print the decision."""
    p = argparse.ArgumentParser(
        description="Evaluate a tool call against the guardrail policy."
    )
    p.add_argument("--tool", default="Bash")
    p.add_argument("--input", default="{}", help="tool_input as JSON")
    p.add_argument(
        "--dump-policy",
        action="store_true",
        help="Print the default policy as JSON (for /etc/agent-os/policy.json)",
    )
    p.add_argument(
        "--log",
        action="store_true",
        help="Show recent guardrail decisions (audit trail)",
    )
    p.add_argument("-n", type=int, default=25, help="Number of audit lines with --log")
    args = p.parse_args(argv)

    if args.dump_policy:
        print(json.dumps(DEFAULT_POLICY, indent=2))
        return 0

    if args.log:
        feed = (
            Path(os.environ.get("AGENTCTL_HOME", str(Path.home() / ".agentctl")))
            / "guardrail.jsonl"
        )
        if not feed.exists():
            print("(no guardrail decisions logged yet)")
            return 0
        lines = feed.read_text(encoding="utf-8", errors="replace").splitlines()[
            -args.n :
        ]
        for ln in lines:
            try:
                e = json.loads(ln)
            except json.JSONDecodeError:
                continue
            mark = {"deny": "✗ DENY", "ask": "? ASK", "allow": "· allow"}.get(
                e.get("action"), e.get("action")
            )
            print(
                f"{e.get('ts','')}  {mark:<8} [{e.get('rule','')}] {e.get('summary','')}"
            )
        return 0
    try:
        ti = json.loads(args.input)
    except json.JSONDecodeError:
        ti = {"command": args.input}
    action, reason, rid = decide(
        args.tool, ti, {"home": os.path.expanduser("~")}, load_policy()
    )
    print(json.dumps({"action": action, "reason": reason, "rule": rid}))
    return 0 if action == "allow" else (1 if action == "ask" else 2)


if __name__ == "__main__":
    sys.exit(main())
