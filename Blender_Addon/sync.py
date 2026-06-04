import sys
import bpy
import hashlib
import struct
import time
import traceback
import uuid

from bpy.app.handlers import persistent

from mathutils import Matrix

from uuid import UUID

try:
    from .network import (
        connect,
        disconnect,
        send_objects,
        send_snapshot,
        serialize_object,
        serialize_object_v3,
        serialize_delete_v3,
        serialize_rename,
        serialize_hierarchy,
        serialize_visibility,
        serialize_delete,
        is_connected,
        get_last_error,
        get_last_error_severity,
        get_status_detail,
        check_reconnected,
        get_queue_depth,
        get_reconnect_count,
        get_runtime_stats,
        set_critical_error,
        set_verbose as _network_set_verbose,
        PRIMITIVE_CUBE,
        PRIMITIVE_SPHERE,
        PRIMITIVE_CYLINDER,
        PRIMITIVE_PLANE,
        PRIMITIVE_EMPTY,
        PT_BeginSnapshot,
        PT_EndSnapshot,
        PT_AssetDef,
        PT_Rename,
        PT_Hierarchy,
        PT_Delete_V5,
        PT_Visibility,
        PT_Collection,
        LIVE_SYNC_VERSION_V5,
        get_mesh_identity_hash,
        get_material_identity_hash,
        serialize_asset_identity,
        serialize_collection_identity,
        serialize_collection_membership,
        serialize_material_slots,
        COLLECTION_OP_ADD,
        COLLECTION_OP_REMOVE,
        COLLECTION_OP_MOVE,
        COLLECTION_OP_CLEAR,
        COLLECTION_OP_COLLECTION_CREATE,
        COLLECTION_OP_COLLECTION_DELETE,
        COLLECTION_OP_COLLECTION_REPARENT,
        COLLECTION_PACKET_VERSION_V1,
        start_collection_replay_recording,
        clear_collection_replay_stream,
        compute_full_snapshot_hash,
        compute_collection_membership_hash,
        make_collection_subheader,
        start_world_replay_recording,
        clear_world_replay_stream,
        record_world_entry,
        set_world_replay_enabled,
        PT_Mesh,
        extract_evaluated_mesh_data,
        compute_geometry_version_hash,
        serialize_mesh_chunk,
        MESH_CHUNK_FLAG_FIRST_CHUNK,
        MESH_CHUNK_FLAG_LAST_CHUNK,
        PT_PlaybackState,
        serialize_playback_state,
        is_playback_effective,
        set_playback_enabled,
        PLAYBACK_PLAY,
        PLAYBACK_PAUSE,
        PLAYBACK_STOP,
        _playback_sequence as _net_playback_sequence,
        playback_packets_sent as _net_playback_packets_sent,
        playback_state_changes as _net_playback_state_changes,
        PT_Timeline,
        serialize_timeline,
        TIMELINE_PAYLOAD_SIZE,
        is_timeline_effective,
        set_timeline_enabled,
        PT_ActiveCamera,
        serialize_active_camera,
        ACTIVE_CAMERA_PAYLOAD_SIZE,
        NULL_CAMERA_GUID,
        is_active_camera_effective,
        set_active_camera_enabled,
        _active_camera_enabled,
        PT_SequencerOp,
        is_sequencer_ops_effective,
        set_sequencer_op_enabled,
        SEQUENCER_OP_COMMON_HEADER_SIZE,
        SEQUENCER_OP_CREATE_SEQUENCE,
        SEQUENCER_OP_ADD_POSSESSABLE,
        SEQUENCER_OP_REMOVE_POSSESSABLE,
        SEQUENCER_OP_ADD_CAMERA_CUT,
        SEQUENCER_OP_CLEAR_SEQUENCE,
        SEQUENCER_OP_SET_FRAME_RANGE,
        SEQUENCER_OP_MIN_OPCODE,
        SEQUENCER_OP_MAX_OPCODE,
        SEQUENCER_OP_PAYLOAD_SIZES,
        SEQUENCER_OP_CREATE_SEQUENCE_PAYLOAD_SIZE,
        SEQUENCER_OP_ADD_POSSESSABLE_PAYLOAD_SIZE,
        SEQUENCER_OP_REMOVE_POSSESSABLE_PAYLOAD_SIZE,
        SEQUENCER_OP_ADD_CAMERA_CUT_PAYLOAD_SIZE,
        SEQUENCER_OP_CLEAR_SEQUENCE_PAYLOAD_SIZE,
        SEQUENCER_OP_SET_FRAME_RANGE_PAYLOAD_SIZE,
        serialize_sequencer_op_create_sequence,
        serialize_sequencer_op_add_possessable,
        serialize_sequencer_op_remove_possessable,
        serialize_sequencer_op_add_camera_cut,
        serialize_sequencer_op_clear_sequence,
        serialize_sequencer_op_set_frame_range,
        _sequencer_op_sequence as _net_sequencer_op_sequence,
        _sequencer_op_packets_sent as _net_sequencer_op_packets_sent,
        _sequencer_op_state_changes as _net_sequencer_op_state_changes,
        PT_Keyframe,
        is_keyframe_effective,
        set_keyframe_enabled,
        serialize_keyframe,
        KEYFRAME_HEADER_SIZE,
        KEYFRAME_ENTRY_SIZE,
        KEYFRAME_MAX_KEYS,
        KEYFRAME_MAX_CHANNEL,
        KEYFRAME_CHANNEL_VISIBILITY_VIEWPORT,
        KEYFRAME_CHANNEL_VISIBILITY_RENDER,
        _keyframe_sequence as _net_keyframe_sequence,
        _keyframe_packets_sent as _net_keyframe_packets_sent,
        _keyframes_sent as _net_keyframes_sent,
        _animated_objects_scanned as _net_animated_objects_scanned,
    )
except ImportError:
    from network import (
        connect,
        disconnect,
        send_objects,
        send_snapshot,
        serialize_object,
        serialize_object_v3,
        serialize_delete_v3,
        serialize_rename,
        serialize_hierarchy,
        serialize_visibility,
        serialize_delete,
        is_connected,
        get_last_error,
        get_last_error_severity,
        get_status_detail,
        check_reconnected,
        get_queue_depth,
        get_reconnect_count,
        get_runtime_stats,
        set_critical_error,
        set_verbose as _network_set_verbose,
        PRIMITIVE_CUBE,
        PRIMITIVE_SPHERE,
        PRIMITIVE_CYLINDER,
        PRIMITIVE_PLANE,
        PRIMITIVE_EMPTY,
        PT_BeginSnapshot,
        PT_EndSnapshot,
        PT_AssetDef,
        PT_Rename,
        PT_Hierarchy,
        PT_Delete_V5,
        PT_Visibility,
        PT_Collection,
        PT_Material,
        LIVE_SYNC_VERSION_V5,
        get_mesh_identity_hash,
        get_material_identity_hash,
        serialize_asset_identity,
        serialize_collection_identity,
        serialize_collection_membership,
        serialize_material_slots,
        COLLECTION_OP_ADD,
        COLLECTION_OP_REMOVE,
        COLLECTION_OP_MOVE,
        COLLECTION_OP_CLEAR,
        COLLECTION_OP_COLLECTION_CREATE,
        COLLECTION_OP_COLLECTION_DELETE,
        COLLECTION_OP_COLLECTION_REPARENT,
        COLLECTION_PACKET_VERSION_V1,
        start_collection_replay_recording,
        clear_collection_replay_stream,
        compute_full_snapshot_hash,
        compute_collection_membership_hash,
        make_collection_subheader,
        PT_Mesh,
        extract_evaluated_mesh_data,
        compute_geometry_version_hash,
        serialize_mesh_chunk,
        MESH_CHUNK_FLAG_FIRST_CHUNK,
        MESH_CHUNK_FLAG_LAST_CHUNK,
        PT_PlaybackState,
        serialize_playback_state,
        is_playback_effective,
        set_playback_enabled,
        PLAYBACK_PLAY,
        PLAYBACK_PAUSE,
        PLAYBACK_STOP,
        _playback_sequence as _net_playback_sequence,
        playback_packets_sent as _net_playback_packets_sent,
        playback_state_changes as _net_playback_state_changes,
        PT_Timeline,
        serialize_timeline,
        TIMELINE_PAYLOAD_SIZE,
        is_timeline_effective,
        set_timeline_enabled,
        PT_ActiveCamera,
        serialize_active_camera,
        ACTIVE_CAMERA_PAYLOAD_SIZE,
        NULL_CAMERA_GUID,
        is_active_camera_effective,
        set_active_camera_enabled,
        _active_camera_enabled,
        PT_Keyframe,
        is_keyframe_effective,
        set_keyframe_enabled,
        serialize_keyframe,
        KEYFRAME_HEADER_SIZE,
        KEYFRAME_ENTRY_SIZE,
        KEYFRAME_MAX_KEYS,
        KEYFRAME_MAX_CHANNEL,
        KEYFRAME_CHANNEL_VISIBILITY_VIEWPORT,
        KEYFRAME_CHANNEL_VISIBILITY_RENDER,
        _keyframe_sequence as _net_keyframe_sequence,
        _keyframe_packets_sent as _net_keyframe_packets_sent,
        _keyframes_sent as _net_keyframes_sent,
        _animated_objects_scanned as _net_animated_objects_scanned,
    )


