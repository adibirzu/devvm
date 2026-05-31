#!/bin/bash
# git-whoami — show the git/GitHub identity that THIS account will use, and verify
# isolation holds (important when committing in shared /opt/shared-dev repos).
set -euo pipefail
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'

echo -e "${CYAN}Account:${NC} $(whoami)"

# Effective author identity (env vars win over repo-level config — this is what a
# commit will actually record). Mirrors git's precedence.
author_name="${GIT_AUTHOR_NAME:-$(git config user.name 2>/dev/null || echo '?')}"
author_email="${GIT_AUTHOR_EMAIL:-$(git config user.email 2>/dev/null || echo '?')}"
echo -e "${CYAN}Commits as:${NC} ${author_name} <${author_email}>"
if [[ -n "${GIT_AUTHOR_EMAIL:-}" ]]; then
    echo -e "  ${GREEN}✓ enforced via GIT_AUTHOR_EMAIL${NC} — a shared repo's user.email cannot override it."
else
    echo -e "  ${YELLOW}! using config (no GIT_AUTHOR_EMAIL). A repo-level user.email could override this.${NC}"
fi

# Warn loudly about the one thing that breaks per-account push isolation.
if [[ -n "${GITHUB_TOKEN:-}${GH_TOKEN:-}" ]]; then
    echo -e "${RED}✗ GITHUB_TOKEN/GH_TOKEN is set in this shell.${NC} If it's a shared token, pushes go to the"
    echo -e "  wrong account. Unset it and use 'gh auth login' (per-user) or your own SSH key."
else
    echo -e "${GREEN}✓ no shared GITHUB_TOKEN${NC} — push auth comes from your own creds."
fi

# Per-user GitHub auth status (creds live in ~/.config/gh, isolated per account).
if command -v gh >/dev/null 2>&1; then
    acct="$(gh auth status 2>&1 | grep -oE 'account [A-Za-z0-9-]+' | head -1 || true)"
    echo -e "${CYAN}gh:${NC} ${acct:-not logged in — run 'gh auth login'}"
fi

# If in a repo, show the remote + which auth it will use.
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    url="$(git remote get-url origin 2>/dev/null || echo 'no origin')"
    echo -e "${CYAN}repo:${NC} $(git rev-parse --show-toplevel) → ${url}"
    case "$url" in
        git@github.com:*|ssh://git@github.com/*) echo -e "  push auth: ${GREEN}SSH key (~/.ssh/id_github)${NC}";;
        https://github.com/*) echo -e "  push auth: ${YELLOW}HTTPS (gh/credential helper — keep it per-user)${NC}";;
    esac
fi
