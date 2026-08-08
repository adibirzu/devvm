#!/bin/bash
# verify.sh — post-deploy verification, run ON THE VM.
# Confirms the agentic dev OS came up: systemd units, VPN-only endpoints, the agent
# CLIs, and a live exercise of the guardrail + notification hooks. Exits non-zero if
# anything essential failed, so it doubles as a CI/smoke gate after `deploy.sh`.
set -uo pipefail

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
PASS=0; FAIL=0; WARN=0
# Defaults match a cloud deploy (services on the WireGuard IP); a direct install
# binds the loopback instead and exports these, so both paths verify the same way.
WG_IP="${WG_SERVER_IP:-10.200.200.1}"
GW_PORT="${MULTILLM_GATEWAY_PORT:-8080}"
CP_PORT="${CONTROL_PLANE_PORT:-8082}"
DASH_PORT="${DASHBOARD_PORT:-80}"

ok()   { echo -e "  ${GREEN}✓${NC} $*"; PASS=$((PASS+1)); }
bad()  { echo -e "  ${RED}✗${NC} $*"; FAIL=$((FAIL+1)); }
warn() { echo -e "  ${YELLOW}!${NC} $*"; WARN=$((WARN+1)); }
sect() { echo -e "\n${CYAN}== $* ==${NC}"; }

http_code() { python3 - "$1" <<'PY' 2>/dev/null || echo 000
import sys, urllib.request, urllib.error
try:
    with urllib.request.urlopen(sys.argv[1], timeout=4) as r: print(r.status)
except urllib.error.HTTPError as e: print(e.code)
except Exception: print("000")
PY
}

sect "Agent CLIs installed"
for c in agentctl palace usage-report context guardrail guardrail-hook mcp-registry \
         agent-status project-status agent-job agent-notify git-whoami control-plane; do
  command -v "$c" >/dev/null 2>&1 && ok "$c" || bad "$c missing from PATH"
done

sect "Systemd units"
for u in multillm-gateway.service dev-dashboard.service agent-status.timer \
         project-status.timer control-plane.service agent-jobs.timer agentctl-restore.service; do
  if ! systemctl list-unit-files "$u" >/dev/null 2>&1; then warn "$u not installed"; continue; fi
  state="$(systemctl is-active "$u" 2>/dev/null || true)"
  case "$state" in
    active) ok "$u active";;
    *) if [[ "$u" == *.timer || "$u" == agentctl-restore.service ]]; then
         systemctl is-enabled "$u" >/dev/null 2>&1 && ok "$u enabled ($state)" || warn "$u $state"
       else bad "$u $state"; fi;;
  esac
done

sect "VPN-only endpoints"
for probe in "http://${WG_IP}:${DASH_PORT}/|landing :${DASH_PORT}" \
             "http://${WG_IP}:${GW_PORT}/health|gateway /health" \
             "http://${WG_IP}:${CP_PORT}/healthz|control-plane /healthz"; do
  url="${probe%%|*}"; label="${probe##*|}"
  code="$(http_code "$url")"
  [[ "$code" == "200" ]] && ok "$label ($code)" || bad "$label ($code)"
done

sect "Guardrail hook (live) — destructive call must be DENIED"
deny="$(echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf /"}}' | guardrail-hook 2>/dev/null)"
echo "$deny" | grep -q '"permissionDecision": "deny"' && ok "rm -rf / denied" || bad "guardrail did not deny rm -rf / (got: ${deny:-empty})"
allow="$(echo '{"tool_name":"Bash","tool_input":{"command":"ls -la"}}' | guardrail-hook 2>/dev/null)"
[[ -z "$allow" ]] && ok "safe command allowed (silent)" || warn "expected silent allow, got: $allow"

sect "Notification feed (live)"
AGENTCTL_SESSION="verify:self" agent-notify "verify.sh smoke" >/dev/null 2>&1 \
  && grep -q "verify.sh smoke" "$HOME/.agentctl/notifications.jsonl" 2>/dev/null \
  && ok "agent-notify wrote to the feed" || warn "agent-notify feed not confirmed (ok if first run)"

sect "Board snapshots"
for f in /opt/dashboard/agents.json /opt/dashboard/projects.json; do
  if [[ -f "$f" ]] && python3 -c "import json,sys; json.load(open('$f'))" 2>/dev/null; then
    ok "$(basename "$f") present + valid JSON"
  else warn "$(basename "$f") not yet generated (timer runs every 15–30s)"; fi
done

sect "Per-account git identity"
git-whoami >/dev/null 2>&1 && ok "git-whoami runs" || warn "git-whoami issue"

echo ""
echo -e "${CYAN}Summary:${NC} ${GREEN}${PASS} passed${NC}, ${YELLOW}${WARN} warnings${NC}, ${RED}${FAIL} failed${NC}"
[[ $FAIL -eq 0 ]] && { echo -e "${GREEN}Verification OK.${NC}"; exit 0; } || { echo -e "${RED}Verification found failures.${NC}"; exit 1; }
