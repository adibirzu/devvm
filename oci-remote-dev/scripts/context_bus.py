#!/usr/bin/env python3
"""context — terminal client for the shared MultiLLM memory / context bus.

Wraps the gateway's ``/api/memory`` endpoints with convention-based scoping so
each developer gets a private namespace by default and shares only on purpose:

  * default scope  -> project ``user-<whoami>``  (your own memory)
  * ``--shared``    -> project ``shared``         (cross-developer context)
  * ``--all``       -> no project filter          (search/list across everything)

Standard-library only. Reads the gateway URL from ``MULTILLM_GATEWAY`` and the
API key (needed for writes) from ``MULTILLM_API_KEY`` — both set in
``/etc/multillm/collector.env`` on the VM.

Scope is a convention enforced by this client, not yet a hard boundary in the
gateway (all rows still share one store). True per-tenant enforcement is a
multillm-side follow-up — see ROADMAP-v2.md, Phase 2.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

DEFAULT_GATEWAY = os.environ.get("MULTILLM_GATEWAY", "http://localhost:8080")
SHARED_SCOPE = "shared"


def scope_for(user: str, shared: bool) -> str:
    """Resolve the project namespace for a given user and sharing intent."""
    return SHARED_SCOPE if shared else f"user-{user}"


def tenant_for(user: str, shared: bool, all_scopes: bool = False) -> str:
    """Hard ownership tenant (sent as X-MultiLLM-Tenant), aligned with the scope:
    --all → '' (no tenant filter, see everything); --shared → 'shared'; else the user.
    The gateway tags writes with it and filters reads by it — so isolation is enforced
    server-side, not just by the project naming convention."""
    if all_scopes:
        return ""
    return SHARED_SCOPE if shared else user


def build_put_payload(title: str, content: str, category: str, project: str, source_llm: str = "cli") -> Dict[str, Any]:
    return {
        "title": title,
        "content": content,
        "project": project,
        "category": category,
        "source_llm": source_llm,
    }


def format_memory_rows(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "  (no matching memories)"
    lines = []
    for row in rows:
        mem_id = str(row.get("id", "?"))[:8]
        project = str(row.get("project") or "?")
        title = str(row.get("title") or "(untitled)")
        snippet = " ".join(str(row.get("content") or "").split())[:80]
        lines.append(f"  [{mem_id}] ({project}) {title}")
        if snippet:
            lines.append(f"          {snippet}")
    return "\n".join(lines)


def _request(method: str, url: str, api_key: str = "", body: Optional[dict] = None,
             tenant: str = "", timeout: float = 5.0) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if api_key:
        headers["X-API-Key"] = api_key
    if tenant:
        headers["X-MultiLLM-Tenant"] = tenant
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (trusted VPN URL)
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _qs(params: Dict[str, Any]) -> str:
    clean = {k: v for k, v in params.items() if v not in (None, "")}
    return ("?" + urllib.parse.urlencode(clean)) if clean else ""


def cmd_put(args: argparse.Namespace, base: str, key: str, project: str, tenant: str = "") -> int:
    payload = build_put_payload(args.title, args.content, args.category, project)
    res = _request("POST", base + "/api/memory", api_key=key, body=payload, tenant=tenant)
    print(f"stored [{str(res.get('id', '?'))[:8]}] in {project}: {args.title}")
    return 0


def cmd_search(args: argparse.Namespace, base: str, key: str, project: Optional[str], tenant: str = "") -> int:
    url = base + "/api/memory/search" + _qs({"q": args.query, "project": project, "limit": args.limit})
    res = _request("GET", url, api_key=key, tenant=tenant)
    rows = res if isinstance(res, list) else res.get("results", res.get("memories", []))
    print(format_memory_rows(rows))
    return 0


def cmd_list(args: argparse.Namespace, base: str, key: str, project: Optional[str], tenant: str = "") -> int:
    url = base + "/api/memory" + _qs({"project": project, "category": args.category, "limit": args.limit})
    res = _request("GET", url, api_key=key, tenant=tenant)
    rows = res if isinstance(res, list) else res.get("memories", [])
    print(format_memory_rows(rows))
    return 0


def cmd_rm(args: argparse.Namespace, base: str, key: str, project: Optional[str], tenant: str = "") -> int:
    _request("DELETE", base + f"/api/memory/{urllib.parse.quote(args.id)}", api_key=key, tenant=tenant)
    print(f"deleted {args.id}")
    return 0


def _resolve_project(args: argparse.Namespace, user: str) -> Optional[str]:
    """All-scope (search/list) -> None; otherwise the user/shared namespace."""
    if getattr(args, "all_scopes", False):
        return None
    return scope_for(user, args.shared)


def main(argv: Optional[List[str]] = None) -> int:
    # Common options live on a parent parser so they work *after* the subcommand
    # (e.g. `context search foo --shared`), which is how people actually type them.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--gateway", default=DEFAULT_GATEWAY, help=f"Gateway base URL (default {DEFAULT_GATEWAY})")
    common.add_argument("--shared", action="store_true", help="Use the cross-developer 'shared' namespace")
    common.add_argument("--user", default=getpass.getuser(), help="Override the developer namespace (default: $USER)")

    parser = argparse.ArgumentParser(prog="context", description="Shared MultiLLM memory/context bus client.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_put = sub.add_parser("put", parents=[common], help="Store a memory")
    p_put.add_argument("title")
    p_put.add_argument("content")
    p_put.add_argument("--category", default="general")

    p_search = sub.add_parser("search", parents=[common], help="Search memories")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=10)
    p_search.add_argument("--all", dest="all_scopes", action="store_true", help="Search across all namespaces")

    p_list = sub.add_parser("list", parents=[common], help="List recent memories")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--category", default="")
    p_list.add_argument("--all", dest="all_scopes", action="store_true", help="List across all namespaces")

    p_rm = sub.add_parser("rm", parents=[common], help="Delete a memory by id")
    p_rm.add_argument("id")

    args = parser.parse_args(argv)
    base = args.gateway.rstrip("/")
    key = os.environ.get("MULTILLM_API_KEY", "")
    project = _resolve_project(args, args.user)
    tenant = tenant_for(args.user, args.shared, getattr(args, "all_scopes", False))

    handlers = {"put": cmd_put, "search": cmd_search, "list": cmd_list, "rm": cmd_rm}
    try:
        return handlers[args.command](args, base, key, project, tenant)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            print("error: write rejected — set MULTILLM_API_KEY (see /etc/multillm/collector.env).", file=sys.stderr)
        else:
            print(f"error: gateway returned HTTP {exc.code}: {exc.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"error: cannot reach gateway at {base} ({exc.reason}).", file=sys.stderr)
        return 1
    except (json.JSONDecodeError, OSError) as exc:
        print(f"error: bad response from gateway: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
