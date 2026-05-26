# Phase 6 Minimal Vertical Slice — Rename Replication

> **Created**: 2026-05-25
> **Phase 5**: COMPLETE · **Phase 6**: ACTIVE (Rename: STABILIZED via Phase 6A/6B)
> **Runtime core**: FROZEN (`v0.5.0-stabilized`)
>
> This document defines the FIRST implementation slice for Phase 6.
> Rename replication is the smallest viable editor-authority workflow
> that exercises provenance tracking, suppression systems, reconnect
> replay, and observability — without requiring the full Live Editing
> System.

---

## 1. Purpose

This vertical slice exists to validate the following architectural
foundations using the **smallest possible editor-authority workflow**:

| Foundation | Why Rename Is the Right Test |
|-----------|------------------------------|
| **Provenance tracking** | Rename must flow through `LOCAL_USER → REMOTE_REPLICATED → SuppressedCallback`. If provenance fails, a rename loop occurs immediately. |
| **Suppression systems** | `OnActorLabelChanged` fires synchronously on `SetActorLabel`. Without correct suppression, the rename replicates back to Blender → infinite loop. |
| **Reconnect replay semantics** | Reconnecting while renames were made on both sides tests timestamp comparison, idempotency, and last-writer-wins. |
| **Editor callback handling** | UE editor fires a documented callback (`OnActorLabelChanged`) that must be suppressed but not permanently disabled. |
| **Observability model** | Rename is a single scalar change — easy to trace, log, and verify. |
| **Lifecycle synchronization** | Renaming a Tombstoned, Orphaned, or Reconnecting actor tests lifecycle state machines. |
| **Authority assumptions** | Single-direction rename (Blender→UE with UE→Blender optional) tests last-writer-wins without conflict resolution complexity. |

### Why Rename and Not Visibility

Visibility was also considered as the first slice. Rename was chosen because:

- **`OnActorLabelChanged` callback** is synchronous and well-understood
- **Name comparison** is trivially observable (string equality)
- **No external dependencies** on asset resolution, collection state, or render state
- **Rename storms** are the simplest stress test for coalescing and rate limiting
- **Replay idempotency** is easy to verify (compare final name string)

---

## 2. Exact Scope

### IN SCOPE

| Item | Description |
|------|-------------|
| Blender object rename detection | Detect when `obj.name` changes between sync iterations in `sync.py`. Must handle Blender's internal rename API (`obj.name = new_name`). |
| Rename packet serialization | New packet type `PT_RENAME` (`0x0C`). Payload: GUID (16 bytes) + name-length (2 bytes, uint16) + UTF-8 name (variable, max 256 bytes). |
| UE actor rename application | On receiving `PT_RENAME`, call `AActor::SetActorLabel()`. |
| Provenance tagging | Tag every rename mutation with `EChangeOrigin` before applying. Never apply without provenance set. |
| Recursive callback suppression | Wrap `SetActorLabel()` in `FScopedReplicationSuppression` to prevent `OnActorLabelChanged` from re-replicating. |
| Reconnect replay handling for renames | During snapshot replay on reconnect, include rename state for each GUID. Replayed renames must be tagged `REPLAY` and must NOT replicate. |
| Rename observability/logging | `UE_LOG(LogLiveSync, ...)` messages for every rename: origin, GUID, old name, new name, suppression state. |
| Stale GUID rename handling | If `PT_RENAME` arrives for a Tombstoned or Unknown GUID, reject the rename (log warning). |
| Rename stress testing | Support for batch rename testing: rename 500 objects, rename same object 100 times rapidly, interleave rename with other packet types. |

### OUT OF SCOPE

| Item | Rationale |
|------|-----------|
| Hierarchy changes (re-parent) | Different callback, different packet type. Separate vertical slice. |
| Visibility sync | Different UE callback (`OnActorVisibilityChanged`). Separate vertical slice. |
| Duplicate detection | Requires new GUID generation, different lifecycle. Separate vertical slice. |
| Collection sync | Requires folder API, different packet structure. Separate vertical slice. |
| Bidirectional rename authority | UE→Blender rename direction is deferred until Blender-side TCP listener exists. This slice is Blender→UE only. |
| Undo/redo sync | Undo transaction management adds complexity not needed for initial provenance validation. |
| Asset rename handling | Renaming a mesh datablock in Blender is separate from renaming the object. Not in scope. |
| Editor transaction merging | Deferred to Phase 9 complexity registry. This slice uses no-undo for renames. |

