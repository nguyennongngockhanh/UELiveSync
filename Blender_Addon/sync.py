import sys
import bpy
import hashlib
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
        PT_Collection,
        LIVE_SYNC_VERSION_V5,
        get_mesh_identity_hash,
        serialize_asset_identity,
        serialize_collection_identity,
        serialize_collection_membership,
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
        PT_Collection,
        LIVE_SYNC_VERSION_V5,
        get_mesh_identity_hash,
        serialize_asset_identity,
        serialize_collection_identity,
        serialize_collection_membership,
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
    )


# =========================================================
# GLOBAL STATE
# =========================================================

timer_running = False

last_sent_transforms = {}

# Phase 5D: Per-GUID last mesh identity for change detection
# Stores (identity_low, identity_high, mesh_name)
_last_mesh_identity = {}

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

    raw = (
        f"{obj.name}|"
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

    if not timer_running:
        return 0.016

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
        _known_guids.clear()
        _last_collection_state.clear()
        _collection_anti_loop_guids.clear()

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

            # Phase 6: Rename detection (semantic event)
            current_name = obj.name
            prev_name = _last_object_names.get(guid)
            if not is_first_send and prev_name is not None and prev_name != current_name:
                renames_to_send.append(
                    serialize_rename(guid_obj, prev_name, current_name)
                )
                if _verbose_logging:
                    print(f"[RENAME] GUID={guid} \"{prev_name}\" → \"{current_name}\"")
            _last_object_names[guid] = current_name

            # Phase 6: Visibility detection (semantic event)
            current_vis = obj.hide_get()
            prev_vis = _last_visibility_state.get(guid)
            if not is_first_send and prev_vis is not None and prev_vis != current_vis:
                vis_payloads_to_send.append(
                    serialize_visibility(guid_obj, current_vis)
                )
                if _verbose_logging:
                    print(f"[VISIBILITY] GUID={guid} hidden={current_vis}")
            _last_visibility_state[guid] = current_vis

            # Phase 6D: Hierarchy detection (semantic attach/detach/reparent)
            # parent_guid is already computed above for transform serialization
            current_parent_guid = parent_guid
            prev_parent_guid = _last_parent_guid.get(guid)
            if not is_first_send and guid in _last_parent_guid and prev_parent_guid != current_parent_guid:
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
                    print(f"[HIERARCHY] GUID={guid} parent={prev_parent_str} → {parent_str}")
            _last_parent_guid[guid] = current_parent_guid

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

                for removed_coll_str in removed:
                    removed_coll_uuid = UUID(removed_coll_str)
                    collection_payloads_to_send.append(
                        serialize_collection_membership(
                            guid_obj, removed_coll_uuid,
                            COLLECTION_OP_REMOVE
                        )
                    )

                if _verbose_logging and (added or removed):
                    print(
                        f"[COLLECTION] GUID={guid} added={len(added)} "
                        f"removed={len(removed)}"
                    )

        _last_collection_state[guid] = current_coll_guids

        # Clear anti-loop guard for this GUID after processing
        _collection_anti_loop_guids.discard(guid)

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

    print("[LiveSync] Start button pressed — entering start_sync()", flush=True)

    try:

        last_sent_transforms.clear()
        _last_object_names.clear()
        _last_visibility_state.clear()
        _last_parent_guid.clear()
        _last_collection_state.clear()

        print("[LiveSync] State cleared, starting collection replay recording", flush=True)

        # Phase 6F Stage 5: Start replay recording on sync start
        start_collection_replay_recording()

        tracked_objects.clear()

        _sync_start_time = time.time()

        _last_heartbeat_time = time.time()

        _last_object_count = len(bpy.data.objects)

        _scan_counter = 0

        _sync_runtime_config()

        _verbose_logging = _get_threshold(
            "verbose_logging",
            False
        )

        _network_set_verbose(
            _verbose_logging
        )

        mesh_count = 0

        for obj in bpy.data.objects:
            if obj.type == 'MESH':
                guid = ensure_unique_guid(obj, tracked_objects)
                tracked_objects[guid] = (
                    obj,
                    UUID(guid)
                )
                mesh_count += 1

        print(
            f"[LiveSync] Scanned scene: {mesh_count} mesh objects tracked, "
            f"{len(bpy.data.objects)} total objects",
            flush=True
        )

        _reconcile_guids_on_load()

        port = _get_threshold(
            "server_port",
            57000
        )

        print(
            f"[LiveSync] Creating socket — connecting to 127.0.0.1:{port}",
            flush=True
        )

        connect(port=port)

        print("[LiveSync] connect() returned — socket/thread bootstrap complete", flush=True)

        timer_running = True

        if _timer_ref is not None:
            try:
                bpy.app.timers.unregister(_timer_ref)
            except ValueError:
                pass

        _timer_ref = bpy.app.timers.register(
            lambda: check_updates()
        )

        print("[LiveSync] Sync timer registered — main loop active", flush=True)

        print("[LiveSync] Startup complete — UE Live Sync Started", flush=True)

    except Exception:
        traceback.print_exc()
        msg = f"Startup failed: {traceback.format_exc()}"
        print(f"[LiveSync] {msg}", flush=True)
        set_critical_error(msg)


# =========================================================
# STOP SYNC
# =========================================================

def stop_sync():

    global timer_running
    global _timer_ref
    global _last_object_names
    global _last_visibility_state
    global _last_parent_guid
    global _known_guids
    global _last_collection_state  # Phase 6F

    timer_running = False
    _last_object_names.clear()
    _last_visibility_state.clear()
    _last_parent_guid.clear()
    _known_guids.clear()
    _last_collection_state.clear()  # Phase 6F

    if _timer_ref is not None:
        try:
            bpy.app.timers.unregister(_timer_ref)
        except ValueError:
            pass
        _timer_ref = None

    disconnect()

    print("UE Live Sync Stopped")
