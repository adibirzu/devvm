# 📜 Session Log

Newest first. Move finished threads here from `OPEN-THREADS.md`.

## 2026-05-30 — WireGuard fix, MultiLLM observability, context bus, resilience

- **WireGuard routing fix (root cause + hardening).** Removed the `DNS =` line from
  split-tunnel client configs (the macOS DNS-blackhole bug); standardized both
  deployers on the shared `wg_config.render_wg_client_config()`; added
  `WG_FULL_TUNNEL`/`WG_DNS` opt-ins; stripped DNS from the live on-disk configs.
- **Shared MultiLLM gateway** as a hardened systemd service on `10.200.200.1:8080`,
  VPN-scoped, with a dynamic per-developer landing dashboard.
- **Phase 1 — observability.** Per-user collectors tag usage `tenant=<user>`; `/team`
  dashboard; `usage-report` CLI (aggregate + `--team` + `--budgets`); daily
  budget-breach timer.
- **Phase 2 — context bus.** `context` CLI over the gateway memory API with
  `user-<whoami>` / `--shared` scoping; documented that enforcement is multillm-side.
- **WireGuard app-cache fix.** `connect.sh` now uses `wg-quick` (live file) with a
  PATH shim for the macOS bash-3.2 / `wg`-not-on-PATH problems.
- **Resilience + memory palace (this entry's work).** `agentctl` durable tmux agent
  sessions; mosh + `loginctl` linger; this memory palace.

Commits on `main` (local; no git remote configured yet):
`dd68e8d` WireGuard fix + shared gateway · `ec8cf36` shared wg renderer ·
`3a09c24` Phase 1 observability · `beea5cf` budget checks · `0e30807` .env + validation ·
`91748bd` Phase 2 context bus · `5a4c8ae` wg-quick app-cache fix.
