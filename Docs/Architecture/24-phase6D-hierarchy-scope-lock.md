# Phase 6D — Hierarchy Replication Scope Lock

> **Created**: 2026-05-26
> **Status**: STABILIZED (Stage 13, 97/97 standalone tests pass)
> **Predecessors**: Rename (STABILIZED, Phase 6A/6B · `0x0C`) · Visibility (STABILIZED, Phase 6C · `0x0B`)
> **Next**: Vertical Slice Design (`25-phase6D-vertical-slice-hierarchy.md`)
>
> This document defines the **hard scope boundaries** for the third Phase 6
> semantic-event vertical slice: hierarchy (parent-child) replication.
>
> Hierarchy is the **first dependency-sensitive semantic lane** — it introduces
> inter-object ordering, graph consistency constraints, orphan states, replay
> dependency chains, and cycle detection. This is a substantial complexity
> increase over both rename and visibility.

---

## 1. Purpose

Hierarchy replication is the third minimal editor-authority workflow. It
replicates **attachment intent** between Blender and the Unreal Editor:
when Blender re-parents an object, the corresponding UE actor is re-attached
to the matching parent actor.

### Why Hierarchy After Visibility

| Criterion | Rename | Visibility | Hierarchy |
|-----------|--------|------------|-----------|
| **Mutation scope** | Single object | Single object | Object pair (parent + child) |
| **Dependency sensitivity** | None | None | **Required** — parent must exist |
| **Replay complexity** | Low (per-GUID dedup) | Low (per-GUID dedup) | **Medium/High** — ordering dependent |
| **Graph constraints** | None | None | Acyclic graph, no self-parent |
| **Orphan states** | None | None | Missing-parent deferral |
| **Existing runtime support** | None | None | Existing `ResolvePendingAttachments`, `AttachToParent` |
| **Cycle detection** | None | None | Required (self, direct cycle, chain cycle) |
| **Suppression risk** | Callback loop | None (pattern-only) | No standard callback — pattern-only |

Hierarchy is architecturally more complex than both prior lanes, requiring
explicit scope control to prevent scope creep.

### Key Insight: Hierarchy ≠ Transform Ownership

The hierarchy semantic lane replicates **attachment intent only** — the fact
that object A is a child of object B. It does **NOT** own:

- Transform propagation (parent motion → child motion)
- Interpolation behavior (world-space vs local-space)
- Local/world transform evaluation
- Scene graph mutation timing

These are owned by the **existing runtime hierarchy systems**:

| System | Location | Owner | Frozen? |
|--------|----------|-------|---------|
| `FSyncTransformState::ParentGuid` / `bHasParent` | `SyncTypes.h:169-176` | Phase 5 runtime | **FROZEN** |
| `ResolvePendingAttachments` | `UELiveSyncSubsystem.cpp:5487-5689` | Phase 5 runtime | **FROZEN** |
| `AttachToParent` | `UELiveSyncSubsystem.cpp:4181-4484` | Phase 5 runtime | **FROZEN** |
| `InterpolateTransforms` (local-space child path) | `UELiveSyncSubsystem.cpp` Tick pipeline | Phase 5 runtime | **FROZEN** |
| `bPendingSceneGraphWrite` lifecycle | `SyncTypes.h:166-167` | Phase 5 runtime | **FROZEN** |
| Detach/reparent timing | `UELiveSyncSubsystem.cpp` | Phase 5 runtime | **FROZEN** |

The hierarchy semantic lane produces **semantic events** that may eventually
call into these systems via the same `AttachToActor` / `DetachFromActor`
APIs, but it must NOT modify, refactor, or duplicate them.

---

## 2. IN SCOPE

### 2.1 Hierarchy Intent Packet (PT_Hierarchy)

| Field | Value |
|-------|-------|
| **Packet type** | `PT_Hierarchy = 0x0D` |
| **Direction** | Blender → UE (Phase 6D); UE → Blender deferred (see §3) |
| **Semantics** | Replicates attachment intent: child GUID + parent GUID (+ null parent = detach) |
| **Wire format** | Variable: `GUID(16) + parentGUID(16) + seq(4) + ts(8)` — 44 bytes fixed per object (parent GUID all-zero = detach to root) |
| **Event type** | Discrete semantic mutation — NOT a state stream. Fires on user re-parent action, not on every transform tick. |

### 2.2 Blender-Side Detection

