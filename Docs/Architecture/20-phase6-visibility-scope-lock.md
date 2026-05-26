# Phase 6 — Visibility Replication Scope Lock

> **Created**: 2026-05-25 · **Updated**: 2026-05-26
> **Status**: STABILIZED — Phase 6C Complete (2026-05-26)
> **Predecessor**: Rename Vertical Slice (STABILIZED, `19-phase6-vertical-slice-rename.md`)
>
> This document defines the exact boundaries for the SECOND Phase 6
> semantic-event vertical slice: visibility replication.

---

## 1. Purpose

Visibility replication is the second minimal editor-authority workflow.
It validates that the semantic-event pattern (provenance → suppression →
replay → observability) generalizes from rename to a different editor
mutation — one with different callbacks, different suppression
requirements, and different replay semantics.

### Why Visibility After Rename

| Criterion | Rename Slice | Visibility Slice |
|-----------|-------------|------------------|
| Callback risk | High: `OnActorLabelChanged` fires synchronously | Low: no standard callback on `SetIsTemporarilyHiddenInEditor` |
| Suppression requirement | Hard: infinite loop without it | Soft: pattern adherence, no recursion risk |
| Wire complexity | Variable-length (strings) | Fixed-length (1 byte bool) |
| Blender API | `obj.name = ...` | `obj.hide_set(True/False)` |
| UE API | `AActor::SetActorLabel()` | `AActor::SetIsTemporarilyHiddenInEditor()` |
| Replay idempotency | String comparison | Bool comparison |
| Semantic purity | Pure semantic event | Semantic event with bool state |

Visibility is architecturally simpler than rename (no variable-length
strings, no callback recursion), making it an ideal second slice to
validate the pattern generalizes correctly.

---

## 2. IN SCOPE

| Item | Description |
|------|-------------|
| Editor hidden-state detection (Blender) | Detect `obj.hide_get()` or `obj.hide_viewport` changes between sync iterations in `sync.py`. Changes are discrete user toggles. |
| Visibility packet serialization | New packet type `PT_Visibility` (`0x0B`). Payload: GUID (16 bytes) + hidden (1 byte, 0=visible, 1=hidden) + seq (4 bytes) + ts (8 bytes). Fixed 29 bytes per object. |
| UE visibility application | On receiving `PT_Visibility`, apply `AActor::SetIsTemporarilyHiddenInEditor(hidden)`. Must run on game thread. |
| Provenance propagation | Reuse `EChangeOrigin` enum. Tag every visibility mutation before applying. `REMOTE_REPLICATED` for normal packets, `REPLAY` for snapshot replay. |
| Scoped suppression (RAII) | `FScopedVisibilitySuppression` guard wrapping the apply call. Pattern-adherence suppression — no callback risk but maintains architectural consistency. |
| Reconnect replay handling | During snapshot replay on reconnect, include visibility state per GUID. Replayed visibility toggles tagged `REPLAY`. |
| Monotonic per-GUID sequence | Same pattern as `FRenameSequenceTracker`: `FVisibilitySequenceTracker` — maps GUID → last sequence, rejects stale (`<=`), bounded at 2048 entries. |
| Observability/logging | `[VISIBILITY]` prefixed UE_LOG messages: origin, GUID, state, suppression entry/exit. TRACE_CPUPROFILER_EVENT_SCOPE on HandleVisibility + parse block. |
| FLiveSyncStats counters | `VisibilityProcessed`, `VisibilityStaleRejections`, `VisibilityReplayApplied`, `VisibilityReplaySkipped` — `std::memory_order_relaxed` atomic increments. |
| Malformed packet handling | Truncated payload, oversized batch — reject with Warning log, increment `Stats.MalformedPackets`, return. |
| Focused test suite | `tests/phase6_visibility_validation.py`: single toggle, storm, reconnect, stale, duplicate, malformed, suppression loop. |
| Reconnect safety | Tracker cleared in `StopNetworkThread()` and `ConsoleReset()`. Blender-side tracker cleaned in `_close_internal()`. |

---

## 3. OUT OF SCOPE

