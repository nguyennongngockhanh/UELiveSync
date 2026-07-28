"""
Object MsgType payload builders.

Each function returns the body bytes for one OBJECT_* message.
The caller passes these to MsgTransport.send_msg().

Wire format matches C++ deserializer expectations:
  OBJECT_CREATE: persistent_id(UUID), name(str), [parent_id(UUID)], transform(f32x10)
  OBJECT_UPDATE: persistent_id(UUID), transform(f32x10), name(str), visibility(u8)
  OBJECT_DELETE: persistent_id(UUID)
"""

import struct
from typing import Optional, Tuple

from .msg_transport import (
    MsgType, pack_u8, pack_utf8, pack_uuid,
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
) -> bytes:
    """OBJECT_CREATE body bytes.

    Wire format (matches C++ deserialize_body_object_create):
      persistent_id: UUID (16 bytes)
      name: utf8_string
      parent_id: UUID (16 bytes, optional — sent if present)
      transform: 10 floats LE (loc.xyz, rot.xyzw, scale.xyz)
    """
    body = bytearray()

    # persistent_id: UUID (16 bytes, FGuid LE layout)
    body.extend(_uuid_to_fguid_bytes(persistent_id))

    # name: utf8_string
    body.extend(pack_utf8(name))

    # parent_id: UUID (optional, 16 bytes, FGuid LE layout)
    if parent_id is not None:
        body.extend(_uuid_to_fguid_bytes(parent_id))

    # transform: 10 floats LE
    body.extend(struct.pack('<3f', *location[:3]))
    body.extend(struct.pack('<4f', *rotation[:4]))
    body.extend(struct.pack('<3f', *scale[:3]))

    return bytes(body)


def build_object_update(
    persistent_id,
    location: Tuple[float, float, float],
    rotation: Tuple[float, float, float, float],
    scale: Tuple[float, float, float],
    name: Optional[str] = None,
    visibility: Optional[int] = None,
) -> bytes:
    """OBJECT_UPDATE body bytes.

    Wire format (matches C++ deserialize_body_object_update):
      persistent_id: UUID (16 bytes)
      transform: 10 floats LE
      name: utf8_string
      visibility: uint8
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

    return bytes(body)


def build_object_delete(
    persistent_id,
) -> bytes:
    """OBJECT_DELETE body bytes.

    Wire format (matches C++ deserialize_body_object_delete):
      persistent_id: UUID (16 bytes)
    """
    body = bytearray()
    body.extend(_uuid_to_fguid_bytes(persistent_id))
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
