# UELiveSync — Current State

**Generated**: 2026-05-26 (updated Phase 6E: Stages 12-13 complete, STABILIZED)  
**Branch**: `main`  
**Phase**: Phase 6 — Live Editing System (Rename STABILIZED, Visibility STABILIZED, Hierarchy STABILIZED, Lifecycle STABILIZED)

---

## Phase 6 — Rename Replication Vertical Slice (2026-05-25)

The first Phase 6 editor-authority workflow has been implemented:

- **Packet type**: `PT_Rename = 0x0C` — discrete semantic event, NOT a state-stream packet
- **Provenance**: `EChangeOrigin` enum (`LOCAL_USER`, `REMOTE_REPLICATED`, `REPLAY`, `RECOVERY`) — in-memory only, not on wire
- **Suppression**: `FScopedRenameSuppression` RAII guard prevents `OnActorLabelChanged` callback re-replication
- **Replay safety**: `FRenameSequenceTracker` with monotonic sequences per-GUID — stale/duplicate replay rejection
- **Blender**: Rename detection in `sync.py` via `_last_object_names` diff; serialized by `serialize_rename()` in `network.py`
- **UE**: `HandleRename()` with provenance tagging, suppression scope, sequence validation, and `bInSnapshotBuild` → REPLAY tagging
- **Observability**: `[RENAME]` logs, `FLiveSyncStats` counters (`RenamesProcessed`, `RenameSuppressions`, `RenameStaleRejections`, `RenameReplayApplied`, `RenameReplaySkipped`), FNV checksum updated
- **Tests**: `tests/phase6_rename_validation.py` — 10 tests (single, storm, 500-GUID storm, delete race, duplicate replay, stale sequence, malformed truncated, malformed oversized, reconnect storm, suppression loop)

## Phase 5 Pre-Phase-6 Preparation (2026-05-25)

Before Phase 6 begins, the Phase 5 runtime foundation has been formally frozen:

- **Release tag**: `v0.5.0-stabilized` created locally
- **Core runtime frozen**: Freeze banners added to `UELiveSyncSubsystem.cpp`, `PendingAssetQueue.h`, `LiveSyncQueue.h`, `SyncTypes.h`, `LiveSyncRunnable.h`
- **Architecture docs created**:
  - `12-core-runtime-invariants.md` — packet lifecycle, thread/queue ownership, Tick ordering, parser invariants
  - `13-phase6-design-constraints.md` — unresolved authority questions for rename, visibility, collections, duplicate detection
  - `14-editor-sync-safety.md` — replication suppression rules, feedback loop prevention, rename storm prevention
  - `15-architecture-decision-records.md` — 15 ADRs covering protocol, threading, queue, pipeline, shutdown
  - `16-known-safe-modification-zones.md` — SAFE/CAUTION/HIGH-RISK/FROZEN modification zones
  - `17-phase6-readiness.md` — 14/14 readiness conditions complete
  - `18-phase6-scope-lock.md` — IN-SCOPE/OUT-OF-SCOPE definition, authority boundaries, escalation rules, "done" criteria
- **Profiling/debug infrastructure**: TRACE_CPUPROFILER_EVENT_SCOPE and BEGIN/END tracing explicitly documented as INTENTIONALLY RETAINED

## Completed Phases

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Foundation: Blender addon + UE plugin scaffold, basic TCP, V2 protocol | Done |
| 2 | Core sync: transform streaming, coordinate conversion, MESH-only filter | Done |
| 3 | Production hardening: thread safety, heartbeat, V3 protocol, reconnection, Actor cache | Done |
| 3.4–3.6 | Robustness: V4, CREATE/DELETE lifecycle, snapshot batching, watchdog | Done |
| 4 | Stability core: CVars, diagnostics bar, console commands, protocol validation | Done |
| 5A | Workflow: primitive UI, full-snapshot flag, DumpState/Ping/Stats/Reset | Done |
| 5B | Hierarchy authority model: local-space interpolation, attachment lifecycle | Done |
| 5C | Diagnostics & Editor UX: runtime metrics, debug overlay, Blender status UI | Done |
| 5D | Runtime stability: Asset Identity/V5 protocol, PendingAssetQueue, freeze investigation | Done |
| 5E | Stress testing: long-duration, large-scene, reconnect storm, malformed packet, observability | Done |