| Item | Rationale |
|------|-----------|
| Hierarchy visibility (child follows parent) | Different UE API (`AActor::SetIsHiddenParent`). Requires parent-child resolution and cascade logic. Separate vertical slice. |
| Collection visibility (collection-level toggle) | Uses `UWorld::RemoveFromWorld` / editor-collection API. Different packet type and scope. Separate vertical slice. |
| Runtime/game visibility (`AActor::SetActorHiddenInGame`) | Different UE flag with different networking semantics (relevancy, culling). Different lifecycle. Separate vertical slice. |
| Render-layer visibility | Per-layer visibility is a render-pass concern, not an editor identity concern. Not in Phase 6 scope. |
| Generalized semantic framework | No generic "editor event" abstraction. Each slice is an isolated packet type and handler. |
| Bidirectional visibility replication (UE→Blender) | Deferred until Blender-side TCP listener infrastructure exists (same constraint as rename). This slice is Blender→UE only. |
| Undo/redo sync | Visibility undo would require recording pre-toggle state and sending inverse mutation. Not in this slice. |
| Transaction merge systems | No visibility coalescing beyond per-GUID sequence dedup. |
| Editor-outliner visibility (eye icon) | The eye-icon toggle in UE World Outliner fires different API calls depending on UE version. Deferred. |
| Multi-user visibility arbitration | Single peer only (one Blender, one UE Editor) — same constraint as rename. |
| Visibility during PIE | PIE mode suppresses all replication; visibility during PIE is ignored. |
| Scripted visibility changes (Python in UE) | Only user-initiated toggles from Blender; UE-side scripted changes are excluded. |

---

## 4. Authority Model

Same as rename slice: **Blender is authoritative for visibility state**.

```
Blender user toggles visibility
  → PT_Visibility packet
  → UE applies SetIsTemporarilyHiddenInEditor()
  → (optional future: UE→Blender direction deferred)
```

### Why Blender-Authoritative

1. **Consistency with rename slice** — Both slices share the same authority direction. Two-slice consistency builds user trust.
2. **Infrastructure readiness** — Blender has no TCP listener for UE→Blender replication. Deferred until common networking infrastructure supports it.
3. **No conflict resolution needed** — Single-direction replication avoids last-writer-wins arbitration. Simple, predictable behavior.

---

## 5. Escalation Rules

If visibility implementation requires any of the following, work must
pause and an architecture review must be scheduled:

| Condition | Why | Action |
|-----------|-----|--------|
| Modification to `LiveSyncQueue.h`, `PendingAssetQueue.h`, `LiveSyncRunnable.h` | Queue/thread ownership is FROZEN. Visibility packets must use existing enqueue paths. | Pause → ADR review → Defer |
| Modification to Tick pipeline ordering | Pipeline order is FROZEN. Visibility must be inlined into `ProcessQueuedPackets` without reordering. | Pause → ADR review → Verify no reorder |
| Modification to `FSyncTransformState` | Object layout is FROZEN. Visibility state lives in a separate `TMap<FGuid, bool>`. | Pause → ADR review → Use separate map |
| Modification to 24-byte packet header | Header layout is FROZEN. New packet type is handled via existing type byte. | Pause → ADR review → New packet type only |
| Addition of cross-thread visibility state | Thread safety model is FROZEN. All visibility state accessed only from game thread. | Pause → ADR review → Game-thread only |
| Generalized semantic event system | No generic dispatcher — each semantic event has its own case branch. | Pause → ADR review → Keep isolated branches |

---

## 6. Done Criteria

The visibility vertical slice is complete when:

1. PT_Visibility packet (`0x0B`) defined in `SyncTypes.h` and `network.py`
2. `FVisibilitySequenceTracker` defined in `SyncTypes.h` (bounded 2048, stale/duplicate rejection)
3. Blender visibility detection in `sync.py` (diff `hide_get()` per GUID)
4. Visibility serialization in `network.py` (`serialize_visibility()`)
5. UE `HandleVisibility()` in `UELiveSyncSubsystem.cpp` (provenance scope, suppression scope, sequence validation)
6. PT_Visibility dispatch case in `ProcessBinaryPacket` (separate block, before main transform loop)
7. `FScopedVisibilitySuppression` RAII guard
8. `FLiveSyncStats` visibility counters
9. `TRACE_CPUPROFILER_EVENT_SCOPE` on HandleVisibility + parse block
10. Tracker cleared on StopNetworkThread + ConsoleReset + Blender disconnect
11. `kValidTypes[]` updated to include `0x0B`
12. FNV checksum updated
13. Test suite: `tests/phase6_visibility_validation.py` (10+ tests)
14. No frozen-zone modifications
15. All Phase 5 tests still pass
16. Documentation updated

---

## 7. Revision History

| Date | Version | Changes |
|------|---------|---------|
| 2026-05-25 | 1.0 | Initial scope lock for visibility replication vertical slice |
| 2026-05-25 | 2.0 | Implementation complete — Phase 6C delivered |
