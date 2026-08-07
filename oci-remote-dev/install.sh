#!/bin/bash
# install.sh — one entry point for the Agentic Dev OS.
# =============================================================================
# Three ways to land the same workspace:
#
#   ./install.sh                      # configure THIS Linux machine
#   ./install.sh --mode remote --host 10.0.0.5
#                                     # configure an existing host over SSH
#   ./install.sh --mode cloud         # provision a cloud VM, then configure it
#                                     # (the original OCI/AWS/GCP/Azure path)
#
# Everything is flag- or env-driven, so the same script runs unattended from
# cloud-init user-data, a CI job, or a config-management tool:
#
#   curl -fsSL <raw-url>/install.sh | DEVVM_UNATTENDED=1 bash     # see docs/INSTALL.md
#   ./install.sh --unattended --admin-user dev --ssh-key /tmp/id_ed25519.pub
#
# It detects the distribution and package manager instead of assuming Ubuntu:
# the Debian family (Debian, Ubuntu, Mint) and the RedHat family (Oracle Linux,
# RHEL, Rocky, AlmaLinux, CentOS, Fedora) are supported out of the box.
# Re-running is safe: every step is idempotent.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"

# shellcheck source=scripts/lib/distro.sh
. "$SCRIPT_DIR/scripts/lib/distro.sh"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'

log()  { echo -e "${GREEN}[install]${NC} $*"; }
info() { echo -e "${CYAN}[info]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*" >&2; }
die()  { echo -e "${RED}[error]${NC} $*" >&2; exit 1; }

# --- configuration (flag > DEVVM_* env > .env file > default) ----------------

MODE="${DEVVM_MODE:-local}"
ENV_FILE="${DEVVM_ENV_FILE:-.env}"
ADMIN_USER="${DEVVM_ADMIN_USER:-}"
SSH_PUBLIC_KEY="${DEVVM_SSH_KEY:-}"
SSH_IDENTITY="${DEVVM_SSH_IDENTITY:-}"
TARGET_HOST="${DEVVM_HOST:-}"
TARGET_USER="${DEVVM_USER:-}"
BIND_ADDRESS="${DEVVM_BIND_ADDRESS:-}"
OCI_PROFILE_ARG="${DEVVM_PROFILE:-}"
ANSIBLE_TAGS="${DEVVM_TAGS:-}"
ANSIBLE_SKIP_TAGS="${DEVVM_SKIP_TAGS:-}"

UNATTENDED="${DEVVM_UNATTENDED:-0}"
ASSUME_YES="${DEVVM_YES:-0}"
WITH_WIREGUARD="${DEVVM_WIREGUARD:-0}"
WITH_DESKTOP="${DEVVM_DESKTOP:-1}"
MINIMAL="${DEVVM_MINIMAL:-0}"
CHECK_MODE="${DEVVM_CHECK:-0}"
DRY_RUN="${DEVVM_DRY_RUN:-0}"
SKIP_BOOTSTRAP="${DEVVM_SKIP_BOOTSTRAP:-0}"
PRINT_CONFIG=0
ADMIN_USER_EXPLICIT=0

