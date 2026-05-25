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
# PROTOCOL SIGNATURE (FNV-1a 32-bit)
# Must match UE LIVE_SYNC_PROTOCOL_SIG in SyncTypes.h.
# Logged at startup; mismatch = binary protocol drift.
# =========================================================

def _compute_protocol_signature():
    FNV_OFFSET = 2166136261
    FNV_PRIME = 16777619

    def _fnv(h, b):
        return ((h ^ b) * FNV_PRIME) & 0xFFFFFFFF

    h = FNV_OFFSET
    h = _fnv(h, LIVE_SYNC_MAGIC & 0xFF)
    h = _fnv(h, (LIVE_SYNC_MAGIC >> 8) & 0xFF)
    h = _fnv(h, (LIVE_SYNC_MAGIC >> 16) & 0xFF)
    h = _fnv(h, (LIVE_SYNC_MAGIC >> 24) & 0xFF)
    for v in (2, 3, 4, 5):
        h = _fnv(h, v & 0xFF)
        h = _fnv(h, (v >> 8) & 0xFF)
    import struct as _s
    for size in (24, 22, 80, 81, 16, 33):
        h = _fnv(h, size)
    for pt in (0x01, 0x03, 0x04, 0x07, 0x08, 0x09, 0x0A):
        h = _fnv(h, pt)
    return h

LIVE_SYNC_PROTOCOL_SIG = _compute_protocol_signature()

# Verbose logging flag (set by sync.py from addon prefs)
_network_verbose = False

def set_verbose(enabled):
    global _network_verbose
    _network_verbose = enabled

# Primitive type constants (1 byte, appended to CREATE packets only)
PRIMITIVE_CUBE = 0x00
PRIMITIVE_SPHERE = 0x01
PRIMITIVE_CYLINDER = 0x02
PRIMITIVE_PLANE = 0x03
PRIMITIVE_EMPTY = 0x04

# Packet type constants (beyond V3 base)
PT_BeginSnapshot = 0x09
PT_EndSnapshot = 0x0A
PT_AssetDef = 0x08
PT_Visibility = 0x0B
PT_Rename = 0x0C

# V4 protocol version
LIVE_SYNC_VERSION_V4 = 4

# V5 protocol version
LIVE_SYNC_VERSION_V5 = 5


# =========================================================
# XXHASH64 (pure Python, deterministic, fast)
# =========================================================

_XXH_PRIME64_1 = 0x9E3779B185EBCA87
_XXH_PRIME64_2 = 0xC2B2AE3D27D4EB4F
_XXH_PRIME64_3 = 0x165667B19E3779F9
_XXH_PRIME64_4 = 0x85EBCA77C2B2AE63
_XXH_PRIME64_5 = 0x27D4EB2F165667C5


def _xxh64_round(acc, seed):
    acc += seed * _XXH_PRIME64_2
    acc = ((acc << 31) | (acc >> 33))
    acc *= _XXH_PRIME64_1
    return acc & 0xFFFFFFFFFFFFFFFF


def _xxh64_merge_round(acc, val):
    acc = ((acc ^ _xxh64_round(0, val)) * _XXH_PRIME64_1) + _XXH_PRIME64_4
    return acc & 0xFFFFFFFFFFFFFFFF


def xxh64(data, seed=0):
    length = len(data)
    remaining_length = length
    acc = seed + _XXH_PRIME64_5 + _XXH_PRIME64_5

    if length >= 32:
        v1 = seed + _XXH_PRIME64_1 + _XXH_PRIME64_2
        v2 = seed + _XXH_PRIME64_2
        v3 = seed
        v4 = seed - _XXH_PRIME64_1

        limit = length - 32
        offset = 0

        while offset <= limit:
            v1 = _xxh64_round(v1, struct.unpack_from("<Q", data, offset)[0])
            v2 = _xxh64_round(v2, struct.unpack_from("<Q", data, offset + 8)[0])
            v3 = _xxh64_round(v3, struct.unpack_from("<Q", data, offset + 16)[0])
            v4 = _xxh64_round(v4, struct.unpack_from("<Q", data, offset + 24)[0])
            offset += 32

        acc = ((v1 << 1) | (v1 >> 63))
        acc = _xxh64_merge_round(acc, v2)
        acc = _xxh64_merge_round(acc, v3)
        acc = _xxh64_merge_round(acc, v4)

        remaining_length = length - offset
    else:
        acc += _XXH_PRIME64_5

    offset = length - remaining_length
    while remaining_length >= 8:
        val = struct.unpack_from("<Q", data, offset)[0]
        acc = ((acc ^ _xxh64_round(0, val)) * _XXH_PRIME64_1) + _XXH_PRIME64_4
        acc &= 0xFFFFFFFFFFFFFFFF
        offset += 8
        remaining_length -= 8

    while remaining_length >= 4:
        val = struct.unpack_from("<I", data, offset)[0]
        acc = ((acc ^ (val * _XXH_PRIME64_1)) * _XXH_PRIME64_3) + _XXH_PRIME64_5
        acc &= 0xFFFFFFFFFFFFFFFF
        offset += 4
        remaining_length -= 4

    while remaining_length > 0:
        val = data[offset]
        acc = ((acc ^ (val * _XXH_PRIME64_5)) * _XXH_PRIME64_3) + _XXH_PRIME64_5
        acc &= 0xFFFFFFFFFFFFFFFF
        offset += 1
        remaining_length -= 1

    # Final avalanche
    acc ^= acc >> 37
    acc = (acc * _XXH_PRIME64_3) + _XXH_PRIME64_5
    acc ^= acc >> 37
    acc = (acc * _XXH_PRIME64_4) + _XXH_PRIME64_5
    acc ^= acc >> 37

    return acc & 0xFFFFFFFFFFFFFFFF


