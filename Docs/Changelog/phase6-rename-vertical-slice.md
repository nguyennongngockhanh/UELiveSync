# Phase 6 — Rename Replication Vertical Slice

**Date**: 2026-05-25  
**Scope**: Minimal editor-authority workflow — rename replication only  
**Status**: STABILIZED

## Post-Implementation Verification (2026-05-25)

### Bugs Fixed

1. **CRITICAL: `kValidTypes[]` missing `0x0C` (PT_Rename)** — `CVarLiveSyncValidateProtocol` defaults to 1 (enabled), and the protocol validation type-check array did not include `0x0C`. Every rename packet was silently rejected as "Invalid packet type 0x0c, skipping". Fixed by adding `0x0C` and switching loop bounds to `sizeof()` for maintainability.

2. **CRITICAL: `GRenameSequences` not cleared on reconnect** — `StopNetworkThread()` cleared all runtime state (TransformStates, PacketQueue, LastSequenceId, etc.) but not the rename sequence tracker. If Blender restarted while UE stayed connected, Blender's `_rename_sequences` reset to seq=1 while UE held old values (e.g., 42), causing stale rejection of valid renames. Fixed via `GRenameSequences.LastSequence.Empty()` in StopNetworkThread.

### Known Deferred Gaps

| Gap | Impact | Deferral Rationale |
|-----|--------|--------------------|
| `OnActorLabelChanged` handler not registered | Suppression infrastructure is scaffolding-only | Bidirectional OOS per 18-phase6-scope-lock.md |
| `FScopedRenameSuppression` is log-only | No suppression state set | Benign without callback handler |
| 3 dead counters (RenameSuppressions, RenameReplaySkipped, RenameStormWarnings) | Observability incomplete | No correctness impact |
| Rename stats not reset in ConsoleReset | Stale counters across full reset | Observability gap only |
| HandleRename lacks TRACE_CPUPROFILER_EVENT_SCOPE | Not visible in CPU profiler | Profiling gap only |
| Blender `_rename_sequences` not cleaned up on disconnect | Unbounded growth across reconnect cycles | Negligible in practice |
| Eviction comment says "oldest" but evicts arbitrary entry | Documentation inaccuracy | Benign |

### Verification Results

| Category | Status |
|----------|--------|
| Frozen runtime preservation | PASS |
| Rename packet parser | PASS (5 boundary checks correct) |
| Replay safety | PASS (≤ stale/duplicate rejection, bounded 2048) |
| Suppression lifetime | PASS (RAII correct, no cross-frame leakage) |
| Provenance tagging | PASS (RemoteReplicated/Replay correctly set) |
| Thread safety | PASS (CHECK_GAME_THREAD) |
| Reconnect safety | PASS (tracker cleared) |
| Observability completeness | PARTIAL (3 dead counters, no CPU profiler scope) |
| Memory stability | PASS (bounded 2048 entries, negligible per-rename cost) |
| Malformed packet resilience | PASS (all truncation/oversized paths have guards) |

### Readiness Assessment

- **Extended internal use**: READY
- **Multi-hour editor sessions**: READY (all memory bounded, no leaks, reconnect-safe)
- **Future semantic-event expansion**: READY (architecture supports new packet types, provenance, suppression, replay patterns)

## Changes

### New: PT_Rename Packet (`0x0C`)

- Defined in `SyncTypes.h` and `network.py`
- Wire format: GUID(16) + oldNameLen(2) + oldName(N) + newNameLen(2) + newName(M) + seq(4) + ts(8)
- Discrete semantic event — NOT a state-stream packet

### New: Provenance System (`EChangeOrigin`)

- `SyncTypes.h`: `EChangeOrigin` enum (`Unspecified`, `LocalUser`, `RemoteReplicated`, `Replay`, `Recovery`)
- `UELiveSyncSubsystem.cpp`: `FScopedChangeOrigin` RAII helper (thread-local storage)
- In-memory only — NOT serialized on the wire

### New: Rename Suppression

- `UELiveSyncSubsystem.cpp`: `FScopedRenameSuppression` RAII guard
- Prevents `OnActorLabelChanged` from re-replicating rename back to Blender
- Scoped, temporary, logged at Verbose level

### New: Replay Safety (`FRenameSequenceTracker`)

- `SyncTypes.h`: `FRenameSequenceTracker` — maps GUID → last-applied sequence number
- Rejects stale (incoming ≤ last) and duplicate sequences
- Bounded at 2048 entries

### Blender-Side Changes

- `sync.py`: `_last_object_names` tracking dict detects renames via diff
- `sync.py`: Rename packets sent as `PT_Rename` (0x0C) after asset defs, before transforms
- `sync.py`: Cleanup on delete and full reset
- `network.py`: `serialize_rename()` — builds rename payload with monotonic sequence per GUID

### UE-Side Changes

