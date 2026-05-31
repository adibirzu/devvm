# Getting Started — Remote Coding with Agents

A step-by-step guide for a **new user** to go from nothing to driving AI coding
agents on the shared cloud VM, with work that survives dropped connections.

> **What you'll have at the end:** your own isolated account on the VM, the VPN up,
> a coding agent (Claude, Codex, Antigravity/AGY, Hermes, or nano-claw) running in a
> durable session you can disconnect from and reattach to, and your PAI/Obi
> knowledge available — same on every machine you use.

---

## The mental model (read this first)

```
   YOUR LAPTOP (thin client)              SHARED VM (does the heavy lifting)
 ┌────────────────────────┐            ┌──────────────────────────────────────┐
 │ cmux / terminal / IDE  │            │  your isolated UNIX account (0700)    │
 │                        │  WireGuard │  agents run here in detached tmux     │
 │  you type, you watch   │═══════════▶│  → survive your laptop sleeping /     │
 │                        │   (VPN)    │    the tunnel dropping / SSH timing out│
 └────────────────────────┘            │  PAI/Obi + guardrails + gateway       │
                                        └──────────────────────────────────────┘
```

**The agent does not run on your laptop.** It runs on the VM in a `tmux` session
managed by `agentctl`. Your laptop is just a window into it. Close the lid, lose
WiFi, switch networks — the agent keeps working. You reconnect and reattach.

Three things make this work:
- **WireGuard VPN** — every VM service is reachable only over the private tunnel.
- **`agentctl`** — runs agents in server-side tmux (decoupled from your connection).
- **PreToolUse guardrail** — every agent is gated; destructive commands are blocked/asked.

---

## Step 0 — Prerequisites (one-time, on your machine)

