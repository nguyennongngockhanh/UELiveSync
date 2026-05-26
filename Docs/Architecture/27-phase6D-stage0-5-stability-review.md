# Phase 6D Hierarchy Stages 0–5: Stability & Isolation Review

**Document:** 27-phase6D-stage0-5-stability-review.md  
**Review Date:** 2026-05-26  
**Review Scope:** Protocol registration · Parser isolation · Replay sequencing · Stale/duplicate rejection · Observability foundation · Test scaffolding  
**Review Threshold:** Pre-Stage-6 GO/NO-GO gate  
**Subsequent Implementation**: Stages 6-9 COMPLETE (basic attach/detach · deferred queue · orphan lifecycle stabilization · explicit cycle detection). This review's findings (GO with constraints) remain valid — no frozen-runtime violations were introduced.

---

## 1. Parser Isolation Audit

### Verdict: FULLY ISOLATED — ZERO graph side effects

The `PT_Hierarchy` parser branch in `ProcessBinaryPacket()` (`UELiveSyncSubsystem.cpp:2271–2323`) is structurally identical to the PT_Visibility branch:

| Property | Status | Evidence |
|----------|--------|----------|
| Fixed-size 44-byte boundary check | ✅ | `if (Ptr + 44 > PacketEnd)` per iteration |
| Early return on truncation | ✅ | Returns before transform-object loop |
| Early return after loop completion | ✅ | `return` at line 2322 |
| No fall-through to transform/Create/Delete | ✅ | Returns before UNKNOWN PACKET TYPE check (line 2325) |
| No shared mutable parser state | ✅ | All locals (ChildGuid, ParentGuid, etc.) stack-allocated |
| No graph mutation | ✅ | `HandleHierarchy()` (Stage 4) logs intent only — no UObject calls |
| Profiler scope | ✅ | `UELiveSync_ProcessHierarchyPackets` |
| Snapshot awareness (Replay vs RemoteReplicated) | ✅ | `bInSnapshotBuild` → `EChangeOrigin` correctly set |

### Object-per-iteration boundary safety

The hierarchy branch has a single `Ptr + 44 > PacketEnd` guard per iteration. Unlike PT_Rename (5 checks for variable-length strings), the fixed 44-byte payload means one check suffices. This is correct for the wire format:

- Offset 0:  ChildGuid (16 bytes)
- Offset 16: ParentGuid (16 bytes)
- Offset 32: SequenceNumber (4 bytes)
- Offset 36: Timestamp (8 bytes)
- Total:    44 bytes

A truncated packet cannot desync stream parsing because the branch returns on the first failed boundary check.

### Null-parent semantics (all-zero ParentGuid)

No special handling at the parser level — the 16 zero bytes are decoded into an `FGuid` with all fields zero. The semantic interpretation of "detach-to-root" is deferred entirely to HandleHierarchy (Stage 6+). The parser treats all-zero ParentGuid identically to any other GUID value. This is correct for Stage 5.

### Branch ordering in ProcessBinaryPacket()

```
Heartbeat (0x07)            → return at line ~1992
BeginSnapshot (0x09)        → return at line ~2006
EndSnapshot (0x0A)          → return at line ~2012
AssetDef (0x08)             → return at line ~2082
Visibility (0x0B)           → return at line ~2146
Rename (0x0C)               → return at line ~2252
HIERARCHY (0x0D)            → return at line ~2322  ← NEW
UNKNOWN PACKET TYPE check   → return if not 0x01/0x03/0x04
Transform/Create/Delete     → object loop
```

The hierarchy branch cannot be reached by any other packet type. All prior branches return before reaching it. It returns before reaching the transform object loop. The ordering is safe.

---

## 2. Replay Tracker Audit

### Verdict: STABLE — Deterministic rejection, bounded, correctly cleared

#### `FHierarchySequenceTracker` (`SyncTypes.h:605–627`)

| Property | Status | Evidence |
|----------|--------|----------|
| Monotonic per-child-GUID semantics | ✅ | `IncomingSeq <= *LastSeq` = stale |
| Stale rejection deterministic | ✅ | Pure function of (GUID, incoming seq, last seq) |
| Duplicate rejection deterministic | ✅ | Same check — `<=` covers both stale and duplicate |
| Bounded at 2048 | ✅ | `MAX_TRACKED_GUIDS = 2048` |
| Eviction policy | ✅ | Removes first iterator entry when full (arbitrary but deterministic) |
| Thread safety | ✅ | Game-thread only (`CHECK_GAME_THREAD()` at line 5211) |