## Active Work

### Phase 6C — Visibility Replication: STABILIZED (2026-05-26)

The second semantic-event vertical slice is structurally complete and
operationally validated against a live UE 5.7.4 Editor:

- **Packet type**: `PT_Visibility = 0x0B` — fixed 29 bytes per object (GUID(16)+bHidden(1)+seq(4)+ts(8))
- **FVisibilitySequenceTracker**: bounded 2048, stale/duplicate rejection via `<=`
- **FScopedVisibilitySuppression**: RAII guard (architectural consistency — no callback recursion risk)
- **FScopedChangeOrigin**: provenance tagging (RemoteReplicated / Replay)
- **FLiveSyncStats counters**: `VisibilityProcessed`, `VisibilityStaleRejections`, `VisibilityReplayApplied`, `VisibilityReplaySkipped`
- **TRACE_CPUPROFILER_EVENT_SCOPE**: `UELiveSync_HandleVisibility`, `UELiveSync_ProcessVisibilityPackets`
- **Blender**: `_last_visibility_state` diff + `hide_get()` detection + `serialize_visibility()`
- **Tests**: `tests/phase6_visibility_validation.py` — 11/11 live PASS (4 SKIP — reconnect infra)
- **Live validation**: 9/9 stabilization criteria met — see `Docs/Architecture/23-phase6-live-runtime-validation.md`

### Phase 6A/6B — Rename Replication: STABILIZED

Rename stabilization completed with 49/49 runtime audit checks passing,
stale and duplicate-replay rejection verified, all fix items resolved
(CPU profiler scopes, dead counters, reconnect cleanup, ConsoleReset):

- **Phase 6B report**: `Docs/Architecture/21-phase6b-runtime-confidence-report.md`
- **Stabilization findings**: 10 fixes applied (profiler scopes, counter cleanup, reconnect tracker clear, stale sequence eviction comment)
- **Verification methodology**: source-code audit (49 checks), failure injection, soak, replay robustness — all structural validation complete (UE-dependent execution pending)

### Phase 6 Live Runtime Validation (2026-05-26)

Source-code structural audit completed. See `Docs/Architecture/23-phase6-live-runtime-validation.md`:

- **Visibility lane**: Full convention compliance (12/12 sections), zero forbidden patterns
- **Rename lane**: Full convention compliance (12/12 sections), zero forbidden patterns
- **Phase 6B audit**: 49/49 PASS — all runtime invariants intact
- **FNV protocol signature**: Defect found and fixed — `0x0B`/`0x0C` were missing from FNV hashes on UE and Blender sides
- **Frozen boundaries**: Zero violations across both lanes

**Visibility: STABILIZED** ✅ — all 9 live stabilization criteria met.
See `Docs/Architecture/23-phase6-live-runtime-validation.md`.

### Phase 6 Documentation Consolidation

Semantic architecture conventions formalized:

- **`22-semantic-event-architecture-conventions.md`**: canonical reference for all semantic lanes — defines mandatory requirements (packet type, parser branch, GUID lookup, replay tracker, provenance, suppression, profiler, observability, bounded memory, reconnect cleanup), forbidden patterns, replay/ provenance/ suppression/ observability/ packet numbering/ frozen boundary/ future slice standards
- **`23-phase6-live-runtime-validation.md`**: live runtime validation report — source-code audit, FNV fix, classification assessment
- **Semantic lane inventory**: Rename (STABILIZED), Visibility (STABILIZED), Hierarchy (STABILIZED), Lifecycle/Collection/Duplicate (DEFERRED — hierarchy prerequisite), Bidirectional/Generalized framework/ Transaction merge (DEFERRED)

