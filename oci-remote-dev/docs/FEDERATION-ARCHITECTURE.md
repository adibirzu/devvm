# Federated PAI — Multi-Location Architecture (House · Office · Apartment · Cloud DevVM)

> **Status: DESIGN.** This doc proposes the architecture; nothing here is deployed.
> It exists so we agree the shape before building. Decision points are marked **[DECIDE]**.

## The goal

One **central PAI (Obi)** that the principal talks to from anywhere (Telegram/WhatsApp/
iMessage), coordinating **four nodes** — three physical homes (House, Office, Apartment)
and the Cloud DevVM — where each node runs **its own local agents** (and optionally a
**local PAI server** for offline autonomy), with **near-realtime sync** of knowledge and
state between all of them.

```
                         ┌──────────────────────────────┐
                         │  PRINCIPAL (phone / laptop)  │
                         │  Telegram · WhatsApp · iMsg   │
                         └───────────────┬──────────────┘
                                         │  one conversation, any location
                         ┌───────────────▼──────────────┐
                         │     CENTRAL PAI / OBI         │  ← routing + identity + policy
                         │  (the brain you talk to)      │     decides WHICH node executes
                         └───┬────────┬────────┬─────────┘
              ┌──────────────┘        │        └──────────────┐
        ┌─────▼─────┐          ┌──────▼─────┐          ┌───────▼──────┐     ┌──────────────┐
        │  HOUSE    │          │  OFFICE    │          │  APARTMENT   │     │  CLOUD DEVVM │
        │ local PAI │          │ local PAI  │          │  local PAI   │     │ (coding)     │
        │ + agents  │          │ + agents   │          │  + agents    │     │ agentctl     │
        │ HomeKit/HA│          │ HomeKit/HA │          │  HomeKit/HA  │     │ hermes/agy   │
        └─────┬─────┘          └──────┬─────┘          └───────┬──────┘     └───────┬──────┘
              └────────────── near-realtime sync bus ──────────┴─────────────────────┘
                         (encrypted; knowledge + state, NOT secrets)
```

## Principles (carried from the secure-agent work)

1. **Each location is its own security tenant.** A compromised agent at the Office must
   not reach the House's locks or the DevVM's code. This is the lethal-trifecta rule
   applied geographically: physical-world reach is *per-location*, never global.
2. **Local-first autonomy.** Each node keeps working if the central PAI or the network is
   down — a House agent can still answer "is the door locked?" offline. The central PAI
   *coordinates*; it is not a single point of failure for local actions.
3. **Sync knowledge, never secrets.** The sync bus carries MEMORY/context/state
   (age-encrypted), never API keys / HA tokens / WG keys — those stay per-node.
4. **Physical actions stay tiered + local.** Locks/alarm/garage remain T3 (typed-yes,
   per-action) and are executed by the *local* node that owns that device, never by a
   remote agent reaching across the bus.

## The four decisions to make first

**[DECIDE-1] Topology: hub-and-spoke vs mesh.**
- *Hub* (recommended): central PAI is the router; nodes sync to/from it. Simpler, one
  policy point, matches "central PAI" wording. Central outage → nodes still local-autonomous,
  but cross-node coordination pauses.
- *Mesh*: every node syncs with every other peer-to-peer. More resilient, much more
  complex (N² trust, conflict resolution). Probably overkill for 4 nodes.

**[DECIDE-2] Local node = full local PAI server, or thin agent + central brain?**
- *Full local PAI per home* (your "local PAI servers"): each home runs its own Obi
  instance with local MEMORY + local agents; offline-capable; heavier to maintain (4 PAI
  installs to keep current). Best for privacy + resilience.
- *Thin agent per home* (Cloud DevVM model): home runs only `agentctl`-style executors;
  the brain is central. Lighter; depends on connectivity for anything smart.
- *Hybrid* (likely answer): full local PAI at House (primary residence), thin agents at
  Office/Apartment, DevVM stays coding-only.

**[DECIDE-3] Sync mechanism for "near-realtime."**
- *Option A — Encrypted git + push notifications*: extend the existing `pai-sync` (age) to
  a hub remote; each node pulls on a webhook/notification. Near-realtime (seconds), reuses
  what's built, full history/rollback. **Recommended** — it's an evolution of Phase 6.
