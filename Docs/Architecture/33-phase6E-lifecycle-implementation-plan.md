# Phase 6E — Lifecycle/Delete Replication: Implementation Plan

> **Created**: 2026-05-26
> **Status**: STABILIZED — ALL DEVELOPMENT STAGES 0-13 COMPLETE (308/308 tests pass, 102/102 audit checks pass)
> **Predecessors**: Scope Lock (`29-phase6E-lifecycle-scope-lock.md`) · Vertical Slice Design (`30-phase6E-vertical-slice-lifecycle.md`) · Threat Audit (`31-phase6E-lifecycle-threat-audit.md`) · Design Remediation (`32-phase6E-remediation-summary.md`)
> **Next**: None — Lifecycle/Delete lane is complete
>
> This document defines the **operational implementation plan** for the
> lifecycle/delete semantic lane. It bridges the design phase (what to build)
> to the implementation phase (how to build it safely).
>
> **This is a planning document. No runtime code has been modified.**
> **Implementation Note (2026-05-26)**: All development stages 0-13 complete. See `35-phase6E-live-runtime-validation.md`
> for the full stabilization report. Plan stages 4-12 map to development stages 0-11 (parser isolation,
> replay rejection, tombstone map, basic destroy, detach cascade, CREATE blocking, snapshot deferral,
> reconnect determinism, suppression, Blender detection, Blender serialization all implemented).
> Stage 12 (validation expansion) and Stage 13 (stabilization) added in the final pass:
> 21 new test sections, 114 new tests, 53 new audit checks, 17/17 stabilization criteria met.

---

## Table of Contents

