import os
import socket
import struct
import sys
import threading
import queue
import time

from .msg_transport import init_transport, get_transport


# Phase 10J.5L: Blender-side debug log for material extraction diagnostics.
# Written to ~/.cache/uelivesync/uelivesync_blender_debug.log
# Append-only; safe to read while addon is running.
BLENDER_DEBUG_LOG_PATH = os.path.join(
    os.path.expanduser("~/.cache/uelivesync"),
    "uelivesync_blender_debug.log",
)


def _append_blender_debug_log(msg):
    """Append a line to the Blender debug log file."""
    try:
        os.makedirs(os.path.dirname(BLENDER_DEBUG_LOG_PATH), exist_ok=True)
        with open(BLENDER_DEBUG_LOG_PATH, "a") as f:
            f.write(msg + "\n")
    except Exception:
        pass


# =========================================================
# PHASE 3.3 PROTOCOL CONSTANTS
# =========================================================

LIVE_SYNC_MAGIC = 0x4C56534D
LIVE_SYNC_VERSION = 2
LIVE_SYNC_VERSION_V3 = 3

# =========================================================
# PROTOCOL SIGNATURE (FNV-1a 32-bit)
# Must match UE LIVE_SYNC_PROTOCOL_SIG in SyncTypes.h.
# Logged at startup; mismatch = binary protocol drift.
# =========================================================

def _compute_protocol_signature():
    FNV_OFFSET = 2166136261
    FNV_PRIME = 16777619

    def _fnv(h, b):
        return ((h ^ b) * FNV_PRIME) & 0xFFFFFFFF

    h = FNV_OFFSET
    h = _fnv(h, LIVE_SYNC_MAGIC & 0xFF)
    h = _fnv(h, (LIVE_SYNC_MAGIC >> 8) & 0xFF)
    h = _fnv(h, (LIVE_SYNC_MAGIC >> 16) & 0xFF)
    h = _fnv(h, (LIVE_SYNC_MAGIC >> 24) & 0xFF)
    for v in (2, 3, 4, 5):
        h = _fnv(h, v & 0xFF)
        h = _fnv(h, (v >> 8) & 0xFF)
    import struct as _s
    for size in (24, 22, 80, 81, 16, 33, 28, 28, 4, 4, 16, 32, 33, 40, 14, 25, 680):
        h = _fnv(h, size)
    for pt in (0x01, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18):
        h = _fnv(h, pt)
    return h

LIVE_SYNC_PROTOCOL_SIG = _compute_protocol_signature()

# Phase 7C: Playback state enum
PLAYBACK_PLAY  = 0
PLAYBACK_PAUSE = 1
PLAYBACK_STOP  = 2

# Verbose logging flag (set by sync.py from addon prefs)
_network_verbose = False

def set_verbose(enabled):
    global _network_verbose
    _network_verbose = enabled


# Phase 7C: playback preference toggle
_playback_enabled = False

def set_playback_enabled(enabled):
    global _playback_enabled
    _playback_enabled = enabled


# Phase 7C: playback sync state globals
_playback_sequence = 0
_last_playback_state = None
playback_packets_sent = 0
playback_state_changes = 0


def is_playback_effective():
    """Playback sync is effective iff the user preference is enabled.
    (Capability negotiation is not yet wired — this simplifies to pref check.)
    """
    return _playback_enabled


# Phase 7B: timeline preference toggle
_timeline_enabled = False

def set_timeline_enabled(enabled):
    global _timeline_enabled, _last_timeline_sent, _timeline_sequence
    _timeline_enabled = enabled
    _last_timeline_sent = None
    _timeline_sequence = 0


def is_timeline_effective():
    global _client
    if not _timeline_enabled:
        return False
    if _client is None:
        return False
    if not getattr(_client, 'connected', False):
        return False
    if not getattr(_client, '_capability_response_received', False):
        return False
    remote = getattr(_client, '_remote_capabilities', 0)
    return bool(remote & CAP_SUPPORTS_TIMELINE_SYNC)


# Phase 7B: timeline sync state globals
_timeline_sequence = 0
_last_timeline_sent = None  # tuple (frame_current, frame_start, frame_end, fps_num, fps_den) or None
timeline_packets_sent = 0
timeline_state_changes = 0


# Phase 7D: active camera preference toggle
_active_camera_enabled = False

def set_active_camera_enabled(enabled):
    global _active_camera_enabled
    _active_camera_enabled = enabled


def _send_announce(client_obj=None):
    """Send a PT_CapabilityAnnounce packet with _local_capabilities.

    Designed to be called after connect/reconnect. Returns True if the
    packet was enqueued successfully, False if no client is connected.
    Accepts an optional client_obj parameter; falls back to global _client.
    """
    global _client
    c = client_obj if client_obj is not None else _client
    if c is None or not c.connected:
        if client_obj is not None:
            print("[LiveSync][CAP] _send_announce: client_obj NOT connected")
        return False
    raw = struct.pack('<I', _local_capabilities)
    # Pad to 16 bytes so ObjectCount=1 passes UE's V3+ payload
    # validation (PayloadSize >= ObjectCount * LIVE_SYNC_V3_DELETE_SIZE).
    payload = raw + b'\x00' * (16 - len(raw))
    c.send_packet(
        [payload],
        packet_type=PT_CapabilityAnnounce,
    )
    print("[LiveSync][CAP] Sent PT_CapabilityAnnounce mask=0x%08X" % _local_capabilities, flush=True)
    return True


def _try_recv_capability_response():
    """Non-blocking attempt to read a PT_CapabilityResponse from the socket.

    Sets _remote_capabilities and _capability_response_received on success.
    Intended to be called from _idle_probe() after announce is sent.
    Does nothing if response already received or no client is connected.
    """
    global _client, _remote_capabilities, _capability_response_received
    if _client is None or not _client.connected or _capability_response_received:
        return
    sock = _client.sock
    if sock is None:
        return
    try:
        sock.setblocking(False)
        header = sock.recv(24, socket.MSG_PEEK)
        if len(header) < 24:
            sock.setblocking(True)
            return
        magic, ver, pt, flags, seq_id, pkt_size, obj_count = \
            struct.unpack('<I H B B Q I I', header)
        if magic != LIVE_SYNC_MAGIC or pt != PT_CapabilityResponse:
            sock.setblocking(True)
            return
        data = sock.recv(pkt_size)
        sock.setblocking(True)
        if len(data) >= 28:
            payload_data = data[24:]
            if len(payload_data) >= 4:
                _remote_capabilities = \
                    struct.unpack('<I', payload_data[:4])[0]
                _capability_response_received = True
                _client._remote_capabilities = _remote_capabilities
                _client._capability_response_received = True
        if _capability_response_received:
            print("[LiveSync][CAP] Received PT_CapabilityResponse mask=0x%08X" % _remote_capabilities, flush=True)
            _append_blender_debug_log("[DIAG][CAP] CapabilityResponse received mask=0x%08X" % _remote_capabilities)
    except (BlockingIOError, ConnectionResetError, BrokenPipeError):
        try:
            sock.setblocking(True)
        except Exception:
            pass
    except Exception:
        try:
            sock.setblocking(True)
        except Exception:
            pass


def is_active_camera_effective():
    """Active camera sync is effective iff:
      1. User preference is enabled
      2. _client exists and is connected
      3. Capability response has been received from UE
      4. Remote capabilities include the active camera bit
    """
    global _client
    if not _active_camera_enabled:
        return False
    if _client is None:
        return False
    if not getattr(_client, 'connected', False):
        return False
    if not getattr(_client, '_capability_response_received', False):
        return False
    remote = getattr(_client, '_remote_capabilities', 0)
    return bool(remote & CAP_SUPPORTS_ACTIVE_CAMERA_SYNC)


# Phase 7E: sequencer ops preference toggle
_sequencer_op_enabled = False

def set_sequencer_op_enabled(enabled):
    global _sequencer_op_enabled, _last_sequencer_op_state, _sequencer_op_sequence, _sequencer_op_packets_sent, _sequencer_op_state_changes
    _sequencer_op_enabled = enabled
    _last_sequencer_op_state = None
    _sequencer_op_sequence = 0
    _sequencer_op_packets_sent = 0
    _sequencer_op_state_changes = 0


def is_sequencer_ops_effective():
    global _client
    if not _sequencer_op_enabled:
        return False
    if _client is None:
        return False
    if not getattr(_client, 'connected', False):
        return False
    if not getattr(_client, '_capability_response_received', False):
        return False
    remote = getattr(_client, '_remote_capabilities', 0)
    return bool(remote & CAP_SUPPORTS_SEQUENCER_OPS)


# Phase 7E: sequencer ops state globals
_sequencer_op_sequence = 0
_last_sequencer_op_state = None
_sequencer_op_packets_sent = 0
_sequencer_op_state_changes = 0


# Phase 7E Stage 7-8: keyframe sync preference toggle
_keyframe_enabled = False

def set_keyframe_enabled(enabled):
    global _keyframe_enabled, _keyframe_sequence, _keyframe_packets_sent, _keyframes_sent
    _keyframe_enabled = enabled
    _keyframe_sequence = 0
    _keyframe_packets_sent = 0
    _keyframes_sent = 0


# Phase 10A.4: flag to avoid repeating capability-wait diagnostics.
_keyframe_cap_ready_logged = False


def is_keyframe_effective():
    global _client, _keyframe_cap_ready_logged
    if not _keyframe_enabled:
        return False
    if _client is None:
        return False
    if not getattr(_client, 'connected', False):
        return False

    # Phase 10A.4: brief poll for capability response before giving up.
    # This avoids silent keyframe suppression during the brief window
    # between connection and capability handshake completion (~100-500ms).
    if not getattr(_client, '_capability_response_received', False):
        cap_polled = False
        for _ in range(40):  # up to ~2 seconds (40 × 50ms)
            import time
            time.sleep(0.05)
            if getattr(_client, '_capability_response_received', False):
                cap_polled = True
                break
        if not cap_polled:
            # UE may not send CapabilityResponse; proceed anyway if connected.
            print("[KEYFRAME][NO_RESPONSE] capability_response not received "
                  f"connected={getattr(_client, 'connected', False)} — "
                  "proceeding without remote capability confirmation")
        else:
            print("[KEYFRAME][CAPABILITY_WAIT] capability_response_received after poll")

    remote = getattr(_client, '_remote_capabilities', 0)
    has_cap = bool(remote & CAP_SUPPORTS_KEYFRAME_REPLICATION)
    # If we're connected but never got the capability response, UE may
    # not implement CapabilityResponse — assume keyframes are supported.
    if not _capability_response_received and _client and _client.connected:
        has_cap = True
    if not _keyframe_cap_ready_logged:
        _keyframe_cap_ready_logged = True
        print(f"[KEYFRAME][CAPABILITY_READY] remote_capabilities={remote} "
              f"supports_keyframes={has_cap}")
    return has_cap


def is_camera_fov_keyframe_effective():
    """True if remote peer advertised CAP_SUPPORTS_CAMERA_FOV_KEYFRAME
    and general keyframe sync is effective without debug logging.

    Does not access private capability state directly from callers.
    """
    global _client
    if _client is None:
        return False
    if not getattr(_client, 'connected', False):
        return False
    if not getattr(_client, '_capability_response_received', False):
        return False
    remote = getattr(_client, '_remote_capabilities', 0)
    has_cap = bool(remote & CAP_SUPPORTS_CAMERA_FOV_KEYFRAME)
    return has_cap


# Phase 7E Stage 7-8: keyframe sync state globals
_keyframe_sequence = 0
_keyframe_packets_sent = 0
_keyframes_sent = 0
_animated_objects_scanned = 0


# Primitive type constants (1 byte, appended to CREATE packets only)
PRIMITIVE_CUBE     = 0x00
PRIMITIVE_SPHERE   = 0x01
PRIMITIVE_CYLINDER = 0x02
PRIMITIVE_PLANE    = 0x03
PRIMITIVE_EMPTY    = 0x04
PRIMITIVE_CAMERA   = 0x05

# Packet type constants (beyond V3 base)
PT_BeginSnapshot = 0x09
PT_EndSnapshot = 0x0A
PT_AssetDef = 0x08
PT_Hierarchy = 0x0D
PT_Delete_V5 = 0x0E  # Phase 6E: lifecycle/delete (V5+, 28-byte fixed payload)
PT_Collection = 0x0F  # Phase 6F: collection/group replication (metadata-only)
PT_Material = 0x05   # Phase 7B: material slot identity
PT_Mesh = 0x06       # Phase 7C: mesh geometry chunk
PT_Timeline = 0x13   # Phase 7B: timeline/playhead frame sync
PT_PlaybackState = 0x14  # Phase 7C: playback state (play/pause/stop/loop)
PT_ActiveCamera = 0x15  # Phase 7D: active camera selection (GUID-only, no params)

# Phase 7E Stage 7: Keyframe replication (PT_Keyframe = 0x17)
PT_Keyframe = 0x17  # Keyframe replication (fixed header + repeated entries)

# Phase 7E: Sequencer ops (PT_SequencerOp = 0x18)
# Wire format: fixed-size 16-byte common header + optional opcode payload.
PT_SequencerOp = 0x18  # Sequencer operation (discrete event, NOT state stream)

# Phase 7F Stage 1: Timeline state (PT_TimelineState = 0x19)
# Applies frame range + FPS to LiveSync LevelSequence playback range.
PT_TimelineState = 0x19  # Timeline state (applied to Sequencer, unlike PT_Timeline)

# Phase 7F Stage 2: Playback transport (PT_PlaybackTransport = 0x1A)
# Sends play/pause/stop/scrub commands to UE Sequencer.
PT_PlaybackTransport = 0x1A  # Playback transport (command + current frame)

# Phase 7G Stage 3: Camera definition / parameter sync (PT_CameraDef = 0x1B)
PT_CameraDef = 0x1B  # Camera parameters (focal, sensor, clip, ortho, flags)

# Camera definition flags (bitfield)
CAMERA_DEF_FLAG_IS_ORTHO       = 0x01  # Orthographic projection
CAMERA_DEF_FLAG_HAS_CAMERA_DEF = 0x02  # Has camera definition data

# Playback transport commands (must match SyncTypes.h EPlaybackTransportCommand)
PLAYBACK_TRANSPORT_SET_FRAME = 0  # SetFrame/Scrub
PLAYBACK_TRANSPORT_PLAY     = 1  # Play
PLAYBACK_TRANSPORT_PAUSE    = 2  # Pause
PLAYBACK_TRANSPORT_STOP     = 3  # Stop

# Sequencer opcodes (must match SyncTypes.h ESequencerOpcode)
SEQUENCER_OP_CREATE_SEQUENCE   = 0  # Create/replace sequence with frame range + FPS
SEQUENCER_OP_ADD_POSSESSABLE   = 1  # Add possessable binding to sequence
SEQUENCER_OP_REMOVE_POSSESSABLE = 2  # Remove possessable binding from sequence
SEQUENCER_OP_ADD_CAMERA_CUT    = 3  # Add camera cut to sequence
SEQUENCER_OP_CLEAR_SEQUENCE    = 4  # Clear all tracks/possessables from sequence
SEQUENCER_OP_SET_FRAME_RANGE   = 5  # Update sequence frame range + FPS

# Phase 7C Stage 3A.1: FBX Import Request payload: 688 bytes fixed
FBX_IMPORT_REQUEST_PAYLOAD_SIZE = 688

def compute_fbx_geometry_hash(mesh):
    """Compute a stable 64-bit geometry hash for FBX duplicate detection.

    Uses evaluated mesh vertex coordinates, loop triangle topology,
    and material slot count. Deterministic for identical geometry;
    changes when vertex positions, topology, or material slot count change.

    Guarantees non-zero return for any non-empty mesh.
    Falls back to a deterministic non-zero sentinel derived from
    mesh content if primary xxh64 returns 0 (astronomically rare).

    Args:
        mesh: bpy.types.Mesh (evaluated) with loop triangles available.

    Returns:
        int: unsigned 64-bit xxh64 hash. 0 if computation fails.
    """
    try:
        if not mesh.loop_triangles and hasattr(mesh, 'calc_loop_triangles'):
            mesh.calc_loop_triangles()
        data = b''
        for v in mesh.vertices:
            data += struct.pack('<fff', v.co.x, v.co.y, v.co.z)
        for t in mesh.loop_triangles:
            data += struct.pack('<III', t.vertices[0], t.vertices[1], t.vertices[2])
        data += struct.pack('<I', len(mesh.materials))
        h = xxh64(data)
        if h == 0:
            h = xxh64(data, seed=1)
        return h
    except Exception:
        return 0


# Phase 9: Capability negotiation (announce/response)
PT_CapabilityAnnounce  = 0x11  # Phase 9: capability bitmask from Blender to UE
PT_CapabilityResponse  = 0x12  # Phase 9: capability bitmask from UE to Blender

# Capability payload sizes (uint32 each)
CAPABILITY_ANNOUNCE_PAYLOAD_SIZE  = 4
CAPABILITY_RESPONSE_PAYLOAD_SIZE  = 4

# =========================================================
# CAPABILITY BITS (Phase 9, wired in capability announce/response)
# =========================================================

