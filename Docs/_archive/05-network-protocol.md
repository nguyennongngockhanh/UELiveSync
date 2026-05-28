# Network Protocol

> **Frozen at v3.0 — Phase 3.6 (2026-05-22)**
> All subsequent changes MUST increment the version field in the header.

## GUID Identity

GUIDs are generated as `uuid.uuid4().hex` (32‑character hex string) on the Blender side and encoded in the packet as 4× `uint32 LE`.

### Duplicate GUID Prevention

When a Blender object is duplicated (via `obj.copy()`), custom properties including `ue_guid` are inherited. The addon detects this in `ensure_unique_guid()`:

1. After `ensure_guid()` returns the inherited GUID, the function checks if the GUID already exists in `tracked_objects` **for a different object**
2. If a collision is detected, the GUID is regenerated via `obj["ue_guid"] = uuid.uuid4().hex`
3. The new object is then tracked with a fully unique identity

This guarantees:
- No two Blender objects ever share the same GUID
- No UE actor identity overwrites from duplicate GUIDs
- No ambiguous delete/update ownership

## Version 3 Protocol (Current)

### Packet Header (24 bytes)

```
┌─────────────────────────────────────────────────────────┐
│ HEADER (24 bytes)                                       │
├─────────────┬──────────┬────────────────────────────────┤
│ Magic       │ uint32   │ 0x4C56534D ("ULSM")           │
│ Version     │ uint16   │ 3                             │
│ PacketType  │ uint8    │ See packet types table         │
│ Flags       │ uint8    │ Bitfield (see Flags table)     │
│ SequenceId  │ uint64   │ Monotonically incrementing     │
│ PacketSize  │ uint32   │ Total packet size (header+pay) │
│ ObjectCount │ uint32   │ Number of objects in payload   │
└─────────────┴──────────┴────────────────────────────────┘
```

Python format: `"<I H B B Q I I"`
C++ struct: `FPacketHeaderV3` (packed, 24 bytes)

### Flags Bitfield

| Bit | Name | Description |
|-----|------|-------------|
| 0x01 | PF_HasLocalTransform | Object uses local-space transform (has tracked parent) |
| 0x02 | PF_FullSnapshot     | Packet is a full-state snapshot burst (all objects re-sent) |
| 0x04 | PF_RequestAck       | Sender requests acknowledgment (reserved) |

When `PF_HasLocalTransform` is set, the UE side converts local→world by multiplying with the parent's current world transform. `PF_FullSnapshot` triggers a state-table reset on the UE side before applying.

### Packet Types

| Value | Name | Description |
|-------|------|-------------|
| 0x01  | TRANSFORM | Object transform update (snapshot) |
| 0x02  | HIERARCHY | Parent-child relationship (future) |
| 0x03  | CREATE | New object creation |
| 0x04  | DELETE | Object removal |
| 0x05  | MATERIAL | Material change (future) |
| 0x06  | MESH | Mesh data stream (future) |
| 0x07  | HEARTBEAT | Connection keepalive |

### CREATE Object (80 bytes)

```
┌─────────────────────────────────────────────────────────┐
│ TRANSFORM OBJECT (80 bytes)                             │
├─────────────┬──────────┬────────────────────────────────┤
│ GUID        │ 16 bytes │ 4 × uint32 LE                  │
│ Location    │ 12 bytes │ 3 × float32 LE (UE cm)         │
│ Rotation    │ 16 bytes │ 4 × float32 LE (quat xyzw)     │
│ Scale       │ 12 bytes │ 3 × float32 LE                 │
│ Timestamp   │  8 bytes │ double (Blender time seconds)  │
│ ParentGUID  │ 16 bytes │ 4 × uint32 LE (zero = no parent)│
└─────────────┴──────────┴────────────────────────────────┘
```

Python format: `"<IIII fff ffff fff d IIII"`
C++ struct: Direct `FMemory::Memcpy` into `FVector3f`, `FQuat4f`, `FGuid`

### DELETE Object (16 bytes)

```
┌─────────────────────────────────────────────────────────┐
│ DELETE OBJECT (16 bytes)                                │
├─────────────┬──────────┬────────────────────────────────┤
│ GUID        │ 16 bytes │ 4 × uint32 LE                  │
└─────────────┴──────────┴────────────────────────────────┘
```

### Wire Format (V3)

```
Header (24 bytes)
  ┌── Magic (4 bytes)
  ├── Version (2 bytes)
  ├── PacketType (1 byte)
  ├── Flags (1 byte)
  ├── SequenceId (8 bytes)
  ├── PacketSize (4 bytes)
  └── ObjectCount (4 bytes)

Payload (ObjectCount × object_size bytes)
  ┌── Object[0] (80 bytes for CREATE)
  ├── Object[1] (80 bytes for CREATE)
  └── ...
```

### Heartbeat Packet

A heartbeat is a V3 packet with `PacketType = 0x07` and `ObjectCount = 0`. The payload is empty; `PacketSize` equals header size (24 bytes).

