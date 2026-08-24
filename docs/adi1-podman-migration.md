# adi1 Podman service migration — runbook

Migration of the macOS Podman machine services onto **adi1** (`192.168.10.163`,
Ubuntu 24.04, aarch64, rootless Podman 4.9.3). Performed by firstmate worker
`fm/adi1-podman-service-migration`; Mac originals were left untouched and remain
the rollback path until cutover is ordered.

## Source inventory (measured on the Mac podman machine)

| Container | Image | Data volume | Size | Host port | Restart |
|---|---|---|---|---|---|
| homeassistant | home-assistant/home-assistant:stable (2026.5.2, arm64) | ha-config | 5.9M | 8123 | unless-stopped |
| rai-spoolman | ghcr.io/donkie/spoolman:latest (0.23.1) | spoolman-data | 3.5M | 8000 | unless-stopped |
| cyberdesk-pg | postgres:17-alpine | anonymous volume `94cb0325…` | 64M | 5432 | no |
| handyman-pg | postgres:16 | anonymous volume `da63b79b…` | 74M | 5432 | no |
| supabase_db_platform | supabase/postgres:17.6.1.106 | supabase_db_platform | 68M | 54322→5432 | unless-stopped |
| supabase_kong_platform | supabase/kong:2.8.1 | none (config baked into entrypoint) | – | 54321→8000 | unless-stopped |
| supabase_rest_platform | supabase/postgrest:v14.10 | none | – | – | unless-stopped |
| supabase_pg_meta_platform | supabase/postgres-meta:v0.96.4 | none | – | – | unless-stopped |

Notes:

* cyberdesk-pg and handyman-pg both bound host port 5432 on the Mac and can
  never run simultaneously there.
* The supabase stack is compose project `platform` on podman network
  `supabase_network_platform` (aliases: db/db.supabase.internal,
  rest, pg_meta, kong/api.supabase.internal).
* Dangling volumes not attached to any container were archived but not wired:
  bot-state, telegrambot_bot-state, supabase_db_wlpwvxmivolpizplybor,
  two anonymous volumes. Archive tarballs live on adi1 under
  `~/podman-migration/archives/`.

## Target port map (documented deviation)

| Service | Mac port | adi1 port | Reason |
|---|---|---|---|
| homeassistant | 8123 | 8123 | unchanged |
| rai-spoolman | 8000 | 8000 | unchanged |
| cyberdesk-pg | 5432 | 5432 | unchanged |
| handyman-pg | 5432 | **5433** | collision-free coexistence (impossible on the Mac) |
| supabase db / kong | 54322 / 54321 | 54322 / 54321 | unchanged |

## Migration methods

* All images pulled natively as `linux/arm64` on adi1 — no x86 layers copied.
* **homeassistant / spoolman**: named-volume copy. Source container stopped
  (HA) or already exited (spoolman), `tar` of the volume streamed over SSH into
  the freshly created target volume. HA file list verified identical modulo
  SQLite WAL/log files recreated at every boot.
* **cyberdesk-pg / handyman-pg** (vanilla Postgres): `pg_dump -Fc` streamed over
  SSH into fresh containers, plus `pg_dumpall --globals-only` applied first so
  global roles exist (alpine images bootstrap the login role as superuser, not
  `postgres`). Row/table/grant counts verified equal to source.
* **supabase_db_platform**: physical volume copy while stopped. Rationale: same
  major version and image build on both sides and both aarch64, and the cluster
  carries Supabase-specific roles/extensions/event triggers that make logical
  restore noisy; byte count verified identical (70,182,226 bytes).
* **supabase kong/rest/pg_meta**: containers recreated with the original env;
  the giant heredoc entrypoints (kong declarative config, db init SQL) were
  extracted verbatim from the source container configs with
  `scripts/extract-container-entrypoint.sh`, stored server-side under
  `~/podman-migration/` on adi1, and bind-mounted read-only as the container
  command. They contain deployment-specific material (declarative routes,
  pgsodium root key) and are deliberately **not committed** to this public
  repository.

## Rebuild commands