CAP_SUPPORTS_TIMELINE_SYNC       = 0x10  # Bit 4: PT_Timeline (0x13) supported
CAP_SUPPORTS_KEYFRAME_REPLICATION = 0x20  # Bit 5: PT_Keyframe (0x17) supported
CAP_SUPPORTS_ACTIVE_CAMERA_SYNC  = 0x40  # Bit 6: PT_ActiveCamera (0x15) supported
CAP_SUPPORTS_SEQUENCER_OPS       = 0x80  # Bit 7: PT_SequencerOp (0x18) supported
CAP_SUPPORTS_CAMERA_DEF_SYNC     = 0x100 # Bit 8: PT_CameraDef (0x1B) supported
CAP_SUPPORTS_CAMERA_FOV_KEYFRAME = 0x400 # Bit 10: Camera FOV keyframe (channel 11)

# Local capabilities bitmask — sent to UE during capability announce.
_local_capabilities = CAP_SUPPORTS_TIMELINE_SYNC | CAP_SUPPORTS_KEYFRAME_REPLICATION | CAP_SUPPORTS_ACTIVE_CAMERA_SYNC | CAP_SUPPORTS_SEQUENCER_OPS | CAP_SUPPORTS_CAMERA_DEF_SYNC | CAP_SUPPORTS_CAMERA_FOV_KEYFRAME

# Remote capabilities received from UE via PT_CapabilityResponse.
_remote_capabilities = 0
_capability_response_received = False


# Collection packet versioning (Phase 6F Stage 5)
COLLECTION_PACKET_VERSION_V1 = 0x01

# Collection operation type constants (must match SyncTypes.h)
COLLECTION_OP_ADD = 0x01                 # Add actor to collection
COLLECTION_OP_REMOVE = 0x02              # Remove actor from collection
COLLECTION_OP_MOVE = 0x03                # Move actor between collections
COLLECTION_OP_CLEAR = 0x04               # Clear all actors from collection
COLLECTION_OP_COLLECTION_CREATE = 0x06   # Create a new collection
COLLECTION_OP_COLLECTION_DELETE = 0x07   # Delete a collection
COLLECTION_OP_COLLECTION_REPARENT = 0x08 # Reparent a collection

# Collection packet payload sub-header size (version byte + reserved byte)
LIVE_SYNC_COLLECTION_SUBHEADER_SIZE = 2

# Collection packet header flag: set bit 0 to indicate Stage 5+ sub-header present
COLLECTION_PACKET_FLAG_HAS_SUBHEADER = 0x01

# V4 protocol version
LIVE_SYNC_VERSION_V4 = 4

# V5 protocol version
LIVE_SYNC_VERSION_V5 = 5


# =========================================================
# XXHASH64 (pure Python, deterministic, fast)
# =========================================================

_XXH_PRIME64_1 = 0x9E3779B185EBCA87
_XXH_PRIME64_2 = 0xC2B2AE3D27D4EB4F
_XXH_PRIME64_3 = 0x165667B19E3779F9
_XXH_PRIME64_4 = 0x85EBCA77C2B2AE63
_XXH_PRIME64_5 = 0x27D4EB2F165667C5


def _xxh64_round(acc, seed):
    acc += seed * _XXH_PRIME64_2
    acc = ((acc << 31) | (acc >> 33))
    acc *= _XXH_PRIME64_1
    return acc & 0xFFFFFFFFFFFFFFFF


def _xxh64_merge_round(acc, val):
    acc = ((acc ^ _xxh64_round(0, val)) * _XXH_PRIME64_1) + _XXH_PRIME64_4
    return acc & 0xFFFFFFFFFFFFFFFF


def xxh64(data, seed=0):
    length = len(data)
    remaining_length = length
    acc = seed + _XXH_PRIME64_5 + _XXH_PRIME64_5

    if length >= 32:
        v1 = seed + _XXH_PRIME64_1 + _XXH_PRIME64_2
        v2 = seed + _XXH_PRIME64_2
        v3 = seed
        v4 = seed - _XXH_PRIME64_1

        limit = length - 32
        offset = 0

        while offset <= limit:
            v1 = _xxh64_round(v1, struct.unpack_from("<Q", data, offset)[0])
            v2 = _xxh64_round(v2, struct.unpack_from("<Q", data, offset + 8)[0])
            v3 = _xxh64_round(v3, struct.unpack_from("<Q", data, offset + 16)[0])
            v4 = _xxh64_round(v4, struct.unpack_from("<Q", data, offset + 24)[0])
            offset += 32

        acc = ((v1 << 1) | (v1 >> 63))
        acc = _xxh64_merge_round(acc, v2)
        acc = _xxh64_merge_round(acc, v3)
        acc = _xxh64_merge_round(acc, v4)

        remaining_length = length - offset
    else:
        acc += _XXH_PRIME64_5

    offset = length - remaining_length
    while remaining_length >= 8:
        val = struct.unpack_from("<Q", data, offset)[0]
        acc = ((acc ^ _xxh64_round(0, val)) * _XXH_PRIME64_1) + _XXH_PRIME64_4
        acc &= 0xFFFFFFFFFFFFFFFF
        offset += 8
        remaining_length -= 8

    while remaining_length >= 4:
        val = struct.unpack_from("<I", data, offset)[0]
        acc = ((acc ^ (val * _XXH_PRIME64_1)) * _XXH_PRIME64_3) + _XXH_PRIME64_5
        acc &= 0xFFFFFFFFFFFFFFFF
        offset += 4
        remaining_length -= 4

    while remaining_length > 0:
        val = data[offset]
        acc = ((acc ^ (val * _XXH_PRIME64_5)) * _XXH_PRIME64_3) + _XXH_PRIME64_5
        acc &= 0xFFFFFFFFFFFFFFFF
        offset += 1
        remaining_length -= 1

    # Final avalanche
    acc ^= acc >> 37
    acc = (acc * _XXH_PRIME64_3) + _XXH_PRIME64_5
    acc ^= acc >> 37
    acc = (acc * _XXH_PRIME64_4) + _XXH_PRIME64_5
    acc ^= acc >> 37

    return acc & 0xFFFFFFFFFFFFFFFF


class _Xxh64Stream:
    """Streaming (incremental) xxh64 that matches the one-shot xxh64().

    Usage::

        hasher = _Xxh64Stream(seed=0)
        hasher.update(chunk1)
        hasher.update(chunk2)
        result = hasher.digest()      # int
        hex_result = hasher.hexdigest()  # 16-char lowercase str
    """

    __slots__ = ('_seed', '_total', '_buf', '_v1', '_v2', '_v3', '_v4')

    def __init__(self, seed=0):
        self._seed = seed
        self._total = 0
        self._buf = bytearray()
        self._v1 = None
        self._v2 = None
        self._v3 = None
        self._v4 = None

    def _process_stripe(self, buffer, offset):
        if self._v1 is None:
            self._v1 = self._seed + _XXH_PRIME64_1 + _XXH_PRIME64_2
            self._v2 = self._seed + _XXH_PRIME64_2
            self._v3 = self._seed
            self._v4 = self._seed - _XXH_PRIME64_1
        self._v1 = _xxh64_round(self._v1, struct.unpack_from("<Q", buffer, offset)[0])
        self._v2 = _xxh64_round(self._v2, struct.unpack_from("<Q", buffer, offset + 8)[0])
        self._v3 = _xxh64_round(self._v3, struct.unpack_from("<Q", buffer, offset + 16)[0])
        self._v4 = _xxh64_round(self._v4, struct.unpack_from("<Q", buffer, offset + 24)[0])

    def update(self, data):
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("expected bytes, bytearray, or memoryview")
        if isinstance(data, memoryview):
            view = data
        else:
            view = memoryview(data)
        index = 0
        self._total += len(view)

        if self._buf:
            needed = 32 - len(self._buf)
            take = min(needed, len(view))
            self._buf.extend(view[:take])
            index += take
            if len(self._buf) == 32:
                self._process_stripe(self._buf, 0)
                self._buf.clear()

        limit = len(view) - 32
        while index <= limit:
            self._process_stripe(view, index)
            index += 32

        if index < len(view):
            self._buf.extend(view[index:])

    def digest(self):
        length = self._total
        if length >= 32:
            if self._v1 is None:
                acc = self._seed + _XXH_PRIME64_5 + _XXH_PRIME64_5
            else:
                acc = ((self._v1 << 1) | (self._v1 >> 63))
                acc = _xxh64_merge_round(acc, self._v2)
                acc = _xxh64_merge_round(acc, self._v3)
                acc = _xxh64_merge_round(acc, self._v4)
        else:
            acc = self._seed + _XXH_PRIME64_5 + _XXH_PRIME64_5 + _XXH_PRIME64_5

        remaining = bytes(self._buf)
        offset = 0
        rlen = len(remaining)
        while rlen - offset >= 8:
            val = struct.unpack_from("<Q", remaining, offset)[0]
            acc = ((acc ^ _xxh64_round(0, val)) * _XXH_PRIME64_1 + _XXH_PRIME64_4) & 0xFFFFFFFFFFFFFFFF
            offset += 8
        while rlen - offset >= 4:
            val = struct.unpack_from("<I", remaining, offset)[0]
            acc = ((acc ^ (val * _XXH_PRIME64_1)) * _XXH_PRIME64_3 + _XXH_PRIME64_5) & 0xFFFFFFFFFFFFFFFF
            offset += 4
        while offset < rlen:
            val = remaining[offset]
            acc = ((acc ^ (val * _XXH_PRIME64_5)) * _XXH_PRIME64_3 + _XXH_PRIME64_5) & 0xFFFFFFFFFFFFFFFF
            offset += 1

        # Final avalanche (no intermediate masks — matches one-shot xxh64)
        acc ^= acc >> 37
        acc = (acc * _XXH_PRIME64_3) + _XXH_PRIME64_5
        acc ^= acc >> 37
        acc = (acc * _XXH_PRIME64_4) + _XXH_PRIME64_5
        acc ^= acc >> 37

        return acc & 0xFFFFFFFFFFFFFFFF

    def hexdigest(self):
        return '{:016x}'.format(self.digest())


def _xxh64_file_hex(path, chunk_size=1048576):
    """Compute xxh64 of file bytes with bounded memory, returning 16-char hex.

    Reads the file in *chunk_size*-byte chunks (default 1 MiB) and feeds
    them incrementally into the streaming xxh64 hasher.  Never loads the
    complete file into memory.

    Returns:
        str: 16-character lowercase hex, or '' on any open/read/hash error.
    """
    try:
        hasher = _Xxh64Stream(seed=0)
        with open(path, 'rb') as stream:
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception:
        return ''


# =========================================================
# ASSET IDENTITY HELPERS (Phase 5D)
# =========================================================

def get_mesh_identity_hash(obj):
    """Return (low: int, high: int, primitive_type: int).

    xxHash64 of the Blender mesh datablock name.
    Deterministic across sessions and duplicated object instances.
    NOT stable across datablock renames.

    If obj is not a MESH or has no data, returns (0, 0, PRIMITIVE_EMPTY).
    """
    if obj.type != 'MESH' or obj.data is None:
        return (0, 0, PRIMITIVE_EMPTY)

    name_bytes = obj.data.name.encode("utf-8")
    hash_value = xxh64(name_bytes)

    low = hash_value & 0xFFFFFFFFFFFFFFFF
    high = (hash_value >> 64) & 0xFFFFFFFFFFFFFFFF

    # Get the configured primitive type as fallback
    try:
        from . import sync
        primitive = sync._get_primitive_type()
    except (ImportError, AttributeError):
        primitive = PRIMITIVE_CUBE

    return (low, high, primitive if primitive is not None else PRIMITIVE_CUBE)


def serialize_asset_identity(guid_obj, identity_low, identity_high, primitive_type):
    """33 bytes per object: GUID(16) + IdentityHash(16) + PrimitiveFallback(1).

    PT_AssetDef (V5) payload format.
    """
    payload = bytearray()

    # GUID (4 × uint32 LE) — same decomposition as serialize_object_v3
    a = guid_obj.time_low
    b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
    c = (guid_obj.clock_seq_hi_variant << 24) | (guid_obj.clock_seq_low << 16) | ((guid_obj.node >> 32) & 0xFFFF)
    d = guid_obj.node & 0xFFFFFFFF
    payload.extend(struct.pack("<IIII", a, b, c, d))

    # Identity hash (2 × uint64 LE)
    payload.extend(struct.pack("<QQ", identity_low & 0xFFFFFFFFFFFFFFFF, identity_high & 0xFFFFFFFFFFFFFFFF))

    # Primitive fallback (1 byte)
    payload.extend(struct.pack("<B", primitive_type))

    return bytes(payload)


# =========================================================
# MATERIAL IDENTITY HELPERS (Phase 7B)
# =========================================================

def get_material_identity_hash(material):
    """Return (low: int, high: int).

    xxHash64 of the Blender material datablock name.
    Deterministic across sessions. NOT stable across material renames.

    If material is None or has no name, returns (0, 0).
    """
    if material is None:
        return (0, 0)

    name_bytes = material.name.encode("utf-8")
    hash_value = xxh64(name_bytes)

    low = hash_value & 0xFFFFFFFFFFFFFFFF
    high = (hash_value >> 64) & 0xFFFFFFFFFFFFFFFF

    return (low, high)


def get_object_material_slots(obj):
    """Return a dict mapping slot_index -> (material_low, material_high).

    Extracts material identity for each material slot on a Blender object.
    Slots with no material assigned return (0, 0).
    Only MESH objects with material_slots are processed.

    Returns {} for non-MESH or data-less objects.
    """
    if obj.type != 'MESH' or obj.data is None:
        return {}

    if not hasattr(obj, "material_slots"):
        return {}

    slots = {}
    for slot_index, slot in enumerate(obj.material_slots):
        mat = slot.material
        if mat is not None:
            low, high = get_material_identity_hash(mat)
        else:
            low, high = (0, 0)
        slots[slot_index] = (low, high)

    return slots


# =========================================================
# MATERIAL PACKET CONSTANTS (Phase 7B Stage 1C)
# =========================================================

# Per-slot wire size: SlotIndex(1) + MaterialLow(8) + MaterialHigh(8)
LIVE_SYNC_V5_MATERIAL_SLOT_SIZE = 17

# Per-object base size: GUID(16) + SlotCount(1)
LIVE_SYNC_V5_MATERIAL_OBJECT_BASE_SIZE = 17

MAX_MATERIAL_SLOTS = 8

# =========================================================
# MATERIAL BASIC PROPERTIES EXTENSION (Phase 10J.5H)
# =========================================================

# MATX magic: 'MATX' as little-endian uint32
MATX_MAGIC = 0x4D415458
MATX_VERSION = 1
# Per-slot extension: SlotIndex(1) + R(4) + G(4) + B(4) + A(4) + Roughness(4) + Metallic(4)
MATX_PROP_SLOT_SIZE = 25


