#!/usr/bin/env python3
"""pai-runtimes — pluggable agent-runtime registry for the PAI-integrated fleet.

One data source (``agent-os/runtimes.json`` → ``/opt/agent-os/runtimes.json``)
describes every governed coding-agent backend the fleet can launch: Claude,
Codex, Gemini, plus Antigravity (AGY), Hermes, and nano-claw/OpenClaw. Adding a
runtime is a JSON edit, not a code change.

Every runtime inherits two things it CANNOT opt out of:

  * the per-UNIX-user sandbox (it runs as the calling developer), and
  * the Claude Code ``PreToolUse`` guardrail (``guardrail-hook``) that denies/asks
    on destructive tool calls.

The registry only resolves *which command to launch*; it never weakens the gate.
``gateway_routed`` runtimes get their base-URL env var pointed at the shared
MultiLLM gateway so token usage is attributed per tenant.

Standard-library only (matches the repo). Mirrors ``mcp_registry.py`` in shape.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

DEFAULT_REGISTRY = os.environ.get("PAI_RUNTIMES_REGISTRY", "/opt/agent-os/runtimes.json")

_REQUIRED_FIELDS = ("name", "enabled", "exec_template")


class RegistryError(ValueError):
    """Raised on a malformed registry or an unresolvable runtime."""


def load_registry(path: str) -> Dict[str, Any]:
    """Load and parse the registry JSON. Raises RegistryError on bad JSON/shape."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError as exc:
        raise RegistryError(f"registry not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RegistryError(f"registry is not valid JSON ({path}): {exc}") from exc
    validate_registry(data)
    return data


def validate_registry(data: Dict[str, Any]) -> List[str]:
    """Validate a parsed registry. Returns the list of runtime names, or raises."""
    if not isinstance(data, dict):
        raise RegistryError("registry root must be a JSON object")
    runtimes = data.get("runtimes")
    if not isinstance(runtimes, list) or not runtimes:
        raise RegistryError("registry must contain a non-empty 'runtimes' array")
    names: List[str] = []
    seen = set()
    for i, rt in enumerate(runtimes):
        if not isinstance(rt, dict):
            raise RegistryError(f"runtime #{i} must be an object")
        for field in _REQUIRED_FIELDS:
            if field not in rt:
                raise RegistryError(f"runtime #{i} missing required field '{field}'")
        if not isinstance(rt["exec_template"], list) or not rt["exec_template"]:
            raise RegistryError(f"runtime '{rt.get('name')}' exec_template must be a non-empty array")
        name = rt["name"]
        if name in seen:
            raise RegistryError(f"duplicate runtime name '{name}'")
        seen.add(name)
        # Aliases must not collide with names or other aliases.
        for alias in rt.get("aliases", []) or []:
            if alias in seen:
                raise RegistryError(f"alias '{alias}' collides with an existing name/alias")
            seen.add(alias)
        names.append(name)
    return names


def _index(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Map every name AND alias → its runtime entry."""
    idx: Dict[str, Dict[str, Any]] = {}
    for rt in data.get("runtimes", []):
        idx[rt["name"]] = rt
        for alias in rt.get("aliases", []) or []:
            idx[alias] = rt
    return idx


def enabled_runtimes(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the enabled runtime entries, in declaration order."""
    return [rt for rt in data.get("runtimes", []) if rt.get("enabled")]


def get_runtime(data: Dict[str, Any], name: str) -> Dict[str, Any]:
    """Look up a runtime by name or alias. Raises on unknown OR disabled."""
    rt = _index(data).get(name)
    if rt is None:
        known = ", ".join(sorted(_index(data))) or "(none)"
        raise RegistryError(f"unknown runtime '{name}'. Known: {known}")
    if not rt.get("enabled"):
        raise RegistryError(f"runtime '{name}' is disabled in the registry")
    return rt


def resolve_command(
    data: Dict[str, Any],
    name: str,
    prompt: Optional[str] = None,
    interactive: bool = False,
) -> List[str]:
    """Resolve a runtime to a concrete launch command (argv list).

    ``interactive=True`` uses ``interactive_template`` (for cmux / agentctl attach);
    otherwise ``exec_template`` (non-interactive, for agent-job). ``{prompt}`` is
    substituted with the given prompt. Unknown/disabled runtimes raise — they are
    never silently launched.
    """
    rt = get_runtime(data, name)
    template = rt.get("interactive_template") if interactive else rt.get("exec_template")
    if not template:
        # Interactive falls back to exec_template's binary if no interactive form.
        template = rt["exec_template"][:1]
    argv: List[str] = []
    for token in template:
        if token == "{prompt}":
            argv.append(prompt if prompt is not None else "")
        else:
            argv.append(token.replace("{prompt}", prompt or ""))
    return argv


def gateway_env_for(data: Dict[str, Any], name: str, gateway_url: str) -> Dict[str, str]:
    """Return the env overlay that points a gateway-routed runtime at the gateway.

    Empty dict if the runtime is not gateway-routed or declares no env var.
    """
    rt = get_runtime(data, name)
    if not rt.get("gateway_routed"):
        return {}
    env_var = rt.get("gateway_env")
    return {env_var: gateway_url} if env_var else {}


# --- CLI -------------------------------------------------------------------

def cmd_list(args: argparse.Namespace, data: Dict[str, Any]) -> int:
    rows = data.get("runtimes", []) if args.all else enabled_runtimes(data)
    if not rows:
        print("  (no runtimes)")
        return 0
    for rt in rows:
        flag = "on " if rt.get("enabled") else "off"
        gw = "→gateway" if rt.get("gateway_routed") else "        "
        aliases = rt.get("aliases") or []
        alias_str = f"  (aka {', '.join(aliases)})" if aliases else ""
        print(f"  [{flag}] {gw}  {rt['name']:<12} {rt.get('description', '')}{alias_str}")
    return 0


def cmd_validate(args: argparse.Namespace, data: Dict[str, Any]) -> int:
    names = validate_registry(data)
    print(f"OK — {len(names)} runtimes: {', '.join(names)}")
    return 0


def cmd_resolve(args: argparse.Namespace, data: Dict[str, Any]) -> int:
    argv = resolve_command(data, args.name, prompt=args.prompt, interactive=args.interactive)
    overlay = gateway_env_for(data, args.name, args.gateway)
    if overlay and not args.interactive:
        env_prefix = " ".join(f"{k}={v}" for k, v in overlay.items())
        print(f"{env_prefix} " + " ".join(_shquote(a) for a in argv))
    else:
        print(" ".join(_shquote(a) for a in argv))
    return 0


def _shquote(token: str) -> str:
    if token == "" or any(c.isspace() for c in token):
        return "'" + token.replace("'", "'\\''") + "'"
    return token


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="pai-runtimes", description="Pluggable agent-runtime registry.")
    parser.add_argument("--registry", default=DEFAULT_REGISTRY, help=f"registry path (default {DEFAULT_REGISTRY})")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List runtimes")
    p_list.add_argument("--all", action="store_true", help="Include disabled runtimes")

    sub.add_parser("validate", help="Validate the registry")

    p_res = sub.add_parser("resolve", help="Resolve a runtime to a launch command")
    p_res.add_argument("name", help="Runtime name or alias")
    p_res.add_argument("--prompt", default=None, help="Prompt to substitute for {prompt}")
    p_res.add_argument("--interactive", action="store_true", help="Use the interactive template")
    p_res.add_argument("--gateway", default=os.environ.get("MULTILLM_GATEWAY", "http://10.200.200.1:8080"),
                       help="Gateway base URL for gateway-routed runtimes")

    args = parser.parse_args(argv)
    try:
        data = load_registry(args.registry)
        handlers = {"list": cmd_list, "validate": cmd_validate, "resolve": cmd_resolve}
        return handlers[args.command](args, data)
    except RegistryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
