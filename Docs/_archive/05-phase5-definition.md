# Phase 5 — Feature Completeness & Production Polish

> **DEPRECATED**: This file is superseded by `00-consolidated-roadmap.md` (v1.0, 2026-05-24).
> The consolidated roadmap covers all phases from current state through Phase 10.
> This file is preserved for historical reference only.

**Status**: Planning · **Estimate**: 6–8 days · **Risk**: Medium
**Depends on**: Phase 4 (production hardening) is committed and compiling against UE5.7.

---

## Goal

Close the remaining feature gaps between the current real-time sync system and a production-grade Blender ↔ UE5 pipeline. Phase 5 prioritizes workflow ergonomics (5A), correct hierarchical transform reconstruction (5B), editor-side diagnostics (5C), asset parameter sync (5D), and multi-connection architecture (5E). A new protocol versioning section (5F) is added to safely evolve the wire format ahead of Phase 6's more complex features.

---

## Phase 5A — Workflow & UX

### A1 — Actor Creation UI

**Currently**: `HandleCreateObject()` always spawns `/Engine/BasicShapes/Cube.Cube` — hardcoded, no user control.

**Change**: Add a 1-byte `PrimitiveType` enum field to the V3 CREATE packet payload (appended after parent GUID, before the end of the fixed-size object record).

| Value | Type |
|-------|------|
| 0x00 | Cube (default) |
| 0x01 | Sphere |
| 0x02 | Cylinder |
| 0x03 | Plane |
| 0x04 | Empty (no mesh component — used for hierarchy-only objects) |

**Blender side** (`__init__.py` prefs):
- Add `default_primitive: EnumProperty` in addon preferences (dropdown: Cube/Sphere/Cylinder/Plane/Empty).
- Sidebar panel shows the dropdown under "Actor Spawn Settings".

**UE side** (`UELiveSyncSubsystem.cpp`):
- `HandleCreateObject()` reads the 1-byte enum → `LoadObject<T>` the corresponding `/Engine/BasicShapes/<Name>.<Name>`.
- Empty type → spawn with no mesh component; only root component.

**Rationale**: A 1-byte enum keeps protocol overhead trivial and avoids introducing string-based mesh paths in the hot path. Users who need custom meshes can replace the spawned actors in UE manually — full mesh asset path support is deferred to the FBX pipeline in Phase 5D.

---

| File(s) | What |
|---------|------|
| `__init__.py` | Add `default_primitive` EnumProperty in prefs; add dropdown to sidebar panel |
| `network.py` | Extend `serialize_object_v3()` to accept and append `primitive_type` byte |
| `sync.py` | Pass `primitive_type` from prefs into serialization calls |
| `UELiveSyncSubsystem.cpp` | `HandleCreateObject()` switch on primitive type to load the correct mesh |
| `SyncTypes.h` | Document the 1-byte primitive field position in V3 CREATE object layout |

---

### A2 — GUID Hardening

**Currently**: `ensure_unique_guid()` detects collisions in the `tracked_objects` dict but has no defense against stale GUIDs surviving `.blend` save/load cycles or collisions between objects never simultaneously tracked.

**Change**: Add a persistent **owner hash** stored alongside `ue_guid`.

```python
import hashlib

def _compute_owner_hash(obj):
    raw = f"{obj.name}|{obj.data.name if obj.data else ''}|{uuid.uuid4().hex}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
```

```
obj["ue_guid"]            = uuid.uuid4().hex
obj["ue_guid_owner_hash"] = _compute_owner_hash(obj)
```

| Scenario | Detection | Action |
|----------|-----------|--------|
| Same GUID, same owner hash | Legitimate reload | Keep GUID |
| Same GUID, same obj name, different mesh | Mesh swapped | Keep GUID, regenerate owner hash |
| Same GUID, different obj name | Collision — stale from prior session | Regenerate GUID + owner hash |
| Same GUID, same obj name, same mesh, different `.blend` instance | Extremely rare UUID collision | Regenerate GUID + owner hash |

