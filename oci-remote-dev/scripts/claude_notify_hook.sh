#!/bin/bash
# Claude Code Notification hook → agent-notify.
# Claude Code runs this on its Notification event (e.g. "waiting for your input",
# "needs permission"), passing a JSON object on stdin with a `message` field. We
# forward that message to agent-notify, which tags it with $AGENTCTL_SESSION (set
# by agentctl in the tmux env) so the board rings the right session.
msg="$(python3 -c 'import sys, json
try:
    print(json.load(sys.stdin).get("message", "needs your input"))
except Exception:
    print("needs your input")' 2>/dev/null || echo "needs your input")"
exec /usr/local/bin/agent-notify "$msg"
