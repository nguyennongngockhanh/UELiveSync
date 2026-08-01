"""
Object MsgType payload builders.

Each function returns the body bytes for one OBJECT_* message.
The caller passes these to MsgTransport.send_msg().

Wire format matches C++ deserializer expectations:
  OBJECT_CREATE: persistent_id(UUID), name(str), [parent_id(UUID)], primitive_type(u8), transform(f32x10), sequence_number(u32), timestamp(f64)
  OBJECT_UPDATE: persistent_id(UUID), transform(f32x10), name(str), visibility(u8), sequence_number(u32), timestamp(f64)
  OBJECT_DELETE: persistent_id(UUID), sequence_number(u32), timestamp(f64)
"""

import struct
import time
from typing import Optional, Tuple

from .msg_transport import (
    MsgType, pack_u8, pack_u32, pack_f64, pack_utf8, pack_uuid,
)


def _uuid_to_fguid_bytes(uuid_obj) -> bytes:
    """Convert uuid.UUID to 16 bytes matching FGuid internal LE layout.

    FGuid stores A,B,C,D as uint32 in native (LE) byte order.
    FMemory::Memcpy reads these bytes directly, so we must pack
    in the same layout as the legacy serializer (struct.pack('<IIII', ...)).

    This is DIFFERENT from uuid.UUID.bytes (big-endian RFC 4122).
    Using uuid.UUID.bytes produces a byte-swapped GUID that won't match
    actors spawned by the legacy PT_Create path.
    """
    a = uuid_obj.time_low
    b = (uuid_obj.time_mid << 16) | uuid_obj.time_hi_version
    c = ((uuid_obj.clock_seq_hi_variant << 24) |
         (uuid_obj.clock_seq_low << 16) |
         ((uuid_obj.node >> 32) & 0xFFFF))
    d = uuid_obj.node & 0xFFFFFFFF
    return struct.pack('<IIII', a, b, c, d)


def build_object_create(
    persistent_id,
    name: str,
    location: Tuple[float, float, float],
    rotation: Tuple[float, float, float, float],
    scale: Tuple[float, float, float],
    parent_id=None,
    primitive_type: int = 1,
    sequence_number: int = 0,
    timestamp: float = 0.0,
) -> bytes:
    """OBJECT_CREATE body bytes.

    Wire format (matches C++ deserialize_body_object_create):
      persistent_id:    UUID (16 bytes)
      name:             utf8_string
      parent_id:        UUID (16 bytes, optional — sent if present)
      primitive_type:   uint8
      transform:        10 floats LE (loc.xyz, rot.xyzw, scale.xyz)
      sequence_number:  uint32 LE
      timestamp:        float64 LE
    """
    body = bytearray()

    # persistent_id: UUID (16 bytes, FGuid LE layout)
    body.extend(_uuid_to_fguid_bytes(persistent_id))

    # name: utf8_string
    body.extend(pack_utf8(name))

    # parent_id: UUID (optional, 16 bytes, FGuid LE layout)
    if parent_id is not None:
        body.extend(_uuid_to_fguid_bytes(parent_id))

    # primitive_type: uint8
    body.extend(pack_u8(primitive_type))

    # transform: 10 floats LE
    body.extend(struct.pack('<3f', *location[:3]))
    body.extend(struct.pack('<4f', *rotation[:4]))
    body.extend(struct.pack('<3f', *scale[:3]))

    # sequence_number: uint32 LE
    body.extend(pack_u32(sequence_number))

    # timestamp: float64 LE
    body.extend(pack_f64(timestamp))

    return bytes(body)


def build_object_update(
    persistent_id,
    location: Tuple[float, float, float],
    rotation: Tuple[float, float, float, float],
    scale: Tuple[float, float, float],
    name: Optional[str] = None,
    visibility: Optional[int] = None,
    sequence_number: int = 0,
    timestamp: float = 0.0,
) -> bytes:
    """OBJECT_UPDATE body bytes.

    Wire format (matches C++ deserialize_body_object_update):
      persistent_id:    UUID (16 bytes)
      transform:        10 floats LE
      name:             utf8_string
      visibility:       uint8
      sequence_number:  uint32 LE
      timestamp:        float64 LE
    """
    body = bytearray()

    # persistent_id: UUID (16 bytes, FGuid LE layout)
    body.extend(_uuid_to_fguid_bytes(persistent_id))

    # transform: 10 floats LE
    body.extend(struct.pack('<3f', *location[:3]))
    body.extend(struct.pack('<4f', *rotation[:4]))
    body.extend(struct.pack('<3f', *scale[:3]))

    # name: utf8_string (always present for now)
    body.extend(pack_utf8(name or ""))

    # visibility: uint8
    body.extend(pack_u8(visibility if visibility is not None else 1))

    # sequence_number: uint32 LE
    body.extend(pack_u32(sequence_number))

    # timestamp: float64 LE
    body.extend(pack_f64(timestamp))

    return bytes(body)