---

## 3. Semantic Event vs State Stream

> **Critical conceptual boundary.** Rename replication is a **semantic
> editor event**, NOT a state stream. It must NOT inherit assumptions
> from the Phase 5 transform replication model.
>
> Treating rename as a state stream will produce recursive callback loops,
> replay corruption, and lifecycle desynchronization.

### The Distinction

| Property | Transform Replication (Phase 5) | Rename Replication (Phase 6) |
|----------|--------------------------------|------------------------------|
| **Nature** | Continuous state stream | Discrete semantic editor event |
| **Frequency** | High (60 Hz typical) | Low (user-initiated, bursty) |
| **Semantics** | Overwrite-oriented — latest state wins | Lifecycle-sensitive — depends on actor state (Active, Tombstoned, etc.) |
| **Interpolation** | Friendly — missing frames are interpolated | Not applicable — rename is atomic |
| **Callback impact** | None — `SetActorTransform` does not fire editor callbacks | High — `SetActorLabel` fires `OnActorLabelChanged` synchronously |
| **Provenance sensitivity** | None — transform has no origin tracking | Required — every rename carries `EChangeOrigin` |
| **Ordering sensitivity** | Low — out-of-order transforms produce minor visual glitch | High — rename after delete is a bug; rename during replay requires ordering |
| **Replay sensitivity** | Low — overwrite with latest transform | High — must check tombstone, deduplicate, verify idempotency |
| **Undo/redo interaction** | None — transforms are not undoable | Required — rename creates undo transactions |
| **Suppression required** | No | Yes — callback suppression prevents feedback loops |
| **Packet interpretation** | Stateless — each packet is self-contained | Stateful — depends on actor lifecycle, provenance, suppression scope |

### Semantic-Event Characteristics

Rename operations:

1. **Have intent** — A user chose to rename, or a remote peer replicated a
   rename. The intent (origin) must be preserved through the pipeline.
2. **Affect editor identity semantics** — The actor label is how users identify
   actors in the World Outliner. Losing or corrupting a rename affects user
   workflow directly.
3. **Trigger editor callbacks** — `OnActorLabelChanged` fires synchronously.
   The callback handler must check provenance and suppression state before
   acting.
4. **May interact with undo/redo** — Rename creates a transaction in UE's undo
   buffer. Undoing a synced rename must not cause desync.
5. **Require suppression scopes** — Every rename application must wrap
   `SetActorLabel` in a suppression scope to prevent recursive replication.
6. **May require replay deduplication** — During reconnect replay, the same
   rename may be received multiple times. Duplicates must be detected and
   skipped.
7. **May require tombstone awareness** — A rename targeting a deleted actor
   must be rejected.
8. **May affect hierarchy resolution** — Actor label changes may affect
   hierarchy display in the World Outliner (parent labels in tree view).

### Prohibited Coupling

Rename replication must NOT:

| Prohibition | Why |
|-------------|-----|
| **Reuse transform interpolation logic** | Interpolation assumes continuous numeric state. Rename is discrete string state. Blending or smoothing a name is meaningless. |
| **Reuse transform smoothing / state blending** | There is no intermediate state between "Cube" and "MyCube". Rename is atomic. |
| **Reuse transform packet assumptions** | Transform packets assume the payload is always 81 bytes. Rename payload is variable-length. Packets must not share a serialization path. |
| **Bypass provenance tagging** | Transform packets do not carry provenance. Rename packets MUST carry provenance internally (in-memory, not on the wire). |
| **Bypass suppression systems** | Transform application has no callback suppression. Rename application MUST suppress `OnActorLabelChanged`. |
| **Bypass replay validation** | Transform replay applies every packet unconditionally. Rename replay MUST check tombstone, lifecycle state, and deduplicate. |
| **Bypass reconnect replay ordering** | Transform replay is unordered (last transform per GUID wins). Rename replay MUST process in order (or per-GUID coalesce to final name). |

### Packet-Design Guidance

Rename packets must be treated as:

- **Discrete editor events** — Each packet represents a single user action
  (one rename), not a continuous state sample.
- **Ordered semantic mutations** — The order of rename events matters.
  Applying "A→B" then "B→C" is different from "A→C" directly.
- **Provenance-carrying operations** — The in-memory provenance tag is
  essential for correct callback suppression.

