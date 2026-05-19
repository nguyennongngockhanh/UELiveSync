import bpy
import time

from bpy.app.handlers import persistent

from mathutils import Matrix

from .network import send_snapshot, connect


timer_running = False

last_sent_transforms = {}


def transforms_different(a, b):

    if b is None:
        return True

    for i in range(3):

        if abs(
            a["location"][i] -
            b["location"][i]) > 0.01:

            return True

    for i in range(4):

        if abs(
            a["rotation"][i] -
            b["rotation"][i]) > 0.0001:

            return True

    for i in range(3):

        if abs(
            a["scale"][i] -
            b["scale"][i]) > 0.001:

            return True

    return False


def get_transform(obj):

    mw = obj.matrix_world.copy()

    conversion = Matrix((
        (1,  0, 0, 0),
        (0, -1, 0, 0),
        (0,  0, 1, 0),
        (0,  0, 0, 1)
    ))

    ue_matrix = conversion @ mw @ conversion

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


@persistent
def check_updates():

    global timer_running
    global last_sent_transforms

    if not timer_running:
        return 0.016

    objects_to_send = []

    for obj in bpy.data.objects:

        if obj.type != 'MESH':
            continue

        transform = get_transform(obj)

        previous = last_sent_transforms.get(
            obj.name)

        if transforms_different(
            transform,
            previous):

            objects_to_send.append({

                "object": obj.name,

                "transform": transform
            })

            last_sent_transforms[obj.name] = {

                "location":
                    transform["location"][:],

                "rotation":
                    transform["rotation"][:],

                "scale":
                    transform["scale"][:]
            }

    if objects_to_send:

        snapshot = {

            "type": "snapshot",

            "timestamp": time.time(),

            "objects":
                objects_to_send
        }

        send_snapshot(snapshot)

    return 0.016


def start_sync():

    global timer_running
    global last_sent_transforms

    last_sent_transforms.clear()

    connect()

    timer_running = True

    bpy.app.timers.register(
        lambda: check_updates())

    print("UE Live Sync Started")


def stop_sync():

    global timer_running

    timer_running = False

    print("UE Live Sync Stopped")
