# Phase 1.2 — Verification Complete

## Summary

Phase 1.2 verified the C++ serializer/deserializer against the Python reference
implementation and the frozen protocol spec. The verification found and fixed
**two real bugs** — one in the Python reference, one in C++ — confirming the
test suite is an independent verification system, not just a C++-parity checker.

## Final Test Results

| Suite | Result |
|---|---|
| Primitives | 25/25 PASS |
| Serializer (31 golden vectors) | 31/31 PASS |
| Deserializer (31 golden vectors) | 31/31 PASS |
| Round-trip (byte + semantic) | 31/31 PASS |
| Property tests (13,525 checks) | 13,525/13,525 PASS |
| Cross-language (C++ deserialize→JSON) | 31/31 PASS |
| Cross-language (C++ serialize→bin→JSON) | 31/31 PASS |
| Cross-language (Python deserialize C++ .bin) | 31/31 PASS |
| Python parity (pytest) | 51/51 PASS |
| Negative tests | 18/18 PASS |
| Fuzz / robustness (768 checks) | 768/768 PASS |

## Bugs Found and Fixed

### Bug 1: `uuid_bytes_to_string` hyphen-insertion (C++)

The C++ `uuid_bytes_to_string()` used a fixed 36-char buffer and inserted
hyphens at hardcoded positions, causing subsequent hex digits to shift.

**Fix:** Build string sequentially with a `pos` counter, inserting hyphens at
byte boundaries 3, 5, 7, 9.

**File:** `tests/support/reserialize.h`

### Bug 2: Python deserializer u32_array length prefix (Python)

The Python serializer writes a `uint32` length prefix before `u32_array` data,
but the Python deserializer read count from the YAML definition without
consuming the wire prefix. This caused `MESH_DATA.indices` to read `[3, 0, 1]`
instead of `[0, 1, 2]`.

**Fix:** Added `unpack_uint32()` call before `unpack_u32_array()` in the
Python deserializer.

**File:** `Tests/Protocol/serializer/deserializer.py`

## Wire Format Lessons Learned

These are not spec changes — they document the actual wire behavior confirmed
by cross-language testing.

### 1. `u32_array` has a `uint32` length prefix

```
[uint32 LE count] [count × uint32 LE values]
```

`f32_array` does NOT have a length prefix — count is derived from a sibling
field via YAML `count` expression.

**Why it matters:** Implementations must not confuse these two array encodings.

### 2. `raw_bytes` has a `uint32` length prefix

```
[uint32 LE byte_count] [byte_count × bytes]
```

Used in `MESH_CHUNK.data`.

### 3. `transform3d` is exactly 40 bytes

```
[float32×3 position] [float32×4 quaternion] [float32×3 scale]
```

No length prefix. Quaternion is `(rx, ry, rz, rw)` — `w` is last.

### 4. Quaternion canonicalization

The serializer normalizes quaternions via `float64` intermediate precision.
Both Python and C++ use the same approach: normalize in double, cast back to
float, then `canonicalize_float()` each component.

**The serializer does NOT enforce `w ≥ 0`.** Negative-w quaternions are
serialized as-is. Implementations should normalize on deserialization.

### 5. Float canonicalization

All `float32` values pass through `canonicalize_float()`:
- `-0.0` → `+0.0`
- NaN → rejected (serialize error)
- Inf / denormal → pass through (no rejection at wire level)

### 6. UUID wire format

RFC 4122 16-byte binary. Serialized as raw bytes, NOT as Windows GUID
(`Data1`, `Data2`, `Data3` mixed-endian). Hyphenated representation is
display-only.

Wire order: bytes 0–15 directly, no endian conversion.

### 7. String encoding

UTF-8 with `uint16` LE length prefix. Length is byte count, not character
count. Invalid UTF-8 is not validated at the wire level.

### 8. Pre/post-session rule

| Message | Session in header? | Session in body? |
|---|---|---|
| HELLO | No | No |
| HELLO_ACK | No | Yes (body field `session_id`) |
| REJECT | No | No |
| All others | Yes | N/A |

