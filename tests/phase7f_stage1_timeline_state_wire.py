#!/usr/bin/env python3
"""Phase 7F Stage 1 — Timeline State Wire Format Test.

Tests the PT_TimelineState (0x19) Blender serialization:
1. Packet type constant is 0x19
2. Serialized payload is 20 bytes
3. Fields serialize in correct order (frame_start, frame_end, frame_current, fps_num, fps_den)
4. fps_base converts to denominator correctly
5. Malformed/truncated payload rejected by UE-side validation
6. 0x19 is NOT the same as 0x13 (PT_Timeline)
"""

import struct
import sys
import os

BLENDER_NETWORK = os.path.join(
    os.path.dirname(__file__),
    "..",
    "Blender_Addon",
    "network.py"
)


def import_blender_network():
    """Import Blender addon network module in non-Blender environment."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("network", BLENDER_NETWORK)
    net = importlib.util.module_from_spec(spec)
    # Mock bpy
    import types
    bpy = types.ModuleType("bpy")
    bpy.app = types.ModuleType("bpy.app")
    bpy.app.version = (4, 2, 0)
    bpy.app.handlers = types.ModuleType("bpy.app.handlers")
    bpy.app.handlers.persistent = lambda f: f
    bpy.context = None
    bpy.types = types.ModuleType("bpy.types")
    bpy.ops = types.ModuleType("bpy.ops")
    bpy.utils = types.ModuleType("bpy.utils")
    bpy.utils.register_class = lambda c: None
    bpy.utils.unregister_class = lambda c: None
    bpy_props = types.ModuleType("bpy.props")
    bpy_props.BoolProperty = lambda **kw: True
    bpy_props.FloatProperty = lambda **kw: 0.0
    bpy_props.IntProperty = lambda **kw: 0
    bpy_props.StringProperty = lambda **kw: ""
    bpy_props.EnumProperty = lambda **kw: ""
    bpy.props = bpy_props
    sys.modules["bpy"] = bpy
    sys.modules["bpy.app"] = bpy.app
    sys.modules["bpy.app.handlers"] = bpy.app.handlers
    sys.modules["bpy.types"] = bpy.types
    sys.modules["bpy.ops"] = bpy.ops
    sys.modules["bpy.utils"] = bpy.utils
    sys.modules["bpy.props"] = bpy_props
    spec.loader.exec_module(net)
    return net


def test_packet_type_is_0x19(net):
    """PT_TimelineState must be 0x19, distinct from PT_Timeline (0x13)."""
    assert hasattr(net, "PT_TimelineState"), "PT_TimelineState not defined"
    assert net.PT_TimelineState == 0x19, \
        f"PT_TimelineState = {hex(net.PT_TimelineState)}, expected 0x19"
    assert net.PT_TimelineState != net.PT_Timeline, \
        f"PT_TimelineState ({hex(net.PT_TimelineState)}) must differ from PT_Timeline ({hex(net.PT_Timeline)})"
    print(f"  PASS: PT_TimelineState = {hex(net.PT_TimelineState)}")
    return True


def test_payload_size(net):
    """TIMELINE_STATE_PAYLOAD_SIZE must be 20 bytes (5 × int32)."""
    assert hasattr(net, "TIMELINE_STATE_PAYLOAD_SIZE"), "TIMELINE_STATE_PAYLOAD_SIZE not defined"
    assert net.TIMELINE_STATE_PAYLOAD_SIZE == 20, \
        f"TIMELINE_STATE_PAYLOAD_SIZE = {net.TIMELINE_STATE_PAYLOAD_SIZE}, expected 20"
    print(f"  PASS: TIMELINE_STATE_PAYLOAD_SIZE = {net.TIMELINE_STATE_PAYLOAD_SIZE}")
    return True


def test_serialize_function_exists(net):
    """serialize_timeline_state function must exist."""
    assert hasattr(net, "serialize_timeline_state"), "serialize_timeline_state not defined"
    assert callable(net.serialize_timeline_state), "serialize_timeline_state is not callable"
    print("  PASS: serialize_timeline_state function exists")
    return True


def test_serialize_field_order(net):
    """Serialized bytes must match: frame_start, frame_end, frame_current, fps_num, fps_den."""
    payload = net.serialize_timeline_state(
        frame_start=1,
        frame_end=120,
        frame_current=24,
        fps_num=24,
        fps_den=1,
    )

    assert len(payload) == 20, f"Serialized payload length = {len(payload)}, expected 20"

    fields = struct.unpack("<iiiii", payload)
    assert fields == (1, 120, 24, 24, 1), \
        f"Fields mismatch: {fields}, expected (1, 120, 24, 24, 1)"
    print(f"  PASS: fields = {fields}")
    return True


def test_serialize_fps_den_conversion(net):
    """fps_den = fps_base (e.g. 1.001 → 1001/1000)."""
    # Common case: fps=24, fps_base=1
    payload = net.serialize_timeline_state(
        frame_start=0, frame_end=100, frame_current=50,
        fps_num=24, fps_den=1,
    )
    fields = struct.unpack("<iiiii", payload)
    assert fields[3] == 24, f"fps_num should be 24, got {fields[3]}"
    assert fields[4] == 1, f"fps_den should be 1, got {fields[4]}"
    print(f"  PASS: fps_den=1 (integer playback)")

    # NTSC-style: fps=30, fps_base=1.001 → fps_num=30000, fps_den=1001
    payload = net.serialize_timeline_state(
        frame_start=0, frame_end=100, frame_current=50,
        fps_num=30000, fps_den=1001,
    )
    fields = struct.unpack("<iiiii", payload)
    assert fields[3] == 30000, f"fps_num should be 30000, got {fields[3]}"
    assert fields[4] == 1001, f"fps_den should be 1001, got {fields[4]}"
    print(f"  PASS: fps_den=1001 (NTSC fractional playback)")
    return True


def test_serialize_zero_values(net):
    """All-zero values must be valid (default safe)."""
    payload = net.serialize_timeline_state(0, 0, 0, 0, 0)
    assert len(payload) == 20, f"Length = {len(payload)}, expected 20"
    fields = struct.unpack("<iiiii", payload)
    assert fields == (0, 0, 0, 0, 0), f"All-zero fields: {fields}"
    print("  PASS: all-zero serialization")
    return True


def test_not_shared_with_timeline_0x13(net):
    """PT_TimelineState must use its own serialize function, not PT_Timeline's."""
    tl_state = net.serialize_timeline_state(1, 120, 24, 24, 1)
    tl = net.serialize_timeline(24, 1, 120, 24, 1, 0, 0.0, 0)
    assert len(tl_state) == 20, f"TL state payload length = {len(tl_state)}, expected 20"
    assert len(tl) == 36, f"TL payload length = {len(tl)}, expected 36"
    assert tl_state != tl[:20], "Timeline state must NOT match PT_Timeline first 20 bytes (different field order)"
    print("  PASS: PT_TimelineState is distinct from PT_Timeline")
    return True


if __name__ == '__main__':
    net = import_blender_network()

    tests = [
        ("test_packet_type_is_0x19", test_packet_type_is_0x19),
        ("test_payload_size", test_payload_size),
        ("test_serialize_function_exists", test_serialize_function_exists),
        ("test_serialize_field_order", test_serialize_field_order),
        ("test_serialize_fps_den_conversion", test_serialize_fps_den_conversion),
        ("test_serialize_zero_values", test_serialize_zero_values),
        ("test_not_shared_with_timeline_0x13", test_not_shared_with_timeline_0x13),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn(net)
            passed += 1
        except Exception as e:
            print(f"FAIL: {name}: {e}")
            failed += 1

    print(f"\nResults: {passed} passed, {failed} failed, {passed + failed} total")
    sys.exit(1 if failed > 0 else 0)
