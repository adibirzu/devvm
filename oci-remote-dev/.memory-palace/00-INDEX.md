# 🗺️ Palace Index

**Project:** OCI Agentic Dev OS — a multi-developer remote workspace on an OCI VM
where humans and AI coding agents collaborate over a WireGuard VPN, with isolated
per-user sandboxes, a shared MultiLLM gateway, durable agent sessions, and shared
memory.

**Repo:** `~/dev/devvm/oci-remote-dev` (project home is `~/dev/devvm`).

## Read order for a fresh agent / after a reconnect
1. [`OPEN-THREADS.md`](OPEN-THREADS.md) — what's in flight right now.
2. [`ARCHITECTURE.md`](ARCHITECTURE.md) — the lay of the land.
3. [`DECISIONS.md`](DECISIONS.md) — why things are the way they are.
4. [`SESSION-LOG.md`](SESSION-LOG.md) — recent history.

## Source-of-truth pointers (don't duplicate, link)
- Build/run + status table: `README.md`
- Roadmap to the agentic OS: `ROADMAP-v2.md`
- Troubleshooting (errors only): `KB/oci-provisioning/ISSUE-CATALOG.md`
- Agent ↔ tool wiring: `ansible/multillm_tasks.yml`, each user's `~/.claude/.mcp.json`
