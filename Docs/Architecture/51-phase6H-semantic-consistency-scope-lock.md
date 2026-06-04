# Phase 6H — Semantic Consistency Hardening (Scope Lock)

**Status**: IMPLEMENTED (no closeout stage)
**Last Updated**: 2026-06-02
**Current Implementation**: `UELiveSyncSubsystem_Phase6H.inl` (1134 lines)
**Invariants Doc**: `Docs/CRITICAL_INVARIANTS.md` §K (10 invariants)

---

## 1. Definition

**Semantic Consistency** is the property that all Phase 6 semantic lanes (rename, visibility, hierarchy, delete, collection, identity) produce deterministic, idempotent, and replay-safe results regardless of event ordering within and across packets, ticks, and reconnect cycles.

It is **not** a new feature or protocol — it is a **diagnostics + hardening** layer that detects violations, verifies invariants, and provides stress-testing utilities without mutating any runtime state.

---

## 2. Lanes Covered

| Lane | Packet | Phase | Current State |
|------|--------|-------|---------------|
| Rename | 0x0C | 6A/B | STABILIZED |
| Visibility | 0x0B | 6C | STABILIZED |
| Hierarchy | 0x0D | 6D | COMPLETE |
| Delete | 0x0E | 6E | COMPLETE |
| Collection | 0x0F | 6F | IMPLEMENTED |
| Identity | (metadata) | 6G | IMPLEMENTED |

---

## 3. Ordering Invariants

### 3.1 Intra-Packet Ordering

Within a single packet, semantic events are processed in fixed order:

```
Visibility (0x0B) → Rename (0x0C) → Hierarchy (0x0D) → Delete (0x0E) → Collection (0x0F) → Transform (0x01)
```

**Rule ORD-1**: Create (0x03) must precede all other operations for the same GUID within a packet.

**Rule ORD-2**: If Delete and any other semantic event arrive for the same GUID in the same packet, the handler order determines the final state (Delete must be last to ensure other handlers see a valid actor).

### 3.2 Cross-Packet Ordering

**Rule ORD-3**: Across packets, FIFO order within the replay buffer (`GWorldReplayBuffer`) determines replay sequence. `CheckReplayDependencies` in `UELiveSyncSubsystem_Replay.inl` validates create-before-all for every replay entry.

**Rule ORD-4**: `PacketStaleReplayOrder` and `PacketReplaySequenceGap` counters exist (declared in SyncTypes.h) but are **never incremented** — stale replay ordering is detected but not tracked.

### 3.3 Tick Pipeline Ordering

```
ProcessQueuedPackets
  → EvictStaleTransformStates
  → InterpolateTransforms
  → ResolvePendingAttachments
  → ResolveHierarchyAttachments      ← AFTER runtime attachment resolution (FINDING-009)
  → RecoverMissingActors
  → ResolvePendingAssets
  → ResolvePendingMaterials
  → ReconstructCompletedMeshes
  → ValidateHierarchy                ← every 300 frames
  → TickPhase6H                      ← every 300 frames
  → TickMetrics / TickSafetyMonitors
```

**Rule ORD-5**: `ResolveHierarchyAttachments` must run after `ResolvePendingAttachments` to ensure the runtime attachment graph is settled before semantic deferred attachments are applied.

**Rule ORD-6**: `TickPhase6H` must run at ≤ 300 frame intervals (diagnostics must not impact production performance). Verified by `Phase6HFrameCounter % Phase6HRunInterval` gate.

---

## 4. Replay / Idempotency Invariants

### 4.1 Per-Handler Sequence Rejection

All semantic handlers implement per-GUID monotonic sequence rejection:

| Handler | Tracker Type | Rejects When | Reset On |
|---------|-------------|-------------|----------|
| GRenameSequences | TMap<FGuid, uint32> | IncomingSeq ≤ LastSeq | StopNetworkThread, ConsoleReset |
| GHierarchySequences | TMap<FGuid, uint32> | IncomingSeq ≤ LastSeq | StopNetworkThread, ConsoleReset |
| GVisibilitySequences | TMap<FGuid, uint32> | IncomingSeq ≤ LastSeq | StopNetworkThread, ConsoleReset |
| GDeleteSequences | FDeleteSequenceTracker | IncomingSeq ≤ LastSeq | StopNetworkThread, ConsoleReset |
| GCollectionSequences | TMap<FGuid, TMap<FGuid, uint32>> | Per-collection per-GUID staleness | StopNetworkThread, ConsoleReset |

**Rule REP-1**: Sequence trackers enforce at-most-once delivery. A repeated packet with `seq ≤ lastSeq` is silently rejected.

**Rule REP-2**: All sequence trackers are cleared on `StopNetworkThread()` and `ConsoleReset()` — no sequence state survives disconnect.

### 4.2 Tombstone Rejection

**Rule REP-3**: `HandleRename`, `HandleVisibility`, `HandleHierarchy` reject incoming events if the GUID is tombstoned. `HandleDelete` rejects if the GUID is ALREADY tombstoned (prevents double-delete). `HandleCreateObject` rejects if the GUID is tombstoned.

**Rule REP-4**: Tombstones (`GDeleteTombstoneMap`) are cleared on `StopNetworkThread()` and `ConsoleReset()`. Tombstones do NOT survive reconnect.

### 4.3 Replay Fuzz

**Rule REP-5**: `RunReplayFuzz` operates on a **shuffled copy** of the replay buffer — never modifies the live buffer in place (6H-3 invariant). Simulates reorder violations and duplication patterns.

**Rule REP-6**: Replay buffer (`GWorldReplayBuffer`) is cleared on `StopNetworkThread` and `ConsoleReset` (RD-5 invariant). Max 4096 entries.

---

## 5. Tombstone / Delete Interaction Rules

### 5.1 Lifecycle

```
Create (0x03)       → Spawn actor, register in ActorCache
Delete (0x0E / 0x04) → Destroy actor, set tombstone, clear per-GUID registries
```

**Rule DEL-1**: After a Delete, the tombstone blocks ALL subsequent semantic events for that GUID until a new Create with a fresh sequence number arrives (or ConsoleReset clears the tombstone).

### 5.2 Delete + Rename

- If **Rename arrives first**: `GRenamePersistentLabel` is set, `SetActorLabel()` is called.
- If **Delete arrives second**: Actor is destroyed. `GRenamePersistentLabel` is NOT cleared (RN-2 invariant).
- If **Create arrives later**: `HandleCreateObject` restores the label from `GRenamePersistentLabel`.
- Result: Rename labels survive delete → create cycles.

### 5.3 Delete + Hierarchy

- If **Hierarchy arrives after Delete**: Rejected by tombstone check in `HandleHierarchy`. The hierarchy event is lost.
- If **Hierarchy arrives before Delete**: Attachment is applied normally. Delete destroys the actor, clearing the attachment.
- Implication: Child actors in a deleted parent chain must be deleted independently.

### 5.4 Delete + Collection

- Collection membership is metadata-only (not actor-bound). A delete does NOT trigger collection membership cleanup per-GUID.
- `GCollectionMembership` persists across delete/create cycles for the same GUID.
- **Rule DEL-2**: Collection membership for a deleted GUID becomes an orphan. `CheckCollectionAuthority` (Goal B) detects orphan membership: GUID in collection but not in ActorCache → increments `AuthorityCollectionDivergence`.

### 5.5 Delete + Visibility

- If **Visibility arrives after Delete**: Rejected by tombstone check in `HandleVisibility`.
- If **Visibility arrives before Delete**: Applied normally. Delete destroys actor. Visibility state is lost.

---

## 6. Reconnect / Snapshot Replay Rules

### 6.1 What Survives Reconnect