| You need | macOS | Ubuntu | Windows |
|---|---|---|---|
| WireGuard | `brew install wireguard-tools` | `apt install wireguard` | [WireGuard for Windows](https://www.wireguard.com/install/) |
| SSH | built in | built in | built in (or use WSL2) |
| mosh (resilient SSH) | `brew install mosh` | `apt install mosh` | via WSL2 |
| cmux (optional, best UX) | [cmux.com](https://cmux.com/) | — | — (use SSH/IDE) |

---

## Step 1 — Get your access (ask the admin once)

Send the admin **your SSH public key** (`cat ~/.ssh/id_ed25519.pub` — or generate
one with `ssh-keygen -t ed25519`). The admin adds you as a developer and gives you:

- your **username** on the VM (e.g. `royce`),
- a **WireGuard client config** (`client_<you>.conf`), and
- the VM's VPN IP (the server is always `10.200.200.1`).

> Behind the scenes the admin sets `DEV_N_*` in `.env.local` and re-runs
> `deploy.sh`, which creates your isolated account, code-server, and dashboard card.
> See **[Adding Developers](../README.md#-adding-developers)**.

---

## Step 2 — Bring up the VPN

**macOS / Ubuntu** — use `wg-quick` (NOT the WireGuard GUI app on macOS; it caches a
stale DNS line and breaks your internet — see [DECISIONS](../.memory-palace/DECISIONS.md)):

```bash
./scripts/connect.sh -u <you> wg-up      # or: sudo wg-quick up ./client_<you>.conf
ping 10.200.200.1                        # confirm the tunnel is up
```

**Windows** — import `client_<you>.conf` into the WireGuard app and activate it.

This is a **split tunnel**: only `10.200.200.0/24` goes through the VPN; your normal
internet and DNS are untouched.

---

## Step 3 — Connect to your account

```bash
# normal SSH
ssh -i ~/.ssh/<yourkey> <you>@10.200.200.1

# OR mosh — survives IP changes, sleep, and roaming (best for long agent sessions)
mosh --ssh="ssh -i ~/.ssh/<yourkey>" <you>@10.200.200.1
```

Verify you landed in your own isolated account:

```bash
whoami && git-whoami      # git-whoami shows the GitHub identity your commits will use
```

---

## Step 4 — Get your code onto the VM

Your account commits and pushes as **your own GitHub identity** (enforced per-user,
even in shared repos). Authenticate once:

```bash
gh auth login            # or drop your key at ~/.ssh/id_github
git clone git@github.com:<you>/<your-repo>.git ~/myproject
```

Shared work goes in `~/shared-workspace` (the group `/opt/shared-dev`); your private
work stays in your home. **Personal data never leaks** — homes are `0700`.

---

## Step 5 — Start an agent (the durable way)

`agentctl` runs the agent in a **detached tmux session on the VM**. This is the
whole point — it keeps running if your laptop disconnects.

```bash
# Pick a runtime: claude · codex · agy (Antigravity) · hermes · nanoclaw
agentctl start claude -p myproject -d ~/myproject
agentctl start agy    -p myproject -d ~/myproject      # Antigravity = the Google-family runtime

agentctl ls                          # see every session + state (running / attached / dead)
agentctl attach 'agent:myproject:claude'   # jump into it
#   detach without killing it:  Ctrl-b then d
agentctl logs 'agent:myproject:claude'     # tail what it did while you were away
agentctl stop 'agent:myproject:claude'
```

> The available runtimes come from the pluggable registry — `pai-runtimes list`.
> Any registered runtime (Antigravity/AGY, Hermes, nano-claw, …) launches the same
> way and inherits the guardrail + gateway routing. See
> [AGENT-RUNTIMES.md](AGENT-RUNTIMES.md).

---

## Step 6 — Disconnect and come back (the resilience payoff)

Close your laptop. Lose WiFi. Switch from office to home. The agent **kept working**.

```bash
mosh --ssh="ssh -i ~/.ssh/<yourkey>" <you>@10.200.200.1
agentctl resume                  # shows your live sessions AND your open threads — fastest way back
agentctl attach 'agent:myproject:claude'
```

Even a **VM reboot** is survivable — `agentctl restore` replays your sessions on boot.

---

## Step 7 — Reload context after a gap (Memory Palace)

```bash
palace threads                   # what you were doing (read first on reconnect)
palace recall "auth refactor"    # search project memory + the shared bus
context search "routing" --shared   # cross-developer shared context
```

---

## Step 8 — Use the same Obi everywhere (PAI sync)

So Obi knows the *same* TELOS, projects, and conventions on every machine —
encrypted, never plaintext in the cloud:

```bash
pai-runtimes list                # the agent backends Obi can drive
pai-sync status                  # your encrypted-MEMORY sync state
pai-sync pull                    # decrypt the latest MEMORY into ~/.claude/PAI
```

Full setup (one age key per device) is in
[MULTI-DEVICE-SYNC.md](MULTI-DEVICE-SYNC.md).

---

## Step 9 — Let an agent work unattended (optional)

Schedule an agent to run a task on a cadence, guardrail-gated, under your identity:

```bash
agent-job add nightly-tests --runtime claude -p myproject \
  -d ~/myproject --prompt "run the test suite and fix any trivial failures" --every 1d
agent-job ls
```

Completions ring the **live agent board** (`http://10.200.200.1/agents.html`) — watch
all your agents, projects, and spend from your phone over the VPN.

---

## Watch everything (from anywhere on the VPN)

| What | URL |
|---|---|
| Live agent board (×project ×state ×cost) | `http://10.200.200.1/agents.html` |
| Developer landing dashboard | `http://10.200.200.1` |
| MultiLLM usage / team spend | `http://10.200.200.1:8080/team` |
| Your Web IDE (code-server) | `http://10.200.200.1:<your-port>` |
| Control plane health | `http://10.200.200.1:8082/healthz` |

---

## Safety — what the guardrail does for you

Every agent tool call passes a **PreToolUse** check first:
- **denied:** catastrophic shell (`rm -rf /`, `mkfs`, fork bombs), force-push to `main`.
- **asks you:** cloud/cluster deletes, `terraform destroy`, destructive SQL, system installs.
- **allowed:** everything else.

```bash
guardrail --log          # what was recently blocked/asked
```

You cannot accidentally let an agent nuke something — the gate is on by construction
and an unattended job that hits an "ask" simply waits for you.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Internet/DNS dies after VPN connect (macOS) | You used the WireGuard **app**. Use `connect.sh wg-up` (wg-quick) instead; delete the app tunnel. |
| `http://10.200.200.1` won't load | Are you on the VPN? `ping 10.200.200.1`. Then `systemctl status dev-dashboard`. |
| Agent session "dead" | `agentctl logs <name>` to see why; `agentctl start …` again. |
| `gh` push fails | `unset GITHUB_TOKEN` (a stray invalid one overrides stored creds), then `gh auth login`. |

---

## Cheat sheet

```bash
connect.sh -u <you> wg-up                 # VPN up
mosh --ssh="ssh -i ~/.ssh/<key>" <you>@10.200.200.1   # resilient connect
agentctl start <claude|codex|agy|hermes|nanoclaw> -p <proj> -d <dir>
agentctl ls / attach / logs / resume / stop
pai-runtimes list                         # available agent backends
palace threads ; context search "<q>" --shared
agent-job add <name> --runtime <rt> -p <proj> -d <dir> --prompt "…" --every 1d
```
