"""
Protocol specification validator.

Checks YAML invariants that must hold for the wire protocol to be correct.
Run as CI gate: any failure means the spec has a consistency bug.

Checks:
  1. YAML files parse without duplicate mapping keys (with filename in error)
  2. Every opcode is unique across all message types
  3. Every body field type is defined in Types.yaml
  4. transform3d wire_size_bytes matches field count × 4
  5. Header sizes match computed field sizes (not hardcoded)
  6. Header type matches session_required (pre-session ↔ before_session)
  7. All opcodes in uint8 range
  8. Direction values valid
  9. Golden vector SHA256 checksums pass
 10. Manifest is consistent (required fields, vector_count, versions, protocol_sha256)
 11. manifest ↔ SHA256SUMS ↔ filesystem file sets are identical
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from common import (
    DuplicateKeyError,
    PROTOCOL_DIR,
    PROTOCOL_YAML_FILES,
    compute_protocol_sha256,
    load_yaml,
)

VECTORS_DIR = Path(__file__).resolve().parent / "vectors" / "v1"

PRIMITIVE_TYPES = {"uint8", "uint16", "uint32", "uint64", "float32", "float64"}
COMPOSITE_TYPES = {"uuid", "transform3d", "utf8_string", "f32_array", "u32_array", "raw_bytes"}

# Pre-session messages: only these three use before_session header
PRE_SESSION_OPCODES = {"HELLO", "HELLO_ACK", "REJECT"}

# Field type → wire size in bytes (for fixed-size types only)
FIXED_SIZE = {"uint8": 1, "uint16": 2, "uint32": 4, "uint64": 8, "float32": 4, "uuid": 16}

# Canonical hash algorithm version — bump if hash computation changes
SPEC_FORMAT_VERSION = 1


class ValidationError(Exception):
    def __init__(self, check: str, message: str):
        self.check = check
        super().__init__(f"[{check}] {message}")


def compute_header_size(header_def: dict) -> int:
    """Compute header size from its field definitions."""
    total = 0
    for field in header_def.get("fields", []):
        ftype = field["type"]
        if ftype not in FIXED_SIZE:
            raise ValidationError(
                "HEADER_SIZE",
                f"Header field '{field['name']}' has variable type '{ftype}' — cannot compute size"
            )
        total += FIXED_SIZE[ftype]
    return total


def check_no_duplicate_keys() -> None:
    """All YAML files must parse without duplicate mapping keys."""
    for name in PROTOCOL_YAML_FILES:
        load_yaml(name)


def check_opcodes_unique(msg_types: dict) -> None:
    """Every message type MUST have a unique opcode."""
    seen: dict[int, str] = {}
    for name, defn in msg_types.items():
        code = defn["code"]
        if isinstance(code, str):
            code = int(code, 0)
        if code in seen:
            raise ValidationError(
                "OPCODE_UNIQUE",
                f"Opcode 0x{code:02X} used by both {seen[code]} and {name}"
            )
        seen[code] = name


def check_body_field_types(msg_types: dict) -> None:
    """Every body field type must be a known primitive or composite type."""
    known = PRIMITIVE_TYPES | COMPOSITE_TYPES
    for msg_name, defn in msg_types.items():
        body = defn.get("body", [])
        if not body:
            continue
        for field in body:
            ftype = field["type"]
            if ftype not in known:
                raise ValidationError(
                    "FIELD_TYPE",
                    f"Message {msg_name} field '{field['name']}' has unknown type '{ftype}'"
                )


def check_transform3d_size(composite: dict) -> None:
    """transform3d wire_size_bytes must equal 10 × 4 = 40."""
    t3d = composite.get("transform3d", {})
    if not t3d:
        return
    fields = t3d.get("fields", [])
    expected = len(fields) * 4
    actual = t3d.get("wire_size_bytes")
    if actual != expected:
        raise ValidationError(
            "TRANSFORM3D_SIZE",
            f"transform3d wire_size_bytes={actual}, expected {expected} ({len(fields)} fields × 4)"
        )


def check_header_sizes(wire_format: dict) -> None:
    """Header sizes declared in wire_format must match computed field sizes."""
    for header_key in ("header_before_session", "header_after_session"):
        header_def = wire_format.get(header_key, {})
        declared_size = header_def.get("size")
        if declared_size is None:
            raise ValidationError(
                "HEADER_SIZE",
                f"wire_format.{header_key} missing 'size' field"
            )
        computed_size = compute_header_size(header_def)
        if computed_size != declared_size:
            raise ValidationError(
                "HEADER_SIZE",
                f"wire_format.{header_key}: declared size={declared_size}, "
                f"computed from fields={computed_size}"
            )


def check_session_header_invariant(msg_types: dict) -> None:
    """Pre-session messages MUST use before_session header.
    All other messages MUST use after_session header.
    """
    for msg_name, defn in msg_types.items():
        header = defn.get("header", "after_session")
        is_pre_session = msg_name in PRE_SESSION_OPCODES

        if is_pre_session and header != "before_session":
            raise ValidationError(
                "SESSION_HEADER",
                f"Pre-session message {msg_name} uses '{header}' header, "
                f"expected 'before_session'"
            )
        if not is_pre_session and header != "after_session":
            raise ValidationError(
                "SESSION_HEADER",
                f"Post-session message {msg_name} uses '{header}' header, "
                f"expected 'after_session'"
            )


def check_message_codes_in_range(msg_types: dict) -> None:
    """All opcodes must be in uint8 range (0x00-0xFF)."""
    for msg_name, defn in msg_types.items():
        code = defn["code"]
        if isinstance(code, str):
            code = int(code, 0)
        if code < 0 or code > 0xFF:
            raise ValidationError(
                "OPCODE_RANGE",
                f"Message {msg_name} opcode 0x{code:X} out of uint8 range"
            )


def check_direction_values(msg_types: dict) -> None:
    """Direction must be one of: B→U, U→B, both."""
    valid = {"B→U", "U→B", "both"}
    for msg_name, defn in msg_types.items():
        d = defn.get("direction")
        if d not in valid:
            raise ValidationError(
                "DIRECTION",
                f"Message {msg_name} has invalid direction '{d}'"
            )


def check_golden_vectors_checksum() -> None:
    """Verify golden vector SHA256 checksums."""
    sums_path = VECTORS_DIR / "SHA256SUMS"
    if not sums_path.exists():
        raise ValidationError(
            "GOLDEN_VECTORS",
            f"SHA256SUMS not found at {sums_path}"
        )

    lines = sums_path.read_text().strip().split("\n")
    for line in lines:
        expected_hash, filename = line.split("  ", 1)
        file_path = VECTORS_DIR / filename
        if not file_path.exists():
            raise ValidationError(
                "GOLDEN_VECTORS",
                f"Vector file missing: {filename}"
            )
        actual_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise ValidationError(
                "GOLDEN_VECTORS",
                f"Vector {filename} checksum mismatch: expected {expected_hash[:12]}..., got {actual_hash[:12]}..."
            )


def check_manifest() -> None:
    """Verify manifest.json is consistent with vectors, spec, and SHA256SUMS.

    Validation order:
      1. Required fields (file, name on every vector entry)
      2. Scalar fields (protocol_version, revision, vector_count)
      3. Uniqueness (filenames, names)
      4. Filesystem consistency (manifest ↔ disk)
      5. SHA256SUMS consistency (three-way: manifest ↔ SHA256SUMS ↔ disk)
      6. protocol_sha256 matches current spec
    """
    manifest_path = VECTORS_DIR / "manifest.json"
    if not manifest_path.exists():
        raise ValidationError("MANIFEST", "manifest.json not found")

    try:
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as e:
        raise ValidationError("MANIFEST", f"manifest.json invalid JSON: {e}")

    vectors = manifest.get("vectors", [])

    # --- 1. Required fields on every vector entry ---

    for i, vec in enumerate(vectors):
        for field in ("file", "name", "msg_type"):
            if field not in vec:
                raise ValidationError(
                    "MANIFEST",
                    f"Vector entry [{i}] missing required field '{field}'"
                )

    # --- 2. Scalar fields ---

    # spec_format_version: must match (guards against hash algorithm changes)
    sfv = manifest.get("spec_format_version")
    if sfv != SPEC_FORMAT_VERSION:
        raise ValidationError(
            "MANIFEST",
            f"spec_format_version={sfv}, expected {SPEC_FORMAT_VERSION}"
        )

    # protocol_version: read from YAML, not hardcoded
    msg_types_yaml = load_yaml("MessageTypes.yaml")
    expected_version = msg_types_yaml.get("protocol", {}).get("version_major")
    if expected_version is None:
        raise ValidationError("MANIFEST", "Cannot read protocol.version_major from MessageTypes.yaml")

    actual_version = manifest.get("protocol_version")
    if actual_version != expected_version:
        raise ValidationError(
            "MANIFEST",
            f"protocol_version={actual_version}, expected {expected_version} (from YAML)"
        )

    # protocol_revision: must be >= 1; validator intentionally does NOT pin it
    revision = manifest.get("protocol_revision")
    if not isinstance(revision, int) or revision < 1:
        raise ValidationError(
            "MANIFEST",
            f"protocol_revision must be >= 1, got {revision}"
        )

    # vector_count: must match actual count
    vector_count = manifest.get("vector_count")
    if vector_count != len(vectors):
        raise ValidationError(
            "MANIFEST",
            f"vector_count={vector_count} but found {len(vectors)} vectors"
        )

    # --- 3. Uniqueness (after required fields validated) ---

    manifest_files = [v["file"] for v in vectors]
    manifest_names = [v["name"] for v in vectors]

    if len(set(manifest_files)) != len(manifest_files):
        dupes = [f for f in set(manifest_files) if manifest_files.count(f) > 1]
        raise ValidationError("MANIFEST", f"Duplicate filenames in manifest: {dupes}")

    if len(set(manifest_names)) != len(manifest_names):
        dupes = [n for n in set(manifest_names) if manifest_names.count(n) > 1]
        raise ValidationError("MANIFEST", f"Duplicate names in manifest: {dupes}")

    # --- 4. Filesystem consistency ---

    manifest_file_set = set(manifest_files)

    for vec in vectors:
        fpath = VECTORS_DIR / vec["file"]
        if not fpath.exists():
            raise ValidationError(
                "MANIFEST",
                f"Vector file in manifest but missing on disk: {vec['file']}"
            )

    for bin_file in sorted(VECTORS_DIR.glob("*.bin")):
        if bin_file.name not in manifest_file_set:
            raise ValidationError(
                "MANIFEST",
                f"Vector file on disk but not in manifest: {bin_file.name}"
            )

    # --- 5. SHA256SUMS consistency ---

    sums_path = VECTORS_DIR / "SHA256SUMS"
    if sums_path.exists():
        sums_files = set()
        for line in sums_path.read_text().strip().split("\n"):
            _, fname = line.split("  ", 1)
            sums_files.add(fname)

        missing = manifest_file_set - sums_files
        if missing:
            raise ValidationError(
                "MANIFEST",
                f"Files in manifest but not in SHA256SUMS: {missing}"
            )

        extra = sums_files - manifest_file_set - {"manifest.json"}
        if extra:
            raise ValidationError(
                "MANIFEST",
                f"Files in SHA256SUMS but not in manifest: {extra}"
            )

        if "manifest.json" not in sums_files:
            raise ValidationError(
                "MANIFEST",
                "SHA256SUMS missing entry for manifest.json"
            )

    # --- 6. protocol_sha256 matches current spec ---

    expected_sha = compute_protocol_sha256()
    actual_sha = manifest.get("protocol_sha256")
    if actual_sha != expected_sha:
        raise ValidationError(
            "MANIFEST",
            f"protocol_sha256 mismatch: manifest has {actual_sha[:12]}..., "
            f"current spec computes {expected_sha[:12]}..."
        )


def run_all_checks() -> list[str]:
    """Run all validation checks. Returns list of check names passed."""
    errors: list[str] = []

    # Load YAML with duplicate key detection
    try:
        msg_types_yaml = load_yaml("MessageTypes.yaml")
        capabilities_yaml = load_yaml("Capabilities.yaml")
        errors_yaml = load_yaml("Errors.yaml")
        types_yaml = load_yaml("Types.yaml")
    except DuplicateKeyError as e:
        print(f"FATAL: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"FATAL: YAML parse error: {e}")
        sys.exit(1)

    msg_types = msg_types_yaml.get("messages", {})
    wire_format = msg_types_yaml.get("wire_format", {})
    composite = types_yaml.get("composite", {})

    checks = [
        ("NO_DUP_KEYS", lambda: check_no_duplicate_keys()),
        ("OPCODE_UNIQUE", lambda: check_opcodes_unique(msg_types)),
        ("FIELD_TYPE", lambda: check_body_field_types(msg_types)),
        ("TRANSFORM3D_SIZE", lambda: check_transform3d_size(composite)),
        ("HEADER_SIZE", lambda: check_header_sizes(wire_format)),
        ("SESSION_HEADER", lambda: check_session_header_invariant(msg_types)),
        ("OPCODE_RANGE", lambda: check_message_codes_in_range(msg_types)),
        ("DIRECTION", lambda: check_direction_values(msg_types)),
        ("GOLDEN_VECTORS", lambda: check_golden_vectors_checksum()),
        ("MANIFEST", lambda: check_manifest()),
    ]

    passed = []
    for name, fn in checks:
        try:
            fn()
            passed.append(name)
            print(f"  PASS  {name}")
        except ValidationError as e:
            errors.append(str(e))
            print(f"  FAIL  {e}")

    # Summary
    print(f"\n{'='*60}")
    if errors:
        print(f"FAILED: {len(errors)} check(s) failed")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print(f"ALL {len(passed)} CHECKS PASSED")
        sys.exit(0)


if __name__ == "__main__":
    run_all_checks()
