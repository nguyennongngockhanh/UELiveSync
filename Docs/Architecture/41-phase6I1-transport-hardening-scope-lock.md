# Phase 6I.1 — Transport Hardening Scope Lock

> **Created**: 2026-05-30 · **Updated**: 2026-05-30
> **Status**: STAGE 1A VERIFIED — Bounds hardening implemented and live-validated
> **Predecessors**: Rename (STABILIZED, Phase 6A/6B · `0x0C`) · Visibility (STABILIZED, Phase 6C · `0x0B`) · Collection (IMPLEMENTED, Phase 6F · `0x0F`) · Hierarchy (IN PROGRESS, Phase 6D · `0x0D`)
> **Next**: Stage 1B Observability — CVars, counters, error enum
>
> This document defines the **hard scope boundaries** for the first Phase 6I
> sub-slice: transport/protocol hardening.
>
> Phase 6I.1 is a **non-architectural stability slice**. It targets concrete
> gaps in TCP lifecycle management, packet bounds enforcement, malformed-packet
> robustness, reconnect safety, and transport-level diagnostics — all
> **before** Phase 7 introduces mesh asset, geometry, and material payloads
> over the same TCP connection.

---

## 1. Purpose

All prior Phase 6 semantic lanes (rename, visibility, collection, hierarchy)
operated over the Phase 5 transport — a direct TCP socket with a
24-byte binary header, 128-entry MPSC queue, and single-threaded receive
loop. The transport has been **production-stable** through 6+ phases of
additive semantic work.

However, the transport was never independently hardened. It carries the
same code from Phase 3. The coming Phase 7 work (static mesh identity,
asset registry, material mapping) will push **larger payloads** (mesh
paths, material paths) and **more complex validation** over the same wire.
Every gap in the transport layer becomes a reliability risk at Phase 7
payload sizes.

### Why Transport Hardening Before Phase 7

| Risk | Phase 6 Payload | Phase 7 Payload | Transport Impact |
|------|----------------|-----------------|------------------|
| Max object count | Few hundred transforms | Few hundred asset defs | Same risk — no per-object cap |
| String length | Rename: names ≤256 chars | Mesh/material paths: up to 512 chars | Runaway allocation if length unchecked |
| Broken connection | Heartbeat timeout (15s) | Asset resolution pipeline state | Lost state on reconnect more costly |
| Malformed flood | 30+ fuzz cases tested | More complex parse paths | New malformed entry points |
| Queue overflow drop | Dropped transforms re-sent next tick | Dropped asset defs may not re-send | **Semantic loss risk** — asset identity is one-shot |

Phase 6I.1 closes these gaps so Phase 7 payloads arrive over a
**validated, instrumented, bounded** transport.

---

## 2. Title

**Phase 6I.1 — Transport Hardening**

Phase 6I.1 tightens the TCP lifecycle, packet validation, reconnect safety,
and diagnostic observability of the UELiveSync transport layer. It makes
**no changes to packet types, semantic handlers, protocol versions, or
the runtime tick pipeline ordering.** All changes are to the transport
layer alone: `LiveSyncRunnable.cpp`, `network.py`, `SyncTypes.h` (transport
constants only), and the validation test suite.

---

## 3. IN SCOPE

Every item in this section must:
- Be a **transport-level change only** — no semantic handler modifications
- Be **backward compatible** with V3/V4/V5 wire protocol
- Have an **associated validation test** in the test suite
- **Not change packet type byte assignments** or handler dispatch

### 3.1 — Receive-Side Timeout & Keepalive

| Item | Rationale |
|------|-----------|
| Add socket receive timeout to network-thread `Recv()` | A peer that signals readability but never sends blocks the network thread indefinitely. Current recovery depends on the game-thread watchdog (35s). A 5-second `Recv()` timeout lets the network thread self-recover. |
| Enable `SO_KEEPALIVE` on UE connection socket | Dead connections (process kill, power loss) are detected only via application heartbeat (15s). TCP keepalive (default 2h Linux, configurable) is a complementary OS-level detection. |
| Enable `SO_KEEPALIVE` on Blender sender socket | Same rationale. Blender-side reconnect timer already handles retry; keepalive makes OS-level death detection faster. |
| Add CVar `UE.LiveSync.RecvTimeoutMs` (default 5000) | Configurable receive timeout in milliseconds. 0 = infinite (current behavior). |

