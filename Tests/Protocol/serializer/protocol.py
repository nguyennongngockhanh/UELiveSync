"""
Protocol constants loaded from YAML files.

YAML files are the normative source of truth.
This module provides Python access to all protocol definitions.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any

from common import load_yaml


# ─── Path Resolution ────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PROTOCOL_DIR = _PROJECT_ROOT / "Shared" / "Protocol"


# ─── Load All YAML Files ────────────────────────────────────────

MSG_TYPES_RAW = load_yaml("MessageTypes.yaml")
TYPES_RAW = load_yaml("Types.yaml")
CAPABILITIES_RAW = load_yaml("Capabilities.yaml")
ERRORS_RAW = load_yaml("Errors.yaml")


# ─── Message Opcodes ────────────────────────────────────────────

class MsgType(IntEnum):
    """All message opcodes. Values from MessageTypes.yaml."""
    HELLO = 0x10
    HELLO_ACK = 0x11
    REJECT = 0x12
    HEARTBEAT = 0x00
    HEARTBEAT_ACK = 0x01
    SCENE_HASH = 0x02
    SCENE_FULL = 0x03
    SCENE_DELTA = 0x04
    OBJECT_CREATE = 0x20
    OBJECT_UPDATE = 0x21
    OBJECT_DELETE = 0x22
    OBJECT_RENAME = 0x23
    OBJECT_REPARENT = 0x24
    OBJECT_VISIBILITY = 0x25
    MESH_DATA = 0x30
    MESH_DELTA = 0x31
    MESH_START = 0x32
    MESH_CHUNK = 0x33
    MESH_END = 0x34
    MATERIAL_CREATE = 0x40
    MATERIAL_UPDATE = 0x41
    MATERIAL_ASSIGN = 0x42
    CAMERA_CREATE = 0x50
    CAMERA_UPDATE = 0x51
    CAMERASETACTIVE = 0x52
    FBX_IMPORT_REQUEST = 0x60
    SYNC_ACK = 0xF0
    ERROR = 0xFE
    DISCONNECT = 0xFF


# ─── Header Invariants ──────────────────────────────────────────

PRE_SESSION_OPCodes = frozenset({MsgType.HELLO, MsgType.HELLO_ACK, MsgType.REJECT})

HEADER_BEFORE_SESSION_SIZE = 6   # MsgType(1) + Flags(1) + SequenceId(4)
HEADER_AFTER_SESSION_SIZE = 14   # + SessionId(8)
LENGTH_PREFIX_SIZE = 4


# ─── Primitive Sizes ────────────────────────────────────────────

PRIMITIVE_SIZES = {
    "uint8": 1,
    "uint16": 2,
    "uint32": 4,
    "uint64": 8,
    "float32": 4,
}


# ─── Message Definitions ────────────────────────────────────────

@dataclass
class FieldDef:
    name: str
    type: str
    optional: bool = False
    endian: str = "little"
    count: str | int | None = None


@dataclass
class MessageDef:
    code: int
    name: str
    direction: str
    header: str  # "before_session" or "after_session" (implicit)
    session_required: bool
    body_fields: list[FieldDef] = field(default_factory=list)


def _parse_messages() -> dict[MsgType, MessageDef]:
    """Parse message definitions from YAML."""
    messages = MSG_TYPES_RAW["messages"]
    result = {}

    for name, msg in messages.items():
        code = msg["code"]
        direction = msg.get("direction", "both")
        session_required = msg.get("session_required", True)
        header = msg.get("header", "after_session")

        body_fields = []
        body = msg.get("body")
        if body and isinstance(body, list):
            for field_def in body:
                body_fields.append(FieldDef(
                    name=field_def["name"],
                    type=field_def["type"],
                    optional=field_def.get("optional", False),
                    endian=field_def.get("endian", "little"),
                    count=field_def.get("count", None),
                ))

        msg_type = MsgType(code)
        result[msg_type] = MessageDef(
            code=code,
            name=name,
            direction=direction,
            header=header,
            session_required=session_required,
            body_fields=body_fields,
        )

    return result


MESSAGES: dict[MsgType, MessageDef] = _parse_messages()


# ─── Composite Types ────────────────────────────────────────────

UUID_SIZE = 16
TRANSFORM3D_SIZE = 40  # 10 × float32, packed, no alignment


# ─── Canonical Float Rules (from MessageTypes.yaml) ─────────────

def canonicalize_float(v: float) -> float:
    """Apply canonical float rules: -0.0 → +0.0, reject NaN."""
    import math
    if math.isnan(v):
        raise ValueError("NaN value rejected by canonical float rules")
    if v == 0.0:
        return 0.0  # Converts -0.0 to +0.0
    return v


def canonicalize_quaternion(x: float, y: float, z: float, w: float) -> tuple[float, float, float, float]:
    """Normalize quaternion. If degenerate, return identity (0,0,0,1)."""
    import math
    mag = math.sqrt(x*x + y*y + z*z + w*w)
    if mag < 1e-7:
        return (0.0, 0.0, 0.0, 1.0)
    return (x/mag, y/mag, z/mag, w/mag)