**Implementation** (`sync.py`):
- `ensure_guid()` extended to also write `ue_guid_owner_hash` on first creation.
- `ensure_unique_guid()` checks `ue_guid_owner_hash` when a collision is found — if hash differs, regenerate GUID and owner hash.
- Add `_reconcile_guids_on_load()` helper called during `start_sync()` and `scan_scene()` to detect stale GUIDs from prior sessions.

**Note**: `hashlib.sha256` is used instead of Python's built-in `hash()` because `hash()` is salted per interpreter session and non-deterministic across restarts. The SHA-256 digest is stable, deterministic, and produces the same result every time the same inputs are hashed.

| File(s) | What |
|---------|------|
| `sync.py:263-290` | Add `_compute_owner_hash()`, extend `ensure_guid()` to write owner hash, extend `ensure_unique_guid()` to validate owner hash on collision |
| `__init__.py` | (optional) Add a "Regenerate All GUIDs" button for manual recovery |

**Rationale**: Without owner hashing, a `.blend` reload with renamed objects can silently map transforms to the wrong UE actors. The hash is cheap (SHA-256 on three short strings, truncated to 16 hex chars = 64 bits of collision resistance) and is never sent over the wire — it is Blender-local state only.

---

### A3 — Rebind Snapshot Batching

**Currently**: A naive "Rebind All" resends all objects as individual CREATE/TRANSFORM packets — no atomic boundary, hierarchy races possible.

**Change**: Introduce two new packet types to bracket a snapshot rebuild:

| Type | Byte | Purpose |
|------|------|---------|
| `PT_BeginSnapshot` | 0x09 | Signals UE to enter snapshot accumulation mode |
| `PT_EndSnapshot`   | 0x0A | Signals UE to flush and apply the batched snapshot |

**Behavior during snapshot**:

**UE side** — When `PT_BeginSnapshot` is received:
- Set `bInSnapshotBuild = true`.
- `ProcessBinaryPacket()`: update `TransformStates` entries but do NOT apply transforms to actors yet.
- `InterpolateTransforms()`: skip all actors whose state was updated within the snapshot window.
- `AttachToParent()`: queue all hierarchy binds into `PendingAttachments` (see 5B2) but do not resolve yet.

When `PT_EndSnapshot` is received:
- Resolve all deferred `PendingAttachments`.
- Apply final transform batch to each actor via `SetActorTransform`.
- Set `bInSnapshotBuild = false`.

**Blender side** (`sync.py`):
- `rebind_all()` operator sends: `PT_BeginSnapshot` → all tracked objects as CREATE packets (with `PF_FullSnapshot` flag) → `PT_EndSnapshot`.
- Existing reconnect snapshot (`check_reconnected()`) uses the same batching when `_runtime_config` has `"use_snapshot_batching": true`.

| File(s) | What |
|---------|------|
| `SyncTypes.h` | Add `PT_BeginSnapshot = 0x09` and `PT_EndSnapshot = 0x0A` constants |
| `network.py` | Add `send_snapshot_batch(objects_data)` — wraps with begin/end markers |
| `sync.py` | `rebind_all()` operator and reconnect-snapshot path use batching |
| `UELiveSyncSubsystem.cpp` | Add `HandleBeginSnapshot()` / `HandleEndSnapshot()` + `bInSnapshotBuild` flag guard |
| `__init__.py` | Add "Rebind All" button in sidebar panel |

**Rationale**: Without batch boundaries, UE starts interpolating and attaching mid-snapshot — children can attach to non-existent parents, snap to origin, then correct when the parent arrives. Batching guarantees atomicity within a single tick's processing window and avoids transient visual artifacts.

---

### A4 — Missing Actor Recovery

**Currently**: No detection. If a UE actor is destroyed (manually by the user, or by GC), the GUID stays in `TransformStates` until the 60s TTL evicts it, with no recovery.

**Change**: Add per-GUID missing-actor detection and auto-recovery.

```cpp
struct FMissingActorState {
    int32 MissingFrames;
    bool bRecoveryAttempted;
    double LastWarningTime;
};

TMap<FGuid, FMissingActorState> MissingActorTracker;
```

