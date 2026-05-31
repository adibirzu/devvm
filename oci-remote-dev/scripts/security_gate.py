#!/usr/bin/env python3
"""
Security Gate Scanner
====================
Enforces compliance with security rules by scanning code changes or repository
files for sensitive patterns, such as tenancy OCIDs, restricted public IP ranges,
tenancy namespaces, API keys, or private key materials.

Designed to be executed as a local git pre-commit hook or in a CI/CD workflow.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

# Tenancy-specific namespaces are NEVER hardcoded here — that would publish the very
# secrets this scanner guards. They are loaded at runtime from the environment
# (SECURITY_GATE_NAMESPACES, comma-separated) or a git-ignored file
# (configs/restricted-namespaces.txt, one per line). Absent both, the namespace
# check is skipped; the OCID/IP/key checks still run.
NAMESPACES_FILE = (
    Path(__file__).resolve().parent.parent / "configs" / "restricted-namespaces.txt"
)


def load_restricted_namespaces() -> List[str]:
    raw = os.environ.get("SECURITY_GATE_NAMESPACES", "").strip()
    if not raw and NAMESPACES_FILE.exists():
        raw = ",".join(
            ln.strip()
            for ln in NAMESPACES_FILE.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")
        )
    return [n.strip() for n in raw.split(",") if n.strip()]


def build_patterns(
    namespaces: Optional[List[str]] = None,
) -> List[Tuple["re.Pattern[str]", str]]:
    """Build the detection pattern list. Tenancy namespaces are injected, never literal."""
    if namespaces is None:
        namespaces = load_restricted_namespaces()
    patterns: List[Tuple["re.Pattern[str]", str]] = [
        # 1. OCID Patterns (tenancy, compartment, cluster, vnic, user, etc.)
        (
            re.compile(
                r"ocid1\.(tenancy|compartment|instance|cluster|networksecuritygroup|loadbalancer|subnet|vnic|bootvolume|loganalytics[a-z]+|user)\.oc1\.[a-z]*\.[a-z0-9]+"
            ),
            "Restricted OCI Resource OCID (leak danger)",
        ),
        # 2. Restricted Public OCI/CAP IP Ranges (Oracle-published /16 ranges)
        (
            re.compile(
                r"\b(130\.61|161\.153|144\.24|129\.153|141\.147|82\.77|109\.166)\.[0-9]+\.[0-9]+\b"
            ),
            "Restricted Infrastructure Public IP range",
        ),
    ]
    # 3. Known Restricted Tenancy Namespaces (loaded, never hardcoded)
    if namespaces:
        joined = "|".join(re.escape(n) for n in namespaces)
        patterns.append(
            (re.compile(r"\b(" + joined + r")\b"), "Restricted Tenancy Namespace")
        )
    patterns += [
        # 4. Common API Secrets
        (re.compile(r"sk-[a-zA-Z0-9]{48}"), "Potential OpenAI API Key"),
        (
            re.compile(r"sk-ant-sid01-[a-zA-Z0-9\-_]{36,96}"),
            "Potential Anthropic API Key",
        ),
        # 5. Private Keys
        (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "Private Key Block"),
    ]
    return patterns


PATTERNS = build_patterns()

# Files/extensions that are allowed to contain placeholders or ignore
IGNORE_PATTERNS = [
    re.compile(r"\.git/"),
    re.compile(r"\.env\.example$"),
    re.compile(r"\.env\.local$"),
    re.compile(r"\.local\.[a-z0-9]+$"),  # *.local.* — local-only, git-ignored
    re.compile(r"README\.md$"),
    re.compile(r"security_gate\.py$"),
    re.compile(r"keys\.txt$"),
    re.compile(r"configs/"),
    re.compile(r"\b8(\.pub)?$"),
    re.compile(r"\.pyc$"),
    re.compile(r"__pycache__/"),
    re.compile(r"tests/"),
]


def is_ignored(path: str) -> bool:
    for pat in IGNORE_PATTERNS:
        if pat.search(path):
            return True
    return False


def scan_text(
    text: str, source_label: str, patterns=None
) -> List[Tuple[int, str, str]]:
    """Scan a text content for security violations."""
    patterns = patterns if patterns is not None else PATTERNS
    violations = []
    lines = text.splitlines()
    for idx, line in enumerate(lines, 1):
        for pattern, label in patterns:
            match = pattern.search(line)
            if match:
                violations.append((idx, label, match.group(0)))
    return violations


def scan_staged_changes() -> int:
    """Scan git staged changes (git diff --cached) for violations."""
    print("Security Gate: Scanning git staged changes (diff)...")
    try:
        diff_output = subprocess.check_output(
            ["git", "diff", "--cached", "-U0"], stderr=subprocess.PIPE, text=True
        )
    except subprocess.CalledProcessError as exc:
        print(f"Error checking git diff: {exc.stderr}")
        return 0

    violations_found = 0
    current_file = ""

    for line in diff_output.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            if is_ignored(current_file):
                continue

            added_text = line[1:]
            for pattern, label in PATTERNS:
                match = pattern.search(added_text)
                if match:
                    print(
                        f"\033[0;31m[SECURITY VIOLATION]\033[0m in \033[1m{current_file}\033[0m: "
                        f"{label} -> '{match.group(0)}'"
                    )
                    violations_found += 1

    return violations_found


def scan_full_repo(dir_path: Path) -> int:
    """Scan the entire repository recursively."""
    print(f"Security Gate: Scanning all repository files in {dir_path}...")
    violations_found = 0

    for p in dir_path.rglob("*"):
        if p.is_dir() or not p.exists() or is_ignored(str(p)):
            continue

        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        violations = scan_text(content, str(p))
        if violations:
            for line_no, label, matched_text in violations:
                print(
                    f"\033[0;31m[SECURITY VIOLATION]\033[0m in \033[1m{p.relative_to(dir_path)}\033[0m "
                    f"at line {line_no}: {label} -> '{matched_text}'"
                )
                violations_found += 1

    return violations_found


def main() -> int:
    parser = argparse.ArgumentParser(description="Security compliance check tool")
    parser.add_argument(
        "--mode",
        choices=["staged", "full"],
        default="staged",
        help="Check staged changes (pre-commit) or full repository scan",
    )
    args = parser.parse_args()

    project_dir = Path(__file__).resolve().parent.parent

    if args.mode == "staged":
        violations = scan_staged_changes()
    else:
        violations = scan_full_repo(project_dir)

    if violations > 0:
        print(
            f"\n\033[0;31mABORT: Security gate failed. {violations} violation(s) detected!\033[0m"
        )
        print(
            "Please scrub all sensitive data (OCIDs, public IPs, secret tokens) before committing."
        )
        return 1

    print("\n\033[0;32mSecurity gate passed! No violations found.\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
