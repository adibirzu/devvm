#!/bin/bash
# palace — memory palace for the coding project.
# ============================================================================
# A structured, durable project memory made of "rooms" (markdown files under
# .memory-palace/). Humans and agents read it to regain full context after a
# disconnect, a new session, or a fresh agent. Optionally mirrors notes into the
# shared MultiLLM context bus so agents can semantically recall them.
#
#   palace rooms                      list rooms
#   palace show <room>                print a room
#   palace note <room> "<text>"       append a timestamped note (+ bus if --share)
#   palace recall "<query>"           search across all rooms (and the bus)
#   palace threads                    show OPEN-THREADS (what you were doing)
set -euo pipefail

PALACE="${MEMORY_PALACE_DIR:-$PWD/.memory-palace}"
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

die() { echo "error: $*" >&2; exit 1; }

_lower() { echo "$1" | tr '[:upper:]' '[:lower:]'; }

_room_path() {
    # Accept "decisions" or "DECISIONS" or "DECISIONS.md"; resolve case-insensitively.
    # Uses tr for lowercasing (not bash-4 case-expansion) so it runs under macOS bash 3.2 too.
    local q="$1" f base ql bl
    q="${q%.md}"; ql="$(_lower "$q")"
    for f in "$PALACE"/*.md; do
        [[ -e "$f" ]] || continue
        base="$(basename "$f" .md)"; bl="$(_lower "$base")"
        case "$bl" in
            "$ql"|*"$ql"*) echo "$f"; return 0;;
        esac
    done
    return 1
}

cmd_rooms() {
    [[ -d "$PALACE" ]] || die "no memory palace at $PALACE (cd to the project root or set MEMORY_PALACE_DIR)"
    echo -e "${CYAN}Memory palace: $PALACE${NC}"
    for f in "$PALACE"/*.md; do
        [[ -e "$f" ]] || continue
        local title; title="$(grep -m1 '^# ' "$f" 2>/dev/null | sed 's/^# //')"
        printf '  %-22s %s\n' "$(basename "$f")" "${title:-}"
    done
}

cmd_show() {
    local room="${1:?usage: palace show <room>}"
    local f; f="$(_room_path "$room")" || die "room not found: $room (try: palace rooms)"
    cat "$f"
}

cmd_note() {
    local share=0
    [[ "${1:-}" == "--share" ]] && { share=1; shift; }
    local room="${1:?usage: palace note [--share] <room> \"<text>\"}"; shift || true
    local text="${*:?note text required}"
    local f; f="$(_room_path "$room")" || die "room not found: $room (try: palace rooms)"
    local ts; ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '\n- **%s** (%s): %s\n' "$ts" "$(whoami)" "$text" >> "$f"
    echo -e "${GREEN}noted${NC} → $(basename "$f")"
    if [[ $share -eq 1 ]] && command -v context >/dev/null 2>&1; then
        context put "palace:$(basename "$f" .md)" "$text" --category palace --shared >/dev/null 2>&1 \
            && echo "  mirrored to shared context bus" || echo -e "  ${YELLOW}(bus mirror skipped)${NC}"
    fi
}

cmd_recall() {
    local q="${1:?usage: palace recall \"<query>\"}"
    [[ -d "$PALACE" ]] || die "no memory palace at $PALACE"
    echo -e "${CYAN}Palace matches for '$q':${NC}"
    grep -rin --include='*.md' -- "$q" "$PALACE" | sed 's/^/  /' || echo "  (no local matches)"
    if command -v context >/dev/null 2>&1; then
        echo -e "${CYAN}Context bus matches:${NC}"
        context search "$q" --all 2>/dev/null | sed 's/^/  /' || true
    fi
}

cmd_threads() {
    local f; f="$(_room_path OPEN-THREADS)" || die "no OPEN-THREADS room"
    cat "$f"
}

case "${1:-help}" in
    rooms|ls) shift; cmd_rooms;;
    show|cat) shift; cmd_show "$@";;
    note|add) shift; cmd_note "$@";;
    recall|search) shift; cmd_recall "$@";;
    threads) shift; cmd_threads;;
    *)
        cat <<EOF
palace — memory palace for this coding project ($PALACE)

  palace rooms                    list rooms
  palace show <room>              print a room (e.g. palace show decisions)
  palace note [--share] <room> "<text>"   append a timestamped note
  palace recall "<query>"         search rooms (+ shared context bus)
  palace threads                  what you were doing (OPEN-THREADS)
EOF
        ;;
esac
