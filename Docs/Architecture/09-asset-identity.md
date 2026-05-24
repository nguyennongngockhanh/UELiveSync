# Asset Identity Architecture

## Overview

Phase 6A introduces a deterministic asset identity system that maps Blender mesh datablocks to UE static meshes. The system is designed to be asynchronous, non-blocking, and fully separate from the realtime transform replication layer.

## Identity Model

### Blender Side

An asset identity is derived from the **mesh datablock name** (`obj.data.name`):

```
Identity = xxHash64(obj.data.name.encode("utf-8"))
```

This yields a 64-bit hash value, transmitted as two 64-bit integers (low, high) in the protocol.

### Identity Stability Guarantees

| Condition | Identity Stable? | Reason |
|-----------|-----------------|--------|
| Same session, same object | ✅ Yes | Datablock name unchanged |
| Different session, same .blend | ✅ Yes | Datablock names persist in .blend |
| Duplicated object instance | ✅ Yes | Same underlying datablock reference |
| Reconnect / snapshot rebuild | ✅ Yes | GUID-independent, data-driven |
| Datablock rename | ❌ No | Name is the input to the hash |
| Mesh data replacement | ❌ No | New datablock = new name/hash |
| Object rename | ✅ Yes | Only datablock name matters |

### Identity ≠ Actor Identity

| Aspect | Actor Identity (GUID) | Asset Identity (Hash) |
|--------|----------------------|----------------------|
| Scope | Per-object instance | Per-mesh datablock |
| Basis | Random UUID stored in custom property | Deterministic hash of datablock name |
| Lifetime | Object lifetime | Datablock lifetime |
| Purpose | Track actor across reconnects | Find/cache UE static mesh |
| Stability | Always stable | Stable unless mesh changes |

## Data Flow

```
Blender:
  MESH object
    → obj.data.name ("SM_Chair")
    → xxHash64("SM_Chair") → 0xA1B2C3D4E5F67890
    → serialize_asset_identity(GUID, low, high, primitive_fallback)
    → PT_AssetDef packet (V5)

UE Receive (game thread):
  PT_AssetDef
    → HandleAssetDef(GUID, identity, fallback)
    → Store in TMap<FGuid, FAssetMetadata>
    → Enqueue GUID in PendingAssetQueue

UE Resolution (game thread, max 8/tick):
  Dequeue GUID from PendingAssetQueue
    → Lookup FAssetIdentityRef → FSoftObjectPath in AssetPathCache
    → If cache hit: AssignStaticMesh(GUID, path)
    → If cache miss: retry with backoff (1s→2s→4s→8s→16s)
    → After 5 retries: AssignFallbackPrimitive(GUID, fallback)

UE Assignment:
  AssignStaticMesh:
    → FindActorFast(GUID)
    → FindOrCreate UStaticMeshComponent
    → StaticLoadObject(UStaticMesh, path)
    → SetStaticMesh + SetMobility(Movable)
    → Live-swap (preserves transform, hierarchy)

  AssignFallbackPrimitive:
    → Same flow but uses engine basic shape (Cube/Sphere/etc.)
    → TEMPORARY — can be overridden by late resolution
```

## Fallback Lifecycle

1. **On CREATE** → Actor spawned with primitive mesh (existing behavior)
2. **On PT_AssetDef** → Metadata stored, resolution queued
3. **During resolution** → Actor keeps primitive mesh
4. **On successful resolution** → Static mesh live-swapped:
   - Transform preserved
   - Hierarchy preserved
   - Actor identity preserved
   - No actor recreation
5. **On resolution failure** → Fallback becomes permanent
6. **Late resolution** → If cache populated later, fallback can still be replaced
   (e.g., asset imported after Blender connection established)

## Lookup Strategy (Phase 6A)

The identity hash is a **cache/dedup key only**. The actual UE asset lookup is by naming convention:

```
Blender mesh datablock name "SM_Chair"
  → expected UE path: /Game/Assets/SM_Chair.SM_Chair
```

The `AssetPathCache` (`TMap<FAssetIdentityRef, FSoftObjectPath>`) maps identity hashes to resolved paths. This must be pre-populated or populated by convention-based lookups.

**Phase 6A limitation**: Automatic UE Asset Registry querying is not implemented. The cache is populated by explicit calls to `CacheAssetPath()`.

## Non-Goals (Phase 6A)

- ❌ No material slot replication
- ❌ No source FBX path metadata transmission
- ❌ No UE Asset Registry query by identity hash
- ❌ No geometry streaming or mesh data transfer
- ❌ No skeletal mesh support
- ❌ No mesh deformation sync

## Key Files

| File | Role |
|------|------|
| `AssetIdentityTypes.h` | FAssetIdentityRef, FAssetMetadata, FAssetDiagnostics, constants |
| `PendingAssetQueue.h` | Bounded (2048) FIFO for pending resolution GUIDs |
| `UELiveSyncSubsystem.h/cpp` | HandleAssetDef, ResolvePendingAssets, AssignStaticMesh |
| `SyncTypes.h` | PT_AssetDef, LIVE_SYNC_VERSION_V5, asset stats counters |
| `network.py` | xxHash64, get_mesh_identity_hash, serialize_asset_identity |
| `sync.py` | PT_AssetDef sending on CREATE and mesh change |