#### Replay safety scenarios

| Scenario | Behavior | Correct? |
|----------|----------|----------|
| Stale replay after reconnect | First valid seq always accepted (map empty after clear) | ✅ |
| Duplicate replay flood | All duplicates with seq <= last rejected; counter incremented | ✅ |
| Replay counter correctness | `HierarchyReplayApplied` for Replay origin, `HierarchyReplaySkipped` for stale Replay | ✅ |
| Replay/live origin classification | `bInSnapshotBuild` determines origin in parser branch; HandleHierarchy counters discriminate | ✅ |

#### Clear points

| Location | Action | Scope |
|----------|--------|-------|
| `StopNetworkThread()` (line 1473) | `GHierarchySequences.LastSequence.Empty()` | On disconnect |
| `ConsoleReset()` (line 7432) | `GHierarchySequences.LastSequence.Empty()` + 7 counter resets | On `UE.LiveSync.Reset` |

Both mirrors the existing `GRenameSequences` and `GVisibilitySequences` lifecycle. No gaps.

#### FINDING-001 mitigation path

FINDING-001 (deferred sequence re-check) requires orphan/retry logic that is deferred to Stage 6+. In the current Stage 5, there is no pipeline that re-checks deferred GUIDs. This is acceptable because:

1. No graph mutation exists — there is nothing to "fix" retroactively
2. The sequence tracker correctly records what has been "acknowledged" (logged intent)
3. When Stage 6 introduces attachment application, the sequence tracker provides the authoritative last-seen value for each child GUID, enabling the retry logic to determine what still needs applying

The mitigation path is: **Tracker update + last-known state survive into Stage 6**. The `GHierarchySequences.LastSequence` map retains the highest sequence seen per child GUID. When attachment application is added, HandleHierarchy can check: "Was this sequence already applied? If not (but tracker says we saw it), we need to retroactively apply it." This works because the tracker is the source of truth for "what has been processed," not "what has been applied to the graph."

The current design is forward-compatible. No rework needed.

---

## 3. Protocol Integrity Audit

### Verdict: PASS — All 11 source-code integrity checks pass

| Check | File | Line | Status |
|-------|------|------|--------|
| `PT_Hierarchy = 0x0D` in UE enum | SyncTypes.h | 225 | ✅ |
| `PT_Reserved_02 = 0x02` (legacy renamed) | SyncTypes.h | 208 | ✅ |
| `PT_Hierarchy = 0x0D` in Blender | network.py | 66 | ✅ |
| `0x0D` in UE FNV checksum | SyncTypes.h | 761 | ✅ |
| `0x0D` in Blender FNV checksum | network.py | 40 | ✅ |
| `0x0D` in `kValidTypes[]` | UELiveSyncSubsystem.cpp | 1937 | ✅ |
| `PT_Hierarchy` dispatch case | UELiveSyncSubsystem.cpp | 2271 | ✅ |
| HandleHierarchy declaration | UELiveSyncSubsystem.h | 192–197 | ✅ |
| HandleHierarchy definition | UELiveSyncSubsystem.cpp | 5203–5286 | ✅ |
| 7 hierarchy counters in FLiveSyncStats | SyncTypes.h | 383–390 | ✅ |
| Test file uses `PT_Hierarchy = 0x0D` | phase6d_hierarchy_validation.py | 26 | ✅ |

### No packet collisions

- `0x0D` does not conflict with any existing packet type (0x01–0x0C)
- Legacy `PT_Hierarchy = 0x02` renamed to `PT_Reserved_02 = 0x02` — value preserved for theoretical backward compatibility, no longer used for dispatch
- FNV checksum now includes `0x0B`, `0x0C`, `0x0D` (visibility, rename, hierarchy) — all three Phase 6 semantic lanes are hashed

### Protocol drift detection

Both UE and Blender checksums include `0x0D`. If one side is rebuilt without the constant, startup will log a signature mismatch. No silent drift possible.

### Documentation inconsistencies (non-blocking — RESOLVED)

Four docs had stale/conflicting references to `0x0D`. These have been fixed
in the Phase 6 terminology consolidation pass (2026-05-26):