def get_material_basic_properties(material):
    """Extract basic material properties from a Blender material.

    Priority (Phase 10J.5L):
    1. Principled BSDF Base Color (if non-default)
    2. material.diffuse_color (if user set viewport color)
    3. Principled BSDF Base Color (even if default, as fallback)

    Roughness/Metallic are always read from Principled BSDF if available.

    Returns:
        dict with BaseColorR/G/B/A (0..1), Roughness (0..1), Metallic (0..1)
        All floats clamped to [0, 1].
        Returns None if material is None.
    """
    if material is None:
        return None

    DEFAULT_PRINCIPLED_COLOR = (0.8, 0.8, 0.8, 1.0)

    props = {}
    source = "default"
    mat_name = material.name if material else "None"

    # Try Principled BSDF node inputs
    if material.node_tree and material.node_tree.nodes:
        principled = None
        for node in material.node_tree.nodes:
            if getattr(node, "type", None) == "BSDF_PRINCIPLED":
                principled = node
                break

        if principled:
            # Phase 10J.5L: robust socket name matching — try common variants
            bc = None
            bc_sock_name = None
            for _sock_name in ("Base Color", "Basecolour", "base_color", "BaseColor", "Color"):
                bc = principled.inputs.get(_sock_name)
                if bc is not None:
                    bc_sock_name = _sock_name
                    break

            if bc is not None:
                v = bc.default_value
                p_color = (v[0], v[1], v[2], v[3] if len(v) > 3 else 1.0)
                is_default = (
                    abs(p_color[0] - DEFAULT_PRINCIPLED_COLOR[0]) < 0.001
                    and abs(p_color[1] - DEFAULT_PRINCIPLED_COLOR[1]) < 0.001
                    and abs(p_color[2] - DEFAULT_PRINCIPLED_COLOR[2]) < 0.001
                    and abs(p_color[3] - DEFAULT_PRINCIPLED_COLOR[3]) < 0.001
                )

                # Check if diffuse_color differs from principled default
                dc = material.diffuse_color
                dc_color = (dc[0], dc[1], dc[2], dc[3] if len(dc) > 3 else 1.0)
                dc_is_default = (
                    abs(dc_color[0] - DEFAULT_PRINCIPLED_COLOR[0]) < 0.001
                    and abs(dc_color[1] - DEFAULT_PRINCIPLED_COLOR[1]) < 0.001
                    and abs(dc_color[2] - DEFAULT_PRINCIPLED_COLOR[2]) < 0.001
                    and abs(dc_color[3] - DEFAULT_PRINCIPLED_COLOR[3]) < 0.001
                )

                if is_default and not dc_is_default:
                    # Principled color is default gray but diffuse_color is set → use diffuse_color
                    props["BaseColorR"] = max(0.0, min(1.0, dc[0]))
                    props["BaseColorG"] = max(0.0, min(1.0, dc[1]))
                    props["BaseColorB"] = max(0.0, min(1.0, dc[2]))
                    props["Alpha"] = max(0.0, min(1.0, dc[3] if len(dc) > 3 else 1.0))
                    source = "diffuse_color_override"
                else:
                    # Use Principled BSDF color
                    props["BaseColorR"] = max(0.0, min(1.0, v[0]))
                    props["BaseColorG"] = max(0.0, min(1.0, v[1]))
                    props["BaseColorB"] = max(0.0, min(1.0, v[2]))
                    props["Alpha"] = max(0.0, min(1.0, v[3] if len(v) > 3 else 1.0))
                    source = "principled"
            else:
                # Base Color socket not found — enumerate all socket names for diagnostics
                socket_names = [s.name for s in principled.inputs]
                _append_blender_debug_log(
                    f"[MAT][SOCKETS] mat={mat_name} sockets={socket_names}"
                )
                # Fall back to diffuse_color
                dc = material.diffuse_color
                props["BaseColorR"] = max(0.0, min(1.0, dc[0]))
                props["BaseColorG"] = max(0.0, min(1.0, dc[1]))
                props["BaseColorB"] = max(0.0, min(1.0, dc[2]))
                props["Alpha"] = max(0.0, min(1.0, dc[3] if len(dc) > 3 else 1.0))
                source = "diffuse_color_fallback"

            r = principled.inputs.get("Roughness")
            props["Roughness"] = max(0.0, min(1.0, r.default_value)) if r is not None else 0.5

            m = principled.inputs.get("Metallic")
            props["Metallic"] = max(0.0, min(1.0, m.default_value)) if m is not None else 0.0
        else:
            # No Principled BSDF node — fallback to diffuse_color
            dc = material.diffuse_color
            props["BaseColorR"] = max(0.0, min(1.0, dc[0]))
            props["BaseColorG"] = max(0.0, min(1.0, dc[1]))
            props["BaseColorB"] = max(0.0, min(1.0, dc[2]))
            props["Alpha"] = max(0.0, min(1.0, dc[3] if len(dc) > 3 else 1.0))
            props["Roughness"] = 0.5
            props["Metallic"] = 0.0
            source = "diffuse_color_no_principled"
    else:
        # No node tree — fallback to diffuse_color
        dc = material.diffuse_color
        props["BaseColorR"] = max(0.0, min(1.0, dc[0]))
        props["BaseColorG"] = max(0.0, min(1.0, dc[1]))
        props["BaseColorB"] = max(0.0, min(1.0, dc[2]))
        props["Alpha"] = max(0.0, min(1.0, dc[3] if len(dc) > 3 else 1.0))
        props["Roughness"] = 0.5
        props["Metallic"] = 0.0
        source = "diffuse_color_no_nodes"

    return props


# =========================================================
# TEXTURE MAP IDENTITY EXTENSION (Phase 10K.1)
# =========================================================

# MTEX magic: 'MTEX' as little-endian uint32
MTEX_MAGIC = 0x4D544558
MTEX_VERSION = 1

# Channel enum
MTEX_CHANNEL_BASECOLOR = 1
MTEX_CHANNEL_ROUGHNESS = 2
MTEX_CHANNEL_METALLIC = 3
MTEX_CHANNEL_ALPHA = 4
MTEX_CHANNEL_NORMAL = 5

# MTEX flags
MTEX_FLAG_PATH_ABSOLUTE = 0x01
MTEX_FLAG_IMAGE_PACKED = 0x02
MTEX_FLAG_COLORSPACE_SRGB = 0x04
MTEX_FLAG_COLORSPACE_NON_COLOR = 0x08

# Clamp limits
MTEX_MAX_PATH_LEN = 2048
MTEX_MAX_IMAGE_NAME_LEN = 255

# =========================================================
# Material texture extraction logging controls (Task 9B.6B.13)
# =========================================================

# Global verbose flag: when false, only summaries/errors are logged.
# Set via addon preferences or diagnostic operator.
material_verbose_logging = False


def material_verbose_logging_fn(enabled=True):
    """Toggle verbose material extraction logging."""
    global material_verbose_logging
    material_verbose_logging = enabled

# Deduplication set for MTEX extraction records.
# Key: (sync_id, guid, slot_index, channel, canonical_image_key)
# Cleared on Stop Sync, reconnect, addon reload, or new explicit sync.
_mtex_extract_dedup_set = set()

# Per-sync collector: list of (slot_index, channel, image_name, filepath, flags, is_packed)
# Populated when _mtex_collecting is True, cleared when _mtex_collecting is False.
_mtex_collect_records = []
_mtex_collecting = False


def _mtex_clear_dedup_state():
    """Clear the MTEX extraction dedup set. Call on Stop Sync, reconnect, reload."""
    global _mtex_extract_dedup_set, _mtex_collecting, _mtex_collect_records
    _mtex_extract_dedup_set = set()
    _mtex_collecting = False
    _mtex_collect_records = []


def _mtex_start_collecting(sync_id, guid):
    """Enable MTEX record collection for one sync. Call before extract_texture_maps_for_slot()."""
    global _mtex_collecting, _mtex_collect_records
    _mtex_collecting = True
    _mtex_collect_sync_id = sync_id
    _mtex_collect_guid = guid
    _mtex_collect_records = []


def _mtex_collect_channel(slot_index, channel, image_name, filepath, flags, is_packed):
    """Append a channel record to the current collection."""
    global _mtex_collecting, _mtex_collect_records
    if _mtex_collecting:
        _mtex_collect_records.append((slot_index, channel, image_name, filepath, flags, is_packed))


# =========================================================
# Phase 10J.5L: Material basic property extraction collection
# =========================================================
# Replaces [MAT][EXTRACT] per-call log spam (Task 9B.6B.14).
# Transaction-scoped summaries only on actual material sends or
# explicit FBX syncs.

_mat_basic_collecting = False
_mat_basic_collect_sync_id = 0
_mat_basic_collect_guid = ""
_mat_basic_collect_records = []  # list of (slot_index, props_dict)


def _mt_basic_clear_state():
    """Clear material basic property collection state."""
    global _mat_basic_collecting, _mat_basic_collect_records
    _mat_basic_collecting = False
    _mat_basic_collect_records = []


def _mt_basic_start_collecting(sync_id, guid):
    """Enable material basic property collection for one sync."""
    global _mat_basic_collecting, _mat_basic_collect_sync_id, _mat_basic_collect_guid, _mat_basic_collect_records
    _mat_basic_collecting = True
    _mat_basic_collect_sync_id = sync_id
    _mat_basic_collect_guid = guid
    _mat_basic_collect_records = []


def _mt_basic_collect_slot(slot_index, props):
    """Append a material slot's basic properties to the current collection."""
    global _mat_basic_collecting, _mat_basic_collect_records
    if _mat_basic_collecting and props is not None:
        _mat_basic_collect_records.append((slot_index, props))


# Exposed for sync.py / __init__.py access
mat_basic_collect_records = _mat_basic_collect_records


def compute_material_basic_changed_fields(prev_props, cur_props):
    """Return a list of field names that changed between prev and cur.

    Only keys present in both dicts are compared.
    """
    if prev_props is None or cur_props is None:
        return []
    changed = []
    for key in cur_props:
        if key in prev_props and prev_props[key] != cur_props[key]:
            changed.append(key)
    return changed


def _get_image_colorspace_flag(image):
    """Return MTEX color space flag for a Blender image."""
    if image is None:
        return 0
    cs = getattr(image, "colorspace_settings", None)
    if cs is None:
        return 0
    name = getattr(cs, "name", "")
    if not name:
        return 0
    name_lower = name.lower()
    if "non-color" in name_lower or "noncolor" in name_lower or "raw" in name_lower:
        return MTEX_FLAG_COLORSPACE_NON_COLOR
    if "srgb" in name_lower or "sRGB" in name:
        return MTEX_FLAG_COLORSPACE_SRGB
    return 0


def get_texture_identity_name(source, is_packed, filepath, image_name):
    """Return basename without the final extension.

    FILE + unpacked:
        normalize both slash styles and derive from filepath.

    Packed/generated/other:
        derive from image_name.

    Preserve original case.
    """
    if source == 'FILE' and not is_packed and filepath:
        normalised = filepath.replace("\\", "/")
        base = os.path.basename(normalised)
        if base:
            return os.path.splitext(base)[0]
    return os.path.splitext(image_name)[0]


def get_texture_canonical_key(source, is_packed, filepath, image_name):
    """Return lowercase lookup key derived from get_texture_identity_name()."""
    return get_texture_identity_name(source, is_packed, filepath, image_name).lower()


# =========================================================
# A3.1 Texture identity helpers
# =========================================================


def _canonical_locator_bytes(source_kind, packed_status, locator):
    """Return domain-separated canonical locator bytes for SHA-256 suffix.

    Args:
        source_kind: 'FILE', 'PACKED', or 'GENERATED'
        packed_status: 'packed' | 'unpacked' | 'generated'
        locator: absolute filesystem path (FILE) or image name (packed/generated)

    Returns:
        bytes suitable for hashing.
    """
    return f"{source_kind}:{packed_status}:{locator}".encode("utf-8")


def _sanitize_filename_component(component):
    """Sanitize a filename component for safe filesystem use.

    Replaces:
        - NUL bytes and control characters (U+0000-U+001F, U+007F)
        - Trailing dots and spaces
        - Exact '.' and '..' → '_'
        - Windows reserved names: CON, PRN, AUX, NUL, COM1-9, LPT1-9
        - Colon and slash characters

    Args:
        component: The raw filename component (before hash suffix).

    Returns:
        Sanitized component safe for use as a filename prefix.
    """
    if not component:
        return "_"

    # Replace NUL and control characters
    result = []
    for ch in component:
        cp = ord(ch)
        if cp == 0 or cp == 0x7F or (0x01 <= cp <= 0x1F):
            result.append("_")
        else:
            result.append(ch)
    s = "".join(result)

    # Replace colon and slash with underscore
    s = s.replace(":", "_").replace("/", "_").replace("\\", "_")

    # Trim trailing dots and spaces
    s = s.rstrip(". ")

    # Replace exactly '.' or '..'
    if s == "." or s == "..":
        s = "_"

    # Check for Windows reserved names (case-insensitive)
    reserved = {"con", "prn", "aux", "nul"}
    reserved |= {f"com{i}" for i in range(1, 10)}
    reserved |= {f"lpt{i}" for i in range(1, 10)}
    if s.lower() in reserved:
        s = "_" + s

    return s if s else "_"


def _truncate_to_utf8_bytes(text, max_bytes):
    """Truncate text to fit within max_bytes UTF-8 bytes.

    Ensures truncation never splits a multi-byte UTF-8 character.

    Args:
        text: The string to truncate.
        max_bytes: Maximum number of UTF-8 bytes.

    Returns:
        Truncated string guaranteed to encode to <= max_bytes bytes.
    """
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    truncated = encoded[:max_bytes]
    return truncated.decode("utf-8", errors="ignore")


def _get_name_max(dest_dir):
    """Return the maximum filename byte length for the destination filesystem.

    Uses os.pathconf() when available (POSIX). Fallback to 255.

    Args:
        dest_dir: Directory path on the target filesystem.

    Returns:
        Maximum filename length in bytes (NAME_MAX).
    """
    try:
        return os.pathconf(dest_dir, "PC_NAME_MAX")
    except (AttributeError, ValueError, OSError, NotImplementedError):
        return 255


def make_sidecar_key(display_prefix, content_hash_hex, ext, dest_dir):
    """Build a deterministic sidecar filename with content-based hash suffix.

    Filename format:
        <sanitized_prefix>__<16-char-content-xxh64><ext>

    U1 strategy: the content hash suffix makes the basename deterministic
    for identical file bytes, independent of filepath, locator bytes,
    or image name.  UE's insertion key
    (FPaths::GetBaseFilename(SourceFilename).ToLower()) and lookup key
    (TexRef.ImageName.ToLower()) match because ImageName derives from the
    same basename.

    Byte budgeting uses the smaller of the filesystem NAME_MAX and the
    MTEX ImageName field limit (MTEX_MAX_IMAGE_NAME_LEN).

    Args:
        display_prefix: Human-readable prefix (e.g., image name).
        content_hash_hex: 16-character lowercase xxh64 hex of final bytes.
        ext: File extension including dot (e.g., '.png').
        dest_dir: Destination directory (for NAME_MAX detection).

    Returns:
        Tuple of (sidecar_filename, sidecar_key, content_hash_hex)
        where sidecar_key is the basename without extension (ImageName).
    """
    hash_suffix = content_hash_hex  # 16 characters

    safe_prefix = _sanitize_filename_component(display_prefix)

    name_max = _get_name_max(dest_dir)
    ext_bytes = ext.encode("utf-8")
    key_budget = min(
        name_max - len(ext_bytes),
        MTEX_MAX_IMAGE_NAME_LEN,
    )
    prefix_budget = key_budget - 2 - 16
    if prefix_budget < 1:
        prefix_budget = 1
    truncated_prefix = _truncate_to_utf8_bytes(safe_prefix, prefix_budget)
    filename = f"{truncated_prefix}__{hash_suffix}{ext}"

    filename_base = os.path.splitext(filename)[0]

    # Postcondition assertions
    _base_bytes = filename_base.encode("utf-8")
    _full_bytes = filename.encode("utf-8")
    assert len(_base_bytes) <= MTEX_MAX_IMAGE_NAME_LEN, \
        f"ImageName {len(_base_bytes)}B exceeds MTEX limit {MTEX_MAX_IMAGE_NAME_LEN}B"
    assert len(_full_bytes) <= name_max, \
        f"Filename {len(_full_bytes)}B exceeds NAME_MAX {name_max}B"

    return filename, filename_base, content_hash_hex