Rename packets must NOT be treated as:

- **Generic object state snapshots** — A rename is NOT a full actor state
  dump. It is a single semantic mutation on one property.
- **Continuously overwritable state blobs** — Unlike transform data, where
  dropping intermediate packets is safe (interpolation fills gaps), dropping
  rename packets causes permanent label loss.

### Replay Semantics Clarification

| Aspect | Transform Replay | Rename Replay |
|--------|-----------------|---------------|
| Tolerance for last-state overwrite | High — dropping intermediate transforms is safe | Low — each rename is meaningful |
| Ordering correctness | Not required — final state wins | Required — "A→B then B→C" ≠ "A→C" |
| Idempotency validation | Nice-to-have — same transform applied twice is harmless | Required — replay must detect and skip duplicate renames |
| Duplicate suppression | Not needed — duplicates are harmless | Required — suppress when GUID+name already match |
| Tombstone checks | Not needed — transforms for deleted GUIDs are dropped silently | Required — rename for Tombstoned GUID must be rejected and logged |

### Observability Distinction

| Focus for Transform Observability | Focus for Rename Observability |
|-----------------------------------|-------------------------------|
| Interpolation metrics | Intent tracking (provenance flow) |
| Smoothing diagnostics | Suppression scope entry/exit |
| Throughput optimization | Callback suppression events |
| Packet rate (Hz) | Replay ordering verification |
| Queue depth | Semantic lifecycle state transitions |

### Future Extensibility

This semantic-event model will apply to all Phase 6 editor workflows:

- **Collection moves** — Moving an actor between collections is a semantic
  event, not a folder state snapshot.
- **Visibility changes** — Hiding/showing an actor is a discrete user action,
  not a continuous visibility value.
- **Hierarchy changes** — Re-parenting is a semantic event with lifecycle
  implications (orphan prevention, cycle detection).
- **Duplicate actions** — Duplicating an actor is a semantic event with GUID
  generation and identity implications.
- **Delete/create editor workflows** — Editor-side create/delete are lifecycle
  events, not state increments.

> Phase 6 introduces **semantic editor-event replication**, not just
> additional packet types. Every editor workflow follows the pattern:
> intent → provenance → suppression → replay-safety.

### Architecture Warning

```
╔══════════════════════════════════════════════════════════════════════╗
║                                                                      ║
║   WARNING: DO NOT TREAT EDITOR SEMANTIC EVENTS                      ║
║   AS TRANSFORM-STYLE STATE STREAMS                                  ║
║                                                                      ║
║   Doing so risks:                                                    ║
║                                                                      ║
║     • Recursive callback loops    — OnActorLabelChanged re-fires     ║
║     • Replay corruption           — Reconnect replay applies stale   ║
║     • Lifecycle desynchronization — Rename targets deleted actor     ║
║     • Stale-state resurrection    — Tombstoned GUID re-activated     ║
║     • Semantic ordering bugs      — Reorder of rename sequence       ║
║     • Editor transaction divergence — Undo creates permanent desync  ║
║                                                                      ║
║   Every Phase 6 editor event must be implemented as a semantic        ║
║   mutation with provenance, suppression, and replay validation.      ║
║   Never reuse Phase 5 transform-stream patterns for Phase 6 events.  ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝
```

| # | Criterion | Verification Method | Pass Condition |
|---|-----------|-------------------|----------------|
| 1 | Single rename replicates correctly | Blender rename "Cube" → "MyCube" → verify UE actor label == "MyCube" | Label matches within 1 second |
| 2 | Rapid rename storms handled safely | Rename same object 100 times in 1 second | All 100 renames applied (or coalesced to final) with no crash, no flooding |
| 3 | Reconnect replay does not duplicate renames | Disconnect → rename 10 objects in Blender → reconnect → verify labels | Each actor has correct final label, no double-sets |
| 4 | No recursive rename loops | Rename in Blender → watch UE log | Exactly 1 rename log with `REMOTE_REPLICATED` origin, 0 origin=LOCAL_USER for the same GUID |
| 5 | No stale GUID rename application | Delete actor in UE → send PT_RENAME for its GUID → verify | Rename rejected, warning logged, actor not re-spawned |
| 6 | No actor identity loss | Rename changes label but GUID remains same | Tracked actor map still maps GUID → same actor pointer |
| 7 | Reconnect-safe rename replay | UE renames actor during disconnect → Blender renames same actor → reconnect | Last-writer-wins: Blender name (Blender authoritative during this slice) |
| 8 | Tick pipeline invariants preserved | Phase 5 validation tests still pass | 5/5 tests pass (`phase5e_validation.py`) |
| 9 | No frozen-zone modifications required | Git diff shows only new files + SAFE/CAUTION zone changes | Zero changes to parser, queues, threads, pipeline order |
| 10 | Rename does not affect transform sync | Rename 30 objects while transforms are streaming | Transform interpolation continues without interruption or desync |

