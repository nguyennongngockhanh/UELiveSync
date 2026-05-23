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
        self.last_error = ""
        self.last_error_severity = "INFO"
        self._status_detail = "Initializing"

        self._lock = threading.Lock()

        self._send_queue = queue.Queue(
            maxsize=256
        )

        self._running = True
        self._was_connected = False
        self.reconnected = False

        self._reconnect_attempts = 0
        self._reconnect_max_delay = 10.0
        self._reconnect_base_delay = 0.5
        self._reconnect_start_time = 0.0
        self._last_send_attempt = 0.0
        self._idle_probe_interval = 5.0

        self._runtime_stats = {
            "queue_depth": 0,
            "reconnect_count": 0,
            "last_error": "",
            "last_error_severity": "INFO",
            "last_send_time": 0.0,
            "dropped_packets": 0,
            "packets_sent": 0,
            "bytes_sent": 0,
            "uptime": 0.0,
            "start_time": 0.0,
        }

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
                    timeout=1.0
                )

                if data is None:
                    break

                self._last_send_attempt = time.time()

                data_len = len(data)

                print(
                    f"[SYNC-DBG] 4 Sender dequeued: {data_len} bytes"
                )

                with self._lock:

                    if not self.connected or not self.sock:
                        self._connect_internal()

                    if self.connected and self.sock:

                        try:

                            self.sock.sendall(data)

                            self._reconnect_attempts = 0

                            self._runtime_stats["last_send_time"] = time.time()
                            self._runtime_stats["packets_sent"] += 1
                            self._runtime_stats["bytes_sent"] += data_len

                            self._runtime_stats["reconnect_count"] = self._reconnect_attempts

                            print(
                                f"[SYNC-DBG] 5 Socket send OK: {data_len} bytes"
                            )

                        except (

                            BrokenPipeError,
                            ConnectionResetError,
                            OSError

                        ) as e:

                            self.last_error = str(e)

                            print(
                                f"[SYNC-DBG] 5 Socket send FAILED: {e}"
                            )

                            self._reconnect_internal()

            except queue.Empty:

                self._idle_probe()

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
            self.last_error = ""
            self.last_error_severity = "INFO"
            self._status_detail = (
                f"Connected to {self.host}:{self.port}"
            )

            self._reconnect_attempts = 0
            self._reconnect_start_time = 0.0

            self._runtime_stats["start_time"] = time.time()
            self._runtime_stats["reconnect_count"] = 0

            if self._was_connected:
                self.reconnected = True

            self._was_connected = True

            print("[LiveSync] Connected to UE")

        except ConnectionRefusedError:

            self.connected = False
            self.sock = None
            self.last_error = (
                f"Connection refused — is UE listening on {self.port}?"
            )
            self.last_error_severity = "WARNING"
            self._status_detail = "Connection refused"

            print(
                "[LiveSync] Connection refused:",
                self.last_error
            )

        except socket.timeout:

            self.connected = False
            self.sock = None
            self.last_error = (
                f"Connection timeout — "
                f"no response from {self.host}:{self.port}"
            )
            self.last_error_severity = "WARNING"
            self._status_detail = "Connection timeout"

            print(
                "[LiveSync] Connection timeout:",
                self.last_error
            )

        except OSError as e:

            self.connected = False
            self.sock = None

            if "address already in use" in str(e).lower():
                self.last_error = (
                    f"Port {self.port} is already in use"
                )
                self.last_error_severity = "CRITICAL"
            else:
                self.last_error = str(e)
                self.last_error_severity = "WARNING"

            self._status_detail = f"Connection failed: {self.last_error}"

            print(
                "[LiveSync] Connection failed:",
                e
            )

        except Exception as e:

            self.connected = False
            self.sock = None
            self.last_error = str(e)
            self.last_error_severity = "WARNING"
            self._status_detail = f"Connection failed: {self.last_error}"

            print(
                "[LiveSync] Connection failed:",
                e
            )

    def _reconnect_internal(self):

        self._close_internal()

        self._reconnect_attempts += 1

        self._runtime_stats["reconnect_count"] = (
            self._reconnect_attempts
        )

        if self._reconnect_start_time == 0.0:
            self._reconnect_start_time = time.time()

        delay = min(
            self._reconnect_base_delay *
            (2 ** (self._reconnect_attempts - 1)),
            self._reconnect_max_delay
        )

        reconnect_elapsed = (
            time.time() -
            self._reconnect_start_time
        )

        self._status_detail = (
            f"Reconnecting (attempt {self._reconnect_attempts}) "
            f"in {delay:.0f}s..."
        )

        if reconnect_elapsed > 30.0:

            self.last_error = (
                f"Reconnect failed after {reconnect_elapsed:.0f}s "
                f"({self._reconnect_attempts} attempts)"
            )
            self.last_error_severity = "CRITICAL"

            print(
                "[LiveSync] CRITICAL: "
                f"persistent reconnect failure "
                f"({reconnect_elapsed:.0f}s, "
                f"{self._reconnect_attempts} attempts)"
            )

        else:

            self.last_error = (
                f"Reconnecting (attempt {self._reconnect_attempts})"
            )
            self.last_error_severity = "WARNING"

            print(
                f"[LiveSync] Reconnect attempt {self._reconnect_attempts}"
                f" in {delay:.1f}s"
            )

        time.sleep(delay)

        self._connect_internal()

    def _idle_probe(self):

        if self.connected:
            return

        if not self._was_connected:
            return

        now = time.time()

        if now - self._last_send_attempt < self._idle_probe_interval:
            return

        self._last_send_attempt = now

        with self._lock:

            if not self.connected or not self.sock:

                print(
                    f"[LiveSync] Idle probe — attempting reconnection"
                )

                self._reconnect_internal()

    def _close_internal(self):

        if self.sock:

            try:

                self.sock.close()

            except:
                pass

        self.sock = None
        self.connected = False
        self._status_detail = "Disconnected"

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
        packet_type=0x01,
        flags=0x00
    ):

        try:

            packet = self._build_packet(
                objects_data,
                packet_type=packet_type,
                flags=flags
            )

        except Exception as e:

            self.last_error = (
                f"Packet build failed: {e}"
            )
            self.last_error_severity = "CRITICAL"

            print(
                "[LiveSync] CRITICAL: "
                f"Packet build failed: {e}"
            )

            return

        try:

            self._send_queue.put_nowait(
                packet
            )

            print(
                f"[SYNC-DBG] 3 Enqueued: {len(packet)} bytes"
            )

        except queue.Full:

            self.last_error = "Send queue full"

            self._runtime_stats["dropped_packets"] += 1

            # Log cooldown: at most once per 5s
            _now = time.time()

            if not hasattr(
                self,
                "_last_queue_full_log"
            ) or _now - self._last_queue_full_log > 5.0:

                self._last_queue_full_log = _now

                print(
                    "[SYNC-DBG] 3 Enqueue FAILED: "
                    f"queue full ({self._runtime_stats['dropped_packets']} dropped)"
                )