| Item | Description |
|------|-------------|
| Change detection | Diff `obj.parent` reference between sync iterations in `sync.py`. Store `_last_parent_guid: Dict[str, str]` mapping object UUID → parent UUID. |
| Scope | All MESH objects (existing filter) — no cameras, lights, armatures |
| Detach detection | `parent = None` → parent GUID becomes all-zero |
| Reparent detection | `_last_parent_guid[uuid] != new_parent_uuid` → emit PT_Hierarchy |
| Initial state sync | During first-sync or reconnect-snapshot, emit PT_Hierarchy for every non-root object |
| Sequence tracking | Per-object monotonic sequence counter (`_hierarchy_sequences: Dict[str, int]`), cleared on disconnect |

### 2.3 UE-Side Application

| Item | Description |
|------|-------------|
| Handler | `HandleHierarchy(FGuid ChildGuid, FGuid ParentGuid, uint32 Seq, double Timestamp)` |
| Provenance | `EChangeOrigin::RemoteReplicated` (normal) / `EChangeOrigin::Replay` (snapshot) |
| Suppression | `FScopedHierarchySuppression` RAII guard — pattern-adherence (no standard callback risk) |
| Cycle detection | **Required**: reject self-parent, direct cycle (A→B→A), chain cycle (walk depth-limited parent chain) |
| Existence check | Child must exist in `ActorCache`; parent GUID all-zero is valid (detach) |
| Deferred resolution | If parent GUID is non-zero and parent not in `ActorCache` → enqueue deferred retry (see §2.5) |
| Detach | Parent GUID all-zero → `DetachFromActor(KeepWorldTransform)` |
| Re-attach | Parent GUID changed → `DetachFromActor` (if currently attached) → `AttachToActor(KeepWorldTransform)` |
| Log prefix | `[HIERARCHY]` |
| Profiler scopes | `UELiveSync_HandleHierarchy`, `UELiveSync_ProcessHierarchyPackets` |

### 2.4 Replay Semantics

| Aspect | Behavior |
|--------|----------|
| Sequence tracker | `FHierarchySequenceTracker` — bounded 2048, stale/duplicate rejection via `<=` |
| Replay ordering | Hierarchy packets during snapshot replay **must** respect parent-before-child ordering. |
| Ordering guarantee | The snapshot builder on Blender side **must** emit parent objects' hierarchy intent before child objects' hierarchy intent. |
| Stale rejection | Same pattern as rename/visibility: `IncomingSeq <= LastSeq` → reject |
| Snapshot replay | `bInSnapshotBuild == true` → tag `EChangeOrigin::Replay`; ordering enforced by Blender snapshot emission order |
| Reconnect | Tracker cleared in `StopNetworkThread()`, `ConsoleReset()`, Blender `_close_internal()` |

### 2.5 Dependency Ordering Rules

Hierarchy is the **first dependency-sensitive semantic lane**. The following
rules govern ordering:

| Rule | Description |
|------|-------------|
| **Parent must exist** | Before attaching child to parent, the parent actor must be present in `ActorCache`. This is the fundamental dependency. |
| **Child must exist** | The child actor must exist in `ActorCache`. If the child hasn't been spawned yet (CREATE packet not yet processed), defer. |
| **Deferred retry** | If dependency not met → enqueue with bounded retry window (5 seconds / 60 frames, matching existing `ResolvePendingAttachments` behavior). |
| **Replay ordering** | Blender snapshot builder must emit hierarchy intent for parents before children. Without this ordering guarantee, replay will trigger max deferred retries for every child whose parent arrives later in the snapshot stream. |
| **Out-of-order recovery** | If parent arrives after child in snapshot, the deferred retry mechanism resolves it within the 5s window. |
| **Detach has no dependency** | Detach (parent GUID = all-zero) requires no parent existence check — always applied immediately. |

### 2.6 Orphan Policy

An **orphan** is a child whose parent GUID is non-zero but the parent actor
never arrives (or arrives outside the retry window).

| Policy | Behavior |
|--------|----------|
| **Defer temporarily** | On missing parent, enqueue in `PendingHierarchyAttachments` (separate array from runtime `PendingAttachments`) |
| **Bounded retry window** | 5 seconds / 60 frames max (matching existing runtime deferred attachment behavior) |
| **Retry frequency** | Every frame for first 10 frames, then every 5th frame (matching existing pattern) |
| **Timeout behavior** | After timeout, **log warning** with `[HIERARCHY][ORPHAN]` prefix, evict from queue, leave child as root |
| **Late parent recovery** | If parent arrives after timeout: the child remains root. No retroactive attachment — the semantic event is considered stale. A re-sync (new hierarchy packet for the same child with same parent GUID) would be required to attach. |
| **Orphan logging** | `[HIERARCHY][ORPHAN] Child=%s parent=%s — deferred (attempt %d/%d)` on each retry; `[HIERARCHY][ORPHAN] Child=%s parent=%s — TIMEOUT — leaving as root` on eviction. |
| **Reconnect orphan recovery** | On reconnect, Blender re-emits hierarchy intent for all non-root objects. Orphans from the previous session are replaced by fresh snapshot state. |

