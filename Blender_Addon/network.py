import socket
import struct
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
# GLOBAL STATE
# =========================================================

_sequence_id = 0
_seq_lock = threading.Lock()


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


def serialize_object_v3(guid_obj, transform, timestamp, parent_guid_obj=None):

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

        self._lock = threading.Lock()

        self._send_queue = queue.Queue(
            maxsize=256
        )

        self._running = True

        self._thread = threading.Thread(
            target=self._sender_loop,
            daemon=True
        )

        self._thread.start()

        self.connect()

    # =====================================================
    # BACKGROUND SENDER
    # =====================================================

    def _sender_loop(self):

        while self._running:

            try:

                data = self._send_queue.get(
                    timeout=0.5
                )

                if data is None:
                    break

                print(
                    f"[SYNC-DBG] 4 Sender dequeued: {len(data)} bytes"
                )

                with self._lock:

                    if not self.connected or not self.sock:
                        self._connect_internal()

                    if self.connected and self.sock:

                        try:

                            self.sock.sendall(data)

                            print(
                                f"[SYNC-DBG] 5 Socket send OK: {len(data)} bytes"
                            )

                        except (

                            BrokenPipeError,
                            ConnectionResetError,
                            OSError

                        ) as e:

                            print(
                                f"[SYNC-DBG] 5 Socket send FAILED: {e}"
                            )

                            self._reconnect_internal()

            except queue.Empty:
                continue

    # =====================================================
    # INTERNAL I/O (caller must hold _lock)
    # =====================================================

    def _connect_internal(self):

        try:

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

    def _reconnect_internal(self):

        self._close_internal()

        time.sleep(0.5)

        self._connect_internal()

    def _close_internal(self):

        if self.sock:

            try:

                self.sock.close()

            except:
                pass

        self.sock = None
        self.connected = False

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
        version=LIVE_SYNC_VERSION_V3,
        packet_type=0x01,
        flags=0x00
    ):

        global _sequence_id

        with _seq_lock:

            _sequence_id += 1

            seq_id = _sequence_id

        payload = bytearray()

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

        return header + payload

    # =====================================================
    # SEND PACKET (non-blocking)
    # =====================================================

    def send_packet(
        self,
        objects_data,
        packet_type=0x01
    ):

        packet = self._build_packet(
            objects_data,
            packet_type=packet_type
        )

        try:

            self._send_queue.put_nowait(
                packet
            )

            print(
                f"[SYNC-DBG] 3 Enqueued: {len(packet)} bytes"
            )

        except queue.Full:

            print("[SYNC-DBG] 3 Enqueue FAILED: queue full")


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

        _client.stop()

        _client = None


def send_objects(
    objects_data,
    packet_type=0x01
):

    global _client

    if _client is None:

        connect()

    if _client:

        _client.send_packet(
            objects_data,
            packet_type
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