# =========================================================
# GLOBAL STATE
# =========================================================

timer_running = False

last_sent_transforms = {}

# Phase 5D: Per-GUID last mesh identity for change detection
# Stores (identity_low, identity_high, mesh_name)
_last_mesh_identity = {}

# Phase 7B: Per-GUID last material slot identities for change detection
# Maps guid -> {slot_index: (material_identity_low, material_identity_high)}
_last_material_identity = {}

# Phase 7C: Per-GUID last geometry version hash for change detection
# Maps guid -> SHA-256 hex string of evaluated mesh geometry.
# Cleared on start_sync, stop_sync, and object delete.
# None means "not yet evaluated".
_last_geometry_version = {}

# Phase 6: Per-GUID last object name for rename detection
_last_object_names = {}

# Phase 6: Per-GUID last visibility state for visibility toggle detection
_last_visibility_state = {}

# Phase 6D: Per-GUID last parent GUID for hierarchy attach/detach/reparent detection
_last_parent_guid = {}

# Phase 6E: Set of known GUIDs from end of previous tick.
# Used for delete detection: GUIDs present in _known_guids but absent
# from tracked_objects at start of tick = Blender-deleted objects.
# Cleared on start_sync, stop_sync, and reconnect to prevent false emits.
_known_guids = set()

# Phase 6F: Per-collection last known member GUIDs for collection diff detection.
# Maps collection UUID string → set of member GUID UUID strings.
# Updated each tick; diff against current state emits PT_Collection packets.
_last_collection_state = {}

# Phase 6F: Anti-loop guard set — GUIDs of objects whose collection state
# was just updated by UE. These should not trigger a re-emission.
# Cleared at the start of each tick.
_collection_anti_loop_guids = set()

tracked_objects = {}

_timer_ref = None

_last_heartbeat_time = 0.0

_last_object_count = 0

_scan_counter = 0

_verbose_logging = False

_last_critical_error = ""

_sync_start_time = 0.0

# Phase 7C: local tracking of last-sent playback state for transition detection
_last_playback_state = None

# Phase 7B: timeline detection state machine
_last_timeline_state = None  # None=uninitialized, else tuple(fc, fs, fe, fps_n, fps_d)
_timeline_sequence = 0
_timeline_packets_sent = 0
_timeline_state_changes = 0

# Phase 7D Stage 2: active camera detection state machine
_last_active_camera_guid = None  # None=uninitialized, b''=reconnect, bytes=last sent
_active_camera_sequence = 0
_active_camera_packets_sent = 0
_active_camera_state_changes = 0

# Phase 7E: sequencer ops state (no detection yet — storage only)
_sequencer_op_sequence = 0
_sequencer_op_packets_sent = 0
_sequencer_op_state_changes = 0

# Phase 7E Stage 8: keyframe extraction state
_keyframe_sequence = 0
_keyframe_packets_sent = 0
_keyframes_sent = 0
_animated_objects_scanned = 0
# GUID → (action_name, action_hash) for duplicate suppression
_last_keyframe_action = {}

# Centralized runtime metrics (all diagnostics/UI should read from here)
_runtime_stats = {
    "tracked_objects": 0,
    "queue_depth": 0,
    "reconnect_count": 0,
    "uptime": 0.0,
    "last_send_time": 0.0,
    "last_error": "",
    "last_error_severity": "INFO",
    "dropped_packets": 0,
    "serialization_failures": 0,
    "packets_sent": 0,
    "bytes_sent": 0,
    "reconnect_escalated": False,
    "has_critical_error": False,
    "playback_packets_sent": 0,
    "playback_state_changes": 0,
    "timeline_packets_sent": 0,
    "timeline_state_changes": 0,
    "active_camera_packets_sent": 0,
    "active_camera_state_changes": 0,
    "sequencer_op_packets_sent": 0,
    "sequencer_op_state_changes": 0,
    "keyframe_packets_sent": 0,
    "keyframes_sent": 0,
    "animated_objects_scanned": 0,
    "last_heartbeat_time": 0.0,
    "heartbeat_interval": 5.0,
    "scan_interval": 300,
}

# Cached preferences (avoids RNA lookup every tick)
_runtime_config = {
    "threshold_location": 0.01,
    "threshold_rotation": 0.0001,
    "threshold_scale": 0.001,
    "heartbeat_interval": 5.0,
    "scan_interval": 300,
    "server_port": 57000,
    "verbose_logging": False,
    "default_primitive": 'CUBE',
}


# =========================================================
# PREFERENCES HELPER
# =========================================================

def _get_prefs():
    import bpy
    try:
        return bpy.context.preferences.addons[
            __package__
        ].preferences
    except Exception:
        return None


def _sync_runtime_config():

    prefs = _get_prefs()

    if prefs is None:
        return

    for key in list(
        _runtime_config.keys()
    ):
        _runtime_config[key] = (
            getattr(
                prefs, key,
                _runtime_config[key]
            )
        )


def _update_runtime_stats():

    global _sync_start_time

    # Sync from tracked_objects
    _runtime_stats["tracked_objects"] = (
        len(tracked_objects)
    )

    # Sync from network stats
    net_stats = get_runtime_stats()

    _runtime_stats["queue_depth"] = (
        net_stats.get(
            "queue_depth", 0
        )
    )

    _runtime_stats["reconnect_count"] = (
        net_stats.get(
            "reconnect_count", 0
        )
    )

    _runtime_stats["last_send_time"] = (
        net_stats.get(
            "last_send_time", 0.0
        )
    )

    _runtime_stats["dropped_packets"] = (
        net_stats.get(
            "dropped_packets", 0
        )
    )

    _runtime_stats["packets_sent"] = (
        net_stats.get(
            "packets_sent", 0
        )
    )

    _runtime_stats["bytes_sent"] = (
        net_stats.get(
            "bytes_sent", 0
        )
    )

    _runtime_stats["last_error"] = (
        net_stats.get(
            "last_error", ""
        )
    )

    _runtime_stats["last_error_severity"] = (
        net_stats.get(
            "last_error_severity", "INFO"
        )
    )

    # Local uptime
    if _sync_start_time > 0.0:

        _runtime_stats["uptime"] = (
            time.time() -
            _sync_start_time
        )
    else:

        _runtime_stats["uptime"] = 0.0

    # Heartbeat tracking
    _runtime_stats["last_heartbeat_time"] = (
        _last_heartbeat_time
    )

    _runtime_stats["heartbeat_interval"] = (
        _runtime_config.get(
            "heartbeat_interval", 5.0
        )
    )

    _runtime_stats["scan_interval"] = (
        _runtime_config.get(
            "scan_interval", 300
        )
    )

    # Reconnect escalation state
    _runtime_stats["reconnect_escalated"] = (
        _runtime_stats["reconnect_count"] > 5
    )

    # Critical error
    sev = get_last_error_severity()

    _runtime_stats["has_critical_error"] = (
        sev == "CRITICAL"
    )


