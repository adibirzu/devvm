"""Unit tests for apply-from-queue.

Every test here is pure logic. The execution boundary (`ansible-playbook`) is a
fake runner, so no test ever creates a UNIX account, touches /etc/agent-os, or
runs real provisioning — the assertions are on the exact commands and extra-vars
the real runner *would* receive.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.append(str(Path(__file__).resolve().parent.parent))

from scripts.apply_pending import (
    ansible_command,
    applied_ids,
    audit_records,
    change_id,
    developer_vars,
    execute_plan,
    extra_vars_for,
    main,
    merge_queue_lines,
    merge_roster,
    next_code_server_port,
    next_wg_ip,
    parse_queue,
    plan_changes,
    remaining_queue,
    summarize,
    validate_change,
)

try:  # PyYAML ships with Ansible and requirements.txt; only the asset fence needs it
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

GOOD_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAExample carlos@mac"


def add(name="carlos", **kw):
    return {"op": "add", "name": name, "ssh_key": GOOD_KEY, "ts": "2026-01-01T00:00:00Z", **kw}


def remove(name="carlos", **kw):
    return {"op": "remove", "name": name, "ts": "2026-01-01T00:00:01Z", **kw}


def fake_runner(rc=0, calls=None, output="ok"):
    """Stand-in for ansible-playbook: records extra-vars, never executes anything."""
    calls = calls if calls is not None else []

    def run(extra_vars):
        calls.append(extra_vars)
        return (rc(extra_vars) if callable(rc) else rc), output

    return run, calls


class TestValidation(unittest.TestCase):
    def test_add_ok(self) -> None:
        ok, errs = validate_change(add())
        self.assertTrue(ok)
        self.assertEqual(errs, [])

    def test_add_bad_name(self) -> None:
        ok, errs = validate_change(add(name="Bad Name!"))
        self.assertFalse(ok)

    def test_add_bad_key(self) -> None:
        ok, _ = validate_change(add(ssh_key="hunter2"))
        self.assertFalse(ok)

    def test_add_bad_port(self) -> None:
        ok, errs = validate_change(add(code_server_port=80))
        self.assertFalse(ok)
        self.assertIn("code_server_port", errs[0])

    def test_add_bad_wg_ip(self) -> None:
        ok, errs = validate_change(add(wg_ip="10.200.999.4"))
        self.assertFalse(ok)
        self.assertIn("wg_ip", errs[0])

    def test_add_null_optionals_are_fine(self) -> None:
        # The control-plane enqueues explicit nulls for omitted fields.
        ok, _ = validate_change(add(wg_ip=None, code_server_port=None, github_user=None))
        self.assertTrue(ok)

    def test_remove_ok(self) -> None:
        self.assertTrue(validate_change(remove("royce"))[0])

    def test_remove_needs_no_ssh_key(self) -> None:
        ok, errs = validate_change({"op": "remove", "name": "royce"})
        self.assertTrue(ok)

    def test_remove_bad_name(self) -> None:
        self.assertFalse(validate_change({"op": "remove", "name": "../root"})[0])

    def test_unknown_op(self) -> None:
        ok, errs = validate_change({"op": "nuke", "name": "royce"})
        self.assertFalse(ok)
        self.assertIn("unknown op", errs[0])


class TestQueueParsing(unittest.TestCase):
    def test_parses_and_skips_blanks(self) -> None:
        text = json.dumps(add()) + "\n\n" + json.dumps(remove("royce")) + "\n"
        entries, malformed = parse_queue(text)
        self.assertEqual([e["op"] for e in entries], ["add", "remove"])
        self.assertEqual(malformed, [])

    def test_malformed_lines_are_isolated(self) -> None:
        entries, malformed = parse_queue("{not json\n" + json.dumps(add()) + "\n[1,2]\n")
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(malformed), 2)

    def test_merge_queue_lines_preserves_entries_enqueued_mid_run(self) -> None:
        late = add("royce")
        lines = merge_queue_lines([], json.dumps(late) + "\n", {change_id(add())})
        self.assertEqual(lines, [json.dumps(late)])

    def test_merge_queue_lines_does_not_duplicate_a_kept_entry(self) -> None:
        entry = add()
        lines = merge_queue_lines([entry], json.dumps(entry) + "\n", set())
        self.assertEqual(lines, [json.dumps(entry)])

    def test_change_id_is_stable_and_content_addressed(self) -> None:
        self.assertEqual(change_id(add()), change_id(dict(reversed(list(add().items())))))
        self.assertNotEqual(change_id(add()), change_id(add(name="royce")))

    def test_applied_ids_only_counts_terminal_statuses(self) -> None:
        text = "\n".join(
            json.dumps(r)
            for r in [
                {"id": "aaa", "status": "applied"},
                {"id": "bbb", "status": "failed"},
                {"id": "ccc", "status": "rejected"},
                {"id": "ddd", "status": "superseded"},
                "not-a-dict",
            ]
        )
        self.assertEqual(applied_ids(text + "\n{bad json\n"), {"aaa", "ccc", "ddd"})


class TestAllocation(unittest.TestCase):
    def test_port_defaults_when_roster_empty(self) -> None:
        self.assertEqual(next_code_server_port([]), 8443)

    def test_port_is_one_past_the_highest(self) -> None:
        roster = [{"code_server_port": 8443}, {"code_server_port": 8445}]
        self.assertEqual(next_code_server_port(roster), 8446)

    def test_port_ignores_junk(self) -> None:
        self.assertEqual(next_code_server_port([{"code_server_port": "nope"}]), 8443)

    def test_wg_ip_defaults_after_the_server(self) -> None:
        self.assertEqual(next_wg_ip([]), "10.200.200.2")

    def test_wg_ip_is_one_past_the_highest_in_network(self) -> None:
        roster = [{"wg_ip": "10.200.200.2"}, {"wg_ip": "10.200.200.7"}]
        self.assertEqual(next_wg_ip(roster), "10.200.200.8")

    def test_wg_ip_respects_a_custom_network(self) -> None:
        self.assertEqual(next_wg_ip([], "10.9.0.0/24"), "10.9.0.2")

    def test_wg_ip_ignores_addresses_outside_the_network(self) -> None:
        self.assertEqual(next_wg_ip([{"wg_ip": "192.168.1.50"}]), "10.200.200.2")


class TestDeveloperVars(unittest.TestCase):
    def test_defaults_mirror_the_deployers_git_identity(self) -> None:
        dev = developer_vars(add(), [])
        self.assertEqual(
            dev,
            {
                "name": "carlos",
                "ssh_key": GOOD_KEY,
                "code_server_port": 8443,
                "wg_ip": "10.200.200.2",
                "git_name": "carlos",
                "git_email": "carlos@users.noreply.github.com",
                "github_user": "carlos",
            },
        )

    def test_github_user_drives_the_identity(self) -> None:
        dev = developer_vars(add(github_user="carlos-gh"), [])
        self.assertEqual(dev["git_name"], "carlos-gh")
        self.assertEqual(dev["git_email"], "carlos-gh@users.noreply.github.com")

    def test_explicit_values_win_over_allocation(self) -> None:
        dev = developer_vars(add(code_server_port=9000, wg_ip="10.200.200.42"), [])
        self.assertEqual(dev["code_server_port"], 9000)
        self.assertEqual(dev["wg_ip"], "10.200.200.42")


class TestPlanning(unittest.TestCase):
    def test_valid_entry_is_ready(self) -> None:
        (action,) = plan_changes([add()])
        self.assertEqual(action["status"], "ready")
        self.assertEqual(action["dev"]["name"], "carlos")

    def test_invalid_entry_is_rejected_with_a_reason(self) -> None:
        (action,) = plan_changes([add(ssh_key="nope")])
        self.assertEqual(action["status"], "rejected")
        self.assertIn("ssh_key", action["reason"])

    def test_already_applied_ids_are_skipped(self) -> None:
        entry = add()
        (action,) = plan_changes([entry], {change_id(entry)})
        self.assertEqual(action["status"], "already_applied")
        self.assertNotIn("dev", action)

    def test_last_entry_per_developer_wins(self) -> None:
        first, second = add(), remove()
        actions = plan_changes([first, second])
        self.assertEqual(actions[0]["status"], "superseded")
        self.assertEqual(actions[0]["reason"], f"superseded by {change_id(second)}")
        self.assertEqual(actions[1]["status"], "ready")

    def test_different_developers_do_not_supersede_each_other(self) -> None:
        actions = plan_changes([add("carlos"), add("royce")])
        self.assertEqual([a["status"] for a in actions], ["ready", "ready"])

    def test_batch_allocations_do_not_collide(self) -> None:
        actions = plan_changes([add("carlos"), add("royce")])
        self.assertEqual(
            [(a["dev"]["code_server_port"], a["dev"]["wg_ip"]) for a in actions],
            [(8443, "10.200.200.2"), (8444, "10.200.200.3")],
        )

    def test_allocation_continues_past_the_existing_roster(self) -> None:
        existing = [{"name": "adi", "code_server_port": 8443, "wg_ip": "10.200.200.2"}]
        (action,) = plan_changes([add()], existing=existing)
        self.assertEqual(action["dev"]["code_server_port"], 8444)
        self.assertEqual(action["dev"]["wg_ip"], "10.200.200.3")

    def test_a_removal_frees_the_slot_for_a_later_add(self) -> None:
        existing = [{"name": "adi", "code_server_port": 8443, "wg_ip": "10.200.200.2"}]
        actions = plan_changes([remove("adi"), add("carlos")], existing=existing)
        self.assertEqual(actions[1]["dev"]["code_server_port"], 8443)

    def test_a_rejected_entry_does_not_supersede_a_valid_one(self) -> None:
        good, bad = add(), add(ssh_key="nope")
        actions = plan_changes([good, bad])
        self.assertEqual([a["status"] for a in actions], ["ready", "rejected"])


class TestAnsibleBoundary(unittest.TestCase):
    def test_extra_vars_for_an_add(self) -> None:
        (action,) = plan_changes([add()])
        self.assertEqual(
            extra_vars_for(action),
            {
                "apply_add": [action["dev"]],
                "apply_remove": [],
                "purge_removed": False,
            },
        )

    def test_extra_vars_for_a_removal_never_purge_by_default(self) -> None:
        (action,) = plan_changes([remove("royce")])
        self.assertEqual(
            extra_vars_for(action),
            {"apply_add": [], "apply_remove": ["royce"], "purge_removed": False},
        )

    def test_purge_is_only_set_when_asked_for(self) -> None:
        (action,) = plan_changes([remove("royce")])
        self.assertTrue(extra_vars_for(action, purge=True)["purge_removed"])

    def test_purge_never_leaks_into_an_add(self) -> None:
        (action,) = plan_changes([add()])
        self.assertFalse(extra_vars_for(action, purge=True)["purge_removed"])

    def test_base_vars_are_reused_but_the_roster_is_dropped(self) -> None:
        base = {
            "developers": [{"name": "adi"}],
            "install_code_server": False,
            "multillm_gateway_port": 8080,
        }
        (action,) = plan_changes([add()])
        got = extra_vars_for(action, base)
        self.assertNotIn("developers", got)
        self.assertEqual(got["install_code_server"], False)
        self.assertEqual(got["multillm_gateway_port"], 8080)

    def test_ansible_command_is_exact(self) -> None:
        self.assertEqual(
            ansible_command("/tmp/v.json", "ansible/apply_changes.yml", "configs/hosts.ini"),
            [
                "ansible-playbook",
                "-i",
                "configs/hosts.ini",
                "--extra-vars",
                "@/tmp/v.json",
                "ansible/apply_changes.yml",
            ],
        )

    def test_ansible_command_with_limit_connection_and_check(self) -> None:
        self.assertEqual(
            ansible_command(
                "/tmp/v.json",
                "p.yml",
                "localhost,",
                limit="devserver",
                connection="local",
                check=True,
            ),
            [
                "ansible-playbook",
                "-i",
                "localhost,",
                "--connection",
                "local",
                "--limit",
                "devserver",
                "--check",
                "--extra-vars",
                "@/tmp/v.json",
                "p.yml",
            ],
        )


class TestExecution(unittest.TestCase):
    def test_success_marks_applied_and_invokes_ansible_once(self) -> None:
        runner, calls = fake_runner(0)
        actions = execute_plan(plan_changes([add()]), runner)
        self.assertEqual(actions[0]["status"], "applied")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["apply_add"][0]["name"], "carlos")

    def test_failure_marks_failed_and_keeps_the_entry_queued(self) -> None:
        runner, _ = fake_runner(2)
        entry = add()
        actions = execute_plan(plan_changes([entry]), runner)
        self.assertEqual(actions[0]["status"], "failed")
        self.assertEqual(actions[0]["rc"], 2)
        self.assertEqual(remaining_queue(actions), [entry])

    def test_one_failure_does_not_block_the_rest_of_the_batch(self) -> None:
        runner, calls = fake_runner(
            rc=lambda ev: 5 if ev["apply_add"] and ev["apply_add"][0]["name"] == "carlos" else 0
        )
        carlos, royce = add("carlos"), add("royce")
        actions = execute_plan(plan_changes([carlos, royce]), runner)
        self.assertEqual([a["status"] for a in actions], ["failed", "applied"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(remaining_queue(actions), [carlos])

    def test_non_ready_actions_are_never_executed(self) -> None:
        runner, calls = fake_runner(0)
        entry = add()
        actions = execute_plan(plan_changes([entry], {change_id(entry)}), runner)
        self.assertEqual(calls, [])
        self.assertEqual(actions[0]["status"], "already_applied")

    def test_rejected_and_superseded_entries_leave_the_queue(self) -> None:
        runner, calls = fake_runner(0)
        actions = execute_plan(plan_changes([add(), remove(), add(ssh_key="x")]), runner)
        self.assertEqual([a["status"] for a in actions], ["superseded", "applied", "rejected"])
        self.assertEqual(remaining_queue(actions), [])
        self.assertEqual(len(calls), 1)

    def test_rerun_after_success_is_a_no_op(self) -> None:
        runner, calls = fake_runner(0)
        entry = add()
        first = execute_plan(plan_changes([entry]), runner)
        done = {a["id"] for a in first if a["status"] == "applied"}
        # The queue is rewritten empty, but even a replayed entry must not re-run.
        second = execute_plan(plan_changes([entry], done), runner)
        self.assertEqual(second[0]["status"], "already_applied")
        self.assertEqual(len(calls), 1)

    def test_retry_of_a_failed_entry_applies_it(self) -> None:
        outcomes = iter([3, 0])
        runner, calls = fake_runner(rc=lambda _ev: next(outcomes))
        entry = add()
        first = execute_plan(plan_changes([entry]), runner)
        self.assertEqual(remaining_queue(first), [entry])
        second = execute_plan(plan_changes(remaining_queue(first)), runner)
        self.assertEqual(second[0]["status"], "applied")
        self.assertEqual(len(calls), 2)

    def test_summarize_counts_statuses(self) -> None:
        runner, _ = fake_runner(0)
        actions = execute_plan(plan_changes([add(), remove(), add(ssh_key="x")]), runner)
        self.assertEqual(summarize(actions), {"superseded": 1, "applied": 1, "rejected": 1})


class TestAuditAndRoster(unittest.TestCase):
    def test_audit_record_carries_the_original_change_and_reason(self) -> None:
        runner, _ = fake_runner(4)
        actions = execute_plan(plan_changes([add()]), runner)
        (rec,) = audit_records(actions, "2026-01-02T03:04:05Z")
        self.assertEqual(rec["ts"], "2026-01-02T03:04:05Z")
        self.assertEqual(rec["status"], "failed")
        self.assertEqual(rec["op"], "add")
        self.assertEqual(rec["name"], "carlos")
        self.assertEqual(rec["change"], add())
        self.assertEqual(rec["rc"], 4)
        self.assertIn("exited 4", rec["reason"])

    def test_audit_records_are_json_serializable(self) -> None:
        runner, _ = fake_runner(0)
        actions = execute_plan(plan_changes([add()]), runner)
        json.dumps(audit_records(actions, "now"))

    def test_roster_gains_applied_adds(self) -> None:
        runner, _ = fake_runner(0)
        base = {"developers": [{"name": "adi", "code_server_port": 8443}]}
        actions = execute_plan(plan_changes([add()], existing=base["developers"]), runner, base)
        merged = merge_roster(base, actions)
        self.assertEqual([d["name"] for d in merged["developers"]], ["adi", "carlos"])
        self.assertEqual(merged["developers"][1]["code_server_port"], 8444)

    def test_roster_does_not_record_key_material(self) -> None:
        runner, _ = fake_runner(0)
        base = {"developers": []}
        actions = execute_plan(plan_changes([add()]), runner, base)
        merged = merge_roster(base, actions)
        self.assertNotIn("ssh_key", merged["developers"][0])
        # …but the key IS what gets handed to Ansible for the account itself.
        self.assertEqual(extra_vars_for(actions[0])["apply_add"][0]["ssh_key"], GOOD_KEY)

    def test_roster_loses_applied_removals(self) -> None:
        runner, _ = fake_runner(0)
        base = {"developers": [{"name": "adi"}, {"name": "royce"}]}
        actions = execute_plan(plan_changes([remove("royce")]), runner, base)
        self.assertEqual([d["name"] for d in merge_roster(base, actions)["developers"]], ["adi"])

    def test_roster_ignores_failed_changes(self) -> None:
        runner, _ = fake_runner(1)
        base = {"developers": [{"name": "adi"}]}
        actions = execute_plan(plan_changes([add()]), runner, base)
        self.assertEqual([d["name"] for d in merge_roster(base, actions)["developers"]], ["adi"])

    def test_roster_re_add_does_not_duplicate(self) -> None:
        runner, _ = fake_runner(0)
        base = {"developers": [{"name": "carlos", "code_server_port": 8443}]}
        actions = execute_plan(plan_changes([add()], existing=base["developers"]), runner, base)
        merged = merge_roster(base, actions)
        self.assertEqual(len(merged["developers"]), 1)


class TestMainWorkflow(unittest.TestCase):
    """End-to-end through main(), with the Ansible boundary mocked.

    `make_runner` is patched to a recorder, so no test ever shells out to
    ansible-playbook or touches anything outside its own temp directory.
    """

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.queue = self.root / "pending-changes.jsonl"
        self.audit = self.root / "applied-changes.jsonl"
        self.base_vars = self.root / "ansible_vars.json"

    def enqueue(self, *changes) -> None:
        self.queue.write_text(
            "".join(json.dumps(c) + "\n" for c in changes), encoding="utf-8"
        )

    def run_main(self, *extra_args, rc=0, on_run=None):
        calls = []

        def runner(extra_vars):
            calls.append(extra_vars)
            if on_run is not None:
                on_run(extra_vars)
            return rc, "ok"

        argv = [
            "--queue",
            str(self.queue),
            "--audit",
            str(self.audit),
            "--base-vars",
            str(self.base_vars),
            "--inventory",
            "localhost,",
            "--connection",
            "local",
            *extra_args,
        ]
        out = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                mock.patch("scripts.apply_pending.make_runner", return_value=runner)
            )
            stack.enter_context(
                mock.patch(
                    "scripts.apply_pending.shutil.which",
                    return_value="/usr/bin/ansible-playbook",
                )
            )
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(out))
            code = main(argv)
        return code, calls, out.getvalue()

    def test_check_mode_runs_ansible_but_mutates_nothing(self) -> None:
        self.base_vars.write_text(
            json.dumps(
                {
                    "developers": [
                        {"name": "adi", "code_server_port": 8443, "wg_ip": "10.200.200.2"}
                    ]
                }
            )
        )
        base_before = self.base_vars.read_text()
        self.enqueue(add())
        queue_before = self.queue.read_text()

        code, calls, _ = self.run_main("--check")

        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.audit.exists())
        self.assertEqual(self.queue.read_text(), queue_before)
        self.assertEqual(self.base_vars.read_text(), base_before)

    def test_a_checked_entry_is_still_applied_by_the_next_real_run(self) -> None:
        self.base_vars.write_text(json.dumps({"developers": []}))
        self.enqueue(add())
        self.run_main("--check")

        code, calls, _ = self.run_main()

        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.queue.read_text(), "")
        audit = [json.loads(line) for line in self.audit.read_text().splitlines()]
        self.assertEqual([r["status"] for r in audit], ["applied"])

    def test_enqueue_during_apply_survives_the_queue_rewrite(self) -> None:
        self.base_vars.write_text(json.dumps({"developers": []}))
        self.enqueue(add("carlos"))
        late = add("royce")

        def enqueue_late(_extra_vars):
            with self.queue.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(late) + "\n")

        code, calls, _ = self.run_main(on_run=enqueue_late)

        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        kept = [json.loads(line) for line in self.queue.read_text().splitlines()]
        self.assertEqual(kept, [late])

    def test_garbage_enqueued_during_apply_is_not_silently_lost(self) -> None:
        self.base_vars.write_text(json.dumps({"developers": []}))
        self.enqueue(add("carlos"))

        def scribble(_extra_vars):
            with self.queue.open("a", encoding="utf-8") as fh:
                fh.write("{not json\n")

        code, _, _ = self.run_main(on_run=scribble)

        self.assertEqual(code, 0)
        self.assertEqual(self.queue.read_text(), "{not json\n")

    def test_missing_base_vars_fails_fast_when_an_add_needs_allocation(self) -> None:
        self.enqueue(add())
        queue_before = self.queue.read_text()

        code, calls, out = self.run_main()

        self.assertEqual(code, 2)
        self.assertEqual(calls, [])
        self.assertEqual(self.queue.read_text(), queue_before)
        self.assertFalse(self.audit.exists())
        self.assertIn("base-vars", out)

    def test_missing_base_vars_is_fine_when_the_add_is_fully_specified(self) -> None:
        self.enqueue(add(code_server_port=9000, wg_ip="10.200.200.9"))

        code, calls, _ = self.run_main()

        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["apply_add"][0]["code_server_port"], 9000)

    def test_missing_base_vars_is_fine_for_a_removal(self) -> None:
        self.enqueue(remove("royce"))

        code, calls, _ = self.run_main()

        self.assertEqual(code, 0)
        self.assertEqual(calls[0]["apply_remove"], ["royce"])

    def test_unwritable_audit_path_stops_the_run_before_ansible(self) -> None:
        self.base_vars.write_text(json.dumps({"developers": []}))
        blocker = self.root / "blocker"
        blocker.write_text("")
        self.enqueue(add())
        queue_before = self.queue.read_text()

        code, calls, out = self.run_main("--audit", str(blocker / "applied.jsonl"))

        self.assertEqual(code, 2)
        self.assertEqual(calls, [])
        self.assertEqual(self.queue.read_text(), queue_before)
        self.assertIn("--audit", out)


@unittest.skipIf(yaml is None, "PyYAML not installed")
class TestPlaybookAssets(unittest.TestCase):
    """Regression fence on the Ansible side — parsed, never executed."""

    ANSIBLE = Path(__file__).resolve().parent.parent / "ansible"

    def _tasks(self, name):
        return yaml.safe_load((self.ANSIBLE / name).read_text(encoding="utf-8"))

    def test_apply_playbook_exists_and_parses(self) -> None:
        (play,) = self._tasks("apply_changes.yml")
        self.assertEqual(play["hosts"], "all")
        self.assertTrue(play["become"])

    def test_apply_defaults_are_non_destructive(self) -> None:
        (play,) = self._tasks("apply_changes.yml")
        self.assertIs(play["vars"]["purge_removed"], False)
        self.assertEqual(play["vars"]["apply_add"], [])
        self.assertEqual(play["vars"]["apply_remove"], [])

    def test_only_the_purge_task_deletes_an_account(self) -> None:
        (play,) = self._tasks("apply_changes.yml")
        destructive = [
            t
            for t in play["tasks"]
            if (t.get("ansible.builtin.user") or {}).get("state") == "absent"
        ]
        self.assertEqual(len(destructive), 1, "exactly one task may delete an account")
        self.assertIn("purge_removed", str(destructive[0].get("when")))

    def test_both_playbooks_provision_through_the_same_task_file(self) -> None:
        shared = "developer_account_tasks.yml"
        (apply_play,) = self._tasks("apply_changes.yml")
        (deploy_play,) = self._tasks("playbook.yml")
        for play in (apply_play, deploy_play):
            includes = [
                t.get("ansible.builtin.include_tasks")
                for t in play["tasks"]
                if "ansible.builtin.include_tasks" in t
            ]
            flat = [i if isinstance(i, str) else i.get("file") for i in includes]
            self.assertIn(shared, flat)

    def test_the_shared_file_includes_the_real_user_tasks(self) -> None:
        tasks = self._tasks("developer_account_tasks.yml")
        self.assertIn(
            "user_tasks.yml",
            [t.get("ansible.builtin.include_tasks") for t in tasks],
        )


if __name__ == "__main__":
    unittest.main()
