# Phase 6E — Stage 0–3 Stability Review

> **Created**: 2026-05-26
> **Status**: PASS — all frozen-runtime invariants confirmed
> **Scope**: Pre-implementation audit for Stages 0–3 of Phase 6E lifecycle/delete replication
>
> This document records the **pre-implementation frozen-runtime audit** required
> by Stage 0 of the Phase 6E implementation plan (see
> `33-phase6E-lifecycle-implementation-plan.md §3.5 — Stage 0`).

---

## 1. Frozen System Audit

### 1.1 LiveSyncQueue

| Property | Status | Detail |
|----------|--------|--------|
| Modified? | **NOT TOUCHED** | No changes to `LiveSyncQueue.h`. Delete packets are NOT enqueued as `FLiveSyncPacket` variants. They are parsed and handled immediately on the game thread (same pattern as rename, visibility, hierarchy). |
| Risk | None | Delete never enters the MPSC queue. No change to queue ownership, blocking, or bounded-buffer behavior. |
| Verification | `LiveSyncQueue.h` — zero changes. |

### 1.2 PendingAssetQueue

| Property | Status | Detail |
|----------|--------|--------|
| Modified? | **NOT TOUCHED** | No changes to `PendingAssetQueue.h`. Delete packets have no interaction with asset resolution. |
| Risk | None | Delete never reads or writes the pending asset queue. |
| Verification | `PendingAssetQueue.h` — zero changes. |

### 1.3 LiveSyncRunnable

| Property | Status | Detail |
|----------|--------|--------|
| Modified? | **NOT TOUCHED** | No changes to `LiveSyncRunnable.h` or `.cpp`. Network thread lifecycle is unchanged. Delete packets are not parsed on the network thread. |
| Risk | None | Network thread continues to enqueue raw `FLiveSyncPacket` data; delete packets are parsed from the `RawData` buffer on the game thread. |
| Verification | `LiveSyncRunnable.h`, `LiveSyncRunnable.cpp` — zero changes. |

### 1.4 FSyncTransformState

| Property | Status | Detail |
|----------|--------|--------|
| Modified? | **NOT TOUCHED** | No changes to `SyncTypes.h` struct layout. `FSyncTransformState` remains POD-only. |
| Risk | None | Delete does not read or write transform state directly. On future stages, deleted actors are removed from `TransformStates` map, but the struct itself is unchanged. |
| Verification | `SyncTypes.h` lines 41–198 unchanged. |

### 1.5 Tick Ordering

| Property | Status | Detail |
|----------|--------|--------|
| Modified? | **NOT TOUCHED** | The Tick pipeline ordering is unchanged. Delete parsing occurs within `ProcessBinaryPacket` (step 1 of Tick), not as a new pipeline stage. |
| Risk | None | Delete is processed during packet parsing, before interpolation, attachment resolution, and recovery — all of which naturally skip deleted actors via ActorCache lookup. |
| Verification | `Tick()` pipeline (lines 1087–1201) — zero changes. |

### 1.6 Hierarchy Resolver (ResolveHierarchyAttachments)

| Property | Status | Detail |
|----------|--------|--------|
| Modified? | **NOT TOUCHED** | `ResolveHierarchyAttachments()` is NOT modified. Deferred hierarchy entry eviction will be added in Stage 8 as a separate helper called from `HandleDelete()`, not as a modification to the resolver itself. |
| Risk | None | The delete handler may call `PendingHierarchyAttachments.RemoveAll()` (bounded read-only access), but the resolver's iteration logic, orphan state machine, and retry cadence are unchanged. |
| Verification | `ResolveHierarchyAttachments()` — zero changes. |

### 1.7 Transform Interpolation

| Property | Status | Detail |
|----------|--------|--------|
| Modified? | **NOT TOUCHED** | `InterpolateTransforms()` is not modified. |
| Risk | None | Deleted actors are removed from `ActorCache` and/or `TransformStates`; interpolation will naturally skip them via lookup failure. |

### 1.8 Other Frozen Systems

| System | Status | Detail |
|--------|--------|--------|
| `AttachToParent()` | NOT TOUCHED | Delete may call `DetachFromActor()` for child detachment (Stage 8), but `AttachToParent()` is never called by delete. |
| `DetachFromParent()` | NOT TOUCHED | Delete calls raw `DetachFromActor()` API, not the frozen `DetachFromParent()` wrapper. |
| `HandleCreateObject()` | NOT TOUCHED | Tombstone check for CREATE is additive-only (early return at the top of the handler). |
| `HandleDeleteObject()` (0x04) | NOT TOUCHED | PT_Delete (0x0E) is an entirely separate handler. The legacy V3 PT_Delete (0x04) code path is unchanged. |
| `ConsoleReset()` | ADDITION ONLY | Delete counters will be reset alongside existing rename/visibility/hierarchy counters. Additive code only. |
| `StopNetworkThread()` | ADDITION ONLY | `GDeleteSequences.Clear()` and tombstone map clear will be added alongside existing tracker clears. Additive code only. |
| `HandleEndSnapshot()` | ADDITION ONLY | `DeferredDeleteQueue.Empty()` will be added. Additive code only. |

---

## 2. Parser Isolation Verification

