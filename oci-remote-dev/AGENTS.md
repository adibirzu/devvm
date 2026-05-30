# Agent Instructions

For all AI coding tasks in this repository:

1. Treat `KB/` as a required source of operational truth for provisioning and troubleshooting.
2. Read `KB/README.md` first, then the relevant doc in `KB/oci-provisioning/`.
3. Before changing provisioning logic, check `KB/oci-provisioning/ISSUE-CATALOG.md` for known failure patterns and mitigations.
4. Prefer Python SDK workflows (`scripts/oci_sdk_ops.py`) for tenancy/instance state operations when CLI behavior is inconsistent.
