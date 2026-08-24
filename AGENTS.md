# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

The project lives entirely in `oci-remote-dev/`. Read `oci-remote-dev/AGENTS.md`
first — it makes `KB/` required reading before changing provisioning logic.

## Deployment shape

- `oci-remote-dev/install.sh` is the one entry point: `--mode local` (this
  machine, the default), `--mode remote` (SSH), `--mode cloud` (delegates to
  `scripts/deploy.sh` → `deploy_multicloud.py`). `docs/INSTALL.md` documents all
  of it, including the unattended/`DEVVM_*` contract.
- `scripts/deploy_config.py` is the single source of truth turning `.env` into the
  developer list and Ansible extra-vars. Add a knob there, not in the callers.
- Distro portability lives in `ansible/vars/<os_family>.yml` (Debian, RedHat):
  package names, service names, sudo group, firewall backend. Both files must
  define the same keys — `tests/test_portable_install.py` fences that, plus the
  rule that `apt`/`dnf` modules stay out of shared task files.
- `ansible-core` does not ship `ufw`, `npm`, `git_config`, `firewalld` or
  `authorized_key`; they come from `ansible/requirements.yml`. A new collection
  module must be declared there or installs break.

## Verifying changes

- `make check` = the full local CI (lint, shellcheck, security gate, ansible
  syntax, pytest). `make install-check` runs Ansible in check mode.
- Never provision against a dev machine. Test in a throwaway systemd container:
  `podman run --systemd=always -d -v <repo>:/src:ro oraclelinux:9 /sbin/init`,
  then run `install.sh --unattended --minimal` inside it twice (the second run
  proves idempotency). `sudo -u` fails under PAM in the OL container — set
  `ANSIBLE_BECOME_METHOD=su` there; it is a container artifact, not a bug.
  Ubuntu 24.04 variant: the base image has no `/sbin/init` and no zstd —
  `apt-get install systemd zstd python3-certifi`, commit, then run
  `/lib/systemd/systemd` as PID1. `INSTALL_*` flags reach deploy_config only
  through the `.env` file (or install.sh `--set`), not process env.
- `github.com/adibirzu/multillm` currently 404s. Set
  `INSTALL_MULTILLM_GATEWAY=false` (or point `MULTILLM_GIT_URL` elsewhere) when
  testing, or the run fails at the clone.

## Sharp edges

- `ansible.builtin.file` with `state: link` follows the link when applying
  `owner`/`group` unless you set `follow: false` — that once left the shared
  `/opt/shared-dev` owned by whichever developer the loop ended on.
- A direct install has no cloud-init and therefore no WireGuard: services bind
  `127.0.0.1` unless `--wireguard` or `--bind-address` says otherwise.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