| Doc | Issue | Resolution |
|-----|-------|------------|
| `Docs/Roadmap/00-consolidated-roadmap.md:519` | Listed `PT_Rename = 0x0D` (should be `0x0C`) | Fixed — now `PT_Rename = 0x0C`, `PT_Hierarchy = 0x0D` |
| `Docs/Roadmap/00-consolidated-roadmap.md:1044` | Listed `0x0D \| PT_MaterialAssign` | Fixed — now `0x0D \| PT_Hierarchy (6D, In Progress)` |
| `Docs/Architecture/22-semantic-event-architecture-conventions.md:472` | Listed `0x0D` as "Reserved" | Fixed — now `0x0D \| PT_HIERARCHY (Phase 6D)` |
| `Docs/Architecture/18-phase6-scope-lock.md:87` | Proposed `PT_COLLECTION = 0x0D` | Fixed — now `0x0E` (next available) |

Per review finding: these were documentation-only issues with no runtime protocol impact. All now resolved.

---

## 4. Frozen Runtime Audit

### Verdict: NO frozen runtime modified — all changes additive

| Frozen Component | File | Modified? | Details |
|------------------|------|-----------|---------|
| LiveSyncQueue (MPSC buffer) | LiveSyncQueue.h | ❌ No | No hierarchy references found |
| PendingAssetQueue (bounded 2048) | PendingAssetQueue.h | ❌ No | No hierarchy references found |
| LiveSyncRunnable (network thread) | LiveSyncRunnable.h/.cpp | ❌ No | No hierarchy references found |
| Tick pipeline ordering | UELiveSyncSubsystem.cpp | ❌ No | Tick() unchanged, pipeline ordering preserved |
| ProcessQueuedPackets | UELiveSyncSubsystem.cpp | ❌ No | Delegates to ProcessBinaryPacket — unchanged |
| InterpolateTransforms | UELiveSyncSubsystem.cpp | ❌ No | Signature and implementation untouched |
| FSyncTransformState (POD invariant) | SyncTypes.h:41–198 | ❌ No | Zero fields added |
| Network-thread ownership | LiveSyncRunnable.cpp | ❌ No | No hierarchy references |
| StopNetworkThread shutdown order | UELiveSyncSubsystem.cpp | ❌ No | Only additive `GHierarchySequences.LastSequence.Empty()` |
| ConsoleReset | UELiveSyncSubsystem.cpp | ❌ No | Only additive hierarchy counter resets + tracker clear |
| Protocol struct packing | SyncTypes.h (all structs) | ❌ No | No struct fields changed |

### What was modified (purely additive)

1. **`SyncTypes.h`** — 3 additive sections:
   - `PT_Reserved_02 = 0x02` rename (comment-only change to existing enum entry)
   - `PT_Hierarchy = 0x0D` (new enum entry)
   - 7 new `std::atomic<int32>` counters in `FLiveSyncStats` (additive to stats struct)
   - `FHierarchySequenceTracker` (entirely new struct)
   - `0x0D` added to FNV checksum

2. **`UELiveSyncSubsystem.cpp`** — 5 additive insertions:
   - `FHierarchySequenceTracker GHierarchySequences` global (non-frozen: file-scope tracker instance)
   - `GHierarchySequences.LastSequence.Empty()` in StopNetworkThread (non-frozen: lifecycle cleanup)
   - `0x0D` in `kValidTypes[]` (non-frozen: protocol version gate)
   - `if (PacketType == PT_Hierarchy)` parser branch (non-frozen: new packet type branch, returns early)
   - `HandleHierarchy()` implementation (non-frozen: new handler function)
   - Hierarchy counter resets + tracker clear in ConsoleReset (non-frozen: lifecycle cleanup)

3. **`UELiveSyncSubsystem.h`** — 2 additive declarations:
   - `HandleHierarchy()` declaration (non-frozen: header; no freeze banner)
   - `ValidateHierarchy()` declaration (pre-existing, not touched by this work)

4. **`network.py`** — 2 additive changes:
   - `PT_Hierarchy = 0x0D` constant
   - `0x0D` in FNV checksum loop

### `ValidateHierarchy()` — existing code, NOT Stage 5

The existing `ValidateHierarchy()` function at `UELiveSyncSubsystem.cpp:6774` is pre-existing Phase 3.6/4 safety validation logic that checks the transform-level parent-child graph integrity. It is called periodically (every 300 frames) after the core pipeline. It is NOT part of the Stage 5 implementation. It coexists with the new `HandleHierarchy()` — one validates the transform attachment graph, the other handles semantic hierarchy events. They operate on different data and have different lifetimes.

---

## 5. Counter + Observability Audit

### Verdict: ALL CORRECTLY WIRED — safe when unused, deterministic, thread-safe

#### General philosophy

