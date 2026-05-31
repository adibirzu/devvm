# Pluggable Agent-Runtime Registry

One data source describes every governed coding-agent backend the fleet can run.
Adding a runtime is a **JSON edit, not a code change**.

- Source of truth: `agent-os/runtimes.json` → deployed to `/opt/agent-os/runtimes.json`
- CLI: `pai-runtimes` (`scripts/pai_runtime_registry.py`)
- Shipped runtimes: `claude`, `codex`, **`antigravity` (aliases `agy`, `gemini`)**,
  **`hermes`**, **`nanoclaw` (aliases `nano-claw`, `openclaw`)**
- **Antigravity (AGY) replaces the Google Gemini CLI** as the Google-family runtime.
  The `gemini` alias routes to `antigravity`, so existing `gemini` call sites keep working.

## Two guarantees every runtime inherits (and cannot opt out of)

1. **Per-UNIX-user sandbox** — it runs as the calling developer; no cross-tenant access.
2. **PreToolUse guardrail** — `guardrail-hook` denies/asks on destructive tool calls.
   The registry only resolves *which command to launch*; it never weakens the gate.

`gateway_routed` runtimes additionally get their base-URL env var pointed at the
shared MultiLLM gateway, so token usage is attributed per tenant on `/team`.

## CLI

```bash
pai-runtimes list                       # enabled runtimes
pai-runtimes list --all                 # include disabled
pai-runtimes validate                   # schema check (used by Ansible on deploy)
pai-runtimes resolve agy --prompt "fix the bug"        # non-interactive (agent-job)
pai-runtimes resolve claude --interactive              # interactive (cmux / agentctl attach)
```

`resolve` prints the exact launch command. For gateway-routed runtimes it prefixes
the base-URL env var:

```
$ pai-runtimes resolve agy --prompt "build the thing"
ANTIGRAVITY_BASE_URL=http://10.200.200.1:8080 antigravity run --prompt 'build the thing'
```

## Registry schema

```json
{
  "version": 1,
  "runtimes": [
    {
      "name": "hermes",                       // required, unique
      "aliases": ["herm"],                     // optional alternate names
      "enabled": true,                          // required
      "description": "Local Hermes agent.",
      "kind": "cli",
      "interactive_template": ["hermes"],       // for cmux / attach
      "exec_template": ["hermes", "--task", "{prompt}"],  // required; {prompt} substituted
      "gateway_routed": true,                   // point base-URL env at the gateway
      "gateway_env": "HERMES_BASE_URL",         // which env var to set
      "guardrail_scope": "default",             // inherits the PreToolUse policy
      "agentctl_compatible": true
    }
  ]
}
```

## Adding a runtime

1. Add an entry to `agent-os/runtimes.json`.
2. `pai-runtimes validate` (also runs on deploy).
3. Re-run `deploy.sh` (or `scp` the file) to push it to `/opt/agent-os/runtimes.json`.

Unknown or disabled runtimes are **rejected with a clear error** — they are never
silently launched.

## Wiring into agentctl / agent-job (follow-up)

`resolve` produces an `agentctl`/`agent-job`-compatible argv. The intended call site
is `agentctl start <runtime> …` / an `agent-job` definition selecting a runtime by
name. That wiring is a small follow-up on top of this registry; the registry is the
contract it builds on.

> The command templates for Hermes and nano-claw are **placeholders** until the
> exact binaries/flags are supplied — update the JSON, no code change.

### Antigravity (AGY) — install + headless

Antigravity is Google's agentic IDE; it ships a **terminal coding agent** (`agy`)
with a headless command mode, which makes it usable as a fleet runtime.

- **Install (macOS/Linux):** download the installer, review it, then run it
  (avoid blind pipe-to-shell):
  `curl -fsSL https://antigravity.google/cli/install.sh -o agy-install.sh`
  then `bash agy-install.sh`. (Windows: download `install.ps1`, then run it.)
  The Ansible PAI task does exactly this — `get_url` to `/opt/agent-os/agy-install.sh`
  (optionally checksum-pinned via `agy_installer_sha256`), then executes the file.
- **Binary:** `agy`.
- **Headless:** `agy -p "<prompt>"` — command mode, non-interactive (add
  `--output-format json` to pipe structured output). The registry's `exec_template`
  is `agy -p "{prompt}"`.
- **Headless auth:** over SSH, `agy` prints an authorization URL + one-time code;
  open it on your laptop, paste the code, and the CLI completes the handshake —
  works on the VM behind the VPN.
- The `gemini` alias resolves here, so existing `gemini` call sites keep working.

Docs: https://antigravity.google/docs/cli-using ·
https://antigravity.google/docs/cli-getting-started