usage() {
    cat <<'EOF'
Usage: ./install.sh [options]

Modes
  --mode local            Configure this machine (default)
  --mode remote           Configure an existing host over SSH (needs --host)
  --mode cloud            Provision a cloud VM first, then configure it
                          (OCI / AWS / GCP / Azure — driven by .env)

Common options
  -y, --yes               Assume yes for confirmations
  -u, --unattended        Fully non-interactive; never prompt (implies --yes)
  -c, --config FILE       Env file to read (default: .env, then .env.local)
      --admin-user NAME   Primary developer account
                          (default: your account in local mode, else devuser)
      --ssh-key PATH      Public key to authorize for the developer accounts
      --wireguard         Also set up a WireGuard server on the target
      --bind-address IP   Address the gateway/dashboard bind to
                          (default: 127.0.0.1 locally, the WireGuard IP with --wireguard)
      --no-desktop        Skip the XFCE desktop and XRDP
      --minimal           Headless + no cloud CLIs, Cursor or containers
  -t, --tags TAGS         Only run these Ansible tags
      --skip-tags TAGS    Skip these Ansible tags
      --check             Ansible check mode: report changes, apply nothing
      --dry-run           Print the resolved plan and exit
      --skip-bootstrap    Do not install prerequisites with the package manager
      --print-config      Print the resolved Ansible variables and exit
  -h, --help              This help

Remote mode
      --host HOST         Target hostname or IP
      --user USER         SSH login user (default: the admin user)
      --ssh-identity PATH Private key for the SSH connection

Cloud mode
      --profile NAME      OCI CLI profile to deploy with

Every option has a DEVVM_* environment equivalent (DEVVM_MODE, DEVVM_UNATTENDED,
DEVVM_ADMIN_USER, DEVVM_SSH_KEY, DEVVM_HOST, DEVVM_WIREGUARD, ...), so the whole
run can be driven from cloud-init user-data or a CI job. See docs/INSTALL.md.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode)          MODE="${2:-}"; shift 2 ;;
        --mode=*)        MODE="${1#*=}"; shift ;;
        -c|--config)     ENV_FILE="${2:-}"; shift 2 ;;
        --config=*)      ENV_FILE="${1#*=}"; shift ;;
        --admin-user)    ADMIN_USER="${2:-}"; ADMIN_USER_EXPLICIT=1; shift 2 ;;
        --admin-user=*)  ADMIN_USER="${1#*=}"; ADMIN_USER_EXPLICIT=1; shift ;;
        --ssh-key)       SSH_PUBLIC_KEY="${2:-}"; shift 2 ;;
        --ssh-key=*)     SSH_PUBLIC_KEY="${1#*=}"; shift ;;
        --ssh-identity)  SSH_IDENTITY="${2:-}"; shift 2 ;;
        --ssh-identity=*) SSH_IDENTITY="${1#*=}"; shift ;;
        --host)          TARGET_HOST="${2:-}"; shift 2 ;;
        --host=*)        TARGET_HOST="${1#*=}"; shift ;;
        --user)          TARGET_USER="${2:-}"; shift 2 ;;
        --user=*)        TARGET_USER="${1#*=}"; shift ;;
        --bind-address)  BIND_ADDRESS="${2:-}"; shift 2 ;;
        --bind-address=*) BIND_ADDRESS="${1#*=}"; shift ;;
        --profile)       OCI_PROFILE_ARG="${2:-}"; shift 2 ;;
        --profile=*)     OCI_PROFILE_ARG="${1#*=}"; shift ;;
        -t|--tags)       ANSIBLE_TAGS="${2:-}"; shift 2 ;;
        --tags=*)        ANSIBLE_TAGS="${1#*=}"; shift ;;
        --skip-tags)     ANSIBLE_SKIP_TAGS="${2:-}"; shift 2 ;;
        --skip-tags=*)   ANSIBLE_SKIP_TAGS="${1#*=}"; shift ;;
        -y|--yes)        ASSUME_YES=1; shift ;;
        -u|--unattended) UNATTENDED=1; ASSUME_YES=1; shift ;;
        --wireguard)     WITH_WIREGUARD=1; shift ;;
        --no-desktop)    WITH_DESKTOP=0; shift ;;
        --minimal)       MINIMAL=1; WITH_DESKTOP=0; shift ;;
        --check)         CHECK_MODE=1; shift ;;
        --dry-run)       DRY_RUN=1; shift ;;
        --skip-bootstrap) SKIP_BOOTSTRAP=1; shift ;;
        --print-config)  PRINT_CONFIG=1; shift ;;
        -h|--help)       usage; exit 0 ;;
        *)               usage >&2; die "Unknown option: $1" ;;
    esac
done

[[ "$UNATTENDED" == "1" ]] && ASSUME_YES=1

case "$MODE" in
    local|remote|cloud) ;;
    *) die "Unknown --mode '$MODE'. Expected: local, remote or cloud." ;;
esac

# --- helpers -----------------------------------------------------------------

confirm() {
    local prompt="$1"
    [[ "$ASSUME_YES" == "1" ]] && return 0
    if [[ ! -t 0 ]]; then
        die "$prompt — no terminal to ask on. Re-run with --yes (or --unattended)."
    fi
    read -r -p "$prompt [y/N]: " reply
    [[ "$reply" =~ ^[Yy] ]]
}

# Read a key from the env file without sourcing it (never execute config).
env_file_value() {
    local key="$1" file="$2"
    [[ -f "$file" ]] || return 1
    local line
    line="$(grep -E "^[[:space:]]*${key}=" "$file" | tail -n1 || true)"
    [[ -n "$line" ]] || return 1
    line="${line#*=}"
    line="${line%\"}"; line="${line#\"}"
    line="${line%\'}"; line="${line#\'}"
    [[ -n "$line" ]] || return 1
    echo "$line"
}