All hierarchy counters use `std::atomic<int32>` with `std::memory_order_relaxed`. This is appropriate for display-only diagnostic counters and matches the existing rename/visibility pattern. No consumer reads these counters for control flow decisions.

#### Profiler scopes

| Scope | Location | Present? |
|-------|----------|----------|
| `UELiveSync_ProcessHierarchyPackets` | ProcessBinaryPacket (line 2273) | ✅ |
| `UELiveSync_HandleHierarchy` | HandleHierarchy (line 5212) | ✅ |

#### Log prefix hygiene

All hierarchy event-pipeline log messages use the `[HIERARCHY]` prefix:

| Line | Message |
|------|---------|
| 2280 | `[HIERARCHY] Truncated packet: ...` |
| 5222 | `[HIERARCHY] Rejected — no tracked actor for ChildGuid=...` |
| 5238 | `[HIERARCHY] Rejected — stale/duplicate sequence ...` |
| 5264 | `[HIERARCHY] Intent: ChildGuid=%s Origin=%s ...` |
| 7434 | `[HIERARCHY] Replay tracker reset (ConsoleReset)` |

The existing `ValidateHierarchy()` uses `[HierarchySafety]` prefix — intentionally different to distinguish safety validation from the semantic event pipeline.

#### Counter completeness

| Counter | Wired? | Increment location(s) | Reset location |
|---------|--------|----------------------|----------------|
| `HierarchyPackets` | ✅ | `ProcessBinaryPacket` after loop (line 2316) | ConsoleReset (line 7425) |
| `HierarchyProcessed` | ✅ | `HandleHierarchy` — RemoteReplicated origin (line 5279) | ConsoleReset (line 7426) |
| `HierarchyStaleRejections` | ✅ | `HandleHierarchy` — no actor OR stale seq (lines 5227, 5244) | ConsoleReset (line 7427) |
| `HierarchyReplayApplied` | ✅ | `HandleHierarchy` — Replay origin (line 5283) | ConsoleReset (line 7428) |
| `HierarchyReplaySkipped` | ✅ | `HandleHierarchy` — stale Replay (line 5248) | ConsoleReset (line 7429) |
| `HierarchyOrphans` | ⚠️ Placeholder | Never incremented (Stage 6+) | ConsoleReset (line 7430) |
| `HierarchyCycles` | ⚠️ Placeholder | Never incremented (Stage 6+) | ConsoleReset (line 7431) |

`HierarchyOrphans` and `HierarchyCycles` are declared but never incremented. They are zero-cost placeholders (`std::atomic<int32>` initialized to 0) reserved for Stage 6+. No code reads them for control flow. They are harmless.

#### Counter semantics

| Counter | Increments when | Correctness |
|---------|----------------|-------------|
| `HierarchyPackets` | One per PT_Hierarchy packet (regardless of ObjectCount) | ✅ Matches declared semantics |
| `HierarchyProcessed` | Each non-stale event from RemoteReplicated origin | ✅ Live attach intent logged |
| `HierarchyStaleRejections` | No tracked actor OR stale/duplicate sequence | ✅ Both rejection paths covered |
| `HierarchyReplayApplied` | Each non-stale event from Replay origin | ✅ Replay intent logged |
| `HierarchyReplaySkipped` | Stale event from Replay origin | ✅ Replay skip counted |

#### Origin coverage edge case

For `Origin == LocalUser` or `Origin == Recovery`, the sequence tracker is updated but **no counter is incremented**. This means legitimate local/recovery events are invisible in the metrics. This is acceptable because:
- These origins are not expected in the current Stage 5 (no Blender-side emission yet)
- The sequence tracker still prevents re-application of stale events from these origins
- This can be addressed in Stage 6+ when these origins become active

---

## 6. Validation Suite Audit

### File: `tests/phase6d_hierarchy_validation.py`

### Verdict: Tests validate ONLY parser safety, replay rejection, and protocol integrity. NO graph behavior tests exist.

#### Test categories (24 total: 18 standalone + 6 integration/skip)

