# 🏛️ Memory Palace — OCI Agentic Dev OS

A structured, durable memory for this coding project. Both humans and AI agents
read it to regain **full context** after a disconnect, a new session, or a fresh
agent — which is exactly what makes work resumable when WireGuard, SSH, or the
internet drops.

## Rooms

| Room | Holds |
|------|-------|
| [`00-INDEX.md`](00-INDEX.md) | Map of the palace + how to use it |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | What the system is and how the pieces fit |
| [`DECISIONS.md`](DECISIONS.md) | Decisions and *why* (the non-obvious rationale) |
| [`SESSION-LOG.md`](SESSION-LOG.md) | Chronological log of what was done |
| [`OPEN-THREADS.md`](OPEN-THREADS.md) | In-flight work + next steps (read this first on reconnect) |
| [`GLOSSARY.md`](GLOSSARY.md) | Project-specific terms |

## Using it

```bash
palace rooms                         # list rooms
palace show decisions                # read a room
palace note open-threads "did X, next Y"   # append a timestamped note
palace note --share decisions "chose Z"    # also mirror to the shared context bus
palace recall "wireguard"            # search rooms + the bus
palace threads                       # what you were doing (for reconnects)
agentctl resume                      # live agent sessions + open threads in one shot
```

## Conventions

- Keep entries **append-only and timestamped** (the `palace note` command does this).
- Capture the **why**, not just the what — the code already records the what.
- When you finish a thread, move it from `OPEN-THREADS.md` to `SESSION-LOG.md`.
- This palace lives in the repo, so it is versioned with the code it describes.
