#!/usr/bin/env python3
"""apply-pending — materialize the control-plane's queued developer changes.

The control-plane API (`control_plane.py`) only ever *queues* account changes to
`/etc/agent-os/pending-changes.jsonl`; nothing is executed by the web service.
This is the other half: it reads that queue, validates every entry, and applies
each one by running `ansible/apply_changes.yml`, which includes the very same
`developer_account_tasks.yml` → `user_tasks.yml` that a from-scratch deploy runs.
A developer added at runtime is therefore provisioned identically to one created
by `deploy.sh` — there is no parallel, hand-rolled useradd path.

Processed entries leave the queue and land in a durable append-only audit log
(`applied-changes.jsonl`) with one of:

  applied         Ansible ran and succeeded
  failed          Ansible returned non-zero — the entry STAYS queued for retry
  rejected        the entry can never succeed (validation) — dropped from the queue
  superseded      a later entry for the same developer won — dropped
  already_applied its change-id is already in the audit log — dropped
  malformed       the queue line was not JSON — dropped

Re-runs are therefore idempotent (an empty queue is a no-op; Ansible itself is
idempotent) and partial failures are safe (only failures are retried). Both
`--dry-run` (print the plan) and `--check` (ansible-playbook check mode) are
previews: neither touches the audit log, the queue, or the roster.

Removals disable an account and PRESERVE its home directory. `--purge` is the
explicit, destructive opt-in that also deletes the account and `/home/<name>`;
it is never inferred from a queue entry.

Where to run it: on the Ansible controller (the machine that ran `deploy.sh`),
which has `ansible-playbook` and `configs/hosts.ini`. Point `--queue` at the
queue — fetch it from the VM first, e.g.
`scp <vm>:/etc/agent-os/pending-changes.jsonl configs/`. If you run it on the VM
itself, use the real path plus `--inventory 'localhost,' --connection local`.

Pure planning/validation/idempotency logic is unit-tested; subprocess and file IO
are thin edges (`make_runner`, `main`).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:  # run as a script: scripts/ is on sys.path
    from control_plane import validate_developer_name, validate_developer_request
except ImportError:  # imported as scripts.apply_pending (tests, tooling)
    from scripts.control_plane import (  # type: ignore[no-redef]
        validate_developer_name,
        validate_developer_request,
    )

PROJECT_DIR = Path(__file__).resolve().parent.parent
QUEUE_FILE = Path("/etc/agent-os/pending-changes.jsonl")
AUDIT_FILE = Path("/etc/agent-os/applied-changes.jsonl")
APPLY_PLAYBOOK = PROJECT_DIR / "ansible" / "apply_changes.yml"
BASE_VARS_FILE = PROJECT_DIR / "configs" / "ansible_vars.json"
INVENTORY_FILE = PROJECT_DIR / "configs" / "hosts.ini"

DEFAULT_CODE_SERVER_PORT = 8443
DEFAULT_WG_NETWORK = "10.200.200.0/24"
_IPV4_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")

# Statuses that retire an entry from the queue. Anything else (i.e. "failed")
# is kept so the next run retries it.
TERMINAL = ("applied", "rejected", "superseded", "already_applied", "malformed")

Runner = Callable[[Dict[str, Any]], Tuple[int, str]]


# ── Pure helpers ─────────────────────────────────────────────────────────────


def change_id(change: Dict[str, Any]) -> str:
    """Stable content-addressed id, so an entry is applied at most once."""
    canonical = json.dumps(change, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def parse_queue(text: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Split a JSONL queue into decoded entries and unparseable raw lines."""
    entries: List[Dict[str, Any]] = []
    malformed: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            malformed.append(line)
            continue
        if isinstance(obj, dict):
            entries.append(obj)
        else:
            malformed.append(line)
    return entries, malformed


def applied_ids(audit_text: str) -> set:
    """Change-ids already retired by an earlier run (crash-safe replay guard)."""
    done = set()
    for line in audit_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and rec.get("status") in TERMINAL and rec.get("id"):
            done.add(rec["id"])
    return done


def _valid_port(value: Any) -> bool:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return False
    return 1024 <= port <= 65535


def _valid_ipv4(value: Any) -> bool:
    m = _IPV4_RE.fullmatch(str(value))
    return bool(m) and all(0 <= int(o) <= 255 for o in m.groups())


