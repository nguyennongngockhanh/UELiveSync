"""
Material MsgType payload builders.

Each function returns the body bytes for one MATERIAL_* message.
The caller passes these to MsgTransport.send_msg().

Wire format matches C++ deserializer expectations:
  MATERIAL_CREATE: material_id(UUID), name(str), base_color(f32x4),
                   metallic(f32), roughness(f32), emission(f32x3),
                   [texture_path(str)], sequence_number(u32), timestamp(f64)
  MATERIAL_UPDATE: material_id(UUID), base_color(f32x4),
                   metallic(f32), roughness(f32), emission(f32x3),
                   [texture_path(str)], sequence_number(u32), timestamp(f64)
  MATERIAL_ASSIGN: persistent_id(UUID), material_id(UUID), slot_index(u8),
                   sequence_number(u32), timestamp(f64)

Material properties are always packed (full state); texture_path is the
only optional field. texture_path presence is derived from the trailing
sequence_number/timestamp: remaining >= 2 (min utf8 prefix) + 4 + 8 = 14.
"""

import struct
from typing import Optional, Tuple, List, Dict

from .msg_transport import (
    MsgType, pack_u8, pack_u32, pack_u64, pack_f32, pack_f64,
    pack_utf8, pack_uuid,
)
from .protocol_guid import uuid_to_fguid_bytes, uuid_to_rfc4122_bytes

# Per-material-id sequence counters (like objects and cameras).
# Key: str(material_id). create/update tracked separately so stale
# create packets never advance the update counter and vice versa.
_material_create_sequences: Dict[str, int] = {}
_material_update_sequences: Dict[str, int] = {}
# Per-object-id sequence counters for assigns. Key: str(persistent_id).
_material_assign_sequences: Dict[str, int] = {}


def clear_material_sequences() -> None:
    """Reset all material sequence counters (call on session start/end)."""
    _material_create_sequences.clear()
    _material_update_sequences.clear()
    _material_assign_sequences.clear()


def _next_material_create_sequence(material_id) -> int:
    key = str(material_id)
    seq = _material_create_sequences.get(key, 0) + 1
    _material_create_sequences[key] = seq
    return seq


def _next_material_update_sequence(material_id) -> int:
    key = str(material_id)
    seq = _material_update_sequences.get(key, 0) + 1
    _material_update_sequences[key] = seq
    return seq


def _next_material_assign_sequence(persistent_id) -> int:
    key = str(persistent_id)
    seq = _material_assign_sequences.get(key, 0) + 1
    _material_assign_sequences[key] = seq
    return seq


def _uuid_to_raw_from_hex(hex_str: str) -> bytes:
    """Convert 32-char hex string to 16 raw bytes."""
    import uuid as _uuid
    return _uuid.UUID(hex_str).bytes


def build_material_create(
    material_id,
    name: str,
    base_color: Tuple[float, float, float, float],
    metallic: float,
    roughness: float,
    emission: Tuple[float, float, float],
    texture_path: Optional[str] = None,
    sequence_number: int = 0,
    timestamp: float = 0.0,
) -> bytes:
    """MATERIAL_CREATE body bytes."""
    body = bytearray()

    # material_id: UUID (16 bytes)
    if isinstance(material_id, bytes) and len(material_id) == 16:
        body.extend(material_id)
    else:
        body.extend(uuid_to_rfc4122_bytes(material_id))

    # name: utf8_string
    body.extend(pack_utf8(name))

    # base_color: f32_array(4) — RGBA
    body.extend(struct.pack('<4f', *base_color[:4]))

    # metallic: float32
    body.extend(pack_f32(metallic))

    # roughness: float32
    body.extend(pack_f32(roughness))

    # emission: f32_array(3) — RGB
    body.extend(struct.pack('<3f', *emission[:3]))

    # texture_path: utf8_string (optional)
    if texture_path:
        body.extend(pack_utf8(texture_path))

    # sequence_number: uint32 LE
    body.extend(pack_u32(sequence_number))

    # timestamp: float64 LE
    body.extend(pack_f64(timestamp))

    return bytes(body)


def build_material_update(
    material_id,
    base_color: Tuple[float, float, float, float],
    metallic: float,
    roughness: float,
    emission: Tuple[float, float, float],
    texture_path: Optional[str] = None,
    sequence_number: int = 0,
    timestamp: float = 0.0,
) -> bytes:
    """MATERIAL_UPDATE body bytes."""
    body = bytearray()

    # material_id: UUID (16 bytes)
    if isinstance(material_id, bytes) and len(material_id) == 16:
        body.extend(material_id)
    else:
        body.extend(uuid_to_rfc4122_bytes(material_id))

    # base_color: f32_array(4)
    body.extend(struct.pack('<4f', *base_color[:4]))

    # metallic: float32
    body.extend(pack_f32(metallic))

    # roughness: float32
    body.extend(pack_f32(roughness))

    # emission: f32_array(3)
    body.extend(struct.pack('<3f', *emission[:3]))

    # texture_path: utf8_string (optional)
    if texture_path:
        body.extend(pack_utf8(texture_path))

    # sequence_number: uint32 LE
    body.extend(pack_u32(sequence_number))

    # timestamp: float64 LE
    body.extend(pack_f64(timestamp))

    return bytes(body)


def build_material_assign(
    persistent_id,
    material_id,
    slot_index: int,
    sequence_number: int = 0,
    timestamp: float = 0.0,
) -> bytes:
    """MATERIAL_ASSIGN body bytes."""
    body = bytearray()

    # persistent_id: UUID (16 bytes) — Object-GUID reference (FGuid LE layout,
    # MIG-006): must resolve the actor spawned by the OBJECT channel.
    if isinstance(persistent_id, bytes) and len(persistent_id) == 16:
        body.extend(persistent_id)
    else:
        body.extend(uuid_to_fguid_bytes(persistent_id))

    # material_id: UUID (16 bytes) — material-namespace identity (RFC 4122).
    if isinstance(material_id, bytes) and len(material_id) == 16:
        body.extend(material_id)
    else:
        body.extend(uuid_to_rfc4122_bytes(material_id))

    # slot_index: uint8
    body.extend(pack_u8(slot_index))

    # sequence_number: uint32 LE
    body.extend(pack_u32(sequence_number))

    # timestamp: float64 LE
    body.extend(pack_f64(timestamp))

    return bytes(body)