### Phase 6E — Lifecycle/Delete Replication: STABILIZED (2026-05-26)

The fourth semantic-event vertical slice has been implemented through
Stages 0–13 and is now classified as STABILIZED:

- **Scope lock**: `Docs/Architecture/29-phase6E-lifecycle-scope-lock.md` (430 lines — IN/OUT scope, tombstone model, GUID lifetime rules, hierarchy invalidation policy, frozen-runtime audit)
- **Vertical slice design**: `Docs/Architecture/30-phase6E-vertical-slice-lifecycle.md` (1163 lines — packet definition, replay analysis, tombstone semantics, reconnect semantics, GUID lifetime rules, determinism proofs, frozen-runtime audit, complexity assessment)
- **Threat audit**: `Docs/Architecture/31-phase6E-lifecycle-threat-audit.md` (983 lines — adversarial design review, 16 findings: 4 P1, 10 P2, 3 P3. Verdict: GO WITH BLOCKERS)
- **Design remediation**: `Docs/Architecture/32-phase6E-remediation-summary.md` (234 lines — all 4 P1 findings resolved, verdict: GO FOR IMPLEMENTATION PLANNING)
- **Implementation plan**: `Docs/Architecture/33-phase6E-lifecycle-implementation-plan.md` — 14-stage bottom-up rollout, GO verdict
- **Stability review**: `Docs/Architecture/34-phase6E-stage0-3-stability-review.md` — Stage 0-3 frozen-runtime audit
- **Live runtime validation**: `Docs/Architecture/35-phase6E-live-runtime-validation.md` — 308/308 tests, 102/102 audit checks, 17/17 criteria met, STABILIZED verdict
- **Status**: STABILIZED ✅ — Stages 0–13 complete. 308/308 standalone tests pass, 102/102 audit checks pass.
- **Blender emission active**: Delete detection via `_known_guids` diff, `serialize_delete()` 28-byte fixed payload, per-GUID monotonic `_delete_sequences`
- **Delete lane fully wired end-to-end**: Blender detection → serialization → TCP send → UE parse → tombstone gate → actor destruction → child detach → tombstone insert

#### Stage 12 — Validation Expansion (2026-05-26)
- Tombstone FIFO eviction boundary at 2048 cap verified
- Reconnect clearing semantics (StopNetworkThread + ConsoleReset) fully verified
- Deferred delete ordering during snapshot rebuild verified (FIFO)
- Delete-after-create replay ordering verified
- Duplicate and stale delete replay rejection verified
- Delete of already-destroyed actor (three-barrier: stale→tombstone→missing)
- Parent delete with surviving detached children verified
- Child delete while parent survives verified
- Delete + hierarchy deferred queue interaction verified
- Delete during reconnect snapshot replay verified
- Mixed traffic: transforms+delete, rename+delete, visibility+delete, hierarchy+delete
- Batch delete storms: x100 and x500 verified
- Tombstone gating across all required handlers (rename, visibility, hierarchy, assetdef, create)
- Deferred queue overflow eviction at 2048 verified
- Sequence tracker overflow eviction at 2048 verified
- Malformed payload variations (truncation, zero-length, oversized, zero GUID)
- EndSnapshot deterministic FIFO ordering verified
- 21 new sections added, 114 new tests (194 → 308 total)

