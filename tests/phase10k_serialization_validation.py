"""
phase10k_serialization_validation.py

Tests for _build_packet() serialization validation.
Validates that _build_packet() correctly accepts valid inputs and rejects
invalid inputs (raw bytes, integers, mixed lists) with structured TypeError.

Also validates _send_announce() wraps its payload in a list.

No protocol changes — only defensive validation added.
"""

import ast
import importlib.util
import os
import struct
import sys
import unittest

# Load network module from the actual Blender_Addon source path.
_NETWORK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Blender_Addon",
    "network.py",
)
_SRC_NETWORK_MODULE_LOADED = False
_network_module = None


def _load_network_module():
    global _network_module, _SRC_NETWORK_MODULE_LOADED
    if _SRC_NETWORK_MODULE_LOADED:
        return _network_module
    spec = importlib.util.spec_from_file_location("network", _NETWORK_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["network"] = mod
    spec.loader.exec_module(mod)
    _network_module = mod
    _SRC_NETWORK_MODULE_LOADED = True
    return mod


class TestBuildPacketValidation(unittest.TestCase):
    """Validate _build_packet input type checking."""

    @classmethod
    def setUpClass(cls):
        cls.network = _load_network_module()
        # Use __new__ to avoid LiveSyncClient constructor side effects
        # (socket creation, thread startup). _build_packet() does not use
        # any instance fields initialized by __init__.
        cls.client = cls.network.LiveSyncClient.__new__(cls.network.LiveSyncClient)
        # Reset sequence lock to avoid cross-test ordering issues.
        cls.network._sequence_id = 0

    def _build(self, objects_data, **kw):
        """Call _build_packet on the mock client."""
        return self.client._build_packet(objects_data, **kw)

    def test_module_loaded_from_source(self):
        """Loaded network module must come from Blender_Addon source."""
        mod_path = getattr(self.network, "__file__", "")
        self.assertEqual(mod_path, _NETWORK_PATH)

    def test_valid_list_of_bytes(self):
        """list[bytes] should pass validation and produce packet."""
        payloads = [b"abc", b"def"]
        result = self._build(payloads)
        self.assertIsInstance(result, bytes)
        self.assertGreater(len(result), 24)

    def test_valid_list_of_bytearray(self):
        """list[bytearray] should pass validation and produce packet."""
        payloads = [bytearray(b"abc"), bytearray(b"def")]
        result = self._build(payloads)
        self.assertIsInstance(result, bytes)
        self.assertGreater(len(result), 24)

    def test_valid_list_of_memoryview(self):
        """list[memoryview] should pass validation and produce packet."""
        payloads = [memoryview(b"abc"), memoryview(b"def")]
        result = self._build(payloads)
        self.assertIsInstance(result, bytes)
        self.assertGreater(len(result), 24)

    def test_mixed_valid_bytes_like(self):
        """Mixed bytes/bytearray/memoryview list should pass."""
        payloads = [b"abc", bytearray(b"def"), memoryview(b"ghi")]
        result = self._build(payloads)
        self.assertIsInstance(result, bytes)
        # abc + def + ghi = 9 payload bytes
        expected_payload_len = 9
        header_size = 24
        self.assertEqual(len(result), header_size + expected_payload_len)

    def test_raw_bytes_raises_type_error(self):
        """Raw bytes (not in list) must raise structured TypeError."""
        with self.assertRaises(TypeError) as ctx:
            self._build(b"raw")
        msg = str(ctx.exception)
        self.assertIn("raw bytes-like payload", msg)
        self.assertIn("bytes", msg)

    def test_raw_bytearray_raises_type_error(self):
        """Raw bytearray (not in list) must raise structured TypeError."""
        with self.assertRaises(TypeError) as ctx:
            self._build(bytearray(b"raw"))
        msg = str(ctx.exception)
        self.assertIn("raw bytes-like payload", msg)
        self.assertIn("bytearray", msg)

    def test_raw_memoryview_raises_type_error(self):
        """Raw memoryview (not in list) must raise structured TypeError."""
        with self.assertRaises(TypeError) as ctx:
            self._build(memoryview(b"raw"))
        msg = str(ctx.exception)
        self.assertIn("raw bytes-like payload", msg)
        self.assertIn("memoryview", msg)

    def test_integer_element_raises_type_error(self):
        """Integer element in list must raise structured TypeError."""
        with self.assertRaises(TypeError) as ctx:
            self._build([1, 2, 3])
        msg = str(ctx.exception)
        self.assertIn("packet_type", msg)
        self.assertIn("index=0", msg)
        self.assertIn("int", msg)

    def test_mixed_list_detected(self):
        """Mixed list (bytes + int) should raise on the integer element."""
        with self.assertRaises(TypeError) as ctx:
            self._build([b"valid", 42, b"also_valid"])
        msg = str(ctx.exception)
        self.assertIn("index=1", msg)
        self.assertIn("int", msg)

    def test_empty_list(self):
        """Empty list should produce valid (empty payload) packet."""
        result = self._build([])
        self.assertIsInstance(result, bytes)
        header_size = 24
        self.assertEqual(len(result), header_size)
        # Payload portion should be empty.
        self.assertEqual(len(result) - header_size, 0)

    def test_byte_equivalent_output(self):
        """Valid packet output must be byte-identical to expected concatenation."""
        payloads = [b"hello", b" ", b"world"]
        result = self._build(payloads)
        header_size = 24
        payload_bytes = result[header_size:]
        expected_payload = b"hello world"
        self.assertEqual(payload_bytes, expected_payload)
        self.assertEqual(len(payload_bytes), len(expected_payload))

    def test_simple_byte_equivalent_output(self):
        """list of non-space bytes must concatenate without extra padding."""
        payloads = [b"hello", b"world"]
        result = self._build(payloads)
        header_size = 24
        payload_bytes = result[header_size:]
        expected_payload = b"helloworld"
        self.assertEqual(payload_bytes, expected_payload)
        self.assertEqual(len(payload_bytes), len(expected_payload))

    def test_packet_lengths_unchanged(self):
        """Packet header and payload lengths must remain within expected bounds."""
        payloads = [b"a" * 100, b"b" * 200]
        result = self._build(payloads)
        header_size = 24
        self.assertEqual(len(result), header_size + 300)
        # Verify header fields
        magic, ver, pt, flags, seq_id, pkt_size, obj_count = struct.unpack(
            "<I H B B Q I I", result[:header_size]
        )
        self.assertEqual(magic, self.network.LIVE_SYNC_MAGIC)
        self.assertEqual(ver, self.network.LIVE_SYNC_VERSION_V4)
        self.assertEqual(pt, 0x01)
        self.assertEqual(pkt_size, header_size + 300)
        self.assertEqual(obj_count, 2)

    def test_zero_object_produces_packet(self):
        """Zero objects (not empty list) should be rejected."""
        with self.assertRaises(TypeError):
            self._build(0)


class _FakeClient:
    """Minimal stand-in for LiveSyncClient used by _send_announce tests.

    Records send_packet calls for verification without opening sockets
    or starting threads.
    """

    def __init__(self, connected=True):
        self.connected = connected
        self.send_packet_calls = []

    def send_packet(self, objects_data, packet_type=0x01, flags=0x00, version=None):
        self.send_packet_calls.append((objects_data, packet_type, flags, version))


class TestCapabilityAnnounce(unittest.TestCase):
    """Validate _send_announce wraps its payload in a list.

    Ensures the fix for the pre-existing raw-bytes caller is correct
    and remains in place.
    """

    @classmethod
    def setUpClass(cls):
        cls.network = _load_network_module()
        cls.known_caps = 7

    def setUp(self):
        self.fake = _FakeClient(connected=True)
        self.orig_client = self.network._client
        self.orig_local_caps = self.network._local_capabilities
        self.network._client = self.fake
        self.network._local_capabilities = self.known_caps

    def tearDown(self):
        self.network._client = self.orig_client
        self.network._local_capabilities = self.orig_local_caps

    def test_connected_announce_sends_list(self):
        """_send_announce with connected client sends [payload] as list."""
        result = self.network._send_announce()
        self.assertTrue(result)
        self.assertEqual(len(self.fake.send_packet_calls), 1)
        objects_data, pt, flags, version = self.fake.send_packet_calls[0]
        self.assertIsInstance(objects_data, list)
        self.assertEqual(len(objects_data), 1)
        self.assertIsInstance(objects_data[0], bytes)
        expected_payload = struct.pack('<I', self.known_caps)
        self.assertEqual(objects_data[0], expected_payload)
        self.assertEqual(pt, self.network.PT_CapabilityAnnounce)

    def test_announce_packet_fields(self):
        """Full announce packet has object_count=1 and correct payload."""
        payload_bytes = struct.pack('<I', self.known_caps)
        client = self.network.LiveSyncClient.__new__(self.network.LiveSyncClient)
        self.network._sequence_id = 0
        packet = client._build_packet(
            [payload_bytes],
            packet_type=self.network.PT_CapabilityAnnounce,
        )
        header_size = 24
        magic, ver, pt, flags, seq_id, pkt_size, obj_count = struct.unpack(
            "<I H B B Q I I", packet[:header_size]
        )
        self.assertEqual(magic, self.network.LIVE_SYNC_MAGIC)
        self.assertEqual(pt, self.network.PT_CapabilityAnnounce)
        self.assertEqual(obj_count, 1)
        payload = packet[header_size:]
        self.assertEqual(payload, payload_bytes)
        self.assertEqual(pkt_size, header_size + len(payload_bytes))

    def test_disconnected_announce_returns_false(self):
        """_send_announce with disconnected client returns False."""
        self.fake.connected = False
        result = self.network._send_announce()
        self.assertFalse(result)
        self.assertEqual(len(self.fake.send_packet_calls), 0)

    def test_send_announce_source_wraps_payload(self):
        """AST confirms _send_announce passes [payload] not raw bytes."""
        net_src_path = _NETWORK_PATH
        with open(net_src_path) as f:
            tree = ast.parse(f.read())
        func_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == '_send_announce':
                func_node = node
                break
        self.assertIsNotNone(func_node, '_send_announce not found')
        call_nodes = []
        for node in ast.walk(func_node):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == 'send_packet':
                    call_nodes.append(node)
        self.assertEqual(len(call_nodes), 1, 'expected one send_packet call')
        call = call_nodes[0]
        self.assertGreaterEqual(len(call.args), 1)
        first_arg = call.args[0]
        self.assertIsInstance(first_arg, ast.List,
                              'first arg to send_packet must be a list literal')
        self.assertEqual(len(first_arg.elts), 1,
                         'list must contain exactly one element')


if __name__ == "__main__":
    unittest.main()