The C++ deserializer does NOT validate that pre-session messages lack session_id
bytes on the wire — it simply doesn't read them. Extra bytes are silently
ignored.

### 9. Optional-field limitation

`MATERIAL_UPDATE`, `OBJECT_UPDATE`, and `CAMERA_UPDATE` have optional fields
in the YAML spec but NO presence bitmask on the wire. When optional fields are
skipped mid-stream, the deserializer cannot distinguish them from trailing data.

**Workaround:** Golden vectors always serialize ALL optional fields. The
deserializer reads in YAML field order and uses `state.offset < total_size`
to detect remaining data.

**This is a protocol v1 limitation.** If field-skipping becomes necessary in
v2, add a `uint32 field_mask` after the required fields.

### 10. Dynamic-length arrays

`MESH_DATA` and `MESH_DELTA` use sibling fields as array counts:

```
vertex_count → vertices, normals (count = vertex_count × 3)
              → uvs (count = vertex_count × 2)
index_count  → indices (count = index_count, with uint32 length prefix)
```

If `vertex_count` or `index_count` is large but the frame is truncated, the
deserializer throws `std::runtime_error("Truncated ...")`.

### 11. Object_create parent_id detection

`OBJECT_CREATE` has optional `parent_id`. Detection: if ≥ 56 bytes remain
after reading `persistent_id` + `name` (16 + 2 + name_len), read
`parent_id` + `transform`. Otherwise, read only `transform`.

This heuristic works for the current field set but is fragile if fields are
added before `parent_id`.

## File Inventory

### Protocol spec (source of truth)
- `Shared/Protocol/MessageTypes.yaml` — 28 message definitions (frozen)
- `Shared/Protocol/Types.yaml` — composite types (frozen)
- `Shared/Protocol/Capabilities.yaml` — capability bits (frozen)
- `Shared/Protocol/Errors.yaml` — error codes + rules (frozen)
- `Shared/Protocol/Versioning.md` — versioning rules

### Python reference
- `Tests/Protocol/serializer/protocol.py` — MsgType enum, field defs
- `Tests/Protocol/serializer/serializer.py` — binary serializer
- `Tests/Protocol/serializer/deserializer.py` — binary deserializer
- `Tests/Protocol/common.py` — `compute_protocol_sha256()`, `load_yaml()`
- `Tests/Protocol/vectors/generate_vectors.py` — golden vector generator
- `Tests/Protocol/vectors/v1/` — 31 .bin files + manifest + SHA256SUMS

### C++ implementation
- `Shared/Serializer/livesync_serializer.h` — primitives + PackFrame
- `Shared/Serializer/livesync_messages.h` — 28 serialize_body_* functions
- `Shared/Serializer/livesync_deserializer.h` — DeserializeFrame
- `Shared/Serializer/serializer_utils.h` — parse_uuid

### Tests
- `Shared/Serializer/test_primitives.cpp` — 25 raw-input tests
- `Shared/Serializer/test_serializer.cpp` — 31 golden vector byte tests
- `Shared/Serializer/test_deserializer.cpp` — 31 golden vector parse tests
- `Shared/Serializer/test_roundtrip.cpp` — byte + semantic round-trip
- `Shared/Serializer/test_property.cpp` — 13,525 property checks
- `Shared/Serializer/test_cross_language.cpp` — C++ ↔ Python cross-lang
- `Shared/Serializer/test_fuzz.cpp` — 768 robustness checks
- `Shared/Serializer/run_all_tests.sh` — unified runner (9 suites)
- `Tests/Protocol/tests/test_serialization.py` — 51 Python tests
- `Tests/Protocol/tests/test_negative.py` — 18 negative tests
- `Tests/Protocol/cross_language_verify.py` — Python verification of C++ output

### Test adapters
- `Shared/Serializer/tests/support/manifest_loader.h` — manifest → serialize
- `Shared/Serializer/tests/support/reserialize.h` — round-trip adapter
