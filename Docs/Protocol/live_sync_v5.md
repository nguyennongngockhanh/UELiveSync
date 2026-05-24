# Live Sync V5 Protocol

## Overview

**Version**: 5
**Date**: 2026-05-24

V5 extends the V4 protocol with asset replication support. It adds one new packet type (`PT_AssetDef = 0x08`) for defining the expected UE asset for synced Blender objects.

## Changes from V4

- Added `PT_AssetDef` (0x08) packet type for asset identity definition
- V3/V4 header format unchanged (24 bytes: `<I H B B Q I I`)
- V2 legacy unchanged
- No changes to existing `PT_Transform`, `PT_Create`, `PT_Delete` layouts

## Header (24 bytes, identical to V3/V4)

```
Offset  Size  Type    Field       Notes
0       4     uint32  Magic       0x4C56534D ("LVSM")
4       2     uint16  Version     5
6       1     uint8   PacketType  EPacketType
7       1     uint8   Flags       EPacketFlags bitfield
8       8     uint64  SequenceId  Monotonically incrementing
16      4     uint32  PacketSize  Total packet size (header + payload)
20      4     uint32  ObjectCount Number of objects in payload
```

## PT_AssetDef (0x08)

### Purpose
Define the expected UE static mesh for a synced Blender MESH object. Sent on CREATE, on mesh datablock change, and during full snapshot rebuild.

### Wire Layout (per object, 33 bytes fixed-size)

```
Offset  Size  Type    Field
0       16    bytes   Object GUID (standard FGuid: 4 × uint32 LE)
16      8     uint64  Asset Identity Hash Low  (xxHash64 low)
24      8     uint64  Asset Identity Hash High (xxHash64 high)
32      1     uint8   Primitive Fallback (ELiveSyncPrimitiveType)
```

**Total per object**: 33 bytes (fixed-size, no variable-length fields)

### Field Semantics

- **Object GUID**: Standard FGuid decomposition (same as `PT_Transform` and `PT_Create`)

- **Asset Identity Hash**: xxHash64 of the Blender mesh datablock name (`obj.data.name`).
  - Deterministic across sessions and duplicated object instances
  - NOT stable across datablock renames
  - Used as cache/dedup key on the UE side
  - Not used as a human-facing search key

- **Primitive Fallback**: `ELiveSyncPrimitiveType` value
  - Used as temporary mesh until asset resolution completes
  - If resolution fails permanently, this fallback stays

### When Sent

| Condition | Timing |
|-----------|--------|
| First time a MESH object is tracked | Immediately after the CREATE packet |
| Mesh datablock changes on existing object | During the next sync tick, after TRANSFORM |
| Full snapshot rebuild | Between PT_BeginSnapshot and PT_EndSnapshot |

### Resolution Protocol (UE-side)

1. `PT_AssetDef` received → identity hash stored in `AssetMetadata` map
2. `ResolvePendingAssets()` runs every game tick (max 8/tick)
3. Lookup: identity hash → `FSoftObjectPath` via `AssetPathCache`
4. Cache miss → exponential backoff retry: 1s, 2s, 4s, 8s, 16s (max 5 attempts)
5. Cache hit → `AssignStaticMesh()` → live-swap mesh on existing actor
6. Resolution failure after max retries → `AssignFallbackPrimitive()` → permanent

### Wire Size Reference

| Packet Type | Per-Object Size | Header Size | Total (N=1) |
|-------------|----------------|-------------|-------------|
| V4 CREATE   | 81 bytes       | 24 bytes    | 105 bytes   |
| V5 AssetDef | 33 bytes       | 24 bytes    | 57 bytes    |
| V3 TRANSFORM| 80 bytes       | 24 bytes    | 104 bytes   |
| V3 DELETE   | 16 bytes       | 24 bytes    | 40 bytes    |
| HEARTBEAT   | 0 bytes        | 24 bytes    | 24 bytes    |

## Backward Compatibility

- Unchanged `PT_Transform`/`PT_Create`/`PT_Delete` handlers continue to work
- `PT_AssetDef` packets are skipped by V3/V4 receivers (packet type 0x08 is unrecognized)
- No V3/V4 code needs changes to coexist with V5 traffic
- Blender addon can send V5 asset defs alongside V4 transforms without conflict

## Interoperability

| Blender Sends | UE Receives | Result |
|---------------|-------------|--------|
| V5 (AssetDef+Transform) | V5 (Phase 6A) | Full asset resolution |
| V5 (AssetDef+Transform) | V4 (pre-6A) | AssetDef ignored, primitives used |
| V4 (Transform only) | V5 (Phase 6A) | No asset defs, primitives used |
| V3 (Transform only) | V5 (Phase 6A) | No asset defs, primitives used |
