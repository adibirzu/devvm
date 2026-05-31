#!/usr/bin/env python3
"""control-plane — read-only fleet API for the agentic dev OS (VPN-only).

A small, dependency-free REST API (stdlib http.server) that exposes fleet state so
tools/dashboards can query it without scraping files:

  GET /healthz          liveness
  GET /fleet/status     the live agent board (delegates to `agent-status`)
  GET /developers       configured developers (from /home + agentctl presence)
  GET /fleet/services   systemd state of the agent-OS units

Read-only by design. Mutating endpoints (POST /developers → run Ansible) are a
separate, audited task. Bound to the WireGuard IP and firewalled to the WG subnet.

The `dispatch()` router is pure (handlers injected) so it's unit-tested without a
socket; the HTTP wrapper and the subprocess/file IO are thin.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

Handler = Callable[[Dict[str, Any]], Tuple[int, Any]]


def _ok(body: Any) -> Tuple[int, Any]:
    return 200, body


def dispatch(method: str, path: str, handlers: Dict[Tuple[str, str], Handler],
             deps: Dict[str, Any]) -> Tuple[int, Any]:
    """Pure router: (method, path) -> handler(deps) -> (status, body)."""
    if method != "GET":
        return 405, {"error": "method not allowed"}
    handler = handlers.get((method, path))
    if handler is None:
        return 404, {"error": "not found", "path": path}
    try:
        return handler(deps)
    except Exception as e:  # never leak a stack trace to a client
        return 500, {"error": "handler failed", "detail": str(e)[:200]}


# ── Handlers (pure given injected deps) ──────────────────────────────────────

def h_healthz(deps: Dict[str, Any]) -> Tuple[int, Any]:
    return _ok({"status": "ok", "service": "control-plane"})


def h_fleet_status(deps: Dict[str, Any]) -> Tuple[int, Any]:
    return _ok(deps["fleet_status"]())


def h_developers(deps: Dict[str, Any]) -> Tuple[int, Any]:
    return _ok({"developers": deps["developers"]()})


def h_services(deps: Dict[str, Any]) -> Tuple[int, Any]:
    return _ok({"services": deps["services"]()})


HANDLERS: Dict[Tuple[str, str], Handler] = {
    ("GET", "/healthz"): h_healthz,
    ("GET", "/fleet/status"): h_fleet_status,
    ("GET", "/developers"): h_developers,
    ("GET", "/fleet/services"): h_services,
}


# ── Real dependency implementations (IO) ─────────────────────────────────────

def _fleet_status(gateway: str, home_root: str) -> Dict[str, Any]:
    try:
        out = subprocess.run(
            ["agent-status", "--home-root", home_root, "--gateway", gateway],
            capture_output=True, text=True, timeout=20,
        )
        return json.loads(out.stdout) if out.returncode == 0 and out.stdout.strip() else {"developers": [], "totals": {}}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {"developers": [], "totals": {}, "error": "agent-status unavailable"}


def _developers(home_root: str) -> List[Dict[str, Any]]:
    root = Path(home_root)
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.iterdir()):
        if (p / ".agentctl").is_dir():
            out.append({"name": p.name, "home": str(p)})
    return out


def _services() -> List[Dict[str, str]]:
    units = ["multillm-gateway.service", "agent-status.timer", "project-status.timer",
             "dev-dashboard.service", "agentctl-restore.service"]
    out = []
    for u in units:
        try:
            r = subprocess.run(["systemctl", "is-active", u], capture_output=True, text=True, timeout=5)
            state = (r.stdout or r.stderr).strip() or "unknown"
        except (OSError, subprocess.SubprocessError):
            state = "unknown"
        out.append({"unit": u, "state": state})
    return out


def build_deps(gateway: str, home_root: str) -> Dict[str, Any]:
    return {
        "fleet_status": lambda: _fleet_status(gateway, home_root),
        "developers": lambda: _developers(home_root),
        "services": _services,
    }


def make_handler(deps: Dict[str, Any]):
    class CP(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def do_GET(self):  # noqa: N802
            status, body = dispatch("GET", self.path.split("?")[0], HANDLERS, deps)
            payload = json.dumps(body, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    return CP


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Read-only fleet control-plane API.")
    p.add_argument("--host", default=os.environ.get("CP_HOST", "10.200.200.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("CP_PORT", "8082")))
    p.add_argument("--gateway", default=os.environ.get("CP_GATEWAY", "http://10.200.200.1:8080"))
    p.add_argument("--home-root", default="/home")
    args = p.parse_args(argv)
    deps = build_deps(args.gateway, args.home_root)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(deps))
    print(f"control-plane listening on http://{args.host}:{args.port} (read-only)", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
