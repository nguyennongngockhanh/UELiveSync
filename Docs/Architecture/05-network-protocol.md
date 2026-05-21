# Network Protocol

## Version 3 Protocol (Current)

### Packet Header (24 bytes)

```
┌─────────────────────────────────────────────────────────┐
│ HEADER (24 bytes)                                       │
├─────────────┬──────────┬────────────────────────────────┤
│ Magic       │ uint32   │ 0x4C56534D ("ULSM")           │
│ Version     │ uint16   │ 3                             │
│ PacketType  │ uint8    │ See packet types table         │
│ Flags       │ uint8    │ Bitfield (reserved)            │
│ SequenceId  │ uint64   │ Monotonically incrementing     │
│ PacketSize  │ uint32   │ Total packet size (header+pay) │
│ ObjectCount │ uint32   │ Number of objects in payload   │
└─────────────┴──────────┴────────────────────────────────┘
```

Python format: `"<I H B B Q I I"`
C++ struct: `FPacketHeaderV3` (packed, 24 bytes)

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

## Protocol Constants

```cpp
static constexpr uint32 LIVE_SYNC_MAGIC      = 0x4C56534D;  // "ULSM"
static constexpr uint16 LIVE_SYNC_VERSION    = 2;            // V2 legacy
static constexpr uint16 LIVE_SYNC_VERSION_V3 = 3;            // V3 active
static constexpr int32  LIVE_SYNC_OBJECT_SIZE      = 56;     // V2 object
static constexpr int32  LIVE_SYNC_V3_OBJECT_SIZE    = 80;    // V3 object
static constexpr int32  LIVE_SYNC_V3_DELETE_SIZE    = 16;    // V3 delete
```
