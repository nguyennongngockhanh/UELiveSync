# UELiveSync — Current State

**Generated**: 2026-05-24  
**Branch**: `main`  
**Phase**: 6A — Asset Identity & Static Mesh Resolution (in progress)

---

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

## Active Work

### Phase 6A — Asset Identity & Static Mesh Resolution

**Goal**: Deferred static mesh assignment via xxHash64 identity, independent from transform pipeline.

**Protocol**: V5 (`PT_AssetDef = 0x08`, 33 bytes per object, fixed-size)

**Blender side**:
- `xxh64()` pure-python hash of `obj.data.name`
- `get_mesh_identity_hash()`, `serialize_asset_identity()`
- `_last_mesh_identity` change tracking per GUID
- PT_AssetDef sent after CREATE and on mesh datablock change

**UE side**:
- `FAssetIdentityRef` (16B POD: `uint64 High/Low`)
- `FAssetMetadata`, `FAssetDiagnostics` in `TMap<FGuid, FAssetMetadata>` (cold path)
- `PendingAssetQueue` bounded at 2048 entries
- `HandleAssetDef` → `ResolvePendingAssets` (8/tick, exp. backoff 1s→16s, max 5 retries)
- `AssignStaticMesh` live-swaps mesh on existing actor (preserves transform/hierarchy)
- `AssignFallbackPrimitive` via `GetPrimitiveMesh()` static helper

**Key constraints**:
- `FSyncTransformState` remains POD-only (no FString)
- No dedicated resolution thread (game-thread only)
- No variable-length protocol fields in 6A
- Fallback is TEMPORARY — late resolution replaces in-place

### Documentation Added
- `Docs/Protocol/live_sync_v5.md` — V5 protocol spec
- `Docs/Architecture/09-asset-identity.md` — Identity model & data flow
- AGENTS.md updated with Phase 6A files, CVars, and invariants

### Test Infrastructure
- `tests/phase6_validation_A_asset_identity.py` — automated validation suite
- `tests/run_phase6_all.py` — Phase 6 test runner

### Roadmap Updated
- `Docs/Roadmap/00-consolidated-roadmap.md` — Phase 6 → 6A/6B/6C subphase split

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
| `UE_Plugin/.../AssetIdentityTypes.h` | FAssetIdentityRef, FAssetMetadata (Phase 6A) |
| `UE_Plugin/.../PendingAssetQueue.h` | Bounded (2048) pending resolution queue (Phase 6A) |
| `UE_Plugin/.../SLiveSyncStatusWidget.cpp/h` | Compact status indicator panel |
| `UE_Plugin/.../SLiveSyncDiagnosticsWidget.cpp/h` | Full diagnostics panel |

---

## Upcoming

| Phase | Description | Est. |
|-------|-------------|------|
| 6B | Material assignment + cache persistence | 5–7d |
| 6C | FBX mesh push pipeline | 7–10d |
| 7 | Live editing: create/delete/rename/visibility | 8–12d |
| 8 | Animation & Sequencer sync | 14–21d |
| 9 | High-performance streaming | 10–16d |
| 10 | Production ecosystem | Ongoing |
