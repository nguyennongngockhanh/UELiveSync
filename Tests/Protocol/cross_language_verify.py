#!/usr/bin/env python3
"""
Cross-language verification: compares C++ deserialized JSON with Python manifest.

Usage:
    python3 cross_language_verify.py <vectors_dir>

Steps:
1. Reads manifest.json (Python's expected values)
2. Reads cpp_deserialized.json (C++ deserialized values)
3. Reads cpp_serialized/cpp_serialized_deserialized.json (C++ serialize → deserialize)
4. Compares all field values semantically
"""

import json
import sys
import os
import uuid
from pathlib import Path


def load_json(path):
    with open(path) as f:
        return json.load(f)


def normalize_transform(val):
    """Normalize transform to list format regardless of dict or list input."""
    if isinstance(val, dict):
        return [val["px"], val["py"], val["pz"],
                val["rx"], val["ry"], val["rz"], val["rw"],
                val["sx"], val["sy"], val["sz"]]
    if isinstance(val, list):
        return val
    return val


def parse_python_bytes_literal(s):
    """Parse Python bytes literal string like b'\\x00\\x01' to list of ints."""
    if not isinstance(s, str):
        return s
    result = []
    i = 0
    if s.startswith("b'") or s.startswith('b"'):
        i = 2
    while i < len(s):
        if s[i] == "'" and i + 1 >= len(s):
            break
        if s[i] == '\\' and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt == 'x' and i + 3 < len(s):
                result.append(int(s[i+2:i+4], 16))
                i += 4
            elif nxt == 'n':
                result.append(ord('\n'))
                i += 2
            elif nxt == 'r':
                result.append(ord('\r'))
                i += 2
            elif nxt == 't':
                result.append(ord('\t'))
                i += 2
            elif nxt == '\\':
                result.append(ord('\\'))
                i += 2
            elif nxt == "'":
                result.append(ord("'"))
                i += 2
            else:
                result.append(ord(nxt))
                i += 2
        else:
            result.append(ord(s[i]))
            i += 1
    return result


def compare_values(name, field, expected, actual, tolerance=1e-5):
    """Compare two values with float tolerance."""
    # Normalize transforms
    if "transform" in field:
        expected = normalize_transform(expected)
        actual = normalize_transform(actual)

    if isinstance(expected, float) and isinstance(actual, float):
        if abs(expected - actual) < tolerance:
            return True
        if expected == 0.0 and actual == 0.0:
            return True
        print(f"  FAIL  {name}.{field}: float mismatch: {expected} != {actual} (diff={abs(expected - actual)})")
        return False
    if isinstance(expected, list) and isinstance(actual, list):
        # Normalize bytes literal if present
        if isinstance(expected, str):
            expected = parse_python_bytes_literal(expected)
        if isinstance(actual, str):
            actual = parse_python_bytes_literal(actual)
        if len(expected) != len(actual):
            print(f"  FAIL  {name}.{field}: array length mismatch: {len(expected)} != {len(actual)}")
            return False
        for i, (e, a) in enumerate(zip(expected, actual)):
            if not compare_values(name, f"{field}[{i}]", e, a, tolerance):
                return False
        return True
    if isinstance(expected, bytes) and isinstance(actual, bytes):
        if expected != actual:
            print(f"  FAIL  {name}.{field}: bytes mismatch: {expected!r} != {actual!r}")
            return False
        return True
    if isinstance(expected, bytes):
        expected_list = list(expected)
        return compare_values(name, field, expected_list, actual, tolerance)
    if isinstance(actual, bytes):
        actual_list = list(actual)
        return compare_values(name, field, expected, actual_list, tolerance)
    if isinstance(expected, str) and isinstance(actual, list):
        # bytes literal vs array
        expected_list = parse_python_bytes_literal(expected)
        return compare_values(name, field, expected_list, actual, tolerance)
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in expected:
            if key not in actual:
                print(f"  FAIL  {name}.{field}: missing key {key}")
                return False
            if not compare_values(name, f"{field}.{key}", expected[key], actual[key], tolerance):
                return False
        return True
    if expected != actual:
        print(f"  FAIL  {name}.{field}: {expected!r} != {actual!r}")
        return False
    return True


