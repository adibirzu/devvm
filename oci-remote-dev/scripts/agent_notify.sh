#!/bin/bash
# agent-notify — record "an agent needs attention" events for the notification ring.
# ============================================================================
# Append-only per-user JSONL feed at ~/.agentctl/notifications.jsonl. Agents call
# this (via their Notification hook) when they need input; the status board reads
# RECENT events and shows a glow ring + a phone notification. Events are not marked
# read — they expire by time on the read side, so no write-back endpoint is needed.
#
#   agent-notify "<message>"                 (session from $AGENTCTL_SESSION)
#   agent-notify -s <session> "<message>"
#   agent-notify --list [-n N]
set -euo pipefail

STATE_DIR="${AGENTCTL_HOME:-$HOME/.agentctl}"
FEED="$STATE_DIR/notifications.jsonl"
mkdir -p "$STATE_DIR"

_json_escape() {
    # Minimal JSON string escaper (quotes, backslashes, control chars).
    local s="$1"
    s="${s//\\/\\\\}"; s="${s//\"/\\\"}"
    s="${s//$'\n'/ }"; s="${s//$'\t'/ }"; s="${s//$'\r'/ }"
    printf '%s' "$s"
}

case "${1:-}" in
    --list)
        n=20; [[ "${2:-}" == "-n" ]] && n="${3:-20}"
        [[ -f "$FEED" ]] && tail -n "$n" "$FEED" || echo "(no notifications)"
        ;;
    *)
        session="${AGENTCTL_SESSION:-${HOSTNAME:-shell}}"
        if [[ "${1:-}" == "-s" ]]; then session="$2"; shift 2; fi
        message="${*:-needs your attention}"
        ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf '{"ts":"%s","session":"%s","user":"%s","message":"%s"}\n' \
            "$ts" "$(_json_escape "$session")" "$(whoami)" "$(_json_escape "$message")" >> "$FEED"
        # Keep the feed bounded (last 500 events).
        if [[ "$(wc -l < "$FEED" 2>/dev/null || echo 0)" -gt 600 ]]; then
            tail -n 500 "$FEED" > "$FEED.tmp" && mv "$FEED.tmp" "$FEED"
        fi
        ;;
esac