def _get_threshold(key, default):
    # Prefer runtime_config cache (avoids RNA lookup every tick)
    val = _runtime_config.get(
        key
    )

    if val is not None:
        return val

    prefs = _get_prefs()

    if prefs is None:
        return default

    return getattr(
        prefs, key, default
    )


# =========================================================
# GUID SYSTEM
# =========================================================

def _compute_owner_hash(obj):

    datablock_name = (
        obj.data.name
        if obj.data else ""
    )

    # NOTE: obj.name is intentionally excluded.
    # Object renames are independent semantic operations handled by
    # the PT_Rename packet path. Including obj.name would cause
    # _reconcile_guids_on_load to regenerate the GUID on every rename,
    # triggering a delete+create cycle on the UE side and destroying
    # the authoritative rename label.
    raw = (
        f"{datablock_name}"
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:16]


def ensure_guid(obj):

    if "ue_guid" not in obj:

        obj["ue_guid"] = uuid.uuid4().hex

        obj["ue_guid_owner_hash"] = (
            _compute_owner_hash(obj)
        )

    return obj["ue_guid"]


def ensure_unique_guid(obj, tracked):

    guid = ensure_guid(obj)

    if guid in tracked and tracked[guid][0] != obj:

        old_guid = guid

        obj["ue_guid"] = uuid.uuid4().hex

        obj["ue_guid_owner_hash"] = (
            _compute_owner_hash(obj)
        )

        guid = obj["ue_guid"]

        if _verbose_logging:
            print(
                f"[GUID] Regenerated {old_guid}"
                f" -> {guid} for {obj.name}"
            )

    return guid


def _reconcile_guids_on_load():

    global tracked_objects

    reconciled = 0

    for guid in list(
        tracked_objects.keys()
    ):

        obj, guid_obj = (
            tracked_objects[guid]
        )

        try:
            _ = obj.name
        except ReferenceError:
            continue

        stored_hash = obj.get(
            "ue_guid_owner_hash"
        )

        if stored_hash is None:
            obj["ue_guid_owner_hash"] = (
                _compute_owner_hash(obj)
            )
            continue

        current_hash = _compute_owner_hash(obj)

        if current_hash != stored_hash:

            old_guid = guid

            obj["ue_guid"] = (
                uuid.uuid4().hex
            )

            obj["ue_guid_owner_hash"] = (
                _compute_owner_hash(obj)
            )

            new_guid = obj["ue_guid"]

            tracked_objects.pop(guid, None)

            tracked_objects[new_guid] = (
                obj,
                UUID(new_guid)
            )

            if _verbose_logging:
                print(
                    f"[GUID] Stale collision: "
                    f"{old_guid} -> {new_guid} "
                    f"for {obj.name}"
                )

            reconciled += 1

    if reconciled > 0:
        print(
            f"[GUID] Reconciled "
            f"{reconciled} stale GUID(s)"
        )


_PRIMITIVE_MAP = {
    'CUBE': PRIMITIVE_CUBE,
    'SPHERE': PRIMITIVE_SPHERE,
    'CYLINDER': PRIMITIVE_CYLINDER,
    'PLANE': PRIMITIVE_PLANE,
    'EMPTY': PRIMITIVE_EMPTY,
}


def _get_primitive_type():

    prim_str = _get_threshold(
        "default_primitive",
        'CUBE'
    )

    return _PRIMITIVE_MAP.get(
        prim_str,
        PRIMITIVE_CUBE
    )


def get_parent_guid(obj):

    if obj.parent and obj.parent.type == 'MESH':

        return ensure_guid(obj.parent)

    return None


# =========================================================
# COLLECTION GUID HELPER (Phase 6F)
# =========================================================
# Generates a deterministic UUID for a Blender collection based
# on its name, using UUID5 (SHA-1 based).
# Stable across sessions as long as the collection name doesn't
# change. The collection "GUID" is used as the collection
# identity in PT_Collection packets.
# =========================================================

import uuid as _uuid

_COLLECTION_NAMESPACE = _uuid.uuid5(_uuid.NAMESPACE_DNS, "uelivesync-collections")


def _get_collection_guid(collection):
    """Return a deterministic UUID for a Blender collection."""
    return _uuid.uuid5(_COLLECTION_NAMESPACE, collection.name)


def _get_collection_guid_str(collection):
    """Return a deterministic UUID hex string for a Blender collection."""
    return _get_collection_guid(collection).hex


def _get_parent_depth(guid, parent_map, depth_cache, max_depth=256):
    """Compute parent-chain depth for snapshot ordering (0 = root).

    Uses memoized depth_cache for O(N) total across all objects.
    Bounded at max_depth to prevent infinite loops from cycles.
    """
    if guid in depth_cache:
        return depth_cache[guid]
    visited = set()
    depth = 0
    cur = guid
    while cur in parent_map and parent_map[cur] is not None:
        if cur in visited:
            break
        visited.add(cur)
        depth += 1
        cur = parent_map[cur]
        if depth > max_depth:
            break
    depth_cache[guid] = depth
    return depth


# =========================================================
# TRANSFORM COMPARISON
# =========================================================

def transforms_different(a, b):

    if b is None:
        return True

    thr_loc = _get_threshold(
        "threshold_location",
        0.01
    )

    thr_rot = _get_threshold(
        "threshold_rotation",
        0.0001
    )

    thr_scl = _get_threshold(
        "threshold_scale",
        0.001
    )

    for i in range(3):

        if abs(
            a["location"][i] -
            b["location"][i]
        ) > thr_loc:

            return True

    for i in range(4):

        if abs(
            a["rotation"][i] -
            b["rotation"][i]
        ) > thr_rot:

            return True

    for i in range(3):

        if abs(
            a["scale"][i] -
            b["scale"][i]
        ) > thr_scl:

            return True

    return False


# =========================================================
# TRANSFORM EXTRACTION
# =========================================================

def get_transform(obj):

    has_parent = (
        obj.parent and
        obj.parent.get("ue_guid")
        in tracked_objects
    )

    if has_parent:

        mw = obj.matrix_local.copy()

    else:

        mw = obj.matrix_world.copy()

    conversion = Matrix((
        (1,  0, 0, 0),
        (0, -1, 0, 0),
        (0,  0, 1, 0),
        (0,  0, 0, 1)
    ))

    ue_matrix = (
        conversion @
        mw @
        conversion
    )

    loc = ue_matrix.to_translation()

    rot = ue_matrix.to_quaternion()

    scale = ue_matrix.to_scale()

    return {

        "location": [

            loc.x * 100.0,
            loc.y * 100.0,
            loc.z * 100.0
        ],

        "rotation": [

            rot.x,
            rot.y,
            rot.z,
            rot.w
        ],

        "scale": [

            scale.x,
            scale.y,
            scale.z
        ]
    }


# =========================================================
# KEYFRAME EXTRACTION (Phase 7E Stage 8)
# =========================================================

