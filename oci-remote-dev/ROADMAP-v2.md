# ROADMAP v2 — Toward an Agentic Development OS

This document captures the direction from "multi-developer remote workspace" to a
genuine **agentic development OS**: a shared substrate where human developers and
AI agents collaborate on the same isolated VM, with observability, policy, and
memory as first-class platform services.

The v1 platform (provisioning, isolation, VPN, shared MultiLLM gateway, dashboards)
is **done** — see the status table in `README.md`. v2 builds the agent layer on top.

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

**Remaining:**
- **Budget enforcement UX** — surface a soft warning banner on `/team` (and optionally
  on the landing dashboard) when a developer crosses their daily cap.
- **Structured agent log sink** — ship gateway + session logs (already `LOG_FORMAT=json`)
  to a queryable local sink and link it from the dashboard.
- **`gateway-health` panel** — fold the `/health` probe into the `:80` landing page.

---

## Phase 2 — Shared Agent Memory / Context Bus

Goal: let agents (and humans) share durable, scoped context across sessions and users.

- **Context store** — a lightweight service (SQLite + FastAPI, or reuse the gateway's
  process) holding namespaced key/value + vector context at three scopes:
  `user:<name>`, `project:<slug>`, `shared`.
- **MCP surface** — expose `context.get` / `context.put` / `context.search` as MCP
  tools so any agent can read/write the bus with scope enforcement.
- **Pairing integration** — `pair-claude` sessions write a shared transcript summary
  into `project:` scope so a developer joining later has continuity.
- **Toggle:** `ENABLE_CONTEXT_BUS=true`.

**Risk to manage:** scope leakage. Writes must be authenticated to the calling UNIX
user; `shared`-scope writes require `developers` group membership.

---

## Phase 3 — Central MCP Tool Registry & Guardrails

Goal: a curated, governed set of MCP tools available to every agent, with policy.

- **Tool registry** — a single `/opt/agent-os/registry.json` describing approved MCP
  servers (multillm, context bus, git ops, OCI read-only ops). Per-user `.mcp.json`
  is generated from the registry instead of hand-maintained.
- **Policy/guardrail layer** — a gateway middleware that can deny or require
  confirmation for sensitive tool calls (e.g. `oci ... delete`, writes outside
  `~/shared-workspace`). Decisions logged to the observability sink.
- **OCI read-only agent tools** — wrap `scripts/oci_sdk_ops.py` as an MCP server so
  agents can inspect tenancy/instance state without shell access to credentials.
- **Toggle:** `ENABLE_TOOL_REGISTRY=true`, `ENABLE_GUARDRAILS=true`.

**Guardrail default:** deny-by-default for destructive verbs; allow read + scoped
writes. Mirrors the tenancy-boundary rules in the global operating instructions.

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