---

## 4. Proposed Rename Lifecycle (Conceptual)

```
BLENDER SIDE
============

sync.py: OnTick()
 │
 ├─ scene scan detects obj.name != _last_object_names[guid]
 │
 ▼
sync.py: collect_object_states()
 │
 ├─ obj.name changed → set flag → serialize PT_RENAME
 │
 ▼
network.py: serialize_rename(guid, name)
 │
 ├─ struct.pack('<16sH{name_len}s'.format(name_len=len(name)),
 │              guid.bytes, len(name), name.encode('utf-8'))
 │
 ▼
network.py: build_packet(PT_RENAME, payload)
 │
 ├─ 24-byte header (magic, version=5, type=0x0C, payload_size, ...)
 ├─ append serialized rename payload
 │
 ▼
Blender send queue (Python list)
 │
 ▼
Blender daemon thread: socket.sendall()


WIRE FORMAT
===========

 [24-byte header] [GUID (16)] [name_length (2)] [UTF-8 name (N)]
   │                  │             │                  │
   │                  ▼             ▼                  ▼
   │            FGuid bytes    uint16 LE        name_length bytes
   ▼
 Header: MAGIC=0x4C56534D, version=5, type=0x0C


UE SIDE
=======

LiveSyncRunnable: Recv thread
 │
 ├─ recv() → parse 24-byte header
 ├─ if type == PT_RENAME: parse GUID + name_length + name
 │  → enqueue FLiveSyncPacket(kind=Rename, guid, name)
 │  → provenance tag applied on enqueue: REMOTE_REPLICATED
 │
 ▼
UELiveSyncSubsystem: Game thread Tick
 │
 ├─ ProcessQueuedPackets()
 │    └─ case PT_RENAME:
 │         ├─ check GUID lifecycle state
 │         │    ├─ Tombstoned → reject, log warning
 │         │    ├─ Unknown → reject, log warning
 │         │    └─ Active → continue
 │         │
 │         ├─ FScopedReplicationSuppression Suppressor
 │         ├─ FScopedChangeOrigin Origin(EChangeOrigin::RemoteReplicated)
 │         │
 │         ├─ AActor* Actor = FindTrackedActor(Guid)
 │         ├─ if Actor:
 │         │    ├─ UE_LOG origin, GUID, old_name, new_name
 │         │    ├─ Actor->SetActorLabel(NewName)
 │         │    │    └─ OnActorLabelChanged fires
 │         │    │         └─ callback checks suppression scope
 │         │    │              → REMOTE_REPLICATED → do nothing
 │         │    └─ update tracked state with new name
 │         │
 │         └─ ← Suppressor destructor runs, re-enables replication
 │
 ▼
 Observability: FLiveSyncStats counters updated
```

### Reconnect Replay Lifecycle

```
BLENDER SIDE                     UE SIDE
============                     ======

Reconnect detected                                                       │
 │                                                                       │
 ├─ PT_BEGINSNAPSHOT (0x09)      ───TCP──→     Clear replay batch set   │
 │                                                                       │
 ├─ for each tracked GUID:                                               │
 │    ├─ PT_RENAME for current name    ──→     ProcessRenameReplay()     │
 │    │                                        ├─ Tag: REPLAY           │
 │    │                                        ├─ Check tombstone set   │
 │    │                                        ├─ If tombstoned → skip  │
 │    │                                        ├─ SetActorLabel(name)   │
 │    │                                        │  → callback suppressed │
 │    │                                        └─ Add GUID to batch set │
 │    │                                                                  │
 │    ├─ PT_TRANSFORM (existing) ──→     (existing pipeline)            │
 │    └─ PT_ASSETDEF (existing)  ──→     (existing pipeline)            │
 │                                                                       │
 ├─ PT_ENDSNAPSHOT (0x0A)         ──→     Stale-object pruning          │
 │                                        GUIDs not in batch set         │
 │                                        → Orphaned → Tombstoned after  │
 │                                          60s grace period             │
```

