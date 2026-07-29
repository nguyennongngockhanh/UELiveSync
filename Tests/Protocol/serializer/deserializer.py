"""
Binary deserializer for LiveSync protocol messages.

Consumes byte-exact wire format per Shared/Protocol/*.yaml.
All multi-byte fields are little-endian unless explicitly stated.
"""

from __future__ import annotations

import struct
import uuid as uuid_mod
from dataclasses import dataclass, field
from typing import Any

from .protocol import (
    MsgType,
    MESSAGES,
    PRE_SESSION_OPCodes,
    HEADER_BEFORE_SESSION_SIZE,
    HEADER_AFTER_SESSION_SIZE,
    LENGTH_PREFIX_SIZE,
    UUID_SIZE,
    TRANSFORM3D_SIZE,
)


class DeserializeError(Exception):
    """Raised when deserialization fails due to malformed input."""
    pass


def _resolve_count(field_def, body_fields: dict) -> int:
    """Resolve dynamic count expression like 'vertex_count * 3'."""
    count_str = getattr(field_def, "count", None)
    if count_str is None:
        return 0
    if isinstance(count_str, int):
        return count_str
    # Parse expressions like "vertex_count * 3"
    parts = str(count_str).split("*")
    var_name = parts[0].strip()
    multiplier = int(parts[1].strip()) if len(parts) > 1 else 1
    return body_fields.get(var_name, 0) * multiplier


# ─── Deserialized Message ───────────────────────────────────────

@dataclass
class DeserializedMessage:
    msg_type: MsgType
    flags: int
    sequence_id: int
    session_id: int | None
    body: dict[str, Any]
    raw_body: bytes
    total_size: int  # Total bytes consumed (length prefix + header + body)


# ─── Primitive Deserializers ────────────────────────────────────

