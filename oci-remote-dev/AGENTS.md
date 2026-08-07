# Agent Instructions

For all AI coding tasks in this repository:

1. Treat `KB/` as a required source of operational truth for provisioning and troubleshooting.
2. Read `KB/README.md` first, then the relevant doc in `KB/oci-provisioning/`.
3. Before changing provisioning logic, check `KB/oci-provisioning/ISSUE-CATALOG.md` for known failure patterns and mitigations.
4. Prefer Python SDK workflows (`scripts/oci_sdk_ops.py`) for tenancy/instance state operations when CLI behavior is inconsistent.
5. Ansible-first: developers are provisioned only through `ansible/developer_account_tasks.yml` → `ansible/user_tasks.yml`. Both `playbook.yml` (full deploy) and `apply_changes.yml` (runtime apply-from-queue, driven by `scripts/apply_pending.py`) include it — add per-developer provisioning there, never as a parallel hand-rolled path.
6. Scripts are stdlib-only with a pure core plus thin IO/subprocess edges; tests are `unittest` and must mock every execution boundary (never create real users or run real Ansible). Run `make check` for local CI parity.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