resolved_env_file() {
    local candidate="$ENV_FILE"
    [[ "$candidate" != /* ]] && candidate="$PROJECT_DIR/$candidate"
    if [[ ! -f "$candidate" && "$(basename "$ENV_FILE")" == ".env" && -f "$PROJECT_DIR/.env.local" ]]; then
        candidate="$PROJECT_DIR/.env.local"
    fi
    echo "$candidate"
}

# The invoking human, even under sudo — a sane default admin on a direct install.
invoking_user() {
    if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
        echo "$SUDO_USER"
    elif [[ "$(id -un)" != "root" ]]; then
        id -un
    else
        echo ""
    fi
}

# getent is Linux-only; cloud mode can be driven from a Mac, so fall back to $HOME.
home_dir_of() {
    local user="$1" home_dir=""
    if command -v getent >/dev/null 2>&1; then
        home_dir="$(getent passwd "$user" 2>/dev/null | cut -d: -f6 || true)"
    fi
    if [[ -z "$home_dir" && "$user" == "$(id -un)" ]]; then
        home_dir="$HOME"
    fi
    echo "$home_dir"
}

first_public_key() {
    local home_dir="$1" candidate
    for candidate in "$home_dir"/.ssh/id_ed25519.pub "$home_dir"/.ssh/id_rsa.pub \
                     "$home_dir"/.ssh/id_ecdsa.pub; do
        [[ -f "$candidate" ]] && { echo "$candidate"; return 0; }
    done
    return 1
}

# --- preflight ---------------------------------------------------------------

distro_detect
log "Detected ${DISTRO_PRETTY} — family ${DISTRO_FAMILY}, package manager ${PKG_MANAGER:-none}"

if [[ "$MODE" == "local" && "$PKG_FAMILY_SUPPORTED" != "1" ]]; then
    warn "This distribution is not one we bootstrap automatically."
    warn "Supported: Debian/Ubuntu and Oracle Linux/RHEL/Rocky/AlmaLinux/Fedora."
    warn "Install python3, ansible-core and git yourself, then re-run with --skip-bootstrap."
    [[ "$SKIP_BOOTSTRAP" == "1" ]] || die "Unsupported distribution '$DISTRO_ID' and prerequisites not pre-installed."
fi

# Root is needed to change this machine (local mode) and to install prerequisites
# in any mode. Remote and cloud modes only drive another host, so a missing root
# here is not fatal for them — the bootstrap step reports it if it matters.
SUDO=""
if ! SUDO="$(distro_sudo_prefix)"; then
    SUDO=""
    if [[ "$MODE" == "local" ]]; then
        die "This install needs root. Run it as root, or install sudo first."
    fi
fi
if [[ "$MODE" == "local" && -n "$SUDO" && "$SUDO" != "sudo -n" && "$UNATTENDED" == "1" ]]; then
    die "Unattended mode needs root without a password prompt. Run as root, or grant passwordless sudo to $(id -un)."
fi

ENV_PATH="$(resolved_env_file)"
if [[ -f "$ENV_PATH" ]]; then
    info "Using configuration: $ENV_PATH"
else
    info "No env file at $ENV_PATH — using built-in defaults (every value has one)."
fi

# Cloud provisioning is the only mode that genuinely cannot proceed on defaults.
if [[ "$MODE" == "cloud" && ! -f "$ENV_PATH" ]]; then
    die "Cloud mode needs $ENV_PATH. Create it with:
  cp .env.example .env && \$EDITOR .env      # or: ./scripts/setup-wizard.sh
It must name the provider (CLOUD_PROVIDER) and its credentials/profile."
fi

# Admin account: an explicit flag wins, then the env file, then the human running
# this. Falling back to 'devuser' on someone's laptop would be a surprise.
if [[ "$ADMIN_USER_EXPLICIT" != "1" && -z "$ADMIN_USER" ]]; then
    if env_file_value ADMIN_USERNAME "$ENV_PATH" >/dev/null 2>&1; then
        ADMIN_USER=""   # let the env file speak
    elif [[ "$MODE" == "local" ]]; then
        ADMIN_USER="$(invoking_user)"
        [[ -n "$ADMIN_USER" ]] || die "Could not infer the admin account (running as root with no SUDO_USER). Pass --admin-user NAME."
        info "Admin account defaults to '$ADMIN_USER' (the account running this)."
    fi
fi

# SSH key: authorize one if we can find one, otherwise carry on — a local account
# that already exists does not need a key installed to be usable.
if [[ -z "$SSH_PUBLIC_KEY" ]] && ! env_file_value SSH_PUBLIC_KEY_PATH "$ENV_PATH" >/dev/null 2>&1; then
    admin_home="$(home_dir_of "${ADMIN_USER:-$(id -un)}")"
    if [[ -n "$admin_home" ]] && SSH_PUBLIC_KEY="$(first_public_key "$admin_home")"; then
        info "Authorizing SSH key: $SSH_PUBLIC_KEY"
    else
        SSH_PUBLIC_KEY=""
        [[ "$MODE" == "local" ]] && info "No SSH public key found — skipping key authorization."
    fi
fi

if [[ "$MODE" == "remote" ]]; then
    [[ -n "$TARGET_HOST" ]] || die "Remote mode needs a target: --host <hostname-or-ip> (or DEVVM_HOST)."
    [[ -n "$TARGET_USER" ]] || TARGET_USER="${ADMIN_USER:-$(id -un)}"
    if [[ -z "$SSH_IDENTITY" && -n "$SSH_PUBLIC_KEY" ]]; then
        SSH_IDENTITY="${SSH_PUBLIC_KEY%.pub}"
    fi
fi

# --- bootstrap prerequisites -------------------------------------------------

# deploy_config.py compiles the inventory and variables, so python3 is needed
# before anything else — including --print-config on a bare machine.
ensure_python() {
    command -v python3 >/dev/null 2>&1 && return 0
    [[ "$SKIP_BOOTSTRAP" == "1" ]] && die "python3 is required but missing, and --skip-bootstrap was given. Install python3 and re-run."
    log "Installing python3 with ${PKG_MANAGER:-the system package manager}"
    distro_pkg_refresh "$SUDO" || warn "Package index refresh failed — continuing with the cached index."
    distro_pkg_install "$SUDO" "$(distro_pkg_name python)" \
        || die "Could not install python3. Install it with your package manager, then re-run."
}

bootstrap_prerequisites() {
    ensure_python
    local wanted=()
    command -v python3 >/dev/null 2>&1 || wanted+=("$(distro_pkg_name python)")
    command -v git >/dev/null 2>&1 || wanted+=(git)
    command -v curl >/dev/null 2>&1 || wanted+=(curl)
    command -v ansible-playbook >/dev/null 2>&1 || wanted+=("$(distro_pkg_name ansible)")
    # Ansible's apt module needs python3-apt. It self-heals on a normal run but
    # not in check mode, and minimal images ship without it.
    if [[ "$DISTRO_FAMILY" == "debian" ]] && ! python3 -c "import apt" >/dev/null 2>&1; then
        wanted+=(python3-apt)
    fi
    if [[ "$MODE" == "cloud" ]]; then
        command -v wg >/dev/null 2>&1 || wanted+=(wireguard-tools)
    fi

    if [[ ${#wanted[@]} -eq 0 ]]; then
        info "Prerequisites already present."
        return 0
    fi

    if [[ "$SKIP_BOOTSTRAP" == "1" ]]; then
        die "Missing prerequisites (${wanted[*]}) and --skip-bootstrap was given. Install them, then re-run."
    fi

    log "Installing prerequisites with ${PKG_MANAGER}: ${wanted[*]}"
    distro_pkg_refresh "$SUDO" || warn "Package index refresh failed — continuing with the cached index."
    if ! distro_pkg_install "$SUDO" "${wanted[@]}"; then
        die "Could not install: ${wanted[*]}
Install them with your package manager, then re-run with --skip-bootstrap."
    fi

    command -v ansible-playbook >/dev/null 2>&1 || die "ansible-playbook is still missing after bootstrap. Install Ansible (>=2.14) and re-run with --skip-bootstrap."
}

# ansible-core ships without community.general / ansible.posix; the full `ansible`
# package bundles them. Install only what is missing, and never fail the run for
# it — the playbook says clearly which module it could not find.
bootstrap_collections() {
    command -v ansible-galaxy >/dev/null 2>&1 || return 0
    local missing=0 collection
    for collection in community.general ansible.posix; do
        ansible-galaxy collection list "$collection" 2>/dev/null | grep -q "$collection" || missing=1
    done
    [[ "$missing" == "1" ]] || return 0

    log "Installing required Ansible collections (community.general, ansible.posix)"
    ansible-galaxy collection install -r "$PROJECT_DIR/ansible/requirements.yml" >/dev/null \
        || warn "Could not install the Ansible collections. If the run fails on ufw/firewalld/npm, install them manually:
  ansible-galaxy collection install -r ansible/requirements.yml"
}

# --- plan --------------------------------------------------------------------

CONFIGS_DIR="$PROJECT_DIR/configs"
INVENTORY="$CONFIGS_DIR/hosts.ini"
EXTRA_VARS="$CONFIGS_DIR/ansible_vars.json"

# Without a WireGuard server there is no 10.200.200.1 to bind to, so services
# bind the loopback instead of failing to start.
resolve_bind_address() {
    if [[ -n "$BIND_ADDRESS" ]]; then
        echo "$BIND_ADDRESS"
    elif [[ "$WITH_WIREGUARD" == "1" ]]; then
        env_file_value WG_SERVER_IP "$ENV_PATH" 2>/dev/null || echo "10.200.200.1"
    else
        echo "127.0.0.1"
    fi
}

# Fills the global CONFIG_ARGS array (a nameref would need bash 4.3+, and cloud
# mode can be driven from a Mac's bash 3.2).
CONFIG_ARGS=()
build_config_args() {
    CONFIG_ARGS=(--env-file "$ENV_PATH")
    [[ -n "$ADMIN_USER" ]] && CONFIG_ARGS+=(--admin-user "$ADMIN_USER")
    [[ -n "$SSH_PUBLIC_KEY" ]] && CONFIG_ARGS+=(--admin-ssh-key "$SSH_PUBLIC_KEY")

    if [[ "$MODE" == "remote" ]]; then
        CONFIG_ARGS+=(--connection ssh --host "$TARGET_HOST" --user "$TARGET_USER")
        [[ -n "$SSH_IDENTITY" ]] && CONFIG_ARGS+=(--ssh-key "$SSH_IDENTITY")
    else
        CONFIG_ARGS+=(--connection local --host localhost)
    fi

    local wireguard="false" desktop="false"
    [[ "$WITH_WIREGUARD" == "1" ]] && wireguard="true"
    [[ "$WITH_DESKTOP" == "1" ]] && desktop="true"
    CONFIG_ARGS+=(--set "install_wireguard=$wireguard" --set "install_desktop=$desktop")

    local bind
    bind="$(resolve_bind_address)"
    CONFIG_ARGS+=(--set "wg_server_ip=$bind")
    if [[ "$WITH_WIREGUARD" != "1" ]]; then
        # Without a tunnel, scope the "VPN-only" firewall rules to the bind address.
        CONFIG_ARGS+=(--set "wg_network=${bind}/32")
    fi

    if [[ "$MINIMAL" == "1" ]]; then
        CONFIG_ARGS+=(--set install_csp_clis=false --set install_cursor=false --set install_podman=false)
    fi
}

render_config() {
    mkdir -p "$CONFIGS_DIR"
    build_config_args
    python3 "$PROJECT_DIR/scripts/deploy_config.py" "${CONFIG_ARGS[@]}" \
        --emit-vars "$EXTRA_VARS" --emit-inventory "$INVENTORY" \
        || die "Could not compile the deployment configuration from $ENV_PATH (see the error above)."
}

print_plan() {
    echo ""
    echo -e "${BLUE}================ install plan ================${NC}"
    printf "  %-22s %s\n" "mode" "$MODE"
    printf "  %-22s %s\n" "distribution" "$DISTRO_PRETTY ($DISTRO_FAMILY / ${PKG_MANAGER:-none})"
    printf "  %-22s %s\n" "config" "$ENV_PATH"
    printf "  %-22s %s\n" "target" "$([[ "$MODE" == "remote" ]] && echo "$TARGET_USER@$TARGET_HOST" || echo "this machine")"
    printf "  %-22s %s\n" "admin account" \
        "${ADMIN_USER:-$(env_file_value ADMIN_USERNAME "$ENV_PATH" 2>/dev/null || echo devuser)}"
    printf "  %-22s %s\n" "ssh key" "${SSH_PUBLIC_KEY:-none}"
    printf "  %-22s %s\n" "bind address" "$(resolve_bind_address)"
    printf "  %-22s %s\n" "wireguard server" "$([[ "$WITH_WIREGUARD" == "1" ]] && echo yes || echo "no (services bind loopback)")"
    printf "  %-22s %s\n" "desktop (XFCE/XRDP)" "$([[ "$WITH_DESKTOP" == "1" ]] && echo yes || echo no)"
    printf "  %-22s %s\n" "minimal" "$([[ "$MINIMAL" == "1" ]] && echo yes || echo no)"
    printf "  %-22s %s\n" "unattended" "$([[ "$UNATTENDED" == "1" ]] && echo yes || echo no)"
    [[ -n "$ANSIBLE_TAGS" ]] && printf "  %-22s %s\n" "tags" "$ANSIBLE_TAGS"
    [[ -n "$ANSIBLE_SKIP_TAGS" ]] && printf "  %-22s %s\n" "skip tags" "$ANSIBLE_SKIP_TAGS"
    [[ "$CHECK_MODE" == "1" ]] && printf "  %-22s %s\n" "ansible" "check mode (no changes)"
    echo -e "${BLUE}=============================================${NC}"
    echo ""
}

# --- run ---------------------------------------------------------------------

run_cloud_mode() {
    local deploy_args=()
    [[ "$ASSUME_YES" == "1" ]] && deploy_args+=(--yes)
    [[ "$DRY_RUN" == "1" ]] && deploy_args+=(--dry-run)
    [[ -n "$OCI_PROFILE_ARG" ]] && deploy_args+=(--profile "$OCI_PROFILE_ARG")
    [[ "$ENV_FILE" != ".env" ]] && deploy_args+=(--env-file "$ENV_FILE")

    log "Handing over to the cloud deployer (scripts/deploy.sh ${deploy_args[*]:-})"
    exec "$PROJECT_DIR/scripts/deploy.sh" ${deploy_args[@]+"${deploy_args[@]}"}
}

run_ansible() {
    local cmd=(ansible-playbook -i "$INVENTORY" --extra-vars "@$EXTRA_VARS")
    [[ -n "$ANSIBLE_TAGS" ]] && cmd+=(--tags "$ANSIBLE_TAGS")
    [[ -n "$ANSIBLE_SKIP_TAGS" ]] && cmd+=(--skip-tags "$ANSIBLE_SKIP_TAGS")
    [[ "$CHECK_MODE" == "1" ]] && cmd+=(--check --diff)

    # become is declared in the play; only the password handling differs by mode.
    if [[ "$MODE" == "local" && "$SUDO" == "sudo" ]]; then
        cmd+=(--ask-become-pass)
    fi
    cmd+=("$PROJECT_DIR/ansible/playbook.yml")

    log "Running: ${cmd[*]}"
    if ! "${cmd[@]}"; then
        die "Ansible run failed. Fix the reported task and re-run this script — every step is idempotent, so completed work is not redone.
Re-run just the failing area with --tags, or preview with --check."
    fi
}

print_next_steps() {
    local bind
    bind="$(resolve_bind_address)"
    echo ""
    echo -e "${GREEN}Done.${NC} The workspace is configured on $([[ "$MODE" == "remote" ]] && echo "$TARGET_HOST" || echo "this machine")."
    echo ""
    echo "  Landing dashboard   http://${bind}"
    echo "  MultiLLM dashboard  http://${bind}:$(env_file_value MULTILLM_GATEWAY_PORT "$ENV_PATH" 2>/dev/null || echo 8080)/dashboard"
    echo "  Verify anytime      verify-agent-os"
    if [[ "$WITH_WIREGUARD" == "1" ]]; then
        echo "  WireGuard clients   /etc/wireguard/clients/<user>/client_<user>.conf"
    else
        echo ""
        echo "  Services bind ${bind}. Re-run with --wireguard to expose them over a VPN instead."
    fi
    echo ""
}

main() {
    if [[ "$MODE" == "cloud" ]]; then
        # The cloud path owns its own prerequisites, dry-run and confirmation.
        [[ "$SKIP_BOOTSTRAP" == "1" ]] || bootstrap_prerequisites
        run_cloud_mode
        return
    fi

    print_plan

    if [[ "$PRINT_CONFIG" == "1" ]]; then
        ensure_python
        build_config_args
        python3 "$PROJECT_DIR/scripts/deploy_config.py" "${CONFIG_ARGS[@]}" --print
        return
    fi

    if [[ "$DRY_RUN" == "1" ]]; then
        info "Dry run — nothing was changed. Drop --dry-run to apply, or use --check to have Ansible report what would change."
        return
    fi

    confirm "Configure $([[ "$MODE" == "remote" ]] && echo "$TARGET_HOST" || echo "this machine") as an agentic dev workspace?" \
        || die "Cancelled."

    [[ "$SKIP_BOOTSTRAP" == "1" ]] || bootstrap_prerequisites
    bootstrap_collections
    render_config
    run_ansible
    print_next_steps
}

main