| Registry | Survives StopNetworkThread? | Cleared On |
|----------|---------------------------|------------|
| GRenamePersistentLabel | YES (RN-2) | ConsoleReset only |
| ActorCache | NO | StopNetworkThread (+ rebuilt via BuildActorCache) |
| TransformStates | NO | StopNetworkThread |
| AssetMetadata / MaterialMetadata | YES | New SessionGUID |
| AssetPathCache / MaterialPathCache | YES | New SessionGUID |
| GDeleteTombstoneMap | NO | StopNetworkThread |
| GWorldReplayBuffer | NO | StopNetworkThread |
| All Sequence Trackers | NO | StopNetworkThread |
| PendingHierarchyAttachments | YES | StopNetworkThread + ConsoleReset + HandleEndSnapshot |

### 6.2 Snapshot Replay Ordering

**Rule REC-1**: Snapshot replay sends creates in depth-sorted order (parents before children) so that `HandleHierarchy` finds valid parent actors.

**Rule REC-2**: `ResolveHierarchyAttachments` is cleared at the end of snapshot replay via `HandleEndSnapshot()` → `PendingHierarchyAttachments.Empty()`.

**Rule REC-3**: `RunReconnectStress` verifies `GRenamePersistentLabel` count is unchanged after `StopNetworkThread` (6H-5 invariant). Verifies `GWorldReplayBuffer` is cleared (6H-7 check).

**Rule REC-4**: `VerifyReplayDeterminism` (Goal E) performs full domain-by-domain comparison: saves state, replays buffer, compares rename/collection/lifecycle/transform domains, then restores. Reports drift by category.

---

## 7. Conflict Rules (Multiple Semantic Lanes, Same GUID)

### 7.1 Create Stampede

**Rule CONF-1**: `HandleCreateObject` checks tombstone + ActorCache before spawning. If the actor exists, it does NOT re-spawn — updates the existing entry.

**Rule CONF-2**: If the same GUID appears in a Create and a Delete in the same packet, the processing order determines the result (Create → Delete = deleted; Delete → Create = exists). Packet format guarantees at most one create event per GUID per packet.

### 7.2 Rename + Hierarchy (Same GUID, Same Tick)

- **Rename**: Updates `GRenamePersistentLabel` + `SetActorLabel()`.
- **Hierarchy**: Attaches/detaches actor. Does NOT read or write the rename registry.
- No conflict — independent registries, both non-mutating on each other.
- **Detection**: `CheckTransformGateSemanticEvents` (Goal F) detects if a rename was gated behind transform diff — compares `GRenamePersistentLabel` against live `GetActorLabel()`.

### 7.3 Hierarchy + Transform (Same GUID, Same Tick)

- **Transform**: Updates `TransformStates` (interpolation target), applied on next InterpolateTransforms.
- **Hierarchy**: Changes attachment parent. `AttachToActor` with `KeepWorldTransform` preserves world transform.
- Potential conflict: After hierarchy attach, the interpolated local transform target becomes invalid (the parent space changed). The next tick's transform packet will correct this.
- **Detection**: `CheckParentAuthority` (Goal B) detects mismatch between `FSyncTransformState.bHasParent` and actual `GetAttachParentActor()` — increments `AuthorityParentMismatch`.

### 7.4 Delete + Create (Same GUID, Cross-Packet)

- If Delete arrives, then Create arrives later with the same GUID: `HandleCreateObject` checks tombstone first — **rejects** (would accept if ConsoleReset clears tombstone between them).
- If Create arrives, then Delete arrives: Normal lifecycle.
- **Detection**: `PacketHierarchyBeforeCreate` / `PacketRenameBeforeCreate` etc. counters detect semantic events that arrive before the create event for that GUID.

### 7.5 Visibility + Delete (Same GUID, Same Tick)

- Visibility handler runs first (0x0B before 0x0E in processing order). It calls `FindActorFast` which returns the actor. Toggle is applied.
- Delete handler runs second. It calls `FindActorFast`, destroys the actor, sets tombstone.
- Result: Visibility is applied then immediately undone by the delete. This is correct — the visibility event was authoritative at the time it was sent, but the delete supersedes it.
- **Detection**: Not explicitly detected by Phase 6H — both handlers execute correctly independently.

