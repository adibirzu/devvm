#!/usr/bin/env python3
"""
Deployment configuration compiler
=================================
Single source of truth for turning an ``.env`` file into (a) the developer list
and (b) the Ansible extra-vars every deployment path feeds the playbook.

Both entry points that configure a machine share it, so they behave identically:

* ``install.sh`` — direct install on this machine, or over SSH to an existing
  host (no cloud provisioning at all);
* ``scripts/deploy_multicloud.py`` — provisions a cloud VM (itself delegating to
  ``deploy_sdk.py`` on the OCI SDK path), then configures it.

Run standalone to render the inventory + extra-vars without deploying::

    python3 scripts/deploy_config.py --env-file .env --print
    python3 scripts/deploy_config.py --emit-vars configs/ansible_vars.json \\
        --emit-inventory configs/hosts.ini --connection local
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# A Linux-safe account name: what useradd will accept on every supported distro.
USERNAME_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
SSH_KEY_PREFIXES = ("ssh-rsa ", "ssh-ed25519 ", "ecdsa-sha2-", "sk-ssh-", "sk-ecdsa-")


class ConfigError(ValueError):
    """Raised when the environment file cannot produce a valid deployment."""


# --------------------------------------------------------------------------- #
# env file handling
# --------------------------------------------------------------------------- #


def parse_env_file(path: Path) -> Dict[str, str]:
    """Parse a shell-style ``KEY=value`` env file. Missing file -> empty dict."""
    data: Dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def resolve_env_file(project_dir: Path, raw_path: str) -> Path:
    """Resolve an env-file argument, falling back to the legacy ``.env.local``."""
    requested = Path(raw_path).expanduser()
    if not requested.is_absolute():
        requested = project_dir / requested
    if requested.exists() or Path(raw_path).name != ".env":
        return requested
    legacy = project_dir / ".env.local"
    return legacy if legacy.exists() else requested


def env_get(env: Dict[str, str], key: str, default: str = "") -> str:
    return env.get(key, default) or default


def env_bool(env: Dict[str, str], key: str, default: bool = False) -> bool:
    value = env_get(env, key)
    if value == "":
        return default
    return value.lower() in {"1", "true", "yes", "y", "on"}


def env_int(env: Dict[str, str], key: str, default: int) -> int:
    value = env_get(env, key, str(default))
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer, got '{value}'") from exc


# --------------------------------------------------------------------------- #
# developers
# --------------------------------------------------------------------------- #


def resolve_ssh_key(value: str) -> str:
    """Return a public-key string from either a literal key or a path to one.

    A path that does not exist yields no key rather than the path itself: the
    default ``~/.ssh/id_rsa.pub`` is frequently absent, and a direct install is
    perfectly valid without one.
    """
    value = (value or "").strip()
    if not value:
        return ""
    # Check the key form first — base64 key material can contain '/', so the
    # "looks like a path" test alone would misread a literal key.
    if value.startswith(SSH_KEY_PREFIXES):
        return value
    path = Path(value).expanduser()
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return ""


def validate_developer(
    dev: Dict[str, Any], require_ssh_key: bool = True
) -> Dict[str, Any]:
    name = str(dev.get("name", ""))
    if not USERNAME_RE.fullmatch(name):
        raise ConfigError(
            f"Invalid developer username '{name}'. Use a Linux-safe name: "
            "lowercase letter/underscore first, then lowercase letters, digits, "
            "underscores, or hyphens."
        )
    key = str(dev.get("ssh_key", ""))
    if key and not key.startswith(SSH_KEY_PREFIXES):
        raise ConfigError(
            f"Developer '{name}' has an SSH key that is not a public key "
            "(expected it to start with ssh-rsa / ssh-ed25519 / ecdsa-sha2-)."
        )
    if require_ssh_key and not key:
        raise ConfigError(
            f"Developer '{name}' has no valid SSH public key configured. "
            "Set SSH_PUBLIC_KEY_PATH (admin) or DEV_<n>_SSH_KEY_PATH."
        )
    return dev


def git_identity(env: Dict[str, str], name: str, prefix: str) -> Dict[str, str]:
    """Resolve a developer's GitHub identity, defaulting to the noreply address."""
    gh_user = env_get(env, f"{prefix}GITHUB_USER", name)
    return {
        "git_name": env_get(env, f"{prefix}GIT_NAME", gh_user or name),
        "git_email": env_get(
            env, f"{prefix}GIT_EMAIL", f"{gh_user or name}@users.noreply.github.com"
        ),
        "github_user": gh_user or name,
    }