### 2.7 Cycle Policy

Cyclic attachments are **always rejected immediately**:

| Cycle Type | Detection | Action |
|------------|-----------|--------|
| Self-parent | `ChildGuid == ParentGuid` | Reject immediately. Log `[HIERARCHY][CYCLE] Self-parent rejected: GUID=%s`. No deferral. |
| Direct cycle | A→B→A (B is child of A, A wants to be child of B) | Reject immediately. Log `[HIERARCHY][CYCLE] Direct cycle rejected: child=%s parent=%s`. No deferral. |
| Chain cycle | A→B→C→A (walk parent chain from B up to root, detect A in chain) | Reject immediately. Log `[HIERARCHY][CYCLE] Chain cycle rejected: child=%s parent=%s (depth=%d)`. No deferral. |
| Recursive attachment loop | Repeated cycle attempts for same pair | Log `[HIERARCHY][CYCLE] Repeated cycle attempt: child=%s parent=%s (count=%d)` — suppress further warnings (ratelimit). |

Explicitly forbidden:

- **No auto-repair**: The system does NOT attempt to fix cycles by implicit detach.
- **No implicit detach-to-root**: If a cycle is detected, the attachment is rejected but the child's **current** parent (if any) is left unchanged.
- **No automatic retry**: Cycle rejection is permanent for that packet. The user must correct the hierarchy in Blender and trigger a new hierarchy event.
- **No partial application**: If a batch of hierarchy packets contains a cycle, the offending packet is rejected individually; other packets in the batch are processed normally.

### 2.8 Observability

| Item | Specification |
|------|---------------|
| **Counters** | `HierarchyProcessed`, `HierarchyStaleRejections`, `HierarchyReplayApplied`, `HierarchyReplaySkipped`, `HierarchyOrphans`, `HierarchyCycles` |
| **Log prefix** | `[HIERARCHY]` |
| **Orphan prefix** | `[HIERARCHY][ORPHAN]` |
| **Cycle prefix** | `[HIERARCHY][CYCLE]` |
| **Application log** | `[HIERARCHY] Applying: child=%s parent=%s (origin=%s seq=%u)` |
| **Detach log** | `[HIERARCHY] Detach: child=%s (origin=%s seq=%u)` |
| **Deferred log** | `[HIERARCHY][ORPHAN] Deferred: child=%s parent=%s attempt=%d/%d` |
| **Timeout log** | `[HIERARCHY][ORPHAN] TIMEOUT: child=%s parent=%s — leaving as root` |

### 2.9 Reconnect Cleanup

| Action | Behavior |
|--------|----------|
| `StopNetworkThread()` | Clear `FHierarchySequenceTracker` |
| `ConsoleReset()` | Clear tracker, zero all `Hierarchy*` counters |
| Blender `_close_internal()` | Clear `_hierarchy_sequences`, clear `_last_parent_guid` |
| `PendingHierarchyAttachments` | Cleared on `HandleEndSnapshot` and `ConsoleReset` |

---

## 3. OUT OF SCOPE

### 3.1 Excluded Features

