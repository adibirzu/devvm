# Installing the Agentic Dev OS

One entry point — [`install.sh`](../install.sh) — covers every way you might land
this workspace:

| You want to… | Command |
|---|---|
| Set up **this** Linux machine | `./install.sh` |
| Set up **another** machine you can SSH to | `./install.sh --mode remote --host <ip>` |
| **Provision a cloud VM** and set it up | `./install.sh --mode cloud` |
| Have a machine set itself up **automatically** | `./install.sh --unattended` (see [Unattended](#unattended-deployment)) |

It detects the distribution and package manager rather than assuming Ubuntu.
Supported out of the box:

* **Debian family** — Debian, Ubuntu, Linux Mint (`apt-get`, `ufw`, `sudo` group)
* **RedHat family** — Oracle Linux, RHEL, Rocky, AlmaLinux, CentOS, Fedora
  (`dnf`/`yum`, `firewalld`, `wheel` group, EPEL enabled automatically)

Anything else stops with a message naming what to install by hand; the
distro-specific names live in [`ansible/vars/<os_family>.yml`](../ansible/vars/)
if you want to add a family.

---

## 1. Direct install (interactive)

On the machine you want to turn into a workspace:

```bash
git clone <this repo> && cd oci-remote-dev
./install.sh
```

That is the whole thing. With no `.env` present every value falls back to a
sane default and the account running the script becomes the primary developer.
The script:

1. detects the distro and package manager;
2. installs what is missing — `python3`, `ansible`, `git`, `curl` (and the
   `community.general` / `ansible.posix` collections);
3. compiles the developer list and Ansible variables from `.env` plus your flags;
4. runs the playbook against `localhost` over a local connection;
5. prints where the dashboard and the verifier are.

Preview first, change nothing:

```bash
./install.sh --dry-run        # print the resolved plan and exit
./install.sh --print-config   # print the exact Ansible variables
./install.sh --check          # let Ansible report every change it would make
```

Useful shapes:

```bash
./install.sh --minimal                     # headless: no desktop, cloud CLIs, containers
./install.sh --no-desktop                  # keep everything but XFCE/XRDP
./install.sh --admin-user maria --ssh-key ~/.ssh/id_ed25519.pub
./install.sh --wireguard                   # also run a WireGuard server here
./install.sh --tags clauded                # re-apply one layer only
```

### What lands on the machine

Accounts (one isolated UNIX user per developer, in the `developers` group and
the distro's sudo group), the shared `/opt/shared-dev` workspace, Node.js + the
AI CLIs, `agentctl`/`palace`/`guardrail`/`mcp-registry`/`control-plane`, the
systemd units and timers, the landing dashboard, and — unless you pass
`--no-desktop` — an XFCE desktop over XRDP.

### Agent CLIs and local LLMs

Claude Code, Codex and Gemini CLI install by default. Additional agent CLIs
(OpenCode, pi, Grok, Cline, GitHub Copilot CLI, Cursor agent) and Ollama
local-LLM serving are opt-in — flip the matching `INSTALL_*` flag in `.env`
or answer the wizard prompts. Every tool has a verified arm64 (aarch64) Linux
path; x86_64-only components (Cursor IDE AppImage) are skipped on arm64 with
a note instead of failing. Per-tool install paths and architecture evidence:
[TOOLCHAIN.md](TOOLCHAIN.md).

With `INSTALL_OLLAMA=true` the machine runs an Ollama server (bound to the
WireGuard IP or loopback, like every other service) and each developer's shell
gets `OLLAMA_HOST` plus a `claude-local` alias pointed at it; `codex --oss`
reaches the same models out of the box.


### Networking: bind address and WireGuard

On a cloud VM, cloud-init brings up WireGuard first and every service binds the
tunnel IP (`10.200.200.1`). A direct install has no tunnel, so services bind
**`127.0.0.1`** instead — nothing is exposed to the network by accident.

Three ways to change that:

```bash
./install.sh --wireguard                   # run a WireGuard server here; services bind its IP
./install.sh --bind-address 10.0.0.5       # bind a real interface (LAN/VPN you already run)
WG_SERVER_IP=... ./install.sh --wireguard  # or set it in .env
```

With `--wireguard`, the playbook generates the server keypair, a keypair per
developer, `/etc/wireguard/wg0.conf`, and one importable client config each at
`/etc/wireguard/clients/<user>/client_<user>.conf`. Keys are generated once and
never rotated on re-run, so a config already imported on a laptop keeps working.
Set `WG_ENDPOINT_HOST` when the machine is reached at an address other than its
primary IP.

---

## 2. Remote install (an existing machine over SSH)

```bash
./install.sh --mode remote --host 10.0.0.5 --user ubuntu \
             --ssh-identity ~/.ssh/id_ed25519
```

Ansible runs from here against that host; the target needs SSH access and
`python3`. Everything else is identical to a direct install, including
`--check`, `--tags` and `--wireguard`.

---

## 3. Cloud provisioning (the OCI-optimized path)

Unchanged, and still the right choice when you want the VM created for you:

```bash
cp .env.example .env && ./scripts/setup-wizard.sh   # or edit .env by hand
./install.sh --mode cloud --dry-run --profile <OCI_PROFILE>
./install.sh --mode cloud --yes --profile <OCI_PROFILE>
```

`--mode cloud` hands over to [`scripts/deploy.sh`](../scripts/deploy.sh) →
`deploy_multicloud.py`, which compiles WireGuard keys, renders cloud-init,
launches the instance on OCI/AWS/GCP/Azure, waits for SSH, and runs the same
playbook over SSH. Calling `./scripts/deploy.sh` directly still works exactly as
before. See the [staging-deploy checklist](STAGING-DEPLOY.md) for the ordered
first-time run.

---

## Unattended deployment

`--unattended` (or `DEVVM_UNATTENDED=1`) makes the run fully non-interactive: no
confirmation, no prompts, and a hard error instead of a hang if something needed
is missing. It is designed for cloud-init user-data, CI, and image builds.

```bash
./install.sh --unattended --admin-user dev --ssh-key /tmp/dev.pub --minimal
```

Requirements in unattended mode:

* **root, without a password prompt** — run as root or with passwordless sudo.
  Otherwise the script exits immediately saying so, rather than blocking on a
  prompt no one will answer.
* **network access** to the distro repositories and to GitHub.
* For `--mode remote`, a `--host`; for `--mode cloud`, a `.env` naming the
  provider and its credentials. Both fail fast with the exact missing input.

### Every flag has an environment variable

Handy when you cannot pass arguments (user-data, a Dockerfile, a CI secret):

| Variable | Meaning |
|---|---|
| `DEVVM_MODE` | `local` (default), `remote`, `cloud` |
| `DEVVM_UNATTENDED` | `1` = never prompt |
| `DEVVM_YES` | `1` = assume yes, still allows prompts elsewhere |
| `DEVVM_ENV_FILE` | env file path (default `.env`, then `.env.local`) |
| `DEVVM_ADMIN_USER` | primary developer account |
| `DEVVM_SSH_KEY` | public key to authorize |
| `DEVVM_HOST` / `DEVVM_USER` / `DEVVM_SSH_IDENTITY` | remote target |
| `DEVVM_BIND_ADDRESS` | address services bind |
| `DEVVM_WIREGUARD` | `1` = set up a WireGuard server |
| `DEVVM_DESKTOP` | `0` = skip XFCE/XRDP |
| `DEVVM_MINIMAL` | `1` = headless, no cloud CLIs/containers |
| `DEVVM_TAGS` / `DEVVM_SKIP_TAGS` | limit the Ansible run |
| `DEVVM_CHECK` / `DEVVM_DRY_RUN` | check mode / plan only |
| `DEVVM_SKIP_BOOTSTRAP` | `1` = never touch the package manager |
| `DEVVM_PROFILE` | OCI CLI profile for cloud mode |

Anything in `.env` (`INSTALL_*`, `MULTILLM_*`, `DEV_<n>_*`, …) still applies —
see [`.env.example`](../.env.example).

### cloud-init user-data

```yaml
#cloud-config
package_update: true
packages: [git]
runcmd:
  - git clone https://github.com/<you>/<this-repo>.git /opt/devvm
  - |
    cd /opt/devvm/oci-remote-dev && \
    DEVVM_UNATTENDED=1 DEVVM_ADMIN_USER=dev DEVVM_MINIMAL=1 ./install.sh
```

### CI / image build

```bash
./install.sh --unattended --minimal --skip-bootstrap --check   # verify only
./install.sh --unattended --minimal                            # build the image
```

### Re-running is safe

Every step is idempotent: packages are `state: present`, accounts and keys are
converged not recreated, WireGuard keys are generated only when absent, and
config files are written only when their content differs.

A second run over a finished install reports **6 changed out of ~100 tasks**, and
all six are by design, not drift: `dev-dashboard` and `control-plane` are declared
`state: restarted` so a redeploy picks up new configuration, `mcp-registry apply`
reports itself applied every time, and git's multi-valued `safe.directory` entries
are re-asserted per developer. Nothing is reinstalled or recreated, which makes
`install.sh` safe to run from a configuration-management loop or on every boot.

---

## Verifying

```bash
verify-agent-os        # installed by the run; checks units, endpoints, guardrail
./install.sh --check   # what would change if you ran again
```

The landing dashboard is at `http://<bind address>` and the MultiLLM dashboard at
`http://<bind address>:8080/dashboard`.

## Troubleshooting

**"Unsupported OS family"** — the playbook only ships package maps for the Debian
and RedHat families. Add `ansible/vars/<family>.yml` modelled on the existing two.

**"No supported firewall found"** — informational. ufw/firewalld is not installed,
so no rules were applied; the ports the run wanted open are listed in the message.
Pass `CONFIGURE_FIREWALL=false` to silence it deliberately.

**Ansible module not found (`ufw`, `npm`, `firewalld`)** — the collections are
missing. `install.sh` installs them; to do it by hand:

```bash
ansible-galaxy collection install -r ansible/requirements.yml
```

**`sudo: a password is required` during per-developer tasks** — the run needs
passwordless root. Run as root, or grant passwordless sudo to the invoking user.

**The MultiLLM clone fails** — set `MULTILLM_GIT_URL` to a repository you can
reach, or turn the layer off with `INSTALL_MULTILLM_GATEWAY=false`.

**A run failed halfway** — fix the reported task and re-run. Nothing is redone,
so recovery is just another `./install.sh`.
