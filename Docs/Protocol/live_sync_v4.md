# Live Sync V4 Protocol Specification

**Version**: 4 (wire)
**Status**: Implemented (`phase5A-stable`)
**Supersedes**: V3 (V2 legacy coexists for backward compatibility)
**Endianness**: Little-endian throughout
**Transport**: TCP (ordered, reliable delivery; no reassembly layer)

---

## A. Header Layout

Every packet begins with a fixed-size 24-byte header:

```
Offset  Size  Field         Type     Description
------  ----  -----         ----     -----------
 0       4    Magic         uint32   Always 0x4C56534D ("LVSM")
 4       2    Version       uint16   Wire protocol version (4)
 6       1    PacketType    uint8    See §B
 7       1    Flags         uint8    See §A.1
 8       8    SequenceId    uint64   Monotonic counter per connection (§F)
16       4    PacketSize    uint32   Total packet size including header
20       4    ObjectCount   uint32   Number of object payloads in this packet
```

**Layout**: `<I H B B Q I I` (24 bytes total)
**Alignment**: All fields are densely packed (no padding). Multi-byte fields are naturally aligned by their position in the struct.

### A.1 Flags

| Bit  | Name               | Description                          |
|------|--------------------|--------------------------------------|
| 0x00 | PF_None            | No flags                             |
| 0x01 | PF_HasLocalTransform | Object payloads contain local (not world) transforms |
| 0x02 | PF_FullSnapshot    | This packet is part of a full snapshot rebuild |
| 0x04 | PF_RequestAck      | Receiver should acknowledge this packet (reserved, not implemented) |

Flags are validated against known values. Unknown flag bits cause the packet to be skipped with a warning.

---

## B. Packet Types

### B.1 PT_Transform (0x01)

**Payload**: N × transform object records
**Version requirement**: V3+

| Version | Per-object size | Layout |
|---------|----------------|--------|
| V3      | 80 bytes       | GUID(16) + Loc(12) + Rot(16) + Scl(12) + TS(8) + Parent(16) |
| V4+     | 81 bytes       | V3 fields + PrimitiveType(1) appended at offset 80 |

**V3 layout (80 bytes)**:
```
Offset  Size  Field        Type     Description
------  ----  -----        ----     -----------
  0      16    Guid         uint32×4 Object GUID (§H)
 16      12    Location     float×3  World-space X, Y, Z (UE centimeters)
 28      16    Rotation     float×4  Quaternion W, X, Y, Z (normalized)
 44      12    Scale        float×3  X, Y, Z scale factors
 56       8    Timestamp    double   Seconds since epoch
 64      16    ParentGuid   uint32×4 Parent GUID (zero if root)
```

**V4+ layout (81 bytes)**: Same as V3 with an additional byte at offset 80:
```
 80       1    PrimitiveType uint8   See §C (always present in V4+ payloads)
```

**Important**: In V4+, ALL object payloads (TRANSFORM and CREATE) are always 81 bytes. The primitive type byte is always present in V4+ wire format regardless of packet type. V3 TRANSFORM packets remain 80 bytes per object.

### B.2 PT_Create (0x03)

**Payload**: N × create object records (81 bytes each in V4)
**Version requirement**: V4+ (see §E for V3 compatibility)

```
Offset  Size  Field        Type     Description
------  ----  -----        ----     -----------
 0      16    Guid         uint32×4 Object GUID
16      12    Location     float×3  Spawn location
28      16    Rotation     float×4  Spawn rotation (quaternion, normalized)
44      12    Scale        float×3  Spawn scale
56       8    Timestamp    double   Seconds since epoch
64      16    ParentGuid   uint32×4 Parent GUID (zero if root)
80       1    PrimitiveType uint8   See §C
```

**Total**: 81 bytes per object in V4; 80 bytes per object in V3 (no primitive byte).

### B.3 PT_Delete (0x04)

**Payload**: N × 16-byte GUID records

```
Offset  Size  Field   Type      Description
------  ----  -----   ----      -----------
 0      16    Guid    uint32×4  GUID of object to destroy
```

**Total**: 16 bytes per object.
**Version requirement**: V3+

### B.4 PT_Heartbeat (0x07)