### 3.2 — Packet Bounds Hardening

| Item | Rationale |
|------|-----------|
| Add `LIVE_SYNC_MAX_OBJECTS_PER_PACKET = 4096` | No independent object-count cap exists. A single 512 KB packet can declare 2^31 objects. The freeze guard (5-second stall) is the only safety net. |
| Re-check `LIVE_SYNC_MAX_PACKET_SIZE` in game-thread parser | Currently checked only in network thread before enqueue. A future code path or queue bug could bypass the network-thread check. |
| Add per-name length cap for rename (256 bytes) | Rename strings are bounded only by `PacketEnd` pointer check. A malformed 65535-byte name within the advertised `PacketSize` passes through. |
| Add NaN/Inf rejection at parse time for float fields | NaN/Inf floats are accepted by the parser and only detected later in `InterpolateTransforms`. Reject at parse time for deterministic early failure. |
| Add op-type range validation for collection membership | Only 0x01-0x04 are documented; values above pass through. |

### 3.3 — Reconnect Safety

| Item | Rationale |
|------|-----------|
| Flush/drain Blender send queue on reconnect | Stale packets from a previous connection are sent on the new connection. `LastSequenceId` guard prevents misprocessing but wastes bandwidth and creates confusing logs. |
| Fix `StartNetworkThread` double-accept race | If `StartNetworkThread` is called while a thread is already running, it calls `StopNetworkThread` (which nulls the socket) then bails because `ConnectionSocket` is null. The new socket is lost. |
| Add send queue high-water mark warning on Blender side | Current silent drop at 256-entry limit. A high-water warning at 75% capacity alerts the operator before drops occur. |

### 3.4 — Diagnostics & Observability

| Item | Rationale |
|------|-----------|
| Fill inconsistent `MalformedPackets` counter gaps | Invalid flags, invalid types, and header-too-small cases do not increment `MalformedPackets`. These should increment for uniform monitoring. |
| Add CVar `UE.LiveSync.TransportVerbose` for per-packet transport tracing | Separate verbosity control for transport-layer logging (independent of semantic-lane verbosity). |
| Add structured error code enum `ETransportError` | Replace string-only error classification with an enum for programmatic handling in diagnostics. |
| Add per-connection stats counter for transport-level events | Counters: `RecvTimeouts`, `KeepaliveProbes`, `PacketSizeRejections`, `ObjectCountRejections`, `ReconnectQueueFlushes`. |

---

## 4. OUT OF SCOPE

| Item | Rationale |
|------|-----------|
| **New packet types** | No `PT_*` additions. Phase 6I.1 hardens existing transport only. |
| **Semantic handler changes** | No changes to HandleRename, HandleVisibility, HandleHierarchy, HandleCollection, HandleCreate, HandleDelete, HandleAssetDef, or their Blender-side equivalents. |
| **Protocol version bump (V6)** | No new wire format. All changes are to host-side transport code, not the protocol itself. |
| **Packet compression** | Deferred to Phase 7+ — compression adds per-packet encode/decode overhead that should be evaluated alongside larger payloads. |
| **Delta serialization** | Architectural change — deferred to Phase 7+. |
| **Adaptive update rates** | Phase 6I (core) territory, not this sub-slice. |
| **Interest management / filtering** | Architectural change — deferred. |
| **Bidirectional communication / ACK** | `PF_RequestAck` flag remains reserved. No ACK handshake. |
| **TLS / encryption** | Out of scope for localhost editor sync. No security requirement. |
| **IPv6 support** | The plugin uses `FTcpSocketBuilder` which supports IPv6; no explicit IPv6 work in this slice. |
| **UE → Blender reverse channel** | Unidirectional (Blender→UE) remains. |
| **Concurrent connections / multi-client** | Single-connection model unchanged. |
| **Asset payload changes** | No mesh FBX, no material paths, no geometry data. Phase 7 work is explicitly deferred. |

---

## 5. Implementation Plan

### Stage 0 — Audit & Traceability (documentation/audit/test-planning only, no runtime code)

