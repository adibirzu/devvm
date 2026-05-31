# 🌌 OCI Agentic Development OS — Multi-Developer Remote Workspace

A secure, production-grade **multi-developer remote development OS** built on Oracle Cloud Infrastructure (OCI). One command provisions a high-performance Ubuntu VM, wires up a private WireGuard mesh, gives every developer a fully isolated UNIX sandbox (terminal, web IDE, RDP desktop, OAuth/API-key vault), and runs a shared **MultiLLM gateway** so all AI-agent traffic is proxied, tracked, and observable from a single dashboard.

You drive it from your Mac with the native **cmux** agent workspace over the VPN; the heavy lifting (agents, desktops, builds) runs on the remote VM.

---

## 🚀 What You Get

- **👥 Isolated multi-developer sandboxes** — dedicated UNIX accounts (`${ADMIN_USERNAME}`, `${DEV_N_NAME}`, … unlimited) each with their own `code-server`, XFCE/XRDP desktop, shell, OAuth sessions, and API keys. Nothing leaks between users.
- **🔐 Split-tunnel WireGuard VPN** — every service is reachable **only** over the private `${WG_NETWORK}` tunnel. Defaults are tuned so the VPN never hijacks your Mac's DNS or internet routing.
- **🤖 Shared MultiLLM gateway** — a system service that proxies Claude / Codex / Gemini / Ollama traffic, tracks token + cost usage per developer, and serves a live dashboard over the VPN.
- **🖥️ cmux-driven local workflow** — run the native macOS agent workspace locally and connect its panes to the remote sandbox over WireGuard.
- **🤝 Live pair programming** — `pair-claude` shares one AI coding session across developers via a group-owned tmux socket.
- **⚡ Idempotent Ansible** — decoupled from cloud-init (dodging OCI's 32 KB metadata limit); a tiny boot hook brings up networking + WireGuard, then Ansible configures everything else.
- **🛡️ Security gate** — `security_gate.py` blocks OCIDs, infrastructure IPs, namespaces, and secrets from entering the git tree.

---

## 📐 Systems Architecture

```
                          ┌──────────────────────────────────────────────┐
                          │                Public Internet                │
                          └──────────────────────────────────────────────┘
                                              │  SSH:22  WG:${WG_PORT} (only)
                                   ┌──────────┴──────────┐
                                   │   OCI VM Instance   │  <VM_PUBLIC_IP>
                                   │ ${VM_SHAPE}         │  ${VM_OCPUS} OCPU / ${VM_MEMORY_GB} GB / Ubuntu ${UBUNTU_VERSION}
                                   └──────────┬──────────┘
                                              │
        Local Mac (cmux) ───[ WireGuard split tunnel ${WG_NETWORK} ]─────┐
                                              │                          │
                          ┌───────────────────┴───────────────────────┐  │
                          │  WireGuard Server  wg0  → ${WG_SERVER_IP}  │◄─┘
                          └───────────────────┬───────────────────────┘
                                              │
        ┌─────────────────────────────────────┼─────────────────────────────────────┐
        │  Reachable ONLY over the VPN at ${WG_SERVER_IP}                             │
        │                                                                            │
        │   :80   Developer landing dashboard (per-user cards)                        │
        │   :${MULTILLM_GATEWAY_PORT} MultiLLM gateway + /dashboard                   │
        │   :${RDP_PORT} XRDP visual desktops (XFCE, one session per user)            │
        │                                                                            │
        │   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐                    │
        │   │ ${ADMIN_USERNAME} │ │ ${DEV_2_NAME} │ │ ${DEV_3_NAME} │   … unlimited   │
        │   │ code-server  │   │ code-server  │   │ code-server  │                    │
        │   │:${CODE_SERVER_PORT}││:${DEV_2_CODE_SERVER_PORT}││:${DEV_3_CODE_SERVER_PORT}│
        │   │ ~/.bashrc    │   │ ~/.bashrc    │   │ ~/.bashrc    │  ← isolated creds  │
        │   │ OAuth/API ✓  │   │ OAuth/API ✓  │   │ OAuth/API ✓  │                    │
        │   └──────┬───────┘   └──────┬───────┘   └──────┬───────┘                    │
        │          └─────────── /opt/shared-dev (SGID, group: developers) ───────────┤
        │                       pair-claude shared tmux socket                        │
        └────────────────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Quick Start

### 1. Configure

```bash
cp .env.example .env             # local-only; edit or let the wizard write it
./scripts/setup-wizard.sh        # interactive → renders .env from .env.example
```

Prompts for OCI profile/compartment, VM shape, WireGuard settings (including **split vs full tunnel**), developer accounts, and the MultiLLM gateway.

### 2. Deploy

```bash
# Preview without applying
./scripts/deploy.sh --dry-run --profile <OCI_PROFILE> --yes

# Provision the VM and run the Ansible playbook
./scripts/deploy.sh --profile <OCI_PROFILE> --yes
```

`deploy.sh` wraps `deploy_multicloud.py`, which compiles per-developer WireGuard keys, renders a ~3 KB network-only cloud-init, launches the instance, waits for SSH, and runs the Ansible playbook (GUI, desktops, code-servers, AI CLIs, shared MultiLLM gateway, dashboard).

### 3. Connect

```bash
./scripts/connect.sh wg-up                 # macOS / Linux
./scripts/connect.sh -u adi wg-up          # bring up a specific developer's tunnel
./scripts/connect.sh qr                    # QR code for phones
```

Once connected, everything lives behind the tunnel:

| Service | URL |
|---|---|
| Developer landing dashboard | `http://${WG_SERVER_IP}` |
| MultiLLM usage dashboard | `http://${WG_SERVER_IP}:${MULTILLM_GATEWAY_PORT}/dashboard` |
| Admin Web IDE | `http://${WG_SERVER_IP}:${CODE_SERVER_PORT}` |
| Developer N Web IDE | `http://${WG_SERVER_IP}:${DEV_N_CODE_SERVER_PORT}` |
| RDP desktops | `${WG_SERVER_IP}:${RDP_PORT}` |

---

## 💻 cmux Native macOS Workspace

While the server runs on Linux, you drive it from your Mac with [**cmux**](https://cmux.com/) — the native, Swift-based terminal workspace built for AI agents.

**Download cmux locally** on each developer's Mac, then connect its tabbed agent panes to your isolated remote sandbox over the WireGuard tunnel:

```bash
ssh -i "${SSH_PRIVATE_KEY_PATH:-${SSH_PUBLIC_KEY_PATH%.pub}}" "${DEV_N_NAME}@${WG_SERVER_IP}"
```

Use cmux's tabbed agent panels, vertical splits, **glow rings**, and macOS **notification rings** to orchestrate remote agents (Claude Code, Codex, Gemini) with real-time visual alerts when an agent is waiting for input. Each tab is a live SSH session into your own UNIX account on the VM — your panes, your shell, your agents, nobody else's.

> cmux is a **local** macOS app; nothing is installed server-side for it. The VM just needs to be reachable over the VPN at `${WG_SERVER_IP}`.

---

## 🔐 Personal Agent & OAuth / API-Key Isolation

Because every developer runs under a **dedicated, isolated UNIX account**, all credentials and agent state are fully sandboxed:

- **Terminals & shell** — each user has their own `/home/<username>` and `~/.bashrc`. Environment exports, aliases, and `PATH` never collide.
- **API keys** — developers export `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_AI_API_KEY` in their **own** `~/.bashrc`. They are never readable by other users (home dirs are `0750`).
- **OAuth sessions** — Claude Code CLI browser logins are written to per-user config dirs (e.g. `~/.config/@anthropic-ai/claude-code/`). One developer signing in **never** overwrites or exposes another's session.
- **MCP & agent config** — `~/.claude/.mcp.json`, `~/.claude/hooks.json`, and Codex's `~/.codex/config.toml` are all per-user.

The only **shared** surface is the deliberate one: `/opt/shared-dev` (group `developers`, SGID, `umask 002`) symlinked into each home as `~/shared-workspace`, plus the `pair-claude` socket. Sharing is opt-in, not accidental.

### Per-account GitHub identity — even in shared repos

Each UNIX account commits and pushes as **its own GitHub account**, including inside
the shared `/opt/shared-dev` repos:

- **Attribution is enforced by environment, not config.** Each `~/.bashrc` exports
  `GIT_AUTHOR_*` / `GIT_COMMITTER_*` for that user. These outrank a repo-level
  `user.email` — so even if one developer sets an identity in a shared repo's
  `.git/config`, everyone else's commits are still attributed to *them*.
- **Push auth is per-user.** Credentials live only in each home (`~/.config/gh` after
  `gh auth login`, or `~/.ssh/id_github`). The per-user `~/.ssh/config` routes
  `github.com` through that account's own key. Nothing authenticates from the shared
  folder.
- **No shared token.** A shared `GITHUB_TOKEN` would make everyone push as one account,
  so it's never set system-wide — each user authenticates individually.
- **Verify anytime:** `git-whoami` prints the identity a commit will use, whether it's
  enforced, your `gh` account, and the repo's push-auth method — and warns if a shared
  `GITHUB_TOKEN` is present.

Configure per developer via `GIT_NAME`/`GIT_EMAIL`/`GITHUB_USER` (and `DEV_N_*`); email
defaults to GitHub's `<user>@users.noreply.github.com` so no personal address is required.

---

## 📊 MultiLLM Gateway & Usage Monitor

Each shared workstation provisions the **MultiLLM gateway** plus a **per-user usage collector**, giving you a single team view of how much every developer is spending across **their own** Claude, Codex, and Gemini accounts — without changing anyone's workflow.

### How it captures multi-user, multi-account usage

The AI CLIs log token usage locally, per UNIX user (`~/.claude`, `~/.codex`, `~/.gemini`). A `systemd` timer runs a collector **as each developer** (mirroring the `code-server@%i` pattern), reads that developer's local stats, and pushes a daily snapshot to the gateway tagged with the developer (`tenant_id`) and the LLM account label. Because each developer keeps using their own logins, nothing about their workflow changes — and **no prompt content is ever collected**, only token counts and a best-effort account label.

```
 ${ADMIN_USERNAME} ─ multillm-collector@${ADMIN_USERNAME}.timer ─┐
 ${DEV_2_NAME}     ─ multillm-collector@${DEV_2_NAME}.timer ─────┤ POST /api/usage/ingest
 ${DEV_3_NAME}     ─ multillm-collector@${DEV_3_NAME}.timer ─────┘        │
                                                       ▼
                                   multillm-gateway.service (${WG_SERVER_IP}:${MULTILLM_GATEWAY_PORT})
                                   team_usage table  →  GET /api/team-usage  →  /team
```

### Architecture

- **Shared gateway service** — `multillm-gateway.service` runs as a hardened, dedicated `multillm` user, bound to the WireGuard IP and firewalled to the WG subnet only. Data lives in `/var/lib/multillm`; config in `/etc/multillm/`.
- **Per-user collectors** — `multillm-collector@<user>.timer` fires every 15 min (tunable), running `multillm-collect --user <user>` as that developer. Snapshots UPSERT on `(user, backend, account, model, day)`, so re-runs are idempotent and never double-count.
- **Auto-generated API key** — a 40-char key is generated once on first deploy (persisted at `/etc/multillm/api_key`, never rotated on re-run). Collectors read it from the developer-group-readable `/etc/multillm/collector.env`.
- **Team dashboard** — per-developer and per-account token + cost rollups, backend breakdown, and over-budget flags:

  ```
  http://${WG_SERVER_IP}:${MULTILLM_GATEWAY_PORT}/team          # multi-user usage
  http://${WG_SERVER_IP}:${MULTILLM_GATEWAY_PORT}/dashboard     # full gateway dashboard
  ```

- **`usage-report` CLI** — terminal-side rollup at `/usr/local/bin/usage-report` for
  developers who live in the shell. Honors `MULTILLM_GATEWAY` (set in
  `/etc/multillm/collector.env`):

  ```bash
  usage-report                    # aggregate: by model + by project, last 24h
  usage-report --team --hours 168 # per-developer (tenant) rollup, last 7 days
  usage-report --budgets          # flag developers over their daily cap (exit 2 on breach)
  usage-report --team --json      # raw JSON for scripting
  ```

  A `multillm-budget-check.timer` runs `usage-report --budgets` daily when
  `MULTILLM_USER_BUDGETS` is set; a breach fails the oneshot unit, surfacing in
  `systemctl status multillm-budget-check` and journald.

### Toggles

```bash
INSTALL_MULTILLM_GATEWAY=true       # set false to skip monitoring entirely
MULTILLM_GATEWAY_PORT=<PORT>        # gateway / dashboard port (WG-only)
MULTILLM_COLLECT_INTERVAL_MIN=15    # how often each collector reports
MULTILLM_USER_BUDGETS="${DEV_2_NAME}=5,${DEV_3_NAME}=10"   # optional per-user daily USD caps
MULTILLM_INSTALL_SOURCE=/opt/multillm    # pip target: synced source (default), PyPI spec, or git URL
MULTILLM_SOURCE_PATH=../multillm          # local source copied into /opt/multillm
```

---

## 🧠 Shared Context Bus

Agents and developers share durable memory through the gateway's memory/context
store. AI agents reach it via the MCP tools registered in each user's
`~/.claude/.mcp.json` (`llm_memory_store`, `llm_memory_search`, `llm_share_context`,
…); humans reach the same store from the shell via the `context` CLI
(`/usr/local/bin/context`).

**Scope: private by default, shared on purpose.**

```bash
context put "Routing decision" "split tunnel, no DNS"   # → your private namespace (user-<you>)
context search "routing"                                # search your namespace
context put "API base URL" "10.200.200.1:8080" --shared # → cross-developer 'shared' namespace
context search "routing" --shared                       # search shared
context search "routing" --all                          # search across everything
context list --shared
context rm <id>
```

- **Default scope** is `user-<whoami>` — your memories stay yours.
- **`--shared`** writes/reads the team `shared` namespace.
- Reads are public over the VPN; writes send `X-API-Key` (from `/etc/multillm/collector.env`).

> Scoping is now **enforced server-side**: the `context` CLI sends an
> `X-MultiLLM-Tenant` header (your user / `shared` / none-for-`--all`), and each
> developer's MCP server runs with `MULTILLM_ENFORCE_TENANT=true` so the gateway tags
> writes and filters reads by tenant. `user-<you>` is a real ownership boundary, not
> just a naming convention.

---

## 🛟 Durable Agent Sessions & Disconnect Resilience

Coding agents run **on the VM in detached tmux sessions**, decoupled from your
client — so a WireGuard drop, an SSH timeout, or your laptop going to sleep does
**not** stop them. Reconnect and reattach exactly where things were.

```bash
agentctl start claude -p myapp -d ~/shared-workspace/myapp   # launch a detached agent
agentctl ls                       # see every session + state (running/attached/dead)
agentctl ls --json                # machine-readable (feeds the status dashboard)
agentctl attach 'agent:myapp:claude'   # reattach after a reconnect
agentctl logs 'agent:myapp:claude'     # tail what it did while you were gone
agentctl resume                   # live sessions + open threads from the memory palace
agentctl stop 'agent:myapp:claude'
```

What keeps work alive across drops:

- **Server-side tmux** — the agent process is independent of your SSH/WG link.
- **`loginctl enable-linger`** — your processes survive after the login session ends.
- **mosh** — a client link that survives IP changes, sleep, and roaming. From your Mac:
  ```bash
  mosh --ssh="ssh -i ~/.ssh/<key>" <you>@10.200.200.1 -- tmux -S ~/.agentctl/tmux.sock attach
  ```
- **sshd keepalive** tuned to tolerate brief WireGuard/internet blips (`sshd_config.d/10-resilience.conf`).

> Toggle the whole layer with `install_resilience_layer` (default true). mosh uses
> UDP `60000–61000`, opened only to the WireGuard subnet.

---

## 🏛️ Memory Palace

A structured, durable **project memory** lives in [`.memory-palace/`](.memory-palace/)
as markdown "rooms" (architecture, decisions, session log, open threads, glossary).
Humans and agents read it to reload full context after a disconnect or a fresh
session — the other half of resilience.

```bash
palace rooms                              # list rooms
palace show decisions                     # read a room
palace threads                            # what you were doing (read first on reconnect)
palace note open-threads "did X, next Y"  # append a timestamped note
palace note --share decisions "chose Z"   # also mirror to the shared context bus
palace recall "wireguard"                 # search rooms + the bus
```

`agentctl resume` prints your live sessions **and** the open threads in one shot —
the fastest way back into flow after the tunnel drops.

---

## 🛡️ Agent-OS — Tool Guardrails & Governance

Agents run with real tools; Agent-OS governs what they're allowed to do.

### Guardrails (PreToolUse policy)
A Claude Code **`PreToolUse` hook** evaluates every tool call **before it runs** and
can **deny**, **ask** (require confirmation), or **allow** — the correct enforcement
point, since MCP tool calls execute in the agent, not the gateway.

```bash
guardrail --log              # audit trail of recent decisions
guardrail --dump-policy      # the active rules (also at /etc/agent-os/policy.json)
```

The default policy **denies** catastrophic shell (`rm -rf /`, `mkfs`, `dd` to disk,
fork bombs, `shutdown`, force-push to `main`), **asks** for cloud/cluster mutations
(`oci/aws/gcloud … delete`, `terraform destroy`, `kubectl delete`), destructive SQL,
system installs, secret-file access, and writes outside home/shared/tmp — and allows
everything else. Edit `/etc/agent-os/policy.json` to tune. Every decision is
audit-logged; deny/ask also fire the notification ring and show on the board's
🛡️ Guardrail panel.

### Central MCP tool registry
One approved-servers source (`/opt/agent-os/registry.json`) generates each developer's
`~/.claude/.mcp.json` — so the fleet exposes one governed tool surface instead of
hand-maintained configs.

```bash
mcp-registry list            # approved servers
mcp-registry apply           # (re)generate ~/.claude/.mcp.json (merge-safe)
```

Re-applying **preserves your personal/experimental servers** and updates the approved
ones. Disabled registry entries are removed.

### OCI read-only MCP server
`oci-readonly` lets agents inspect tenancy/compartment/instance state (list/get) with
**no ability to mutate** — every tool maps to a fixed read-only `oci … list|get`, a
read-only verb allowlist refuses anything else. Defense in depth under the guardrail.

> Toggle the whole layer with `install_agent_os` (default true).

---

## 🌐 WireGuard VPN & Mac Routing

By design, this is a **split tunnel**: only the `10.200.200.0/24` VPN subnet is routed through WireGuard. Your Mac keeps its normal internet path and its normal DNS.

### Why there is no `DNS =` line (important)

Generated split-tunnel client configs **deliberately omit the `DNS =` line**. On macOS, a `DNS =` entry in a split-tunnel config makes the WireGuard app install those servers as the **system-wide** resolvers and scope them to the tunnel interface. Because the tunnel only routes `10.200.200.0/24`, that hijacks (and often breaks) name resolution the moment you connect — the classic "my internet/DNS dies after WireGuard connects" symptom. Every service here is reached **by IP** (`10.200.200.1:*`), so no in-tunnel DNS is needed.

A generated config looks like:

```ini
[Interface]
PrivateKey = <client_private_key>
Address = 10.200.200.3/24
# no DNS line — split tunnel keeps your Mac's own resolvers

[Peer]
PublicKey = <server_public_key>
Endpoint = <VM_PUBLIC_IP>:51820
AllowedIPs = 10.200.200.0/24
PersistentKeepalive = 25
```

### Full-tunnel opt-in

Want all traffic routed through the VM (e.g. for a clean exit IP)? Set in `.env.local`:

```bash
WG_FULL_TUNNEL=true            # AllowedIPs becomes 0.0.0.0/0, ::/0
WG_DNS="1.1.1.1, 8.8.8.8"      # only meaningful with a full tunnel
```

Both deployers (`deploy_multicloud.py` and `deploy_sdk.py`) honor these flags identically, and the behavior is locked by tests in `tests/test_deploy_multicloud.py`.

> **Already connected and seeing broken DNS?** The WireGuard **app** caches the config it imported, so editing the file isn't enough. Bring the tunnel up with `./scripts/connect.sh wg-up` (uses `wg-quick`, which reads the current file), or delete + re-import the tunnel in the app. See [Troubleshooting](#-troubleshooting).

---

## 🤝 Shared Collaboration Surface

### `/opt/shared-dev`
Group-writable workspace owned by `root:developers` with the SGID bit (`chmod 2770`) so new files inherit the `developers` group, and every developer runs `umask 002`. Symlinked into each home as `~/shared-workspace`.

### `pair-claude`
A shared-session helper at `/usr/local/bin/pair-claude` backed by a group-owned tmux socket:

```bash
pair-claude start    # developer 1 starts a shared Claude Code session
pair-claude join     # others attach to the live session
pair-claude status   # is a session active?
pair-claude note "decided to refactor auth"   # save a note to the shared context bus
pair-claude summary  # capture the transcript to the shared context bus
pair-claude kill     # auto-saves a summary, then tears down the socket
```

A developer joining later regains continuity with `context search pairing --shared`.

### RDP desktops
XFCE4 over XRDP (port `3389`), one simultaneous session per user. Ansible installs a Polkit colord rule (`/etc/polkit-1/localauthority/50-local.d/45-allow-colord.pkla`) so logins never get stuck on an authentication popup.

---

## ➕ Adding Developers

The deployer parses `.env.local` dynamically — you are not limited to three users.

```bash
MULTI_DEV_ENABLED=true
DEV_4_NAME="carlos"
DEV_4_SSH_KEY_PATH="~/.ssh/id_rsa_carlos.pub"
DEV_4_WG_IP="10.200.200.5"
DEV_4_CODE_SERVER_PORT=8446
```

Re-run `./scripts/deploy.sh --profile <OCI_PROFILE> --yes`. The deployer compiles the new keys, adds them to cloud-init + OCI security rules, and Ansible registers the UNIX user, code-server service, shared-workspace symlink, MultiLLM hooks, and a dashboard card.

---

## ✅ Implemented vs. Roadmap

| Capability | Status |
|---|---|
| OCI VM provisioning (CLI / SDK / multi-cloud) | ✅ Implemented |
| Multi-developer UNIX sandboxes + code-server | ✅ Implemented |
| WireGuard split-tunnel VPN (no-DNS default) + full-tunnel opt-in | ✅ Implemented |
| XFCE/XRDP desktops, Polkit fix | ✅ Implemented |
| `pair-claude` shared sessions, `/opt/shared-dev` | ✅ Implemented |
| AI CLIs (Claude / Codex / Gemini), Cursor | ✅ Implemented |
| Shared MultiLLM gateway service + `/dashboard` over VPN | ✅ Implemented |
| Per-user MultiLLM hooks, launchers, MCP registration | ✅ Implemented |
| Per-account GitHub identity in shared repos (`git-whoami`) | ✅ Implemented |
| Per-developer usage attribution (collectors → `tenant=<user>`) | ✅ Implemented |
| Team usage dashboard `/team` + `usage-report` CLI | ✅ Implemented |
| Per-user daily budget plumbing (`MULTILLM_USER_BUDGETS`) | ✅ Implemented |
| Dynamic per-developer landing dashboard cards | ✅ Implemented |
| Security gate scanner + tests | ✅ Implemented |
| Budget-breach warning UX + structured log sink | 🔭 Roadmap (Phase 1 tail) |
| Shared context bus — MCP tools + `context` CLI (scope by convention) | ✅ Implemented |
| Durable agent sessions (`agentctl`) surviving WG/SSH/internet drops | ✅ Implemented |
| Connection resilience — mosh, `loginctl` linger, sshd keepalive | ✅ Implemented |
| Memory palace (`.memory-palace/` + `palace` CLI) | ✅ Implemented |
| Hard per-tenant memory enforcement (X-MultiLLM-Tenant + MCP) | ✅ Implemented |
| Live multi-agent status board (`/agents.html`, `agent-status` timer) | ✅ Implemented |
| Project-status surface — per-project git state + active agents | ✅ Implemented |
| Notification ring — agent-needs-input alerts + browser/phone push | ✅ Implemented |
| Tool guardrails — PreToolUse deny/ask policy + audit + board panel | ✅ Implemented |
| Central MCP tool registry → per-user `.mcp.json` (merge-safe) | ✅ Implemented |
| OCI read-only MCP server (read-only verb allowlist) | ✅ Implemented |
| Read-only fleet control-plane API (VPN-only, `:8082`) | ✅ Implemented |
| Control-plane mutations — admin-token auth, queued account changes, live budgets | ✅ Implemented |
| Scheduled autonomous agent jobs (`agent-job`, per-user timer) | ✅ Implemented |
| **PAI / Obi per-user deploy** (`install_pai`, `pai-bootstrap`, 0700, never shared) | ✅ Implemented |
| **Pluggable agent-runtime registry** (Claude/Codex/Gemini + Antigravity/Hermes/nano-claw) | ✅ Implemented |
| **Multi-device encrypted MEMORY sync** (`pai-sync`, age, offline-first) | ✅ Implemented |
| Central MCP tool registry & policy/guardrail engine | 🔭 Roadmap |
| Control-plane REST API + fleet telemetry | 🔭 Roadmap |

See **[`ROADMAP-v2.md`](ROADMAP-v2.md)** for the agentic-OS direction.

---

## 🧬 PAI / Obi Integration (Phase 6)

Run the principal's own **PAI** (the Algorithm + skills + MEMORY + the *Obi* DA) on
the fleet, and carry the *same* Obi knowledge across all your machines — without
personal data ever sitting in plaintext in the cloud or on the shared VM.

The substrate (durable sessions, per-tenant isolation, MultiLLM gateway, guardrails,
agent jobs) already exists — Phase 6 is the **bridge**, not a rebuild.

```bash
# .env.local
INSTALL_PAI=true
PAI_REPO="git@github.com:<you>/pai.git"                 # skills/Algorithm/hooks (shareable)
PAI_MEMORY_REPO="git@github.com:<you>/pai-memory.git"   # PRIVATE, age-encrypted MEMORY
PAI_SYNC_ENABLED=true
PAI_AGE_RECIPIENTS="age1...,age1..."                    # one public key per device
```

- **Per-user, private** — PAI installs into each developer's `0700` home; personal
  MEMORY (TELOS/health/finances) is `age`-encrypted at rest, decrypted only in
  session, **never** in `/opt/shared-dev`.
- **Same Obi everywhere** — `pai-sync` keeps MEMORY identical across Macs/Ubuntu/
  Windows via a private encrypted repo (offline-first; add a cloud remote by config).
- **Governed multi-runtime** — `pai-runtimes` registers Claude, Codex, Gemini, and
  **Antigravity (AGY), Hermes, nano-claw/OpenClaw** behind one data file; all inherit
  the PreToolUse guardrail and route usage through the MultiLLM gateway.

Docs: [`docs/PAI-INTEGRATION.md`](docs/PAI-INTEGRATION.md) ·
[`docs/MULTI-DEVICE-SYNC.md`](docs/MULTI-DEVICE-SYNC.md) ·
[`docs/AGENT-RUNTIMES.md`](docs/AGENT-RUNTIMES.md). The capability is tracked as an
ISA at [`ISA.md`](ISA.md).

---

## 🛡️ Security Gate & Redaction

```bash
python3 scripts/security_gate.py --mode full
```

Never commit real topology or secrets. Always use placeholders:

- Tenancy/compartment OCIDs → `<OCI_TENANCY_OCID>`, `<OCI_COMPARTMENT_OCID>`
- Public IPs → `<VM_PUBLIC_IP>` (never `130.61.*`, `129.153.*`, `141.147.*`, …)
- Registry namespace → `<OCI_TENANCY_NAMESPACE>`
- Keys → `<ANTHROPIC_API_KEY>`, `<OPENAI_API_KEY>`, `<GOOGLE_AI_API_KEY>`

All of `configs/` (WireGuard keys, deployment info, rendered vars) and `.env.local` are git-ignored.

---

## 🧪 Testing

```bash
pip install -r requirements-test.txt
pytest tests/ -v
```

Covers cloud-init rendering, security-gate logic, and the WireGuard split-tunnel/no-DNS routing fix.

---

## 💡 Troubleshooting

### Internet / DNS breaks after WireGuard connects (macOS)

**Symptom:** raw-IP `ping 1.1.1.1` works, but names don't resolve and nothing loads.

**Root cause:** the **WireGuard macOS app stores a *copy* of the config inside its
NetworkExtension at import time.** Editing `client_<you>.conf` on disk afterward does
**not** update what the app pushes — so a stale `DNS = 1.1.1.1, 8.8.8.8` line keeps
getting installed as the system resolver, scoped to the tunnel. On a split tunnel
(`AllowedIPs = 10.200.200.0/24`) those DNS servers aren't routed through the tunnel, so
every query blackholes.

**Fix — use `wg-quick` (reads the current file, no cache):**

```bash
./scripts/connect.sh -u <you> wg-up      # uses wg-quick under the hood; prompts for sudo
./scripts/connect.sh -u <you> wg-down
```

`connect.sh` injects the Homebrew prefix into the sudo PATH so `wg-quick` finds bash 4+
(macOS ships 3.2) and the `wg` binary. If you previously imported the tunnel into the
**app, delete that tunnel** (`WireGuard.app → select → minus`) so it can't reconnect with
the stale DNS.

**If you must keep using the app:** delete the tunnel and **re-import** the regenerated
`client_<you>.conf` (the `DNS =` line is gone); a plain re-activate is not enough. To run
`connect.sh` against the app instead of `wg-quick`, set `WG_USE_APP=1`.

See [WireGuard VPN & Mac Routing](#-wireguard-vpn--mac-routing).

### Landing page at `http://10.200.200.1` not loading
1. `ping 10.200.200.1` — are you on the VPN?
2. `sudo systemctl status dev-dashboard`
3. `sudo ufw status` — expect `22/tcp`, `80/tcp`, `3389/tcp`, `51820/udp`, and `8080/tcp` from `10.200.200.0/24`.

### MultiLLM dashboard at `:8080` not loading
```bash
sudo systemctl status multillm-gateway
sudo journalctl -u multillm-gateway -n 50 --no-pager
```
Confirm API keys exist in `/opt/multillm/.env`.

### Restart a developer's services
```bash
sudo systemctl restart code-server@adi
sudo systemctl restart xrdp
```

> For provisioning failures, consult `KB/oci-provisioning/ISSUE-CATALOG.md` first — it catalogs known OCI failure patterns and fixes.

---

## 📄 License

Distributed under the [MIT License](LICENSE).
