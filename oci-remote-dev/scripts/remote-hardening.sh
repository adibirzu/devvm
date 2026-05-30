#!/bin/bash
# Remote hardening/bootstrap for OCI dev VM.
# - Enforces code-server password auth
# - Installs persistent services for Claude Code UI and Vibe Kanban
# - Applies/persists Oracle-image iptables allow rules
# - Enables fail2ban SSH jail

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

[[ -f "$PROJECT_DIR/.env.local" ]] && { set -a; source "$PROJECT_DIR/.env.local"; set +a; }

PUBLIC_IP="${1:-}"
if [[ -z "$PUBLIC_IP" && -f "$PROJECT_DIR/configs/deployment-info.txt" ]]; then
    PUBLIC_IP="$(grep "Public IP:" "$PROJECT_DIR/configs/deployment-info.txt" | cut -d: -f2 | tr -d ' ')"
fi

if [[ -z "$PUBLIC_IP" ]]; then
    echo "Usage: $0 <public-ip>"
    exit 1
fi

ADMIN_USER="${ADMIN_USERNAME:-devuser}"
SSH_PUB="${SSH_PUBLIC_KEY_PATH:-$HOME/.ssh/id_rsa.pub}"
SSH_KEY="${SSH_PUB%.pub}"

if [[ ! -f "$SSH_KEY" ]]; then
    echo "SSH private key not found: $SSH_KEY"
    exit 1
fi

SSH_OPTS=(
    -i "$SSH_KEY"
    -o BatchMode=yes
    -o ConnectTimeout=12
    -o StrictHostKeyChecking=no
    -o UserKnownHostsFile=/dev/null
    -o IdentitiesOnly=yes
    -o ControlMaster=auto
    -o ControlPath=/tmp/oci-cm-%r@%h:%p
    -o ControlPersist=300
)

TARGET="${ADMIN_USER}@${PUBLIC_IP}"

ssh "${SSH_OPTS[@]}" "$TARGET" 'bash -s' <<'REMOTE'
set -euo pipefail

# Get all developers in 'developers' group
DEVS=($(getent group developers | cut -d: -f4 | tr ',' ' '))
if [ ${#DEVS[@]} -eq 0 ]; then
    DEVS=("${ADMIN_USER}")
fi

echo "Found developers to harden: ${DEVS[*]}"

# Enable and start claudecodeui & vibe-kanban for owner/admin
sed -i "/\/bin\/brew shellenv/d" "$HOME/.bashrc" || true
if ! grep -q '^export PATH="\$HOME/.npm-global/bin' "$HOME/.bashrc"; then
    echo 'export PATH="$HOME/.npm-global/bin:$HOME/bin:$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
fi

# Hardening code-server for each developer
for dev in "${DEVS[@]}"; do
    DEV_HOME="/home/$dev"
    echo "Hardening code-server for $dev..."

    mkdir -p "$DEV_HOME/.config/remote-dev"
    chmod 700 "$DEV_HOME/.config/remote-dev"
    
    CODE_SERVER_PASSWORD="$(openssl rand -base64 30 | tr -d '=+/' | cut -c1-24)"
    cat > "$DEV_HOME/.config/remote-dev/credentials.env" <<EOF
CODE_SERVER_PASSWORD=${CODE_SERVER_PASSWORD}
EOF
    chmod 600 "$DEV_HOME/.config/remote-dev/credentials.env"
    chown -R $dev:$dev "$DEV_HOME/.config/remote-dev"

    # Preserving custom ports if config exists, else use base sequential logic
    PORT=8443
    if [ -f "$DEV_HOME/.config/code-server/config.yaml" ]; then
        PORT=$(grep "bind-addr:" "$DEV_HOME/.config/code-server/config.yaml" | cut -d: -f3 | tr -d ' ')
    fi
    PORT=${PORT:-8443}

    mkdir -p "$DEV_HOME/.config/code-server"
    cat > "$DEV_HOME/.config/code-server/config.yaml" <<EOF
bind-addr: 0.0.0.0:${PORT}
auth: password
password: ${CODE_SERVER_PASSWORD}
cert: false
EOF
    chown -R $dev:$dev "$DEV_HOME/.config/code-server"
    
    sudo systemctl restart code-server@$dev || true
    echo "Developer: $dev | Port: $PORT | Password: $CODE_SERVER_PASSWORD"
done

# claudecodeui and vibe-kanban system services
sudo tee /etc/systemd/system/claudecodeui.service >/dev/null <<EOF
[Unit]
Description=Claude Code UI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${ADMIN_USER}
WorkingDirectory=/home/${ADMIN_USER}/apps/claudecodeui
Environment=PATH=/home/${ADMIN_USER}/.npm-global/bin:/home/${ADMIN_USER}/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/home/${ADMIN_USER}/.npm-global/bin/claude-code-ui --port 3001
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/vibe-kanban.service >/dev/null <<EOF
[Unit]
Description=Vibe Kanban
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${ADMIN_USER}
WorkingDirectory=/home/${ADMIN_USER}/apps/vibe-kanban
Environment=PATH=/home/${ADMIN_USER}/.npm-global/bin:/home/${ADMIN_USER}/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
Environment=PORT=3000
Environment=HOST=0.0.0.0
ExecStart=/home/${ADMIN_USER}/.npm-global/bin/vibe-kanban
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now claudecodeui.service vibe-kanban.service || true

ensure_rule() {
    if ! sudo iptables -C INPUT "$@" 2>/dev/null; then
        sudo iptables -I INPUT 4 "$@"
    fi
}

ensure_rule -p tcp -m state --state NEW -m tcp --dport 3000 -j ACCEPT
ensure_rule -p tcp -m state --state NEW -m tcp --dport 3001 -j ACCEPT
ensure_rule -p tcp -m state --state NEW -m tcp --dport 80 -j ACCEPT
ensure_rule -p udp -m udp --dport 51820 -j ACCEPT

# Allow all developers' code-server ports
for dev in "${DEVS[@]}"; do
    DEV_HOME="/home/$dev"
    if [ -f "$DEV_HOME/.config/code-server/config.yaml" ]; then
        PORT=$(grep "bind-addr:" "$DEV_HOME/.config/code-server/config.yaml" | cut -d: -f3 | tr -d ' ')
        if [[ -n "$PORT" ]]; then
            ensure_rule -p tcp -m state --state NEW -m tcp --dport "$PORT" -j ACCEPT
        fi
    fi
done

sudo DEBIAN_FRONTEND=noninteractive apt-get update -y >/dev/null
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent fail2ban >/dev/null
sudo netfilter-persistent save >/dev/null

sudo tee /etc/fail2ban/jail.d/sshd.local >/dev/null <<EOF
[sshd]
enabled = true
port = 22
logpath = %(sshd_log)s
backend = systemd
maxretry = 5
findtime = 10m
bantime = 1h
EOF
sudo systemctl enable --now fail2ban >/dev/null
REMOTE

echo ""
echo "HARDENING COMPLETE"
echo "Host: $PUBLIC_IP"
echo "SSH: ssh -i $SSH_KEY ${ADMIN_USER}@${PUBLIC_IP}"
echo "Dev Dashboard: http://${PUBLIC_IP}:80"
echo "claudecodeui: http://${PUBLIC_IP}:3001"
echo "vibe-kanban: http://${PUBLIC_IP}:3000"
