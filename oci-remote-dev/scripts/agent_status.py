#!/usr/bin/env python3
"""agent-status — aggregate every developer's live agent sessions into one board.

Runs as root on the VM (systemd timer), reads each developer's agentctl metadata
and live tmux state, joins recent LLM cost from the gateway, and writes a JSON
snapshot the landing dashboard renders as a live multi-agent board.

Standard-library only. The pure merge/parse helpers are unit-tested; the tmux and
HTTP calls are thin best-effort IO so a single failing developer never breaks the
whole board.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


def parse_meta_dir(meta_dir: Path) -> List[Dict[str, str]]:
    """Parse all agentctl *.env metadata files in a developer's meta directory."""
    sessions: List[Dict[str, str]] = []
    if not meta_dir.is_dir():
        return sessions
    for f in sorted(meta_dir.glob("*.env")):
        row: Dict[str, str] = {}
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                row[k.strip()] = v
        if row.get("name"):
            sessions.append(row)
    return sessions


def state_from_live(name: str, live: Dict[str, str]) -> str:
    """Resolve a session's state from a {sanitized_name: attached_flag} map."""
    key = _sanitize(name)
    if key not in live:
        return "dead"
    return "attached" if live[key] not in ("0", "", None) else "running"


def _sanitize(name: str) -> str:
    return "".join(c if (c.isalnum() or c in "_.-") else "_" for c in name)


def merge_board(
    per_user: Dict[str, List[Dict[str, str]]],
    live: Dict[str, Dict[str, str]],
    costs: Dict[str, float],
) -> Dict[str, Any]:
    """Build the board payload from metadata, live tmux state, and per-user cost.

    per_user: {username: [session-meta, ...]}
    live:     {username: {sanitized_session_name: attached_flag}}
    costs:    {username: cost_usd_24h}
    """
    developers = []
    totals = {"sessions": 0, "running": 0, "cost_usd": 0.0}
    for user in sorted(per_user):
        rows = []
        for meta in per_user[user]:
            st = state_from_live(meta["name"], live.get(user, {}))
            rows.append({
                "name": meta.get("name", "?"),
                "agent": meta.get("agent", "?"),
                "project": meta.get("project", "?"),
                "dir": meta.get("dir", ""),
                "started_at": meta.get("started_at", ""),
                "state": st,
            })
            totals["sessions"] += 1
            if st in ("running", "attached"):
                totals["running"] += 1
        cost = float(costs.get(user, 0.0) or 0.0)
        totals["cost_usd"] += cost
        developers.append({"name": user, "sessions": rows, "cost_usd_24h": round(cost, 4)})
    return {"developers": developers, "totals": totals}


# ── Thin best-effort IO (not unit-tested) ────────────────────────────────────

def live_sessions_for(user: str, socket: str) -> Dict[str, str]:
    """Query a user's tmux server for live sessions. Best-effort; {} on any error."""
    try:
        out = subprocess.run(
            ["runuser", "-u", user, "--", "tmux", "-S", socket,
             "list-sessions", "-F", "#{session_name} #{session_attached}"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    live: Dict[str, str] = {}
    for line in out.stdout.splitlines():
        parts = line.split()
        if parts:
            live[parts[0]] = parts[1] if len(parts) > 1 else "0"
    return live


def fetch_costs(gateway: str, hours: int = 24) -> Dict[str, float]:
    """Per-tenant cost from /api/team-usage. Best-effort; {} on any error."""
    try:
        url = gateway.rstrip("/") + f"/api/team-usage?hours={hours}"
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return {}
    costs: Dict[str, float] = {}
    for row in data.get("by_user", []) or []:
        bucket = row.get("bucket")
        if bucket:
            costs[bucket] = float(row.get("cost_usd") or 0.0)
    return costs


def discover_developers(home_root: Path) -> List[str]:
    """Developers are home dirs that contain an .agentctl directory."""
    if not home_root.is_dir():
        return []
    return sorted(p.name for p in home_root.iterdir() if (p / ".agentctl").is_dir())


def build(developers: List[str], home_root: Path, gateway: Optional[str]) -> Dict[str, Any]:
    per_user, live = {}, {}
    for user in developers:
        agentctl = home_root / user / ".agentctl"
        per_user[user] = parse_meta_dir(agentctl / "meta")
        live[user] = live_sessions_for(user, str(agentctl / "tmux.sock"))
    costs = fetch_costs(gateway) if gateway else {}
    return build_with(per_user, live, costs)


def build_with(per_user, live, costs) -> Dict[str, Any]:
    return merge_board(per_user, live, costs)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Aggregate live agent sessions into a board JSON.")
    p.add_argument("--developers", default="", help="Comma-separated usernames (default: scan --home-root)")
    p.add_argument("--home-root", default="/home")
    p.add_argument("--gateway", default="http://10.200.200.1:8080", help="Gateway for cost (empty to skip)")
    p.add_argument("--out", default="", help="Write JSON here (default: stdout)")
    args = p.parse_args(argv)

    home_root = Path(args.home_root)
    devs = [d for d in args.developers.split(",") if d] or discover_developers(home_root)
    board = build(devs, home_root, args.gateway or None)
    out = json.dumps(board, indent=2)
    if args.out:
        Path(args.out).write_text(out + "\n", encoding="utf-8")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
