# Phase 9 Stage 3A — Auto-discovery / Connection UX (Scope Lock)

## Purpose

Currently, a user must manually type the UE editor's IP address and port
into Blender. If the listener is on localhost (the common case), no
configuration should be necessary. If the listener is on another machine,
the user should be able to discover it without guessing IP addresses.

This document defines the discovery strategy and connection UX without
implementing runtime code.

---

## Current behaviour

- Blender defaults to `127.0.0.1:57000`
- If the IP/port is wrong, the connection fails with a socket error
- No scan, no beacon, no auto-detect
- Single-instance only — one Blender → one UE
- No UE-side advertising; UE just listens passively on port 57000

---

## Discovery strategies compared

| Strategy | Complexity | Works offline | User action | Reliability |
|----------|------------|--------------|-------------|-------------|
| **Manual IP/port** (current) | None | Yes | Must type | 100% |
| **Localhost default** | Trivial | Yes | None | 100% (local) |
| **TCP scan (Blender probes ports)** | Low | Yes (LAN) | Click "Scan" | High |
| **UDP beacon (UE broadcasts)** | Medium | Yes (LAN) | None (auto) | High |
| **mDNS / Zeroconf** | High | Yes | None | High but complex |
| **Project config file** | Low | Yes | One-time setup | 100% |

---

## Recommended approach (three tiers, additive)

### Tier 1 — Localhost default (trivial, already partially implemented)

Blender defaults to `127.0.0.1:57000`. No configuration needed when Blender
and UE run on the same machine. If connect fails, fall back to the configured
manual address or scan.

### Tier 2 — TCP scan (Blender "Scan for UE" button)

A button in the Blender sidebar panel. When clicked:

1. For each address in a probe list, attempt TCP connect to port 57000
2. The probe list is:
   - The localhost address (already known)
   - The subnet broadcast address (e.g., `192.168.1.255`)
   - Common LAN addresses derived from the machine's own IP
   - Any user-configured "known hosts" list
3. On each successful connect, send a lightweight probe packet
   (reuse existing `PT_CapabilityAnnounce` and check for a response)
4. Present all responders in a list
5. User selects one to connect to
6. Timeout per probe: 2 seconds
7. Max concurrent probes: 8 (to avoid overwhelming the network)

### Tier 3 — UDP beacon (UE advertises, optional)

UE sends a small UDP broadcast or multicast packet on a dedicated port
(e.g., 57001) advertising its presence:

```
Magic: 0x4C56534D ("LVSM")
Type: 0x01 (beacon)
Payload:
  - UE protocol version (uint16)
  - Listener port (uint16)
  - Session uptime (uint32 seconds)
  - Hostname (variable-length string, max 64 bytes)
  - Capability flags (uint32, same bitmask as capability negotiation)
```

Timer: every 5 seconds
TTL: 1 (link-local only, no router forwarding)
Blender listens on the beacon port. When a beacon arrives, UE is added to
the discovered list.

---

## Multi-UE instance behaviour

When multiple UE instances are running on the same network:

| Scenario | Behaviour |
|----------|-----------|
| Single UE, single Blender | Auto-connect to the only responder |
| Multiple UE, one Blender | Show a picker with hostname + uptime + caps |
| One UE, multiple Blender | Each Blender connects independently (current behaviour — no change) |
| Multiple UE, multiple Blender | Each Blender shows a picker; each UE accepts multiple connections |

Blender's UI uses a dropdown or list to select the target UE. The list
shows:
- Hostname
- IP:port
- Uptime
- Capability flags (compression, ACK) as icons

---

## Security constraints

| Concern | Mitigation |
|---------|------------|
| Unauthorised UE advertises itself | Capability negotiation still authenticates the session. Blender can refuse unknown instances. |
| UDP beacon spoofing | Beacon is discovery-only. Actual data still flows over TCP. A spoofed beacon leads to a failed TCP connect, not data corruption. |
| LAN-only scope | UDP TTL=1 prevents beacon from leaving the local subnet. TCP scan also limited to LAN. |
| Firewall blocking beacon port | Fall back to manual connect or TCP scan. Auto-discovery is optional. |

---

## UX states in Blender panel

