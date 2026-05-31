# ROADMAP v2 — Toward an Agentic Development OS

This document captures the direction from "multi-developer remote workspace" to a
genuine **agentic development OS**: a shared substrate where human developers and
AI agents collaborate on the same isolated VM, with observability, policy, and
memory as first-class platform services.

The v1 platform (provisioning, isolation, VPN, shared MultiLLM gateway, dashboards)
is **done** — see the status table in `README.md`. v2 builds the agent layer on top.

**North star:** a Linux/server equivalent of **cmux / AgentsRoom** (which are
macOS-only) — run many coding agents in parallel on the VM with real-time status,
local-project integration, multi-LLM usage, project status, and full **resilience to
disconnects** — all reachable over the WireGuard VPN, including from mobile.

---

## Phase 0 — Resilience & Memory (done)

The substrate that makes everything else survivable, shipped first:

- ✅ **Durable agent sessions** — `agentctl` runs agents in detached server-side tmux;
  they keep working through WireGuard/SSH/internet drops and reattach on reconnect.
  `loginctl enable-linger`, mosh, and sshd keepalive back this up.
- ✅ **Memory palace** — `.memory-palace/` structured rooms + `palace` CLI so humans
  and agents reload full project context after a disconnect or fresh session.
- ✅ **Multi-agent status board** — `/agents.html` (fed by `agent-status` on a 15s
  timer) shows agents × project × state × 24h LLM cost across all developers: the
  AgentsRoom "room" view for Linux.
- ✅ **Project-status surface** — `project-status` (30s timer → `projects.json`) joins
  per-project git state (branch, clean/dirty, ahead/behind, last commit) with the
  agents working in it; rendered as a Projects section on the board.

- ✅ **Reboot persistence** — `agentctl restore` replays session metadata, and
  `agentctl-restore.service` runs it per-developer on boot, so detached agents come
  back after a VM reboot (skips `--no-restart` sessions). More reliable for CLI agents
  than tmux-resurrect, which can't restore an agent's internal state anyway.

- ✅ **Mobile-friendly board** — responsive layout (wide session tables stack into
  labelled rows on phones), pause/refresh controls, and polling that suspends while the
  tab is backgrounded. Monitor agents from your phone over the WireGuard VPN.

- ✅ **Notification ring** — Claude's Notification hook → `agent-notify` (tagged with
  `$AGENTCTL_SESSION`) → a per-user JSONL feed the board reads. Sessions/developers
  waiting for input get a pulsing ring + a `🔔` badge; the board fires a browser
  notification (phone alert over the VPN) for each new event. Notifications expire by
  time, so no write-back endpoint is needed.

**Next on this track:** Phase 3 proper — central MCP tool registry + guardrail
enforcement (multillm-side: hard tenant scope + deny/confirm on destructive tool calls).

---

## Design Principles

1. **Isolation by default, sharing by intent.** Per-user UNIX sandboxes stay the
   security boundary. Anything shared (memory bus, tool registry) is an explicit,
   group-scoped service — never an accidental leak.
2. **Everything proxied is observable.** All agent LLM traffic already flows through
   the MultiLLM gateway. v2 makes that the single source of truth for cost, latency,
   tool calls, and policy decisions.
3. **The VPN is the trust boundary.** New services bind to `10.200.200.0/24` and are
   firewalled to it. Nothing new is exposed publicly.
4. **Reproducible + idempotent.** Every capability ships as an Ansible task gated by
   an `.env.local` toggle, so deploys stay declarative.

---

## Phase 1 — Observability & Cost Control (in progress)

Goal: a fleet-wide view of who/what is spending tokens and where time goes.

**Delivered:**
- ✅ **Per-developer usage attribution** — `multillm-collector@<user>.timer` runs as
  each developer and pushes their local AI-CLI stats to the gateway tagged
  `tenant=<user>` (`ansible/multillm_tasks.yml`). Attribution happens at collection
  time, so it works with the single shared gateway — no per-user gateways needed.
- ✅ **Team dashboard + API** — the gateway serves `/team`, `/api/team-usage`, and
  `/api/usage/ingest`; the unit is hardened (dedicated `multillm` user, venv,
  `ProtectSystem=strict`) and VPN-scoped.
- ✅ **`usage-report` CLI** (`/usr/local/bin/usage-report`) — aggregate rollup
  (`--`, by model/project) and per-developer rollup (`--team`, by tenant), honoring
  `MULTILLM_GATEWAY`. Pure-stdlib, covered by `tests/test_usage_report.py`.
- ✅ **Per-user daily budgets** — `MULTILLM_USER_BUDGETS="user=usd,…"` plumbed into
  the gateway env.

- ✅ **Budget-breach surfacing (ops)** — `usage-report --budgets` joins caps against
  spend and exits 2 on breach; a daily `multillm-budget-check.timer` runs it and
  fails its unit on breach (visible in journald / `systemctl status`).
- ✅ **Budget warning banner (UI)** — the agent board shows an "over budget" total, a
  per-developer spend/cap badge (red when over), fed by `agent-status` (no cross-origin
  fetch — the aggregator joins cost server-side from `/api/team-usage`).
- ✅ **`gateway-health` panel** — the board polls `/health` (server-side via the
  aggregator) and shows a green/red gateway pill.
- ✅ **Structured agent log sink (queryable)** — `agent-status`/`guardrail.jsonl`/
  `notifications.jsonl` are per-user JSONL feeds; the gateway runs `LOG_FORMAT=json`.
  The board surfaces guardrail + notification streams.