| Item | Rationale |
|------|-----------|
| **Transform ownership** | The semantic lane replicates attachment intent only. Transform propagation, interpolation, local/world evaluation remain owned by the frozen Phase 5 runtime. See §1 Key Insight. |
| **Runtime hierarchy modification** | No changes to `ResolvePendingAttachments`, `AttachToParent`, `InterpolateTransforms`, `FSyncTransformState::ParentGuid`/`bHasParent`, `bPendingSceneGraphWrite`, or any other frozen runtime hierarchy system. |
| **UE→Blender direction** | Deferred until Blender-side TCP listener infrastructure exists. This slice is Blender→UE only, matching the rename and visibility patterns. |
| **Lifecycle/delete replication** | Deferred until hierarchy is stabilized. Hierarchy is a prerequisite for lifecycle — orphan detection requires hierarchy resolution before destruction can be safely replicated. See §7. |
| **Collection hierarchy** | Blender collection nesting is not the same as object parent-child hierarchy. Requires separate packet type and scope lock. |
| **Deferred sub-object sync (skeletal/posed children)** | Only rigid MESH parent-child is in scope. Skeletal attachment uses different UE APIs. |
| **Generalized semantic framework** | No shared abstraction for dependency-sensitive lanes. 5+ lanes required before abstraction consideration. |
| **Bidirectional re-parent** | UE user re-parent → Blender update is deferred (requires Blender-side TCP listener). |
| **Non-MESH hierarchy** | Cameras, lights, armatures are not in sync scope. Their parent-child relationships are excluded. |
| **Editor-only attachment concepts** | UE attachment rules (KeepWorldTransform vs KeepRelativeTransform) are not exposed through the semantic lane — `KeepWorldTransform` is the sole behavior. |
| **Attachment constraint rules** | UE attachment constraint snapping, socket attachments, bone attachments — all out of scope. Only `AttachToActor(KeepWorldTransform)` is used. |
| **Undo/redo of hierarchy changes** | No recording of pre-mutation parent state. Undo would re-sync from Blender on next hierarchy event. |
| **Physics-driven attachment** | If physics simulation drives parent-child separation in UE, the hierarchy lane does NOT reconcile it. Blender is authoritative for attachment intent. |
| **Parent-aware replay reordering** | Replay ordering is guaranteed by Blender snapshot emission order (parents before children). No UE-side topological sort is implemented. |

### 3.2 Deferred to Post-Hierarchy-Stabilization

| Item | Dependency on Hierarchy | Target |
|------|------------------------|--------|
| Lifecycle/delete replication | Orphan detection requires hierarchy | After hierarchy stabilization |
| Tombstone systems | Requires orphan lifecycle | After lifecycle replication |
| UE→Blender hierarchy | Requires Blender TCP listener | After bidirectional infra |
| Collection→folder mapping | Different packet type | Separate scope lock |
| Attachment constraint rules | Beyond KeepWorldTransform | Phase 7+ |

---

## 4. Authority Model

**Blender is authoritative for parent-child relationships** (matching the
rename and visibility authority pattern).

```
Blender user re-parents object
  → Blender detects parent change via _last_parent_guid diff
  → PT_Hierarchy packet (child GUID + parent GUID + seq + ts)
  → UE HandleHierarchy()
      → Cycle detection (reject if cyclic)
      → Existence check (defer if parent missing, bounded retry)
      → AttachToActor(KeepWorldTransform) / DetachFromActor(KeepWorldTransform)
```

### Why Blender-Authoritative

1. **Consistency** — Rename, visibility, and hierarchy all share the same
   authority direction. Three-slice consistency builds architectural trust.
2. **Infrastructure readiness** — No Blender-side TCP listener for UE→Blender
   replication. Deferred until common networking infra supports it.
3. **Deterministic behavior** — Single-direction replication avoids
   last-writer-wins arbitration for parent relationships, which could
   produce split-brain cyclic states.

### Authority Restrictions

| Operation | Authority | Notes |
|-----------|-----------|-------|
| Parent assignment | Blender | Semantic event (PT_Hierarchy) |
| Detach to root | Blender | Same packet type, all-zero parent GUID |
| Reparent to different parent | Blender | Same packet type |
| Cycle rejection | UE | Always reject, never apply. Log, increment cycle counter. |
| Orphan timeout | UE | Reject after bounded window. Child remains root. |
| Transform evaluation | Phase 5 runtime | Unchanged — frozen system |
| Interpolation mode (local vs world) | Phase 5 runtime | Unchanged — frozen system |

---

## 5. Frozen Runtime Separation

### 5.1 Systems the Hierarchy Semantic Lane Must NOT Modify