def extract_texture_maps_for_slot(material, material_name="", slot_index=-1, _collect=False, _suppress_summary=False):
    """Extract texture map references from a Blender material node tree.

    Phase 10K.1: diagnostic-only. Supports direct links from Image Texture
    nodes to Principled BSDF inputs, and one hop of indirect connections
    (e.g. Image Texture → MixRGB/ColorRamp → Principled input).

    Supported channels:
        BaseColor: Image Texture Color → Principled Base Color
        Roughness: Image Texture Color/Non-Color → Principled Roughness
        Metallic:  Image Texture Color/Non-Color → Principled Metallic
        Alpha:     Image Texture Alpha → Principled Alpha
        Normal:    Image Texture Color → Normal Map Color → Principled Normal

    For each channel, only the first connected Image Texture is reported.
    Procedural nodes are not evaluated.

    Args:
        material: Blender material object.
        material_name: Display name for diagnostic logging.
        slot_index: Display slot index for diagnostic logging.

    Returns:
        list of (channel, filepath, image_name, flags) or empty list
    """
    if material is None or not material.use_nodes:
        return []
    if not material.node_tree or not material.node_tree.nodes:
        return []

    # Build a map: socket → (channel, is_normal_map_chain)
    # We detect direct Image Texture connections and Normal Map chains.
    principled = None
    for node in material.node_tree.nodes:
        if getattr(node, "type", None) == "BSDF_PRINCIPLED":
            principled = node
            break

    if principled is None:
        return []

    # Map Principled input names to MTEX channels.
    # Base channels are checked first; if unlinked, Coat fallback is considered.
    target_sockets = {}
    for sock_name, channel in (("Base Color", MTEX_CHANNEL_BASECOLOR),
                                ("Roughness", MTEX_CHANNEL_ROUGHNESS),
                                ("Metallic", MTEX_CHANNEL_METALLIC),
                                ("Alpha", MTEX_CHANNEL_ALPHA),
                                ("Normal", MTEX_CHANNEL_NORMAL)):
        sock = principled.inputs.get(sock_name)
        if sock is not None and sock.is_linked:
            target_sockets[sock_name] = channel

    # Task 8A.2: Coat fallback for Roughness and Normal.
    # If base Roughness is unlinked, try Coat Roughness as fallback.
    # If base Normal is unlinked, try Coat Normal as fallback.
    roughness_socket = principled.inputs.get("Roughness")
    coat_roughness_socket = principled.inputs.get("Coat Roughness")
    if "Roughness" not in target_sockets and roughness_socket is not None \
            and coat_roughness_socket is not None and coat_roughness_socket.is_linked:
        target_sockets["Coat Roughness"] = MTEX_CHANNEL_ROUGHNESS

    normal_socket = principled.inputs.get("Normal")
    coat_normal_socket = principled.inputs.get("Coat Normal")
    if "Normal" not in target_sockets and normal_socket is not None and normal_socket.is_linked:
        # base Normal IS linked — no coat fallback needed
        pass
    elif "Normal" not in target_sockets and normal_socket is not None and not normal_socket.is_linked:
        if coat_normal_socket is not None and coat_normal_socket.is_linked:
            target_sockets["Coat Normal"] = MTEX_CHANNEL_NORMAL
    elif "Normal" not in target_sockets and normal_socket is None:
        if coat_normal_socket is not None and coat_normal_socket.is_linked:
            target_sockets["Coat Normal"] = MTEX_CHANNEL_NORMAL

    if not target_sockets:
        return []

    results = []
    # Track which MTEX channels have been emitted to prevent duplicates.
    emitted_channels = set()
    # Channel name lookup for diagnostics.
    ch_names = {1: "BaseColor", 2: "Roughness", 3: "Metallic", 4: "Alpha", 5: "Normal"}

    for sock_name, channel in target_sockets.items():
        sock = principled.inputs.get(sock_name)
        if sock is None or not sock.is_linked:
            continue

        from_node = sock.links[0].from_node

        # Handle Normal Map node chain: Image Texture → Normal Map → Principled Normal
        if channel == MTEX_CHANNEL_NORMAL and getattr(from_node, "type", None) == "NORMAL_MAP":
            # Find the Color input of the Normal Map node
            nm_color = from_node.inputs.get("Color")
            if nm_color is not None and nm_color.is_linked:
                from_node = nm_color.links[0].from_node

        # If not directly an Image Texture, try one hop of indirect color connections
        # for Roughness / Metallic / Alpha channels (e.g. Image Texture → MixRGB → Principled).
        if getattr(from_node, "type", None) != "TEX_IMAGE":
            # Supported indirect node types that pass a color through
            indirect_types = {"MIX_RGB", "COLOR_RAMP", "INVERT", "GAMMA", "CURVES", "HUE_SATURATION"}
            if getattr(from_node, "type", None) in indirect_types:
                # Check Color input (or Fac/Z for non-color nodes like Gamma)
                color_input = from_node.inputs.get("Color")
                if color_input is None:
                    color_input = from_node.inputs.get("Fac")
                if color_input is None:
                    color_input = from_node.inputs.get("Value")
                if color_input is not None and color_input.is_linked:
                    from_node = color_input.links[0].from_node

        # Must be an Image Texture node (direct or after one indirect hop)
        if getattr(from_node, "type", None) != "TEX_IMAGE":
            continue

        image = getattr(from_node, "image", None)
        if image is None:
            continue

        filepath = getattr(image, "filepath", "") or ""
        is_packed = getattr(image, "packed_file", None) is not None
        image_name = get_texture_identity_name(
            source=getattr(image, "source", "") or "",
            is_packed=is_packed,
            filepath=filepath,
            image_name=getattr(image, "name", "") or "",
        )

        flags = 0
        if filepath and not is_packed:
            if filepath.startswith("/") or filepath.startswith("\\") or (len(filepath) > 1 and filepath[1] == ":"):
                flags |= MTEX_FLAG_PATH_ABSOLUTE
        if is_packed:
            flags |= MTEX_FLAG_IMAGE_PACKED
        cs_flag = _get_image_colorspace_flag(image)
        if cs_flag:
            flags |= cs_flag
        elif channel in (MTEX_CHANNEL_ROUGHNESS, MTEX_CHANNEL_METALLIC, MTEX_CHANNEL_NORMAL):
            flags |= MTEX_FLAG_COLORSPACE_NON_COLOR

        # Clamp lengths
        if len(filepath) > MTEX_MAX_PATH_LEN:
            filepath = filepath[:MTEX_MAX_PATH_LEN]
        if len(image_name) > MTEX_MAX_IMAGE_NAME_LEN:
            image_name = image_name[:MTEX_MAX_IMAGE_NAME_LEN]

        # Prevent duplicate channel records (task 8A.2 policy #5).
        if channel in emitted_channels:
            continue
        emitted_channels.add(channel)

        results.append((channel, filepath, image_name, flags))

        # Diagnostic: channel source tracking (suppress on every tick).
        is_coat_fallback = sock_name in ("Coat Roughness", "Coat Normal")
        if is_coat_fallback:
            print(
                f"[MATERIAL][COAT_FALLBACK] slot={slot_index} material={material_name} "
                f"coatInput={sock_name} mappedChannel={ch_names.get(channel, str(channel))}"
            )

        # Task 9B.6B.13: collect channel for transaction-scoped summary (only when _collect=True).
        # The hot path (timer tick) must never emit per-channel debug lines.
        if _collect:
            _mtex_collect_channel(slot_index, channel, image_name, filepath, flags, is_packed)

    # Task 9B.6B.13: per-slot/channel extraction summary suppressed on timer ticks.
    # When _suppress_summary=False (explicit FBX sync), emit for diagnostic visibility.
    if not _suppress_summary:
        detected = [ch_names.get(r[0], f"chan{r[0]}") for r in results]
        print(f"[MATERIAL][CHANNEL_EXTRACT_SUMMARY] slot={slot_index} material={material_name} channels={detected}")

    return results


# =========================================================
# MATERIAL TEXTURE DIRTY HASH (Phase 7H)
# =========================================================


def compute_material_texture_hash(slot_index, texture_maps):
    """Return a deterministic hash key for the texture metadata of one slot.

    Each entry in texture_maps is (channel, filepath, image_name, flags).
    Includes: file size + mtime for FILE sources, packed flag, path,
    and image name.  Channel order is deterministic.

    Returns:
        tuple of (low64, high64) or (0, 0) if no maps.
    """
    if not texture_maps:
        return (0, 0)

    # Build a deterministic string: channel|path_or_packed|image_name|size|mtime
    parts = []
    for ch, fpath, img_name, flags in texture_maps:
        is_packed = bool(flags & MTEX_FLAG_IMAGE_PACKED)
        file_size = 0
        file_mtime = 0
        source = "PACKED" if is_packed else "FILE"

        if not is_packed and fpath:
            try:
                st = os.stat(fpath)
                file_size = st.st_size
                file_mtime = int(st.st_mtime)
            except Exception:
                pass

        parts.append(
            f"{ch}|{source}|{fpath}|{img_name}|{file_size}|{file_mtime}"
        )

    key_str = "|".join(parts)
    hash_value = xxh64(key_str.encode("utf-8"))

    low = hash_value & 0xFFFFFFFFFFFFFFFF
    high = (hash_value >> 64) & 0xFFFFFFFFFFFFFFFF

    return (low, high)


def compute_material_dirty_sig(current_prop_sig, current_tex_sigs):
    """Return a combined dirty-signal tuple for material dirty detection.

    Args:
        current_prop_sig: dict slot_index -> (BaseColorR, G, B, Alpha, Roughness, Metallic)
        current_tex_sigs: dict slot_index -> (low64, high64) texture hash

    Returns:
        tuple (scalar_hash, texture_hash, combined_hash) where each is a string.
    """
    # Scalar hash from property sig
    scalar_items = []
    for si in sorted(current_prop_sig.keys()):
        sig = current_prop_sig[si]
        scalar_items.append(f"{si}:{sig[0]:.6f},{sig[1]:.6f},{sig[2]:.6f},{sig[3]:.6f},{sig[4]:.6f},{sig[5]:.6f}")
    scalar_hash = xxh64("|".join(scalar_items).encode("utf-8")) if scalar_items else 0

    # Texture hash from texture sigs
    tex_items = []
    for si in sorted(current_tex_sigs.keys()):
        low, high = current_tex_sigs[si]
        tex_items.append(f"{si}:{low:016x}{high:016x}")
    texture_hash = xxh64("|".join(tex_items).encode("utf-8")) if tex_items else 0

    # Combined hash
    combined_str = f"s{scalar_hash:016x}t{texture_hash:016x}"
    combined_hash = xxh64(combined_str.encode("utf-8"))

    return (int(scalar_hash), int(texture_hash), int(combined_hash))


def serialize_material_slots(guid_obj, slots, properties=None, texture_maps=None):
    """Serialize material slot data for one object into PT_Material wire format.

    Preserves old identity block exactly as before.
    Appends MATX extension block when properties dict is provided.
    Appends MTEX extension block when texture_maps dict is provided,
    after MATX (or after identity block if MATX absent).

    Args:
        guid_obj: uuid.UUID of the target object.
        slots: dict mapping slot_index -> (material_low, material_high)
        properties: optional dict mapping slot_index -> material property dict
                    (as returned by get_material_basic_properties)
        texture_maps: optional dict mapping slot_index -> list of
                      (channel, path, image_name, flags) tuples
                      (as returned by extract_texture_maps_for_slot)

    Returns:
        bytes payload for one object in PT_Material batch.
    """
    payload = bytearray()
    _guid_str = guid_obj.hex[:8] if hasattr(guid_obj, 'hex') else str(guid_obj)[:8]
    try:
        # GUID (4 × uint32 LE)
        a = guid_obj.time_low
        b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
        c = (guid_obj.clock_seq_hi_variant << 24) | (guid_obj.clock_seq_low << 16) | ((guid_obj.node >> 32) & 0xFFFF)
        d = guid_obj.node & 0xFFFFFFFF
        payload.extend(struct.pack("<IIII", a, b, c, d))

        # Slot count (clamped to MAX_MATERIAL_SLOTS)
        slot_count = min(len(slots), MAX_MATERIAL_SLOTS)
        if not isinstance(slot_count, int):
            raise TypeError(f"slot_count type={type(slot_count).__name__} expected=int")
        payload.extend(struct.pack("<B", slot_count))

        # Per-slot data: SlotIndex(1) + MaterialLow(8) + MaterialHigh(8)
        for slot_index in range(slot_count):
            low, high = slots.get(slot_index, (0, 0))
            if not isinstance(slot_index, int):
                raise TypeError(f"slot_index type={type(slot_index).__name__} expected=int field=identity_slot_index")
            payload.extend(struct.pack("<B", slot_index & 0xFF))
            payload.extend(struct.pack("<QQ", low & 0xFFFFFFFFFFFFFFFF, high & 0xFFFFFFFFFFFFFFFF))

        # MATX extension block (optional)
        if properties is not None and properties:
            ext_slot_count = min(len(properties), MAX_MATERIAL_SLOTS)
            if not isinstance(ext_slot_count, int):
                raise TypeError(f"ext_slot_count type={type(ext_slot_count).__name__} expected=int")
            payload.extend(struct.pack("<I", MATX_MAGIC))
            payload.extend(struct.pack("<B", MATX_VERSION))
            payload.extend(struct.pack("<B", ext_slot_count))
            for slot_index in range(ext_slot_count):
                p = properties.get(slot_index)
                if not isinstance(slot_index, int):
                    raise TypeError(f"slot_index type={type(slot_index).__name__} expected=int field=matx_slot_index")
                if p is None:
                    payload.extend(struct.pack("<B", slot_index & 0xFF))
                    payload.extend(struct.pack("<ffff", 0.8, 0.8, 0.8, 1.0))
                    payload.extend(struct.pack("<ff", 0.5, 0.0))
                else:
                    payload.extend(struct.pack("<B", slot_index & 0xFF))
                    payload.extend(struct.pack("<ffff",
                        p.get("BaseColorR", 0.8),
                        p.get("BaseColorG", 0.8),
                        p.get("BaseColorB", 0.8),
                        p.get("Alpha", 1.0)))
                    payload.extend(struct.pack("<ff",
                        p.get("Roughness", 0.5),
                        p.get("Metallic", 0.0)))

        # MTEX extension block (optional, after MATX or after identity if MATX absent)
        if texture_maps is not None and texture_maps:
            # Flatten all records from all slots
            flat_records = []
            for slot_index in sorted(texture_maps.keys()):
                records = texture_maps[slot_index]
                if not records:
                    continue
                for rec in records:
                    channel, filepath, image_name, flags = rec
                    if not isinstance(channel, int):
                        raise TypeError(f"channel type={type(channel).__name__} expected=int field=mtex_channel slot={slot_index}")
                    if not isinstance(flags, int):
                        raise TypeError(f"flags type={type(flags).__name__} expected=int field=mtex_flags slot={slot_index} channel={channel}")
                    if not isinstance(filepath, str):
                        raise TypeError(f"filepath type={type(filepath).__name__} expected=str field=mtex_path slot={slot_index} channel={channel}")
                    if not isinstance(image_name, str):
                        raise TypeError(f"image_name type={type(image_name).__name__} expected=str field=mtex_name slot={slot_index} channel={channel}")
                    flat_records.append((slot_index, channel, filepath, image_name, flags))

            if flat_records:
                rec_count = len(flat_records)
                if not isinstance(rec_count, int):
                    raise TypeError(f"rec_count type={type(rec_count).__name__} expected=int")
                payload.extend(struct.pack("<I", MTEX_MAGIC))
                payload.extend(struct.pack("<B", MTEX_VERSION))
                payload.extend(struct.pack("<B", rec_count))

                for slot_index, channel, filepath, image_name, flags in flat_records:
                    if not isinstance(slot_index, int):
                        raise TypeError(f"slot_index type={type(slot_index).__name__} expected=int field=mtex_record_slot")
                    if not isinstance(channel, int):
                        raise TypeError(f"channel type={type(channel).__name__} expected=int field=mtex_record_channel slot={slot_index}")
                    if not isinstance(flags, int):
                        raise TypeError(f"flags type={type(flags).__name__} expected=int field=mtex_record_flags slot={slot_index} channel={channel}")
                    # Clamp string lengths
                    path_bytes = filepath.encode("utf-8", errors="replace")
                    if not isinstance(path_bytes, (bytes, bytearray)):
                        raise TypeError(f"path_bytes type={type(path_bytes).__name__} expected=bytes slot={slot_index}")
                    if len(path_bytes) > MTEX_MAX_PATH_LEN:
                        path_bytes = path_bytes[:MTEX_MAX_PATH_LEN]
                    name_bytes = image_name.encode("utf-8", errors="replace")
                    if not isinstance(name_bytes, (bytes, bytearray)):
                        raise TypeError(f"name_bytes type={type(name_bytes).__name__} expected=bytes slot={slot_index}")
                    if len(name_bytes) > MTEX_MAX_IMAGE_NAME_LEN:
                        name_bytes = name_bytes[:MTEX_MAX_IMAGE_NAME_LEN]

                    path_len = len(path_bytes)
                    name_len = len(name_bytes)

                    payload.extend(struct.pack("<B", slot_index & 0xFF))
                    payload.extend(struct.pack("<B", channel & 0xFF))
                    payload.extend(struct.pack("<B", flags & 0xFF))
                    if not isinstance(path_len, int):
                        raise TypeError(f"path_len type={type(path_len).__name__} expected=int slot={slot_index}")
                    payload.extend(struct.pack("<H", path_len))
                    payload.extend(path_bytes)
                    if not isinstance(name_len, int):
                        raise TypeError(f"name_len type={type(name_len).__name__} expected=int slot={slot_index}")
                    payload.extend(struct.pack("<B", name_len))
                    payload.extend(name_bytes)

                _append_blender_debug_log(
                    f"[MTEX][SEND] records={rec_count} bytes={len(payload)}"
                )

        return bytes(payload)
    except (TypeError, struct.error) as _ser_exc:
        _slot_info = f"slots={len(slots)}" if slots else "slots=0"
        _prop_info = f"props={len(properties)}" if properties else "props=None"
        _tex_info = f"tex={len(texture_maps)}" if texture_maps else "tex=None"
        print(f"[MATERIAL][PACKET_BUILD_ERROR] guid={_guid_str} {_slot_info} {_prop_info} {_tex_info} error={_ser_exc}")
        print(f"[MATERIAL][PACKET_BUILD_ERROR] guid={_guid_str} valueType={type(_ser_exc).__name__}")
        raise


# =========================================================
# PLAYBACK STATE SERIALIZATION (Phase 7C)
# =========================================================

# Payload: state(1) + loop_enabled(1) + sequence_number(4) + timestamp(8) = 14 bytes
PLAYBACK_PAYLOAD_SIZE = 14

def serialize_playback_state(state, sequence_number, timestamp, loop_enabled=0):
    """Serialize playback state into fixed-size 14-byte payload.

    Payload layout:
      [0]    state         uint8    — PLAYBACK_PLAY/PAUSE/STOP
      [1]    loop_enabled  uint8    — 0 or 1 (reserved, always 0 for now)
      [2-5]  sequence      uint32 LE — monotonic counter
      [6-13] timestamp     double LE — time.time() at detection

    Returns bytes of length PLAYBACK_PAYLOAD_SIZE.
    """
    return struct.pack("<BBId", state & 0xFF, loop_enabled & 0xFF, sequence_number & 0xFFFFFFFF, timestamp)


# =========================================================
# ACTIVE CAMERA SERIALIZATION (Phase 7D)
# =========================================================

# Payload: guid(16) + sequence(4) + timestamp(8) = 28 bytes
ACTIVE_CAMERA_PAYLOAD_SIZE = 28

# =========================================================
# CAMERA DEFINITION SERIALIZATION (Phase 7G Stage 3)
# =========================================================

# PT_CameraDef (0x1B) fixed-size payload: 44 bytes
# Payload layout:
#   [0-15]  guid          bytes   — 16-byte camera object GUID
#   [16-19] focal_length  float LE — focal length in mm
#   [20-23] sensor_width  float LE — sensor width in mm
#   [24-27] sensor_height float LE — sensor height in mm
#   [28-31] clip_start    float LE — near clip plane
#   [32-35] clip_end      float LE — far clip plane
#   [36-39] ortho_scale   float LE — orthographic scale
#   [40]    flags         uint8    — bit 0=is_ortho, bit 1=has_camera_def
#   [41-43] reserved      bytes    — zero padding
CAMERA_DEF_PAYLOAD_SIZE = 48

