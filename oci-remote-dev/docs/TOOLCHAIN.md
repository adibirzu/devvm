# Developer toolchain: install paths and architecture support

Every agent CLI and local-LLM component this project can provision, with the
install surface used and where its arm64 (aarch64) support was actually
verified. GB10-class hosts (Ubuntu 24.04 aarch64) are a primary target, so
**a tool is only added when a working linux/arm64 path exists**; anything
x86_64-only is skipped with an explicit note instead of failing at provision
time.

All new toggles default to OFF (`.env.example`, playbook vars,
`deploy_config.py`): an existing deployment never silently grows new global
installs on its next run. Opt in per tool with the matching `INSTALL_*` flag.

## Global npm CLIs (`ansible/playbook.yml`)

| Tool | npm package | linux/amd64 | linux/arm64 | Verification |
| --- | --- | --- | --- | --- |
| OpenCode | `opencode-ai` | yes | yes | Registry metadata shows `opencode-linux-arm64` (+musl) optional deps (checked 2026-08-24, v1.18.21). |
| Cline | `cline` | yes | yes | Registry metadata shows `@cline/cli-linux-arm64` optional dep (checked 2026-08-24, v3.0.57). |
| pi coding agent | `@earendil-works/pi-coding-agent` | yes | yes | Pure-JS bundle (single `dist/bundle/cli.js`); declares `engines.node >= 22.19`. Requires Node 22 at **runtime**, so the gate checks the actually-installed Node major rather than the declared `NODE_VERSION` (a re-run does not upgrade an existing Node install) (checked 2026-08-24, v0.84.3). |
| GitHub Copilot CLI | `@github/copilot` | yes | yes | Registry metadata shows `@github/copilot-linux-arm64` + `linuxmusl-arm64` optional deps; upstream documents Node 22+, gated the same way as pi (checked 2026-08-24, v1.0.80). |

The original trio (`@anthropic-ai/claude-code`, `@openai/codex`,
`@google/gemini-cli`) ships platform binaries or pure JS with first-class
arm64 support upstream.

## Per-developer vendor installers (`ansible/user_tasks.yml`)

These run per developer account because they install into `$HOME`; each
installer detects OS and architecture itself. The installer scripts were
fetched and inspected for arch handling on 2026-08-24.

| Tool | Installer | linux/amd64 | linux/arm64 | Notes |
| --- | --- | --- | --- | --- |
| Antigravity CLI (`agy`) | `https://antigravity.google/cli/install.sh` | x86_64 branch present | aarch64 branch present | Fixes the old gap where `install_antigravity` installed the skills pack for a harness whose CLI was never provisioned. The skills-pack harness list now also gates on the installed binary, so a failed download can never advertise antigravity. |
| Cursor agent CLI | `https://cursor.com/install` → `~/.local/bin/cursor-agent` | x86_64 branch present | aarch64 branch present | Terminal agent only. The **Cursor IDE AppImage is x86_64-only upstream** and is skipped on arm64 with a debug note (`install_cursor`). |
| Grok CLI | `https://x.ai/cli/install.sh` → `~/.grok/bin/grok` | x86_64 branch present | aarch64 branch present | Installs into the user home. |

## Local LLM serving (`ansible/ollama_tasks.yml`, opt-in `INSTALL_OLLAMA=true`)

- **Ollama**: official `https://ollama.com/install.sh`, which is arch-aware
  (x86_64 and aarch64 are both first-class upstream — it is the standard path
  on GB10 / Jetson aarch64 hosts) and registers the `ollama` systemd service.
- Binding follows the project-wide rule: `WG_SERVER_IP` when a tunnel exists,
  loopback otherwise; override with `OLLAMA_BIND_ADDRESS`. Port via
  `OLLAMA_PORT` (default 11434), opened in the firewall only when
  `INSTALL_OLLAMA=true`.
- Models: set `OLLAMA_MODELS="qwen3-coder,gpt-oss:20b"` to pull after install;
  when empty, the installer pulls `OLLAMA_DEFAULT_MODEL`, which also drives the
  per-user `claude-local` alias.
- Client wiring (per-developer `.bashrc` block): `OLLAMA_HOST` export plus
  `claude-local` alias (Ollama serves the Anthropic Messages API natively);
  Codex works via `codex --oss`; Gemini CLI accepts an OpenAI-compatible
  provider at `http://<bind>:<port>/v1`.

## Deliberately not provisioned

| Tool | Reason |
| --- | --- |
| Kimi CLI, Muse | Not part of the reference machine's toolset at the time of writing. |
| axi/fleet helpers (`gh-axi`, `tasks-axi`, `quota-axi`, `lavish-axi`, `no-mistakes`, `treehouse`, `herdr`, `chrome-devtools-axi`) | Private supervisor tooling of the operator's harness, not distributable from this public repository. |
| Cursor IDE on arm64 | Upstream publishes no aarch64 AppImage; the task notes it rather than installing a wrong-arch binary. |

## Supply-chain notes

- **Helm (Debian/arm64 apt path)**: the former mirror domain `baltocdn.com` was
  decommissioned by Balto in Sept 2025 and its expired domain was later
  re-registered by a third party that may serve malicious content
  ([helm.sh security notice, 2026-05-29](https://helm.sh/blog/security-notice-baltocdn/)).
  The playbook installs Helm from the current community mirror
  (`packages.buildkite.com/helm-linux/helm-debian`, which serves `binary-arm64`)
  and pins the signing-key fingerprint to the value published at
  helm.sh/docs/intro/install, aborting the run on any mismatch. Legacy keyring +
  sources files from the old domain are removed first. See also
  `KB/oci-provisioning/ISSUE-CATALOG.md` entry 13.

## Assertion status

Verified directly (2026-08-24): every row above cites registry metadata or a
fetched installer script checked for `x86_64`/`aarch64` handling. What is
asserted by analogy rather than tested here: runtime behaviour of each vendor
installer on a live Ubuntu 24.04 arm64 host (they were read, not executed),
and the RedHat-family equivalents of the same URLs (the installers are
distro-agnostic shell scripts; Ansible tasks use no distro-specific modules
for these tools).