# =========================================================
# ASSET IDENTITY HELPERS (Phase 5D)
# =========================================================

def get_mesh_identity_hash(obj):
    """Return (low: int, high: int, primitive_type: int).

    xxHash64 of the Blender mesh datablock name.
    Deterministic across sessions and duplicated object instances.
    NOT stable across datablock renames.

    If obj is not a MESH or has no data, returns (0, 0, PRIMITIVE_EMPTY).
    """
    if obj.type != 'MESH' or obj.data is None:
        return (0, 0, PRIMITIVE_EMPTY)

    name_bytes = obj.data.name.encode("utf-8")
    hash_value = xxh64(name_bytes)

    low = hash_value & 0xFFFFFFFFFFFFFFFF
    high = (hash_value >> 64) & 0xFFFFFFFFFFFFFFFF

    # Get the configured primitive type as fallback
    try:
        from . import sync
        primitive = sync._get_primitive_type()
    except (ImportError, AttributeError):
        primitive = PRIMITIVE_CUBE

    return (low, high, primitive if primitive is not None else PRIMITIVE_CUBE)


def serialize_asset_identity(guid_obj, identity_low, identity_high, primitive_type):
    """33 bytes per object: GUID(16) + IdentityHash(16) + PrimitiveFallback(1).

    PT_AssetDef (V5) payload format.
    """
    payload = bytearray()

    # GUID (4 × uint32 LE) — same decomposition as serialize_object_v3
    a = guid_obj.time_low
    b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
    c = (guid_obj.clock_seq_hi_variant << 24) | (guid_obj.clock_seq_low << 16) | ((guid_obj.node >> 32) & 0xFFFF)
    d = guid_obj.node & 0xFFFFFFFF
    payload.extend(struct.pack("<IIII", a, b, c, d))

    # Identity hash (2 × uint64 LE)
    payload.extend(struct.pack("<QQ", identity_low & 0xFFFFFFFFFFFFFFFF, identity_high & 0xFFFFFFFFFFFFFFFF))

    # Primitive fallback (1 byte)
    payload.extend(struct.pack("<B", primitive_type))

    return bytes(payload)


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