def render_aspect_ratio(context):
    """Blender render aspect ratio: (resolution_x * pax) / (resolution_y * pay).

    This is the single source of truth for camera aspect ratio in the
    protocol — never derived from sensor or viewport dimensions.
    """
    render = context.scene.render
    pax = getattr(render, 'pixel_aspect_x', 1.0)
    pay = getattr(render, 'pixel_aspect_y', 1.0)
    return (render.resolution_x * pax) / (max(render.resolution_y * pay, 1))

def serialize_camera_def(camera_guid, focal_length_mm=50.0,
                          sensor_width_mm=36.0, sensor_height_mm=24.0,
                          clip_start=0.1, clip_end=1000.0,
                          ortho_scale=6.0, aspect_ratio=0.0, flags=0):
    """Serialize camera definition into fixed-size 48-byte payload.

    Args:
        camera_guid: UUID object, or None for default (all-zero GUID).
        focal_length_mm: Focal length in millimeters.
        sensor_width_mm: Sensor width in millimeters.
        sensor_height_mm: Sensor height in millimeters.
        clip_start: Near clip plane distance.
        clip_end: Far clip plane distance.
        ortho_scale: Orthographic scale factor.
        aspect_ratio: Blender render aspect (resolution_x / resolution_y).
        flags: Bitfield (bit 0=is_ortho, bit 1=has_camera_def).

    Returns bytes of length CAMERA_DEF_PAYLOAD_SIZE.
    """
    if camera_guid is None:
        guid_bytes = b'\x00' * 16
    else:
        guid_bytes = pack_ue_fguid(camera_guid)
    return struct.pack(
        "<16sfffffffB3s",
        guid_bytes,
        focal_length_mm,
        sensor_width_mm,
        sensor_height_mm,
        clip_start,
        clip_end,
        ortho_scale,
        aspect_ratio,
        flags & 0xFF,
        b'\x00\x00\x00'
    )

# =========================================================
# TIMELINE SERIALIZATION (Phase 7B)
# =========================================================

# PT_Timeline (0x13) fixed-size payload: 36 bytes
# Payload layout:
#   [0-3]   frame_current  int32   — current frame number
#   [4-7]   frame_start    int32   — timeline start frame
#   [8-11]  frame_end      int32   — timeline end frame
#   [12-15] fps_num        int32   — FPS numerator (e.g. 24)
#   [16-19] fps_den        int32   — FPS denominator (e.g. 1)
#   [20-23] sequence       uint32  — monotonic global counter (LE)
#   [24-27] reserved       int32   — reserved for future use
#   [28-35] timestamp      double  — time.time() at detection (LE)
TIMELINE_PAYLOAD_SIZE = 36

def serialize_timeline(frame_current, frame_start, frame_end,
                       fps_num, fps_den, sequence, timestamp,
                       reserved=0):
    return struct.pack(
        "<iiiiiIid",
        frame_current, frame_start, frame_end,
        fps_num, fps_den,
        sequence & 0xFFFFFFFF,
        reserved, timestamp
    )

# =========================================================
# TIMELINE STATE SERIALIZATION (Phase 7F Stage 1)
# =========================================================

# PT_TimelineState (0x19) fixed-size payload: 20 bytes
# Payload layout:
#   [0-3]   frame_start  int32  — timeline start frame
#   [4-7]   frame_end    int32  — timeline end frame
#   [8-11]  frame_current int32 — current playhead position
#   [12-15] fps_num      int32  — FPS numerator (e.g. 24)
#   [16-19] fps_den      int32  — FPS denominator (e.g. 1)
TIMELINE_STATE_PAYLOAD_SIZE = 20


def serialize_timeline_state(frame_start, frame_end, frame_current,
                              fps_num, fps_den):
    """Serialize timeline state payload for PT_TimelineState (0x19)."""
    return struct.pack(
        "<iiiii",
        frame_start, frame_end, frame_current,
        fps_num, fps_den
    )


# =========================================================
# PLAYBACK TRANSPORT SERIALIZATION (Phase 7F Stage 2)
# =========================================================

# PT_PlaybackTransport (0x1A) fixed-size payload: 6 bytes
# Payload layout:
#   [0]    command       uint8   — 0=SetFrame, 1=Play, 2=Pause, 3=Stop
#   [1-4]  frame_current int32  — current playhead position
#   [5]    flags         uint8   — bit 0 = loop enabled (reserved)
PLAYBACK_TRANSPORT_PAYLOAD_SIZE = 6


def serialize_playback_transport(command, frame_current, flags=0):
    """Serialize playback transport payload for PT_PlaybackTransport (0x1A)."""
    return struct.pack(
        "<BiB",
        command & 0xFF,
        frame_current,
        flags & 0xFF
    )


def send_playback_transport(conn, command, frame_current, flags=0):
    """Build and send a PT_PlaybackTransport packet (obj_count=0 for non-object payload)."""
    import struct as _struct
    global _sequence_id
    _sequence_id += 1
    payload = serialize_playback_transport(command, frame_current, flags)
    header = _struct.pack('<I H B B Q I I',
                          LIVE_SYNC_MAGIC, LIVE_SYNC_VERSION_V4,
                          PT_PlaybackTransport, 0,
                          _sequence_id,
                          24 + len(payload), 0)
    conn.sendall(header + payload)


NULL_CAMERA_GUID = b'\x00' * 16

def serialize_active_camera(guid_obj, sequence, timestamp):
    """Serialize active camera payload into fixed-size 28-byte payload.

    Payload layout:
      [0-15]  guid      bytes   — 16-byte camera object GUID (all-zero = no active camera)
      [16-19] sequence  uint32 LE — monotonic global counter
      [20-27] timestamp double LE — time.time() at detection

    guid_obj: UUID object, or None for no active camera (writes all-zero GUID).
    Returns bytes of length ACTIVE_CAMERA_PAYLOAD_SIZE.
    """
    if guid_obj is None:
        guid_bytes = NULL_CAMERA_GUID
    else:
        guid_bytes = pack_ue_fguid(guid_obj)
    return struct.pack(
        "<16sId",
        guid_bytes,
        sequence & 0xFFFFFFFF,
        timestamp
    )


# =========================================================
# SEQUENCER OP SERIALIZATION (Phase 7E)
# =========================================================

# PT_SequencerOp (0x18) fixed-size common header: 16 bytes
# Wire format:
#   [0]     opcode     uint8   — SEQUENCER_OP_* constant
#   [1]     flags      uint8   — reserved (0 for now)
#   [2-3]   reserved   uint16  — reserved for future use
#   [4-7]   sequence   uint32 LE — monotonic global counter
#   [8-15]  timestamp  double LE — time.time() at detection
SEQUENCER_OP_COMMON_HEADER_SIZE = 16

# Opcode payload sizes (excluding the 16-byte common header)
SEQUENCER_OP_CREATE_SEQUENCE_PAYLOAD_SIZE   = 16  # frame_start(4) + frame_end(4) + fps_num(4) + fps_den(4)
SEQUENCER_OP_ADD_POSSESSABLE_PAYLOAD_SIZE   = 17  # object_guid(16) + binding_type(1)
SEQUENCER_OP_REMOVE_POSSESSABLE_PAYLOAD_SIZE = 16  # object_guid(16)
SEQUENCER_OP_ADD_CAMERA_CUT_PAYLOAD_SIZE     = 24  # camera_guid(16) + frame_start(4) + frame_end(4)
SEQUENCER_OP_CLEAR_SEQUENCE_PAYLOAD_SIZE     = 0   # no extra payload
SEQUENCER_OP_SET_FRAME_RANGE_PAYLOAD_SIZE    = 16  # frame_start(4) + frame_end(4) + fps_num(4) + fps_den(4)

SEQUENCER_OP_PAYLOAD_SIZES = {
    SEQUENCER_OP_CREATE_SEQUENCE:   SEQUENCER_OP_CREATE_SEQUENCE_PAYLOAD_SIZE,
    SEQUENCER_OP_ADD_POSSESSABLE:   SEQUENCER_OP_ADD_POSSESSABLE_PAYLOAD_SIZE,
    SEQUENCER_OP_REMOVE_POSSESSABLE: SEQUENCER_OP_REMOVE_POSSESSABLE_PAYLOAD_SIZE,
    SEQUENCER_OP_ADD_CAMERA_CUT:    SEQUENCER_OP_ADD_CAMERA_CUT_PAYLOAD_SIZE,
    SEQUENCER_OP_CLEAR_SEQUENCE:    SEQUENCER_OP_CLEAR_SEQUENCE_PAYLOAD_SIZE,
    SEQUENCER_OP_SET_FRAME_RANGE:   SEQUENCER_OP_SET_FRAME_RANGE_PAYLOAD_SIZE,
}

SEQUENCER_OP_MIN_OPCODE = SEQUENCER_OP_CREATE_SEQUENCE
SEQUENCER_OP_MAX_OPCODE = SEQUENCER_OP_SET_FRAME_RANGE


def _serialize_sequencer_op_common(opcode, sequence, timestamp, flags=0):
    """Serialize the 16-byte common header for PT_SequencerOp."""
    return struct.pack(
        "<BBHI d",
        opcode & 0xFF,
        flags & 0xFF,
        0,  # reserved uint16
        sequence & 0xFFFFFFFF,
        timestamp,
    )


def serialize_sequencer_op_create_sequence(sequence, timestamp,
                                           frame_start, frame_end,
                                           fps_num, fps_den, flags=0):
    """Serialize a CREATE_SEQUENCE sequencer op.

    Total payload: 16 (common) + 16 (payload) = 32 bytes
    """
    common = _serialize_sequencer_op_common(
        SEQUENCER_OP_CREATE_SEQUENCE, sequence, timestamp, flags)
    payload = struct.pack("<iiii", frame_start, frame_end, fps_num, fps_den)
    return common + payload


def serialize_sequencer_op_add_possessable(sequence, timestamp,
                                           object_guid, binding_type,
                                           flags=0):
    """Serialize an ADD_POSSESSABLE sequencer op.

    object_guid: UUID object (packed via pack_ue_fguid).

    Total payload: 16 (common) + 17 (payload) = 33 bytes
    """
    common = _serialize_sequencer_op_common(
        SEQUENCER_OP_ADD_POSSESSABLE, sequence, timestamp, flags)
    guid_bytes = pack_ue_fguid(object_guid)
    payload = struct.pack(
        "<16sB",
        guid_bytes,
        binding_type & 0xFF,
    )
    return common + payload


def serialize_sequencer_op_remove_possessable(sequence, timestamp,
                                              object_guid, flags=0):
    """Serialize a REMOVE_POSSESSABLE sequencer op.

    object_guid: UUID object (packed via pack_ue_fguid).

    Total payload: 16 (common) + 16 (payload) = 32 bytes
    """
    common = _serialize_sequencer_op_common(
        SEQUENCER_OP_REMOVE_POSSESSABLE, sequence, timestamp, flags)
    guid_bytes = pack_ue_fguid(object_guid)
    payload = struct.pack(
        "<16s",
        guid_bytes,
    )
    return common + payload


def serialize_sequencer_op_add_camera_cut(sequence, timestamp,
                                          camera_guid, frame_start,
                                          frame_end, flags=0):
    """Serialize an ADD_CAMERA_CUT sequencer op.

    camera_guid: UUID object (packed via pack_ue_fguid).

    Total payload: 16 (common) + 24 (payload) = 40 bytes
    """
    common = _serialize_sequencer_op_common(
        SEQUENCER_OP_ADD_CAMERA_CUT, sequence, timestamp, flags)
    guid_bytes = pack_ue_fguid(camera_guid)
    payload = struct.pack(
        "<16sii",
        guid_bytes,
        frame_start, frame_end,
    )
    return common + payload


def serialize_sequencer_op_clear_sequence(sequence, timestamp, flags=0):
    """Serialize a CLEAR_SEQUENCE sequencer op.

    Total payload: 16 (common) + 0 (payload) = 16 bytes
    """
    return _serialize_sequencer_op_common(
        SEQUENCER_OP_CLEAR_SEQUENCE, sequence, timestamp, flags)


def serialize_sequencer_op_set_frame_range(sequence, timestamp,
                                           frame_start, frame_end,
                                           fps_num, fps_den, flags=0):
    """Serialize a SET_FRAME_RANGE sequencer op.

    Total payload: 16 (common) + 16 (payload) = 32 bytes
    """
    common = _serialize_sequencer_op_common(
        SEQUENCER_OP_SET_FRAME_RANGE, sequence, timestamp, flags)
    payload = struct.pack("<iiii", frame_start, frame_end, fps_num, fps_den)
    return common + payload


# =========================================================
# KEYFRAME REPLICATION (Phase 7E Stage 7 — PT_Keyframe 0x17)
# =========================================================
# Variable-size payload: 14-byte header + N × 25-byte entries.
#
# Header:
#   [0-3]   Sequence     uint32 LE
#   [4-11]  Timestamp    double LE
#   [12]    KeyCount     uint8    (1-255)
#   [13]    Flags        uint8
#
# Each entry:
#   [0-15]  ObjectGUID   FGuid (16 bytes)
#   [16-19] Frame        int32 LE
#   [20-23] Value        float LE
#   [24]    ChannelIndex uint8

KEYFRAME_HEADER_SIZE = 14
KEYFRAME_ENTRY_SIZE = 25
KEYFRAME_MIN_KEYS = 1
KEYFRAME_MAX_KEYS = 255
KEYFRAME_MIN_CHANNEL = 0
KEYFRAME_MAX_CHANNEL = 255

# Phase 7E Stage 10A: Visibility keyframe channels (within PT_Keyframe)
KEYFRAME_CHANNEL_VISIBILITY_VIEWPORT = 9   # hide_viewport → bool visible/hidden
KEYFRAME_CHANNEL_VISIBILITY_RENDER = 10    # hide_render → bool renderable/not

# Phase 7E Stage 12: Camera FOV keyframe channel (within PT_Keyframe)
KEYFRAME_CHANNEL_CAMERA_FOV = 11           # FOV degrees → float track


def serialize_keyframe(sequence, timestamp, entries, flags=0):
    """Serialize a PT_Keyframe packet.

    entries: list of (guid_obj, frame, value, channel_index) tuples.
             guid_obj must be a UUID object (packed via pack_ue_fguid).

    Total payload: 14 + N * 25 bytes.
    """
    count = len(entries)
    if count < KEYFRAME_MIN_KEYS or count > KEYFRAME_MAX_KEYS:
        raise ValueError(
            f"Key count {count} out of range [{KEYFRAME_MIN_KEYS}, {KEYFRAME_MAX_KEYS}]")

    header = struct.pack("<IdBB",
        sequence & 0xFFFFFFFF,
        timestamp,
        count & 0xFF,
        flags & 0xFF)

    body = b''
    for guid_obj, frame, value, channel_index in entries:
        guid_bytes = pack_ue_fguid(guid_obj)
        body += struct.pack("<16sifB",
            guid_bytes[:16].ljust(16, b'\x00'),
            frame,
            value,
            channel_index & 0xFF)

    return header + body


# =========================================================
# GLOBAL STATE
# =========================================================

_sequence_id = 0
_seq_lock = threading.Lock()


# =========================================================
# GEOMETRY CONSTANTS (Phase 7C)
# =========================================================

# PT_Mesh chunk header: GUID(16) + VersionHash(64) + ChunkIndex(4) + ChunkCount(4) + Flags(1) = 89 bytes
LIVE_SYNC_V5_MESH_CHUNK_HEADER_SIZE = 89

# Per-chunk payload flags (bitmask)
MESH_CHUNK_FLAG_HAS_POSITIONS     = 0x01  # Chunk contains vertex positions
MESH_CHUNK_FLAG_HAS_TRIANGLES     = 0x02  # Chunk contains triangle indices
MESH_CHUNK_FLAG_HAS_MATERIAL_IDX  = 0x04  # Chunk contains per-triangle material indices
MESH_CHUNK_FLAG_HAS_NORMALS       = 0x08  # Reserved for Stage 2
MESH_CHUNK_FLAG_HAS_UVS           = 0x10  # Reserved for Stage 2
MESH_CHUNK_FLAG_FIRST_CHUNK       = 0x20  # First chunk of multi-chunk mesh
MESH_CHUNK_FLAG_LAST_CHUNK        = 0x40  # Last chunk of multi-chunk mesh
MESH_CHUNK_FLAG_FULL_ATTR         = 0x80  # Phase 7C Stage 2A: full-attribute schema gate

# Phase 7C Stage 2B.2: Full-attribute schema version and vertex strides
MESH_FULL_ATTR_SCHEMA_VERSION = 1
MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR = 32
MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0 = 48


