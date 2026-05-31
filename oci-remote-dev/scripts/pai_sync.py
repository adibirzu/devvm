#!/usr/bin/env python3
"""pai-sync — carry PAI/Obi knowledge across the principal's own devices.

Sensitivity-split source of truth:

  * code               -> normal GitHub repos (not this tool's concern)
  * skills / Algorithm -> the PAI repo (shareable, synced normally)
  * personal MEMORY +   -> a SEPARATE, PRIVATE repo, *age-encrypted at rest*, so the
    USER context           same Obi knowledge follows you to every Mac/Ubuntu/Windows
                           box without personal data ever sitting in plaintext in the
                           cloud OR on a shared VM disk.

Works fully offline today: with no remote configured, ``push`` still commits the
encrypted blobs to a local git repo and ``status`` reports "no remote". Point
``PAI_MEMORY_REPO`` at a private GitHub remote later and the same commands sync to
the cloud — config only, no code change.

Encryption uses the ``age`` binary (audited, standard) with recipient public keys.
Each device holds its own age identity; the repo only ever holds ciphertext.

Standard-library only. The pure functions (path planning, recipient checks,
remote detection) are unit-tested without touching git or age.
"""

from __future__ import annotations

import argparse
import os
import subprocess  # noqa: S404 (calls to git/age are intentional, args are not shell-interpolated)
import sys
from pathlib import Path
from typing import List, Optional

# Paths inside ~/.claude/PAI that are PERSONAL and must be encrypted before they
# leave the owning home directory.
SENSITIVE_SUBDIRS = ("MEMORY", "USER")


def pai_dir() -> Path:
    """Resolve the PAI directory (default ~/.claude/PAI)."""
    return Path(os.environ.get("PAI_DIR", str(Path.home() / ".claude" / "PAI"))).expanduser()


def memory_repo() -> Path:
    """Resolve the private encrypted-MEMORY repo working dir (default ~/.pai-memory)."""
    return Path(os.environ.get("PAI_MEMORY_REPO_DIR", str(Path.home() / ".pai-memory"))).expanduser()


def sensitive_paths(base: Path) -> List[Path]:
    """The personal subdirectories under a PAI dir that must be encrypted."""
    return [base / name for name in SENSITIVE_SUBDIRS]