# =========================================================
# GLOBAL CLIENT
# =========================================================

_client = None


# =========================================================
# PUBLIC API
# =========================================================

def is_connected():

    global _client

    return (
        _client is not None and
        _client.connected
    )


def get_last_error():

    global _client

    if _client is None:

        return "Not initialized"

    # Sync to runtime_stats
    _client._runtime_stats["last_error"] = (
        _client.last_error
    )

    return _client.last_error


def get_last_error_severity():

    global _client

    if _client is None:

        return "INFO"

    # Sync to runtime_stats
    _client._runtime_stats["last_error_severity"] = (
        _client.last_error_severity
    )

    return _client.last_error_severity


def get_status_detail():

    global _client

    if _client is None:

        return "Not started"

    return _client._status_detail


def check_reconnected():

    global _client

    if _client is None:

        return False

    with _client._lock:

        val = _client.reconnected

        _client.reconnected = False

        return val


def connect(
    host="127.0.0.1",
    port=5000
):

    global _client

    if _client is None:

        _client = LiveSyncClient(
            host=host,
            port=port
        )

    elif not _client.connected:

        _client.reconnect()


def disconnect():

    global _client

    if _client:

        _client.stop()

        _client = None


def send_objects(
    objects_data,
    packet_type=0x01,
    flags=0x00
):

    global _client

    if _client is None:

        connect()

    if _client:

        _client.send_packet(
            objects_data,
            packet_type,
            flags
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


def get_queue_depth():

    global _client

    if _client is None:

        return 0

    depth = _client._send_queue.qsize()

    _client._runtime_stats["queue_depth"] = (
        depth
    )

    return depth


def get_reconnect_count():

    global _client

    if _client is None:

        return 0

    return _client._runtime_stats.get(
        "reconnect_count", 0
    )


def get_runtime_stats():

    global _client

    if _client is None:

        return {}

    # Snapshot live values into stats dict
    get_queue_depth()

    stats = dict(
        _client._runtime_stats
    )

    if stats["start_time"] > 0.0:

        stats["uptime"] = (
            time.time() -
            stats["start_time"]
        )

    return stats


def set_critical_error(message):

    global _client

    if _client is None:

        return

    _client.last_error = message
    _client.last_error_severity = "CRITICAL"

    _client._runtime_stats["last_error"] = message
    _client._runtime_stats["last_error_severity"] = "CRITICAL"

    print(
        f"[LiveSync] CRITICAL: {message}"
    )
