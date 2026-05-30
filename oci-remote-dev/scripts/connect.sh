#!/bin/bash
# OCI Remote Development Server - Connect Script
# ===============================================
# Helpers for connecting to the remote development server

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Load config
if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a; source "$PROJECT_DIR/.env"; set +a
elif [[ -f "$PROJECT_DIR/.env.local" ]]; then
    set -a; source "$PROJECT_DIR/.env.local"; set +a
fi

# Get deployment info
DEPLOY_INFO="$PROJECT_DIR/configs/deployment-info.txt"
if [[ -f "$DEPLOY_INFO" ]]; then
    PUBLIC_IP=$(grep "Public IP:" "$DEPLOY_INFO" | cut -d: -f2 | tr -d ' ')
fi

# Multi-Developer Options Parsing
DEVELOPER="${ADMIN_USERNAME:-devuser}"
COMMAND=""

# Parse options
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        -u|--user)
            DEVELOPER="$2"
            shift 2
            ;;
        -*)
            echo "Unknown option: $1"
            exit 1
            ;;
        *)
            COMMAND="$1"
            shift
            ;;
    esac
done

usage() {
    echo "Usage: $0 [-u <developer_name>] <command>"
    echo ""
    echo "Options:"
    echo "  -u, --user    - Target developer profile (defaults to ${ADMIN_USERNAME:-devuser})"
    echo ""
    echo "Commands:"
    echo "  ssh           - SSH to the server"
    echo "  tunnel        - Create SSH tunnel for RDP"
    echo "  code          - Open code-server in browser (via SSH tunnel)"
    echo "  wg-up         - Bring up WireGuard connection (macOS/Linux)"
    echo "  wg-down       - Bring down WireGuard connection (macOS/Linux)"
    echo "  wg-status     - Show WireGuard status"
    echo "  qr            - Show WireGuard QR code for phone"
    echo "  copy-config   - Copy WireGuard config to clipboard"
    echo ""
}

# Resolve custom code-server ports for any configured developer.
PORT="${CODE_SERVER_PORT:-8443}"
while IFS= read -r var; do
    idx="${var#DEV_}"
    idx="${idx%_NAME}"
    if [[ "$DEVELOPER" == "${!var}" ]]; then
        port_var="DEV_${idx}_CODE_SERVER_PORT"
        PORT="${!port_var:-$PORT}"
    fi
done < <(compgen -A variable | grep -E '^DEV_[0-9]+_NAME$' || true)

# Get SSH key path
get_ssh_key() {
    local ssh_pub="${SSH_PUBLIC_KEY_PATH/#\~/$HOME}"
    local ssh_key="${ssh_pub%.pub}"
    if [[ -f "$ssh_key" ]]; then
        echo "$ssh_key"
    else
        echo ""
    fi
}

SSH_KEY=$(get_ssh_key)
SSH_CONTROL_PATH="/tmp/oci-remote-dev-%r@%h:%p"
SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ControlMaster=auto -o "ControlPath=$SSH_CONTROL_PATH" -o ControlPersist=300)
if [[ -n "$SSH_KEY" ]]; then
    SSH_OPTS+=(-i "$SSH_KEY" -o IdentitiesOnly=yes)
fi

# Resolve developer specific WireGuard files
WG_CONF="$PROJECT_DIR/configs/wireguard/client_${DEVELOPER}.conf"
if [[ ! -f "$WG_CONF" ]]; then
    WG_CONF="$PROJECT_DIR/configs/wireguard/client.conf"
fi

QR_FILE="$PROJECT_DIR/configs/wireguard/client_${DEVELOPER}-qr.txt"
if [[ ! -f "$QR_FILE" ]]; then
    QR_FILE="$PROJECT_DIR/configs/wireguard/client-qr.txt"
fi

# Run wg-quick robustly on macOS. Two gotchas this works around:
#   1. wg-quick needs bash 4+, but macOS ships bash 3.2 as /bin/bash, and under
#      `sudo` the secure PATH only exposes that old bash → version-mismatch error.
#   2. wg-quick calls `wg` / `bash`, which live in the Homebrew prefix that sudo's
#      secure PATH drops.
# Injecting the wg-quick directory (Homebrew prefix) into PATH for the sudo call
# fixes both: the modern bash and `wg` are found.
run_wg_quick() {
    local action="$1" conf="$2"
    local wgq wgq_dir
    wgq="$(command -v wg-quick 2>/dev/null)"
    if [[ -z "$wgq" ]]; then
        echo -e "${YELLOW}wg-quick not found. Install with: brew install wireguard-tools${NC}"
        return 1
    fi
    wgq_dir="$(dirname "$wgq")"
    sudo env PATH="$wgq_dir:/usr/bin:/bin:/usr/sbin:/sbin" wg-quick "$action" "$conf"
}