# Channel mapping: (data_path, array_index) → channel index
# Transform channels 0–8: location(0-2), rotation_euler(3-5), scale(6-8)
# Visibility channels 9–10: hide_viewport(9), hide_render(10)
# Channels 11–255 reserved for future extension.
_KEYFRAME_CHANNEL_MAP = {
    ("location", 0): 0,  # locX
    ("location", 1): 1,  # locY
    ("location", 2): 2,  # locZ
    ("rotation_euler", 0): 3,  # rotX
    ("rotation_euler", 1): 4,  # rotY
    ("rotation_euler", 2): 5,  # rotZ
    ("scale", 0): 6,  # scaleX
    ("scale", 1): 7,  # scaleY
    ("scale", 2): 8,  # scaleZ
    ("hide_viewport", -1): 9,   # viewport visibility (bool: 0=visible, 1=hidden)
    ("hide_render", -1): 10,    # render visibility (bool: 0=renderable, 1=not)
}


def _extract_keyframes(obj, guid_bytes):
    """Extract transform and visibility keyframes from Blender object's FCurves.

    Returns list of (guid_bytes, frame, value, channel_index) tuples.
    Supports:
      - Channels 0-2: location (locX, locY, locZ)
      - Channels 3-5: rotation_euler (rotX, rotY, rotZ)
      - Channels 6-8: scale (scaleX, scaleY, scaleZ)
      - Channel 9: hide_viewport (0.0=visible, 1.0=hidden)
      - Channel 10: hide_render (0.0=renderable, 1.0=not)
    Skips unsupported FCurves (camera props, hide_select, etc.).
    Returns empty list if no animation data or no supported FCurves.
    """
    if not obj.animation_data or not obj.animation_data.action:
        return []

    entries = []
    for fcurve in obj.animation_data.action.fcurves:
        channel = _KEYFRAME_CHANNEL_MAP.get(
            (fcurve.data_path, fcurve.array_index))
        if channel is None:
            continue
        for kp in fcurve.keyframe_points:
            entries.append((
                guid_bytes,
                int(kp.co.x),
                float(kp.co.y),
                channel,
            ))
    return entries


def _hash_keyframes(entries):
    """Compute FNV-1a 32-bit hash of keyframe entries for duplicate detection."""
    if not entries:
        return 0
    h = 2166136261
    for guid_bytes, frame, value, channel in entries:
        for b in guid_bytes:
            h = ((h ^ b) * 16777619) & 0xFFFFFFFF
        h = ((h ^ (frame & 0xFFFFFFFF)) * 16777619) & 0xFFFFFFFF
        val_bytes = struct.pack("<f", value)
        for b in val_bytes:
            h = ((h ^ b) * 16777619) & 0xFFFFFFFF
        h = ((h ^ channel) * 16777619) & 0xFFFFFFFF
    return h


# =========================================================
# SCENE SCAN (detect new/deleted objects)
# =========================================================

def scan_scene():

    global last_sent_transforms
    global tracked_objects

    current_count = len(bpy.data.objects)

    stale_handled = 0

    # =====================================================
    # DETECT STALE OBJECTS (deleted outside our tracking)
    # =====================================================

    for guid in list(tracked_objects.keys()):

        obj, guid_obj = (
            tracked_objects[guid]
        )

        try:
            _ = obj.name
        except ReferenceError:
            tracked_objects.pop(
                guid, None
            )

            last_sent_transforms.pop(
                guid, None
            )

            stale_handled += 1

    # =====================================================
    # DETECT NEW MESH OBJECTS
    # =====================================================

    new_count = 0

    for obj in bpy.data.objects:

        if obj.type != 'MESH':
            continue

        guid = ensure_unique_guid(obj, tracked_objects)

        if guid not in tracked_objects:

            tracked_objects[guid] = (
                obj,
                UUID(guid)
            )

            new_count += 1

    if new_count > 0 or stale_handled > 0:

        _reconcile_guids_on_load()

    return stale_handled, new_count


# =========================================================
# MAIN UPDATE LOOP
# =========================================================

