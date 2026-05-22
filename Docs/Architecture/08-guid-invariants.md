# GUID Invariants

> **Phase 3.6 — 2026-05-22**
> Formal invariants of the GUID-based object identity system across Blender and Unreal Engine.

## 1. GUID Generation

- Every Blender object tracked by the addon is assigned a GUID via `uuid.uuid4().hex`
- The result is a 32-character lowercase hex string (128 bits)
- The GUID is stored in the Blender object's custom properties as `obj["ue_guid"]`
- GUIDs are never reassigned — once generated, they identify that object until deletion
- Deleted GUIDs are released from tracking and MAY be reused by the UUID4 space (astronomically unlikely)

## 2. Core Invariants

### 2.1 Injection (Blender → UE)

```
∀ obj ∈ tracked_objects:
  ∃! guid = obj["ue_guid"]
  ∧ guid ∉ tracked_objects[other]       (no duplicate in tracked set)
  ∧ 32-char hex                          (well-formed)
```

### 2.2 Identity (UE side)

```
∀ guid ∈ TransformStates:
  ∃! actor ∈ World
  ∧ actor.Tag("LiveSync_GUID") = guid
  ∧ actor.Binding ≤ 1                    (single guid per actor)
```

### 2.3 Surjectivity

```
∀ guid ∈ TransformStates:
  guid originated as obj["ue_guid"] on Blender side
```

No UE-side synthetic GUIDs. All identities originate from Blender.

## 3. Invariants During Operations

### 3.1 Object Creation

1. Blender generates new GUID → `ensure_guid(obj)`
2. Collision check: `guid ∈ tracked_objects` for different object → regenerate
3. CREATE packet sent with GUID in payload
4. UE receives → `FindGuidForActor(guid)` → `BuildActorCache()` scan → create actor if not found
5. Actor tagged with `LiveSync_GUID=<guid>`
6. `TransformStates[guid]` initialized

### 3.2 Object Update

1. Blender reads `obj["ue_guid"]`
2. TRANSFORM packet sent with existing GUID
3. UE resolves `TransformStates[guid]` → applies transform to bound actor
4. If `PF_HasLocalTransform` flag: local→world conversion uses `TransformStates[ParentGUID].WorldTransform`

### 3.3 Object Deletion

1. Blender removes object → DELETE packet sent with GUID
2. UE removes `TransformStates[guid]`
3. Cache (actor lookup) cleared via `OnActorDestroyed` → `TransformStates.erase(guid)`
4. Children with this parent GUID: next UPDATE triggers local→world conversion with missing parent → child treated as root

### 3.4 Full-State Snapshot (Reconnect)

1. ALL `tracked_objects` re-sent with `PF_FullSnapshot` flag
2. UE clears `TransformStates` entirely before applying
3. GUID map rebuilt from snapshot
4. All actors re-tagged if needed

### 3.5 Duplicate (Blender `.copy()`)

1. `obj.copy()` clones custom properties → `ue_guid` copied verbatim
2. Collision: the new object's GUID matches the original in `tracked_objects`
3. `ensure_unique_guid()` detects collision → regenerates new GUID
4. **Current limitation (Phase 3.6):** `ensure_unique_guid` exists in docs but is not yet implemented in `sync.py`. Duplicated objects inherit the original GUID until the next sync cycle where collision is detected and resolved.

## 4. Wire Encoding

GUID is transmitted as 4 × `uint32` LE (16 bytes total):

```python
def guid_to_uint32s(guid_str: str) -> tuple[int, int, int, int]:
    raw = bytes.fromhex(guid_str)
    return struct.unpack("<IIII", raw)
```

Zero GUID (`00000000-0000-0000-0000-000000000000`) is reserved to indicate "no parent" for root objects.

## 5. Cache and Lifetime

| Cache | Location | Scope | Invalidated By |
|-------|----------|-------|----------------|
| `tracked_objects` | Blender `sync.py` | Per-addon session | Reload, reconnect |
| `TransformStates` | UE `UELiveSyncSubsystem` | Subsystem lifetime | `PF_FullSnapshot`, `StateTTL` expiry, `OnActorDestroyed` |
| `ActorCache` (guid→actor) | UE `UELiveSyncSubsystem` | Subsystem lifetime | `OnActorDestroyed`, full snapshot |

## 6. Required Checks

When adding or modifying GUID-related code, verify:

- [ ] No two Blender objects share a GUID in `tracked_objects`
- [ ] `PF_FullSnapshot` fully clears `TransformStates`
- [ ] `OnActorDestroyed` removes the entry from `TransformStates`
- [ ] Zero GUID is only used for root objects (no parent)
- [ ] `ParentGUID` lookup failure falls back to world-space transform
- [ ] Duplicate detection does not log false positives during snapshot burst
