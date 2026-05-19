import socket
import struct
import time

# =========================================================
# PHASE 3.2 PROTOCOL CONSTANTS
# =========================================================
LIVE_SYNC_MAGIC = 0x4C56534D
LIVE_SYNC_VERSION = 1

# =========================================================
# GLOBAL STATE (sequence tracking)
# =========================================================
_sequence_id = 0


class LiveSyncClient:
    def __init__(self, host="127.0.0.1", port=5000):
        self.host = host
        self.port = port

        self.sock = None
        self.connected = False

        self.connect()

    # =========================================================
    # CONNECTION LAYER (PHASE 3.2 HARDENED)
    # =========================================================
    def connect(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            self.connected = True
            print("[LiveSync] Connected to UE")
        except Exception as e:
            self.connected = False
            self.sock = None
            print("[LiveSync] Connection failed:", e)

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

    # =========================================================
    # MAIN SEND PIPELINE
    # =========================================================
    def send_packet(self, objects_data):
        global _sequence_id

        if not self.connected or not self.sock:
            self.reconnect()
            if not self.connected:
                return

        _sequence_id += 1

        payload = bytearray()

        object_count = len(objects_data)

        # =====================================================
        # OBJECT SERIALIZATION (UNCHANGED LOGIC SLOT)
        # =====================================================
        for obj in objects_data:
            payload.extend(obj)

        # =====================================================
        # HEADER (PHASE 3.2 ENHANCED)
        # =====================================================
        packet_size = len(payload) + struct.calcsize("<I H Q I I")

        header = struct.pack(
            "<I H Q I I",
            LIVE_SYNC_MAGIC,     # uint32 magic
            LIVE_SYNC_VERSION,   # uint16 version
            _sequence_id,        # uint64 sequence
            packet_size,         # uint32 size
            object_count        # uint32 objects
        )

        # =====================================================
        # SAFE SEND (HANDLE DROP)
        # =====================================================
        try:
            self.sock.sendall(header + payload)

        except (BrokenPipeError, ConnectionResetError, OSError):
            print("[LiveSync] Connection lost, retrying...")
            self.reconnect()