@persistent
def check_updates():

    global timer_running
    global last_sent_transforms
    global tracked_objects
    global _last_heartbeat_time
    global _last_object_count
    global _scan_counter
    global _known_guids
    global _last_active_camera_guid
    global _active_camera_sequence
    global _active_camera_packets_sent
    global _active_camera_state_changes
    global _sequencer_op_packets_sent
    global _sequencer_op_state_changes
    global _keyframe_sequence
    global _keyframe_packets_sent
    global _keyframes_sent
    global _animated_objects_scanned
    global _last_keyframe_action

    if not timer_running:
        return 0.016

    # DIAG: print internal state each tick
    print(
        f"[DIAG] tick: timer_running={timer_running} tracked={len(tracked_objects)}",
        flush=True
    )

    # First-tick diagnostic
    if _sync_start_time > 0 and time.time() - _sync_start_time < 0.1:
        print(
            "[LiveSync] Timer callback fired — main loop active",
            flush=True
        )

    _update_runtime_stats()

    _verbose_logging = _get_threshold(
        "verbose_logging",
        False
    )

    _network_set_verbose(
        _verbose_logging
    )

    _heartbeat_interval = _get_threshold(
        "heartbeat_interval",
        5.0
    )

    _scan_interval = _get_threshold(
        "scan_interval",
        300
    )

    if check_reconnected():

        last_sent_transforms.clear()

        # Phase 6E: clear known GUIDs to prevent false delete emissions
        # on the first tick after reconnect.
        global _known_guids
        global _last_collection_state
        global _collection_anti_loop_guids
        global _last_active_camera_guid
        global _last_keyframe_action
        _known_guids.clear()
        _last_collection_state.clear()
        _collection_anti_loop_guids.clear()
        _last_active_camera_guid = b''  # Phase 7D: resend on next tick
        _last_keyframe_action.clear()  # Phase 7E Stage 8: resend keyframes on reconnect

        if _verbose_logging:
            print(
                "[Snapshot] Reconnect detected,"
                " sending full snapshot"
            )

        snapshot_roots = []
        snapshot_children = []

        # Depth-sort: build parent map, sort tracked objects by parent depth
        # so that parents always precede children in snapshot emission.
        _depth_parent_map = {}
        for g, (o, _) in tracked_objects.items():
            try:
                _ = o.name
            except ReferenceError:
                continue
            _depth_parent_map[g] = get_parent_guid(o)

        _depth_cache = {}
        _sorted_guids = sorted(
            tracked_objects.items(),
            key=lambda item: _get_parent_depth(
                item[0], _depth_parent_map, _depth_cache
            )
        )

        for guid, obj_data in _sorted_guids:

            obj, guid_obj = obj_data

            try:
                _ = obj.name
            except ReferenceError:
                continue

            transform = get_transform(obj)

            parent_guid = get_parent_guid(obj)

            parent_guid_obj = (
                UUID(parent_guid)
                if parent_guid else None
            )

            timestamp = time.time()

            try:

                serialized = serialize_object_v3(
                    guid_obj,
                    transform,
                    timestamp,
                    parent_guid_obj,
                    primitive_type=_get_primitive_type(),
                )

            except Exception as e:

                set_critical_error(
                    f"Serialization failed for {obj.name}: {e}"
                )

                _runtime_stats["serialization_failures"] += 1

                continue

            if parent_guid_obj:
                snapshot_children.append(serialized)
            else:
                snapshot_roots.append(serialized)

            last_sent_transforms[guid] = {

                "location":
                    transform["location"][:],

                "rotation":
                    transform["rotation"][:],

                "scale":
                    transform["scale"][:]
            }

        if _verbose_logging and _sorted_guids:
            depth_counts = {}
            for g, _ in _sorted_guids:
                d = _depth_cache.get(g, 0)
                depth_counts[d] = depth_counts.get(d, 0) + 1
            depth_summary = ", ".join(
                f"depth{d}:{c}" for d, c in sorted(depth_counts.items())
            )
            print(
                f"[Snapshot][ORDER] Depth distribution: "
                f"{depth_summary} "
                f"({len(snapshot_roots)} roots, {len(snapshot_children)} children)"
            )

        if snapshot_roots:

            send_objects(
                snapshot_roots,
                packet_type=0x03,
                flags=0x02
            )

        if snapshot_children:

            send_objects(
                snapshot_children,
                packet_type=0x03,
                flags=0x02 | 0x01
            )

            if _verbose_logging:
                print(
                    f"[Snapshot] Sent {len(snapshot_objects)}"
                    " objects"
                )

    objects_to_send = []
    create_objects = []
    children_to_send = []
    children_create = []
    deletes_to_send = []
    deletes_v5_to_send = []
    asset_defs_to_send = []
    renames_to_send = []
    vis_payloads_to_send = []
    hierarchies_to_send = []
    collection_payloads_to_send = []
    material_payloads_to_send = []
    mesh_payloads_to_send = []

    # =====================================================
    # SCENE SCAN (only when object count changes or
    # periodic check for edge cases)
    # =====================================================

    current_count = (
        len(bpy.data.objects)
    )

    if (current_count !=
        _last_object_count):

        _last_object_count = (
            current_count
        )

        deletes_to_send_scan, _ = (
            scan_scene()
        )

    else:

        deletes_to_send_scan = 0

    # Periodic scan to catch edge cases
    # (object added then removed between frames)

    _scan_counter += 1

    if _scan_counter >= _scan_interval:

        _scan_counter = 0

        if current_count == (
            _last_object_count
        ):
            scan_scene()

    # =====================================================
    # PHASE 6E: DELETE DETECTION — track GUIDs that have
    # disappeared from tracked_objects since last tick.
    # These are Blender-deleted objects (removed by
    # scan_scene or direct scene manipulation).
    # Emit V5 delete semantic events for each.
    #
    # Does NOT emit on:
    #   - startup (_known_guids is empty)
    #   - reconnect (_known_guids cleared in reconnect block)
    #   - stop_sync (timer not running)
    # =====================================================

    if _known_guids:

        current_guids = set(tracked_objects.keys())
        disappeared = _known_guids - current_guids

        for guid in disappeared:

            try:
                guid_obj = UUID(guid)
            except Exception:
                if _verbose_logging:
                    print(
                        f"[LiveSync] WARNING: invalid GUID in delete detection: {guid}",
                        flush=True
                    )
                continue

            deletes_v5_to_send.append(
                serialize_delete(guid_obj)
            )

            # Cleanup per-GUID state for deleted object
            _last_object_names.pop(guid, None)
            _last_visibility_state.pop(guid, None)
            _last_parent_guid.pop(guid, None)
            _last_mesh_identity.pop(guid, None)
            _last_collection_state.pop(guid, None)  # Phase 6F: cleanup collection state
            _last_material_identity.pop(guid, None)  # Phase 7B
            _last_geometry_version.pop(guid, None)  # Phase 7C

            if _verbose_logging:
                print(f"[DELETE] Detected: GUID={guid} — emitted V5 delete")

    # =====================================================
    # OBJECT ITERATION
    # =====================================================

    for guid, obj_data in list(
        tracked_objects.items()):

        obj, guid_obj = obj_data

        try:
            _ = obj.name
        except ReferenceError:
            tracked_objects.pop(guid, None)

            last_sent_transforms.pop(guid, None)

            _last_mesh_identity.pop(guid, None)
            _last_object_names.pop(guid, None)
            _last_visibility_state.pop(guid, None)
            _last_parent_guid.pop(guid, None)
            _last_material_identity.pop(guid, None)  # Phase 7B
            _last_geometry_version.pop(guid, None)  # Phase 7C
            _last_collection_state.pop(guid, None)  # Phase 6F: cleanup collection state

            deletes_to_send.append(
                serialize_delete_v3(guid_obj)
            )

            # Phase 6E: also emit V5 delete for Phase 6E UE handler
            deletes_v5_to_send.append(
                serialize_delete(guid_obj)
            )

            continue

        transform = get_transform(obj)

        previous = last_sent_transforms.get(
            guid
        )

        if transforms_different(
            transform,
            previous
        ):

            parent_guid = get_parent_guid(obj)

            parent_guid_obj = (
                UUID(parent_guid)
                if parent_guid else None
            )

            timestamp = time.time()

            try:

                serialized = serialize_object_v3(
                    guid_obj,
                    transform,
                    timestamp,
                    parent_guid_obj,
                    primitive_type=_get_primitive_type(),
                )

            except Exception as e:

                set_critical_error(
                    f"Serialization failed for {obj.name}: {e}"
                )

                _runtime_stats["serialization_failures"] += 1

                continue

            is_first_send = (
                previous is None
            )

            has_parent = (
                parent_guid_obj is not None
            )

            if has_parent:

                if is_first_send:

                    children_create.append(
                        serialized
                    )

                else:

                    children_to_send.append(
                        serialized
                    )

            else:

                if is_first_send:

                    create_objects.append(
                        serialized
                    )

                else:

                    objects_to_send.append(
                        serialized
                    )

            last_sent_transforms[guid] = {

                "location":
                    transform["location"][:],

                "rotation":
                    transform["rotation"][:],

                "scale":
                    transform["scale"][:]
            }

            # Phase 5D: Asset identity tracking
            mesh_low, mesh_high, mesh_prim = (
                get_mesh_identity_hash(obj)
            )

            if mesh_low != 0 or mesh_high != 0:

                prev_identity = (
                    _last_mesh_identity.get(guid)
                )

                # Send PT_AssetDef on first detection or mesh change
                if is_first_send or (
                    prev_identity is not None and
                    (prev_identity[0] != mesh_low or
                     prev_identity[1] != mesh_high)
                ):
                    asset_defs_to_send.append(
                        serialize_asset_identity(
                            guid_obj,
                            mesh_low,
                            mesh_high,
                            mesh_prim
                        )
                    )

                _last_mesh_identity[guid] = (
                    mesh_low,
                    mesh_high,
                    obj.data.name if obj.data else ""
                )

        # Phase 6: Visibility detection (semantic event)
        # NOTE: Lives OUTSIDE the transforms_different gate so that
        # visibility changes are detected even when the object does
        # not move. The prev_vis guard prevents first-tick emission.
        current_vis = obj.hide_get()
        prev_vis = _last_visibility_state.get(guid)
        if prev_vis is not None and prev_vis != current_vis:
            vis_payloads_to_send.append(
                serialize_visibility(guid_obj, current_vis)
            )
            if _verbose_logging:
                print(f"[VISIBILITY][DIAG] Change detected guid={guid} hidden={current_vis}")
        _last_visibility_state[guid] = current_vis

        # Phase 6: Rename detection (semantic event)
        # NOTE: Lives OUTSIDE the transforms_different gate so that
        # rename changes are detected even when the object does
        # not move. The prev_name guard prevents first-tick emission.
        current_name = obj.name
        prev_name = _last_object_names.get(guid)
        is_first_send_rename = (previous is None)
        if not is_first_send_rename and prev_name is not None and prev_name != current_name:
            renames_to_send.append(
                serialize_rename(guid_obj, prev_name, current_name)
            )
            if _verbose_logging:
                print(f"[RENAME][DIAG] Rename detected without transform change")
                print(f"[RENAME][DIAG] Old={prev_name}")
                print(f"[RENAME][DIAG] New={current_name}")
                print(f"[RENAME][DIAG] Packet queued")
        _last_object_names[guid] = current_name

        # Phase 6D: Hierarchy detection (semantic attach/detach/reparent)
        # NOTE: Lives OUTSIDE the transforms_different gate so that
        # parent changes are detected even when the object does
        # not move. Computes parent_guid independently via
        # get_parent_guid() rather than depending on the transform
        # path's parent_guid variable.
        current_parent_guid = get_parent_guid(obj)
        prev_parent_guid = _last_parent_guid.get(guid)
        is_first_send_hierarchy = (previous is None)
        if not is_first_send_hierarchy and guid in _last_parent_guid and prev_parent_guid != current_parent_guid:
            parent_guid_obj_for_hierarchy = (
                UUID(current_parent_guid)
                if current_parent_guid else None
            )
            hierarchies_to_send.append(
                serialize_hierarchy(guid_obj, parent_guid_obj_for_hierarchy)
            )
            if _verbose_logging:
                parent_str = current_parent_guid if current_parent_guid else "(root)"
                prev_parent_str = prev_parent_guid if prev_parent_guid else "(root)"
                print(f"[HIERARCHY][DIAG] Parent change detected without transform change")
                print(f"[HIERARCHY][DIAG] Child guid={guid}")
                print(f"[HIERARCHY][DIAG] Old parent={prev_parent_str}")
                print(f"[HIERARCHY][DIAG] New parent={parent_str}")
                print(f"[HIERARCHY][DIAG] Packet queued")
        _last_parent_guid[guid] = current_parent_guid

        # =================================================
        # Phase 7B Stage 1C: Material slot identity detection
        # Sends PT_Material only when slot identities change.
        # Suppressed on first send (prevents emit on startup/reconnect)
        # and when no change is detected.
        #
        # NOTE: Material packets are stored but NOT assigned to UE
        # components yet. SetMaterial() is deferred to Stage 2.
        # =================================================

        current_slots = get_object_material_slots(obj)
        prev_slots = _last_material_identity.get(guid)
        is_first_material = (prev_slots is None)

        if not is_first_material and current_slots != prev_slots:
            material_payloads_to_send.append(
                serialize_material_slots(guid_obj, current_slots)
            )
            if _verbose_logging:
                print(f"[MATERIAL][DIAG] Material change detected guid={guid}")
                print(f"[MATERIAL][DIAG] Slots={current_slots}")

        _last_material_identity[guid] = current_slots

        # =================================================
        # Phase 7C Stage 1D: Geometry change detection
        # Evaluates the depsgraph mesh and sends PT_Mesh
        # chunks when the geometry version hash changes.
        #
        # Suppressed on first send (is_first_send) to avoid
        # flooding on reconnect. _last_geometry_version
        # is None for new objects, triggering a first-tick
        # evaluation without emission.
        #
        # Only processes MESH objects with valid data.
        # Skips silently if depsgraph evaluation fails
        # (e.g. outside Blender, no context, error state).
        # =================================================

        if is_first_send:
            _last_geometry_version.pop(guid, None)
        else:
            try:
                mesh_data = extract_evaluated_mesh_data(obj)
                if mesh_data is not None:
                    current_hash = compute_geometry_version_hash(
                        mesh_data["vertices"],
                        mesh_data["triangles"],
                        mesh_data["material_indices"],
                    )
                    prev_hash = _last_geometry_version.get(guid)
                    if prev_hash is not None and current_hash != prev_hash:
                        version_hash = current_hash
                        chunk_flags = (
                            MESH_CHUNK_FLAG_FIRST_CHUNK |
                            MESH_CHUNK_FLAG_LAST_CHUNK
                        )
                        chunk_payload = serialize_mesh_chunk(
                            guid_obj,
                            version_hash,
                            0,   # chunk_index
                            1,   # chunk_count
                            mesh_data["vertices"],
                            mesh_data["triangles"],
                            mesh_data["material_indices"],
                            flags=chunk_flags,
                        )
                        mesh_payloads_to_send.append(chunk_payload)
                        if _verbose_logging:
                            print(f"[GEOMETRY][DIAG] Geometry change detected guid={guid}")
                            print(f"[GEOMETRY][DIAG] Verts={mesh_data['vertex_count']} Tris={mesh_data['triangle_count']}")
                    _last_geometry_version[guid] = current_hash
            except Exception:
                if _verbose_logging:
                    print(f"[GEOMETRY][WARN] Geometry extraction failed for guid={guid}")

        # =================================================
        # Phase 6F: Collection membership detection
        # Build current collection membership and diff against
        # last known state. Emit ADD/REMOVE for each change.
        # Skips anti-loop suppressed GUIDs.
        #
        # is_first_collection: True if this is the first tick
        # tracking this object's collection state (prevents
        # false emissions on startup/reconnect).
        # =================================================

        is_first_collection = guid not in _last_collection_state
        current_coll_guids = set()

        try:
            for coll in obj.users_collection:
                coll_guid_str = _get_collection_guid_str(coll)
                current_coll_guids.add(coll_guid_str)
        except Exception:
            if _verbose_logging:
                print(
                    f"[LiveSync] WARNING: collection iteration failed for GUID={guid}",
                    flush=True
                )

        if not is_first_collection:
            prev_colls = _last_collection_state.get(guid, set())

            if guid not in _collection_anti_loop_guids:
                added = current_coll_guids - prev_colls
                removed = prev_colls - current_coll_guids

                for added_coll_str in added:
                    added_coll_uuid = UUID(added_coll_str)
                    collection_payloads_to_send.append(
                        serialize_collection_membership(
                            guid_obj, added_coll_uuid,
                            COLLECTION_OP_ADD
                        )
                    )
                    print(
                        f"[COLLECTION][DIAG] Membership ADD "
                        f"obj_guid={guid} coll_guid={added_coll_str}"
                    )

                for removed_coll_str in removed:
                    removed_coll_uuid = UUID(removed_coll_str)
                    collection_payloads_to_send.append(
                        serialize_collection_membership(
                            guid_obj, removed_coll_uuid,
                            COLLECTION_OP_REMOVE
                        )
                    )
                    print(
                        f"[COLLECTION][DIAG] Membership REMOVE "
                        f"obj_guid={guid} coll_guid={removed_coll_str}"
                    )

                if _verbose_logging and (added or removed):
                    print(
                        f"[COLLECTION] GUID={guid} added={len(added)} "
                        f"removed={len(removed)}"
                    )

        _last_collection_state[guid] = current_coll_guids

        # Clear anti-loop guard for this GUID after processing
        _collection_anti_loop_guids.discard(guid)

        # =================================================
        # Phase 7E Stage 8: Transform keyframe extraction
        # Scans FCurves for location/rotation/scale channels,
        # batches into PT_Keyframe packets, suppresses
        # duplicates via action/key hash.
        # =================================================

        if is_keyframe_effective() and not is_first_send:
            try:
                kf_entries = _extract_keyframes(obj, guid_obj.bytes)
            except Exception:
                kf_entries = []

            if kf_entries:
                _animated_objects_scanned += 1
                kf_hash = _hash_keyframes(kf_entries)
                prev_hash = _last_keyframe_action.get(guid)

                if prev_hash is None or kf_hash != prev_hash:
                    _last_keyframe_action[guid] = kf_hash

                    # Batch into PT_Keyframe packets (max KEYFRAME_MAX_KEYS per packet)
                    for batch_start in range(0, len(kf_entries), KEYFRAME_MAX_KEYS):
                        batch = kf_entries[batch_start:batch_start + KEYFRAME_MAX_KEYS]
                        _keyframe_sequence += 1
                        try:
                            serialized = serialize_keyframe(
                                _keyframe_sequence,
                                time.time(),
                                batch,
                            )
                            send_objects(
                                [serialized],
                                packet_type=PT_Keyframe,
                                version=5,
                            )
                            _keyframe_packets_sent += 1
                            _keyframes_sent += len(batch)
                            _runtime_stats["keyframe_packets_sent"] = _keyframe_packets_sent
                            _runtime_stats["keyframes_sent"] = _keyframes_sent
                            _runtime_stats["animated_objects_scanned"] = _animated_objects_scanned
                        except Exception:
                            if _verbose_logging:
                                print(
                                    f"[KEYFRAME][WARN] Send failed guid={guid} "
                                    f"batch_start={batch_start}",
                                    flush=True,
                                )

    # =====================================================
    # SEND DELETE PACKETS (Phase 6E V5 — identity-destruction)
    # =====================================================

    if deletes_v5_to_send:

        if _verbose_logging:
            print(f"[DELETE][SEND] Sending {len(deletes_v5_to_send)} V5 delete(s)")

        send_objects(
            deletes_v5_to_send,
            packet_type=PT_Delete_V5,
            version=LIVE_SYNC_VERSION_V5
        )

    # =====================================================
    # SEND DELETE PACKETS (V3 legacy)
    # =====================================================

    if deletes_to_send:

        send_objects(
            deletes_to_send,
            packet_type=0x04
        )

    # =====================================================
    # SEND CREATE PACKETS (first-time objects, roots)
    # =====================================================

    if create_objects:

        send_objects(
            create_objects,
            packet_type=0x03
        )

    # =====================================================
    # SEND CREATE PACKETS (first-time objects, children)
    # =====================================================

    if children_create:

        send_objects(
            children_create,
            packet_type=0x03,
            flags=0x01
        )

    # =====================================================
    # SEND ASSET DEF PACKETS (Phase 5D: V5 PT_AssetDef)
    # Sent after CREATE, before TRANSFORM — non-blocking
    # =====================================================

    if asset_defs_to_send:

        send_objects(
            asset_defs_to_send,
            packet_type=PT_AssetDef,
            version=LIVE_SYNC_VERSION_V5
        )

    # =====================================================
    # SEND RENAME PACKETS (Phase 6 — Semantic Event)
    # =====================================================

    if renames_to_send:

        send_objects(
            renames_to_send,
            packet_type=PT_Rename
        )

    # =====================================================
    # SEND VISIBILITY PACKETS (Phase 6 — Semantic Event)
    # =====================================================

    if vis_payloads_to_send:

        send_objects(
            vis_payloads_to_send,
            packet_type=PT_Visibility
        )

    # =====================================================
    # SEND HIERARCHY PACKETS (Phase 6D — Semantic Event)
    # =====================================================

    if hierarchies_to_send:

        send_objects(
            hierarchies_to_send,
            packet_type=PT_Hierarchy
        )

    # =====================================================
    # SEND COLLECTION PACKETS (Phase 6F — Semantic Event)
    # =====================================================

    if collection_payloads_to_send:

        if _verbose_logging:
            print(f"[COLLECTION][SEND] Sending {len(collection_payloads_to_send)} collection event(s)")

        send_objects(
            collection_payloads_to_send,
            packet_type=PT_Collection,
            version=LIVE_SYNC_VERSION_V5
        )

    # =====================================================
    # SEND MATERIAL PACKETS (Phase 7B Stage 1C — PT_Material)
    # =====================================================

    if material_payloads_to_send:

        if _verbose_logging:
            print(f"[MATERIAL][SEND] Sending {len(material_payloads_to_send)} material slot packet(s)")

        send_objects(
            material_payloads_to_send,
            packet_type=PT_Material,
            version=LIVE_SYNC_VERSION_V5
        )

    # =====================================================
    # SEND MESH CHUNK PACKETS (Phase 7C Stage 1D — PT_Mesh)
    # =====================================================

    if mesh_payloads_to_send:

        if _verbose_logging:
            print(f"[GEOMETRY][SEND] Sending {len(mesh_payloads_to_send)} mesh chunk packet(s)")

        send_objects(
            mesh_payloads_to_send,
            packet_type=PT_Mesh,
            version=LIVE_SYNC_VERSION_V5
        )

    # =====================================================
    # SEND TRANSFORM PACKETS (existing objects, roots)
    # =====================================================

    if objects_to_send:

        send_objects(
            objects_to_send
        )

    # =====================================================
    # SEND TRANSFORM PACKETS (existing objects, children)
    # =====================================================

    if children_to_send:

        send_objects(
            children_to_send,
            flags=0x01
        )

    # =====================================================
    # HEARTBEAT (every 5 seconds)
    # =====================================================

    now = time.time()

    if now - _last_heartbeat_time >= _heartbeat_interval:

        print(
            "[HEARTBEAT][DIAG] heartbeat queued",
            flush=True
        )

        if _verbose_logging:
            print(
                "[LiveSync] Sending heartbeat",
                flush=True
            )

        send_objects(
            [],
            packet_type=0x07
        )

        _last_heartbeat_time = now

    # =====================================================
    # PLAYBACK STATE DETECTION (Phase 7C)
    # =====================================================

    if is_playback_effective():

        try:
            screen = bpy.context.screen
            is_playing = screen.is_animation_playing
        except (AttributeError, RuntimeError, TypeError):
            is_playing = False

        current_state = PLAYBACK_PLAY if is_playing else PLAYBACK_STOP

        if current_state != _last_playback_state and _last_playback_state is not None:
            payload = serialize_playback_state(
                current_state,
                _net_playback_sequence + 1,
                time.time(),
            )
            send_objects([payload], packet_type=PT_PlaybackState, version=5)
            _net_playback_sequence += 1
            _net_playback_packets_sent += 1
            _net_playback_state_changes += 1
            _runtime_stats["playback_packets_sent"] = _net_playback_packets_sent
            _runtime_stats["playback_state_changes"] = _net_playback_state_changes

        _last_playback_state = current_state

    # =====================================================
    # TIMELINE DETECTION (Phase 7B)
    # =====================================================

    if is_timeline_effective():
        try:
            scene = bpy.context.scene
            fc = scene.frame_current
            fs = scene.frame_start
            fe = scene.frame_end
            fps_num = scene.render.fps
            fps_den = scene.render.fps_base
        except (AttributeError, RuntimeError, TypeError):
            fc = fs = fe = fps_num = 0
            fps_den = 1

        current_tl = (fc, fs, fe, fps_num, fps_den)

        if _last_timeline_state is None:
            pass
        elif current_tl != _last_timeline_state:
            _timeline_sequence += 1
            payload = serialize_timeline(
                fc, fs, fe, fps_num, fps_den,
                _timeline_sequence, time.time(),
            )
            send_objects([payload], packet_type=PT_Timeline, version=5)
            _timeline_packets_sent += 1
            _timeline_state_changes += 1
            _runtime_stats["timeline_packets_sent"] = _timeline_packets_sent
            _runtime_stats["timeline_state_changes"] = _timeline_state_changes

        _last_timeline_state = current_tl

    # =====================================================
    # ACTIVE CAMERA DETECTION (Phase 7D Stage 2)
    # =====================================================

    if is_active_camera_effective():
        try:
            scene = bpy.context.scene
            camera_obj = scene.camera
        except (AttributeError, RuntimeError, TypeError):
            camera_obj = None

        if camera_obj is not None:
            guid_hex = ensure_guid(camera_obj)
            guid_bytes = UUID(guid_hex).bytes
        else:
            guid_bytes = NULL_CAMERA_GUID

        if _last_active_camera_guid is None:
            pass
        elif _last_active_camera_guid == b'' or guid_bytes != _last_active_camera_guid:
            _active_camera_sequence += 1
            payload = serialize_active_camera(
                guid_bytes,
                _active_camera_sequence,
                time.time(),
            )
            send_objects([payload], packet_type=PT_ActiveCamera, version=5)
            _active_camera_packets_sent += 1
            _active_camera_state_changes += 1
            _runtime_stats["active_camera_packets_sent"] = _active_camera_packets_sent
            _runtime_stats["active_camera_state_changes"] = _active_camera_state_changes

        _last_active_camera_guid = guid_bytes

    # =====================================================
    # AUTO-POPUP CRITICAL ERRORS
    # =====================================================

    global _last_critical_error

    error_severity = (
        get_last_error_severity()
    )

    if error_severity == 'CRITICAL':

        error_msg = get_last_error()

        if error_msg and error_msg != _last_critical_error:

            _last_critical_error = error_msg

            try:

                bpy.ops.uelivesync.show_error(
                    'INVOKE_DEFAULT',
                    error_message=error_msg
                )

            except Exception:
                pass

    # Phase 6E: Update known GUIDs for next tick's delete detection.
    # Must happen AFTER all tracked_objects modifications (scan_scene,
    # iteration, ReferenceError removals) to ensure accurate next-tick diff.
    _known_guids = set(tracked_objects.keys())

    return 0.016