#### Stage 13 — Runtime Confidence / Stabilization (2026-05-26)
- 102/102 runtime audit checks pass (49 original + 53 new delete-lane checks)
- 17/17 stabilization criteria met
- Frozen-runtime: zero violations across all files
- Cross-lane: zero sequence coupling confirmed (delete never touches rename/vis/hierarchy trackers)
- Three-barrier stale rejection: sequence + tombstone + ActorCache — all verified
- Bounded behavior: all structures bounded at 2048 with FIFO eviction
- Observability: 8 counters, 3 profiler scopes, 11 log prefixes — all present
- Reconnect determinism: all state cleared on StopNetworkThread + ConsoleReset
- Consolidated runner created: `tests/run_phase6e_all.py` — supports `--quick`, `--standalone-only`, `--integration-only`
- Stabilization report: `Docs/Architecture/35-phase6E-live-runtime-validation.md`

#### Key Properties
- **Packet**: `PT_Delete = 0x0E`, 28-byte fixed payload (TargetGuid(16)+seq(4)+ts(8)), first terminal semantic event
- **Counters**: DeletePackets, DeleteProcessed, DeleteReplayApplied, DeleteReplaySkipped, DeleteStaleRejections, DeleteTombstoneRejections, DeleteMissingActor, DeleteDeferredDuringSnapshot (8 total)
- **Profiler**: UELiveSync_HandleDelete, UELiveSync_ProcessDeletePackets, UELiveSync_ProcessDeferredDeletes (3 scopes)
- **Suppression**: FScopedDeleteSuppression RAII guard
- **Zero cross-lane sequence coupling**: Delete tracker/handlers never touch rename, visibility, or hierarchy trackers
- **Tests**: `tests/phase6e_delete_validation.py` — 308 tests across 48 sections (Stages 0–13 coverage)

### Phase 6D — Hierarchy Replication: STABILIZED (2026-05-26)

The third semantic-event vertical slice is structurally complete and
operationally validated:

- **Scope lock**: `Docs/Architecture/24-phase6D-hierarchy-scope-lock.md` (467 lines — IN/OUT scope, orphan/cycle policy, frozen runtime separation, lifecycle/delete deferral)
- **Vertical slice design**: `Docs/Architecture/25-phase6D-vertical-slice-hierarchy.md` (1601+ lines — packet definition, replay chain analysis, deferred semantics, snapshot contract, invariants, orphan lifecycle, cycle prevention, runtime interaction, observability, failure-safety, complexity assessment)
- **Architecture review**: PASS — 9 findings, 0 blocking. See §14 in vertical slice design.
- **Implementation plan**: `Docs/Architecture/26-phase6D-hierarchy-implementation-plan.md` — 14 incremental stages, validation gates, rollback strategy.
- **Live validation report**: `Docs/Architecture/28-phase6D-live-runtime-validation.md` — 97/97 standalone tests pass, 7 integration SKIP (UE required), 49/49 Phase 6B audit PASS, classification STABILIZED.
- **Status**: STABILIZED ✅ — all static verification criteria met. 7 integration tests pending live UE confirmation.
- **Packet**: `PT_Hierarchy = 0x0D`, 44-byte fixed payload (ChildGuid(16)+ParentGuid(16)+seq(4)+ts(8)), all-zero ParentGuid = detach-to-root
- **Blender**: `_last_parent_guid` diff detection → `serialize_hierarchy()` → depth-sorted snapshot emission. Per-GUID monotonic sequences.
- **UE**: `HandleHierarchy()` → `ProcessHierarchyPackets()` → raw `AttachToActor()`/`DetachFromActor()` → `ResolveHierarchyAttachments()` deferred queue. Cycle detection via `WouldCreateHierarchyCycle()` (depth-256 bounded walk). Orphan lifecycle with 10-fast/10-slow/60-timeout cadence.
- **Counters**: HierarchyPackets, HierarchyProcessed, HierarchyStaleRejections, HierarchyReplayApplied, HierarchyReplaySkipped, HierarchyOrphans, HierarchyCycles, HierarchyDeferredResolved (8 total)
- **Profiler**: UELiveSync_HandleHierarchy, UELiveSync_ProcessHierarchyPackets, UELiveSync_ResolveHierarchyAttachments (3 scopes)
- **No runtime code has been modified** in frozen zones. All hierarchy code is additive with new case branches, new arrays, new functions.

