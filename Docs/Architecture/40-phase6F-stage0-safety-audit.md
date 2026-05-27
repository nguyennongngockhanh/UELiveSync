# Phase 6F — Stage 0: Pre-Implementation Safety Audit

> **Created**: 2026-05-27
> **Status**: COMPLETE — All checks PASS
> **Scope**: Verification that `PT_Collection = 0x0F` can be safely introduced
> **Freeze**: Phase 6 Stabilization Freeze ACTIVE — this audit confirms additive-only compliance

---

## 1. PT_Collection (0x0F) Registration

### 1.1 UE Side — SyncTypes.h

**Result**: ⚠️ NOT YET PRESENT — must be added.

`EPacketType` enum (line 205–230) currently defines types `0x01` through `0x0E`:

```cpp
PT_Transform = 0x01,
PT_Reserved_02 = 0x02,
PT_Create      = 0x03,
PT_Delete    = 0x04,
PT_Material  = 0x05,
PT_Mesh      = 0x06,
PT_Heartbeat = 0x07,
PT_AssetDef  = 0x08,
PT_BeginSnapshot = 0x09,
PT_EndSnapshot   = 0x0A,
PT_Visibility    = 0x0B,
PT_Rename        = 0x0C,
PT_Hierarchy     = 0x0D,
PT_Delete_V5      = 0x0E,
```

`0x0F` is unassigned — no collision risk.

**Action**: Append `PT_Collection = 0x0F` after `PT_Delete_V5`.

### 1.2 Blender Side — network.py

**Result**: ⚠️ NOT YET PRESENT — must be added.

Constants block (lines 61–67) defines `PT_BeginSnapshot` through `PT_Delete_V5`. `PT_Collection` is absent.

**Action**: Append `PT_Collection = 0x0F`.

### 1.3 FNV Signature Chain

**UE** — SyncTypes.h lines 822–826:
```cpp
H = fnv(H, 0x01); H = fnv(H, 0x03);
H = fnv(H, 0x04); H = fnv(H, 0x07);
H = fnv(H, 0x08); H = fnv(H, 0x09);
H = fnv(H, 0x0A); H = fnv(H, 0x0B); H = fnv(H, 0x0C); H = fnv(H, 0x0D);
H = fnv(H, 0x0E); // PT_Delete_V5
```

**Blender** — network.py line 40:
```python
for pt in (0x01, 0x03, 0x04, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E):
    h = _fnv(h, pt)
```

**Result**: ⚠️ NOT YET PRESENT — `0x0F` absent from both.

**Action**: Append `0x0F` to both FNV signature chains, after `0x0E`.

### 1.4 Protocol Validation Array

UE side — UELiveSyncSubsystem.cpp line 2049:
```cpp
static constexpr uint8 kValidTypes[] =
    { 0x01, 0x03, 0x04, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E };
```

**Result**: ⚠️ NOT YET PRESENT — `0x0F` absent from kValidTypes.

**Action**: Append `0x0F` to kValidTypes.

---

## 2. Collision Analysis

| Type | Value | Used By | Conflict? |
|------|-------|---------|-----------|
| `PT_Transform` | `0x01` | Transform sync (V2/V3) | NO |
| `PT_Reserved_02` | `0x02` | Legacy (unused) | NO |
| `PT_Create` | `0x03` | Object creation | NO |
| `PT_Delete` | `0x04` | V2/V3 delete | NO |
| `PT_Material` | `0x05` | Material sync | NO |
| `PT_Mesh` | `0x06` | Mesh sync | NO |
| `PT_Heartbeat` | `0x07` | Heartbeat | NO |
| `PT_AssetDef` | `0x08` | Asset identity (V5) | NO |
| `PT_BeginSnapshot` | `0x09` | Snapshot marker | NO |
| `PT_EndSnapshot` | `0x0A` | Snapshot marker | NO |
| `PT_Visibility` | `0x0B` | Visibility toggle | NO |
| `PT_Rename` | `0x0C` | Rename event | NO |
| `PT_Hierarchy` | `0x0D` | Attach/detach event | NO |
| `PT_Delete_V5` | `0x0E` | Lifecycle/delete | NO |
| **`PT_Collection`** | **`0x0F`** | **Collection/group** | **NO** ✅ |

`0x0F` is the next contiguous available byte. No existing type uses this value.

---

## 3. Dependency Analysis

### 3.1 Dependency on Hierarchy/Attach Systems

**Question**: Does PT_Collection parsing depend on hierarchy attachment logic?