def build_developers(
    env: Dict[str, str],
    require_ssh_key: bool = True,
    admin_override: str = "",
    admin_ssh_key_override: str = "",
) -> List[Dict[str, Any]]:
    """Compile the developer list: the admin account plus any ``DEV_<n>_*`` blocks.

    ``require_ssh_key`` is True for cloud provisioning (the key is the only way
    into a fresh VM) and False for a direct install, where the account may
    already exist with its own credentials. The overrides let a caller (install.sh
    --admin-user / --ssh-key) win over the env file without rewriting it.
    """
    admin_name = admin_override or env_get(env, "ADMIN_USERNAME", "devuser")
    admin_key = resolve_ssh_key(
        admin_ssh_key_override
        or env_get(env, "SSH_PUBLIC_KEY_PATH", "~/.ssh/id_rsa.pub")
    )

    developers: List[Dict[str, Any]] = [
        validate_developer(
            {
                "name": admin_name,
                "ssh_key": admin_key,
                "wg_ip": env_get(env, "WG_CLIENT_IP", "10.200.200.2"),
                "code_server_port": env_int(env, "CODE_SERVER_PORT", 8443),
                "private_key": "",
                "public_key": "",
                **git_identity(env, admin_name, ""),
            },
            require_ssh_key=require_ssh_key,
        )
    ]

    if not env_bool(env, "MULTI_DEV_ENABLED", False):
        return developers

    # Parse arbitrarily many developers: DEV_2_NAME, DEV_3_NAME, ... Sparse
    # numbering is tolerated (look ahead two slots before giving up).
    idx = 2
    while True:
        dev_name = env_get(env, f"DEV_{idx}_NAME")
        if not dev_name:
            if not any(env_get(env, f"DEV_{n}_NAME") for n in range(idx + 1, idx + 3)):
                break
            idx += 1
            continue

        dev_ssh_path = env_get(env, f"DEV_{idx}_SSH_KEY_PATH")
        if dev_ssh_path or not require_ssh_key:
            developers.append(
                validate_developer(
                    {
                        "name": dev_name,
                        "ssh_key": resolve_ssh_key(dev_ssh_path),
                        "wg_ip": env_get(
                            env, f"DEV_{idx}_WG_IP", f"10.200.200.{idx + 1}"
                        ),
                        "code_server_port": env_int(
                            env, f"DEV_{idx}_CODE_SERVER_PORT", 8443 + idx - 1
                        ),
                        "private_key": "",
                        "public_key": "",
                        **git_identity(env, dev_name, f"DEV_{idx}_"),
                    },
                    require_ssh_key=require_ssh_key,
                )
            )
        idx += 1

    return developers


# --------------------------------------------------------------------------- #
# ansible extra-vars
# --------------------------------------------------------------------------- #


