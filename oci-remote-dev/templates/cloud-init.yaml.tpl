#cloud-config
# OCI Remote Development Server Cloud-Init Configuration
# ========================================================
# Configures base networking, WireGuard, and SSH user accounts
# Configures the server to be immediately ready for Ansible setup.

hostname: {{VM_NAME}}
fqdn: {{VM_NAME}}.local
manage_etc_hosts: true

apt:
  conf: |
    Dpkg::Options {
      "--force-confdef";
      "--force-confold";
    }
    APT::Get::Assume-Yes "true";
    DPkg::Lock::Timeout "300";

bootcmd:
  - echo 'debconf debconf/frontend select Noninteractive' | debconf-set-selections

groups:
  - developers

{{USERS_CONFIG}}

package_update: true
package_upgrade: true

packages:
  - curl
  - wget
  - git
  - vim
  - tmux
  - jq
  - wireguard
  - wireguard-tools
  - dbus-x11
  - ufw
  - sudo

write_files:
  # Detect primary network interface script
  - path: /opt/detect-interface.sh
    permissions: '0755'
    content: |
      #!/bin/bash
      ip route | grep default | awk '{print $5}' | head -1

  # WireGuard server configuration
  - path: /etc/wireguard/wg0.conf
    permissions: '0600'
    content: |
      [Interface]
      PrivateKey = {{WG_SERVER_PRIVATE_KEY}}
      Address = {{WG_SERVER_IP}}/24
      ListenPort = {{WG_PORT}}
      PostUp = IFACE=$(/opt/detect-interface.sh); iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o $IFACE -j MASQUERADE
      PostDown = IFACE=$(/opt/detect-interface.sh); iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o $IFACE -j MASQUERADE

{{WG_PEERS_CONFIG}}

  # MOTD with connection info
  - path: /etc/update-motd.d/99-devserver-info
    permissions: '0755'
    content: |
      #!/bin/bash
      PUBLIC_IP=$(curl -s http://169.254.169.254/opc/v1/vnics/ 2>/dev/null | jq -r '.[0].publicIp' 2>/dev/null || echo "unknown")
      echo ""
      echo "╔══════════════════════════════════════════════════════════════╗"
      echo "║           OCI Remote Development Server                      ║"
      echo "╠══════════════════════════════════════════════════════════════╣"
      echo "║  Public IP: $PUBLIC_IP"
      echo "║  WireGuard VPN: wg0 ({{WG_SERVER_IP}})"
      echo "║  Desktop: XFCE via RDP on port {{RDP_PORT}}"
      echo "║  Collaboration Hub Dashboard: http://{{WG_SERVER_IP}}"
      echo "╚══════════════════════════════════════════════════════════════╝"
      echo ""
      echo "Connect via WireGuard VPN first, then open http://{{WG_SERVER_IP}} in your browser!"
      echo ""

runcmd:
  # Enable WireGuard
  - systemctl enable wg-quick@wg0
  - systemctl start wg-quick@wg0 || echo "WireGuard start failed"

  # Initial user setups
  - |
    DEVELOPERS=({{DEVELOPERS_LIST}})
    for dev in "${DEVELOPERS[@]}"; do
      usermod -aG developers $dev || true
      chown -R $dev:$dev /home/$dev
    done

  # Log completion
  - echo "=== Base OCI Remote Dev Server Setup Complete at $(date) ===" >> /var/log/cloud-init-output.log

final_message: |
  ============================================
  Base Remote Development Server is ready!
  Now run Ansible to complete the setup.
  ============================================