| System | File(s) | Why |
|--------|---------|-----|
| `FLiveSyncQueue` (128 MPSC) | `LiveSyncQueue.h` | Queue ownership FROZEN — hierarchy packets use existing enqueue paths |
| `FLiveSyncPendingAssetQueue` (2048) | `PendingAssetQueue.h` | Queue ownership FROZEN |
| `LiveSyncRunnable` (network thread) | `LiveSyncRunnable.h/cpp` | Thread lifecycle FROZEN |
| `UELiveSyncSubsystem::Tick()` pipeline ordering | `UELiveSyncSubsystem.cpp` | Pipeline FROZEN — hierarchy inlined into ProcessQueuedPackets |
| `FSyncTransformState` layout (incl. ParentGuid, bHasParent) | `SyncTypes.h:169-176` | Object layout FROZEN — hierarchy state in separate TMap |
| 24-byte packet header | `SyncTypes.h` (implicit) | Header FROZEN |
| `ProcessBinaryPacket` version dispatch | `UELiveSyncSubsystem.cpp` | Parser FROZEN — add new case only |
| `ResolvePendingAttachments` | `UELiveSyncSubsystem.cpp:5487-5689` | Runtime FROZEN — semantic lane has its own deferred resolution |
| `AttachToParent` | `UELiveSyncSubsystem.cpp:4181-4484` | Runtime FROZEN — semantic lane calls `AttachToActor` directly |
| `InterpolateTransforms` (local-space child path) | `UELiveSyncSubsystem.cpp` | Runtime FROZEN |
| `UpdateTargetTransform` | `UELiveSyncSubsystem.cpp` | Runtime FROZEN |
| `bPendingSceneGraphWrite` lifecycle | `UELiveSyncSubsystem.cpp` | Runtime FROZEN |
| `FindActorFast` / `ActorCache` ownership | `UELiveSyncSubsystem.h/cpp` | Lookup is safe; mutation is not |

### 5.2 What the Hierarchy Semantic Lane Gets

| Resource | How Accessed |
|----------|-------------|
| `ActorCache` for existence checks | Read-only `FindActorFast(Guid)` — game thread safe |
| `AttachToActor(KeepWorldTransform)` | Direct `AActor` API call (not via frozen `AttachToParent` helper) |
| `DetachFromActor(KeepWorldTransform)` | Direct `AActor` API call |
| Existing `FSyncTransformState::ParentGuid` | Read-only for validation (must NOT write) |
| Per-actor transform state | Read-only for parent chain walking in cycle detection |

### 5.3 Parser Invariants (from §9.4 of 22-semantic-event-architecture-conventions.md)

| Rule | Enforcement |
|------|-------------|
| Separate case `PT_Hierarchy` in `ProcessBinaryPacket` | New case only — no existing branch modification |
| Per-object boundary checks | Guard every Memcpy/read against remaining payload |
| PayloadSize validation | Reject if < 44 bytes per object or > MAX_PACKET_SIZE |
| Return on first malformed object | Do not partially apply a batch |
| `Stats.MalformedPackets++` on parse failure | Every malformed path must increment |

---

## 6. Complexity Classification

### 6.1 Complexity Matrix

| Dimension | Rename | Visibility | Hierarchy |
|-----------|--------|------------|-----------|
| **Semantic mutation type** | String assignment | Bool assignment | Graph edge reassignment |
| **Objects affected per event** | 1 | 1 | 2 (parent + child) |
| **Dependency sensitivity** | None | None | **High** — parent must exist |
| **Cycle risk** | None | None | **High** — self, direct, chain cycles |
| **Orphan risk** | None | None | **Medium** — missing parent |
| **Replay ordering required** | No | No | **Yes** — parent before child in snapshot |
| **Replay complexity** | Low — per-GUID dedup suffices | Low — per-GUID dedup suffices | **Medium** — ordering + dedup |
| **State explosion risk** | Low — one string per GUID | Low — one bool per GUID | **Low** — edge per pair, bounded by object count |
| **Suppression necessity** | Required (callback loop) | Pattern-only | **Pattern-only** (no standard callback) |
| **Existing runtime overlap** | None | None | **High** — intentional separation from frozen systems |
| **Validation complexity** | Low — is rename correct? | Low — is visibility correct? | **High** — is parent-correct? children-updated? cycle-free? orphans-resolved? |
| **Test scenarios needed** | ~10 | ~12 | **~20+** (single parent, multi-level, reparent, detach, cycle, orphan timeout, snapshot ordering, large hierarchy, reconnect, mixed events, stress) |

### 6.2 Why Hierarchy Is Higher Complexity

1. **Dependency sensitivity**: Attaching A to B requires B to exist. This is
   the first lane where packet processing order matters. Incorrect ordering
   during replay produces transient (or permanent, if timeout) attachment
   failures.

2. **Graph consistency**: The system must maintain an acyclic directed graph.
   Cycles must be detected and rejected without corrupting the graph or
   leaving dangling references.

3. **Orphan states**: If a parent never arrives, the child must degrade
   gracefully (remain root) — but this produces a mismatch between Blender's
   hierarchy (child is attached) and UE's hierarchy (child is root). This
   mismatch is acceptable only as a transient state; if persistent, it is
   a bug that must be surfaced via `[ORPHAN]` logging.

