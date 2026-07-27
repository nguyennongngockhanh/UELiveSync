"""
MsgType Transport Layer — framing, sequence, session, TCP send.

This module is protocol-agnostic. It knows nothing about Material,
Mesh, Camera, or Object payloads. It only handles:

  - Header serialization (pre-session / post-session)
  - Sequence number ownership
  - Session ID management
  - Frame assembly: [4-byte LE length][header][body]
  - TCP send via existing LiveSyncClient._send_queue

Feature modules call:

    transport.send_msg(MsgType.MATERIAL_CREATE, payload_bytes)

and receive correctly framed packets on the wire.

Wire format (all multi-byte fields LE):

    [4-byte LE length] [header] [body]

    length = sizeof(header) + sizeof(body)

    Pre-session header (HELLO, HELLO_ACK, REJECT):
        uint8   msg_type
        uint8   flags
        uint32  sequence_id      LE
        Total: 6 bytes

    Post-session header (all other MsgTypes):
        uint8   msg_type
        uint8   flags
        uint32  sequence_id      LE
        uint64  session_id       LE
        Total: 14 bytes

Detection by receiver:
    first_u32 < 0x4C56534D  → MsgType packet
    first_u32 == 0x4C56534D → Legacy packet
"""

import struct
import threading
import time
from enum import IntEnum


# ─── MsgType Opcodes ─────────────────────────────────────────

class MsgType(IntEnum):
    HEARTBEAT       = 0x00
    HEARTBEAT_ACK   = 0x01
    SCENE_HASH      = 0x02
    SCENE_FULL      = 0x03
    SCENE_DELTA     = 0x04

    OBJECT_CREATE   = 0x20
    OBJECT_UPDATE   = 0x21
    OBJECT_DELETE   = 0x22
    OBJECT_RENAME   = 0x23
    OBJECT_REPARENT = 0x24
    OBJECT_VISIBILITY = 0x25

    MESH_DATA       = 0x30
    MESH_DELTA      = 0x31
    MESH_START      = 0x32
    MESH_CHUNK      = 0x33
    MESH_END        = 0x34

    MATERIAL_CREATE = 0x40
    MATERIAL_UPDATE = 0x41
    MATERIAL_ASSIGN = 0x42

    CAMERA_CREATE   = 0x50
    CAMERA_UPDATE   = 0x51
    CAMERASETACTIVE = 0x52

    HELLO           = 0x10
    HELLO_ACK       = 0x11
    REJECT          = 0x12
    SYNC_ACK        = 0xF0
    ERROR           = 0xFE
    DISCONNECT      = 0xFF


# ─── Pre-session opcodes ─────────────────────────────────────

_PRE_SESSION_OPS = frozenset({
    MsgType.HELLO,
    MsgType.HELLO_ACK,
    MsgType.REJECT,
})


def is_pre_session(msg_type: int) -> bool:
    return msg_type in _PRE_SESSION_OPS


# ─── Header sizes ────────────────────────────────────────────

HEADER_BEFORE_SESSION_SIZE = 6
HEADER_AFTER_SESSION_SIZE = 14
LENGTH_PREFIX_SIZE = 4


# ─── Primitive packers ───────────────────────────────────────

def pack_u8(v: int) -> bytes:
    return struct.pack('<B', v & 0xFF)


def pack_u16(v: int) -> bytes:
    return struct.pack('<H', v & 0xFFFF)


def pack_u32(v: int) -> bytes:
    return struct.pack('<I', v & 0xFFFFFFFF)


def pack_u64(v: int) -> bytes:
    return struct.pack('<Q', v & 0xFFFFFFFFFFFFFFFF)


def pack_f32(v: float) -> bytes:
    return struct.pack('<f', v)


def pack_uuid(raw_16: bytes) -> bytes:
    assert len(raw_16) == 16
    return raw_16


def pack_utf8(s: str) -> bytes:
    encoded = s.encode('utf-8')
    if len(encoded) > 0xFFFF:
        raise ValueError(f"String too long: {len(encoded)} bytes")
    return pack_u16(len(encoded)) + encoded


def pack_transform3d(
    px, py, pz,
    rx, ry, rz, rw,
    sx, sy, sz
) -> bytes:
    return struct.pack('<10f', px, py, pz, rx, ry, rz, rw, sx, sy, sz)


# ─── Header serialization ────────────────────────────────────

def serialize_header_before_session(
    msg_type: int,
    flags: int = 0,
    sequence_id: int = 0,
) -> bytes:
    """6-byte header for HELLO, HELLO_ACK, REJECT."""
    return struct.pack('<BBI',
        msg_type & 0xFF,
        flags & 0xFF,
        sequence_id & 0xFFFFFFFF,
    )


def serialize_header_after_session(
    msg_type: int,
    flags: int = 0,
    sequence_id: int = 0,
    session_id: int = 0,
) -> bytes:
    """14-byte header for all post-session messages."""
    return struct.pack('<BBIQ',
        msg_type & 0xFF,
        flags & 0xFF,
        sequence_id & 0xFFFFFFFF,
        session_id & 0xFFFFFFFFFFFFFFFF,
    )


# ─── Common body builders ────────────────────────────────────

PROTOCOL_VERSION_MAJOR = 2
PROTOCOL_VERSION_MINOR = 0