def extract_evaluated_mesh_data(obj):
    """Extract evaluated mesh geometry from a Blender object.

    Uses the dependency graph to get the modifier-applied mesh,
    then extracts vertex positions, triangle indices, and per-triangle
    material slot indices.

    Returns a dict:
        {
            "vertices": [(x, y, z), ...],
            "triangles": [(v0, v1, v2), ...],
            "material_indices": [mat_idx_per_triangle, ...],
            "vertex_count": int,
            "triangle_count": int,
        }
    Returns None if obj is not a MESH, has no data, or extraction fails.
    """
    try:
        import bpy
        # Phase 10J.5I+5J: flush edit mode changes before evaluation.
        # obj.update_from_editmode() is needed because view_layer.update()
        # alone may not flush edit-mode vertex coordinate changes for the
        # object whose data-block is being read. Both are required.
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass

        depsgraph = bpy.context.evaluated_depsgraph_get()
        if depsgraph is None:
            return None

        evaluated_obj = obj.evaluated_get(depsgraph)
        if evaluated_obj is None:
            return None

        if evaluated_obj.type != 'MESH':
            return None

        mesh = evaluated_obj.to_mesh()
        if mesh is None:
            return None

        mesh_data = {
            "vertices": [],
            "triangles": [],
            "material_indices": [],
            "vertex_count": 0,
            "triangle_count": 0,
        }

        # Vertex positions
        mesh_data["vertices"] = [
            (v.co.x, v.co.y, v.co.z) for v in mesh.vertices
        ]
        mesh_data["vertex_count"] = len(mesh.vertices)

        # Triangle topology + per-face material index
        mesh.calc_loop_triangles()
        for tri in mesh.loop_triangles:
            mesh_data["triangles"].append(
                (tri.vertices[0], tri.vertices[1], tri.vertices[2])
            )
            mesh_data["material_indices"].append(tri.material_index)

        mesh_data["triangle_count"] = len(mesh.loop_triangles)

        # Cleanup
        evaluated_obj.to_mesh_clear()

        return mesh_data

    except (ImportError, AttributeError, RuntimeError, ReferenceError) as e:
        # Outside Blender or no depsgraph available
        return None


def compute_geometry_version_hash(vertices, triangles, material_indices):
    """Return SHA-256 hex digest of deterministic geometry byte stream.

    Hashes: vertex count + vertex positions + triangle count +
    triangle indices + material indices (all as packed LE bytes).

    Deterministic across sessions for identical mesh content.
    CHANGES when vertex positions, topology, or material assignment changes.
    """
    import hashlib
    h = hashlib.sha256()

    # Vertex count (uint32 LE)
    h.update(struct.pack("<I", len(vertices)))

    # Vertex positions (float32 x 3 per vertex)
    for v in vertices:
        h.update(struct.pack("<fff", v[0], v[1], v[2]))

    # Triangle count (uint32 LE)
    h.update(struct.pack("<I", len(triangles)))

    # Triangle indices (uint32 x 3 per triangle)
    for t in triangles:
        h.update(struct.pack("<III", t[0], t[1], t[2]))

    # Material indices (int32 per triangle)
    for m in material_indices:
        h.update(struct.pack("<i", m))

    return h.hexdigest()


def serialize_mesh_chunk(guid_obj, version_hash, chunk_index, chunk_count,
                          vertices, triangles, material_indices, flags=0):
    """Serialize one PT_Mesh chunk to bytes.

    Args:
        guid_obj: uuid.UUID of the target object.
        version_hash: str — hex digest from compute_geometry_version_hash.
        chunk_index: int — 0-based index of this chunk.
        chunk_count: int — total number of chunks for this mesh.
        vertices: list of (x, y, z) tuples.
        triangles: list of (v0, v1, v2) tuples.
        material_indices: list of int — per-triangle material slot index.
        flags: bitmask of MESH_CHUNK_FLAG_* values.

    Returns:
        bytes — the complete chunk payload (header + data blocks).
    """
    payload = bytearray()

    # GUID (4 × uint32 LE)
    a = guid_obj.time_low
    b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
    c = (guid_obj.clock_seq_hi_variant << 24) | (guid_obj.clock_seq_low << 16) | ((guid_obj.node >> 32) & 0xFFFF)
    d = guid_obj.node & 0xFFFFFFFF
    payload.extend(struct.pack("<IIII", a, b, c, d))

    # Geometry version hash (32 bytes = SHA-256 hex decoded to bytes)
    version_bytes = version_hash.encode("ascii")
    if len(version_bytes) != 64:
        version_bytes = version_bytes.ljust(64, b'\x00')[:64]
    payload.extend(version_bytes)

    # Chunk index + chunk count (uint32 LE)
    payload.extend(struct.pack("<II", chunk_index, chunk_count))

    # Flags (uint8)
    payload.extend(struct.pack("<B", flags))

    # Vertex count (uint32 LE) + positions
    payload.extend(struct.pack("<I", len(vertices)))
    for v in vertices:
        payload.extend(struct.pack("<fff", v[0], v[1], v[2]))

    # Triangle count (uint32 LE) + indices
    payload.extend(struct.pack("<I", len(triangles)))
    for t in triangles:
        payload.extend(struct.pack("<III", t[0], t[1], t[2]))

    # Material indices (uint32 count + int32 per triangle)
    payload.extend(struct.pack("<I", len(material_indices)))
    for m in material_indices:
        payload.extend(struct.pack("<i", m))

    return bytes(payload)


# =========================================================
# PHASE 7C STAGE 2B.2: FULL-ATTR MESH SERIALIZATION (V1)
# =========================================================

def serialize_full_attr_mesh_chunk_v1(
    guid_obj,
    version_hash,
    chunk_index,
    chunk_count,
    render_vertices,
    local_indices,
    flags=0,
    vertex_stride=32,
):
    """Serialize a FULL_ATTR PT_Mesh chunk with SchemaVersion=1 payload.

    Wire format (shared 89-byte header matches serialize_mesh_chunk):

      Chunk 0:  Header(89) + SchemaVersion(4) + Stride(4) + VertCount(4)
                + VertexV1[N] + IndexCount(4) + Indices[]
      Chunk>0:  Header(89) + Stride(4) + VertCount(4)
                + VertexV1[N] + IndexCount(4) + Indices[]

    VertexV1 stride=32: pos(float3) + normal(float3) + uv0(float2)
    VertexV1 stride=48: pos(float3) + normal(float3) + uv0(float2) + color0(float4)

    Args:
        guid_obj: uuid.UUID of the target object.
        version_hash: str — 64-char hex digest from compute_geometry_version_hash.
        chunk_index: int — 0-based index of this chunk.
        chunk_count: int — total number of chunks for this mesh.
        render_vertices: list of dicts with position, normal, uv0, color0.
        local_indices: list of int — triangle indices into render_vertices.
        flags: bitmask — must include MESH_CHUNK_FLAG_FULL_ATTR.
        vertex_stride: int — 32 (no color) or 48 (with color0).

    Returns:
        bytes — the complete chunk payload (header + attribute data).

    Raises:
        ValueError: on any validation failure.
    """
    if not (flags & MESH_CHUNK_FLAG_FULL_ATTR):
        raise ValueError("flags must include MESH_CHUNK_FLAG_FULL_ATTR")

    if vertex_stride not in (MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR, MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0):
        raise ValueError(f"vertex_stride must be 32 or 48, got {vertex_stride}")

    if vertex_stride == MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0:
        for i, rv in enumerate(render_vertices):
            if rv.get("color0") is None:
                raise ValueError(
                    f"render_vertices[{i}] missing color0 (stride=48)")

    for i, idx in enumerate(local_indices):
        if not (0 <= idx < len(render_vertices)):
            raise ValueError(
                f"local_indices[{i}] = {idx} out of range "
                f"[0, {len(render_vertices)})")

    if not (0 <= chunk_index < chunk_count):
        raise ValueError(
            f"chunk_index {chunk_index} must be < chunk_count {chunk_count}")

    version_bytes = version_hash.encode("ascii")
    if len(version_bytes) != 64:
        raise ValueError(
            f"version_hash must be 64 ASCII chars, got {len(version_bytes)}")

    # Build 89-byte header (matches serialize_mesh_chunk)
    payload = bytearray()

    a = guid_obj.time_low
    b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
    c = (guid_obj.clock_seq_hi_variant << 24
         | guid_obj.clock_seq_low << 16
         | (guid_obj.node >> 32) & 0xFFFF)
    d = guid_obj.node & 0xFFFFFFFF
    payload.extend(struct.pack("<IIII", a, b, c, d))

    payload.extend(version_bytes)

    payload.extend(struct.pack("<II", chunk_index, chunk_count))
    payload.extend(struct.pack("<B", flags))

    # Payload
    if chunk_index == 0:
        payload.extend(struct.pack("<I", MESH_FULL_ATTR_SCHEMA_VERSION))

    payload.extend(struct.pack("<I", vertex_stride))
    payload.extend(struct.pack("<I", len(render_vertices)))

    for rv in render_vertices:
        p = rv["position"]
        n = rv["normal"]
        uv = rv["uv0"]
        payload.extend(struct.pack("<fff", p[0], p[1], p[2]))
        payload.extend(struct.pack("<fff", n[0], n[1], n[2]))
        payload.extend(struct.pack("<ff", uv[0], uv[1]))
        if vertex_stride == MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0:
            c = rv["color0"]
            payload.extend(struct.pack("<ffff", c[0], c[1], c[2], c[3]))

    payload.extend(struct.pack("<I", len(local_indices)))
    for idx in local_indices:
        payload.extend(struct.pack("<I", idx))

    return bytes(payload)


# =========================================================
# PHASE 7C STAGE 2B.3: MANUAL MESH SYNC HELPERS
# =========================================================

TRIANGLES_PER_CHUNK = 8192


def extract_loop_expanded_render_vertices(mesh):
    """Extract loop-expanded render vertices from a Blender mesh.

    Args:
        mesh: bpy.types.Mesh (evaluated, loop_triangles calc'd).

    Returns:
        (render_vertices, stride, uv0_fallback, diagnostics)
    """
    has_uv = len(mesh.uv_layers) > 0
    uv_layer = mesh.uv_layers.active.data if has_uv else None

    has_color = len(mesh.vertex_colors) > 0
    color_layer = mesh.vertex_colors.active.data if has_color else None

    stride = MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0 if has_color \
        else MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR
    uv0_fallback = 0
    diagnostics = []

    loops = mesh.loops
    vertices = mesh.vertices

    render_vertices = []

    for tri in mesh.loop_triangles:
        for loop_idx in tri.loops:
            loop = loops[loop_idx]

            pos = (vertices[loop.vertex_index].co.x,
                   vertices[loop.vertex_index].co.y,
                   vertices[loop.vertex_index].co.z)

            no = (loop.normal.x, loop.normal.y, loop.normal.z)

            if uv_layer:
                uv = uv_layer[loop_idx].uv
                uv0 = (uv.x, uv.y)
            else:
                uv0 = (0.0, 0.0)
                uv0_fallback = 1

            if color_layer:
                c = color_layer[loop_idx].color
                color0 = (c[0], c[1], c[2], c[3])
            else:
                color0 = None

            render_vertices.append({
                "position": pos,
                "normal": no,
                "uv0": uv0,
                "color0": color0,
            })

    if uv0_fallback:
        diagnostics.append("[MESH][ATTR] uv0Fallback=1")
    elif has_uv:
        diagnostics.append(
            f"[MESH][ATTR] uv0Layer={mesh.uv_layers.active.name}")

    return render_vertices, stride, uv0_fallback, diagnostics


def compute_render_vertex_version_hash(render_vertices, stride):
    """SHA-256 hex digest over full render vertex payload.

    Deterministic across sessions for identical mesh content.
    Changes when any vertex attribute changes.
    """
    import hashlib
    h = hashlib.sha256()
    for rv in render_vertices:
        p = rv["position"]
        n = rv["normal"]
        uv = rv["uv0"]
        h.update(struct.pack("<fff", p[0], p[1], p[2]))
        h.update(struct.pack("<fff", n[0], n[1], n[2]))
        h.update(struct.pack("<ff", uv[0], uv[1]))
        if stride == MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0:
            c = rv["color0"]
            h.update(struct.pack("<ffff", c[0], c[1], c[2], c[3]))
    return h.hexdigest()


