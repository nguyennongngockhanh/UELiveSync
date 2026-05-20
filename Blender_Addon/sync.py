import bpy
import time
import uuid

from bpy.app.handlers import persistent

from mathutils import Matrix

from .network import (
    connect,
    send_objects,
    serialize_object
)


# =========================================================
# GLOBAL STATE
# =========================================================

timer_running = False

last_sent_transforms = {}


# =========================================================
# GUID SYSTEM
# =========================================================

def ensure_guid(obj):

    if "ue_guid" not in obj:

        obj["ue_guid"] = uuid.uuid4().hex

    return obj["ue_guid"]


# =========================================================
# TRANSFORM COMPARISON
# =========================================================

def transforms_different(a, b):

    if b is None:
        return True

    # =====================================================
    # LOCATION
    # =====================================================

    for i in range(3):

        if abs(
            a["location"][i] -
            b["location"][i]
        ) > 0.01:

            return True

    # =====================================================
    # ROTATION
    # =====================================================

    for i in range(4):

        if abs(
            a["rotation"][i] -
            b["rotation"][i]
        ) > 0.0001:

            return True

    # =====================================================
    # SCALE
    # =====================================================

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

    # =====================================================
    # BLENDER -> UE COORDINATE CONVERSION
    # =====================================================

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

        # =================================================
        # LOCATION (cm)
        # =================================================

        "location": [

            loc.x * 100.0,
            loc.y * 100.0,
            loc.z * 100.0
        ],

        # =================================================
        # ROTATION (quat x y z w)
        # =================================================

        "rotation": [

            rot.x,
            rot.y,
            rot.z,
            rot.w
        ],

        # =================================================
        # SCALE
        # =================================================

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

    if not timer_running:
        return 0.016

    objects_to_send = []

    # =====================================================
    # OBJECT ITERATION
    # =====================================================

    for obj in bpy.data.objects:

        if obj.type != 'MESH':
            continue

        # =================================================
        # GUID
        # =================================================

        guid = ensure_guid(obj)

        # =================================================
        # TRANSFORM
        # =================================================

        transform = get_transform(obj)

        previous = last_sent_transforms.get(
            guid
        )

        # =================================================
        # CHANGE DETECTION
        # =================================================

        if transforms_different(
            transform,
            previous
        ):

            # =============================================
            # SERIALIZE OBJECT
            # =============================================

            serialized = serialize_object(
                guid,
                transform
            )

            objects_to_send.append(
                serialized
            )

            # =============================================
            # CACHE LAST STATE
            # =============================================

            last_sent_transforms[guid] = {

                "location":
                    transform["location"][:],

                "rotation":
                    transform["rotation"][:],

                "scale":
                    transform["scale"][:]
            }

    # =====================================================
    # SEND PACKET
    # =====================================================

    if objects_to_send:

        send_objects(
            objects_to_send
        )

    return 0.016


# =========================================================
# START SYNC
# =========================================================

def start_sync():

    global timer_running
    global last_sent_transforms

    last_sent_transforms.clear()

    connect()

    timer_running = True

    bpy.app.timers.register(
        lambda: check_updates()
    )

    print("UE Live Sync Started")


# =========================================================
# STOP SYNC
# =========================================================

def stop_sync():

    global timer_running

    timer_running = False

    print("UE Live Sync Stopped")