| Threshold | Action |
|-----------|--------|
| `MissingFrames == 10` | Log a throttled warning (max once per 30s per GUID) |
| `MissingFrames == 30` | Re-spawn actor via `HandleCreateObject()` using stored transform state |
| `MissingFrames > 60` | Evict from `TransformStates` and `MissingActorTracker` (assume deliberate deletion) |

**Warning throttling**: Use `FMissingActorState::LastWarningTime` — suppress repeated logs within 30s per GUID.

| File(s) | What |
|---------|------|
| `UELiveSyncSubsystem.cpp` | Add `RecoverMissingActors()` called in `Tick()` after `InterpolateTransforms()` |
| `UELiveSyncSubsystem.h` | Add `MissingActorTracker TMap` member + `FMissingActorState` struct |

**Rationale**: Silent desync is worse than an incorrect auto-recovery. The 30-frame wait gives the normal `OnActorSpawned` callback time to fire (in case the actor was recreated by UE itself). After that, we assume Blender still intends the actor to exist.

---

## Phase 5B — Scene Structure

### B1 — Hierarchy Fix

**Currently**: `get_transform()` uses `matrix_local` for parented objects (local transform) and `matrix_world` for roots. UE's `ProcessBinaryPacket()` reconstructs world from local × parent's current world. This is mostly correct but has one flaw: `InterpolateTransforms()` sets the actor's world transform directly while the actor is also parent-attached, causing the attachment system and the interpolation to fight.

**Change**: In `InterpolateTransforms()`, skip world transform writes for actors with a valid parent attachment. The child's world position is **driven entirely by the parent's UE attachment system**. Only the child's **relative (local)** offset is stored for velocity estimation.

| Step | What happens |
|------|-------------|
| 1 | Packet arrives with child's local transform |
| 2 | `ProcessBinaryPacket()` computes child's world position: `ChildLocal × ParentWorld` |
| 3 | `SetActorTransform(ChildWorld)` applied once on packet receipt |
| 4 | `AttachToActor(Parent, KeepWorldTransform)` — child now moves with parent automatically |
| 5 | `InterpolateTransforms()`: if `State.bHasParent && ParentGuid.IsValid()` → skip `SetActorTransform` entirely |

**Velocity estimation**: The child's `CurrentLocation`/`CurrentRotation`/`CurrentScale` state vectors are still advanced toward `TargetLocation` internally for the next packet's delta computation, but the results are NOT written to the actor transform when parented.

| File(s) | What |
|---------|------|
| `UELiveSyncSubsystem.cpp:1733-1900` | `InterpolateTransforms()`: guard world transform write behind `!State.bHasParent` |
| `UELiveSyncSubsystem.cpp:1511-1724` | `UpdateTargetTransform()`: ensure `bHasParent` is set early enough for the interp guard |
| `SyncTypes.h` | No new fields needed — existing `bHasParent + ParentGuid` are sufficient |

**Rationale**: UE's built-in scene hierarchy attachment is more efficient and correct than manual world-space interp for children. The patch is minimal — only the `InterpolateTransforms()` write path needs a single guard condition.

---

### B2 — Deferred Attachments

**Currently**: `AttachToParent()` logs "parent not yet cached" and returns silently. Parent may arrive in a later packet; child never retries.

**Change**: Introduce a structured pending-attachment tracker.

```cpp
struct FPendingAttachment
{
    FGuid Child;
    FGuid Parent;
    int32 RetryFrames;
    double CreatedTime;
};

TArray<FPendingAttachment> PendingAttachments;
```

**Retry cadence**:

| Frame range | Retry frequency |
|-------------|-----------------|
| 0–9 (first 10 frames) | Every tick |
| 10–59 | Every 5 ticks |
| 60+ | Timeout — evict entry |

**Timeout behavior**:
- After 60 total retry frames (~1s at 60fps), the entry is evicted from `PendingAttachments`.
- The actor is detached from any parent (attached to world root).
- A throttled warning is logged (max once per 10s across all GUIDs).

**Cleanup**:
- `PendingAttachments` is scanned and stale entries evicted each tick.
- On successful attachment, entry is removed immediately.
- On actor deletion, any pending entry referencing that GUID is removed.

