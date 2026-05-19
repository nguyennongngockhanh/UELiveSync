import socket
import struct
import time


HOST = "127.0.0.1"
PORT = 5000

MAGIC = 0x534E5955

sock = None


def connect():

    global sock

    try:
        sock = socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM
        )

        sock.setsockopt(
            socket.IPPROTO_TCP,
            socket.TCP_NODELAY,
            1
        )

        sock.connect(
            (HOST, PORT)
        )

        print(
            "UE Live Sync Connected"
        )

        return True

    except Exception as e:

        print(
            f"Connection failed: {e}"
        )

        sock = None

        return False


def disconnect():

    global sock

    if sock:

        try:
            sock.close()

        except:
            pass

    sock = None

    print(
        "UE Live Sync Disconnected"
    )


def is_connected():

    global sock

    return sock is not None


def build_snapshot_packet(objects):

    payload = bytearray()

    object_count = len(objects)

    for obj in objects:

        name_bytes = obj[
            "object"
        ].encode("utf-8")

        transform = obj[
            "transform"
        ]

        loc = transform[
            "location"
        ]

        rot = transform[
            "rotation"
        ]

        scale = transform[
            "scale"
        ]

        # object name
        payload.extend(
            struct.pack(
                f"<H{len(name_bytes)}s",
                len(name_bytes),
                name_bytes
            )
        )

        # location
        payload.extend(
            struct.pack(
                "<3f",
                loc[0],
                loc[1],
                loc[2]
            )
        )

        # rotation quaternion
        payload.extend(
            struct.pack(
                "<4f",
                rot[0],
                rot[1],
                rot[2],
                rot[3]
            )
        )

        # scale
        payload.extend(
            struct.pack(
                "<3f",
                scale[0],
                scale[1],
                scale[2]
            )
        )

    header_size = (
        4 +  # magic
        4 +  # packet size
        4 +  # object count
        8    # timestamp
    )

    packet_size = (
        header_size +
        len(payload)
    )

    header = (
        struct.pack("<I", MAGIC) +
        struct.pack("<I", packet_size) +
        struct.pack("<I", object_count) +
        struct.pack("<d", time.time())
    )

    return header + payload


def send_snapshot(snapshot):

    global sock

    if not sock:
        return False

    try:

        packet = build_snapshot_packet(
            snapshot["objects"]
        )

        sock.sendall(packet)

        return True

    except Exception as e:

        print(
            f"Send failed: {e}"
        )

        disconnect()

        return False
