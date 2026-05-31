#!/usr/bin/env python3
"""mcp-registry — generate each developer's ~/.claude/.mcp.json from the central
registry of APPROVED MCP servers (/opt/agent-os/registry.json).

One governed tool surface for the agent fleet: enabled servers are merged into the
user's config; disabled servers are removed; servers not in the registry are left
untouched (so a developer's personal experiments survive). Idempotent.

  mcp-registry list                      show approved servers
  mcp-registry apply [--config PATH]     merge into ~/.claude/.mcp.json (default)
  mcp-registry validate                  check the registry parses + is well-formed
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REGISTRY_PATH = os.environ.get("MCP_REGISTRY", "/opt/agent-os/registry.json")
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def load_registry(path: str = REGISTRY_PATH) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _subst(value: Any, subs: Dict[str, str]) -> Any:
    """Recursively replace ${VAR} in strings using subs (default: env, then '')."""
    if isinstance(value, str):
        return _VAR.sub(
            lambda m: subs.get(m.group(1), os.environ.get(m.group(1), "")), value
        )
    if isinstance(value, list):
        return [_subst(v, subs) for v in value]
    if isinstance(value, dict):
        return {k: _subst(v, subs) for k, v in value.items()}
    return value


def render_server(server: Dict[str, Any], subs: Dict[str, str]) -> Dict[str, Any]:
    """Produce the .mcp.json entry body for an approved server."""
    out: Dict[str, Any] = {
        "command": _subst(server.get("command", ""), subs),
        "args": _subst(server.get("args", []), subs),
    }
    if server.get("env"):
        out["env"] = _subst(server["env"], subs)
    return out


def merge_mcp(
    existing: Dict[str, Any], registry: Dict[str, Any], subs: Dict[str, str]
) -> Dict[str, Any]:
    """Merge approved servers into an existing .mcp.json structure (pure)."""
    result = dict(existing) if existing else {}
    servers = dict(result.get("mcpServers", {}))
    for s in registry.get("servers", []):
        name = s.get("name")
        if not name:
            continue
        if s.get("enabled", True):
            servers[name] = render_server(s, subs)
        else:
            servers.pop(name, None)  # disabled → remove from user config
    result["mcpServers"] = servers
    return result


def _default_subs() -> Dict[str, str]:
    return {
        "MULTILLM_GATEWAY_PORT": os.environ.get("MULTILLM_GATEWAY_PORT", "8080"),
        "OCI_PROFILE": os.environ.get("OCI_PROFILE", "DEFAULT"),
    }


def cmd_list(reg: Dict[str, Any]) -> int:
    print("Approved MCP servers (registry):")
    for s in reg.get("servers", []):
        flag = "✓" if s.get("enabled", True) else "✗ (disabled)"
        print(f"  {flag} {s.get('name','?'):<14} {s.get('description','')}")
    return 0


def cmd_apply(reg: Dict[str, Any], config_path: Path) -> int:
    existing: Dict[str, Any] = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
    merged = merge_mcp(existing, reg, _default_subs())
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    names = ", ".join(sorted(merged.get("mcpServers", {}).keys()))
    print(f"applied → {config_path}  (servers: {names})")
    return 0


def cmd_validate(reg: Dict[str, Any]) -> int:
    errs: List[str] = []
    if not isinstance(reg.get("servers"), list):
        errs.append("missing 'servers' array")
    for i, s in enumerate(reg.get("servers", [])):
        if not s.get("name"):
            errs.append(f"server[{i}] missing name")
        if not s.get("command"):
            errs.append(f"server[{i}] ({s.get('name','?')}) missing command")
    if errs:
        for e in errs:
            print(f"INVALID: {e}", file=sys.stderr)
        return 1
    print(f"registry OK — {len(reg.get('servers', []))} servers")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Generate per-user .mcp.json from the approved MCP registry."
    )
    p.add_argument("command", choices=["list", "apply", "validate"])
    p.add_argument("--registry", default=REGISTRY_PATH)
    p.add_argument("--config", default=str(Path.home() / ".claude" / ".mcp.json"))
    args = p.parse_args(argv)
    try:
        reg = load_registry(args.registry)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: cannot read registry {args.registry}: {e}", file=sys.stderr)
        return 1
    if args.command == "list":
        return cmd_list(reg)
    if args.command == "apply":
        return cmd_apply(reg, Path(args.config))
    return cmd_validate(reg)


if __name__ == "__main__":
    sys.exit(main())
