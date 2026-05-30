# UELiveSync — Project Initialization

**Load this before starting any task.**

## Recommended Task Startup Order

Do NOT begin broad repository exploration before reading these:

1. **`PROJECT_INIT.md`** — current status, protocol versions, dangerous areas (this file)
2. **`ARCHITECTURE.md`** — system topology, packet flow, tick pipeline, registries, invariants
3. **`CRITICAL_INVARIANTS.md`** — hard rules, do-not-break barriers, forbidden changes
4. **`BUG_ENTRYPOINTS.md`** — surgical entrypoints with exact files/functions/line numbers
5. **`KNOWN_GOOD_FLOWS.md`** — canonical working paths for all 9 major flows

Then use `TASK_PROMPT_TEMPLATES.md` to scope the specific task.

## Current Status

- **Phase 6**: Active — Live Editing System
- **Lanes STABILIZED**: Rename (0x0C), Visibility (0x0B), Hierarchy (0x0D), Lifecycle/Delete (0x0E)
- **Lanes IMPLEMENTED**: Collection (0x0F, Stages 0-7), World Replay (6G), Identity Stability (6G)
- **Runtime core**: FROZEN — Tick pipeline, queue, thread lifecycle, parser
- **Freeze checkpoint**: Active since 2026-05-27 — additive-only, no frozen-runtime modifications
- **66/66 invariants**: Verified across Structural, Runtime, Cross-Lane, Observability, Blender-Side
- **Phase 6H**: Active — Semantic Consistency Hardening (stabilization + determinism + replay-hardening)

## Active Protocol Versions

| Name | Value |
|------|-------|
| Magic | `0x4C56534D` |
| V3 header | 24 bytes |
| V4+ object | 81 bytes (incl primitive type byte at offset 80) |
| Default port | 57000 |
| Protocol signature | FNV-1a (sync.py:38-42, SyncTypes.h:755-761) |

## Critical Replay Invariants

1. **Deterministic replay**: Same sequence of packets → same world state
2. **Create-before-X**: Dependencies enforced in CheckReplayDependencies
3. **EWorldReplayDomain**: Collection(1), Lifecycle(2), Rename(3), Transform(4)
4. **GWorldReplayBuffer**: 4096 entries max, cleared on ConsoleReset and StopNetworkThread
5. **RestoreWorldState**: Temp save → replay → hash compare → rollback if divergent
6. **GRenamePersistentLabel**: Survives StopNetworkThread; cleared ONLY on ConsoleReset
7. All replay operations use `EChangeOrigin::Replay` provenance

## Known Dangerous Areas

| Area | Risk | Guard |
|------|------|-------|
| Tick ordering | Reordering breaks attachment/transform timing | FROZEN banner |
| StopNetworkThread order | Deadlock if Shutdown before Close | Strict sequence |
| `_compute_owner_hash` | GUID churn if obj.name included | Explicit comment guarding |
| Duplicate GUID collision | Copy inherits GUID | ensure_unique_guid |
| Parent-child transform space | Local vs world mixup | InterpolateTransforms ordering |
| Replay buffer overflow | Lost events | 4096 cap + oldest eviction |

## Key Console Commands