| File(s) | What |
|---------|------|
| `UELiveSyncSubsystem.cpp` | Replace the single log-and-return in `AttachToParent()` with `FPendingAttachment` push; add `ResolvePendingAttachments()` called each tick |
| `UELiveSyncSubsystem.h` | Add `FPendingAttachment` struct, `PendingAttachments TArray`, `ResolvePendingAttachments()` method |

**Rationale**: The current silent-fail approach means children spawned before their parent never attach. A bounded retry queue with exponential backoff (every tick → every 5 ticks) solves this deterministically without wasting CPU on persistent retries.

---

### B3 — Transform Scope Reduction

**Explicitly deferred to Phase 6+**:
- Armature/deformation support
- Pose-space (`matrix_basis`) transforms
- Skeletal mesh sync
- Non-uniform scale hierarchies

**Phase 5 scope**:
- `matrix_world` for root objects (unchanged)
- `matrix_local` for parented objects (unchanged)

**Rationale**: Skeletal animation sync introduces nonlinear scale chains, bone-space transforms, and pose blending that require their own protocol design. Adding pose-space support alongside the hierarchy fix would triple the testing surface with no immediate production need.

---

## Phase 5C — Diagnostics

### C1 — Status Bar Widget (re-do)

**Currently**: Was removed in the Phase 4 compile fix because `UStatusBarSubsystem::AddStatusBarWidget` does not exist in UE5.7.

**Change**: Implement via `RegisterGlobalStatusBarExtension` — the correct API for adding persistent widgets to the UE editor status bar.

```cpp
class FLiveSyncStatusBarExtension : public IGlobalStatusBarExtension
{
    virtual void GenerateWidget(TArray<TSharedRef<SWidget>>& OutExtensions) override;
};
```

| Component | Behavior |
|-----------|----------|
| Icon | Green circle when connected, red when disconnected, gray when not started |
| Text | Brief summary: "Sync: 42 objects" or "Disconnected" |
| Click | Opens the Live Sync Status tab (registered nomad tab spawner) |
| Refresh | Cached `FText` values, updated at most every 250ms |

**Refresh throttling**: Store `double LastWidgetRefreshTime` in the extension instance. Skip `Tick` polling if < 250ms has elapsed. Use `FTimerManager` or a simple frame counter.

| File(s) | What |
|---------|------|
| `UELiveSyncEditorModule.cpp` | Register `FLiveSyncStatusBarExtension` in `StartupModule()`, unregister in `ShutdownModule()` |
| `Public/LiveSyncStatusBarExtension.h` | Declare `FLiveSyncStatusBarExtension : IGlobalStatusBarExtension` |
| `Private/LiveSyncStatusBarExtension.cpp` | Implement `GenerateWidget()` with cached `FText`, refresh throttle, click-to-open-tab |

**Rationale**: Slate updates on every tick (even cached ones) trigger layout passes. 4 Hz refresh is more than adequate for connection status — the user does not need frame-perfect accuracy on a status bar icon.

---

### C2 — Live Stats (existing tab enhancements)

Keep the existing `SLiveSyncStatusWidget` tab. Add real-time metrics:

| Metric | Source | Exposed via |
|--------|--------|-------------|
| Packets/sec | `Stats.PacketsReceived` rate | `GetPacketsPerSecondText()` |
| Bytes/sec | `Stats.TotalBytesReceived` rate | `GetBytesPerSecondText()` |
| Dropped count | `Stats.PacketsDropped` | `GetDroppedPacketsText()` |
| Avg process time | `Stats.AvgProcessTimeMs` | `GetAvgProcessTimeText()` |

Same 250ms refresh throttle as the status bar widget.

| File(s) | What |
|---------|------|
| `UELiveSyncSubsystem.h` | Add public `GetPacketsPerSecondText()`, `GetBytesPerSecondText()`, etc. |
| `UELiveSyncSubsystem.cpp` | Compute rate metrics in `LogRuntimeMetrics()` or a new `UpdateMetrics()`; store for polling |
| `SLiveSyncStatusWidget.cpp` | Poll new metrics and display in the tab |

---

### C3 — Debug Overlay (CVar)

| CVar | Default | Description |
|------|---------|-------------|
| `UE.LiveSync.DebugOverlay` | 0 | Draw on-screen debug text in PIE/editor viewport |