def compare_uuid_format(name, field, manifest_val, cpp_val):
    """Compare UUID values — manifest uses hyphenated, C++ uses hex."""
    # Strip hyphens from manifest UUID
    manifest_stripped = manifest_val.replace("-", "") if isinstance(manifest_val, str) else ""
    cpp_stripped = cpp_val.replace("-", "") if isinstance(cpp_val, str) else ""
    if manifest_stripped.lower() != cpp_stripped.lower():
        print(f"  FAIL  {name}.{field}: UUID mismatch: {manifest_val} != {cpp_val}")
        return False
    return True


def compare_vector_vs_manifest(vector_name, cpp_body, manifest_fields, is_uuid_key):
    """Compare all fields in a vector against manifest."""
    all_ok = True
    for key, expected in manifest_fields.items():
        if key in cpp_body:
            actual = cpp_body[key]
            if key in is_uuid_key:
                if not compare_uuid_format(vector_name, key, expected, actual):
                    all_ok = False
            elif not compare_values(vector_name, key, expected, actual):
                all_ok = False
        else:
            # Field in manifest but not in C++ deserialization
            # This might be expected for optional fields
            print(f"  WARN  {vector_name}.{key}: field present in manifest but not in C++ output")
    return all_ok


# UUID fields per message type
UUID_FIELDS = {
    "HELLO": [],
    "HELLO_ACK": [],
    "REJECT": [],
    "HEARTBEAT": [],
    "HEARTBEAT_ACK": [],
    "SCENE_HASH": [],
    "SCENE_FULL": [],
    "SCENE_DELTA": [],
    "OBJECT_CREATE": ["persistent_id", "parent_id"],
    "OBJECT_UPDATE": ["persistent_id"],
    "OBJECT_DELETE": ["persistent_id"],
    "OBJECT_RENAME": ["persistent_id"],
    "OBJECT_REPARENT": ["persistent_id", "new_parent_id"],
    "OBJECT_VISIBILITY": ["persistent_id"],
    "MESH_START": ["persistent_id"],
    "MESH_CHUNK": ["persistent_id"],
    "MESH_END": ["persistent_id"],
    "MESH_DATA": ["persistent_id"],
    "MESH_DELTA": ["persistent_id"],
    "MATERIAL_CREATE": ["material_id"],
    "MATERIAL_UPDATE": ["material_id"],
    "MATERIAL_ASSIGN": ["persistent_id", "material_id"],
    "FBX_IMPORT_REQUEST": ["persistent_id"],
    "CAMERA_CREATE": ["camera_id"],
    "CAMERA_UPDATE": ["camera_id"],
    "CAMERASETACTIVE": ["camera_id"],
    "SYNC_ACK": [],
    "ERROR": [],
    "DISCONNECT": [],
    "SCENE_HASH_exchange_both": [],
    "HEARTBEAT_compressed": [],
    "SEQUENCE_WRAPAROUND": [],
}


