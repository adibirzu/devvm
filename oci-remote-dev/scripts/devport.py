#!/usr/bin/env python3
"""Allocate stable, per-worktree development ports for one UNIX user."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class DevportError(RuntimeError):
    """A user-facing broker error."""


def load_config(path: Path) -> dict[str, object]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DevportError(f"cannot read config {path}: {exc}") from exc
    try:
        start = int(config["range_start"])
        end = int(config["range_end"])
        url_host = str(config["url_host"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DevportError(f"invalid config {path}") from exc
    if not (1024 <= start <= end <= 65535) or not url_host:
        raise DevportError(f"invalid port range or URL host in {path}")
    return {"range_start": start, "range_end": end, "url_host": url_host}


def port_is_free(port: int) -> bool:
    """Return whether a TCP server can bind this port on any IPv4 interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("0.0.0.0", port))
        except OSError:
            return False
    return True


def validate_name(name: str) -> str:
    if not NAME_RE.fullmatch(name):
        raise DevportError(
            "name must be 1-128 letters, digits, dots, underscores, or hyphens"
        )
    return name


def allocate_port(
    name: str, claims: dict[str, int], start: int, end: int
) -> tuple[int, bool]:
    """Return a stable existing claim or allocate the first usable free port."""
    validate_name(name)
    if name in claims:
        return int(claims[name]), False
    claimed = {int(port) for port in claims.values()}
    for port in range(start, end + 1):
        if port not in claimed and port_is_free(port):
            claims[name] = port
            return port, True
    raise DevportError(f"no free ports remain in {start}-{end}")


def url_for(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def read_claims(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TypeError("root must be an object")
        claims = {validate_name(str(name)): int(port) for name, port in raw.items()}
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise DevportError(f"cannot read state {path}: {exc}") from exc
    if any(not 1024 <= port <= 65535 for port in claims.values()):
        raise DevportError(f"state contains an invalid port: {path}")
    return claims


def write_claims(path: Path, claims: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix="claims.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(claims, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


@contextmanager
def locked_claims(state_file: Path) -> Iterator[dict[str, int]]:
    state_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_file = state_file.with_suffix(".lock")
    with lock_file.open("a+", encoding="utf-8") as lock:
        os.chmod(lock_file, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield read_claims(state_file)


def default_worktree_name(cwd: Path) -> str:
    """Derive a readable identity that stays stable anywhere in one worktree."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        root = Path(result.stdout.strip()).resolve()
    except (OSError, subprocess.CalledProcessError):
        root = cwd.resolve()
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", root.name).strip("-") or "worktree"
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="devport", description="claim stable ports for parallel dev worktrees"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    claim = subparsers.add_parser("claim", help="claim a stable port and print its URL")
    claim.add_argument("name")
    release = subparsers.add_parser("release", help="release a named port claim")
    release.add_argument("name", nargs="?")
    subparsers.add_parser("list", help="list this user's claims")
    env = subparsers.add_parser("env", help="emit shell exports for a worktree")
    env.add_argument("name", nargs="?")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    home = Path.home()
    config_path = Path(
        os.environ.get("DEVPORT_CONFIG", home / ".config/devport/config.json")
    )
    state_root = Path(
        os.environ.get(
            "XDG_STATE_HOME",
            os.environ.get("DEVPORT_STATE_HOME", home / ".local/state"),
        )
    )
    state_file = state_root / "devport/claims.json"
    try:
        config = load_config(config_path)
        start = int(config["range_start"])
        end = int(config["range_end"])
        host = str(config["url_host"])

        if args.command == "list":
            with locked_claims(state_file) as claims:
                for name, port in sorted(claims.items()):
                    print(f"{name}\t{port}\t{url_for(host, port)}")
            return 0

        name = args.name or default_worktree_name(Path.cwd())
        if args.command == "release":
            with locked_claims(state_file) as claims:
                released = claims.pop(validate_name(name), None)
                if released is not None:
                    write_claims(state_file, claims)
            print(
                f"released {name}" if released is not None else f"not claimed: {name}"
            )
            return 0

        with locked_claims(state_file) as claims:
            port, changed = allocate_port(name, claims, start, end)
            if changed:
                write_claims(state_file, claims)
        url = url_for(host, port)
        if args.command == "claim":
            print(url)
        else:
            exports = {
                "PORT": str(port),
                "DEV_URL": url,
                "HOST": "0.0.0.0",
                "UVICORN_HOST": "0.0.0.0",
                "UVICORN_PORT": str(port),
                "FLASK_RUN_HOST": "0.0.0.0",
                "FLASK_RUN_PORT": str(port),
                "VITE_PORT": str(port),
            }
            for key, value in exports.items():
                print(f"export {key}={shlex.quote(value)}")
        return 0
    except DevportError as exc:
        print(f"devport: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