# ─── Per-GUID sequence trackers ──────────────────────────────
# Monotonic counters per GUID for stale-rejection on UE side.
# Each message type has its own namespace so counters are
# independent across create/update/delete.
# Cleared on disconnect via clear_all_sequences().

_delete_sequences = {}
_update_sequences = {}
_create_sequences = {}


def clear_all_sequences():
    """Reset all per-GUID sequence counters (call on disconnect)."""
    _delete_sequences.clear()
    _update_sequences.clear()
    _create_sequences.clear()


def clear_delete_sequences():
    """Reset per-GUID delete sequence counters (legacy alias)."""
    _delete_sequences.clear()


def next_create_sequence(persistent_id) -> int:
    """Return next monotonic sequence for OBJECT_CREATE for this GUID."""
    key = str(persistent_id)
    seq = _create_sequences.get(key, 0) + 1
    _create_sequences[key] = seq
    return seq


def next_update_sequence(persistent_id) -> int:
    """Return next monotonic sequence for OBJECT_UPDATE for this GUID."""
    key = str(persistent_id)
    seq = _update_sequences.get(key, 0) + 1
    _update_sequences[key] = seq
    return seq


def build_object_delete(
    persistent_id,
) -> bytes:
    """OBJECT_DELETE body bytes.

    Wire format (matches C++ deserialize_body_object_delete):
      persistent_id:     UUID (16 bytes)
      sequence_number:   uint32 LE — monotonic per-GUID counter
      timestamp:         float64 LE — seconds since epoch

    Total body: 28 bytes.
    """
    body = bytearray()
    body.extend(_uuid_to_fguid_bytes(persistent_id))

    # Monotonic sequence per GUID (replay dedup / stale rejection)
    guid_key = str(persistent_id)
    seq = _delete_sequences.get(guid_key, 0) + 1
    _delete_sequences[guid_key] = seq
    body.extend(pack_u32(seq))

    # Timestamp (float64, seconds since epoch)
    body.extend(pack_f64(time.time()))

    return bytes(body)


def build_object_rename(
    persistent_id,
    new_name: str,
) -> bytes:
    """OBJECT_RENAME body bytes.

    Wire format (matches C++ BuildObjectRenameView):
      persistent_id: UUID (16 bytes, FGuid LE)
      new_name:      utf8_string
    """
    body = bytearray()
    body.extend(_uuid_to_fguid_bytes(persistent_id))
    body.extend(pack_utf8(new_name))
    return bytes(body)


def build_object_visibility(
    persistent_id,
    visible: bool,
) -> bytes:
    """OBJECT_VISIBILITY body bytes.

    Wire format (matches C++ BuildObjectVisibilityView):
      persistent_id: UUID (16 bytes, FGuid LE)
      visible:       uint8 (1=visible, 0=hidden)
    """
    body = bytearray()
    body.extend(_uuid_to_fguid_bytes(persistent_id))
    body.extend(pack_u8(1 if visible else 0))
    return bytes(body)


def build_object_reparent(
    persistent_id,
    new_parent_id=None,
) -> bytes:
    """OBJECT_REPARENT body bytes.

    Wire format (matches C++ BuildObjectReparentView):
      persistent_id:  UUID (16 bytes, FGuid LE)
      new_parent_id:  UUID (16 bytes, FGuid LE, all-zero = detach)
    """
    body = bytearray()
    body.extend(_uuid_to_fguid_bytes(persistent_id))
    if new_parent_id is not None:
        body.extend(_uuid_to_fguid_bytes(new_parent_id))
    else:
        body.extend(b'\x00' * 16)
    return bytes(body)


# ─── Camera MsgType builders (MIG-003) ───────────────────────
# Per-GUID sequence trackers for camera operations.
# Separate from object sequences because camera GUIDs live in
# their own namespace.

_camera_create_sequences = {}
_camera_update_sequences = {}


def clear_camera_sequences():
    """Reset all per-GUID camera sequence counters (call on disconnect)."""
    _camera_create_sequences.clear()
    _camera_update_sequences.clear()


def _next_camera_create_sequence(camera_id) -> int:
    key = str(camera_id)
    seq = _camera_create_sequences.get(key, 0) + 1
    _camera_create_sequences[key] = seq
    return seq


