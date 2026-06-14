#!/usr/bin/env python3
"""Phase 7E Stage 10A.5A -- send_sequencer_op() packet wrap validation.

Proves that send_sequencer_op():
- Does NOT enqueue raw payload bytes directly
- Builds a packet with correct LiveSync magic/header
- Uses PT_SequencerOp (0x18) as packet type
- Has valid object count and payload length
- Increments the sequence counter
- Does not regress the protocol format
"""

import struct
import sys
import os
import queue

# Import the addon's network module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Blender_Addon"))
import network as net

# Constants
LIVE_SYNC_MAGIC = 0x4C56534D
PT_SEQUENCER_OP = 0x18
LIVE_SYNC_VERSION_V4 = 4


def test_send_sequencer_op_wraps_payload():
    """send_sequencer_op() must wrap payload through _build_packet, not enqueue raw."""
    client = net.LiveSyncClient()
    client.connect = lambda *a, **k: None
    client.is_connected = lambda: True
    client._send_queue = queue.Queue()
    client._network_verbose = False
    net._client = client

    opcode = 0x01
    seq_id = 1
    timestamp = 1.0
    guid_bytes = b'\x00' * 16
    seq_num = 1
    payload = struct.pack('<I f I 16s I', opcode, timestamp, seq_id, guid_bytes, seq_num)

    initial_seq = net._sequencer_op_sequence
    initial_pkts = net._sequencer_op_packets_sent

    result = net.send_sequencer_op(payload)

    assert result is True, f"send_sequencer_op() should return True, got {result}"
    assert client._send_queue.qsize() == 1, f"Exactly one packet queued, got {client._send_queue.qsize()}"

    built_packet = client._send_queue.get()
    assert built_packet != payload, "Packet must NOT be raw payload"
    assert len(built_packet) > len(payload), f"Built packet must be larger than payload"

    assert len(built_packet) >= 24, f"Must have 24-byte header, got {len(built_packet)}"

    hdr = struct.unpack('<I H B B Q I I', built_packet[:24])
    magic, version, ptype, flags, seq_id_val, pkt_size, obj_count = hdr

    assert magic == LIVE_SYNC_MAGIC, f"Magic mismatch: 0x{magic:08X} != 0x{LIVE_SYNC_MAGIC:08X}"
    assert version == LIVE_SYNC_VERSION_V4, f"Version mismatch: {version} != {LIVE_SYNC_VERSION_V4}"
    assert ptype == PT_SEQUENCER_OP, f"Packet type mismatch: 0x{ptype:02x} != 0x{PT_SEQUENCER_OP:02x}"
    assert obj_count == 1, f"Object count mismatch: {obj_count} != 1"
    assert pkt_size == len(built_packet), f"Packet size mismatch: {pkt_size} != {len(built_packet)}"

    assert net._sequencer_op_sequence == initial_seq + 1, "SequencerOp sequence must increment"
    assert net._sequencer_op_packets_sent == initial_pkts + 1, "Packets sent counter must increment"

    print("PASS: test_send_sequencer_op_wraps_payload")
    return True


def test_send_sequencer_op_no_client():
    """send_sequencer_op() must return False when client is None."""
    net._client = None
    net._sequencer_op_sequence = 0
    net._sequencer_op_packets_sent = 0

    result = net.send_sequencer_op(b'\x00' * 32)

    assert result is False, "send_sequencer_op() must return False with no client"

    print("PASS: test_send_sequencer_op_no_client")
    return True


def test_send_sequencer_op_header_format():
    """Verify the built packet header matches LiveSync V3+ wire format exactly."""
    client = net.LiveSyncClient()
    client.connect = lambda *a, **k: None
    client.is_connected = lambda: True
    client._send_queue = queue.Queue()
    client._network_verbose = False
    net._client = client

    payload = net.serialize_sequencer_op_create_sequence(
        sequence=1, timestamp=100.0,
        frame_start=1, frame_end=60,
        fps_num=24, fps_den=1
    )

    result = net.send_sequencer_op(payload)
    assert result is True

    built_packet = client._send_queue.get()

    hdr = struct.unpack('<I H B B Q I I', built_packet[:24])
    magic, version, ptype, flags, seq_id_val, pkt_size, obj_count = hdr

    assert magic == LIVE_SYNC_MAGIC, f"Magic mismatch: 0x{magic:08X}"
    assert version == LIVE_SYNC_VERSION_V4
    assert ptype == PT_SEQUENCER_OP
    assert obj_count == 1
    assert pkt_size == len(built_packet)

    header_payload = built_packet[24:]
    assert len(header_payload) == len(payload), f"Payload length mismatch: {len(header_payload)} != {len(payload)}"

    print("PASS: test_send_sequencer_op_header_format")
    return True


def test_no_protocol_regression():
    """Ensure send_sequencer_op() does not break existing packet types."""
    client = net.LiveSyncClient()
    client.connect = lambda *a, **k: None
    client.is_connected = lambda: True
    client._send_queue = queue.Queue()
    client._network_verbose = False
    net._client = client

    obj_payload = b'\x00' * 32
    net.send_objects([obj_payload], packet_type=net.PT_Keyframe, version=5)

    assert client._send_queue.qsize() == 1

    built_packet = client._send_queue.get()
    hdr = struct.unpack('<I H B B Q I I', built_packet[:24])
    magic, version, ptype, flags, seq_id_val, pkt_size, obj_count = hdr

    assert magic == LIVE_SYNC_MAGIC, f"Magic mismatch: 0x{magic:08X}"
    assert ptype == net.PT_Keyframe, f"PT_Keyframe type mismatch: 0x{ptype:02x}"
    assert version == 5, f"Version mismatch: {version} != 5"
    assert obj_count == 1, f"Object count mismatch: {obj_count}"

    print("PASS: test_no_protocol_regression")
    return True


if __name__ == '__main__':
    tests = [
        test_send_sequencer_op_wraps_payload,
        test_send_sequencer_op_no_client,
        test_send_sequencer_op_header_format,
        test_no_protocol_regression,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"FAIL: {test.__name__}: {e}")
            failed += 1

    print(f"\nResults: {passed} passed, {failed} failed, {passed + failed} total")
    sys.exit(1 if failed > 0 else 0)
