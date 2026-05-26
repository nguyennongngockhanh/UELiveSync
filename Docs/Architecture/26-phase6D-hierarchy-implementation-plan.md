# Phase 6D — Hierarchy Replication: Implementation Plan

> **Created**: 2026-05-26
> **Status**: IN PROGRESS — STAGES 0-7 COMPLETE
> **Predecessors**: Scope Lock (`24-phase6D-hierarchy-scope-lock.md`) · Vertical Slice Design (`25-phase6D-vertical-slice-hierarchy.md`) · Architecture Review (§14 of design doc)
> **Next**: Stage 0 — Pre-Implementation Audit
>
> This document defines the **operational implementation plan** for the hierarchy
> semantic lane. It bridges the design phase (what to build) to the implementation
> phase (how to build it safely).
>
> **This is a planning document. No runtime code has been modified.**

---

## Table of Contents

1. [Implementation Philosophy](#1-implementation-philosophy)
2. [Runtime Preservation Checklist](#2-runtime-preservation-checklist)
3. [Planned Packet Integration Order](#3-planned-packet-integration-order)
4. [Deferred Queue Ownership Plan](#4-deferred-queue-ownership-plan)
5. [Replay Tracker Integration Plan](#5-replay-tracker-integration-plan)
6. [Cycle Detection Implementation Plan](#6-cycle-detection-implementation-plan)
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

Hierarchy is the first dependency-sensitive semantic lane. It introduces
graph consistency constraints, orphan states, replay dependency chains,
and cycle detection — none of which exist in rename or visibility.

**Implementation priority order** (ranked from most to least important):

| Priority | Principle | Why |
|----------|-----------|-----|
| P1 | **Determinism** | Same input → same output. No randomness, no timing-dependent behavior, no frame-rate-dependent divergence. |
| P2 | **Bounded behavior** | Every operation has a known worst-case cost. No unbounded queues, no infinite retries, no unbounded memory growth. |
| P3 | **Observability** | Every state transition is logged and counted. No silent failures, no hidden state, no black-box behavior. |
| P4 | **Replay safety** | Reconnect replay is deterministic and convergent. No stale state leaks across sessions. |
| P5 | **Runtime preservation** | Frozen systems remain untouched. Semantic lane is parser-isolated and data-path-isolated. |
| P6 | **Performance** | Acceptable for realtime use (hierarchy events are low-frequency, so this is the lowest priority). |

### 1.2 Correctness Over Optimization

```
HIERARCHY IMPLEMENTATION CONTRACT:

1. If it is not deterministic, it is not correct.
2. If it is not observable, it is not debuggable.
3. If it is not bounded, it is not safe.
4. If it modifies a frozen system, it is not allowed.
5. Optimization is deferred until correctness is proven.
```

### 1.3 Incremental Implementation

Every stage of implementation must:
- Be independently testable before the next stage begins
- Have a defined pass criterion before proceeding
- Have a rollback path if the criteria are not met
- Add observability (logs + counters) at the same time as functionality

### 1.4 What We Are Building

```
The hierarchy semantic lane is a PURELY ADDITIVE system:

NEW files:   None (additions to existing files only)
NEW types:   PT_Hierarchy = 0x0D
             FPendingHierarchyAttachment
             FHierarchySequenceTracker
             FScopedHierarchySuppression
             (plus counters, logs, and profiler scopes)

MODIFIED files (planned):
  SyncTypes.h:       PT_Hierarchy constant, FHierarchySequenceTracker, counters
  UELiveSyncSubsystem.h:  FPendingHierarchyAttachment struct, deferred queue, function declarations
  UELiveSyncSubsystem.cpp: New case branch, HandleHierarchy, ResolveHierarchyAttachments,
                           Tracker clear points in StopNetworkThread/ConsoleReset
  network.py:        PT_HIERARCHY constant, serialize_hierarchy()
  sync.py:           _last_parent_guid diff, _hierarchy_sequences, snapshot ordering

FROZEN files (NOT modified):
  LiveSyncQueue.h, PendingAssetQueue.h, LiveSyncRunnable.h/cpp,
  AssetIdentityTypes.h, SLiveSyncStatusWidget.*, SLiveSyncDiagnosticsWidget.*,
  UELiveSyncEditorModule.*
```

---

## 2. Runtime Preservation Checklist

### 2.1 Frozen System Declaration

The following systems are **FROZEN** and must NOT be modified, extended,
refactored, or inspected at runtime by the hierarchy semantic lane:

| System | File(s) | Risk if Modified |
|--------|---------|------------------|
| Packet parser (version dispatch, magic, header parsing, FNV validation) | `UELiveSyncSubsystem.cpp` (ProcessBinaryPacket outer dispatch) | Backward compat breakage, malformed packet crashes |
| Tick pipeline ordering | `UELiveSyncSubsystem.cpp` (main Tick) | Transform-before-spawn races; BEGIN/END imbalance |
| Queue ownership (FLiveSyncQueue) | `LiveSyncQueue.h` | Data races, queue corruption, use-after-free |
| Queue ownership (FLiveSyncPendingAssetQueue) | `PendingAssetQueue.h` | Data races, asset resolution corruption |
| Network thread lifecycle & shutdown order | `LiveSyncRunnable.h/cpp` | Game thread deadlock (Linux: missing Shutdown before Close) |
| Thread ownership (network enqueue only) | All runtime files | Cross-thread UObject access crashes |
| FSyncTransformState layout (incl. ParentGuid, bHasParent, bPendingSceneGraphWrite) | `SyncTypes.h` | Wire format incompatibility, transform evaluation corruption |
| 24-byte header layout | `SyncTypes.h` (implicit) | Protocol breakage across all versions |
| InterpolateTransforms | `UELiveSyncSubsystem.cpp` | Transform drift for attached children |
| UpdateTargetTransform | `UELiveSyncSubsystem.cpp` | Transform ingestion corruption |
| AttachToParent | `UELiveSyncSubsystem.cpp` | Runtime hierarchy corruption |
| ResolvePendingAttachments | `UELiveSyncSubsystem.cpp` | Runtime deferred attachment corruption |
| DetachFromParent | `UELiveSyncSubsystem.cpp` | Runtime detach corruption |
| PendingAttachments array | `UELiveSyncSubsystem.h` | Runtime deferred queue — FROZEN |
| Heartbeat timeout (15s threshold) | `LiveSyncRunnable.cpp` | Connection state machine desync |
| BEGIN/END tracing at every Tick stage | `UELiveSyncSubsystem.cpp` | Removing would blind future debugging |

### 2.2 Pre-Implementation Audit Checklist

Before ANY implementation begins, verify the following:

| # | Check | Status |
|---|-------|--------|
| A1 | Vertical slice design reviewed and all 9 findings addressed | PENDING |
| A2 | No modifications to frozen system list (above) are required by the design | CONFIRMED — zero frozen-zone modifications |
| A3 | PT_Hierarchy type byte (0x0D) does not conflict with existing types | PENDING (verify SyncTypes.h) |
| A4 | All counters defined in design doc (7 total) | PENDING |
| A5 | All log prefixes defined and consistent with conventions | PENDING |
| A6 | All profiler scope names defined and follow naming convention | PENDING |
| A7 | Rollback strategy documented and understood | PENDING (this document) |
| A8 | Test framework ready for hierarchy validation tests | PENDING |
| A9 | Architecture review sign-off obtained | PENDING |

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
Stage 1:  PT_Hierarchy enum reservation (SyncTypes.h + network.py)
Stage 2:  FNV signature update (both sides)
Stage 3:  FHierarchySequenceTracker + counters (UE, SyncTypes.h)
Stage 4:  Parser-isolated packet branch (UE, ProcessBinaryPacket)
            └── No handler yet — just parse + sequence check + reject
Stage 5:  Replay rejection (UE, HandleHierarchy stub)
            └── Sequence check + origin tagging + logging
Stage 6:  Basic attach/detach (UE, HandleHierarchy full)
            └── AttachToActor + DetachFromActor + existing-parent short-circuit
Stage 7:  Deferred queue + ResolveHierarchyAttachments (UE)
            └── PendingHierarchyAttachments + retry + timeout + FINDING-001/002
Stage 8:  Orphan lifecycle stabilization (UE)
            └── EOrphanState enum (DEFERRED/RETRYING/RESOLVED/EVICTED/STALE_REJECTED), enhanced state logging
Stage 9:  Cycle detection (UE)
            └── WouldCreateHierarchyCycle() — self-cycle, direct 2-cycle, indirect N-cycle, depth-256 bound
Stage 10: Blender hierarchy detection (sync.py)
            └── _last_parent_guid diff — attach/detach/reparent via `guid in _last_parent_guid` check
Stage 11: Blender serialization (network.py)
            └── serialize_hierarchy() — 44-byte fixed payload, per-GUID _hierarchy_sequences monotonic counter
Stage 12: Depth-sort snapshot ordering (sync.py)
            └── _get_parent_depth() bounded walk, _snapshot_depth_cache memoization, O(N log N) sort
Stage 13: Integration tests (tests/phase6_hierarchy_validation.py)
            └── 25 new tests (Stages 10–12), 97 total standalone tests
Stage 13: Soak + stress tests
            └── Long-duration, storm, mixed traffic, reconnect
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
  │                       Stage 6 (attach/detach) ←──┤
  │                       Stage 7 (deferred queue)   │
  │                       Stage 8 (orphan)           │
  │                       Stage 9 (cycle)            │
  │                       Stage 10 (Blender detect)  │
  │                       Stage 11 (serialization)   │
  │                       Stage 12 (depth-sort)       │
  │                                                 │
  └── Stage 13 (stabilization) ────────────────────────┘
```

### 3.3 Prohibited Approaches

| Approach | Why Prohibited |
|----------|----------------|
| "Implement everything simultaneously" | No incremental verification. If something breaks, root cause is ambiguous. |
| "Skip the parser isolation stage" | Parser is the entry point. Without it, nothing else can be tested. |
| "Add Blender emission before UE handling" | Blender sends packets that UE doesn't understand → log spam, malformed packet counters. UE must be ready first. |
| "Implement cycle detection last" | Without cycle detection, a malformed Blender payload could corrupt the scene graph. Cycle detection was Stage 9 (post-deferred queue, verified in isolation). |
| "Optimize before correctness" | No perf work until all stages pass validation. |

### 3.4 Stage Entry/Exit Criteria

Each stage has explicit entry and exit criteria:

```
Stage N:
  Entry: All stages < N are complete and passing validation.
  Exit: Stage N functionality implemented, observable, tested.
  Rollback: If Stage N fails validation for >2 consecutive attempts,
            roll back to Stage N-1 state and re-verify.
```

---

## 4. Deferred Queue Ownership Plan

### 4.1 Queue Definition

```cpp
// New struct in UELiveSyncSubsystem.h
struct FPendingHierarchyAttachment
{
    FGuid ChildGuid;        // The child to attach (must exist in ActorCache)
    FGuid ParentGuid;       // The intended parent (may not exist yet)
    int32 RetryFrames;      // Number of retry attempts so far (0..60)
    double CreatedTime;     // FPlatformTime::Seconds() when deferred
    uint32 Sequence;        // Original monotonic sequence from packet
};
```

### 4.2 Ownership

| Property | Specification |
|----------|---------------|
| **Data structure** | `TArray<FPendingHierarchyAttachment> PendingHierarchyAttachments` — member of `UUELiveSyncSubsystem` |
| **Owner thread** | Game thread only. All mutations occur on game thread. |
| **Read access** | Game thread only (inside `ResolveHierarchyAttachments()`) |
| **Write access** | Game thread only. Added by `HandleHierarchy()` on deferral, removed on resolution/timeout/clear. |
| **Iteration** | `ResolveHierarchyAttachments()` — called once per Tick (see FINDING-009 for position) |
| **Max size** | 2048 entries (matching `PendingAssetQueue` bound) |

### 4.3 Mutation Points

| Mutation | When | Where |
|----------|------|-------|
| **Add** | PT_Hierarchy packet parsed → parent not in ActorCache → `HandleHierarchy()` defers | `HandleHierarchy()` — after sequence check passes, before cycle check |
| **Update** (dedup) | New PT_Hierarchy for same child with higher seq while deferred | `HandleHierarchy()` — see FINDING-002 |
| **Remove** (resolved) | Deferred entry's parent found in ActorCache during retry | `ResolveHierarchyAttachments()` |
| **Remove** (timeout) | Entry exceeds 60 retries or 5s wall-clock | `ResolveHierarchyAttachments()` |
| **Remove** (same child, new attach) | New PT_Hierarchy for same child resolves immediately (parent found) | `HandleHierarchy()` — remove stale deferred entry before applying new attach |
| **Clear** (reconnect) | `StopNetworkThread()` or `ConsoleReset()` or `HandleEndSnapshot()` | Dedicated clear call |
| **Clear** (delete) | Child or parent GUID deleted | Remove matching entries in delete handler |

### 4.4 Deduplication Rules

When a new PT_Hierarchy packet arrives for a child that already has a pending
deferred entry:

```
if (FPendingHierarchyAttachment* Existing = FindPending(ChildGuid))
{
    if (Seq > Existing->Sequence)
    {
        // Newer sequence — update existing entry, reset retry.
        Existing->Sequence = Seq;
        Existing->ParentGuid = ParentGuid;  // Parent may have changed
        Existing->RetryFrames = 0;
        Existing->CreatedTime = Now;
        Log: [HIERARCHY][ORPHAN] Updated deferred: child=%s (seq %u → %u)
    }
    else
    {
        // Stale or duplicate — skip silently.
        Log: [HIERARCHY][STALE] Deferred update skipped: child=%s (incoming %u <= existing %u)
    }
    return; // Don't add duplicate
}
```

### 4.5 Eviction Policy

| Eviction Trigger | Behavior | Log |
|------------------|----------|-----|
| **Queue overflow** (2048+1) | O(1) FIFO eviction: `RemoveAt(0)`. Increment `HierarchyOrphans`. | `[HIERARCHY][ORPHAN] Deferred queue overflow — evicting child=%s` |
| **Timeout** (60 frames / 5s) | Remove entry. Increment `HierarchyOrphans`. Child retains current parent. | `[HIERARCHY][ORPHAN] TIMEOUT: child=%s parent=%s — evicting after %d retries` |
| **Resolution** (parent found) | Remove entry. Increment `HierarchyDeferredResolved`. Apply attachment. | `[HIERARCHY][ORPHAN] Resolved: child=%s parent=%s (after %d retries, seq=%u)` |
| **Reconnect** | `Empty()` entire queue. | `[HIERARCHY] PendingHierarchyAttachments cleared (reason=reconnect)` |

### 4.6 Reconnect Cleanup

```
StopNetworkThread():
    PendingHierarchyAttachments.Empty();
    FHierarchySequenceTracker.LastSequence.Empty();

ConsoleReset():
    PendingHierarchyAttachments.Empty();
    FHierarchySequenceTracker.LastSequence.Empty();
    All 7 hierarchy counters = 0;

HandleEndSnapshot():
    PendingHierarchyAttachments.Empty();
    // Tracker is NOT cleared here — it persists across snapshot boundaries
    // within the same session.
```

### 4.7 Key Design Invariant

```
The deferred queue NEVER persists across sessions.

On every reconnect, the queue is emptied and re-populated by the fresh
snapshot replay. No orphan state leaks between sessions.
```

---

## 5. Replay Tracker Integration Plan

### 5.1 FHierarchySequenceTracker

```cpp
// New struct in SyncTypes.h
struct FHierarchySequenceTracker
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

// Global instance (same pattern as GRenameSequences, GVisibilitySequences)
extern FHierarchySequenceTracker GHierarchySequences;
```

### 5.2 Integration Points

| Point | Operation | Why |
|-------|-----------|-----|
| `HandleHierarchy()` — before attach/detach | `IsStaleOrDuplicate()` check | Reject stale/duplicate live packets |
| `HandleHierarchy()` — after successful attach | `Update()` | Record the applied sequence |
| `HandleHierarchy()` — on detach-to-root | `Update()` | Detach is also a sequenceable event |
| `ResolveHierarchyAttachments()` — before applying deferred resolve | `IsStaleOrDuplicate()` check | FINDING-001: re-validate after deferral |
| `ResolveHierarchyAttachments()` — on successful deferred resolve | `Update()` | Record sequence from deferred entry |
| `StopNetworkThread()` | `Clear()` | Fresh state on next connection |
| `ConsoleReset()` | `Clear()` + zero counters | Full reset |

### 5.3 Stale Rejection During Resolution (FINDING-001 Mitigation)

This is the most critical replay safety measure. During deferred resolution,
the tracker may have advanced while the entry was pending.

```cpp
void UUELiveSyncSubsystem::ResolveHierarchyAttachments()
{
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ResolveHierarchyAttachments);
    double Now = FPlatformTime::Seconds();

    TArray<FPendingHierarchyAttachment> Remaining;

    for (const FPendingHierarchyAttachment& Entry : PendingHierarchyAttachments)
    {
        // ---- FINDING-001: Re-validate sequence against tracker ----
        if (GHierarchySequences.IsStaleOrDuplicate(Entry.ChildGuid, Entry.Sequence))
        {
            // Tracker advanced while deferred — this entry is stale.
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[HIERARCHY][STALE] Deferred resolution stale: child=%s (deferred seq=%u, current seq=%u)"),
                *Entry.ChildGuid.ToString(EGuidFormats::Digits),
                Entry.Sequence,
                GHierarchySequences.GetLastSeq(Entry.ChildGuid));
            Stats.HierarchyStaleRejections++;
            continue; // Drop — do NOT apply, do NOT re-queue
        }

        // ... timeout check, parent lookup, attach logic ...
    }

    PendingHierarchyAttachments = MoveTemp(Remaining);
}
```

### 5.4 Tracker Bounded Eviction

When the tracker reaches 2048 entries, the oldest entry (arbitrary — `TMap`
iterator order) is evicted. This means the tracker may forget a GUID's last
sequence. The consequence is that a future packet for that GUID with a
previously-applied sequence would be accepted (not rejected as stale).

**Impact**: LOW. 2048 is a generous bound. Scene-wide GUID counts rarely
exceed 500. The evicted GUID is likely a deleted or long-since-settled
object. Re-applying a stale hierarchy event for it would be a no-op
(AttachToActor with same parent is idempotent).

**Mitigation**: None beyond the 2048 bound. This is an accepted limitation
shared with rename and visibility trackers.

### 5.5 FINDING-001 Pre-Implementation Checklist

| # | Item | Status |
|---|------|--------|
| T1 | `IsStaleOrDuplicate()` check in `ResolveHierarchyAttachments()` before applying deferred resolve | PENDING |
| T2 | `[STALE]` log message for stale deferred resolution | PENDING |
| T3 | `HierarchyStaleRejections++` on stale deferred resolution | PENDING |
| T4 | Do NOT re-queue stale deferred entries (drop permanently) | PENDING |
| T5 | `Update()` call after successful deferred resolve | PENDING |

---

## 6. Cycle Detection Implementation Plan

### 6.1 Validation Order

Cycle detection must follow this exact order:

```
HandleHierarchy(ChildGuid, ParentGuid, ...):
  │
  ├── 1. Self-parent check (ChildGuid == ParentGuid?)
  │     → O(1). Reject immediately. No deferral.
  │     → [HIERARCHY][CYCLE] Self-parent rejected.
  │
  ├── 2. Child exists in ActorCache?
  │     → O(1). Reject immediately. No deferral.
  │     → [HIERARCHY] Rejected: unknown child.
  │     (This is technically not a cycle check, but it runs before cycle detection
  │      because cycle detection requires the child AActor*.)
  │
  ├── 3. Parent GUID all-zero? (detach-to-root)
  │     → No cycle possible. Apply immediately.
  │     → Skip all cycle checks.
  │
  ├── 4. Parent exists in ActorCache?
  │     → NO: Defer. Cycle check will run when resolved.
  │     → YES: Continue to cycle check.
  │
  ├── 5. Parent chain walk (WouldCreateCycle):
  │     ┌─ 5a. GetParentAttachmentActor(ParentActor) → Current
  │     ├─ 5b. While Current != nullptr && Depth < MAX_HIERARCHY_DEPTH:
  │     │     if Current == ChildActor: CYCLE → reject.
  │     │     Current = GetAttachParentActor(Current)
  │     │     Depth++
  │     ├─ 5c. If Depth == MAX_HIERARCHY_DEPTH: assume cycle → reject.
  │     └─ 5d. No cycle found → apply attachment.
  │
  └── 6. Apply AttachToActor(KeepWorldTransform).
```

### 6.2 Cycle Detection Function

```cpp
static bool WouldCreateCycle(AActor* Child, AActor* Parent)
{
    // Self-parent check (belt-and-suspenders — already checked at GUID level)
    if (Child == Parent)
        return true;

    // Walk parent chain from Parent up to root, checking for Child
    AActor* Current = Parent->GetAttachParentActor();
    int32 Depth = 0;

    while (Current != nullptr && Depth < MAX_HIERARCHY_DEPTH)
    {
        if (Current == Child)
            return true;  // Cycle: Parent is a descendant of Child

        Current = Current->GetAttachParentActor();
        Depth++;
    }

    // Depth limit reached — assume cycle (conservative)
    if (Depth >= MAX_HIERARCHY_DEPTH)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[HIERARCHY][CYCLE] Depth limit exceeded (%d): child=%s parent=%s — assuming cycle"),
            MAX_HIERARCHY_DEPTH, ...);
        return true;
    }

    return false;  // No cycle
}
```

### 6.3 MAX_HIERARCHY_DEPTH

| Property | Value |
|----------|-------|
| Constant | `MAX_HIERARCHY_DEPTH = 256` |
| Rationale | Prevents infinite loop on corrupted runtime attachment state. No real scene has 256 hierarchy levels. |
| Behavior at limit | Assume cycle → reject attachment. Log warning. Increment `HierarchyCycles`. |
| Test coverage | Test with depth=257 to verify limit is enforced. |

### 6.4 Prohibited Patterns

| Pattern | Why Prohibited |
|---------|----------------|
| Recursive graph search (DFS with visited set) | Not needed. Parent chain walk is O(depth) and bounded. DFS over the entire graph would be O(N) per packet and risk stack overflow. |
| Cycle detection during deferral | Cycles do not resolve with time. If a cycle is detected during the parent-walk at resolution time, it is still a cycle. But cycle check is deferred until parent exists — see below. |
| Implicit graph repair | No auto-detach, no auto-reparent-to-root, no back-edge removal. |
| Cycle detection on intent-chain | We walk the RUNTIME parent chain, not the pending-intent chain. See FINDING-006. Accepted limitation. |

### 6.5 Cycle Detection During Deferred Resolution

When a deferred entry resolves, the cycle check runs at that point:

```
ResolveHierarchyAttachments():
  ├── Parent found. Child exists.
  ├── WouldCreateCycle(Child, Parent)?
  │     YES → Do NOT attach. Log [CYCLE] at deferred resolution time.
  │           Increment HierarchyCycles. Remove entry. Do NOT re-queue.
  │     NO  → Proceed with AttachToActor.
  └── (The cycle check at resolution time uses the CURRENT runtime parent chain,
       which may differ from the parent chain at original deferral time.
       This is correct — the runtime graph may have changed in the interim.)
```

---

## 7. Parser Isolation Plan

### 7.1 Parser Structure

The hierarchy parser MUST be:

1. An isolated `case PT_Hierarchy:` branch in `ProcessBinaryPacket`
2. After all existing case branches (by type byte ordering: `0x0D` > `0x0C`)
3. NOT modifying any existing case branch
4. NOT entering `FLiveSyncQueue` (hierarchy packets are parsed and handled
   immediately, not enqueued as FLiveSyncPacket variants)

### 7.2 Parser Pseudocode

```cpp
// In ProcessBinaryPacket, after the last existing case branch:
case PT_Hierarchy:
{
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessHierarchyPackets);

    constexpr int32 ObjSize = 44;  // 16+16+4+8
    const int32 Count = PayloadSize / ObjSize;

    // ---- MALFORMED PACKET CHECKS ----
    if (PayloadSize % ObjSize != 0 || Count == 0)
    {
        Stats.MalformedPackets++;
        UE_LOG(LogLiveSync, Warning,
            TEXT("[HIERARCHY] Malformed packet — payload %d bytes (expected multiple of %d)"),
            PayloadSize, ObjSize);
        return;
    }

    if (Count > MAX_OBJECTS_PER_BATCH)  // 1024
    {
        Stats.MalformedPackets++;
        UE_LOG(LogLiveSync, Warning,
            TEXT("[HIERARCHY] Malformed packet — batch too large: %d objects (max %d)"),
            Count, MAX_OBJECTS_PER_BATCH);
        return;
    }

    // ---- PER-OBJECT PARSE LOOP ----
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
```

### 7.3 Malformed Packet Handling Checklist

| # | Check | Behavior |
|---|-------|----------|
| M1 | Payload size not a multiple of 44 bytes | Reject entire batch. `Stats.MalformedPackets++`. Warning log. |
| M2 | Object count == 0 | Reject. `Stats.MalformedPackets++`. Warning log. |
| M3 | Object count > MAX_OBJECTS_PER_BATCH (1024) | Reject. `Stats.MalformedPackets++`. Warning log. |
| M4 | Child GUID all-zero (invalid) | Reject single object. `HierarchyStaleRejections++`. Warning log. Continue to next object. |
| M5 | Parent GUID all-zero | Valid — detach-to-root semantics. Process normally. |
| M6 | Partial batch truncation (first obj valid, second truncated) | First object processed. Second detected by M1 on next TCP recv. |
| M7 | Boundary overflow during Memcpy | Guarded by M1 — if payload % 44 == 0, all 44-byte strides are in-bounds. |

### 7.4 Prohibited Patterns

| Pattern | Why Prohibited |
|---------|----------------|
| Entering hierarchy packets into FLiveSyncQueue | Semantic events are NOT transform state. FLiveSyncQueue is for transform MPSC. Hierachy packets are parsed and handled immediately on the game thread. |
| Modifying existing case branches | Each semantic lane has its own isolated case branch. No `else if` chains. |
| Reusing FLiveSyncPacket union for hierarchy | Hierarchy has its own wire format (44 bytes fixed). Parsing into FLiveSyncPacket would require extending the union, which is FROZEN. |
| Cross-packet coupling during parse | Each packet is parsed independently. No batch-level state machine. |

---

## 8. Runtime Interaction Plan

### 8.1 Interaction Boundaries

The hierarchy semantic lane interacts with runtime systems through
**well-defined, read-only interfaces**:

```
┌─────────────────────────────────────────────────────────────────────┐
│                     ALLOWED RUNTIME ACCESS                            │
│                                                                      │
│  Read-only queries:                                                   │
│    • FindActorFast(Guid) → AActor* (ActorCache lookup)               │
│    • GetAttachParentActor() → AActor* (UE engine API)                │
│    • GetAttachParentActor() in chain walk for cycle detection        │
│                                                                      │
│  Write operations (direct engine API, not frozen helpers):           │
│    • AttachToActor(Parent, KeepWorldTransform)                       │
│    • DetachFromActor(KeepWorldTransform)                             │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                     FORBIDDEN RUNTIME ACCESS                          │
│                                                                      │
│  • FSyncTransformState::ParentGuid (read-only for cycle walk — MUST  │
│    NOT write)                                                        │
│  • FSyncTransformState::bHasParent                                   │
│  • FSyncTransformState::bPendingSceneGraphWrite                      │
│  • FSyncTransformState::LocalTargetLocation/Rotation/Scale           │
│  • InterpolateTransforms()                                           │
│  • ResolvePendingAttachments()                                       │
│  • AttachToParent()                                                  │
│  • PendingAttachments (runtime array)                                │
│  • UpdateTargetTransform()                                           │
│  • Any frozen system (see §2.1)                                      │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 8.2 Semantic Lane Request Model

The semantic lane does NOT "own" the scene graph. It **requests** attachment
intent through the same UE engine API (`AttachToActor`) that any other system
would use.

```
Semantic Lane → "I intend A to be a child of B"
  → Existence check: does B exist?
  → Cycle check: would this create a cycle?
  → AttachToActor(B, KeepWorldTransform) — REQUEST
  → UE engine evaluates and applies the attachment

The semantic lane does NOT:
  - Manage the parent-child lifecycle
  - Update interpolation state
  - Track scene graph topology across Ticks (except via tracker)
  - Own transform evaluation
  - Repair inconsistent graph state
```

### 8.3 Tick Pipeline Position

```
Current Tick pipeline (FROZEN):
  1. ProcessQueuedPackets()         ← PT_Hierarchy parsed here
  2. ResolvePendingAttachments()    ← Runtime deferred (FROZEN)
  3. InterpolateTransforms()        ← FROZEN
  4. ResolveAssetIdentities()       ← FROZEN
  5. PurgeStaleActors()             ← FROZEN
  6. ...remaining pipeline...       ← FROZEN

ADDED by Phase 6D (between 2 and 3):
  2a. ResolveHierarchyAttachments() ← Semantic lane deferred
      ├── Iterates PendingHierarchyAttachments
      ├── Checks timeout (60 frames / 5s)
      ├── Re-checks sequence tracker (FINDING-001)
      ├── Looks up parent in ActorCache
      ├── If found: cycle check → AttachToActor
      └── If not found: re-queue or evict
```

**Why between 2 and 3?**
- Runtime deferred attachments resolve first (step 2)
- Then semantic lane deferred attachments resolve (step 2a)
- Then interpolation runs (step 3) using the final attachment state
- This ensures the interpolation loop sees the correct parent-child
  relationships before computing local/world transforms

### 8.4 Non-MESH Parent Handling

If a MESH child's parent is a non-MESH object (camera, light, armature):

1. The parent will never be created in UE (MESH-only filter)
2. The hierarchy event enters deferred retry
3. After 60 frames / 5 seconds → orphan timeout
4. Child remains root in UE

**This is an accepted limitation.** The MESH-only filter is a Phase 5
invariant. Expanding sync scope to non-MESH objects is deferred.

**Observability**: The orphan timeout log (`[HIERARCHY][ORPHAN] TIMEOUT`)
surfaces this condition. The user can identify that their parent is
a non-MESH object by checking the parent GUID in the log.

---

## 9. Observability Integration Plan

### 9.1 Profiler Scopes

| Scope Name | Location | Condition | When Added |
|------------|----------|-----------|------------|
| `UELiveSync_ProcessHierarchyPackets` | `ProcessBinaryPacket` — `case PT_Hierarchy:` | Always | Stage 4 |
| `UELiveSync_HandleHierarchy` | `HandleHierarchy()` function entry | Always | Stage 6 |
| `UELiveSync_ResolveHierarchyAttachments` | `ResolveHierarchyAttachments()` function entry | Always | Stage 7 |

### 9.2 Log Statements

| Log | Prefix | Level | Stage |
|-----|--------|-------|-------|
| Parser — batch parsed | `[HIERARCHY]` | Verbose | 4 |
| Parser — malformed packet | `[HIERARCHY]` | Warning | 4 |
| Apply attach | `[HIERARCHY][ATTACH]` | Verbose | 6 |
| Apply detach (to root) | `[HIERARCHY][DETACH]` | Verbose | 6 |
| Apply detach (was attached to X) | `[HIERARCHY][DETACH]` | Verbose | 6 |
| Skip — already attached | `[HIERARCHY][ATTACH]` | Verbose | 6 |
| Deferred (parent missing) | `[HIERARCHY][ORPHAN]` | Log | 7 |
| Updated deferred entry | `[HIERARCHY][ORPHAN]` | Verbose | 7 |
| Deferred queue overflow eviction | `[HIERARCHY][ORPHAN]` | Warning | 7 |
| Deferred resolution (success) | `[HIERARCHY][ORPHAN]` | Log | 7 |
| Deferred resolution stale (FINDING-001) | `[HIERARCHY][STALE]` | Verbose | 7 |
| Orphan timeout | `[HIERARCHY][ORPHAN]` | Warning | 8 |
| Self-cycle | `[HIERARCHY][CYCLE]` | Warning | 9 |
| Direct cycle | `[HIERARCHY][CYCLE]` | Warning | 9 |
| Chain cycle | `[HIERARCHY][CYCLE]` | Warning | 9 |
| Depth limit cycle | `[HIERARCHY][CYCLE]` | Warning | 9 |
| Stale seq (live packet) | `[HIERARCHY][STALE]` | Verbose | 5 |
| Stale deferred update (new seq <= deferred seq) | `[HIERARCHY][STALE]` | Verbose | 7 |
| Replay apply | `[HIERARCHY][REPLAY]` | Verbose | 5 |
| Replay skip (stale) | `[HIERARCHY][REPLAY]` | Verbose | 5 |
| Unkonwn child GUID | `[HIERARCHY]` | Warning | 6 |
| Suppression enter | `[HIERARCHY][SUPPRESSION]` | Verbose | 6 |
| Suppression exit | `[HIERARCHY][SUPPRESSION]` | Verbose | 6 |
| Tracker clear | `[HIERARCHY]` | Log | 3 |
| EndSnapshot summary | `[HIERARCHY]` | Log | 7 |

### 9.3 Counters

| Counter | Location | Type | Stage | When Incremented |
|---------|----------|------|-------|------------------|
| `HierarchyProcessed` | `FLiveSyncStats` | `std::atomic<int32>` (relaxed) | 6 | Every hierarchy event accepted and applied (attach or detach, live) |
| `HierarchyStaleRejections` | `FLiveSyncStats` | `std::atomic<int32>` (relaxed) | 5 | Every stale/duplicate sequence rejection (live + deferred resolution) |
| `HierarchyReplayApplied` | `FLiveSyncStats` | `std::atomic<int32>` (relaxed) | 5 | Every hierarchy event applied during snapshot replay |
| `HierarchyReplaySkipped` | `FLiveSyncStats` | `std::atomic<int32>` (relaxed) | 5 | Every hierarchy event skipped during replay (stale/duplicate) |
| `HierarchyOrphans` | `FLiveSyncStats` | `std::atomic<int32>` (relaxed) | 8 | Every deferred entry that times out or is evicted (overflow) |
| `HierarchyCycles` | `FLiveSyncStats` | `std::atomic<int32>` (relaxed) | 9 | Every cycle detection (self, direct, chain, depth-limit) |
| `HierarchyDeferredResolved` | `FLiveSyncStats` | `std::atomic<int32>` (relaxed) | 8 | Every deferred entry that successfully resolves |

**Total: 7 counters.**

### 9.4 Counter Initialization and Reset

```
ConsoleReset():
    Stats.HierarchyProcessed.store(0, std::memory_order_relaxed);
    Stats.HierarchyStaleRejections.store(0, std::memory_order_relaxed);
    Stats.HierarchyReplayApplied.store(0, std::memory_order_relaxed);
    Stats.HierarchyReplaySkipped.store(0, std::memory_order_relaxed);
    Stats.HierarchyOrphans.store(0, std::memory_order_relaxed);
    Stats.HierarchyCycles.store(0, std::memory_order_relaxed);
    Stats.HierarchyDeferredResolved.store(0, std::memory_order_relaxed);
```

### 9.5 Diagnostics Widget Integration

The 7 hierarchy counters should be added to the `SLiveSyncDiagnosticsWidget`
display (when it exists). No modification to the widget's update mechanism —
counters are `std::memory_order_relaxed` display values, read atomically.

---

## 10. Validation Plan

### 10.1 Implementation-Phase Validation Stages

Each stage has a validation gate before the next stage begins.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    VALIDATION GATES                                   │
│                                                                      │
│  Stage 1-3 (enum, FNV, tracker):                                     │
│    Gate A: Unit test — enum value matches, FNV compiles,             │
│             tracker stores/retrieves correctly                        │
│    → Proceed to Stage 4                                              │
│                                                                      │
│  Stage 4-5 (parser, replay rejection):                               │
│    Gate B: Parser test — send malformed PT_Hierarchy, verify         │
│             rejection. Send valid PT_Hierarchy, verify parse.         │
│             Reject test — verify sequence check rejects <=.           │
│    → Proceed to Stage 6                                              │
│                                                                      │
│  Stage 6 (basic attach/detach):                                      │
│    Gate C: Attach test — send PT_Hierarchy, verify AttachToActor.    │
│             Detach test — send all-zero ParentGuid, verify            │
│             DetachFromActor. No-op test — verify skip if already      │
│             attached.                                                 │
│    → Proceed to Stage 7                                              │
│                                                                      │
│  Stage 7-8 (deferred queue, orphan):                                 │
│    Gate D: Defer test — send PT_Hierarchy with missing parent,       │
│             verify deferred entry created. Resolution test — add     │
│             parent, verify deferred entry resolves. Timeout test —   │
│             wait 60 frames, verify orphan timeout. Overflow test —   │
│             2049 deferred entries, verify oldest evicted.            │
│             ═══ STATUS: COMPLETE (Stage 7-8, 72 tests pass) ═══     │
│    → Proceed to Stage 9                                              │
│                                                                      │
│  Stage 9 (cycle detection):                                          │
│    Gate E: Self-cycle test — send ChildGuid==ParentGuid, verify      │
│             rejection. Direct cycle test — create A→B, send B→A,     │
│             verify rejection. Chain cycle test — create A→B→C,       │
│             send C→A, verify rejection. Depth limit test — create    │
│             257-level hierarchy (synthetic), verify limit.           │
│             ═══ STATUS: COMPLETE (Stage 9, 72 tests pass) ═══       │
│    → Proceed to Stage 10 (observability)                             │
│                                                                      │
│  Stage 10 (Blender detection):                                       │
│    Gate F: _last_parent_guid diff detects attach/detach/reparent.    │
│             First-send correctly initializes without emission.        │
│             guid in _last_parent_guid disambiguates "never seen"      │
│             from "is root". 8 standalone detection tests pass.        │
│             ═══ STATUS: COMPLETE (Stage 10, 8 tests) ═══            │
│    → Proceed to Stage 11                                             │
│                                                                      │
│  Stage 11 (Blender serialization):                                   │
│    Gate G: serialize_hierarchy() produces 44-byte fixed payload.     │
│             Per-GUID _hierarchy_sequences monotonic counter correct.  │
│             5 standalone sequence tests pass.                         │
│             ═══ STATUS: COMPLETE (Stage 11, 5 tests) ═══            │
│    → Proceed to Stage 12                                             │
│                                                                      │
│  Stage 12 (snapshot ordering):                                       │
│    Gate H: _get_parent_depth() bounded at 256, correct for roots,    │
│             children, nested, cycles, orphans. Depth-sort produces   │
│             parents-before-children. Stable sort preserves sibling   │
│             order. [SNAPSHOT][ORDER] verbose log present. Applied    │
│             to both reconnect-snapshot and rebind_all codepaths.     │
│             ═══ STATUS: COMPLETE (Stage 12, 12 tests) ═══           │
│    → Proceed to Stage 13                                             │
│                                                                      │
│  Stage 13 (soak/stress):                                             │
│    Gate I: 10-minute soak with mixed traffic. 1000-event storm.      │
│             5 reconnect cycles with hierarchy replay.                 │
│             No crashes, no memory leaks, no orphan drift.             │
│             ═══ STATUS: COMPLETE (Standalone 97/97, 7 integration    │
│             SKIP — UE required for soak) ═══                          │
│    → IMPLEMENTATION COMPLETE — HIERARCHY STABILIZED                   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 10.2 Regression Validation

After every gate, run existing test suites:

```
python3 tests/run_phase5_all.py       # Phase 5 regression
python3 tests/run_phase6_rename.py    # Rename regression
python3 tests/run_phase6_visibility.py # Visibility regression
python3 tests/phase6b_runtime_audit.py # Runtime audit
```

Any regressions → STOP → diagnose → roll back affected stages → re-verify.

### 10.3 Rollback Criteria Per Stage

| Stage | Rollback if |
|-------|-------------|
| 1-3 | Enum conflict, FNV mismatch, tracker corruption |
| 4-5 | Parser crashes on malformed input, sequence check allows duplicates |
| 6 | AttachToActor corrupts transform, detach crashes, no-op skips fails |
| 7-8 | Deferred queue memory leak, timeout doesn't fire, orphan counter wrong |
| 9 | Cycle detection misses a cycle, false-positive on valid deep hierarchy |
| 10 | Counter mismatch, log prefix missing, profiler scope missing |
| 11 | Blender packet invalid, snapshot ordering incorrect |
| 12-13 | Integration test failure, soak crash, reconnect corruption |

---

## 11. Rollback Strategy

### 11.1 Rollback Conditions

Implementation must roll back if ANY of the following conditions are detected:

| # | Condition | Severity | Detection Method |
|---|-----------|----------|-----------------|
| R1 | **Replay nondeterminism** — same packet sequence produces different attachment state across runs | P0 | Repeated replay test |
| R2 | **Orphan leakage** — orphan entries survive across reconnects or persist after timeout | P0 | Memory/heap check after reconnect |
| R3 | **Queue growth instability** — `PendingHierarchyAttachments` grows without bound | P0 | Watchdog on queue size > 2048 + warning |
| R4 | **Reconnect corruption** — attachment state differs between pre- and post-reconnect | P0 | DumpState comparison |
| R5 | **Runtime graph destabilization** — `InterpolateTransforms` produces NaN or drift for attached children after hierarchy event | P0 | Interpolation health check |
| R6 | **Tick regression** — hierarchy processing adds >1ms per Tick in any scenario | P1 | Performance counter |
| R7 | **Attachment oscillation** — child repeatedly attaches/detaches across consecutive Ticks | P1 | Log pattern detection |
| R8 | **Frozen system modification** — any code change to a FROZEN system | P0 | Git diff review |

### 11.2 Rollback Procedure

If any P0 condition (R1-R5, R8) is detected:

```
Step 1: FREEZE
  - Stop all hierarchy implementation work.
  - Disconnect Blender from UE.
  - Log: [HIERARCHY] ROLLBACK TRIGGERED: reason=%s

Step 2: DISABLE
  - Comment out the `case PT_Hierarchy:` branch in ProcessBinaryPacket.
  - Set PT_Hierarchy to an unreachable constant (commented out).
  - Remove HandleHierarchy() call from Tick pipeline (if called outside parser).
  - Do NOT modify any other code path.
  - Result: hierarchy events are silently ignored (no parse, no handling).

Step 3: PRESERVE
  - KEEP all hierarchy data structures (tracker, deferred queue, counters).
  - KEEP all hierarchy logs, scopes, and profiler stubs (if any).
  - REMOVE only the active processing paths.
  - Rationale: Preserved structures ease debugging and re-enablement.

Step 4: DIAGNOSE
  - Determine root cause of rollback condition.
  - Check if condition existed before hierarchy work (regression vs. new bug).
  - Check if condition is specific to a single stage or systemic.

Step 5: REMEDIATE
  - Fix the root cause.
  - Re-enable hierarchy processing.
  - Re-run all validation gates from the affected stage onward.
```

### 11.3 Protocol Compatibility During Rollback

If hierarchy processing is disabled (Step 2), the protocol remains compatible:

- UE ignores PT_Hierarchy packets (the case branch is dead code)
- Blender still sends PT_Hierarchy packets (cannot be disabled independently)
- No protocol version bump needed — type byte `0x0D` remains reserved
- FNV checksum should still include `0x0D` (it's reserved even if unhandled)
- All other semantic lanes (rename, visibility) continue unaffected

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
| **Replay tracker testable before deferred queue** | Stage 3/5 (tracker + replay rejection) can be tested with the parser stub. No deferred queue needed. |
| **Deferred queue testable before reconnect replay** | Stage 7 (deferred queue) can be tested with live packets. Reconnect replay comes in Stage 12. |
| **Cycle detection testable independently** | Stage 9 (cycle detection) can be tested with a synthetic ActorCache. No network connection needed. |
| **Observability is added alongside functionality** | Each stage adds its logs, counters, and scopes at the same time as the functionality — not retroactively. |

### 12.2 Risk Isolation

| Risk | Isolation Strategy |
|------|-------------------|
| Parser corrupts other packet types | Hierarchy parser is an independent `case` branch. It cannot affect transform/rename/visibility parsing. |
| Cycle detection slows Tick | Cycle detection is O(depth), max 256. Per-packet cost is negligible. |
| Deferred queue memory exhaustion | Bounded at 2048. O(1) eviction on overflow. Size is logged and monitored. |
| Blender emits invalid hierarchy packets | UE validates all fields: zero GUID, self-parent, non-existent child/ADC. Malformed packets are rejected. |
| Replay state corruption | Tracker cleared on every reconnect. No cross-session persistence. |
| Attachment oscillation | No-op check (FINDING-005) prevents unnecessary `AttachToActor` calls. Tick order ensures stability. |

### 12.3 Monitoring During Implementation

During implementation and testing, monitor:

| Metric | Warning Signal | Action |
|--------|---------------|--------|
| `PendingHierarchyAttachments.Num()` | > 1000 | Check for orphan storm or leak |
| `HierarchyOrphans` / `HierarchyProcessed` ratio | > 0.1 (10%) | Investigate Blender snapshot ordering compliance |
| `HierarchyCycles` > 0 during snapshot replay | Any | Cycle in replay = Blender bug or corrupted snapshot |
| Frame time impact | > 0.5ms added | Profile `UELiveSync_ProcessHierarchyPackets` and `UELiveSync_ResolveHierarchyAttachments` |
| `Stats.MalformedPackets` from hierarchy | > 5 | Investigate Blender serialization bug |

### 12.4 Failure Mode Analysis

| Failure Mode | Effect | Detection | Recovery |
|-------------|--------|-----------|----------|
| Orphan queue never clears | Memory leak. Children never attach. | Counters show zero resolves. Queue size grows. | Reconnect clears queue. |
| Cycle detection misses cycle | Scene graph corruption. Transform drift. | Visible in editor: child orbits world origin instead of parent. | Reconnect resets graph. User must fix hierarchy. |
| Sequence tracker evicts important GUID | Stale hierarchy event accepted (re-applied). | HierarchyProcessed counts an event that should have been skipped. | No-op (already attached). No corruption. |
| Deferred queue dedup fails (FINDING-002 missed) | Duplicate deferred entries for same child. | Log shows multiple `[ORPHAN] Deferred` for same child. | Both resolve to same attachment. Redundant but harmless. |
| AttachToActor called when already attached (FINDING-005 missed) | Extra scene graph mutation per hierarchy event. | Verbose log shows attach + skip pattern. | No corruption. Slight perf cost. |

---

## 13. Test Matrix Planning

### 13.1 Test Categories

| Category | Count | Stage | Priority |
|----------|-------|-------|----------|
| **Parser validation** | 4 | Stage 5 | High |
| **Replay validation** | 4 | Stage 6 | High |
| **Orphan validation** | 3 | Stage 8 | High |
| **Reconnect validation** | 3 | Stage 6 | High |
| **Cycle rejection validation** | 4 | Stage 9 | High |
| **Hierarchy detection (Blender)** | 8 | Stage 10 | High |
| **Per-GUID sequences (Blender)** | 5 | Stage 11 | High |
| **Depth-sort ordering** | 12 | Stage 12 | High |
| **Mixed lane validation** | 2 | Stage 13 | Medium |
| **Phase 5 regression** | (existing) | Stage 13 | Required |
| **Soak/stress validation** | 3 | Stage 13 | Required |
| **Total (new)** | **25** | | |

### 13.2 Test Descriptions

**Parser validation** (`tests/phase6_hierarchy_validation.py`):

| # | Test | Description |
|---|------|-------------|
| P1 | **Single attach** | Send PT_Hierarchy (child=A, parent=B). Verify A→B via AttachToActor. |
| P2 | **Single detach** | Send PT_Hierarchy (child=A, parent=null). Verify A detached to root. |
| P3 | **Chain attach** | Send A→B, then B→C, verify A→B→C chain. |
| P4 | **Chain detach** | Attach A→B, detach A. Verify A is root, B still exists. |

**Replay validation**:

| # | Test | Description |
|---|------|-------------|
| R1 | **Correct order replay** | Snapshot with parents before children. All attach immediately. |
| R2 | **Reverse order replay** | Snapshot with children before parents. Verify deferred retry resolves. |
| R3 | **Duplicate seq replay** | Same child, same seq twice. Verify second is rejected. |
| R4 | **Stale seq replay** | Seq=5 applied, seq=5 arrives again. Verify stale rejection. |

**Orphan validation**:

| # | Test | Description |
|---|------|-------------|
| O1 | **Orphan timeout** | Parent never arrives. Verify timeout after 60 frames. |
| O2 | **Orphan resolution** | Parent arrives within window. Verify attachment succeeds. |
| O3 | **Orphan dedup** | Two PT_Hierarchy for same child while deferred. Verify entry is updated, not duplicated. |

**Reconnect validation**:

| # | Test | Description |
|---|------|-------------|
| C1 | **Reconnect replay** | Disconnect+reconnect. Verify hierarchy re-established from snapshot. |
| C2 | **Reconnect stale cleanup** | Reconnect with changed hierarchy. Verify new hierarchy applies, stale state cleared. |
| C3 | **Reconnect orphan** | Reconnect with parent still missing. Verify re-orphan lifecycle. |

**Cycle rejection validation**:

| # | Test | Description |
|---|------|-------------|
| Y1 | **Self-parent** | Send child=A parent=A. Verify rejection + log + counter. |
| Y2 | **Direct cycle** | Create A→B, send B→A. Verify rejection. |
| Y3 | **Chain cycle** | Create A→B→C, send C→A. Verify rejection via parent walk. |
| Y4 | **Depth limit** | Create 257-level hierarchy (synthetic). Verify attachment at level 257 rejected. |

**Mixed lane validation**:

| # | Test | Description |
|---|------|-------------|
| M1 | **Transform + hierarchy** | Send transform and PT_Hierarchy for same child in same batch. Verify final state is correct. |
| M2 | **Rename + hierarchy** | Rename a parent, then re-parent a child to it. Verify both operations succeed. |

**Soak/stress validation** (`tests/phase6_hierarchy_soak.py`):

| # | Test | Description |
|---|------|-------------|
| S1 | **Long soak (10 min)** | Continuous hierarchy + transform traffic. No memory leak, no drift. |
| S2 | **Attach storm** | 300 simultaneous re-parent events. Verify all processed without packet loss. |
| S3 | **Reconnect storm** | 10 reconnect cycles with hierarchy replay. Verify consistent state. |

### 13.3 Test Framework Considerations

- Tests should use a mock `ActorCache` for UE-side testing without editor
- Blender-side tests should verify serialization output bytes match expected format
- Integration tests require UE editor listening on `:57000` (same as rename/visibility)
- Phase 5 regression tests must pass before any hierarchy test is considered passing
- Tests are added to `tests/run_phase6_hierarchy.py` runner script

---

## 14. Final Go/No-Go Gate

### 14.1 Implementation-Entry Criteria

Implementation of the hierarchy semantic lane may begin ONLY IF all of the
following criteria are met:

| # | Criterion | Verification | Status |
|---|-----------|-------------|--------|
| G1 | **Replay semantics frozen** | Replay scenarios (8) documented in design doc §2. All deterministic outcomes defined. | ✅ PASS (design §2) |
| G2 | **Orphan semantics frozen** | DEFERRED → ORPHANED lifecycle defined. Timeout behavior specified. Queue dedup defined. | ✅ PASS (design §6, plan §4) |
| G3 | **Authority boundaries frozen** | "Hierarchy replicates attachment intent only" — documented in scope lock §1, design §8. Transform/runtime wins. | ✅ PASS (scope lock §1, design §8.6) |
| G4 | **Cycle policy frozen** | Self/direct/chain/depth-limit all reject immediately. No auto-repair, no implicit detach. | ✅ PASS (design §7) |
| G5 | **Rollback strategy defined** | §11 of this document. 8 rollback conditions, 5-step procedure, re-enablement path. | ✅ PASS (this document §11) |
| G6 | **Observability complete** | 3 profiler scopes, 23 log statements, 7 counters, all prefixes defined. | ✅ PASS (plan §9) |
| G7 | **Runtime preservation checklist complete** | Frozen systems listed (§2.1). Pre-implementation audit checklist complete (§2.2). | ✅ PASS (plan §2) |
| G8 | **Architecture review findings addressed** | 9 findings from §14 of design doc. Mitigations defined in plan §5.3 (FINDING-001), §4.4 (FINDING-002), §4.5 (FINDING-003), §7.4 (FINDING-004 documented), §8.2 (FINDING-005), design §5.4 (FINDING-006 documented), plan §4.7 (FINDING-007 corrected), plan §9.3 (FINDING-008), plan §8.3 (FINDING-009). | ✅ PASS (all mitigations defined) |
| G9 | **Implementation sequence defined** | 14 stages with dependency graph, entry/exit criteria, rollback per stage. | ✅ PASS (plan §3) |
| G10 | **Test matrix defined** | ~23 tests across 8 categories. Soak + stress. Phase 5 regression. | ✅ PASS (plan §13) |

### 14.2 Final Verdict

**Implementation Readiness Verdict: GO (with constraints)**

The hierarchy semantic lane design is complete, reviewed, and ready for
implementation under the following constraints:

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
   constraint. No optimization work before all 13 stages pass validation.

### 14.3 Implementation Summary

```
Phase 6D — Hierarchy Replication Implementation

  Status:         PLANNING COMPLETE — GO for implementation
  Stages:         14 stages (0-13)
  New files:      None (additions to 5 existing files)
  New types:      PT_Hierarchy (0x0D), FHierarchySequenceTracker,
                  FPendingHierarchyAttachment, FScopedHierarchySuppression
  New counters:   7
  New log prefixes: [HIERARCHY], [ATTACH], [DETACH], [ORPHAN], [CYCLE],
                    [REPLAY], [SUPPRESSION], [STALE]
  New profiler scopes: 3
  Frozen systems modified: NONE
  Rollback conditions: 8 defined
  Estimated tests: ~23 (new) + existing regression suites
  Risk level:     LOW-MEDIUM (comparable to visibility with added deferred queue + cycle detection)
```

### 14.4 Sign-Off

| Role | Sign-Off | Date |
|------|----------|------|
| Design author | (pending) | 2026-05-26 |
| Architecture reviewer | (pending) | 2026-05-26 |
| Implementation lead | (pending) | 2026-05-26 |

---

## Revision History

| Date | Version | Changes |
|------|---------|---------|
| 2026-05-26 | 1.0 | Initial implementation plan — defines 14 stages, validation gates, rollback strategy, risk containment, test matrix, and go/no-go criteria for Phase 6D hierarchy replication. |