def parse_recipients(raw: str) -> List[str]:
    """Parse PAI_AGE_RECIPIENTS — comma/space/newline separated age public keys."""
    if not raw:
        return []
    parts: List[str] = []
    for chunk in raw.replace("\n", ",").replace(" ", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            parts.append(chunk)
    return parts


def require_recipients(recipients: List[str]) -> None:
    """Refuse to proceed when encryption is required but no recipients are set.

    This is the guard behind ISC-17: we never silently fall back to plaintext.
    """
    if not recipients:
        raise SystemExit(
            "error: encryption is required but PAI_AGE_RECIPIENTS is empty.\n"
            "Set it to one or more age public keys (age1...) — one per device.\n"
            "Generate a device identity with: age-keygen -o ~/.config/pai/age.key"
        )


def is_within(path: Path, root: Path) -> bool:
    """True iff ``path`` is inside ``root`` (used to prove no plaintext escapes home)."""
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def encrypted_target(src: Path, repo: Path, base: Path) -> Path:
    """Where the encrypted archive for a sensitive subdir lands inside the repo.

    e.g. MEMORY -> <repo>/encrypted/MEMORY.tar.age — always *inside* the repo,
    never alongside the plaintext source.
    """
    rel = src.name
    return repo / "encrypted" / f"{rel}.tar.age"


def build_age_encrypt_cmd(recipients: List[str], outfile: Path) -> List[str]:
    """argv for `age` encrypting stdin → outfile for the given recipients."""
    cmd = ["age"]
    for r in recipients:
        cmd += ["-r", r]
    cmd += ["-o", str(outfile)]
    return cmd


def build_age_decrypt_cmd(identity: Path, infile: Path) -> List[str]:
    """argv for `age` decrypting infile → stdout using an identity file."""
    return ["age", "-d", "-i", str(identity), str(infile)]


def has_remote(repo: Path) -> bool:
    """True iff the git repo has at least one remote configured."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "remote"],
            capture_output=True, text=True, check=False,
        )
        return bool(out.stdout.strip())
    except FileNotFoundError:
        return False


def remote_status(repo: Path) -> str:
    """Human-readable remote status ('no remote' when none) — backs ISC-14."""
    if not (repo / ".git").exists():
        return "not a git repo (run: pai-sync init)"
    return "remote configured" if has_remote(repo) else "no remote (local-only mode)"


# --- subcommands -----------------------------------------------------------

def _run(cmd: List[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)  # noqa: S603 (argv list, no shell)


def cmd_init(args: argparse.Namespace) -> int:
    repo = memory_repo()
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "encrypted").mkdir(exist_ok=True)
    if not (repo / ".git").exists():
        _run(["git", "-C", str(repo), "init", "-q"])
    os.chmod(repo, 0o700)
    print(f"initialized encrypted MEMORY repo at {repo} ({remote_status(repo)})")
    print("next: set PAI_AGE_RECIPIENTS, then `pai-sync push`. Add a remote later with:")
    print(f"  git -C {repo} remote add origin git@github.com:<you>/pai-memory.git")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    base, repo = pai_dir(), memory_repo()
    print(f"PAI dir       : {base}")
    print(f"MEMORY repo   : {repo}")
    print(f"remote        : {remote_status(repo)}")
    recips = parse_recipients(os.environ.get("PAI_AGE_RECIPIENTS", ""))
    print(f"age recipients: {len(recips)} configured")
    for src in sensitive_paths(base):
        mark = "✓" if src.exists() else "—"
        print(f"  {mark} {src.name}  (encrypts to {encrypted_target(src, repo, base)})")
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    base, repo = pai_dir(), memory_repo()
    recips = parse_recipients(os.environ.get("PAI_AGE_RECIPIENTS", ""))
    require_recipients(recips)  # ISC-17: hard refusal, never plaintext
    if not (repo / ".git").exists():
        raise SystemExit("error: MEMORY repo not initialized — run `pai-sync init` first.")
    (repo / "encrypted").mkdir(exist_ok=True)
    encrypted_any = False
    for src in sensitive_paths(base):
        if not src.exists():
            continue
        out = encrypted_target(src, repo, base)
        # ISC-16: the encrypted blob lands INSIDE the repo, never beside plaintext.
        # Explicit raise (not assert) — this is a security boundary and must hold
        # even under `python -O`, which strips asserts.
        if not is_within(out, repo):
            raise SystemExit(f"refusing: encrypted target escaped the repo: {out}")
        tar = subprocess.Popen(
            ["tar", "-C", str(src.parent), "-cf", "-", src.name],
            stdout=subprocess.PIPE,
        )
        with open(out, "wb") as fh:
            age = subprocess.Popen(build_age_encrypt_cmd(recips, out), stdin=tar.stdout, stdout=fh)
            tar.stdout.close()
            age.communicate()
            if age.returncode != 0:
                raise SystemExit(f"error: age encryption failed for {src.name}")
        encrypted_any = True
        print(f"encrypted {src.name} → {out.name}")
    if not encrypted_any:
        print("nothing to encrypt (no MEMORY/USER present).")
        return 0
    _run(["git", "-C", str(repo), "add", "encrypted"])
    msg = args.message or "pai-sync: update encrypted MEMORY"
    commit = subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", msg], check=False)
    if commit.returncode != 0:
        print("no changes to commit.")
    if has_remote(repo):
        _run(["git", "-C", str(repo), "push", "-q", "origin", "HEAD"])
        print("pushed to remote.")
    else:
        print("committed locally (no remote configured — local-only mode).")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    base, repo = pai_dir(), memory_repo()
    identity = Path(os.environ.get("PAI_AGE_IDENTITY", str(Path.home() / ".config" / "pai" / "age.key"))).expanduser()
    if not (repo / ".git").exists():
        raise SystemExit("error: MEMORY repo not initialized — run `pai-sync init` first.")
    if has_remote(repo):
        _run(["git", "-C", str(repo), "pull", "-q", "--ff-only", "origin", "HEAD"])
    if not identity.exists():
        raise SystemExit(f"error: age identity not found at {identity} (set PAI_AGE_IDENTITY).")
    for src in sensitive_paths(base):
        blob = encrypted_target(src, repo, base)
        if not blob.exists():
            continue
        age = subprocess.Popen(build_age_decrypt_cmd(identity, blob), stdout=subprocess.PIPE)
        untar = subprocess.Popen(["tar", "-C", str(src.parent), "-xf", "-"], stdin=age.stdout)
        age.stdout.close()
        untar.communicate()
        if untar.returncode != 0:
            raise SystemExit(f"error: decrypt/extract failed for {blob.name}")
        print(f"restored {src.name} from {blob.name}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="pai-sync", description="Sync PAI MEMORY across devices (age-encrypted).")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Initialize the local encrypted MEMORY repo")
    sub.add_parser("status", help="Show sync configuration and state")
    p_push = sub.add_parser("push", help="Encrypt + commit (+ push if a remote exists)")
    p_push.add_argument("-m", "--message", default="", help="commit message")
    sub.add_parser("pull", help="Pull (if remote) + decrypt into the PAI dir")

    args = parser.parse_args(argv)
    handlers = {"init": cmd_init, "status": cmd_status, "push": cmd_push, "pull": cmd_pull}
    try:
        return handlers[args.command](args)
    except subprocess.CalledProcessError as exc:
        print(f"error: command failed: {' '.join(exc.cmd)} (exit {exc.returncode})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