**Payload**: Empty (0 objects, no payload bytes)
**Behavior**:
- Resets the UE heartbeat timeout timer (default 15s)
- No objects are processed
- Only the `PacketSize` and `SequenceId` fields in the header are meaningful

### B.5 PT_BeginSnapshot (0x09)

**Payload**: Empty (0 objects, no payload bytes)
**Version requirement**: V4+

**Behavior**:
- Sets `bInSnapshotBuild = true` on the receiver
- All subsequent transforms are deferred — not interpolated
- Deletes during snapshot are skipped (not applied)
- Attachments are deferred to `PT_EndSnapshot`
- Snapshot auto-aborts if:
  - Duration exceeds 5 seconds
  - Connection drops while in snapshot mode

### B.6 PT_EndSnapshot (0x0A)

**Payload**: Empty (0 objects, no payload bytes)
**Version requirement**: V4+

**Behavior**:
- Sets `bInSnapshotBuild = false`
- Resolves all deferred pending attachments
- Resumes normal transform interpolation

### B.7 PT_MaterialParams (0x05) — Reserved

**Status**: Reserved for Phase 5D
**Not implemented** — receiver currently skips with "unknown packet type" warning.

### B.8 PT_MeshUpdate (0x06) — Reserved

**Status**: Reserved for Phase 5D
**Not implemented** — receiver currently skips with "unknown packet type" warning.

---

## C. Primitive Type Enum

Single-byte field appended at offset 80 in all V4+ object payloads (both TRANSFORM and CREATE).

| Value | Name              | UE Behavior                                                     |
|-------|-------------------|-----------------------------------------------------------------|
| 0x00  | PRIMITIVE_Cube    | `/Engine/BasicShapes/Cube.Cube` via `UStaticMeshComponent`      |
| 0x01  | PRIMITIVE_Sphere  | `/Engine/BasicShapes/Sphere.Sphere`                             |
| 0x02  | PRIMITIVE_Cylinder| `/Engine/BasicShapes/Cylinder.Cylinder`                         |
| 0x03  | PRIMITIVE_Plane   | `/Engine/BasicShapes/Plane.Plane`                               |
| 0x04  | PRIMITIVE_Empty   | Root-only actor with no mesh component                          |

### Validation Rules

- Values 0x00–0x04 are valid.
- Values > 0x04 (including 0xFF) are clamped to PRIMITIVE_Cube with a warning log.
- The primitive byte is only present in V4+ wire format. V3 packets have no primitive byte.
  - V4+ always reads 81 bytes per object; the byte at offset 80 is always the primitive type.
  - V3 always reads 80 bytes per object; no primitive type is read.
- Invalid values never cause a crash — the unknown value is logged and Cubed.

---

## D. Snapshot Batching Rules

### D.1 Flow

```
Sender                              Receiver
------                              --------
PT_BeginSnapshot (0x09)  ────────→  bInSnapshotBuild = true
PT_Create × N           ────────→  Spawn actors (deferred attach)
PT_Transform × N        ────────→  Skipped (not interpolated)
PT_Delete × N           ────────→  Skipped (not applied)
PT_EndSnapshot (0x0A)   ────────→  bInSnapshotBuild = false
                                     Resolve pending attachments
                                     Resume interpolation
```

### D.2 Deferred Attachment Resolution

During snapshot build, `AttachToParent()` pushes entries to a `PendingAttachments` array. On `PT_EndSnapshot`, `ResolvePendingAttachments()` iterates all entries and attempts attachment.

After snapshot mode ends, pending attachments from normal operation (child before parent) are retried:
- **Fast window**: Every frame for first 10 frames
- **Throttled**: Every 5th frame after frame 10
- **Timeout**: Dropped after 60 retry frames or 5 seconds wall-clock

### D.3 Timeout Abort

If `PT_EndSnapshot` is never received:
- Auto-aborts after 5 seconds of wall-clock time in `bInSnapshotBuild = true`
- Also aborts immediately if connection drops during snapshot
- On abort: clears all pending attachments, resets batching state, logs warning
- Future snapshots work normally after abort

### D.4 Skip Rules During Snapshot

