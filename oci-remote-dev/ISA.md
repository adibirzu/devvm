---
project: oci-remote-dev
task: PAI/Obi integration — portable multi-device life-OS on the agentic dev fleet
effort: E3
phase: verify
progress: 40/42
mode: build
started: 2026-05-31
updated: 2026-05-31
---

# ISA — Phase 6: PAI / Obi Integration

> Project ISA for the PAI-integration capability added to `oci-remote-dev`.
> The fleet already delivers durable sessions, per-tenant isolation, cmux, the
> MultiLLM gateway, guardrails, and agent jobs (Phases 0–5). This phase makes
> **PAI itself** — the Algorithm, skills, MEMORY, and the Obi DA — a first-class,
> portable, privacy-isolated tenant of that fleet, and syncs it across the
> principal's own devices.

## Problem

The principal runs PAI/Obi locally on one Mac. The request is to use the *same*
Obi — same knowledge and same workflows — across 3–4 Macs plus Ubuntu and
Windows machines in different locations, to keep work running on a backend when a
remote connection drops, to drive multiple agent runtimes (Claude, Codex,
Gemini, **Antigravity/AGY, Hermes, nano-claw/OpenClaw**) from the Mac via cmux,
and to keep personal data private even on a *shared* development VM.

`oci-remote-dev` already solves the hard substrate problems (drop-resilient
`agentctl` sessions, hard per-tenant memory isolation, MultiLLM gateway, cmux
local→remote workflow, autonomous agent jobs, PreToolUse guardrails, per-account
GitHub identity). **What is missing is PAI itself.** The VM runs generic
`claude`/`codex`/`gemini` CLIs, not Adrian's Algorithm + skills + MEMORY + Obi
identity. There is no mechanism to carry the *same* PAI knowledge across the
principal's own machines, no privacy model strong enough for PAI's personal
MEMORY (TELOS, health, finances) on shared infrastructure, and the additional
agent runtimes are not registered as governed backends.

## Vision

Obi is the same colleague everywhere. The principal opens a terminal on any of
their machines — a Mac at home, the Ubuntu box at the office, a borrowed Windows
laptop, or a cmux pane into the shared OCI VM — and Obi already knows the TELOS,
the projects, the conventions, and the in-flight work, because the knowledge
followed them. They kick off a task, the WireGuard tunnel drops, they close the
lid, and on reconnect the work is still running and Obi picks up mid-thought. On
the shared VM, a second developer in the same `developers` group cannot read a
single byte of the principal's personal MEMORY — not even the ciphertext is
useful to them. The euphoric surprise: *the infrastructure was already there;
PAI just needed a bridge, and now the life-OS is genuinely portable without ever
leaking the life.*

## Out of Scope

- Re-implementing durability, per-tenant isolation, the MultiLLM gateway, cmux,
  guardrails, or agent jobs — these exist and are reused, not rebuilt.
- Live deployment to the OCI VM — connection details arrive later; this phase
  ships design + toggle-gated scaffolding that a subsequent `deploy.sh` run
  materializes. Live-only ISCs are marked `[DEFERRED-VERIFY]`.
- A bespoke encryption protocol — uses `age` (audited, standard), not homegrown crypto.
- Changing PAI's own internal architecture (Algorithm, skill format). PAI is
  consumed as-is; only a thin deploy/sync/runtime-registration layer is added.
- Putting personal MEMORY into `/opt/shared-dev` or any cross-tenant surface — ever.

## Principles

- **Isolation by default, sharing by intent** (inherited from the fleet) — PAI's
  personal MEMORY is private by construction; only explicitly-shared context
  reaches the cross-tenant bus.
- **Source of truth is split by sensitivity** — code → normal GitHub repos;
  skills/Algorithm/hooks → the PAI repo; personal MEMORY + USER → a *separate
  private* repo, encrypted at rest. Each thing lives where its sensitivity dictates.
- **Encryption is offline-first and key-per-device** — `age` identities live on
  each device; the cloud (and the shared VM disk) only ever holds ciphertext.
- **Reproducible + idempotent + toggle-gated** — every new capability is an
  Ansible task behind an `.env.local` flag, matching the existing deploy model.
- **The VPN is the trust boundary** — nothing new is exposed publicly.
- **Match the surrounding code** — this repo is Python (stdlib + pytest), Ansible,
  and bash; the PAI bridge is written in those, not TypeScript.

## Constraints

