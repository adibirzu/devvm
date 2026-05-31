#!/bin/bash
# agentctl — durable multi-agent session manager for the remote dev OS.
# ============================================================================
# Runs coding agents (claude, codex, gemini, aider, opencode, or any command)
# inside detached tmux sessions ON THE VM, so they keep working when your
# WireGuard tunnel, SSH connection, or internet drops. Reconnect and `attach`
# to pick up exactly where things were. Sessions are per-UNIX-user and isolated.
#
# Why this survives disconnects:
#   * the agent runs server-side in tmux, decoupled from your SSH/WG client;
#   * `loginctl enable-linger <user>` (set by Ansible) keeps your processes
#     alive after the login session ends;
#   * pair this with mosh (also installed) for a client link that survives IP
#     changes, sleep, and roaming.
#
# Usage:
#   agentctl start <agent> [-p <project>] [-d <dir>] [-- <cmd...>]
#   agentctl attach <name>
#   agentctl ls | status [--json]
#   agentctl logs <name> [-n <lines>]
#   agentctl stop <name>
#   agentctl resume            # print OPEN-THREADS from the memory palace
set -euo pipefail

STATE_DIR="${AGENTCTL_HOME:-$HOME/.agentctl}"
SOCKET="$STATE_DIR/tmux.sock"
mkdir -p "$STATE_DIR/meta"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'

TMUX=(tmux -S "$SOCKET")

die() { echo -e "${RED}error:${NC} $*" >&2; exit 1; }
command -v tmux >/dev/null 2>&1 || die "tmux not installed (Ansible installs it on the VM)."

# Session names are namespaced: agent:<project>:<agent>. Sanitize to a tmux-safe id.
_sanitize() { echo "$1" | tr -c 'A-Za-z0-9_.-' '_'; }

_meta_file() { echo "$STATE_DIR/meta/$(_sanitize "$1").env"; }

# Read a single key from a metadata file WITHOUT sourcing it (sourcing would run
# values like `cmd=sleep 120` as commands and break on spaces / inject shell).
_meta_get() { grep -m1 "^$2=" "$1" 2>/dev/null | cut -d= -f2-; }

cmd_start() {
    local agent="" project="default" dir="$PWD" cmd=()
    agent="${1:-}"; shift || true
    [[ -n "$agent" ]] || die "usage: agentctl start <agent> [-p project] [-d dir] [--no-restart] [-- cmd...]"
    local restartable="true"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -p|--project) project="$2"; shift 2;;
            -d|--dir) dir="$2"; shift 2;;
            --no-restart) restartable="false"; shift;;
            --) shift; cmd=("$@"); break;;
            *) die "unknown arg: $1";;
        esac
    done
    [[ -d "$dir" ]] || die "directory not found: $dir"

    # Default command per known agent; fall back to the agent name as a binary.
    if [[ ${#cmd[@]} -eq 0 ]]; then
        case "$agent" in
            claude)  cmd=(claude);;
            codex)   cmd=(codex);;
            gemini)  cmd=(gemini);;
            aider)   cmd=(aider);;
            opencode) cmd=(opencode);;
            *)       cmd=("$agent");;
        esac
    fi

    local name="agent:${project}:${agent}"
    local sname; sname="$(_sanitize "$name")"

    if "${TMUX[@]}" has-session -t "$sname" 2>/dev/null; then
        echo -e "${YELLOW}Session '$name' already running.${NC} Attach with: agentctl attach '$name'"
        return 0
    fi

    # -e injects AGENTCTL_SESSION so the agent's notification hook can tag the ring.
    "${TMUX[@]}" new-session -d -s "$sname" -e "AGENTCTL_SESSION=$name" -c "$dir" "${cmd[@]}"
    # Persist metadata for status/restoration (timestamps come from `date` at write time).
    {
        echo "name=$name"
        echo "agent=$agent"
        echo "project=$project"
        echo "dir=$dir"
        echo "cmd=${cmd[*]}"
        echo "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "restartable=$restartable"
    } > "$(_meta_file "$name")"

    echo -e "${GREEN}Started${NC} $name  (dir: $dir)"
    echo "Detach with Ctrl-b d; it keeps running if you disconnect. Reattach: agentctl attach '$name'"
}

cmd_attach() {
    local name="${1:?usage: agentctl attach <name>}"
    local sname; sname="$(_sanitize "$name")"
    "${TMUX[@]}" has-session -t "$sname" 2>/dev/null || die "no live session '$name' (see: agentctl ls)"
    exec "${TMUX[@]}" attach-session -t "$sname"
}

# Map a tmux session to a coarse state for the visibility layer.
_session_state() {
    local sname="$1"
    local attached; attached="$("${TMUX[@]}" display-message -p -t "$sname" '#{session_attached}' 2>/dev/null || echo 0)"
    if [[ "$attached" != "0" ]]; then echo "attached"; else echo "running"; fi
}

