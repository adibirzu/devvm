# 🧵 Open Threads

What's in flight. **Read this first on reconnect.** Move finished items to `SESSION-LOG.md`.

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
- [ ] Project-status surface: per-project git state alongside active agents.

## Next candidates
- [ ] Phase 3 proper: central MCP tool-registry + guardrail policy (multillm-side
      enforcement of tenant scope + deny/confirm on destructive tool calls).
- [ ] Decide git remote strategy — repo root is `~/dev` (multi-project); likely split
      `devvm` into its own repo before any push so siblings aren't published.
- [ ] tmux-resurrect/continuum for session persistence across VM reboots.

## Watch-outs
- WireGuard macOS **app** caches imported configs — always use `connect.sh wg-up`
  (wg-quick) or delete+re-import. See `DECISIONS.md`.
- No git remote yet; `git push` has no destination. Everything is local on `main`.