---

## 5. Provenance Expectations

### Provenance Transitions for Rename

| Mutation Step | Provenance | Notes |
|--------------|------------|-------|
| Blender user renames object | `LOCAL_USER` | Tagged at detection point in `sync.py` |
| Blender serializes PT_RENAME | `LOCAL_USER` (unchanged) | Serialization does not change provenance |
| UE receives PT_RENAME (network thread) | `REMOTE_REPLICATED` | Tagged on enqueue into `FLiveSyncQueue` |
| UE applies rename (game thread) | `REMOTE_REPLICATED` | Must NOT re-replicate |
| UE editor callback fires | `REMOTE_REPLICATED` (suppressed scope) | Callback checks current-thread provenance |
| UE reconnect replay receives PT_RENAME | `REPLAY` | Tagged on enqueue during replay batch |
| UE applies rename during replay | `REPLAY` | Must NOT re-replicate | | UE recovery renames actor after re-spawn | `RECOVERY` | Re-link name after lost-actor recovery |
| UE undo reverts a rename | `UNDO_REDO` | Reconciliation send to Blender (future slice) |
| UE duplicate creates renamed actor | `DUPLICATE` | Name set during duplicate creation |

### Rules

1. **Never apply a rename without provenance** — The rename handler must
   assert that the current-thread provenance is non-default before calling
   `SetActorLabel`. Default provenance = uninitialized = code bug.
2. **REMOTE_REPLICATED must never re-replicate** — If `OnActorLabelChanged`
   fires while provenance is `REMOTE_REPLICATED`, the handler must return
   immediately. No packet enqueued, no network send.
3. **REPLAY must never re-replicate** — Same suppression as REMOTE_REPLICATED,
   but additionally, replay renames must not generate undo transactions.
4. **RECOVERY must never re-replicate** — Recovery renames restore state that
   the Blender peer already knows.
5. **LOCAL_USER on the UE side** — Only applies when UE→Blender rename
   replication is implemented (future slice). Blender→UE only in this slice.

---

## 6. Suppression Expectations

### Expected Semantics

| Property | Requirement |
|----------|-------------|
| Scoped | Suppression must be active only within the rename handler's call stack. After `SetActorLabel` returns, replication must be re-enabled. RAII scope guard. |
| Temporary | Suppression must not persist across frames. No global "suppression enabled" flag that leaks. |
| No cross-frame leakage | If `OnActorLabelChanged` defers work via `AsyncTask` or `FTimerHandle`, the deferred work must NOT assume suppression is still active. Deferred callbacks must re-check provenance. |
| Observable | Suppression entry/exit must be logged (Verbose level). Count of suppression events tracked in `FLiveSyncStats`. |
| Non-re-entrant | If suppression is active and a second rename arrives for the same GUID, the second is queued (not dropped). Queue depth tracked. |

### Suppression Implementation Pattern

```cpp
void UELiveSyncSubsystem::HandleRename(const FGuid& Guid, const FString& NewName)
{
    // 1. Check lifecycle state
    if (!IsTrackedActor(Guid))
    {
        UE_LOG(LogLiveSync, Warning, TEXT("Rename for untracked GUID: %s"), *Guid.ToString());
        return;
    }

    // 2. Check provenance is set
    EChangeOrigin Origin = FScopedChangeOrigin::GetCurrent();
    check(Origin != EChangeOrigin::Unspecified);

    // 3. Suppress replication scope
    {
        FScopedReplicationSuppression SuppressRename;
        FScopedRenameGuard GuidGuard(Guid); // per-GUID dedup within frame

        UE_LOG(LogLiveSync, Verbose, TEXT("Rename: GUID=%s, Origin=%s, Name=%s"),
            *Guid.ToString(), *LexToString(Origin), *NewName);

        AActor* Actor = FindTrackedActor(Guid);
        if (Actor)
        {
            Actor->SetActorLabel(NewName);
            // OnActorLabelChanged fires here → checks suppression → no re-replicate
        }

        UpdateTrackedName(Guid, NewName);
    }

    // 4. Suppressor destructor re-enables replication
}
```

### Debugging Suppression

Suppression scope entry/exit should be logged at `LogLiveSync` Verbose level:

