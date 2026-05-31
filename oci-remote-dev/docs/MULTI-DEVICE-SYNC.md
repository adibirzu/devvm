# Multi-Device PAI Sync (age-encrypted)

Keep the **same Obi knowledge and workflows** across all your machines — 3–4 Macs,
the Ubuntu box, a Windows laptop — without your personal life data ever sitting in
plaintext in the cloud or on a shared VM disk.

## The sensitivity split

| What | Where it lives | Sync mechanism |
|---|---|---|
| Code | normal GitHub repos | git (as today) |
| Skills / Algorithm / hooks | the **PAI repo** | git (shareable, plaintext) |
| **Personal MEMORY + USER** (TELOS, health, finances, identity) | a **separate PRIVATE repo** | `pai-sync` — **`age`-encrypted at rest** |

GitHub stays the source of truth for everything. The personal repo only ever
contains ciphertext, so even if it (or a shared VM snapshot) is read by someone
else, there is nothing usable.

## How it works

```
~/.claude/PAI/MEMORY  ──tar──▶ age -r <device-keys> ──▶ ~/.pai-memory/encrypted/MEMORY.tar.age ──git push──▶ private repo
~/.claude/PAI/USER    ──tar──▶ age -r <device-keys> ──▶ ~/.pai-memory/encrypted/USER.tar.age   ──┘
                                                                                 │
   other device: git pull ──▶ age -d -i <this device's key> ──▶ untar ──▶ ~/.claude/PAI/{MEMORY,USER}
```

- **Key per device.** Each machine has its own `age` identity (`age-keygen`). You
  add every device's **public** key to `PAI_AGE_RECIPIENTS`, so any device can
  encrypt and each device decrypts with its own private key.
- **Offline-first.** With no remote configured, `pai-sync push` still commits the
  encrypted blobs locally and `status` reports `no remote (local-only mode)`. Add a
  remote later (`git remote add origin …`) and the same commands sync to the cloud —
  config only, no code change.
- **Hard refusal, never plaintext.** `pai-sync push` aborts if `PAI_AGE_RECIPIENTS`
  is empty — it never silently falls back to plaintext.

## First-time setup (any device)

```bash
# 1. One identity per device
age-keygen -o ~/.config/pai/age.key
age-keygen -y ~/.config/pai/age.key       # prints the PUBLIC key — collect one per device

# 2. Point the tool at the repos + recipients
export PAI_DIR="$HOME/.claude/PAI"
export PAI_AGE_RECIPIENTS="age1deviceA...,age1deviceB...,age1deviceC..."
export PAI_AGE_IDENTITY="$HOME/.config/pai/age.key"

# 3. Initialize + push from your primary device
pai-sync init
pai-sync push -m "seed MEMORY"
# later, point at a private GitHub remote:
git -C ~/.pai-memory remote add origin git@github.com:<you>/pai-memory.git
pai-sync push

# 4. On every other device
pai-sync init
git -C ~/.pai-memory remote add origin git@github.com:<you>/pai-memory.git
pai-sync pull          # decrypts MEMORY+USER into ~/.claude/PAI
```

## Per-OS client notes

| OS | PAI runtime | VPN to the VM | Notes |
|---|---|---|---|
| **macOS** | native | `connect.sh wg-up` (wg-quick) | Drive remote agents with **cmux**; never the WireGuard app (stale-DNS bug — see DECISIONS) |
| **Ubuntu** | native | `wg-quick up` | Same `pai-sync`; can also host its own agents |
| **Windows** | WSL2 (recommended) | WireGuard Windows client + WSL | Run `pai-sync`/PAI inside WSL2; or use the VM's web IDE (`code-server`) + RDP over the VPN. cmux is macOS-only, so on Windows drive agents via SSH/mosh or the web IDE |

`age`, `git`, and `python3` are the only dependencies on each device.

## Future: cloud key custody

The `age` identity can move from a file to OCI Vault or a passphrase-protected key
later — set `PAI_AGE_IDENTITY` accordingly. The encrypted-at-rest contract does not
change.