def _next_camera_update_sequence(camera_id) -> int:
    key = str(camera_id)
    seq = _camera_update_sequences.get(key, 0) + 1
    _camera_update_sequences[key] = seq
    return seq


def build_camera_create(
    camera_id,
    name: str,
    parent_id=None,
    location: Tuple[float, float, float] = (0, 0, 0),
    rotation: Tuple[float, float, float, float] = (0, 0, 0, 1),
    scale: Tuple[float, float, float] = (1, 1, 1),
    focal_length: float = 50.0,
    sensor_width: float = 36.0,
    sensor_height: float = 24.0,
    clip_start: float = 0.1,
    clip_end: float = 1000.0,
    ortho_scale: float = 6.0,
    camera_flags: int = 0,
    sequence_number: int = 0,
    timestamp: float = 0.0,
) -> bytes:
    """CAMERA_CREATE body bytes (full camera state).

    Wire format (matches C++ deserialize_body_camera_create):
      camera_id:      UUID (16 bytes)
      name:           utf8_string
      parent_id:      UUID (16 bytes, optional)
      transform:      10 floats LE (loc.xyz, rot.xyzw, scale.xyz)
      focal_length:   float32 LE
      sensor_width:   float32 LE
      sensor_height:  float32 LE
      clip_start:     float32 LE
      clip_end:       float32 LE
      ortho_scale:    float32 LE
      camera_flags:   uint8 (bit0=is_ortho; bit1=DEPRECATED)
      sequence_number: uint32 LE
      timestamp:      float64 LE
    """
    body = bytearray()
    body.extend(_uuid_to_fguid_bytes(camera_id))
    body.extend(pack_utf8(name))
    if parent_id is not None:
        body.extend(_uuid_to_fguid_bytes(parent_id))
    body.extend(struct.pack('<10f',
        location[0], location[1], location[2],
        rotation[0], rotation[1], rotation[2], rotation[3],
        scale[0], scale[1], scale[2]))
    body.extend(struct.pack('<f', focal_length))
    body.extend(struct.pack('<f', sensor_width))
    body.extend(struct.pack('<f', sensor_height))
    body.extend(struct.pack('<f', clip_start))
    body.extend(struct.pack('<f', clip_end))
    body.extend(struct.pack('<f', ortho_scale))
    body.extend(pack_u8(camera_flags))
    body.extend(pack_u32(sequence_number))
    body.extend(pack_f64(timestamp))
    return bytes(body)


def build_camera_update(
    camera_id,
    location: Tuple[float, float, float],
    rotation: Tuple[float, float, float, float],
    scale: Tuple[float, float, float],
    focal_length: float,
    sensor_width: float,
    sensor_height: float,
    clip_start: float,
    clip_end: float,
    ortho_scale: float,
    camera_flags: int = 0,
    sequence_number: int = 0,
    timestamp: float = 0.0,
) -> bytes:
    """CAMERA_UPDATE body bytes (full camera state).

    Wire format (matches C++ deserialize_body_camera_update):
      camera_id:      UUID (16 bytes)
      transform:      10 floats LE
      focal_length:   float32 LE
      sensor_width:   float32 LE
      sensor_height:  float32 LE
      clip_start:     float32 LE
      clip_end:       float32 LE
      ortho_scale:    float32 LE
      camera_flags:   uint8
      sequence_number: uint32 LE
      timestamp:      float64 LE
    """
    body = bytearray()
    body.extend(_uuid_to_fguid_bytes(camera_id))
    body.extend(struct.pack('<10f',
        location[0], location[1], location[2],
        rotation[0], rotation[1], rotation[2], rotation[3],
        scale[0], scale[1], scale[2]))
    body.extend(struct.pack('<f', focal_length))
    body.extend(struct.pack('<f', sensor_width))
    body.extend(struct.pack('<f', sensor_height))
    body.extend(struct.pack('<f', clip_start))
    body.extend(struct.pack('<f', clip_end))
    body.extend(struct.pack('<f', ortho_scale))
    body.extend(pack_u8(camera_flags))
    body.extend(pack_u32(sequence_number))
    body.extend(pack_f64(timestamp))
    return bytes(body)


def build_camera_setactive(
    camera_id,
) -> bytes:
    """CAMERASETACTIVE body bytes.

    Wire format (matches C++ BuildCameraSetActiveView):
      camera_id: UUID (16 bytes)
    """
    body = bytearray()
    body.extend(_uuid_to_fguid_bytes(camera_id))
    return bytes(body)