When enabled, draw via `DrawDebugString()` or `GEngine->AddOnScreenDebugMessage()`:

```
[LiveSync] Connected | 42 objects | Queue: 0 | 120 pkts/s
```

| File(s) | What |
|---------|------|
| `UELiveSyncSubsystem.cpp` | Add `CVarLiveSyncDebugOverlay`; call `DrawDebugOverlay()` in `Tick()` |
| `UELiveSyncSubsystem.h` | Add `DrawDebugOverlay()` private method |

---

## Phase 5D — Asset Layer

### D1 — Material Parameter Sync (renamed from "Material Sync")

**Packet type**: `PT_MaterialParams = 0x0B` (new ID — 0x05 is reserved from the original protocol stub but we use a fresh ID to avoid ambiguity).

**Scope limitation**: Only sync **dynamic material instance parameters**. No shader graph creation, no material asset generation, no slot management.

| Parameter | Type | Bytes |
|-----------|------|-------|
| Base Color | `float[4]` (RGBA) | 16 |
| Metallic | `float` | 4 |
| Roughness | `float` | 4 |
| (reserved) | `uint8[4]` | 4 |
| **Total** | | **28** |

**Payload layout** (per object):
```
GUID (16 bytes) + BaseColor (4×float) + Metallic (float) + Roughness (float) + Reserved (4 bytes) = 40 bytes
```

**Blender side**:
- `collect_material_params(obj)` reads the active material's `Principled BSDF` node inputs: `Base Color`, `Metallic`, `Roughness`.
- If no Principled BSDF node exists, use fallback defaults (white color, 0 metallic, 0.5 roughness).
- `serialize_material_params(guid_obj, color, metallic, roughness)` packs the 40-byte record.
- Sent every N frames (CVar `UE.LiveSync.MaterialSyncInterval`, default 30 frames = ~0.5s) when parameters have changed.

**UE side**:
- `HandleMaterialParams(FGuid, FLinearColor, float, float)`:
  1. Find actor via `FindActorFast(Guid)`.
  2. Get first `UMeshComponent`.
  3. If no MID exists → `CreateDynamicMaterialInstance()` on the component.
  4. `MID->SetVectorParameterValue("BaseColor", Color)`.
  5. `MID->SetScalarParameterValue("Metallic", Metallic)`.
  6. `MID->SetScalarParameterValue("Roughness", Roughness)`.
- Standard parameter names (`BaseColor`, `Metallic`, `Roughness`) — users can target these with any material graph.

**Do NOT**:
- Sync material slots (multiple materials per mesh).
- Create new material assets.
- Recreate shader graphs.
- Sync texture paths.

| File(s) | What |
|---------|------|
| `network.py` | Add `serialize_material_params(guid_obj, color, metallic, roughness)` |
| `sync.py` | Add `collect_material_params(obj)` + change-detection logic; integrate into `check_updates()` loop |
| `UELiveSyncSubsystem.cpp` | Add `HandleMaterialParams()` + `PT_MaterialParams` case in `ProcessBinaryPacket()` |
| `SyncTypes.h` | Add `PT_MaterialParams = 0x0B` constant |

**Rationale**: Material parameter sync covers 90% of common needs (color + metalness + roughness) with < 30 bytes per object. Full material asset management would require material compilation on UE side, shader model compatibility checks, and complex slot management — all beyond Phase 5's stability mandate.

---

### D2 — FBX Mesh Push Pipeline

**Design**: Blender exports each mesh as FBX → writes to a known project path → UE auto-reimports via `FAssetRegistryModule` or directory watcher.

**Asset identity strategy**:

```
Each Blender GUID maps deterministically to:
  /Game/LiveSync/Meshes/<GUID>.<GUID>
```

| Requirement | Implementation |
|-------------|---------------|
| Overwrite existing assets | Write FBX to same path; trigger reimport via `UAutoReimportManager` |
| Preserve actor references | Actors reference mesh via GUID path; reimport preserves asset identity |
| Preserve material assignments | If slot count matches, reimport preserves assigned materials |
| Avoid duplicate asset generation | Deterministic path from GUID → one asset per GUID |
| Handle renames | Blender name changes don't affect GUID → no rename needed |

