# Phase 6 — PAI / Obi Integration

This phase makes **PAI** (the principal's personal AI infrastructure — the
Algorithm, skills, MEMORY, and the *Obi* DA) a first-class, portable,
privacy-isolated tenant of the agentic dev fleet. Phases 0–5 already built the
substrate (durable sessions, per-tenant isolation, the MultiLLM gateway,
guardrails, agent jobs); this phase deploys **PAI itself** on top of it and makes
the *same* Obi available across the principal's own devices.

> **The key insight:** the fleet already solved durability, isolation, cmux, the
> gateway, and agent jobs. PAI didn't need a rebuild — it needed a *bridge*. This
> phase is that bridge.

## What it adds

| Piece | File | Purpose |
|---|---|---|
| Per-user PAI bootstrap | `scripts/pai_bootstrap.sh` | Clone/update `~/.claude/PAI` per developer, 0700, never in `/opt/shared-dev` |
| Ansible task (toggle) | `ansible/pai_tasks.yml` | `install_pai` gates the whole layer; installs `age`, the CLIs, runs bootstrap per-user |
| Pluggable runtime registry | `agent-os/runtimes.json` + `scripts/pai_runtime_registry.py` | One data source for every agent backend (Claude/Codex/Gemini/**Antigravity/Hermes/nano-claw**) |
| Multi-device encrypted sync | `scripts/pai_sync.py` | `age`-encrypted MEMORY sync via a private repo — see [MULTI-DEVICE-SYNC.md](MULTI-DEVICE-SYNC.md) |

## Architecture

```
   Principal's devices                          Shared OCI VM (multi-tenant)
 ┌───────────────────────┐                  ┌──────────────────────────────────┐
 │ Mac · Mac · Ubuntu ·  │   WireGuard      │  /home/adi/.claude/PAI  (0700)    │
 │ Windows               │═════════════════▶│    skills/ Algorithm/ hooks/      │
 │                       │   (cmux / SSH /  │    MEMORY/  ← age-encrypted at rest│
 │  ~/.claude/PAI        │    mosh / web)   │    USER/    ← decrypted in-session │
 │   full Obi, full      │                  │                                   │
 │   MEMORY              │                  │  agent-os/runtimes.json (governed)│
 └──────────┬────────────┘                  │  PreToolUse guardrail (inherited) │
            │                               │  MultiLLM gateway (per-tenant)    │
   pai-sync │ (age-encrypted)               └──────────────────────────────────┘
            ▼
   github.com/<you>/pai-memory  (PRIVATE, ciphertext only)
```

PAI's **shareable** parts (skills, Algorithm, hooks) ride the normal PAI repo.
PAI's **personal** parts (MEMORY, USER → TELOS, health, finances, identity) ride a
*separate private* repo, encrypted with `age`. Code lives in normal GitHub repos.
Each thing lives where its sensitivity dictates.

## How PAI rides the existing substrate (reuse, not rebuild)

- **Durability across drops** — PAI agents launch via the existing `agentctl`
  (detached server-side tmux + `loginctl` linger + mosh). A WireGuard/SSH/laptop
  drop does not stop Obi's work; reattach with `agentctl attach`.
- **Privacy on the shared VM** — PAI lives in the per-user 0700 home; personal
  MEMORY is `age`-encrypted at rest and only decrypted in the owning session.
  Nothing PAI-personal is ever symlinked into `~/shared-workspace`.
- **Governance** — every PAI-launched runtime inherits the `PreToolUse` guardrail
  (deny/ask on destructive calls). A runtime entry **cannot** opt out of the gate.
- **Observability/cost** — gateway-routed runtimes report usage to the MultiLLM
  gateway tagged with the tenant, so Obi's spend shows on `/team` like any agent.
- **Autonomous work** — `agent-job` can run an Obi runtime non-interactively on a
  per-user timer, guardrail-gated.

## Deploy

```bash
# .env.local
INSTALL_PAI=true
PAI_REPO="git@github.com:<you>/pai.git"            # shareable PAI (skills/Algorithm/hooks)
PAI_MEMORY_REPO="git@github.com:<you>/pai-memory.git"  # PRIVATE encrypted MEMORY (optional)
PAI_SYNC_ENABLED=true
PAI_AGE_RECIPIENTS="age1yourdevicekey..."          # one per device; ciphertext only at rest
PAI_RUNTIMES="claude,codex,gemini,antigravity,hermes,nanoclaw"
```

```bash
./scripts/deploy.sh --profile <OCI_PROFILE> --yes   # materializes Phase 6 behind the toggle
```

After deploy, on the VM (as your user):

```bash
pai-runtimes list                 # the governed agent backends
pai-runtimes resolve agy -p "..." # what would launch (AGY = Antigravity)
pai-sync status                   # MEMORY sync state
```

## Status / pending

- Runtime command templates for **Antigravity, Hermes, nano-claw** are placeholders
  until the principal supplies the exact binaries/flags — edit `agent-os/runtimes.json`
  (data, not code). The registry validates and resolves them today.
- Live deployment is **deferred** until the VM connection details land. All code,
  toggles, and tests ship now; `deploy.sh` materializes the layer when run.
