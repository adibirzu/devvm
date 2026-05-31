#!/bin/bash
# egress_allowlist.sh — Profile A (cloud VM) outbound network allowlist.
#
# Restricts the VM's OUTBOUND traffic to only what coding agents legitimately need
# (LLM APIs + package registries), blocking everything else to break the third leg
# of the lethal trifecta (external comms) for a compromised/poisoned agent.
#
# OPT-IN and reversible. Review the allowlist before applying. Run as root on the VM:
#   sudo ./egress_allowlist.sh apply     # enable
#   sudo ./egress_allowlist.sh status    # show rules
#   sudo ./egress_allowlist.sh revert    # disable (restore default-allow egress)
#
# NOTE: this uses ufw outbound rules. It deliberately keeps the WireGuard subnet and
# DNS open so the fleet keeps working. Tune ALLOW_DOMAINS to your providers.
set -euo pipefail

# Endpoints coding agents need. Resolved to IPs at apply time (ufw is IP-based).
ALLOW_DOMAINS=(
  "api.anthropic.com"
  "api.openai.com"
  "generativelanguage.googleapis.com"
  "antigravity.google"
  "registry.npmjs.org"
  "pypi.org"
  "files.pythonhosted.org"
  "github.com"
  "objects.githubusercontent.com"
)
WG_SUBNET="10.200.200.0/24"

require_root() { [ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)"; exit 1; }; }

apply() {
  require_root
  command -v ufw >/dev/null || { echo "ufw not installed"; exit 1; }
  echo "Configuring outbound egress allowlist (default-deny outbound)…"
  # Keep loopback + VPN + DNS so the fleet and name resolution survive.
  ufw allow out on lo
  ufw allow out to "$WG_SUBNET"
  ufw allow out 53
  for d in "${ALLOW_DOMAINS[@]}"; do
    for ip in $(getent ahosts "$d" | awk '{print $1}' | sort -u); do
      ufw allow out to "$ip" comment "egress: $d" || true
    done
  done
  ufw default deny outgoing
  ufw reload
  echo "Egress allowlist applied. Review: ufw status numbered"
  echo "Revert with: $0 revert"
}

status() { require_root; ufw status numbered | grep -i "out\|OUT\|egress" || ufw status; }

revert() {
  require_root
  echo "Restoring default-allow outbound…"
  ufw default allow outgoing
  ufw reload
  echo "Reverted. (Per-IP 'egress:' allow rules are harmless once default is allow; remove individually if desired.)"
}

case "${1:-help}" in
  apply) apply;;
  status) status;;
  revert) revert;;
  *) echo "usage: $0 {apply|status|revert}";;
esac