**Blender side** (`sync.py`):
- `export_mesh_fbx(obj, output_dir)` — exports FBX with embed textures, triangulate, scale = 100.
- Called when mesh is first tracked (CREATE) or when `obj.data` polygon count changes (detected via `is_modified` check).
- Configurable export path in prefs (default: `../../Content/LiveSync/Meshes/` relative to `.blend` file).

**UE side**:
- `HandleMeshUpdate(FGuid Guid, FString FilePath)` — queue the reimport request onto an **editor async task** to avoid blocking the game thread.
- On CREATE for an actor whose mesh GUID already has an FBX → assign the existing mesh directly (no reimport needed).
- Reimport via `FAssetRegistryModule::Get().GetAssetByObjectPath()` + `UAssetEditorSubsystem` or `UAutoReimportManager::Tick()`.

```cpp
// Queue reimport onto editor thread:
Async(EAsyncExecution::TaskGraphMainThread, [FilePath]()
{
    UAutoReimportManager* ReimportManager = GEditor->GetEditorSubsystem<UAutoReimportManager>();
    if (ReimportManager)
    {
        ReimportManager->ReimportAssetWithNewFile(FilePath);
    }
});
```

**Note on threading**: The reimport request must NOT run on the game thread (where `Tick()` executes). FBX import can take hundreds of milliseconds for complex meshes. Queue onto `EAsyncExecution::TaskGraphMainThread` with appropriate priority.

**Explicitly deferred to Phase 6**:
- Binary mesh streaming over TCP.
- Incremental mesh deltas.
- High-poly mesh support (10M+ triangles).
- LOD generation.

| File(s) | What |
|---------|------|
| `sync.py` | Add `export_mesh_fbx()` + `check_mesh_modified()` + `_mesh_export_queue` background thread |
| `network.py` | Add `send_mesh_update(guid_obj, fbx_path_str)` — new packet type `PT_MeshUpdate = 0x0C` (GUID + path string) |
| `UELiveSyncSubsystem.cpp` | Add `HandleMeshUpdate()` — queue async reimport |
| `SyncTypes.h` | Add `PT_MeshUpdate = 0x0C` constant |
| `__init__.py` | Add `mesh_export_path` string property in prefs |

**Rationale**: FBX is the lowest-risk mesh pipeline that integrates with UE's existing import infrastructure. Writing a custom binary mesh streamer would require significant testing for edge cases (non-manifold, degenerate tris, UV sets, vertex colors) that Blender's FBX exporter already handles. The async reimport queue prevents frame hitching.

---

## Phase 5E — Advanced Networking

### E1 — Connection Context Abstraction

**Do not** use `TArray<FSocket*>` alone — that would force scattered per-connection state across multiple ad-hoc maps.

```cpp
struct FLiveSyncConnectionContext
{
    FSocket* Socket;
    FLiveSyncQueue Queue;
    uint32 ConnectionId;
    double LastHeartbeatTime;
    double ConnectTime;
    int32 PacketsReceived;
    int32 PacketsDropped;
    uint64 LastSequenceId;               // per-connection sequence tracking
    bool bAuthenticated;                 // reserved for Phase 6 auth
};

TArray<TSharedPtr<FLiveSyncConnectionContext>> Connections;
```

**LastSequenceId moved per-connection**: Previously `LastSequenceId` was a single member on the subsystem. With multiple connections, each connection has its own monotonically increasing sequence space. Sequence deduplication must check `Connections[Idx].LastSequenceId`, not a global value.

**Listener behavior**:
- `StartServer()` accepts connections, wraps each in `FLiveSyncConnectionContext`, assigns incremental `ConnectionId`.
- On accept: log "New connection #N from <IP>".

**Processing**:
- `ProcessQueuedPackets()` iterates `Connections` and drains each queue.
- If a connection's `Socket` is closed → log, drain remaining packets, remove from array.

**Future-proof fields**:
- `bAuthenticated`: reserved for a future authentication handshake.
- `PacketsReceived / PacketsDropped`: per-client stats for diagnostics.
- `LastHeartbeatTime`: per-connection heartbeat timeout.