- New code is Python 3 stdlib-only + pytest (repo convention; see `AGENTS.md`),
  Ansible tasks, and POSIX bash — no new runtime deps where avoidable.
- Personal MEMORY must be encrypted at rest on the shared VM and in any cloud
  remote; decryptable only inside the owning UNIX user's session.
- Must not weaken or bypass the existing PreToolUse guardrail, tenant
  enforcement, or per-account GitHub identity.
- `security_gate.py` must still pass — no OCIDs, IPs, namespaces, or secrets in
  the tree.
- Sync must work with NO cloud remote (local bare repo / offline) now, and extend
  to a GitHub private remote later by config only.
- gh CLI auth is currently invalid; the PR is delivered via SSH push + manual link.

## Goal

Add a **Phase 6 — PAI Integration** capability to `oci-remote-dev`, shipped as a
PR to `adibirzu/devvm`, that: (1) bootstraps PAI/Obi per-user on the fleet behind
an `install_pai` toggle; (2) syncs the principal's PAI knowledge across their own
devices via a private, `age`-encrypted git repo with GitHub as source of truth
and local-only operation today; (3) registers Antigravity/AGY, Hermes, and
nano-claw/OpenClaw as governed agent runtimes through a pluggable registry that
also routes via the MultiLLM gateway; and (4) keeps personal MEMORY private —
per-user home, encrypted at rest, never on a shared surface — all documented and
unit-tested to the repo's existing standards.

## Criteria

### Deliverable: PR to devvm
- [ ] ISC-1: A branch `feat/pai-integration-phase6` exists in the devvm clone.
- [ ] ISC-2: The branch is committed with a conventional-commit message.
- [ ] ISC-3: The branch is pushed to `origin` (SSH). `[DEFERRED-VERIFY: gh re-auth]` for the PR object itself.
- [ ] ISC-4: A copy-paste `gh pr create` command + compare URL is given to the principal.
- [ ] ISC-5: Anti: No real OCID, public IP, tenancy namespace, or API key is committed (`security_gate.py` clean).

