"""
FBX Import MsgType payload builders.

Each function returns the body bytes for one FBX_IMPORT_REQUEST message.
The caller passes these to MsgTransport.send_msg().

Wire format matches C++ deserializer expectations:
  FBX_IMPORT_REQUEST: persistent_id(UUID), version(u32), fbx_path(utf8),
                      object_name(utf8), vert_count(u32), tri_count(u32),
                      mat_slot_count(u32), geometry_hash(u64),
                      sequence_number(u32), timestamp(f64)

Represents the same capability as the legacy PT_FBXImportRequest (0x16)
production packet. sequence_number/timestamp are trailing fields
(MIG-003 pattern).
"""

import struct
from typing import Dict

from .msg_transport import (
    MsgType, pack_u8, pack_u32, pack_u64, pack_f32, pack_f64,
    pack_utf8, pack_uuid,
)
from .protocol_guid import uuid_to_fguid_bytes

# Per-object-id sequence counters for FBX import requests. Key: str(persistent_id).
_fbx_import_sequences: Dict[str, int] = {}


def clear_fbx_sequences() -> None:
    """Reset all FBX import sequence counters (call on session start/end)."""
    _fbx_import_sequences.clear()


def _next_fbx_import_sequence(persistent_id) -> int:
    key = str(persistent_id)
    seq = _fbx_import_sequences.get(key, 0) + 1
    _fbx_import_sequences[key] = seq
    return seq


def build_fbx_import_request(
    persistent_id,
    fbx_path: str,
    object_name: str,
    vert_count: int,
    tri_count: int,
    mat_slot_count: int,
    geometry_hash: int,
    version: int = 1,
    sequence_number: int = 0,
    timestamp: float = 0.0,
) -> bytes:
    """FBX_IMPORT_REQUEST body bytes."""
    body = bytearray()

    # persistent_id: UUID (16 bytes) — Object-GUID reference (FGuid LE layout,
    # MIG-006): must resolve the actor spawned by the OBJECT channel.
    if isinstance(persistent_id, bytes) and len(persistent_id) == 16:
        body.extend(persistent_id)
    else:
        body.extend(uuid_to_fguid_bytes(persistent_id))

    # version: uint32 LE
    body.extend(pack_u32(version))

    # fbx_path: utf8_string
    body.extend(pack_utf8(fbx_path))

    # object_name: utf8_string
    body.extend(pack_utf8(object_name))

    # vert_count: uint32 LE
    body.extend(pack_u32(vert_count))

    # tri_count: uint32 LE
    body.extend(pack_u32(tri_count))

    # mat_slot_count: uint32 LE
    body.extend(pack_u32(mat_slot_count))

    # geometry_hash: uint64 LE
    body.extend(pack_u64(geometry_hash))

    # sequence_number: uint32 LE
    body.extend(pack_u32(sequence_number))

    # timestamp: float64 LE
    body.extend(pack_f64(timestamp))

    return bytes(body)
