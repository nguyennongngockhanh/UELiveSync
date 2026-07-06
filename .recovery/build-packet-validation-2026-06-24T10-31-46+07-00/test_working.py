"""
phase10k_serialization_validation.py

Tests for _build_packet() serialization validation.
Validates that _build_packet() correctly accepts valid inputs and rejects
invalid inputs (raw bytes, integers, mixed lists) with structured TypeError.

No protocol changes — only defensive validation added.
"""

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
        # Minimal mock with attributes _build_packet may reference.
        cls.client = cls.network.LiveSyncClient()
        cls.client.connected = False
        cls.client.sock = None
        cls.client._capability_response_received = False
        cls.client._remote_capabilities = 0
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


if __name__ == "__main__":
    unittest.main()