| Step | Description |
|------|-------------|
| 0.1 | Document all current transport invariants (derived from `SyncTypes.h` and `12-core-runtime-invariants.md`) |
| 0.2 | Audit all parse paths and produce a gap report listing which paths are missing `MalformedPackets` counter increments |
| 0.3 | Document current error classification gaps and propose `ETransportError` schema (no code — schema design doc only) |
| 0.4 | Write validation test plan for Stage 1 and Stage 2 changes |
| 0.5 | Review all existing malformed-packet tests for gap coverage |
| 0.6 | Map each hardening target to implementation order category (Bounds / Observability / Lifecycle) |

**Validation gate**: Stage 0 validation tests pass with existing runtime code (no behavioral change). Stage 0 produces documents and test plans only — zero source files modified.

### Stage 1 — Receive-Side Hardening

| Step | Description |
|------|-------------|
| 1.1 | Add socket receive timeout to network thread `Recv()` — CVar `UE.LiveSync.RecvTimeoutMs`, default 5000ms |
| 1.2 | Add `SO_KEEPALIVE` to UE listener socket, UE connection socket, Blender sender socket |
| 1.3 | Add `LIVE_SYNC_MAX_OBJECTS_PER_PACKET = 4096` — reject packets exceeding this at network thread level |
| 1.4 | Re-check `LIVE_SYNC_MAX_PACKET_SIZE` at game-thread `ProcessBinaryPacket` entry |
| 1.5 | Add per-name length cap (256 bytes) in rename parser path |
| 1.6 | Add NaN/Inf rejection at parse time for transform float fields (Location, Rotation, Scale) |
| 1.7 | Add collection op-type range validation |
| 1.8 | Add `UE.LiveSync.TransportVerbose` CVar for transport-layer logging |
| 1.9 | Add `MalformedPackets` counter increments to all flagged parse paths (from Stage 0.2 gap report) |
| 1.10 | Add `ETransportError` enum definition in `SyncTypes.h` and wire through diagnostics (from Stage 0.3 schema) |
| 1.11 | Validation tests for all Stage 1 changes |

**Validation gate**: All existing fuzz/malformed tests continue to pass. New Stage 1 validation tests pass.

### Stage 2 — Reconnect & Queue Hardening

| Step | Description |
|------|-------------|
| 2.1 | Add send queue drain on Blender reconnect (`_close_internal()` → clear `_send_queue`) |
| 2.2 | Add high-water warning in Blender sender loop (log at 75% queue capacity) |
| 2.3 | Fix `StartNetworkThread` double-accept race (guard with mutex + state check) |
| 2.4 | Add per-connection transport stats counters |
| 2.5 | Validation tests for all Stage 2 changes |
| 2.6 | Full regression: run all Phase 3.6/4/5/6 validation suites |

**Validation gate**: All prior test suites pass. No regressions in semantic-lane behavior.

---

## 6. Recommended Implementation Order

Within each stage, implement items in the following category order.
This ensures the most critical safety work (bounds) is in place before
observability is added, and lifecycle hardening is completed last.

### A. Bounds Hardening (most critical — malformed-packet resilience)

| Order | Step | Stage | Description |
|-------|------|-------|-------------|
| A1 | 1.3 | 1 | Add `LIVE_SYNC_MAX_OBJECTS_PER_PACKET = 4096` cap |
| A2 | 1.4 | 1 | Re-check `LIVE_SYNC_MAX_PACKET_SIZE` in game-thread parser |
| A3 | 1.5 | 1 | Add per-name length cap (256 bytes) in rename parser |
| A4 | 1.6 | 1 | Add NaN/Inf rejection at parse time |
| A5 | 1.7 | 1 | Add collection op-type range validation |
| A6 | 1.9 | 1 | Fill `MalformedPackets` counter increments on all flagged paths |

All bounds items are in Stage 1. If interrupted after A6, the transport is
strictly more resilient than before — every known malformed-input path is
rejected early and counted.

### B. Observability (diagnostics — depends on bounded transport)

| Order | Step | Stage | Description |
|-------|------|-------|-------------|
| B1 | 1.8 | 1 | Add `UE.LiveSync.TransportVerbose` CVar |
| B2 | 1.10 | 1 | Add `ETransportError` enum + wire through diagnostics |
| B3 | 2.4 | 2 | Add per-connection transport stats counters |
| B4 | 2.2 | 2 | Add Blender send queue high-water warning |

Observability can be implemented independently once bounds are in place.
`ETransportError` depends on knowing which error paths exist (produced by
Stage 0.3 schema document).

### C. Lifecycle Hardening (TCP/connection management)