4. **Frozen runtime overlap**: Unlike rename and visibility (which operate
   on entirely new state), hierarchy intent maps directly onto existing
   frozen runtime systems (`ParentGuid`, `ResolvePendingAttachments`).
   The semantic lane must NOT touch these systems, even though they perform
   the same operation (attachment). This is a deliberate architectural
   separation — see §5.

5. **Snapshot ordering guarantee**: Blender must emit hierarchy intent for
   parents before children during snapshot builds. This is a new
   serialization constraint not present in rename or visibility.

---

## 7. Lifecycle/Delete Warning

### 7.1 Dependency Chain

Hierarchy stabilization is a **prerequisite** for lifecycle/delete replication:

```
Hierarchy STABILIZED
  → Orphan detection possible (know which children would be orphaned by delete)
  → Delete intent can be safely replicated (with orphan impact assessment)
  → Tombstone systems can be designed (cleanup of orphaned children on parent delete)
  → Lifecycle replication can proceed
```

### 7.2 Why Lifecycle/Delete Is Deferred

| Reason | Detail |
|--------|--------|
| **Delete without hierarchy is destructive** | If Blender deletes a parent, UE must decide what happens to children. Without hierarchy awareness, children become detached orphans with no tracking. |
| **Orphan cleanup requires hierarchy** | After parent deletion, orphaned children must either be re-parented or destroyed. The hierarchy lane must be stable before designing this behavior. |
| **Tombstone systems depend on hierarchy state** | Tombstones (markers indicating "this actor was intentionally deleted") interact with hierarchy — if a parent is tombstoned but children remain, the scene graph is inconsistent. |
| **Cycle detection is required for delete propagation** | Deleting a node in a cycle is ambiguous. Cycle detection must be resolved before delete semantics can be defined. |
| **Bidirectional delete arbitration is unresolved** | If both Blender and UE delete the same object, which authority wins? This question is deferred (see `13-phase6-design-constraints.md`). |

### 7.3 Explicit Exclusion

- **Lifecycle/delete replication** is **OUT OF SCOPE** for Phase 6D.
- No tombstone system, no orphan cleanup system, no destruction replay semantics.
- These systems require `bHasParent` awareness at minimum, and stable hierarchy detection at production quality.

---

## 8. Escalation Rules

If hierarchy implementation requires any of the following, work must pause
and an architecture review must be scheduled:

| Condition | Why | Action |
|-----------|-----|--------|
| Modification to `LiveSyncQueue.h`, `PendingAssetQueue.h`, `LiveSyncRunnable.h` | Queue/thread ownership is FROZEN | Pause → ADR review → Defer |
| Modification to Tick pipeline ordering | Pipeline FROZEN | Pause → ADR review → Verify no reorder |
| Modification to `FSyncTransformState` (ParentGuid, bHasParent, bPendingSceneGraphWrite) | Object layout FROZEN | Pause → ADR review → Use separate TMap |
| Modification to `ResolvePendingAttachments` or `AttachToParent` | Runtime hierarchy FROZEN | Pause → ADR review → Use direct AActor API |
| Modification to 24-byte packet header | Header FROZEN | Pause → ADR review → New packet type only |
| Addition of cross-thread hierarchy state | Thread safety FROZEN | Pause → ADR review → Game-thread only |
| Generalized semantic event system | No generic dispatcher | Pause → ADR review → Keep isolated branches |
| Adding delete/lifecycle semantics | Deferred until hierarchy stabilized | Pause → Defer to post-hierarchy scope lock |
| Modifying `InterpolateTransforms` | Transform pipeline FROZEN | Pause → ADR review → Semantic lane does not interpolate |
| Implicit detach-to-root on cycle | Explicitly forbidden (§2.7) | Pause → Review cycle policy |
| Auto-repair of orphan after timeout | Explictly forbidden (§2.6) | Pause → Review orphan policy |
| Cross-frame suppression persistence | Forbidden pattern (§3.5 of conventions) | Pause → Fix to RAII-scoped |

---

## 9. Done Criteria

The hierarchy vertical slice is complete when all of the following are
verified:

### Feature Completion