def validate_change(change: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate one queue entry. Reuses the control-plane's own validators."""
    op = change.get("op")
    if op not in ("add", "remove"):
        return False, [f"unknown op {op!r} (expected 'add' or 'remove')"]
    if op == "remove":
        if not validate_developer_name(change.get("name", "")):
            return False, ["invalid developer name"]
        return True, []
    errs = list(validate_developer_request(change)[1])
    if change.get("code_server_port") is not None and not _valid_port(
        change["code_server_port"]
    ):
        errs.append("code_server_port must be an integer in 1024..65535")
    if change.get("wg_ip") is not None and not _valid_ipv4(change["wg_ip"]):
        errs.append("wg_ip must be a dotted-quad IPv4 address")
    return (not errs), errs


def next_code_server_port(existing: Sequence[Dict[str, Any]]) -> int:
    ports = [
        int(d["code_server_port"])
        for d in existing
        if _valid_port(d.get("code_server_port"))
    ]
    return max(ports) + 1 if ports else DEFAULT_CODE_SERVER_PORT


def next_wg_ip(
    existing: Sequence[Dict[str, Any]], network: str = DEFAULT_WG_NETWORK
) -> str:
    """Next free host address in the WireGuard /24 (.1 is the server itself)."""
    prefix = ".".join(str(network).split("/")[0].split(".")[:3]) + "."
    hosts = [1]
    for dev in existing:
        ip = str(dev.get("wg_ip") or "")
        if _valid_ipv4(ip) and ip.startswith(prefix):
            hosts.append(int(ip.rsplit(".", 1)[1]))
    return f"{prefix}{min(max(hosts) + 1, 254)}"


def developer_vars(
    change: Dict[str, Any],
    existing: Sequence[Dict[str, Any]],
    network: str = DEFAULT_WG_NETWORK,
) -> Dict[str, Any]:
    """Turn an 'add' entry into a `developers[]` var, filling gaps as deploy does.

    Git identity defaults mirror deploy_multicloud._git_identity: the GitHub
    noreply address, so no personal email is ever required or leaked.
    """
    name = str(change["name"])
    gh_user = str(change.get("github_user") or name)
    port = change.get("code_server_port")
    wg_ip = change.get("wg_ip")
    return {
        "name": name,
        "ssh_key": str(change.get("ssh_key") or ""),
        "code_server_port": (
            int(port) if _valid_port(port) else next_code_server_port(existing)
        ),
        "wg_ip": str(wg_ip) if _valid_ipv4(wg_ip) else next_wg_ip(existing, network),
        "git_name": str(change.get("git_name") or gh_user),
        "git_email": str(
            change.get("git_email") or f"{gh_user}@users.noreply.github.com"
        ),
        "github_user": gh_user,
    }


def plan_changes(
    entries: Sequence[Dict[str, Any]],
    done_ids: Optional[set] = None,
    existing: Optional[Sequence[Dict[str, Any]]] = None,
    network: str = DEFAULT_WG_NETWORK,
) -> List[Dict[str, Any]]:
    """Decide, without touching anything, what happens to every queue entry.

    Returns one action per entry, in queue order. Exactly the last valid entry
    per developer is left "ready"; earlier ones are "superseded" (queue an add
    then a remove and only the remove runs). Roster allocations are threaded
    through the batch so two adds in one run never collide on a port or VPN IP.
    """
    done_ids = done_ids or set()
    roster = [dict(d) for d in (existing or [])]
    actions: List[Dict[str, Any]] = []

    for entry in entries:
        cid = change_id(entry)
        action: Dict[str, Any] = {
            "id": cid,
            "change": entry,
            "op": entry.get("op"),
            "name": entry.get("name"),
        }
        if cid in done_ids:
            action["status"] = "already_applied"
            action["reason"] = "change-id already present in the audit log"
        else:
            ok, errs = validate_change(entry)
            action["status"] = "ready" if ok else "rejected"
            if not ok:
                action["reason"] = "; ".join(errs)
        actions.append(action)

    # Last writer wins per developer.
    winner: Dict[str, str] = {}
    for action in actions:
        if action["status"] == "ready":
            winner[str(action["name"])] = action["id"]
    for action in actions:
        if action["status"] == "ready" and winner[str(action["name"])] != action["id"]:
            action["status"] = "superseded"
            action["reason"] = f"superseded by {winner[str(action['name'])]}"

    # Allocate ports/IPs only for the adds that will actually run.
    for action in actions:
        if action["status"] != "ready":
            continue
        if action["op"] == "add":
            dev = developer_vars(action["change"], roster, network)
            action["dev"] = dev
            roster = [d for d in roster if d.get("name") != dev["name"]] + [dev]
        else:
            roster = [d for d in roster if d.get("name") != action["name"]]
    return actions


def extra_vars_for(
    action: Dict[str, Any],
    base_vars: Optional[Dict[str, Any]] = None,
    purge: bool = False,
) -> Dict[str, Any]:
    """Build the --extra-vars payload for one action.

    The deploy's own `configs/ansible_vars.json` is the base, minus its
    `developers` roster, so every install_* toggle and network var matches the
    deploy that built the VM.
    """
    out = {k: v for k, v in (base_vars or {}).items() if k != "developers"}
    if action["op"] == "add":
        out["apply_add"] = [action["dev"]]
        out["apply_remove"] = []
        out["purge_removed"] = False
    else:
        out["apply_add"] = []
        out["apply_remove"] = [action["name"]]
        out["purge_removed"] = bool(purge)
    return out


def ansible_command(
    vars_file: str,
    playbook: str = str(APPLY_PLAYBOOK),
    inventory: str = str(INVENTORY_FILE),
    limit: Optional[str] = None,
    connection: Optional[str] = None,
    check: bool = False,
) -> List[str]:
    cmd = ["ansible-playbook", "-i", inventory]
    if connection:
        cmd += ["--connection", connection]
    if limit:
        cmd += ["--limit", limit]
    if check:
        cmd.append("--check")
    cmd += ["--extra-vars", f"@{vars_file}", playbook]
    return cmd


def merge_roster(
    base_vars: Dict[str, Any], actions: Sequence[Dict[str, Any]]
) -> Dict[str, Any]:
    """Fold successfully applied changes into the deploy's developers roster.

    Keeps port/VPN-IP allocation monotonic across separate apply runs. A
    from-scratch redeploy still rebuilds this from `.env`, so runtime-added
    developers must also be written there — see the README.
    """
    out = dict(base_vars)
    roster = [dict(d) for d in out.get("developers") or []]
    for action in actions:
        if action.get("status") != "applied":
            continue
        roster = [d for d in roster if d.get("name") != action["name"]]
        if action["op"] == "add":
            # Same shape the deployer writes: the roster records allocations, not
            # key material (the key lives in the account's authorized_keys).
            roster.append({k: v for k, v in action["dev"].items() if k != "ssh_key"})
    out["developers"] = roster
    return out


def remaining_queue(actions: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Entries to keep queued: only the ones that failed and can be retried."""
    return [a["change"] for a in actions if a.get("status") not in TERMINAL]


def merge_queue_lines(
    kept: Sequence[Dict[str, Any]], current_text: str, processed_ids: set
) -> List[str]:
    """Serialized queue lines to write back after a run.

    The control-plane may append to the queue while Ansible is running, so the
    rewrite must not clobber the file with the pre-run snapshot: any line whose
    change-id was not part of this batch is preserved verbatim.
    """
    lines = [json.dumps(c) for c in kept]
    seen = {change_id(c) for c in kept} | set(processed_ids)
    entries, malformed = parse_queue(current_text)
    for entry in entries:
        cid = change_id(entry)
        if cid not in seen:
            lines.append(json.dumps(entry))
            seen.add(cid)
    lines.extend(raw for raw in malformed if change_id({"raw": raw}) not in seen)
    return lines


def needs_allocation(actions: Sequence[Dict[str, Any]]) -> bool:
    """True when a ready add left port or VPN IP for the roster to allocate."""
    return any(
        a["status"] == "ready"
        and a["op"] == "add"
        and not (
            _valid_port(a["change"].get("code_server_port"))
            and _valid_ipv4(a["change"].get("wg_ip"))
        )
        for a in actions
    )


def audit_records(actions: Sequence[Dict[str, Any]], now: str) -> List[Dict[str, Any]]:
    records = []
    for action in actions:
        rec = {
            "ts": now,
            "id": action["id"],
            "status": action["status"],
            "op": action.get("op"),
            "name": action.get("name"),
            "change": action["change"],
        }
        for key in ("reason", "rc", "output"):
            if action.get(key) is not None:
                rec[key] = action[key]
        records.append(rec)
    return records


def execute_plan(
    actions: Sequence[Dict[str, Any]],
    runner: Runner,
    base_vars: Optional[Dict[str, Any]] = None,
    purge: bool = False,
) -> List[Dict[str, Any]]:
    """Run every ready action, one Ansible invocation each.

    One invocation per change is deliberate: a failure for one developer cannot
    take the rest of the batch down with it.
    """
    for action in actions:
        if action["status"] != "ready":
            continue
        rc, output = runner(extra_vars_for(action, base_vars, purge))
        action["rc"] = rc
        action["output"] = output[-2000:] if output else ""
        if rc == 0:
            action["status"] = "applied"
        else:
            action["status"] = "failed"
            action["reason"] = f"ansible-playbook exited {rc}"
    return list(actions)


def summarize(actions: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for action in actions:
        counts[action["status"]] = counts.get(action["status"], 0) + 1
    return counts


# ── IO edges ─────────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _unwritable(path: Path, atomic: bool = False) -> Optional[str]:
    """Why a later write to `path` would fail, or None if it looks writable.

    `atomic` targets are replaced via a sibling temp file, so their directory
    must be writable even when the file itself already is.
    """
    probe = path
    while not probe.exists():
        if probe.parent == probe:
            return None
        probe = probe.parent
    if probe == path:
        if path.is_dir():
            return f"{path} is a directory"
        if not os.access(path, os.W_OK):
            return f"{path} is not writable"
        if atomic and not os.access(path.parent, os.W_OK | os.X_OK):
            return f"directory {path.parent} is not writable"
        return None
    if not probe.is_dir():
        return f"{probe} is not a directory"
    if not os.access(probe, os.W_OK | os.X_OK):
        return f"directory {probe} is not writable"
    return None


def _append_jsonl(path: Path, records: Sequence[Dict[str, Any]]) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def make_runner(
    playbook: str,
    inventory: str,
    limit: Optional[str],
    connection: Optional[str],
    check: bool,
    verbose: bool,
) -> Runner:
    """Real execution boundary: write extra-vars, shell out to ansible-playbook."""

    def run(extra_vars: Dict[str, Any]) -> Tuple[int, str]:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", prefix="apply-pending-", delete=False
        ) as fh:
            json.dump(extra_vars, fh)
            vars_path = fh.name
        cmd = ansible_command(vars_path, playbook, inventory, limit, connection, check)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if verbose:
                sys.stderr.write(proc.stdout)
            return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
        except (OSError, subprocess.SubprocessError) as exc:
            return 127, f"could not run ansible-playbook: {exc}"
        finally:
            Path(vars_path).unlink(missing_ok=True)

    return run


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Apply the control-plane's queued developer changes via Ansible.",
    )
    p.add_argument("--queue", default=str(QUEUE_FILE), help="pending-changes.jsonl")
    p.add_argument("--audit", default=str(AUDIT_FILE), help="durable audit log (JSONL)")
    p.add_argument("--playbook", default=str(APPLY_PLAYBOOK))
    p.add_argument("--inventory", default=str(INVENTORY_FILE))
    p.add_argument("--limit", default=None, help="ansible --limit host pattern")
    p.add_argument(
        "--connection", default=None, help="ansible --connection (e.g. local on the VM)"
    )
    p.add_argument(
        "--base-vars",
        default=str(BASE_VARS_FILE),
        help="the deploy's ansible_vars.json — reused so toggles match the VM",
    )
    p.add_argument(
        "--purge",
        action="store_true",
        help="DESTRUCTIVE: removals also delete the account AND /home/<name>. "
        "Without it, removals only disable the account and keep every file.",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="preview: pass --check to ansible-playbook and leave the audit "
        "log, queue and roster untouched",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan and the exact Ansible vars; change nothing",
    )
    p.add_argument("--json", action="store_true", help="emit the plan/result as JSON")
    p.add_argument("-v", "--verbose", action="store_true", help="stream Ansible output")
    args = p.parse_args(argv)

    queue_path, audit_path = Path(args.queue), Path(args.audit)
    if not queue_path.exists():
        print(f"no queue at {queue_path} — nothing to apply")
        return 0

    entries, malformed = parse_queue(_read_text(queue_path))
    base_vars = _read_json(Path(args.base_vars))
    network = str(base_vars.get("wg_network") or DEFAULT_WG_NETWORK)
    actions = plan_changes(
        entries,
        applied_ids(_read_text(audit_path)),
        base_vars.get("developers"),
        network,
    )

    # A missing/unreadable roster would silently restart port/VPN-IP allocation
    # at the defaults and collide with the developers the deploy already placed.
    base_vars_gap = (
        f"--base-vars {args.base_vars} is missing or unreadable, and a queued add "
        f"needs a code_server_port/wg_ip allocation — allocating against an empty "
        f"roster would restart at {DEFAULT_CODE_SERVER_PORT}/.2 and collide with "
        "existing developers. Copy the deploy's configs/ansible_vars.json here "
        "(scp it from the controller) or set explicit code_server_port and wg_ip "
        "on the queue entry."
        if needs_allocation(actions) and not base_vars
        else None
    )

    if args.dry_run:
        if base_vars_gap:
            print(f"WARNING: {base_vars_gap}", file=sys.stderr)
        payload = [
            {
                "id": a["id"],
                "status": a["status"],
                "op": a.get("op"),
                "name": a.get("name"),
                "reason": a.get("reason"),
                "extra_vars": (
                    extra_vars_for(a, base_vars, args.purge)
                    if a["status"] == "ready"
                    else None
                ),
                "command": (
                    ansible_command(
                        "<extra-vars>.json",
                        args.playbook,
                        args.inventory,
                        args.limit,
                        args.connection,
                        args.check,
                    )
                    if a["status"] == "ready"
                    else None
                ),
            }
            for a in actions
        ]
        print(json.dumps({"dry_run": True, "plan": payload}, indent=2))
        if malformed:
            print(f"{len(malformed)} malformed queue line(s) would be dropped")
        return 0

    if base_vars_gap:
        print(base_vars_gap, file=sys.stderr)
        return 2

    # Every real run appends to the audit log and rewrites the queue (and, when
    # something lands, the roster) — prove those writes can succeed BEFORE
    # Ansible materializes anything, so a permissions problem is a clean error
    # instead of a crash that loses the audit trail after the fact.
    if not args.check:
        preflight = [
            ("--audit", _unwritable(audit_path)),
            ("--queue", _unwritable(queue_path, atomic=True)),
        ]
        if Path(args.base_vars).exists() and any(
            a["status"] == "ready" for a in actions
        ):
            preflight.append(
                ("--base-vars", _unwritable(Path(args.base_vars), atomic=True))
            )
        problems = [(flag, why) for flag, why in preflight if why]
        if problems:
            for flag, why in problems:
                print(f"cannot write the {flag} target: {why}", file=sys.stderr)
            print(
                "point the flag(s) at writable paths (or re-run with enough "
                "privilege) — nothing was applied",
                file=sys.stderr,
            )
            return 2

    if not any(a["status"] == "ready" for a in actions):
        print(f"nothing to apply ({len(actions)} queued entr(y|ies), none ready)")
    elif shutil.which("ansible-playbook") is None:
        print("ansible-playbook not found in PATH — install Ansible", file=sys.stderr)
        return 2
    # A missing inventory would fail every change with an opaque Ansible error and
    # leave the whole queue in "failed" — say so up front instead.
    elif not Path(args.inventory).exists() and not args.inventory.endswith(","):
        print(
            f"inventory {args.inventory} not found — run a deploy first, or pass "
            "--inventory (on the VM itself: --inventory 'localhost,' --connection local)",
            file=sys.stderr,
        )
        return 2

    actions = execute_plan(
        actions,
        make_runner(
            args.playbook,
            args.inventory,
            args.limit,
            args.connection,
            args.check,
            args.verbose,
        ),
        base_vars,
        args.purge,
    )

    now = _now()
    records = audit_records(actions, now)
    records += [
        {
            "ts": now,
            "id": change_id({"raw": raw}),
            "status": "malformed",
            "raw": raw[:500],
        }
        for raw in malformed
    ]

    kept = remaining_queue(actions)
    # --check is a preview: ansible-playbook ran in check mode, so nothing was
    # materialized — recording the batch as applied would retire the queue for
    # changes that never happened.
    if not args.check:
        _append_jsonl(audit_path, records)

        processed = {a["id"] for a in actions} | {
            change_id({"raw": raw}) for raw in malformed
        }
        lines = merge_queue_lines(kept, _read_text(queue_path), processed)
        _write_atomic(queue_path, "".join(line + "\n" for line in lines))

        # Only rewrite the deploy's roster when something actually landed, so a
        # no-op run never churns a file the deployer owns.
        if (
            any(a["status"] == "applied" for a in actions)
            and Path(args.base_vars).exists()
        ):
            _write_atomic(
                Path(args.base_vars),
                json.dumps(merge_roster(base_vars, actions), indent=2),
            )

    counts = summarize(actions)
    if args.json:
        payload = {"summary": counts, "actions": records}
        if args.check:
            payload["check"] = True
        print(json.dumps(payload, indent=2))
    elif args.check:
        print(
            f"apply-pending --check: {counts or {'queued': 0}} — preview only; "
            "audit, queue and roster untouched"
        )
    else:
        print(f"apply-pending: {counts or {'queued': 0}} — audit: {audit_path}")
        for a in actions:
            if a["status"] in ("failed", "rejected"):
                print(
                    f"  {a['status']}: {a.get('op')} {a.get('name')} — {a.get('reason')}"
                )
        if kept:
            print(f"{len(kept)} entr(y|ies) kept queued for retry")
    return 1 if counts.get("failed") else 0


if __name__ == "__main__":
    sys.exit(main())
