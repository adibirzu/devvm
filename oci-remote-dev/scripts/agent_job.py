#!/usr/bin/env python3
"""agent-job — scheduled, unattended coding-agent jobs (Phase 5).

Define a prompt + project + cadence; a per-user timer runs the agent
non-interactively when due, captures output, and records the run. Safety is
inherited: the agent runs as your UNIX user (sandboxed) through the same Claude
Code PreToolUse guardrail that blocks/holds destructive tool calls — so an
unattended job can't quietly do something dangerous.

  agent-job define <name> --project <dir> --prompt "<text>" [--agent claude] [--every <min>]
  agent-job list
  agent-job run <name>      run now (ignores schedule)
  agent-job tick            run every due job (called by the timer)
  agent-job logs <name>
  agent-job remove <name>

Standard-library only. Schedule logic is pure/tested; running the agent is IO.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

STATE_DIR = Path(os.environ.get("AGENTCTL_HOME", str(Path.home() / ".agentctl")))
JOBS_DIR = STATE_DIR / "jobs"
RUNS_FEED = STATE_DIR / "jobs.jsonl"
DEFAULT_TIMEOUT = int(os.environ.get("AGENT_JOB_TIMEOUT", "900"))

# Non-interactive invocation per agent ("-p" = print/headless mode for Claude Code).
AGENT_CMD = {
    "claude": ["claude", "-p"],
    "codex": ["codex", "exec"],
    "gemini": ["gemini", "-p"],
}


def _now_epoch() -> float:
    return datetime.datetime.now(datetime.timezone.utc).timestamp()


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(ts: str) -> float:
    try:
        return (
            datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
            .replace(tzinfo=datetime.timezone.utc)
            .timestamp()
        )
    except (ValueError, TypeError):
        return 0.0


# ── Pure schedule logic ──────────────────────────────────────────────────────


def is_due(job: Dict[str, Any], now_epoch: float) -> bool:
    """A job is due if enabled and at least interval_minutes since its last run."""
    if not job.get("enabled", True):
        return False
    interval = float(job.get("interval_minutes", 0) or 0) * 60.0
    if interval <= 0:
        return False
    last = _parse_iso(job.get("last_run", ""))
    return (now_epoch - last) >= interval


def due_jobs(jobs: List[Dict[str, Any]], now_epoch: float) -> List[Dict[str, Any]]:
    return [j for j in jobs if is_due(j, now_epoch)]


def build_agent_argv(agent: str, prompt: str) -> List[str]:
    base = AGENT_CMD.get(agent, [agent, "-p"])
    return [*base, prompt]


# ── IO ───────────────────────────────────────────────────────────────────────


def _job_path(name: str) -> Path:
    safe = "".join(c if (c.isalnum() or c in "_.-") else "_" for c in name)
    return JOBS_DIR / f"{safe}.json"


def load_jobs() -> List[Dict[str, Any]]:
    if not JOBS_DIR.is_dir():
        return []
    out = []
    for f in sorted(JOBS_DIR.glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    return out


def save_job(job: Dict[str, Any]) -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    _job_path(job["name"]).write_text(json.dumps(job, indent=2), encoding="utf-8")


def _record_run(entry: Dict[str, Any]) -> None:
    try:
        RUNS_FEED.parent.mkdir(parents=True, exist_ok=True)
        with RUNS_FEED.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({**entry, "ts": _now_iso()}) + "\n")
    except OSError:
        pass


def run_job(job: Dict[str, Any]) -> int:
    name = job["name"]
    project = job.get("project", str(Path.home()))
    if not Path(project).is_dir():
        _record_run(
            {"job": name, "status": "error", "detail": f"missing dir {project}"}
        )
        return 1
    argv = build_agent_argv(job.get("agent", "claude"), job["prompt"])
    logfile = JOBS_DIR / f"{_job_path(name).stem}.last.log"
    started = _now_epoch()
    try:
        with logfile.open("w", encoding="utf-8") as out:
            r = subprocess.run(
                argv,
                cwd=project,
                stdout=out,
                stderr=subprocess.STDOUT,
                timeout=DEFAULT_TIMEOUT,
            )
        code = r.returncode
        status = "ok" if code == 0 else "failed"
    except subprocess.TimeoutExpired:
        code, status = 124, "timeout"
    except (OSError, subprocess.SubprocessError) as e:
        code, status = 1, f"error: {e}"
    duration = round(_now_epoch() - started, 1)
    job["last_run"] = _now_iso()
    job["last_status"] = status
    save_job(job)
    _record_run(
        {
            "job": name,
            "status": status,
            "exit": code,
            "duration_s": duration,
            "log": str(logfile),
        }
    )
    # Surface completion on the board via the existing notification feed.
    try:
        subprocess.run(
            ["agent-notify", f"job '{name}' {status} ({duration}s)"],
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass
    return code


# ── Commands ─────────────────────────────────────────────────────────────────


def cmd_define(args) -> int:
    job = {
        "name": args.name,
        "agent": args.agent,
        "project": str(Path(args.project).expanduser()),
        "prompt": args.prompt,
        "interval_minutes": args.every,
        "enabled": True,
        "last_run": "",
    }
    save_job(job)
    print(
        f"defined job '{args.name}' (agent={args.agent}, every {args.every}m, project={job['project']})"
    )
    return 0


def cmd_list(args) -> int:
    jobs = load_jobs()
    if not jobs:
        print(
            '(no jobs defined — agent-job define <name> --project <dir> --prompt "...")'
        )
        return 0
    print(f"  {'NAME':<20} {'AGENT':<8} {'EVERY':>7} {'LAST RUN':<22} STATUS")
    for j in jobs:
        print(
            f"  {j['name'][:20]:<20} {j.get('agent','?'):<8} {str(j.get('interval_minutes',0))+'m':>7} "
            f"{j.get('last_run','') or '(never)':<22} {j.get('last_status','-')}"
        )
    return 0


def cmd_run(args) -> int:
    for j in load_jobs():
        if j["name"] == args.name:
            print(f"running '{args.name}'…")
            return run_job(j)
    print(f"no such job: {args.name}", file=sys.stderr)
    return 1


def cmd_tick(args) -> int:
    due = due_jobs(load_jobs(), _now_epoch())
    for j in due:
        run_job(j)
    print(f"tick: ran {len(due)} due job(s)")
    return 0


def cmd_logs(args) -> int:
    logfile = JOBS_DIR / f"{_job_path(args.name).stem}.last.log"
    print(logfile.read_text(encoding="utf-8") if logfile.exists() else "(no run yet)")
    return 0


def cmd_remove(args) -> int:
    p = _job_path(args.name)
    if p.exists():
        p.unlink()
        print(f"removed job '{args.name}'")
    else:
        print(f"no such job: {args.name}", file=sys.stderr)
        return 1
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-job", description="Scheduled unattended agent jobs."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("define")
    d.add_argument("name")
    d.add_argument("--project", required=True)
    d.add_argument("--prompt", required=True)
    d.add_argument("--agent", default="claude")
    d.add_argument(
        "--every", type=int, default=1440, help="interval in minutes (default daily)"
    )
    sub.add_parser("list")
    r = sub.add_parser("run")
    r.add_argument("name")
    sub.add_parser("tick")
    lg = sub.add_parser("logs")
    lg.add_argument("name")
    rm = sub.add_parser("remove")
    rm.add_argument("name")
    args = parser.parse_args(argv)
    return {
        "define": cmd_define,
        "list": cmd_list,
        "run": cmd_run,
        "tick": cmd_tick,
        "logs": cmd_logs,
        "remove": cmd_remove,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
