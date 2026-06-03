import socket
import struct
import sys
import threading
import queue
import time


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
    for size in (24, 22, 80, 81, 16, 33, 28, 28, 4, 4):
        h = _fnv(h, size)
    for pt in (0x01, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x11, 0x12, 0x13, 0x14, 0x15):
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

# Phase 9: Capability negotiation (announce/response)
PT_CapabilityAnnounce  = 0x11  # Phase 9: capability bitmask from Blender to UE
PT_CapabilityResponse  = 0x12  # Phase 9: capability bitmask from UE to Blender

# Capability payload sizes (uint32 each)
CAPABILITY_ANNOUNCE_PAYLOAD_SIZE  = 4
CAPABILITY_RESPONSE_PAYLOAD_SIZE  = 4

# =========================================================
# CAPABILITY BITS (Phase 9, wired in capability announce/response)
# =========================================================

CAP_SUPPORTS_ACTIVE_CAMERA_SYNC = 0x40  # Bit 6: PT_ActiveCamera (0x15) supported

# Local capabilities bitmask — sent to UE during capability announce.
_local_capabilities = CAP_SUPPORTS_ACTIVE_CAMERA_SYNC

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


def serialize_material_slots(guid_obj, slots):
    """Serialize material slot data for one object into PT_Material wire format.

    Args:
        guid_obj: uuid.UUID of the target object.
        slots: dict mapping slot_index -> (material_low, material_high)

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

NULL_CAMERA_GUID = b'\x00' * 16

def serialize_active_camera(guid_bytes, sequence, timestamp):
    """Serialize active camera payload into fixed-size 28-byte payload.

    Payload layout:
      [0-15]  guid      bytes   — 16-byte camera object GUID (all-zero = no active camera)
      [16-19] sequence  uint32 LE — monotonic global counter
      [20-27] timestamp double LE — time.time() at detection

    guid_bytes: 16 bytes from UUID(...).bytes, or NULL_CAMERA_GUID for no camera.
    Returns bytes of length ACTIVE_CAMERA_PAYLOAD_SIZE.
    """
    return struct.pack(
        "<16sId",
        guid_bytes[:16].ljust(16, b'\x00'),
        sequence & 0xFFFFFFFF,
        timestamp
    )


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
# GUID PACKING HELPER (shared by collection serializers)
# =========================================================

def _pack_guid(guid_obj):
    """Pack a GUID into 16 bytes (4 × uint32 LE)."""
    d_a = guid_obj.time_low
    d_b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
    d_c = (guid_obj.clock_seq_hi_variant << 24
           | guid_obj.clock_seq_low << 16
           | (guid_obj.node >> 32) & 0xFFFF)
    d_d = guid_obj.node & 0xFFFFFFFF
    return struct.pack("<IIII", d_a, d_b, d_c, d_d)


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
