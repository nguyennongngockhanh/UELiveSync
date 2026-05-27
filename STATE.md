# Current State — UELiveSync

## Objective

Debug UE-side actor spawn/apply pipeline for PT_Create (0x03) packets. Actors are received but do not appear in the viewport.

## Changes Made

### UE Plugin: `UELiveSyncSubsystem.cpp`

**1. `GetPrimitiveMesh()` — Primitive resolution fallback paths**
- Added alternate mesh paths for UE5.4+ content layout (`BasicSphere`, `BasicCube`, etc.)
- Added `[DIAG][PRIMITIVE]` Warning log if all resolution paths fail
- Refactored from early-return to local-var + return pattern for fallback chaining

**2. Tick pipeline — Periodic health diagnostics**
- Every 100 frames (`VerboseFrameCounter % 100 == 1`): logs `ActorCache` size, alive/dead actor count, `TransformStates` size
- Catches actors that die between ticks

**3. `ProcessBinaryPacket()` — Primitive type byte diagnostics**
- Added `[CREATE][DIAG] PARSED primitive_type=0x%02X` log when primitive type byte is read for CREATE
- Added `[CREATE][DIAG] V4+ CREATE packet has no primitive type byte` Error log when V4+ CREATE object is truncated
- Added `[CREATE][DIAG] DISPATCH` Warning log before `HandleCreateObject()` call showing all parsed fields

**4. `HandleCreateObject()` — Comprehensive entry diagnostics**
- World type string (Editor/Game/PIE/EditorPreview/GamePreview/GameRPC/Inactive)
- World name, level name, ActorCache pre-count
- All payload fields: GUID, PrimitiveType (hex), Location, Rotation, Scale, ParentGuid, local flag, timestamp
- Invalid GUID (all-zero) → Error, return
- Suspicious scale (zero/negative) → Warning
- Suspicious location magnitude (>1e12) → Warning

**5. `HandleCreateObject()` — Spawn diagnostics**
- Spawn failure: logs world name, actor class, world type, elapsed ms
- Spawn failure reason detection: `EditorPreview`/`Inactive` world type, null/invisible level
- Spawn success: logs actor name, class, world, elapsed ms
- Spawn transform: post-spawn actor location/rotation/scale
- World visibility check: Error if spawned into non-visible world type

**6. `HandleCreateObject()` — Registry and attachment diagnostics**
- Post-ActorCache-insert verification: `[CREATE][DIAG] REGISTRY` FOUND/MISSING
- Immediate pending-destroy detection: `[CREATE][DIAG] ACTOR PENDING DESTROY IMMEDIATELY AFTER SPAWN`
- Post-attach verification: logs actual parent actor name

**7. `HandleCreateObject()` — Component/primitive diagnostics**
- `NewObject<UStaticMeshComponent>` null check with Error log
- `[CREATE][DIAG] PRIMITIVE` — mesh name on success, Error on failure
- `[CREATE][DIAG] REGISTER COMPLETE` — mesh name and registration time

**8. `HandleDeleteObject()` — Unexpected delete diagnostics**
- `[DELETE][DIAG] Deleting EXISTING/MISSING actor` — catches rogue DELETE packets
- `[DELETE][DIAG] DESTROYING actor` — flags exact moment an actor is destroyed

### Other files (pre-existing changes)

- `SyncTypes.h`: `FWorldStateSnapshot::operator==` rewritten with explicit TMap/TSet comparison (avoids `operator==` ambiguity for template containers); `EReplayResult` → `ELiveSyncReplayResult` rename
- `Blender_Addon/__init__.py`: Exception handling wrapper around `start_sync()` with traceback dump
- `Blender_Addon/network.py`: Collection operation constants (`COLLECTION_OP_*`), client constructor/sender thread diagnostics, flush=True on prints
- `Blender_Addon/sync.py`: Collection replay integration, snapshot depth distribution logging

## Next Step (Required)

Run UE Editor + Blender, capture `[CREATE][DIAG]` output from UE Output Log.