1. [Implementation Philosophy](#1-implementation-philosophy)
2. [Runtime Preservation Checklist](#2-runtime-preservation-checklist)
3. [Planned Packet Integration Order](#3-planned-packet-integration-order)
4. [Tombstone Map Plan](#4-tombstone-map-plan)
5. [Delete Sequence Tracker Plan](#5-delete-sequence-tracker-plan)
6. [Deferred Hierarchy Eviction Plan](#6-deferred-hierarchy-eviction-plan)
7. [Parser Isolation Plan](#7-parser-isolation-plan)
8. [Runtime Interaction Plan](#8-runtime-interaction-plan)
9. [Observability Integration Plan](#9-observability-integration-plan)
10. [Validation Plan](#10-validation-plan)
11. [Rollback Strategy](#11-rollback-strategy)
12. [Risk Containment Plan](#12-risk-containment-plan)
13. [Test Matrix Planning](#13-test-matrix-planning)
14. [Final Go/No-Go Gate](#14-final-gono-go-gate)

---

## 1. Implementation Philosophy

### 1.1 Guiding Principles

Lifecycle/delete is the first **identity-destruction semantic lane**. Unlike
rename, visibility, or hierarchy (all reversible property mutations), delete
is **terminal**. Every packet permanently removes an actor from the scene.

**Implementation priority order** (ranked from most to least important):

| Priority | Principle | Why |
|----------|-----------|-----|
| P0 | **Non-resurrection guarantee** | A deleted actor must NEVER be resurrected by stale replay, reconnect snapshot reordering, or any other mechanism within the same connection. |
| P1 | **Determinism** | Same input → same output. Deletes are order-sensitive (child-before-parent), sequence-sensitive, and reconnect-sensitive. |
| P2 | **Bounded behavior** | Every operation has a known worst-case cost. Tombstone map bounded at 2048. Sequence tracker bounded at 2048. No unbounded retries, no unbounded memory growth. |
| P3 | **Observability** | Every state transition is logged and counted. No silent failures, no hidden state, no black-box behavior. Tombstone hits, stale rejections, and children-detached events must all be visible. |
| P4 | **Replay safety** | Reconnect replay is deterministic and convergent. Deleted objects stay dead; live objects stay alive. Tombstone map is cleared on reconnect intentionally — snapshot authority governs. |
| P5 | **Runtime preservation** | Frozen systems remain untouched. Semantic lane is parser-isolated and data-path-isolated. |
| P6 | **Performance** | Acceptable for realtime use (delete events are low-frequency, so this is the lowest priority). |

### 1.2 Correctness Over Optimization

```
DELETE IMPLEMENTATION CONTRACT:

1. If it can resurrect a deleted actor, it is not correct.
2. If it is not deterministic, it is not correct.
3. If it is not observable, it is not debuggable.
4. If it is not bounded, it is not safe.
5. If it modifies a frozen system, it is not allowed.
6. Optimization is deferred until correctness is proven.
```

### 1.3 Incremental Implementation

Every stage of implementation must:
- Be independently testable before the next stage begins
- Have a defined pass criterion before proceeding
- Have a rollback path if the criteria are not met
- Add observability (logs + counters) at the same time as functionality

### 1.4 What We Are Building

```
The lifecycle/delete semantic lane is a PURELY ADDITIVE system:

NEW files:   None (additions to existing files only)
NEW types:   PT_Delete = 0x0E
             FDeleteSequenceTracker
             FDeleteTombstoneMap (TMap<FGuid, uint32>)
             FScopedDeleteSuppression
             (plus counters, logs, and profiler scopes)

MODIFIED files (planned):
  SyncTypes.h:       PT_Delete constant, FDeleteSequenceTracker, counters,
                     kValidTypes, FNV-signatured type list
  UELiveSyncSubsystem.h:  HandleDelete() declaration, ProcessDeletePackets()
  UELiveSyncSubsystem.cpp: New case branch (0x0E), HandleDelete() body,
                           ProcessDeletePackets(), deferred entry eviction,
                           tombstone clear in StopNetworkThread/ConsoleReset,
                           tombstone check in RecoverMissingActors
  network.py:        PT_DELETE constant, serialize_delete()
  sync.py:           _delete_sequences per-GUID counter, ReferenceError path

FROZEN files (NOT modified):
  LiveSyncQueue.h, PendingAssetQueue.h, LiveSyncRunnable.h/cpp,
  AssetIdentityTypes.h, SLiveSyncStatusWidget.*, SLiveSyncDiagnosticsWidget.*,
  UELiveSyncEditorModule.*
```

---

## 2. Runtime Preservation Checklist

### 2.1 Frozen System Declaration

The following systems are **FROZEN** and must NOT be modified, extended,
refactored, or inspected at runtime by the lifecycle/delete semantic lane:

| System | File(s) | Risk if Modified |
|--------|---------|------------------|
| Packet parser (version dispatch, magic, header parsing, FNV validation) | `UELiveSyncSubsystem.cpp` (ProcessBinaryPacket outer dispatch) | Backward compat breakage, malformed packet crashes |
| Tick pipeline ordering | `UELiveSyncSubsystem.cpp` (main Tick) | Transform-before-spawn races; BEGIN/END imbalance |
| Queue ownership (FLiveSyncQueue) | `LiveSyncQueue.h` | Data races, queue corruption, use-after-free |
| Queue ownership (FLiveSyncPendingAssetQueue) | `PendingAssetQueue.h` | Data races, asset resolution corruption |
| Network thread lifecycle & shutdown order | `LiveSyncRunnable.h/cpp` | Game thread deadlock (Linux: missing Shutdown before Close) |
| Thread ownership (network enqueue only) | All runtime files | Cross-thread UObject access crashes |
| FSyncTransformState layout | `SyncTypes.h` | Wire format incompatibility, transform evaluation corruption |
| 24-byte header layout | `SyncTypes.h` (implicit) | Protocol breakage across all versions |
| InterpolateTransforms | `UELiveSyncSubsystem.cpp` | Transform drift for attached children |
| UpdateTargetTransform | `UELiveSyncSubsystem.cpp` | Transform ingestion corruption |
| AttachToParent | `UELiveSyncSubsystem.cpp` | Runtime hierarchy corruption |
| ResolvePendingAttachments | `UELiveSyncSubsystem.cpp` | Runtime deferred attachment corruption |
| DetachFromParent | `UELiveSyncSubsystem.cpp` | Runtime detach corruption |
| PendingAttachments array | `UELiveSyncSubsystem.h` | Runtime deferred queue — FROZEN |
| Heartbeat timeout (15s threshold) | `LiveSyncRunnable.cpp` | Connection state machine desync |
| BEGIN/END tracing at every Tick stage | `UELiveSyncSubsystem.cpp` | Removing would blind future debugging |
| **PendingHierarchyAttachments** (Phase 6D) | `UELiveSyncSubsystem.h` | Hierarchy deferred queue — FROZEN. HandleDelete may READ to evict entries but MUST NOT modify the queue's ownership model. |
| **FHierarchySequenceTracker** (Phase 6D) | `SyncTypes.h` | Hierarchy tracker — FROZEN. HandleDelete MUST NOT update. |

### 2.2 Pre-Implementation Audit Checklist

Before ANY implementation begins, verify the following:

| # | Check | Status |
|---|-------|--------|
| A1 | Vertical slice design reviewed and all 4 P1 findings resolved | ✅ RESOLVED — 32-phase6E-remediation-summary.md |
| A2 | No modifications to frozen system list (above) are required by the design | ✅ CONFIRMED — zero frozen-zone modifications |
| A3 | PT_Delete type byte (0x0E) does not conflict with existing types | ✅ CONFIRMED — 0x0E is next available after PT_Hierarchy (0x0D) |
| A4 | All counters defined in design doc (8 total) | ✅ CONFIRMED — §9 of vertical slice, §3.7 of scope lock |
| A5 | All log prefixes defined and consistent with conventions | ✅ CONFIRMED — §9.2 of vertical slice |
| A6 | All profiler scope names defined and follow naming convention | ✅ CONFIRMED — §9.3 of vertical slice |
| A7 | Rollback strategy documented and understood | PENDING (this document) |
| A8 | Test framework ready for lifecycle validation tests | PENDING |
| A9 | Architecture review sign-off obtained | PENDING |
| A10 | Three-barrier stale rejection system formally proved | ✅ PROVED — §7.2 of vertical slice |
| A11 | CREATE-blocked-by-tombstone rule formally proved | ✅ PROVED — §3.5 of vertical slice |
| A12 | Reconnect determinism formally proved (snapshot cannot resurrect) | ✅ PROVED — §7.1 of vertical slice |

### 2.3 Per-Stage Audit Gate

Before each implementation stage begins:
1. Re-read the frozen system list
2. Verify the stage does not require any frozen system modification
3. If frozen system modification appears necessary → PAUSE → ADR review → defer
4. If the stage is purely additive (new case branch, new handler, new data) → proceed

---

## 3. Planned Packet Integration Order

### 3.1 Implementation Sequence

Implementation follows a **bottom-up dependency order**: each stage builds on
the previous stage's output. Stages are independently testable.

```
Stage 0:  Pre-implementation audit & environment setup
Stage 1:  PT_Delete enum reservation (SyncTypes.h + network.py)
Stage 2:  FNV signature update (both sides)
Stage 3:  FDeleteSequenceTracker + counters (UE, SyncTypes.h)
Stage 4:  Parser-isolated packet branch (UE, ProcessBinaryPacket)
            └── No handler yet — just parse + sequence check + reject
Stage 5:  Replay rejection (UE, HandleDelete stub)
            └── Sequence check + origin tagging + logging
Stage 6:  Tombstone map (UE, GDeleteTombstoneMap, bounded 2048 LRU)
Stage 7:  HandleDelete() — basic destroy + tombstone record
            └── Actor->Destroy() + tombstone map insert + FScopedDeleteSuppression
Stage 8:  Parent-delete-child-detach cascade + deferred hierarchy entry eviction
            └── GetAttachedChildren() → DetachFromActor() each → RemoveAll on PendingHierarchyAttachments
Stage 9:  CREATE tombstone blocking + delete-during-replay deferral
            └── Tombstone check gates all handlers; delete defers to after EndSnapshot during replay
Stage 10: Blender detection + serialization (sync.py + network.py)
            └── ReferenceError catch → serialize_delete() → PT_Delete emission, per-GUID _delete_sequences
Stage 11: Standalone validation tests (tests/phase6_lifecycle_validation.py)
            └── Wire format, sequence rejection, tombstone LRU, detach cascade
Stage 12: Integration tests (UE required)
            └── Basic delete, parent delete, orphan, reconnect, mixed lane
Stage 13: Observability + diagnostics integration
            └── 8 counters, 2 profiler scopes, log prefixes, DumpState
Stage 13: Soak + stress + live runtime stabilization
            └── Long-duration, storm, mixed traffic, reconnect, fuzz
```

### 3.2 Dependency Graph

```
Stage 0 (audit)
  │
  ├── Stage 1 (enum) → Stage 2 (FNV)
  │
  ├── Stage 3 (tracker + counters) ────────────────┐
  │                                                 │
  ├── Stage 4 (parser) ──→ Stage 5 (replay) ────────┤
  │                       Stage 6 (tombstone)        │
  │                       Stage 7 (basic destroy) ───┤
  │                       Stage 8 (detach cascade)   │
  │                       Stage 9 (CREATE blocking)  │
  │                       Stage 10 (Blender emit)    │
  │                                                 │
  └── Stage 11 (standalone tests) ────────────────────┘
  └── Stage 12 (integration tests)
  └── Stage 13 (observability)
  └── Stage 13 (stabilization)
```

### 3.3 Prohibited Approaches

| Approach | Why Prohibited |
|----------|----------------|
| "Implement everything simultaneously" | No incremental verification. If something breaks, root cause is ambiguous. |
| "Skip the parser isolation stage" | Parser is the entry point. Without it, nothing else can be tested. |
| "Add Blender emission before UE handling" | Blender sends packets that UE doesn't understand → log spam, malformed packet counters. UE must be ready first. |
| "Skip tombstone map — rely on ActorCache alone" | ActorCache barrier alone cannot prevent stale replay during a live connection where the actor was recreated between the tombstone deletion and a stale replay. Three-barrier system required by design. |
| "Implement detach cascade before basic destroy" | Basic destroy (Stage 7) must be verified before adding the detach cascade (Stage 8). Without basic destroy, cascade cannot be tested independently. |
| "Optimize before correctness" | No perf work until all stages pass validation. |

### 3.4 Stage Entry/Exit Criteria

```
Stage N:
  Entry: All stages < N are complete and passing validation.
  Exit: Stage N functionality implemented, observable, tested.
  Rollback: If Stage N fails validation for >2 consecutive attempts,
            roll back to Stage N-1 state and re-verify.
```

### 3.5 Stage Details

#### Stage 0: Pre-Implementation Audit

| Item | Description |
|------|-------------|
| Entry | Scope lock, vertical slice, threat audit, remediation all complete |
| Work | Verify pre-implementation checklist (§2.2). Confirm no frozen-zone modifications required. Set up test infrastructure. |
| Exit | All A1-A12 checks pass. Test framework ready. |
| Observability | N/A |

#### Stage 1: PT_Delete Enum Reservation

| Item | Description |
|------|-------------|
| Entry | Stage 0 complete |
| Work | Add `PT_Delete = 0x0E` to `SyncTypes.h` packet type enum. Add `PT_DELETE = 0x0E` to `network.py` constants. |
| Exit | Enum value does not conflict. Both sides define `0x0E`. Compilation succeeds. |
| Observability | N/A |

#### Stage 2: FNV Signature Update

| Item | Description |
|------|-------------|
| Entry | Stage 1 complete |
| Work | Add `0x0E` to the FNV-signatured bytes list in both `SyncTypes.h:755-761` and `network.py:38-42`. Recompute both hashes to match. |
| Exit | Both sides produce matching FNV signatures. `kValidTypes` includes `0x0E`. |
| Observability | N/A |

#### Stage 3: FDeleteSequenceTracker + Counters

| Item | Description |
|------|-------------|
| Entry | Stage 2 complete |
| Work | Define `FDeleteSequenceTracker` in `SyncTypes.h` (identical pattern to `FRenameSequenceTracker`, `FHierarchySequenceTracker`). Declare `GDeleteSequences` extern. Add 8 delete counters to `FLiveSyncStats`: `DeletesProcessed`, `DeleteStaleRejections`, `DeleteReplayApplied`, `DeleteReplaySkipped`, `DeleteTombstoneHits`, `DeleteChildrenDetached`, `DeleteDeferredEvictions`, `DeleteTombstoneEvictions`. |
| Exit | Tracker compiles, stores/retrieves correctly. Counters declared, initialized. |
| Observability | 8 counter fields, `[DELETE]` log prefix in tracker operations |

#### Stage 4: Parser Isolation

| Item | Description |
|------|-------------|
| Entry | Stage 3 complete |
| Work | Add `case PT_Delete:` branch in `ProcessBinaryPacket`. Parse 28-byte fixed payload per object (GUID + seq + ts). Malformed packet checks (payload % 28 != 0, count == 0, count > 1024, all-zero GUID). Sequence check against `GDeleteSequences`. Logging. NO handler call yet. |
| Exit | Malformed PT_Delete packets rejected. Valid packets parsed. Sequence check functional. |
| Observability | `[DELETE]` batch parsed log (verbose), `[DELETE]` malformed warning, `Stats.MalformedPackets` increment |

See §7.2 for full parser pseudocode.

#### Stage 5: Replay Rejection

| Item | Description |
|------|-------------|
| Entry | Stage 4 complete |
| Work | Add `HandleDelete()` stub. Tag packets with `EChangeOrigin::Replay` or `EChangeOrigin::RemoteReplicated`. Log origin. Stale/duplicate rejection via `GDeleteSequences.IsStaleOrDuplicate()`. Update tracker on acceptance. Do NOT call `Actor->Destroy()` yet. |
| Exit | Replay-tagged packets logged correctly. Stale sequences rejected. Tracker updated. |
| Observability | `DeleteReplayApplied++`, `DeleteReplaySkipped++`, `DeleteStaleRejections++` |

#### Stage 6: Tombstone Map

| Item | Description |
|------|-------------|
| Entry | Stage 5 complete |
| Work | Declare `TMap<FGuid, uint32> GDeleteTombstoneMap` in `UELiveSyncSubsystem.cpp`. Bounded at 2048 entries with LRU eviction (remove oldest key when at capacity). Cleared in `StopNetworkThread()` and `ConsoleReset()`. Add tombstone check to `HandleDelete()` stub — if GUID in tombstone map, increment `DeleteTombstoneHits` and return. |
| Exit | Tombstone map rejects packets for tombstoned GUIDs. LRU eviction verified at 2048. Clear on reconnect/reset verified. |
| Observability | `DeleteTombstoneHits++`, `DeleteTombstoneEvictions++`, `[DELETE][TOMBSTONE]` verbose log |

#### Stage 7: Basic Destroy + FScopedDeleteSuppression

| Item | Description |
|------|-------------|
| Entry | Stage 6 complete |
| Work | Full `HandleDelete()`: check `ActorCache` → call `Actor->Destroy()` → remove from `ActorCache` → add tombstone entry → update sequence tracker. Wrap destroy in `FScopedDeleteSuppression` RAII guard (suppression enter before destroy, exit after). Handle missing actor case (silently discard). Handle duplicate delete (tombstone check catches it). |
| Exit | Actor destroyed and removed from ActorCache. Tombstone recorded. Suppression guard enter/exit logged. Missing actor silently discarded. |
| Observability | `DeletesProcessed++`, `[DELETE]` actor destroyed log, `[DELETE][SUPPRESS]` verbose enter/exit, `[DELETE][MISSING]` verbose for missing actor |

#### Stage 8: Parent-Delete-Child-Detach Cascade + Deferred Eviction

| Item | Description |
|------|-------------|
| Entry | Stage 7 complete |
| Work | Before destroying parent: iterate `GetAttachedChildren()` → `DetachFromActor(KeepWorldTransform)` each → update `FSyncTransformState` (bHasParent=false, ParentGuid=0) → evict pending deferred hierarchy entries for each child. After destroy: evict ALL `PendingHierarchyAttachments` entries where `Entry.ParentGuid == TargetGuid`. Evict entries where `Entry.ChildGuid == TargetGuid`. |
| Exit | Children detached to root before parent destroy. Child `FSyncTransformState` updated. Deferred entries evicted for all three categories (child-of-deleted-parent, deleted-child, deleted-parent). No hierarchy sequence tracker interaction. |
| Observability | `DeleteChildrenDetached++`, `DeleteDeferredEvictions++`, `[DELETE][DETACH]` logs, `[DELETE][EVICT]` log per eviction batch |

#### Stage 9: CREATE Tombstone Blocking + Delete-During-Replay Deferral

| Item | Description |
|------|-------------|
| Entry | Stage 8 complete |
| Work | Add tombstone check gate at the top of EVERY semantic handler (Transform, Create, AssetDef, Visibility, Rename, Hierarchy) — if GUID in tombstone map, discard. For PT_Create specifically: blocked by tombstone. For PT_Delete during snapshot replay (`bInSnapshotBuild == true`): if the GUID's CREATE packet has not yet been processed, defer the delete to after `EndSnapshot` (accumulate in a temporary array, process in `HandleEndSnapshot()`). |
| Exit | CREATE blocked by tombstone. Delete deferred during replay. No actor flicker (create-then-destroy) during reconnect. |
| Observability | `DeleteTombstoneHits++` for CREATE block, `[DELETE][REPLAY]` for deferred delete, `[REPLAY]` log prefix |

**Frozen-runtime constraint**: The tombstone check gates are purely additive —
they sit at the top of existing handlers and return early if tombstoned.
They do not modify the handler logic itself.

#### Stage 10: Blender Detection + Serialization

| Item | Description |
|------|-------------|
| Entry | Stage 9 complete |
| Work | In `sync.py`: add `_delete_sequences` dict (per-GUID uint32, cleared on disconnect). In `ReferenceError` catch: serialize and queue PT_Delete packet. In `network.py`: add `serialize_delete()` producing 28-byte fixed payload (GUID(16) + seq(4) + ts(8)). Add `PT_DELETE` case to `send_objects()` batch dispatch. |
| Exit | Blender detects deletion (existing `ReferenceError` catch). Sends PT_Delete packet. Per-GUID sequence monotonic. No duplicate sends. |
| Observability | `[DELETE]` Blender-side log on detection. Sequence counter logged per delete. |

#### Stage 11: Standalone Validation Tests

| Item | Description |
|------|-------------|
| Entry | Stage 10 complete |
| Work | Wire format validation (28-byte payload, field offsets). Sequence rejection (first accepted, duplicate rejected, stale rejected, higher-seq accepted). Tombstone LRU eviction (2048 boundary). Detach cascade (simulated ActorCache). Deferred entry eviction (simulated PendingHierarchyAttachments). FNV signature match test. |
| Exit | All standalone tests pass. |
| Observability | N/A |

#### Stage 12: Integration Tests (UE Required)

| Item | Description |
|------|-------------|
| Entry | Stage 11 complete |
| Work | Basic delete (send PT_Delete → actor destroyed). Parent delete (parent destroyed → children detached to root). Orphan parent delete (deferred child's parent deleted → child becomes root). Reconnect (delete → disconnect → reconnect → actor stays dead). Mixed lane (delete + transform, delete + rename, delete + visibility, delete + hierarchy). |
| Exit | All integration tests pass on UE editor on `:57000`. |
| Observability | N/A |

#### Stage 13: Observability

| Item | Description |
|------|-------------|
| Entry | Stage 12 complete |
| Work | Wire all 8 counters to `DumpState` and `ConsoleReset`. Add profiler scopes (`UELiveSync_HandleDelete`, `UELiveSync_ProcessDeletePackets`). Add all log prefix patterns. Add tombstone map size to `DumpState`. Delete tracker entries to `DumpState`. Add 2 profiler scopes. |
| Exit | All 8 counters increment correctly. All log prefixes present. Profiler scopes visible in UE traces. |
| Observability | Full observability suite |

#### Stage 13: Soak + Stress + Live Runtime Stabilization

| Item | Description |
|------|-------------|
| Entry | Stage 13 observability complete |
| Work | 10-minute mixed soak (delete + transform + rename + visibility + hierarchy + reconnect). 100-delete storm. 5 reconnect cycles with delete replay. Fuzz: malformed PT_Delete packets. |
| Exit | No crashes, no memory leaks, no orphan drift, no resurrection. All counters reasonable. |
| Observability | Full observability suite confirms correct behavior |

---

## 4. Tombstone Map Plan

### 4.1 Map Definition

```cpp
// In UELiveSyncSubsystem.cpp or SyncTypes.h
//
// Tombstone map: GUID → last delete sequence number.
// Bounded at 2048 entries with FIFO eviction on overflow.
// Cleared on StopNetworkThread() and ConsoleReset().
// Game-thread only (same as all actor-state maps).

static constexpr uint32 MAX_TOMBSTONE_ENTRIES = 2048;

TMap<FGuid, uint32> GDeleteTombstoneMap;
```

### 4.2 Ownership

| Property | Specification |
|----------|---------------|
| **Data structure** | `TMap<FGuid, uint32> GDeleteTombstoneMap` — global (same pattern as `GRenameSequences`) |
| **Owner thread** | Game thread only. All mutations occur on game thread. |
| **Read access** | Game thread only (from all semantic handlers via tombstone check) |
| **Write access** | Game thread only. Added by `HandleDelete()` on successful destroy, removed on eviction/clear. |
| **Iteration** | `DumpState()` for diagnostics; eviction on overflow |
| **Max size** | 2048 entries (matching all other bounded structures) |

### 4.3 Mutation Points

| Mutation | When | Where |
|----------|------|-------|
| **Add** | Actor successfully destroyed | `HandleDelete()` — after `Actor->Destroy()`, before return |
| **Remove** (eviction) | Tombstone map reaches 2048 capacity | Insert path — removes oldest key before adding new one |
| **Clear** (reconnect) | `StopNetworkThread()` or `ConsoleReset()` | Dedicated clear call |
| **Read** (check) | Every semantic handler entry | Top of each handler — return early if GUID is tombstoned |

### 4.4 Eviction Policy

| Eviction Trigger | Behavior | Log |
|------------------|----------|-----|
| **Map overflow** (2048+1) | Remove arbitrary entry (`TMap` iterator order). Increment `DeleteTombstoneEvictions`. | `[DELETE][TOMBSTONE] Evicted tombstone: guid=%s seq=%u (map full)` |
| **Reconnect** | `Empty()` entire map. | `[DELETE] Tombstone map cleared (reason=reconnect)` |
| **ConsoleReset** | `Empty()` entire map. Zero all counters. | `[DELETE] Tombstone map cleared (reason=reset)` |

### 4.5 Tombstone Check Pattern

```cpp
// Gate at the top of every semantic handler:
if (const uint32* TombstoneSeq = GDeleteTombstoneMap.Find(TargetGuid))
{
    Stats.DeleteTombstoneHits.fetch_add(1, std::memory_order_relaxed);
    UE_LOG(LogLiveSync, Verbose,
        TEXT("[DELETE][TOMBSTONE] Blocked packet type=0x%02x guid=%s tombstone_seq=%u"),
        PacketType, *TargetGuid.ToString(EGuidFormats::Digits), *TombstoneSeq);
    return;
}
```

### 4.6 Reconnect Cleanup

```
StopNetworkThread():
    GDeleteTombstoneMap.Empty();
    GDeleteSequences.Clear();

ConsoleReset():
    GDeleteTombstoneMap.Empty();
    GDeleteSequences.Clear();
    All 8 delete counters = 0;

HandleEndSnapshot():
    // Tombstone map is NOT cleared here — it persists across snapshot boundaries
    // within the same session. Cleared on reconnect only.
```

### 4.7 Key Design Invariant

```
Tombstones NEVER persist across connections.

On every reconnect, the tombstone map is cleared. The snapshot is the
authoritative state: if the deleted object is absent from the snapshot,
it stays dead. If the object was recreated in Blender, it is present
in the snapshot and correctly created in UE.
```

### 4.8 CREATE-Blocked-By-Tombstone Special Case

```
During snapshot replay, deletes are deferred to after EndSnapshot (§2.4 of
vertical slice). This means:

1. Intra-snapshot CREATEs are processed first.
2. If a CREATE and a DELETE for the same GUID appear in the same snapshot
   batch (race condition: user deleted object during snapshot build),
   the CREATE is processed first, then the DELETE is applied after
   EndSnapshot — but ONLY if the delete was deferred.

   Wait — this needs more precision:

   Scenario: Object A exists in Blender. User deletes A while snapshot
   is being built. Snapshot batch includes CREATE for A (built before
   delete). Delete packet arrives between BeginSnapshot and EndSnapshot.

   UE processes:
   - CREATE A → actor created (during snapshot replay, bInSnapshotBuild=true)
   - DELETE A → bInSnapshotBuild=true, so defer to after EndSnapshot
   - EndSnapshot → process deferred deletes → DELETE A → actor destroyed

   Net result: Actor A created then immediately destroyed. Flickers for
   one frame but correct final state. ✅

   Alternative scenario: Delete arrives AFTER EndSnapshot:
   - CREATE A → actor created (during snapshot replay)
   - EndSnapshot → snapshot done
   - DELETE A → normal processing → actor destroyed
   Same net result. ✅
```

---

## 5. Delete Sequence Tracker Plan

### 5.1 FDeleteSequenceTracker

```cpp
// New struct in SyncTypes.h (alongside FRenameSequenceTracker, etc.)
struct FDeleteSequenceTracker
{
    static constexpr uint32 MAX_TRACKED_GUIDS = 2048;
    TMap<FGuid, uint32> LastSequence;

    bool IsStaleOrDuplicate(const FGuid& Guid, uint32 IncomingSeq) const
    {
        if (const uint32* LastSeq = LastSequence.Find(Guid))
            return IncomingSeq <= *LastSeq;
        return false;
    }

    uint32 GetLastSeq(const FGuid& Guid) const
    {
        if (const uint32* LastSeq = LastSequence.Find(Guid))
            return *LastSeq;
        return 0;
    }

    void Update(const FGuid& Guid, uint32 AppliedSeq)
    {
        if (LastSequence.Num() >= MAX_TRACKED_GUIDS)
            LastSequence.Remove(LastSequence.CreateIterator().Key());
        LastSequence.Add(Guid, AppliedSeq);
    }

    void Clear()
    {
        LastSequence.Empty();
    }
};

// Global instance (same pattern as GRenameSequences, GVisibilitySequences, etc.)
extern FDeleteSequenceTracker GDeleteSequences;
```

### 5.2 Integration Points

| Point | Operation | Why |
|-------|-----------|-----|
| `HandleDelete()` — before destroying | `IsStaleOrDuplicate()` check | Reject stale/duplicate live delete packets |
| `HandleDelete()` — after successful destroy | `Update()` | Record the applied delete sequence |
| `HandleDelete()` — after tombstone check passes but ActorCache miss | Do NOT update tracker | No state change — the actor was already gone; updating tracker would skip future genuine deletes |
| `StopNetworkThread()` | `Clear()` | Fresh state on next connection |
| `ConsoleReset()` | `Clear()` + zero counters | Full reset |

### 5.3 Three-Barrier Stale Rejection

This is the most critical replay safety measure for delete. Unlike all prior
lanes, delete has **three independent safety barriers**:

| Barrier | What It Prevents | Across Reconnect? |
|---------|-----------------|-------------------|
| Sequence tracker (per-GUID) | Intra-connection duplicate/stale deletes | Cleared — does NOT protect across reconnect |
| Tombstone map | Intra-connection re-delete of same GUID | Cleared — does NOT protect across reconnect |
| **ActorCache existence check** | Any packet for a non-existent actor | **Yes** — actor must exist to be deleted |

The ActorCache existence check is the **only** barrier that works across
reconnect boundaries. A stale delete packet for a GUID that was never
created in this connection will find no actor and be silently discarded.

```cpp
void UUELiveSyncSubsystem::HandleDelete(FGuid TargetGuid, uint32 Seq, double Timestamp, EChangeOrigin Origin)
{
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_HandleDelete);

    // ---- BARRIER 1: Sequence check ----
    if (GDeleteSequences.IsStaleOrDuplicate(TargetGuid, Seq))
    {
        Stats.DeleteStaleRejections.fetch_add(1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[DELETE][STALE] Sequence stale: guid=%s incoming_seq=%u last_seq=%u"),
            *TargetGuid.ToString(EGuidFormats::Digits),
            Seq, GDeleteSequences.GetLastSeq(TargetGuid));
        return;
    }

    // ---- BARRIER 2: Tombstone check ----
    if (GDeleteTombstoneMap.Contains(TargetGuid))
    {
        Stats.DeleteTombstoneHits.fetch_add(1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[DELETE][TOMBSTONE] Already tombstoned: guid=%s seq=%u"),
            *TargetGuid.ToString(EGuidFormats::Digits), Seq);
        return;
    }

    // ---- BARRIER 3: ActorCache existence check ----
    AActor* TargetActor = FindActorFast(TargetGuid);
    if (!TargetActor)
    {
        // Actor does not exist — silently discard.
        // (Could be stale from previous connection, or delete-during-disconnect
        //  where the actor was never created in this connection.)
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[DELETE][MISSING] Actor not found: guid=%s seq=%u"),
            *TargetGuid.ToString(EGuidFormats::Digits), Seq);
        // Do NOT update tracker — no state change.
        return;
    }

    // ---- DETACH CASCADE (Stage 8) ----
    // ... (see §6 for deferred eviction, §8 for detach cascade) ...

    // ---- DESTROY ----
    {
        FScopedDeleteSuppression Suppression(TargetGuid);
        TargetActor->Destroy();
    }

    // ---- POST-DESTROY ----
    GDeleteTombstoneMap.Add(TargetGuid, Seq);
    GDeleteSequences.Update(TargetGuid, Seq);
    ActorCache.Remove(TargetGuid);

    Stats.DeletesProcessed.fetch_add(1, std::memory_order_relaxed);
    UE_LOG(LogLiveSync, Log,
        TEXT("[DELETE] Actor destroyed: guid=%s seq=%u"),
        *TargetGuid.ToString(EGuidFormats::Digits), Seq);
}
```

### 5.4 Tracker Bounded Eviction

When the tracker reaches 2048 entries, the oldest entry (arbitrary — `TMap`
iterator order) is evicted. This means the tracker may forget a GUID's last
sequence.

**Impact**: LOW. 2048 is a generous bound. Scene-wide GUID counts rarely
exceed 500. The evicted GUID is likely a long-since-deleted object.
Re-accepting a stale delete packet for it would hit the tombstone map
barrier (if still tombstoned) or the ActorCache barrier (if actor doesn't exist).

**Mitigation**: None beyond the 2048 bound. The three-barrier system
provides defense-in-depth.

---

## 6. Deferred Hierarchy Eviction Plan

### 6.1 Why Explicit Eviction Is Required

When a parent actor is deleted, its children are implicitly detached to root.
The hierarchy sequence tracker is NOT updated (by design — see DEL-001
resolution in §32-phase6E-remediation-summary.md). This means:

1. Child C was attached to parent P (hierarchy seq=N)
2. P is deleted → C is detached implicitly (tracker unchanged: seq=N)
3. C has a pending deferred hierarchy entry for parent X (seq=N+1)
4. `ResolveHierarchyAttachments` checks: `IsStaleOrDuplicate(C, N+1)` → tracker has N → N+1 > N → **not stale**
5. C is attached to X — **incorrect**: C should be root

Explicit eviction prevents this by removing all pending deferred entries
for children of the deleted parent, the deleted actor itself, and any
entries referencing the deleted actor as a parent.

### 6.2 Eviction Functions

```cpp
// In UELiveSyncSubsystem.cpp

void UUELiveSyncSubsystem::EvictDeferredEntriesForChild(const FGuid& ChildGuid)
{
    int32 Evicted = PendingHierarchyAttachments.RemoveAll(
        [&](const FPendingHierarchyAttachment& Entry)
        {
            return Entry.ChildGuid == ChildGuid;
        });
    if (Evicted > 0)
    {
        Stats.DeleteDeferredEvictions.fetch_add(Evicted, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Log,
            TEXT("[DELETE][EVICT] Evicted %d deferred entries for child=%s"),
            Evicted, *ChildGuid.ToString(EGuidFormats::Digits));
    }
}

void UUELiveSyncSubsystem::EvictDeferredEntriesForParent(const FGuid& ParentGuid)
{
    int32 Evicted = PendingHierarchyAttachments.RemoveAll(
        [&](const FPendingHierarchyAttachment& Entry)
        {
            return Entry.ParentGuid == ParentGuid;
        });
    if (Evicted > 0)
    {
        Stats.DeleteDeferredEvictions.fetch_add(Evicted, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Log,
            TEXT("[DELETE][EVICT] Evicted %d deferred entries for parent=%s"),
            Evicted, *ParentGuid.ToString(EGuidFormats::Digits));
    }
}
```

### 6.3 Integration Points

| Eviction Point | What Gets Evicted | When in HandleDelete |
|----------------|-------------------|---------------------|
| `Entry.ChildGuid == TargetGuid` | The actor being deleted has a pending deferred hierarchy entry | After existence check, before destroy |
| `Entry.ChildGuid == ChildGuid` for each child of deleted parent | The child's pending hierarchy intent was captured before parent delete | After child detach, before parent destroy |
| `Entry.ParentGuid == TargetGuid` | All deferred entries awaiting the deleted actor as parent | After destroy |

### 6.4 Insertion Order in HandleDelete

```cpp
void UUELiveSyncSubsystem::HandleDelete(FGuid TargetGuid, ...)
{
    // ... BARRIER 1-3 checks ...

    // ---- PHASE 1: Evict deferred entries for self (as child) ----
    EvictDeferredEntriesForChild(TargetGuid);

    // ---- PHASE 2: Detach children and evict their deferred entries ----
    TArray<AActor*> Children = TargetActor->GetAttachedChildren();
    for (AActor* Child : Children)
    {
        FGuid ChildGuid = ActorCacheToGuid(Child);  // Reverse lookup
        if (ChildGuid.IsValid())
        {
            EvictDeferredEntriesForChild(ChildGuid);
            Child->DetachFromActor(FDetachmentTransformRules::KeepWorldTransform);
            UpdateChildSyncTransformState(ChildGuid);  // Set bHasParent=false, ParentGuid=0
            Stats.DeleteChildrenDetached.fetch_add(1, std::memory_order_relaxed);
        }
    }

    // ---- PHASE 3: Destroy actor ----
    {
        FScopedDeleteSuppression Suppression(TargetGuid);
        TargetActor->Destroy();
    }

    // ---- PHASE 4: Evict deferred entries for self (as parent) ----
    EvictDeferredEntriesForParent(TargetGuid);

    // ---- PHASE 5: Record tombstone and update tracker ----
    GDeleteTombstoneMap.Add(TargetGuid, Seq);
    GDeleteSequences.Update(TargetGuid, Seq);
    ActorCache.Remove(TargetGuid);
    Stats.DeletesProcessed.fetch_add(1, std::memory_order_relaxed);
}
```

### 6.5 Observability

| Item | Detail |
|------|--------|
| Counter | `DeleteDeferredEvictions` — total deferred entries evicted across all three categories |
| Counter | `DeleteChildrenDetached` — children detached from parent before destroy |
| Log | `[DELETE][EVICT] Evicted %d deferred entries for child=%s` — per eviction batch |
| Log | `[DELETE][EVICT] Evicted %d deferred entries for parent=%s` — per eviction batch |
| Log | `[DELETE][DETACH] Detached child=%s from parent=%s` — per child detach |

### 6.6 Safety Properties

| Property | Guarantee |
|----------|-----------|
| **No double-attach** | Eviction prevents stale deferred resolution within same Tick |
| **No memory leak** | All three eviction categories bounded by `PendingHierarchyAttachments` max size (2048) |
| **Deterministic** | `GetAttachedChildren()` returns stable order; `RemoveAll` is deterministic |
| **No tracker coupling** | Hierarchy sequence tracker is NEVER modified by delete handler |

---

## 7. Parser Isolation Plan

### 7.1 Parser Structure

The delete parser MUST be:

1. An isolated `case PT_Delete:` branch in `ProcessBinaryPacket`
2. After all existing case branches (by type byte ordering: `0x0E` > `0x0D`)
3. NOT modifying any existing case branch
4. NOT entering `FLiveSyncQueue` (delete packets are parsed and handled
   immediately, not enqueued as `FLiveSyncPacket` variants)

### 7.2 Parser Pseudocode

```cpp
// In ProcessBinaryPacket, after the last existing case branch:
case PT_Delete:
{
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessDeletePackets);

    constexpr int32 ObjSize = 28;  // 16+4+8
    const int32 Count = PayloadSize / ObjSize;

    // ---- MALFORMED PACKET CHECKS ----
    if (PayloadSize % ObjSize != 0 || Count == 0)
    {
        Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Warning,
            TEXT("[DELETE] Malformed packet — payload %d bytes (expected multiple of %d)"),
            PayloadSize, ObjSize);
        return;
    }

    if (Count > MAX_OBJECTS_PER_BATCH)  // 1024
    {
        Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Warning,
            TEXT("[DELETE] Malformed packet — batch too large: %d objects (max %d)"),
            Count, MAX_OBJECTS_PER_BATCH);
        return;
    }

    // ---- PER-OBJECT PARSE LOOP ----
    for (int32 i = 0; i < Count; i++)
    {
        const uint8* ObjPtr = PayloadPtr + (i * ObjSize);

        uint32 GuidParts[4];
        FMemory::Memcpy(GuidParts, ObjPtr, 16);
        FGuid TargetGuid(GuidParts[0], GuidParts[1], GuidParts[2], GuidParts[3]);

        // ---- ALL-ZERO GUID CHECK ----
        if (!TargetGuid.IsValid())
        {
            Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[DELETE] Malformed packet — all-zero GUID at index %d"), i);
            continue;  // Skip this object, continue with batch
        }

        uint32 Seq;
        FMemory::Memcpy(&Seq, ObjPtr + 16, 4);

        double Timestamp;
        FMemory::Memcpy(&Timestamp, ObjPtr + 20, 8);

        EChangeOrigin Origin = bInSnapshotBuild
            ? EChangeOrigin::Replay
            : EChangeOrigin::RemoteReplicated;

        FScopedChangeOrigin OriginScope(Origin);

        // ---- DURING SNAPSHOT REPLAY: defer delete if CREATE not yet processed ----
        if (bInSnapshotBuild && !HasCreateBeenProcessed(TargetGuid))
        {
            // Defer to after EndSnapshot
            DeferredDeleteQueue.Add({TargetGuid, Seq, Timestamp});
            Stats.DeleteReplaySkipped.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[DELETE][REPLAY] Deferred delete during replay: guid=%s seq=%u"),
                *TargetGuid.ToString(EGuidFormats::Digits), Seq);
            continue;
        }

        HandleDelete(TargetGuid, Seq, Timestamp);
    }
    return;
}
```

Note: `HasCreateBeenProcessed()` and `DeferredDeleteQueue` are new data
structures added alongside the parser (not modifications to frozen code).
`DeferredDeleteQueue` is a `TArray<FDeferredDelete>` cleared in
`HandleEndSnapshot()`, `StopNetworkThread()`, and `ConsoleReset()`.

### 7.3 Malformed Packet Handling Checklist

| # | Check | Behavior |
|---|-------|----------|
| M1 | Payload size not a multiple of 28 bytes | Reject entire batch. `Stats.MalformedPackets++`. Warning log. |
| M2 | Object count == 0 | Reject. `Stats.MalformedPackets++`. Warning log. |
| M3 | Object count > MAX_OBJECTS_PER_BATCH (1024) | Reject. `Stats.MalformedPackets++`. Warning log. |
| M4 | Target GUID all-zero (invalid) | Reject single object. `Stats.MalformedPackets++`. Warning log. Continue to next object. |
| M5 | Partial batch truncation | First objects processed. Truncation detected by M1 on next TCP recv. |
| M6 | Boundary overflow during Memcpy | Guarded by M1 — if payload % 28 == 0, all 28-byte strides are in-bounds. |

### 7.4 Prohibited Patterns

| Pattern | Why Prohibited |
|---------|----------------|
| Entering delete packets into `FLiveSyncQueue` | Semantic events are NOT transform state. Delete packets are parsed and handled immediately on the game thread. |
| Modifying existing case branches | Each semantic lane has its own isolated case branch. No `else if` chains. |
| Reusing `FLiveSyncPacket` union for delete | Delete has its own wire format (28 bytes fixed). Parsing into `FLiveSyncPacket` would require extending the union, which is FROZEN. |
| Cross-packet coupling during parse | Each packet is parsed independently. No batch-level state machine. |

### 7.5 EndSnapshot Deferred Delete Processing

```cpp
void UUELiveSyncSubsystem::HandleEndSnapshot()
{
    // Clear hierarchy deferred queue (existing Phase 6D behavior)
    PendingHierarchyAttachments.Empty();

    // Process deferred deletes
    for (const FDeferredDelete& Del : DeferredDeleteQueue)
    {
        HandleDelete(Del.TargetGuid, Del.Seq, Del.Timestamp);
    }
    DeferredDeleteQueue.Empty();

    // ... rest of EndSnapshot handling ...
}

void UUELiveSyncSubsystem::StopNetworkThread()
{
    DeferredDeleteQueue.Empty();
    GDeleteTombstoneMap.Empty();
    GDeleteSequences.Clear();
    // ... existing cleanup ...
}
```

---

## 8. Runtime Interaction Plan

### 8.1 Interaction Boundaries

The lifecycle/delete semantic lane interacts with runtime systems through
**well-defined, read-only interfaces**:

```
ALLOWED RUNTIME ACCESS:

  Read-only queries:
    • FindActorFast(Guid) → AActor* (ActorCache lookup)
    • GetAttachedChildren() → TArray<AActor*> (UE engine API)
    • PendingHierarchyAttachments (read-only for eviction)
    • GDeleteTombstoneMap.Contains(Guid) (tombstone check)

  Write operations (direct engine API):
    • Actor->Destroy() (NOT K2_DestroyActor)
    • DetachFromActor(KeepWorldTransform) (for each child)

FORBIDDEN RUNTIME ACCESS:

  • FSyncTransformState direct modification (must use UpdateTargetTransform or similar)
  • FHierarchySequenceTracker (MUST NOT read or write)
  • PendingAttachments (frozen runtime array)
  • InterpolateTransforms()
  • ResolvePendingAttachments()
  • AttachToParent()
  • Any frozen system (see §2.1)
```

### 8.2 Semantic Lane Request Model

The semantic lane does NOT "own" actor lifetime. It **requests** destruction
of the actor through the same UE engine API (`Actor->Destroy()`) that any
other system would use.

```
Semantic Lane → "I intend actor G to be destroyed"
  → Existence check: does G exist in ActorCache?
  → Sequence check: is this not a stale/duplicate?
  → Tombstone check: was G already destroyed?
  → Children check: does G have children?
     → Detach children to root
     → Evict children's deferred hierarchy entries
  → Destroy actor via Actor->Destroy()
  → Record tombstone for G

The semantic lane does NOT:
  - Manage the actor lifecycle (IS ABOVE the lifecycle system)
  - Update interpolation state
  - Handle physics or collision cleanup (UE engine handles this)
  - Track destroyed actors across reconnects (tombstone cleared intentionally)
  - OWN the ActorCache — it only removes entries after successful destroy
```

### 8.3 Tick Pipeline Position

```
Current Tick pipeline (FROZEN):
  1. ProcessQueuedPackets()          ← PT_Delete parsed here
  2. ResolvePendingAttachments()     ← Runtime deferred (FROZEN)
  2a. ResolveHierarchyAttachments()  ← Hierarchy deferred (ADDED by Phase 6D)
  3. InterpolateTransforms()         ← FROZEN
  4. ResolveAssetIdentities()        ← FROZEN
  5. PurgeStaleActors()              ← FROZEN
  6. ...remaining pipeline...        ← FROZEN

ADDED by Phase 6E (within step 1):
  ProcessQueuedPackets() parses PT_Delete → calls HandleDelete() immediately.
  The actor is destroyed and removed from ActorCache during packet processing.
  This means:
  • ResolvePendingAttachments (step 2) will not find the deleted actor — correct
  • ResolveHierarchyAttachments (step 2a) will not find the deleted actor — correct
  • InterpolateTransforms (step 3) will not process the deleted actor — correct
  • PurgeStaleActors (step 5) also won't find the deleted actor — correct
```

**Why within step 1 (not between steps)?** Unlike hierarchy (which needs a
dedicated deferred resolution pass between ResolvePendingAttachments and
InterpolateTransforms), delete is immediate. Once the actor is destroyed,
all downstream pipeline stages naturally skip it (ActorCache miss).

**Exception**: Delete during snapshot replay is deferred to after EndSnapshot
(see Stage 9). This is because CREATE and DELETE for the same GUID in the
same batch must be ordered: CREATE first, then DELETE. Deferred deletes
are processed in `HandleEndSnapshot()`.

### 8.4 Deferred Delete Queue Ownership

```cpp
struct FDeferredDelete
{
    FGuid TargetGuid;
    uint32 Sequence;
    double Timestamp;
};

// In UELiveSyncSubsystem.h:
TArray<FDeferredDelete> DeferredDeleteQueue;
```

| Property | Specification |
|----------|---------------|
| **Owner** | `UUELiveSyncSubsystem` member |
| **Owner thread** | Game thread only |
| **Add** | `ProcessBinaryPacket` — case `PT_Delete` during `bInSnapshotBuild` with CREATE not yet processed |
| **Process** | `HandleEndSnapshot()` — iterates and calls `HandleDelete()` for each |
| **Clear** | `StopNetworkThread()`, `ConsoleReset()`, `HandleEndSnapshot()` after processing |

---

## 9. Observability Integration Plan

### 9.1 Profiler Scopes

| Scope Name | Location | Condition | When Added |
|------------|----------|-----------|------------|
| `UELiveSync_ProcessDeletePackets` | `ProcessBinaryPacket` — `case PT_Delete:` | Always | Stage 4 |
| `UELiveSync_HandleDelete` | `HandleDelete()` function entry | Always | Stage 7 |

### 9.2 Log Statements

| Log | Prefix | Level | Stage |
|-----|--------|-------|-------|
| Parser — batch parsed (count) | `[DELETE]` | Verbose | 4 |
| Parser — malformed packet | `[DELETE]` | Warning | 4 |
| Parser — all-zero GUID | `[DELETE]` | Warning | 4 |
| Sequence stale rejection | `[DELETE][STALE]` | Verbose | 5 |
| Replay — applied | `[DELETE][REPLAY]` | Verbose | 5 |
| Replay — skipped | `[DELETE][REPLAY]` | Verbose | 5 |
| Replay — deferred | `[DELETE][REPLAY]` | Verbose | 9 |
| Tombstone block | `[DELETE][TOMBSTONE]` | Verbose | 6 |
| Tombstone eviction | `[DELETE][TOMBSTONE]` | Verbose | 6 |
| Tombstone map cleared (reconnect) | `[DELETE]` | Log | 6 |
| Tombstone map cleared (reset) | `[DELETE]` | Log | 6 |
| Actor destroyed | `[DELETE]` | Log | 7 |
| Actor not found (missing) | `[DELETE][MISSING]` | Verbose | 7 |
| Suppression enter | `[DELETE][SUPPRESS]` | Verbose | 7 |
| Suppression exit | `[DELETE][SUPPRESS]` | Verbose | 7 |
| Child detached from parent | `[DELETE][DETACH]` | Log | 8 |
| Deferred entry evicted (child) | `[DELETE][EVICT]` | Log | 8 |
| Deferred entry evicted (parent) | `[DELETE][EVICT]` | Log | 8 |
| CREATE blocked by tombstone | `[DELETE][TOMBSTONE]` | Verbose | 9 |
| EndSnapshot deferred delete processing | `[DELETE][REPLAY]` | Log | 9 |
| Tracker clear | `[DELETE]` | Log | 3 |
| ConsoleReset counters zeroed | `[DELETE]` | Log | 13 |

### 9.3 Counters

| Counter | Location | Type | Stage | When Incremented |
|---------|----------|------|-------|------------------|
| `DeletesProcessed` | `FLiveSyncStats` | `std::atomic<int32>` (relaxed) | 7 | Actor successfully destroyed |
| `DeleteStaleRejections` | `FLiveSyncStats` | `std::atomic<int32>` (relaxed) | 5 | Stale/duplicate sequence rejection (live + replay) |
| `DeleteReplayApplied` | `FLiveSyncStats` | `std::atomic<int32>` (relaxed) | 5 | Delete applied during snapshot replay |
| `DeleteReplaySkipped` | `FLiveSyncStats` | `std::atomic<int32>` (relaxed) | 5 | Delete skipped during replay (deferred or stale) |
| `DeleteTombstoneHits` | `FLiveSyncStats` | `std::atomic<int32>` (relaxed) | 6 | Packet blocked by tombstone check (any packet type) |
| `DeleteChildrenDetached` | `FLiveSyncStats` | `std::atomic<int32>` (relaxed) | 8 | Children detached from parent before parent destroy |
| `DeleteDeferredEvictions` | `FLiveSyncStats` | `std::atomic<int32>` (relaxed) | 8 | Deferred hierarchy entries evicted due to delete |
| `DeleteTombstoneEvictions` | `FLiveSyncStats` | `std::atomic<int32>` (relaxed) | 6 | Tombstone entry evicted at 2048 capacity |

**Total: 8 counters.**

### 9.4 Counter Initialization and Reset

```
ConsoleReset():
    Stats.DeletesProcessed.store(0, std::memory_order_relaxed);
    Stats.DeleteStaleRejections.store(0, std::memory_order_relaxed);
    Stats.DeleteReplayApplied.store(0, std::memory_order_relaxed);
    Stats.DeleteReplaySkipped.store(0, std::memory_order_relaxed);
    Stats.DeleteTombstoneHits.store(0, std::memory_order_relaxed);
    Stats.DeleteChildrenDetached.store(0, std::memory_order_relaxed);
    Stats.DeleteDeferredEvictions.store(0, std::memory_order_relaxed);
    Stats.DeleteTombstoneEvictions.store(0, std::memory_order_relaxed);
```

### 9.5 Diagnostics Widget Integration

The 8 delete counters should be added to the `SLiveSyncDiagnosticsWidget`
display alongside the Phase 6D hierarchy counters. No modification to the
widget's update mechanism — counters are `std::memory_order_relaxed` display
values, read atomically.

### 9.6 DumpState Integration

```
UE.LiveSync.DumpState includes:
  - Tombstone map size
  - Delete tracker entries
  - All 8 delete counters
```

---

## 10. Validation Plan

### 10.1 Implementation-Phase Validation Stages

Each stage has a validation gate before the next stage begins.

```
VALIDATION GATES:

  Stage 1-3 (enum, FNV, tracker):
    Gate A: Unit test — enum value matches, FNV compiles,
             tracker stores/retrieves correctly
    → Proceed to Stage 4

  Stage 4-5 (parser, replay rejection):
    Gate B: Parser test — send malformed PT_Delete, verify
             rejection. Send valid PT_Delete, verify parse.
             Reject test — verify sequence check rejects <=.
    → Proceed to Stage 6

  Stage 6 (tombstone map):
    Gate C: Tombstone test — add tombstone, verify packet blocked.
             LRU eviction test — 2049 entries, verify oldest evicted.
             Clear test — verify clear on reconnect/reset.
    → Proceed to Stage 7

  Stage 7 (basic destroy):
    Gate D: Destroy test — send PT_Delete, verify actor destroyed.
             ActorCache test — verify entry removed after destroy.
             Missing actor test — verify silent discard.
             Suppression test — verify FScopedDeleteSuppression enter/exit.
    → Proceed to Stage 8

  Stage 8 (detach cascade):
    Gate E: Detach test — create parent with children, delete parent,
             verify children detached to root.
             Deferred eviction test — child with pending deferred entry,
             delete parent, verify deferred entry evicted.
             No-op test — delete root with no children (no cascade).
    → Proceed to Stage 9

  Stage 9 (CREATE blocking + replay deferral):
    Gate F: CREATE block test — delete actor, send CREATE for same GUID,
             verify blocked.
             Replay deferral test — create GUID during snapshot, delete
             same GUID during snapshot, verify delete deferred to
             after EndSnapshot.
    → Proceed to Stage 10

  Stage 10 (Blender emission):
    Gate G: Detection test — delete object in Blender, verify PT_Delete
             emitted.
             Sequence test — per-GUID monotonic counter verified.
             5 standalone detection tests pass.
    → Proceed to Stage 11

  Stage 11 (standalone tests):
    Gate H: All standalone tests pass — wire format, sequence, tombstone,
             detach, eviction, FNV, fuzz.
    → Proceed to Stage 12

  Stage 12 (integration tests):
    Gate I: All integration tests pass on UE editor.
             Basic delete, parent delete, orphan, reconnect, mixed lane.
    → Proceed to Stage 13

  Stage 13 (observability + stabilization):
    Gate J: All 8 counters increment correctly. All log prefixes present.
             2 profiler scopes visible. DumpState includes tombstone and
             tracker info.
    Gate K: 10-minute mixed soak: no crashes, no memory leaks, no
             resurrection, no corruption. 5 reconnect cycles.
    → IMPLEMENTATION COMPLETE — LIFECYCLE STABILIZED
```

### 10.2 Regression Validation

After every gate, run existing test suites:

```
python3 tests/run_phase5_all.py           # Phase 5 regression
python3 tests/run_phase6_rename.py        # Rename regression
python3 tests/run_phase6_visibility.py    # Visibility regression
python3 tests/phase6b_runtime_audit.py    # Runtime audit
python3 tests/run_phase6d_hierarchy.py    # Hierarchy regression
```

Any regressions → STOP → diagnose → roll back affected stages → re-verify.

### 10.3 Rollback Criteria Per Stage

| Stage | Rollback if |
|-------|-------------|
| 1-3 | Enum conflict, FNV mismatch, tracker corruption |
| 4-5 | Parser crashes on malformed input, sequence check allows duplicates |
| 6 | Tombstone allows stale packet, LRU eviction corrupts, clear doesn't work |
| 7 | Destroy crashes, ActorCache not updated, suppression guard missing enter/exit |
| 8 | Children not detached, deferred entry not evicted, hierarchy tracker modified |
| 9 | CREATE not blocked by tombstone, delete not deferred during replay, actor flickers |
| 10 | Counter mismatch, log prefix missing, profiler scope missing |
| 11 | Standalone test failure |
| 12 | Integration test failure |
| 13 | Observability failure, soak crash, reconnect corruption, resurrection detected |

---

## 11. Rollback Strategy

### 11.1 Rollback Conditions

Implementation must roll back if ANY of the following conditions are detected:

| # | Condition | Severity | Detection Method |
|---|-----------|----------|-----------------|
| R1 | **Resurrection** — deleted actor reappears without user action | P0 | Replay + reconnect tests |
| R2 | **Stale delete destroys valid actor** — delete from previous connection kills current actor | P0 | Repeated stale packet injection test |
| R3 | **Children not detached before parent destroy** — parent deleted before child detachment | P0 | Integration test: delete parent with children |
| R4 | **Tombstone map growth instability** — exceeds 2048 bound | P0 | Watchdog on map size > 2048 + warning |
| R5 | **Reconnect corruption** — actor state differs between pre- and post-reconnect | P0 | DumpState comparison |
| R6 | **CREATE bypasses tombstone** — actor created for tombstoned GUID | P0 | Integration test: delete + create |
| R7 | **Deferred entry not evicted** — stale hierarchy resolution after parent delete | P0 | Integration test: parent delete + hierarchy replay |
| R8 | **Frozen system modification** — any code change to a FROZEN system | P0 | Git diff review |

### 11.2 Rollback Procedure

If any P0 condition (R1-R8) is detected:

```
Step 1: FREEZE
  - Stop all lifecycle implementation work.
  - Disconnect Blender from UE.
  - Log: [DELETE] ROLLBACK TRIGGERED: reason=%s

Step 2: DISABLE
  - Comment out the `case PT_Delete:` branch in ProcessBinaryPacket.
  - Set PT_Delete to an unreachable constant (commented out).
  - Remove HandleDelete() from call sites.
  - Do NOT modify any other code path.
  - Result: PT_Delete packets are silently ignored.

Step 3: PRESERVE
  - KEEP all delete data structures (tracker, tombstone map, counters).
  - KEEP all delete logs, scopes, and profiler stubs (if any).
  - REMOVE only the active processing paths.
  - Rationale: Preserved structures ease debugging and re-enablement.

Step 4: DIAGNOSE
  - Determine root cause of rollback condition.
  - Check if condition existed before lifecycle work (regression vs. new bug).
  - Check if condition is specific to a single stage or systemic.

Step 5: REMEDIATE
  - Fix the root cause.
  - Re-enable delete processing.
  - Re-run all validation gates from the affected stage onward.
```

### 11.3 Protocol Compatibility During Rollback

If delete processing is disabled (Step 2), the protocol remains compatible:

- UE ignores PT_Delete packets (the case branch is dead code)
- Blender still sends PT_Delete packets (cannot be disabled independently)
- No protocol version bump needed — type byte `0x0E` remains reserved
- FNV checksum should still include `0x0E` (it's reserved even if unhandled)
- All other semantic lanes (rename, visibility, hierarchy) continue unaffected
- **CRITICAL**: Actor deletions are NOT replicated during rollback. The user
  must manually delete actors in UE. Blender track_objects still removes
  deleted objects — reconnecting will bring them back (if they were deleted
  during the rollback window, Blender's snapshot won't include them).

### 11.4 Re-Enablement Path

```
To re-enable after rollback:
  1. Apply the fix.
  2. Re-run validation gates from the affected stage.
  3. Uncomment the case branch.
  4. Run full Stage 12 integration tests.
  5. Run Stage 13 soak.
  6. Resume normal operation.
```

---

## 12. Risk Containment Plan

### 12.1 Incremental Safety Principles

| Principle | Implementation |
|-----------|----------------|
| **Each stage is independently testable** | Before stage N begins, stage N-1 is complete and gated. Stage N must have a defined test before it is considered complete. |
| **Parser branch testable before Blender emission** | Stage 4 (parser) can be tested with crafted TCP packets without any Blender changes. Manual `send()` calls with binary payloads. |
| **Tombstone testable before basic destroy** | Stage 6 (tombstone) can be tested with the parser stub. No actor destruction needed. |
| **Basic destroy testable before detach cascade** | Stage 7 (basic destroy) verified with isolated actors (no children). Cascade tested in Stage 8. |
| **CREATE blocking testable after tombstone** | Stage 9 (CREATE blocking + replay deferral) requires tombstone and parser — both verified by Stage 6 and 4. |
| **Blender emission testable after all UE handlers** | Stage 10 (Blender) is last because it requires the full UE handler stack to be functional. |
| **Observability is added alongside functionality** | Each stage adds its logs, counters, and scopes at the same time as the functionality — not retroactively. |

### 12.2 Risk Isolation

| Risk | Isolation Strategy |
|------|-------------------|
| Parser corrupts other packet types | Delete parser is an independent `case` branch. It cannot affect transform/rename/visibility/hierarchy parsing. |
| DestroyActor crashes | `FScopedDeleteSuppression` RAII guard. ActorCache existence check before destroy. |
| Tombstone memory exhaustion | Bounded at 2048. O(1) eviction on overflow. Size is logged and monitored. |
| Blender emits invalid delete packets | UE validates all fields: zero GUID, ActorCache existence, sequence check, tombstone check. |
| Replay state corruption | Tracker cleared on every reconnect. Tombstone map cleared on every reconnect. |
| Hierarchy deferred queue corruption | Eviction via `RemoveAll` is additive — does not modify Phase 6D code paths. |
| CREATE bypasses tombstone | Tombstone check gate at the top of PT_Create handler — purely additive early return. |

### 12.3 Monitoring During Implementation

During implementation and testing, monitor:

| Metric | Warning Signal | Action |
|--------|---------------|--------|
| `GDeleteTombstoneMap.Num()` | > 1000 | Check for tombstone leak or orphan storm |
| `DeleteTombstoneHits` / `DeletesProcessed` ratio | > 0.5 (50%) | Investigate stale packet source — possibly tracker clearing issue |
| `DeleteChildrenDetached` without parent delete | Any | False detach — cascade logic bug |
| `DeleteDeferredEvictions` without parent delete | Any | False eviction — eviction logic bug |
| Frame time impact | > 0.5ms added | Profile `UELiveSync_ProcessDeletePackets` and `UELiveSync_HandleDelete` |

### 12.4 Failure Mode Analysis

| Failure Mode | Effect | Detection | Recovery |
|-------------|--------|-----------|----------|
| Tombstone eviction allows stale replay | Deleted actor recreated | Actor appears after processing stale packet (extremely unlikely — 2048 deletes within same connection) | Manual delete in UE |
| Detach cascade misses a child | Child remains under deleted parent (UE keeps orphan attached to destroyed parent? — this would crash) | Visible crash or dangling pointer | Parent delete is rare; this would be caught in Stage 8 tests |
| Deferred entry eviction misses an entry | Child transiently attached to wrong parent | ResolveHierarchyAttachments applies stale attachment | Next hierarchy event corrects it; one-frame glitch |
| Sequence tracker evicts important GUID | Stale delete accepted (re-applied) | Tombstone map catches it (if still tombstoned) or ActorCache miss (if actor gone) | Three-barrier system catches it |
| Delete during snapshot replay not deferred | Actor flickers: create → destroy within same batch | Visible in editor: actor appears and disappears in ~1 frame | Stylistic issue — eventual consistency |
| PendingHierarchyAttachments accessed during eviction from non-game thread | Data race | Crash or corruption | Game-thread-only access enforced by design |

---

## 13. Test Matrix Planning

### 13.1 Test Categories

| Category | Count | Stage | Priority |
|----------|-------|-------|----------|
| **Parser validation** | 4 | Stage 5 | High |
| **Sequence validation** | 4 | Stage 5 | High |
| **Tombstone validation** | 4 | Stage 6 | High |
| **Basic destroy validation** | 4 | Stage 7 | High |
| **Detach cascade validation** | 4 | Stage 8 | High |
| **CREATE blocking + replay deferral** | 3 | Stage 9 | High |
| **Blender detection + serialization** | 5 | Stage 10 | High |
| **Integration tests (UE required)** | 6 | Stage 12 | High |
| **Mixed lane validation** | 3 | Stage 12 | Medium |
| **Observability validation** | 3 | Stage 13 | Medium |
| **Phase 5 + Phase 6 regression** | (existing) | Stage 13 | Required |
| **Soak/stress validation** | 3 | Stage 13 | Required |
| **Total (new)** | **47** | | |

### 13.2 Test Descriptions

**Parser validation** (`tests/phase6_lifecycle_validation.py`):

| # | Test | Description |
|---|------|-------------|
| P1 | **Valid single delete** | Send PT_Delete with valid 28-byte payload. Verify parsed correctly. |
| P2 | **Malformed payload size** | Send PT_Delete with 29-byte payload (not multiple of 28). Verify rejection. |
| P3 | **Zero-length payload** | Send PT_Delete with 0-byte payload. Verify rejection. |
| P4 | **All-zero GUID** | Send PT_Delete with all-zero GUID. Verify rejection of single object. |

**Sequence validation**:

| # | Test | Description |
|---|------|-------------|
| S1 | **First sequence accepted** | Send seq=1. Verify accepted. |
| S2 | **Duplicate sequence rejected** | Send seq=1 again. Verify stale rejection. |
| S3 | **Stale sequence rejected** | Send seq=5, then seq=3. Verify stale rejection. |
| S4 | **Higher sequence accepted** | Send seq=5, then seq=6. Verify both accepted. |

**Tombstone validation**:

| # | Test | Description |
|---|------|-------------|
| T1 | **Tombstone blocks packet** | Delete actor, send another PT_Delete for same GUID. Verify tombstone hit. |
| T2 | **LRU eviction** | Add 2049 tombstone entries. Verify oldest evicted. |
| T3 | **Clear on reconnect** | Verify GDeleteTombstoneMap cleared after StopNetworkThread. |
| T4 | **Clear on reset** | Verify GDeleteTombstoneMap cleared after ConsoleReset. |

**Basic destroy validation**:

| # | Test | Description |
|---|------|-------------|
| D1 | **Actor destroyed** | Send PT_Delete for valid actor. Verify Actor->Destroy() called. |
| D2 | **ActorCache entry removed** | Verify actor removed from ActorCache after destroy. |
| D3 | **Missing actor discard** | Send PT_Delete for non-existent GUID. Verify silent discard, no crash. |
| D4 | **Suppression guard** | Verify FScopedDeleteSuppression enter/exit logged. |

**Detach cascade validation**:

| # | Test | Description |
|---|------|-------------|
| C1 | **Children detached to root** | Create parent with 2 children. Delete parent. Verify both children are root. |
| C2 | **Child FSyncTransformState updated** | Verify bHasParent=false, ParentGuid=0 for each child. |
| C3 | **Deferred entry eviction (child)** | Create child with pending deferred hierarchy entry. Delete child. Verify entry evicted. |
| C4 | **Deferred entry eviction (parent)** | Create actor with deferred entries awaiting it as parent. Delete actor. Verify entries evicted. |

**CREATE blocking + replay deferral validation**:

| # | Test | Description |
|---|------|-------------|
| B1 | **CREATE blocked by tombstone** | Delete actor, send CREATE for same GUID. Verify blocked. |
| B2 | **Delete deferred during replay** | During snapshot replay (bInSnapshotBuild=true), send DELETE for GUID whose CREATE hasn't arrived. Verify deferred to DeferredDeleteQueue. |
| B3 | **Deferred delete processed after EndSnapshot** | Verify DeferredDeleteQueue processed in HandleEndSnapshot. |

**Blender detection + serialization validation**:

| # | Test | Description |
|---|------|-------------|
| E1 | **ReferenceError detection** | Delete object in Blender. Verify PT_Delete packet queued. |
| E2 | **Wire format (28 bytes)** | Verify serialize_delete() produces exactly 28 bytes per object. |
| E3 | **Per-GUID sequence monotonic** | Verify _delete_sequences increments per delete event. |
| E4 | **Sequence cleared on disconnect** | Verify _delete_sequences cleared on Blender disconnect. |
| E5 | **No duplicate sends** | Verify same object not sent twice. |

**Integration validation** (requires UE editor on `:57000`):

| # | Test | Description |
|---|------|-------------|
| I1 | **Basic delete** | Send PT_Delete, verify actor destroyed in UE viewport. |
| I2 | **Parent delete cascade** | Create A→B (hierarchy), delete A. Verify B is root. |
| I3 | **Orphan parent delete** | Create B with pending deferred attachment to A. Delete A first on Blender. Verify B becomes root. |
| I4 | **Reconnect delete persistence** | Delete actor, disconnect, reconnect. Verify actor stays dead. |
| I5 | **Delete + transform storm** | 100 deletes interleaved with transforms. Verify no crashes. |
| I6 | **Fuzz: malformed delete** | Send malformed PT_Delete payloads. Verify graceful rejection. |

**Mixed lane validation**:

| # | Test | Description |
|---|------|-------------|
| M1 | **Delete + rename** | Delete actor while rename packet in same batch. Verify actor destroyed. |
| M2 | **Delete + visibility** | Delete actor while visibility packet in same batch. Verify actor destroyed. |
| M3 | **Delete + hierarchy** | Delete parent while hierarchy packet for child in same batch. Verify child root + deferred entry evicted. |

**Observability validation**:

| # | Test | Description |
|---|------|-------------|
| O1 | **Counter accuracy** | Verify all 8 counters increment correctly under known loads. |
| O2 | **Log prefix presence** | Verify all log prefixes appear in output at expected log levels. |
| O3 | **Profiler scope presence** | Verify UELiveSync_HandleDelete and UELiveSync_ProcessDeletePackets appear in UE traces. |

**Soak/stress validation** (`tests/phase6_lifecycle_soak.py`):

| # | Test | Description |
|---|------|-------------|
| K1 | **Long soak (10 min)** | Continuous delete + transform + rename + visibility + hierarchy traffic. No memory leak, no drift. |
| K2 | **Delete storm** | 100 simultaneous delete events. Verify all processed without packet loss. |
| K3 | **Reconnect storm** | 5 reconnect cycles with mixed traffic. Verify consistent state. |

### 13.3 Test Framework Considerations

- Standalone tests use a mock `ActorCache` for UE-side testing without editor
- Blender-side tests verify serialization output bytes match expected format
- Integration tests require UE editor listening on `:57000` (same as rename/visibility/hierarchy)
- Phase 5 + Phase 6 regression tests must pass before any lifecycle test is considered passing
- Tests are added to `tests/run_phase6e_lifecycle.py` runner script

---

## 14. Final Go/No-Go Gate

### 14.1 Implementation-Entry Criteria

Implementation of the lifecycle/delete semantic lane may begin ONLY IF all of
the following criteria are met:

| # | Criterion | Verification | Status |
|---|-----------|-------------|--------|
| G1 | **Replay semantics frozen** | Replay scenarios documented in design doc §2. All deterministic outcomes defined. Three-barrier stale rejection proved. | ✅ PASS (design §7.2) |
| G2 | **Tombstone semantics frozen** | Tombstone lifecycle (ENTER → BLOCK → EVICT → CLEAR) defined. LRU eviction policy specified. CREATE-blocked rule documented. | ✅ PASS (design §3, plan §4) |
| G3 | **Authority boundaries frozen** | "Delete replicates destruction intent only" — documented in scope lock §3. Blender-authority only. Editor-authority delete deferred. | ✅ PASS (scope lock §3, §4) |
| G4 | **Hierarchy coupling frozen** | No cross-lane sequence coupling. Deferred entry eviction is explicit, not tracker-mediated. Implicit detach does not emit hierarchy events. | ✅ PASS (design §6, §7.4) |
| G5 | **Rollback strategy defined** | §11 of this document. 8 rollback conditions, 5-step procedure, re-enablement path. | ✅ PASS (this document §11) |
| G6 | **Observability complete** | 2 profiler scopes, 19 log statements, 8 counters, all prefixes defined. | ✅ PASS (plan §9) |
| G7 | **Runtime preservation checklist complete** | Frozen systems listed (§2.1). Pre-implementation audit checklist complete (§2.2). Pre-implementation audit passes (A1-A12). | ✅ PASS (plan §2) |
| G8 | **Design remediation findings addressed** | 4 P1 findings from threat audit resolved. DEL-001 (hierarchy seq coupling removed), DEL-002 (CREATE tombstone rule), DEL-003 (FScopedDeleteSuppression), DEL-004 (deferred eviction). | ✅ PASS (32-phase6E-remediation-summary.md) |
| G9 | **Implementation sequence defined** | 14 stages with dependency graph, entry/exit criteria, rollback per stage. | ✅ PASS (plan §3) |
| G10 | **Test matrix defined** | ~47 tests across 13 categories. Soak + stress. Phase 5 + Phase 6 regression. | ✅ PASS (plan §13) |

### 14.2 Final Verdict

**Implementation Readiness Verdict: GO (with constraints)**

The lifecycle/delete semantic lane design is complete, reviewed, risk-audited,
remediated, and ready for implementation under the following constraints:

1. **Incremental only**: No stage may be skipped. Each stage must pass its
   validation gate before the next begins.

2. **Frozen systems inviolate**: Any implementation that requires modification
   of a frozen system must be paused and escalated via ADR review.

3. **Rollback always available**: If any P0 condition (§11.1) is detected,
   implementation must roll back immediately. The disable path (comment out
   the case branch) must be verified to be safe before production deployment.

4. **Observability first**: Logs and counters must be added at the same time
   as the functionality they observe. No silent code paths.

5. **Correctness over performance**: The priority order (§1.1) is a hard
   constraint. No optimization work before all 14 stages pass validation.

6. **Resurrection prevention is non-negotiable**: If any test demonstrates
   resurrection of a deleted actor (R1), implementation must roll back
   immediately regardless of stage.

### 14.3 Implementation Summary

```
Phase 6E — Lifecycle/Delete Replication Implementation

  Status:         IMPLEMENTATION COMPLETE — STABILIZED (2026-05-26)
  Stages:         14 stages (0-13, with Stage 13 having two phases)
  New files:      None (additions to 5 existing files)
  New types:      PT_Delete (0x0E), FDeleteSequenceTracker,
                  FDeleteTombstoneMap (TMap<FGuid, uint32>),
                  FScopedDeleteSuppression, FDeferredDelete
  New counters:   8
  New log prefixes: [DELETE], [DETACH], [TOMBSTONE], [STALE],
                    [REPLAY], [SUPPRESS], [EVICT], [MISSING]
  New profiler scopes: 2
  Frozen systems modified: NONE
  Rollback conditions: 8 defined
  Actual tests:   308 standalone (new) + 102 audit checks + existing regression suites
  Risk level:     HIGH (first identity-destruction lane; resurrection risk)
  Mitigations:    3-barrier stale rejection, bounded structures at 2048, RAII suppression,
                  FIFO eviction, reconnect clearing, cross-lane isolation
```

### 14.4 Sign-Off

| Role | Sign-Off | Date |
|------|----------|------|
| Design author | ✅ COMPLETE | 2026-05-26 |
| Threat auditor | ✅ COMPLETE | 2026-05-26 |
| Implementation lead | ✅ COMPLETE | 2026-05-26 |

---

## Revision History

| Date | Version | Changes |
|------|---------|---------|
| 2026-05-26 | 1.0 | Initial implementation plan — defines 14 stages, validation gates, rollback strategy, risk containment, test matrix, and go/no-go criteria for Phase 6E lifecycle/delete replication. |
| 2026-05-26 | 1.1 | Updated status to STABILIZED — Stages 0-13 complete. 308/308 tests pass, 102/102 audit checks pass, 17/17 criteria met. See `35-phase6E-live-runtime-validation.md` for final report. |