def chunk_render_vertices(render_vertices, stride, triangle_count):
    """Split render vertices into triangle-range chunks.

    Returns list of chunk dicts:
        'vertices', 'indices', 'vertex_count',
        'triangle_count', 'chunk_index'
    """
    num_chunks = max(1, (triangle_count + TRIANGLES_PER_CHUNK - 1)
                     // TRIANGLES_PER_CHUNK)
    chunks = []
    vc_start = 0

    for ci in range(num_chunks):
        tri_start = ci * TRIANGLES_PER_CHUNK
        tri_end = min(tri_start + TRIANGLES_PER_CHUNK, triangle_count)
        tri_in_chunk = tri_end - tri_start
        vc = tri_in_chunk * 3

        chunk_verts = render_vertices[vc_start:vc_start + vc]
        vc_start += vc

        chunk_indices = []
        for ti in range(tri_in_chunk):
            base = ti * 3
            chunk_indices.extend([base, base + 1, base + 2])

        chunks.append({
            "vertices": chunk_verts,
            "indices": chunk_indices,
            "vertex_count": vc,
            "triangle_count": tri_in_chunk,
            "chunk_index": ci,
        })

    return chunks


# =========================================================
# OBJECT SERIALIZATION
# =========================================================

def serialize_object(guid_hex, transform):

    payload = bytearray()

    # =====================================================
    # GUID (16 bytes) — hex string → bytes
    # =====================================================

    guid_bytes = bytes.fromhex(guid_hex)

    payload.extend(guid_bytes)

    # =====================================================
    # LOCATION
    # =====================================================

    payload.extend(struct.pack(
        "<fff",

        transform["location"][0],
        transform["location"][1],
        transform["location"][2]
    ))

    # =====================================================
    # ROTATION (QUATERNION)
    # =====================================================

    payload.extend(struct.pack(
        "<ffff",

        transform["rotation"][0],
        transform["rotation"][1],
        transform["rotation"][2],
        transform["rotation"][3]
    ))

    # =====================================================
    # SCALE
    # =====================================================

    payload.extend(struct.pack(
        "<fff",

        transform["scale"][0],
        transform["scale"][1],
        transform["scale"][2]
    ))

    return payload


def serialize_object_v3(guid_obj, transform, timestamp, parent_guid_obj=None, primitive_type=None):

    payload = bytearray()

    # =====================================================
    # GUID (4 × uint32 LE) — proper field widths
    # =====================================================

    guid_a = guid_obj.time_low
    guid_b = (
        guid_obj.time_mid << 16
    ) | guid_obj.time_hi_version
    guid_c = (
        guid_obj.clock_seq_hi_variant << 24
    ) | (
        guid_obj.clock_seq_low << 16
    ) | (
        (guid_obj.node >> 32) & 0xFFFF
    )
    guid_d = (
        guid_obj.node & 0xFFFFFFFF
    )

    payload.extend(struct.pack(
        "<IIII",
        guid_a,
        guid_b,
        guid_c,
        guid_d
    ))

    # =====================================================
    # LOCATION
    # =====================================================

    payload.extend(struct.pack(
        "<fff",

        transform["location"][0],
        transform["location"][1],
        transform["location"][2]
    ))

    # =====================================================
    # ROTATION (QUATERNION)
    # =====================================================

    payload.extend(struct.pack(
        "<ffff",

        transform["rotation"][0],
        transform["rotation"][1],
        transform["rotation"][2],
        transform["rotation"][3]
    ))

    # =====================================================
    # SCALE
    # =====================================================

    payload.extend(struct.pack(
        "<fff",

        transform["scale"][0],
        transform["scale"][1],
        transform["scale"][2]
    ))

    # =====================================================
    # TIMESTAMP (double, seconds)
    # =====================================================

    payload.extend(struct.pack(
        "<d",
        timestamp
    ))

    # =====================================================
    # PARENT GUID (4 × uint32 LE, zero if no parent)
    # =====================================================

    if parent_guid_obj:

        pg_a = parent_guid_obj.time_low
        pg_b = (
            parent_guid_obj.time_mid << 16
        ) | parent_guid_obj.time_hi_version
        pg_c = (
            parent_guid_obj.clock_seq_hi_variant << 24
        ) | (
            parent_guid_obj.clock_seq_low << 16
        ) | (
            (parent_guid_obj.node >> 32) & 0xFFFF
        )
        pg_d = (
            parent_guid_obj.node & 0xFFFFFFFF
        )

        payload.extend(struct.pack(
            "<IIII",
            pg_a,
            pg_b,
            pg_c,
            pg_d
        ))

    else:

        payload.extend(struct.pack(
            "<IIII",
            0, 0, 0, 0
        ))

    # =====================================================
    # PRIMITIVE TYPE (1 byte, CREATE-only, 0x00 = Cube)
    # =====================================================

    if primitive_type is None:
        primitive_type = PRIMITIVE_CUBE

    payload.extend(struct.pack(
        "<B",
        primitive_type
    ))

    return payload


# =========================================================
# HIERARCHY SERIALIZATION (Phase 6D, PT_Hierarchy = 0x0D)
# =========================================================
# Fixed-size wire format per object (44 bytes):
#   + GUID(16) + ParentGuid(16) + sequence(4) + timestamp(8)
#
# All-zero ParentGuid = detach-to-root semantic.
# This is a discrete semantic attachment event, NOT a state stream.
# See Docs/Architecture/24-phase6D-hierarchy-scope-lock.md
# =========================================================

_hierarchy_sequences = {}

# Phase 6E: Per-GUID delete sequence tracker (monotonic, replay dedup)
_delete_sequences = {}

def serialize_delete(guid_obj):
    """28 bytes per object: GUID(16) + sequence(4) + timestamp(8).

    PT_Delete_V5 (0x0E) fixed-size wire format.
    First identity-destruction semantic lane.
    """
    payload = bytearray()

    # GUID decomposition (4 x uint32 LE)
    d_a = guid_obj.time_low
    d_b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
    d_c = (guid_obj.clock_seq_hi_variant << 24
           | guid_obj.clock_seq_low << 16
           | (guid_obj.node >> 32) & 0xFFFF)
    d_d = guid_obj.node & 0xFFFFFFFF
    payload.extend(struct.pack("<IIII", d_a, d_b, d_c, d_d))

    # Monotonic sequence per GUID (replay dedup)
    guid_key = str(guid_obj)
    seq = _delete_sequences.get(guid_key, 0) + 1
    _delete_sequences[guid_key] = seq
    payload.extend(struct.pack("<I", seq))

    # Timestamp (double, seconds)
    payload.extend(struct.pack("<d", time.time()))

    return payload


def serialize_hierarchy(guid_obj, parent_guid_obj):
    """44 bytes per object: GUID(16) + ParentGuid(16) + sequence(4) + timestamp(8).

    PT_Hierarchy (V5+) fixed-size wire format.
    parent_guid_obj=None means detach-to-root (all-zero ParentGuid).
    """
    payload = bytearray()

    # GUID
    d_a = guid_obj.time_low
    d_b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
    d_c = (guid_obj.clock_seq_hi_variant << 24
           | guid_obj.clock_seq_low << 16
           | (guid_obj.node >> 32) & 0xFFFF)
    d_d = guid_obj.node & 0xFFFFFFFF
    payload.extend(struct.pack("<IIII", d_a, d_b, d_c, d_d))

    # Parent GUID (all-zero = detach-to-root)
    if parent_guid_obj is not None:
        p_a = parent_guid_obj.time_low
        p_b = (parent_guid_obj.time_mid << 16) | parent_guid_obj.time_hi_version
        p_c = (parent_guid_obj.clock_seq_hi_variant << 24
               | parent_guid_obj.clock_seq_low << 16
               | (parent_guid_obj.node >> 32) & 0xFFFF)
        p_d = parent_guid_obj.node & 0xFFFFFFFF
        payload.extend(struct.pack("<IIII", p_a, p_b, p_c, p_d))
    else:
        payload.extend(struct.pack("<IIII", 0, 0, 0, 0))

    # Monotonic sequence per GUID (replay dedup)
    guid_key = str(guid_obj)
    seq = _hierarchy_sequences.get(guid_key, 0) + 1
    _hierarchy_sequences[guid_key] = seq
    payload.extend(struct.pack("<I", seq))

    # Timestamp
    payload.extend(struct.pack("<d", time.time()))

    return payload


# =========================================================
# GUID PACKING: UE FGuid compatible (4 × uint32 LE)
# =========================================================

def pack_ue_fguid(guid_obj):
    """Pack a UUID object into 16 bytes matching UE FGuid layout.

    UE FGuid reads GUID bytes as struct.unpack('<IIII', data):
      A = time_low
      B = (time_mid << 16) | time_hi_version
      C = (clock_seq_hi_variant << 24 | clock_seq_low << 16 | (node >> 32) & 0xFFFF)
      D = node & 0xFFFFFFFF
    """
    d_a = guid_obj.time_low
    d_b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
    d_c = (guid_obj.clock_seq_hi_variant << 24
           | guid_obj.clock_seq_low << 16
           | (guid_obj.node >> 32) & 0xFFFF)
    d_d = guid_obj.node & 0xFFFFFFFF
    return struct.pack("<IIII", d_a, d_b, d_c, d_d)


def _pack_guid(guid_obj):
    """Pack a GUID into 16 bytes (4 × uint32 LE). Delegates to pack_ue_fguid."""
    return pack_ue_fguid(guid_obj)


# =========================================================
# COLLECTION SERIALIZATION (Phase 6F, PT_Collection = 0x0F)
# =========================================================
# Two variants:
#   1. Identity variant (30 bytes) — for collection-identity ops
#      (COLLECTION_CREATE, COLLECTION_DELETE, COLLECTION_RENAME,
#       COLLECTION_REPARENT). TargetGuid is the collection GUID.
#
#   2. Membership variant (46 bytes) — for membership ops
#      (ADD, REMOVE, MOVE, CLEAR). Includes TargetGuid (actor) +
#      CollectionGuid.
#
# See Docs/Architecture/39-phase6F-vertical-slice-collection.md §1.2
# =========================================================

_collection_sequences = {}

# Anti-loop guard: GUIDs that were just updated by UE and should
# not trigger Blender re-emission. Cleared each tick.
_collection_suppressed_guids = set()


def serialize_collection_identity(guid_obj, op_type, op_flags=0):
    """30 bytes: TargetGuid(16) + OpType(1) + OpFlags(1) + seq(4) + ts(8).

    Used for COLLECTION_CREATE, COLLECTION_DELETE, COLLECTION_RENAME,
    COLLECTION_REPARENT operations.
    """
    payload = bytearray()

    # TargetGuid (collection identity)
    payload.extend(_pack_guid(guid_obj))

    # OpType
    payload.extend(struct.pack("<B", op_type))

    # OpFlags
    payload.extend(struct.pack("<B", op_flags))

    # Monotonic sequence per GUID (replay dedup)
    guid_key = str(guid_obj)
    seq = _collection_sequences.get(guid_key, 0) + 1
    _collection_sequences[guid_key] = seq
    payload.extend(struct.pack("<I", seq))

    # Timestamp
    payload.extend(struct.pack("<d", time.time()))

    # Record to replay stream (Stage 5)
    record_collection_payload(payload)

    return payload


def serialize_collection_membership(guid_obj, collection_guid_obj, op_type, op_flags=0):
    """46 bytes: TargetGuid(16) + CollectionGuid(16) + OpType(1) + OpFlags(1) + seq(4) + ts(8).

    Used for ADD, REMOVE, MOVE, CLEAR operations.
    guid_obj = actor GUID
    collection_guid_obj = collection GUID
    """
    payload = bytearray()

    # TargetGuid (actor)
    payload.extend(_pack_guid(guid_obj))

    # CollectionGuid
    payload.extend(_pack_guid(collection_guid_obj))

    # OpType
    payload.extend(struct.pack("<B", op_type))

    # OpFlags
    payload.extend(struct.pack("<B", op_flags))

    # Monotonic sequence per pair key (actor + collection for dedup)
    pair_key = str(guid_obj) + ":" + str(collection_guid_obj)
    seq = _collection_sequences.get(pair_key, 0) + 1
    _collection_sequences[pair_key] = seq
    payload.extend(struct.pack("<I", seq))

    # Timestamp
    payload.extend(struct.pack("<d", time.time()))

    # Record to replay stream (Stage 5)
    record_collection_payload(payload)

    return payload


# =========================================================
# CANONICAL SORTING HELPERS (Phase 6F Stage 5)
# =========================================================
# All collection GUID iteration must be sorted before
# hashing or snapshot serialization to guarantee
# deterministic output across runs and platforms.
# =========================================================

def _sorted_guids(guid_set):
    """Return a sorted list of GUID hex strings from a set."""
    return sorted(str(g) for g in guid_set)


def _sorted_membership(membership_map):
    """Return collection-GUID → sorted member-GUIDs, canonically ordered.

    Args:
        membership_map: dict of {collection_guid_str: set_of_member_guid_strs}

    Returns:
        list of (collection_guid_str, list_of_member_guid_strs) sorted
        by collection GUID, with each member list sorted.
    """
    result = []
    for coll_str in sorted(membership_map.keys()):
        result.append((coll_str, sorted(membership_map[coll_str])))
    return result


# =========================================================
# STABLE HASHING HELPERS (Phase 6F Stage 5)
# =========================================================

def compute_collection_membership_hash(membership_map):
    """Compute a deterministic xxHash64 of the full membership state.

    Args:
        membership_map: dict of {collection_guid_str: set_of_member_guid_strs}

    Returns:
        64-bit integer hash
    """
    canonical_pairs = _sorted_membership(membership_map)
    buf = bytearray()
    for coll_str, members in canonical_pairs:
        buf.extend(coll_str.encode("ascii"))
        for m in members:
            buf.extend(m.encode("ascii"))
    return xxh64(bytes(buf))


def compute_full_snapshot_hash(collection_identities, membership_map):
    """Compute a deterministic xxHash64 of the entire collection snapshot.

    Hashes collection identities (sorted by GUID) first, then
    full membership state (sorted).

    Args:
        collection_identities: dict/set of collection GUID strings
        membership_map: dict of {collection_guid_str: set_of_member_guid_strs}

    Returns:
        64-bit integer hash
    """
    buf = bytearray()

    # Hash identities in canonical order
    for coll_str in _sorted_guids(collection_identities):
        buf.extend(coll_str.encode("ascii"))

    # Hash membership in canonical order
    for coll_str, members in _sorted_membership(membership_map):
        buf.extend(coll_str.encode("ascii"))
        for m in members:
            buf.extend(m.encode("ascii"))

    return xxh64(bytes(buf))


# =========================================================
# COLLECTION PACKET SUB-HEADER (Phase 6F Stage 5)
# =========================================================
# Prepended once before the collection objects array.
# Format: Version(1) + Reserved(1)
# V1 = current 30B/46B layout
# =========================================================

def make_collection_subheader(version=COLLECTION_PACKET_VERSION_V1):
    """2 bytes: version + reserved."""
    return struct.pack("<BB", version, 0)


def parse_collection_subheader(data):
    """Parse (version, reserved) from data bytes.

    Returns (version, reserved) or (0, 0) if data is too short.
    """
    if len(data) < 2:
        return (0, 0)
    version, reserved = struct.unpack_from("<BB", data, 0)
    return (version, reserved)


# =========================================================
# COLLECTION REPLAY STREAM (Blender-side recording, Phase 6F Stage 5)
# =========================================================
# Append-only in-memory stream of sent PT_Collection packets.
# Each entry is the raw per-object payload bytes (30 or 46 bytes).
# Reset on disconnect/reconnect/end-snapshot.
# Bounded at COLLECTION_MAX_REPLAY_RECORD entries.
# =========================================================

COLLECTION_MAX_REPLAY_RECORD = 2048
_collection_replay_stream = []
_collection_replay_enabled = True


def start_collection_replay_recording():
    """Enable replay recording and reset the stream."""
    global _collection_replay_stream, _collection_replay_enabled
    _collection_replay_stream = []
    _collection_replay_enabled = True


def stop_collection_replay_recording():
    """Disable replay recording without clearing the stream."""
    global _collection_replay_enabled
    _collection_replay_enabled = False


def record_collection_payload(payload):
    """Append a collection payload to the replay stream (if enabled)."""
    global _collection_replay_stream
    if not _collection_replay_enabled:
        return
    if len(_collection_replay_stream) >= COLLECTION_MAX_REPLAY_RECORD:
        return
    _collection_replay_stream.append(bytes(payload))


def get_collection_replay_stream():
    """Return the current replay stream as a list of bytes objects."""
    return list(_collection_replay_stream)


def clear_collection_replay_stream():
    """Reset the replay stream."""
    global _collection_replay_stream
    _collection_replay_stream = []


# =========================================================
# LIVE SYNC CLIENT
# =========================================================

class LiveSyncClient:

    def __init__(
        self,
        host="127.0.0.1",
        port=5000
    ):

        print(
            f"[LiveSync] Client constructor: host={host} port={port}",
            flush=True
        )

        self.host = host
        self.port = port

        self.sock = None
        self.connected = False
        self.last_error = ""
        self.last_error_severity = "INFO"
        self._status_detail = "Initializing"

        self._lock = threading.Lock()

        self._send_queue = queue.Queue(
            maxsize=256
        )

        self._running = True
        self._was_connected = False
        self.reconnected = False

        self._capability_response_received = False
        self._remote_capabilities = 0
        self._last_capability_announce_time = 0.0

        self._reconnect_attempts = 0
        self._reconnect_max_delay = 10.0
        self._reconnect_base_delay = 0.5
        self._reconnect_start_time = 0.0
        self._last_send_attempt = 0.0
        self._idle_probe_interval = 5.0

        self._runtime_stats = {
            "queue_depth": 0,
            "reconnect_count": 0,
            "last_error": "",
            "last_error_severity": "INFO",
            "last_send_time": 0.0,
            "dropped_packets": 0,
            "packets_sent": 0,
            "bytes_sent": 0,
            "uptime": 0.0,
            "start_time": 0.0,
            "playback_packets_sent": 0,
            "playback_state_changes": 0,
            "timeline_packets_sent": 0,
            "timeline_state_changes": 0,
        }

        self._thread = threading.Thread(
            target=self._sender_loop,
            daemon=True
        )

        print("[LiveSync] Starting sender thread...", flush=True)

        self._thread.start()

        print("[LiveSync] Sender thread started, initiating connect...", flush=True)

        self.connect()

    # =====================================================
    # BACKGROUND SENDER
    # =====================================================

    def _sender_loop(self):

        print("[LiveSync] Sender thread started", flush=True)

        while self._running:

            try:

                data = self._send_queue.get(
                    timeout=1.0
                )

                if data is None:
                    break

                self._last_send_attempt = time.time()

                data_len = len(data)

                with self._lock:

                    if not self.connected or not self.sock:
                        self._connect_internal()

                    if self.connected and self.sock:

                        try:

                            self.sock.sendall(data)

                            self._reconnect_attempts = 0

                            self._runtime_stats["last_send_time"] = time.time()
                            self._runtime_stats["packets_sent"] += 1
                            self._runtime_stats["bytes_sent"] += data_len

                            self._runtime_stats["reconnect_count"] = self._reconnect_attempts

                            if _network_verbose:
                                print(
                                    f"[SYNC-DBG] 5 Socket send OK: {data_len} bytes",
                                    flush=True
                                )

                            # Phase 9: try to receive capability response
                            _try_recv_capability_response()

                        except (

                            BrokenPipeError,
                            ConnectionResetError,
                            OSError

                        ) as e:

                            self.last_error = str(e)

                            print(
                                f"[SYNC-DBG] 5 Socket send FAILED: {e}",
                                flush=True
                            )

                            self._reconnect_internal()

            except queue.Empty:

                self._idle_probe()

                # Phase 9: try to receive capability response
                _try_recv_capability_response()

                # Phase 9: re-send capability announce periodically until
                # response received. This handles the case where the
                # initial announce is lost during UE's connection startup.
                if self.connected and not self._capability_response_received:
                    now = time.time()
                    if now - self._last_capability_announce_time >= 5.0:
                        self._last_capability_announce_time = now
                        _send_announce(self)

                continue

    # =====================================================
    # INTERNAL I/O (caller must hold _lock)
    # =====================================================

    def _connect_internal(self):

        try:

            print(
                "[LiveSync] Creating TCP socket (AF_INET, SOCK_STREAM)",
                flush=True
            )

            self.sock = socket.socket(
                socket.AF_INET,
                socket.SOCK_STREAM
            )

            self.sock.setsockopt(
                socket.IPPROTO_TCP,
                socket.TCP_NODELAY,
                1
            )

            self.sock.settimeout(10.0)

            print(
                f"[LiveSync] Attempting connect to {self.host}:{self.port}",
                flush=True
            )

            self.sock.connect((
                self.host,
                self.port
            ))

            self.connected = True
            self.last_error = ""
            self.last_error_severity = "INFO"
            self._status_detail = (
                f"Connected to {self.host}:{self.port}"
            )

            self._reconnect_attempts = 0
            self._reconnect_start_time = 0.0

            self._runtime_stats["start_time"] = time.time()
            self._runtime_stats["reconnect_count"] = 0

            if self._was_connected:
                self.reconnected = True

            self._was_connected = True

            print(
                f"[LiveSync] Connected to UE {self.host}:{self.port} "
                f"[sig=0x{LIVE_SYNC_PROTOCOL_SIG:08X}]",
                flush=True
            )

            import struct as _pstruct
            print(
                f"[Protocol] "
                f"magic=0x{LIVE_SYNC_MAGIC:08X} LE "
                f"hdr_v3={_pstruct.calcsize('<I H B B Q I I')} "
                f"hdr_v2={_pstruct.calcsize('<I H Q I I')} "
                f"obj_v3={_pstruct.calcsize('<IIIIfff ffff fff d IIII B') - 1} "
                f"obj_v4={_pstruct.calcsize('<IIIIfff ffff fff d IIII B')} "
                f"obj_del={_pstruct.calcsize('<IIII')} "
                f"obj_asset={_pstruct.calcsize('<IIII QQ B')}",
                flush=True
            )

            # Phase 9: reset capability state and send announce on connect
            self._capability_response_received = False
            self._remote_capabilities = 0
            self._last_capability_announce_time = time.time()
            _send_announce(self)

        except ConnectionRefusedError:

            self.connected = False
            self.sock = None
            self.last_error = (
                f"Connection refused — is UE listening on {self.port}?"
            )
            self.last_error_severity = "WARNING"
            self._status_detail = "Connection refused"

            print(
                "[LiveSync] Connection refused: "
                f"is UE listening on {self.host}:{self.port}?",
                flush=True
            )

        except socket.timeout:

            self.connected = False
            self.sock = None
            self.last_error = (
                f"Connection timeout — "
                f"no response from {self.host}:{self.port}"
            )
            self.last_error_severity = "WARNING"
            self._status_detail = "Connection timeout"

            print(
                f"[LiveSync] Connection timeout: "
                f"no response from {self.host}:{self.port}",
                flush=True
            )

        except OSError as e:

            self.connected = False
            self.sock = None

            import errno as _errno
            err_code = getattr(e, 'errno', None)
            err_str = _errno.errorcode.get(err_code, 'UNKNOWN') if err_code is not None else 'UNKNOWN'
            err_msg = str(e)

            if "address already in use" in err_msg.lower():
                self.last_error = (
                    f"Port {self.port} is already in use"
                )
                self.last_error_severity = "CRITICAL"
            else:
                self.last_error = f"[errno={err_code} {err_str}] {err_msg}"
                self.last_error_severity = "WARNING"

            self._status_detail = f"Connection failed: {self.last_error}"

            print(
                f"[LiveSync] Socket connect FAILED: "
                f"errno={err_code} ({err_str}) — {err_msg}",
                flush=True
            )

        except Exception as e:

            self.connected = False
            self.sock = None
            self.last_error = str(e)
            self.last_error_severity = "WARNING"
            self._status_detail = f"Connection failed: {self.last_error}"

            print(
                f"[LiveSync] Connection failed (unexpected): {e}",
                flush=True
            )

    def _reconnect_internal(self):

        self._close_internal()

        self._reconnect_attempts += 1

        self._runtime_stats["reconnect_count"] = (
            self._reconnect_attempts
        )

        if self._reconnect_start_time == 0.0:
            self._reconnect_start_time = time.time()

        delay = min(
            self._reconnect_base_delay *
            (2 ** (self._reconnect_attempts - 1)),
            self._reconnect_max_delay
        )

        reconnect_elapsed = (
            time.time() -
            self._reconnect_start_time
        )

        self._status_detail = (
            f"Reconnecting (attempt {self._reconnect_attempts}) "
            f"in {delay:.0f}s..."
        )

        if reconnect_elapsed > 30.0:

            self.last_error = (
                f"Reconnect failed after {reconnect_elapsed:.0f}s "
                f"({self._reconnect_attempts} attempts)"
            )
            self.last_error_severity = "CRITICAL"

            print(
                "[LiveSync] CRITICAL: "
                f"persistent reconnect failure "
                f"({reconnect_elapsed:.0f}s, "
                f"{self._reconnect_attempts} attempts)"
            )

        else:

            self.last_error = (
                f"Reconnecting (attempt {self._reconnect_attempts})"
            )
            self.last_error_severity = "WARNING"

            print(
                f"[LiveSync] Reconnect attempt {self._reconnect_attempts}"
                f" in {delay:.1f}s"
            )

        time.sleep(delay)

        self._connect_internal()

    def _idle_probe(self):

        if self.connected:
            return

        if not self._was_connected:
            return

        now = time.time()

        if now - self._last_send_attempt < self._idle_probe_interval:
            return

        self._last_send_attempt = now

        with self._lock:

            if not self.connected or not self.sock:

                print(
                    f"[LiveSync] Idle probe — attempting reconnection"
                )

                self._reconnect_internal()

    def _close_internal(self):

        if self.sock:

            try:

                self.sock.close()

            except Exception:
                pass

        self.sock = None
        self.connected = False
        self._status_detail = "Disconnected"

        # Phase 6D: reset hierarchy sequence tracker on disconnect
        global _hierarchy_sequences
        if _hierarchy_sequences:
            _hierarchy_sequences.clear()
            print("[HIERARCHY] Sequence tracker cleared on disconnect")

        # Phase 6E: reset delete sequence tracker on disconnect
        global _delete_sequences
        if _delete_sequences:
            _delete_sequences.clear()
            print("[DELETE] Sequence tracker cleared on disconnect")

        # Phase 7C: reset playback sequence on disconnect
        global _playback_sequence, _last_playback_state
        _playback_sequence = 0
        _last_playback_state = None

        # Phase 7B: reset timeline sequence on disconnect
        global _timeline_sequence, _last_timeline_sent
        _timeline_sequence = 0
        _last_timeline_sent = None

        # Phase 9: reset capability state on disconnect
        global _remote_capabilities, _capability_response_received
        _remote_capabilities = 0
        _capability_response_received = False
        self._remote_capabilities = 0
        self._capability_response_received = False
        self._last_capability_announce_time = 0.0

        # Phase 6I.1 Stage 2: drain send queue on reconnect
        # to avoid sending stale packets from the previous
        # connection on the new socket.
        if not self._send_queue.empty():
            drained = 0
            while not self._send_queue.empty():
                try:
                    self._send_queue.get_nowait()
                    drained += 1
                except queue.Empty:
                    break
            print(
                f"[LIFECYCLE] Drained {drained} stale packet(s) "
                "from send queue on disconnect"
            )

    # =====================================================
    # PUBLIC API (thread-safe)
    # =====================================================

    def connect(self):

        with self._lock:

            self._connect_internal()

    def reconnect(self):

        with self._lock:

            self._reconnect_internal()

    def close(self):

        with self._lock:

            self._close_internal()

    def stop(self):

        self._running = False

        self._send_queue.put(None)

        self._thread.join(
            timeout=2.0
        )

        self.close()

    # =====================================================
    # BUILD PACKET
    # =====================================================

    def _build_packet(
        self,
        objects_data,
        version=LIVE_SYNC_VERSION_V4,
        packet_type=0x01,
        flags=0x00
    ):

        global _sequence_id

        with _seq_lock:

            _sequence_id += 1

            seq_id = _sequence_id

        payload = bytearray()

        # Phase 6F Stage 5: Prepend collection sub-header for PT_Collection packets
        if packet_type == PT_Collection:
            payload.extend(make_collection_subheader())
            # Set flag bit 0 to indicate sub-header is present
            flags |= COLLECTION_PACKET_FLAG_HAS_SUBHEADER

        # Harden: validate objects_data type and element types.
        if isinstance(objects_data, (bytes, bytearray, memoryview)):
            raise TypeError(
                f"_build_packet: objects_data must be an iterable of serialized "
                f"records, not a raw bytes-like payload (got {type(objects_data).__name__})"
            )

        object_count = len(objects_data)

        for idx, obj in enumerate(objects_data):
            if not isinstance(obj, (bytes, bytearray, memoryview)):
                raise TypeError(
                    f"_build_packet packet_type={packet_type} index={idx} "
                    f"expected bytes/bytearray/memoryview but got "
                    f"{type(obj).__name__} value={repr(obj)[:120]}"
                )
            payload.extend(obj)

        if version >= LIVE_SYNC_VERSION_V3:

            header_size = struct.calcsize(
                "<I H B B Q I I"
            )

            packet_size = (
                header_size +
                len(payload)
            )

            header = struct.pack(

                "<I H B B Q I I",

                LIVE_SYNC_MAGIC,
                version,
                packet_type,
                flags,
                seq_id,
                packet_size,
                object_count
            )

        else:

            header_size = struct.calcsize(
                "<I H Q I I"
            )

            packet_size = (
                header_size +
                len(payload)
            )

            header = struct.pack(

                "<I H Q I I",

                LIVE_SYNC_MAGIC,
                version,
                seq_id,
                packet_size,
                object_count
            )

        if _network_verbose:

            hex_dump = " ".join(
                f"{b:02x}"
                for b in header[:24]
            )

            print(
                f"[Packet] ver={version} "
                f"type=0x{packet_type:02x} "
                f"flags=0x{flags:02x} "
                f"seq={seq_id} "
                f"size={packet_size} "
                f"objs={object_count}  "
                f"hdr: {hex_dump}"
            )

        return header + payload

    # =====================================================
    # SEND PACKET (non-blocking)
    # =====================================================

    def send_packet(
        self,
        objects_data,
        packet_type=0x01,
        flags=0x00,
        version=None
    ):

        try:

            packet = self._build_packet(
                objects_data,
                packet_type=packet_type,
                flags=flags,
                version=(
                    version if version
                    is not None
                    else LIVE_SYNC_VERSION_V4
                )
            )

        except Exception as e:

            self.last_error = (
                f"Packet build failed: {e}"
            )
            self.last_error_severity = "CRITICAL"

            print(
                "[LiveSync] CRITICAL: "
                f"Packet build failed: {e}"
            )

            return

        try:

            self._send_queue.put_nowait(
                packet
            )

            # High-water warning: log if queue exceeds 75% capacity
            qdepth = self._send_queue.qsize()
            if qdepth >= 192:
                _now = time.time()
                if not hasattr(
                    self,
                    "_last_queue_high_water_log"
                ) or _now - self._last_queue_high_water_log > 5.0:
                    self._last_queue_high_water_log = _now
                    print(
                        f"[SYNC] Warning: send queue at "
                        f"{qdepth}/256 ({qdepth*100//256}% full)"
                    )

            if _network_verbose:
                print(
                    f"[SYNC-DBG] 3 Enqueued: {len(packet)} bytes",
                    flush=True
                )

        except queue.Full:

            self.last_error = "Send queue full"

            self._runtime_stats["dropped_packets"] += 1

            # Log cooldown: at most once per 5s
            _now = time.time()

            if not hasattr(
                self,
                "_last_queue_full_log"
            ) or _now - self._last_queue_full_log > 5.0:

                self._last_queue_full_log = _now

                print(
                    "[SYNC-DBG] 3 Enqueue FAILED: "
                    f"queue full ({self._runtime_stats['dropped_packets']} dropped)"
                )


# =========================================================
# PHASE 9 STAGE 3B — DISCOVERY SCAN
# =========================================================

DISCOVERY_DEFAULT_PORT = 57000
DISCOVERY_DEFAULT_TIMEOUT = 1.0
DISCOVERY_DEFAULT_CANDIDATES = ["127.0.0.1", "localhost"]

_discovery_results = []
_discovery_lock = threading.Lock()
_discovery_running = False
_discovery_total_candidates = 0
_discovery_completed_candidates = 0

# Configured host/port (used by discover_servers and apply_discovery_result)
_host = "127.0.0.1"
_port = DISCOVERY_DEFAULT_PORT


def discover_servers(candidates=None, port=DISCOVERY_DEFAULT_PORT, timeout=DISCOVERY_DEFAULT_TIMEOUT):
    """Probe candidate hosts by TCP connect.

    Synchronous — blocks for up to len(candidates) * timeout seconds.
    Candidates default: [127.0.0.1, localhost] + configured host if present.

    Returns list of dicts:
        {host, port, success(bool), error(str or None)}
    """
    global _discovery_results, _discovery_running, _discovery_total_candidates, _discovery_completed_candidates

    if candidates is None:
        candidates = list(DISCOVERY_DEFAULT_CANDIDATES)
        global _host
        if _host and _host not in candidates:
            candidates.append(_host)

    with _discovery_lock:
        _discovery_results = []
        _discovery_running = True
        _discovery_total_candidates = len(candidates)
        _discovery_completed_candidates = 0

    print(
        f"[DISCOVERY][START] probing {len(candidates)} candidate(s): {candidates}"
    )

    results = []
    for host in candidates:
        result = {"host": host, "port": port, "success": False, "error": None}
        print(f"[DISCOVERY][PROBE] {host}:{port} (timeout={timeout}s)")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((host, port))
            sock.close()
            result["success"] = True
            print(f"[DISCOVERY][FOUND] {host}:{port}")
        except socket.timeout:
            result["error"] = "timeout"
            print(f"[DISCOVERY][MISS] {host}:{port} (timeout)")
        except ConnectionRefusedError:
            result["error"] = "connection refused"
            print(f"[DISCOVERY][MISS] {host}:{port} (refused)")
        except OSError as e:
            result["error"] = str(e)
            print(f"[DISCOVERY][MISS] {host}:{port} ({e})")
        except Exception as e:
            result["error"] = str(e)
            print(f"[DISCOVERY][MISS] {host}:{port} ({e})")
        finally:
            try:
                sock.close()
            except Exception:
                pass
        results.append(result)
        with _discovery_lock:
            _discovery_completed_candidates += 1

    with _discovery_lock:
        _discovery_results = list(results)
        _discovery_running = False

    found = sum(1 for r in results if r["success"])
    print(
        f"[DISCOVERY][DONE] found {found}/{len(results)}"
    )
    return results


def get_discovery_results():
    """Returns last discovery scan results (thread-safe)."""
    with _discovery_lock:
        return list(_discovery_results)


def is_discovery_in_progress():
    """Returns True if an async discovery scan is in progress."""
    with _discovery_lock:
        return _discovery_running


def get_discovery_progress():
    """Returns (completed, total) for current/previous scan."""
    with _discovery_lock:
        return _discovery_completed_candidates, _discovery_total_candidates


def get_best_discovery_result():
    """Returns the first successful discovery result, or None."""
    results = get_discovery_results()
    for r in results:
        if r["success"]:
            return dict(r)
    return None


def apply_discovery_result(index=0):
    """Apply a discovery result to _host and _port globals.

    Args:
        index: Index into the successful results list (default 0).
               If index is out of range, does nothing and returns False.

    Returns:
        True if applied, False if no result at that index.
    """
    global _host, _port
    results = [r for r in get_discovery_results() if r["success"]]
    if index < 0 or index >= len(results):
        return False
    result = results[index]
    _host = result["host"]
    _port = result["port"]
    return True


# =========================================================
# GLOBAL CLIENT
# =========================================================

_client = None


# =========================================================
# PUBLIC API
# =========================================================

def is_connected():

    global _client

    return (
        _client is not None and
        _client.connected
    )


def get_last_error():

    global _client

    if _client is None:

        return "Not initialized"

    # Sync to runtime_stats
    _client._runtime_stats["last_error"] = (
        _client.last_error
    )

    return _client.last_error


def get_last_error_severity():

    global _client

    if _client is None:

        return "INFO"

    # Sync to runtime_stats
    _client._runtime_stats["last_error_severity"] = (
        _client.last_error_severity
    )

    return _client.last_error_severity


def get_status_detail():

    global _client

    if _client is None:

        return "Not started"

    return _client._status_detail


def check_reconnected():

    global _client

    if _client is None:

        return False

    with _client._lock:

        val = _client.reconnected

        _client.reconnected = False

        return val


def connect(
    host="127.0.0.1",
    port=5000
):

    global _client

    if _client is None:

        _client = LiveSyncClient(
            host=host,
            port=port
        )

        init_transport(_client)

    elif not _client.connected:

        _client.reconnect()


def disconnect():

    global _client

    if _client:

        _client.stop()

        _client = None


def send_objects(
    objects_data,
    packet_type=0x01,
    flags=0x00,
    version=None
):

    global _client

    if _client is None:

        connect()

    if _client:

        _client.send_packet(
            objects_data,
            packet_type,
            flags,
            version
        )

def send_snapshot(snapshot):

    objects_data = []

    for obj in snapshot["objects"]:

        objects_data.append(
            obj["binary"]
        )

    send_objects(
        objects_data
    )


def send_sequencer_op(payload_bytes):
    """Send a PT_SequencerOp packet with proper protocol header wrapping.

    payload_bytes: already-serialized packet (FSequencerOpHeader + opcode payload).
    Returns True on success, False on failure.
    """

    global _client
    global _sequencer_op_sequence
    global _sequencer_op_packets_sent

    if _client is None:

        return False

    try:

        # Build the protocol header wrapping the sequencer op payload
        packet = _client._build_packet(
            objects_data=[payload_bytes],
            packet_type=PT_SequencerOp,
            version=LIVE_SYNC_VERSION_V4,
        )

        _client._send_queue.put_nowait(packet)

        _sequencer_op_sequence += 1
        _sequencer_op_packets_sent += 1

        print(
            f"[SequencerOp] sent seq={_sequencer_op_sequence} "
            f"payload_len={len(payload_bytes)} total={len(packet)}"
        )

        return True

    except queue.Full:

        print("[SequencerOp] ERROR: send queue full")
        return False


def get_queue_depth():

    global _client

    if _client is None:

        return 0

    depth = _client._send_queue.qsize()

    _client._runtime_stats["queue_depth"] = (
        depth
    )

    return depth


def get_reconnect_count():

    global _client

    if _client is None:

        return 0

    return _client._runtime_stats.get(
        "reconnect_count", 0
    )


def get_runtime_stats():

    global _client

    if _client is None:

        return {}

    # Snapshot live values into stats dict
    get_queue_depth()

    stats = dict(
        _client._runtime_stats
    )

    # Overlay module-level playback counters (incremented by sync.py caller)
    stats["playback_packets_sent"] = playback_packets_sent
    stats["playback_state_changes"] = playback_state_changes

    # Overlay module-level timeline counters (incremented by sync.py caller)
    stats["timeline_packets_sent"] = timeline_packets_sent
    stats["timeline_state_changes"] = timeline_state_changes

    if stats["start_time"] > 0.0:

        stats["uptime"] = (
            time.time() -
            stats["start_time"]
        )

    return stats


def set_critical_error(message):

    global _client

    if _client is None:

        return

    _client.last_error = message
    _client.last_error_severity = "CRITICAL"

    _client._runtime_stats["last_error"] = message
    _client._runtime_stats["last_error_severity"] = "CRITICAL"

    print(
        f"[LiveSync] CRITICAL: {message}"
    )