Per canonical roadmap:
- **Phase 5**: Protocol Evolution & Runtime Stabilization ← COMPLETE
- **Phase 6**: Live Editing System ← Rename STABILIZED, Visibility STABILIZED, Hierarchy STABILIZED, Lifecycle STABILIZED
   - ✅ Rename replication (semantic event, Blender→UE, provenance, suppression, replay-safe, 49/49 audit)
   - ✅ Visibility/hidden state sync (semantic event, Blender→UE, provenance, suppression, replay-safe, 28/28 constructs, 11/11 live PASS, 9/9 soak PASS)
   - ✅ Hierarchy replication (STABILIZED — Blender detection + serialization + depth-sort snapshot + orphan lifecycle + cycle detection, 97/97 standalone PASS)
   - ✅ Lifecycle/delete replication (STABILIZED — Stages 0–13 complete, 308/308 standalone tests, 102/102 audit checks, 17/17 stabilization criteria)
   - ⏳ Collection/folder structure sync (blocked — lifecycle prerequisite)
   - ⏳ Duplicate detection (blocked — lifecycle prerequisite)
   - ⏳ Object create from editor (blocked — lifecycle prerequisite)

---

## Architecture Overview

```
Blender Main Thread                    UE Network Thread           UE Game Thread
┌─────────────────────┐               ┌──────────────────┐       ┌──────────────────────┐
│ Scene scan & diff   │               │ Recv()           │       │ ProcessQueuedPackets │
│ => TransformState[] │───TCP────────>│ => FLiveSyncPkt  │───Q──>│ => InterpolateTransf │
│ => AssetIdentity[]  │               │ Enqueue (MPSC)   │       │ => ResolveAssetDefs  │
└─────────────────────┘               └──────────────────┘       └──────────────────────┘
        │                                                                  │
Blender Daemon Thread                                                      │
┌─────────────────────┐                                                    │
│ socket.sendall()    │                                                    │
│ (non-blocking enq)  │                                                    │
└─────────────────────┘                                                    ▼
                                                                     AssetResolution:
                                                                     8/tick, exp backoff,
                                                                     live-swap mesh
```

---

## Protocol Versions

| Version | Status | Key Features |
|---------|--------|-------------|
| V2 | Legacy | 22-byte header, hex GUID, port 5000 |
| V3 | Stable | 24-byte header, binary GUID, packet types |
| V4 | Stable | Snapshot batching, local-transform flag |
| V5 | Active | PT_AssetDef (0x08), xxHash64 identity, 33B fixed payload |
| V5+ | Active | PT_Rename (0x0C), PT_Visibility (0x0B), PT_Hierarchy (0x0D), semantic event lanes |
| V4+ | Stable | V4+ objects always 81 bytes (primitive type byte at offset 80 for ALL V4+ payloads) |

---

## Key Files

