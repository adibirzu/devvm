# 🏗️ Architecture

## One-paragraph
An OCI VM (`VM.Standard.E6.Flex`, Ubuntu 24.04) is provisioned by
`scripts/deploy_multicloud.py` (the `deploy.sh` entrypoint) and configured by an
idempotent Ansible playbook. Every developer gets an isolated UNIX account with
its own `code-server`, XFCE/XRDP desktop, shell, OAuth sessions, and API keys.
Everything is reachable **only over a split-tunnel WireGuard VPN** (`10.200.200.0/24`).
A shared **MultiLLM gateway** proxies and meters all agent LLM traffic.

## Layers
- **Provisioning** — `deploy_multicloud.py` (primary), `deploy_sdk.py` (SDK path),
  `templates/cloud-init.yaml.tpl` (network + WireGuard boot hook). WG client configs
  rendered by the shared `scripts/wg_config.py` (split-tunnel, no DNS by default).
- **Configuration** — `ansible/playbook.yml` includes `ansible/multillm_tasks.yml`
  (gateway + per-user collectors) and resilience tasks (durable sessions, mosh).
- **LLM plane** — MultiLLM gateway as a hardened systemd service bound to
  `10.200.200.1:8080`; per-user collectors push usage tagged `tenant=<user>`;
  `/team` + `usage-report` for visibility; `context` CLI + MCP tools for memory.
- **Agent plane** — `agentctl` runs coding agents in detached tmux on the VM so
  they survive disconnects; `palace` + `.memory-palace/` hold project memory.

## Network / reachability (all VPN-only)
| Port | Service |
|------|---------|
| 80 | Developer landing dashboard (per-user cards) |
| 8080 | MultiLLM gateway + `/dashboard` + `/team` |
| 8443+ | per-user `code-server` |
| 3389 | XRDP desktops |
| 60000–61000/udp | mosh (resilient client link) |

## Key isolation boundary
Per-UNIX-user sandboxes are the security boundary. Shared surfaces are deliberate:
`/opt/shared-dev` (SGID, group `developers`), the `pair-claude` socket, the shared
gateway, and the `shared` context-bus namespace.
