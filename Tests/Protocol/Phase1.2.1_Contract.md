# Phase 1.2.1 — Serialization Contract

## Priority of Truth

This document covers **wire encoding only**. Protocol semantics are defined
by the YAML files, not by this document.

| Concern | Source of truth |
|---------|----------------|
| Wire encoding (byte layout) | Golden vectors (see §10) |
| Protocol semantics (opcodes, field names, meaning) | MessageTypes.yaml, Types.yaml |
| This document | Descriptive. May lag behind both. |

---

## Scope

This contract describes **what the Python reference serializer currently does**.
It is a port target, not a design document.

- What the Python serializer does → contract
- What the YAML spec says → YAML
- What we wish it did → future work (not in this contract)

This document intentionally mirrors the current reference implementation
and does not introduce new protocol behavior.

---

## 1. Wire Frame Format

Every message on the wire:

```
[4-byte LE length][header][body]
```

- **length** = `sizeof(header) + sizeof(body)` (uint32 LE, does NOT include itself)
- **header** = pre-session (6 bytes) OR post-session (14 bytes)
- **body** = message-type-specific, serialized in YAML field order

---

## 2. Header Layout

### Pre-session (HELLO, HELLO_ACK, REJECT only)

| Offset | Size | Field       | Type    |
|--------|------|-------------|---------|
| 0      | 1    | MsgType     | uint8   |
| 1      | 1    | Flags       | uint8   |
| 2      | 4    | SequenceId  | uint32 LE |

**Total: 6 bytes. No SessionId.**

### Post-session (all other messages)

| Offset | Size | Field       | Type    |
|--------|------|-------------|---------|
| 0      | 1    | MsgType     | uint8   |
| 1      | 1    | Flags       | uint8   |
| 2      | 4    | SequenceId  | uint32 LE |
| 6      | 8    | SessionId   | uint64 LE |

**Total: 14 bytes. SessionId present.**

### Invariant

Pre-session messages MUST NOT contain SessionId.
Post-session messages MUST contain SessionId.

---

## 3. Primitive Types

All multi-byte values are **little-endian**.

| Type     | Size   | Encoding            |
|----------|--------|---------------------|
| uint8    | 1 byte | Unsigned integer    |
| uint16   | 2 bytes| Unsigned LE         |
| uint32   | 4 bytes| Unsigned LE         |
| uint64   | 8 bytes| Unsigned LE         |
| float32  | 4 bytes| IEEE 754 LE         |

---

## 4. Float Handling

The Python reference serializer applies these rules before packing every float32:

