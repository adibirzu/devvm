# 🌌 OCI Agentic Development OS — Multi-Developer Remote Workspace

A secure, production-grade **multi-developer remote development OS** built on Oracle Cloud Infrastructure (OCI). One command provisions a high-performance Ubuntu VM, wires up a private WireGuard mesh, gives every developer a fully isolated UNIX sandbox (terminal, web IDE, RDP desktop, OAuth/API-key vault), and runs a shared **MultiLLM gateway** so all AI-agent traffic is proxied, tracked, and observable from a single dashboard.

You drive it from your Mac with the native **cmux** agent workspace over the VPN; the heavy lifting (agents, desktops, builds) runs on the remote VM.

---

## 🚀 What You Get

- **👥 Isolated multi-developer sandboxes** — dedicated UNIX accounts (`devuser`, `adi`, `royce`, … unlimited) each with their own `code-server`, XFCE/XRDP desktop, shell, OAuth sessions, and API keys. Nothing leaks between users.
- **🔐 Split-tunnel WireGuard VPN** — every service is reachable **only** over the private `10.200.200.0/24` tunnel. Defaults are tuned so the VPN never hijacks your Mac's DNS or internet routing.
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
                                              │  SSH:22  WG:51820 (only)
                                   ┌──────────┴──────────┐
                                   │   OCI VM Instance   │  <VM_PUBLIC_IP>
                                   │ VM.Standard.E6.Flex │  4 OCPU / 32 GB / Ubuntu 24.04
                                   └──────────┬──────────┘
                                              │
        Local Mac (cmux) ───[ WireGuard split tunnel 10.200.200.0/24 ]───┐
                                              │                          │
                          ┌───────────────────┴───────────────────────┐  │
                          │  WireGuard Server  wg0  → 10.200.200.1     │◄─┘
                          └───────────────────┬───────────────────────┘
                                              │
        ┌─────────────────────────────────────┼─────────────────────────────────────┐
        │  Reachable ONLY over the VPN at 10.200.200.1                                │
        │                                                                            │
        │   :80   Developer landing dashboard (per-user cards)                        │
        │   :8080 MultiLLM gateway + /dashboard (usage, cost, latency)                │
        │   :3389 XRDP visual desktops (XFCE, one session per user)                   │
        │                                                                            │
        │   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐                    │
        │   │ devuser      │   │ adi          │   │ royce        │   … unlimited      │
        │   │ code-server  │   │ code-server  │   │ code-server  │                    │
        │   │  :8443       │   │  :8444       │   │  :8445       │                    │
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
./scripts/setup-wizard.sh        # interactive → renders .env.local from .env.example
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
| Developer landing dashboard | `http://10.200.200.1` |
| MultiLLM usage dashboard | `http://10.200.200.1:8080/dashboard` |
| `devuser` Web IDE | `http://10.200.200.1:8443` |
| `adi` Web IDE | `http://10.200.200.1:8444` |
| `royce` Web IDE | `http://10.200.200.1:8445` |
| RDP desktops | `10.200.200.1:3389` |

---

## 💻 cmux Native macOS Workspace

While the server runs on Linux, you drive it from your Mac with [**cmux**](https://cmux.com/) — the native, Swift-based terminal workspace built for AI agents.

**Download cmux locally** on each developer's Mac, then connect its tabbed agent panes to your isolated remote sandbox over the WireGuard tunnel:

```bash
ssh -i ~/.ssh/<your_key> adi@10.200.200.1
```

Use cmux's tabbed agent panels, vertical splits, **glow rings**, and macOS **notification rings** to orchestrate remote agents (Claude Code, Codex, Gemini) with real-time visual alerts when an agent is waiting for input. Each tab is a live SSH session into your own UNIX account on the VM — your panes, your shell, your agents, nobody else's.

> cmux is a **local** macOS app; nothing is installed server-side for it. The VM just needs to be reachable over the VPN (it is, by default, on `10.200.200.1`).

---

## 🔐 Personal Agent & OAuth / API-Key Isolation

Because every developer runs under a **dedicated, isolated UNIX account**, all credentials and agent state are fully sandboxed:

- **Terminals & shell** — each user has their own `/home/<username>` and `~/.bashrc`. Environment exports, aliases, and `PATH` never collide.
- **API keys** — developers export `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_AI_API_KEY` in their **own** `~/.bashrc`. They are never readable by other users (home dirs are `0750`).
- **OAuth sessions** — Claude Code CLI browser logins are written to per-user config dirs (e.g. `~/.config/@anthropic-ai/claude-code/`). One developer signing in **never** overwrites or exposes another's session.
- **MCP & agent config** — `~/.claude/.mcp.json`, `~/.claude/hooks.json`, and Codex's `~/.codex/config.toml` are all per-user.

The only **shared** surface is the deliberate one: `/opt/shared-dev` (group `developers`, SGID, `umask 002`) symlinked into each home as `~/shared-workspace`, plus the `pair-claude` socket. Sharing is opt-in, not accidental.

---

## 📊 MultiLLM Gateway & Usage Monitor

The repo at `/Users/abirzu/dev/multillm` is automatically synced to **`/opt/multillm`** on the VM during deploy, installed system-wide, and run as a **single shared gateway service** reachable over the VPN.

### Architecture

- **Shared system service** — `multillm-gateway.service` runs `python -m multillm.gateway` bound to `0.0.0.0:8080`, reachable at `http://10.200.200.1:8080` over the tunnel. One gateway for the whole VM (no per-user port collisions), firewalled to the WireGuard subnet only.
- **Per-user auto-start hook** — each developer's `~/.claude/hooks.json` gets a `SessionStart` hook (`hooks/start-gateway.sh`) that health-checks the gateway and starts a local one only if the shared service is down. Running `claude` "just works."
- **Per-user launchers** — `~/.local/bin/claude-multillm` and `~/.local/bin/codex-multillm` point `ANTHROPIC_BASE_URL` at the gateway so all agent traffic is proxied and tracked.
- **MCP registration** — the `multillm` MCP server is registered in each user's `~/.claude/.mcp.json` (and via `codex mcp add` for the Codex CLI) with `LLM_GATEWAY_URL=http://localhost:8080`.
- **Live dashboard** — token counts, cost rollups, response latency, and side-by-side backend comparisons at:

  ```
  http://10.200.200.1:8080/dashboard
  ```

### Toggles

```bash
INSTALL_MULTILLM_GATEWAY=true   # set false to skip the shared gateway
MULTILLM_GATEWAY_PORT=8080      # change the gateway/dashboard port
```

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

> **Already connected and seeing broken DNS?** Re-import the regenerated `configs/wireguard/client_<you>.conf` into the WireGuard app (the `DNS =` line has been removed) and reconnect.

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
pair-claude kill     # tear down and clean up the socket
```

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
| Dynamic per-developer landing dashboard cards | ✅ Implemented |
| Security gate scanner + tests | ✅ Implemented |
| Cross-developer shared agent memory / context bus | 🔭 Roadmap (see `ROADMAP-v2.md`) |
| Central MCP tool registry & policy/guardrail engine | 🔭 Roadmap |
| Control-plane REST API + fleet telemetry | 🔭 Roadmap |

See **[`ROADMAP-v2.md`](ROADMAP-v2.md)** for the agentic-OS direction.

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
Your client config has a stale `DNS =` line. Re-import the regenerated `configs/wireguard/client_<you>.conf` (DNS removed) and reconnect, or set `WG_DNS=""` and redeploy. See [WireGuard VPN & Mac Routing](#-wireguard-vpn--mac-routing).

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