| Operation | During Snapshot? | Behavior |
|-----------|-----------------|----------|
| PT_Transform | Deferred | States stored but not interpolated; bulk-applied on EndSnapshot |
| PT_Delete    | Skipped | Object remains in cache; delete ignored |
| PT_Create    | Applied | Actor spawned immediately; attachment deferred |
| PT_Heartbeat | Processed | Heartbeat timeout reset as normal |

---

## E. Version Compatibility Rules

### E.1 V3 Compatibility

V3 packets (Version=3 in header) are parsed with these differences:
- No primitive type byte in CREATE objects (80 bytes total)
- GUIDs use the same 4×uint32 format as V4
- All V3 Transform/Delete/Create/Heartbeat types are supported
- Unknown V3 packet types are skipped with warning

### E.2 V4 Parsing

- Primitive byte is only read when `Version >= 4`
- This ensures V3 packets with the same payload size (80 bytes per CREATE) are not corrupted by reading an extra byte
- V3 packets containing 81-byte objects are NOT supported — the extra byte is consumed as part of the next object

### E.3 Unknown Packet Types

Packet types not in {0x01, 0x03, 0x04, 0x07, 0x09, 0x0A} are:
- Logged with "Unknown packet type 0x%02X — skipping"
- Skipped without processing
- Object count and payload are ignored (no crash)

### E.4 Malformed Packet Rejection

| Condition | Behavior |
|-----------|----------|
| Magic != 0x4C56534D | Packet skipped (string match check) |
| PacketSize < header | Bounds check fails; parsing aborted |
| ObjectCount with insufficient payload | Bounds check per field; silent return if ptr exceeds PacketEnd |
| Trailing garbage after last object | Ignored; only header-delimited objects are read |
| Invalid flags | Warning logged; packet skipped |

Size validation is bounds-based (ptr + field_size <= PacketEnd), not equality-based. This means trailing garbage in the payload past the declared objects is silently ignored.

---

## F. Sequence ID Rules

### F.1 Monotonic Counter

- Each connection maintains an incrementing `_sequence_id` counter
- Incremented once per packet (not per object)
- Sequence IDs are set by the sender; the receiver does not modify or respond with them

### F.2 Reconnect Reset

- On disconnect, the UE side resets `LastSequenceId = 0`
- On reconnect, Blender continues incrementing from its last value (Globals are not cleared on disconnect)
- The receiver does NOT reject packets based on sequence order — all packets are processed

### F.3 Deduplication

Per-tick deduplication is applied within `ProcessBinaryPacket()`:
- A `SeenThisTick TSet<FGuid>` collects GUIDs already processed in the current tick
- Duplicate objects within the same tick are skipped (pointer advanced past their payload)
- This is a same-tick dedup, NOT a cross-tick dedup
- Sequence IDs are not used for deduplication

### F.4 Per-Connection Tracking

`LastSequenceId` is stored per-subsystem instance. Since only one connection is supported (single-socket model), there is no per-connection tracking table. Multi-connection per-socket sequence tracking is deferred to Phase 5E.

---

## G. Deferred / Reserved Features

### G.1 Binary Mesh Streaming (Phase 5D)

Full mesh asset path support (string-based) for custom mesh streaming. Currently only primitive types are supported. Phase 5D will add material parameter sync and mesh path transfer.

### G.2 ACK Handshake (Phase 6)

`PF_RequestAck` flag (0x04) is defined but not processed by the receiver. Future implementations will:
- Acknowledge receipt of flagged packets
- Enable Blender to throttle sends based on UE processing rate
- Provide round-trip timing metrics

### G.3 Bidirectional Communication (Phase 6)

The current protocol is unidirectional (Blender → UE only). Future phases may add:
- UE → Blender acknowledgment packets
- UE → Blender property edits
- UE → Blender scene queries

### G.4 Compression (Deferred)

No compression is applied to packet payloads. If packet sizes exceed MTU limits for large scenes, future compression may use:
- zlib/deflate on payloads > 1400 bytes
- Delta compression for transform updates

### G.5 Skeletal Animation Sync (Deferred)

No pose or bone data is transmitted. The protocol has no skeletal payload structures. Deferred to post-v1.0.

### G.6 Pose-Space Transforms (Deferred)

All transforms are currently sent as world-space coordinates. Local/pose-space transform support is deferred.

### G.7 Material Parameter Sync (Phase 5D)