| Check | Status | Detail |
|-------|--------|--------|
| Is PT_Delete an independent case branch? | ✅ YES | `case PT_Delete:` is a new independent case in the `ProcessBinaryPacket` switch, after all existing case branches. |
| Does it modify existing case branches? | ❌ NO | No changes to existing `0x01`–`0x0D` branches. |
| Does it enter FLiveSyncQueue? | ❌ NO | Delete packets are parsed and handled in-place on the game thread. |
| Does it reuse FLiveSyncPacket union? | ❌ NO | Delete uses its own 28-byte wire format, not the FLiveSyncPacket union. |
| Are boundary checks applied before memcpy? | ✅ YES | Payload size % 28 == 0, valid GUID, valid count. |
| Is there cross-packet coupling during parse? | ❌ NO | Each packet is parsed independently. No batch-level state machine. |

---

## 3. Cross-Lane Coupling Verification

| Interaction | Mechanism | Coupling? |
|-------------|-----------|-----------|
| Delete → Hierarchy tracker | `GDeleteSequences` vs `GHierarchySequences` | **ZERO** — fully independent sequence trackers. Delete never reads/writes `GHierarchySequences`. |
| Delete → Hierarchy deferred queue | `PendingHierarchyAttachments.RemoveAll()` | **Read-only, bounded access** — delete may evict entries from the deferred queue, but does NOT modify the queue's ownership model, iteration logic, or state machine. |
| Delete → Tombstone map | `GDeleteTombstoneMap` | **Self-contained** — delete owns its tombstone map. No other lane reads it (yet). In Stage 9, other lanes will read it via an additive tombstone check at the top of their handlers. |
| Delete → ActorCache | `ActorCache.Remove()` | **Shared but properly isolated** — delete removes entries from ActorCache (same as legacy `HandleDeleteObject`). No other lane's state machine is coupled to ActorCache entry existence. |

**Verdict**: Zero cross-lane sequence coupling confirmed. All interactions are
through well-defined, bounded interfaces.

---

## 4. Additive-Only Verification

| Addition | Location | Type |
|----------|----------|------|
| `PT_Delete = 0x0E` | `SyncTypes.h` enum | Constant |
| `LIVE_SYNC_DELETE_V5_SIZE = 28` | `SyncTypes.h` constants | Constant |
| `0x0E` in FNV signature | `SyncTypes.h` + `network.py` | Additive byte |
| `FDeleteSequenceTracker` | `SyncTypes.h` | New struct |
| 8 delete counters | `SyncTypes.h` FLiveSyncStats | New fields |
| `GDeleteSequences` | `UELiveSyncSubsystem.cpp` | New global |
| `GDeleteTombstoneMap` | `UELiveSyncSubsystem.cpp` | New global |
| `HandleDelete()` | `UELiveSyncSubsystem.cpp` | New function |
| `case PT_Delete:` | `UELiveSyncSubsystem.cpp` | New case branch |
| Clear in `StopNetworkThread()` | `.cpp` | Additive line |
| Clear in `ConsoleReset()` | `.cpp` | Additive lines |
| Clear in `HandleEndSnapshot()` | `.cpp` | Additive line |

**Verdict**: All Phase 6E additions are **purely additive**. No existing code
path is modified.

---

## 5. No Generalized Semantic Framework

| Check | Status |
|-------|--------|
| Does delete introduce a generalized packet dispatch system? | ❌ NO — delete uses the same isolated case-branch pattern as rename, visibility, and hierarchy. |
| Does delete introduce a generic semantic event base class? | ❌ NO — there is no shared base class or virtual dispatch. Each lane has its own tracker type, handler function, and counters. |
| Does delete share tracker infrastructure with other lanes? | ❌ NO — `FDeleteSequenceTracker` is a distinct type (same pattern, different type). |
| Does delete introduce any virtual functions or runtime type dispatch? | ❌ NO — all dispatch is compile-time via the `case PT_Delete:` branch. |

---

## 6. Implementation Constraints for Stages 1–3

| Constraint | Detail |
|------------|--------|
| No actor destruction | `HandleDelete()` in Stages 1–3 is **log-only**. It must NOT call `Actor->Destroy()` or remove entries from `ActorCache`. |
| No hierarchy queue mutation | Stage 1–3 must NOT call `PendingHierarchyAttachments.RemoveAll()`. That is Stage 8 work. |
| No tombstone insertion | `GDeleteTombstoneMap.Add()` is NOT called in Stages 1–3. `GDeleteTombstoneMap.Contains()` IS allowed for read-only tombstone lookups. |
| No Blender emission | No changes to Blender `sync.py` or new `serialize_delete()` in `network.py`. Stage 10 work. |
| No DeferredDeleteQueue | The deferred delete queue is not implemented until Stage 9. During Stages 1–3, delete packets received during `bInSnapshotBuild` are logged but NOT deferred. |

---

## Audit Summary

| Category | Verdict |
|----------|---------|
| Frozen systems | ✅ ALL INTACT — zero modifications |
| Parser isolation | ✅ PRESERVED — independent case branch |
| Cross-lane coupling | ✅ ZERO — no sequence or state coupling |
| Additive-only | ✅ CONFIRMED — all additions are additive |
| Generalized framework | ✅ AVOIDED — per-lane pattern maintained |
| Stage boundaries | ✅ Stages 1–3 scope respected — no actor destruction, no queue mutation, no tombstone insertion, no Blender emission |

**Status: PASS — GO for Stages 1–3 implementation.**
