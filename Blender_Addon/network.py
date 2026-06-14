import os
import socket
import struct
import sys
import threading
import queue
import time


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


def _send_announce():
    """Send a PT_CapabilityAnnounce packet with _local_capabilities.

    Designed to be called after connect/reconnect. Returns True if the
    packet was enqueued successfully, False if no client is connected.
    """
    global _client
    if _client is None or not _client.connected:
        return False
    payload = struct.pack('<I', _local_capabilities)
    _client.send_packet(payload, packet_type=PT_CapabilityAnnounce)
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


# Phase 7E Stage 7-8: keyframe sync state globals
_keyframe_sequence = 0
_keyframe_packets_sent = 0
_keyframes_sent = 0
_animated_objects_scanned = 0


# Primitive type constants (1 byte, appended to CREATE packets only)
PRIMITIVE_CUBE = 0x00
PRIMITIVE_SPHERE = 0x01
PRIMITIVE_CYLINDER = 0x02
PRIMITIVE_PLANE = 0x03
PRIMITIVE_EMPTY = 0x04

# Packet type constants (beyond V3 base)
PT_BeginSnapshot = 0x09
PT_EndSnapshot = 0x0A
PT_AssetDef = 0x08
PT_Visibility = 0x0B
PT_Rename = 0x0C
PT_Hierarchy = 0x0D
PT_Delete_V5 = 0x0E  # Phase 6E: lifecycle/delete (V5+, 28-byte fixed payload)
PT_Collection = 0x0F  # Phase 6F: collection/group replication (metadata-only)
PT_Material = 0x05   # Phase 7B: material slot identity
PT_Mesh = 0x06       # Phase 7C: mesh geometry chunk
PT_Timeline = 0x13   # Phase 7B: timeline/playhead frame sync
PT_PlaybackState = 0x14  # Phase 7C: playback state (play/pause/stop/loop)
PT_ActiveCamera = 0x15  # Phase 7D: active camera selection (GUID-only, no params)

# Phase 7C Stage 3A.1: FBX Mesh Handoff Import (PT_FBXImportRequest = 0x16)
PT_FBXImportRequest = 0x16  # FBX mesh import request (fixed 680-byte payload)

# Phase 7E Stage 7: Keyframe replication (PT_Keyframe = 0x17)
PT_Keyframe = 0x17  # Keyframe replication (fixed header + repeated entries)

# Phase 7E: Sequencer ops (PT_SequencerOp = 0x18)
# Wire format: fixed-size 16-byte common header + optional opcode payload.
PT_SequencerOp = 0x18  # Sequencer operation (discrete event, NOT state stream)

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


def serialize_fbx_import_request(
    guid_obj, fbx_path, object_name,
    vert_count, tri_count, mat_slot_count,
    timestamp, geometry_hash=0, version=1
):
    """Serialize a PT_FBXImportRequest (0x16) fixed-size payload.

    Wire format (688 bytes):
        ObjectGUID  : 16 bytes (4 × uint32 LE)
        Version     : uint32 LE
        FbxPath     : 512 bytes, UTF-8 null-padded
        ObjectName  : 128 bytes, UTF-8 null-padded
        VertCount   : uint32 LE
        TriCount    : uint32 LE
        MatSlotCount: uint32 LE
        Timestamp   : double LE
        GeometryHash: uint64 LE  — geometry content signature (Phase 10J.5F)

    Backward compatible: geometry_hash=0 indicates old/unknown protocol.
    Old 680-byte payloads are accepted on the UE side (GeometryHash = 0).

    Args:
        guid_obj: UUID object for the object GUID.
        fbx_path: Absolute filesystem path to the exported .fbx file.
        object_name: Display name for the object.
        vert_count: Vertex count in the exported mesh.
        tri_count: Triangle count in the exported mesh.
        mat_slot_count: Number of material slots.
        timestamp: Unix timestamp (seconds since epoch) as float.
        geometry_hash: 64-bit geometry content hash (0 = unknown/old protocol).
        version: Payload format version (default 1).

    Returns:
        bytes: 688-byte fixed-size payload.
    """
    guid_bytes = pack_ue_fguid(guid_obj)
    fbx_path_bytes = fbx_path.encode('utf-8')
    name_bytes = object_name.encode('utf-8')
    fmt = '<16sI512s128sIIIdQ'
    return struct.pack(
        fmt,
        guid_bytes,
        version,
        fbx_path_bytes.ljust(512, b'\x00')[:512],
        name_bytes.ljust(128, b'\x00')[:128],
        vert_count,
        tri_count,
        mat_slot_count,
        timestamp,
        geometry_hash,
    )

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