def serialize_object_v3(guid_obj, transform, timestamp, parent_guid_obj=None, primitive_type=None):

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

    # =====================================================
    # PRIMITIVE TYPE (1 byte, CREATE-only, 0x00 = Cube)
    # =====================================================

    if primitive_type is None:
        primitive_type = PRIMITIVE_CUBE

    payload.extend(struct.pack(
        "<B",
        primitive_type
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
# SERIALIZE RENAME (Phase 6 — Semantic Event)
# =========================================================
# Wire format:
#   GUID (16 bytes) + old_name_length (2) + old_name (N)
#   + new_name_length (2) + new_name (M) + sequence (4) + timestamp (8)
#
# This is a semantic editor event, NOT a state stream packet.
# See Docs/Architecture/19-phase6-vertical-slice-rename.md §4
# =========================================================

_rename_sequences = {}

def serialize_rename(guid_obj, old_name, new_name):

    payload = bytearray()

    # GUID
    d_a = guid_obj.time_low
    d_b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
    d_c = (guid_obj.clock_seq_hi_variant << 24
           | guid_obj.clock_seq_low << 16
           | (guid_obj.node >> 32) & 0xFFFF)
    d_d = guid_obj.node & 0xFFFFFFFF

    payload.extend(struct.pack("<IIII", d_a, d_b, d_c, d_d))

    # Old name
    old_bytes = old_name.encode("utf-8")
    payload.extend(struct.pack("<H", len(old_bytes)))
    payload.extend(old_bytes)

    # New name
    new_bytes = new_name.encode("utf-8")
    payload.extend(struct.pack("<H", len(new_bytes)))
    payload.extend(new_bytes)

    # Monotonic sequence per GUID (replay dedup)
    guid_key = str(guid_obj)
    seq = _rename_sequences.get(guid_key, 0) + 1
    _rename_sequences[guid_key] = seq
    payload.extend(struct.pack("<I", seq))

    # Timestamp
    payload.extend(struct.pack("<d", time.time()))

    return payload


# =========================================================
# VISIBILITY SERIALIZATION (Phase 6, PT_Visibility = 0x0B)
# =========================================================
# Fixed-size wire format per object (29 bytes):
#   + GUID(16) + bHidden(1) + sequence(4) + timestamp(8)
#
# This is a discrete semantic editor event, NOT a state stream.
# See Docs/Architecture/21-phase6-vertical-slice-visibility.md §2
# =========================================================

_visibility_sequences = {}

def serialize_visibility(guid_obj, b_hidden):

    payload = bytearray()

    # GUID
    d_a = guid_obj.time_low
    d_b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
    d_c = (guid_obj.clock_seq_hi_variant << 24
           | guid_obj.clock_seq_low << 16
           | (guid_obj.node >> 32) & 0xFFFF)
    d_d = guid_obj.node & 0xFFFFFFFF

    payload.extend(struct.pack("<IIII", d_a, d_b, d_c, d_d))

    # bHidden (uint8: 0=visible, 1=hidden)
    payload.extend(struct.pack("<B", 1 if b_hidden else 0))

    # Monotonic sequence per GUID (replay dedup)
    guid_key = str(guid_obj)
    seq = _visibility_sequences.get(guid_key, 0) + 1
    _visibility_sequences[guid_key] = seq
    payload.extend(struct.pack("<I", seq))

    # Timestamp
    payload.extend(struct.pack("<d", time.time()))

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

            print(
                f"[LiveSync] Connected to UE  "
                f"[sig=0x{LIVE_SYNC_PROTOCOL_SIG:08X}]"
            )

            import struct as _pstruct
            print(
                f"[Protocol] "
                f"magic=0x{LIVE_SYNC_MAGIC:08X} LE "
                f"hdr_v3={_pstruct.calcsize('<I H B B Q I I')} "
                f"hdr_v2={_pstruct.calcsize('<I H Q I I')} "
                f"obj_v3={_pstruct.calcsize('<IIIIfff ffff fff d IIII B') - 1} "
                f"obj_v4={_pstruct.calcsize('<IIIIfff ffff fff d IIII B')} "
                f"obj_del={_pstruct.calcsize('<IIII')} "
                f"obj_asset={_pstruct.calcsize('<IIII QQ B')}"
            )

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

        # Phase 6: reset rename sequence tracker on disconnect
        global _rename_sequences
        if _rename_sequences:
            _rename_sequences.clear()
            print("[RENAME] Sequence tracker cleared on disconnect")

        # Phase 6: reset visibility sequence tracker on disconnect
        global _visibility_sequences
        if _visibility_sequences:
            _visibility_sequences.clear()
            print("[VISIBILITY] Sequence tracker cleared on disconnect")

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
        version=LIVE_SYNC_VERSION_V4,
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

        if _network_verbose:

            hex_dump = " ".join(
                f"{b:02x}"
                for b in header[:24]
            )

            print(
                f"[Packet] ver={version} "
                f"type=0x{packet_type:02x} "
                f"flags=0x{flags:02x} "
                f"seq={seq_id} "
                f"size={packet_size} "
                f"objs={object_count}  "
                f"hdr: {hex_dump}"
            )

        return header + payload

    # =====================================================
    # SEND PACKET (non-blocking)
    # =====================================================

    def send_packet(
        self,
        objects_data,
        packet_type=0x01,
        flags=0x00,
        version=None
    ):

        try:

            packet = self._build_packet(
                objects_data,
                packet_type=packet_type,
                flags=flags,
                version=(
                    version if version
                    is not None
                    else LIVE_SYNC_VERSION_V4
                )
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
    flags=0x00,
    version=None
):

    global _client

    if _client is None:

        connect()

    if _client:

        _client.send_packet(
            objects_data,
            packet_type,
            flags,
            version
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