| File | Role |
|------|------|
| `Blender_Addon/__init__.py` | Registration, UI panel, operators |
| `Blender_Addon/sync.py` | Core sync loop, scene iteration, diff detection |
| `Blender_Addon/network.py` | TCP client, binary serialization, threaded sender, xxHash64 |
| `UE_Plugin/.../UELiveSyncSubsystem.cpp/h` | Main game-thread orchestrator |
| `UE_Plugin/.../LiveSyncRunnable.cpp/h` | Dedicated network receive thread |
| `UE_Plugin/.../LiveSyncQueue.h` | Bounded MPSC packet buffer (128 entries) |
| `UE_Plugin/.../SyncTypes.h` | Structs, protocol constants, log category, FLiveSyncStats |
| `UE_Plugin/.../AssetIdentityTypes.h` | FAssetIdentityRef, FAssetMetadata (Phase 5D) |
| `UE_Plugin/.../PendingAssetQueue.h` | Bounded (2048) pending resolution queue (Phase 5D) |
| `UE_Plugin/.../SLiveSyncStatusWidget.cpp/h` | Compact status indicator panel |
| `UE_Plugin/.../SLiveSyncDiagnosticsWidget.cpp/h` | Full diagnostics panel |
| `Docs/Architecture/12-core-runtime-invariants.md` | Core runtime invariants (Phase 5 freeze) |
| `Docs/Architecture/13-phase6-design-constraints.md` | Phase 6 authority model constraints |
| `Docs/Architecture/14-editor-sync-safety.md` | Editor synchronization safety rules |
| `Docs/Architecture/15-architecture-decision-records.md` | 15 ADRs for major Phase 5 choices |
| `Docs/Architecture/16-known-safe-modification-zones.md` | SAFE/HIGH-RISK/FROZEN modification zones |
| `Docs/Architecture/17-phase6-readiness.md` | Phase 6 readiness checklist |
| `Docs/Architecture/18-phase6-scope-lock.md` | Phase 6 scope boundaries, authority model, escalation rules (rename) |
| `Docs/Architecture/19-phase6-vertical-slice-rename.md` | Rename replication vertical slice plan |
| `Docs/Architecture/20-phase6-visibility-scope-lock.md` | Visibility replication scope boundaries (planned) |
| `Docs/Architecture/21-phase6-vertical-slice-visibility.md` | Visibility replication vertical slice plan |
| `Docs/Architecture/21-phase6b-runtime-confidence-report.md` | Phase 6B runtime confidence report |
| `Docs/Architecture/22-semantic-event-architecture-conventions.md` | Semantic event architecture conventions |
| `Docs/Architecture/23-phase6-live-runtime-validation.md` | Live runtime validation report (Phase 6C visibility) |
| `Docs/Architecture/24-phase6D-hierarchy-scope-lock.md` | Phase 6D hierarchy scope lock — IN/OUT scope, orphan/cycle policy |
| `Docs/Architecture/25-phase6D-vertical-slice-hierarchy.md` | Phase 6D hierarchy vertical slice design — replay chains, deferred semantics, cycle detection |
| `Docs/Architecture/26-phase6D-hierarchy-implementation-plan.md` | Phase 6D hierarchy implementation plan — 14 stages, rollback, risk containment |
| `Docs/Architecture/28-phase6D-live-runtime-validation.md` | Phase 6D live runtime validation report — 97/97 tests, STABILIZED classification |
| `Docs/Architecture/29-phase6E-lifecycle-scope-lock.md` | Phase 6E lifecycle scope lock — IN/OUT scope, tombstone model, GUID lifetime rules |
| `Docs/Architecture/30-phase6E-vertical-slice-lifecycle.md` | Phase 6E lifecycle vertical slice design — replay chains, tombstone semantics, determinism proofs |
| `Docs/Architecture/31-phase6E-lifecycle-threat-audit.md` | Phase 6E threat audit — 16 findings (4 P1, 10 P2, 3 P3), adversarial review |
| `Docs/Architecture/32-phase6E-remediation-summary.md` | Phase 6E remediation record — all 4 P1 findings resolved |
| `Docs/Architecture/33-phase6E-lifecycle-implementation-plan.md` | Phase 6E lifecycle implementation plan — 14 stages, validation gates, rollback, 47 tests |
| `Docs/Architecture/34-phase6E-stage0-3-stability-review.md` | Phase 6E Stage 0–3 frozen-runtime audit — frozen boundary verification, parser isolation, cross-lane coupling, additive-only confirmation |

---

## Upcoming

| Phase | Description | Est. |
|-------|-------------|------|
| 6 | Live editing: rename (stable), visibility (stable), hierarchy (stable), lifecycle/delete (STABILIZED), collections, duplicate (rest TBD) | TBD |
| 7 | Animation & Sequencer sync | TBD |
| 8 | High-performance streaming | TBD |
| 9 | Production ecosystem | TBD |