# =========================================================
# START SYNC
# =========================================================

def get_tracked_count():

    count = len(tracked_objects)

    _runtime_stats["tracked_objects"] = (
        count
    )

    return count


def rebind_all():

    global tracked_objects
    global last_sent_transforms

    if not tracked_objects:
        return 0

    roots = []
    children = []

    # Depth-sort: parents before children in snapshot emission
    _rb_parent_map = {}
    for g, (o, _) in tracked_objects.items():
        try:
            _ = o.name
        except ReferenceError:
            continue
        _rb_parent_map[g] = get_parent_guid(o)

    _rb_depth_cache = {}
    _rb_sorted = sorted(
        tracked_objects.items(),
        key=lambda item: _get_parent_depth(
            item[0], _rb_parent_map, _rb_depth_cache
        )
    )

    for guid, obj_data in _rb_sorted:

        obj, guid_obj = obj_data

        try:
            _ = obj.name
        except ReferenceError:
            continue

        transform = get_transform(obj)

        parent_guid = get_parent_guid(obj)

        parent_guid_obj = (
            UUID(parent_guid)
            if parent_guid else None
        )

        timestamp = time.time()

        try:

            serialized = serialize_object_v3(
                guid_obj,
                transform,
                timestamp,
                parent_guid_obj,
                primitive_type=(
                    _get_primitive_type()
                ),
            )

        except Exception as e:

            print(
                "[Rebind] Serialization failed "
                f"for {obj.name}: {e}"
            )

            continue

        if parent_guid_obj:
            children.append(serialized)
        else:
            roots.append(serialized)

        last_sent_transforms[guid] = {

            "location":
                transform["location"][:],

            "rotation":
                transform["rotation"][:],

            "scale":
                transform["scale"][:]
        }

    total_sent = 0

    if roots or children:

        send_objects(
            [],
            packet_type=PT_BeginSnapshot,
        )

        if roots:

            send_objects(
                roots,
                packet_type=0x03,
                flags=0x02,
            )

            total_sent += len(roots)

        if children:

            send_objects(
                children,
                packet_type=0x03,
                flags=0x02 | 0x01,
            )

            total_sent += len(children)

        send_objects(
            [],
            packet_type=PT_EndSnapshot,
        )

    if _verbose_logging:
        print(
            f"[Rebind] Sent {total_sent} objects "
            f"({len(roots)} roots, {len(children)} children)"
        )

    return total_sent