---

## 8. Current Implementation State

### 8.1 What Is Implemented (1134 lines in Phase6H.inl)

| Goal | Component | Function | Status |
|------|-----------|----------|--------|
| **A** | Packet ordering validation | `ValidatePacketOrdering` | **IMPLEMENTED** — checks create-before-X for Hierarchy (0x0D), Rename (0x0C), Visibility (0x0B), Collection (0x0F). Also detects duplicate attach/detach for Hierarchy. |
| A | Stale replay order counter | `PacketStaleReplayOrder` | **DECLARED, NEVER INCREMENTED** |
| A | Replay sequence gap counter | `PacketReplaySequenceGap` | **DECLARED, NEVER INCREMENTED** |
| **B** | Semantic authority audit | `VerifySemanticState` | **IMPLEMENTED** — iterates ActorCache, checks 4 domains |
| B | Parent authority | `CheckParentAuthority` | **IMPLEMENTED** — compares `bHasParent`/`ParentGuid` against live actor |
| B | Visibility authority | `CheckVisibilityAuthority` | **STUB** — always returns true (toggle events, not state stream) |
| B | Rename authority | `CheckRenameAuthority` | **IMPLEMENTED** — compares `GRenamePersistentLabel` against `GetActorLabel()` |
| B | Collection authority | `CheckCollectionAuthority` | **IMPLEMENTED** — detects orphan membership |
| B | Dump authority state | `DumpAuthorityState` | **IMPLEMENTED** — per-actor report with drift detection |
| **C** | Replay fuzz | `RunReplayFuzz` | **IMPLEMENTED** — reorder shuffle, duplication analysis, rollback safety |
| C | Hierarchy stress | `RunHierarchyStress` | **IMPLEMENTED** — random attach/detach with cycle detection gate |
| C | Reconnect stress | `RunReconnectStress` | **IMPLEMENTED** — stop/start cycles, verifies RN-2 + RD-5 invariants |
| **D** | Burst metrics | `GetBurstMetrics` | **IMPLEMENTED** — peak packets/tick, replay growth, rollback/divergence |
| D | Per-tick burst tracking | `Phase6HBurstTickPacketCount` / `Phase6HBurstTickPeak` | **IMPLEMENTED** — tracked in ProcessQueuedPackets |
| **E** | Replay determinism verification | `VerifyReplayDeterminism` | **IMPLEMENTED** — snapshots all domains, replays, compares domain-by-domain, restores |
| **F** | Known-bad-pattern enforcement | `EnforceKnownBadPatterns` | **IMPLEMENTED** — runs `CheckTransformGateSemanticEvents` + `CheckStaleLocalAuthority` |
| F | Transform-gated semantic events | `CheckTransformGateSemanticEvents` | **IMPLEMENTED** — heuristic: rename in registry but label not applied |
| F | Stale local authority | `CheckStaleLocalAuthority` | **IMPLEMENTED** — detects KBP-1, KBP-2, KBP-3 patterns |
| F | Hierarchy overwrite from transform | `KBPHierarchyOverwriteFromTransform` | **DECLARED, NEVER INCREMENTED** |

### 8.2 Counters (Declared in FLiveSyncStats, SyncTypes.h lines 775-814)

**Packet ordering counters** (Goal A):
- `PacketHierarchyBeforeCreate`, `PacketRenameBeforeCreate`, `PacketVisibilityBeforeCreate`, `PacketCollectionBeforeCreate` — all incremented in `ValidatePacketOrdering`
- `PacketDuplicateAttachDetected`, `PacketDuplicateDetachDetected` — incremented in `ValidatePacketOrdering`
- `PacketStaleReplayOrder` — **declared, never set**
- `PacketReplaySequenceGap` — **declared, never set**

