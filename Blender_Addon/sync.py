import sys
import os
import bpy
import hashlib
import struct
import time
import traceback
import uuid

from bpy.app.handlers import persistent

from mathutils import Matrix

from uuid import UUID

from .msg_transport import get_transport, MsgType
from .material_protocol import build_material_create, build_material_update
from .object_protocol import build_object_create, build_object_update, build_object_reparent, build_object_visibility, build_object_rename, build_object_delete, clear_all_sequences, clear_delete_sequences, next_create_sequence, next_update_sequence

try:
    from . import network as _network_mod
    from .network import (
        connect,
        disconnect,
        send_objects,
        send_snapshot,
        serialize_object,
        serialize_object_v3,
        serialize_delete_v3,
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
        PRIMITIVE_CAMERA,
        PT_BeginSnapshot,
        PT_EndSnapshot,
        PT_AssetDef,
        PT_Delete_V5,
        PT_Collection,
        PT_Material,
        get_object_material_slots,
        LIVE_SYNC_VERSION_V5,
        get_mesh_identity_hash,
        get_material_identity_hash,
        get_material_basic_properties,
        extract_texture_maps_for_slot,
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
        _mtex_start_collecting,
        _mtex_collect_records,
        _mtex_collecting,
        _mtex_clear_dedup_state,
        material_verbose_logging,
        _mt_basic_clear_state,
        _mt_basic_start_collecting,
        _mt_basic_collect_slot,
        mat_basic_collect_records,
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
        PT_TimelineState,
        serialize_timeline_state,
        TIMELINE_STATE_PAYLOAD_SIZE,
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
        KEYFRAME_CHANNEL_CAMERA_FOV,
        _keyframe_sequence as _net_keyframe_sequence,
        _keyframe_packets_sent as _net_keyframe_packets_sent,
        _keyframes_sent as _net_keyframes_sent,
        _animated_objects_scanned as _net_animated_objects_scanned,
        pack_ue_fguid,
        PT_CameraDef,
        serialize_camera_def,
        render_aspect_ratio,
        CAMERA_DEF_PAYLOAD_SIZE,
        CAMERA_DEF_FLAG_IS_ORTHO,
        CAMERA_DEF_FLAG_HAS_CAMERA_DEF,
        CAP_SUPPORTS_CAMERA_DEF_SYNC,
        CAP_SUPPORTS_CAMERA_FOV_KEYFRAME,
        is_camera_fov_keyframe_effective,
        compute_material_texture_hash,
        compute_material_dirty_sig,
        _append_blender_debug_log,
    )
except ImportError:
    import network as _network_mod
    from network import (
        connect,
        disconnect,
        send_objects,
        send_snapshot,
        serialize_object,
        serialize_object_v3,
        serialize_delete_v3,
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
        PRIMITIVE_CAMERA,
        PT_BeginSnapshot,
        PT_EndSnapshot,
        PT_AssetDef,
        PT_Delete_V5,
        PT_Collection,
        PT_Material,
        LIVE_SYNC_VERSION_V5,
        get_object_material_slots,
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
        PT_TimelineState,
        serialize_timeline_state,
        TIMELINE_STATE_PAYLOAD_SIZE,
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
        KEYFRAME_CHANNEL_CAMERA_FOV,
        _keyframe_sequence as _net_keyframe_sequence,
        _keyframe_packets_sent as _net_keyframe_packets_sent,
        _keyframes_sent as _net_keyframes_sent,
        _animated_objects_scanned as _net_animated_objects_scanned,
        pack_ue_fguid,
        CAP_SUPPORTS_CAMERA_FOV_KEYFRAME,
        is_camera_fov_keyframe_effective,
        compute_material_texture_hash,
        compute_material_dirty_sig,
        _append_blender_debug_log,
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

# Phase 1.4.2a: Track material names already sent as MATERIAL_CREATE this session
_mat_create_sent_names = set()

# Phase 1.4.2b: Per-material last-sent property state for MATERIAL_UPDATE detection
# Maps mat_name -> (base_color_4f, metallic_f, roughness_f)
_last_material_sent_props = {}

# Phase 10J.5I: Per-GUID last material property signature for change detection
# Maps guid -> {slot_index: (r, g, b, a, roughness, metallic)}
_last_material_property_sig = {}

# Phase 7H: transition tracker for SIG_CACHE_HIT log (one log per change→unchanged transition)
# Maps guid -> last reason string; cleared after SIG_CACHE_HIT is logged.
_last_material_sent_reason = {}

# Phase 7H: set of GUIDs that have already printed the initial decision log.
# Prevents [MATERIAL][DECISION_INIT] from spamming every tick.
_last_decision_init_printed = set()

# Phase 7C: Per-GUID last geometry version hash for change detection
# Maps guid -> SHA-256 hex string of evaluated mesh geometry.
# Cleared on start_sync, stop_sync, and object delete.
# None means "not yet evaluated".
_last_geometry_version = {}

# Phase 9B.6: Per-GUID last sidecar texture digest for skip-on-unchanged optimization
# Maps guid -> int hash of current texture state (filepaths + sizes + mtimes)
_last_sidecar_digest = {}

# Phase 9B.6: Per-GUID last sidecar info list (reused on texture-unchanged skip)
_last_sidecar_info = {}

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
_camera_def_packets_sent = 0

# Phase 10A.2: Per-GUID camera parameter signature for dirty detection
# Maps guid_bytes → tuple(lens, sensor_width, sensor_height, clip_start, clip_end, ortho_scale, is_ortho)
_last_camera_signature = {}

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
    "burst_packet_count": 0,
    "burst_packet_count_peak": 0,
}

# Cached preferences (avoids RNA lookup every tick)
_runtime_config = {
    "threshold_location": 0.01,
    "threshold_rotation": 0.0001,
    "threshold_scale": 0.001,
    "heartbeat_interval": 10.0,
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
    # the OBJECT_RENAME MsgType path. Including obj.name would cause
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
    'CAMERA': PRIMITIVE_CAMERA,
}


def _get_primitive_type(obj=None):
    """Return primitive type for a given Blender object.

    Cameras are detected by their Blender type ('CAMERA') and always
    use PRIMITIVE_CAMERA so UE can spawn an ACameraActor.

    For non-camera objects the user-pref default is used.
    """

    if obj is not None and hasattr(obj, 'type') and obj.type == 'CAMERA':
        return PRIMITIVE_CAMERA

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

    # Blender camera looks along its local -Z.
    # Unreal camera looks along its local +X.
    # C*M*C converts the object's coordinate frame (Y-flip) but does not
    # change the camera's intrinsic view axis. After converting the object
    # basis, apply an additional fixed camera-local basis rotation so both
    # cameras observe the same world-space view direction.
    # Local mapping: +X->-Z, +Y->+X, +Z->-Y.
    if obj.type == 'CAMERA':
        camera_correction = Matrix((
            (0,  1,  0,  0),
            (0,  0, -1,  0),
            (-1, 0,  0,  0),
            (0,  0,  0,  1)
        ))
        ue_matrix = ue_matrix @ camera_correction

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


def _ue_ortho_scale(cam_data):
    # INV-2026-011: ortho_scale must go through the same Blender->UE unit
    # conversion as location (m -> cm) in get_transform.
    return cam_data.ortho_scale * 100.0


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
    ("hide_viewport", 0): 9,   # viewport visibility (bool: 0=visible, 1=hidden)
    ("hide_render", 0): 10,    # render visibility (bool: 0=renderable, 1=not)
}

# Supported Blender object types for live sync tracking.
# Add new types here as support is implemented downstream.
# All downstream consumers must be audited before adding a new type.
SUPPORTED_TRACKED_TYPES = {
    'MESH',
    'CAMERA',
}

# Phase 7E Stage 10A.4: Blender 5.1+ slotted action fcurve iterator
# -------------------------------------------------------------------

def _iter_action_fcurves_51(action, obj=None):
    """Iterate FCurves from a Blender 5.1+ slotted/layered Action.

    Blender 5.0+ removed action.fcurves.  FCurves are now stored inside:
      action -> layers -> strips (ActionKeyframeStrip) -> channelbags -> fcurves

    Each channelbag is linked to a slot via slot_handle.
    Each slot identifies an animated datablock (e.g. OBJECT) by its handle.

    Yields (fcurve, slot_handle) tuples.
    If *obj* is provided, only yields fcurves from the matching OBJECT slot.
    Safe for missing/incomplete data.

    Diagnostics use [KEYFRAME][BLENDER51] prefix.
    """
    import bpy

    if not action:
        return
    if getattr(action, 'is_action_layered', False) is False:
        print("[KEYFRAME][BLENDER51_SKIP] reason=not_layered")
        return

    # --- 1. locate the OBJECT slot matching obj (if requested) ---
    target_handle = None
    slots_found = []
    try:
        slots_seq = list(action.slots)
        slots_found = [(s.identifier, s.handle, s.target_id_type) for s in slots_seq]
    except Exception:
        print("[KEYFRAME][BLENDER51_SKIP] reason=slots_unreadable")
        return

    if obj is not None:
        expected_ident = "OB" + obj.name
        for slot in action.slots:
            if not getattr(slot, 'target_id_type', None):
                continue
            if slot.target_id_type != 'OBJECT':
                continue
            sid = getattr(slot, 'identifier', '')
            if sid == expected_ident:
                target_handle = getattr(slot, 'handle', None)
                print(f"[KEYFRAME][BLENDER51_SLOT] object={obj.name} "
                      f"slot={sid} handle={target_handle} matched=True")
                break
        else:
            print(f"[KEYFRAME][BLENDER51_SLOT] object={obj.name} "
                  f"no_matching_slot slots={slots_found}")
            return
    else:
        # No object filter — iterate all slots; use all slot handles.
        pass

    # --- 2. traverse layers -> strips -> channelbags -> fcurves ---
    try:
        layers = list(action.layers)
    except Exception:
        print("[KEYFRAME][BLENDER51_SKIP] reason=layers_unreadable")
        return

    print(f"[KEYFRAME][BLENDER51] action={action.name} "
          f"slots={len(slots_found)} layers={len(layers)}")

    for layer in layers:
        try:
            strips = list(layer.strips)
        except Exception:
            continue
        for strip in strips:
            strip_type = getattr(strip, 'type', '')
            if strip_type != 'KEYFRAME':
                continue
            try:
                channelbags = list(strip.channelbags)
            except Exception:
                continue
            for cbag in channelbags:
                ch_slot_handle = getattr(cbag, 'slot_handle', None)
                ch_slot_ref = getattr(cbag, 'slot', None)
                if ch_slot_handle is None:
                    continue
                # If targeting a specific object, skip non-matching bags
                if target_handle is not None and ch_slot_handle != target_handle:
                    continue
                try:
                    fcurves = list(cbag.fcurves)
                except Exception:
                    continue
                for fcurve in fcurves:
                    dp = getattr(fcurve, 'data_path', '')
                    idx = getattr(fcurve, 'array_index', 0)
                    kf_count = len(getattr(fcurve, 'keyframe_points', []))
                    print(f"[KEYFRAME][BLENDER51_FCURVE] path={dp} "
                          f"index={idx} keys={kf_count}")
                    yield (fcurve, ch_slot_handle)
    else:
        return