def dump_diagnostics():

    _update_runtime_stats()

    print("=" * 50)
    print("UE Live Sync — Diagnostics Dump")
    print("=" * 50)

    s = _runtime_stats

    print(f"  [Status]")
    print(f"    Timer running:     {timer_running}")
    print(f"    Connected:         {is_connected()}")

    detail = get_status_detail()
    if detail:
        print(f"    Status:            {detail}")

    print(f"    Uptime (s):        {s['uptime']:.1f}")
    print(f"    Heartbeat interval: {s['heartbeat_interval']:.1f}s")
    print(f"    Scan interval:     {s['scan_interval']} frames")

    print(f"  [Objects]")
    print(f"    Tracked:           {s['tracked_objects']}")
    print(f"    Scan counter:      {_scan_counter}")
    print(f"    Last obj count:    {_last_object_count}")

    print(f"  [Network]")
    print(f"    Queue depth:       {s['queue_depth']}")
    print(f"    Reconnect count:   {s['reconnect_count']}")
    print(f"    Dropped packets:   {s['dropped_packets']}")
    print(f"    Packets sent:      {s['packets_sent']}")
    print(f"    Bytes sent:        {s['bytes_sent']}")
    print(f"    Last send:         {s['last_send_time']:.1f}")

    print(f"  [Playback]")
    print(f"    Packets sent:      {s['playback_packets_sent']}")
    print(f"    State changes:     {s['playback_state_changes']}")

    print(f"  [Timeline]")
    print(f"    Packets sent:      {s['timeline_packets_sent']}")
    print(f"    State changes:     {s['timeline_state_changes']}")

    print(f"  [Active Camera]")
    print(f"    Packets sent:      {s['active_camera_packets_sent']}")
    print(f"    State changes:     {s['active_camera_state_changes']}")

    error = s["last_error"]
    severity = s["last_error_severity"]
    if error:
        print(f"    Last error:        [{severity}] {error}")

    print(f"  [Health]")
    print(f"    Reconnect escalated: {s['reconnect_escalated']}")
    print(f"    Has critical error:  {s['has_critical_error']}")
    print(f"    Serialization fails: {s['serialization_failures']}")

    print(f"  [Runtime Config]")
    for key, val in _runtime_config.items():
        print(f"    {key}: {val}")

    print("=" * 50)


