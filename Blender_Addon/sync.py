import bpy
import time
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
        is_connected,
        get_last_error,
        get_last_error_severity,
        get_status_detail,
        check_reconnected,
        get_queue_depth,
        get_reconnect_count,
        get_runtime_stats,
        set_critical_error,
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
        is_connected,
        get_last_error,
        get_last_error_severity,
        get_status_detail,
        check_reconnected,
        get_queue_depth,
        get_reconnect_count,
        get_runtime_stats,
        set_critical_error,
    )


# =========================================================
# GLOBAL STATE
# =========================================================

timer_running = False

last_sent_transforms = {}

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

def ensure_guid(obj):

    if "ue_guid" not in obj:

        obj["ue_guid"] = uuid.uuid4().hex

    return obj["ue_guid"]


def ensure_unique_guid(obj, tracked):

    guid = ensure_guid(obj)

    if guid in tracked and tracked[guid][0] != obj:

        old_guid = guid

        obj["ue_guid"] = uuid.uuid4().hex

        guid = obj["ue_guid"]

        if _verbose_logging:
            print(
                f"[GUID] Regenerated {old_guid}"
                f" -> {guid} for {obj.name}"
            )

    return guid


def get_parent_guid(obj):

    if obj.parent and obj.parent.type == 'MESH':

        return ensure_guid(obj.parent)

    return None


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

    if not timer_running:
        return 0.016

    _update_runtime_stats()

    _verbose_logging = _get_threshold(
        "verbose_logging",
        False
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

        if _verbose_logging:
            print(
                "[Snapshot] Reconnect detected,"
                " sending full snapshot"
            )

        snapshot_roots = []
        snapshot_children = []

        for guid, obj_data in list(
            tracked_objects.items()):

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
                    parent_guid_obj
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

            deletes_to_send.append(
                serialize_delete_v3(guid_obj)
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
                    parent_guid_obj
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

    # =====================================================
    # SEND DELETE PACKETS
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

    last_sent_transforms.clear()

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

    for obj in bpy.data.objects:
        if obj.type == 'MESH':
            guid = ensure_unique_guid(obj, tracked_objects)
            tracked_objects[guid] = (
                obj,
                UUID(guid)
            )

    port = _get_threshold(
        "server_port",
        57000
    )

    connect(port=port)

    timer_running = True

    if _timer_ref is not None:
        try:
            bpy.app.timers.unregister(_timer_ref)
        except ValueError:
            pass

    _timer_ref = bpy.app.timers.register(
        lambda: check_updates()
    )

    print("UE Live Sync Started")


# =========================================================
# STOP SYNC
# =========================================================

def stop_sync():

    global timer_running
    global _timer_ref

    timer_running = False

    if _timer_ref is not None:
        try:
            bpy.app.timers.unregister(_timer_ref)
        except ValueError:
            pass
        _timer_ref = None

    disconnect()

    print("UE Live Sync Stopped")
