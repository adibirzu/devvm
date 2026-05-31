#!/bin/bash
# pai-bootstrap — install / update PAI (Obi) for the CURRENT UNIX user.
#
# Runs per-user (never as root, never into /opt/shared-dev). Clones or updates the
# PAI repo into ~/.claude/PAI (shareable: skills, Algorithm, hooks), ensures an age
# identity exists for personal-MEMORY encryption, and pulls the private encrypted
# MEMORY repo if configured. Personal data is decrypted only inside this user's
# session and the PAI tree is locked to 0700.
#
# Toggled by install_pai in .env; invoked by ansible/pai_tasks.yml as each developer.
#
# Env (all optional, with safe defaults):
#   PAI_REPO            git URL for the shareable PAI repo (skills/Algorithm/hooks)
#   PAI_MEMORY_REPO     git URL for the PRIVATE encrypted MEMORY repo (optional)
#   PAI_AGE_RECIPIENTS  age public keys (age1...) for MEMORY encryption
#   PAI_SYNC_ENABLED    "true" to pull the encrypted MEMORY repo on bootstrap
set -euo pipefail

CLAUDE_DIR="${HOME}/.claude"
PAI_DIR="${CLAUDE_DIR}/PAI"
PAI_REPO="${PAI_REPO:-}"
PAI_MEMORY_REPO="${PAI_MEMORY_REPO:-}"
PAI_SYNC_ENABLED="${PAI_SYNC_ENABLED:-false}"
AGE_IDENTITY="${PAI_AGE_IDENTITY:-${HOME}/.config/pai/age.key}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { echo "[pai-bootstrap:$(whoami)] $*"; }

if [ "$(id -u)" -eq 0 ]; then
  echo "refusing to run as root — pai-bootstrap is per-user." >&2
  exit 1
fi

# 1. Shareable PAI tree (skills, Algorithm, hooks) — clone or update.
mkdir -p "${CLAUDE_DIR}"
chmod 0700 "${CLAUDE_DIR}"
if [ -n "${PAI_REPO}" ]; then
  if [ -d "${PAI_DIR}/.git" ]; then
    log "updating PAI in ${PAI_DIR}"
    git -C "${PAI_DIR}" pull --ff-only -q || log "warning: PAI pull failed (continuing)"
  else
    log "cloning PAI from ${PAI_REPO}"
    git clone -q "${PAI_REPO}" "${PAI_DIR}"
  fi
else
  log "PAI_REPO unset — leaving ${PAI_DIR} as-is (set PAI_REPO to clone)."
  mkdir -p "${PAI_DIR}"
fi
chmod -R 0700 "${PAI_DIR}" 2>/dev/null || true

# 2. Per-user age identity for personal-MEMORY encryption (created if absent).
if command -v age-keygen >/dev/null 2>&1; then
  if [ ! -f "${AGE_IDENTITY}" ]; then
    mkdir -p "$(dirname "${AGE_IDENTITY}")"
    chmod 0700 "$(dirname "${AGE_IDENTITY}")"
    log "generating age identity at ${AGE_IDENTITY}"
    age-keygen -o "${AGE_IDENTITY}" 2>/dev/null
    chmod 0600 "${AGE_IDENTITY}"
    log "PUBLIC KEY (add to PAI_AGE_RECIPIENTS on your other devices):"
    age-keygen -y "${AGE_IDENTITY}" 2>/dev/null | sed 's/^/    /'
  fi
else
  log "warning: 'age' not installed — personal MEMORY cannot be encrypted yet."
  log "         install it (apt-get install age) before running pai-sync."
fi

# 3. Private encrypted MEMORY repo — pull + decrypt if sync is enabled.
if [ "${PAI_SYNC_ENABLED}" = "true" ] && [ -n "${PAI_MEMORY_REPO}" ]; then
  MEM_DIR="${HOME}/.pai-memory"
  if [ -d "${MEM_DIR}/.git" ]; then
    git -C "${MEM_DIR}" pull --ff-only -q || log "warning: MEMORY pull failed"
  else
    log "cloning private MEMORY repo"
    git clone -q "${PAI_MEMORY_REPO}" "${MEM_DIR}" || log "warning: MEMORY clone failed"
  fi
  chmod -R 0700 "${MEM_DIR}" 2>/dev/null || true
  if [ -f "${SCRIPT_DIR}/pai_sync.py" ] && [ -f "${AGE_IDENTITY}" ]; then
    log "decrypting MEMORY into ${PAI_DIR}"
    PAI_DIR="${PAI_DIR}" PAI_MEMORY_REPO_DIR="${MEM_DIR}" PAI_AGE_IDENTITY="${AGE_IDENTITY}" \
      python3 "${SCRIPT_DIR}/pai_sync.py" pull || log "warning: pai-sync pull failed"
  fi
else
  log "PAI_SYNC_ENABLED!=true or PAI_MEMORY_REPO unset — skipping MEMORY pull."
fi

# 4. Guarantee nothing PAI-personal is reachable from the shared workspace.
if [ -L "${HOME}/shared-workspace" ]; then
  if find -L "${HOME}/shared-workspace" -maxdepth 2 -name "MEMORY" -path "*PAI*" 2>/dev/null | grep -q .; then
    echo "FATAL: PAI MEMORY is reachable from shared-workspace — aborting." >&2
    exit 2
  fi
fi

log "done. PAI at ${PAI_DIR} (0700, per-user)."
