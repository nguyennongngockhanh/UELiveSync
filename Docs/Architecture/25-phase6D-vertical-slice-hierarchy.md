# Phase 6D — Hierarchy Replication: Vertical Slice Design

> **Created**: 2026-05-26
> **Status**: IMPLEMENTED — STABILIZED (Stage 13, 97/97 standalone tests pass)
> **Scope Lock**: `24-phase6D-hierarchy-scope-lock.md`
> **Predecessors**: Rename (`0x0C` · STABILIZED) · Visibility (`0x0B` · STABILIZED)
> **Live Validation**: `28-phase6D-live-runtime-validation.md`
>
> This document defines the **complete vertical slice design** for the third
> Phase 6 semantic-event lane: hierarchy (parent-child) replication. It is
> the **first dependency-sensitive semantic lane** — introducing inter-object
> ordering, graph consistency, orphan states, replay dependency chains, and
> cycle detection.
>
> **This is a design document, NOT an implementation specification.**
> No runtime code has been modified. No parser branches have been added.

---

## Table of Contents

1. [Packet Definition](#1-packet-definition)
2. [Replay Dependency Chain Analysis](#2-replay-dependency-chain-analysis)
3. [Deferred Attachment Semantics](#3-deferred-attachment-semantics)
4. [Snapshot Ordering Contract](#4-snapshot-ordering-contract)
5. [Graph Consistency Invariants](#5-graph-consistency-invariants)
6. [Orphan Semantics](#6-orphan-semantics)
7. [Cycle Prevention Semantics](#7-cycle-prevention-semantics)
8. [Runtime Hierarchy Interaction](#8-runtime-hierarchy-interaction)
9. [Observability Requirements](#9-observability-requirements)
10. [Failure-Safety Rules](#10-failure-safety-rules)
11. [Complexity Assessment](#11-complexity-assessment)
12. [Lifecycle/Delete Dependency Warning](#12-lifecycledelete-dependency-warning)
13. [Final Deliverable](#13-final-deliverable)

---

## 1. Packet Definition

### 1.1 Packet Type

| Field | Value |
|-------|-------|
| Constant | `PT_Hierarchy = 0x0D` |
| Type space | Next available byte (§8.2 of conventions) |
| Status | Reserved — NOT implemented |
| Direction | Blender → UE (Phase 6D) |
| Semantic classification | Discrete semantic mutation (NOT state stream) |

### 1.2 Proposed Wire Format

**Fixed-length payload**: 44 bytes per object.

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0 | 16 | `ChildGuid` | FGuid of the child object (binary, 4×uint32 LE) |
| 16 | 16 | `ParentGuid` | FGuid of the intended parent (all-zero = detach to root) |
| 32 | 4 | `Sequence` | Monotonic per-GUID sequence number (uint32 LE) |
| 36 | 8 | `Timestamp` | UE-style `FPlatformTime::Seconds()` double (LE) |
| 44 | — | End | Fixed 44 bytes per object |

**Total per-object**: 44 bytes fixed.

### 1.3 Fixed vs Variable Tradeoff

| Criterion | Fixed (44 bytes) | Variable |
|-----------|------------------|----------|
| **Parse complexity** | Trivial — known size per object | Higher — length prefix per object |
| **Boundary checks** | Single `remaining >= 44` per object | Per-field checks (length fields) |
| **Batching density** | Fixed stride | Variable stride, gaps possible |
| **Future extensibility** | Requires new PT_* or version bump | Per-object flags possible |

**Decision**: Fixed-length 44 bytes. Rationale:
- Matches the simplicity of visibility (29 bytes fixed) and transform (81 bytes V4+)
- No variable-length fields (no strings) — parent GUID is always 16 bytes
- Simpler boundary checks reduce malformed-packet surface area
- Future extensibility via version bump if needed

### 1.4 Attach Semantics

| Condition | Behavior |
|-----------|----------|
| `ParentGuid` non-zero, valid child | Attach child to parent actor via `AttachToActor(KeepWorldTransform)` |
| `ParentGuid` non-zero, parent not yet in `ActorCache` | Enter deferred retry (see §3) |
| `ParentGuid` non-zero, both exist, cycle detected | Reject immediately (see §7) |
| `ParentGuid` non-zero, child not in `ActorCache` | Reject — unknown GUID. Log warning. |

### 1.5 Detach Semantics

| Condition | Behavior |
|-----------|----------|
| `ParentGuid` all-zero (null), child currently attached | `DetachFromActor(KeepWorldTransform)`. **Always applied immediately** — no deferral. |
| `ParentGuid` all-zero, child already root | No-op. Log at Verbose. |
| `ParentGuid` all-zero, child not in `ActorCache` | Reject — unknown GUID. Log warning. |

**Detach has zero dependencies** — it always applies immediately. This is
a critical property: detach cannot orphan, cannot cycle, cannot fail
(except unknown GUID).

### 1.6 Root/Null-Parent Semantics

A **root actor** is one with no parent. In the hierarchy semantic lane:

- `ParentGuid = {0,0,0,0}` (all-zero) is the canonical representation of "no parent"
- This is **not** the same as "parent GUID not set" (which is a malformed packet)
- A root actor's transform is evaluated as world-space by the existing runtime
- Changing from root to child: `ParentGuid` transitions from all-zero to a valid GUID
- Changing from child to root: `ParentGuid` transitions from a valid GUID to all-zero

**Wire invariant**: There is exactly one representation of "no parent" —
all-zero 16 bytes. Any other bit pattern in `ParentGuid` is a valid
parent reference (even if the parent object doesn't exist yet).

### 1.7 Replay Semantics

| Situation | Sequence behavior |
|-----------|-------------------|
| Normal live packet | `IncomingSeq > LastSeq` → apply |
| Normal live duplicate | `IncomingSeq == LastSeq` → stale reject |
| Snapshot replay packet | `IncomingSeq > LastSeq` → apply (tagged `EChangeOrigin::Replay`) |
| Snapshot replay stale | `IncomingSeq <= LastSeq` → `HierarchyReplaySkipped++` |
| Out-of-order replay | Sequence is per-GUID, not global — each child independently tracked |

### 1.8 Provenance

The provenance is set per-handler-invocation, not per-packet-on-wire:

| Context | `EChangeOrigin` |
|---------|-----------------|
| Normal PT_Hierarchy packet (`bInSnapshotBuild == false`) | `RemoteReplicated` |
| Snapshot replay PT_Hierarchy (`bInSnapshotBuild == true`) | `Replay` |
| Future UE→Blender direction | `LocalUser` |

No provenance field on the wire. Provenance is determined by context and
applied via `FScopedChangeOrigin` RAII guard in the handler.

---

## 2. Replay Dependency Chain Analysis

### 2.1 The Fundamental Problem

Unlike rename and visibility — where each packet is an independent
single-object mutation — hierarchy packets describe a **relationship
between two objects**. For the relationship to be established correctly,
**both objects must exist** in the UE actor graph.

During replay (snapshot rebuild on reconnect), packet arrival order
determines whether attachments succeed on first attempt or require
deferred retry. The Blender-side snapshot builder controls this order,
and the UE-side system must handle both correct and incorrect ordering
deterministically.

### 2.2 Concrete Replay Scenarios: A→B→C Hierarchy

Consider a three-level hierarchy:

```
A (root)
  └── B (child of A)
        └── C (child of B)
```

During snapshot replay, Blender emits PT_Hierarchy packets for
each non-root object. The **contract** is that parents arrive before
children, but we must analyze both compliance and violation.

#### Scenario 1: Correct Order (Compliance)

```
Replay order: B's hierarchy (child=A), then C's hierarchy (child=B)
  ┌─ Process B: parent=A. A exists in ActorCache. Attach B→A. ✓
  └─ Process C: parent=B. B exists in ActorCache (just attached). Attach C→B. ✓
```

**Result**: All attachments succeed immediately. No deferred retries.
This is the ideal replay path.

#### Scenario 2: Reverse Order (Ordering Violation)

```
Replay order: C's hierarchy (child=B), then B's hierarchy (child=A)
  ┌─ Process C: parent=B. B does NOT exist in ActorCache yet.
  │   → Enqueue deferred: C waiting for B.
  │   → Return. No attachment applied.
  │
  └─ Process B: parent=A. A exists in ActorCache. Attach B→A. ✓
      → Deferred retry pass: C's parent B now exists.
      → Attach C→B. ✓
```

**Result**: C's attachment deferred, resolved within the fast retry window
(≤10 frames). This is the expected recovery path for ordering violations.
**No data loss, no desync.**

#### Scenario 3: Deep Chain, Reverse Order

```
Hierarchy: A→B→C→D→E
Replay order: E, D, C, B (all parents missing on first pass)
  ┌─ E: parent=D missing. Deferred.
  ├─ D: parent=C missing. Deferred.
  ├─ C: parent=B missing. Deferred.
  ├─ B: parent=A exists. Attach B→A. ✓
  │
  └─ Deferred retry (frame 1):
        ├─ E: parent=D still missing. Re-deferred.
        ├─ D: parent=C still missing. Re-deferred.
        └─ C: parent=B exists now. Attach C→B. ✓
      Deferred retry (frame 2):
        ├─ E: parent=D still missing. Re-deferred.
        └─ D: parent=C exists now. Attach D→C. ✓
      Deferred retry (frame 3):
        └─ E: parent=D exists now. Attach E→D. ✓
```

**Result**: All attachments resolve within 3 retry frames. The chain
resolves from the root downward as each level's parent becomes available.
**Deterministic, convergent, no state explosion.**

#### Scenario 4: Orphan — Parent Never Arrives

```
Replay: B's hierarchy (child=A). A does NOT exist in ActorCache.
        (A was deleted before snapshot, or A's CREATE was lost.)

  ┌─ Frame 1-10 (fast retry): A missing. Deferred every frame.
  ├─ Frame 15 (slow retry): A missing. Deferred.
  ├─ Frame 20 (slow retry): A missing. Deferred.
  ├─ Frame 25 (slow retry): A missing. Deferred.
  ├─ Frame 30 (slow retry): A missing. Deferred.
  ├─ Frame 35 (slow retry): A missing. Deferred.
  ├─ Frame 40 (slow retry): A missing. Deferred.
  ├─ Frame 45 (slow retry): A missing. Deferred.
  ├─ Frame 50 (slow retry): A missing. Deferred.
  ├─ Frame 55 (slow retry): A missing. Deferred.
  └─ Frame 60 → Timeout reached. [ORPHAN] TIMEOUT. Evict. B remains root.
```

**Result**: B exists as a root actor (world-space transform). Blender
thinks B is attached to A. This is a **persistent mismatch** until the
user corrects the hierarchy in Blender (triggering a new PT_Hierarchy
packet) or the parent actor is added to the scene and a re-sync occurs.

#### Scenario 5: Stale Hierarchy Replay

```
Pre-disconnect state: B is child of A (seq=5 for B).
Disconnect. In Blender, B is re-parented to C (seq=6 for B).
Reconnect. Snapshot emits B's hierarchy with seq=6, parent=C.

  ┌─ Process B: incoming seq=6 > last seq=5. Accept.
  │   parent=C. C exists. Attach B→C. ✓
  └─ No stale rejection — seq correctly advanced.
```

**Stale scenario** (rare, but possible if tracker wasn't cleared):

```
Pre-disconnect state: B is child of A (seq=5).
Reconnect. Snapshot emits B's hierarchy with seq=5 (same as pre-disconnect).
Tracker was NOT cleared on disconnect (BUG).

  ┌─ Process B: incoming seq=5 <= last seq=5. Stale reject.
  │   HierarchyStaleRejections++. B remains attached to A? Unclear.
  └─ Determined result: B does NOT get re-attached. Behavior depends on
      whether tracker was correctly cleared.
```

**Correction**: The tracker IS cleared on `StopNetworkThread()` and
`ConsoleReset()`, so this scenario should never occur in correct code.
But it illustrates why tracker cleanup is critical.

#### Scenario 6: Duplicate Hierarchy Replay (Same Seq)

```
Reconnect. Snapshot emits B's hierarchy twice (duplicate packets).

  ┌─ Process first B: seq=5 > last=0. Accept. Attach B→A. tracker[B]=5.
  ├─ Process second B: seq=5 <= last=5. Stale reject.
  │   HierarchyReplaySkipped++. (bInSnapshotBuild == true)
  └─ No double-attachment. Correct.
```

**Result**: Duplicate detection via sequence number prevents
double-application. The `<=` inequality catches both stale and
identical replayed events.

#### Scenario 7: Replay Attach Storm — 300 Concurrent Reparents

```
Reconnect. Snapshot emits 300 hierarchy events in a single packet batch.

  ├─ 200 have valid parents present in ActorCache. Apply immediately. ✓
  ├─ 80 have parents arriving later in the same batch (order violation).
  │   Deferred. Resolved within fast retry window. ✓
  └─ 20 have parents that never arrive. Deferred → timeout → orphaned. ✓
```

**Result**: No packet loss due to batch processing. Each event is
independently validated. Orphans are handled within timeout.
The batch boundary does not cause cascade failures — an orphan in
position 50 does not prevent events 51-300 from being processed.

#### Scenario 8: Orphan Replay Chain

```
Hierarchy: A→B→C (3 levels).
Replay order: A's hierarchy, then C's hierarchy (B missing entirely).

  ┌─ Process A: parent=(null). Root. No attachment. ✓
  ├─ Process C: parent=B. B does not exist. Deferred.
  │
  ├─ Retry: parent=B still missing. Deferred.
  ├─ Retry: parent=B still missing. Deferred.
  ├─ ... (60 frames / 5 seconds)
  └─ Timeout → [ORPHAN] C is now root. B never existed.
```

**Result**: C is orphaned at the timeout boundary. C remains root with
world-space transform. This is a **corner case with persistent mismatch**
— it requires user intervention (re-sync) or manual hierarchy fix in
Blender. This is acceptable because:
- B's absence is a precondition violation (B should have been created)
- The system degrades gracefully (C is root, not destroyed)
- Observability (`[ORPHAN]` logs) surfaces the issue

### 2.3 Replay Risk Summary Table

| Scenario | Risk | Determinism | Recovery | Data Loss |
|----------|------|-------------|----------|-----------|
| Correct order | None | Deterministic | Immediate attach | None |
| Reverse order (2 levels) | Low | Deterministic | Fast retry (≤10 frames) | None |
| Reverse order (deep chain) | Low | Deterministic | Chain resolution (N frames) | None |
| Orphan (parent missing) | Medium | Deterministic | Timeout → root | Hierarchy mismatch |
| Stale seq (tracker not cleared) | High | Non-deterministic if bug | Fix tracker cleanup | Hierarchy mismatch |
| Duplicate seq | Low | Deterministic | ReplaySkipped++ | None |
| Attach storm | Medium | Deterministic | Batch processing | None (bounded queue) |
| Orphan chain (deep) | Medium | Deterministic | Cascade orphan → all roots | Multiple hierarchy mismatches |

### 2.4 Replay Dependency Invariants

| Invariant | Type | Rationale |
|-----------|------|-----------|
| Parent-child dependency is **not** a hard ordering requirement | Soft | Deferred retry handles out-of-order deterministically |
| Sequence tracker clear on disconnect is **hard** requirement | Hard | Without it, stale rejection produces incorrect behavior |
| Per-GUID monotonic sequencing is sufficient | Design choice | No global ordering needed — each child tracked independently |
| Replay batches are processed atomically within a single Tick? | Design choice | No — each packet independently; partial batch application is safe |
| Orphan timeout is **deterministic** (60 frames @ 60 fps = 1s) | Hard | Must be frame-count-based, not wall-clock drift |

---

## 3. Deferred Attachment Semantics

### 3.1 Ownership

The hierarchy semantic lane maintains its **own** deferred attachment queue,
separate from the frozen runtime `PendingAttachments` array.

| Queue | Owner | Frozen? | Purpose |
|-------|-------|---------|---------|
| `PendingAttachments` (`UELiveSyncSubsystem.h:399-414`) | Phase 5 runtime | FROZEN | Runtime deferred attachment (transform-driven) |
| `PendingHierarchyAttachments` (new, separate) | Phase 6D semantic lane | NEW | Semantic deferred attachment (hierarchy-event-driven) |

**Why separate?**
- The runtime queue is FROZEN — cannot modify its behavior or lifecycle
- The semantic queue has different retry semantics (dedicated counters, `[ORPHAN]` logging, timeout eviction)
- A semantic hierarchy event may arrive for an object whose parent IS present in `ActorCache` (no deferral needed), while the runtime queue may have the same object deferred for transform-order reasons
- Separation prevents cross-system contamination

### 3.2 Retry Cadence

The deferred retry cadence matches the existing runtime pattern:

| Phase | Frame Range | Retry Frequency |
|-------|-------------|-----------------|
| Fast retry | Frames 1-10 | Every frame |
| Slow retry | Frames 11-60 | Every 5th frame (11, 16, 21, 26, 31, 36, 41, 46, 51, 56) |
| Timeout | Frame 61+ | Evicted |

**Total maximum retries**: 10 + 10 = 20 retry attempts.

### 3.3 Retry Timeout

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Max frames | 60 | Matches existing `ResolvePendingAttachments` behavior |
| Wall-clock equivalent | ~1 second at 60 fps; ~2 seconds at 30 fps | Acceptable — hierarchy is not frame-rate-critical |
| Wall-clock hard cap | 5 seconds | Matching existing `ResolvePendingAttachments` timeout |

**Timeout is hit when**: Neither fast retry nor slow retry resolved the
parent dependency within the frame/wall-clock budget.

### 3.4 Retry Eviction

On timeout, the deferred entry is **evicted** with the following behavior:

1. Log: `[HIERARCHY][ORPHAN] TIMEOUT: child=%s parent=%s — evicting after %d retries`
2. Increment: `HierarchyOrphans++`
3. Evict from `PendingHierarchyAttachments`
4. Child **remains as root** with its current world-space transform
5. No further retry for this (child, parent) pair

**Explicitly no**: retroactive attachment, secondary recovery path,
permanent pending state, or zombie entries.

### 3.5 Reconnect Retry Behavior

On reconnect:
- `PendingHierarchyAttachments` is **cleared** (matching `PendingAttachments` behavior)
- The fresh snapshot replay re-establishes hierarchy intent
- Orphans from the previous session are replaced by fresh retry attempts

### 3.6 Attachment Lifecycle State Machine

```
┌──────────────────────────────────────────────────────────────────┐
│                    ATTACHMENT LIFECYCLE                           │
│                                                                  │
│  PT_Hierarchy arrives                                             │
│       │                                                          │
│       ├── Parent present?                                         │
│       │     YES → Cycle check → PASS → Apply Immediately ──────┐ │
│       │                          → FAIL → Reject (no deferral)  │ │
│       │                          → CYCLE → Reject + log         │ │
│       │     NO  → Enter DEFERRED state                          │ │
│       │                                                          │ │
│       ▼                                                          │ │
│  ┌──────────┐                                                    │ │
│  │ DEFERRED │── Fast retry frames 1-10 (every frame)             │ │
│  └──────────┘── Slow retry frames 11-60 (every 5th frame)        │ │
│       │                                                          │ │
│       ├── Parent resolved within window → Apply ─────────────────┤ │
│       │                                                          │ │
│       └── Timeout (60 frames / 5 sec) ──┐                        │ │
│                                         ▼                        │ │
│                                ┌──────────┐                      │ │
│                                │ ORPHANED │── Log, evict, child  │ │
│                                └──────────┘  remains root        │ │
│                                                                  │ │
│  ┌─────────────────────────────────────────────────────────┐     │ │
│  │ REJECTED (cycle, unknown GUID, malformed)               │     │ │
│  │ No deferral, no retry, permanent rejection for packet   │     │ │
│  └─────────────────────────────────────────────────────────┘     │ │
│                                                                  │ │
│  ┌─────────────────────────────────────────────────────────┐     │ │
│  │ APPLIED (attached or detached)                          │     │ │
│  │ Attachment persists until next PT_Hierarchy or delete   │     │ │
│  └─────────────────────────────────────────────────────────┘     │ │
└──────────────────────────────────────────────────────────────────┘
```

The deferred → orphaned → rejected path only applies to missing-parent
deferrals. Cycle rejections and malformed-packet rejections are
**immediate** — they never enter the DEFERRED state.

### 3.7 Deferred Queue Ownership

| Property | Specification |
|----------|---------------|
| Data structure | `TArray<FPendingHierarchyAttachment>` |
| Max size | 2048 (matching `PendingAssetQueue` bound) |
| Overflow policy | Reject oldest entry on overflow, log warning |
| Add operation | `Add()` — appends at end |
| Remove operation | `RemoveAtSwap()` or `RemoveAll()` — rebuilt via `MoveTemp` each Tick |
| Iteration | `ResolveHierarchyAttachments()` called once per Tick after `ProcessQueuedPackets` |
| Clear trigger | `HandleEndSnapshot`, `StopNetworkThread`, `ConsoleReset` |

```
struct FPendingHierarchyAttachment
{
    FGuid ChildGuid;        // The child to attach
    FGuid ParentGuid;       // The intended parent
    int32 RetryFrames;      // Number of retries so far
    double CreatedTime;     // FPlatformTime::Seconds() when deferred
    uint32 Sequence;        // Original sequence number (for logging)
};
```

---

## 4. Snapshot Ordering Contract

### 4.1 The Contract

```
The Blender snapshot builder MUST emit PT_Hierarchy packets for
parent objects BEFORE their children.
```

This is a **protocol contract**, not an implementation detail.

### 4.2 Why This Contract Exists

The ordering contract exists because:

1. **Deterministic first-pass resolution**: When parents arrive before
   children, the first pass of replay can attach immediately without
   deferred retry. This is the fast path.

2. **Observability**: Ordering violations are detectable — if a child's
   parent is missing on first pass, the deferred retry count is a
   direct measure of ordering compliance.

3. **Retry is recovery, not normal operation**: The deferred retry
   system exists for edge cases (ordering violations, orphan parents,
   race conditions). It should not be the common case. The contract
   ensures the common case is the fast path.

4. **Future enforcement**: A future diagnostic could warn when
   `HierarchyOrphans` exceeds a threshold, indicating a Blender-side
   ordering bug.

### 4.3 Blender-Side Implementation (Design Only)

The snapshot builder in `sync.py` must:

```
For each tracked object (ordered by hierarchy depth ascending):
  if object has a parent:
    emit PT_Hierarchy(child_guid, parent_guid, seq, ts)
```

This requires the snapshot iteration to visit root objects first,
then their children, then grandchildren, etc. — a breadth-first or
depth-first pre-order traversal.

**Recommended**: During snapshot construction, collect all objects,
sort by hierarchy depth (root = 0, child = 1, grandchild = 2, etc.),
then emit hierarchy events for non-root objects in ascending depth order.

```
def _build_hierarchy_snapshot(objects):
    depth_map = _compute_hierarchy_depth(objects)
    sorted_objects = sorted(objects, key=lambda o: depth_map[o.guid])
    for obj in sorted_objects:
        if obj.parent:
            emit_pt_hierarchy(obj.guid, obj.parent.guid, ...)
```

### 4.4 Runtime Behavior When Contract Is Violated

The UE side **must** handle ordering violations deterministically.
This is not optional — the system must survive incorrect order.

| Violation | Behavior | Degradation |
|-----------|----------|-------------|
| Child arrives before parent | Deferred retry (see §3) | Transient — resolved within fast window |
| Multiple levels reversed | Chain resolution (see §2.2 Scenario 3) | O(N) retry frames for N-level reversal |
| Parent never arrives (orphan) | Timeout → orphaned (see §6) | Persistent mismatch — user intervention |
| Parent arrives but after timeout | Child remains root permanently | Persistent mismatch — re-sync required |
| Batch split across Ticks | Normal deferred processing | None — intra-batch ordering is preserved |

### 4.5 Contract Enforcement

| Level | Enforcement | Action |
|-------|-------------|--------|
| Development | Log warning when deferred count spikes | `[HIERARCHY] Ordering note: %d deferred after snapshot replay (expected 0)` |
| Test | Snapshot ordering test with both compliant and non-compliant order | Verify deterministic behavior in both cases |
| Documentation | This contract is in both the design doc and the scope lock | Developers must understand it |
| Production | No structural enforcement (cannot abort on ordering violation) | Deferred retry handles it gracefully |

---

## 5. Graph Consistency Invariants

### 5.1 Invariant Table

| # | Invariant | Category | Violation Behavior | Rationale |
|---|-----------|----------|-------------------|-----------|
| I1 | No self-parenting (`ChildGuid != ParentGuid`) | **Hard rejection** | Immediate reject. Log `[CYCLE]`. No deferral. | Self-parent is meaningless — no graph can have a node as its own parent. |
| I2 | No direct cycles (A→B→A) | **Hard rejection** | Immediate reject. Log `[CYCLE]`. No deferral. | A→B→A is a 2-cycle. Attachment would violate acyclic constraint. |
| I3 | No indirect chain cycles (A→B→C→A) | **Hard rejection** | Immediate reject. Log `[CYCLE]`. Depth-limited walk (max 256). | N-cycles detected via ancestor walk. 256-level cap prevents infinite loop on corrupted graph. |
| I4 | Child GUID must be valid and non-zero | **Hard rejection** | Immediate reject. Log warning. `HierarchyStaleRejections++`. | Zero GUID is sentinel — never a valid child. |
| I5 | Child must exist in `ActorCache` | **Hard rejection** | Immediate reject. Log `[HIERARCHY] Rejected: unknown child=%s`. | Cannot attach a non-existent actor. |
| I6 | Parent GUID may be all-zero (detach) | **Always allowed** | Immediate detach or no-op. | Detach-to-root has zero dependencies. Always safe. |
| I7 | Parent may be missing (non-zero GUID, not in `ActorCache`) | **Deferred** | Enter deferred retry (see §3). Log `[ORPHAN]`. | Parent may arrive later. Temporal ordering violation is recoverable. |
| I8 | No implicit graph healing | **Warning-only** | The system does NOT auto-repair cycles, fill missing parents, or rewrite parent chains. Log if detected. | Graph healing requires semantic understanding of the user's intent. The system only replicates intent; it does not infer it. |
| I9 | All-zero [ParentGuid] means root | **Semantic invariant** | If child currently attached → detach. If already root → no-op. | All-zero is the canonical "no parent" representation. |
| I10 | Root does not need to be explicit | **Implicit invariant** | If no PT_Hierarchy has been received for a GUID, the object is implicitly root. | Absence of hierarchy intent = root. |
| I11 | Deterministic replay | **Hard guarantee** | Given the same packet sequence, replay produces the same attachment state. | Sequence tracker + deferred retry + timeout are deterministic by construction. |
| I12 | Reconnect clears all transient state | **Hard guarantee** | `PendingHierarchyAttachments` cleared. `FHierarchySequenceTracker` cleared. Fresh snapshot starts clean. | No cross-session state leaks. |
| I13 | Hierarchy event does NOT imply existence | **Deferred invariant** | A hierarchy event referencing a non-existent parent does NOT auto-create the parent. | CREATE/DELETE lifecycle is a separate lane. Hierarchy only establishes relationships between EXISTING objects. |
| I14 | Attachment count is bounded by object count | **Structural guarantee** | Max one parent per child. Total edges ≤ total objects. No edge explosion. | Hierarchy is a forest of trees. Each node has at most one parent. |

### 5.2 Categories Defined

| Category | Description | Examples |
|----------|-------------|---------|
| **Hard rejection** | The packet is rejected immediately. Never deferred. Never retried. | I1, I2, I3, I4, I5 |
| **Deferred** | The packet enters deferred retry. May eventually apply or timeout. | I7 |
| **Always allowed** | Always applies immediately. No conditions. | I6 |
| **Warning-only** | The system detects but does not act. | I8 |
| **Hard guarantee** | An architectural guarantee — must never be violated by correct code. | I11, I12 |
| **Structural guarantee** | A mathematical guarantee derived from the problem domain. | I14 |

### 5.3 Rejection vs Deferral Decision Tree

```
PT_Hierarchy packet arrives
  │
  ├── ChildGuid is zero? → REJECT (I4)
  ├── ChildGuid == ParentGuid? → REJECT (I1)
  ├── Child in ActorCache? ──NO──→ REJECT (I5)
  │
  ├── YES → ParentGuid all-zero? → APPLY immediate detach/no-op (I6)
  │
  ├── Parent in ActorCache? ──NO──→ DEFER (I7) → enter retry loop
  │
  ├── YES → Cycle detection:
  │         ├── Is parent currently a descendant of child? (walk parent chain)
  │         │     YES → REJECT (I2/I3)
  │         │     NO  → APPLY (AttachToActor)
  │         └── (parent chain walk is depth-limited to 256 levels)
  │
  └── All checks passed → APPLY
```

### 5.4 Cycle Detection Algorithm (Design)

```
function WouldCreateCycle(ChildActor, ParentActor) -> bool:
    // Self-parent check
    if ChildActor == ParentActor:
        return true

    // Walk parent chain from ParentActor up to root
    Current = ParentActor
    Depth = 0
    while Current is not None and Depth < MAX_HIERARCHY_DEPTH:
        if Current == ChildActor:
            return true           // Cycle detected: ParentActor is descendant of ChildActor
        Current = GetAttachedParent(Current)
        Depth += 1

    return false                  // No cycle
```

**MAX_HIERARCHY_DEPTH = 256** — prevents infinite loop in case of
corrupted runtime attachment state. If this limit is reached, the
attachment is REJECTED with a `[CYCLE]` log (conservative — assume
cycle at extreme depth).

**Important**: The cycle detection walks the **current** runtime parent
chain, not the intended-intent chain. This is because multiple
hierarchy events may be processed in the same Tick — the cycle check
evaluates against CURRENT state, not pending state.

---

## 6. Orphan Semantics

### 6.1 Orphan Definition

An **orphan** in the hierarchy semantic lane is a (child, parent) pair
where:

1. The child has received a PT_Hierarchy packet with a non-zero ParentGuid
2. The parent actor exists in neither `ActorCache` nor the current Tick's
   incoming packet batch
3. The deferred retry window (60 frames / 5 seconds) has expired
4. The entry has been evicted from `PendingHierarchyAttachments`
5. The child remains as a root actor in UE, even though Blender considers
   it attached

### 6.2 Orphan Creation Conditions

| Condition | Likelihood | Example |
|-----------|------------|---------|
| Parent object was deleted before export | Rare (edge case) | User deletes parent in Blender between sync frames |
| Parent object was filtered (non-MESH) | Low | Parent is a camera/light/armature — excluded from sync |
| Parent GUID changed on reconnect | Low | GUID collision recovery changed parent's GUID |
| Parent CREATE packet lost | Very rare | TCP data corruption (detected by sequence gap?) |
| Packet reordering across Ticks | Medium | More common during snapshot replay with large scenes |
| Race condition: hierarchy before create | Medium | Hierarchy event arrives before CREATE in same batch |

### 6.3 Orphan Retry Lifecycle

```
┌───────────────────────────────────────────────────────────┐
│                   ORPHAN LIFECYCLE                         │
│                                                           │
│  Stage 1: DEFERRED                                         │
│    • Entry added to PendingHierarchyAttachments             │
│    • RetryFrames = 0                                        │
│    • Log: [HIERARCHY][ORPHAN] Deferred: child=%s           │
│      parent=%s attempt=%d/%d                                │
│                                                           │
│       ↓ (retry loop)                                       │
│                                                           │
│  Stage 2: PENDING (frames 1-10, every frame)               │
│    • RetryFrames = 1..10                                    │
│    • Check parent existence every frame                     │
│    • Log: [HIERARCHY][ORPHAN] Deferred: child=%s           │
│      parent=%s attempt=%d/%d (Verbose after frame 2)       │
│                                                           │
│       ↓ (frames 11-60, every 5th frame)                    │
│                                                           │
│  Stage 3: SLOW RETRY (frames 11-60)                        │
│    • RetryFrames = 15, 20, 25, 30, 35, 40, 45, 50, 55, 60 │
│    • Check parent existence on retry frames only            │
│    • Log: [HIERARCHY][ORPHAN] Slow retry: child=%s         │
│      parent=%s attempt=%d/%d                                │
│                                                           │
│       ↓ (timeout)                                          │
│                                                           │
│  Stage 4: ORPHANED (evicted)                                │
│    • Log: [HIERARCHY][ORPHAN] TIMEOUT: child=%s            │
│      parent=%s — evicting after %d retries                  │
│    • HierarchyOrphans++                                     │
│    • Remove from PendingHierarchyAttachments                 │
│    • Child remains root actor in UE                          │
│                                                           │
└───────────────────────────────────────────────────────────┘
```

### 6.4 Orphan Timeout Behavior

| Property | Value | Rationale |
|----------|-------|-----------|
| Timeout type | Frame-count + wall-clock (whichever hits first) | Frame-count ensures determinism; wall-clock prevents infinite stall at low fps |
| Max frames | 60 | Matching existing runtime behavior |
| Max wall-clock | 5 seconds | Safety net for very low frame rates |
| On timeout | Evict, log, leave child as root | Graceful degradation |
| Child's transform | Unchanged — child retains current world-space transform | Transform ownership is NOT part of hierarchy semantic lane |
| Future hierarchy events | Accepted normally for the same child GUID | A new PT_Hierarchy for the same child starts a fresh lifecycle |
| Recovery expectation | User must trigger re-sync (new hierarchy event) | No automatic recovery for orphaned entries |

### 6.5 Reconnect Orphan Recovery

On reconnect, the following sequence occurs:

1. `PendingHierarchyAttachments` is cleared (no stale orphans)
2. `FHierarchySequenceTracker` is cleared (fresh sequence state)
3. Blender snapshot emits PT_Hierarchy for all non-root objects
4. Fresh replay: parents before children (contract) → immediate attachment
5. Any parent that exists in Blender but was missing in the previous
   session will now be present (snapshot includes all objects)
6. Result: orphans from the previous session are replaced by fresh
   attachment attempts. If the parent still doesn't exist (e.g., it was
   deleted and not included in the snapshot), the child will re-enter
   the orphan lifecycle.

**Key property**: Reconnect does NOT revive orphaned entries — it starts
a completely fresh lifecycle. No cross-session orphan state leaks.

### 6.6 Observability Requirements for Orphans

| Log point | Level | Format |
|-----------|-------|--------|
| Deferral | Log | `[HIERARCHY][ORPHAN] Deferred: child=%s parent=%s attempt=%d/%d` |
| Slow retry | Verbose | `[HIERARCHY][ORPHAN] Slow retry: child=%s parent=%s attempt=%d/%d` |
| Timeout | Warning | `[HIERARCHY][ORPHAN] TIMEOUT: child=%s parent=%s — evicting after %d retries` |
| Re-orphan (same child, new attempt after timeout) | Warning | `[HIERARCHY][ORPHAN] New hierarchy for previously-orphaned child=%s parent=%s` |
| Orphan count at EndSnapshot | Log | `[HIERARCHY] EndSnapshot: %d deferred, %d orphaned total this session` |

---

## 7. Cycle Prevention Semantics

### 7.1 Cycle Types and Detection

| Cycle Type | Detection Method | Example | Complexity |
|------------|-----------------|---------|------------|
| **Self-cycle** | Direct comparison: `ChildGuid == ParentGuid` | A←A (A is its own parent) | O(1) |
| **Direct 2-cycle** | After attaching A→B, if B's incoming PT_Hierarchy says B→A | A→B, B→A | O(1) — check if B's intended parent equals A, and A's current parent chain includes B |
| **Indirect N-cycle** | Ancestor walk from ParentActor up to root, checking if any ancestor equals ChildActor | A→B→C, C→A (C tries to become A's parent) | O(depth), max 256 |

### 7.2 Cycle Rejection Policy

| Policy | Behavior | Rationale |
|--------|----------|-----------|
| **Reject immediately** | The packet is dropped. No attachment occurs. | Cycles are graph errors. Applying them would corrupt the scene graph. |
| **No deferral** | Cycle rejection is permanent for the packet. | A cycle will not resolve with time — it is a structural error. |
| **No auto-repair** | Do NOT detach child from current parent "to break the cycle." | Auto-repair would lose the user's original parent assignment. The user must fix the cycle in Blender. |
| **No auto-reparent-to-root** | Do NOT clear the child's parent to root. | Implicit detach-to-root would change the child's transform semantics (world-space vs local-space). |
| **No automatic retry** | The rejected packet is not re-queued. | The cycle will persist on retry. Only a new PT_Hierarchy with corrected parent can succeed. |
| **Existing hierarchy preserved** | The child's current attachment (if any) is left unchanged. | If the child was previously attached to a different parent, that attachment remains. |
| **Batch isolation** | Other packets in the same batch are processed normally. | Cycle rejection does NOT cascade to adjacent packets. |

### 7.3 Explicitly Prohibited Behaviors

```
┌────────────────────────────────────────────────────────────┐
│ PROHIBITED CYCLE BEHAVIORS                                  │
├────────────────────────────────────────────────────────────┤
│ 1. Implicit detach-to-root on cycle detection               │
│    "We detected a cycle, so we'll detach the child to       │
│     root to 'fix' it."                                      │
│    → Forbidden. Child's hierarchy is not our property to    │
│      modify outside the semantic lane.                      │
│                                                             │
│ 2. Implicit graph rewriting                                 │
│    "A→B→A is a cycle. Let's make it A→B only (remove       │
│     the A→B back-edge)."                                    │
│    → Forbidden. The system does not infer user intent.      │
│                                                             │
│ 3. Partial cycle resolution                                 │
│    "A→B→C→A is a cycle. Let's attach A→B and skip C→A."     │
│    → Forbidden. All cycle edges are rejected. Each packet   │
│      is evaluated independently.                            │
│                                                             │
│ 4. Deferred cycle evaluation                                │
│    "Let's defer the cycle check — maybe it resolves."       │
│    → Forbidden. Cycle check is immediate, always.           │
│                                                             │
│ 5. Silent cycle handling                                     │
│    "Let's reject the cycle but not log it."                  │
│    → Forbidden. Every cycle rejection is logged with         │
│      [HIERARCHY][CYCLE] prefix.                              │
│                                                             │
│ 6. Cycle counter suppression                                 │
│    "Let's not increment HierarchyCycles — the user might     │
│     not notice."                                             │
│    → Forbidden. Every cycle detection increments the         │
│      counter, regardless of suppression.                     │
└────────────────────────────────────────────────────────────┘
```

### 7.4 Cycle Logging Format

```
[CYCLE] Self-parent rejected: child=%s
[CYCLE] Direct cycle rejected: child=%s parent=%s (current parent of child=%s)
[CYCLE] Chain cycle rejected: child=%s parent=%s (ancestor walk depth=%d)
[CYCLE] Depth limit exceeded (256): child=%s parent=%s — assuming cycle
```

### 7.5 Cycle Counter Behavior

| Counter | When Incremented |
|---------|-----------------|
| `HierarchyCycles` | Every cycle detection (self, direct, chain, depth-limit). Incremented AFTER the packet is rejected. |

### 7.6 Edge Cases

| Edge Case | Behavior |
|-----------|----------|
| A→B, B→A in the same packet batch | B's packet is processed after A's. When B's packet arrives, A is now B's parent. The cycle check walks A's parent chain → B is found → cycle rejected. **Correct.** |
| A→B, B→A, A→B in same batch (pendulum) | A→B attaches. B→A is rejected (cycle). A→B is duplicate (stale seq or no-op since already attached). **Correct — no oscillation.** |
| A→B, then UE user manually attaches B→A | The semantic lane does NOT detect UE-user-initiated cycles (it is not bidirectional yet). A next PT_Hierarchy for B would detect the cycle. **Acceptable — implicit cycle detection is deferred.** |
| Depth-limit rejection on valid deep hierarchy | If a real hierarchy exceeds 256 levels, the 257th attachment is rejected as a suspected cycle. **This is an acceptable limitation for a realtime sync tool.** |

---

## 8. Runtime Hierarchy Interaction

### 8.1 Critical Distinction

The hierarchy semantic lane and the Phase 5 runtime hierarchy system
operate on the **same scene graph** but through **different entry points**.

```
Phase 5 Runtime (FROZEN):                   Phase 6D Semantic Lane (NEW):
┌─────────────────────────────┐             ┌─────────────────────────────┐
│ UpdateTargetTransform()     │             │ HandleHierarchy()           │
│   → AttachToParent()       │             │   → Cycle detection         │
│     → AttachToActor()       │             │   → Existence check         │
│   → DetachFromParent()      │             │   → AttachToActor()         │
│     → DetachFromActor()     │             │   → DetachFromActor()       │
│                             │             │                             │
│ ResolvePendingAttachments() │             │ ResolveHierarchyAttachments │
│   → Deferred attach logic   │             │   → Separate queue          │
│   → 60-frame timeout        │             │   → 60-frame timeout        │
└─────────────────────────────┘             └─────────────────────────────┘
                            │                                      │
                            └────────── Both call ────────────────┘
                                        │
                                        ▼
                              ┌─────────────────────┐
                              │ AActor::AttachTo    │
                              │ AActor::DetachFrom  │
                              │ (UE Engine API)     │
                              └─────────────────────┘
```

Both paths converge at the same UE engine API, but they are **architecturally
independent**. The semantic lane does NOT call `AttachToParent()` or
`ResolvePendingAttachments()` — it calls `AttachToActor()` directly.

### 8.2 What the Semantic Lane Reuses (Read-Only)

| Resource | Access Pattern | Rationale |
|----------|---------------|-----------|
| `ActorCache` (GUID→AActor* lookup) | `FindActorFast(Guid)` — read only | Parent/child existence check |
| `FSyncTransformState::ParentGuid` | Read only — for cycle detection parent chain walk | Must NOT write — layout is FROZEN |
| `GetAttachParentActor()` | Read only — UE engine API, always safe | Walking current parent chain for cycle detection |
| `ActorCache` iteration | Read only — validating orphan queue against cache | No cache mutation |

### 8.3 What the Semantic Lane Does NOT Touch

| System | Why |
|--------|-----|
| `FSyncTransformState::bHasParent` | FROZEN — runtime transform evaluation owns this |
| `FSyncTransformState::bPendingSceneGraphWrite` | FROZEN — runtime interpolation owns this |
| `FSyncTransformState::LocalTargetLocation/Rotation/Scale` | FROZEN — runtime local-space interpolation owns these |
| `PendingAttachments` (runtime queue) | FROZEN — runtime deferred attachment owns this |
| `AttachToParent()` (runtime function) | FROZEN — contains runtime-specific validation (self, stale, cycle) that the semantic lane duplicates independently |
| `InterpolateTransforms()` | FROZEN — world/local interp decision is runtime-owned |
| `UpdateTargetTransform()` | FROZEN — transform ingestion owns parent detection |

### 8.4 How the Semantic Lane Requests Attachment

The semantic lane calls `AttachToActor()` / `DetachFromActor()` **directly**
on the `AActor` instances, bypassing all frozen runtime helpers:

```cpp
// Semantic lane: direct engine API call
void HandleHierarchy(const FGuid& ChildGuid, const FGuid& ParentGuid, ...)
{
    AActor* Child = FindActorFast(ChildGuid);
    if (!Child) { /* reject */ return; }

    if (!ParentGuid.IsValid())  // Detach to root
    {
        Child->DetachFromActor(FDetachmentTransformRules::KeepWorldTransform);
        return;
    }

    AActor* Parent = FindActorFast(ParentGuid);
    if (!Parent) { /* defer */ return; }

    if (WouldCreateCycle(Child, Parent)) { /* reject cycle */ return; }

    Child->AttachToActor(Parent, FAttachmentTransformRules::KeepWorldTransform);
}
```

This is intentional: the semantic lane has its own validation (cycle checks,
existence checks, deferred retry) that are INDEPENDENT of the runtime system.

### 8.5 Interaction Matrix

| Event | Runtime Behavior | Semantic Lane Behavior | Interaction |
|-------|-----------------|----------------------|-------------|
| Transform packet for child | `UpdateTargetTransform` → reads parent → interpolates local | No action (transform is not hierarchy) | Independent — both may update same actor in same Tick |
| Hierarchy packet for child | No action (semantic lane only) | Attach/detach per semanic | Semantic attach may set `ParentGuid` in `FSyncTransformState` via engine? No — the runtime reads `GetAttachParentActor()` directly |
| Snapshot begin | `bInSnapshotBuild = true`, defer runtime attachments | Separate: semantic lane attaches directly or defers | Both systems respect `bInSnapshotBuild` but through different mechanisms |
| Snapshot end | `ResolvePendingAttachments` runs | `ResolveHierarchyAttachments` runs (separate) | Independent resolution of each system's deferred queue |
| Delete object | `HandleDeleteObject` → remove from `PendingAttachments` | Should ALSO remove from `PendingHierarchyAttachments` | Semantic lane must observe GUID deletion to avoid attaching to deleted parent |
| Reconnect | `PendingAttachments` cleared | `PendingHierarchyAttachments` cleared | Both systems clear on reconnect |

### 8.6 Conflict Case: Semantic Attach + Runtime Detach

**Scenario**: 
1. Semantic lane attaches A→B (via PT_Hierarchy, seq=5)
2. Runtime transform packet arrives for A with all-zero parent GUID (detach)
3. Runtime detaches A from B via `DetachFromActor`

**Result**: The runtime's transform interpretation takes precedence for
transform evaluation. The semantic lane's parent intent is OVERWRITTEN
by the runtime transform stream.

**Assessment**: This is acceptable because:
- Blender is authoritative for BOTH transforms AND hierarchy
- If Blender's transform stream says "no parent," it means Blender's
  hierarchy was updated between the hierarchy packet and the transform packet
- The latest Blender state wins (transform stream is higher-frequency)

**Correction**: In practice, Blender should not emit conflicting
hierarchy + transform in the same session. If it does, the transform
stream wins by being processed later in the pipeline.

**Architectural note**: This conflict case is inherent to the separation
of concerns. A future improvement might add `bHasParent`/`ParentGuid`
validation in the transform stream to detect conflicts, but that is
deferred until both lanes are stabilized.

---

## 9. Observability Requirements

### 9.1 Profiler Scopes (Mandatory)

| Scope Name | Location | Purpose |
|------------|----------|---------|
| `UELiveSync_ProcessHierarchyPackets` | Parse block in `ProcessBinaryPacket` | Measures time to parse all PT_Hierarchy packets in a batch |
| `UELiveSync_HandleHierarchy` | `HandleHierarchy()` function | Measures time for a single hierarchy event (validation + application + cycle check) |

### 9.2 Log Prefixes

| Prefix | Usage | Level |
|--------|-------|-------|
| `[HIERARCHY]` | General hierarchy events (apply, detach, error) | Log |
| `[HIERARCHY][ATTACH]` | Successful attachment | Verbose |
| `[HIERARCHY][DETACH]` | Successful detach | Verbose |
| `[HIERARCHY][REPLAY]` | Hierarchy events during snapshot replay | Verbose |
| `[HIERARCHY][ORPHAN]` | Orphan deferral, retry, timeout | Warning (timeout) / Log (defer) |
| `[HIERARCHY][CYCLE]` | Cycle detection and rejection | Warning |
| `[HIERARCHY][SUPPRESSION]` | Suppression scope entry/exit | Verbose |
| `[HIERARCHY][STALE]` | Stale sequence rejection | Verbose |

### 9.3 Mandatory Log Statements

| Event | Log |
|-------|-----|
| Apply attach | `[HIERARCHY][ATTACH] child=%s parent=%s (origin=%s seq=%u)` |
| Apply detach | `[HIERARCHY][DETACH] child=%s (origin=%s seq=%u) — detached from root` |
| Apply detach (from specific parent) | `[HIERARCHY][DETACH] child=%s (origin=%s seq=%u) — was attached to %s` |
| Deferred (missing parent) | `[HIERARCHY][ORPHAN] Deferred: child=%s parent=%s attempt=%d/%d` |
| Orphan timeout | `[HIERARCHY][ORPHAN] TIMEOUT: child=%s parent=%s — evicting after %d retries` |
| Self-cycle | `[HIERARCHY][CYCLE] Self-parent rejected: child=%s (seq=%u)` |
| Direct cycle | `[HIERARCHY][CYCLE] Direct cycle rejected: child=%s parent=%s (seq=%u)` |
| Chain cycle | `[HIERARCHY][CYCLE] Chain cycle rejected: child=%s parent=%s (ancestor depth=%d, seq=%u)` |
| Depth limit cycle | `[HIERARCHY][CYCLE] Depth limit exceeded: child=%s parent=%s (depth=256, seq=%u) — assuming cycle` |
| Stale seq | `[HIERARCHY][STALE] Rejected: child=%s (incoming seq=%u, last seq=%u)` |
| Unknown child | `[HIERARCHY] Rejected: unknown child=%s (seq=%u)` |
| Malformed packet | `[HIERARCHY] Malformed packet — %s` |
| Suppression enter | `[HIERARCHY][SUPPRESSION] Enter: child=%s` |
| Suppression exit | `[HIERARCHY][SUPPRESSION] Exit: child=%s` |
| Replay apply | `[HIERARCHY][REPLAY] Applied: child=%s parent=%s (seq=%u)` |
| Replay skip | `[HIERARCHY][REPLAY] Skipped (stale): child=%s (incoming seq=%u, last seq=%u)` |
| Tracker clear | `[HIERARCHY] Tracker cleared: reason=%s` |
| EndSnapshot summary | `[HIERARCHY] EndSnapshot: %d deferred remaining, %d hierarchy events processed` |

### 9.4 Counters (Mandatory)

| Counter | Type | When Incremented |
|---------|------|------------------|
| `HierarchyProcessed` | `std::atomic<int32>` (relaxed) | Every hierarchy event accepted and applied (attach or detach) |
| `HierarchyStaleRejections` | `std::atomic<int32>` (relaxed) | Every stale/duplicate sequence rejection |
| `HierarchyReplayApplied` | `std::atomic<int32>` (relaxed) | Every hierarchy event applied during snapshot replay |
| `HierarchyReplaySkipped` | `std::atomic<int32>` (relaxed) | Every hierarchy event skipped during replay (stale/duplicate) |
| `HierarchyOrphans` | `std::atomic<int32>` (relaxed) | Every deferred entry that times out and is evicted |
| `HierarchyCycles` | `std::atomic<int32>` (relaxed) | Every cycle detection (self, direct, chain, depth-limit) |

All counters follow the convention: `std::memory_order_relaxed`, display-only,
no fencing.

### 9.5 Tracker Clear Points

| Clear Point | Action |
|-------------|--------|
| `StopNetworkThread()` | `FHierarchySequenceTracker.LastSequence.Empty()` + log |
| `ConsoleReset()` | `FHierarchySequenceTracker.LastSequence.Empty()` + all counters `.store(0)` |
| Blender `_close_internal()` | `_hierarchy_sequences.clear()` + `_last_parent_guid.clear()` |

---

## 10. Failure-Safety Rules

### 10.1 Rule Table

| # | Rule | Violation Behavior | Category |
|---|------|-------------------|----------|
| F1 | Hierarchy MUST reject stale replay | `IncomingSeq <= LastSeq` → `HierarchyStaleRejections++` | Hard |
| F2 | Hierarchy MUST reject duplicate replay | Same as F1 (duplicate = `==`) | Hard |
| F3 | Hierarchy MUST reject self-parent | `ChildGuid == ParentGuid` → `HierarchyCycles++` | Hard |
| F4 | Hierarchy MUST reject cyclic attachment | Walk parent chain → cycle found → `HierarchyCycles++` | Hard |
| F5 | Hierarchy MUST reject malformed packets | < 44 bytes per object → `Stats.MalformedPackets++` | Hard |
| F6 | Hierarchy MUST survive reconnect storms | Tracker cleared every reconnect;  TCP backpressure | Hard |
| F7 | Hierarchy MUST preserve deterministic replay | Same packet sequence → same attachment state | Hard |
| F8 | Hierarchy MUST handle missing parent gracefully | Deferred retry → timeout → orphan | Hard |
| F9 | Hierarchy MUST handle missing child gracefully | Unknown child → reject immediately | Hard |
| F10 | Hierarchy MUST NOT modify frozen systems | $5 frozen zone audit | Hard |
| F11 | Hierarchy MUST NOT create permanent pending state | 60-frame max retry, then eviction | Hard |
| F12 | Hierarchy MUST process packets in bounded time | O(1) per packet, O(1) cycle detection (amortized) | Soft (perf) |
| F13 | Hierarchy MUST NOT cascade failure from one packet to batch | Each packet independently validated | Hard |
| F14 | Hierarchy MUST NOT allocate per-frame | Sequence tracker bounded 2048; deferred queue bounded 2048 | Hard |
| F15 | Hierarchy MUST log all rejection reasons | Every rejection has a specific log message with [HIERARCHY][CATEGORY] | Hard |

### 10.2 Malformed Packet Handling

| Condition | Detection | Behavior |
|-----------|-----------|----------|
| Payload < 44 bytes | `RemainingBytes < 44` | Reject. `Stats.MalformedPackets++`. Log: `[HIERARCHY] Malformed packet — truncated payload (%d bytes, expected >=44)` |
| Object count > max | `NumObjects > MAX_OBJECTS_PER_BATCH` (1024) | Reject. `Stats.MalformedPackets++`. Log: `[HIERARCHY] Malformed packet — oversized batch (%d objects, max %d)` |
| Invalid GUID (all-zero child) | `ChildGuid == FGuid()` | Reject. `HierarchyStaleRejections++`. Log: `[HIERARCHY] Rejected: zero child GUID` |
| Partial batch malformation | First object valid, second truncated | Apply first, reject truncated second. Log malformed. Partial application is safe — hierarchy events are independent per child. |

### 10.3 Reconnect Storm Survival

| Condition | Behavior |
|-----------|----------|
| 20 rapid reconnect cycles | Tracker cleared each cycle. No state accumulation. |
| 3000 hierarchy events in first batch after reconnect | Batch processed normally. Each event independently validated. Deferred queue accepts up to 2048. |
| Parent arrives mid-batch during replay | Deferred entries resolved in same Tick's `ResolveHierarchyAttachments`. |
| Blender disconnects mid-snapshot | `PendingHierarchyAttachments` cleared on next reconnect. No orphan persistence across sessions. |
| TCP buffer overflow | `FLiveSyncQueue` drop-oldest protects the network thread. Lost hierarchy events are re-sent on next snapshot. |

### 10.4 Deterministic Replay Guarantee

The following inputs uniquely determine the replay output:

```
Deterministic Replay Input:
  - Packet batch (ordered list of PT_Hierarchy events)
  - ActorCache state at start of replay
  - Sequence tracker state (empty — cleared on reconnect)
  - Random seed (not used)

Deterministic Replay Output:
  - For each child: either attached to parent, or deferred, or orphaned
  - HierarchyCycles = 0 (cycles cannot occur in replay — packets are Blender-generated)
  - Orphan set = children whose parents are not in ActorCache at timeout
```

Given identical inputs, replay produces identical attachment state.
This is guaranteed because:
- No external dependencies (physics, user input, network)
- No random number generation
- Deterministic timeout (frame-count based)
- No cross-packet coupling in validation

---

## 11. Complexity Assessment

### 11.1 Complexity Comparison

| Dimension | Rename (`0x0C`) | Visibility (`0x0B`) | Hierarchy (`0x0D`) |
|-----------|-----------------|---------------------|---------------------|
| **Semantic nature** | String mutation | Bool mutation | Graph edge mutation |
| **Objects affected** | 1 (the renamed object) | 1 (the toggled object) | 2 (child + parent), potentially N (orphan cascade) |
| **Dependency sensitivity** | None — rename is always safe | None — toggle is always safe | **High** — parent must exist |
| **Cycle risk** | None | None | **High** — self, direct, chain |
| **Orphan risk** | None | None | **Medium** — missing parent → deferred → timeout |
| **Replay ordering required** | No — each rename is independent | No — each toggle is independent | **Yes** — parents before children for optimal path |
| **Replay ordering tolerance** | N/A (no dependency) | N/A (no dependency) | **Deferred retry** handles violations |
| **State per object** | One string (name) | One bool (hidden) | One edge (parent GUID) |
| **Tracker entries** | 2048 max | 2048 max | 2048 max |
| **Validation depth** | Compare strings | Compare bools | **Walk parent chain** (max 256) |
| **Failure modes** | Storm, duplicate, stale | Storm, duplicate, stale | Storm, duplicate, stale, **cycle, orphan, ordering violation** |
| **Failure cascade risk** | None — isolated | None — isolated | **Low** — per-packet isolation prevents cascade |
| **Packet size** | Variable (up to ~544 bytes) | Fixed 29 bytes | **Fixed 44 bytes** |
| **Test scenarios needed** | ~10 | ~12 | **~20+** (see done criteria in scope lock) |
| **Runtime overlap** | None | None | **High** — must avoid contaminating frozen systems |

### 11.2 Why Hierarchy Is the First True Graph-Consistency Lane

Rename and visibility are **property mutations** — they change a single
attribute of a single object. The correctness of a rename does not depend
on any other object's state. The correctness of a visibility toggle does
not depend on any other object's state.

Hierarchy is a **graph edge mutation** — it changes the relationship
between two objects. The correctness of a parent assignment depends on:
1. The parent object existing
2. The assignment not creating a cycle
3. The child not being an ancestor of the parent (same as 2)
4. The parent not being deleted in the same session
5. The ordering of replay packets relative to creates and other hierarchy events

This makes hierarchy the first lane where:
- **Temporal ordering matters** (parent must be created before it can be parented)
- **Graph topology must be validated** (acyclic constraint)
- **Failure is not isolated to one object** (orphan affects the child, but the child
  still exists as root — it's not deleted, but its transform semantics differ)
- **Replay has a preferred path and a fallback path** (in-order = fast, out-of-order = deferred)

### 11.3 Complexity Budget

| Operation | Cost | Notes |
|-----------|------|-------|
| Parse packet (fixed 44 bytes) | O(1) | Trivial — known stride |
| Existence check (ActorCache lookup) | O(1) | TMap lookup |
| Sequence check | O(1) | TMap lookup |
| Self-cycle check | O(1) | Direct comparison |
| Chain-cycle check (no cycle) | O(depth) | Walk parent chain; depth ≤ 256; average depth ≤ 5 |
| Chain-cycle check (cycle) | O(depth) | Walk until cycle found or depth limit |
| AttachToActor | Engine cost | Single scene graph operation |
| Defer (add to queue) | O(1) | TArray::Add |
| Retry pass (resolve deferred) | O(N_deferred) | Each entry does one ActorCache lookup |
| Max per-frame work | O(batch_size + deferred_count) | Bounded by packet batch size and 2048 queue |

**Per-frame worst case**: 1024 batch packets × O(256) cycle checks + 2048 deferred
retries = ~262K operations. Each operation is lightweight (TMap lookup + FGuid
comparison). This is acceptable for a realtime sync tool.

---

## 12. Lifecycle/Delete Dependency Warning

### 12.1 Formal Dependency Statement

```
Hierarchy replication MUST be STABILIZED before lifecycle/delete
replication can be designed or implemented.
```

### 12.2 Why Delete Depends on Hierarchy

| Dependency | Explanation |
|------------|-------------|
| **Parent delete orphans children** | If Blender deletes a parent object, what happens to the UE children? They must either re-parent to grandparent, become root, or be destroyed. Without hierarchy awareness, the system cannot make this decision. |
| **Child-first vs parent-first delete order** | In UE, deleting a parent actor auto-destroys children (engine behavior). Blender may delete children individually. The hierarchy lane must be stable to predict and reconcile these ordering differences. |
| **Tombstone interaction** | A tombstone (marker for "intentionally deleted actor") must record the deleted actor's children so the system can decide heir behavior on reconnect. This requires a hierarchy snapshot at deletion time. |
| **Delete replay on reconnect** | During snapshot replay, deleted actors must not be re-created. But if a deleted actor was a parent, its children must be handled. Without hierarchy awareness, the system cannot correctly replay delete tombstones. |
| **Orphan prevention on delete** | A well-designed system would re-parent orphaned children to the grandparent on parent delete. This requires hierarchy graph traversal capability. |

### 12.3 Risks of Implementing Delete Without Stable Hierarchy

| Risk | Severity | Scenario |
|------|----------|----------|
| **Orphan corruption** | HIGH | Parent deleted in UE. Children are detached by engine (auto-destroy or auto-detach). Blender still thinks children have a parent. Next transform packet for child tries to compute local transform using non-existent parent. Crash or NAN transform. |
| **Replay corruption** | HIGH | On reconnect, snapshot replays hierarchy for an orphaned child. Parent CREATE was skipped (tombstoned). Child enters infinite deferred retry. |
| **Graph inconsistency** | MEDIUM | Child has parent reference in FSyncTransformState, but parent actor doesn't exist. Interpolation reads parent world transform → reads garbage or crashes. |
| **Reconnect nondeterminism** | MEDIUM | Depending on packet ordering, orphaned children may or may not find their deleted parent in ActorCache (if tombstone is present). Behavior varies by arrival order. |
| **Tombstone explosion** | LOW | Deleted parent's children each produce orphan entries. If 300 children were attached to a deleted parent, 300 orphan entries flood the deferred queue. |
| **Cycle in delete** | MEDIUM | If A is parent of B and both are deleted, the order of tombstone creation matters. If B is tombstoned before A, the system may attempt to re-parent A to B's tombstone. |

### 12.4 Prerequisite Chain

```
Phase 6D Hierarchy STABILIZED
  ↓ (provides: stable parent-child detection, orphan handling, cycle rejection)
Phase 6E Lifecycle/Delete PLANNING (NOTE: "6E" is provisional — see canonical conventions §12.2)
  ↓ (requires: hierarchy-aware delete, tombstone with hierarchy snapshot)
Phase 6E Lifecycle/Delete STABILIZED
  ↓ (provides: delete replication, tombstone management, orphan cleanup)
Phase 6F Collection Sync (NOTE: "6F" is provisional)
  ↓ (requires: both hierarchy and lifecycle for collection membership)
Phase 6G Duplicate Detection (NOTE: "6G" is provisional)
  ↓ (requires: all prior lanes for correct GUID management)
Phase 7+ Bidirectional
```

### 12.5 What Delete without Hierarchy Looks Like (Warning)

If delete replication were implemented without hierarchy stabilization,
the following behaviors would be **undefined**:

1. **Orphaned children after parent delete**: No specification for how
   children are re-parented or root-ified.
2. **Child delete before parent delete**: Packet ordering dependency with
   no resolution strategy.
3. **Reconnect with tombstones and hierarchy**: No rule for whether a
   tombstoned parent's children should be re-created as roots.
4. **Bidirectional delete conflict**: If both Blender and UE delete
   different nodes in the same hierarchy tree, the unwinding order is
   non-deterministic.

**Recommendation**: Do NOT begin lifecycle/delete design until:
- Hierarchy lane has passed stabilization (all 42 done criteria met)
- At least one soak test with hierarchy + delete interaction has been run
- Orphan-to-delete transition has been observed in testing

---

## 13. Final Deliverable

### 13.1 Hierarchy Replay-Risk Summary

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Ordering violation (child before parent) | Medium (snapshot replay) | Transient — resolved within fast retry window | Deferred retry with bounded timeout |
| Orphan (parent never arrives) | Low (edge case — deleted parent) | Persistent mismatch (child is root in UE) | [ORPHAN] logging + timeout eviction; re-sync required |
| Stale sequence (tracker not cleared) | Very low (regression) | Hierarchy events silently ignored | Tracker clear in StopNetworkThread/ConsoleReset — test coverage |
| Duplicate sequence (same seq in replay) | Low (bug in Blender) | Second event silently ignored (<= rejection) | Correct by design — <= inequality covers duplicates |
| Attach storm (300+ events in single batch) | Medium (large scene) | Transient latency spike | O(N) batch processing, O(1) per event |
| Chain reversal (5+ levels reversed) | Low (Blender snapshot bug) | N-frame cascade resolution | Each level resolves as parent becomes available — convergent |
| Sequence wrap (uint32 overflow) | Extremely low (4B events per GUID) | Seq 0 would be rejected as stale (<= last=MAX_UINT) | Sequence starts at 1, never wraps in practice |

**Overall replay risk**: LOW. The deferred retry mechanism handles all
ordering violations deterministically. No replay scenario causes data loss,
crash, or non-deterministic behavior.

### 13.2 Orphan-Risk Summary

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Parent never exists in UE | Low (non-MESH parent, deleted parent) | Child is root in UE; Blender thinks child is attached | Graceful degradation; [ORPHAN] logging; re-sync fixes |
| Parent arrives after timeout | Very low (frame ordering + 5-second delay) | Child remains root permanently | New PT_Hierarchy from Blender re-attaches |
| Deferred queue overflow (2048) | Low (massive scene + massive ordering violation) | Oldest entries evicted (last-chance lost) | Overflow warning log; re-sync would fix |
| Orphan at disconnect | Low (reconnect clears queue) | No cross-session persistence | PendingHierarchyAttachments cleared on reconnect |
| Orphan during storm (parent missing for 300 children) | Low (one parent, 300 children) | 300 orphan entries; 300 evictions at timeout | Bounded queue (2048) handles 300 easily |

**Overall orphan risk**: LOW-MEDIUM. Orphaned entries always leave the child
as root (not destroyed, not corrupted). Recovery requires a re-sync or manual
hierarchy fix in Blender. Observability ([ORPHAN] logs) surfaces the issue.

### 13.3 Dependency-Ordering Summary

| Dependency | Resolution | Timeframe |
|------------|------------|-----------|
| Child's parent must exist in ActorCache | Deferred retry | ≤60 frames / ≤5 seconds |
| Child must exist in ActorCache | Immediate reject (hard) | — |
| Cycle detection (parent walk) | Immediate rejection (hard) | — |
| Detach-to-root has no dependency | Immediate application | — |
| Snapshot replay: parents before children | Protocol contract (Blender-side) | — |
| Snapshot replay: ordering violation | Deferred retry (UE-side) | ≤N frames for N-level reversal |
| Reconnect: stale state from previous session | Tracker cleared (no dependency on old state) | — |

**Key principle**: Soft dependencies (missing parent) are deferred. Hard
dependencies (missing child, cycle) are rejected. This ensures the system
always makes forward progress — no packet causes an infinite stall.

### 13.4 Reconnect-Risk Summary

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Stale sequence tracker from previous session | Very low (must be cleared) | Hierarchy events silently dropped | Tracker cleared in StopNetworkThread — verified by test |
| Orphaned entries from previous session persist | Very low (queue cleared on reconnect) | Stale orphans affecting fresh replay | PendingHierarchyAttachments cleared on reconnect |
| Attach to wrong parent (GUID collision) | Extremely low (GUIDs are UUID) | None if collision-free | Not a reconnect-specific risk |
| Deferred queue from pre-reconnect not cleared | Very low (bug) | Deferred entries applied during replay with stale parent references | Clear on HandleEndSnapshot and Connect |
| Sequence tracker at 2048 capacity | Low (only if same GUID set persists across reconnects) | Oldest GUID evicted from tracker | Tracker cleared on reconnect — always starts empty |
| Blender snapshot omits some hierarchy events | Low (Blender bug) | Some children left as root | Snapshot includes all non-root objects — verified by test |

**Overall reconnect risk**: LOW. Both `PendingHierarchyAttachments` and
`FHierarchySequenceTracker` are cleared on reconnect. Fresh snapshot replay
re-establishes all hierarchy relationships. No cross-session state persists.

### 13.5 Recommended Future Implementation Ordering

```
Phase 6D Hierarchy — THIS DESIGN
  ├── 24-phase6D-hierarchy-scope-lock.md ✓ (COMPLETE)
  └── 25-phase6D-vertical-slice-hierarchy.md ✓ (THIS DOCUMENT)
  └── Implementation (STABILIZED — Stages 0-13 complete)
        ├── Blender: _last_parent_guid diff + serialize_hierarchy() (NOT YET — Stage 11)
        ├── UE: PT_Hierarchy case + HandleHierarchy() + cycle detection ✅ (Stage 9)
        ├── UE: PendingHierarchyAttachments + ResolveHierarchyAttachments() ✅ (Stage 7)
        ├── UE: FHierarchySequenceTracker + counters ✅ (Stage 2-3)
        ├── UE: EOrphanState + state lifecycle logging ✅ (Stage 8)
        └── Tests: phase6_hierarchy_validation.py (72 standalone pass, 7 integration skip)

Phase 6E Lifecycle/Delete (BLOCKED — hierarchy must stabilize first)
  ├── Dependency: Hierarchy STABILIZED (all 42 done criteria met)
  ├── Requires: Tombstone design, orphan-cleanup design
  └── Risk: HIGH without hierarchy awareness

> **Note**: Phase letter designations 6E/6F/6G are provisional. See
> `22-semantic-event-architecture-conventions.md §12.2` for the
> canonical roadmap hierarchy.

Phase 6F Collection Sync (BLOCKED — lifecycle may be needed)
  ├── Dependency: Collection→folder mapping design
  └── Different packet type (0x0E proposed)

Phase 6G Duplicate Detection (BLOCKED — all prior lanes)
  ├── Dependency: GUID management, lifecycle
  └── Different packet semantics

Bidirectional UE→Blender (DEFERRED to Phase 7+)
  ├── Dependency: Blender-side TCP listener
  └── Applies to: rename, visibility, hierarchy, all future lanes
```

---

## Appendix A — Key Design Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Fixed 44-byte payload (not variable) | No variable-length fields; simpler parse; fewer malformed-packet paths |
| D2 | Separate deferred queue from runtime `PendingAttachments` | Frozen runtime cannot be modified; different retry semantics |
| D3 | All-zero ParentGuid = detach-to-root | Canonical "no parent" representation; no ambiguity |
| D4 | Cycle detection walks current runtime parent chain | Runtime state is the ground truth for topology; avoids speculative cycle detection on intent-chain |
| D5 | 256-level depth limit for cycle detection | Prevents infinite loop on corrupted graph; acceptible tradeoff (no scene has 256 levels) |
| D6 | Blender-side snapshot ordering contract (parents before children) | Enables fast-path resolution; violations recover via deferred retry |
| D7 | Frame-count-based timeout (60 frames) | Deterministic across different frame rates; matches existing runtime behavior |
| D8 | 6 counters (Processed, StaleRejections, ReplayApplied, ReplaySkipped, Orphans, Cycles) | Standard 4 + 2 lane-specific (orphans, cycles) — both new failure modes |
| D9 | No implicit graph healing | System replicates intent; it does not infer intent |
| D10 | Trackers cleared on every reconnect | Fresh state per session; no cross-session leakage |

---

## Appendix B — Packet Handling Pseudocode (Design Reference)

```
=== BLENDER (sync.py) ===

# On each sync iteration:
for obj in scene.objects:
    if obj.type != 'MESH':
        continue
    uuid = obj.get("ue_guid")
    if not uuid:
        continue

    new_parent_uuid = ""
    if obj.parent:
        new_parent_uuid = obj.parent.get("ue_guid", "")

    old_parent_uuid = _last_parent_guid.get(uuid, "")

    if new_parent_uuid != old_parent_uuid:
        _hierarchy_sequences[uuid] = _hierarchy_sequences.get(uuid, 0) + 1
        seq = _hierarchy_sequences[uuid]
        ts = time.time()

        # Determine parent GUID bytes (all-zero if no parent)
        if new_parent_uuid:
            parent_bytes = uuid_to_bytes(new_parent_uuid)
        else:
            parent_bytes = b'\x00' * 16

        child_bytes = uuid_to_bytes(uuid)
        seq_bytes = struct.pack("<I", seq)
        ts_bytes = struct.pack("<d", ts)

        payload = child_bytes + parent_bytes + seq_bytes + ts_bytes
        _send_packet(PT_HIERARCHY, payload)

        _last_parent_guid[uuid] = new_parent_uuid

# On snapshot build:
snapshot_hierarchy = []
for obj in sorted_by_hierarchy_depth(scene.objects):
    uuid = obj.get("ue_guid")
    if not uuid or not obj.parent:
        continue
    parent_uuid = obj.parent.get("ue_guid", "")
    seq = _hierarchy_sequences.get(uuid, 1)
    ts = time.time()
    snapshot_hierarchy.append((uuid, parent_uuid, seq, ts))
# Emit after parents, before children (depth order ensures this)


=== UE (UELiveSyncSubsystem.cpp) ===

// ProcessBinaryPacket — new case
case PT_Hierarchy:
{
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessHierarchyPackets);

    const int32 ObjSize = 44; // 16+16+4+8
    int32 Count = PayloadSize / ObjSize;

    if (PayloadSize % ObjSize != 0 || Count == 0)
    {
        Stats.MalformedPackets++;
        UE_LOG(LogLiveSync, Warning,
            TEXT("[HIERARCHY] Malformed packet — payload %d bytes (expected multiple of %d)"),
            PayloadSize, ObjSize);
        return;
    }

    if (Count > MAX_OBJECTS_PER_BATCH)
    {
        Stats.MalformedPackets++;
        UE_LOG(LogLiveSync, Warning,
            TEXT("[HIERARCHY] Malformed packet — batch too large: %d objects"),
            Count);
        return;
    }

    for (int32 i = 0; i < Count; i++)
    {
        const uint8* ObjPtr = PayloadPtr + (i * ObjSize);

        uint32 ChildParts[4];
        FMemory::Memcpy(ChildParts, ObjPtr, 16);
        FGuid ChildGuid(ChildParts[0], ChildParts[1], ChildParts[2], ChildParts[3]);

        uint32 ParentParts[4];
        FMemory::Memcpy(ParentParts, ObjPtr + 16, 16);
        FGuid ParentGuid(ParentParts[0], ParentParts[1], ParentParts[2], ParentParts[3]);

        uint32 Seq;
        FMemory::Memcpy(&Seq, ObjPtr + 32, 4);

        double Timestamp;
        FMemory::Memcpy(&Timestamp, ObjPtr + 36, 8);

        EChangeOrigin Origin = bInSnapshotBuild
            ? EChangeOrigin::Replay
            : EChangeOrigin::RemoteReplicated;

        FScopedChangeOrigin OriginScope(Origin);
        HandleHierarchy(ChildGuid, ParentGuid, Seq, Timestamp);
    }
    return;
}


// HandleHierarchy
void UUELiveSyncSubsystem::HandleHierarchy(
    const FGuid& ChildGuid,
    const FGuid& ParentGuid,
    uint32 Seq,
    double Timestamp)
{
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_HandleHierarchy);

    // 1. Zero child GUID check
    if (!ChildGuid.IsValid())
    {
        Stats.HierarchyStaleRejections++;
        UE_LOG(LogLiveSync, Warning,
            TEXT("[HIERARCHY] Rejected: zero child GUID (seq=%u)"), Seq);
        return;
    }

    // 2. Self-parent check
    if (ChildGuid == ParentGuid)
    {
        Stats.HierarchyCycles++;
        UE_LOG(LogLiveSync, Warning,
            TEXT("[HIERARCHY][CYCLE] Self-parent rejected: child=%s (seq=%u)"),
            *ChildGuid.ToString(EGuidFormats::Digits), Seq);
        return;
    }

    // 3. Child existence check
    AActor* Child = FindActorFast(ChildGuid);
    if (!Child)
    {
        Stats.HierarchyStaleRejections++;
        UE_LOG(LogLiveSync, Warning,
            TEXT("[HIERARCHY] Rejected: unknown child=%s (seq=%u)"),
            *ChildGuid.ToString(EGuidFormats::Digits), Seq);
        return;
    }

    // 4. Detach-to-root (no dependency, immediate)
    if (!ParentGuid.IsValid())
    {
        FScopedHierarchySuppression Suppress(ChildGuid);
        DetachFromRoot(Child);
        Stats.HierarchyProcessed++;
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[HIERARCHY][DETACH] child=%s (origin=%s seq=%u)"),
            *ChildGuid.ToString(EGuidFormats::Digits),
            *LexToString(FScopedChangeOrigin::GetCurrent()), Seq);
        return;
    }

    // 5. Sequence stale check
    if (GHierarchySequences.IsStaleOrDuplicate(ChildGuid, Seq))
    {
        if (bInSnapshotBuild)
            Stats.HierarchyReplaySkipped++;
        else
            Stats.HierarchyStaleRejections++;
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[HIERARCHY][STALE] Rejected: child=%s (incoming seq=%u, last seq=%u)"),
            *ChildGuid.ToString(EGuidFormats::Digits), Seq,
            GHierarchySequences.GetLastSeq(ChildGuid));
        return;
    }

    // 6. Parent existence check (defer if missing)
    AActor* Parent = FindActorFast(ParentGuid);
    if (!Parent)
    {
        DeferHierarchyAttachment(ChildGuid, ParentGuid, Seq, Timestamp);
        UE_LOG(LogLiveSync, Log,
            TEXT("[HIERARCHY][ORPHAN] Deferred: child=%s parent=%s attempt=0/%d"),
            *ChildGuid.ToString(EGuidFormats::Digits),
            *ParentGuid.ToString(EGuidFormats::Digits),
            MAX_DEFERRED_RETRIES);
        return;
    }

    // 7. Cycle detection
    if (WouldCreateCycle(Child, Parent))
    {
        Stats.HierarchyCycles++;
        UE_LOG(LogLiveSync, Warning,
            TEXT("[HIERARCHY][CYCLE] Cycle rejected: child=%s parent=%s (seq=%u)"),
            *ChildGuid.ToString(EGuidFormats::Digits),
            *ParentGuid.ToString(EGuidFormats::Digits), Seq);
        return;
    }

    // 8. Apply attachment
    {
        FScopedHierarchySuppression Suppress(ChildGuid);
        ApplyHierarchyAttachment(Child, Parent);
    }

    // 9. Update sequence tracker
    GHierarchySequences.Update(ChildGuid, Seq);

    // 10. Count
    if (bInSnapshotBuild)
        Stats.HierarchyReplayApplied++;
    else
        Stats.HierarchyProcessed++;

    UE_LOG(LogLiveSync, Verbose,
        TEXT("[HIERARCHY][ATTACH] child=%s parent=%s (origin=%s seq=%u)"),
        *ChildGuid.ToString(EGuidFormats::Digits),
        *ParentGuid.ToString(EGuidFormats::Digits),
        *LexToString(FScopedChangeOrigin::GetCurrent()), Seq);
}
```

---

## Appendix C — Terminology

| Term | Definition |
|------|------------|
| Hierarchy semantic lane | The Phase 6D pathway that replicates parent-child attachment intent via PT_Hierarchy packets |
| Attachment intent | The semantic fact that object A should be a child of object B. Distinct from transform propagation. |
| Dependency sensitivity | A property of semantic lanes where the correctness of one packet depends on the prior arrival of another packet (parent before child) |
| Orphan | A child whose parent GUID is non-zero but whose parent actor never arrives within the deferred retry window |
| Cycle | A graph state where following parent references eventually returns to the starting node (self-cycle, direct cycle, chain cycle) |
| Deferred retry | The mechanism that retries attachment when the parent is missing, with bounded retry count and timeout |
| Orphaned | The final state of a deferred entry after timeout — child is root, hierarchy mismatch exists |
| Detach-to-root | The semantic operation of clearing a child's parent, represented by all-zero ParentGuid |
| Parent chain walk | The algorithm that walks from a parent actor up to root via `GetAttachParentActor()` to detect cycles |
| Replay dependency chain | The sequence of dependencies during snapshot replay where parents must be processed before children for optimal attachment |
| Protocol contract | A rule that must be followed by the sender (Blender snapshot builder) but is not enforced by the receiver (UE) |
| Graph healing | The forbidden practice of automatically modifying the scene graph to resolve cycles or fill missing parents |
| Frozen runtime | The Phase 5 systems (FSyncTransformState, ResolvePendingAttachments, AttachToParent, InterpolateTransforms) that the semantic lane must not modify |

---

## Revision History

| Date | Version | Changes |
|------|---------|---------|
| 2026-05-26 | 1.0 | Initial vertical slice design — Phase 6D planning. Defines packet format, replay dependency chain analysis, deferred attachment semantics, snapshot ordering contract, graph consistency invariants, orphan semantics, cycle prevention, runtime interaction, observability, failure-safety, complexity assessment, and lifecycle/delete dependency warning. |
| 2026-05-26 | 1.1 | Architecture review findings appended — 9 issues identified, 0 blocking. See §14 Architecture Review Findings. |

---

## 14. Architecture Review Findings

> **Review date**: 2026-05-26
> **Review type**: Dedicated architecture review of the hierarchy semantic lane design
> **Review scope**: All 13 sections + appendices of this document; cross-referenced against
>   `24-phase6D-hierarchy-scope-lock.md`, `22-semantic-event-architecture-conventions.md`,
>   and existing Phase 5 runtime systems.
> **Review result**: **PASS — 9 findings, 0 blocking, 0 P0 risks.**
> **Go/no-go**: **GO** for implementation planning (after findings are addressed).

### 14.1 Review Methodology

Each of the 10 required review areas was assessed independently:

| # | Review Area | Status |
|---|-------------|--------|
| 1 | Replay Dependency Chain Audit | PASS — 1 finding (FINDING-001) |
| 2 | Deferred Queue Audit | PASS — 2 findings (FINDING-002, FINDING-003) |
| 3 | Authority Boundary Audit | PASS — 1 finding (FINDING-004) |
| 4 | Runtime Interaction Audit | PASS — 1 finding (FINDING-005) |
| 5 | Cycle Detection Audit | PASS — 1 finding (FINDING-006) |
| 6 | Orphan Lifecycle Audit | PASS — 1 finding (FINDING-007) |
| 7 | Snapshot Ordering Contract Audit | PASS — 0 findings |
| 8 | Observability Audit | PASS — 1 finding (FINDING-008) |
| 9 | Frozen Runtime Boundary Audit | PASS — 1 finding (FINDING-009) |
| 10 | Additional Edge Cases | PASS — integrated into other findings |

### 14.2 Findings

#### FINDING-001: Deferred Resolution Must Re-Check Sequence Tracker

**Severity**: MEDIUM
**Area**: Replay Dependency Chain Audit (§2)

**Description**:
When a deferred entry resolves (parent found within retry window), the current
design applies the attachment using the original sequence number stored in the
deferred entry. However, the sequence tracker may have advanced while the entry
was pending (e.g., a newer hierarchy event for the same child was applied via a
different path). The deferred entry's sequence would then be stale, and applying
it would regress the tracked sequence.

**Scenario**:
```
1. PT_Hierarchy seq=5 for child B → parent A missing. Deferred.
   (tracker[B] still undefined — not updated on deferral)
2. A later PT_Hierarchy seq=7 for child B → parent C (exists). Applied.
   tracker[B] = 7. B is now attached to C.
3. Deferred entry for B→A (seq=5) resolves (A now exists).
   Current design: apply seq=5, attach B→A, set tracker[B]=5.
   // WRONG — seq=5 is stale; seq=7 was already applied. tracker[B] regressed.
```

**Root cause**: `HandleHierarchy()` (pseudocode step 6) returns after deferring
without updating the tracker. When `ResolveHierarchyAttachments()` finds the
parent, it must re-validate against the tracker before applying.

**Resolution required**:
In `ResolveHierarchyAttachments()`, before applying a resolved deferred entry:

```
if (GHierarchySequences.IsStaleOrDuplicate(Entry.ChildGuid, Entry.Sequence))
{
    // Tracker advanced while deferred — this entry is stale.
    // Log [HIERARCHY][STALE] Deferred resolution stale: child=%s, deferred seq=%u, current seq=%u
    // Do NOT apply. Remove from queue. Increment HierarchyStaleRejections.
    continue;
}
```

**Affected sections**: §3.7 (deferred queue struct and resolve logic), Appendix B
(pseudocode must include this check in `ResolveHierarchyAttachments()`).

**Status**: ACCEPTED — must be added before implementation.

---

#### FINDING-002: Deferred Entries Must Be Deduplicated, Not Duplicated

**Severity**: MEDIUM
**Area**: Deferred Queue Audit (§3)

**Description**:
If a new PT_Hierarchy packet arrives for a child GUID that already has a pending
deferred entry, the current design does not specify whether the new packet creates
a second entry or updates the existing one. Multiple entries for the same child
would cause redundant retries, confusing logs, and potential double-application.

**Scenario**:
```
1. PT_Hierarchy seq=5 for child B → parent A missing. Deferred.
   PendingHierarchyAttachments = [{B, A, seq=5, retry=0}]
2. PT_Hierarchy seq=6 for child B → parent A (still missing). Sequence check passes
   (tracker[B] undefined). Deferred again.
   PendingHierarchyAttachments = [{B, A, seq=5}, {B, A, seq=6}]
   // Duplicate entries for same (child, parent) pair. Wasteful. Confusing.
```

**Resolution required**:
Before adding to `PendingHierarchyAttachments`, check if an entry for `ChildGuid`
already exists:

```
if (FPendingHierarchyAttachment* Existing = FindPending(ChildGuid))
{
    if (Seq > Existing->Sequence)
    {
        // Newer sequence — update existing entry, reset retry.
        Existing->Sequence = Seq;
        Existing->ParentGuid = ParentGuid;
        Existing->RetryFrames = 0;
        Existing->CreatedTime = Now;
        Log: [HIERARCHY][ORPHAN] Updated deferred: child=%s (seq %u → %u)
    }
    else
    {
        // Stale or duplicate — skip.
        Log: [HIERARCHY][STALE] Deferred update skipped: child=%s (incoming seq=%u <= existing seq=%u)
    }
    return; // Don't add duplicate
}
// No existing entry — add normally.
PendingHierarchyAttachments.Add({ChildGuid, ParentGuid, Seq, 0, Now});
```

**Affected sections**: §3.7 (deferred queue add logic), §6.3 (orphan lifecycle).

**Status**: ACCEPTED — must be added before implementation.

---

#### FINDING-003: Deferred Queue Overflow Eviction Must Be Observable

**Severity**: LOW
**Area**: Deferred Queue Audit (§3)

**Description**:
The design specifies "Reject oldest entry on overflow, log warning" (§3.7) but
does not define "oldest" (by `CreatedTime` or array position) or what counter
is incremented on eviction.

**Resolution required**:

```
// Overflow policy: evict entry with oldest CreatedTime (linear scan, O(2048) worst case).
// This is acceptable because eviction is rare — queue is sized at 2048.
int32 EvictIdx = 0;
double OldestTime = PendingHierarchyAttachments[0].CreatedTime;
for (int32 i = 1; i < PendingHierarchyAttachments.Num(); i++)
{
    if (PendingHierarchyAttachments[i].CreatedTime < OldestTime)
    {
        OldestTime = PendingHierarchyAttachments[i].CreatedTime;
        EvictIdx = i;
    }
}
Log: [HIERARCHY][ORPHAN] Deferred queue overflow — evicting child=%s (created %.1fs ago)
HierarchyOrphans++;  // Eviction is an orphan event — child loses retry chance.
PendingHierarchyAttachments.RemoveAt(EvictIdx);
```

**Alternative**: If O(N) scan is undesirable, use the oldest-by-position (index 0)
as the eviction candidate (FIFO-like). Simpler, but less fair.

**Recommendation**: Use `RemoveAt(0)` (oldest-added) for O(1) eviction. Accept
that this may evict an entry that was just updated (FINDING-002). The bounded
queue guarantee is the priority — fairness is secondary.

**Affected sections**: §3.7. §6.4.

**Status**: ACCEPTED — O(1) eviction via `RemoveAt(0)` recommended.

---

#### FINDING-004: Packet Type Dispatch Order in ProcessBinaryPacket

**Severity**: LOW
**Area**: Authority Boundary Audit (§8.6)

**Description**:
The design documents that a conflict between semantic attach and runtime detach
resolves as "transform/runtime wins" (§8.6). However, the actual resolution
depends on the `case PT_*` dispatch order within `ProcessBinaryPacket`. If
transform packets are parsed before hierarchy packets, the transform's parent
intent is processed first, then hierarchy overwrites. If hierarchy is parsed
first, hierarchy's intent is processed first, then transform overwrites.

The current `ProcessBinaryPacket` dispatch order (derived from existing packet
type constants) processes packet types in type-byte order. Since `PT_Hierarchy`
is proposed as `0x0D` and `PT_Transform` is `0x01`, hierarchy packets would
appear AFTER transform packets in the binary stream (type bytes are checked in
ascending order within the parser).

**Implication**: For packets within the same TCP buffer, the dispatch order
determines which system's intent "wins" at the per-packet level. The real
authority is Blender's emission order — whatever Blender sends last (in the
same send buffer) wins. This is an accepted limitation of the decoupled design.

**Resolution required**:
Document the dispatch order explicitly in §8.6 and state that:

```
Packet type dispatch order within ProcessBinaryPacket follows kValidTypes[]
ascending. PT_Hierarchy (0x0D) is processed after PT_Transform (0x01).
This means hierarchy events parsed AFTER transform events within the same
TCP buffer. However, because Blender sends hierarchy events IN ADDITION TO
transform events (not instead of), the actual authority is determined by
Blender's emission order: whichever is sent last (within the same send()
call) produces the final state. This is documented as an accepted limitation.
```

**Affected sections**: §8.6.

**Status**: ACCEPTED — documentation only, no code change needed.

---

#### FINDING-005: Skip No-Op Attachments to Reduce Scene Graph Churn

**Severity**: LOW
**Area**: Runtime Interaction Audit (§8.4)

**Description**:
The semantic lane calls `AttachToActor()` even when the child is already attached
to the intended parent. While UE makes this near-idempotent, it still processes
attachment rules and dirties child transforms. This is wasteful, especially
during replay where the runtime system may have already established the correct
attachment.

**Scenario**:
```
1. Runtime transform stream: A→B attached via UpdateTargetTransform + AttachToParent.
2. PT_Hierarchy for A→B arrives. HandleHierarchy() lookup: A exists, B exists,
   no cycle. Calls AttachToActor(A, B, KeepWorldTransform).
   // A is already attached to B. This is a no-op. But UE still processes it.
```

**Resolution required**:
Before calling `AttachToActor()` in `HandleHierarchy()`, check current attachment:

```cpp
// Skip if already attached to intended parent
AActor* CurrentParent = Child->GetAttachParentActor();
if (CurrentParent == Parent)
{
    // Already correctly attached — no-op. Log at Verbose.
    UE_LOG(LogLiveSync, Verbose,
        TEXT("[HIERARCHY][ATTACH] Skipped — already attached: child=%s parent=%s"),
        ...);
    // Still update sequence tracker and count.
    GHierarchySequences.Update(ChildGuid, Seq);
    Stats.HierarchyProcessed++;
    return;
}
```

Same check applies in `ResolveHierarchyAttachments()` for deferred resolution.

**Affected sections**: §8.4, Appendix B pseudocode.

**Status**: ACCEPTED — should be added before implementation.

---

#### FINDING-006: Cycle Detection Walks Runtime Chain, Not Intent Chain

**Severity**: LOW
**Area**: Cycle Detection Audit (§5.4, §7.6)

**Description**:
The cycle detection algorithm (`WouldCreateCycle`) walks the **current runtime**
parent chain via `GetAttachParentActor()`. When multiple hierarchy events are
processed in the same Tick, the runtime chain reflects only ALREADY-APPLIED
attachments, not pending ones. This means a cycle spanning two hierarchy events
in the same batch may not be detected.

**Scenario**:
```
Batch: [A→B, B→A] (processed sequentially in HandleHierarchy)
1. Process A→B: WouldCreateCycle(A, B) walks B's parent chain → B is root → no cycle.
   Attach A→B. Runtime chain: B is root, A is child of B.
2. Process B→A: WouldCreateCycle(B, A) walks A's parent chain → A's parent is B.
   Check: is B == A's parent? Yes, parent chain includes B. CYCLE DETECTED.
   → Rejected. Correct.
```

This works correctly for the batch scenario. ✓

**However**, if BOTH events arrived in the same batch but the runtime attachment
from A→B hasn't been applied yet (if AttachToActor is deferred within the
engine's internal pending queue), the cycle check for B→A would walk a stale
chain and miss the cycle.

**Assessment**: This is an inherent limitation — we cannot reliably detect
cycles that depend on uncommitted engine attachment state. The risk is LOW
because:
1. UE's `AttachToActor()` is typically synchronous (game thread)
2. The batch processing in `HandleHierarchy` is single-threaded game thread
3. Between step 1 and step 2 of the above scenario, the engine processes
   the `AttachToActor` call synchronously

**Resolution required**:
Document this limitation in §5.4:

```
Limitation: WouldCreateCycle walks the CURRENT runtime parent chain, which
reflects only already-committed AttachToActor calls within the same Tick.
If the UE engine defers AttachToActor internally (not observed in practice),
a cycle in adjacent batch entries may not be detected. This is an accepted
limitation — the cycle would be detected on the next PT_Hierarchy event
for the same child, as the runtime chain would then reflect all prior
attachments.
```

**Affected sections**: §5.4.

**Status**: ACCEPTED — documentation only.

---

#### FINDING-007: Orphan Timeout Leaves Child in Current Attachment State, Not Root

**Severity**: MEDIUM
**Area**: Orphan Lifecycle Audit (§6.4, §3.4)

**Description**:
The design states "Child remains as root with its current world-space transform"
(§3.4, §6.4). This is correct ONLY if the child was root before the hierarchy
event. If the child was previously attached to a DIFFERENT parent, the timeout
should leave the child attached to its CURRENT parent — NOT detach it to root.

The orphan timeout merely means "the new parent never arrived" — it does NOT
mean "the child should become root." Implicitly detaching to root on timeout
would violate the "no implicit detach" principle established in the cycle policy.

**Scenario**:
```
1. Child C is attached to Parent A (established by a prior successful PT_Hierarchy
   or by the runtime transform stream).
2. PT_Hierarchy arrives: C→B (re-parent to B). Parent B doesn't exist. Deferred.
3. Timeout after 60 frames. Parent B still doesn't exist.
4. Current design says: "child remains root." This would DETACH C from A.
   → WRONG. C should remain attached to A, not become root.
```

**Correction**: The orphan timeout leaves the child in its PRE-EXISTING
attachment state. The timeout means "re-parent intent to B has failed." The
child's current parent (A) is unaffected.

**Resolution required**:
Update all references to "child remains root" to "child retains its current
attachment state" — specifically:

- §3.4: "Child **retains its current attachment state** (its parent, if any, is unchanged)."
- §6.1: "The child is **not detached from any existing parent** — only the new
   parent intent is abandoned."
- §6.4: "Child's current parent (if any) is preserved. No implicit detach occurs."

Add a clarification:
```
Important: The orphan timeout abandons the NEW attachment intent. It does NOT
undo the child's PREVIOUS attachment. If the child was attached to Parent A
before the hierarchy event, it remains attached to Parent A after timeout.
If the child was root before the hierarchy event, it remains root after timeout.
```

**Affected sections**: §3.4 §3.6 (state machine diagram), §6.1, §6.4.

**Status**: ACCEPTED — must be corrected before implementation.

---

#### FINDING-008: Missing Counter: HierarchyDeferredResolved

**Severity**: LOW
**Area**: Observability Audit (§9.4)

**Description**:
The design has 6 counters, but lacks a counter for successful deferred resolution.
Without it, the resolution rate (`DeferredResolved / (DeferredResolved + Orphans)`)
cannot be calculated from counters alone. This metric is important for detecting
ordering contract compliance during snapshot replay.

**Resolution required**:
Add the following counter to §9.4 and all related sections:

| Counter | Type | When Incremented |
|---------|------|------------------|
| `HierarchyDeferredResolved` | `std::atomic<int32>` (relaxed) | A deferred entry successfully resolves (parent found within window) and the attachment is applied |

Update the total: **7 counters**.

**Also recommended**: Add a corresponding log:
```
[HIERARCHY][ORPHAN] Resolved: child=%s parent=%s (after %d retries, seq=%u)
```

**Affected sections**: §9.4, §3.7, §6.6, §9.3, Appendix B pseudocode.

**Status**: ACCEPTED — should be added before implementation.

---

#### FINDING-009: Tick Pipeline Ordering for ResolveHierarchyAttachments

**Severity**: LOW
**Area**: Frozen Runtime Boundary Audit (§8.5)

**Description**:
The design specifies `ResolveHierarchyAttachments()` is called once per Tick
after `ProcessQueuedPackets` (§3.7). However, it does not specify where this
sits relative to the runtime `ResolvePendingAttachments()` in the Tick pipeline.
If the runtime resolves an attachment and then the semantic lane resolves a
DIFFERENT parent intent for the same child, the semantic lane's intent should
win (more explicit). The opposite order would be incorrect.

**Resolution required**:
Document the explicit Tick ordering:

```
Tick pipeline position for hierarchy resolution:

1. ProcessQueuedPackets() — parse all packet types (including PT_Hierarchy)
2. ResolvePendingAttachments() — runtime deferred attachment resolution (FROZEN)
3. ResolveHierarchyAttachments() — semantic lane deferred resolution (NEW)
4. (remainder of Tick pipeline: interpolate, purge, etc.)
```

Rationale: Runtime attachment resolution runs FIRST, establishing the
transform-stream's best-effort parent assignment. Then semantic lane resolution
runs SECOND, applying the explicit hierarchy intent on top. This ensures
semantic hierarchy intent always wins over runtime transform interpretation.

**Affected sections**: §3.7 (add Tick ordering subsection).

**Status**: ACCEPTED — must be documented before implementation.

---

### 14.3 Pre-Implementation Checklist

Before implementation begins, the following must be complete:

| # | Item | Status | Finding Reference |
|---|------|--------|-------------------|
| 1 | Stale-sequence re-check in `ResolveHierarchyAttachments()` | OPEN | FINDING-001 |
| 2 | Deferred entry deduplication (update, not duplicate) | OPEN | FINDING-002 |
| 3 | O(1) overflow eviction via `RemoveAt(0)` | OPEN | FINDING-003 |
| 4 | Document dispatch order dependency in §8.6 | OPEN | FINDING-004 |
| 5 | `GetAttachParentActor()` no-op check before `AttachToActor()` | OPEN | FINDING-005 |
| 6 | Document cycle detection limitation (runtime chain vs. intent chain) | OPEN | FINDING-006 |
| 7 | Correct orphan timeout behavior: preserve current parent | OPEN | FINDING-007 |
| 8 | Add `HierarchyDeferredResolved` counter + log | OPEN | FINDING-008 |
| 9 | Document Tick pipeline ordering for `ResolveHierarchyAttachments()` | OPEN | FINDING-009 |

### 14.4 Confirmed-Safe Behaviors

The following design aspects were reviewed and confirmed safe:

| Behavior | Confidence | Rationale |
|----------|------------|-----------|
| Fixed 44-byte packet format | HIGH | Trivial parse, no variable-length edge cases. Matches visibility pattern. |
| Per-GUID monotonic sequencing | HIGH | Proven pattern from rename + visibility. Stale/duplicate rejection via `<=` is well-tested. |
| Separate deferred queue from runtime | HIGH | No frozen-zone modifications. Independent retry semantics. |
| 60-frame / 5-second timeout (dual) | HIGH | Matches existing `ResolvePendingAttachments` behavior. Frame-count ensures determinism. |
| All-cycle-type immediate rejection | HIGH | Self, direct, chain, depth-limit — all terminate deterministically. No deferral, no retry. |
| Detach-to-root has zero dependencies | HIGH | Always applies immediately. Cannot orphan, cannot cycle. |
| Reconnect clears all transient state | HIGH | Both tracker and deferred queue cleared. Fresh snapshot every reconnect. |
| Snapshot ordering contract + deferred fallback | HIGH | Best-effort ordering with deterministic fallback. No data loss on violation. |
| `[ORPHAN]` logging at every lifecycle stage | HIGH | All orphan transitions observable (defer, retry, timeout, resolve). |
| Provenance via `EChangeOrigin` + RAII scope | HIGH | Matches rename + visibility pattern. No provenance on wire — context-determined. |
| No frozen system modification | HIGH | Semantic lane calls `AttachToActor()` directly, bypassing all frozen helpers. |

### 14.5 Unresolved Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Cycle detection stale if AttachToActor deferred internally by UE** | LOW | Documented in FINDING-006. Cycle detected on next PT_Hierarchy. |
| **uint32 sequence wrap (4B events per GUID)** | NEGLIGIBLE | Never reached in practice. Sequence starts at 1. |
| **Both runtime and semantic lane attach same child in same Tick** | LOW | FINDING-005 addresses this (skip if already attached). Tick ordering (FINDING-009) ensures semantic wins. |
| **Deferred queue at 2048 capacity during orphan storm** | LOW | 2048 is generous. Overflow evicts oldest via O(1) `RemoveAt(0)`. Logged. |
| **Non-MESH parent causes permanent orphan** | MEDIUM | Non-MESH objects are excluded from sync. If a MESH child's parent is a non-MESH object in Blender, the parent will never have a UE actor. The child becomes a permanent orphan until the parent is changed in Blender. This is an existing limitation inherited from Phase 5 (MESH-only filter). Document explicitly that parent must be MESH for hierarchy replication to succeed. |

**Additional documentation needed for non-MESH parent**: Add to §6.2 and the
non-MESH filter documentation:

```
Known limitation: If a MESH object's parent in Blender is a non-MESH object
(camera, light, armature, etc.), the parent will never be created in UE
(only MESH objects are synced). The hierarchy event for this child will
enter deferred retry and eventually time out as a permanent orphan.
The child will remain root in UE. To fix, either:
1. Convert the parent to MESH in Blender, or
2. Clear the parent relationship.
This is an accepted limitation — non-MESH parent tracking would require
expanding the sync scope beyond MESH objects, which is deferred.
```

### 14.6 Go/No-Go Recommendation

**Result: GO** for implementation planning.

The design is structurally sound. All 9 findings are non-blocking — they
are refinements (7), documentation gaps (1), and an additional counter (1).
No finding invalidates the core design. No P0 (crash/corruption) risks exist.

Implementation should proceed after the 9 pre-implementation checklist items
(§14.3) are addressed. These are primarily:
- Adding stale-sequence re-check on deferred resolution (FINDING-001)
- Adding deferred entry deduplication (FINDING-002)
- Correcting orphan timeout behavior (FINDING-007)
- The remaining 6 are documentation, logging, and minor optimization items

**Estimated implementation risk**: LOW-MEDIUM. Similar in complexity to
visibility (Phase 6C), with the addition of the deferred retry mechanism,
cycle detection, and orphan handling. The isolated parser branch pattern
(from rename/visibility) directly applies.

### 14.7 Review Sign-Off

| Role | Sign-Off | Date |
|------|----------|------|
| Design author | (pending) | 2026-05-26 |
| Architecture reviewer | (pending) | 2026-05-26 |

The 9 pre-implementation items must be resolved before code review begins,
but they do not require a second full architecture review — they are
bounded changes to the existing design.
