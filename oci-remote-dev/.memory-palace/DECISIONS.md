# 🧭 Decisions (and why)

Append-only. Capture the rationale the code can't.

- **2026-05-30 — WireGuard is split-tunnel with NO `DNS =` line by default.**
  Why: on macOS a `DNS =` entry in a split-tunnel config installs those servers as
  the system resolver scoped to the `utun` interface; since `AllowedIPs` only routes
  `10.200.200.0/24`, DNS queries blackhole → "internet dies after connect." Services
  are reached by IP, so no in-tunnel DNS is needed. Full-tunnel + DNS is opt-in via
  `WG_FULL_TUNNEL` / `WG_DNS`.

- **2026-05-30 — Bring tunnels up with `wg-quick`, not the WireGuard macOS app.**
  Why: the app stores a COPY of the config in its NetworkExtension at import time;
  editing the `.conf` later doesn't update it, so a stale DNS line keeps breaking DNS.
  `wg-quick` reads the live file. `connect.sh` injects the Homebrew prefix into the
  sudo PATH (macOS ships bash 3.2; wg-quick needs 4+ and also needs `wg` on PATH).

- **2026-05-30 — One SHARED MultiLLM gateway, not per-user gateways.**
  Why: per-user gateways would all collide on :8080 and bind localhost (unreachable
  over the VPN). A single hardened service binds the WG IP. Per-developer attribution
  is achieved by per-user **collectors** that push local stats tagged `tenant=<user>`
  — attribution at collection time, which works fine with one shared gateway.

- **2026-05-30 — Single shared `wg_config.render_wg_client_config()`.**
  Why: the two deployers had drifted (one split-tunnel, one full-tunnel) — that drift
  WAS the original bug. One tested renderer makes drift impossible.

- **2026-05-30 — Scope in the `context` bus is convention, not enforcement (yet).**
  Why: the gateway's memory API scopes by free-text `project`; `tenant_id` columns
  exist but aren't enforced in the HTTP layer. The `context` CLI namespaces by
  `user-<whoami>` / `--shared` by convention. Hard enforcement is multillm-side work.

- **2026-05-30 — Agents run in detached tmux on the VM (`agentctl`).**
  Why: decoupling the agent process from the SSH/WG client is what makes work survive
  disconnects and resumable on reconnect. Paired with `loginctl enable-linger` and mosh.