**Semantic authority counters** (Goal B):
- `AuthorityParentMismatch`, `AuthorityRenameMismatch`, `AuthorityCollectionDivergence` — all incremented
- `AuthorityVisibilityMismatch`, `AuthorityStaleLocalFlag` — declared but visibility is stub (always returns true), stale local is not incremented

**Burst metric counters** (Goal D):
- `BurstReplayQueueGrowthPeak` — declared, never updated by live code
- `BurstRollbackFrequency`, `BurstDivergenceFrequency` — declared, never updated by live code

**Replay determinism counters** (Goal E):
- `ReplayDeterminismVerifyCount`, `ReplayDeterminismPassCount`, `ReplayDeterminismFailCount` — all incremented
- `ReplayDomainCollectionHash`, `ReplayDomainLifecycleHash`, `ReplayDomainRenameHash`, `ReplayDomainTransformHash` — all incremented

**KBP counters** (Goal F):
- `KBPTransformGatedSemantic`, `KBPStaleLocalAfterDetach`, `KBPWorldLocalAuthorityMixing` — all incremented
- `KBPHierarchyOverwriteFromTransform` — **declared, never incremented**
- `KBPReplayRollbackIncomplete` — declared, never incremented by live code

---

## 9. Known Gaps

| # | Gap | Area | Severity | Notes |
|---|-----|------|----------|-------|
| G1 | `PacketStaleReplayOrder` never incremented | Goal A | Low | Counter exists but no detection logic calls it |
| G2 | `PacketReplaySequenceGap` never incremented | Goal A | Low | Counter exists but no detection logic calls it |
| G3 | `CheckVisibilityAuthority` is a stub | Goal B | Low | Visibility is a toggle event, not state stream — hard to verify |
| G4 | `KBPHierarchyOverwriteFromTransform` never incremented | Goal F | Low | Counter exists but no detection logic calls it |
| G5 | `BurstReplayQueueGrowthPeak` never updated by live code | Goal D | Low | Only calculated in `GetBurstMetrics` as current size / elapsed time |
| G6 | `KBPReplayRollbackIncomplete` never incremented | Goal F | Low | Counter exists but no detection logic calls it |
| G7 | No standalone tests for Phase 6H diagnostics | Testing | Medium | All validation relies on runtime UE execution; no Python test harness |
| G8 | Phase 6H undocumented in STATUS.md | Documentation | Low | No mention of Phase 6H in STATUS.md |
| G9 | No scope-lock doc existed before this document | Documentation | Low | Implementation predates scope-lock |

---

## 10. Acceptance Criteria

### Must-Have (for Phase 6H COMPLETE)

1. **All 8 console commands registered** and functional:
   - `UE.LiveSync.ValidatePacketOrdering` — dumps ordering counters
   - `UE.LiveSync.VerifySemanticState` — runs semantic authority audit
   - `UE.LiveSync.DumpAuthorityState` — per-actor authority report
   - `UE.LiveSync.RunReplayFuzz [seed] [iterations]` — replay fuzz simulation
   - `UE.LiveSync.RunHierarchyStress [objects] [ops]` — hierarchy stress test
   - `UE.LiveSync.RunReconnectStress [cycles]` — reconnect stress test
   - `UE.LiveSync.VerifyReplayDeterminism` — domain-by-domain replay verification
   - `UE.LiveSync.EnforceKnownBadPatterns` — runtime KBP detection

2. **ValidatePacketOrdering** called for every packet in `ProcessQueuedPackets` — **DONE** (line 2445).

3. **TickPhase6H** runs integrated in the tick pipeline at ≤ 300 frame intervals — **DONE** (line 1862).

4. **Non-mutation invariant**: All Phase 6H functions must only increment counters and log — **VERIFIED** (6H-1 through 6H-10 invariants).

5. **All 10 CRITICAL_INVARIANTS.md §K invariants** documented and followed — **DONE**.

### Nice-to-Have (deferred)

6. **Implement stale counter incrementers**: Add detection logic that increments `PacketStaleReplayOrder`, `PacketReplaySequenceGap`, `KBPHierarchyOverwriteFromTransform`.

