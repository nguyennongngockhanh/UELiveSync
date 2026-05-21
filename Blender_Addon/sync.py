import bpy
import time
import uuid

from bpy.app.handlers import persistent

from mathutils import Matrix

try:
    from .network import (
        connect,
        disconnect,
        send_objects,
        serialize_object,
        serialize_object_v3,
        serialize_delete_v3
    )
except ImportError:
    from network import (
        connect,
        disconnect,
        send_objects,
        serialize_object,
        serialize_object_v3,
        serialize_delete_v3
    )


# =========================================================
# GLOBAL STATE
# =========================================================

timer_running = False

last_sent_transforms = {}

tracked_objects = {}

_timer_ref = None

_frame_count = 0

_heartbeat_interval = 300


# =========================================================
# GUID SYSTEM
# =========================================================

def ensure_guid(obj):

    if "ue_guid" not in obj:

        obj["ue_guid"] = uuid.uuid4().hex

    return obj["ue_guid"]


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

    for i in range(3):

        if abs(
            a["location"][i] -
            b["location"][i]
        ) > 0.01:

            return True

    for i in range(4):

        if abs(
            a["rotation"][i] -
            b["rotation"][i]
        ) > 0.0001:

            return True

    for i in range(3):

        if abs(
            a["scale"][i] -
            b["scale"][i]
        ) > 0.001:

            return True

    return False


# =========================================================
# TRANSFORM EXTRACTION
# =========================================================

def get_transform(obj):

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
# MAIN UPDATE LOOP
# =========================================================

@persistent
def check_updates():

    global timer_running
    global last_sent_transforms
    global tracked_objects
    global _frame_count

    if not timer_running:
        return 0.016

    _frame_count += 1

    objects_to_send = []
    create_objects = []
    deletes_to_send = []

    # =====================================================
    # PERIODIC FULL SCAN (every 100 frames)
    # =====================================================

    if _frame_count % 100 == 0:

        current_guids = set()

        for obj in bpy.data.objects:

            if obj.type != 'MESH':
                continue

            guid = ensure_guid(obj)

            current_guids.add(guid)

            if guid not in tracked_objects:

                tracked_objects[guid] = obj

        stale_guids = [
            g for g in tracked_objects
            if g not in current_guids
        ]

        for guid in stale_guids:

            tracked_objects.pop(guid, None)

            last_sent_transforms.pop(guid, None)

            guid_obj = uuid.UUID(guid)

            deletes_to_send.append(
                serialize_delete_v3(guid_obj)
            )

    # =====================================================
    # OBJECT ITERATION (cached tracked list)
    # =====================================================

    for guid, obj in list(
        tracked_objects.items()):

        if not obj or obj.name not in bpy.data.objects:

            tracked_objects.pop(guid, None)

            last_sent_transforms.pop(guid, None)

            guid_obj = uuid.UUID(guid)

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

            guid_obj = uuid.UUID(guid)

            parent_guid = get_parent_guid(obj)

            parent_guid_obj = (
                uuid.UUID(parent_guid)
                if parent_guid else None
            )

            timestamp = time.time()

            serialized = serialize_object_v3(
                guid_obj,
                transform,
                timestamp,
                parent_guid_obj
            )

            is_first_send = (
                previous is None
            )

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
    # SEND CREATE PACKETS (first-time objects)
    # =====================================================

    if create_objects:

        send_objects(
            create_objects,
            packet_type=0x03
        )

    # =====================================================
    # SEND TRANSFORM PACKETS (existing objects)
    # =====================================================

    if objects_to_send:

        send_objects(
            objects_to_send
        )

    # =====================================================
    # HEARTBEAT (every 300 frames ≈ 5s)
    # =====================================================

    if _frame_count % _heartbeat_interval == 0:

        send_objects(
            [],
            packet_type=0x07
        )

    return 0.016


# =========================================================
# START SYNC
# =========================================================

def start_sync():

    global timer_running
    global last_sent_transforms
    global tracked_objects
    global _timer_ref
    global _frame_count

    last_sent_transforms.clear()

    tracked_objects.clear()

    _frame_count = 0

    for obj in bpy.data.objects:
        if obj.type == 'MESH':
            guid = ensure_guid(obj)
            tracked_objects[guid] = obj

    connect()

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