# Local capabilities bitmask — sent to UE during capability announce.
_local_capabilities = CAP_SUPPORTS_TIMELINE_SYNC | CAP_SUPPORTS_KEYFRAME_REPLICATION | CAP_SUPPORTS_ACTIVE_CAMERA_SYNC | CAP_SUPPORTS_SEQUENCER_OPS

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

    # Phase 10J.5L: log extraction to debug file
    _append_blender_debug_log(
        f"[MAT][EXTRACT] mat={mat_name} "
        f"use_nodes={material.use_nodes if material else 'N/A'} "
        f"source={source} "
        f"color=({props.get('BaseColorR', 0):.3f},{props.get('BaseColorG', 0):.3f},{props.get('BaseColorB', 0):.3f},{props.get('Alpha', 1):.3f}) "
        f"roughness={props.get('Roughness', 0.5):.3f} "
        f"metallic={props.get('Metallic', 0):.3f}"
    )

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


def extract_texture_maps_for_slot(material):
    """Extract texture map references from a Blender material node tree.

    Phase 10K.1: diagnostic-only. Supports direct links from Image Texture
    nodes to Principled BSDF inputs. Does not traverse complex node graphs.

    Supported channels:
        BaseColor: Image Texture Color → Principled Base Color
        Roughness: Image Texture Color/Non-Color → Principled Roughness
        Metallic:  Image Texture Color/Non-Color → Principled Metallic
        Alpha:     Image Texture Alpha → Principled Alpha
        Normal:    Image Texture Color → Normal Map Color → Principled Normal

    For each channel, only the first connected Image Texture is reported.
    Procedural nodes are not evaluated.

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

    # Map Principled input names to MTEX channels
    target_sockets = {}
    for sock_name, channel in (("Base Color", MTEX_CHANNEL_BASECOLOR),
                                ("Roughness", MTEX_CHANNEL_ROUGHNESS),
                                ("Metallic", MTEX_CHANNEL_METALLIC),
                                ("Alpha", MTEX_CHANNEL_ALPHA),
                                ("Normal", MTEX_CHANNEL_NORMAL)):
        sock = principled.inputs.get(sock_name)
        if sock is not None and sock.is_linked:
            target_sockets[sock_name] = channel

    if not target_sockets:
        return []

    results = []

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

        # Must be an Image Texture node
        if getattr(from_node, "type", None) != "TEX_IMAGE":
            continue

        image = getattr(from_node, "image", None)
        if image is None:
            continue

        filepath = getattr(image, "filepath", "") or ""
        image_name = getattr(image, "name", "") or ""
        is_packed = getattr(image, "packed_file", None) is not None

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

        results.append((channel, filepath, image_name, flags))

        _append_blender_debug_log(
            f"[MTEX][EXTRACT] slot=NA channel={channel} "
            f"image={image_name} path={filepath[:80] if filepath else '(none)'} "
            f"packed={int(is_packed)} cs_flags={flags}"
        )

    return results


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

    # GUID (4 × uint32 LE)
    a = guid_obj.time_low
    b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
    c = (guid_obj.clock_seq_hi_variant << 24) | (guid_obj.clock_seq_low << 16) | ((guid_obj.node >> 32) & 0xFFFF)
    d = guid_obj.node & 0xFFFFFFFF
    payload.extend(struct.pack("<IIII", a, b, c, d))

    # Slot count (clamped to MAX_MATERIAL_SLOTS)
    slot_count = min(len(slots), MAX_MATERIAL_SLOTS)
    payload.extend(struct.pack("<B", slot_count))

    # Per-slot data: SlotIndex(1) + MaterialLow(8) + MaterialHigh(8)
    for slot_index in range(slot_count):
        low, high = slots.get(slot_index, (0, 0))
        payload.extend(struct.pack("<B", slot_index & 0xFF))
        payload.extend(struct.pack("<QQ", low & 0xFFFFFFFFFFFFFFFF, high & 0xFFFFFFFFFFFFFFFF))

    # MATX extension block (optional)
    if properties is not None and properties:
        ext_slot_count = min(len(properties), MAX_MATERIAL_SLOTS)
        payload.extend(struct.pack("<I", MATX_MAGIC))
        payload.extend(struct.pack("<B", MATX_VERSION))
        payload.extend(struct.pack("<B", ext_slot_count))
        for slot_index in range(ext_slot_count):
            p = properties.get(slot_index)
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
                flat_records.append((slot_index, channel, filepath, image_name, flags))

        if flat_records:
            rec_count = len(flat_records)
            payload.extend(struct.pack("<I", MTEX_MAGIC))
            payload.extend(struct.pack("<B", MTEX_VERSION))
            payload.extend(struct.pack("<B", rec_count))

            for slot_index, channel, filepath, image_name, flags in flat_records:
                # Clamp string lengths
                path_bytes = filepath.encode("utf-8", errors="replace")
                if len(path_bytes) > MTEX_MAX_PATH_LEN:
                    path_bytes = path_bytes[:MTEX_MAX_PATH_LEN]
                name_bytes = image_name.encode("utf-8", errors="replace")
                if len(name_bytes) > MTEX_MAX_IMAGE_NAME_LEN:
                    name_bytes = name_bytes[:MTEX_MAX_IMAGE_NAME_LEN]

                path_len = len(path_bytes)
                name_len = len(name_bytes)

                payload.extend(struct.pack("<B", slot_index & 0xFF))
                payload.extend(struct.pack("<B", channel & 0xFF))
                payload.extend(struct.pack("<B", flags & 0xFF))
                payload.extend(struct.pack("<H", path_len))
                payload.extend(path_bytes)
                payload.extend(struct.pack("<B", name_len))
                payload.extend(name_bytes)

            _append_blender_debug_log(
                f"[MTEX][SEND] records={rec_count} bytes={len(payload)}"
            )

    return bytes(payload)


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


def serialize_delete_v3(guid_obj):

    payload = bytearray()

    d_a = guid_obj.time_low
    d_b = (
        guid_obj.time_mid << 16
    ) | guid_obj.time_hi_version
    d_c = (
        guid_obj.clock_seq_hi_variant << 24
    ) | (
        guid_obj.clock_seq_low << 16
    ) | (
        (guid_obj.node >> 32) & 0xFFFF
    )
    d_d = guid_obj.node & 0xFFFFFFFF

    payload.extend(struct.pack(
        "<IIII",
        d_a, d_b, d_c, d_d
    ))

    return payload


# =========================================================
# SERIALIZE RENAME (Phase 6 — Semantic Event)
# =========================================================
# Wire format:
#   GUID (16 bytes) + old_name_length (2) + old_name (N)
#   + new_name_length (2) + new_name (M) + sequence (4) + timestamp (8)
#
# This is a semantic editor event, NOT a state stream packet.
# See Docs/Architecture/19-phase6-vertical-slice-rename.md §4
# =========================================================

_rename_sequences = {}

def serialize_rename(guid_obj, old_name, new_name):

    payload = bytearray()

    # GUID
    d_a = guid_obj.time_low
    d_b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
    d_c = (guid_obj.clock_seq_hi_variant << 24
           | guid_obj.clock_seq_low << 16
           | (guid_obj.node >> 32) & 0xFFFF)
    d_d = guid_obj.node & 0xFFFFFFFF

    payload.extend(struct.pack("<IIII", d_a, d_b, d_c, d_d))

    # Old name
    old_bytes = old_name.encode("utf-8")
    payload.extend(struct.pack("<H", len(old_bytes)))
    payload.extend(old_bytes)

    # New name
    new_bytes = new_name.encode("utf-8")
    payload.extend(struct.pack("<H", len(new_bytes)))
    payload.extend(new_bytes)

    # Monotonic sequence per GUID (replay dedup)
    guid_key = str(guid_obj)
    seq = _rename_sequences.get(guid_key, 0) + 1
    _rename_sequences[guid_key] = seq
    payload.extend(struct.pack("<I", seq))

    # Timestamp
    payload.extend(struct.pack("<d", time.time()))

    return payload


# =========================================================
# VISIBILITY SERIALIZATION (Phase 6, PT_Visibility = 0x0B)
# =========================================================
# Fixed-size wire format per object (29 bytes):
#   + GUID(16) + bHidden(1) + sequence(4) + timestamp(8)
#
# This is a discrete semantic editor event, NOT a state stream.
# See Docs/Architecture/21-phase6-vertical-slice-visibility.md §2
# =========================================================

_visibility_sequences = {}

def serialize_visibility(guid_obj, b_hidden):

    payload = bytearray()

    # GUID
    d_a = guid_obj.time_low
    d_b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
    d_c = (guid_obj.clock_seq_hi_variant << 24
           | guid_obj.clock_seq_low << 16
           | (guid_obj.node >> 32) & 0xFFFF)
    d_d = guid_obj.node & 0xFFFFFFFF

    payload.extend(struct.pack("<IIII", d_a, d_b, d_c, d_d))

    # bHidden (uint8: 0=visible, 1=hidden)
    payload.extend(struct.pack("<B", 1 if b_hidden else 0))

    # Monotonic sequence per GUID (replay dedup)
    guid_key = str(guid_obj)
    seq = _visibility_sequences.get(guid_key, 0) + 1
    _visibility_sequences[guid_key] = seq
    payload.extend(struct.pack("<I", seq))

    # Timestamp
    payload.extend(struct.pack("<d", time.time()))

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

                if _network_verbose:
                    print(
                        f"[SYNC-DBG] 4 Sender dequeued: {data_len} bytes",
                        flush=True
                    )

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
            _send_announce()

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

        # Phase 6: reset rename sequence tracker on disconnect
        global _rename_sequences
        if _rename_sequences:
            _rename_sequences.clear()
            print("[RENAME] Sequence tracker cleared on disconnect")

        # Phase 6: reset visibility sequence tracker on disconnect
        global _visibility_sequences
        if _visibility_sequences:
            _visibility_sequences.clear()
            print("[VISIBILITY] Sequence tracker cleared on disconnect")

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

        object_count = len(objects_data)

        for obj in objects_data:

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
