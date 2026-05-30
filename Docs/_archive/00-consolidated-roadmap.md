# UELiveSync — Consolidated Roadmap

**Document Version**: 1.3  
**Last Updated**: 2026-05-26  
**Status**: Living document — updated as phases complete

---

## Table of Contents

1. [Current Architecture State](#1-current-architecture-state)
2. [Completed Phases](#2-completed-phases)
3. [Phase 5 — Protocol Evolution & Runtime Stabilization](#3-phase-5--protocol-evolution--runtime-stabilization)
   - [5D — Asset Identity & Static Mesh Resolution](#31-5d--asset-identity--static-mesh-resolution)
   - [5E — Stress Testing & Observability](#32-5e--stress-testing--observability)
4. [Phase 6 — Live Editing System](#4-phase-6--live-editing-system)
5. [Phase 7 — Animation & Sequencer Sync](#5-phase-7--animation--sequencer-sync)
6. [Phase 8 — High Performance Streaming](#6-phase-8--high-performance-streaming)
7. [Phase 9 — Production Ecosystem](#7-phase-9--production-ecosystem)
9. [Architectural Identity](#9-architectural-identity)
10. [Non-Goals / Scope Boundaries](#10-non-goals--scope-boundaries)
11. [Technical Debt & Future Risks](#11-technical-debt--future-risks)
12. [Appendix: Protocol Evolution](#12-appendix-protocol-evolution)

---

## 1. Current Architecture State

UELiveSync is a **lightweight realtime state replication framework** for Blender ↔ Unreal Engine 5.7 editor synchronization. It streams transform data over TCP using a compact binary protocol with GUID-based object identity.

### Validated Architecture

| Layer | Technology | Status |
|-------|-----------|--------|
| Transport | Direct TCP (Sockets/Networking module) | Production-stable |
| Protocol | Binary V3/V4 with 24-byte header, flags, packet types | Production-stable |
| Object Identity | `uuid.uuid4()` → 128-bit FGuid, persistent across sessions | Production-stable |
| Sync Loop | Timer-driven scene polling from Blender main thread | Production-stable |
| Thread Sender | Background daemon thread, non-blocking enqueue | Production-stable |
| UE Thread | Dedicated network thread → bounded MPSC queue → game thread | Production-stable |
| Interpolation | Adaptive VInterpTo + Slerp with convergence snap, local-space for children | Production-stable |
| Heartbeat | 5s interval Blender → 15s timeout UE | Production-stable |
| Reconnection | Immediate full snapshot on reconnect, sequence ID dedup | Production-stable |
| Snapshot Batching | PT_BeginSnapshot/PT_EndSnapshot with deferred hierarchy resolution | Production-stable |
| Missing Actor Recovery | 30-frame grace period → auto-recreate from stored state | Production-stable |
| Hierarchy Authority | Local-space interpolation for attached children, UE attachment ownership | Production-stable |
| Deferred Attachments | Bounded retry queue (60 frames max, exponential backoff) | Production-stable |
| Overflow Protection | Bounded 128-entry MPSC queue, drop-oldest | Production-stable |
| Watchdog | Lifecycle monitoring with restart backoff | Production-stable |
| Diagnostics | Status widget, runtime metrics, hierarchy diagnostics | Phase 4 baseline |
| Protocol Versioning | V2/V3/V4/V5 coexistence, version mismatch detection, unknown-type skipping | Production-stable |
| Primitive Type | 1-byte enum in CREATE payload (Cube/Sphere/Cylinder/Plane/Empty) | Production-stable |
| GUID Hardening | Owner hashing for stale-GUID detection across .blend load cycles | Production-stable |
| Asset Identity | xxHash64 of datablock name, POD identity, bounded pending queue | Phase 5D |
| Static Mesh Assignment | Non-blocking deferred resolution, live-swap, exponential backoff retry | Phase 5D |
| Fallback Primitives | Temporary mesh assignment, replaceable on late resolution | Phase 5D |

### Current Metrics

- **Protocol overhead**: 24-byte header + 80-byte transform record (104 bytes per object per tick)
- **Asset def overhead**: 33 bytes per object (one-time + on mesh change)
- **Queue capacity**: 128 packets (bounded MPSC)
- **Asset pending queue capacity**: 2048 entries (bounded FIFO)
- **Asset resolution throttle**: 8 resolves per game tick
- **Asset retry policy**: 5 attempts max, exponential backoff (1s→2s→4s→8s→16s)
- **Default hierarchy depth limit**: 64 levels (configurable via CVar)
- **Interpolation modes**: Direct-set (0), smooth adaptive (1), snap-when-close (2)
- **Drift detection threshold**: 0.01 cm (verbose logging)

---

## 2. Completed Phases

### Phase 1 — Foundation (Done)

Blender addon scaffold, UE plugin scaffold, basic TCP communication, initial GUID mapping, V2 protocol.

### Phase 2 — Core Sync (Done)

Reliable transform streaming, coordinate conversion (Y-axis flip, scale ×100), object filter (MESH only), scene iteration, change detection via threshold comparison.

### Phase 3 — Production Hardening (Done)

Thread safety, heartbeat with timeout, V3 protocol (binary GUID, packet types, parent field), reconnection, bounded queue, Actor cache (incremental + full rebuild), anti-reorder sequence dedup, stale state TTL eviction.

### Phase 3.4—3.6 — Robustness (Done)

V3 → V4 transition, CREATE/UPDATE/DELETE lifecycle, primitive type byte, snapshot batching (PT_BeginSnapshot/PT_EndSnapshot), missing actor recovery, GUID owner hashing, watchdog with restart backoff, overflow stress testing, long-session stability validation, ECVF_Cheat CVar discipline.

### Phase 4 — Stability Core (Done)

CVar system expansion (threshold, interp mode, snap distance, TTL), diagnostics panel, status bar extension, console commands (DumpState, Reset, Ping, Stats), protocol validation (malformed packet rejection, boundary checks, V2/V3/V4 coexistence), queue overflow hardening, reconnection stress testing.

### Phase 5A — Workflow & Protocol (Done)

Primitive type UI in Blender prefs, `PF_FullSnapshot` flag handling, `UE.LiveSync.DumpState` console command for diagnostics, `UE.LiveSync.Ping` / `UE.LiveSync.Stats` / `UE.LiveSync.Reset`, `RecoverMissingActors()` with throttled warnings and timeout, per-frame rate tracking with adaptive interp speed.

### Phase 5B — Hierarchy Authority Model (Done — Production-Stable)

**Validated**: 122/122 tests pass across all suites. Tagged `phase5B-stable`.

Core changes:
- **Local-space interpolation** for attached children via `CurrentLocal*` / `LocalTarget*` fields, separate from world-space root path
- **`bPendingSceneGraphWrite` lifecycle**: world-space `SetActorTransform` only when explicitly requested, not on every interpolation tick
- **`bHasLocalTarget` flag**: distinguishes local-authority (attached child) from world-authority (root) state
- **`bParentChanged` fix**: saved before `ParentGuid` overwrite to detect parent transitions
- **Removed unconditional `AttachToParent` every tick**: parent binding only on GUID change
- **`DetachFromParent`**: exits local-authority mode, re-seeds world-space from actor, sets `bPendingSceneGraphWrite`
- **`AttachToParent`**: self-parent rejection, explicit cycle detection during depth walk, churn counting for diagnostics
- **`HandleCreateObject`**: initialization order flip (`Spawn → Tag → Attach → state-init-by-caller`), removed corrupting `UpdateTargetTransform` calls
- **`RecoverMissingActors`**: local-aware spawn path for children (passes `LocalTarget*` values, `bIsLocalTransform=true`)
- **`ResolvePendingAttachments`**: deferred world rewrite after successful attach
- **`InterpolateTransforms`**: `.GetNormalized()` on all `FQuat::Slerp` calls, thresholded drift diagnostics at >0.01 cm, attached-child guard before root paths
- **`ELiveSyncPrimitiveType`**: renamed from `EPrimitiveType` to resolve UE `RHIDefinitions.h` conflict

### Phase 5C — Diagnostics & Editor UX (Done)

Diagnostics panel with full hierarchy and metrics sections, CVar-controlled debug overlay (`UE.LiveSync.DebugOverlay`), Blender addon status panel with connection/object/sync-rate indicators and manual reconnect/rebind buttons, CVar-controlled logging levels (Error/Warning/Log/Verbose/VeryVerbose), Blender-side diagnostics log (`_log_diagnostics()`), and fail-safe protections (reconnect throttle, send watchdog, max queue cap, device loss emergency off).

---

### Phase 5D — Asset Identity & Static Mesh Resolution (Done)

**Phase 5D work**: xxHash64 deterministic datablock identity (`FAssetIdentityRef`,
16B POD), V5 protocol (`PT_AssetDef = 0x08`, 33B fixed payload), `PendingAssetQueue`
(bounded 2048-entry FIFO), `ResolvePendingAssets` (8/tick, exponential backoff
1s→2s→4s→8s→16s, max 5 retries), `AssignStaticMesh` (live-swap on existing actors),
`AssignFallbackPrimitive` (temporary mesh, replaced on late resolution), `AssetMetadata`
TMap in cold path.

Crash investigation: original "editor freeze" identified as SIGABRT in
`FPendingAssetQueue::Dequeue` → `TSet::Remove` (SparseSet assertion). Fixed with
`Contains()` guard. Secondary infinite-loop risk in `ResolvePendingAssets` fixed
by moving `ResolvedThisTick++` to top of while body.

### Phase 5E — Stress Testing & Observability (Done)

**Phase 5E work**: Long-duration stress test (30+ min), large-scene stress test
(1000+ objects + 1500 hierarchy), reconnect storm test (50 rapid cycles), malformed
packet injection, Unreal Insights `TRACE_CPUPROFILER_EVENT_SCOPE` at every pipeline
stage, runtime metrics dashboard, queue age watchdog, hierarchy validation every 300
ticks, 5-isolation CVars (`DisableInterpolation`, `DisableAttachmentResolution`,
`DisableAssetResolution`, `DisableRecovery`, `BypassSetActorTransform`).

Final freeze root-cause documentation: plugin pipeline confirmed stable (46,400 Tick
frames, 232K balanced BEGIN/END, 14K SetActorTransform calls, 6h38m runtime).
Remaining issue is Linux CEF/Vulkan GPU subprocess instability — external to plugin.

---

## 3. Phase 5 — Protocol Evolution & Runtime Stabilization

**Status**: Complete (2026-05-25) · **Risk**: Low  
**Phase 5 milestones**: 5A (Snapshot), 5B (Hierarchy), 5C (Diagnostics),
5D (Asset Identity), 5E (Stress/Observability)

### 5A — Snapshot Foundations

Protocol-level begin/end snapshot markers, deferred hierarchy during bulk load,
snapshot timeout (5s auto-abort), full-snapshot flag handling.

### 5B — Hierarchy Authority Model

Local-space interpolation for attached children, UE attachment ownership,
parent-change detection, attachment cycle protection, deferred world-space
rewrite after successful attach.

### 5C — Diagnostics Panel & Editor UX

Convert the existing `SLiveSyncStatusWidget` from Phase 4 into a full diagnostics panel:

| Section | Metrics |
|---------|---------|
| Connection | Status, uptime, remote address, protocol version, heartbeat age |
| Throughput | Packets/sec, bytes/sec, avg process time, queue depth |
| Objects | Total tracked, attached children, roots, pending attachments |
| Hierarchy | `WorldErrorDistance`, `MaxWorldErrorDistance`, `AttachmentChurnCount`, `ParentMismatchCount`, `ReattachCount` |
| Warnings | Recent errors, timeout-to-evict counts, drift warnings |
| Reset | `Reset`, `DumpState`, `Ping`, `Start`/`Stop` buttons |

**Refresh throttle**: 250ms minimum interval between UI updates. Cache `FText` values computed from subsystem state.

### 3C.2 — Debug Overlay (CVar)

| CVar | Default | Description |
|------|---------|-------------|
| `UE.LiveSync.DebugOverlay` | 0 | Draw on-screen debug text in editor viewport |

When enabled, display via `GEngine->AddOnScreenDebugMessage()`:

```
[LiveSync] Connected | 42 objects | Queue: 0 | 120 pkts/s | H-Diag: err=0.02cm
```

### 3C.3 — Blender Addon Status UI

Translate the UE diagnostics panel model to Blender's sidebar:

| Blender UI Element | Data Source |
|-------------------|-------------|
| Connection status indicator | Socket state (`_connected` bool in `network.py`) |
| Object count | `len(tracked_objects)` |
| Sync rate | Timer interval from preferences |
| Last heartbeat time | `_last_heartbeat_sent` timestamp |
| Manual reconnect button | Calls `connect_to_unreal()` |
| Rebind All button | Calls `rebind_all()` with snapshot batching |
| Primitive type dropdown | `default_primitive` enum (existing Phase 5A) |
| Verbose logging toggle | `_runtime_config["verbose_logging"]` |

### 3C.4 — Logging Levels

Add CVar-controlled log verbosity:

| Level | Output |
|-------|--------|
| `Error` | Connection failures, protocol mismatches, timeout, cycle detected |
| `Warning` | Deferred attachment timeout, missing actor after 30 frames, churn, drift |
| `Log` | Connect/disconnect, snapshot begin/end, attachment, detach, authority transitions |
| `Verbose` | Per-packet transforms, per-actor authority path updates, hierarchy diagnostics |
| `VeryVerbose` | Raw packet bytes, interpolation step values (development only) |

Current `UE.LiveSync.VerboseSyncLogs` CVar maps to `Verbose` level.

### 3C.5 — Blender-Side Diagnostics Log

Add an in-Blender log panel or file-based diagnostic dump:

```python
def _log_diagnostics():
    """Dump sync state to Blender's info log area."""
    msg = (
        f"Sync: {len(tracked_objects)} objects | "
        f"Connected: {_connected} | "
        f"Queue: {_send_queue.qsize()} | "
        f"Missed ticks: {_missed_tick_count}"
    )
    print(msg)
```

### 3C.6 — Fail-Safe Protections

- **Automatic reconnect throttle**: never retry faster than every 2 seconds
- **Max queued packets per tick on Blender side**: cap at 512 to prevent memory blowout
- **Send watchdog**: if send queue exceeds 1024 entries and no flush within 10s, log error and drain
- **Device loss detection**: if `socket.sendall()` raises persistent errors, switch to emergency off mode and notify user via Blender UI

### Files

| File | What |
|------|------|
| `UELiveSyncSubsystem.cpp` | `DebugOverlay` CVar, `GetDiagnosticsText()`, fail-safe guards |
| `UELiveSyncSubsystem.h` | Public diagnostics accessors, `DrawDebugOverlay()` method |
| `SLiveSyncStatusWidget.cpp/h` | Extended with hierarchy diag + metrics sections |
| `network.py` | Blender-side send watchdog, queue drain on error |
| `sync.py` | `_log_diagnostics()` helper, verbose toggle integration |
| `__init__.py` | Status panel in sidebar with diag fields, log viewer toggle |

---

## 4. Phase 6 — Live Editing System

**Status**: Phase 6 — Live Editing System (Rename STABILIZED · Visibility STABILIZED · Collection IMPLEMENTED · Hierarchy IN PROGRESS)  
**Scope**: See `Docs/Architecture/18-phase6-scope-lock.md` for hard IN-SCOPE/OUT-OF-SCOPE boundaries, authority models,
and escalation rules.

### 4A — Rename Replication Vertical Slice (STABILIZED)

**Status**: Implemented, stabilized, and verified.  
**Docs**: `19-phase6-vertical-slice-rename.md` (plan), `18-phase6-scope-lock.md` (scope)

**Packet type**: `PT_Rename = 0x0C`
- Wire format: GUID(16) + oldNameLen(2) + oldName(N) + newNameLen(2) + newName(M) + seq(4) + ts(8)
- Semantic editor event (NOT state stream)
- Provenance: `EChangeOrigin` (`RemoteReplicated` / `Replay`), in-memory only
- Suppression: `FScopedRenameSuppression` RAII
- Replay safety: `FRenameSequenceTracker` (bounded 2048, stale/duplicate via `<=` sequence)
- Reconnect: tracker cleared in `StopNetworkThread()` + `ConsoleReset()` + Blender `_close_internal()`
- CPU profiler: `UELiveSync_HandleRename`, `UELiveSync_ProcessRenamePackets`
- Counters: `RenamesProcessed`, `RenameStaleRejections`, `RenameReplayApplied`, `RenameReplaySkipped`

### 4B — Visibility Replication Vertical Slice (STABILIZED)

**Status**: Stabilized and validated. 9/9 live validation criteria met, 11/11 live tests PASS.  
**Docs**: `20-phase6-visibility-scope-lock.md` (scope), `21-phase6-vertical-slice-visibility.md` (plan),
`23-phase6-live-runtime-validation.md` (validation report)

**Packet type**: `PT_Visibility = 0x0B`
- Wire format: GUID(16) + bHidden(1) + seq(4) + ts(8) — fixed 29 bytes per object
- Distinct from rename: no callback recursion risk (`SetIsTemporarilyHiddenInEditor` fires no standard callback), fixed-length wire format, idempotent bool state
- Follows identical architectural pattern: provenance → suppression → replay → observability

### 4C — Hierarchy Replication Vertical Slice (IN PROGRESS — Stage 7/14)

**Status**: Implementation in progress. Stages 0-7 complete: enum reservation, parser branch, sequence tracker,
replay rejection, validation foundation, basic AttachToActor/DetachFromActor, deferred queue + orphan lifecycle.
Blender-side emission and cycle detection not yet implemented.
**Docs**: `24-phase6D-hierarchy-scope-lock.md` (scope), `25-phase6D-vertical-slice-hierarchy.md` (design),
`26-phase6D-hierarchy-implementation-plan.md` (14-stage plan)
- **PT_HIERARCHY = 0x0D**: Fixed 44 bytes per object: ChildGuid(16)+ParentGuid(16)+seq(4)+ts(8)
- Follows identical architectural pattern: provenance → suppression → replay → observability
- First dependency-sensitive lane: graph consistency, orphan lifecycle, deferred retry queue (bounded 2048)
- 40 standalone tests pass; 7 integration tests skip (require UE editor)

### 4D — Future Phase 6 Features (BLOCKED / PLANNED)

- Lifecycle/delete replication (BLOCKED — hierarchy must stabilize first, see dependency chain below)
- Collection/folder structure sync (PLANNED — requires hierarchy + lifecycle)
- Duplicate detection and handling (PLANNED — requires all prior lanes)

All protocol parsing, queue safety, reconnect handling, asset identity,
stress infrastructure, and runtime stabilization work was completed in
Phase 5 (5A–5E) and is NOT Phase 6.

**Note**: Asset identity (xxHash64, PT_AssetDef, PendingAssetQueue, V5 protocol) was implemented in **Phase 5D** and completed as part of Phase 5. It is NOT Phase 6. See `Docs/Architecture/09-asset-identity.md` and `Docs/Protocol/live_sync_v5.md`.

### 4.1 — Material Assignment & Cache Persistence (Phase 6)

**Status**: Backlog · **Depends on**: Phase 5D (asset identity)

Assign UE materials to mesh components. No shader graph creation, no material instance generation.

**Packet type**: `PT_MaterialAssign = 0x0B` (proposed)

| Section | Bytes | Description |
|---------|-------|-------------|
| GUID | 16 | Object GUID |
| Slot Index | 1 | Material slot index (uint8) |
| Path Length | 2 | uint16 length of material asset path |
| Path | N | UTF-8 encoded material asset path |

**Blender side**:
- Reads active material name from `obj.active_material.name`
- Maps material name to UE asset path via a configurable lookup table in addon prefs
- Sends `PT_MaterialAssign` on material change (detected via `obj.active_material` pointer change)

**UE side**:
- Finds actor → gets first `UMeshComponent`
- Sets material at slot index via `SetMaterial(SlotIndex, LoadObject<UMaterialInterface>(Path))`
- Caches material path per GUID to avoid redundant assignments

### 4.2 — FBX Mesh Push Pipeline (Phase 6)

**Status**: Backlog · **Depends on**: Phase 5D (asset identity), Phase 6 material assignment

Blender exports each mesh as FBX → writes to a known project path → UE auto-reimports via directory watcher.

**Asset identity strategy**:

```
/Game/LiveSync/Meshes/<GUID>.<GUID>
```

| Requirement | Implementation |
|-------------|---------------|
| Overwrite existing assets | Write FBX to same path; trigger reimport |
| Preserve actor references | Detect GUID path collision → overwrite in place |
| Avoid duplicate asset generation | One asset per GUID |
| Handle renames | GUID is stable; name changes don't affect paths |



**Blender side** (`sync.py`):
- `export_mesh_fbx(obj, output_dir)` — exports FBX with embedded textures, triangulate, scale = 100
- Called when mesh is first tracked or when `obj.data` polygon count changes
- Configurable export path in prefs (default: `../../Content/LiveSync/Meshes/`)

**UE side**:
- `HandleMeshUpdate(FGuid, FString FilePath)` — queue reimport onto editor async task
- Reimport via `UAutoReimportManager` or `UAssetEditorSubsystem`
- On CREATE for a GUID with existing FBX → assign directly

**Threading**: FBX import runs on `EAsyncExecution::TaskGraphMainThread` to avoid blocking game thread.

### 4.3 — Missing Asset Recovery (Phase 6)

**Status**: Backlog — partial resolution (retry + fallback) completed in Phase 5D

When a referenced mesh or material asset path cannot be loaded:

| Threshold | Action |
|-----------|--------|
| Immediate | Log warning, fall back to Cube (Phase 5D) |
| First recovery | Re-query asset after 30 frames (async asset registry query) |
| After 60 frames | Mark as permanently missing, log error once |

### 4.4 — Asset Dependency Tracking (Phase 6)

**Status**: Backlog

Track GUID → asset relationships for orphan detection and health monitoring:

```
GUID
 ├── /Game/LiveSync/Meshes/<GUID>.<GUID>       (mesh asset)
 ├── /Game/LiveSync/Materials/<GUID>.*           (material assignment)
 ├── metadata                                    (export timestamp, hash)
```

Console command `UE.LiveSync.AssetHealth` prints:
- Total tracked assets
- Orphaned (GUID not in TransformStates)
- Stale (hash mismatch)
- Missing (GUID exists but asset path fails to load)

### Files (Phase 5D — Asset Identity, completed)

| File | What |
|------|------|
| `SyncTypes.h` | V5 version constant, `PT_AssetDef = 0x08`, `LIVE_SYNC_V5_ASSET_DEF_SIZE = 33`, asset stats counters |
| `AssetIdentityTypes.h` | `FAssetIdentityRef` (16B POD), `FAssetMetadata`, `FAssetDiagnostics`, resolution constants |
| `PendingAssetQueue.h` | Bounded 2048-entry FIFO with `FCriticalSection`, O(1) Contains/Remove |
| `network.py` | `xxh64()`, `get_mesh_identity_hash()`, `serialize_asset_identity()`, V5 constants |
| `sync.py` | `_last_mesh_identity` change tracking, PT_AssetDef sent after CREATE and on mesh change |
| `UELiveSyncSubsystem.cpp` | `HandleAssetDef`, `ResolvePendingAssets`, `AssignStaticMesh`, `AssignFallbackPrimitive`, `GetPrimitiveMesh()` |
| `UELiveSyncSubsystem.h` | Asset metadata/identity maps, pending queue, resolution methods |
| `LiveSyncRunnable.cpp` | V5 header parsing support |
| `tests/phase5d_validation_A_asset_identity.py` | Phase 5D validation suite |
| `tests/run_phase5_all.py` | Phase 5 test runner |
| `Docs/Protocol/live_sync_v5.md` | V5 protocol documentation |
| `Docs/Architecture/09-asset-identity.md` | Asset identity architecture documentation |

### Files (Phase 6 — rename, stabilized)

| File | What |
|------|------|
| `UELiveSyncSubsystem.cpp` | `HandleRename()`, `FScopedChangeOrigin`, `FScopedRenameSuppression`, PT_Rename dispatch |
| `UELiveSyncSubsystem.h` | `HandleRename` declaration |
| `SyncTypes.h` | `PT_Rename = 0x0C`, `EChangeOrigin`, `FRenameSequenceTracker`, rename counters |
| `network.py` | `serialize_rename()`, `_rename_sequences` tracker, cleanup in `_close_internal()` |
| `sync.py` | `_last_object_names` diff detection, emit PT_Rename, cleanup on stop/reset |

### Files (Phase 6 — visibility, planned)

| File | What |
|------|------|
| `Docs/Architecture/20-phase6-visibility-scope-lock.md` | Visibility scope lock |
| `Docs/Architecture/21-phase6-vertical-slice-visibility.md` | Visibility vertical slice plan |
| (implementation TBD — not yet started) | |

---

## 5. Phase 7 — Animation & Sequencer Sync

**Status**: Planning · **Estimate**: 8–12 days · **Risk**: High  
**Depends on**: Phase 6 (asset identity before editing operations)

### 5.1 — Object Create/Delete Replication (Blender → UE)

**Current**: Objects are created on first sync in Blender and deleted when they leave sync scope.

**Change**: Extend to support create/delete operations during an active sync session:

- **Object created in Blender** → immediate CREATE packet with GUID, transform, primitive type
- **Object deleted in Blender** → immediate DELETE packet
- **UE responds** by spawning/destroying the corresponding actor

**Delete safety**:
- DELETE is authoritative only if Blender confirms removal from scene
- UE side: add a `FDeleteRequest` grace period (5 seconds) before destroying actor
- If a TRANSFORM packet arrives for a GUID in grace period → cancel delete

### 5.2 — Rename Replication

**Status**: STABILIZED (Phase 6A/6B). See Phase 6 section above.
**Packet type**: `PT_Rename = 0x0C` (implemented in Phase 6A, stabilized in Phase 6B)

UE side uses `AActor::SetActorLabel(NewName)` with full provenance
tracking, suppression, and replay safety (see Phase 6A/6B for details).

### 5.3 — Visibility Sync

**Status**: STABILIZED (Phase 6C). See Phase 6 section above.
**Packet type**: `PT_Visibility = 0x0B` (stabilized in Phase 6C)

Editor hidden-state replication (`SetIsTemporarilyHiddenInEditor`).
See `Docs/Architecture/20-phase6-visibility-scope-lock.md` for scope
and `Docs/Architecture/21-phase6-vertical-slice-visibility.md` for
full design.

### 5.4 — Folder / Collection Sync (Blender → UE)

Blender collections can be mapped to UE folder actors (empty actors for organization):

**Packet type**: `PT_Folder` (provisional — collection metadata now uses `PT_Collection = 0x0F`; a future packet type may be allocated for folder actors)

- Collection name → folder actor label
- Collection hierarchy → folder actor hierarchy
- Objects within collection → parented to folder actor
- Collection color → folder actor tag color

**Implementation**:
- Create folder actors on connect during scan
- Track collection membership separately from transform sync
- Collection rename → rename folder actor
- Collection delete → destroy folder actor (reparent children to root)

### 5.5 — Duplicate Detection

**Scenario**: User duplicates an object in Blender (`Shift+D`). The copy inherits `ue_guid` from `obj.copy()`.

**Detection**:
- `ensure_unique_guid()` already catches Guid collision (Phase 5A)
- On collision: compare owner hash — if different, regenerate GUID
- Log: "Duplicate detected: obj=<name> new GUID=<new> old GUID=<old>"

### 5.6 — Lifecycle Authority Rules

| Operation | Authority | Behavior |
|-----------|-----------|----------|
| CREATE | Blender | UE must accept and spawn |
| DELETE | Blender | UE destroys after grace period |
| DELETE | UE (manual) | Detected by missing-actor recovery → re-spawned |
| RENAME | Blender | Best-effort rename on UE side |
| VISIBILITY | Blender | UE actor hidden/shown |
| TRANSFORM | Blender | UE interpolates |

### 5.7 — Undo/Redo Safety

Blender undo/redo can rewind object state. After undo:
- Objects may revert to pre-sync GUID state
- `ensure_unique_guid()` re-assigns GUIDs on next scan
- UE side detects GUID changes as CREATE + DELETE (old GUID removed, new GUID spawned)

**Optimization**: Add an `ue_guid_preserved` flag that short-circuits full re-sync if only transform changed.

### Files

| File | What |
|------|------|
| `SyncTypes.h` | `PT_Rename = 0x0C`, `PT_Visibility = 0x0B`, `PT_Hierarchy = 0x0D` |
| `network.py` | Serialize rename, visibility, folder packets |
| `sync.py` | Poll `obj.name`, `obj.hide_get()`, detect duplicates, collection scan |
| `UELiveSyncSubsystem.cpp` | `HandleRename()`, `HandleVisibility()`, `HandleFolder()`, grace-period delete |
| `UELiveSyncSubsystem.h` | `FDeleteRequest` struct, grace period map |

---

## 6. Phase 8 — Animation & Sequencer Sync

**Status**: Research · **Estimate**: 14–21 days · **Risk**: Very High  
**Depends on**: Phase 7 (live editing before animation operations)

### 6.1 — Timeline Sync

**Goal**: Synchronize Blender timeline (current frame, playback state) with UE Sequencer.

**Packet type**: `PT_Timeline = 0x10`

| Section | Bytes | Description |
|---------|-------|-------------|
| Frame | 4 | uint32 current frame number |
| Playback State | 1 | 0=paused, 1=playing, 2=scrubbing |
| FPS | 4 | float frames per second |
| Start Frame | 4 | uint32 timeline start |
| End Frame | 4 | uint32 timeline end |

**Blender side**:
- Poll `scene.frame_current`, `scene.sync_mode`
- Send on frame change or playback state change
- Throttle: max 30 packets/sec during scrubbing

**UE side**:
- Drive Sequencer transport via `ISequencer::SetLocalTime()`
- Only affect Sequencer if `bAutoSyncSequencer` is enabled in plugin prefs
- Cross-reference by sequence name or an explicit mapping

### 6.2 — Playback Sync

| Scenario | Blender | UE |
|----------|---------|-----|
| Play pressed | Send PT_Timeline (playing) | Sequencer plays from current frame |
| Pause pressed | Send PT_Timeline (paused) | Sequencer pauses |
| Frame scrubbed | Send PT_Timeline (scrubbing) | Sequencer jumps to frame |
| UE starts playback | (receive path TBD) | Send PT_Timeline back to Blender |

**Transport authority**: Configurable:
- **Blender Authority** (default): Timelines driven by Blender, UE follows
- **UE Authority**: UE Sequencer controls transport, Blender follows
- **Independent**: No sync, both sides can play independently

### 6.3 — Camera Sync

**Goal**: Map Blender camera to UE CineCameraActor with lens parameters.

**Packet type**: `PT_Camera = 0x11`

| Section | Bytes | Description |
|---------|-------|-------------|
| GUID | 16 | Camera object GUID |
| FOV | 4 | float field of view (degrees) |
| Focal Length | 4 | float in mm |
| Sensor Width | 4 | float in mm |
| Focus Distance | 4 | float in cm |
| Aperture | 4 | float f-stop |

**Blender side**:
- Detect camera objects via `obj.type == 'CAMERA'`
- Read `obj.data.lens`, `obj.data.sensor_width`, `obj.data.dof.focus_distance`
- Send as part of transform sync tick (throttled to 10 Hz)

**UE side**:
- On CREATE for CAMERA type → spawn `ACineCameraActor` instead of `AActor`
- Apply lens parameters to `UCineCameraComponent`
- Transform sync uses same local/world hierarchy rules as Phase 5B

### 6.4 — Keyframe Replication

**Goal**: Replicate specific keyframes from Blender animation to UE.

**Constraints**:
- Do NOT replicate entire animation curves (huge data volume)
- Only replicate keyframes that Blender explicitly marks for export
- UE side stores keyframes as `FRichCurve` on the actor's transform component

**Packet type**: `PT_Keyframe = 0x12`

| Section | Bytes | Description |
|---------|-------|-------------|
| GUID | 16 | Object GUID |
| Frame | 4 | uint32 keyframe frame number |
| Channel | 1 | 0=location, 1=rotation, 2=scale |
| Value | 12 | FVector or FQuat (padded to 12 bytes) |

**Workflow**:
1. User selects keyframes in Blender's Dope Sheet or Graph Editor
2. Clicks "Export Selected Keyframes" (operator in sync panel)
3. Selected keyframes are sent as batch, bracketed by begin/end markers
4. UE stores keyframes in a dedicated `TMap<FGuid, TArray<FStoredKeyframe>>`
5. Sequencer integration (Phase 8.5) can apply stored keyframes as animation tracks

### 6.5 — Sequencer Integration

**Goal**: Create Sequencer tracks from synced keyframes and live transforms.

**Implementation sketch**:
- `ULiveSyncSequencerTrack` registered as a MovieScene track type
- On `UE.LiveSync.CaptureSequencerTrack` console command:
  - For each tracked GUID, create a transform track in the current sequence
  - Populate with keyframes from the stored keyframe bank
  - Live transforms feed into the track as realtime keyframe updates

**Scope limitation**: This integration is experimental. Sequencer API varies significantly between UE versions.

### 6.6 — Frame-Accurate Synchronization

- Network latency compensation: UE-side frame buffer (configurable, default 2 frames)
- Blender-side send-ahead: `send_transform_at(scene.frame_current + latency_frames)`
- Deterministic playback mode (Phase 7 deferred item): lock delta time to 1/24 or 1/48

### Files

| File | What |
|------|------|
| `SyncTypes.h` | `PT_Timeline = 0x10`, `PT_Camera = 0x11`, `PT_Keyframe = 0x12` |
| `sync.py` | Camera parameter extraction, timeline polling, keyframe selection |
| `network.py` | Serialize timeline, camera, keyframe packets |
| `UELiveSyncSubsystem.cpp` | `HandleTimeline()`, `HandleCamera()`, `HandleKeyframe()` |
| `UELiveSyncSubsystem.h` | Sequencer interop members, keyframe storage, camera spawn logic |

---

## 7. Phase 9 — High Performance Streaming

**Status**: Research · **Estimate**: 10–16 days · **Risk**: Medium  
**Depends on**: Phase 6 and Phase 8 (real-world scene complexity data to guide optimization)

### 7.1 — Binary Packet Compression

Add optional zlib compression for large packets (>1 KB payload):

- Compression flag in V5 header flags byte
- Compress payload, set `PF_Compressed (0x08)` flag
- Decompress on receive thread before enqueuing

**Threshold**: Only compress packets > 1400 bytes (typical MTU). Below MTU, compression overhead outweighs savings.

**Expected savings**: 40–60% reduction for snapshot batches (100+ objects). Negligible for single-transform packets.

### 7.2 — Delta Serialization

Instead of sending full transform records every tick, send only the delta from the last sent state:

| Section | Bytes | Description |
|---------|-------|-------------|
| GUID | 16 | Object GUID |
| Change Mask | 1 | Bitfield: bit0=loc, bit1=rot, bit2=scl, bit3=parent |
| Changed fields | Varies | Only fields whose mask bit is set |

**Trade-off**: Delta serialization reduces per-packet size by ~60% (80 bytes → ~30 bytes for typical partial changes) but adds encode/decode complexity and error-recovery requirements (full state must be periodically re-sent as a checkpoint).

**Implementation**: Full state every 30th update (configurable), deltas in between. On UE side, apply delta to cached full state.

### 7.3 — Packet Batching

Batch multiple object updates into a single TCP packet:

- Multiple transform records in one `PT_Transform` packet (count set in header)
- Currently supported at the protocol level — make it the default path
- Blender side: collect updates over 1 tick interval, send as batch
- UE side: iterate records in one `ProcessBinaryPacket()` call

**Effect**: Reduces syscall overhead (one `send()` call instead of N) and amortizes header overhead.

### 7.4 — Adaptive Update Rates

Instead of fixed 60 Hz polling, adapt based on scene activity:

| Scene State | Poll Interval | Packet Rate |
|-------------|---------------|-------------|
| Idle (no changes detected) | 1000 ms | Heartbeat only |
| Low activity (1–5 objects moving) | 100 ms | 10 Hz |
| Normal (6–50 objects moving) | 33 ms | 30 Hz |
| High activity (50+ objects moving) | 16 ms | 60 Hz |
| Burst (100+ simultaneous changes) | Immediate flush | No limit (burst) |

**Implementation**:
- Track number of changed objects per poll cycle
- Adjust timer interval dynamically
- Cap at 60 Hz (Blender's typical viewport refresh rate)

### 7.5 — Interest Management

**Goal**: Only sync objects that are relevant to the user's current viewport or collection selection:

| Filter | Behavior |
|--------|----------|
| All objects (default) | Current behavior — sync every tracked object |
| Visible only | Only sync objects not hidden in viewport |
| Selected collection | Only sync objects in the active collection |
| Viewport frustum | Only sync objects within camera frustum (requires Blender 4.0+ `bpy.app.background` API) |

**Configurable via Blender addon prefs dropdown**.

### 7.6 — Large-Scene Scaling

**Target**: 10,000+ objects in a single scene.

| Bottleneck | Mitigation |
|------------|------------|
| Blender scene iteration | O(1) count-based diff with periodic full scan (every 300 frames) |
| Serialization CPU | Batch + delta + compression |
| Network bandwidth | ZSTD compression at high compression levels |
| UE actor spawning | Pool actors, reuse destroyed actor slots |
| UE transform processing | Resolve attachments only on change, skip converged objects |
| UE interpolation | Only interpolate actively-changing objects |

### 7.7 — World Partition Compatibility

- Sync with UE5 World Partition system: each partition's actors are streamed in/out independently
- Transform updates for actors in unloaded partitions are queued for delivery on load
- On partition load: apply latest known transform to all actors in the partition

### Files

| File | What |
|------|------|
| `network.py` | Compress/decompress with `zlib`, delta encoding, batch builder |
| `sync.py` | Adaptive poll interval, interest management filter |
| `UELiveSyncSubsystem.cpp` | Decompress step on receive, delta apply, partition awareness |
| `SyncTypes.h` | `PF_Compressed` flag, change mask constants |

---

## 8. Phase 10 — Production Ecosystem

**Status**: Planning · **Estimate**: Ongoing · **Risk**: Low-Medium  
**Depends on**: All prior phases (ecosystem wraps the completed core)

### 8.1 — Installer & Distribution

| Platform | Format |
|----------|--------|
| Blender addon | `.zip` (standard Blender addon distribution) |
| UE plugin | Marketplace-compatible `.uplugin` package |
| Combined | Release archive with both components + docs |

**Blender addon**:
- Version embedded in `blender_manifest.toml`
- Automatic update check via GitHub releases
- Installation instructions for `Edit → Preferences → Add-ons → Install from File`

**UE plugin**:
- Engine-installed plugin (zip to `Engine/Plugins/`) or project-installed (zip to `Project/Plugins/`)
- Pre-compiled binaries for Windows + Linux
- Source included for UE 5.7+ builds

### 8.2 — Auto-Discovery

**Blender → UE**:
- Blender addon scans local network for UE instances with LiveSync enabled
- Uses UDP broadcast on port 57001
- UE plugin responds with IP + port + project name + engine version
- Blender presents discovered instances in a dropdown

**Implementation**:
- UDP discovery is separate from the sync TCP connection
- Zero-config on UE side (enabled by default)
- Timeout: 3 seconds after boot

### 8.3 — Compatibility Layer

| UE Version | Support |
|------------|---------|
| UE 5.7 | Primary target, fully tested |
| UE 5.6 | Best-effort backward compatibility |
| UE 5.5 | Code-compatible with minor ifdefs |
| UE 5.4 and earlier | Community contributions welcome, no official support |

**API stability**:
- Public API (`UELiveSyncSubsystem` methods) are internal only — no public API contract
- Protocol version is the compatibility contract
- Version mismatch detection already in place (Phase 5F)

### 8.4 — Preset System

Save and load addon/plugin configurations as presets:

| Blender Presets | UE Presets |
|-----------------|------------|
| Port number | Port number |
| Poll interval | Interpolation mode |
| Primitive type default | Snap distance |
| Export path | Threshold values |
| Verbose toggle | Verbose toggle |
| Interest filter | Hierarchy depth limit |

**Format**: JSON files stored in `Blender_Addon/presets/` and plugin's `Config/` directory.

### 8.5 — Templates

**Blender**:
- `UELiveSync Starter.blend` — scene with pre-configured sync objects
- Default sync panel layout saved as workspace template

**UE**:
- `UELiveSync Starter Level` — level with LiveSync subsystem setup
- Example level blueprints showing how to react to sync events

### 8.6 — Crash Recovery

**UE side**:
- Crash detection via `IApplicationLifecycleInterface`
- On crash → save `TransformStates` and `MissingActorTracker` to disk
- On next boot → restore state and attempt to reconnect to Blender
- If Blender still running → resume normal sync
- If Blender restarted → treat as new connection with snapshot

**Blender side**:
- `atexit` handler sends "BlenderShutdown" notification (best-effort)
- On reconnect after UE crash → send full snapshot (existing behavior)
- No persistent state needed on Blender side

### 8.7 — Version Migration

When upgrading between major versions:
- Preserve GUIDs across addon updates (UUID generation is deterministic per session)
- Protocol version negotiation happens on connect
- UE rejects unsupported protocol versions (existing behavior)
- Migration path: upgrade UE first, then Blender (forward-compatible)

### 8.8 — Deployment UX

**Flow**:
1. Download release archive from GitHub
2. Install Blender addon: `Edit → Preferences → Add-ons → Install from File`
3. Install UE plugin: copy to `Engine/Plugins/` or project `Plugins/`
4. Restart both editors
5. Sync panel appears in Blender 3D View sidebar + UE status bar

**Troubleshooting**:
- Built-in connection test button (pings UE from Blender)
- Log viewer in both editors
- Diagnostics panel shows connection state
- FAQ / known issues in GitHub Wiki

### Files

| File | What |
|------|------|
| `blender_manifest.toml` | Addon manifest with version, dependencies |
| `UELiveSync.uplugin` | Plugin descriptor (updated with version) |
| `README.md` | Installation, quick start, troubleshooting |
| `network.py` | UDP discovery broadcast/listen |
| `UELiveSyncSubsystem.cpp` | Crash recovery serialization/deserialization |
| `__init__.py` | Preset save/load, auto-discovery UI, connection test |

---

## 9. Architectural Identity

UELiveSync is evolving into **a lightweight realtime state replication framework for Blender ↔ Unreal Engine editor synchronization**. It is not a full digital content creation (DCC) integration platform, but a focused, minimal-dependency bridge between the two most widely used tools in realtime visualization pipelines.

### Core Design Principles

| Principle | Manifestation |
|-----------|---------------|
| **Direct TCP replication** | No intermediary server, no message broker, no cloud relay. Blender → UE is a single TCP connection. |
| **Minimal dependencies** | Blender side uses only `bpy` and `socket` (stdlib). UE side depends only on `Sockets` and `Networking` modules. |
| **Editor-native responsiveness** | Transform changes appear in the UE viewport within ~50ms of the Blender operation. |
| **Deterministic GUID identity** | Every synced object has a persistent, collision-resistent GUID that survives save/load cycles. |
| **Lightweight runtime** | At idle (no changes detected), CPU usage is near zero on both sides. At 60 Hz with 100 moving objects, UE tick cost is < 0.5 ms. |
| **Validation-first development** | Every phase includes automated test suites. Production-stable phases have 100+ passing tests. Protocol changes require backward-compatibility validation. |

### What Makes UELiveSync Distinct

Unlike USD-based pipelines, Omniverse, or custom FBX roundtrip workflows:

- **Latency**: Sub-frame (16–50 ms) vs. batch-export (minutes)
- **Interactivity**: Real-time dragging in Blender reflected live in UE viewport
- **Scope**: Transformation + lightweight semantic data vs. full scene graph dumping
- **Footprint**: Two files (addon + plugin) vs. multi-gigabyte integration frameworks
- **Learning curve**: Minutes, not days. Enable plugin, press Start, move objects in Blender.

---

## 10. Non-Goals / Scope Boundaries

UELiveSync explicitly does **not** attempt to replace or compete with:

| Scope | Boundary |
|-------|----------|
| **Full Omniverse replacement** | No scene graph translation, no material graph compilation, no physics simulation sync, no multi-user collaboration. |
| **Cloud-first architecture** | No cloud relay, no WebSocket gateway, no REST API. The architecture assumes local network (same machine or LAN). |
| **USD-centric pipeline** | No USD stage, no USD composition arcs, no USD schema translation. UELiveSync uses its own binary protocol. |
| **Shader graph translation middleware** | No material node graph conversion, no shader code generation, no HLSL/GLSL cross-compilation. Material sync is limited to parameter values on existing UE materials. |
| **SCM / version control integration** | No Perforce/Git/SVN integration. No change list tracking, no checkout management. |
| **CI/CD / automated rendering** | No headless batch export mode, no command-line rendering pipeline. Designed for interactive editor use. |
| **Multi-user realtime collaboration** | No conflict resolution, no operational transform, no CRDT. Single Blender → single UE direction. |
| **Physics / cloth / hair sync** | No physics body replication, no cloth simulation sync, no hair groom pipeline. |

**Priority**: Fast local editor sync above all else. Latency, determinism, and editor-native latency matter more than feature breadth.

---

## 11. Technical Debt & Future Risks

### 11.1 — Asset Identity Mapping Complexity

**Risk**: High · **Timeframe**: Phase 6+

As asset paths (mesh, material) enter the protocol, maintaining consistent identity mappings across save/load cycles becomes complex:

- Blender material names may not map to UE asset paths (name collision, missing asset, renamed)
- FBX reimport can change asset identity if GUIDs are not stable
- Material slots can change ordering between Blender and UE
- User may manually delete LiveSync-generated assets in UE Content Browser

**Mitigations**:
- Deterministic asset paths from GUID (`/Game/LiveSync/Meshes/<GUID>.<GUID>`)
- Asset registry queries verify path existence before assignment
- Missing asset fallback to default cube + validation warning

### 11.2 — UE / Blender Version Compatibility

**Risk**: Medium · **Timeframe**: Ongoing

- UE 5.x API changes can break plugin compilation (each UE upgrade is a potential breaking change)
- Blender 4.x → 5.x Python API changes could break addon functionality
- Socket API changes or removal would require transport layer rewrite

**Mitigations**:
- CI build with multiple UE versions (future)
- Pin minimum supported versions in documentation
- Protocol version as compatibility contract (independent of editor version)

### 11.3 — Animation Authority Conflicts

**Risk**: High · **Timeframe**: Phase 8+

Simultaneous animation from Blender and UE Sequencer creates conflicting authority claims:

- Blender armature drives mesh → UE Sequencer also drives same mesh → conflict
- Blender plays timeline → UE Sequencer plays → which controls transport?
- Keyframes replicated from Blender may not match Sequencer's evaluation

**Mitigations**:
- Configurable transport authority (Blender / UE / Independent)
- Sequencer sync opt-in only (CVar `UE.LiveSync.AutoSyncSequencer`)
- Phase 8 is explicitly labeled "experimental" until authority model is validated

### 11.4 — Scaling Challenges for Large Scenes

**Risk**: Medium · **Timeframe**: Phase 9+

| Scaling Factor | Current Limit | Mitigation |
|---------------|---------------|------------|
| Objects | ~1000 smooth 60 Hz | Delta compression + batching (Phase 9) |
| Network bandwidth | ~8 Mbps at 1000 objects × 60 Hz × 104 bytes | Compression + adaptive rates |
| UE actor management | 10k actors in persistent level | World Partition + actor pooling |
| Blender iteration | Full scene scan every poll | O(1) count diff + periodic full scan |

### 11.5 — Replication Ordering Guarantees

**Risk**: Medium · **Timeframe**: Ongoing

- TCP guarantees ordered delivery within a connection, but packets may arrive in batches
- Multiple transform packets for the same GUID in a single tick: last-writer-wins is correct but may skip interpolation steps
- CREATE packet before parent's CREATE → deferred attachment queue (handled, but adds temporal complexity)
- DELETE + CREATE for the same GUID (rename → regenerate GUID scenario): order matters

**Current handling**: Sequence ID dedup + per-connection monotonicity. CREATE creates or updates, DELETE removes after grace period. Deferred attachments resolve when parent arrives.

### 11.6 — Threading Complexity

**Risk**: Low · **Timeframe**: Ongoing (managed)

| Thread | Current | Risk |
|--------|---------|------|
| Blender main | bpy API access | No change — bpy is single-threaded |
| Blender sender | `socket.sendall` | Blocking on reconnect; mitigated by timeout |
| UE network | `Wait(10ms)` + `Recv` | Must not hold UObject pointers |
| UE game | `Tick()` | Must not block on I/O |
| Editor async tasks | (Phase 6+) FBX reimport | Must not write to game-thread objects |

**Mitigations**: Strict threading model (documented in `AGENTS.md` and `04-threading-model.md`). No UObject access from network thread. Bounded queues for cross-thread communication. Editor async tasks for asset operations.

### 11.7 — Remaining Concern: Variable-Length Protocol Fields (Phase 6+)

Variable-length strings in the protocol (mesh paths, material paths, names) will add parsing complexity when introduced in future subphases:

- Buffer over-read risk if length field is corrupted
- UTF-8 encoding requires validation
- String comparison for change detection is slower than `memcmp` on fixed structs

**Mitigation**: Phase 6A avoids this entirely with fixed-size 33B PT_AssetDef payload. Phase 6+ uses length-prefixed strings with max length check (4096 bytes). UTF-8 validation on receive.

---

## 12. Appendix: Protocol Evolution

| Version | Status | Key Features |
|---------|--------|-------------|
| V2 | Legacy (preserved) | 22-byte header, hex GUID, port 5000 |
| V3 | Production-stable | 24-byte header, binary GUID, packet types, parent field, port 57000 |
| V4 | Production-stable | `PF_HasLocalTransform` flag, `PF_FullSnapshot` flag, snapshot batching (`PT_BeginSnapshot`/`PT_EndSnapshot`), primitive type byte |
| V5 | Phase 5D (stable) | xxHash64-based identity, fixed-size 33B PT_AssetDef payload, backward compatible with V3/V4 |
| V6 | Research (future) | Delta serialization, change masks, keyframe payloads |

### Packet Type Registry

| Byte | Type | Phase | Status |
|------|------|-------|--------|
| 0x01 | PT_Transform | 3 | Production-stable |
| 0x02 | PT_Reserved_02 | 3 | Legacy stub — unused |
| 0x03 | PT_Create | 3 | Production-stable |
| 0x04 | PT_Delete | 3 | Production-stable |
| 0x05 | PT_Material | 3 | Reserved (original stub) |
| 0x06 | PT_Mesh | 3 | Reserved (original stub) |
| 0x07 | PT_Heartbeat | 3 | Production-stable |
| 0x08 | PT_AssetDef | 5D | Active |
| 0x09 | PT_BeginSnapshot | 5A | Production-stable |
| 0x0A | PT_EndSnapshot | 5A | Production-stable |
| 0x0B | PT_Visibility | 6C | Stabilized |
| 0x0C | PT_Rename | 6A/6B | Active (stabilized) |
| 0x0D | PT_Hierarchy | 6D | In Progress (Stage 7) |
| 0x0E | Reserved | — | Future semantic lane |
| 0x0F | Reserved | — | Future semantic lane |
| 0x10 | PT_Timeline | 7 | Research |
| 0x11 | PT_Camera | 7 | Research |
| 0x12 | PT_Keyframe | 7 | Research |

### Flag Registry

| Bit | Flag | Phase | Status |
|-----|------|-------|--------|
| 0x01 | PF_HasLocalTransform | 5B | Production-stable |
| 0x02 | PF_FullSnapshot | 5A | Production-stable |
| 0x04 | PF_RequestAck | 5E | Deferred |
| 0x08 | PF_Compressed | 9 | Planned |

---

*End of consolidated roadmap. Updated 2026-05-30 (Phase 5E complete, Phase 6 ACTIVE: Rename STABILIZED · Visibility STABILIZED · Collection IMPLEMENTED · Hierarchy IN PROGRESS).*