```
[Verbose] Rename: Enter suppression scope (GUID=ABCDEF)
[Verbose] Rename: OnActorLabelChanged suppressed (depth=1, origin=REMOTE_REPLICATED)
[Verbose] Rename: Exit suppression scope (GUID=ABCDEF)
```

If a callback fires without an active suppression scope, log a warning:

```
[Warning] Rename: OnActorLabelChanged fired outside suppression scope!
    This may indicate a recursive rename loop.
```

---

## 7. Reconnect Expectations

### Replay Ordering

1. `PT_BEGINSNAPSHOT` opens the replay batch
2. `PT_RENAME` packets are interleaved with `PT_TRANSFORM` and `PT_ASSETDEF`
   — Blender must emit packets in dependency-safe order (CREATE before RENAME
   before TRANSFORM)
3. `PT_ENDSNAPSHOT` closes the batch

### Stale Rename Suppression

- If a rename targets a GUID not in the replay batch set, it was **deleted
  during disconnection** → skip the rename
- If the GUID is in the tombstone set → skip the rename (actor was deleted
  on UE side during disconnection)

### Tombstoned Actor Rename Rejection

- `HandleRename` checks `IsTombstoned(Guid)` before any processing
- If tombstoned → log warning, return — do not re-spawn the actor

### Idempotent Replay

- Replaying the same rename twice produces the same label
- Replaying a rename where the label already matches is a no-op (inexpensive
  label comparison before `SetActorLabel` call)

### Rename Replay Deduplication

- Within a single replay batch, if two `PT_RENAME` packets for the same GUID
  arrive, the **later** one wins (by packet order within the batch)
- Intermediate renames are dropped (coalesced by the batch)

---

## 8. Observability Requirements

### Required Diagnostics

| Category | What to Log | Level |
|----------|-------------|-------|
| **Rename application** | Origin, GUID, old name, new name | Log |
| **Rename suppression** | Suppression scope enter/exit, callback suppression | Verbose |
| **Stale GUID rename** | GUID, requested name, current state (Tombstoned/Unknown) | Warning |
| **Replay rename** | Batch ID, GUID, name, skip reason (tombstone/duplicate) | Log |
| **Rename storm counters** | Current rate, threshold, coalescing window activity | Verbose |
| **Duplicate rename** | Same GUID renamed twice within coalesce window; second wins | Verbose |
| **Reconnect replay counters** | Batch size, created, skipped (tombstoned), skipped (duplicate) | Log |

### FLiveSyncStats Counters (O(1), std::memory_order_relaxed)

| Counter | Type | Description |
|---------|------|-------------|
| `RenamesProcessed` | uint64 | Total renames applied (all origins) |
| `RenamesPerSecond` | double | EMA-smoothed rename rate |
| `RenameSuppressions` | uint64 | Callback suppressions applied |
| `RenameStaleRejections` | uint64 | Renames rejected for Tombstoned/Unknown GUID |
| `RenameReplayCount` | uint64 | Renames applied during replay |
| `RenameReplaySkipped` | uint64 | Renames skipped during replay (tombstone/duplicate) |
| `RenameStormWarnings` | uint64 | Flood detection warnings triggered by rename rate |

### Trace Scopes

| Scope | Location |
|-------|----------|
| `UELiveSync_HandleRename` | Wrapping `HandleRename()` |
| `UELiveSync_RenameReplay` | Wrapping replay rename batch |
| `UELiveSync_RenameSuppression` | Wrapping `OnActorLabelChanged` suppression guard |

---

## 9. Explicit Non-Goals

This vertical slice explicitly does NOT attempt to solve:

| Non-Goal | Reason |
|----------|--------|
| Collaborative editing | Requires server authority; deferred to Phase 9 |
| Bidirectional rename authority | Blender→UE only in this slice. UE→Blender requires Blender-side TCP listener (infrastructure not yet built) |
| Conflict resolution | Last-writer-wins only. No three-way merge, no conflict UI |
| Undo synchronization | Rename undo would require recording pre-rename state and sending inverse mutation. Not in this slice |
| Semantic merge logic | No "smart" rename conflict resolution based on naming conventions |
| Editor transaction replay | Undo/redo stack sync is a separate system |
| Multi-user rename arbitration | Single peer only (one Blender, one UE Editor) |
| Rename during PIE | PIE mode suppresses all replication; rename during PIE is ignored |
| Rename via Python API in UE | Only World Outliner user renames; scripted renames are excluded |

---