## Version 2 Protocol (Legacy)

### Header (22 bytes)

```
┌─────────────────────────────────────────────────────────┐
│ HEADER (22 bytes)                                       │
├─────────────┬──────────┬────────────────────────────────┤
│ Magic       │ uint32   │ 0x4C56534D                     │
│ Version     │ uint16   │ 2                              │
│ SequenceId  │ uint64   │ Monotonically incrementing     │
│ PacketSize  │ uint32   │ Total packet size              │
│ ObjectCount │ uint32   │ Number of objects              │
└─────────────┴──────────┴────────────────────────────────┘
```

Python format: `"<I H Q I I"`
C++ struct: `FPacketHeader`

### V2 Object (56 bytes)

```
┌─────────────────────────────────────────────────────────┐
│ OBJECT (56 bytes)                                       │
├─────────────┬──────────┬────────────────────────────────┤
│ GUID        │ 16 bytes │ UUID4 hex → bytes.fromhex()   │
│ Location    │ 12 bytes │ 3 × float32 LE                 │
│ Rotation    │ 16 bytes │ 4 × float32 LE (quat xyzw)    │
│ Scale       │ 12 bytes │ 3 × float32 LE                 │
└─────────────┴──────────┴────────────────────────────────┘
```

## Backward Compatibility

- UE checks `Header.Version` to dispatch between V2 and V3 parsers
- V2 GUID requires hex string roundtrip (bytes → FString → FGuid::ParseExact)
- V3 GUID reads 4 × uint32 directly into `FGuid` fields (no allocation)
- Both versions coexist; Blender can switch by changing `LIVE_SYNC_VERSION` constant

## Local → World Transform Conversion

When `PF_HasLocalTransform` is set on a packet:

1. UE reads `ParentGUID` from the object payload
2. Looks up `ParentGUID` in `TransformStates` map
3. If found: `WorldTransform = ParentWorldTransform * LocalTransform`
4. If not found (stale parent): treats child as root (world-space)
5. The resulting world transform is applied to the UE actor

Root objects (no parent) MUST send `ParentGUID = 0` (all zeros). The `PF_HasLocalTransform` flag is NOT set for root objects.

## Reconnection Behavior

On reconnect, the Blender addon sends a full-state snapshot:
- `PacketType = 0x01` (TRANSFORM)
- `Flags = PF_FullSnapshot`
- All tracked objects are serialized in a single burst
- Children are split into separate packets flagged with `PF_HasLocalTransform`
- UE clears existing `TransformStates` and rebuilds from the snapshot

## Runtime Configuration (CVars)

The following console variables control protocol behavior without recompilation:

> **Note**: The Blender addon defaults to port 57000 via its addon preferences. If preferences fail to load, it falls back to 57000 (hardcoded in `sync.py:698`). The UE listener port is controlled by `UE.LiveSync.Port`.

| CVar | Default | Description |
|------|---------|-------------|
| `UE.LiveSync.Port` | 57000 | TCP listen port |
| `UE.LiveSync.HeartbeatTimeout` | 15.0 | Seconds without heartbeat before disconnect |
| `UE.LiveSync.StateTTL` | 60.0 | Seconds before orphaned transform state is pruned |
| `UE.LiveSync.Verbose` | 0 | Enable verbose logging (1=on) |
| `UE.LiveSync.InterpMode` | 1 | Interpolation mode (0=direct-set, 1=smooth) |
| `UE.LiveSync.InterpSnap` | 0.1 | Snap distance (cm) for interpolation |
| `UE.LiveSync.Threshold.Location` | 0.05 | Minimum location change in cm to trigger update |
| `UE.LiveSync.Threshold.Rotation` | 0.002 | Minimum rotation change (angular distance) to trigger update |
| `UE.LiveSync.Threshold.Scale` | 0.001 | Minimum scale change to trigger update |

## Protocol Constants

```cpp
static constexpr uint32 LIVE_SYNC_MAGIC      = 0x4C56534D;  // "ULSM"
static constexpr uint16 LIVE_SYNC_VERSION    = 2;            // V2 legacy
static constexpr uint16 LIVE_SYNC_VERSION_V3 = 3;            // V3 active
static constexpr int32  LIVE_SYNC_OBJECT_SIZE      = 56;     // V2 object
static constexpr int32  LIVE_SYNC_V3_OBJECT_SIZE    = 80;    // V3 object
static constexpr int32  LIVE_SYNC_V3_DELETE_SIZE    = 16;    // V3 delete
```

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1       | 2025-Q4 | Initial V2 protocol (22-byte header, 56-byte objects, hex GUID) |
| 2       | 2026-Q1 | V3 header (24 bytes, type+flags fields, 80-byte objects, direct uint32 GUID) |
| 3       | 2026-05-22 | Frozen at Phase 3.6. Added PF_HasLocalTransform, PF_FullSnapshot flags, local→world conversion, heartbeat protocol, CVar configuration. Duplicate GUID section updated to reflect actual state (Blender .copy() limitation noted). |
