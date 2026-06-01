# Pi Coding Agent — Integration (local PAI dev + DevVM fleet)

[`earendil-works/pi`](https://github.com/earendil-works/pi) is a self-extensible
TypeScript coding agent (`read`/`write`/`edit`/`bash` tools, JSONL session tree,
multi-provider via `pi-ai`). This doc wires it into both the **local PAI dev
environment** and the **DevVM fleet** as a governed runtime.

> **Why Pi, and the one caveat.** Pi's `pi-ai` provider-normalization layer and its
> session-tree model are genuinely better than launching a bare CLI. Its one
> weakness — **zero built-in sandboxing** ("Pi packages run with full system
> access; extensions execute arbitrary code") — is *exactly* what this fleet
> provides. So Pi runs **inside** the devvm guardrail + per-tenant isolation; its
> missing safety is the sandbox the fleet supplies. Never run Pi on a sensitive
> host outside a sandbox.

## Facts (verified)

| | |
|---|---|
| npm package | `@earendil-works/pi-coding-agent` (v0.78.0) |
| binary | `pi` |
| Node | **≥ 22.19** (the base playbook installs Node 20 → the Pi task upgrades to 22) |
| headless | `pi -p "<prompt>"` (print mode; also reads piped stdin: `cat x | pi -p "summarize"`) |
| structured | `pi --mode json "<prompt>"` |
| provider/model | `--provider <name> --model <pattern>` (default provider is `google` — override it) |
| sessions | `--session-id`, `--fork`, `--no-session`, `--session-dir`, `-c/--continue` |
| auth | `/login` (OAuth: **Claude Pro/Max**, **Codex/ChatGPT Plus**) or `export ANTHROPIC_API_KEY=…` |
| gateway | `OPENAI_BASE_URL` (pi-ai honors OpenAI-compatible base URLs) |

> **Billing note:** Anthropic states third-party-harness (Pi) usage on a Claude
> Pro/Max account draws from *extra usage billed per token*, NOT against plan
> limits. Budget accordingly, or use an API key with a spend cap.

## Local PAI dev install (your Mac — Node 22.22 ✓)

```bash
bun install -g @earendil-works/pi-coding-agent   # or: npm i -g @earendil-works/pi-coding-agent
pi --version                                      # 0.78.0
pi   # then /login → choose Claude Pro/Max or set ANTHROPIC_API_KEY
```

Pi sessions live in `~/.pi/agent/sessions/` (per working dir). Pi is now usable
standalone; to drive it through PAI, see "PAI registration" below.

## DevVM fleet install (Ansible, gated)

Set in `.env.local` and deploy:

```bash
INSTALL_PI=true
```
```bash
ansible-playbook -i '<VM_IP>,' -u devuser --private-key ~/.ssh/<key> \
  -e "install_pai=true install_pi=true" -e '{"developers":[{"name":"royce"},{"name":"adi"}]}' \
  ansible/deploy_pai.yml
```

The `pai_tasks.yml` Pi tasks: (1) ensure **Node ≥22** (NodeSource `setup_22.x` — the
VM ships Node 20), then (2) `npm i -g @earendil-works/pi-coding-agent`. Each
developer runs `pi` → `/login` once for provider auth.

## Runtime registry

Pi is registered in `agent-os/runtimes.json` (`name: pi`, alias `earendil-pi`):

```
$ pai-runtimes resolve pi --prompt "review this file"
OPENAI_BASE_URL=http://10.200.200.1:8080 pi -p 'review this file'
```

So it launches as a durable, guardrail-gated session like any other runtime:

```bash
agentctl start pi -p myproject -d ~/myproject
agent-job add nightly --agent pi -p myproject -d ~/myproject --prompt "run tests, fix trivial fails" --every 1d
```

## PAI registration (local Algorithm)

Pi is a **fourth code-producer option** alongside Forge (GPT-5.4), Anvil (Kimi),
and Engineer (Claude). Its distinctive value for the Algorithm:

- **`pi-ai` cross-provider handoff** — switch model mid-session (thinking-blocks →
  `<thinking>` tags), which fits the Forge/Anvil/Cato cross-vendor pattern.
- **Session tree (branch/fork/clone)** — richer than a flat session, good for the
  federation's many concurrent sessions.

To register Pi as a named producer in the local Algorithm, add an entry to
`~/.claude/PAI/ALGORITHM/capabilities.md` mirroring the Forge/Anvil rows (a
`pi -p` invocation, picked when the self-extensible/session-tree model helps).
This is left to the principal to apply, since `capabilities.md` drives the Forge
auto-include binding and is intentionally hand-curated.

## What we deliberately did NOT copy from Pi

- **Pi's no-sandbox stance** — wrong for a multi-tenant VM + home-automation reach.
  The fleet's guardrail/isolation stays; Pi runs inside it.
- **Pi's "review package source by hand" trust model** — superseded by the central
  MCP registry + supply-chain hardening (see SECURITY-PROFILES.md).

## What we SHOULD adopt from Pi (follow-ups)

- **`pi-ai` as a library** — evaluate `@earendil-works/pi-ai` as PAI's in-process
  provider-abstraction layer (typed, normalized streaming + tool schemas), a layer
  above the process-level `pai-runtimes` registry.
- **Supply-chain hardening** — Pi's `.npmrc` (`save-exact=true`, `min-release-age=2`),
  shrinkwrap with a lifecycle-script allowlist, npm audit in CI. Apply to PAI's TS.