cmd_ls() {
    local as_json=0
    [[ "${1:-}" == "--json" ]] && as_json=1
    local live=()
    while IFS= read -r s; do [[ -n "$s" ]] && live+=("$s"); done < <("${TMUX[@]}" list-sessions -F '#{session_name}' 2>/dev/null || true)

    if [[ $as_json -eq 1 ]]; then
        printf '['
        local first=1
        for f in "$STATE_DIR"/meta/*.env; do
            [[ -e "$f" ]] || continue
            local name agent project dir started state sname
            name="$(_meta_get "$f" name)"; agent="$(_meta_get "$f" agent)"
            project="$(_meta_get "$f" project)"; dir="$(_meta_get "$f" dir)"
            started="$(_meta_get "$f" started_at)"
            sname="$(_sanitize "$name")"
            state="dead"
            for ls in "${live[@]:-}"; do [[ "$ls" == "$sname" ]] && state="$(_session_state "$sname")"; done
            [[ $first -eq 1 ]] || printf ','
            first=0
            printf '{"name":"%s","agent":"%s","project":"%s","dir":"%s","started_at":"%s","state":"%s"}' \
                "$name" "$agent" "$project" "$dir" "$started" "$state"
        done
        printf ']\n'
        return 0
    fi

    echo -e "${CYAN}Durable agent sessions (user: $(whoami))${NC}"
    printf '  %-28s %-10s %-12s %s\n' "NAME" "STATE" "PROJECT" "DIR"
    printf '  %s\n' "------------------------------------------------------------------------"
    local any=0
    for f in "$STATE_DIR"/meta/*.env; do
        [[ -e "$f" ]] || continue
        local name project dir sname state color
        name="$(_meta_get "$f" name)"; project="$(_meta_get "$f" project)"; dir="$(_meta_get "$f" dir)"
        sname="$(_sanitize "$name")"
        state="dead"
        for ls in "${live[@]:-}"; do [[ "$ls" == "$sname" ]] && state="$(_session_state "$sname")"; done
        case "$state" in
            attached) color="$GREEN";; running) color="$CYAN";; *) color="$YELLOW";;
        esac
        printf '  %-28s '"$color"'%-10s'"$NC"' %-12s %s\n' "${name:0:28}" "$state" "${project:0:12}" "$dir"
        any=1
    done
    [[ $any -eq 1 ]] || echo "  (none — start one: agentctl start claude -p myproj -d ~/shared-workspace/myproj)"
}

cmd_logs() {
    local name="${1:?usage: agentctl logs <name> [-n lines]}"; shift || true
    local n=200; [[ "${1:-}" == "-n" ]] && { n="$2"; }
    local sname; sname="$(_sanitize "$name")"
    "${TMUX[@]}" has-session -t "$sname" 2>/dev/null || die "no live session '$name'"
    "${TMUX[@]}" capture-pane -p -t "$sname" -S "-$n"
}

cmd_stop() {
    local name="${1:?usage: agentctl stop <name>}"
    local sname; sname="$(_sanitize "$name")"
    "${TMUX[@]}" kill-session -t "$sname" 2>/dev/null || echo "not running"
    rm -f "$(_meta_file "$name")"
    echo "stopped $name"
}

cmd_resume() {
    # On reconnect, reload what you were doing from the project memory palace.
    local palace="${MEMORY_PALACE_DIR:-$PWD/.memory-palace}"
    local threads="$palace/OPEN-THREADS.md"
    echo -e "${CYAN}Live sessions:${NC}"; cmd_ls
    if [[ -f "$threads" ]]; then
        echo ""; echo -e "${CYAN}Open threads (from memory palace):${NC}"
        sed 's/^/  /' "$threads"
    else
        echo ""; echo -e "${YELLOW}No memory palace at $palace (run from the project root, or set MEMORY_PALACE_DIR).${NC}"
    fi
}

cmd_restore() {
    # Recreate detached sessions from metadata — used by the boot service so agents
    # come back after a VM reboot. Skips sessions that are already live or were
    # started with --no-restart. The agent command is replayed in its project dir;
    # internal agent state is not (the memory palace + agent session files carry that).
    local restored=0 skipped=0
    for f in "$STATE_DIR"/meta/*.env; do
        [[ -e "$f" ]] || continue
        local name dir cmd restartable sname
        name="$(_meta_get "$f" name)"; dir="$(_meta_get "$f" dir)"
        cmd="$(_meta_get "$f" cmd)"; restartable="$(_meta_get "$f" restartable)"
        sname="$(_sanitize "$name")"
        if "${TMUX[@]}" has-session -t "$sname" 2>/dev/null; then skipped=$((skipped+1)); continue; fi
        if [[ "$restartable" == "false" ]]; then skipped=$((skipped+1)); continue; fi
        [[ -d "$dir" ]] || { echo "skip $name: dir gone ($dir)"; skipped=$((skipped+1)); continue; }
        # cmd was stored space-joined; replay via the login shell so PATH/agents resolve.
        "${TMUX[@]}" new-session -d -s "$sname" -e "AGENTCTL_SESSION=$name" -c "$dir" "${cmd:-bash}"
        echo "restored $name (dir: $dir)"
        restored=$((restored+1))
    done
    echo -e "${GREEN}restore complete:${NC} $restored started, $skipped skipped"
}

case "${1:-help}" in
    start)  shift; cmd_start "$@";;
    attach) shift; cmd_attach "$@";;
    ls|list) shift; cmd_ls "${1:-}";;
    status) shift; cmd_ls "${1:-}";;
    logs)   shift; cmd_logs "$@";;
    stop|kill) shift; cmd_stop "$@";;
    resume) shift; cmd_resume "$@";;
    restore) shift; cmd_restore "$@";;
    *)
        cat <<EOF
agentctl — durable multi-agent sessions (survive WireGuard/SSH/internet drops)

  agentctl start <agent> [-p project] [-d dir] [--no-restart] [-- cmd...]   launch a detached agent
  agentctl attach <name>                                     reattach after reconnect
  agentctl ls | status [--json]                              list sessions + state
  agentctl logs <name> [-n lines]                            tail a session's output
  agentctl stop <name>                                       end a session
  agentctl resume                                            sessions + open threads on reconnect
  agentctl restore                                           recreate sessions from metadata (boot)

Agents run server-side in tmux, so a dropped connection never kills them.
Sessions also survive VM reboots: agentctl-restore.service replays metadata on boot.
Pair with mosh for a client link that also survives roaming and sleep.
EOF
        ;;
esac