def get_uptime():

    if _sync_start_time == 0.0:

        _runtime_stats["uptime"] = 0.0

        return 0.0

    uptime = time.time() - _sync_start_time

    _runtime_stats["uptime"] = uptime

    return uptime


def start_sync():

    global timer_running
    global last_sent_transforms
    global tracked_objects
    global _timer_ref
    global _last_heartbeat_time
    global _last_object_count
    global _scan_counter
    global _sync_start_time
    global _last_object_names
    global _last_visibility_state
    global _last_parent_guid
    global _last_collection_state  # Phase 6F
    global _last_mesh_identity  # Phase 7A: clear stale mesh identity cache
    global _last_material_identity  # Phase 7B: clear stale material identity cache
    global _last_geometry_version  # Phase 7C: clear stale geometry version cache
    global _last_playback_state  # Phase 7C: reset playback state
    global _last_timeline_state  # Phase 7B: reset timeline tracking
    global _timeline_sequence
    global _timeline_packets_sent
    global _timeline_state_changes
    global _last_active_camera_guid  # Phase 7D: reset active camera tracking
    global _active_camera_sequence
    global _active_camera_packets_sent
    global _active_camera_state_changes

    timer_running = False
    _last_mesh_identity.clear()  # Phase 7A: prevent stale suppression across sessions
    _last_material_identity.clear()  # Phase 7B: prevent stale material suppression
    _last_object_names.clear()
    _last_visibility_state.clear()
    _last_parent_guid.clear()
    _known_guids.clear()
    _last_collection_state.clear()  # Phase 6F
    _last_keyframe_action.clear()  # Phase 7E Stage 8
    _last_playback_state = None  # Phase 7C: reset playback transition detector
    _last_timeline_state = None  # Phase 7B: reset timeline transition detector
    _timeline_sequence = 0
    _timeline_packets_sent = 0
    _timeline_state_changes = 0
    _last_active_camera_guid = None  # Phase 7D: first tick sets baseline
    _active_camera_sequence = 0
    _active_camera_packets_sent = 0
    _active_camera_state_changes = 0
    _keyframe_sequence = 0
    _keyframe_packets_sent = 0
    _keyframes_sent = 0
    _animated_objects_scanned = 0

    if _timer_ref is not None:
        try:
            bpy.app.timers.unregister(_timer_ref)
        except ValueError:
            pass
        _timer_ref = None

    disconnect()

    print("UE Live Sync Stopped")