## 10. Implementation Ordering

Recommended sequence of implementation. Each step is a discrete,
testable milestone.

| Step | What | Deliverable | Depends On |
|------|------|-------------|------------|
| 1 | **PT_RENAME packet definition** | Add `PT_RENAME = 0x0C` constant. Define `SERIALIZE_RENAME(guid, name)` / `DESERIALIZE_RENAME(payload)` in `network.py` and `SyncTypes.h`. | — |
| 2 | **Blender rename detection** | In `sync.py`, track `obj.name` per GUID between iterations. When changed, set dirty flag → serialize PT_RENAME. | Step 1 |
| 3 | **UE rename application** | Add `case PT_RENAME` in `ProcessBinaryPacket` dispatch. Implement `HandleRename()` that calls `SetActorLabel()`. | Step 1 |
| 4 | **Provenance propagation** | Wire `EChangeOrigin` through `HandleRename()`. Implement `FScopedChangeOrigin` RAII helper. Tag enqueued packets. | Step 3 |
| 5 | **Suppression guards** | Implement `FScopedReplicationSuppression` and `SuppressedGUIDSet`. Wire into `HandleRename()` and `OnActorLabelChanged` handler. | Step 4 |
| 6 | **Reconnect replay** | Blender side: emit `PT_RENAME` during snapshot. UE side: tag as REPLAY, check tombstone, skip duplicates. | Step 5 |
| 7 | **Observability** | Add `UE_LOG` traces, `TRACE_CPUPROFILER_EVENT_SCOPE` markers, `FLiveSyncStats` counters. Add rename diagnostics to panel. | Step 5 |
| 8 | **Stress testing** | Batch rename (500 objects), rapid rename (100× same object), rename/delete races, malformed rename packets. | Step 7 |
| 9 | **Reconnect storm testing** | 20-cycle reconnect storm with interleaved renames during disconnection. | Step 6, Step 8 |
| 10 | **Invariant validation** | Run all Phase 5 validation tests. Verify no frozen-zone modifications. Verify Tick pipeline intact. | Step 9 |

---

## 11. Rollback Criteria

### Immediate Pause Conditions

If any of the following are observed during implementation, work must pause,
an ADR review must be scheduled, and the Phase 6 scope must be reassessed:

| Condition | Why | Action |
|-----------|-----|--------|
| Rename implementation requires modifying the **packet parser** (`ProcessBinaryPacket` version dispatch or header parsing) | Parser is FROZEN. New packet types must be added via new `case` branch, NOT by modifying existing dispatch logic. | Pause → ADR review → Verify new branch only |
| Rename implementation requires changing the **Tick pipeline ordering** | Pipeline order is FROZEN. Rename processing must be a new stage appended after existing stages, OR inlined into ProcessQueuedPackets without reordering. | Pause → ADR review → Verify no reorder |
| Rename implementation requires modifying **FLiveSyncQueue** or **FLiveSyncPendingAssetQueue** ownership model | Queue ownership is FROZEN. Rename packets must use existing enqueue paths. | Pause → ADR review → Use existing queue |
| Rename implementation requires changing **network thread lifecycle** or shutdown order | Thread lifecycle is FROZEN. Rename does not need new threads. | Pause → ADR review → Defer or reject |
| Rename implementation requires adding fields to **FSyncTransformState** | Object layout is FROZEN. Name is NOT part of FSyncTransformState. Store name separately in `TMap<FGuid, FString>`. | Pause → ADR review → Use separate name map |
| Rename implementation requires modifying **FLiveSyncPacket** struct in a way that changes existing field layout | Packet struct layout affects queue serialization. New fields must be added to a rename-specific struct or a union. | Pause → ADR review → Isolate rename data |

### Rollback Sequence

If pause conditions are met:

1. Revert the offending change
2. Log an ADR describing the proposed frozen-zone modification
3. Evaluate alternative approaches that do not touch frozen zones
4. If no alternative exists, escalate to project lead for Phase 6 scope
   reassessment

---

## 12. Test Strategy

### Unit / Functional Tests (Python, Blender-side)

| Test | What | Expected |
|------|------|----------|
| `test_rename_single()` | Rename one object in Blender, verify PT_RENAME packet on wire | Correct GUID, name, header magic, version, checksum |
| `test_rename_rapid_sequential()` | Rename same object 10 times in 0.5s | All intermediate states captured or final state correct |
| `test_rename_no_change_skipped()` | Set obj.name = same value | No PT_RENAME emitted |
| `test_rename_special_chars()` | Rename with Unicode, spaces, special chars | UTF-8 encoding correct, length field accurate |
| `test_rename_max_length()` | Rename with 256-byte name | Truncation or rejection at 256 |

