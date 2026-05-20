# Network Protocol

## Current Protocol (Version 2)

### Packet Structure

```
┌─────────────────────────────────────────────────────────┐
│ HEADER (22 bytes)                                       │
├─────────────┬──────────┬────────────────────────────────┤
│ Magic       │ uint32   │ 0x4C56534D ("ULSM")           │
│ Version     │ uint16   │ 2                             │
│ SequenceId  │ uint64   │ Monotonically incrementing     │
│ PacketSize  │ uint32   │ Header + payload size          │
│ ObjectCount │ uint32   │ Number of objects in payload   │
└─────────────┴──────────┴────────────────────────────────┘
                                                          
┌─────────────────────────────────────────────────────────┐
│ OBJECT (56 bytes each, ObjectCount times)               │
├─────────────┬──────────┬────────────────────────────────┤
│ GUID        │ 16 bytes │ UUID4 hex → bytes.fromhex()   │
│ Location    │ 12 bytes │ 3 × float32 LE (cm)           │
│ Rotation    │ 16 bytes │ 4 × float32 LE (quat xyzw)    │
│ Scale       │ 12 bytes │ 3 × float32 LE                │
└─────────────┴──────────┴────────────────────────────────┘
```

### Wire Format
```
<I H Q I I     header:  magic(4) ver(2) seq(8) size(4) count(4)
<16s            guid
<fff            location
<ffff           rotation
<fff            scale
[... repeat per object]
```

### Packing (Blender)
```python
header = struct.pack("<I H Q I I",
    LIVE_SYNC_MAGIC,      # 4 bytes
    LIVE_SYNC_VERSION,    # 2 bytes
    sequence_id,          # 8 bytes
    packet_size,          # 4 bytes
    object_count          # 4 bytes
)

# Per object:
payload.extend(bytes.fromhex(guid_hex))  # 16 bytes
payload.extend(struct.pack("<fff", loc)) # 12 bytes
payload.extend(struct.pack("<ffff", rot)) # 16 bytes
payload.extend(struct.pack("<fff", scl)) # 12 bytes
```

### Unpacking (UE)
```cpp
// Read header
FMemory::Memcpy(&Header, Ptr, sizeof(FPacketHeader));
Ptr += sizeof(FPacketHeader);

// Per object:
// GUID: bytes 0-15 → hex string → FGuid::ParseExact
// Location: FVector3f (3 floats)
// Rotation: FQuat4f (4 floats)
// Scale: FVector3f (3 floats)
```

## Limitations of Current Protocol

1. **No packet type field** — all packets are TRANSFORM, no distinction for CREATE/DELETE/HIERARCHY/HEARTBEAT
2. **GUID as hex bytes** — forces string conversion on UE side
3. **World-space only** — no local transform slot for hierarchy
4. **No timestamp** — prevents precise interpolation timing
5. **No checksum** — no payload integrity check
6. **No endianness marker** — implicitly LE, assumes platform match

## Proposed Protocol (Version 3)

### Header
```
Magic(4) + Version(2) + Type(1) + Flags(1) + Seq(8) + Size(4) + Count(4) = 24 bytes
```

### Type Field
| Value | Name | Description |
|-------|------|-------------|
| 0x01  | TRANSFORM | Object transform update |
| 0x02  | HIERARCHY | Parent-child relationship |
| 0x03  | CREATE    | New object spawn |
| 0x04  | DELETE    | Remove object |
| 0x05  | MATERIAL  | Material change |
| 0x06  | MESH      | Mesh data stream |
| 0x07  | HEARTBEAT | Connection keepalive |

### Flags Field
| Bit | Meaning |
|-----|---------|
| 0   | Contains local transforms |
| 1   | Full snapshot (initial sync) |
| 2   | Request acknowledgment |

### TRANSFORM Object (Version 3)
```
GUID:         4 × uint32 LE  (16 bytes)
Location:     3 × float      (12 bytes)
Rotation:     4 × float      (16 bytes)
Scale:        3 × float      (12 bytes)
Timestamp:    1 × double     (8 bytes)  — Blender time in seconds
LocalParent:  1 × uint32     (4 bytes)  — parent GUID index (0 = no parent)
LocalLoc:     3 × float      (12 bytes)  — optional local transform
LocalRot:     4 × float      (16 bytes)
LocalScl:     3 × float      (12 bytes)
```
Total: 56–116 bytes per object depending on flags.

### Backward Compatibility
- Version 2 packets have `Version=2` — current UE handler continues to work
- Version 3 packets have `Version=3` and the `Type` byte
- UE checks version first, dispatches to appropriate parser
