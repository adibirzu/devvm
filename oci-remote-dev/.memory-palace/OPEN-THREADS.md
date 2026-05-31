# 🧵 Open Threads

What's in flight. **Read this first on reconnect.** Move finished items to `SESSION-LOG.md`.

## Recently shipped
- [x] Phase 4 read-only control-plane API (GET /fleet/status, /developers, /fleet/services).
- [x] Phase 1 complete (gateway-health pill + budget banner on board). Phase 2 pairing
      integration: pair-claude note/summary/kill → shared context bus.
- [x] Phase 3 (Agent-OS): tool guardrails (PreToolUse deny/ask policy + audit + board
      panel), central MCP registry → per-user `.mcp.json`, OCI read-only MCP server.
- [x] Mobile-friendly board; notification ring (agent-needs-input → pulsing ring +
      browser/phone push via Claude Notification hook → `agent-notify` feed).

## Active direction
Build the **Linux/server equivalent of cmux / AgentsRoom**: run multiple coding
agents in parallel on the VM with visibility (status, project, LLM usage), local
project integration, and resilience to disconnects. AgentsRoom is macOS-only today;
this brings the experience to the VPN-reachable Linux VM.

## In progress
- [x] Durable agent sessions (`agentctl`) — agents survive WG/SSH/internet drops.
- [x] Connection resilience — mosh + `loginctl enable-linger` + tmux config (Ansible).
- [x] Memory palace (this directory) + `palace` CLI.
- [x] Live multi-agent status board — `agent-status` aggregator + `/agents.html`
      board page + 15s timer (`agentctl ls --json` → `agents.json`). Shows agents ×
      projects × state × 24h LLM cost across all developers.
- [x] Project-status surface: `project-status` aggregator + Projects section on the
      board — per-project git state (branch/dirty/ahead-behind/last commit) × active agents.

## Next candidates
- [ ] Phase 3 proper: central MCP tool-registry + guardrail policy (multillm-side
      enforcement of tenant scope + deny/confirm on destructive tool calls).
- [x] Split `devvm` into its own **private** repo (github.com/adibirzu/devvm).
      `~/dev/devvm` now tracks it; parent `~/dev` no longer double-tracks. Pre-push
      audit caught + fixed real tenancy namespaces hardcoded in `security_gate.py`.
- [x] Session persistence across VM reboots — `agentctl restore` + boot service
      (metadata replay; more reliable than tmux-resurrect for CLI agents).

## Watch-outs
- **This is now its own git repo** (`github.com/adibirzu/devvm`). Work in `~/dev/devvm`;
  `git push` goes to the private repo. Don't re-add it to the parent `~/dev` repo.
- WireGuard macOS **app** caches imported configs — always use `connect.sh wg-up`
  (wg-quick) or delete+re-import. See `DECISIONS.md`.
- `gh` needs `GITHUB_TOKEN` unset (an invalid one was overriding stored creds).
- No git remote yet; `git push` has no destination. Everything is local on `main`.

