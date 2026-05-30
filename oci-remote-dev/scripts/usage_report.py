#!/usr/bin/env python3
"""usage-report — aggregate LLM usage rollup from the shared MultiLLM gateway.

Queries the gateway's ``/usage`` endpoint and prints a per-model and per-project
token/cost rollup. Standard-library only (no third-party deps) so it can be
dropped onto the VM as ``/usr/local/bin/usage-report`` and run by any developer
over the VPN.

Attribution note: the shared gateway resolves its project tag once at startup
(``MULTILLM_PROJECT``), so the per-project breakdown is only as granular as the
gateway's configuration. True per-developer attribution requires per-user
gateways or a per-request project header — see ROADMAP-v2.md, Phase 1.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List

# The shared gateway binds to wg_server_ip (e.g. 10.200.200.1), so localhost is
# not a safe default on the VM. Honor MULTILLM_GATEWAY (set in the collector env)
# and fall back to localhost for ad-hoc local use.
DEFAULT_GATEWAY = os.environ.get("MULTILLM_GATEWAY", "http://localhost:8080")


def _fetch_json(url: str, timeout: float) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (trusted VPN URL)
        return json.loads(resp.read().decode("utf-8"))


def fetch_usage(base_url: str, hours: int, project: str = "", timeout: float = 5.0) -> Dict[str, Any]:
    """Fetch the aggregate usage summary (by model + by project) from the gateway."""
    query = f"/usage?hours={int(hours)}"
    if project:
        query += f"&project={urllib.parse.quote(project)}"
    return _fetch_json(base_url.rstrip("/") + query, timeout)


def fetch_team_usage(base_url: str, hours: int, tenant: str = "", timeout: float = 5.0) -> Dict[str, Any]:
    """Fetch the per-developer (tenant) team-usage rollup from the gateway."""
    query = f"/api/team-usage?hours={int(hours)}"
    if tenant:
        query += f"&tenant={urllib.parse.quote(tenant)}"
    return _fetch_json(base_url.rstrip("/") + query, timeout)


def _num(value: Any) -> float:
    """Coerce a possibly-None SQL aggregate to a number."""
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _fmt_int(value: Any) -> str:
    return f"{int(_num(value)):,}"


def _fmt_cost(value: Any) -> str:
    return f"${_num(value):,.4f}"


def summarize_totals(by_model: List[Dict[str, Any]]) -> Dict[str, float]:
    """Roll a by-model list up into aggregate totals."""
    totals = {"requests": 0.0, "input": 0.0, "output": 0.0, "cost": 0.0, "errors": 0.0}
    for row in by_model:
        totals["requests"] += _num(row.get("request_count"))
        totals["input"] += _num(row.get("total_input"))
        totals["output"] += _num(row.get("total_output"))
        totals["cost"] += _num(row.get("total_cost_usd"))
        totals["errors"] += _num(row.get("error_count"))
    return totals


def format_model_table(by_model: List[Dict[str, Any]]) -> str:
    if not by_model:
        return "  (no model usage in window)"
    header = f"  {'MODEL':<28} {'BACKEND':<12} {'REQ':>7} {'IN':>12} {'OUT':>12} {'COST':>12}"
    lines = [header, "  " + "-" * (len(header) - 2)]
    for row in by_model:
        lines.append(
            f"  {str(row.get('model_alias', '?'))[:28]:<28} "
            f"{str(row.get('backend', '?'))[:12]:<12} "
            f"{_fmt_int(row.get('request_count')):>7} "
            f"{_fmt_int(row.get('total_input')):>12} "
            f"{_fmt_int(row.get('total_output')):>12} "
            f"{_fmt_cost(row.get('total_cost_usd')):>12}"
        )
    return "\n".join(lines)


def format_project_table(by_project: List[Dict[str, Any]]) -> str:
    if not by_project:
        return "  (no project usage in window)"
    header = f"  {'PROJECT':<28} {'REQ':>7} {'IN':>12} {'OUT':>12} {'COST':>12}"
    lines = [header, "  " + "-" * (len(header) - 2)]
    for row in by_project:
        lines.append(
            f"  {str(row.get('project') or '(untagged)')[:28]:<28} "
            f"{_fmt_int(row.get('requests')):>7} "
            f"{_fmt_int(row.get('input_tokens')):>12} "
            f"{_fmt_int(row.get('output_tokens')):>12} "
            f"{_fmt_cost(row.get('cost_usd')):>12}"
        )
    return "\n".join(lines)


def format_user_table(by_user: List[Dict[str, Any]]) -> str:
    if not by_user:
        return "  (no developer usage in window)"
    header = f"  {'DEVELOPER':<20} {'REQ':>7} {'IN':>12} {'OUT':>12} {'CACHE':>12} {'COST':>12}"
    lines = [header, "  " + "-" * (len(header) - 2)]
    for row in by_user:
        lines.append(
            f"  {str(row.get('bucket') or '(unknown)')[:20]:<20} "
            f"{_fmt_int(row.get('requests')):>7} "
            f"{_fmt_int(row.get('input_tokens')):>12} "
            f"{_fmt_int(row.get('output_tokens')):>12} "
            f"{_fmt_int(row.get('cache_tokens')):>12} "
            f"{_fmt_cost(row.get('cost_usd')):>12}"
        )
    return "\n".join(lines)


def render_team_report(data: Dict[str, Any], hours: int) -> str:
    by_user = data.get("by_user") or []
    totals = data.get("totals") or {}
    return "\n".join([
        f"MultiLLM team usage — last {hours}h",
        "=" * 60,
        "By developer (tenant):",
        format_user_table(by_user),
        "",
        "Totals:",
        f"  developers={_fmt_int(totals.get('users'))}  "
        f"accounts={_fmt_int(totals.get('accounts'))}  "
        f"requests={_fmt_int(totals.get('requests'))}  "
        f"in={_fmt_int(totals.get('input_tokens'))}  "
        f"out={_fmt_int(totals.get('output_tokens'))}  "
        f"cost={_fmt_cost(totals.get('cost_usd'))}",
    ])


def render_report(data: Dict[str, Any], hours: int) -> str:
    by_model = data.get("by_model") or []
    by_project = data.get("by_project") or []
    totals = summarize_totals(by_model)
    out = [
        f"MultiLLM usage — last {hours}h",
        "=" * 60,
        "By model:",
        format_model_table(by_model),
        "",
        "By project:",
        format_project_table(by_project),
        "",
        "Totals:",
        f"  requests={_fmt_int(totals['requests'])}  "
        f"in={_fmt_int(totals['input'])}  out={_fmt_int(totals['output'])}  "
        f"errors={_fmt_int(totals['errors'])}  cost={_fmt_cost(totals['cost'])}",
    ]
    return "\n".join(out)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate MultiLLM gateway usage rollup.")
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY, help=f"Gateway base URL (default {DEFAULT_GATEWAY})")
    parser.add_argument("--hours", type=int, default=24, help="Window in hours (default 24)")
    parser.add_argument("--project", default="", help="Filter to a single project tag (aggregate mode)")
    parser.add_argument("--team", action="store_true", help="Per-developer rollup from /api/team-usage")
    parser.add_argument("--tenant", default="", help="Filter team mode to a single developer")
    parser.add_argument("--json", action="store_true", help="Emit raw JSON instead of a table")
    args = parser.parse_args(argv)

    try:
        if args.team:
            data = fetch_team_usage(args.gateway, args.hours, args.tenant)
        else:
            data = fetch_usage(args.gateway, args.hours, args.project)
    except urllib.error.URLError as exc:
        print(f"error: cannot reach gateway at {args.gateway} ({exc.reason}).", file=sys.stderr)
        print("Is the multillm-gateway service up? `sudo systemctl status multillm-gateway`", file=sys.stderr)
        return 1
    except (json.JSONDecodeError, OSError) as exc:
        print(f"error: bad response from gateway: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(data, indent=2))
    elif args.team:
        print(render_team_report(data, args.hours))
    else:
        print(render_report(data, args.hours))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