def _extract_keyframes(obj, guid_obj):
    """Extract transform and visibility keyframes from Blender object's FCurves.

    Returns list of (guid_obj, frame, value, channel_index) tuples.
    guid_obj is a UUID object (packed via pack_ue_fguid during serialization).
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

    action = obj.animation_data.action

    entries = []
    if getattr(action, 'is_action_layered', False):
        fcurve_iter = _iter_action_fcurves_51(action, obj=obj)
        for fcurve, _slot_handle in fcurve_iter:
            channel = _KEYFRAME_CHANNEL_MAP.get(
                (fcurve.data_path, fcurve.array_index))
            if channel is None:
                continue
            for kp in fcurve.keyframe_points:
                entries.append((
                    guid_obj,
                    int(kp.co.x),
                    float(kp.co.y),
                    channel,
                ))
    _append_blender_debug_log("[DIAG][FOV] returning %d keyframe entries for %s" % (len(entries), obj.name))
    return entries

    # Legacy path (Blender < 5.0) — kept as fallback for test compat
    if hasattr(action, 'fcurves'):
        for fcurve in action.fcurves:
            channel = _KEYFRAME_CHANNEL_MAP.get(
                (fcurve.data_path, fcurve.array_index))
            if channel is None:
                continue
            for kp in fcurve.keyframe_points:
                entries.append((
                    guid_obj,
                    int(kp.co.x),
                    float(kp.co.y),
                    channel,
                ))
    return entries


def _extract_camera_fov_keyframes(obj, guid_obj):
    """Extract camera FOV keyframes from camera datablock animation.

    Reads camera.data.animation_data.action (not obj.animation_data).
    Supports legacy fcurves and Blender 5.1 slotted/layered actions with
    CAMERA datablock slot.

    Returns list of (guid_obj, frame, fov_degrees, 11) tuples.
    Returns empty list if obj is not a CAMERA, no animation data,
    orthographic camera, or no valid FOV keyframes.
    """
    if obj.type != 'CAMERA':
        return []

    camera = obj.data
    if getattr(camera, 'type', 'PERSP') == 'ORTHO':
        _append_blender_debug_log("[DIAG][FOV] ortho camera")
        return []

    if not camera.animation_data or not camera.animation_data.action:
        _append_blender_debug_log("[DIAG][FOV] no camera.data animation_data/action")
        return []

    _append_blender_debug_log("[DIAG][FOV] has animation data, checking fcurves")

    action = camera.animation_data.action

    # Collect lens and sensor_width fcurves
    lens_fcurves = []
    sensor_fcurves = []

    if getattr(action, 'is_action_layered', False):
        _append_blender_debug_log("[DIAG][FOV] action is layered")
        # Blender 5.1+ slotted action — locate CAMERA slot
        import bpy
        target_handle = None
        try:
            for slot in action.slots:
                _append_blender_debug_log("[DIAG][FOV]  slot id_type=%s handle=%s" % (
                    getattr(slot, 'target_id_type', '?'),
                    getattr(slot, 'handle', '?'),
                ))
                if getattr(slot, 'target_id_type', None) == 'CAMERA':
                    target_handle = getattr(slot, 'handle', None)
                    break
        except Exception as e:
            _append_blender_debug_log("[DIAG][FOV] slot scan exception: %s" % e)
            return []

        if target_handle is None:
            _append_blender_debug_log("[DIAG][FOV] no CAMERA slot found in layered action")
            return []

        _append_blender_debug_log("[DIAG][FOV] target_handle=%s" % target_handle)
        try:
            for layer in action.layers:
                for strip in getattr(layer, 'strips', []):
                    if getattr(strip, 'type', '') != 'KEYFRAME':
                        continue
                    for cbag in getattr(strip, 'channelbags', []):
                        if getattr(cbag, 'slot_handle', None) != target_handle:
                            continue
                        for fcurve in getattr(cbag, 'fcurves', []):
                            dp = getattr(fcurve, 'data_path', '')
                            _append_blender_debug_log("[DIAG][FOV]  layered fcurve dp=%s" % dp)
                            if dp == 'lens':
                                lens_fcurves.append(fcurve)
                            elif dp == 'sensor_width':
                                sensor_fcurves.append(fcurve)
        except Exception as e:
            _append_blender_debug_log("[DIAG][FOV] layered fcurve scan exception: %s" % e)
            pass
    else:
        # Legacy path (Blender < 5.0)
        _append_blender_debug_log("[DIAG][FOV] action is NOT layered (legacy path)")
        if hasattr(action, 'fcurves'):
            _append_blender_debug_log("[DIAG][FOV] action has %d fcurves" % len(action.fcurves))
            for fcurve in action.fcurves:
                dp = getattr(fcurve, 'data_path', '')
                _append_blender_debug_log("[DIAG][FOV]  fcurve dp=%s" % dp)
                if dp == 'lens':
                    lens_fcurves.append(fcurve)
                elif dp == 'sensor_width':
                    sensor_fcurves.append(fcurve)
        else:
            _append_blender_debug_log("[DIAG][FOV] action has no fcurves attribute")

    if not lens_fcurves and not sensor_fcurves:
        _append_blender_debug_log("[DIAG][FOV] no lens/sensor_width fcurves found")
        return []

    _append_blender_debug_log("[DIAG][FOV] found %d lens + %d sensor_width fcurves" % (len(lens_fcurves), len(sensor_fcurves)))

    from math import degrees, atan

    # Collect all keyframe times (union)
    times = set()
    for fc in lens_fcurves:
        for kp in fc.keyframe_points:
            times.add(int(kp.co.x))
    for fc in sensor_fcurves:
        for kp in fc.keyframe_points:
            times.add(int(kp.co.x))

    sorted_times = sorted(times)

    # Build evaluators
    def _eval_first(curves, frame, fallback):
        for fc in curves:
            try:
                return fc.evaluate(frame)
            except Exception:
                continue
        return fallback

    lens_fallback = getattr(camera, 'lens', 50.0)
    sensor_fallback = getattr(camera, 'sensor_width', 36.0)

    import math

    entries = []
    for frame in sorted_times:
        lens_val = _eval_first(lens_fcurves, frame, lens_fallback)
        sensor_val = _eval_first(sensor_fcurves, frame, sensor_fallback)

        if not math.isfinite(lens_val) or not math.isfinite(sensor_val):
            continue
        if lens_val <= 0.0 or sensor_val <= 0.0:
            continue

        fov_deg = degrees(2.0 * atan(sensor_val / (2.0 * lens_val)))

        if not math.isfinite(fov_deg) or fov_deg <= 0.0:
            continue

        entries.append((
            guid_obj,
            frame,
            round(fov_deg, 4),
            11,
        ))

    _append_blender_debug_log("[DIAG][FOV] returning %d keyframe entries" % len(entries))
    return entries


def _hash_keyframes(entries):
    """Compute FNV-1a 32-bit hash of keyframe entries for duplicate detection."""
    if not entries:
        return 0
    h = 2166136261
    for guid_or_obj, frame, value, channel in entries:
        if isinstance(guid_or_obj, bytes):
            guid_data = guid_or_obj
        else:
            guid_data = pack_ue_fguid(guid_or_obj)
        for b in guid_data:
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
    # DETECT NEW TRACKED OBJECTS
    # =====================================================

    new_count = 0

    for obj in bpy.data.objects:

        if obj.type not in SUPPORTED_TRACKED_TYPES:
            continue

        guid = ensure_unique_guid(obj, tracked_objects)

        if guid not in tracked_objects:

            tracked_objects[guid] = (
                obj,
                UUID(guid)
            )

            _append_blender_debug_log(
                f"[DISCOVER] guid={guid} type={obj.type} name={obj.name}"
            )

            new_count += 1

    if new_count > 0 or stale_handled > 0:

        _reconcile_guids_on_load()

    return stale_handled, new_count


# =========================================================
# CAMERA SIGNATURE
# =========================================================

def _build_camera_signature(camera_data):
    is_ortho = (camera_data.type == 'ORTHO')
    return (
        camera_data.lens,
        camera_data.sensor_width,
        camera_data.sensor_height,
        camera_data.clip_start,
        camera_data.clip_end,
        _ue_ortho_scale(camera_data),
        is_ortho,
    )


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
    global _camera_def_packets_sent
    global _sequencer_op_packets_sent
    global _sequencer_op_state_changes
    global _keyframe_sequence
    global _keyframe_packets_sent
    global _keyframes_sent
    global _animated_objects_scanned
    global _last_keyframe_action
    global _last_playback_state
    global _last_timeline_state
    global _net_playback_sequence
    global _net_playback_packets_sent
    global _net_playback_state_changes
    global _timeline_sequence
    global _timeline_packets_sent
    global _timeline_state_changes

    if not timer_running:
        return 0.016

    # DIAG: log every timer invocation (timestamp + monotonic tick count)
    try:
        _diag_ts = time.time()
        _diag_mono = time.monotonic()
        _diag_qsize = 0
        try:
            from . import network as _net_diag
            _cl = getattr(_net_diag, '_client', None)
            if _cl is not None:
                _diag_qsize = _cl._send_queue.qsize()
        except Exception:
            pass
        with open("/home/nguyennongngockhanh/.cache/uelivesync/uelivesync_blender_debug.log", "a") as _df:
            _df.write(f"[DIAG][TIMER] ts={_diag_ts:.6f} mono={_diag_mono:.6f} qsize={_diag_qsize}\n")
    except Exception:
        pass

    # First-tick diagnostic
    if _sync_start_time > 0 and time.time() - _sync_start_time < 0.1:
        print(
            "[LiveSync] Timer callback fired — main loop active",
            flush=True
        )

    _update_runtime_stats()

    _burst_packet_count = 0

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
        global _last_material_identity
        global _last_material_property_sig
        global _last_material_sent_reason
        global _last_decision_init_printed
        _known_guids.clear()
        clear_all_sequences()
        _last_collection_state.clear()
        _collection_anti_loop_guids.clear()
        _last_active_camera_guid = b''  # Phase 7D: resend on next tick
        _last_keyframe_action.clear()  # Phase 7E Stage 8: resend keyframes on reconnect
        # Phase 7H Task 3: clear material caches on reconnect to force full snapshot
        _last_material_identity.clear()
        _last_material_property_sig.clear()
        _last_material_sent_reason.clear()
        _mat_create_sent_names.clear()
        _last_material_sent_props.clear()  # Phase 1.4.2b
        _last_decision_init_printed.clear()
        _last_sidecar_digest.clear()
        _last_sidecar_info.clear()
        _last_camera_signature.clear()

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
                    primitive_type=_get_primitive_type(obj),
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
            _burst_packet_count += 1

        if snapshot_children:

            send_objects(
                snapshot_children,
                packet_type=0x03,
                flags=0x02 | 0x01
            )
            _burst_packet_count += 1

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
    delete_msgs_to_send = []
    asset_defs_to_send = []
    renames_to_send = []
    object_visibility_msgs_to_send = []
    object_update_msgs_to_send = []
    collection_payloads_to_send = []
    material_payloads_to_send = []
    material_creates_to_send = []
    material_updates_to_send = []
    mesh_payloads_to_send = []
    object_create_msgs_to_send = []
    object_reparent_msgs_to_send = []

    # =====================================================
    # MATSTALL: track GUIDs that had material changes this tick
    # for transform-side diagnostics.
    # =====================================================
    _mat_stall_guids = set()

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
    # Emit OBJECT_DELETE via MsgType for each.
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

            delete_msgs_to_send.append(
                (MsgType.OBJECT_DELETE, build_object_delete(guid_obj))
            )

            # Cleanup per-GUID state for deleted object
            _last_object_names.pop(guid, None)
            _last_visibility_state.pop(guid, None)
            _last_parent_guid.pop(guid, None)
            _last_mesh_identity.pop(guid, None)
            _last_collection_state.pop(guid, None)  # Phase 6F: cleanup collection state
            _last_material_identity.pop(guid, None)  # Phase 7B
            _last_geometry_version.pop(guid, None)  # Phase 7C
            _last_camera_signature.pop(guid, None)  # Phase 10A.2

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
            _last_camera_signature.pop(guid, None)  # Phase 10A.2

            deletes_to_send.append(
                serialize_delete_v3(guid_obj)
            )

            # Phase 6E: also emit OBJECT_DELETE via MsgType
            delete_msgs_to_send.append(
                (MsgType.OBJECT_DELETE, build_object_delete(guid_obj))
            )

            continue

        transform = get_transform(obj)

        previous = last_sent_transforms.get(
            guid
        )
        is_first_send = (
            previous is None
        )

        _is_mat_stall_target = (guid in _mat_stall_guids if hasattr(_mat_stall_guids, '__contains__') else False)

        if transforms_different(
            transform,
            previous
        ):

            # MATSTALL: log transform send for material-tracked targets.
            if _verbose_logging or _is_mat_stall_target or obj.name == "Suzanne":
                print(
                    f"[MATSTALL][BLENDER] transform_sent guid={guid} "
                    f"obj={obj.name} first_send={is_first_send} "
                    f"loc=({transform['location'][0]:.4f},{transform['location'][1]:.4f},{transform['location'][2]:.4f}) "
                    f"rot=({transform['rotation'][0]:.4f},{transform['rotation'][1]:.4f},{transform['rotation'][2]:.4f},{transform['rotation'][3]:.4f}) "
                    f"scl=({transform['scale'][0]:.4f},{transform['scale'][1]:.4f},{transform['scale'][2]:.4f})"
                )

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
                    primitive_type=_get_primitive_type(obj),
                )

            except Exception as e:

                set_critical_error(
                    f"Serialization failed for {obj.name}: {e}"
                )

                _runtime_stats["serialization_failures"] += 1

                continue

            has_parent = (
                parent_guid_obj is not None
            )

            if has_parent:

                if is_first_send:

                    children_create.append(
                        serialized
                    )

                    # Phase 1.4.4: MsgType OBJECT_CREATE alongside legacy PT_Create
                    _spawn_loc = transform["location"]
                    _spawn_rot = transform["rotation"]
                    _spawn_scl = transform["scale"]
                    print(f"[SPAWN-TRACE][SEND] guid={guid_obj} name={obj.name} loc=({_spawn_loc[0]:.1f},{_spawn_loc[1]:.1f},{_spawn_loc[2]:.1f}) rot=({_spawn_rot[0]:.4f},{_spawn_rot[1]:.4f},{_spawn_rot[2]:.4f},{_spawn_rot[3]:.4f}) scl=({_spawn_scl[0]:.4f},{_spawn_scl[1]:.4f},{_spawn_scl[2]:.4f})")
                    _append_blender_debug_log(
                        f"[SPAWN-TRACE][SEND] guid={guid_obj} name={obj.name} loc=({_spawn_loc[0]:.1f},{_spawn_loc[1]:.1f},{_spawn_loc[2]:.1f})"
                    )
                    _obj_ts = time.time()
                    _obj_seq = next_create_sequence(guid_obj)
                    object_create_msgs_to_send.append(
                        (MsgType.OBJECT_CREATE, build_object_create(
                            persistent_id=guid_obj,
                            name=obj.name,
                            location=transform["location"],
                            rotation=transform["rotation"],
                            scale=transform["scale"],
                            parent_id=parent_guid_obj,
                            primitive_type=_get_primitive_type(obj),
                            sequence_number=_obj_seq,
                            timestamp=_obj_ts,
                        ))
                    )

                else:

                    children_to_send.append(
                        serialized
                    )
                    # OBJECT_UPDATE alongside PT_Transform for non-first-send children
                    _obj_ts = time.time()
                    _obj_seq = next_update_sequence(guid_obj)
                    object_update_msgs_to_send.append(
                        (MsgType.OBJECT_UPDATE, build_object_update(
                            persistent_id=guid_obj,
                            location=transform["location"],
                            rotation=transform["rotation"],
                            scale=transform["scale"],
                            name=obj.name,
                            visibility=0 if obj.hide_get() else 1,
                            sequence_number=_obj_seq,
                            timestamp=_obj_ts,
                        ))
                    )

            else:

                if is_first_send:

                    create_objects.append(
                        serialized
                    )

                    # Phase 1.4.4: MsgType OBJECT_CREATE alongside legacy PT_Create
                    _spawn_loc = transform["location"]
                    _spawn_rot = transform["rotation"]
                    _spawn_scl = transform["scale"]
                    print(f"[SPAWN-TRACE][SEND] guid={guid_obj} name={obj.name} loc=({_spawn_loc[0]:.1f},{_spawn_loc[1]:.1f},{_spawn_loc[2]:.1f}) rot=({_spawn_rot[0]:.4f},{_spawn_rot[1]:.4f},{_spawn_rot[2]:.4f},{_spawn_rot[3]:.4f}) scl=({_spawn_scl[0]:.4f},{_spawn_scl[1]:.4f},{_spawn_scl[2]:.4f})")
                    _append_blender_debug_log(
                        f"[SPAWN-TRACE][SEND] guid={guid_obj} name={obj.name} loc=({_spawn_loc[0]:.1f},{_spawn_loc[1]:.1f},{_spawn_loc[2]:.1f})"
                    )
                    _obj_ts = time.time()
                    _obj_seq = next_create_sequence(guid_obj)
                    object_create_msgs_to_send.append(
                        (MsgType.OBJECT_CREATE, build_object_create(
                            persistent_id=guid_obj,
                            name=obj.name,
                            location=transform["location"],
                            rotation=transform["rotation"],
                            scale=transform["scale"],
                            parent_id=parent_guid_obj,
                            primitive_type=_get_primitive_type(obj),
                            sequence_number=_obj_seq,
                            timestamp=_obj_ts,
                        ))
                    )

                else:

                    objects_to_send.append(
                        serialized
                    )
                    # OBJECT_UPDATE alongside PT_Transform for non-first-send roots
                    _obj_ts = time.time()
                    _obj_seq = next_update_sequence(guid_obj)
                    object_update_msgs_to_send.append(
                        (MsgType.OBJECT_UPDATE, build_object_update(
                            persistent_id=guid_obj,
                            location=transform["location"],
                            rotation=transform["rotation"],
                            scale=transform["scale"],
                            name=obj.name,
                            visibility=0 if obj.hide_get() else 1,
                            sequence_number=_obj_seq,
                            timestamp=_obj_ts,
                        ))
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

        # MATSTALL: log when transform NOT sent (same transform).
        # Only for material-tracked targets or Suzanne to avoid spam.
        if (not is_first_send) and (_is_mat_stall_target or obj.name == "Suzanne"):
            print(
                f"[MATSTALL][BLENDER] transform_skipped guid={guid} "
                f"obj={obj.name} reason=same_transform"
            )

        # Phase 6: Visibility detection (semantic event)
        # NOTE: Lives OUTSIDE the transforms_different gate so that
        # visibility changes are detected even when the object does
        # not move.
        # On first tick: send visibility if object is hidden (initial state).
        # On subsequent ticks: send if visibility changed.
        current_vis = obj.hide_get()
        prev_vis = _last_visibility_state.get(guid)
        if prev_vis is None:
            if current_vis:
                object_visibility_msgs_to_send.append(
                    (MsgType.OBJECT_VISIBILITY, build_object_visibility(
                        guid_obj, False))
                )
        elif prev_vis != current_vis:
            object_visibility_msgs_to_send.append(
                (MsgType.OBJECT_VISIBILITY, build_object_visibility(
                    guid_obj, not current_vis))
            )
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
                (MsgType.OBJECT_RENAME, build_object_rename(guid_obj, current_name))
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
            # Phase 1.4: Send via MsgType OBJECT_REPARENT through Bridge
            # (replaces legacy PT_Hierarchy which UE removed in Phase 1.3.5a)
            object_reparent_msgs_to_send.append(
                (MsgType.OBJECT_REPARENT, build_object_reparent(
                    guid_obj,
                    parent_guid_obj_for_hierarchy,
                ))
            )
            if _verbose_logging:
                parent_str = current_parent_guid if current_parent_guid else "(root)"
                prev_parent_str = prev_parent_guid if prev_parent_guid else "(root)"
                print(f"[HIERARCHY][DIAG] Parent change detected")
                print(f"[HIERARCHY][DIAG] Child guid={guid}")
                print(f"[HIERARCHY][DIAG] Old parent={prev_parent_str}")
                print(f"[HIERARCHY][DIAG] New parent={parent_str}")
                print(f"[HIERARCHY][DIAG] MsgType OBJECT_REPARENT queued")
        _last_parent_guid[guid] = current_parent_guid

        # When parent changes, get_transform() switches between
        # matrix_local and matrix_world.  Clear-Parent keeps
        # matrix_local unchanged so matrix_world == matrix_local
        # after detach, making transforms_different() return False
        # even though the world position changed.
        # Invalidating the cache forces one PT_Transform on the
        # next tick to reflect the new world transform.
        if prev_parent_guid != current_parent_guid:
            # Set sentinel cache instead of popping. Popping makes
            # previous=None on next tick → is_first_send=True →
            # OBJECT_CREATE instead of PT_Transform → UE skips spawn
            # (actor exists) → no transform update after detach.
            # Sentinel guarantees transforms_different() returns True
            # while is_first_send stays False → routes to PT_Transform.
            last_sent_transforms[guid] = {
                "location": [999999.0, 999999.0, 999999.0],
                "rotation": [0.0, 0.0, 0.0, 0.0],
                "scale": [0.0, 0.0, 0.0],
            }

        # =================================================
        # Phase 7B Stage 1C: Material slot identity detection
        # Sends PT_Material only when slot identities change.
        # Suppressed on first send (prevents emit on startup/reconnect)
        # and when no change is detected.
        #
        # NOTE: Material packets are stored but NOT assigned to UE
        # components yet. SetMaterial() is deferred to Stage 2.
        # =================================================

        # Phase 10J: exception-isolated material detection
        # Material assignment can trigger transient mesh-data rebuild
        # where obj.material_slots throws RuntimeError.
        # =====================================================
        # MATSTALL diagnostics: log material state transitions.
        # =====================================================
        _mat_stall_name = obj.name
        _mat_stall_prev = _last_material_identity.get(guid)
        try:
            current_slots = get_object_material_slots(obj)
        except Exception as _mat_exc:
            # MATSTALL: log the exception so we can detect depsgraph
            # rebuild spurious empty-slot emission.
            if _verbose_logging or _mat_stall_name == "Suzanne":
                print(
                    f"[MATSTALL][BLENDER] mat_slots_threw guid={guid} "
                    f"obj={_mat_stall_name} exc={_mat_exc} "
                    f"prev_slots_count={len(_mat_stall_prev) if _mat_stall_prev is not None else 'None'}"
                )
            # Do NOT re-raise — must not break transform sync loop
            current_slots = {}

        prev_slots = _last_material_identity.get(guid)
        is_first_material = (prev_slots is None)

        # Phase 10J.5I + Phase 7H: also compare material property signatures
        # AND texture metadata hashes to detect BaseColor texture changes.
        bPropertiesChanged = False
        current_prop_sig = None
        current_tex_sigs = {}  # slot_index -> (low64, high64)
        try:
            current_prop_sig = {}
            for slot_index, slot in enumerate(obj.material_slots):
                if slot and slot.material:
                    p = get_material_basic_properties(slot.material)
                    if p is not None:
                        current_prop_sig[slot_index] = (
                            p.get("BaseColorR", 0.0),
                            p.get("BaseColorG", 0.0),
                            p.get("BaseColorB", 0.0),
                            p.get("Alpha", 1.0),
                            p.get("Roughness", 0.5),
                            p.get("Metallic", 0.0),
                        )
        except Exception:
            current_prop_sig = None

        # Task 9B.6B.14: collect material basic properties for transaction summary
        _mt_basic_start_collecting(_network_mod._sequence_id, guid)

        # Task 9B.6B.13: start collecting MTEX records for timer tick summary
        _mtex_start_collecting(_network_mod._sequence_id, guid)

        # Phase 7H: compute per-slot texture hash (exception-isolated)
        current_tex_maps = None
        current_tex_sigs = {}
        _mat_dirty_error = None
        scalar_changed = False
        tex_changed = False
        try:
            if current_prop_sig is not None:
                current_tex_maps = {}
                for slot_index, slot in enumerate(obj.material_slots):
                    if slot and slot.material:
                        # Task 9B.6B.13: suppress per-call summary on timer ticks
                        maps = extract_texture_maps_for_slot(slot.material, slot.material.name, slot_index, _suppress_summary=True, _collect=True)
                        if maps:
                            current_tex_maps[slot_index] = maps
                            tex_hash = compute_material_texture_hash(slot_index, maps)
                            current_tex_sigs[slot_index] = tex_hash

            # Phase 10A.2: defensive initialize reason_log before decision branches
            reason_log = "property_unchanged"
            # Print DECISION_INIT only once per GUID session to avoid tick-spam.
            if guid not in _last_decision_init_printed:
                _last_decision_init_printed.add(guid)
                print(f"[MATERIAL][DECISION_INIT] guid={guid} reason_log=property_unchanged")
            if is_first_material:
                bPropertiesChanged = True
                reason_log = "first_material_send"
                _slot_count_for_log = len(current_slots) if current_slots else 0
                print(f"[MATERIAL][FIRST_SEND_FULL_SNAPSHOT] guid={guid[:8]} slots={_slot_count_for_log}")
            elif current_slots != prev_slots:
                bPropertiesChanged = True
                reason_log = "slots_changed"
            elif current_prop_sig:
                prev_prop_sig = _last_material_property_sig.get(guid)
                scalar_changed = True
                if prev_prop_sig is not None:
                    _scalar_len = len(next(iter(current_prop_sig.values())))
                    prev_scalar = {si: vals[:_scalar_len] for si, vals in prev_prop_sig.items()}
                    scalar_changed = current_prop_sig != prev_scalar
                tex_changed = False
                if prev_prop_sig is not None and len(prev_prop_sig) == len(current_prop_sig):
                    prev_tex_sigs = {}
                    for si in prev_prop_sig:
                        prev_tex = prev_prop_sig[si][6:] if len(prev_prop_sig[si]) > 6 else ()
                        if si in current_tex_sigs or any(v != 0 for v in prev_tex):
                            prev_tex_sigs[si] = prev_tex
                    tex_changed = (current_tex_sigs != prev_tex_sigs)
                # Phase 7H: log signature comparison outcome for diagnostics
                # Only log when something changed or cache missing (suppress noise on unchanged ticks)
                if scalar_changed or tex_changed or prev_prop_sig is None:
                    print(f"[MATERIAL][SIG_COMPARE] guid={guid[:8]} "
                          f"prevExists={int(prev_prop_sig is not None)} "
                          f"scalarChanged={int(scalar_changed)} "
                          f"texChanged={int(tex_changed)}")
                if scalar_changed and tex_changed:
                    bPropertiesChanged = True
                    reason_log = "property_and_texture_changed"
                elif scalar_changed:
                    bPropertiesChanged = True
                    reason_log = "property_changed"
                elif tex_changed:
                    bPropertiesChanged = True
                    reason_log = "texture_changed"
        except Exception as _mat_exc:
            _mat_dirty_error = _mat_exc
            print(f"[MATERIAL][DIRTY_HASH_ERROR] guid={guid} error={_mat_exc} action=send_material_fallback")
            _append_blender_debug_log(
                f"[MAT][ERROR] guid={guid} error={_mat_exc}"
            )
            # Conservative fallback: send material to avoid staleness
            bPropertiesChanged = True
            current_tex_sigs = {}
            current_tex_maps = None
            reason_log = "hash_error_fallback"
            scalar_changed = True  # force value send
            tex_changed = False

        # Phase 7H: log scalar channel scan for diagnostic
        if current_prop_sig is not None and bPropertiesChanged:
            _mat_stall_cur_count = len(current_slots)
            if _verbose_logging or _mat_stall_name == "Suzanne":
                bc_vals = []
                for si in sorted(current_prop_sig.keys()):
                    p = current_prop_sig[si]
                    bc_vals.append(f"slot={si} color=({p[0]:.3f},{p[1]:.3f},{p[2]:.3f},{p[3]:.3f}) roughness={p[4]:.3f} metallic={p[5]:.3f}")
                print(f"[MATERIAL][SCALAR_CHANNEL_SCAN] object={_mat_stall_name} material={obj.name} slots={[str(s) for s in sorted(current_prop_sig.keys())]} {';'.join(bc_vals)}")

        # Material send (wrapped in try/except — must not kill transform loop)
        try:
            if bPropertiesChanged and current_slots:
                _mat_stall_prev_count = len(prev_slots) if prev_slots is not None else 0
                if _verbose_logging or _mat_stall_name == "Suzanne":
                    print(
                        f"[MATSTALL][BLENDER] mat_changed guid={guid} "
                        f"obj={_mat_stall_name} prev_count={_mat_stall_prev_count} "
                        f"cur_count={_mat_stall_cur_count} "
                        f"prev_keys={list(prev_slots.keys()) if prev_slots else 'None'} "
                        f"cur_keys={list(current_slots.keys()) if current_slots else 'None'}"
                    )
                # Phase 10J.5I + Phase 7H: log dirty reason with texture hash info
                _mat_reason = "identity" if current_slots != prev_slots else "properties"
                _mat_reason_log = "first_material_send" if is_first_material else _mat_reason
                if reason_log and reason_log not in ("identity", "properties"):
                    _mat_reason_log = reason_log
                elif not scalar_changed and tex_changed:
                    _mat_reason_log = "texture_changed"
                scalar_hash, tex_hash_val, combined_hash_val = (0, 0, 0)
                if current_prop_sig is not None and current_tex_sigs is not None:
                    scalar_hash, tex_hash_val, combined_hash_val = compute_material_dirty_sig(current_prop_sig, current_tex_sigs)
                print(f"[MATERIAL][DIRTY_HASH] guid={guid} "
                      f"scalarHash={scalar_hash} textureHash={tex_hash_val} "
                      f"combinedHash={combined_hash_val}")
                print(f"[MATERIAL][DIRTY_DECIDE] guid={guid} "
                      f"property_changed={bPropertiesChanged} "
                      f"reason={_mat_reason_log} "
                      f"slots={_mat_stall_cur_count}")
                print(f"[SYNC][DECIDE] seq={_mat_stall_cur_count} guid={guid[:8]} "
                      f"sendMAT=1 reason={_mat_reason_log}")
                _append_blender_debug_log(
                    f"[MAT][SIG] guid={guid} "
                    f"reason={_mat_reason_log} "
                    f"slots={_mat_stall_cur_count}"
                )

                # Phase 10J.5H: extract basic material properties for each slot
                mat_props = None
                try:
                    mat_props = {}
                    for slot_index, slot in enumerate(obj.material_slots):
                        if slot and slot.material:
                            p = get_material_basic_properties(slot.material)
                            if p is not None:
                                mat_props[slot_index] = p
                                # Task 9B.6B.14: collect for transaction summary
                                _mt_basic_collect_slot(slot_index, p)
                except Exception:
                    mat_props = None

                # Phase 1.4.2a: collect MATERIAL_CREATE for unique materials
                _mc_before = len(material_creates_to_send)
                try:
                    for slot_index, slot in enumerate(obj.material_slots):
                        if not slot or not slot.material:
                            continue
                        mat = slot.material
                        mat_name = mat.name
                        if mat_name in _mat_create_sent_names:
                            continue
                        _mat_create_sent_names.add(mat_name)
                        low, high = get_material_identity_hash(mat)
                        mat_uuid = uuid.UUID(
                            int=((high & 0xFFFFFFFFFFFFFFFF) << 64)
                            | (low & 0xFFFFFFFFFFFFFFFF)
                        )
                        p = get_material_basic_properties(mat)
                        if p is None:
                            continue
                        bc = (
                            p.get("BaseColorR", 0.8),
                            p.get("BaseColorG", 0.8),
                            p.get("BaseColorB", 0.8),
                            p.get("Alpha", 1.0),
                        )
                        metallic = p.get("Metallic", 0.0)
                        roughness = p.get("Roughness", 0.5)
                        emission = (0.0, 0.0, 0.0)
                        body = build_material_create(
                            material_id=mat_uuid,
                            name=mat_name,
                            base_color=bc,
                            metallic=metallic,
                            roughness=roughness,
                            emission=emission,
                        )
                        material_creates_to_send.append(
                            (MsgType.MATERIAL_CREATE, body)
                        )
                        _last_material_sent_props[mat_name] = (bc, metallic, roughness)
                except Exception as _mc_exc:
                    print(f"[MATERIAL][MSGTYPE][ERROR] {_mc_exc}")
                _mc_after = len(material_creates_to_send)
                if _mc_after > _mc_before:
                    print(f"[MATERIAL][MSGTYPE] collected {_mc_after - _mc_before} MATERIAL_CREATE for guid={guid[:8]}")

                # Phase 1.4.2b: collect MATERIAL_UPDATE for materials
                # that already exist on UE side but have changed properties.
                # Skip materials just created in this same pass (MATERIAL_CREATE covers them).
                _mu_before = len(material_updates_to_send)
                if bPropertiesChanged and mat_props:
                    for slot_index, slot in enumerate(obj.material_slots):
                        if not slot or not slot.material:
                            continue
                        mat = slot.material
                        mat_name = mat.name
                        if mat_name in _mat_create_sent_names:
                            continue
                        low, high = get_material_identity_hash(mat)
                        mat_uuid = uuid.UUID(
                            int=((high & 0xFFFFFFFFFFFFFFFF) << 64)
                            | (low & 0xFFFFFFFFFFFFFFFF)
                        )
                        p = get_material_basic_properties(mat)
                        if p is None:
                            continue
                        bc = (
                            p.get("BaseColorR", 0.8),
                            p.get("BaseColorG", 0.8),
                            p.get("BaseColorB", 0.8),
                            p.get("Alpha", 1.0),
                        )
                        metallic = p.get("Metallic", 0.0)
                        roughness = p.get("Roughness", 0.5)
                        emission = (0.0, 0.0, 0.0)
                        prev = _last_material_sent_props.get(mat_name)
                        if prev is None:
                            continue
                        prev_bc, prev_metal, prev_rough = prev
                        if (bc == prev_bc and metallic == prev_metal
                                and roughness == prev_rough):
                            continue
                        body = build_material_update(
                            material_id=mat_uuid,
                            base_color=bc,
                            metallic=metallic,
                            roughness=roughness,
                            emission=emission,
                        )
                        material_updates_to_send.append(
                            (MsgType.MATERIAL_UPDATE, body)
                        )
                        _last_material_sent_props[mat_name] = (bc, metallic, roughness)
                _mu_after = len(material_updates_to_send)
                if _mu_after > _mu_before:
                    print(f"[MATERIAL][MSGTYPE] collected {_mu_after - _mu_before} MATERIAL_UPDATE for guid={guid[:8]}")

                # Phase 7H: log MATX_VALUE_SEND for each slot/channel
                if mat_props:
                    for si in mat_props:
                        pp = mat_props[si]
                        print(f"[MATERIAL][MATX_VALUE_SEND] guid={guid[:8]} slot={si} channel=BaseColor value=({pp.get('BaseColorR',0):.3f},{pp.get('BaseColorG',0):.3f},{pp.get('BaseColorB',0):.3f},{pp.get('Alpha',1):.3f})")
                        print(f"[MATERIAL][MATX_VALUE_SEND] guid={guid[:8]} slot={si} channel=Roughness value={pp.get('Roughness',0.5):.3f}")
                        print(f"[MATERIAL][MATX_VALUE_SEND] guid={guid[:8]} slot={si} channel=Metallic value={pp.get('Metallic',0):.3f}")
                        print(f"[MATERIAL][MATX_VALUE_SEND] guid={guid[:8]} slot={si} channel=Alpha value={pp.get('Alpha',1):.3f}")

                # Phase 10K.1: extract texture map references for each slot
                tex_maps = None
                try:
                    tex_maps = {}
                    for slot_index, slot in enumerate(obj.material_slots):
                        if slot and slot.material:
                            # Task 9B.6B.13: suppress per-call summary on timer ticks
                            maps = extract_texture_maps_for_slot(slot.material, slot.material.name, slot_index, _suppress_summary=True, _collect=True)
                            if maps:
                                tex_maps[slot_index] = maps
                except Exception:
                    tex_maps = None

                # Phase 10K.1: log MATX_TEXTURE_SEND for each slot/channel
                if tex_maps:
                    for slot_idx, slot_maps in tex_maps.items():
                        for ch, fpath, img_name, flags in slot_maps:
                            ch_name = {1: "BaseColor", 2: "Roughness", 3: "Metallic", 4: "Alpha", 5: "Normal"}.get(ch, "Unknown")
                            abs_path = bpy.path.abspath(fpath) if fpath else ""
                            file_exists = os.path.isfile(abs_path) if abs_path else False
                            print(f"[MATERIAL][MATX_TEXTURE_SEND] guid={guid[:8]} slot={slot_idx} channel={ch_name} path={abs_path[:200] if abs_path else ''} exists={'1' if file_exists else '0'}")

                try:
                    material_payloads_to_send.append(
                        serialize_material_slots(guid_obj, current_slots, mat_props, tex_maps)
                    )
                except Exception as _send_exc:
                    if _verbose_logging:
                        print(
                            f"[MATERIAL][ERROR] serialize_material_slots failed "
                            f"for {obj.name}: {_send_exc} — skipping"
                        )
                    continue
                if _verbose_logging:
                    print(f"[MATERIAL][DIAG] Material change detected guid={guid}")
                    print(f"[MATERIAL][DIAG] Slots={current_slots}")
                # MATSTALL: track for transform diagnostics
                _mat_stall_guids.add(guid)
                if is_first_material:
                    _sn_complete_slots = len(current_slots) if current_slots else 0
                    print(f"[MATERIAL][SESSION_SNAPSHOT_COMPLETE] guid={guid[:8]} slots={_sn_complete_slots}")

            _last_material_identity[guid] = current_slots

            # Phase 10J.5I + Phase 7H: update property signature for dirty detection.
            # Store texture hash tuples appended to each slot's prop tuple.
            if current_prop_sig is not None:
                merged_sig = {}
                for si in current_prop_sig:
                    prop_tuple = current_prop_sig[si]
                    tex_tuple = current_tex_sigs.get(si, (0, 0))
                    merged_sig[si] = prop_tuple + tuple(tex_tuple)
                _last_material_property_sig[guid] = merged_sig
                if bPropertiesChanged and current_slots:
                    _sh, _th, _ch = compute_material_dirty_sig(current_prop_sig, current_tex_sigs)
                    print(f"[MATERIAL][SIG_CACHE_UPDATE] guid={guid} slots={len(merged_sig)} scalarHash={_sh} textureHash={_th} combinedHash={_ch} reason={reason_log}")
                    _last_material_sent_reason[guid] = reason_log
                else:
                    _prev_reason = _last_material_sent_reason.pop(guid, None)
                    if _prev_reason is not None:
                        print(f"[MATERIAL][SIG_CACHE_HIT] guid={guid} reason=property_unchanged")
            elif guid in _last_material_property_sig:
                del _last_material_property_sig[guid]
        except Exception as _outer_mat_exc:
            print(f"[MATERIAL][SYNC_BLOCK_ERROR] guid={guid} error={_outer_mat_exc} action=skip_material_keep_transform")
            print(f"[LIVESYNC][CHECK_UPDATES_SURVIVED_MATERIAL_ERROR] guid={guid}")

        # =================================================
        # Phase 7C Stage 1D: Geometry change detection
        # Evaluates the depsgraph mesh and sends PT_Mesh
        # chunks when the geometry version hash changes.
        #
        # Suppressed on first send (is_first_send) to avoid
        # flooding on reconnect. On the first evaluation
        # (prev_hash is None), the mesh IS emitted so that
        # newly created objects transmit their geometry.
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
                    if prev_hash is None or current_hash != prev_hash:
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

        try:
            _diag_keyframe_skipped
        except NameError:
            _diag_keyframe_skipped = set()
        _kf_effective = is_keyframe_effective()
        if not _kf_effective:
            pass
        if _kf_effective and not is_first_send:
            try:
                object_entries = _extract_keyframes(obj, guid_obj)
            except Exception:
                object_entries = []

            camera_entries = []
            if obj.type == 'CAMERA' and is_camera_fov_keyframe_effective():
                try:
                    camera_entries = _extract_camera_fov_keyframes(obj, guid_obj)
                except Exception:
                    camera_entries = []
                if not camera_entries:
                    _append_blender_debug_log("[DIAG][FOV] _extract_camera_fov_keyframes returned empty for %s" % obj.name)

            kf_entries = object_entries + camera_entries
            if kf_entries:
                kf_entries.sort(key=lambda e: (e[1], e[3]))
                _animated_objects_scanned += 1
                kf_hash = _hash_keyframes(kf_entries)
                prev_hash = _last_keyframe_action.get(guid)

                if prev_hash is None or kf_hash != prev_hash:
                    _last_keyframe_action[guid] = kf_hash
                    _append_blender_debug_log("[DIAG][FOV] SENDING %d keyframes for %s (prev_hash=%s new_hash=%s)" % (
                        len(kf_entries), obj.name, prev_hash, kf_hash,
                    ))

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
                            _burst_packet_count += 1
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
    # SEND OBJECT DELETE via MsgType (MIG-001)
    # Full semantic: sequence_number + timestamp for
    # stale-rejection and tombstone on UE side.
    # =====================================================

    if delete_msgs_to_send:
        transport = get_transport()
        if transport is not None:
            sent_count = 0
            for msg_type, body in delete_msgs_to_send:
                if transport.send_msg(msg_type, body):
                    sent_count += 1
            if _verbose_logging:
                print(f"[DELETE][MSGTYPE] Sent {sent_count} OBJECT_DELETE via MsgType")
            _append_blender_debug_log(
                f"[DELETE][MSGTYPE] OBJECT_DELETE sent={sent_count}"
            )
            _burst_packet_count += 1

    # =====================================================
    # SEND DELETE PACKETS (V3 legacy — unreachable, kept
    # for backward compat until fully removed in Phase 1.5)
    # =====================================================

    if deletes_to_send:

        send_objects(
            deletes_to_send,
            packet_type=0x04
        )
        _burst_packet_count += 1

    # =====================================================
    # SEND CREATE PACKETS (first-time objects, roots)
    # =====================================================

    if create_objects:

        send_objects(
            create_objects,
            packet_type=0x03
        )
        _burst_packet_count += 1

    # =====================================================
    # SEND CREATE PACKETS (first-time objects, children)
    # =====================================================

    if children_create:

        send_objects(
            children_create,
            packet_type=0x03,
            flags=0x01
        )
        _burst_packet_count += 1

    # =====================================================
    # SEND OBJECT CREATE via MsgType (Phase 1.4.4)
    # Alongside legacy PT_Create for verification.
    # =====================================================

    if object_create_msgs_to_send:
        transport = get_transport()
        if transport is not None:
            sent_count = 0
            for msg_type, body in object_create_msgs_to_send:
                if transport.send_msg(msg_type, body):
                    sent_count += 1
            if _verbose_logging:
                print(f"[OBJECT][MSGTYPE] Sent {sent_count} OBJECT_CREATE via MsgType")
            _append_blender_debug_log(
                f"[OBJ][MSGTYPE] OBJECT_CREATE sent={sent_count}"
            )

    # =====================================================
    # SEND OBJECT UPDATE via MsgType (MIG-002)
    # Alongside legacy PT_Transform for transform changes
    # on existing objects.
    # =====================================================

    if object_update_msgs_to_send:
        transport = get_transport()
        if transport is not None:
            sent_count = 0
            for msg_type, body in object_update_msgs_to_send:
                if transport.send_msg(msg_type, body):
                    sent_count += 1
            if _verbose_logging:
                print(f"[OBJECT][MSGTYPE] Sent {sent_count} OBJECT_UPDATE via MsgType")
            _append_blender_debug_log(
                f"[OBJ][MSGTYPE] OBJECT_UPDATE sent={sent_count}"
            )

    # =====================================================
    # SEND OBJECT REPARENT via MsgType (Phase 1.4)
    # Hierarchy changes flow through Bridge → OnObjectReparent
    # =====================================================

    if object_reparent_msgs_to_send:
        transport = get_transport()
        if transport is not None:
            sent_count = 0
            for msg_type, body in object_reparent_msgs_to_send:
                if transport.send_msg(msg_type, body):
                    sent_count += 1
            if _verbose_logging:
                print(f"[OBJECT][MSGTYPE] Sent {sent_count} OBJECT_REPARENT via MsgType")
            _append_blender_debug_log(
                f"[OBJ][MSGTYPE] OBJECT_REPARENT sent={sent_count}"
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
        _burst_packet_count += 1

    # =====================================================
    # SEND RENAME (Phase 6 — MsgType OBJECT_RENAME)
    # =====================================================

    if renames_to_send:
        transport = get_transport()
        if transport:
            sent_count = 0
            for msg_type, body in renames_to_send:
                if transport.send_msg(msg_type, body):
                    sent_count += 1
            if sent_count > 0:
                _burst_packet_count += 1

    # =====================================================
    # SEND VISIBILITY (Phase 6 — MsgType OBJECT_VISIBILITY)
    # =====================================================

    if object_visibility_msgs_to_send:
        transport = get_transport()
        if transport:
            sent_count = 0
            for msg_type, body in object_visibility_msgs_to_send:
                if transport.send_msg(msg_type, body):
                    sent_count += 1
            if sent_count > 0:
                _burst_packet_count += 1

    # =====================================================
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
        _burst_packet_count += 1

    # =====================================================
    # SEND MATERIAL CREATE (Phase 1.4.2a — MsgType)
    # =====================================================

    if material_creates_to_send:
        transport = get_transport()
        if transport is not None:
            sent_count = 0
            for msg_type, body in material_creates_to_send:
                if transport.send_msg(msg_type, body):
                    sent_count += 1
            if _verbose_logging:
                print(f"[MATERIAL][MSGTYPE] Sent {sent_count} MATERIAL_CREATE via MsgType")
            _append_blender_debug_log(
                f"[MAT][MSGTYPE] MATERIAL_CREATE sent={sent_count}"
            )
            # Phase 1.4.2b: record sent state for MATERIAL_UPDATE tracking
            # (done per-slot during collection; this is a safety net if collection
            #  tracked names but didn't yet update _last_material_sent_props)

    # =====================================================
    # SEND MATERIAL UPDATE (Phase 1.4.2b — MsgType)
    # =====================================================

    if material_updates_to_send:
        transport = get_transport()
        if transport is not None:
            sent_count = 0
            for msg_type, body in material_updates_to_send:
                if transport.send_msg(msg_type, body):
                    sent_count += 1
            if _verbose_logging:
                print(f"[MATERIAL][MSGTYPE] Sent {sent_count} MATERIAL_UPDATE via MsgType")
            _append_blender_debug_log(
                f"[MAT][MSGTYPE] MATERIAL_UPDATE sent={sent_count}"
            )

    # =====================================================
    # SEND MATERIAL PACKETS (Phase 7B Stage 1C — PT_Material)
    # =====================================================

    if material_payloads_to_send:

        if _verbose_logging:
            print(f"[MATERIAL][SEND] Sending {len(material_payloads_to_send)} material slot packet(s)")

        # Phase 10J.5L: log to Blender debug file
        _append_blender_debug_log(
            f"[MAT][SEND] auto_sync count={len(material_payloads_to_send)}"
        )

        send_objects(
            material_payloads_to_send,
            packet_type=PT_Material,
            version=LIVE_SYNC_VERSION_V5
        )
        _burst_packet_count += 1

        # Task 9B.6B.13: emit MTEX timer tick summary (auto-sync path).
        _mtex_records = _mtex_collect_records
        if _mtex_records and _mtex_collecting:
            unique_keys = set()
            for slot_idx, ch, img_name, fpath, flags, is_packed in _mtex_records:
                unique_keys.add((img_name, ch))
            print(
                f"[MTEX][EXTRACT_SUMMARY] syncId={_network_mod._sequence_id} "
                f"object=_auto_sync slots={len(_mtex_records)} records={len(_mtex_records)} "
                f"uniqueRecords={len(unique_keys)}"
            )
        _mtex_clear_dedup_state()

        # Task 9B.6B.14: emit material basic property summary (auto-sync path)
        _mat_records = mat_basic_collect_records
        if _mat_records and _mat_basic_collecting:
            total_slots = len(_mat_records)
            # Count changed fields by comparing with stored property signatures
            total_changed = 0
            all_changed = []
            for si, props in _mat_records:
                # _last_material_property_sig is keyed by slot_index
                prev = sync._last_material_property_sig.get(si)
                if prev:
                    for field in props:
                        if field in prev and prev[field] != props[field]:
                            total_changed += 1
                            all_changed.append(f"slot{si}+{field}")
            _mat_collect_guid = _mat_basic_collect_guid[:8] if _mat_basic_collect_guid else "NONE"
            _mt_basic_changed_fields_str = ",".join(all_changed[:5]) if all_changed else ""
            _append_blender_debug_log(
                f"[MATERIAL][BASIC_EXTRACT_SUMMARY] syncId={_network_mod._sequence_id} "
                f"guid={_mat_collect_guid} object=_auto_sync "
                f"materialSlots={total_slots} materialsExamined={total_slots} "
                f"materialsChanged={total_changed} fields={_mt_basic_changed_fields_str}"
            )
            # Emit changed-record lines only when changes exist (one per changed field, limited)
            for _ch in all_changed[:5]:
                _append_blender_debug_log(
                    f"[MATERIAL][BASIC_CHANGED] syncId={_network_mod._sequence_id} "
                    f"guid={_mat_collect_guid} {_ch}"
                )
        _mt_basic_clear_state()

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
        _burst_packet_count += 1

    # =====================================================
    # SEND TRANSFORM PACKETS (existing objects, roots)
    # =====================================================

    if objects_to_send:
        # DIAG: log transform send with timestamp and count
        try:
            _xf_ts = time.time()
            _xf_mono = time.monotonic()
            _xf_count = len(objects_to_send)
            _xf_child_count = len(children_to_send)
            with open("/home/nguyennongngockhanh/.cache/uelivesync/uelivesync_blender_debug.log", "a") as _xf_f:
                _xf_f.write(f"[DIAG][XFORM_SEND] ts={_xf_ts:.6f} mono={_xf_mono:.6f} roots={_xf_count} children={_xf_child_count}\n")
        except Exception:
            pass
        send_objects(
            objects_to_send
        )
        _burst_packet_count += 1

    # =====================================================
    # SEND TRANSFORM PACKETS (existing objects, children)
    # =====================================================

    if children_to_send:

        send_objects(
            children_to_send,
            flags=0x01
        )
        _burst_packet_count += 1

    # =====================================================
    # HEARTBEAT (every 5 seconds)
    # =====================================================

    now = time.time()

    if now - _last_heartbeat_time >= _heartbeat_interval:



        # MATSTALL: log network send for material and transform.
        if _verbose_logging or (material_payloads_to_send and "Suzanne" in str(material_payloads_to_send)):
            _connected = is_connected()
            print(
                f"[MATSTALL][BLENDER] net_send mat_count={len(material_payloads_to_send)} "
                f"connected={_connected}"
            )
        if _verbose_logging and objects_to_send:
            _connected = is_connected()
            print(
                f"[MATSTALL][BLENDER] net_send transform_count={len(objects_to_send)} "
                f"children={len(children_to_send)} connected={_connected}"
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
        _burst_packet_count += 1

        # Phase 10A.3: log heartbeat to debug file for monitoring
        try:
            _hb_connected = is_connected()
            _hb_client = getattr(_client, '_runtime_stats', {}) if _client else {}
            _hb_queue = _hb_client.get('queue_depth', -1) if _hb_client else -1
            _hb_sent = _hb_client.get('packets_sent', 0) if _hb_client else 0
            with open("/home/nguyennongngockhanh/.cache/uelivesync/uelivesync_blender_debug.log", "a") as _hb_f:
                _hb_f.write(f"[DIAG][HB] sent queue_depth={_hb_queue} total_sent={_hb_sent} connected={_hb_connected}\n")
        except Exception:
            pass

        _last_heartbeat_time = now

    try:
        with open("/home/nguyennongngockhanh/.cache/uelivesync/uelivesync_blender_debug.log", "a") as _f:
            _f.write("[DIAG][FLOW] at Phase 7C (playback) tick_n=%s\n" % (time.time() % 1000,))
    except Exception as _e:
        print("[CHECK_UPDATES] CANNOT WRITE Phase 7C log: %s" % _e, flush=True)

    # =====================================================
    # PLAYBACK STATE DETECTION (Phase 7C)
    # =====================================================

    _pb_eff = is_playback_effective()

    if _pb_eff:

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
            _burst_packet_count += 1
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
            fc = int(scene.frame_current)
            fs = int(scene.frame_start)
            fe = int(scene.frame_end)
            fps_num = int(scene.render.fps)
            fps_den = int(scene.render.fps_base)
        except (AttributeError, RuntimeError, TypeError):
            fc = fs = fe = fps_num = 0
            fps_den = 1

        current_tl = (fc, fs, fe, fps_num, fps_den)

        if _last_timeline_state is None:
            # Send initial timeline sync on first tick
            _timeline_sequence += 1
            payload = serialize_timeline(
                fc, fs, fe, fps_num, fps_den,
                _timeline_sequence, time.time(),
            )
            send_objects([payload], packet_type=PT_Timeline, version=5)
            _burst_packet_count += 1
            _timeline_packets_sent += 1
            _timeline_state_changes += 1
            _runtime_stats["timeline_packets_sent"] = _timeline_packets_sent
            _runtime_stats["timeline_state_changes"] = _timeline_state_changes
            tl_state_payload = serialize_timeline_state(fs, fe, fc, fps_num, fps_den)
            send_objects([tl_state_payload], packet_type=PT_TimelineState, version=5)
            # Invalidate keyframe hash cache so keyframes are re-sent after
            # the UE LevelSequence is established by the Timeline/TimelineState.
            _last_keyframe_action.clear()
        elif current_tl != _last_timeline_state:
            _timeline_sequence += 1
            payload = serialize_timeline(
                fc, fs, fe, fps_num, fps_den,
                _timeline_sequence, time.time(),
            )
            send_objects([payload], packet_type=PT_Timeline, version=5)
            _burst_packet_count += 1
            _timeline_packets_sent += 1
            _timeline_state_changes += 1
            _runtime_stats["timeline_packets_sent"] = _timeline_packets_sent
            _runtime_stats["timeline_state_changes"] = _timeline_state_changes

            # Phase 7F Stage 1: Also send PT_TimelineState with frame range + FPS
            # (applies to UE LevelSequence playback range)
            tl_state_payload = serialize_timeline_state(fs, fe, fc, fps_num, fps_den)
            send_objects([tl_state_payload], packet_type=PT_TimelineState, version=5)

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
            guid_obj = UUID(guid_hex)
            guid_bytes = guid_obj.bytes
        else:
            guid_obj = None
            guid_bytes = NULL_CAMERA_GUID

        if _last_active_camera_guid is None:
            pass
        elif _last_active_camera_guid == b'' or guid_bytes != _last_active_camera_guid:
            _active_camera_sequence += 1
            payload = serialize_active_camera(
                guid_obj,
                _active_camera_sequence,
                time.time(),
            )
            send_objects([payload], packet_type=PT_ActiveCamera, version=5)
            _burst_packet_count += 1
            _active_camera_packets_sent += 1
            _active_camera_state_changes += 1
            _runtime_stats["active_camera_packets_sent"] = _active_camera_packets_sent
            _runtime_stats["active_camera_state_changes"] = _active_camera_state_changes

            # Send CameraDef alongside PT_ActiveCamera when camera is valid
            if camera_obj is not None and hasattr(camera_obj, 'data') and camera_obj.data is not None:
                cam_data = camera_obj.data
                focal = getattr(cam_data, 'lens', 50.0)
                sensor_width = getattr(cam_data, 'sensor_width', 36.0)
                sensor_height = getattr(cam_data, 'sensor_height', 24.0)
                clip_start = getattr(cam_data, 'clip_start', 0.1)
                clip_end = getattr(cam_data, 'clip_end', 1000.0)
                is_ortho = getattr(cam_data, 'type', 'PERSP') == 'ORTHO'
                ortho_scale = _ue_ortho_scale(cam_data)
                flags = 0
                if is_ortho:
                    flags |= CAMERA_DEF_FLAG_IS_ORTHO
                flags |= CAMERA_DEF_FLAG_HAS_CAMERA_DEF
                camdef_payload = serialize_camera_def(
                    guid_obj,
                    focal_length_mm=focal,
                    sensor_width_mm=sensor_width,
                    sensor_height_mm=sensor_height,
                    clip_start=clip_start,
                    clip_end=clip_end,
                    ortho_scale=ortho_scale,
                    aspect_ratio=render_aspect_ratio(bpy.context),
                    flags=flags,
                )
                send_objects([camdef_payload], packet_type=PT_CameraDef, version=5)
                _burst_packet_count += 1
                _camera_def_packets_sent += 1
                _runtime_stats["camera_def_packets_sent"] = _camera_def_packets_sent
                _last_camera_signature[guid_bytes] = _build_camera_signature(cam_data)

        _last_active_camera_guid = guid_bytes

    # =====================================================
    # PHASE 10A.2: CAMERA PARAMETER DIRTY DETECTION
    # Scan all camera objects for parameter changes and
    # emit standalone PT_CameraDef when signature changes.
    # =====================================================

    for obj in bpy.data.objects:
        if obj.type != 'CAMERA':
            continue
        try:
            _ = obj.name
        except ReferenceError:
            continue
        cam_data = obj.data
        if cam_data is None:
            continue

        guid_hex = ensure_guid(obj)
        guid_obj = UUID(guid_hex)
        guid_bytes = guid_obj.bytes

        signature = _build_camera_signature(cam_data)
        last_sig = _last_camera_signature.get(guid_bytes)

        if last_sig is None or signature != last_sig:
            # Aspect ratio is a fundamental camera property — synced from
            # Blender render resolution (including pixel aspect).
            _aspect = render_aspect_ratio(bpy.context)
            is_ortho = (cam_data.type == 'ORTHO')
            flags = CAMERA_DEF_FLAG_HAS_CAMERA_DEF
            if is_ortho:
                flags |= CAMERA_DEF_FLAG_IS_ORTHO
            camdef_payload = serialize_camera_def(
                guid_obj,
                focal_length_mm=cam_data.lens,
                sensor_width_mm=cam_data.sensor_width,
                sensor_height_mm=cam_data.sensor_height,
                clip_start=cam_data.clip_start,
                clip_end=cam_data.clip_end,
                ortho_scale=_ue_ortho_scale(cam_data),
                aspect_ratio=_aspect,
                flags=flags,
            )
            send_objects([camdef_payload], packet_type=PT_CameraDef, version=5)
            _burst_packet_count += 1
            _camera_def_packets_sent += 1
            _runtime_stats["camera_def_packets_sent"] = _camera_def_packets_sent
            _last_camera_signature[guid_bytes] = signature

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

    # Phase 8 Stage 1: per-tick burst packet count
    _runtime_stats["burst_packet_count"] = _burst_packet_count
    _runtime_stats["burst_packet_count_peak"] = max(
        _runtime_stats.get("burst_packet_count_peak", 0),
        _burst_packet_count
    )

    return 0.016


def _check_updates_wrapped():
    """Wrapper for check_updates with exception isolation (Phase 10J).

    bpy.app.timers.register requires the callback to return a float or
    raise an exception.  An unhandled exception from check_updates would
    break the timer loop.  This wrapper catches all exceptions, logs them,
    and returns the normal interval so the timer continues.
    """
    try:
        return check_updates()
    except Exception as _e:
        import traceback
        _tb = traceback.format_exc()
        _append_blender_debug_log("[DIAG][EXC] check_updates exception: %s" % _e)
        for _line in _tb.split("\n"):
            if _line.strip():
                _append_blender_debug_log("[DIAG][EXC] %s" % _line)
        print(
            f"[LIVESYNC][ERROR] check_updates exception: {_e}"
        )
        set_critical_error(
            f"check_updates failed: {_e}"
        )
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
                    _get_primitive_type(obj)
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

    print(f"  [Discovery]")
    print(
        f"    Configured host:   {_network_mod._host}"
    )
    print(
        f"    Configured port:   {_network_mod._port}"
    )
    discovery_results = _network_mod.get_discovery_results()
    if discovery_results:
        for r in discovery_results:
            status = "FOUND" if r["success"] else "MISS"
            err = f" ({r['error']})" if r["error"] else ""
            print(f"    {r['host']}:{r['port']} — {status}{err}")
    else:
        print(f"    No discovery scan performed yet")

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


def stop_sync():

    global timer_running
    global _timer_ref
    global _sync_start_time

    if _timer_ref is not None:
        try:
            bpy.app.timers.unregister(_timer_ref)
        except ValueError:
            pass
        _timer_ref = None

    timer_running = False
    _sync_start_time = 0.0

    disconnect()

    # Task 9B.6B.13/14: clear MTEX dedup and material basic property collection on stop
    import importlib
    _addon_pkg = __name__.split('.')[0]
    _net_mod = importlib.import_module(".network", _addon_pkg)
    _net_mod._mtex_clear_dedup_state()
    _net_mod._mt_basic_clear_state()

    print("UE Live Sync Stopped")


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
    global _last_collection_state
    global _last_mesh_identity
    global _last_material_identity
    global _last_material_property_sig  # Phase 10J.5I
    global _last_material_sent_reason  # Phase 7H
    global _last_geometry_version
    global _last_playback_state
    global _last_timeline_state
    global _timeline_sequence
    global _timeline_packets_sent
    global _timeline_state_changes
    global _last_active_camera_guid
    global _active_camera_sequence
    global _active_camera_packets_sent
    global _active_camera_state_changes
    global _camera_def_packets_sent
    global _last_decision_init_printed

    # Reset runtime state
    timer_running = False
    _tracked_before_clear = len(tracked_objects)
    print(f"[MATERIAL][SESSION_RESTART] action=start_sync tracked={_tracked_before_clear}")
    last_sent_transforms.clear()
    tracked_objects.clear()
    _last_mesh_identity.clear()
    _last_material_identity.clear()
    _last_material_property_sig.clear()  # Phase 10J.5I
    _last_material_sent_reason.clear()  # Phase 7H
    _mat_create_sent_names.clear()  # Phase 1.4.2a
    _last_material_sent_props.clear()  # Phase 1.4.2b
    _last_decision_init_printed.clear()  # Phase 7H
    _last_sidecar_digest.clear()  # Phase 9B.6
    _last_sidecar_info.clear()  # Phase 9B.6

    print(f"[MATERIAL][SESSION_RESTART_FULL_SNAPSHOT] reason=start_sync material_identity_cleared=1 material_sig_cleared=1")

    _last_geometry_version.clear()
    _last_object_names.clear()
    _last_visibility_state.clear()
    _last_parent_guid.clear()
    _known_guids.clear()
    _last_collection_state.clear()
    _last_keyframe_action.clear()
    _last_camera_signature.clear()
    _last_playback_state = None
    _last_timeline_state = None
    _timeline_sequence = 0
    _timeline_packets_sent = 0
    _timeline_state_changes = 0
    _last_active_camera_guid = b''  # Phase 7D: trigger initial PT_ActiveCamera + PT_CameraDef on first tick
    _active_camera_sequence = 0
    _active_camera_packets_sent = 0
    _active_camera_state_changes = 0
    _camera_def_packets_sent = 0
    _keyframe_sequence = 0
    _keyframe_packets_sent = 0
    _keyframes_sent = 0
    _animated_objects_scanned = 0
    _last_heartbeat_time = 0.0
    _last_object_count = 0
    _scan_counter = 0

    # Unregister existing timer if any
    if _timer_ref is not None:
        try:
            bpy.app.timers.unregister(_timer_ref)
        except ValueError:
            pass
        _timer_ref = None

    # Disconnect existing connection
    disconnect()

    # Task 9B.6B.13/14: clear MTEX dedup and material basic property collection on restart
    import importlib
    _addon_pkg = __name__.split('.')[0]
    _net_mod = importlib.import_module(".network", _addon_pkg)
    _net_mod._mtex_clear_dedup_state()
    _net_mod._mt_basic_clear_state()

    # Read host/port from preferences
    _sync_runtime_config()
    host = "127.0.0.1"
    port = _runtime_config.get("server_port", 57000)

    # Connect
    connect(host, port)

    if not is_connected():
        print(f"[LiveSync] Failed to connect to {host}:{port}")
        return

    _sync_start_time = time.time()
    timer_running = True

    # Phase 10J: use exception-isolated wrapper
    _timer_ref = bpy.app.timers.register(_check_updates_wrapped)

    print(f"UE Live Sync Started — connected to {host}:{port}")