### Integration Tests (Blender + UE, requires UE editor)

| Test | What | Expected |
|------|------|----------|
| `test_rename_single_replicates()` | Rename in Blender → verify UE actor label | Label matches within 1s |
| `test_rename_storm_100()` | Rename same actor 100 times rapidly | All renames processed, no crash, no flood trigger |
| `test_rename_storm_500()` | Rename 500 different actors | All labels correct, no packet loss |
| `test_rename_delete_race()` | Delete actor in UE → send rename for its GUID | Rename rejected, warning logged |
| `test_rename_tombstone()` | Delete in Blender → tombstone in UE → re-rename in Blender | Rename rejected while tombstoned |
| `test_rename_reconnect_replay()` | Disconnect → rename in Blender → reconnect | Replay applies final rename, no duplicates |
| `test_rename_reconnect_no_duplicate()` | Disconnect → same rename in Blender+UE → reconnect | Last-writer-wins (Blender authoritative), no feedback loop |
| `test_rename_reconnect_storm()` | Disconnect → rename 100 objects → reconnect | All 100 final labels correct, no replay duplicates |
| `test_rename_malformed_packet()` | Send truncated PT_RENAME, oversized name, bad checksum | Packet rejected, log warning, no crash |

### Protocol Tests (wire format, offline)

| Test | What | Expected |
|------|------|----------|
| `test_rename_packet_roundtrip()` | Serialize → send → receive → deserialize | Binary round-trip identical |
| `test_rename_header_valid()` | Verify PT_RENAME uses correct magic, version, type, checksum | Standard header validation passes |
| `test_rename_payload_bounds()` | Zero-length name, max-length name, oversized name | Zero-length accepted (empty name?), max-length accepted, oversized rejected |

### Invariant Tests (must pass after rename implementation)

| Test | Source | Expected |
|------|--------|----------|
| Phase 5 validation suite | `python3 tests/phase5e_validation.py` | 5/5 PASS |
| Phase 5C fuzz suite | `python3 tests/phase5c_fuzz_protocol.py` | All PASS |
| Phase 5C stress suite | `python3 tests/phase5c_stress_protocol.py` | All PASS |
| Phase 5D asset identity | `python3 tests/phase5d_validation_A_asset_identity.py` | All PASS |
| Tick continuity | Pipeline health check (BEGIN/END balance) | Balanced across all Tick stages |

### Test File Location

```
tests/
├── phase6_rename.test_rename_single.py
├── phase6_rename.test_rename_stress.py
├── phase6_rename.test_rename_reconnect.py
├── phase6_rename.test_rename_malformed.py
├── phase6_rename.test_rename_invariant.py
└── run_phase6_rename.py
```

---

## 13. Cross-Reference Summary

| Document | Sections Referenced | Consistency Check |
|----------|--------------------|-------------------|
| `12-core-runtime-invariants.md` | §4 (Tick pipeline), §5 (Network thread), §7 (Parser), §8 (Object layout) | Rename does not modify any of these systems. New packet type only. |
| `13-phase6-design-constraints.md` | §12 (Provenance), §13 (Propagation), §14 (Suppression tokens), §15 (Ordering risks), §17 (Lifecycle), §18 (Reconnect replay), §19 (Observability) | Provenance model, suppression patterns, reconnect semantics all adopted directly from this document. Semantic-event vs state-stream distinction aligns with §12 provenance requirements. |
| `18-phase6-scope-lock.md` | §3 (IN-SCOPE: rename), §5 (Authority boundaries), §7 (Architectural preservation), §8 (Escalation rules) | Rename is listed as IN-SCOPE. Authority model (Blender→UE only) matches §5. Escalation rules adopted for rollback criteria. |

---

## 14. Revision History

| Date | Version | Author | Changes |
|------|---------|--------|---------|
| 2026-05-25 | 1.0 | System | Initial vertical-slice plan for rename replication |
| 2026-05-25 | 1.1 | System | Added §3: Semantic Event vs State Stream — conceptual boundary between transform streaming and semantic editor-event replication. Updated cross-reference and warning box. |