| State | Blender panel shows | User action |
|-------|---------------------|-------------|
| **Idle** (never configured) | "Connect to 127.0.0.1:57000" button + "Scan for UE" button | Click connect or scan |
| **Connecting** | "Connecting..." spinner | Wait |
| **Connected** | Green dot + UE hostname + uptime | Working |
| **Disconnected** | Red dot + last known UE + "Reconnect" + "Scan" | Click reconnect or scan |
| **Scanning** | "Scanning..." spinner + progress (3/8 probed) | Wait |
| **Multiple found** | Dropdown list of discovered UEs | Select one |
| **None found** | "No UE instances found" + "Try again" | Check UE is running and click "Try again" |

---

## Relationship with capability negotiation

Capability negotiation (Stage 2B–2F) already defines:

- `PT_CapabilityAnnounce` (0x11) — Blender → UE
- `PT_CapabilityResponse` (0x12) — UE → Blender
- `CapabilityFlags` bitmask

Auto-discovery must NOT replace or bypass capability negotiation:

1. Blender discovers UE via TCP scan or UDP beacon
2. Blender connects to the selected UE via TCP
3. **Capability negotiation runs as today** (announce → response)
4. Effective feature gating still depends on capability negotiation, not
   on the discovery method

This means:
- The beacon can include capability flags for informational display, but
  the actual feature negotiation still happens over TCP
- A discovered UE that doesn't respond to `PT_CapabilityAnnounce` (old UE)
  is treated as baseline (no optional features)

---

## Acceptance criteria

| # | Criterion | Verification |
|---|-----------|-------------|
| A1 | Blender connects to `127.0.0.1:57000` without any configuration | Start Blender → start UE → connect works |
| A2 | Blender "Scan for UE" button discovers a UE on the same machine in <5s | Click scan → UE appears in list |
| A3 | Blender "Scan for UE" discovers a UE on a different machine in the same LAN | Two machines, scan finds remote UE |
| A4 | Multiple UE instances all appear in the discovered list | Start two UE editors → scan shows both |
| A5 | User can select which UE to connect to from the list | Click UE in list → connects |
| A6 | Old UE (no capability response) still appears and can connect | No capability flags shown, connects in baseline mode |
| A7 | Manual IP/port override still works | Type IP → connect ignores discovery |
| A8 | UDP beacon (optional) can be enabled/disabled via CVar | `UE.LiveSync.EnableBeacon` — on/off |
| A9 | No data protocol changes | No packet types changed, no version bump |

---

## What is NOT in scope

| Item | Reason |
|------|--------|
| Persistent UE host list | Would require a config file or database — Stage 6 (presets) concern |
| Automatic reconnection to last-known UE | Stage 5 (crash recovery) concern |
| UE discovering Blender | Blender is the TCP client; UE is the server. No reverse discovery needed. |
| Cross-subnet discovery | UDP broadcast and TCP scan are LAN-only. WAN connections require manual IP. |
| Authentication / authorisation | Single user, trusted LAN environment. Not needed. |

---

## Implementation recommendation

**Build Tiers 1 and 2 first.** Tier 1 (localhost default) requires zero
code — the current hardcoded port already works. Tier 2 (TCP scan) is a
Blender-side UI change with no protocol modifications. Tier 3 (UDP beacon)
requires UE-side code changes for the beacon sender and is entirely optional.

Stage 3B is COMPLETE (2026-06-01):
1. `discover_ues()` in network.py — TCP scan of localhost + LAN subnet, sends `PT_CapabilityAnnounce`, records responders with capability info
2. "Scan for UE" button in Blender sidebar panel
3. Discovered UE list appears in panel with host:port, protocol version, capability flags
4. Click a discovered UE to connect
5. Manual connect path unchanged
6. Capability negotiation runs after connect (no stale caps used)
7. `tests/phase9_stage3b_discovery_scan.py` — 20/20 PASS
8. Fixed missing `LIVE_SYNC_PROTOCOL_VERSION = 5` constant

Stage 3D Complete (2026-06-01):
- Result timestamps + "stale" flag (30s TTL)
- `clear_discovery_results()` function
- Stale/discovered/none UX states in sidebar panel
- Clear and Refresh buttons next to discovered list
- `tests/phase9_stage3b_discovery_scan.py` → 26/26 PASS

Stage 3E Complete (2026-06-01):
- Editor confirmed alive 5845 ticks with full pipeline
- Port 57000 confirmed listening
- All 238/238 tests PASS
- Stage 3 closed

UDP beacon (Stage 3C) deferred as optional. Localhost + LAN TCP scan are
sufficient for current use cases. UDP beacon can be implemented if
cross-subnet discovery is needed in the future.

No protocol version bump, no packet type changes, no existing behaviour
changes.