### Deliverable: PAI deploy layer (per-user bootstrap)
- [ ] ISC-6: `ansible/pai_tasks.yml` exists and is gated by `install_pai`.
- [ ] ISC-7: The task is wired into `ansible/playbook.yml` behind the toggle.
- [ ] ISC-8: `scripts/pai_bootstrap.sh` exists, is executable, and clones/updates `~/.claude/PAI` per-user.
- [ ] ISC-9: Bootstrap runs as each developer (per-user), never system-wide, never into `/opt/shared-dev`.
- [ ] ISC-10: Bootstrap sets `~/.claude` perms to `0700` (stricter than the repo's `0750` homes) for PAI dirs.
- [ ] ISC-11: `.env.example` gains `INSTALL_PAI`, `PAI_REPO`, `PAI_MEMORY_REPO`, `PAI_SYNC_ENABLED`, `PAI_AGE_RECIPIENTS`, `PAI_RUNTIMES` with safe defaults and comments.

### Deliverable: multi-device encrypted sync
- [ ] ISC-12: `scripts/pai_sync.py` exists, stdlib-only, with `push`, `pull`, `status`, `init` subcommands.
- [ ] ISC-13: `pai_sync.py` encrypts MEMORY + USER with `age` before commit (recipients from config).
- [ ] ISC-14: `pai_sync.py` works with NO remote configured (local commit succeeds, status reports "no remote").
- [ ] ISC-15: `pai_sync.py` extends to a remote by a single config value (no code change).
- [ ] ISC-16: Anti: `pai_sync.py` never writes plaintext personal MEMORY outside the owning home dir.
- [ ] ISC-17: Anti: `pai_sync.py` refuses to run if `age` recipients are unset while encryption is required.
- [ ] ISC-18: `tests/test_pai_sync.py` covers the no-remote path, the missing-recipients refusal, and the sensitivity split, and passes.

### Deliverable: pluggable agent-runtime registry
- [ ] ISC-19: `agent-os/runtimes.json` exists, defining claude/codex/gemini + antigravity/hermes/nanoclaw entries.
- [ ] ISC-20: Each runtime entry declares: name, enabled, command template, gateway-routed flag, guardrail-scope.
- [ ] ISC-21: `scripts/pai_runtime_registry.py` loads/validates the registry and lists enabled runtimes.
- [ ] ISC-22: The registry resolves a runtime → an `agentctl`/`agent-job`-compatible launch command.
- [ ] ISC-23: Unknown/disabled runtimes are rejected with a clear error (not silently launched).
- [ ] ISC-24: Antigravity/AGY, Hermes, nano-claw entries route through the MultiLLM gateway where applicable.
- [ ] ISC-25: Anti: a runtime cannot bypass the PreToolUse guardrail (registry documents the inherited gate).
- [ ] ISC-26: `tests/test_pai_runtime_registry.py` covers load, validate, resolve, reject-unknown, and passes.

### Deliverable: privacy model on the shared VM
- [ ] ISC-27: `pai_bootstrap.sh` installs `age` (or documents the dependency) and creates a per-user age identity if absent.
- [ ] ISC-28: Personal MEMORY at rest on the VM is age-encrypted; decryption happens only in-session.
- [ ] ISC-29: A documented key path lets the age identity move to OCI Vault / a passphrase later (config only).
- [ ] ISC-30: Anti: nothing PAI-personal is symlinked into `~/shared-workspace` or `/opt/shared-dev`.

### Deliverable: documentation
- [ ] ISC-31: `docs/PAI-INTEGRATION.md` explains the architecture (deploy, runtimes, gateway, guardrails).
- [ ] ISC-32: `docs/MULTI-DEVICE-SYNC.md` explains the encrypted private-repo sync + per-OS client setup (Mac/Ubuntu/Windows).
- [ ] ISC-33: `docs/AGENT-RUNTIMES.md` documents the pluggable registry and how to add a runtime.
- [ ] ISC-34: `README.md` gains a Phase 6 row in the status table and a PAI section.
- [ ] ISC-35: `ROADMAP-v2.md` gains a Phase 6 — PAI Integration section.
- [ ] ISC-36: Windows client path is documented (WireGuard Windows + SSH/mosh/WSL or web IDE).
- [ ] ISC-37: cmux multi-backend usage with the new runtimes is documented.

### Cross-cutting
- [ ] ISC-38: All new Python passes `python3 -m py_compile`.
- [ ] ISC-39: New tests pass under `pytest` (the two new test files).
- [ ] ISC-40: `security_gate.py --mode full` passes on the working tree.
- [ ] ISC-41: Anti: no existing Phase 0–5 file is broken (existing tests still pass).
- [ ] ISC-42: Antecedent: the principal can read the PR diff and recognize it as a coherent, mergeable Phase 6.

## Test Strategy

| isc | type | check | threshold | tool |
|-----|------|-------|-----------|------|
| ISC-1..2 | git | branch + commit exist | present | Bash `git` |
| ISC-3 | git | push exit 0 | success | Bash `git push` |
| ISC-5,40 | security | gate scanner clean | exit 0 | Bash `security_gate.py` |
| ISC-6..11 | file | files exist + content greps | present | Read/Grep |
| ISC-12..18 | unit | sync subcommands + refusals | tests green | `pytest test_pai_sync.py` |
| ISC-19..26 | unit | registry load/resolve/reject | tests green | `pytest test_pai_runtime_registry.py` |
| ISC-27..30 | inspection+code | encryption + no-shared-symlink | present | Read/Grep |
| ISC-31..37 | file | docs + README/ROADMAP sections | present | Read/Grep |
| ISC-38..39 | build | compile + pytest | exit 0 | Bash |
| ISC-41 | regression | existing tests unaffected | green | `pytest tests/` |
| ISC-42 | judgment | coherent diff (proxy: advisor review) | pass | Inference advisor |

## Features

| name | description | satisfies | depends_on | parallelizable |
|------|-------------|-----------|------------|----------------|
| deploy-layer | Ansible `pai_tasks.yml` + `pai_bootstrap.sh` + playbook wiring | ISC-6..11,27..30 | — | yes |
| sync-layer | `pai_sync.py` + tests (age-encrypted private-repo sync) | ISC-12..18 | — | yes |
| runtime-registry | `runtimes.json` + `pai_runtime_registry.py` + tests | ISC-19..26 | — | yes |
| docs | PAI-INTEGRATION, MULTI-DEVICE-SYNC, AGENT-RUNTIMES, README, ROADMAP | ISC-31..37 | deploy/sync/registry | partly |
| env-surface | `.env.example` additions | ISC-11 | — | yes |
| pr-delivery | branch, commit, push, PR link | ISC-1..5 | all above | no |
| verify | compile, pytest, security gate, advisor, red-team | ISC-38..42 | all above | no |

## Decisions

- 2026-05-31 — **Run E3, not E4.** Classifier returned E3 (fail-safe). The work is
  E4-shaped (cross-cutting architecture) but the principal signalled strong
  preference for delivery over ceremony; E3's 8-section ISA + 4 thinking floor is
  honored, live deployment is deferred (VM details pending), so E3 is sufficient
  to ship a complete, verifiable PR. `effort_source: classifier`.
- 2026-05-31 — **Delegation show-your-math.** Scaffolding is written single-author:
  the repo conventions were fully read (Python stdlib + pytest, Ansible, bash),
  the artifacts are bounded and pattern-matched, and delegating to Forge/codex
  would add round-trip latency and drift risk in an unfamiliar repo. Forge/Cato
  are instead used in VERIFY for an adversarial review pass — meeting the spirit of
  the soft delegation floor (review delegation rather than authorship delegation).
- 2026-05-31 — **age, not git-crypt, for MEMORY encryption.** age gives
  recipient-based, key-per-device, offline encryption that maps cleanly to
  "local now, cloud later" and "secure even with multiple people on the VM"
  (ciphertext-only at rest). git-crypt couples to a single symmetric key in the repo.
- 2026-05-31 — **Sensitivity-split source of truth.** Code → normal repos;
  skills/Algorithm → PAI repo; personal MEMORY+USER → separate *private* encrypted
  repo. One repo for everything would either leak personal data or over-encrypt
  shareable code.
- 2026-05-31 — **PR via SSH push.** gh stored token is invalid; SSH remote works.
  Branch is pushed; the PR object is created by the principal after `gh auth login`
  (command provided). Avoids blocking the whole deliverable on an auth fix.

## Changelog

- conjectured: the request needed a from-scratch multi-device + durability build.
  refuted_by: reading `oci-remote-dev` — durability, isolation, cmux, gateway,
  agent jobs already shipped (Phases 0–5).
  learned: the request is a *bridge* (deploy PAI onto the existing substrate +
  sync across the principal's own devices), not a rebuild.
  criterion_now: ISCs target a PAI deploy/sync/runtime layer ON TOP of the fleet,
  with explicit Anti-criteria against rebuilding or leaking.

## Verification

- ISC-1..3: branch `feat/pai-integration-phase6` pushed to origin; 9 commits. (git log/push rc=0)
- ISC-5,40: `security_gate.py --mode full` → "passed! No violations" (run repeatedly).
- ISC-6..11,31..37: deploy/docs/.env/guide files present + committed.
- ISC-12..26: `test_pai_sync.py` + `test_pai_runtime_registry.py` green; registry validates 5 runtimes; agy/gemini aliases resolve to antigravity.
- ISC-19..26 (PAI enhancement): agentctl + agent-job resolve non-builtin runtimes via pai-runtimes; +test stubbing the resolver. 191 tests green total.
- AGY: binary `agy`, headless `agy -p {prompt}` (verified from antigravity.google docs); Ansible installs via download-then-run (no pipe-to-shell), gated by install_antigravity.
- **LIVE on VM (<VM_PUBLIC_IP>), via `ansible/deploy_pai.yml` (ok=6 changed=4 failed=0):**
  `pai-runtimes`, `pai-sync`, `pai-bootstrap` installed in /usr/local/bin; `/opt/agent-os/runtimes.json` deployed; registry validates; `resolve agy` and `resolve gemini` both → `agy -p` with gateway env. `agy` binary present on VM.
- VM deps (separate `install_deps.yml`, ok=7): age 1.1.1, pytest 7.4.4, mosh, rsync installed.
- ISC-4,42: PR pending — branch pushed; gh token invalid so PR object opened manually at github.com/adibirzu/devvm.
- Deferred (DEFERRED-VERIFY): ISC-27..30 per-user encryption-at-rest verified by code+docs; live per-user `pai-bootstrap` runs when a real `developers` list is passed (CLIs-only deploy used `developers=[]`). Follow-up: deploy with the fleet's actual developer list to exercise per-user bootstrap + age-identity creation.

### Phase 7 (secure agents + Hermes + devvm safety)
- Secure profiles `agent-os/policy.{cloud-vm,mac-coding,mac-home}.json` — VERIFIED loading+evaluating via `GUARDRAIL_POLICY=<abs> guardrail.py`: cross-tenant read→deny, exfil scp→ask, pipe-to-shell→deny (cloud-vm); homeassistant→deny air-gap (mac-coding); lock/garage `--yes`→deny T3, thermostat→ask T2, temp read→allow T1 (mac-home). Grounded in verified 2026 research (lethal trifecta, Antigravity exfil, MCP poisoning, GitGuardian).
- Hermes: runtimes.json → real `hermes -z "{prompt}"` + `OPENAI_BASE_URL`; `ansible/pai_tasks.yml` install_hermes task (per-user download-then-run `--skip-browser`, chmod600 ~/.hermes/{.env,SOUL.md,config.yaml}). Verified vs github.com/NousResearch/hermes-agent.
- devvm safety: setup-wizard writes `.env.local` (git-ignored); wizard prompts ADMIN_USERNAME + additional developers on first run; `.env.example` stays the only committed template.
- `docs/SECURITY-PROFILES.md`, `scripts/egress_allowlist.sh` added. 191 tests green, gate clean.
- **LIVE on VM (verified as each owning user — `0750` homes caused earlier false-negatives):** `agy 1.0.3` AND `hermes` installed for royce + adi via `ansible/deploy_pai.yml` (`install_antigravity=true install_hermes=true`, developers=[royce,adi]). Install-task bugs found+fixed during rollout: (a) Hermes setup wizard reads `/dev/tty` not stdin → hung Ansible forever → fixed with the installer's own `--skip-setup` flag; (b) agy/hermes install to `$HOME/.local/bin` → tasks corrected to per-user runs with `creates: ~/.local/bin/<bin>`. Per-user `hermes setup` (API keys) is the remaining by-hand step. **Final deploy (deploy3, ok=13 changed=1) verified live:** VM `runtimes.json` resolves `hermes -z hi`+`OPENAI_BASE_URL` and `agy -p hi`+`ANTIGRAVITY_BASE_URL` (matches git); hermes secrets chmod **600** on `.env`/`SOUL.md`/`config.yaml` for royce AND adi; hermes v0.15.1 runs headless.
- D2 bridge: `ssh-vm` inputAdapter added to LIVE `~/.claude/PAI/TOOLS/RemoteCodeInputRouter.ts` (backup `.pre-sshvm.bak`); base64-safe transport; 3 unit tests green incl. injection-proof. devvm-side: `scripts/register_vm_session.ts` + `docs/REMOTE-CONTROL-BRIDGE.md`.
- Federation: `docs/FEDERATION-ARCHITECTURE.md` — House/Office/Apartment/CloudVM, decisions locked (DevVM hub · hybrid nodes · pai-sync+notify-pull · hub-and-spoke). The `ssh-vm` adapter is the F0 home-node control primitive.
- Operational note: the VM exhibited intermittent SSH reachability (port-22 flapping) during rollout — memory was healthy (30Gi free), so network/OCI-side, not OOM. Re-verify in a stable window if installs appear incomplete.

### Pi coding agent (earendil-works) — integrated + LIVE
- Registered in `agent-os/runtimes.json` (`pi -p {prompt}`, OPENAI_BASE_URL, alias earendil-pi); `pai-runtimes resolve pi` verified on VM.
- Local PAI dev: `pi 0.78.0` installed (bun, Node 22.22) at `~/.bun/bin/pi`; runs, needs `/login` for provider auth (one-time, like hermes setup).
- DevVM: deploy `ok=10 changed=3 failed=0` — **Node upgraded 20→22.22.2** (principal-authorized; NodeSource), **pi 0.78.0** at `/usr/bin/pi`. Node upgrade verified SAFE: claude 2.1.158 + codex 0.135.0 intact, code-server active (bundles own node). `docs/PI-INTEGRATION.md`, `.env.example` INSTALL_PI, README updated. 191 tests green, gate clean.
- Architecture framing: Pi's `pi-ai` provider-normalization + JSONL session-tree are its strengths (worth adopting as a library layer above `pai-runtimes`); Pi's zero-sandbox is its weakness → it runs INSIDE the fleet guardrail/per-tenant sandbox.
- Pre-existing gap (NOT caused by this work): `multillm-gateway` is inactive on the VM — the gateway/agent-os layer was never deployed here (only PAI CLIs + deps + runtimes). Runtimes reference the gateway URL for routing but the service needs `install_multillm_gateway`/`install_agent_os` deploys. Surfaced for a follow-up deploy.
- Follow-ups (principal decisions): (1) evaluate `@earendil-works/pi-ai` as PAI's in-process provider layer; (2) adopt Pi supply-chain hardening for PAI TS; (3) add Pi to `~/.claude/PAI/ALGORITHM/capabilities.md` producer registry by hand.