```
UE.LiveSync.DumpState           — Registry sizes + counters
UE.LiveSync.Stats               — All atomic counters
UE.LiveSync.Ping                — Latency check
UE.LiveSync.Reset               — Full clean (ConsoleReset + reinit)
UE.LiveSync.DumpReplayBuffer    — Replay entries
UE.LiveSync.VerifyWorldReplay   — Hash consistency check
UE.LiveSync.DumpReplayTimeline  — Replay timeline ring buffer
UE.LiveSync.ExportWorldSnapshot — JSON snapshot export
UE.LiveSync.Verbose             — Toggle verbose logging

## Phase 6H Console Commands

```
UE.LiveSync.ValidatePacketOrdering     — Phase 6H: Packet ordering validation counters
UE.LiveSync.VerifySemanticState        — Phase 6H: Semantic authority audit (non-mutating)
UE.LiveSync.DumpAuthorityState         — Phase 6H: Per-actor authority state dump
UE.LiveSync.RunReplayFuzz [s] [n]      — Phase 6H: Replay fuzz (seed, iterations)
UE.LiveSync.RunHierarchyStress [o] [n] — Phase 6H: Hierarchy stress (objects, ops)
UE.LiveSync.RunReconnectStress [c]     — Phase 6H: Reconnect stress cycles
UE.LiveSync.VerifyReplayDeterminism    — Phase 6H: Full replay determinism verification
UE.LiveSync.EnforceKnownBadPatterns    — Phase 6H: Known-bad-pattern detection
```


## Test Commands (Standalone, No UE Required)

```
python3 tests/phase6g_identity_stability.py  — 121 identity stability tests
python3 tests/phase6e_delete_validation.py   — 308 delete lifecycle tests
python3 tests/phase6b_runtime_audit.py       — 102 source-code audit checks
python3 tests/phase6d_hierarchy_validation.py — hierarchy tests (some need UE)
```

## Identity Invariants (Phase 6G)

- GUID depends ONLY on `obj.data.name` — NOT `obj.name`
- Hash is deterministic: SHA-256 of `f"{datablock_name}"` → hex[:16]
- Duplicates regenerate GUID via `ensure_unique_guid` collision detection
- Datablock change → hash change → reconcile → new GUID
- Missing hash → assign new hash (no GUID regeneration)
- Corrupted hash → reconcile → new GUID

## DO NOT READ — Cold-Start Exclusions

These files are HUGE and should NOT be loaded during cold-start repo exploration.
Use targeted grep to find specific functions/symbols instead.

| File | Lines | Why Skipped | How to Read |
|------|-------|-------------|-------------|
| `UELiveSyncSubsystem_Replay.inl` | 1942 | Replay + world snapshot code; only needed for replay divergence bugs | Grep for specific function name |
| `UELiveSyncSubsystem_Diagnostics.inl` | 919 | Console commands + editor accessors; only needed for console command bugs | Grep for specific command |
| `sync.py` | 1819 | NOT split (module-level globals make split risky); only domain-specific sections needed | Use HOT_PATHS.md entry points |
| `network.py` | 1726 | Serialization layer; only needed for packet format bugs | Target specific serialization function |
| `SyncTypes.h` | 1383 | Type definitions; only needed for struct layout questions | Grep for specific type |

**All three C++ files** (`UELiveSyncSubsystem.cpp` + `_Replay.inl` + `_Diagnostics.inl`) compile as a single translation unit. Member functions defined in `.inl` files can call any function or access any static variable from the main `.cpp`.

## Authoritative Ownership

| Domain | Source of Truth | UE Role |
|--------|----------------|---------|
| Transforms | Blender `obj.matrix_world.decompose()` | Interpolation client |
| Create/Delete | Blender scene scan diff | Spawn/destroy |
| Rename | Blender PT_Rename event | Apply + persist |
| Hierarchy | Blender `obj.parent` | AttachActor/DetachFromActor |
| Visibility | Blender PT_Visibility event | Toggle |
| Collection | Blender `obj.users_collection` | Membership registry |

## Replay/Rebuild Lifecycle

```
SaveWorldState: capture all domains → FWorldStateSnapshot
  └─ ActorCache (GUID → label)
  └─ RenamePersistentLabel
  └─ CollectionMembership
  └─ World state hash (FNV-1a)

RestoreWorldState: transactional
  1. Save temp current state
  2. Apply replay entries
  3. Compute new hash
  4. If hash != expected → restore temp → increment rollback counter

RebuildWorldFromSnapshot: idempotent full rebuild
  1. Clear ActorCache, TransformStates, sequence trackers
  2. Spawn actors from exported GUID list
  3. Apply rename labels from exported domain
  4. Apply collection membership from exported domain
  5. BuildActorCache

ConsoleReset: full clean  
  - GRenamePersistentLabel.Empty()  
  - GWorldReplayBuffer.Empty()  
  All sequence trackers cleared  
  ActorCache rebuilt from world scan  
  
ConsoleReset implementation: see _Diagnostics.inl ~576  
  
StopNetworkThread: partial clean (survives reconnect)
  - GWorldReplayBuffer.Empty()
  - Sequence trackers cleared
  - GRenamePersistentLabel NOT cleared
  - ActorCache NOT cleared

---

**Companion docs**:
- `ARCHITECTURE.md` — topology, packet flow, tick pipeline
- `CRITICAL_INVARIANTS.md` — 60 hard invariants across 10 categories
- `KNOWN_GOOD_FLOWS.md` — 9 canonical execution paths (A-I)
- `BUG_ENTRYPOINTS.md` — surgical debugging entrypoints
- `TASK_PROMPT_TEMPLATES.md` — scoped task templates
```