| File(s) | What |
|---------|------|
| `Public/LiveSyncConnectionContext.h` (new) | Define `FLiveSyncConnectionContext` struct |
| `UELiveSyncSubsystem.h` | Replace `ConnectionSocket*` + `PacketQueue` + `LastSequenceId` with `TArray<TSharedPtr<FLiveSyncConnectionContext>>` |
| `UELiveSyncSubsystem.cpp` | Update `StartServer()`, `StopNetworkThread()`, `ProcessQueuedPackets()`, `LiveSyncRunnable` reference to use connection context array |
| `LiveSyncRunnable.cpp/.h` | Accept `TSharedPtr<FLiveSyncConnectionContext>` array; enqueue into per-connection `Queue` |

**Rationale**: A flat `TArray<FSocket*>` creates painful maintenance when adding per-connection heartbeat, stats, or throttling. The struct bundles all per-connection state and can be extended without touching every iteration site.

---

### E2 — Authority Model

**Implementation**: **Last-writer-wins** (matches current single-connection behavior).

- `ProcessBinaryPacket()` processes packets from all connections in sequence.
- If two connections send a TRANSFORM for the same GUID in the same tick, the last one processed wins.
- No locking or ownership tracking needed.
- Sequence ID deduplication is per-connection (see E1).

**Future alternatives** (documented but not implemented):

| Model | Description |
|-------|-------------|
| First-writer ownership | First connection to claim a GUID owns it until disconnect |
| Priority ownership | Connection #1 > #2 > #3 in tiebreaker |
| GUID namespace partitioning | Connection #1 owns GUIDs A–M, #2 owns N–Z |

---

### E3 — Remove ACK Handshake from Phase 5

**Decision**: Do NOT implement:
- Blender receive thread
- ACK packet type (`PT_Ack`)
- Bidirectional handshake protocol

**Keep**: Immediate full snapshot after `connect()` (current behavior, already stable).

**Rationale**: Adding a receive thread to Blender introduces threading complexity (`bpy` API access from receive thread is forbidden), error-recovery paths, and a new packet type with no production-tested handler. The immediate snapshot works and has been stable through Phases 3–4. Defer handshake protocol to Phase 6 when multi-connection has more real-world usage data to inform the design.

---

## Phase 5F — Protocol Versioning & Compatibility (NEW)

### F1 — Protocol Version Field

The V3 header already has a `Version` field. What is missing is a formal **version support contract**.

| Version | Meaning |
|---------|---------|
| 2 | Legacy V2 (GUID as hex string, no parent, no type discriminator) |
| 3 | Current V3 (binary GUID, parent, packet types, flags) |
| 4 | Future V4 (Phase 5 additions: snapshot batching, material params, mesh updates) |

```cpp
static constexpr uint16 CURRENT_PROTOCOL_VERSION = 4;
```

### F2 — Graceful Unknown Packet Skipping

Add a switch guard in `ProcessBinaryPacket()` so unknown packet types are safely skipped:

```cpp
switch (PacketType)
{
case PT_Transform:
case PT_Create:
case PT_Delete:
case PT_Heartbeat:
    // Phase 3-4 handlers
    break;

case PT_BeginSnapshot:
case PT_EndSnapshot:
    // Phase 5A — Snapshot batching
    break;

case PT_MaterialParams:
    // Phase 5D — Material parameter sync
    break;

case PT_MeshUpdate:
    // Phase 5D — FBX mesh reimport notification
    break;

default:
    UE_LOG(LogLiveSync, Warning,
        TEXT("Unknown packet type 0x%02X — skipping"), PacketType);
    break;
}
```

### F3 — Compatibility Validation

On first packet receipt from a connection:

```cpp
if (HeaderV3.Version > CURRENT_PROTOCOL_VERSION)
{
    UE_LOG(LogLiveSync, Error,
        TEXT("Blender sent protocol v%d, this build supports v%d. Closing connection."),
        HeaderV3.Version, CURRENT_PROTOCOL_VERSION);
    // close the connection socket
    return;
}

if (HeaderV3.Version < LIVE_SYNC_VERSION_V3)
{
    UE_LOG(LogLiveSync, Warning,
        TEXT("Blender connected with legacy v%d protocol — limited feature set"),
        HeaderV3.Version);
}
```