def unpack_uint8(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 1 > len(data):
        raise DeserializeError("Truncated uint8")
    return struct.unpack_from("<B", data, offset)[0], offset + 1


def unpack_uint16(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 2 > len(data):
        raise DeserializeError("Truncated uint16")
    return struct.unpack_from("<H", data, offset)[0], offset + 2


def unpack_uint32(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 4 > len(data):
        raise DeserializeError("Truncated uint32")
    return struct.unpack_from("<I", data, offset)[0], offset + 4


def unpack_uint64(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 8 > len(data):
        raise DeserializeError("Truncated uint64")
    return struct.unpack_from("<Q", data, offset)[0], offset + 8


def unpack_float32(data: bytes, offset: int) -> tuple[float, int]:
    if offset + 4 > len(data):
        raise DeserializeError("Truncated float32")
    return struct.unpack_from("<f", data, offset)[0], offset + 4


def unpack_float64(data: bytes, offset: int) -> tuple[float, int]:
    """IEEE 754 float64 LE (double)."""
    if offset + 8 > len(data):
        raise DeserializeError("Truncated float64")
    return struct.unpack_from("<d", data, offset)[0], offset + 8


def unpack_uuid(data: bytes, offset: int) -> tuple[uuid_mod.UUID, int]:
    """RFC 4122 network byte order. 16 raw bytes."""
    if offset + UUID_SIZE > len(data):
        raise DeserializeError("Truncated UUID")
    raw = data[offset:offset + UUID_SIZE]
    return uuid_mod.UUID(bytes=raw), offset + UUID_SIZE


def unpack_utf8_string(data: bytes, offset: int) -> tuple[str, int]:
    """UTF-8 with uint16 LE length prefix."""
    length, offset = unpack_uint16(data, offset)
    if offset + length > len(data):
        raise DeserializeError(f"Truncated UTF-8 string: need {length} bytes")
    raw = data[offset:offset + length]
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise DeserializeError(f"Invalid UTF-8: {e}")
    return value, offset + length


def unpack_transform3d(data: bytes, offset: int) -> tuple[dict[str, float], int]:
    """Packed 3D transform: 10 × float32 LE."""
    if offset + TRANSFORM3D_SIZE > len(data):
        raise DeserializeError("Truncated transform3d")
    values = struct.unpack_from("<ffffffffff", data, offset)
    result = {
        "px": values[0], "py": values[1], "pz": values[2],
        "rx": values[3], "ry": values[4], "rz": values[5], "rw": values[6],
        "sx": values[7], "sy": values[8], "sz": values[9],
    }
    return result, offset + TRANSFORM3D_SIZE


# ─── Type Dispatch ──────────────────────────────────────────────

def unpack_f32_array(data: bytes, offset: int, count: int) -> tuple[list[float], int]:
    """Array of float32 LE values."""
    size = count * 4
    if offset + size > len(data):
        raise DeserializeError(f"Truncated f32_array: need {size} bytes")
    values = list(struct.unpack_from(f"<{count}f", data, offset))
    return values, offset + size


def unpack_u32_array(data: bytes, offset: int, count: int) -> tuple[list[int], int]:
    """Array of uint32 LE values."""
    size = count * 4
    if offset + size > len(data):
        raise DeserializeError(f"Truncated u32_array: need {size} bytes")
    values = list(struct.unpack_from(f"<{count}I", data, offset))
    return values, offset + size


def unpack_raw_bytes_field(data: bytes, offset: int) -> tuple[bytes, int]:
    """Raw bytes with uint32 LE length prefix."""
    length, offset = unpack_uint32(data, offset)
    if offset + length > len(data):
        raise DeserializeError(f"Truncated raw_bytes: need {length} bytes")
    return data[offset:offset + length], offset + length


def unpack_field(field_type: str, data: bytes, offset: int) -> tuple[Any, int]:
    """Deserialize a single field value according to its type."""
    if field_type == "uint8":
        return unpack_uint8(data, offset)
    elif field_type == "uint16":
        return unpack_uint16(data, offset)
    elif field_type == "uint32":
        return unpack_uint32(data, offset)
    elif field_type == "uint64":
        return unpack_uint64(data, offset)
    elif field_type == "float32":
        return unpack_float32(data, offset)
    elif field_type == "float64":
        return unpack_float64(data, offset)
    elif field_type == "uuid":
        return unpack_uuid(data, offset)
    elif field_type == "utf8_string":
        return unpack_utf8_string(data, offset)
    elif field_type == "transform3d":
        return unpack_transform3d(data, offset)
    else:
        raise DeserializeError(f"Unknown field type: {field_type}")


# ─── Frame Deserialization ──────────────────────────────────────

def deserialize_header(
    data: bytes, offset: int, msg_type: MsgType
) -> tuple[int, int, int | None, int]:
    """
    Deserialize header fields.
    Returns: (flags, sequence_id, session_id, new_offset)
    """
    flags, offset = unpack_uint8(data, offset)
    sequence_id, offset = unpack_uint32(data, offset)

    session_id = None
    if msg_type not in PRE_SESSION_OPCodes:
        session_id, offset = unpack_uint64(data, offset)

    return flags, sequence_id, session_id, offset


def deserialize_frame(data: bytes) -> DeserializedMessage:
    """
    Deserialize a complete wire frame.
    Input: [4-byte length][header][body]
    """
    if len(data) < LENGTH_PREFIX_SIZE:
        raise DeserializeError("Data too short for length prefix")

    payload_length, offset = unpack_uint32(data, 0)
    expected_total = LENGTH_PREFIX_SIZE + payload_length

    if len(data) < expected_total:
        raise DeserializeError(
            f"Truncated frame: need {expected_total} bytes, got {len(data)}"
        )

    # Read MsgType
    msg_type_val, offset = unpack_uint8(data, offset)
    try:
        msg_type = MsgType(msg_type_val)
    except ValueError:
        raise DeserializeError(f"Invalid message opcode: 0x{msg_type_val:02X}")

    # Read rest of header
    flags, sequence_id, session_id, offset = deserialize_header(data, offset, msg_type)

    # Validate header invariant
    if msg_type in PRE_SESSION_OPCodes:
        if session_id is not None:
            raise DeserializeError(
                f"Pre-session message {msg_type.name} contains SessionId"
            )

    # Read body
    msg_def = MESSAGES.get(msg_type)
    if msg_def is None:
        raise DeserializeError(f"Unknown message type: {msg_type.name}")

    body_start = offset
    body_fields: dict[str, Any] = {}

    for field_def in msg_def.body_fields:
        if field_def.type in ("f32_array", "u32_array"):
            count = _resolve_count(field_def, body_fields)
            if count == 0:
                body_fields[field_def.name] = []
                continue
        if offset >= expected_total:
            if field_def.optional:
                break
            else:
                raise DeserializeError(
                    f"Missing required field: {field_def.name}"
                )
        if field_def.type == "f32_array":
            value, offset = unpack_f32_array(data, offset, count)
        elif field_def.type == "u32_array":
            wire_count, offset = unpack_uint32(data, offset)
            value, offset = unpack_u32_array(data, offset, wire_count)
        elif field_def.type == "raw_bytes":
            value, offset = unpack_raw_bytes_field(data, offset)
        else:
            value, offset = unpack_field(field_def.type, data, offset)
        body_fields[field_def.name] = value

    raw_body = data[body_start:offset]

    return DeserializedMessage(
        msg_type=msg_type,
        flags=flags,
        sequence_id=sequence_id,
        session_id=session_id,
        body=body_fields,
        raw_body=raw_body,
        total_size=expected_total,
    )