| # | Criterion | Verification |
|---|-----------|-------------|
| 1 | `PT_Hierarchy = 0x0D` defined in `SyncTypes.h` and `network.py` | Code review |
| 2 | `FHierarchySequenceTracker` defined (bounded 2048, stale/duplicate rejection) | Code review |
| 3 | Blender hierarchy detection in `sync.py` (`_last_parent_guid` diff per object) | Code review |
| 4 | Hierarchy serialization in `network.py` (`serialize_hierarchy()`) | Code review |
| 5 | UE `HandleHierarchy()` with provenance scope, suppression scope, sequence validation | Code review |
| 6 | `PT_Hierarchy` dispatch case in `ProcessBinaryPacket` (isolated, before main transform loop) | Code review |
| 7 | Cycle detection: self-parent, direct cycle, chain cycle (depth-limited parent walk) | Code review + test |
| 8 | `FScopedHierarchySuppression` RAII guard | Code review |
| 9 | `FLiveSyncStats` counters: `HierarchyProcessed`, `HierarchyStaleRejections`, `HierarchyReplayApplied`, `HierarchyReplaySkipped`, `HierarchyOrphans`, `HierarchyCycles` | Code review |
| 10 | `TRACE_CPUPROFILER_EVENT_SCOPE` on `HandleHierarchy` + parse block | Code review |
| 11 | Tracker cleared on `StopNetworkThread()` + `ConsoleReset()` + Blender disconnect | Code review |
| 12 | `kValidTypes[]` updated to include `0x0D` | Code review |
| 13 | FNV checksum updated to include `0x0D` | Code review |
| 14 | Malformed packet handling (truncated payload, oversized batch, invalid GUID) | Code review + test |
| 15 | No frozen-zone modifications | Git diff review |
| 16 | Blender-side sequence tracker + disconnect cleanup | Code review |
| 17 | `PendingHierarchyAttachments` array (separate from runtime `PendingAttachments`) | Code review |
| 18 | Orphan deferred retry with bounded window (5s / 60 frames) + timeout eviction | Code review + test |
| 19 | Detach-to-root (all-zero parent GUID) applies immediately, no deferral | Code review + test |
| 20 | Snapshot replay ordering: Blender emits parents before children | Code review (serialization) |
| 21 | Documentation updated (`current-state.md`, `AGENTS.md`, `24-phase6D-hierarchy-scope-lock.md`, `25-phase6D-vertical-slice-hierarchy.md`) | File review |

### Stability & Safety

| # | Criterion | Verification |
|---|-----------|-------------|
| 22 | Single parent-child attachment via Blender re-parent → UE AttachToActor | Integration test |
| 23 | Multi-level hierarchy (A→B→C) correctly attached on snapshot replay | Integration test |
| 24 | Reparent (move child from A to B) → detach from A, attach to B | Integration test |
| 25 | Detach to root → DetachFromActor(KeepWorldTransform) | Integration test |
| 26 | Self-parent cycle → rejected with log + cycle counter | Test |
| 27 | Direct cycle (A↔B) → rejected with log + cycle counter | Test |
| 28 | Chain cycle (A→B→C→A) → rejected with log + cycle counter | Test |
| 29 | Orphan timeout → child remains root, [ORPHAN] warning logged | Test |
| 30 | Parent arrives within retry window → attachment succeeds | Test |
| 31 | Snapshot with parent-before-child ordering → all attachments correct | Integration test |
| 32 | Snapshot with child-before-parent ordering → deferred retry resolves within window | Integration test |
| 33 | 50-object deep chain stress test → all levels attached, frame time acceptable | Stress test |
| 34 | Reconnect → hierarchy re-established from snapshot (no stale attachment state) | Integration test |
| 35 | Storm: 100 simultaneous re-parent events → all processed without packet loss | Stress test |
| 36 | Mixed hierarchy + transform traffic → both lanes correct, no interference | Integration test |
| 37 | Duplicate hierarchy packet with same seq → rejected (stale) | Test |
| 38 | Hierarchy packet for non-tracked GUID → rejected with warning | Test |
| 39 | Malformed hierarchy packet (truncated) → rejected with MalformedPackets++ | Fuzz test |
| 40 | Phase 5 tests still pass | Run `python3 tests/run_phase5_all.py` |
| 41 | Rename + visibility tests still pass | Run rename + visibility suites |
| 42 | Runtime audit passes | Run `python3 tests/phase6b_runtime_audit.py` |

---

## 10. What Hierarchy Phase 6D Is NOT

