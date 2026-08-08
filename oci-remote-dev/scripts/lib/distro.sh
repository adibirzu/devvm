#!/bin/bash
# distro.sh — Linux distribution + package-manager detection helpers.
# ==================================================================
# Sourced by install.sh (and usable standalone: `bash scripts/lib/distro.sh`
# prints what it detected). Nothing here is OCI- or cloud-specific: the goal is
# that the same bootstrap works on Ubuntu/Debian, Oracle Linux/RHEL/Rocky/Alma/
# Fedora/CentOS, and degrades with a clear message anywhere else.
#
# After `distro_detect` the following are exported:
#   DISTRO_ID          e.g. ubuntu, debian, ol, rhel, rocky, almalinux, fedora
#   DISTRO_LIKE        ID_LIKE from /etc/os-release (may be empty)
#   DISTRO_VERSION_ID  e.g. 24.04, 9.4
#   DISTRO_MAJOR       major version only, e.g. 24, 9
#   DISTRO_FAMILY      debian | rhel | suse | arch | unknown
#   PKG_MANAGER        apt-get | dnf | yum | zypper | pacman | ""
#   PKG_FAMILY_SUPPORTED  1 when we ship a tested bootstrap for this family

set -o pipefail

# --- detection ---------------------------------------------------------------

distro_detect() {
    DISTRO_ID="unknown"
    DISTRO_LIKE=""
    DISTRO_VERSION_ID=""
    DISTRO_PRETTY="unknown Linux"

    if [[ -r /etc/os-release ]]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        DISTRO_ID="${ID:-unknown}"
        DISTRO_LIKE="${ID_LIKE:-}"
        DISTRO_VERSION_ID="${VERSION_ID:-}"
        DISTRO_PRETTY="${PRETTY_NAME:-${NAME:-unknown Linux}}"
    fi

    DISTRO_MAJOR="${DISTRO_VERSION_ID%%.*}"
    DISTRO_FAMILY="$(distro_family "$DISTRO_ID" "$DISTRO_LIKE")"
    PKG_MANAGER="$(distro_pkg_manager)"

    case "$DISTRO_FAMILY" in
        debian|rhel) PKG_FAMILY_SUPPORTED=1 ;;
        *)           PKG_FAMILY_SUPPORTED=0 ;;
    esac

    export DISTRO_ID DISTRO_LIKE DISTRO_VERSION_ID DISTRO_MAJOR DISTRO_PRETTY
    export DISTRO_FAMILY PKG_MANAGER PKG_FAMILY_SUPPORTED
}

# Map an os-release ID / ID_LIKE pair onto the family whose conventions we follow
# (package names, firewall backend, sudo group). Matches Ansible's os_family.
distro_family() {
    local id="${1:-}" like="${2:-}"
    local token
    for token in "$id" $like; do
        case "$token" in
            debian|ubuntu|linuxmint|pop|raspbian|devuan)
                echo "debian"; return 0 ;;
            rhel|fedora|centos|ol|oracle|rocky|almalinux|amzn|scientific)
                echo "rhel"; return 0 ;;
            suse|opensuse|opensuse-leap|opensuse-tumbleweed|sles)
                echo "suse"; return 0 ;;
            arch|archlinux|manjaro)
                echo "arch"; return 0 ;;
        esac
    done
    echo "unknown"
}

# First package manager actually present on this machine. Order matters: prefer
# the modern tool (dnf over yum) so EL8+ does not fall back to the compat shim.
distro_pkg_manager() {
    local candidate
    for candidate in apt-get dnf yum zypper pacman; do
        if command -v "$candidate" >/dev/null 2>&1; then
            echo "$candidate"
            return 0
        fi
    done
    echo ""
}

# --- privilege ---------------------------------------------------------------

# Echo the command prefix needed to run as root ("" when already root,
# "sudo -n" when passwordless sudo works, "sudo" when it will prompt).
# Returns 1 when root cannot be reached at all.
distro_sudo_prefix() {
    if [[ "$(id -u)" -eq 0 ]]; then
        echo ""
        return 0
    fi
    if ! command -v sudo >/dev/null 2>&1; then
        return 1
    fi
    if sudo -n true 2>/dev/null; then
        echo "sudo -n"
    else
        echo "sudo"
    fi
    return 0
}

# --- package operations ------------------------------------------------------

# Refresh the package index. No-op for managers that do it implicitly.
# Usage: distro_pkg_refresh "<sudo prefix>"
distro_pkg_refresh() {
    local sudo_prefix="${1:-}"
    case "$PKG_MANAGER" in
        apt-get) $sudo_prefix env DEBIAN_FRONTEND=noninteractive apt-get update -qq ;;
        dnf)     $sudo_prefix dnf -y makecache --refresh >/dev/null ;;
        yum)     $sudo_prefix yum -y makecache >/dev/null ;;
        zypper)  $sudo_prefix zypper --non-interactive refresh >/dev/null ;;
        pacman)  $sudo_prefix pacman -Sy --noconfirm >/dev/null ;;
        *)       return 1 ;;
    esac
}

# Install packages non-interactively. Usage: distro_pkg_install "<sudo>" pkg...
distro_pkg_install() {
    local sudo_prefix="${1:-}"
    shift
    [[ $# -gt 0 ]] || return 0
    case "$PKG_MANAGER" in
        apt-get) $sudo_prefix env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@" ;;
        dnf)     $sudo_prefix dnf install -y "$@" ;;
        yum)     $sudo_prefix yum install -y "$@" ;;
        zypper)  $sudo_prefix zypper --non-interactive install "$@" ;;
        pacman)  $sudo_prefix pacman -S --noconfirm --needed "$@" ;;
        *)       return 1 ;;
    esac
}

# Translate a logical package name to this distro's real package name.
# Only names that actually differ across families need an entry.
distro_pkg_name() {
    local logical="$1"
    case "$DISTRO_FAMILY:$logical" in
        debian:ansible)     echo "ansible" ;;
        rhel:ansible)       echo "ansible-core" ;;
        suse:ansible)       echo "ansible" ;;
        arch:ansible)       echo "ansible" ;;
        debian:python-venv) echo "python3-venv" ;;
        rhel:python-venv)   echo "python3" ;;      # venv ships inside python3-libs
        suse:python-venv)   echo "python3" ;;
        arch:python-venv)   echo "python" ;;
        debian:python-pip)  echo "python3-pip" ;;
        arch:python-pip)    echo "python-pip" ;;
        *:python-pip)       echo "python3-pip" ;;
        arch:python)        echo "python" ;;
        *:python)           echo "python3" ;;
        *)                  echo "$logical" ;;
    esac
}

# Standalone invocation: report what we detected. Useful for bug reports and for
# checking a machine before committing to an install.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    distro_detect
    echo "distro:        $DISTRO_PRETTY"
    echo "id / like:     $DISTRO_ID / ${DISTRO_LIKE:-(none)}"
    echo "version:       ${DISTRO_VERSION_ID:-(unknown)} (major ${DISTRO_MAJOR:-?})"
    echo "family:        $DISTRO_FAMILY"
    echo "pkg manager:   ${PKG_MANAGER:-(none found)}"
    echo "supported:     $([[ "$PKG_FAMILY_SUPPORTED" == "1" ]] && echo yes || echo "no — bootstrap prerequisites manually")"
fi
