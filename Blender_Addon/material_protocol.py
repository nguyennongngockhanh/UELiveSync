"""
Material MsgType payload builders.

Each function returns the body bytes for one MATERIAL_* message.
The caller passes these to MsgTransport.send_msg().

Wire format matches C++ deserializer expectations:
  MATERIAL_CREATE: material_id(UUID), name(str), base_color(f32x4),
                   metallic(f32), roughness(f32), emission(f32x3),
                   [texture_path(str)]
  MATERIAL_UPDATE: material_id(UUID), base_color(f32x4),
                   metallic(f32), roughness(f32), emission(f32x3),
                   [texture_path(str)]
  MATERIAL_ASSIGN: persistent_id(UUID), material_id(UUID), slot_index(u8)
"""

import struct
from typing import Optional, Tuple, List, Dict

from .msg_transport import (
    MsgType, pack_u8, pack_u32, pack_u64,
    pack_f32, pack_utf8, pack_uuid,
)


def _uuid_to_raw(uuid_obj) -> bytes:
    """Convert uuid.UUID to 16 raw bytes (RFC 4122 / network byte order)."""
    return uuid_obj.bytes


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
) -> bytes:
    """MATERIAL_CREATE body bytes."""
    body = bytearray()

    # material_id: UUID (16 bytes)
    if isinstance(material_id, bytes) and len(material_id) == 16:
        body.extend(material_id)
    else:
        body.extend(_uuid_to_raw(material_id))

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

    return bytes(body)


def build_material_update(
    material_id,
    base_color: Tuple[float, float, float, float],
    metallic: float,
    roughness: float,
    emission: Tuple[float, float, float],
    texture_path: Optional[str] = None,
) -> bytes:
    """MATERIAL_UPDATE body bytes."""
    body = bytearray()

    # material_id: UUID (16 bytes)
    if isinstance(material_id, bytes) and len(material_id) == 16:
        body.extend(material_id)
    else:
        body.extend(_uuid_to_raw(material_id))

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

    return bytes(body)


def build_material_assign(
    persistent_id,
    material_id,
    slot_index: int,
) -> bytes:
    """MATERIAL_ASSIGN body bytes."""
    body = bytearray()

    # persistent_id: UUID (16 bytes) — the object
    if isinstance(persistent_id, bytes) and len(persistent_id) == 16:
        body.extend(persistent_id)
    else:
        body.extend(_uuid_to_raw(persistent_id))

    # material_id: UUID (16 bytes)
    if isinstance(material_id, bytes) and len(material_id) == 16:
        body.extend(material_id)
    else:
        body.extend(_uuid_to_raw(material_id))

    # slot_index: uint8
    body.extend(pack_u8(slot_index))

    return bytes(body)