Full recreate commands are recorded in `docs/adi1-container-commands.md`
(generated from the live configs; env *names* documented, secret *values*
redacted). Entrypoint payloads are regenerated from the source machines with
`scripts/extract-container-entrypoint.sh <container-name>` when needed.

## Persistence on adi1

* `loginctl enable-linger adi` enabled.
* `podman generate systemd --new --name` units installed under
  `~/.config/systemd/user/` for all eight containers; supabase dependents carry
  `Requires=`/`After=` on `supabase_db_platform.service`.
* Enabled at boot: homeassistant, rai-spoolman, supabase_{db,rest,pg_meta,kong}.
  cyberdesk-pg and handyman-pg units exist but stay disabled (faithful to their
  `restart=no` origin); start manually with
  `systemctl --user start cyberdesk-pg.service` etc.

## Verification (from the Mac, over LAN)

| Check | Result |
|---|---|
| `curl http://192.168.10.163:8123/` | HTTP 200, migrated instance (onboarding marked done) |
| `curl http://192.168.10.163:8000/api/v1/health` | healthy; spool/filament/vendor counts match source (0/0/0) |
| psql → 192.168.10.163:5432 cyberdesk | 21 public tables, grants intact |
| psql → 192.168.10.163:5433 handyman_dev | 42 app-schema tables across core/work/billing/… schemas |
| psql → 192.168.10.163:54322 postgres | roles anon/authenticator/service_role present |
| `curl http://192.168.10.163:54321/rest/v1/` | HTTP 200 through kong→postgrest chain |
| `curl http://192.168.10.163:54321/pg/server-properties` | HTTP 200 through kong→pg-meta |

## LLM integration (Ollama)

* Ollama listens on `*:11434` on adi1 with llama3.3:70b, qwen2.5-coder:32b,
  qwen3-coder:30b, deepseek-coder-v2:16b.
* Home Assistant runs on the same host; the native Ollama integration was wired
  by adding a config entry (`.storage/core.config_entries`) pointing at
  `http://host.containers.internal:11434` with model `llama3.3:70b`.
* Verified: HA auto-migrated the entry to v3 creating `conversation` +
  `ai_task_data` subentries for the model and registered the entities; zero
  setup errors in `home-assistant.log`.
* Not forced elsewhere: spoolman and the Postgres stacks have no clear LLM
  touchpoints.

## Rollback posture

Mac originals untouched: homeassistant still runs locally, spoolman/PG/supabase
kept their pre-migration states (exited), all source volumes intact. Cutover
(stop Mac side permanently) remains a captain decision.

## Kali on adi1 — measured blocker

The local VMware Fusion guest `/Volumes/ExternalNVME/VM's/KaliLinux.vmwarevm`
is **arm64** (`guestOS = "arm-debian13-64"`, EFI, nvme disk node): it can run
natively on adi1. Measured facts:

* 4 vCPU / 10240 MB RAM configured.
* 189 GB directory footprint: base `Virtual Disk.vmdk` + delta chains
  (`-000001` … `-000006`) ≈ 149 GB, four snapshot memory files (`.vmem`)
  ≈ 40 GB.
* Four snapshots; the VM is **currently running** (lock dirs present, leaf
  extents written minutes ago).

A consistent copy therefore needs the VM powered off or suspended first, which
is a disruption decision above the worker's authority (reported via status file
as `needs-decision [key=kali-live-vm]`). adi1 already has `qemu-system-aarch64`,
`qemu-utils`, and `qemu-efi-aarch64` installed, so either option below starts
immediately after the decision:

1. **Migrate as-is (full fidelity)** — suspend/power off the Fusion guest for a
   ~45–60 min window, copy the disk chain, `qemu-img convert -O qcow2` on adi1
   (snapshots/vmem excluded unless wanted), boot headless
   (`qemu-system-aarch64 -M virt -m 8G -cpu host -bios QEMU_EFI.fd`, NVMe
   device, usermode networking with hostfwd tcp:2222→22), verify SSH.
2. **Fresh arm64 Kali instead** — download the official Kali arm64 cloud qcow2
   (<1 GB) straight onto adi1 and boot it the same way; zero impact on the Mac
   lab, existing snapshots/state not migrated.
