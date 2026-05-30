# 📖 Glossary

- **agentctl** — durable multi-agent session manager; runs agents in detached tmux on
  the VM so they survive disconnects (`scripts/agentctl.sh` → `/usr/local/bin/agentctl`).
- **palace / memory palace** — this `.memory-palace/` directory of structured "rooms";
  durable project memory for humans and agents (`scripts/palace.sh` → `/usr/local/bin/palace`).
- **context bus** — the gateway's shared memory/context store, reached via the `context`
  CLI and `llm_memory_*` MCP tools. Scoped `user-<whoami>` / `shared`.
- **collector** — per-developer `multillm-collector@<user>.timer` that pushes local
  AI-CLI usage to the gateway tagged `tenant=<user>`.
- **split tunnel** — WireGuard mode where only `10.200.200.0/24` is routed; the default.
- **full tunnel** — opt-in (`WG_FULL_TUNNEL=true`) routing all client traffic via the VM.
- **tenant** — a developer's identity for usage attribution and memory scoping.
- **linger** — `loginctl enable-linger <user>`; keeps a user's processes alive after
  their login session ends, so detached agents persist.
- **mosh** — mobile shell; a client link that survives IP changes, sleep, and roaming.
- **emdemo / cap / DEFAULT** — OCI tenancy profiles (see global rules); this project
  deploys to the profile passed via `--profile`.
