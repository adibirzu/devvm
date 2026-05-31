# Secure Agent Profiles — Cloud VM & Personal Mac

Two hardened operating profiles for AI coding agents, grounded in 2026 threat
research (ClawHavoc 824+ malicious skills, MCP tool-poisoning as the #1 new attack
surface, Simon Willison's **lethal trifecta**, Antigravity workspace-escape findings).

> **The one rule that governs everything:** break at least one leg of the **lethal
> trifecta** — an agent must never simultaneously have (1) access to private data,
> (2) exposure to untrusted content, and (3) the ability to communicate externally.

## Profile A — Cloud VM (coding only)

The shared OCI VM. Multi-tenant; the threat model is cross-tenant leakage + a
compromised/poisoned agent exfiltrating or destroying.

| Control | Setting | Already in devvm? |
|---|---|---|
| Per-user isolation | 0700 homes, per-UNIX-user accounts | ✅ |
| Tool enforcement | PreToolUse guardrail, deny-by-default for destructive ops | ✅ (agent-os) |
| Network | WireGuard-only; services bound to `10.200.200.0/24` | ✅ |
| Tenant memory | `X-MultiLLM-Tenant` enforced | ✅ |
| MCP trust | central registry, pinned/approved servers only | ✅ |
| Autonomy | agent-job on branches, time-boxed, logged, notif ring | ✅ |
| **Secrets** | **per-user; move off plaintext dotfiles → OCI Vault / pass** | 🔭 hardening |
| **Egress** | **allowlist outbound to LLM APIs + package registries only** | 🔭 new (this doc) |
| **PAI personal MEMORY** | **age-encrypted at rest, never in /opt/shared-dev** | ✅ (Phase 6) |

**Guardrail profile:** `policy.cloud-vm.json` — denies catastrophic shell, force-push
to protected branches, secret-file reads outside `~`, writes outside home/shared/tmp;
asks for cloud/cluster mutations, `terraform destroy`, destructive SQL, system installs.

## Profile B — Personal Mac (coding **and** home management)

The danger here is **crossing the streams**: a coding agent compromised via a
malicious package/MCP must NEVER be able to reach HomeKit/HomeAssistant + smart locks.

**Hard rule: two separate agent contexts, different credentials, no shared access.**

### B1 — Coding context (Mac)
- Workspace-scoped filesystem access (the project dir), **not** full `$HOME`.
- Secrets in **macOS Keychain**, never plaintext dotfiles the agent can read.
- Same deny-by-default PreToolUse guardrail as the cloud VM.
- MCP servers audited + pinned.

### B2 — Home-management context (Mac) — physical world
- **Air-gapped from coding credentials.** Separate agent identity; cannot read the
  coding context's keys, and the coding agent cannot call HomeAssistant/HomeKit tools.
- **Tiered physical-action policy:**
  | Action class | Tier | Behaviour |
  |---|---|---|
  | Read sensors (temp, occupancy, lock state) | T1 | auto |
  | Lights, scenes, climate **set** | T2 | one-tap confirm |
  | **Locks, garage, alarm, cameras** | **T3** | **never autonomous — typed-yes per action** |
- This maps to the existing PAI tiering (AppleHome/HomeAssistant skills: reads auto,
  writes one-tap, locks/alarms typed-yes-60s).

**Guardrail profile:** `policy.mac-coding.json` (workspace-scoped) +
`policy.mac-home.json` (physical-action tiers; locks/alarm = deny-without-explicit-confirm).

## What this repo ships

- `agent-os/policy.cloud-vm.json` — Profile A guardrail policy.
- `agent-os/policy.mac-coding.json` — Profile B1 guardrail policy.
- `agent-os/policy.mac-home.json` — Profile B2 physical-action tiers.
- `scripts/egress_allowlist.sh` — Profile A outbound egress allowlist (ufw, opt-in).

Apply a profile on the VM with: `guardrail --load /opt/agent-os/policy.cloud-vm.json`
(or copy to `/etc/agent-os/policy.json`). On the Mac, point the PAI PreToolUse hook at
the matching policy per context.

## Sources (2026 research)

- Lethal trifecta — https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/
- Claude Code permissions/hooks — https://docs.anthropic.com/en/docs/claude-code/iam ,
  https://docs.anthropic.com/en/docs/claude-code/hooks
- ClawHavoc / AMOS / Vidar agent-identity theft — see `openclaw-security-monitor`,
  `hermes-security-monitor` (local repos).
- Reddit consensus (r/netsec, r/ClaudeAI, r/selfhosted, r/homeassistant): default-deny,
  sandbox autonomous runs, air-gap home automation, secrets in keychain/vault.