| Test | Type | Scope | Graph? |
|------|------|-------|--------|
| `test_wire_format_size` | Standalone | Validates 44-byte payload, GUID offsets, sequence/timestamp layout | ❌ No |
| `test_detach_to_root` | Standalone | Validates all-zero ParentGuid encoding | ❌ No |
| `test_sequence_tracker_standalone` | Standalone | Simulates monotonic seq, duplicate, stale, higher-seq acceptance | ❌ No |
| `test_malformed_truncated` | Integration | Sends truncated payload — skips if no UE, validates no crash | ❌ No |
| `test_malformed_extra_garbage` | Integration | Sends 4 trailing garbage bytes — skips if no UE | ❌ No |
| `test_batch_two_objects` | Integration | Sends 2-object batch — skips if no UE | ❌ No |
| `test_hierarchy_single` | Integration | Sends single attach event — skips if no UE | ❌ No graph verification |
| `test_hierarchy_detach` | Integration | Sends detach-to-root — skips if no UE | ❌ No graph verification |
| `test_hierarchy_storm` | Integration | Sends 100 events to same GUID — skips if no UE | ❌ No graph verification |

The three integration tests (`single`, `detach`, `storm`) send packets to UE but verify **only**:
- Packet was transmitted successfully (`send_and_close` returned True)
- No crash occurred during transmission

They do NOT:
- Verify actor attachment state
- Call `AttachToActor` / `DetachFromActor`
- Query the UE scene graph
- Assert any visual or spatial outcome

This is appropriate for Stage 5. Graph behavior tests will be added in Stage 6+.

#### Runner: `tests/run_phase6d_hierarchy.py`

Standard pattern matching `run_phase6_rename.py` and `run_phase6_visibility.py`. Executes `phase6d_hierarchy_validation.py` and reports pass/fail.

---

## 7. Risk Assessment Before Stage 6

### Verdict: GO (with documented cautions)

The following risks must be addressed when introducing `AttachToActor()` / `DetachFromActor()` in Stage 6+.

| # | Risk | Severity | Description | Mitigation Required Before Stage 6 |
|---|------|----------|-------------|--------------------------------------|
| R1 | Replay → graph interaction | HIGH | Snapshot replay may send hierarchy events for children whose parents haven't been replayed yet (ordering: Blender emits parents before children, but network may reorder). Without a deferred retry queue (FINDING-001), these events would be silently rejected. | Implement deferred parent resolution (Stage 6+) before enabling replay attachment. |
| R2 | Runtime attachment ownership | HIGH | `AttachToActor()` changes the UE scene graph (root → attached). During interpolation, attached children compute local transforms differently from root actors. The existing `InterpolateTransforms()` has local-vs-world branching that must not be confused by mid-frame attachment changes. | Ensure attachment application happens at a well-defined pipeline point (before or after interpolation), not during. |
| R3 | Reconnect replay ordering | MEDIUM | On reconnect, the snapshot sends `PT_Create` (spawn actors), then `PT_Hierarchy` events. The hierarchy parser's `FindActorFast(ChildGuid)` may fail if the create hasn't been processed yet. Current Stage 5 correctly rejects this as "no tracked actor." Stage 6+ must add deferred retry. | Implement deferred queue with exponential backoff (matching `PendingAssetQueue` pattern). |
| R4 | Hidden graph state | MEDIUM | The existing `FSyncTransformState.ParentGuid` and `bHasParent` fields are written by the transform pipeline (V3+ ParentGuid in object payload). The new `PT_Hierarchy` semantic events will also want to modify these fields. Without coordination, the two systems can conflict. | Define ownership: transform stream owns per-frame ParentGuid; hierarchy events own semantic attach/detach. One source of truth per field. |
| R5 | Cycle detection race | LOW | The existing `AttachToParent()` has cycle detection. If `HandleHierarchy` (Stage 6+) calls `AttachToParent()` from the semantic event path, cycles must still be detected. The existing `ValidateHierarchy()` safety check provides a backstop but is periodic (every 300 frames). | AttachToParent must be called synchronously during HandleHierarchy (game thread), not deferred. Cycle detection in AttachToParent is sufficient. |
| R6 | Orphan lifecycle | LOW | Children whose parent actor doesn't exist yet (deleted, not spawned, renamed) need a lifecycle: defer → retry → timeout → orphan policy. Current Stage 5 has `HierarchyOrphans` counter but no logic. | Implement bounded deferred orphan queue before enabling attachment. Define orphan timeout policy (detach-to-root vs log-and-leave). |
| R7 | Blender-side emission not implemented | INFO | Blender addon does not yet emit `PT_Hierarchy` packets. All Stage 5 testing is via synthetic packets from the test suite. The emission logic (detect parent changes in Blender scene, serialize, send) must be implemented in a future stage. | Not a blocker for UE-side graph mutation, but no end-to-end testing until Blender emits. |

### What MUST remain true before Stage 6 starts