case "${COMMAND:-help}" in
    ssh)
        echo -e "${CYAN}Connecting as developer '${DEVELOPER}' via SSH...${NC}"
        ssh "${SSH_OPTS[@]}" "$DEVELOPER@$PUBLIC_IP"
        ;;

    tunnel)
        echo -e "${CYAN}Creating SSH tunnel for RDP as developer '${DEVELOPER}'...${NC}"
        echo "Connect your RDP client to localhost:${RDP_PORT:-3389}"
        echo "Press Ctrl+C to stop the tunnel"
        ssh "${SSH_OPTS[@]}" -o ExitOnForwardFailure=yes -L 3389:localhost:$RDP_PORT -N "$DEVELOPER@$PUBLIC_IP"
        ;;

    code)
        echo -e "${CYAN}Creating SSH tunnel for code-server on port $PORT as developer '${DEVELOPER}'...${NC}"
        echo "Opening http://localhost:$PORT in browser..."
        ssh "${SSH_OPTS[@]}" -o ExitOnForwardFailure=yes -L $PORT:localhost:$PORT -N "$DEVELOPER@$PUBLIC_IP" &
        SSH_PID=$!
        sleep 2
        open "http://localhost:$PORT" 2>/dev/null || \
            xdg-open "http://localhost:$PORT" 2>/dev/null || \
            echo "Open http://localhost:$PORT in your browser"
        echo "Press Enter to close the tunnel..."
        read
        kill $SSH_PID 2>/dev/null
        ;;

    wg-up)
        if [[ ! -f "$WG_CONF" ]]; then
            echo -e "${YELLOW}WireGuard config not found for developer '${DEVELOPER}' at $WG_CONF. Run deployment first.${NC}"
            exit 1
        fi
        # We bring the tunnel up with wg-quick (reads the .conf directly) rather
        # than the WireGuard macOS app. The app stores a COPY of the config at
        # import time inside its NetworkExtension, so editing the .conf later does
        # NOT update what the app pushes — a stale `DNS =` line in that cached copy
        # is what breaks DNS/internet on a split tunnel. wg-quick always uses the
        # current file. Set WG_USE_APP=1 to force the app instead.
        echo -e "${CYAN}Bringing up WireGuard with config: $WG_CONF...${NC}"
        if [[ "$(uname)" == "Darwin" && "${WG_USE_APP:-0}" == "1" ]]; then
            echo -e "${YELLOW}Using WireGuard.app. IMPORTANT: delete any existing '$(basename "${WG_CONF%.conf}")' tunnel and RE-IMPORT this file,${NC}"
            echo -e "${YELLOW}otherwise the app keeps a stale cached config (incl. an old DNS line).${NC}"
            echo "  $WG_CONF"
            open /Applications/WireGuard.app
        else
            run_wg_quick up "$WG_CONF" && \
                echo -e "${GREEN}Tunnel up via wg-quick (current file, no app cache).${NC}"
        fi
        ;;

    wg-down)
        echo -e "${CYAN}Bringing down WireGuard for developer '${DEVELOPER}'...${NC}"
        if [[ "$(uname)" == "Darwin" && "${WG_USE_APP:-0}" == "1" ]]; then
            echo "Disconnect via WireGuard app"
            open /Applications/WireGuard.app
        else
            run_wg_quick down "$WG_CONF" 2>/dev/null || echo "Not connected"
        fi
        ;;

    wg-status)
        echo -e "${CYAN}WireGuard Status:${NC}"
        sudo wg show 2>/dev/null || echo "WireGuard not active"
        ;;

    qr)
        if [[ -f "$QR_FILE" ]]; then
            echo -e "${CYAN}Scan this QR code with your phone's WireGuard app for developer '${DEVELOPER}':${NC}"
            cat "$QR_FILE"
        else
            if [[ -f "$WG_CONF" ]] && command -v qrencode &>/dev/null; then
                echo -e "${CYAN}Generating QR code for developer '${DEVELOPER}'...${NC}"
                qrencode -t ansiutf8 < "$WG_CONF"
            else
                echo -e "${YELLOW}QR code not available for developer '${DEVELOPER}'. Install qrencode or check configs.${NC}"
            fi
        fi
        ;;

    copy-config)
        if [[ -f "$WG_CONF" ]]; then
            if [[ "$(uname)" == "Darwin" ]]; then
                cat "$WG_CONF" | pbcopy
                echo -e "${GREEN}WireGuard config for developer '${DEVELOPER}' copied to clipboard!${NC}"
            else
                cat "$WG_CONF" | xclip -selection clipboard 2>/dev/null || \
                cat "$WG_CONF" | xsel --clipboard 2>/dev/null || \
                { echo "Install xclip or xsel for clipboard support"; cat "$WG_CONF"; }
            fi
        else
            echo -e "${YELLOW}WireGuard config not found for developer '${DEVELOPER}'.${NC}"
        fi
        ;;

    help|--help|-h|*)
        usage
        ;;
esac