- *Option B — Syncthing mesh*: continuous file sync, no central. Real-time, but no history,
  daemon everywhere, weaker audit.
- *Option C — Message bus (NATS/MQTT) for state + git for knowledge*: events (door locked,
  agent finished) over a lightweight encrypted broker on the DevVM; durable knowledge over
  git. Most "realtime," most moving parts.

**[DECIDE-4] Where does the central PAI live?**
- On the **Cloud DevVM** (always-on, already VPN-hubbed, already has the gateway + WG mesh
  reaching everywhere) — **recommended**. The homes join the existing WireGuard `10.200.200.0/24`
  as peers; the DevVM becomes the federation hub too.
- On a **home always-on box** (e.g. the House) — keeps everything on-prem, but needs a
  stable public endpoint + uptime.

## Proposed architecture (my recommendation, pending [DECIDE])

- **Hub = Cloud DevVM.** It already runs WireGuard, the MultiLLM gateway, per-tenant
  isolation, guardrails, agent jobs. Add each home as a WG peer (`10.200.200.10/11/12`).
  The homes reach the hub over the same split-tunnel VPN; nothing new is public.
- **Per-location node identity.** Each location is a "location tenant" with its own
  `location.yaml` (name, WG IP, owned device classes, local-agent list, autonomy level).
  Mirrors the existing per-developer model, extended to physical sites.
- **Sync = `pai-sync` extended (Option A).** A new `pai-federation` mode: the hub holds the
  canonical age-encrypted MEMORY; each node `pull`s on a notification (Pulse already has a
  notification ring) and `push`es local deltas. Conflict policy: last-writer-wins per
  namespace, with per-location namespaces so homes don't collide.
- **Central routing.** The principal's message → central PAI classifies *which location*
  it concerns ("lock the house" → House node; "what's my office calendar" → Office) →
  dispatches to that node's local agent over the bus → result streams back to the channel.
  This extends the existing `RemoteCodeSessionRegistry` `channelTarget` concept with a
  `locationTarget`.
- **Physical safety unchanged.** Each home's HA/HomeKit token stays on that home's node;
  the `policy.mac-home.json` tier model (T1 auto / T2 one-tap / T3 typed-yes) runs at the
  node that owns the device. The central PAI can *request* a lock action; only the local
  node, with the principal's typed-yes, *executes* it.

## What this reuses (not a rebuild)

| Need | Already exists | Extension |
|---|---|---|
| Secure transport between sites | WireGuard mesh | add home peers |
| Per-tenant isolation | UNIX/tenant model + `X-MultiLLM-Tenant` | → per-*location* tenant |
| Encrypted knowledge sync | `pai-sync` (age) | → hub remote + notify-pull |
| Local agents per node | `agentctl` + runtime registry | run on each home node |
| Channel control (TG/WA) | RemoteCodeSessionRegistry + RemoteTaskRouter | add `locationTarget` |
| Physical-action tiers | `policy.mac-home.json` | run per-location |
| Notifications | Pulse notification ring | sync-trigger + cross-site alerts |

## Phased rollout (proposed)

- **F0 — Bridge (in progress):** Telegram/WhatsApp → Cloud DevVM Hermes/agy session
  (deliverable D2). This is the *first* remote node; proves the channel→node control path.
- **F1 — Location registry + WG peers:** `location.yaml` schema + add House as a WG peer +
  a thin agent. One home, hub-and-spoke.
- **F2 — Federated sync:** `pai-sync` hub mode + notify-pull. House MEMORY syncs to hub.
- **F3 — Office + Apartment:** replicate F1/F2 for the other two sites.
- **F4 — Central routing + local PAI servers:** central `locationTarget` classification;
  full local PAI where chosen ([DECIDE-2]).

## Out of scope (for now)

- Cross-location *autonomous* physical actions (an Office agent triggering House devices) —
  deliberately excluded; physical reach stays local + typed-yes.
- A bespoke realtime database — reuse git + notifications before adding a broker.
- Replacing any home's existing HomeKit/HA setup — PAI federates on top, doesn't replace.
