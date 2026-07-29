"""
Binary serializer for LiveSync protocol messages.

Produces byte-exact wire format per Shared/Protocol/*.yaml.
All multi-byte fields are little-endian unless explicitly stated.
"""

from __future__ import annotations

import struct
import uuid as uuid_mod
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
    FieldDef,
    MessageDef,
    canonicalize_float,
    canonicalize_quaternion,
)


class SerializeError(Exception):
    """Raised when serialization fails due to invalid input."""
    pass


# ─── Primitive Serializers ──────────────────────────────────────

def pack_uint8(v: int) -> bytes:
    if not 0 <= v <= 0xFF:
        raise SerializeError(f"uint8 out of range: {v}")
    return struct.pack("<B", v)


def pack_uint16(v: int) -> bytes:
    if not 0 <= v <= 0xFFFF:
        raise SerializeError(f"uint16 out of range: {v}")
    return struct.pack("<H", v)


def pack_uint32(v: int) -> bytes:
    if not 0 <= v <= 0xFFFFFFFF:
        raise SerializeError(f"uint32 out of range: {v}")
    return struct.pack("<I", v)


def pack_uint64(v: int) -> bytes:
    if not 0 <= v <= 0xFFFFFFFFFFFFFFFF:
        raise SerializeError(f"uint64 out of range: {v}")
    return struct.pack("<Q", v)


def pack_float32(v: float) -> bytes:
    canonical = canonicalize_float(v)
    return struct.pack("<f", canonical)


def pack_float64(v: float) -> bytes:
    return struct.pack("<d", v)


def pack_uuid(v: str | uuid_mod.UUID) -> bytes:
    """
    RFC 4122 network byte order. 16 raw bytes, no field-wise conversion.
    NOT Windows GUID mixed-endian.
    """
    if isinstance(v, str):
        u = uuid_mod.UUID(v)
    else:
        u = v
    return u.bytes  # Already RFC 4122 canonical 16 bytes


def pack_utf8_string(v: str) -> bytes:
    """UTF-8 with uint16 LE length prefix. Length = bytes, not characters."""
    encoded = v.encode("utf-8")
    if len(encoded) > 0xFFFF:
        raise SerializeError(f"String too long: {len(encoded)} bytes")
    return pack_uint16(len(encoded)) + encoded


def pack_transform3d(
    px: float, py: float, pz: float,
    rx: float, ry: float, rz: float, rw: float,
    sx: float, sy: float, sz: float,
) -> bytes:
    """
    Packed 3D transform: 10 × float32 LE. No alignment padding.
    Quaternion MUST be normalized. NaN rejected.
    """
    rx, ry, rz, rw = canonicalize_quaternion(rx, ry, rz, rw)
    return struct.pack(
        "<ffffffffff",
        canonicalize_float(px), canonicalize_float(py), canonicalize_float(pz),
        canonicalize_float(rx), canonicalize_float(ry), canonicalize_float(rz), canonicalize_float(rw),
        canonicalize_float(sx), canonicalize_float(sy), canonicalize_float(sz),
    )


def pack_raw_bytes(v: bytes) -> bytes:
    """Raw bytes with uint32 LE length prefix."""
    return pack_uint32(len(v)) + v


def pack_f32_array(values: list[float]) -> bytes:
    """Array of float32 LE values."""
    return struct.pack(f"<{len(values)}f", *[canonicalize_float(v) for v in values])


# ─── Type Dispatch ──────────────────────────────────────────────

def pack_field(field_type: str, value: Any) -> bytes:
    """Serialize a single field value according to its type."""
    if field_type == "uint8":
        return pack_uint8(value)
    elif field_type == "uint16":
        return pack_uint16(value)
    elif field_type == "uint32":
        return pack_uint32(value)
    elif field_type == "uint64":
        return pack_uint64(value)
    elif field_type == "float32":
        return pack_float32(value)
    elif field_type == "float64":
        return pack_float64(value)
    elif field_type == "uuid":
        return pack_uuid(value)
    elif field_type == "utf8_string":
        return pack_utf8_string(value)
    elif field_type == "f32_array":
        return pack_f32_array(value)
    elif field_type == "u32_array":
        return pack_uint32(len(value)) + b"".join(pack_uint32(v) for v in value)
    elif field_type == "raw_bytes":
        if isinstance(value, bytes):
            return pack_raw_bytes(value)
        else:
            return pack_raw_bytes(bytes(value))
    elif field_type == "transform3d":
        if isinstance(value, dict):
            return pack_transform3d(
                value["px"], value["py"], value["pz"],
                value["rx"], value["ry"], value["rz"], value["rw"],
                value["sx"], value["sy"], value["sz"],
            )
        elif isinstance(value, (list, tuple)) and len(value) == 10:
            return pack_transform3d(*value)
        else:
            raise SerializeError(f"Invalid transform3d value: {value}")
    else:
        raise SerializeError(f"Unknown field type: {field_type}")


# ─── Header Serialization ───────────────────────────────────────

def pack_header(
    msg_type: MsgType,
    flags: int,
    sequence_id: int,
    session_id: int | None = None,
) -> bytes:
    """Serialize message header (pre-session or post-session)."""
    header = bytearray()
    header.extend(pack_uint8(msg_type))
    header.extend(pack_uint8(flags))
    header.extend(pack_uint32(sequence_id))

    if msg_type in PRE_SESSION_OPCodes:
        if session_id is not None:
            raise SerializeError(f"Pre-session message {msg_type.name} must not contain SessionId")
    else:
        if session_id is None:
            raise SerializeError(f"Post-session message {msg_type.name} must contain SessionId")
        header.extend(pack_uint64(session_id))

    return bytes(header)


# ─── Frame Serialization ────────────────────────────────────────

def pack_frame(
    msg_type: MsgType,
    flags: int,
    sequence_id: int,
    session_id: int | None,
    body: bytes,
) -> bytes:
    """
    Complete wire frame: [4-byte length][header][body].
    Length = sizeof(header) + sizeof(body).
    """
    if msg_type in PRE_SESSION_OPCodes:
        header_size = HEADER_BEFORE_SESSION_SIZE
    else:
        header_size = HEADER_AFTER_SESSION_SIZE

    header = pack_header(msg_type, flags, sequence_id, session_id)
    payload_length = header_size + len(body)

    return pack_uint32(payload_length) + header + body


# ─── Message Body Serialization ─────────────────────────────────

def serialize_body(msg_type: MsgType, fields: dict[str, Any]) -> bytes:
    """Serialize message body fields according to message definition."""
    msg_def = MESSAGES.get(msg_type)
    if msg_def is None:
        raise SerializeError(f"Unknown message type: {msg_type}")

    body = bytearray()
    for field_def in msg_def.body_fields:
        if field_def.name not in fields:
            if field_def.optional:
                continue
            else:
                raise SerializeError(f"Missing required field: {field_def.name}")
        value = fields[field_def.name]
        body.extend(pack_field(field_def.type, value))

    return bytes(body)


# ─── High-Level Serialization ───────────────────────────────────

def serialize_message(
    msg_type: MsgType,
    flags: int = 0,
    sequence_id: int = 0,
    header_session_id: int | None = None,
    **fields: Any,
) -> bytes:
    """
    Serialize a complete message to wire format.

    header_session_id: SessionId for the header (None for pre-session messages).
    **fields: Message body fields.

    Returns: [4-byte length][header][body]
    """
    body = serialize_body(msg_type, fields)
    return pack_frame(msg_type, flags, sequence_id, header_session_id, body)
