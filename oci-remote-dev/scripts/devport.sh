#!/usr/bin/env bash
# Source this file, then run `devport-use [name]` in a worktree.

devport-use() {
    local devport_exports
    devport_exports="$(command devport env "${1:-}")" || return
    eval "$devport_exports"
    printf '%s\n' "$DEV_URL"
}

devport-release() {
    command devport release "${1:-}"
}