`PT_MaterialParams` (0x05) is reserved for Phase 5D material parameter transfer. Currently defined but not parsed by the receiver beyond the "unknown type" skip.

### G.8 Physics / Collision Data (Deferred)

No collision shape, physics body, or constraint data is transmitted.

---

## H. GUID Format

GUIDs are transmitted as 4 × `uint32` (16 bytes total) in little-endian byte order:

```python
guid_a = guid_obj.time_low
guid_b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
guid_c = (guid_obj.clock_seq_hi_variant << 24) | \
         (guid_obj.clock_seq_low << 16) | \
         ((guid_obj.node >> 32) & 0xFFFF)
guid_d = guid_obj.node & 0xFFFFFFFF
```

On the UE side, GUIDs are reconstructed via `FGuid(Part[0], Part[1], Part[2], Part[3])`.

A zero GUID (`{00000000-0000-0000-0000-000000000000}`) is used to indicate "no parent" in the ParentGuid field.

---

## I. Wire Size Reference

| Packet Type | Header | Per-Object | Example: 100 objects |
|-------------|--------|------------|----------------------|
| PT_Transform (V3) | 24 | 80 | 8,024 bytes |
| PT_Transform (V4+) | 24 | 81 | 8,124 bytes |
| PT_Create (V3) | 24 | 80 | 8,024 bytes |
| PT_Create (V4+) | 24 | 81 | 8,124 bytes |
| PT_Delete (V3/V4) | 24 | 16 | 1,624 bytes |
| PT_Heartbeat | 24 | 0 | 24 bytes |
| PT_BeginSnapshot | 24 | 0 | 24 bytes |
| PT_EndSnapshot | 24 | 0 | 24 bytes |
| PT_AssetDef (V5) | 24 | 33 | 3,324 bytes |

---

## J. Constants Reference

```python
# Magic
LIVE_SYNC_MAGIC      = 0x4C56534D

# Wire versions
LIVE_SYNC_VERSION     = 2   # backward compat alias
LIVE_SYNC_VERSION_V3  = 3
LIVE_SYNC_VERSION_V4  = 4   # current default
LIVE_SYNC_VERSION_V5  = 5   # adds AssetDef support

# Packet types
PT_Transform         = 0x01
PT_Create            = 0x03
PT_Delete            = 0x04
PT_MaterialParams    = 0x05  # reserved
PT_MeshUpdate        = 0x06  # reserved
PT_Heartbeat         = 0x07
PT_AssetDef          = 0x08  # V5+ asset identity delta
PT_BeginSnapshot     = 0x09
PT_EndSnapshot       = 0x0A

# Primitive types (V4+ always present at offset 80)
PRIMITIVE_Cube       = 0x00
PRIMITIVE_Sphere     = 0x01
PRIMITIVE_Cylinder   = 0x02
PRIMITIVE_Plane      = 0x03
PRIMITIVE_Empty      = 0x04

# Flag bits
PF_None              = 0x00
PF_HasLocalTransform = 0x01
PF_FullSnapshot      = 0x02
PF_RequestAck        = 0x04  # reserved

# Object sizes (bytes)
V3_OBJECT_SIZE       = 80   # GUID(16) + Loc(12) + Rot(16) + Scl(12) + TS(8) + Parent(16)
V4_OBJECT_SIZE       = 81   # V3 + primitive type byte (appended to ALL V4+ object payloads)
V3_DELETE_SIZE       = 16   # GUID only
V5_ASSET_DEF_SIZE    = 33   # GUID(16) + HashLo(8) + HashHi(8) + Prim(1)

# Max packet size guard
LIVE_SYNC_MAX_PACKET_SIZE = 512 * 1024  # 512 KiB rejection threshold
```

---

## Revision History

| Date       | Version | Author | Changes |
|------------|---------|--------|---------|
| 2026-05-23 | 1.0     | Phase 5A | Initial V4 protocol freeze after Phase 5A implementation |
| 2026-05-24 | 1.1     | Phase 5D | Correct TRANSFORM payload size: V4+ always 81 bytes (prim byte present in ALL V4+ object payloads, not just CREATE). Added V5/PT_AssetDef/V4_OBJECT_SIZE/V5_ASSET_DEF_SIZE/LIVE_SYNC_MAX_PACKET_SIZE constants. |
