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
import datetime
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

NOTIFY_WINDOW_SEC = 600  # a notification "rings" for 10 minutes, then fades


def _parse_iso(ts: str) -> float:
    try:
        return (
            datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
            .replace(tzinfo=datetime.timezone.utc)
            .timestamp()
        )
    except (ValueError, TypeError):
        return 0.0


def recent_notifications(lines: List[str], now_epoch: float, window_sec: int = NOTIFY_WINDOW_SEC) -> List[Dict[str, Any]]:
    """Parse JSONL notification lines, keeping only events within the time window."""
    out: List[Dict[str, Any]] = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            ev = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if now_epoch - _parse_iso(ev.get("ts", "")) <= window_sec:
            out.append(ev)
    return out


GUARDRAIL_WINDOW_SEC = 3600  # show the last hour of guardrail decisions on the board


def recent_guardrail(lines: List[str], now_epoch: float, window_sec: int = GUARDRAIL_WINDOW_SEC) -> List[Dict[str, Any]]:
    """Parse guardrail.jsonl, keeping recent deny/ask decisions (allow is noise)."""
    out: List[Dict[str, Any]] = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            ev = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if ev.get("action") in ("deny", "ask") and now_epoch - _parse_iso(ev.get("ts", "")) <= window_sec:
            out.append(ev)
    return out


def summarize_guardrail(events_by_user: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Flatten recent guardrail events into a board section + counts."""
    flat: List[Dict[str, Any]] = []
    denied = asked = 0
    for user, evs in events_by_user.items():
        for e in evs:
            flat.append({**e, "user": user})
            if e.get("action") == "deny":
                denied += 1
            elif e.get("action") == "ask":
                asked += 1
    flat.sort(key=lambda e: e.get("ts", ""), reverse=True)
    return {"recent": flat[:12], "denied": denied, "asked": asked}


def apply_notifications(developers: List[Dict[str, Any]], notifs_by_user: Dict[str, List[Dict[str, Any]]]) -> int:
    """Annotate sessions/developers with needs_input from recent notifications.

    Returns the total count of developers currently ringing.
    """
    ringing_total = 0
    for dev in developers:
        evs = notifs_by_user.get(dev["name"], [])
        ringing_sessions = {e.get("session") for e in evs}
        for s in dev.get("sessions", []):
            s["needs_input"] = s["name"] in ringing_sessions
        dev["notifications"] = evs[-5:]
        dev["needs_input"] = any(s.get("needs_input") for s in dev.get("sessions", [])) or bool(evs)
        if dev["needs_input"]:
            ringing_total += 1
    return ringing_total


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


def parse_budgets(spec: str) -> Dict[str, float]:
    """Parse 'user=usd,user=usd' into a map (mirror of usage_report.parse_budgets;
    replicated because each CLI installs standalone to /usr/local/bin)."""
    out: Dict[str, float] = {}
    for entry in (spec or "").split(","):
        entry = entry.strip()
        if not entry or "=" not in entry:
            continue
        user, _, cap = entry.partition("=")
        try:
            val = float(cap.strip())
        except ValueError:
            continue
        if user.strip() and val >= 0:
            out[user.strip()] = val
    return out


def evaluate_budget_status(developers: List[Dict[str, Any]], spec: str) -> int:
    """Annotate each developer with budget {cap, spent, over}; return over-budget count."""
    budgets = parse_budgets(spec)
    over = 0
    for dev in developers:
        cap = budgets.get(dev["name"])
        if cap is None:
            dev["budget"] = None
            continue
        spent = float(dev.get("cost_usd_24h", 0.0) or 0.0)
        is_over = spent > cap
        dev["budget"] = {"cap": cap, "spent": round(spent, 4), "over": is_over}
        if is_over:
            over += 1
    return over


def fetch_gateway_health(gateway: Optional[str], timeout: float = 4.0) -> Dict[str, Any]:
    """Best-effort gateway /health probe for the dashboard's health pill."""
    if not gateway:
        return {"up": False, "url": None}
    url = gateway.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return {"up": resp.status == 200, "url": gateway}
    except (urllib.error.URLError, OSError):
        return {"up": False, "url": gateway}


def discover_developers(home_root: Path) -> List[str]:
    """Developers are home dirs that contain an .agentctl directory."""
    if not home_root.is_dir():
        return []
    return sorted(p.name for p in home_root.iterdir() if (p / ".agentctl").is_dir())


def _read_feed(home_root: Path, developers: List[str], now_epoch: float, filename: str, parser) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for user in developers:
        feed = home_root / user / ".agentctl" / filename
        if feed.exists():
            out[user] = parser(feed.read_text(encoding="utf-8", errors="replace").splitlines(), now_epoch)
        else:
            out[user] = []
    return out


def build(developers: List[str], home_root: Path, gateway: Optional[str]) -> Dict[str, Any]:
    per_user, live = {}, {}
    for user in developers:
        agentctl = home_root / user / ".agentctl"
        per_user[user] = parse_meta_dir(agentctl / "meta")
        live[user] = live_sessions_for(user, str(agentctl / "tmux.sock"))
    costs = fetch_costs(gateway) if gateway else {}
    now_epoch = datetime.datetime.now(datetime.timezone.utc).timestamp()
    notifs = _read_feed(home_root, developers, now_epoch, "notifications.jsonl", recent_notifications)
    guardrail = _read_feed(home_root, developers, now_epoch, "guardrail.jsonl", recent_guardrail)
    health = fetch_gateway_health(gateway)
    budgets_spec = _load_budgets_spec()
    return build_with(per_user, live, costs, notifs, guardrail, health, budgets_spec)


def _load_budgets_spec() -> str:
    """Budgets set live by the control-plane API (/etc/agent-os/budgets) win over the
    deploy-time MULTILLM_USER_BUDGETS env, so POST /budgets takes effect on next poll."""
    f = Path("/etc/agent-os/budgets")
    try:
        if f.exists():
            txt = f.read_text(encoding="utf-8").strip()
            if txt:
                return txt
    except OSError:
        pass
    return os.environ.get("MULTILLM_USER_BUDGETS", "")


def build_with(per_user, live, costs, notifs=None, guardrail=None, health=None, budgets_spec="") -> Dict[str, Any]:
    board = merge_board(per_user, live, costs)
    ringing = apply_notifications(board["developers"], notifs or {})
    board["totals"]["needs_input"] = ringing
    board["guardrail"] = summarize_guardrail(guardrail or {})
    board["totals"]["blocked"] = board["guardrail"]["denied"]
    board["gateway"] = health or {"up": False, "url": None}
    board["totals"]["over_budget"] = evaluate_budget_status(board["developers"], budgets_spec)
    return board


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