**Answer**: NO. Collection is metadata-only. It does not:
- Call `AttachToActor` / `DetachFromActor`
- Read `GetAttachParentActor()` / `GetAttachChildren()`
- Touch `PendingHierarchyAttachments`
- Interact with `FHierarchySequenceTracker` or `GHierarchySequences`
- Reference `EOrphanState`, deferred retry, or cycle detection

**Verdict**: ✅ No dependency on hierarchy.

### 3.2 Dependency on Lifecycle/Delete Systems

**Question**: Does PT_Collection parsing depend on delete/tombstone logic?

**Answer**: NO. Collection does not:
- Call `IsTombstoned()` / `AddTombstone()` / `RemoveTombstone()`
- Read `GDeleteTombstoneMap` / `GDeleteTombstoneOrder`
- Call `Actor->Destroy()`
- Interact with `FDeleteSequenceTracker` or `GDeleteSequences`
- Reference `DeferredDeleteQueue`

**Verdict**: ✅ No dependency on lifecycle/delete.

### 3.3 Dependency on Visibility

**Question**: Does PT_Collection parsing depend on visibility logic?

**Answer**: NO. No interaction with `GVisibilitySequences`, `FScopedVisibilitySuppression`, or `SetIsTemporarilyHiddenInEditor`.

**Verdict**: ✅ No dependency on visibility.

### 3.4 Dependency on Rename

**Question**: Does PT_Collection parsing depend on rename logic?

**Answer**: NO. No interaction with `GRenameSequences`, `FScopedRenameSuppression`, or `SetActorLabel`.

**Verdict**: ✅ No dependency on rename.

### 3.5 Dependency on Transform/Interpolation

**Question**: Does PT_Collection parsing depend on transform or interpolation systems?

**Answer**: NO. Collection does not read `FSyncTransformState`, `TransformStates`, `InterpolateTransforms`, or `SetActorTransform`.

**Verdict**: ✅ No dependency on transform pipeline.

---

## 4. Additive-Only Compliance

| Check | Status |
|-------|--------|
| Does Stage 0–3 modify frozen runtime systems? | NO — no changes to LiveSyncQueue, PendingAssetQueue, LiveSyncRunnable, FSyncTransformState, Tick pipeline, interpolation, reconnect lifecycle |
| Does Stage 0–3 modify existing Phase 6 handlers? | NO — only adds new handler, new tracker, new counters |
| Does Stage 0–3 introduce cross-lane coupling? | NO — no references to hierarchy, lifecycle, visibility, rename trackers or variables |
| Does Stage 0–3 mutate UE actor state? | NO — log-only, no actor lookup or mutation |
| Does Stage 0–3 enqueue into existing queues? | NO — no queue interaction |
| Is the FNV signature change additive only? | YES — existing types unchanged, one byte appended |

**Verdict**: ✅ Fully additive-only compliant.

---

## 5. Stage 0 Verification Summary

| Item | Status |
|------|--------|
| PT_Collection = 0x0F in SyncTypes.h EPacketType | ⚠️ Not present — will be added |
| PT_Collection = 0x0F in network.py constants | ⚠️ Not present — will be added |
| 0x0F in UE FNV signature (SyncTypes.h) | ⚠️ Not present — will be added |
| 0x0F in Blender FNV signature (network.py) | ⚠️ Not present — will be added |
| 0x0F in kValidTypes protocol validation array | ⚠️ Not present — will be added |
| No collision with 0x01–0x0E | ✅ CONFIRMED |
| No dependency on hierarchy | ✅ CONFIRMED |
| No dependency on lifecycle/delete | ✅ CONFIRMED |
| No dependency on visibility | ✅ CONFIRMED |
| No dependency on rename | ✅ CONFIRMED |
| No dependency on transform pipeline | ✅ CONFIRMED |
| Additive-only compliance | ✅ CONFIRMED |

**Stage 0 verdict**: SAFE TO PROCEED with Stages 1–3 implementation.

---

## 6. Required Changes Summary

| File | Change |
|------|--------|
| `SyncTypes.h` | Add `PT_Collection = 0x0F` to EPacketType; add `LIVE_SYNC_COLLECTION_BASE_SIZE = 30` constant; add `FCollectionSequenceTracker` struct; add collection counters to FLiveSyncStats; add `0x0F` to FNV signature |
| `UELiveSyncSubsystem.cpp` | Add `GCollectionSequences` global; add PT_Collection parser branch in ProcessBinaryPacket; add `HandleCollection()` log-only handler; add tracker reset in StopNetworkThread and ConsoleReset |
| `network.py` | Add `PT_Collection = 0x0F` constant; add `0x0F` to FNV signature |
