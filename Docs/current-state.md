# UELiveSync — Current State

**Generated**: 2026-05-25  
**Branch**: `main`  
**Phase**: Phase 6 — Live Editing System (Rename STABILIZED, Visibility IMPLEMENTED pending live validation)

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

### Phase 6C — Visibility Replication: IMPLEMENTED (pending live validation)

The second semantic-event vertical slice is structurally complete:

- **Packet type**: `PT_Visibility = 0x0B` — fixed 29 bytes per object (GUID(16)+bHidden(1)+seq(4)+ts(8))
- **FVisibilitySequenceTracker**: bounded 2048, stale/duplicate rejection via `<=`
- **FScopedVisibilitySuppression**: RAII guard (architectural consistency — no callback recursion risk)
- **FScopedChangeOrigin**: provenance tagging (RemoteReplicated / Replay)
- **FLiveSyncStats counters**: `VisibilityProcessed`, `VisibilityStaleRejections`, `VisibilityReplayApplied`, `VisibilityReplaySkipped`
- **TRACE_CPUPROFILER_EVENT_SCOPE**: `UELiveSync_HandleVisibility`, `UELiveSync_ProcessVisibilityPackets`
- **Blender**: `_last_visibility_state` diff + `hide_get()` detection + `serialize_visibility()`
- **Tests**: `tests/phase6_visibility_validation.py` — 12 tests (all auto-skip without UE)
- **Pending**: end-to-end live validation against UE editor on `:57000`

### Phase 6A/6B — Rename Replication: STABILIZED

Rename stabilization completed with 49/49 runtime audit checks passing,
stale and duplicate-replay rejection verified, all fix items resolved
(CPU profiler scopes, dead counters, reconnect cleanup, ConsoleReset):

- **Phase 6B report**: `Docs/Architecture/21-phase6b-runtime-confidence-report.md`
- **Stabilization findings**: 10 fixes applied (profiler scopes, counter cleanup, reconnect tracker clear, stale sequence eviction comment)
- **Verification methodology**: source-code audit (49 checks), failure injection, soak, replay robustness — all structural validation complete (UE-dependent execution pending)

### Phase 6 Documentation Consolidation

Semantic architecture conventions formalized:

- **`22-semantic-event-architecture-conventions.md`**: canonical reference for all semantic lanes — defines mandatory requirements (packet type, parser branch, GUID lookup, replay tracker, provenance, suppression, profiler, observability, bounded memory, reconnect cleanup), forbidden patterns, replay/ provenance/ suppression/ observability/ packet numbering/ frozen boundary/ future slice standards
- **Semantic lane inventory**: Rename (STABILIZED), Visibility (IMPLEMENTED pending live validation), Hierarchy/Lifecycle/Collection/Duplicate (PLANNED), Bidirectional/Generalized framework/ Transaction merge (DEFERRED)

Per canonical roadmap:
- **Phase 5**: Protocol Evolution & Runtime Stabilization ← COMPLETE
- **Phase 6**: Live Editing System ← Rename STABILIZED, Visibility IMPLEMENTED (pending live validation)
  - ✅ Rename replication (semantic event, Blender→UE, provenance, suppression, replay-safe, 49/49 audit)
  - ✅ Visibility/hidden state sync (semantic event, Blender→UE, provenance, suppression, replay-safe, 28/28 constructs, 12 tests)
  - ⏳ Collection/folder structure sync (planned, not started)
  - ⏳ Duplicate detection (planned, not started)
  - ⏳ Object create/delete lifecycle from editor (planned, not started)

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
| V5+ | Active | PT_Rename (0x0C), PT_Visibility (0x0B), semantic event lanes |
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

---

## Upcoming

| Phase | Description | Est. |
|-------|-------------|------|
| 6 | Live editing: rename (stable), visibility (pending live validation), collections, duplicate, lifecycle (rest TBD) | TBD |
| 7 | Animation & Sequencer sync | TBD |
| 8 | High-performance streaming | TBD |
| 9 | Production ecosystem | TBD |