### F4 — Sequence ID Rules

- Sequence IDs are monotonically increasing **per connection**.
- Each `FLiveSyncConnectionContext` tracks its own `LastSequenceId`.
- UE ignores packets with `SequenceId <= ConnectionCtx.LastSequenceId` (duplicate or out-of-order).
- On disconnect/reconnect, the new connection's sequence resets to 0.
- Sequence ID overflow (after 2^64 packets) is not a practical concern.

| File(s) | What |
|---------|------|
| `SyncTypes.h` | Add `LIVE_SYNC_VERSION_V4 = 4` constant; add packet type constants for 0x09–0x0C |
| `UELiveSyncSubsystem.cpp` | Add version check guard + unknown-packet-type default handler |
| `UELiveSyncConnectionContext.h` | Move `LastSequenceId` into `FLiveSyncConnectionContext` (see 5E1) |

**Rationale**: As the protocol gains new packet types (snapshot batching, material params, mesh updates), the version field becomes the safety net that prevents a Phase 6 Blender from crashing a Phase 5 UE editor. The unknown-packet-skip pattern ensures forward compatibility — newer Blender clients sending unfamiliar packet types will be safely ignored by older UE builds.

---

## Ordering

| Step | Phase | Why |
|------|-------|-----|
| 1 | **5A** first | Workflow fixes are user-facing and touch the least risky code paths (GUID, prefs, rebind) |
| 2 | **5F** early | Protocol versioning must be in place before any new packet types ship |
| 3 | **5B** second | Hierarchy fixes need stable GUIDs (5A) and version guards (5F) |
| 4 | **5C** third | Diagnostics need 5A–5B's subsystem state to display meaningful values |
| 5 | **5D** fourth | Asset layer is additive; material params depend on 5F's version guard |
| 6 | **5E** last | Multi-connection is highest risk; defer until all other subsystems are stable |

---

## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| Snapshot batching (A3) increases memory during rebuild | Low | Medium | Process batch in-place; no persistent buffer needed |
| GUID owner hash (A2) regresses existing GUIDs on reload | Medium | Low | Hash mismatch triggers regeneration, not error |
| Material params (D1) on actors without MID crashes | Low | High | Guard: `if (!MeshComp) return`; create MID lazily via `CreateDynamicMaterialInstance()` |
| FBX pipeline (D2) blocks game thread | Medium | High | Queue reimport onto editor async task (`EAsyncExecution::TaskGraphMainThread`) |
| Multi-connection queue starvation (E1) | Low | Medium | Round-robin with `MaxPacketsPerConnection` limit per tick |
| Deferred attachment (B2) retry CPU cost | Low | Low | `PendingAttachments.Num()` is bounded by total tracked objects; backoff after 10 frames |
| Status bar widget compile failure (C1) | Low | Medium | Wrap in `WITH_EDITOR`; stub extension class when `IGlobalStatusBarExtension` unavailable |
| Protocol version mismatch kills existing sessions (F3) | Low | High | Logged as Error but degrade gracefully — close only the misbehaving connection |

---

## Deferred to Phase 6

| Feature | Reason |
|---------|--------|
| Binary mesh streaming over TCP | Beyond Phase 5 stability scope; FBX pipeline covers the immediate need |
| Armature / skeletal mesh sync | Requires pose-space transforms and bone remapping |
| Pose-space (`matrix_basis`) transforms | Not needed until skeletal sync |
| Bidirectional handshake / ACK packets | Blender receive thread adds threading risk with no production-proven benefit |
| First-writer authority model | Not needed until multi-connection usage data exists |
| Material slot management | Rarely needed; dynamic material instances cover common cases |
| Packet compression (zlib) | Bandwidth not yet a bottleneck at 100–300 objects |
| Multiple material slot sync | Requires slot-index remapping across Blender ↔ UE |
| Material asset generation | Too risky; crosses into shader compilation territory |
| Incremental mesh deltas | FBX full-export is fast enough for expected scene sizes |