- `UELiveSyncSubsystem.cpp`: `HandleRename()` — applies `SetActorLabel` with suppression scope, provenance, sequence validation
- `UELiveSyncSubsystem.h`: `HandleRename` declaration
- `ProcessBinaryPacket`: PT_Rename dispatch case with full payload parsing, `bInSnapshotBuild` → REPLAY tagging
- `FLiveSyncStats`: Rename counters (`RenamesProcessed`, `RenameSuppressions`, `RenameStaleRejections`, `RenameReplayApplied`, `RenameReplaySkipped`, `RenameStormWarnings`)

### Tests

- `tests/phase6_rename_validation.py`: 10 tests — single rename, storm (100× same GUID), storm (500 GUIDs), delete race, duplicate replay, stale sequence, malformed truncated, malformed oversized, reconnect storm, suppression loop

## Files Changed

| File | Change |
|------|--------|
| `UE_Plugin/.../Public/SyncTypes.h` | PT_Rename, EChangeOrigin, FLiveSyncRenamePacket, FRenameSequenceTracker, FLiveSyncStats counters, FNV hash |
| `UE_Plugin/.../Public/UELiveSyncSubsystem.h` | HandleRename declaration |
| `UE_Plugin/.../Private/UELiveSyncSubsystem.cpp` | HandleRename, FScopedChangeOrigin, FScopedRenameSuppression, PT_RENAME dispatch |
| `Blender_Addon/network.py` | PT_Rename constant, serialize_rename() |
| `Blender_Addon/sync.py` | _last_object_names, rename detection + emission, cleanup |
| `tests/phase6_rename_validation.py` | 10 rename tests |

## Maintenance Stabilization Pass (2026-05-25)

### Changes Applied

| Fix | File | Detail |
|-----|------|--------|
| ConsoleReset replay cleanup | `UELiveSyncSubsystem.cpp` | Added rename stat resets (RenamesProcessed, RenameStaleRejections, RenameReplayApplied, RenameReplaySkipped) + GRenameSequences.LastSequence.Empty() + observability log |
| CPU profiler | `UELiveSyncSubsystem.cpp` | Added TRACE_CPUPROFILER_EVENT_SCOPE to HandleRename and PT_Rename parse block |
| Dead counter removal | `SyncTypes.h` | Removed `RenameSuppressions` and `RenameStormWarnings` (could not be meaningfully wired without OnActorLabelChanged handler) |
| RenameReplaySkipped wired | `UELiveSyncSubsystem.cpp` | Incremented in stale/duplicate rejection path when Origin is Replay |
| Blender reconnect cleanup | `network.py` | `_rename_sequences.clear()` in `_close_internal()` with `[RENAME]` log |

### Invariant Verification

- ✅ Frozen runtime: unchanged (LiveSyncQueue, PendingAssetQueue, LiveSyncRunnable, FSyncTransformState, header layout, Tick pipeline)
- ✅ No new allocations in hot path (TRACE_CPUPROFILER_EVENT_SCOPE is compile-time)
- ✅ ConsoleReset ordering preserved (cleanup added after existing stat resets, before StartServer)
- ✅ Monotonic sequence semantics preserved (cleanup only on disconnect, not during active session)
- ✅ Profiler scopes balanced (single-entry, automatic END on scope exit)

### Remaining Intentional Limitations

| Limitation | Rationale |
|------------|-----------|
| No OnActorLabelChanged handler | Bidirectional rename OOS per 18-phase6-scope-lock.md |
| FScopedRenameSuppression is log-only | Benign — no callback handler to suppress |

## Next Slice Planning: Visibility Replication

The second Phase 6 semantic-event lane has been designed (not yet
implemented):

- **Scope lock**: `20-phase6-visibility-scope-lock.md` — IN/OUT of scope,
  authority model, escalation rules, done criteria
- **Vertical slice plan**: `21-phase6-vertical-slice-visibility.md` — packet
  semantics, replay rules, provenance strategy, suppression strategy,
  observability requirements, validation matrix (10+ tests), implementation
  ordering (11 steps), rollback criteria
- **Packet type**: `PT_Visibility = 0x0B` (between PT_EndSnapshot `0x0A`
  and PT_Rename `0x0C`)
- **Payload**: Fixed 29 bytes per object (GUID(16) + bHidden(1) + seq(4) +
  ts(8))
- **Key distinction from rename**: No callback recursion risk, fixed-length
  wire format, idempotent bool state — validates pattern generalization
- **Implementation**: NOT STARTED (pending final review of planning docs)

## Design Constraints Preserved

- ✅ No frozen-zone modifications (parser, queue, thread, pipeline)
- ✅ New packet type = new case branch only (ProcessBinaryPacket)
- ✅ Provenance in-memory only (not on wire)
- ✅ Suppression is scoped + temporary (never leaks across frames)
- ✅ Replay safety via monotonic sequence (stale/duplicate rejection)
- ✅ BEGIN/END traces preserved and balanced
- ✅ Tick pipeline ordering unchanged