1. **NaN → raise ValueError.** NaN MUST NOT appear on wire.
2. **−0.0 → +0.0.** `if v == 0.0: return 0.0` (Python's `0.0` is always +0.0).
3. **No further rounding.** Value stored at float32 precision as-is.

This is implemented in `canonicalize_float()` and called by:
- `pack_float32()` — scalar float fields
- `pack_transform3d()` — each of the 10 components
- `pack_f32_array()` — each element

C++ port MUST produce the same serialized bytes as the Python reference
for identical inputs. Canonicalization is an implementation detail;
byte-identical output is the contract.

---

## 5. Composite Types

### 5.1 UUID (16 bytes)

RFC 4122 canonical 16-byte sequence. **NOT** Windows GUID mixed-endian.

Example: UUID `00112233-4455-6677-8899-aabbccddeeff`
Wire bytes: `00 11 22 33 44 55 66 77 88 99 aa bb cc dd ee ff`

Python: `uuid.UUID(v).bytes` (stdlib, RFC 4122 canonical).

### 5.2 transform3d (40 bytes, packed)

10 × float32 LE, **no alignment padding**:

| Offset | Size | Field      |
|--------|------|------------|
| 0      | 4    | position_x |
| 4      | 4    | position_y |
| 8      | 4    | position_z |
| 12     | 4    | rotation_x |
| 16     | 4    | rotation_y |
| 20     | 4    | rotation_z |
| 24     | 4    | rotation_w |
| 28     | 4    | scale_x    |
| 32     | 4    | scale_y    |
| 36     | 4    | scale_z    |

**Quaternion order: (x, y, z, w). NOT (w, x, y, z).**

The Python reference serializer applies quaternion canonicalization before packing:

1. Normalize: `q = q / |q|`
2. If `|q| < 1e-7`, return identity `(0, 0, 0, 1)`
3. Apply float canonicalization (§4) to each component

This is implemented in `canonicalize_quaternion()`, called by `pack_transform3d()`.

### 5.3 utf8_string (variable)

```
[uint16 LE length][UTF-8 bytes]
```

- Length in **bytes**, not characters.
- Max: 65535 bytes.
- No null terminator.
- Empty string: length = 0, zero content bytes.

### 5.4 f32_array (variable, NO length prefix on wire)

Sequence of float32 LE values. **No length prefix.**

Length determined by message context (count expression in YAML, e.g. `vertex_count * 3`).
Each element canonicalized per §4.

### 5.5 u32_array (variable, NO length prefix on wire)

Sequence of uint32 LE values. **No length prefix.**

Length determined by message context.

### 5.6 raw_bytes (variable, WITH length prefix)

```
[uint32 LE length][bytes]
```

Length in bytes. No padding.

---

## 6. Flags

| Bit | Name         | v1 status |
|-----|--------------|-----------|
| 0   | compressed   | Implemented (test vector exists) |
| 1   | encrypted    | Reserved. MUST be 0. |
| 2   | ack_required | Reserved. |
| 3   | fragmented   | Reserved. |
| 4-7 | reserved     | MUST be 0. |

Serializer preserves all flag bits. No validation of reserved bits.
Deserializer reads the byte as-is.

---

## 7. Body Serialization Rules

1. **Field order:** Serialized in YAML definition order.
2. **Optional fields:** Only fields explicitly marked `optional: true` in YAML may be omitted by the caller. When omitted, the field is skipped entirely (no placeholder bytes). Skipping shifts offsets of all subsequent fields.
3. **Dynamic count:** Array length resolved from count expression at serialization time. Count references previously serialized fields. Example: if `vertex_count = 5`, then `count: "vertex_count * 3"` → serialize exactly 15 float32 values.
4. **No padding:** No alignment between fields. Packed tightly.
5. **All float32 values** (scalars, arrays, transform components) canonicalized per §4.

---

## 8. Serialization Interface

### Python (reference)

```python
def serialize_message(
    msg_type: MsgType,
    flags: int = 0,
    sequence_id: int = 0,
    header_session_id: int | None = None,
    **fields: Any,
) -> bytes:
    """Returns: [4-byte LE length][header][body]"""
```

### C++ (target — standalone library, no UE dependency)

```cpp
// Pseudocode. Actual types defined during implementation.
// Serializer is a standalone C++ library.
// UE plugin adapts this library to its message types.

struct SerializedMessage {
    std::vector<uint8_t> data;
};

SerializedMessage SerializeMessage(
    uint8_t msgType,
    uint8_t flags,
    uint32_t sequenceId,
    const uint64_t* sessionId,  // nullptr for pre-session
    // Body fields passed via concrete struct per message type
);
```

### Requirements

- **Deterministic:** Same inputs → identical bytes, always.
- **Independent:** No networking, socket, or engine dependencies.
- **Pure function:** No side effects, no global state.

---

## 9. Deserialization Interface

### Python (reference)

```python
def deserialize_frame(data: bytes) -> DeserializedMessage:
    """Input: [4-byte LE length][header][body]"""
```

### C++ (target)

```cpp
// Standalone library. No UE dependencies.
// Returns nullptr / std::nullopt on malformed input.
std::optional<DeserializedMessage> DeserializeMessage(
    std::span<const uint8_t> data
);
```

### Requirements

- Validate header invariants.
- Validate frame length matches declared length.
- Reject malformed data.

---

## 10. Golden Vector Verification

For every message type, both implementations MUST produce **byte-identical** output.

### Method

```
Python serialize(inputs) → bytes.bin
C++   serialize(inputs) → bytes.bin
memcmp(bytes.bin, bytes.bin) == 0  →  PASS
```

No field comparison. No JSON. No checksum. **Byte-for-byte identical or fail.**

### Golden vectors are normative for wire encoding

The golden vectors included in the repository are the normative reference for
wire encoding. They are generated by the Python reference serializer from the
current YAML specification.

If contract text conflicts with the golden vectors, the golden vectors win.

If the YAML specification changes, the vectors must be regenerated
(`generate_vectors.py --force`) before they can be used as the normative
reference again. The manifest `protocol_sha256` field proves which YAML
revision produced the vectors.

### Coverage

| Category     | Messages                                         |
|-------------|--------------------------------------------------|
| Pre-session  | HELLO, HELLO_ACK, REJECT                         |
| Empty body   | HEARTBEAT, HEARTBEAT_ACK, DISCONNECT             |
| Simple body  | SCENE_HASH, SCENE_FULL, SCENE_DELTA, SYNC_ACK   |
| UUID body    | OBJECT_CREATE, OBJECT_DELETE, OBJECT_RENAME       |
| Complex body | MESH_DATA, MESH_DELTA, MESH_CHUNK                |
| Material     | MATERIAL_CREATE, MATERIAL_UPDATE, MATERIAL_ASSIGN|
| Camera       | CAMERA_CREATE, CAMERA_UPDATE, CAMERASETACTIVE    |
| Error        | ERROR                                            |
| Edge cases   | compressed flag, SequenceId wraparound           |

---

## 11. What This Contract Does NOT Cover

These are future enhancements, NOT part of the v1 wire contract:

- Protocol version negotiation beyond HELLO/HELLO_ACK
- Compression (zlib) implementation
- Encryption
- Message fragmentation
- SCENE_HASH computation (xxHash64)
- Chunk transfer timeout semantics
- Error handling semantics beyond deserialization errors

These may be added to future versions of this contract after they are
implemented and verified against golden vectors.
