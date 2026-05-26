# Semantic Event Architecture Conventions

> **Created**: 2026-05-25 · **Updated**: 2026-05-26 (terminology consolidation)
> **Phase 5**: COMPLETE · **Phase 6**: ACTIVE
> **Lanes**: Rename STABILIZED (6A/6B) · Visibility STABILIZED (6C) · Hierarchy STABILIZED (6D) · Lifecycle/Delete STABILIZED (6E)
> **Runtime core**: FROZEN (`v0.5.0-stabilized`)
>
> Architectural conventions established by Rename and Visibility
> semantic-event vertical slices. Formalized before adding
> additional semantic lanes to ensure consistent patterns across all
> editor-authority workflows.

---

## Table of Contents

1. [Semantic Lane Definition](#1-semantic-lane-definition)
2. [Mandatory Semantic Lane Requirements](#2-mandatory-semantic-lane-requirements)
3. [Forbidden Patterns](#3-forbidden-patterns)
4. [Replay Semantics Standard](#4-replay-semantics-standard)
5. [Provenance Standard](#5-provenance-standard)
6. [Suppression Standard](#6-suppression-standard)
7. [Observability Standard](#7-observability-standard)
8. [Packet Numbering Policy](#8-packet-numbering-policy)
9. [Frozen Runtime Boundary](#9-frozen-runtime-boundary)
10. [Future Slice Rules](#10-future-slice-rules)
11. [Semantic Lane Inventory](#11-semantic-lane-inventory)
12. [Canonical Reference](#12-canonical-reference)
13. [Revision History](#13-revision-history)

---

## 1. Semantic Lane Definition

### 1.1 What Is a Semantic Lane

A **semantic lane** is a discrete editor-mutation replication pathway
between Blender and Unreal Editor. Each lane:

- Carries a single semantic mutation type (e.g., rename, visibility toggle)
- Is fully isolated in the parser (its own `case` branch in `ProcessBinaryPacket`)
- Has its own replay tracker, counters, profiler scopes, and observability
- Follows the mandatory pattern: provenance → suppression → replay-safety

Semantic lanes are fundamentally distinct from the transform state stream.
See `19-phase6-vertical-slice-rename.md §3` for the full conceptual boundary.

### 1.2 Semantic Mutation vs State Stream

| Property | Semantic Mutation (Phase 6+) | State Stream (Phase 5) |
|----------|------------------------------|------------------------|
| **Nature** | Discrete editor event | Continuous state sample |
| **Frequency** | Low (user-initiated, bursty) | High (60 Hz typical) |
| **Semantics** | Lifecycle-sensitive: order matters | Overwrite-oriented: latest wins |
| **Interpolation** | Not applicable | Friendly — missing frames interpolated |
| **Callback impact** | May fire editor callbacks | None — `SetActorTransform` fires no callbacks |
| **Provenance required** | Yes (`EChangeOrigin`) | No |
| **Suppression required** | Yes (RAII scope) | No |
| **Replay sensitivity** | Must deduplicate, reject stale | Low — overwrite with latest |
| **Parser isolation** | Separate `case` branch | Main transform loop |
| **Wire format** | Per-lane fixed or variable | Fixed 81 bytes V4+ |

### 1.3 Explicit Distinctions

Semantic-event replication must NOT be confused with:

| Misconception | Correction |
|--------------|------------|
| "Semantic events are just more packet types" | Each semantic lane has unique lifecycle, provenance, suppression, and replay requirements. Packet types are not fungible. |
| "Semantic events could use transform interpolation" | Interpolation assumes continuous numeric state. Semantic mutations are discrete and atomic. |
| "Semantic events could reuse the transform queue" | Transform queue (FLiveSyncQueue) is for overwrite-oriented state streams. Semantic events must not share this path without architectural review. |
| "Semantic events are covered by existing replay" | Existing replay (snapshot) has no provenance, suppression, or sequence tracking. Semantic replay is per-lane with its own tracker. |

---

## 2. Mandatory Semantic Lane Requirements

Every semantic lane MUST implement ALL of the following before it is
considered complete:

### 2.1 Dedicated Packet Type

- Each lane gets a unique `PT_*` constant (see §8 for allocation)
- Wire format documented in canonical reference (SyncTypes.h) with byte-accurate offset/size table

### 2.2 Isolated Parser Branch

- New `case PT_*` in `ProcessBinaryPacket` (UELiveSyncSubsystem.cpp)
- Must NOT modify existing case branches
- Must NOT modify the main transform loop
- Must NOT modify version dispatch or header parsing
- Per-object boundary checks: reject truncated payload, reject oversized object count
- `Stats.MalformedPackets++` on parse failure
- `kValidTypes[]` updated to include the new type constant
- FNV checksum updated

### 2.3 GUID-Authoritative Lookup

- All mutations identify their target by FGuid (actor GUID)
- Actor lookup via `FindActorFast(Guid)` or equivalent GUID-indexed map
- Reject mutation if GUID not tracked (log warning, increment stale-rejection counter)
- Must NOT use actor name, tag, or label as identity key

### 2.4 Replay Tracker

- Per-lane sequence tracker mapping GUID → last-applied sequence number
- Bounded at 2048 entries (evict oldest when full)
- Reject `IncomingSeq <= LastSeq` as stale or duplicate
- Counter for stale rejections (e.g., `VisibilityStaleRejections`)
- Counter for replay-applied events (e.g., `VisibilityReplayApplied`)
- Counter for replay-skipped events (e.g., `VisibilityReplaySkipped`)
- Cleared on `StopNetworkThread()` and `ConsoleReset()`
- Cleared on Blender-side disconnect (`_close_internal()`)

### 2.5 Provenance Propagation

- Every mutation must be tagged with `EChangeOrigin` before application
- Normal packets: `EChangeOrigin::RemoteReplicated`
- Snapshot replay packets: `EChangeOrigin::Replay`
- Set via `FScopedChangeOrigin` RAII guard on the game thread
- Default/unset provenance = code bug (must never apply mutation without it)
- See §5 for provenance details and interaction rules

### 2.6 Scoped Suppression (RAII)

- Per-lane suppression RAII guard (e.g., `FScopedVisibilitySuppression`)
- Wraps the mutation API call (e.g., `SetIsTemporarilyHiddenInEditor`)
- Logged at Verbose level on entry/exit
- Scoped: active only within the handler call stack
- Temporary: never persists across frames
- No global suppression flags
- See §6 for suppression rules

### 2.7 Profiler Scopes

- `TRACE_CPUPROFILER_EVENT_SCOPE` on the handler function (e.g., `UELiveSync_HandleVisibility`)
- `TRACE_CPUPROFILER_EVENT_SCOPE` on the parse block (e.g., `UELiveSync_ProcessVisibilityPackets`)
- Must be compile-time overhead (zero runtime cost when disabled)

### 2.8 Observability Logs

- Lane-specific prefix: `[RENAME]`, `[VISIBILITY]`, `[COLLECTION]`, etc.
- Application log: `[PREFIX] Applying: GUID=... Origin=... [lane-specific fields]`
- Suppression scope logs: `[PREFIX] Enter/Exit suppression scope (GUID=...)` (Verbose)
- Stale rejection log: `[PREFIX] Rejected — stale/duplicate sequence GUID=...` (Warning)
- Untracked GUID log: `[PREFIX] Rejected — no tracked actor for GUID=...` (Warning)
- Tracker clear log: `[PREFIX] Tracker cleared (StopNetworkThread/ConsoleReset)` (Log)
- All logs use UE_LOG with LogLiveSync category

### 2.9 Bounded Memory

- Sequence tracker bounded at 2048 entries
- No unbounded TMap or TSet growth in any lane
- Eviction policy: remove arbitrary entry when full (documented limitation)
- No per-frame allocations in the hot path

### 2.10 Reconnect Cleanup

- Sequence tracker cleared in `StopNetworkThread()` (after `GRenameSequences` clearing)
- Sequence tracker cleared in `ConsoleReset()` (before `StartNetworkThread`)
- Lane counters `.store(0)` in ConsoleReset
- Blender-side sequence tracker cleared in `_close_internal()` with lane-specific log

---

## 3. Forbidden Patterns

The following patterns are explicitly forbidden for semantic lanes:

### 3.1 Semantic Events in Transform Queues

- Must NOT enqueue semantic packets into `FLiveSyncQueue` (the 128-entry transform MPSC)
- Semantic packets must NOT share the transform packet data path
- Must use `FLiveSyncPacket` discriminated union (existing pattern) for all packet types

### 3.2 Transform Interpolation Reuse

- Must NOT reuse `InterpolateTransforms()` for semantic mutations
- No blending, smoothing, or interpolation of discrete state (name, visibility bool, collection)
- Semantic mutations are atomic — there is no intermediate state

### 3.3 Generalized Semantic Packet Abstraction

- No shared/abstract "semantic event" base class
- No generic dispatcher that routes type-byte to handler
- Each lane has its own isolated `case` block — repetition is intentional
- Abstraction can be considered only after 5+ lanes share the same boilerplate (see §10.8)

### 3.4 Global Replay Trackers

- Per-lane trackers are independent and isolated
- No global `TSet<FGuid>` that all lanes share
- No cross-lane sequence deduplication
- Each lane manages its own replay state

### 3.5 Permanent Suppression State

- Suppression must always be RAII-scoped
- No global `bool bSuppressReplication` that persists across frames
- No per-GUID suppression that survives the handler scope
- Suppression is temporary by construction

### 3.6 Network-Thread Semantic Mutation

- All semantic mutation (UObject/actor state changes) must occur on game thread
- Network thread must never call `SetActorLabel`, `SetIsTemporarilyHiddenInEditor`, etc.
- Network thread parses packet type, fields, and enqueues — never applies

---

## 4. Replay Semantics Standard

### 4.1 Per-GUID Monotonic Sequencing

Each semantic lane carries its own per-GUID sequence number:

```cpp
// Pattern for sequence tracker (SyncTypes.h)
struct FLaneSequenceTracker
{
    TMap<FGuid, uint32> LastSequence;
    static constexpr uint32 MAX_TRACKED_GUIDS = 2048;

    bool IsStaleOrDuplicate(const FGuid& Guid, uint32 IncomingSeq) const
    {
        if (const uint32* LastSeq = LastSequence.Find(Guid))
            return IncomingSeq <= *LastSeq;
        return false;
    }

    void Update(const FGuid& Guid, uint32 AppliedSeq)
    {
        if (LastSequence.Num() >= MAX_TRACKED_GUIDS)
            LastSequence.Remove(LastSequence.CreateIterator().Key());
        LastSequence.Add(Guid, AppliedSeq);
    }
};
```

### 4.2 Stale Replay Rejection

| Condition | IncomingSeq relation | Action |
|-----------|---------------------|--------|
| Stale | `IncomingSeq < LastSeq` | Reject with Warning log, increment lane stale-rejection counter |
| Duplicate | `IncomingSeq == LastSeq` | Reject with Warning log, increment lane stale-rejection counter (or replay-skipped if bInSnapshotBuild) |
| Fresh | `IncomingSeq > LastSeq` | Accept, apply mutation, update tracker |

### 4.3 Duplicate Replay Rejection

During snapshot replay (`bInSnapshotBuild == true`):
- Same `<=` rejection logic applies
- Counter incremented: `LaneReplaySkipped` (not `LaneStaleRejections`)
- Distinction enables tracking reconnect-replay quality separately from live traffic quality

### 4.4 Reconnect Reset Rules

| Event | Tracker action | Counter action |
|-------|---------------|----------------|
| StopNetworkThread | `LastSequence.Empty()` | No counter change |
| ConsoleReset | `LastSequence.Empty()` | All lane counters `.store(0)` |
| Blender disconnect | `_lane_sequences.clear()` | Reset Blender-side sequence counters |

### 4.5 Snapshot Replay Semantics

- `PT_BeginSnapshot (0x09)` sets `bInSnapshotBuild = true`
- Semantic events during snapshot are tagged `EChangeOrigin::Replay`
- Sequence tracker operates identically during replay (stale/duplicate rejection active)
- `PT_EndSnapshot (0x0A)` sets `bInSnapshotBuild = false`
- No special replay-only code paths — the provenance tag handles behavioral differences

### 4.6 Replay Observability

| Counter | When incremented |
|---------|-----------------|
| `LaneReplayApplied` | Replay-tagged mutation accepted and applied |
| `LaneReplaySkipped` | Replay-tagged mutation rejected (stale/duplicate/untracked) |

---

## 5. Provenance Standard

### 5.1 EChangeOrigin Enum

```cpp
enum class EChangeOrigin : uint8
{
    Unspecified,        // Default — code bug if applied without explicit origin
    LocalUser,          // Direct user action in the local editor
    RemoteReplicated,   // Change received from remote peer over TCP
    Replay              // Change during reconnect snapshot replay
};
```

Only these four values are currently active. `Unspecified` is a sentinel
that must never reach mutation application. Future expansions (Recovery,
UndoRedo, Duplicate) will be added as needed by new semantic lanes.

### 5.2 Propagation Rules

| Origin | Set by | Behavior |
|--------|--------|----------|
| `RemoteReplicated` | PT_* case branch (bInSnapshotBuild == false) | Apply mutation, increment lane processed counter, log origin. Must NOT re-replicate. |
| `Replay` | PT_* case branch (bInSnapshotBuild == true) | Apply mutation, increment lane replay-applied counter, log origin. Must NOT re-replicate. Must NOT generate undo transactions. |
| `LocalUser` | Future UE→Blender direction | Not wired in any current lane. Will trigger replication to remote peer. |
| `Unspecified` | N/A | Reject mutation with a check/log. Code bug if reached. |

### 5.3 Setting Provenance

```cpp
// In PT_* case branch:
EChangeOrigin Origin = bInSnapshotBuild
    ? EChangeOrigin::Replay
    : EChangeOrigin::RemoteReplicated;

FScopedChangeOrigin OriginScope(Origin);

// Mutation handler:
void HandleMutation(const FGuid& Guid, ...)
{
    EChangeOrigin Origin = FScopedChangeOrigin::GetCurrent();
    check(Origin != EChangeOrigin::Unspecified);

    FScopedSuppression Suppress(Guid);
    // ... apply mutation ...
}
```

### 5.4 Logging Rules

Every mutation application log MUST include the origin:

```
[PREFIX] Applying: GUID=%s Origin=%s [lane-specific fields]
```

Origin is logged via `LexToString(Origin)` or equivalent stringification.

### 5.5 Suppression Interaction

- `EChangeOrigin` is orthogonal to suppression
- Suppression prevents re-replication; provenance identifies the source
- Both are set independently via separate RAII guards
- Suppression without provenance is a code smell (should never happen)

---

## 6. Suppression Standard

### 6.1 RAII-Only Suppression

All suppression MUST use RAII scope guards:

```cpp
struct FScopedLaneSuppression
{
    FString GuidStr;
    FScopedLaneSuppression(const FGuid& InGuid)
        : GuidStr(InGuid.ToString(EGuidFormats::Digits))
    {
        UE_LOG(LogLiveSync, Verbose, TEXT("[PREFIX] Enter suppression scope (GUID=%s)"), *GuidStr);
    }
    ~FScopedLaneSuppression()
    {
        UE_LOG(LogLiveSync, Verbose, TEXT("[PREFIX] Exit suppression scope (GUID=%s)"), *GuidStr);
    }
};
```

### 6.2 Scoped Lifetime

- Active only within the handler call stack
- Destroyed on scope exit (return, exception, early-out)
- Never persists across frames
- Never stored in global or static variables

### 6.3 GUID-Scoped Behavior

- Suppression scope is created per-GUID per-handler-invocation
- No cross-GUID suppression coupling
- Instance is independent — multiple scope guards can coexist on the stack for different GUIDs

### 6.4 No Cross-Frame Persistence

- Suppression must NOT leak across Tick boundaries
- No deferred callbacks, `AsyncTask`, or `FTimerHandle` inside suppression scope
- If a deferred callback fires, it must not assume suppression is active

### 6.5 Architectural Consistency Requirement

Even when a semantic lane has no callback recursion risk (e.g., visibility —
`SetIsTemporarilyHiddenInEditor` fires no standard callback), suppression
must still be present:

1. **Pattern consistency** — Every lane follows the same structure. Future
   maintainers don't need to know which lanes have callback risks.
2. **Future-proofing** — If a future UE version adds a callback for a
   currently-safe API, the suppression infrastructure is already in place.
3. **Verifiable** — The presence of suppression scope is grep-able:
   `FScoped[A-Z][a-z]*Suppression` is a search term for review.

---

## 7. Observability Standard

### 7.1 Log Prefixes

| Lane | Prefix |
|------|--------|
| Rename | `[RENAME]` |
| Visibility | `[VISIBILITY]` |
| Future lane X | `[LANEX]` (all caps, 4-8 chars, descriptive) |

### 7.2 Profiler Scope Naming

| Usage | Pattern | Example |
|-------|---------|---------|
| Handler function | `UELiveSync_LaneName` | `UELiveSync_HandleVisibility` |
| Parse block | `UELiveSync_LaneNamePackets` | `UELiveSync_ProcessVisibilityPackets` |

### 7.3 BEGIN/END Trace Expectations

- Not required at the individual semantic-event level (events are fast)
- Required at the `ProcessBinaryPacket` level (wrapper around all packet types, existing pattern)
- Individual lane handlers may skip BEGIN/END due to high call count or simplicity

### 7.4 Stale Warning Format

```
[Warning] [PREFIX] Rejected — stale/duplicate sequence:
    GUID=%s, IncomingSeq=%u, LastSeq=%u
```

### 7.5 Malformed Packet Warning Format

```
[Warning] [PREFIX] Malformed packet — %s
    (packet rejected)
```

### 7.6 Semantic Counter Naming

| Category | Pattern | Example |
|----------|---------|---------|
| Events applied (normal) | `LaneProcessed` | `VisibilityProcessed` |
| Stale/duplicate rejections | `LaneStaleRejections` | `VisibilityStaleRejections` |
| Replay events applied | `LaneReplayApplied` | `VisibilityReplayApplied` |
| Replay events skipped | `LaneReplaySkipped` | `VisibilityReplaySkipped` |

All counters are `std::atomic<int32>` with `std::memory_order_relaxed`.
O(1) update, no allocation. Display values only.

---

## 8. Packet Numbering Policy

### 8.1 Type Space Allocation

| Range | Status | Description |
|-------|--------|-------------|
| `0x00` | Reserved | Invalid/null type |
| `0x01` | Frozen | PT_TRANSFORM (Phase 1) |
| `0x02` | Reserved | Unused |
| `0x03` | Frozen | PT_CREATE (Phase 1) |
| `0x04` | Frozen | PT_DELETE (Phase 1) |
| `0x05` | Reserved | Unused |
| `0x06` | Reserved | Unused |
| `0x07` | Frozen | PT_HEARTBEAT (Phase 2) |
| `0x08` | Frozen | PT_ASSETDEF (Phase 5D) |
| `0x09` | Frozen | PT_BEGINSNAPSHOT (Phase 5) |
| `0x0A` | Frozen | PT_ENDSNAPSHOT (Phase 5) |
| `0x0B` | Stabilized | PT_VISIBILITY (Phase 6C) |
| `0x0C` | Stabilized | PT_RENAME (Phase 6A) |
| `0x0D` | Stabilized | PT_HIERARCHY (Phase 6D) |
| `0x0E` | Stabilized | PT_DELETE_V5 (Phase 6E — Lifecycle/Delete) |
| `0x0F` | Reserved | Future semantic lane |

### 8.2 Allocation Policy

| Rule | Description |
|------|-------------|
| New semantic lanes use next available byte | Current next: `0x0E` |
| Frozen types never change | Numbers 0x00–0x0A are permanent |
| Stabilized types may be deprecated | With ADR + version bump, type byte can be retired |
| No type reuse | Once assigned, a type byte is never reassigned to a different meaning |

### 8.3 Ordering Policy

- `kValidTypes[]` is ordered by type byte ascending (ensures consistent parse order)
- `PT_BEGINSNAPSHOT (0x09)` before `PT_END (0x0A)` before `PT_VIS (0x0B)` before `PT_RENAME (0x0C)`
- No semantic ordering implicit in type byte value

---

## 9. Frozen Runtime Boundary

### 9.1 Immutable Systems

These systems must NOT be modified, extended, or refactored by any
semantic lane implementation:

| System | Files | Risk if Modified |
|--------|-------|-----------------|
| Packet parser (version dispatch, magic, header parsing) | `UELiveSyncSubsystem.cpp` (ProcessBinaryPacket outer dispatch) | Backward compat breakage, malformed packet crashes |
| Tick pipeline ordering | `UELiveSyncSubsystem.cpp` (main Tick) | Transform-before-spawn races; BEGIN/END imbalance |
| Queue ownership (FLiveSyncQueue) | `LiveSyncQueue.h` | Data races, queue corruption, use-after-free |
| Queue ownership (FLiveSyncPendingAssetQueue) | `PendingAssetQueue.h` | Data races, asset resolution corruption |
| Network thread lifecycle & shutdown order | `LiveSyncRunnable.h/cpp` | Game thread deadlock (Linux requires Shutdown before Close) |
| Thread ownership (network enqueue only) | All runtime files | Cross-thread UObject access crashes |
| FSyncTransformState layout | `SyncTypes.h` | Wire format incompatibility |
| 24-byte header layout | `SyncTypes.h` (implicit) | Protocol breakage across all versions |
| BEGIN/END tracing at every Tick stage | `UELiveSyncSubsystem.cpp` | Removing would blind future debugging |
| Heartbeat timeout (15s threshold) | `LiveSyncRunnable.cpp` | Connection state machine desync |

### 9.2 Forbidden Modifications for Semantic Lanes

| Action | Why |
|--------|-----|
| Add fields to FSyncTransformState | Object layout FROZEN — use separate TMap |
| Modify existing case branches in ProcessBinaryPacket | Parser FROZEN — add new case only |
| Reorder Tick pipeline stages | Pipeline FROZEN — semantic events inlined into ProcessQueuedPackets |
| Modify FLiveSyncQueue capacity or ownership | Queue FROZEN — use existing enqueue paths |
| Modify StopNetworkThread shutdown sequence | Thread lifecycle FROZEN |
| Add cross-thread state for semantic data | Thread safety FROZEN — game-thread only |
| Remove or skip existing Tick stages | Pipeline integrity FROZEN |

### 9.3 Ownership Invariants

| Component | Owner | Access rules |
|-----------|-------|-------------|
| FLiveSyncQueue (128 MPSC) | Game thread (dequeue), Network thread (enqueue) | Enqueue only on network thread; dequeue only on game thread |
| FLiveSyncPendingAssetQueue (2048) | Game thread (dequeue/retry/remove), Network thread (enqueue) | Same as above |
| GVisibilitySequences / GRenameSequences | Game thread only | All sequence tracker access via HandleLane functions (CHECK_GAME_THREAD) |
| Actor cache (GUID → AActor*) | Game thread only | Lookup and mutation on game thread only |
| Blender send queue | Main thread (enqueue), Daemon thread (dequeue) | Mutex-guarded; main thread must not block |

### 9.4 Parser Invariants

| Rule | Enforcement |
|------|-------------|
| Separate case branch per packet type | New PT_* = new `case`; never modify existing |
| Per-object boundary checks | Guard every memcpy/read against remaining payload |
| PayloadSize validation | uint16; reject if < minimum for the type or > MAX_PACKET_SIZE |
| Return on first malformed object | Do not partially apply a batch |
| `Stats.MalformedPackets++` on parse failure | Every malformed path must increment |
| FNV checksum validation | Before any type-specific parsing |

---

## 10. Future Slice Rules

### 10.1 Vertical-Slice-First

Every new semantic lane must be implemented as a minimal vertical slice:
- One mutation type
- One direction (Blender→UE for new infrastructure)
- Minimal replay, suppression, observability
- Expand only after stabilization

### 10.2 No Premature Abstraction

- Do NOT create a "base semantic event handler" or "generic replay tracker"
- Each lane repeats the pattern: tracker struct, suppression guard, case branch, counters, logs
- Boilerplate is intentional — it makes each lane independently reviewable and testable
- Abstraction is deferred until 5+ lanes share identical pattern (see §10.8)

### 10.3 No Shared Semantic Infrastructure Without Evidence

| Prohibited | Rationale |
|------------|-----------|
| Shared `TMap<FGuid, uint32>` for all sequence trackers | Trackers have independent lifecycle and reset semantics |
| Shared suppression flags | Each lane has different callback risks |
| Shared counter update function | Counters are lane-specific; shared abstraction adds coupling without benefit |
| Shared "[PREFIX]" formatting utility | Prefix is a compile-time string literal; abstraction adds no value |

### 10.4 Stabilize Before Expansion

| Gate | Exit criteria |
|------|---------------|
| Phase 6B (rename stabilization) | Required before Phase 6C visibility began |
| Phase 6C (live validation) | Required before Phase 6D begins |
| Phase 6D (next lane) | Each lane validated against running UE editor before next lane |

See `21-phase6b-runtime-confidence-report.md` for the Phase 6B stabilization
methodology.

### 10.5 Runtime Confidence Before New Lane

Before implementing a new semantic lane:

1. All existing Phase 5 tests pass (run `python3 tests/run_phase5_all.py`)
2. All existing Phase 6 lane tests pass (run `python3 tests/run_phase6_rename.py`, etc.)
3. Runtime audit of existing lanes passes (`python3 tests/phase6b_runtime_audit.py`)
4. No unresolved lane-specific bugs in the tracker

### 10.6 Vertical Slice Checklist

Every new semantic lane must satisfy the following at implementation:

| # | Requirement | Verification |
|---|-------------|-------------|
| 1 | Dedicated PT_* constant | Code review |
| 2 | Isolated case branch | Code review |
| 3 | GUID-authoritative lookup | Code review |
| 4 | Per-GUID sequence tracker (bounded 2048) | Code review + audit |
| 5 | Stale/duplicate rejection (`<=`) | Code review + audit |
| 6 | EChangeOrigin propagation (RemoteReplicated/Replay) | Code review |
| 7 | FScopedChangeOrigin RAII in handler | Code review |
| 8 | Per-lane FScopedLaneSuppression RAII | Code review |
| 9 | TRACE_CPUPROFILER_EVENT_SCOPE (handler + parse block) | Code review |
| 10 | Lane-specific log prefix | Code review |
| 11 | 4 minimum FLiveSyncStats counters (Processed, StaleRejections, ReplayApplied, ReplaySkipped) | Code review |
| 12 | Tracker cleared on StopNetworkThread + ConsoleReset + Blender disconnect | Code review |
| 13 | kValidTypes[] updated | Code review |
| 14 | FNV checksum updated | Code review |
| 15 | Malformed packet handling (truncation + oversized boundary checks) | Code review |
| 16 | No frozen-zone modifications | Git diff review |
| 17 | Blender-side detection + serialization | Code review |
| 18 | Blender-side sequence tracker + disconnect cleanup | Code review |
| 19 | Test suite with minimum 10 tests | File review |
| 20 | Documentation updated (current-state.md, AGENTS.md, scope lock, design doc) | File review |

### 10.7 No Bidirectional Without Infrastructure

UE→Blender direction for any lane is deferred until:

1. Blender-side TCP listener exists (new infrastructure, not modifying existing)
2. Origin propagation for `LocalUser` is wired on UE side
3. Conflict resolution strategy is documented (last-writer-wins minimum)

### 10.8 When Abstraction Is Allowed

Consider shared infrastructure only when:

- 5+ semantic lanes exist with identical tracker/counter/suppression patterns
- A formal ADR documents the abstraction design
- The abstraction does not reduce per-lane testability
- The abstraction does not introduce cross-lane coupling (shared mutex, shared TMap, shared state)

Until then, repetition is the correct design choice.

---

## 11. Semantic Lane Inventory

### 11.1 STABILIZED — Rename (Phase 6A/6B)

| Property | Value |
|----------|-------|
| PT constant | `PT_RENAME = 0x0C` |
| Handler | `HandleRename()` |
| Tracker | `FRenameSequenceTracker` (bounded 2048) |
| Suppression | `FScopedRenameSuppression` |
| Counters | `RenamesProcessed`, `RenameStaleRejections`, `RenameReplayApplied`, `RenameReplaySkipped` |
| Profiler | `UELiveSync_HandleRename`, `UELiveSync_ProcessRenamePackets` |
| Prefix | `[RENAME]` |
| Blender detection | `_last_object_names` diff + `obj.name` |
| Serialization | `serialize_rename()` in `network.py` |
| Wire format | Variable: GUID(16)+oldNameLen(2)+oldName(N)+newNameLen(2)+newName(M)+seq(4)+ts(8) |
| Tests | `tests/phase6_rename_validation.py` (10 tests) |
| Scope lock | `18-phase6-scope-lock.md` |
| Design doc | `19-phase6-vertical-slice-rename.md` |
| Stability report | `21-phase6b-runtime-confidence-report.md` |

### 11.2 STABILIZED — Visibility (Phase 6C)

| Property | Value |
|----------|-------|
| PT constant | `PT_VISIBILITY = 0x0B` |
| Handler | `HandleVisibility()` |
| Tracker | `FVisibilitySequenceTracker` (bounded 2048) |
| Suppression | `FScopedVisibilitySuppression` |
| Counters | `VisibilityProcessed`, `VisibilityStaleRejections`, `VisibilityReplayApplied`, `VisibilityReplaySkipped` |
| Profiler | `UELiveSync_HandleVisibility`, `UELiveSync_ProcessVisibilityPackets` |
| Prefix | `[VISIBILITY]` |
| Blender detection | `_last_visibility_state` diff + `obj.hide_get()` |
| Serialization | `serialize_visibility()` in `network.py` |
| Wire format | Fixed 29 bytes: GUID(16)+bHidden(1)+seq(4)+ts(8) |
| Tests | `tests/phase6_visibility_validation.py` (12 tests) |
| Scope lock | `20-phase6-visibility-scope-lock.md` |
| Design doc | `21-phase6-vertical-slice-visibility.md` |
| Live validation | **PASS** — see `23-phase6-live-runtime-validation.md` |

### 11.3 STABILIZED — Lifecycle/Delete (Phase 6E)

| Property | Value |
|----------|-------|
| PT constant | `PT_DELETE_V5 = 0x0E` |
| Handler | `HandleDelete()` |
| Tracker | `FDeleteSequenceTracker` (bounded 2048) |
| Suppression | `FScopedDeleteSuppression` |
| Counters | `DeletePackets`, `DeleteProcessed`, `DeleteStaleRejections`, `DeleteReplayApplied`, `DeleteReplaySkipped`, `DeleteTombstoneRejections`, `DeleteMissingActor`, `DeleteDeferredDuringSnapshot` (8 total) |
| Profiler | `UELiveSync_HandleDelete`, `UELiveSync_ProcessDeletePackets`, `UELiveSync_ProcessDeferredDeletes` |
| Prefix | `[DELETE]` |
| Blender detection | `_known_guids` diff (disappeared objects) |
| Serialization | `serialize_delete()` in `network.py` — 28-byte fixed payload |
| Wire format | Fixed 28 bytes: GUID(16)+seq(4)+ts(8) |
| Stale rejection | Three-barrier: sequence tracker + tombstone map + ActorCache |
| Tests | `tests/phase6e_delete_validation.py` — 308 tests across 48 sections |
| Runner | `tests/run_phase6e_all.py` — consolidated (delete validation + runtime audit) |
| Scope lock | `29-phase6E-lifecycle-scope-lock.md` |
| Design doc | `30-phase6E-vertical-slice-lifecycle.md` |
| Implementation plan | `33-phase6E-lifecycle-implementation-plan.md` |
| Stability review | `34-phase6E-stage0-3-stability-review.md` |
| Live validation | **STABILIZED** — see `35-phase6E-live-runtime-validation.md` |

### 11.4 STABILIZED — Hierarchy (Phase 6D)

| Property | Value |
|----------|-------|
| PT constant | `PT_HIERARCHY = 0x0D` |
| Handler | `HandleHierarchy()` |
| Tracker | `FHierarchySequenceTracker` (bounded 2048) |
| Suppression | `FScopedHierarchySuppression` |
| Counters | `HierarchyProcessed`, `HierarchyStaleRejections`, `HierarchyReplayApplied`, `HierarchyReplaySkipped`, `HierarchyOrphans`, `HierarchyCycles`, `HierarchyDeferredResolved` |
| Profiler | `UELiveSync_HandleHierarchy`, `UELiveSync_ProcessHierarchyPackets`, `UELiveSync_ResolveHierarchyAttachments` |
| Prefix | `[HIERARCHY]` |
| Blender detection | `_last_parent_guid` diff + `obj.parent` (not yet implemented) |
| Serialization | `serialize_hierarchy()` in `network.py` (not yet implemented) |
| Wire format | Fixed 44 bytes: ChildGuid(16)+ParentGuid(16)+seq(4)+ts(8) |
| Tests | `tests/phase6d_hierarchy_validation.py` (40 standalone pass, 7 integration skip) |
| Scope lock | `24-phase6D-hierarchy-scope-lock.md` |
| Design doc | `25-phase6D-vertical-slice-hierarchy.md` |
| Implementation plan | `26-phase6D-hierarchy-implementation-plan.md` |
| Implementation stages | Stage 0-7 complete (enum, parser, tracker, replay, validation, basic attach/detach, deferred queue + orphan lifecycle) |
| Remaining | Stage 8: cycle detection, Stage 9+: Blender emission, reconnect rebuild, snapshot ordering |

### 11.5 PLANNED (future lanes, not started)

| Lane | Phase | PT constant | Blocked By |
|------|-------|-------------|------------|
| Collection/folder sync | 6F | `0x0F` (proposed) | Lifecycle dependency |
| Duplicate detection | 6G | Extends `0x03` | Identity + graph convergence |

### 11.5 EXPLICITLY DEFERRED

| System | Deferred To | Rationale |
|--------|-------------|-----------|
| Bidirectional authority | Phase 9 | Requires Blender-side TCP listener, conflict resolution, last-writer-wins arbitration |
| Generalized semantic framework | ≥5 lanes exist | Premature abstraction creates coupling without evidence |
| Transaction merge systems | Phase 9 | Requires three-way merge, diff algorithm, conflict UI — none scoped |
| Semantic conflict resolution | Phase 9 | Requires edit history, semantic analysis, domain-specific merge rules |
| Undo/redo synchronization | Phase 9 | Requires recording pre-mutation state and sending inverse mutation |
| Multi-user arbitration | Phase 9 | Requires server, identity system, priority model — all absent |
| Editor history synchronization | Phase 9 | Requires serialization of undo transactions |


---

## 12. Canonical Terminology & Roadmap

### 12.1 Terminology Definitions

| Term | Definition |
|------|-----------|
| **Phase 6** | Live Editing System — umbrella for all semantic-event replication between Blender and UE editor. Subdivided into lanes (by mutation type) and validation phases. |
| **Semantic lane** | A discrete editor-mutation replication pathway. One packet type, one direction (Blender→UE for new lanes), isolated parser branch, dedicated replay tracker, suppression guard, counters, profiler scopes. Each lane is independently reviewable and testable. |
| **Runtime confidence phase** | Cross-lane validation effort (Phase 6B). NOT a semantic lane. Validates existing lanes through soak, fuzz, replay robustness, invariant audit, and live runtime validation. |
| **Stabilization** | Structural and live-validation milestone. Gate for starting the next lane. Requires: structural audit pass, live validation pass against running UE editor, all criteria met, zero unresolved bugs. |
| **Implementation stage** | Internal rollout increment within a lane (e.g. Hierarchy Stages 0–14). Stages decompose a single lane's implementation into ordered, independently verifiable steps. Stages are NOT lanes. |
| **Replay-safe** | Packet can be replayed (snapshot rebuild, reconnect) without producing duplicate or incorrect state. Achieved via per-GUID monotonic sequence tracker with stale/duplicate rejection (`<=`). |
| **Suppression-safe** | Replication of a mutation back to its origin is prevented via RAII guard, preventing feedback loops / callback recursion. |
| **Parser-isolated** | Each packet type has its own `case` branch in `ProcessBinaryPacket`. No shared parsing logic, no fallthrough, no modification of existing branches. |
| **Frozen runtime** | The Phase 5 core (parser, queues, threads, Tick pipeline, `FSyncTransformState`, 24-byte header, heartbeat, shutdown sequence). Must NOT be modified by semantic lane implementations. |

### 12.2 Phase 6 Roadmap

```
Phase 6 — Live Editing System
│
├── 6A — Rename Lane               ◉ STABILIZED  (packet: PT_RENAME = 0x0C)
├── 6B — Runtime Confidence        ◉ COMPLETE    (methodology + audit)
│   (soak · fuzz · replay robustness · invariant audit · live validation)
├── 6C — Visibility Lane           ◉ STABILIZED  (packet: PT_VISIBILITY = 0x0B)
├── 6D — Hierarchy Lane            ◉ STABILIZED  (packet: PT_HIERARCHY = 0x0D)
│   ├── Stage 0:  Enum reservation                  ✅
│   ├── Stage 1:  Parser branch + isolation          ✅
│   ├── Stage 2:  FHierarchySequenceTracker           ✅
│   ├── Stage 3:  Replay rejection layer             ✅
│   ├── Stage 4:  Validation foundation              ✅
│   ├── Stage 5:  Blender detection                  ❌
│   ├── Stage 6:  Basic AttachToActor/DetachFromActor ✅
│   ├── Stage 7:  Deferred queue + orphan lifecycle   ✅
│   ├── Stage 8:  Orphan lifecycle stabilization     ✅
│   ├── Stage 9:  Explicit cycle detection           ✅
│   ├── Stage 10: Blender serialization              ❌
│   ├── Stage 11: Snapshot ordering                  ❌
│   ├── Stage 12: Reconnect rebuild                  ❌
│   ├── Stage 13: Stress & stability                 ❌
│   └── Stage 14: Full stabilization                 ❌
├── 6E — Lifecycle/Delete Lane      ◉ STABILIZED  (packet: PT_DELETE_V5 = 0x0E)
├── 6F — Collection/Folder Lane     ○ PLANNED (blocked by hierarchy + lifecycle)
└── 6G — Duplicate Detection Lane   ○ PLANNED (blocked by all prior lanes)

Key:
  ◉ = complete / stabilized
  ◎ = work in progress
  ○ = not started
  ✅ = implemented
  ❌ = not yet implemented
```

**Note**: Lanes are NOT stages. A lane (6A, 6C, 6D) is a discrete semantic mutation type. Stages are internal implementation steps within the hierarchy lane only. Phase 6B is NOT a lane — it is a cross-lane validation methodology. Do not use "Phase 6B" to refer to a semantic lane.

### 12.3 Dependency Chain

```
6A Rename
  └──→ 6B Runtime Confidence (validates rename methodology)
        └──→ 6C Visibility
              └──→ 6D Hierarchy
                    └──→ 6E Lifecycle/Delete ◉ STABILIZED
                          └──→ 6F Collections
                                └──→ 6G Duplicates
```

#### Why Hierarchy Before Lifecycle/Delete
- Hierarchy introduces graph consistency constraints (parent-child relationships must be tracked)
- Lifecycle/delete requires stable orphan semantics — what happens to children when a parent is deleted?
- Without hierarchy stabilization, delete replication is destructive: children become untracked orphans with no recovery path
- Tombstone systems depend on hierarchy state awareness to properly clean up descendants

#### Why Collections Depend on Hierarchy
- UE folder hierarchy mirrors object parent-child attachment
- Collection membership assignment requires understanding which objects exist and their attachment relationships
- Collection→folder mapping benefits from established, tested attachment APIs

#### Why Duplicate Detection Depends on Identity + Graph
- Cross-side duplicate validation (UE Alt+Drag → Blender PT_CREATE) requires stable GUID management
- Duplicate reconciliation requires graph convergence — both sides must agree on the object graph before deduplication is safe
- Identity + hierarchy must both be stable before duplicate replication is deterministic

### 12.4 Phase 6 Status Table

| Lane | Phase | Implementation | Stabilization | Validation |
|------|-------|---------------|---------------|------------|
| Rename | 6A/6B | COMPLETE | PASS (49/49 audit) | PASS (10 tests) |
| Runtime Confidence | 6B | COMPLETE | N/A (methodology) | PASS (49 checks) |
| Visibility | 6C | COMPLETE | PASS (9/9 criteria) | PASS (11/11 live, 12 tests) |
| Hierarchy | 6D | STABILIZED | PASS (97/97 standalone, 7 integration SKIP) | PASS (97 tests) |
| Lifecycle/Delete | 6E | COMPLETE (STABILIZED) | PASS (308/308 tests) | PASS (102/102 audit checks) |
| Collection/Folder | 6F | NOT STARTED (planned) | N/A | N/A |
| Duplicate Detection | 6G | NOT STARTED (planned) | N/A | N/A |

### 12.5 Canonical Cross-Reference

| Reference | Content |
|-----------|---------|
| `18-phase6-scope-lock.md` | Phase 6 scope boundaries, authority model, "done" criteria |
| `19-phase6-vertical-slice-rename.md` | First semantic lane: rename design |
| `20-phase6-visibility-scope-lock.md` | Visibility scope lock, escape hatches |
| `21-phase6-vertical-slice-visibility.md` | Second semantic lane: visibility design |
| `21-phase6b-runtime-confidence-report.md` | Phase 6B stabilization methodology and findings |
| `22-semantic-event-architecture-conventions.md` | **THIS DOCUMENT** — canonical conventions + terminology |
| `23-phase6-live-runtime-validation.md` | Live runtime validation report (Visibility 9/9 PASS) |
| `24-phase6D-hierarchy-scope-lock.md` | Phase 6D hierarchy scope boundaries |
| `25-phase6D-vertical-slice-hierarchy.md` | Phase 6D hierarchy vertical slice design |
| `26-phase6D-hierarchy-implementation-plan.md` | Phase 6D hierarchy 14-stage implementation plan |
| `27-phase6D-stage0-5-stability-review.md` | Phase 6D Stage 0-5 stability review (GO verdict) |
| `00-consolidated-roadmap.md` | Full project roadmap (Phase 1-9) |
| `12-core-runtime-invariants.md` | Frozen runtime invariants |
| `13-phase6-design-constraints.md` | Unresolved Phase 6 design questions |
| `14-editor-sync-safety.md` | Editor synchronization safety rules |
| `15-architecture-decision-records.md` | 15 ADRs for major Phase 5 choices |
| `16-known-safe-modification-zones.md` | SAFE/CAUTION/HIGH-RISK/FROZEN zones |
| `17-phase6-readiness.md` | Phase 6 readiness checklist (14/14 complete) |

---

## 13. Revision History

| Date | Version | Changes |
|------|---------|---------|
| 2026-05-25 | 1.0 | Initial semantic-event architecture conventions — formalized from Phase 6A/B/C experience |
| 2026-05-26 | 2.0 | Terminology consolidation: added §12 canonical terminology, roadmap hierarchy, dependency chain, status table. Updated Packet Numbering Policy (§8.1) to include PT_HIERARCHY (0x0D). Updated Semantic Lane Inventory (§11.3-11.5) with hierarchy IN PROGRESS status. |
| 2026-05-26 | 2.1 | Updated roadmap §12.2: Hierarchy Stage 9 complete (orphan lifecycle stabilization + explicit cycle detection). Updated status table §12.4. |
| 2026-05-26 | 2.2 | Hierarchy STABILIZED: Stage 10-12 implementation complete (Blender detection + serialization + depth-sort snapshot), Stage 13 runtime validation complete (97/97 standalone pass, 49/49 audit pass). Updated lanes header, roadmap §12.2, and status table §12.4. |
| 2026-05-26 | 2.3 | Lifecycle/Delete STABILIZED: Phase 6E Stages 0-13 complete. 308/308 standalone tests, 102/102 audit checks, 17/17 stabilization criteria met. Added §11.3 for lifecycle lane inventory. Updated §8.1 (0x0E → Stabilized), §11.4 (hierarchy → STABILIZED), §12.2-12.4 (roadmap + status table). |