| Order | Step | Stage | Description |
|-------|------|-------|-------------|
| C1 | 1.1 | 1 | Add socket receive timeout on network thread `Recv()` |
| C2 | 1.2 | 1 | Enable `SO_KEEPALIVE` on both sides |
| C3 | 2.1 | 2 | Drain Blender send queue on reconnect |
| C4 | 2.3 | 2 | Fix `StartNetworkThread` double-accept race |

Lifecycle items are the lowest priority within Phase 6I.1 because the
existing heartbeat/watchdog recovery (15-35s) is already functional.
These changes improve recovery speed and reliability but are not required
for safety.

---

## 7. Done Criteria

Phase 6I.1 is **complete** when:

1. All Stage 0/1/2 items are implemented and merged
2. All Stage validation tests pass
3. All prior Phase 3.6/4/5/6 validation suites pass with zero regressions
4. `MalformedPackets` counter increments on every identified malformed-code path
5. No semantic handler, packet type, or protocol version was modified
6. No Phase 7 asset or geometry payload work was started

---

## 8. Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Receive timeout causes spurious disconnects on slow machines | Low | Medium | Configurable CVar with 5000ms default; 0 = infinite fallback |
| Keepalive interferes with localhost loopback | Very Low | Low | `SO_KEEPALIVE` idle time defaults are large (2h); no practical impact on localhost |
| Double-accept fix introduces new race | Low | Medium | Mutex-guarded state machine; validation tests cover concurrent accept |
| Queue drain on reconnect loses valid queued data | Medium | Low | Only drains the send queue, not the packet processing pipeline. UE's `LastSequenceId` guard already handles stale packets. |
| Per-object-count cap breaks existing large scenes | Low | Medium | 4096 objects per packet is generous — current tests max out at ~1000 objects per batch. CVar-gated if needed. |
| Transport hardening delays Phase 7 start | Low | Low | Stage 0 is audit-only (no runtime code). Stage 1 is 3-5 days. Stage 2 is 2-3 days. Total ~1 week. |

---

## 9. Validation Record — Stage 1A

| Field | Value |
|-------|-------|
| **Date** | 2026-05-30 |
| **Command** | `python3 tests/phase6I1_bounds_validation.py` |
| **Result** | **24/24 PASS** |
| **UE binary** | `/home/nguyennongngockhanh/Unreal/UE5.7.4/Engine/Binaries/Linux/UnrealEditor` |
| **Project** | `ProjectTemplate.uproject` (SM6, X11) |
| **Plugin path** | `ProjectTemplate/Plugins/UELiveSync/` (project-local, rebuilt) |
| **UE log confirmation** | All 10 bounds rejection messages confirmed (6 Error-level + 4 Warning-level) |
| **Existing fuzz regression** | `phase5c_fuzz_protocol.py`: 37/39 PASS — 2 pre-existing TCP `sendall` expectation failures, **not regressions** |
| **Editor health** | No crash, no assert, no check failure. Editor alive for full test duration. |
| **Stage 1A status** | **VERIFIED** |

---

## 10. Files Touched

| File | Stage | What |
|------|-------|------|
| `UE_Plugin/.../LiveSyncRunnable.cpp` | 1 | Recv timeout, keepalive, object-count cap |
| `UE_Plugin/.../LiveSyncRunnable.h` | 1 | New CVar binding, timeout member |
| `UE_Plugin/.../UELiveSyncSubsystem.cpp` | 1, 2 | Max-size re-check, transport stats, StartNetworkThread fix |
| `UE_Plugin/.../UELiveSyncSubsystem.h` | 1, 2 | Transport stats counters, StartNetworkThread guard |
| `UE_Plugin/.../SyncTypes.h` | 1, 2 | `ETransportError` enum, `LIVE_SYNC_MAX_OBJECTS_PER_PACKET`, transport stats struct |
| `Blender_Addon/network.py` | 1, 2 | Keepalive, queue drain on reconnect, high-water warning |
| `Blender_Addon/sync.py` | 2 | (none — transport-only changes in network.py) |
| `tests/` | 0, 1, 2 | Stage validation test files |
| `Docs/Architecture/41-phase6I1-transport-hardening-scope-lock.md` | 0 | This document |
| `Docs/Architecture/42-phase6I1-transport-hardening-design.md` | 0 | Vertical slice design (next) |