7. **Standalone test harness**: Python tests that import `FLiveSyncStats` counter definitions and verify detection behavior by injecting test packets into a simulated `ProcessBinaryPacket` pipeline.

8. **Visibility authority**: Replace `CheckVisibilityAuthority` stub with actual visibility state comparison (requires tracking last-applied visibility per GUID).

---

## 11. Test Plan

### 11.1 Existing Coverage (Runtime-only, requires UE)

Phase 6H has no dedicated test file. All validation is runtime-only:
- Console commands can be invoked from UE console
- `ValidatePacketOrdering` runs automatically per-packet
- `TickPhase6H` runs automatically every 300 frames

### 11.2 Recommended Tests

| Test | Type | Covers | Priority |
|------|------|--------|----------|
| Validate order before create | Python sim | Goal A — create-before-X for all 4 semantic types | High |
| Duplicate attach detection | Python sim | Goal A — duplicate attach/detach for hierarchy | High |
| Parent authority mismatch | Python sim | Goal B — CheckParentAuthority detects drift | Medium |
| Rename authority mismatch | Python sim | Goal B — CheckRenameAuthority detects drift | Medium |
| Reconnect stress RN-2 invariant | Python sim | Goal C — GRenamePersistentLabel survives reconnect | Medium |
| Replay fuzz determinism | Python sim | Goal C — shuffled replay detects ordering violations | Low |
| Stale local after detach KBP | Python sim | Goal F — KBPStaleLocalAfterDetach detection | Medium |
| Stale root flag detection | Python sim | Goal F — AuthorityStaleRootFlag detection | Medium |
| TickPhase6H frequency gate | Code review | Invariant 6H-8 — runs at ≤ 300 frames | High |
| Console command registration | Code review | All 8 commands registered | High |

---

## 12. Recommended Implementation Stages

Phase 6H is **already implemented** (1134 lines of production code, 8 console commands, active tick integration). The remaining work is **closeout and gap-filling**:

### Stage 1 — Closeout: Stale Counter Incrementers

Add detection logic for the 4 stale counters:
- `PacketStaleReplayOrder` — increment when a replay entry appears out of order vs. the live sequence tracker
- `PacketReplaySequenceGap` — increment when sequence number jumps > 1 between consecutive packets for the same GUID
- `KBPHierarchyOverwriteFromTransform` — increment when a hierarchy event arrives at the same tick an interpolated transform was applied to the same GUID
- `BurstReplayQueueGrowthPeak` — track peak ReplayBuffer.Num() in TickPhase6H

**No protocol change. No packet layout change. No new packet types.**

### Stage 2 — Closeout: Visibility Authority

Replace `CheckVisibilityAuthority` stub with actual detection:
- Track last-applied visibility state per GUID (add optional map or reuse `GVisibilitySequences`)
- Compare against `Actor->IsTemporarilyHiddenInEditor()` during audit
- Increment `AuthorityVisibilityMismatch` on drift

**No protocol change. No packet layout change. No new packet types.**

### Stage 3 — Closeout: Standalone Tests

Create `tests/phase6h_semantic_consistency.py` with Python-simulated tests for:
- ValidatePacketOrdering logic (create-before-X for each semantic type)
- Duplicate attach/detach detection
- Sequence staleness rejection simulation
- Reconnect stress invariants (RN-2, RD-5)
- KBP pattern detection

**No code changes to UE plugin.**

### Stage 4 — Documentation + STATUS.md Update

- Add Phase 6H section to STATUS.md (this scope-lock doc)
- Mark Phase 6H status in the Phase 6 progress table

---

## 13. Constraints

- **No protocol changes.**
- **No packet layout changes.**
- **No new packet types.**
- **No new packet-type values.**
- **No version bump.**
- **No runtime state mutation** in diagnostics functions (CRITICAL_INVARIANTS.md §K).
- **No auto-correction** of detected bad patterns (6H-7).
- **TickPhase6H must run at ≤ 300 frame intervals** (6H-8).