1. **No AttachToActor/DetachFromActor in HandleHierarchy** — the current pure-replay-rejection layer must remain intact until deferred queue + orphan lifecycle + cycle detection are implemented
2. **Parser isolation preserved** — the PT_Hierarchy branch must continue to return before the transform object loop
3. **Sequence tracker is source of truth** — `GHierarchySequences.LastSequence` records what HandleHierarchy has processed; Stage 6 must read this to determine whether to apply or skip
4. **Frozen runtime untouched** — no modifications to LiveSyncQueue, PendingAssetQueue, LiveSyncRunnable, Tick pipeline, InterpolateTransforms, or FSyncTransformState
5. **No Blender-side hierarchy emission** until UE-side attachment is fully implemented and integration-tested

---

## 8. Current Hierarchy Runtime Capability

### What works NOW (Stage 5)

| Capability | Status |
|------------|--------|
| `PT_Hierarchy = 0x0D` packets parse safely | ✅ |
| Boundary checks guard against truncated/malformed payloads | ✅ |
| Protocol validation (`kValidTypes`, FNV checksum) accepts `0x0D` | ✅ |
| Replay sequence tracking per child GUID | ✅ |
| Stale event rejection (lower or equal sequence) | ✅ |
| Duplicate event rejection (same sequence) | ✅ |
| Replay vs live origin classification | ✅ |
| `[HIERARCHY]` log messages with proper prefixes | ✅ |
| Profiler scopes (`UELiveSync_ProcessHierarchyPackets`, `UELiveSync_HandleHierarchy`) | ✅ |
| 7 diagnostic counters (5 wired, 2 placeholder) | ✅ |
| Tracker clear on disconnect (StopNetworkThread) | ✅ |
| Full counter + tracker reset on ConsoleReset | ✅ |
| Standalone wire-format and sequence-tracker tests | ✅ |
| Integration tests (send-only, skip if no UE) | ✅ |
| AGENTS.md updated with run command and protocol info | ✅ |
| **NO semantic graph mutation exists** | ✅ |

### What does NOT work yet (Stage 6+)

| Capability | Status |
|------------|--------|
| `AttachToActor()` / `DetachFromActor()` application | ❌ Deferred |
| Deferred parent resolution (orphan retry queue) | ❌ Deferred |
| Orphan lifecycle (retry → timeout → policy) | ❌ Deferred |
| Cycle detection for semantic hierarchy events | ❌ Deferred |
| Blender-side PT_Hierarchy emission | ❌ Deferred |
| End-to-end hierarchy replication | ❌ Deferred |
| Reconnect hierarchy replay application | ❌ Deferred |
| Conflict resolution (semantic attach vs transform stream) | ❌ Deferred |

---

## Final Verdict

### GO for Stage 6 (with graph-mutation caution)

**Rationale:** The Phase 6D hierarchy Stages 0–5 implementation is stable, isolated, and introduces zero risk to the existing frozen runtime. It adds:

- A new protocol packet type (`PT_Hierarchy = 0x0D`) that is consistently registered, hashed, and validated across UE and Blender
- A fully isolated parser branch that returns before the transform object loop — no path for hierarchy packets to leak into transform/Create/Delete handling
- A replay rejection layer (`HandleHierarchy`) that has ZERO graph side effects — it logs intent, updates a bounded sequence tracker, and increments counters
- An observability foundation (7 counters, profiler scopes, `[HIERARCHY]` logs)
- A validation suite that tests wire format, parser safety, replay rejection, and protocol integrity — with zero graph behavior tests

All frozen runtime components (LiveSyncQueue, PendingAssetQueue, LiveSyncRunnable, Tick pipeline, FSyncTransformState, InterpolateTransforms) are untouched. All changes are additive.

**Caution:** Stage 6 MUST implement the following before enabling attachment application:

1. Deferred orphan queue (FINDING-001 mitigation)
2. Sequence-based "should I apply this now?" check using `GHierarchySequences.LastSequence`
3. Coordination with existing transform-stream `ParentGuid`/`bHasParent` fields
4. Cycle detection in the semantic event path (reuse existing `AttachToParent` logic)

The sequence tracker already provides the necessary foundation: `GHierarchySequences` records the highest sequence seen per child GUID. When Stage 6 implements attachment application, HandleHierarchy must check: "Has this sequence been applied? If the tracker says we've seen it but the graph doesn't reflect it, apply it now." This forward-compatible design eliminates the need to replay the entire packet stream on reconnect — the tracker already knows what was acknowledged.

**No rework of Stages 0–5 is required before Stage 6 begins.**
