# Phase 7D — Active Camera Sync Scope Lock

**Date**: 2026-06-03  
**Status**: IMPLEMENTED  
**Depends on**: Phase 7C (Playback Sync) ✅  
**Blocks**: Phase 7E (Keyframe Replication) — camera sync provides viewport context for Sequencer playback  
**P2P Priority**: High — recommended next per STATUS.md closeout analysis

---

## Table of Contents

1. [Purpose](#1-purpose)
2. [Blender Side Investigation](#2-blender-side-investigation)
3. [Unreal Side Investigation](#3-unreal-side-investigation)
4. [Semantic Model](#4-semantic-model)
5. [Wire Proposal](#5-wire-proposal)
6. [Packet Ownership Definition](#6-packet-ownership-definition)
7. [Failure-Mode Analysis](#7-failure-mode-analysis)
8. [Acceptance Criteria](#8-acceptance-criteria)
9. [Implementation Stages](#9-implementation-stages)
10. [Explicitly Out of Scope](#10-explicitly-out-of-scope)
11. [Risks & Mitigations](#11-risks--mitigations)
12. [Files Touched](#12-files-touched-estimated)

---

## 1. Purpose

Phase 7D defines the architecture for synchronising **which camera is active** between Blender and Unreal Engine 5.

After Phase 7C, UELiveSync can synchronise playback state (play/pause/stop). Phase 7D adds the ability to replicate Blender's active camera selection to UE's editor viewport.

This scope lock exists to:

1. **Define the minimal wire format** — Camera GUID only. No transform, no FOV, no focal length, no DOF. Phase 7D replicates the *selection*, not the *properties*.
2. **Prevent scope creep** — Camera sync is a common gateway to lens settings, film back, focus distance, and Sequencer cuts. This document fences those as out of scope.
3. **Establish the semantic model** — What "active camera" means on each side, how ownership works, and what happens when the camera actor does not exist on the UE side.
4. **Provide clear "done" definitions** — Each implementation stage has measurable exit criteria.

### 1.1 — Relationship to Wider Phase 7

| Domain | Packet | Phase | Dependency |
|--------|--------|-------|------------|
| Timeline (playhead position) | `PT_Timeline (0x13)` | 7B | Independent |
| Playback state (play/pause/stop) | `PT_PlaybackState (0x14)` | 7C | ✅ Done |
| **Active camera (this doc)** | **`PT_ActiveCamera (0x15)`** | **7D** | **Scope lock** |
| Camera parameters (FOV, focal, DOF) | `PT_CameraDef (0x16)` | 7D.2 | Deferred |
| Keyframe replication | `PT_Keyframe (0x17)` | 7E | Requires 7D camera actors |
| Sequencer operations | `PT_SequencerOp (0x18)` | 7F | Requires 7E keyframe data |

---

## 2. Blender Side Investigation

### 2.1 — Active Scene Camera

Blender stores the active scene camera at `bpy.context.scene.camera`. This is a `bpy.types.Object` reference (or `None` if no camera is assigned).

| Property | Path | Type | Mutability |
|----------|------|------|------------|
| Active scene camera | `scene.camera` | `bpy.types.Object` or `None` | User-assignable in Properties > Scene > Camera |
| Camera object type | `obj.type` | `'CAMERA'` | Set at creation; immutable after |
| Camera data | `obj.data` | `bpy.types.Camera` | Datablock shared across objects |
| Camera name | `obj.name` | `str` | User-renameable |
| Camera GUID | `obj["ue_guid"]` | `str` (UUID) | Set by ensure_unique_guid() |

**Detection strategies:**

1. **Poll `scene.camera`** — The simplest approach. Read `bpy.context.scene.camera` each sync tick and compare to cached value.
2. **Depgraph update hook** — `depsgraph_update_post` fires when scene.camera is reassigned. Already used by existing sync for transform detection.
3. **Frame change handler** — `frame_change_pre/post` fires on every frame step, including during playback when camera cuts may occur.

**Recommendation**: Poll `scene.camera` in `check_updates()`, same cadence as playback detection. This is a single pointer comparison (nanoseconds) and does not need a dedicated handler.

### 2.2 — Viewport Camera Override

Blender has multiple viewport camera modes that override `scene.camera`:

| Mode | Detection | Camera Source | Sync Behaviour |
|------|-----------|---------------|----------------|
| **Camera view** (Numpad 0) | `bpy.context.space_data.region_3d.view_perspective == 'CAMERA'` | `scene.camera` | ✅ Sync normally |
| **Local view** (`/` key) | `bpy.context.space_data.local_view` is not None | Isolates subset; camera assignment is scene-global | ⚠️ Do NOT sync — local view is editor-only, not a scene camera change |
| **Orbit / Walk / Fly** | `view_perspective == 'PERSP'` or `'ORTHO'` | No camera override | ✅ Sync `guid=0` (no camera active); UE reverts to free viewport |
| **Active camera in local view** | `scene.camera` is set + local_view active | User may have overridden camera while isolated | ⚠️ Deferred — local view camera state is transient |

**Recommendation**: Poll `scene.camera` only. Ignore `local_view` state — local view is a transient editor affordance that should not be replicated. If `scene.camera` is `None`, send a null GUID (all zeros) to tell UE to release camera lock.

### 2.3 — Camera Object GUID

Every tracked Blender object has a GUID stored as `obj["ue_guid"]` (string UUID format). The camera is a regular tracked object — it goes through the same `PT_Create` / `PT_AssetDef` / `PT_Transform` lifecycle as meshes and empties.

**Key invariant**: A camera object MUST exist in the UE scene (created via `PT_Create`) before `PT_ActiveCamera` can reference it. The camera object is tracked in `tracked_objects` and has a known GUID.

### 2.4 — Camera Switching Detection

Camera switches are discrete events:

1. User selects a different camera in Scene > Camera property
2. User creates a new camera (auto-assigned as active by Blender)
3. User deletes the active camera (Blender sets `scene.camera = None`)
4. User presses Numpad 0 to enter/exit camera view

**Detection in `check_updates()`**:

```
current_camera = bpy.context.scene.camera
current_guid = get_ue_guid(current_camera) if current_camera else NULL_GUID

if current_guid != _last_active_camera_guid:
    send PT_ActiveCamera(current_guid, sequence, timestamp)
    _last_active_camera_guid = current_guid
```

This is a one-line pointer comparison per tick.

### 2.5 — Scene Camera Changes

When `scene.camera` is reassigned to a different camera object:
- Blender fires `depsgraph_update_post` (already hooked)
- Next `check_updates()` tick detects the GUID change
- `PT_ActiveCamera` packet is emitted

**No special handler needed** — the poll-in-loop pattern in `check_updates()` catches this naturally.

### 2.6 — Camera Deletion / Replacement Behaviour

| Action | Blender Behaviour | Sync Implication |
|--------|-------------------|------------------|
| Delete active camera | Blender sets `scene.camera = None` | Send `PT_ActiveCamera` with null GUID |
| Delete inactive camera | `scene.camera` unchanged | No camera switch packet; existing `PT_Delete` lifecycle handles removal |
| Replace camera datablock (link/replace) | Object identity unchanged; datablock swapped | No camera switch — same object, same GUID |
| New camera created | Blender auto-assigns if no active camera exists | `PT_Create` lifecycle + `PT_ActiveCamera` with new GUID |
| Undo camera delete | Camera restored; `scene.camera` may be restored | State-driven re-sync on next poll |

### 2.7 — Blender Side Summary

| Capability | Available | Notes |
|------------|-----------|-------|
| Read active camera | ✅ `scene.camera` | Returns Object or None |
| Read camera GUID | ✅ `obj["ue_guid"]` | String UUID, already set by ensure_unique_guid |
| Detect camera switch | ✅ Poll in check_updates | Single pointer comparison per tick |
| Detect camera delete | ✅ Existing PT_Delete pipeline | Handled by lifecycle sync |
| Viewport camera mode | ⚠️ Partial | Local view detection possible but deferred |
| Null camera (no active) | ✅ `scene.camera == None` | Maps to null GUID |
| Multiple viewports | ⚠️ Partial | Only primary scene camera is synced |

---

## 3. Unreal Side Investigation

### 3.1 — Editor Active Camera

In UE Editor, the "active camera" is determined by the Editor Viewport's view target:

| API | Header | Purpose |
|-----|--------|---------|
| `EditorViewportClient::SetViewTarget()` | `EditorViewportClient.h` | Sets the actor the viewport looks through |
| `EditorViewportClient::GetViewTarget()` | `EditorViewportClient.h` | Returns current view target actor |
| `EditorViewportClient::ViewFOV` | `EditorViewportClient.h` | Current viewport FOV (may be from camera component) |
| `GEditor->GetActiveViewport()` | `EditorEngine.h` | Currently focused viewport |
| `GEditor->GetAllViewportClients()` | `UnrealClient.h` | All viewport clients (for multi-monitor) |

**Critical constraint**: `SetViewTarget()` is editor-only. At runtime (standalone game), viewport control is different. Phase 7D targets **editor viewport sync only**.

### 3.2 — CameraActor

`ACameraActor` is a built-in UE5 class:

```cpp
class ACameraActor : public AActor
{
    // Contains a UCameraComponent
    UCameraComponent* GetCameraComponent() const;
};
```

- Can be placed in any level
- Has a `UCameraComponent` that defines FOV, post-process, etc.
- Works with `SetViewTarget()` in both editor and runtime
- Has a `Tags` array for GUID matching (existing `LIVE_SYNC_GUID_TAG` pattern)

**Current behaviour**: When `PT_Create` spawns an `AActor`, it creates a `AActor` with `UStaticMeshComponent` or `UProceduralMeshComponent`. For camera objects, we need to spawn `ACameraActor` instead.

**Detection mechanism**: A new primitive type OR implicit detection via object type flag in `PT_Create` determines whether to spawn `ACameraActor` vs `AActor`.

### 3.3 — CineCameraActor

`ACineCameraActor` extends `ACameraActor` with `UCineCameraComponent`:

```cpp
class ACineCameraActor : public ACameraActor
{
    UCineCameraComponent* GetCineCameraComponent() const;
};
```

`UCineCameraComponent` adds:
- Focal length (mm)
- Sensor size (width/height)
- Focus distance
- Aperture
- Current aperture
- Film back presets

**Phase 7D uses `ACameraActor`** — not `ACineCameraActor`. The extra parameters of `CineCameraComponent` are out of scope (deferred to Phase 7D.2 or 7E). Using base `ACameraActor` keeps the scope minimal.

### 3.4 — Viewport Camera Control

The UE side needs to:

1. **Set viewport to look through a camera**:
   ```cpp
   // Editor viewport
   if (FViewport* ActiveViewport = GEditor->GetActiveViewport())
   {
       if (FEditorViewportClient* ViewportClient = (FEditorViewportClient*)ActiveViewport->GetClient())
       {
           ViewportClient->SetViewTarget(CameraActor);
       }
   }
   ```

2. **Release camera lock** (null GUID received):
   ```cpp
   ViewportClient->SetViewTarget(nullptr);
   // This returns the viewport to free-fly orbit mode
   ```

3. **Find camera actor by GUID**:
   ```cpp
   AActor* FindCameraByGuid(const FGuid& Guid)
   {
       // Iterate all actors with LIVE_SYNC_GUID_TAG
       // Match against Guid.ToString()
   }
   ```

### 3.5 — Camera Lookup by GUID

The existing GUID-tag system (`LIVE_SYNC_GUID_TAG`) stores the GUID string on every spawned actor. Camera lookup follows the same pattern as any actor lookup:

```cpp
FString GuidStr = Guid.ToString(EGuidFormats::DigitsWithHyphens);
// Search tracked actors by this GUID tag
```

If the camera's `PT_Create` was already processed, the actor exists in the `LiveSyncActors` map and can be found by GUID.

### 3.6 — Missing Camera Behaviour

**Scenario**: `PT_ActiveCamera` arrives referencing a GUID that does not correspond to any spawned actor.

**Causes**:
1. `PT_Create` was never received for that camera (network loss, packet dropped, out-of-order delivery)
2. Camera was deleted on Blender side but delete packet not yet processed
3. Reconnect snapshot races: `PT_ActiveCamera` arrives before camera's `PT_Create` during snapshot replay

**Safety rule**: If the camera actor cannot be found by GUID, UE MUST:
1. Log a warning: `"[CAMERA] Active camera GUID=%s not found in scene"`
2. Set viewport to free-fly (null target) — do NOT crash
3. Increment a `CameraMissingGuid` diagnostic counter
4. Accept future `PT_ActiveCamera` if the actor becomes available (ordering-tolerant)

**Do NOT**:
- Block or wait for the actor to appear
- Spawn a placeholder actor
- Crash or assert

---

## 4. Semantic Model

### 4.1 — What "Active Camera" Means

| Context | Definition | Owner |
|---------|------------|-------|
| Blender scene camera | The camera object assigned to `scene.camera` | Scene data |
| Blender viewport camera | The camera currently used for viewport rendering | Editor view (may differ from scene.camera in local view) |
| UE active camera | The viewport `ViewTarget` actor | Editor viewport client |
| Sync semantic | Blender's `scene.camera` → UE's editor viewport `SetViewTarget()` | Blender-owned, unidirectional |

**Phase 7D syncs the following equivalence**:
```
Blender scene.camera == UE EditorViewportClient::ViewTarget
```

### 4.2 — Ownership Model

| Aspect | Owner | Rationale |
|--------|-------|-----------|
| Active camera identity | **Blender** | Blender is the authoring environment; UE is the display target |
| Camera actor lifecycle | **Blender** (via PT_Create/PT_Delete) | Camera objects are created/deleted in Blender; UE mirrors |
| Viewport release | **UE** (automatic) | When Blender has no active camera (`scene.camera == None`), UE releases lock independently |
| Camera parameters | **Deferred** | FOV, focal, sensor, DOF are Phase 7D.2 or 7E |

**Unidirectionality**: Phase 7D is strictly Blender→UE. There is no UE→Blender camera sync. If an artist creates a camera in UE Sequencer, it does not propagate to Blender.

### 4.3 — Event-Driven vs State-Driven

| Packet | Model | Rationale |
|--------|-------|-----------|
| `PT_ActiveCamera` | **Event-driven** (send on change only) | Camera switches are infrequent (seconds to minutes apart). No benefit to periodic broadcast. |
| Null camera (GUID=0) | **Event-driven** (send when `scene.camera` becomes None) | Single packet on transition to no-camera state. |

**States**:
- **Active camera set**: GUID is valid, non-null → UE locks view to that camera actor
- **No active camera**: GUID is null (all zeros) → UE releases viewport to free-fly
- **Camera unknown**: GUID is non-null but actor not found → UE logs warning, releases viewport

### 4.4 — Replay Semantics

`PT_ActiveCamera` replay follows the same pattern as Phase 7C Playback:

1. **Replay recording**: Each `PT_ActiveCamera` packet is recorded in the replay buffer with its sequence number.
2. **Replay application**: On reconnect during snapshot replay, the last known active camera GUID is re-sent.
3. **Sequence tracking**: A global sequence counter (not per-GUID) tracks camera switch order. Global sequence is appropriate because there is exactly one active camera at any time.
4. **No per-GUID tracker**: Unlike rename/hierarchy (per-object sequence), camera sync uses a single global sequence — there is only one "active camera" slot.

**Reconnect flow**:
1. Blender reconnects
2. Snapshot marker exchange (PT_BeginSnapshot / PT_EndSnapshot)
3. Blender sends current active camera GUID as a single `PT_ActiveCamera`
4. UE applies it (or logs warning if actor not yet created)

### 4.5 — Reconnect Behaviour

| Scenario | Behaviour |
|----------|-----------|
| Full reconnect (new session) | Blender sends single `PT_ActiveCamera` after snapshot barrier |
| Blender restart | Camera state lost in Blender; reset on first `check_updates()` tick |
| UE restart | UE loses all actors; camera `PT_Create` must arrive before `PT_ActiveCamera` |
| Network interruption, no restart | Sequence tracking ensures stale camera packets are rejected |
| UE hot-reload | Actor state preserved if `LiveSyncActors` map survives |

**Reconnection ordering**:
1. `PT_BeginSnapshot`
2. `PT_Create` / `PT_AssetDef` / `PT_Transform` for camera object
3. `PT_ActiveCamera` (references the camera GUID from step 2)
4. `PT_EndSnapshot`

**If `PT_ActiveCamera` arrives before `PT_Create` of the camera object**:
- UE logs warning, increments `CameraMissingGuid`
- Viewport stays in free-fly
- Accepted: no crash, no assert, no block

### 4.6 — Missing-Camera Behaviour

**Formal rule**: When `HandleActiveCamera(Payload)` is called and the camera actor cannot be found:

```
1. Increment CameraMissingGuid counter
2. Log warning: "[CAMERA] Active camera GUID=%s not found"
3. Release viewport target (SetViewTarget(nullptr))
4. DO NOT cache the failed GUID (future PDUs with same GUID retry lookup)
5. Return normally (no crash, no assert)
```

**Why not cache the failure**: The camera actor may become available later (e.g., if `PT_Create` was delayed in the queue). Each `PT_ActiveCamera` retries the lookup from scratch.

---

## 5. Wire Proposal

### 5.1 — `PT_ActiveCamera (0x15)` Payload

**Fixed-size payload: 28 bytes**

| Offset | Size | Field | Type | Notes |
|--------|------|-------|------|-------|
| 0 | 16 | CameraGUID | `FGuid` (4× uint32 LE) | Object GUID of the active Blender camera. All zeros = no active camera. |
| 16 | 4 | Sequence | `uint32` LE | Monotonic global sequence counter. Incremented on every camera switch. |
| 20 | 8 | Timestamp | `double` LE | `time.time()` at detection on Blender side. For diagnostics and replay ordering. |

**Total: 28 bytes** (`static_assert` guard)

**Payload struct** (C++):
```cpp
struct FActiveCameraPayload
{
    FGuid   CameraGUID   = FGuid();      // All zeros = no active camera
    uint32  Sequence     = 0;            // Monotonic global counter
    double  Timestamp    = 0.0;          // Blender detection time
};
static_assert(sizeof(FActiveCameraPayload) == 28,
    "FActiveCameraPayload must be exactly 28 bytes");
```

**Python serialization**:
```python
ACTIVE_CAMERA_PAYLOAD_SIZE = 28

def serialize_active_camera(guid_bytes, sequence, timestamp):
    """Serialize active camera payload: 16 + 4 + 8 = 28 bytes."""
    return struct.pack("<16sId", guid_bytes, sequence & 0xFFFFFFFF, timestamp)
```

Where `guid_bytes` is:
- 16 bytes from `UUID(obj["ue_guid"]).bytes` for a valid camera
- 16 zero bytes (`b'\x00' * 16`) for no active camera

### 5.2 — Null GUID Convention

| Field | Value | Meaning |
|-------|-------|---------|
| CameraGUID | `{00000000-0000-0000-0000-000000000000}` | No active camera in Blender → UE releases viewport lock |
| Sequence | N | Incremented on every state change, including transition to null |
| Timestamp | T | When the state was observed |

The null GUID is a valid, distinct state. It is emitted when:
- `scene.camera` is `None`
- Active camera object was deleted and Blender auto-cleared the reference
- Scene has no camera objects at all

### 5.3 — No Transform, No Camera Settings

The packet carries **no**:
- Camera world transform (location, rotation, scale)
- Field of view
- Focal length
- Sensor size
- Focus distance
- Aperture
- Clip planes
- Camera type (perspective/orthographic/panoramic)

All of these are explicitly out of scope for Phase 7D (see §10).

---

## 6. Packet Ownership Definition

### 6.1 — PT_ActiveCamera Ownership Card

| Field | Value |
|-------|-------|
| **Packet** | `PT_ActiveCamera = 0x15` |
| **Primary direction** | Blender → UE |
| **Reverse direction** | None (Phase 7D is unidirectional) |
| **Owner** | Blender `scene.camera` |
| **Lifecycle** | Event-driven; sent on camera assignment change |
| **Sequence domain** | Global (single active camera) |
| **State on disconnect** | UE viewport reverts to free-fly; no state preserved |
| **Replay domain** | Global sequence tracker (one entry per switch) |
| **Re-apply on reconnect** | Last known active camera GUID re-sent as single packet |

### 6.2 — Why Unidirectional

| Bidirectional Consideration | Decision | Rationale |
|----------------------------|----------|-----------|
| UE→Blender camera sync | ❌ Out of scope | UE Sequencer may change view targets, but Blender should not be overridden |
| UE Sequencer camera cuts | ❌ Deferred | Phase 7F (Sequencer Integration) may add reverse path; not part of Phase 7D |
| UE editor viewport orbit | ❌ Not applicable | Orbit mode has no "camera" to sync — it is a free view |

### 6.3 — Relationship to Camera Actor Lifecycle

The camera **actor** is owned by the existing lifecycle system (`PT_Create` / `PT_Delete` / `PT_Transform`). Phase 7D does not introduce a new actor lifecycle — it only adds an "active" designation on top of the existing actor.

| Action | Packet | Existing? | Phase 7D? |
|--------|--------|-----------|-----------|
| Camera object created in Blender | `PT_Create` (0x03) | ✅ Phase 3 | Reused — no change |
| Camera object named/identified | `PT_AssetDef` (0x08) | ✅ Phase 5 | Reused — no change |
| Camera object deleted | `PT_Delete_V5` (0x0E) | ✅ Phase 6E | Reused — no change |
| Camera transform changes | `PT_Transform` (0x01) | ✅ Phase 3 | Reused — no change |
| **Camera becomes active** | **`PT_ActiveCamera` (0x15)** | ❌ | **New — Phase 7D** |
| Camera changes from active to inactive | `PT_ActiveCamera` (0x15) | ❌ | **New — same packet, new GUID** |
| Another camera becomes active | `PT_ActiveCamera` (0x15) | ❌ | **New — new GUID** |
| No camera active | `PT_ActiveCamera` (0x15, null GUID) | ❌ | **New — null GUID** |

---

## 7. Failure-Mode Analysis

### 7.1 — Failure Mode Table

| # | Failure Mode | Symptom | Severity | Detection | Mitigation |
|---|--------------|---------|----------|-----------|------------|
| F1 | `PT_ActiveCamera` references non-existent GUID | Viewport stays in free-fly; camera not found | Medium | `CameraMissingGuid` counter incremented | Warning log; no crash; future packets retry lookup |
| F2 | Null GUID storm (rapid camera None↔assign cycles) | Many packets sent; UE releasing/acquiring viewport | Low | Rate-limited by packet rate limit (120/s) | Sequence filter rejects duplicate null states |
| F3 | Stale camera GUID after reconnect (actor not yet created) | `PT_ActiveCamera` arrives during snapshot before `PT_Create` | Medium | Ordering: Create before ActiveCamera in snapshot | Missing GUID handling (F1) covers this |
| F4 | Duplicate `PT_ActiveCamera` (same GUID, same sequence) | Applied multiple times | Low | Sequence monotonicity check | `Seq <= LastSeq` → stale rejection |
| F5 | Sequence overflow (uint32 wraps) | Next seq = 0 ≤ LastSeq = 0xFFFFFFFF → all packets stale | Low | Wrap-around unsafe (very rare at 1 switch/day → 11M years) | Accept if `bHasActiveCamera == false` after overflow; document as accepted edge case |
| F6 | Camera object deleted on Blender side; `scene.camera` still set (race) | `PT_Delete` arrives before or after `PT_ActiveCamera` | Low | Delete processing removes actor; next camera poll sends null GUID | Eventual consistency: camera actor may briefly exist without viewport lock |
| F7 | Corrupt packet payload (truncated, bit-flipped) | Invalid GUID, sequence, or timestamp | Low | Size check (< 28 bytes → Malformed); GUID validity check | Malformed → increment counter, discard, log warning |
| F8 | Multiple UE viewport clients (multi-monitor) | Only primary viewport sees camera switch | Medium | Only `GEditor->GetActiveViewport()` is updated | Documented limitation; multi-viewport sync is deferred |
| F9 | Blender viewport in local view mode | `scene.camera` may still be set; local view is editor-private | Low | Poll `scene.camera` unconditionally; local view not detected | Acceptable: local view camera changes still replicated (window into same scene) |
| F10 | Pre-existing camera actor not managed by UELiveSync (manually placed in UE level) | Camera exists in UE but not created via `PT_Create`; not in `LiveSyncActors` | Low | Lookup by GUID fails; falls into F1 | Warning log; viewport stays free. Actor not in LiveSyncActors → not found. |

### 7.2 — Diagnostic Counters

| Counter | Type | Read | Cleared By | Purpose |
|---------|------|------|------------|---------|
| `ActiveCameraPacketsReceived` | `std::atomic<int32>` | `DumpState` | `ConsoleReset` | Total PT_ActiveCamera packets received |
| `ActiveCameraPacketsApplied` | `std::atomic<int32>` | `DumpState` | `ConsoleReset` | Successfully applied camera switches |
| `ActiveCameraPacketsStale` | `std::atomic<int32>` | `DumpState` | `ConsoleReset` | Duplicate or out-of-order (rejected) |
| `ActiveCameraPacketsMalformed` | `std::atomic<int32>` | `DumpState` | `ConsoleReset` | Truncated or corrupt payloads |
| `CameraMissingGuid` | `std::atomic<int32>` | `DumpState` | `ConsoleReset` | Times PT_ActiveCamera GUID not found in scene |
| `ActiveCameraViewTargetSet` | `std::atomic<int32>` | `DumpState` | `ConsoleReset` | Successful `SetViewTarget()` calls |
| `ActiveCameraViewTargetReleased` | `std::atomic<int32>` | `DumpState` | `ConsoleReset` | Viewport released to free-fly (null GUID) |

### 7.3 — Blender-Side Failure Modes

| # | Failure Mode | Severity | Mitigation |
|---|--------------|----------|------------|
| B1 | `bpy.context.scene.camera` raises `AttributeError` (context without a scene) | Low | Wrap in try/except; treat as null GUID |
| B2 | Camera object exists but `obj["ue_guid"]` key is missing | Low | Camera should have GUID from `ensure_unique_guid()`. If missing, skip and log. |
| B3 | Camera object deleted between `scene.camera` read and GUID access | Low | ReferenceError → treat as null GUID |

### 7.4 — Recovery Paths

| Scenario | Recovery |
|----------|----------|
| Camera actor not found | Next `PT_ActiveCamera` with same GUID retries; viewport stays free until found |
| Camera actor deleted while active | UE viewport notices `ViewTarget` actor was destroyed → auto-reverts to free-fly (UE engine behaviour, no code needed) |
| Reconnect with no camera | Blender sends null GUID; UE viewport stays free |
| UE hot-reload mid-sync | `ConsoleReset` clears all camera state; next `PT_ActiveCamera` establishes fresh state |

---

## 8. Acceptance Criteria

### 8.1 — Wire Format Validation

| # | Criterion | Verification |
|---|-----------|-------------|
| W1 | `PT_ActiveCamera (0x15)` constant matches on both sides | Compare `network.py` and `SyncTypes.h` |
| W2 | Payload is exactly 28 bytes | `static_assert(sizeof(FActiveCameraPayload) == 28)` |
| W3 | Null GUID `{00000000-...-000000000000}` serialises to 16 zero bytes | Python `struct.pack("<16sId", b'\x00'*16, ...)` |
| W4 | FNV protocol signature includes `0x15` and payload size 28 | Both sides updated |
| W5 | `kValidTypes[]` includes `0x15` | UE side |

### 8.2 — Blender Detection

| # | Criterion | Verification |
|---|-----------|-------------|
| B1 | `scene.camera` change → `PT_ActiveCamera` with new GUID | Integration test: set `scene.camera = cam2` → packet sent |
| B2 | `scene.camera = None` → `PT_ActiveCamera` with null GUID | Integration test: clear camera → packet sent |
| B3 | No camera assigned → no packet sent (initial state) | First tick: no packet if no change |
| B4 | Same camera re-assigned → no duplicate packet | GUID unchanged → no send |
| B5 | Camera deletion → `PT_ActiveCamera` with null GUID | Delete active camera → next tick sends null |
| B6 | Camera creation (auto-assigned) → `PT_ActiveCamera` with new GUID | Create new camera in empty scene → packet sent |
| B7 | Preference gate: `camera_sync` disabled → no packets | Default OFF; packets suppressed when disabled |

### 8.3 — UE Handler

| # | Criterion | Verification |
|---|-----------|-------------|
| U1 | `HandleActiveCamera()` parses payload correctly | Unit test: inject bytes → verify GUID/Sequence/Timestamp |
| U2 | Valid GUID with existing actor → `SetViewTarget()` called | Integration: camera actor exists → viewport updated |
| U3 | Null GUID → `SetViewTarget(nullptr)` → free-fly | Integration: null GUID → viewport released |
| U4 | Non-existent GUID → warning logged, viewport free | Integration: GUID not in LiveSyncActors → log, no crash |
| U5 | Sequence ≤ LastSeq → stale rejection | Unit test: duplicate seq → rejected |
| U6 | Payload size < 28 bytes → malformed rejection | Unit test: truncated → malformed counter incremented |
| U7 | Malformed state → `ConsoleReset` clears counters | Integration: `ConsoleReset` → all camera counters zero |
| U8 | `ConsoleDumpState` includes all 7 camera counters | Integration: counters visible in output |

### 8.4 — Reconnect Behaviour

| # | Criterion | Verification |
|---|-----------|-------------|
| R1 | Reconnect → Blender sends current active camera | Integration: disconnect → reconnect → camera packet sent |
| R2 | Snapshot ordering: camera `PT_Create` before `PT_ActiveCamera` | Replay test: ordered replay applies correctly |
| R3 | Out-of-order: `PT_ActiveCamera` before camera `PT_Create` → no crash | Stress: swap packet order → `CameraMissingGuid` incremented |
| R4 | Sequence monotonicity survives reconnect | Integration: reconnected camera has higher sequence |

### 8.5 — Duplicate Suppression

| # | Criterion | Verification |
|---|-----------|-------------|
| D1 | Same GUID + same sequence → discarded | Unit test: exact duplicate → stale |
| D2 | Same GUID + lower sequence → discarded | Unit test: old seq → stale |
| D3 | Same GUID + higher sequence → applied | Unit test: new seq → applied |
| D4 | Different GUID → always applied (if sequence advances) | Unit test: switch cameras → applied |

### 8.6 — Missing Camera Safety

| # | Criterion | Verification |
|---|-----------|-------------|
| M1 | Non-existent GUID → no crash, no assert | Stress: random GUID → no UE crash |
| M2 | `CameraMissingGuid` counter incremented for each miss | Unit test: 10 unknown GUIDs → counter = 10 |
| M3 | Future packet with now-available GUID → succeeds | Integration: delayed create → subsequent PT_ActiveCamera works |
| M4 | `ViewTarget` set to nullptr for unknown GUID | Integration: unknown GUID → `GetViewTarget()` = nullptr |

### 8.7 — Validation Test Plan

| Test Category | Count (est.) | Focus |
|---------------|--------------|-------|
| Wire format (Stage 1) | ~35 | Serialization, size, null GUID, sequence, timestamp, FNV signature |
| Blender detection (Stage 2) | ~30 | scene.camera poll, null transitions, preference gate, error handling |
| UE handler (Stage 3) | ~40 | Parse, apply, stale, malformed, missing GUID, reconnect, dump |
| Total | ~105 | All standalone (Python simulated UE) |

---

## 9. Implementation Stages

### Stage 0 — Scope Lock (This Document)

| Step | Description | Deliverable |
|------|-------------|-------------|
| 0.1 | Write this scope lock document | `Docs/Architecture/53-phase7d-camera-sync-scope-lock.md` |
| 0.2 | Update STATUS.md | Phase 7D shown as SCOPE LOCK |
| 0.3 | Reserve `0x15` in protocol doc | No code changes |

**Validation gate**: Documents only — zero source files modified.

### Stage 1 — Wire Format + Constants

| Step | Description | Verification |
|------|-------------|--------------|
| 1.1 | Add `PT_ActiveCamera = 0x15` to Blender `network.py` | Constant defined |
| 1.2 | Add `PT_ActiveCamera = 0x15` to UE `SyncTypes.h` `EPacketType` enum | Constant defined |
| 1.3 | Add `FActiveCameraPayload` struct + `static_assert(28)` in `SyncTypes.h` | Struct compiles, size verified |
| 1.4 | Add `serialize_active_camera()` in `network.py` | Function defined, returns 28 bytes |
| 1.5 | Add `NULL_GUID = b'\x00' * 16` constant in `network.py` | Null GUID available for comparison |
| 1.6 | Update FNV protocol signature (both sides): include `0x15` + `28` | Signatures match |
| 1.7 | Add `ACTIVE_CAMERA_PAYLOAD_SIZE = 28` constant | Available for import |
| 1.8 | Add protocol blob for `0x15` to compute signature | Wire format frozen |

**Validation gate**: 35 standalone tests — payload layout, size, null GUID, sequence wrapping, timestamp, FNV signature match.

### Stage 2 — Blender Detection

| Step | Description | Verification |
|------|-------------|--------------|
| 2.1 | Add `camera_sync: BoolProperty` to Blender preferences (default OFF) | UI toggle visible |
| 2.2 | Add `_on_camera_sync_update()` callback → `network.set_camera_sync_enabled()` | Toggle wiring |
| 2.3 | Add `_camera_sync_enabled`, `set_camera_sync_enabled()`, `is_camera_sync_effective()` in `network.py` | Module-level state |
| 2.4 | Add `_camera_sequence = 0`, `_last_active_camera_guid = None`, counters in `network.py` | State tracking globals |
| 2.5 | Add detection block in `sync.py check_updates()` after playback block | Detect and send `PT_ActiveCamera` |
| 2.6 | Detection logic: poll `scene.camera`, get GUID, compare to cached, send on change | GUID change → packet sent; null → null GUID sent |
| 2.7 | Wrap in try/except for `AttributeError`, `ReferenceError` | Context-safe |
| 2.8 | `start_sync()` / `stop_sync()` reset `_last_active_camera_guid` | Clean connection/disconnection |
| 2.9 | `dump_diagnostics()` includes camera counters | Observability |

**Validation gate**: 30 standalone tests — scene.camera poll, null transitions, preference gating, error handling, reset on start/stop.

### Stage 3 — UE Receive + Handler

| Step | Description | Verification |
|------|-------------|--------------|
| 3.1 | Add `FActiveCameraPayload` parse in `ProcessBinaryPacket` dispatch for `0x15` | Dispatch branch added |
| 3.2 | Add `kValidTypes[]` entry for `0x15` | Packet type accepted |
| 3.3 | Implement `HandleActiveCamera()` with full validation chain: size, null GUID detection, sequence monotonicity | Handler complete |
| 3.4 | Implement camera actor lookup by GUID (`FindCameraByGuid()`) | Lookup function |
| 3.5 | Implement `SetViewTarget(CameraActor)` on `GEditor->GetActiveViewport()->GetClient()` | Viewport updated |
| 3.6 | Implement null GUID path: `SetViewTarget(nullptr)` | Viewport released |
| 3.7 | Implement missing-GUID path: warning log, counter increment | Safe fallback |
| 3.8 | Add all 7 diagnostics counters + `CameraMissingGuid` | Observability |
| 3.9 | Add `ConsoleReset` + `ConsoleDumpState` entries for camera counters | Admin commands |
| 3.10 | Add `bHasActiveCamera`, `LastActiveCameraGUID`, `LastActiveCameraSequence`, `LastActiveCameraTimestamp` member vars | State storage |
| 3.11 | Add `ActiveCameraPacketsReceived/Applied/Stale/Malformed` to `FLiveSyncStats` | Stats tracked |

**Validation gate**: 40 standalone tests — parse, apply, stale, malformed, missing GUID, reset, dump, reconnect, null GUID.

### Stage 4 — Reconnect Safety

| Step | Description | Verification |
|------|-------------|--------------|
| 4.1 | Blender sends `PT_ActiveCamera` during snapshot (after object creation) | Snapshot order: Create → ActiveCamera |
| 4.2 | UE handles pre-Create ActiveCamera gracefully (F1 mitigation) | Missing GUID path covers this |
| 4.3 | Sequence tracker reset on reconnect | Fresh connection → fresh sequence |
| 4.4 | Replay recording for active camera transitions | Replay buffer entry |

**Validation gate**: 10 tests — reconnect sequence, snapshot ordering, out-of-order delivery.

---

## 10. Explicitly Out of Scope

The following items are **explicitly excluded** from Phase 7D:

| Feature | Reason | Deferred To |
|---------|--------|-------------|
| Camera world transform sync | Existing `PT_Transform` handles this | ✅ Already works |
| Camera FOV / focal length | Requires camera parameter packet (`PT_CameraDef`) | Phase 7D.2 or 7E |
| Camera sensor size | Requires `PT_CameraDef` | Phase 7D.2 |
| Camera focus distance | Requires `PT_CameraDef` | Phase 7D.2 |
| Camera aperture | Requires `PT_CameraDef` | Phase 7D.2 |
| Camera clip planes | Requires `PT_CameraDef` | Phase 7D.2 |
| Camera type (persp/ortho/pano) | Requires `PT_CameraDef` | Phase 7D.2 |
| DOF / focus settings | Requires `PT_CameraDef` | Phase 7D.2 |
| Lens settings / shift | Requires `PT_CameraDef` | Phase 7D.2 |
| Camera cuts track | Requires Sequencer integration (Phase 7F) | Phase 7F |
| Sequencer integration | Sequence/track/curve creation is Phase 7F | Phase 7F |
| Multi-viewport sync | Only primary viewport is updated | Future |
| UE→Blender reverse camera sync | Unidirectional only; Blender is authoring source | Future |
| Local view camera state | Transient editor affordance; not scene-state | Deferred |
| Runtime (standalone game) viewport | Editor-only for Phase 7D | Phase 8+ |
| Camera animation curves | Keyframe replication is Phase 7E | Phase 7E |
| Post-process settings | Not mapped to Blender camera properties | Future |
| Camera rigs / multi-camera switcher | UE-specific feature | Future |
| `ACineCameraActor` spawn | Use base `ACameraActor`; cine parameters deferred | Phase 7D.2 |

---

## 11. Risks & Mitigations

| # | Risk | Probability | Impact | Mitigation |
|---|------|-------------|--------|------------|
| R1 | `SetViewTarget()` is editor-only; runtime builds cannot lock viewport | High | Medium | Phase 7D targets editor only. Document as limitation. Runtime viewport sync deferred. |
| R2 | `GEditor->GetActiveViewport()` returns nullptr in some contexts (e.g., commandlet, standalone) | Medium | Medium | Null-check before access; treat as no-op with warning log. |
| R3 | `SetViewTarget()` causes viewport jitter (camera instantiation latency) | Low | Medium | Camera actor already exists via `PT_Create` before `PT_ActiveCamera`. No jitter expected. |
| R4 | Rapid camera switching (scripted or accidental) floods network | Low | Low | Sequence dedup suppresses same-GUID repeats. 120 pkt/s rate limit applies. |
| R5 | Blender `scene.camera` poll conflicts with depsgraph evaluation | Low | Low | `scene.camera` is a simple RNA pointer read; no depsgraph access needed. |
| R6 | UE editor viewport client type is not `FEditorViewportClient` (e.g., `SLevelViewport` wrapper) | Medium | Low | Use `GetClient()` which returns `FViewportClient*`; cast to `FEditorViewportClient*` with null check. |
| R7 | Packet type 0x15 conflicts with future Phase 7E/7F allocations | Low | Low | Reserved in this document. Phase 7D uses 0x15; Phase 7E uses 0x17; Phase 7F uses 0x18. |

---

## 12. Files Touched (Estimated)

### Stage 1 — Wire Format

| File | Change |
|------|--------|
| `Blender_Addon/network.py` | Add `PT_ActiveCamera = 0x15`, `ACTIVE_CAMERA_PAYLOAD_SIZE = 28`, `serialize_active_camera()`, update FNV signature |
| `UE_Plugin/.../Public/SyncTypes.h` | Add `PT_ActiveCamera = 0x15` to `EPacketType`, add `FActiveCameraPayload` struct + static_assert, update FNV signature |

### Stage 2 — Blender Detection

| File | Change |
|------|--------|
| `Blender_Addon/__init__.py` | Add `camera_sync: BoolProperty` (default OFF), `_on_camera_sync_update` callback |
| `Blender_Addon/network.py` | Add `_camera_sync_enabled`, `set_camera_sync_enabled()`, `is_camera_sync_effective()`, `_camera_sequence`, `_last_active_camera_guid`, counters |
| `Blender_Addon/sync.py` | Import camera functions, add detection block in `check_updates()`, `dump_diagnostics()` stats, `start_sync()`/`stop_sync()` reset |

### Stage 3 — UE Handler

| File | Change |
|------|--------|
| `UE_Plugin/.../Public/SyncTypes.h` | Add `ActiveCameraPackets*` counters to `FLiveSyncStats` |
| `UE_Plugin/.../Public/UELiveSyncSubsystem.h` | Add `HandleActiveCamera()` decl, `bHasActiveCamera`, `LastActiveCameraGUID/Sequence/Timestamp` members |
| `UE_Plugin/.../Private/UELiveSyncSubsystem.cpp` | Add `0x15` to `kValidTypes[]`, dispatch case, `HandleActiveCamera()` implementation, `FindCameraByGuid()`, `SetViewTarget()` calls |
| `UE_Plugin/.../Private/UELiveSyncSubsystem_Diagnostics.inl` | Add `ConsoleReset` + `ConsoleDumpState` for 7 camera counters |

### Stage 4 — Tests

| File | Change |
|------|--------|
| `tests/phase7d_stage1_active_camera_wire.py` | ~35 tests — payload layout, size, null GUID, sequence, timestamp, FNV signature |
| `tests/phase7d_stage2_active_camera_detection.py` | ~30 tests — scene.camera poll, transitions, preference gating, error handling |
| `tests/phase7d_stage3_ue_handler_validation.py` | ~40 tests — parse, apply, stale, malformed, missing GUID, reconnect, dump |
| `tests/phase7d_stage4_reconnect_safety.py` | ~10 tests — reconnect ordering, snapshot, out-of-order |

---

## Appendix A — Protocol Signature Compatibility

Adding `PT_ActiveCamera = 0x15` requires the FNV protocol signature to be updated on both sides. The existing signature includes all packet types from `0x01` through `0x14`. Adding `0x15` (and payload size `28`) changes the computed hash.

**Compatibility matrix** for signature mismatch:

| Blender | UE | Result |
|---------|----|--------|
| Without Phase 7D | Without Phase 7D | ✅ Match (current) |
| Without Phase 7D | With Phase 7D | ⚠️ Mismatch; UE logs signature warning but continues processing |
| With Phase 7D | Without Phase 7D | ⚠️ Mismatch; `0x15` silently ignored (not in `kValidTypes[]`) |
| With Phase 7D | With Phase 7D | ✅ Match |

**Mitigation**: Signature mismatch is a warning, not a fatal error. Old UE ignores `0x15` packets. New UE with old Blender simply never receives `0x15` packets.

## Appendix B — Comparison with Original Phase 7 Proposal

The original Phase 7 scoping document (`52-phase7-animation-sequencer-scope-lock.md`) proposed:

- `PT_ActiveCamera (0x15)`: 18 bytes (CameraGUID[16] + Sequence[2])
- `PT_CameraDef (0x16)`: 53 bytes (GUID + FOV + FocalLength + Sensor + Focus + Aperture + ClipNear + ClipFar + CameraType)

**Phase 7D scope lock changes**:

| Aspect | Original Proposal | Phase 7D (This Document) | Rationale |
|--------|-------------------|--------------------------|-----------|
| `PT_ActiveCamera` size | 18 bytes | **28 bytes** | Added Timestamp (8 bytes) for diagnostics and replay ordering. Sequence expanded from uint16 to uint32 for production safety. |
| `PT_CameraDef (0x16)` | 53 bytes, proposed | **Out of scope** | Camera parameters deferred to Phase 7D.2. Phase 7D scoped to camera selection only. |
| Camera transform | Included in `PT_CameraState` proposal from STATUS.md | **Out of scope** | Transform data already handled by existing `PT_Transform` packets. No need for a duplicate camera transform pathway. |
| Sequence field | uint16 (2 bytes) | **uint32 (4 bytes)** | Consistent with existing packet sequence fields (rename, hierarchy, collection use uint32). Avoids premature wrap-around. |
| Timestamp field | Not included | **double (8 bytes)** | Aligns with `FPlaybackStatePayload` pattern. Useful for diagnostics and replay ordering. |
| Null GUID semantics | Not defined | **Explicitly defined** | `{00000000-...-0000}` = no active camera → viewport released. Critical for clean state transitions. |
| UE side | Spawn `ACineCameraActor` | **Spawn `ACameraActor`** | Base `ACameraActor` avoids requiring `CineCamera` module dependency. Cine params out of scope. |
| Packet ownership | Not explicitly defined | **Section 6** | Full ownership card: unidirectional, Blender-owned, event-driven, global sequence domain. |
| Failure-mode analysis | Not included | **Section 7** | 10 failure modes documented with mitigations, 7 diagnostic counters, 3 Blender-side failure modes. |
| Missing-camera behaviour | Not addressed | **Section 4.6** | Formal rule: log warning, release viewport, increment counter, no crash. |

**Why `PT_CameraDef` is deferred**: Camera parameters (FOV, focal length, sensor, DOF) are semantically independent from camera selection. An artist may:
1. Switch cameras without changing parameters
2. Change parameters of a non-active camera
3. Want different sync cadences for selection vs parameters

Splitting selection (Phase 7D) from parameters (Phase 7D.2/7E) keeps each phase minimal, testable, and independently valuable.

**Why not `PT_CameraState`** (as STATUS.md proposed): The STATUS.md proposal bundled camera GUID + transform + FOV + aspect ratio into a single packet. Bundling transform with camera selection creates coupling between two unrelated concerns. Phase 7D separates them: `PT_Transform` handles transform; `PT_ActiveCamera` handles selection.
