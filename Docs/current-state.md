# UELiveSync — Current State

**Generated**: 2026-05-25  
**Branch**: `main`  
**Phase**: Late Phase 5E — Stress Testing & Observability (COMPLETE)

---

## Phase 5 Pre-Phase-6 Preparation (2026-05-25)

Before Phase 6 begins, the Phase 5 runtime foundation has been formally frozen:

- **Release tag**: `v0.5.0-stabilized` created locally
- **Core runtime frozen**: Freeze banners added to `UELiveSyncSubsystem.cpp`, `PendingAssetQueue.h`, `LiveSyncQueue.h`, `SyncTypes.h`, `LiveSyncRunnable.h`
- **Architecture docs created**:
  - `12-core-runtime-invariants.md` — packet lifecycle, thread/queue ownership, Tick ordering, parser invariants
  - `13-phase6-design-constraints.md` — unresolved authority questions for rename, visibility, collections, duplicate detection
  - `14-editor-sync-safety.md` — replication suppression rules, feedback loop prevention, rename storm prevention
  - `15-architecture-decision-records.md` — 15 ADRs covering protocol, threading, queue, pipeline, shutdown
  - `16-known-safe-modification-zones.md` — SAFE/CAUTION/HIGH-RISK/FROZEN modification zones
  - `17-phase6-readiness.md` — 14/14 readiness conditions complete
- **Profiling/debug infrastructure**: TRACE_CPUPROFILER_EVENT_SCOPE and BEGIN/END tracing explicitly documented as INTENTIONALLY RETAINED

## Completed Phases

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Foundation: Blender addon + UE plugin scaffold, basic TCP, V2 protocol | Done |
| 2 | Core sync: transform streaming, coordinate conversion, MESH-only filter | Done |
| 3 | Production hardening: thread safety, heartbeat, V3 protocol, reconnection, Actor cache | Done |
| 3.4–3.6 | Robustness: V4, CREATE/DELETE lifecycle, snapshot batching, watchdog | Done |
| 4 | Stability core: CVars, diagnostics bar, console commands, protocol validation | Done |
| 5A | Workflow: primitive UI, full-snapshot flag, DumpState/Ping/Stats/Reset | Done |
| 5B | Hierarchy authority model: local-space interpolation, attachment lifecycle | Done |
| 5C | Diagnostics & Editor UX: runtime metrics, debug overlay, Blender status UI | Done |
| 5D | Runtime stability: Asset Identity/V5 protocol, PendingAssetQueue, freeze investigation | Done |
| 5E | Stress testing: long-duration, large-scene, reconnect storm, malformed packet, observability | Done |

## Active Work

### Phase 6 — NOT STARTED

Phase 6 (Live Editing System) has not begun. The Asset Identity
and V5 protocol work listed below belongs to Phase 5D (Runtime
Stability & Freeze Fixes) — it is protocol/transport work, not
editor-side live editing.

Per canonical roadmap:
- **Phase 5**: Protocol Evolution & Runtime Stabilization ← COMPLETE
- **Phase 6**: Live Editing System ← NOT STARTED
  - Rename replication
  - Collection/folder structure sync
  - Visibility/hidden state sync
  - Duplicate detection
  - Object create/delete lifecycle management from editor

---

## Architecture Overview

```
Blender Main Thread                    UE Network Thread           UE Game Thread
┌─────────────────────┐               ┌──────────────────┐       ┌──────────────────────┐
│ Scene scan & diff   │               │ Recv()           │       │ ProcessQueuedPackets │
│ => TransformState[] │───TCP────────>│ => FLiveSyncPkt  │───Q──>│ => InterpolateTransf │
│ => AssetIdentity[]  │               │ Enqueue (MPSC)   │       │ => ResolveAssetDefs  │
└─────────────────────┘               └──────────────────┘       └──────────────────────┘
        │                                                                  │
Blender Daemon Thread                                                      │
┌─────────────────────┐                                                    │
│ socket.sendall()    │                                                    │
│ (non-blocking enq)  │                                                    │
└─────────────────────┘                                                    ▼
                                                                     AssetResolution:
                                                                     8/tick, exp backoff,
                                                                     live-swap mesh
```

---

## Protocol Versions

| Version | Status | Key Features |
|---------|--------|-------------|
| V2 | Legacy | 22-byte header, hex GUID, port 5000 |
| V3 | Stable | 24-byte header, binary GUID, packet types |
| V4 | Stable | Snapshot batching, local-transform flag |
| V5 | Active | PT_AssetDef (0x08), xxHash64 identity, 33B fixed payload |
| V4+ | Stable | V4+ objects always 81 bytes (primitive type byte at offset 80 for ALL V4+ payloads) |

---

## Key Files

| File | Role |
|------|------|
| `Blender_Addon/__init__.py` | Registration, UI panel, operators |
| `Blender_Addon/sync.py` | Core sync loop, scene iteration, diff detection |
| `Blender_Addon/network.py` | TCP client, binary serialization, threaded sender, xxHash64 |
| `UE_Plugin/.../UELiveSyncSubsystem.cpp/h` | Main game-thread orchestrator |
| `UE_Plugin/.../LiveSyncRunnable.cpp/h` | Dedicated network receive thread |
| `UE_Plugin/.../LiveSyncQueue.h` | Bounded MPSC packet buffer (128 entries) |
| `UE_Plugin/.../SyncTypes.h` | Structs, protocol constants, log category, FLiveSyncStats |
| `UE_Plugin/.../AssetIdentityTypes.h` | FAssetIdentityRef, FAssetMetadata (Phase 5D) |
| `UE_Plugin/.../PendingAssetQueue.h` | Bounded (2048) pending resolution queue (Phase 5D) |
| `UE_Plugin/.../SLiveSyncStatusWidget.cpp/h` | Compact status indicator panel |
| `UE_Plugin/.../SLiveSyncDiagnosticsWidget.cpp/h` | Full diagnostics panel |
| `Docs/Architecture/12-core-runtime-invariants.md` | Core runtime invariants (Phase 5 freeze) |
| `Docs/Architecture/13-phase6-design-constraints.md` | Phase 6 authority model constraints |
| `Docs/Architecture/14-editor-sync-safety.md` | Editor synchronization safety rules |
| `Docs/Architecture/15-architecture-decision-records.md` | 15 ADRs for major Phase 5 choices |
| `Docs/Architecture/16-known-safe-modification-zones.md` | SAFE/HIGH-RISK/FROZEN modification zones |
| `Docs/Architecture/17-phase6-readiness.md` | Phase 6 readiness checklist |

---

## Upcoming

| Phase | Description | Est. |
|-------|-------------|------|
| 6 | Live editing: create/delete/rename/visibility/collections | TBD |
| 7 | Animation & Sequencer sync | TBD |
| 8 | High-performance streaming | TBD |
| 9 | Production ecosystem | TBD |
