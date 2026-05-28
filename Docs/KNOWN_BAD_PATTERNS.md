# Known Bad Patterns — Do Not Repeat

High-signal anti-patterns. Each entry: why dangerous, symptoms, protected invariants, correct pattern.

## 1. Mutable State in GUID Derivation

- **Why**: `_compute_owner_hash` including `obj.name` caused GUID regeneration on every rename → DELETE+CREATE cycle on UE side
- **Symptoms**: Actor destroyed and re-created on rename, label lost
- **Invariants**: GI-1 (only obj.data.name), GI-2 (no mutable state)
- **Correct**: `_compute_owner_hash` hashes ONLY `obj.data.name` (datablock)

## 2. Replay-Side Actor Relabel Overwrite

- **Why**: Calling `SetActorLabel` during replay without `GRenamePersistentLabel` as source of truth overwrites authoritative label
- **Symptoms**: Stale/non-authoritative labels after replay
- **Invariants**: RN-1 (GRenamePersistentLabel is SOLE authority), RN-5 (overlay on restore)
- **Correct**: Use `FScopedRenameSuppression` + overlay from `GRenamePersistentLabel` in `RestoreWorldState`

## 3. Local/World Transform Mixing

- **Why**: `get_transform()` returns `matrix_local` for parented objects; if `parent_guid_obj` is set in packet but `flags=0x00` (root), UE treats local as world
- **Symptoms**: Duplicates spawn at wrong positions, children float at local-as-world
- **Invariants**: TF-4 (child transforms use KeepRelative)
- **Correct**: Flags must match `get_transform()` output; UE world-spawn computation must handle missing parent

## 4. Non-Canonical Iteration in Hashing

- **Why**: Iterating `TMap` or `TSet` without sorting produces non-deterministic hash
- **Symptoms**: Hash mismatch on replay verification → unnecessary rollback
- **Invariants**: RD-1 (deterministic replay), CL-2 (sorted-GUID ordering)
- **Correct**: Always sort GUIDs before hashing (`TMap<FGuid, T>.KeySort`)

## 5. Replay Mutation During Diagnostics

- **Why**: Diagnostic code paths calling mutation functions (e.g., `ApplyCollectionMembership` inside `DumpState`)
- **Symptoms**: Diagnostics change runtime state, replay divergence
- **Invariants**: RB-2 (diagnostics must NEVER mutate), DG-1/DG-2 (read-only counters)
- **Correct**: Snapshot diagnostics state at entry, never call mutation functions

## 6. Hierarchy Overwrite from Transform Lane

- **Why**: `UpdateTargetTransform` calls `DetachFromParent` when `bParentChanged` is true — if parent GUID changes in packet, child is detached even when parent actor is same
- **Symptoms**: Child becomes root mid-session after unrelated transform packet
- **Invariants**: HI-4 (transforms must NOT detach children)
- **Correct**: Transform lane must NOT modify hierarchy; hierarchy changes only via PT_Hierarchy

## 7. Rollback Without Full Restore

- **Why**: Missing domain in `RestoreWorldState` temp save → half-restored state after rollback
- **Symptoms**: Stale labels after rollback, wrong collection membership
- **Invariants**: RB-1 (full restore), SN-1 (ALL domains in export)
- **Correct**: Every domain saved in `SaveWorldState` must be restored in `RestoreWorldState`

## 8. Replay Buffer Persistence Across Reconnect

- **Why**: Not clearing `GWorldReplayBuffer` on `StopNetworkThread` → stale replay entries applied on new connection
- **Symptoms**: Wrong state after reconnect, phantom actors
- **Invariants**: RD-5 (buffer cleared on Stop AND Reset)
- **Correct**: `GWorldReplayBuffer.Empty()` in both `StopNetworkThread` and `ConsoleReset`

## 9. Child World-Authority Overwrite

- **Why**: `InterpolateTransforms` root path applied to attached children when `bHasLocalTarget=false` → `SetActorTransform` with world transform overwrites attachment relative offset
- **Symptoms**: Child floats away from parent after interpolation tick
- **Invariants**: TF-4 (attached children use local authority)
- **Correct**: Attached children must always use `bHasLocalTarget=true` path; `bPendingSceneGraphWrite` must retry if parent missing

## 10. Broad Repo Exploration for Surgical Tasks

- **Why**: Reading entire repo to fix one bug wastes tokens and context
- **Symptoms**: Excessive token consumption, slow cold-start
- **Correct**: Load `HOT_PATHS.md` first, then grep-target specific function/symbol, read only relevant lines

## 11. Root↔Child Authority Transition Gap

- **Why**: `UpdateTargetTransform` receives `bIsLocalTransform=true` + valid `ParentGuid` but state was initialized as root (`bHasLocalTarget=false`, `State.bInitialized=true`). The `!State.bInitialized` init block is skipped, so the function stores LOCAL transform values as WORLD targets via the `else` (root) branch at line 3952-3964. `InterpolateTransforms` then enters the root path and applies local-as-world → actor jumps to parent origin.
- **Symptoms**: Actor jumps to parent origin on Ctrl+P (parent-at-origin snapping); offset doubles after attach; cumulative drift on detach; replay mismatch after parenting; child transform corruption.
- **Invariants**: TF-5 (authority domain migration), HI-4 (transforms must NOT detach), RD-1 (replay determinism)
- **Correct pattern**:
  ```
  PT_Hierarchy → AttachToActor(KeepWorld) → child stays at correct world position
  PT_Transform(local, ParentGuid) → UpdateTargetTransform:
    detect root→child: bIsLocalTransform && ParentGuid.IsValid() && !bHasLocalTarget
    → migrate: bHasLocalTarget = true
    → init: CurrentLocalLocation/Rotation/Scale = incoming
    → store: LocalTargetLocation/Rotation/Scale = incoming
    → compute world cache: TargetLocation/Rotation/Scale = Local × ParentWorld
  InterpolateTransforms → bHasLocalTarget && bHasParent → local path
    → LocalXForm × ParentActorTransform → correct world position
  ```
- **Protected invariants**: TF-5 (new authority domain migration rule), HI-4 (transforms must not modify hierarchy), RD-1 (deterministic replay after parenting)