Phase 1 complete. (A unified full-text log *search* UI is deferred to Phase 4's
control plane rather than bolted onto the static `:80` page.)

---

## Phase 2 — Shared Agent Memory / Context Bus (in progress)

Goal: let agents (and humans) share durable, scoped context across sessions and users.

**Delivered:**
- ✅ **Context store** — reused the gateway's existing memory subsystem (`/api/memory`,
  `/api/context`, FTS5-backed) rather than standing up a new service. The `memories`
  and `shared_context` tables already carry a `tenant_id` column.
- ✅ **MCP surface** — the gateway already exposes `llm_memory_store`,
  `llm_memory_search`, `llm_memory_list`, `llm_memory_delete`, `llm_share_context`,
  and `llm_get_context`, registered in each developer's `~/.claude/.mcp.json`.
- ✅ **`context` CLI** (`/usr/local/bin/context`) — human-facing client with scope by
  convention: `user-<whoami>` by default, `--shared` for the team namespace, `--all`
  to search across. Pure-stdlib, covered by `tests/test_context_bus.py`.

**Remaining:**
- **Hard scope enforcement (multillm-side)** — the memory HTTP API + tool layer must
  accept and filter on `tenant_id` (the column exists; the API still scopes only by the
  free-text `project`). Until then, `user-<name>` is a naming convention, not a
  boundary: writes are authenticated by the gateway API key, not by UNIX identity, and
  any client can name any project. Closing this is the real Phase 2 deliverable.
- **Pairing integration** — `pair-claude` sessions write a transcript summary into the
  `shared` namespace so a developer joining later has continuity.
- **Toggle:** `ENABLE_CONTEXT_BUS=true` (currently always-on with the gateway).

**Risk to manage:** scope leakage. Once enforcement lands, writes must be authenticated
to the calling UNIX user and `shared`-scope writes gated on `developers` membership.

---

## Phase 3 — Central MCP Tool Registry & Guardrails (done)

A curated, governed tool surface for every agent, with policy enforced at the agent.

**Architecture correction:** guardrails live in the agent's **`PreToolUse` hook**, not
"gateway middleware" — MCP tool calls are executed by the agent, so the gateway never
sees them. The hook is the correct (and only) enforcement point, and it's per-user.

**Delivered:**
- ✅ **Guardrail policy engine** (`guardrail.py` + `guardrail-hook`) — deny/ask/allow
  with a data-driven default policy (`/etc/agent-os/policy.json`). Denies catastrophic
  shell + force-push-to-protected; asks for cloud/cluster/db mutations, system installs,
  secret access, and out-of-root writes. Audit-logged; deny/ask ring the board.
- ✅ **MCP tool registry** (`registry.json` + `mcp-registry`) — generates each user's
  `~/.claude/.mcp.json` (merge-safe: preserves personal servers, removes disabled ones).
- ✅ **OCI read-only MCP server** (`oci_mcp_server.py`) — stdio JSON-RPC; list/get only,
  enforced by a read-only verb allowlist. Registered as `oci-readonly`.
- ✅ **Audit surface** — `guardrail --log` + a 🛡️ Guardrail panel on the board
  (recent deny/ask, blocked count).
- **Toggle:** `install_agent_os` (default true).

---

## Phase 4 — Control-Plane API & Fleet Management

Goal: manage developers, services, and budgets without SSH.

- **Control-plane REST API** (VPN-only) — `POST /developers`, `DELETE /developers/:n`,
  `GET /fleet/status`, `POST /budgets`. Backed by the same Ansible primitives so the
  API and `deploy.sh` converge on identical state.
- **Self-service onboarding** — a developer can request an account; an admin approves;
  the API runs the scoped Ansible play to materialize it.
- **Fleet telemetry** — aggregate health (gateway up, code-servers up, disk, GPU if
  present) surfaced on the landing dashboard.
- **Toggle:** `ENABLE_CONTROL_PLANE=true`.

---

## Phase 5 — Autonomous Agent Workflows (stretch)

Goal: scheduled / triggered agent jobs that run on the VM under a developer's identity.

- **Job runner** — systemd-timer or cron-backed agent jobs (e.g. nightly dependency
  audit, PR triage) executing as the owning UNIX user, logged to the observability sink.
- **Approval queue** — destructive job outputs land in a review queue surfaced on the
  dashboard before anything is applied.
- **Toggle:** `ENABLE_AGENT_JOBS=true`.

---

## Cross-Cutting Hardening (do alongside any phase)

- **code-server auth** — current configs use `auth: none` behind the VPN. Add optional
  per-user password/PAM auth (`CODE_SERVER_AUTH=password`) for defense in depth.
- **Secrets** — move `/opt/multillm/.env` and per-user keys toward a local secret
  store (e.g. `pass` or OCI Vault) instead of plaintext dotfiles.
- **WireGuard key rotation** — a `connect.sh rotate` subcommand to regenerate a
  developer's keypair and push the new peer without a full redeploy.
- **Backups** — snapshot `/opt/shared-dev` and per-user home dotfiles on a schedule.

---

## Sequencing

```
Phase 1 (observability)  ──►  Phase 2 (context bus)  ──►  Phase 3 (registry+guardrails)
        │                                                          │
        └────────────►  Phase 4 (control plane)  ◄─────────────────┘
                                   │
                                   └────►  Phase 5 (agent jobs)
```

Phase 1 is pure wiring on top of what already exists and should land first. Each phase
is independently shippable behind its `.env.local` toggle, keeping deploys declarative
and reversible.