```
Phase 6D IS:                        Phase 6D IS NOT:
┌──────────────────────────┐       ┌──────────────────────────┐
│ Replicate attachment     │       │ Transform ownership      │
│ intent only              │       │ • No world/local eval    │
│                          │       │ • No interpolation       │
├──────────────────────────┤       │ • No scene graph control │
│ Blender → UE direction   │       │                          │
│ (matching rename + vis)  │       ├──────────────────────────┤
│                          │       │ Lifecycle/Delete         │
├──────────────────────────┤       │ • No tombstone           │
│ Dependency-sensitive     │       │ • No orphan cleanup      │
│ with deferred retry      │       │ • No destruction replay  │
│                          │       │                          │
├──────────────────────────┤       ├──────────────────────────┤
│ Cycle detection +        │       │ UE → Blender hierarchy   │
│ rejection                │       │ (deferred)               │
│                          │       │                          │
├──────────────────────────┤       ├──────────────────────────┤
│ Snapshot ordering        │       │ Collection hierarchy     │
│ guarantee (parents       │       │ • No folder mapping      │
│ before children)         │       │ • No collection nesting  │
│                          │       │                          │
├──────────────────────────┤       ├──────────────────────────┤
│ Pattern-matching:        │       │ Generalized framework    │
│ provenance → suppression │       │ • No shared dispatcher   │
│ → replay                 │       │ • No base handler class  │
│ → observability          │       │                          │
│                          │       ├──────────────────────────┤
├──────────────────────────┤       │ Bidirectional re-parent  │
│ Pure semantic lane       │       │ • No UE→Blender socket   │
│ (PT_Hierarchy = 0x0D)   │       │ • No last-writer-wins    │
│                          │       │                          │
└──────────────────────────┘       └──────────────────────────┘
```

---

## 11. Architecture Diagram

```
Blender (Main Thread)                         UE (Game Thread)
┌─────────────────────────────┐               ┌──────────────────────────────────┐
│ Scene scan iteration        │               │ ProcessQueuedPackets (Tick)      │
│                             │               │                                  │
│ 1. Detect parent change:    │               │ 1. Case PT_Hierarchy:             │
│    _last_parent_guid diff   │               │    Parse batch: child+GUID+seq+ts │
│                             │               │    For each packet:               │
│ 2. Serialize PT_Hierarchy:  │               │      Provenance scope             │
│    child GUID (16)          │               │      Sequence check (<= reject)   │
│    parent GUID (16)         │               │      Suppression scope            │
│    seq (4)                  │  ──── TCP ──> │      Cycle detection              │
│    ts (8)                   │               │      Parent exists?               │
│                             │               │        YES → ParentGuid all-zero? │
│ 3. Snapshot ordering:        │               │          YES → DetachFromActor    │
│    Emit parents BEFORE       │               │          NO  → AttachToActor      │
│    children                  │               │        NO  → Deferred retry       │
│                             │               │        NO (cycle) → Reject + log  │
└─────────────────────────────┘               │                                  │
                                               │ 2. ResolveHierarchyAttachments:   │
Blender (Daemon Thread)                        │    Retry deferred (60f/5s max)    │
┌─────────────────────────────┐               │    Timeout → [ORPHAN] log + evict │
│ socket.sendall()            │               └──────────────────────────────────┘
└─────────────────────────────┘
```

---

## 12. Revision History

| Date | Version | Changes |
|------|---------|---------|
| 2026-05-26 | 1.0 | Initial scope lock — Phase 6D planning. Defines hierarchy semantic lane boundaries, orphan/cycle policy, frozen runtime separation, complexity classification, and lifecycle/delete deferral rationale. |
| 2026-05-26 | 1.1 | Terminology consolidation: updated status from PLANNING — NOT IMPLEMENTED to IN PROGRESS — STAGE 7. |
| 2026-05-26 | 1.2 | Hierarchy STABILIZED: Stages 8-13 complete (orphan lifecycle, cycle detection, Blender detection, serialization, snapshot ordering, runtime validation). 97/97 standalone tests pass, 49/49 Phase 6B audit pass. |

---

## Reference Documents

| Document | Relationship |
|----------|-------------|
| `10-hierarchy-implementation-notes.md` | Phase 5B hierarchy contract — parent-owns-world, child-owns-local, interpolation rules. NOT modified by Phase 6D. |
| `18-phase6-scope-lock.md` | Phase 6 master scope boundaries. Hierarchy referenced as §3.6 (editor-side) and §3.4 (original proposal). |
| `20-phase6-visibility-scope-lock.md` | Template pattern for semantic lane scope locks. |
| `22-semantic-event-architecture-conventions.md` | Canonical conventions — all §2 mandatory requirements apply to hierarchy. |
| `13-phase6-design-constraints.md` | Unresolved authority model questions for hierarchy, collections, delete. |
| `12-core-runtime-invariants.md` | Frozen runtime invariants — packet lifecycle, thread/queue ownership, Tick ordering. |
| `16-known-safe-modification-zones.md` | SAFE/CAUTION/HIGH-RISK/FROZEN modification zones. |