# Blender addon capability flags
CAPABILITY_MATERIALS   = 0x0001
CAPABILITY_MESH        = 0x0002
CAPABILITY_CAMERA      = 0x0004
CAPABILITY_FULL_SYNC   = 0x0008
CAPABILITY_DELTA_SYNC  = 0x0010


def build_body_hello(
    major: int = PROTOCOL_VERSION_MAJOR,
    minor: int = PROTOCOL_VERSION_MINOR,
    capabilities: int = (
        CAPABILITY_MATERIALS | CAPABILITY_MESH |
        CAPABILITY_CAMERA | CAPABILITY_FULL_SYNC
    ),
) -> bytes:
    """HELLO body: uint8 major, uint8 minor, uint64 capabilities."""
    return pack_u8(major) + pack_u8(minor) + pack_u64(capabilities)


def parse_body_hello_ack(body: bytes) -> dict:
    """Parse HELLO_ACK body → dict with session_id and negotiated params."""
    if len(body) < 20:
        raise ValueError(f"HELLO_ACK body too short: {len(body)} bytes")
    return {
        "protocol_major": body[0],
        "protocol_minor": body[1],
        "accepted_capabilities": struct.unpack_from('<Q', body, 2)[0],
        "max_chunk_size": struct.unpack_from('<I', body, 10)[0],
        "session_id": struct.unpack_from('<Q', body, 14)[0],
    }


# ─── Frame assembly ──────────────────────────────────────────

def serialize_packet(
    msg_type: int,
    body: bytes = b'',
    *,
    flags: int = 0,
    session_id: int | None = None,
    sequence_id: int = 0,
) -> bytes:
    """
    Build a complete wire frame: [4-byte LE length][header][body].

    If session_id is None → pre-session header (6 bytes).
    If session_id is int  → post-session header (14 bytes).
    """
    if is_pre_session(msg_type):
        if session_id is not None:
            raise ValueError(
                f"Pre-session msg 0x{msg_type:02X} must not have session_id"
            )
        header = serialize_header_before_session(msg_type, flags, sequence_id)
    else:
        if session_id is None:
            raise ValueError(
                f"Post-session msg 0x{msg_type:02X} requires session_id"
            )
        header = serialize_header_after_session(
            msg_type, flags, sequence_id, session_id
        )

    length_prefix = len(header) + len(body)
    return pack_u32(length_prefix) + header + body


# ─── MsgTransport ────────────────────────────────────────────

class MsgTransport:
    """
    MsgType transport layer.

    Owns:
      - sequence counter (thread-safe)
      - session_id (set after HELLO_ACK)
      - reference to LiveSyncClient for TCP send

    Does NOT know about any feature payload.
    """

    def __init__(self, client):
        """
        Args:
            client: LiveSyncClient instance (provides _send_queue)
        """
        self._client = client
        self._sequence = 0
        self._lock = threading.Lock()
        self._session_id: int | None = None

    @property
    def session_id(self) -> int | None:
        return self._session_id

    @session_id.setter
    def session_id(self, value: int | None):
        self._session_id = value

    def next_sequence(self) -> int:
        """Atomically increment and return next sequence number."""
        with self._lock:
            self._sequence += 1
            return self._sequence

    def send_msg(
        self,
        msg_type: int,
        body: bytes = b'',
        *,
        flags: int = 0,
    ) -> bool:
        """
        Build a wire frame and enqueue for TCP send.

        Args:
            msg_type: MsgType opcode
            body: serialized payload bytes (feature-specific)
            flags: packet flags (default 0)

        Returns:
            True if enqueued, False on error.
        """
        seq = self.next_sequence()

        # Use 0 as fallback session_id when legacy handshake hasn't set one
        effective_session_id = self._session_id if self._session_id is not None else 0

        try:
            frame = serialize_packet(
                msg_type,
                body,
                flags=flags,
                session_id=effective_session_id,
                sequence_id=seq,
            )
        except Exception as e:
            print(
                f"[LiveSync] MsgTransport: frame build failed: {e}"
            )
            return False

        try:
            self._client._send_queue.put_nowait(frame)
        except Exception as e:
            print(
                f"[LiveSync] MsgTransport: enqueue failed: {e}"
            )
            return False

        return True

    def send_hello(
        self,
        major: int = PROTOCOL_VERSION_MAJOR,
        minor: int = PROTOCOL_VERSION_MINOR,
        capabilities: int = (
            CAPABILITY_MATERIALS | CAPABILITY_MESH |
            CAPABILITY_CAMERA | CAPABILITY_FULL_SYNC
        ),
    ) -> bool:
        """Send HELLO (pre-session). Returns True if enqueued."""
        body = build_body_hello(major, minor, capabilities)
        return self.send_msg(MsgType.HELLO, body)


# ─── Module-level instance ───────────────────────────────────
# Initialized by init_transport() after LiveSyncClient is created.

_transport: MsgTransport | None = None


def init_transport(client) -> MsgTransport:
    """Create the global MsgTransport from a LiveSyncClient."""
    global _transport
    _transport = MsgTransport(client)
    return _transport


def get_transport() -> MsgTransport | None:
    """Get the global MsgTransport instance."""
    return _transport