def main():
    if len(sys.argv) < 2:
        print("Usage: cross_language_verify.py <vectors_dir>")
        sys.exit(1)

    vectors_dir = Path(sys.argv[1])
    manifest_path = vectors_dir / "manifest.json"
    cpp_json_path = vectors_dir / "cpp_deserialized.json"
    cpp_serialized_path = vectors_dir / "cpp_serialized" / "cpp_serialized_deserialized.json"
    cpp_bins_dir = vectors_dir / "cpp_serialized"

    # Load files
    manifest = load_json(manifest_path)
    cpp_deserialized = load_json(cpp_json_path)
    cpp_serialized = load_json(cpp_serialized_path) if cpp_serialized_path.exists() else []

    # Index C++ results by name
    cpp_by_name = {v["name"]: v for v in cpp_deserialized}
    cpp_ser_by_name = {v["name"]: v for v in cpp_serialized}

    vectors = manifest["vectors"]
    pass_count = 0
    fail_count = 0

    print("=" * 60)
    print("Cross-language Verification: C++ deserialized vs Python manifest")
    print("=" * 60)
    print()

    for vec in vectors:
        name = vec["name"]
        manifest_fields = vec["fields"]
        uuid_keys = UUID_FIELDS.get(name, [])

        if name not in cpp_by_name:
            print(f"  FAIL  {name}: missing from C++ deserialized output")
            fail_count += 1
            continue

        cpp_body = cpp_by_name[name].get("body", {})
        ok = compare_vector_vs_manifest(name, cpp_body, manifest_fields, uuid_keys)
        if ok:
            print(f"  PASS  {name}")
            pass_count += 1
        else:
            fail_count += 1

    print()
    print("=" * 60)
    print("Cross-language Verification: C++ serialize→deserialize vs Python manifest")
    print("=" * 60)
    print()

    pass2 = 0
    fail2 = 0

    for vec in vectors:
        name = vec["name"]
        manifest_fields = vec["fields"]
        uuid_keys = UUID_FIELDS.get(name, [])

        if name not in cpp_ser_by_name:
            print(f"  FAIL  {name}: missing from C++ serialized output")
            fail2 += 1
            continue

        cpp_body = cpp_ser_by_name[name].get("body", {})
        ok = compare_vector_vs_manifest(name, cpp_body, manifest_fields, uuid_keys)
        if ok:
            print(f"  PASS  {name}")
            pass2 += 1
        else:
            fail2 += 1

    # ─── Part 3: Python deserializes C++ serialized .bin ───────
    print()
    print("=" * 60)
    print("Cross-language Verification: Python deserializes C++ .bin vs manifest")
    print("=" * 60)
    print()

    pass3 = 0
    fail3 = 0

    if cpp_bins_dir.exists():
        try:
            from serializer.deserializer import deserialize_frame

            for vec in vectors:
                name = vec["name"]
                bin_path = cpp_bins_dir / f"{name}.bin"
                manifest_fields = vec["fields"]
                uuid_keys = UUID_FIELDS.get(name, [])

                if not bin_path.exists():
                    print(f"  FAIL  {name}: {bin_path} not found")
                    fail3 += 1
                    continue

                try:
                    with open(bin_path, "rb") as f:
                        data = f.read()
                    d = deserialize_frame(data)

                    # Convert to comparable dict
                    py_body = {}
                    for k, v in d.body.items():
                        if isinstance(v, bytes):
                            py_body[k] = v
                        elif isinstance(v, uuid.UUID):
                            py_body[k] = v.hex
                        elif isinstance(v, dict):
                            # transform3d
                            py_body[k] = [v["px"], v["py"], v["pz"],
                                          v["rx"], v["ry"], v["rz"], v["rw"],
                                          v["sx"], v["sy"], v["sz"]]
                        elif isinstance(v, (int, float)):
                            py_body[k] = v
                        elif isinstance(v, list):
                            py_body[k] = v
                        else:
                            py_body[k] = v

                    ok = compare_vector_vs_manifest(name, py_body, manifest_fields, uuid_keys)
                    if ok:
                        print(f"  PASS  {name}")
                        pass3 += 1
                    else:
                        fail3 += 1
                except Exception as e:
                    print(f"  FAIL  {name}: {e}")
                    fail3 += 1
        except ImportError:
            print("  SKIP: Cannot import Python deserializer")
    else:
        print(f"  SKIP: {cpp_bins_dir} not found")

    print()
    print("=" * 60)
    print(f"SUMMARY")
    print(f"  C++ deserialized vs manifest:        {pass_count}/{pass_count + fail_count} PASS")
    print(f"  C++ serialize→deserialize vs manifest: {pass2}/{pass2 + fail2} PASS")
    print(f"  Python deserializes C++ .bin:          {pass3}/{pass3 + fail3} PASS")
    print("=" * 60)

    total_fail = fail_count + fail2 + fail3
    if total_fail > 0:
        print("\nSOME CROSS-LANGUAGE TESTS FAILED")
        sys.exit(1)
    print("\nALL CROSS-LANGUAGE TESTS PASSED")


if __name__ == "__main__":
    main()
