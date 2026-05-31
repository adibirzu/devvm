#!/usr/bin/env python3
"""project-status — per-project git state joined with the agents working on it.

Runs as root on the VM (systemd timer). For every project a developer is actively
coding in (discovered from agentctl sessions, plus git repos under the shared
workspace), it reports branch, clean/dirty, ahead/behind, and last commit, and
lists the agents attached to that project. Writes projects.json for the dashboard.

Standard-library only. Git-output parsers + the merge are unit-tested; the git and
filesystem calls are thin best-effort IO so one bad repo never breaks the board.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Reuse the agentctl metadata parser so "active agents" come from one source.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.agent_status import discover_developers, parse_meta_dir  # noqa: E402


def parse_branch_header(line: str) -> Dict[str, Any]:
    """Parse the `## ...` header of `git status -b --porcelain`.

    Examples:
      "## main...origin/main [ahead 1, behind 2]" -> branch main, ahead 1, behind 2
      "## main"                                   -> branch main, no upstream
      "## HEAD (no branch)"                       -> detached
    """
    out = {"branch": "?", "upstream": None, "ahead": 0, "behind": 0, "detached": False}
    if not line.startswith("## "):
        return out
    body = line[3:].strip()
    if body.startswith("HEAD (no branch)"):
        out["detached"] = True
        out["branch"] = "(detached)"
        return out
    track = ""
    if " [" in body:
        body, track = body.split(" [", 1)
        track = track.rstrip("]")
    if "..." in body:
        out["branch"], out["upstream"] = body.split("...", 1)
    else:
        out["branch"] = body
    for part in track.split(","):
        part = part.strip()
        if part.startswith("ahead "):
            out["ahead"] = int(part[6:] or 0)
        elif part.startswith("behind "):
            out["behind"] = int(part[7:] or 0)
    return out


def parse_status(text: str) -> Dict[str, Any]:
    """Parse full `git status -b --porcelain` output into a compact state dict."""
    lines = text.splitlines()
    state = {
        "branch": "?",
        "ahead": 0,
        "behind": 0,
        "dirty": 0,
        "untracked": 0,
        "clean": True,
    }
    if not lines:
        return state
    state.update(parse_branch_header(lines[0]))
    for ln in lines[1:]:
        if ln.startswith("??"):
            state["untracked"] += 1
        elif ln.strip():
            state["dirty"] += 1
    state["clean"] = state["dirty"] == 0 and state["untracked"] == 0
    return state


def parse_last_commit(text: str) -> Dict[str, str]:
    """Parse `git log -1 --format=%h%x00%s%x00%cr` (NUL-separated)."""
    parts = text.strip().split("\x00")
    if len(parts) < 3 or not parts[0]:
        return {"hash": "", "subject": "", "when": ""}
    return {"hash": parts[0], "subject": parts[1], "when": parts[2]}


def merge_projects(
    sessions_by_dir: Dict[str, List[Dict[str, str]]],
    git_states: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Join per-directory agent sessions with per-directory git state.

    sessions_by_dir: {dir: [{user, agent, project, state}, ...]}
    git_states:      {dir: {...status..., "last_commit": {...}}}
    """
    projects = []
    for d in sorted(set(sessions_by_dir) | set(git_states)):
        agents = sessions_by_dir.get(d, [])
        git = git_states.get(d)
        name = agents[0]["project"] if agents else Path(d).name
        projects.append(
            {
                "project": name,
                "dir": d,
                "git": git,  # None if not a git repo
                "active_agents": [
                    {
                        "user": a.get("user", "?"),
                        "agent": a.get("agent", "?"),
                        "state": a.get("state", "?"),
                    }
                    for a in agents
                ],
            }
        )
    return projects


# ── Thin best-effort IO (not unit-tested) ────────────────────────────────────


def _run_git(owner: str, repo: str, args: List[str]) -> Optional[str]:
    try:
        r = subprocess.run(
            ["runuser", "-u", owner, "--", "git", "-C", repo, *args],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def git_state(repo: str, owner: str) -> Optional[Dict[str, Any]]:
    if not (Path(repo) / ".git").exists():
        return None
    status = _run_git(owner, repo, ["status", "-b", "--porcelain"])
    if status is None:
        return None
    state = parse_status(status)
    log = _run_git(owner, repo, ["log", "-1", "--format=%h%x00%s%x00%cr"]) or ""
    state["last_commit"] = parse_last_commit(log)
    return state


def _owner_of(path: Path) -> str:
    try:
        return path.owner()
    except (KeyError, OSError):
        return "root"


def build(developers: List[str], home_root: Path) -> Dict[str, Any]:
    sessions_by_dir: Dict[str, List[Dict[str, str]]] = {}
    for user in developers:
        for meta in parse_meta_dir(home_root / user / ".agentctl" / "meta"):
            d = meta.get("dir", "")
            if d:
                sessions_by_dir.setdefault(d, []).append({**meta, "user": user})
    git_states: Dict[str, Dict[str, Any]] = {}
    for d in sessions_by_dir:
        owner = _owner_of(Path(d)) if Path(d).exists() else "root"
        gs = git_state(d, owner)
        if gs is not None:
            git_states[d] = gs
    projects = merge_projects(sessions_by_dir, git_states)
    totals = {
        "projects": len(projects),
        "dirty": sum(1 for p in projects if p["git"] and not p["git"]["clean"]),
        "with_agents": sum(1 for p in projects if p["active_agents"]),
    }
    return {"projects": projects, "totals": totals}


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Aggregate per-project git state + active agents."
    )
    p.add_argument("--developers", default="")
    p.add_argument("--home-root", default="/home")
    p.add_argument("--out", default="")
    args = p.parse_args(argv)
    home_root = Path(args.home_root)
    devs = [d for d in args.developers.split(",") if d] or discover_developers(
        home_root
    )
    board = build(devs, home_root)
    out = json.dumps(board, indent=2)
    if args.out:
        Path(args.out).write_text(out + "\n", encoding="utf-8")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
