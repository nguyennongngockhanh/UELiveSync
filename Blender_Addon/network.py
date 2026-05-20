import socket
import struct
import time


# =========================================================
# PHASE 3.3 PROTOCOL CONSTANTS
# =========================================================

LIVE_SYNC_MAGIC = 0x4C56534D
LIVE_SYNC_VERSION = 2


# =========================================================
# GLOBAL STATE
# =========================================================

_sequence_id = 0


# =========================================================
# OBJECT SERIALIZATION
# =========================================================

def serialize_object(guid_hex, transform):

    payload = bytearray()

    # =====================================================
    # GUID (16 bytes)
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


# =========================================================
# LIVE SYNC CLIENT
# =========================================================

class LiveSyncClient:

    def __init__(
        self,
        host="127.0.0.1",
        port=5000
    ):

        self.host = host
        self.port = port

        self.sock = None
        self.connected = False

        self.connect()

    # =====================================================
    # CONNECTION LAYER
    # =====================================================

    def connect(self):

        try:

            self.sock = socket.socket(
                socket.AF_INET,
                socket.SOCK_STREAM
            )

            self.sock.connect((
                self.host,
                self.port
            ))

            self.connected = True

            print("[LiveSync] Connected to UE")

        except Exception as e:

            self.connected = False
            self.sock = None

            print(
                "[LiveSync] Connection failed:",
                e
            )

    def reconnect(self):

        self.close()

        time.sleep(0.5)

        self.connect()

    def close(self):

        if self.sock:

            try:

                self.sock.close()

            except:
                pass

        self.sock = None
        self.connected = False

    # =====================================================
    # MAIN SEND PIPELINE
    # =====================================================

    def send_packet(self, objects_data):

        global _sequence_id

        # =================================================
        # RECONNECT IF NEEDED
        # =================================================

        if not self.connected or not self.sock:

            self.reconnect()

            if not self.connected:
                return

        # =================================================
        # SEQUENCE
        # =================================================

        _sequence_id += 1

        # =================================================
        # BUILD PAYLOAD
        # =================================================

        payload = bytearray()

        object_count = len(objects_data)

        for obj in objects_data:

            payload.extend(obj)

        # =================================================
        # HEADER
        # =================================================

        header_size = struct.calcsize(
            "<I H Q I I"
        )

        packet_size = (
            header_size +
            len(payload)
        )

        header = struct.pack(

            "<I H Q I I",

            LIVE_SYNC_MAGIC,      # uint32 magic
            LIVE_SYNC_VERSION,    # uint16 version
            _sequence_id,         # uint64 sequence
            packet_size,          # uint32 packet size
            object_count          # uint32 object count
        )

        # =================================================
        # SEND
        # =================================================

        try:

            self.sock.sendall(
                header + payload
            )

        except (

            BrokenPipeError,
            ConnectionResetError,
            OSError

        ):

            print(
                "[LiveSync] Connection lost, retrying..."
            )

            self.reconnect()


# =========================================================
# GLOBAL CLIENT
# =========================================================

_client = None


# =========================================================
# PUBLIC API
# =========================================================

def connect():

    global _client

    if _client is None:

        _client = LiveSyncClient()

    elif not _client.connected:

        _client.reconnect()


def disconnect():

    global _client

    if _client:

        _client.close()


def send_objects(objects_data):

    global _client

    if _client is None:

        connect()

    if _client:

        _client.send_packet(
            objects_data
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
