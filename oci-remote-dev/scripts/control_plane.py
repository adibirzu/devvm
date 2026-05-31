#!/usr/bin/env python3
"""control-plane — fleet API for the agentic dev OS (VPN-only).

Read side (open over the VPN):
  GET  /healthz                 liveness
  GET  /fleet/status            the live agent board (delegates to `agent-status`)
  GET  /developers              configured developers
  GET  /fleet/services          systemd state of the agent-OS units
  GET  /pending                 queued (not-yet-applied) account changes

Write side (require `X-Admin-Token`; token in /etc/agent-os/admin.token):
  POST   /developers            validate + QUEUE an add (admin applies via deploy.sh)
  DELETE /developers/<name>     QUEUE a removal (never auto-deletes an account)
  POST   /budgets               set per-user daily caps — applies LIVE (config only)

Account changes are queued, not executed: materializing them runs Ansible and is
destructive, so it stays in the deliberate deploy path. Budgets are non-destructive
and take effect on the aggregator's next poll. Every mutation is audited.

Pure `dispatch()` + validators are unit-tested; sockets/files/subprocess are thin IO.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

Handler = Callable[[Dict[str, Any], Dict[str, Any]], Tuple[int, Any]]
_NAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_SSH_PREFIXES = ("ssh-rsa ", "ssh-ed25519 ", "ecdsa-sha2-")


# ── Pure validation ──────────────────────────────────────────────────────────

def validate_developer_request(body: Dict[str, Any]) -> Tuple[bool, List[str]]:
    errs: List[str] = []
    name = str(body.get("name", ""))
    if not _NAME_RE.fullmatch(name):
        errs.append("invalid name (Linux-safe: ^[a-z_][a-z0-9_-]{0,31}$)")
    key = str(body.get("ssh_key", ""))
    if not key.startswith(_SSH_PREFIXES):
        errs.append("ssh_key must be a valid public key (ssh-rsa/ssh-ed25519/ecdsa-sha2-)")
    return (not errs), errs


def parse_budgets_request(body: Dict[str, Any]) -> Tuple[bool, Dict[str, float], List[str]]:
    """Accept {"budgets": {"adi": 5, ...}} or a flat {"adi": 5, ...}."""
    raw = body.get("budgets", body) if isinstance(body, dict) else {}
    out: Dict[str, float] = {}
    errs: List[str] = []
    if not isinstance(raw, dict) or not raw:
        return False, {}, ["body must be a non-empty object of user->usd caps"]
    for user, cap in raw.items():
        if not _NAME_RE.fullmatch(str(user)):
            errs.append(f"invalid user '{user}'")
            continue
        try:
            val = float(cap)
        except (TypeError, ValueError):
            errs.append(f"cap for '{user}' must be a number")
            continue
        if val < 0:
            errs.append(f"cap for '{user}' must be >= 0")
            continue
        out[str(user)] = val
    return (not errs), out, errs


def budgets_to_spec(budgets: Dict[str, float]) -> str:
    return ",".join(f"{u}={budgets[u]:g}" for u in sorted(budgets))


def authorize(ctx: Dict[str, Any], deps: Dict[str, Any]) -> bool:
    token = (ctx.get("headers") or {}).get("x-admin-token", "")
    expected = deps["admin_token"]()
    return bool(expected) and token == expected


# ── Router (pure) ────────────────────────────────────────────────────────────

def dispatch(method: str, path: str, handlers: Dict[Tuple[str, str], Handler],
             deps: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Tuple[int, Any]:
    ctx = ctx or {}
    # Dynamic route: DELETE /developers/<name>
    if method == "DELETE" and path.startswith("/developers/"):
        name = path[len("/developers/"):]
        return _guarded(h_delete_developer, deps, {**ctx, "name": name})
    handler = handlers.get((method, path))
    if handler is None:
        known = any(p == path for (_, p) in handlers) or path.startswith("/developers/")
        return (405, {"error": "method not allowed"}) if known else (404, {"error": "not found", "path": path})
    try:
        return handler(deps, ctx)
    except Exception as e:  # never leak a stack trace
        return 500, {"error": "handler failed", "detail": str(e)[:200]}


def _guarded(handler: Handler, deps: Dict[str, Any], ctx: Dict[str, Any]) -> Tuple[int, Any]:
    try:
        return handler(deps, ctx)
    except Exception as e:
        return 500, {"error": "handler failed", "detail": str(e)[:200]}


def _ok(body: Any) -> Tuple[int, Any]:
    return 200, body


# ── Handlers ─────────────────────────────────────────────────────────────────

def h_healthz(deps, ctx): return _ok({"status": "ok", "service": "control-plane"})
def h_fleet_status(deps, ctx): return _ok(deps["fleet_status"]())
def h_developers(deps, ctx): return _ok({"developers": deps["developers"]()})
def h_services(deps, ctx): return _ok({"services": deps["services"]()})
def h_pending(deps, ctx): return _ok({"pending": deps["pending"]()})


def h_post_developers(deps, ctx) -> Tuple[int, Any]:
    if not authorize(ctx, deps):
        return 401, {"error": "admin token required (X-Admin-Token)"}
    body = ctx.get("body") or {}
    ok, errs = validate_developer_request(body)
    if not ok:
        return 422, {"error": "validation failed", "details": errs}
    change = {"op": "add", "name": body["name"], "ssh_key": body.get("ssh_key", ""),
              "wg_ip": body.get("wg_ip"), "code_server_port": body.get("code_server_port"),
              "github_user": body.get("github_user")}
    deps["enqueue"](change)
    deps["audit"]({"action": "queue_add_developer", "name": body["name"]})
    return 202, {"status": "queued", "change": change,
                 "note": "Apply with: ./scripts/deploy.sh --profile <p> --yes (materializes pending changes)"}


def h_delete_developer(deps, ctx) -> Tuple[int, Any]:
    if not authorize(ctx, deps):
        return 401, {"error": "admin token required (X-Admin-Token)"}
    name = ctx.get("name", "")
    if not _NAME_RE.fullmatch(name):
        return 422, {"error": "invalid developer name"}
    change = {"op": "remove", "name": name}
    deps["enqueue"](change)
    deps["audit"]({"action": "queue_remove_developer", "name": name})
    return 202, {"status": "queued", "change": change,
                 "note": "Account removal is never automatic; an admin reviews the queue before applying."}


def h_post_budgets(deps, ctx) -> Tuple[int, Any]:
    if not authorize(ctx, deps):
        return 401, {"error": "admin token required (X-Admin-Token)"}
    ok, budgets, errs = parse_budgets_request(ctx.get("body") or {})
    if not ok:
        return 422, {"error": "validation failed", "details": errs}
    spec = budgets_to_spec(budgets)
    deps["set_budgets"](spec)
    deps["audit"]({"action": "set_budgets", "spec": spec})
    return _ok({"status": "applied", "budgets": budgets, "spec": spec,
                "note": "Live on the board within ~15s (agent-status reads /etc/agent-os/budgets)."})


HANDLERS: Dict[Tuple[str, str], Handler] = {
    ("GET", "/healthz"): h_healthz,
    ("GET", "/fleet/status"): h_fleet_status,
    ("GET", "/developers"): h_developers,
    ("GET", "/fleet/services"): h_services,
    ("GET", "/pending"): h_pending,
    ("POST", "/developers"): h_post_developers,
    ("POST", "/budgets"): h_post_budgets,
}


# ── Real dependency implementations (IO) ─────────────────────────────────────

AGENT_OS_DIR = Path("/etc/agent-os")
PENDING_FILE = AGENT_OS_DIR / "pending-changes.jsonl"
BUDGETS_FILE = AGENT_OS_DIR / "budgets"
AUDIT_FILE = AGENT_OS_DIR / "control-plane-audit.jsonl"
ADMIN_TOKEN_FILE = AGENT_OS_DIR / "admin.token"


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_admin_token() -> str:
    try:
        return ADMIN_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _append_jsonl(path: Path, entry: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({**entry, "ts": _now()}) + "\n")
    except OSError:
        pass


def _read_pending() -> List[Dict[str, Any]]:
    if not PENDING_FILE.exists():
        return []
    out = []
    for ln in PENDING_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                pass
    return out


def _fleet_status(gateway: str, home_root: str) -> Dict[str, Any]:
    try:
        out = subprocess.run(["agent-status", "--home-root", home_root, "--gateway", gateway],
                             capture_output=True, text=True, timeout=20)
        return json.loads(out.stdout) if out.returncode == 0 and out.stdout.strip() else {"developers": [], "totals": {}}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {"developers": [], "totals": {}, "error": "agent-status unavailable"}


def _developers(home_root: str) -> List[Dict[str, Any]]:
    root = Path(home_root)
    return [{"name": p.name, "home": str(p)} for p in sorted(root.iterdir())
            if (p / ".agentctl").is_dir()] if root.is_dir() else []


def _services() -> List[Dict[str, str]]:
    units = ["multillm-gateway.service", "agent-status.timer", "project-status.timer",
             "dev-dashboard.service", "control-plane.service", "agentctl-restore.service"]
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
        "pending": _read_pending,
        "admin_token": _read_admin_token,
        "enqueue": lambda change: _append_jsonl(PENDING_FILE, change),
        "audit": lambda entry: _append_jsonl(AUDIT_FILE, entry),
        "set_budgets": lambda spec: BUDGETS_FILE.write_text(spec + "\n", encoding="utf-8"),
    }


def make_handler(deps: Dict[str, Any]):
    class CP(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _ctx(self) -> Dict[str, Any]:
            headers = {k.lower(): v for k, v in self.headers.items()}
            body: Dict[str, Any] = {}
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length:
                try:
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                except (ValueError, json.JSONDecodeError):
                    body = {}
            return {"headers": headers, "body": body}

        def _respond(self, method: str) -> None:
            status, body = dispatch(method, self.path.split("?")[0], HANDLERS, deps, self._ctx())
            payload = json.dumps(body, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self): self._respond("GET")        # noqa: N802
        def do_POST(self): self._respond("POST")      # noqa: N802
        def do_DELETE(self): self._respond("DELETE")  # noqa: N802
    return CP


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Fleet control-plane API (read + queued writes).")
    p.add_argument("--host", default=os.environ.get("CP_HOST", "10.200.200.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("CP_PORT", "8082")))
    p.add_argument("--gateway", default=os.environ.get("CP_GATEWAY", "http://10.200.200.1:8080"))
    p.add_argument("--home-root", default="/home")
    args = p.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(build_deps(args.gateway, args.home_root)))
    print(f"control-plane listening on http://{args.host}:{args.port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