def build_ansible_extra_vars(
    env: Dict[str, str],
    developers: List[Dict[str, Any]],
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compile the extra-vars the playbook expects, identically for every path."""
    dev_vars = [
        {
            "name": dev["name"],
            "code_server_port": dev["code_server_port"],
            "wg_ip": dev["wg_ip"],
            "ssh_key": dev.get("ssh_key", ""),
            "git_name": dev.get("git_name", dev["name"]),
            "git_email": dev.get(
                "git_email", f"{dev['name']}@users.noreply.github.com"
            ),
            "github_user": dev.get("github_user", dev["name"]),
        }
        for dev in developers
    ]

    extra_vars: Dict[str, Any] = {
        "developers": dev_vars,
        "wg_server_ip": env_get(env, "WG_SERVER_IP", "10.200.200.1"),
        "wg_network": env_get(env, "WG_NETWORK", "10.200.200.0/24"),
        "wg_port": env_int(env, "WG_PORT", 51820),
        "node_version": env_get(env, "NODE_VERSION", "20"),
        "dashboard_port": env_int(env, "DASHBOARD_PORT", 80),
        "rdp_port": env_int(env, "RDP_PORT", 3389),
        "install_cursor": env_bool(env, "INSTALL_CURSOR", True),
        "install_claude_code": env_bool(env, "INSTALL_CLAUDE_CODE", True),
        "install_codex": env_bool(env, "INSTALL_CODEX", True),
        "install_gemini": env_bool(env, "INSTALL_GEMINI", True),
        # Additional agent CLIs — opt-in (default off) so an existing
        # deployment never silently grows new global installs.
        "install_opencode": env_bool(env, "INSTALL_OPENCODE", False),
        "install_pi": env_bool(env, "INSTALL_PI", False),
        "install_grok": env_bool(env, "INSTALL_GROK", False),
        "install_cline": env_bool(env, "INSTALL_CLINE", False),
        "install_copilot_cli": env_bool(env, "INSTALL_COPILOT_CLI", False),
        "install_cursor_agent": env_bool(env, "INSTALL_CURSOR_AGENT", False),
        # Local LLM serving (Ollama) and the coding-client wiring for it.
        "install_ollama": env_bool(env, "INSTALL_OLLAMA", False),
        "ollama_bind_address": env_get(env, "OLLAMA_BIND_ADDRESS", ""),
        "ollama_port": env_int(env, "OLLAMA_PORT", 11434),
        "ollama_models": env_get(env, "OLLAMA_MODELS", ""),
        "ollama_default_model": env_get(env, "OLLAMA_DEFAULT_MODEL", "qwen3-coder"),
        "install_code_server": env_bool(env, "INSTALL_CODE_SERVER", True),
        "install_podman": env_bool(env, "INSTALL_PODMAN", True),
        "install_dev_tools": env_bool(env, "INSTALL_DEV_TOOLS", True),
        "install_github_cli": env_bool(env, "INSTALL_GITHUB_CLI", True),
        "install_csp_clis": env_bool(env, "INSTALL_CSP_CLIS", True),
        "install_aws_cli": env_bool(env, "INSTALL_AWS_CLI", True),
        "install_azure_cli": env_bool(env, "INSTALL_AZURE_CLI", True),
        "install_gcp_cli": env_bool(env, "INSTALL_GCP_CLI", True),
        "install_oci_cli": env_bool(env, "INSTALL_OCI_CLI", True),
        "install_desktop": env_bool(env, "INSTALL_DESKTOP", True),
        "install_multillm_gateway": env_bool(env, "INSTALL_MULTILLM_GATEWAY", True),
        "multillm_gateway_port": env_int(env, "MULTILLM_GATEWAY_PORT", 8080),
        "multillm_collect_interval_min": env_int(
            env, "MULTILLM_COLLECT_INTERVAL_MIN", 15
        ),
        "multillm_user_budgets": env_get(env, "MULTILLM_USER_BUDGETS", ""),
        "multillm_install_source": env_get(
            env, "MULTILLM_INSTALL_SOURCE", "/opt/multillm"
        ),
        "multillm_source_path": env_get(env, "MULTILLM_SOURCE_PATH", ""),
        "multillm_git_url": env_get(
            env, "MULTILLM_GIT_URL", "https://github.com/adibirzu/multillm.git"
        ),
        "multillm_git_version": env_get(env, "MULTILLM_GIT_VERSION", "main"),
        "install_resilience_layer": env_bool(env, "INSTALL_RESILIENCE_LAYER", True),
        "install_agent_os": env_bool(env, "INSTALL_AGENT_OS", True),
        "install_oci_skills": env_bool(env, "INSTALL_OCI_SKILLS", True),
        "install_antigravity": env_bool(env, "INSTALL_ANTIGRAVITY", False),
        "oci_skills_source_path": env_get(env, "OCI_SKILLS_SOURCE_PATH", ""),
        "oci_skills_git_url": env_get(
            env, "OCI_SKILLS_GIT_URL", "https://github.com/adibirzu/oci-skills.git"
        ),
        "oci_skills_git_version": env_get(env, "OCI_SKILLS_GIT_VERSION", "main"),
        # Host-level concerns a direct install may need to own, which cloud-init
        # already handled on a provisioned VM.
        "configure_firewall": env_bool(env, "CONFIGURE_FIREWALL", True),
        "install_wireguard": env_bool(env, "INSTALL_WIREGUARD", False),
    }
    if overrides:
        extra_vars.update(overrides)
    return extra_vars


def build_inventory(
    connection: str = "local",
    host: str = "localhost",
    user: str = "",
    ssh_key: str = "",
) -> str:
    """Render a one-host ``hosts.ini`` for either a local or an SSH target."""
    if connection == "local":
        return (
            "[devserver]\n"
            f"{host or 'localhost'} ansible_connection=local "
            f"ansible_python_interpreter={sys.executable}\n"
        )
    if not host:
        raise ConfigError("A remote install needs a target host (--host).")
    line = f"{host}"
    if user:
        line += f" ansible_user={user}"
    if ssh_key:
        line += f" ansible_ssh_private_key_file={ssh_key}"
    line += " ansible_ssh_extra_args='-o StrictHostKeyChecking=no'"
    return f"[devserver]\n{line}\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_overrides(pairs: List[str]) -> Dict[str, Any]:
    """Turn ``--set key=value`` pairs into typed extra-vars overrides."""
    overrides: Dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ConfigError(f"--set expects key=value, got '{pair}'")
        key, value = pair.split("=", 1)
        lowered = value.lower()
        if lowered in {"true", "false"}:
            overrides[key] = lowered == "true"
        elif value.lstrip("-").isdigit():
            overrides[key] = int(value)
        else:
            overrides[key] = value
    return overrides


def main(argv: Optional[List[str]] = None) -> int:
    project_dir = Path(__file__).resolve().parent.parent

    parser = argparse.ArgumentParser(
        description="Compile deployment config (developers + Ansible extra-vars)."
    )
    parser.add_argument("--env-file", default=".env", help="Path to the env file")
    parser.add_argument("--emit-vars", default="", help="Write extra-vars JSON here")
    parser.add_argument("--emit-inventory", default="", help="Write hosts.ini here")
    parser.add_argument(
        "--connection", choices=["local", "ssh"], default="local", help="Inventory type"
    )
    parser.add_argument("--host", default="localhost", help="Target host")
    parser.add_argument("--user", default="", help="SSH user for a remote target")
    parser.add_argument(
        "--ssh-key", default="", help="SSH private key for a remote target"
    )
    parser.add_argument("--admin-user", default="", help="Override ADMIN_USERNAME")
    parser.add_argument(
        "--admin-ssh-key",
        default="",
        help="Override SSH_PUBLIC_KEY_PATH (path or literal public key)",
    )
    parser.add_argument(
        "--require-ssh-key",
        action="store_true",
        help="Fail when a developer has no SSH public key (cloud provisioning)",
    )
    parser.add_argument(
        "--set", action="append", default=[], help="Override an extra-var: key=value"
    )
    parser.add_argument(
        "--print", action="store_true", help="Print the extra-vars JSON"
    )
    args = parser.parse_args(argv)

    try:
        env_path = resolve_env_file(project_dir, args.env_file)
        env = parse_env_file(env_path)
        developers = build_developers(
            env,
            require_ssh_key=args.require_ssh_key,
            admin_override=args.admin_user,
            admin_ssh_key_override=args.admin_ssh_key,
        )
        extra_vars = build_ansible_extra_vars(
            env, developers, _parse_overrides(args.set)
        )

        if args.emit_vars:
            out = Path(args.emit_vars)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(extra_vars, indent=2), encoding="utf-8")
            out.chmod(0o600)
        if args.emit_inventory:
            out = Path(args.emit_inventory)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                build_inventory(args.connection, args.host, args.user, args.ssh_key),
                encoding="utf-8",
            )
        if args.print or not (args.emit_vars or args.emit_inventory):
            print(json.dumps(extra_vars, indent=2))
        return 0
    except ConfigError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
