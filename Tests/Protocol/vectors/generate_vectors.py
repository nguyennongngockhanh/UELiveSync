"""
Golden test vector generator for LiveSync protocol messages.

Generates binary test vectors for all message types.
Each vector includes the input values and the expected binary output.

Usage:
    python generate_vectors.py              # Refuse if v1/ already exists
    python generate_vectors.py --force      # Overwrite existing v1/ vectors
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Add parent to path for serializer package
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from serializer.serializer import serialize_message
from serializer.protocol import MsgType

# Import shared canonical hash from common module
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import compute_protocol_sha256, load_yaml, PROTOCOL_DIR


VECTORS_DIR = Path(__file__).parent / "v1"


# ─── Test Values ────────────────────────────────────────────────

# Fixed test UUIDs
UUID_ZERO = uuid.UUID("00000000-0000-0000-0000-000000000000")
UUID_ONE = uuid.UUID("11111111-1111-1111-1111-111111111111")
UUID_A = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")
UUID_B = uuid.UUID("ffeeddccbbaa99887766554433221100")

# Fixed test session ID
TEST_SESSION_ID = 0xDEADBEEF_CAFEBABE

# Fixed test transforms
IDENTITY_TRANSFORM = {
    "px": 0.0, "py": 0.0, "pz": 0.0,
    "rx": 0.0, "ry": 0.0, "rz": 0.0, "rw": 1.0,
    "sx": 1.0, "sy": 1.0, "sz": 1.0,
}

POSITION_ONLY_TRANSFORM = {
    "px": 100.5, "py": -200.25, "pz": 0.0,
    "rx": 0.0, "ry": 0.0, "rz": 0.0, "rw": 1.0,
    "sx": 1.0, "sy": 1.0, "sz": 1.0,
}

FULL_TRANSFORM = {
    "px": 1.5, "py": 2.5, "pz": 3.5,
    "rx": 0.0, "ry": 0.0, "rz": 0.7071068, "rw": 0.7071068,
    "sx": 2.0, "sy": 2.0, "sz": 2.0,
}


# ─── Vector Definitions ─────────────────────────────────────────

VECTORS = [
    # ── Pre-session messages ──
    {
        "name": "HELLO",
        "file": "HELLO.bin",
        "msg_type": MsgType.HELLO,
        "flags": 0,
        "sequence_id": 0,
        "session_id": None,
        "fields": {
            "protocol_version_major": 2,
            "protocol_version_minor": 0,
            "capabilities": 0x07,  # mesh_sync | material_sync | camera_sync
        },
    },
    {
        "name": "HELLO_ACK",
        "file": "HELLO_ACK.bin",
        "msg_type": MsgType.HELLO_ACK,
        "flags": 0,
        "sequence_id": 0,
        "session_id": None,
        "fields": {
            "protocol_version_major": 2,
            "protocol_version_minor": 0,
            "accepted_capabilities": 0x07,
            "max_chunk_size": 65536,
            "session_id": TEST_SESSION_ID,
        },
    },
    {
        "name": "REJECT",
        "file": "REJECT.bin",
        "msg_type": MsgType.REJECT,
        "flags": 0,
        "sequence_id": 0,
        "session_id": None,
        "fields": {
            "error_code": 0x0001,
            "reason": "Unsupported version",
            "min_version_major": 2,
            "min_version_minor": 0,
            "max_version_major": 2,
            "max_version_minor": 0,
        },
    },

    # ── System messages ──
    {
        "name": "HEARTBEAT",
        "file": "HEARTBEAT.bin",
        "msg_type": MsgType.HEARTBEAT,
        "flags": 0,
        "sequence_id": 1,
        "session_id": TEST_SESSION_ID,
        "fields": {},
    },
    {
        "name": "HEARTBEAT_ACK",
        "file": "HEARTBEAT_ACK.bin",
        "msg_type": MsgType.HEARTBEAT_ACK,
        "flags": 0,
        "sequence_id": 1,
        "session_id": TEST_SESSION_ID,
        "fields": {},
    },

    # ── Scene messages ──
    {
        "name": "SCENE_HASH",
        "file": "SCENE_HASH.bin",
        "msg_type": MsgType.SCENE_HASH,
        "flags": 0,
        "sequence_id": 1,
        "session_id": TEST_SESSION_ID,
        "fields": {
            "hash": 0x0102030405060708,
            "object_count": 42,
        },
    },
    {
        "name": "SCENE_FULL",
        "file": "SCENE_FULL.bin",
        "msg_type": MsgType.SCENE_FULL,
        "flags": 0,
        "sequence_id": 2,
        "session_id": TEST_SESSION_ID,
        "fields": {
            "object_count": 5,
        },
    },
    {
        "name": "SCENE_DELTA",
        "file": "SCENE_DELTA.bin",
        "msg_type": MsgType.SCENE_DELTA,
        "flags": 0,
        "sequence_id": 3,
        "session_id": TEST_SESSION_ID,
        "fields": {
            "object_count": 2,
        },
    },

    # ── Object messages ──
    {
        "name": "OBJECT_CREATE",
        "file": "OBJECT_CREATE.bin",
        "msg_type": MsgType.OBJECT_CREATE,
        "flags": 0,
        "sequence_id": 4,
        "session_id": TEST_SESSION_ID,
        "fields": {
            "persistent_id": str(UUID_A),
            "name": "Suzanne",
            "parent_id": str(UUID_ZERO),
            "primitive_type": 1,
            "transform": IDENTITY_TRANSFORM,
            "sequence_number": 100,
            "timestamp": 1700000000.5,
        },
    },
    {
        "name": "OBJECT_UPDATE",
        "file": "OBJECT_UPDATE.bin",
        "msg_type": MsgType.OBJECT_UPDATE,
        "flags": 0,
        "sequence_id": 5,
        "session_id": TEST_SESSION_ID,
        "fields": {
            "persistent_id": str(UUID_A),
            "transform": POSITION_ONLY_TRANSFORM,
            "name": "Suzanne_Renamed",
            "visibility": 1,
            "sequence_number": 101,
            "timestamp": 1700000001.0,
        },
    },
    {
        "name": "OBJECT_DELETE",
        "file": "OBJECT_DELETE.bin",
        "msg_type": MsgType.OBJECT_DELETE,
        "flags": 0,
        "sequence_id": 6,
        "session_id": TEST_SESSION_ID,
        "fields": {
            "persistent_id": str(UUID_A),
            "sequence_number": 42,
            "timestamp": 1700000000.5,
        },
    },
    {
        "name": "OBJECT_RENAME",
        "file": "OBJECT_RENAME.bin",
        "msg_type": MsgType.OBJECT_RENAME,
        "flags": 0,
        "sequence_id": 7,
        "session_id": TEST_SESSION_ID,
        "fields": {
            "persistent_id": str(UUID_A),
            "new_name": "Cube_Renamed",
        },
    },

    {
        "name": "OBJECT_REPARENT",
        "file": "OBJECT_REPARENT.bin",
        "msg_type": MsgType.OBJECT_REPARENT,
        "flags": 0,
        "sequence_id": 8,
        "session_id": TEST_SESSION_ID,
        "fields": {
            "persistent_id": str(UUID_A),
            "new_parent_id": str(UUID_B),
        },
    },
    {
        "name": "OBJECT_VISIBILITY",
        "file": "OBJECT_VISIBILITY.bin",
        "msg_type": MsgType.OBJECT_VISIBILITY,
        "flags": 0,
        "sequence_id": 9,
        "session_id": TEST_SESSION_ID,
        "fields": {
            "persistent_id": str(UUID_A),
            "visible": 0,
        },
    },

    # ── Mesh messages ──
    {
        "name": "MESH_START",
        "file": "MESH_START.bin",
        "msg_type": MsgType.MESH_START,
        "flags": 0,
        "sequence_id": 13,
        "session_id": TEST_SESSION_ID,
        "fields": {
            "persistent_id": str(UUID_A),
            "total_chunks": 3,
            "format_flags": 0x01,
        },
    },
    {
        "name": "MESH_CHUNK",
        "file": "MESH_CHUNK.bin",
        "msg_type": MsgType.MESH_CHUNK,
        "flags": 0,
        "sequence_id": 14,
        "session_id": TEST_SESSION_ID,
        "fields": {
            "persistent_id": str(UUID_A),
            "chunk_index": 0,
            "vertex_offset": 0,
            "vertex_count": 2,
            "index_count": 6,
            "data": b"\x00\x01\x02\x03\x04\x05",
        },
    },
    {
        "name": "MESH_END",
        "file": "MESH_END.bin",
        "msg_type": MsgType.MESH_END,
        "flags": 0,
        "sequence_id": 15,
        "session_id": TEST_SESSION_ID,
        "fields": {
            "persistent_id": str(UUID_A),
            "checksum": 0xDEADBEEF,
        },
    },
    {
        "name": "MESH_DATA",
        "file": "MESH_DATA.bin",
        "msg_type": MsgType.MESH_DATA,
        "flags": 0,
        "sequence_id": 16,
        "session_id": TEST_SESSION_ID,
        "fields": {
            "persistent_id": str(UUID_A),
            "vertex_count": 2,
            "index_count": 6,
            "format_flags": 0x01,
            "vertices": [0.0, 0.0, 0.0, 1.0, 1.0, 1.0],
            "normals": [0.0, 1.0, 0.0, 0.0, 1.0, 0.0],
            "uvs": [0.0, 0.0, 1.0, 1.0],
            "indices": [0, 1, 2, 3, 4, 5],
        },
    },
    {
        "name": "MESH_DELTA",
        "file": "MESH_DELTA.bin",
        "msg_type": MsgType.MESH_DELTA,
        "flags": 0,
        "sequence_id": 17,
        "session_id": TEST_SESSION_ID,
        "fields": {
            "persistent_id": str(UUID_A),
            "vertex_count": 2,
            "format_flags": 0x01,
            "vertices": [0.5, 0.5, 0.5, 1.5, 1.5, 1.5],
            "normals": [0.0, 1.0, 0.0, 0.0, 1.0, 0.0],
            "uvs": [0.0, 0.0, 1.0, 1.0],
        },
    },

    # ── Material messages ──
    {
        "name": "MATERIAL_CREATE",
        "file": "MATERIAL_CREATE.bin",
        "msg_type": MsgType.MATERIAL_CREATE,
        "flags": 0,
        "sequence_id": 18,
        "session_id": TEST_SESSION_ID,
        "fields": {
            "material_id": str(UUID_B),
            "name": "Gold",
            "base_color": [1.0, 0.84, 0.0, 1.0],
            "metallic": 1.0,
            "roughness": 0.3,
            "emission": [0.0, 0.0, 0.0],
            "texture_path": "T_Gold_BaseColor",
            "sequence_number": 41,
            "timestamp": 1700000041.0,
        },
    },
    {
        "name": "MATERIAL_UPDATE",
        "file": "MATERIAL_UPDATE.bin",
        "msg_type": MsgType.MATERIAL_UPDATE,
        "flags": 0,
        "sequence_id": 19,
        "session_id": TEST_SESSION_ID,
        "fields": {
            "material_id": str(UUID_B),
            "base_color": [0.8, 0.8, 0.8, 1.0],
            "metallic": 0.0,
            "roughness": 0.5,
            "emission": [0.0, 0.0, 0.0],
            "texture_path": "T_Update_BaseColor",
            "sequence_number": 42,
            "timestamp": 1700000042.0,
        },
    },
    {
        "name": "MATERIAL_ASSIGN",
        "file": "MATERIAL_ASSIGN.bin",
        "msg_type": MsgType.MATERIAL_ASSIGN,
        "flags": 0,
        "sequence_id": 20,
        "session_id": TEST_SESSION_ID,
        "fields": {
            "persistent_id": str(UUID_A),
            "material_id": str(UUID_B),
            "slot_index": 0,
            "sequence_number": 43,
            "timestamp": 1700000043.0,
        },
    },

    # ── FBX Import message ──
    {
        "name": "FBX_IMPORT_REQUEST",
        "file": "FBX_IMPORT_REQUEST.bin",
        "msg_type": MsgType.FBX_IMPORT_REQUEST,
        "flags": 0,
        "sequence_id": 24,
        "session_id": TEST_SESSION_ID,
        "fields": {
            "persistent_id": str(UUID_A),
            "version": 1,
            "fbx_path": "/home/user/.cache/uelivesync/fbx/00112233445566778899aabbccddeeff.fbx",
            "object_name": "Cabinet",
            "vert_count": 846,
            "tri_count": 1528,
            "mat_slot_count": 2,
            "geometry_hash": 0x123456789ABCDEF0,
            "sequence_number": 44,
            "timestamp": 1700000044.0,
        },
    },

    # ── Camera messages ──
    {
        "name": "CAMERA_CREATE",
        "file": "CAMERA_CREATE.bin",
        "msg_type": MsgType.CAMERA_CREATE,
        "flags": 0,
        "sequence_id": 21,
        "session_id": TEST_SESSION_ID,
        "fields": {
            "camera_id": str(UUID_A),
            "name": "MainCamera",
            "transform": FULL_TRANSFORM,
            "focal_length": 50.0,
            "sensor_width": 36.0,
            "sensor_height": 24.0,
            "clip_start": 0.1,
            "clip_end": 1000.0,
            "ortho_scale": 6.0,
            "camera_flags": 2,
            "sequence_number": 21,
            "timestamp": 1234500.0,
        },
    },
    {
        "name": "CAMERA_UPDATE",
        "file": "CAMERA_UPDATE.bin",
        "msg_type": MsgType.CAMERA_UPDATE,
        "flags": 0,
        "sequence_id": 22,
        "session_id": TEST_SESSION_ID,
        "fields": {
            "camera_id": str(UUID_A),
            "transform": POSITION_ONLY_TRANSFORM,
            "focal_length": 85.0,
            "sensor_width": 36.0,
            "sensor_height": 24.0,
            "clip_start": 0.5,
            "clip_end": 1000.0,
            "ortho_scale": 6.0,
            "camera_flags": 0,
            "sequence_number": 22,
            "timestamp": 1234600.0,
        },
    },
    {
        "name": "CAMERASETACTIVE",
        "file": "CAMERASETACTIVE.bin",
        "msg_type": MsgType.CAMERASETACTIVE,
        "flags": 0,
        "sequence_id": 23,
        "session_id": TEST_SESSION_ID,
        "fields": {
            "camera_id": str(UUID_A),
        },
    },

    # ── Scene hash exchange ──
    {
        "name": "SCENE_HASH_exchange_both",
        "file": "SCENE_HASH_exchange.bin",
        "msg_type": MsgType.SCENE_HASH,
        "flags": 0,
        "sequence_id": 1,
        "session_id": TEST_SESSION_ID,
        "fields": {
            "hash": 0xAABBCCDD11223344,
            "object_count": 100,
        },
    },

    # ── SYNC_ACK ──
    {
        "name": "SYNC_ACK",
        "file": "SYNC_ACK.bin",
        "msg_type": MsgType.SYNC_ACK,
        "flags": 0,
        "sequence_id": 10,
        "session_id": TEST_SESSION_ID,
        "fields": {
            "acked_seq": 9,
        },
    },

    # ── ERROR ──
    {
        "name": "ERROR",
        "file": "ERROR.bin",
        "msg_type": MsgType.ERROR,
        "flags": 0,
        "sequence_id": 11,
        "session_id": TEST_SESSION_ID,
        "fields": {
            "error_code": 0x0002,
            "message": "Malformed payload",
        },
    },

    # ── DISCONNECT ──
    {
        "name": "DISCONNECT",
        "file": "DISCONNECT.bin",
        "msg_type": MsgType.DISCONNECT,
        "flags": 0,
        "sequence_id": 12,
        "session_id": TEST_SESSION_ID,
        "fields": {},
    },

    # ── Compressed flag ──
    {
        "name": "HEARTBEAT_compressed",
        "file": "HEARTBEAT_compressed.bin",
        "msg_type": MsgType.HEARTBEAT,
        "flags": 0x01,  # compressed bit set
        "sequence_id": 20,
        "session_id": TEST_SESSION_ID,
        "fields": {},
    },

    # ── SequenceId wraparound ──
    {
        "name": "SEQUENCE_WRAPAROUND",
        "file": "SEQUENCE_WRAPAROUND.bin",
        "msg_type": MsgType.HEARTBEAT,
        "flags": 0,
        "sequence_id": 0xFFFFFFFF,
        "session_id": TEST_SESSION_ID,
        "fields": {},
    },
]


# ─── Generate Vectors ───────────────────────────────────────────

def _get_git_commit() -> str:
    """Get current git commit hash, or 'unknown' if not in a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()[:12]
    except Exception:
        pass
    return "unknown"


GENERATOR_VERSION = "1.0.0"
# protocol_revision tracks metadata/generator changes that do NOT affect wire format.
# If vectors change bytes on wire, bump protocol_version instead.
PROTOCOL_REVISION = 1

# Bump this if the hash algorithm or manifest schema changes.
# Vectors are an ABI contract — this version tracks the spec format, not the protocol.
SPEC_FORMAT_VERSION = 1


def generate_vectors(force: bool = False):
    """Generate all golden test vectors.

    Refuses to overwrite unless --force is passed.
    Freeze gate: SHA256SUMS existence (not individual .bin files).
    """
    sums_path = VECTORS_DIR / "SHA256SUMS"
    if sums_path.exists() and not force:
        print(
            f"ERROR: {sums_path} exists — vectors are frozen.\n"
            f"Refusing to overwrite. Use --force to regenerate.\n"
            f"\n"
            f"If you intend to create a new protocol version, create vectors/v2/ instead."
        )
        sys.exit(1)

    VECTORS_DIR.mkdir(parents=True, exist_ok=True)

    vectors_list = []

    for vec in VECTORS:
        binary = serialize_message(
            msg_type=vec["msg_type"],
            flags=vec["flags"],
            sequence_id=vec["sequence_id"],
            header_session_id=vec["session_id"],
            **vec["fields"],
        )

        # Write binary vector
        bin_path = VECTORS_DIR / vec["file"]
        bin_path.write_bytes(binary)

        # Record in manifest
        vectors_list.append({
            "name": vec["name"],
            "file": vec["file"],
            "msg_type": vec["msg_type"].value,
            "msg_type_name": vec["msg_type"].name,
            "flags": vec["flags"],
            "sequence_id": vec["sequence_id"],
            "session_id": vec["session_id"],
            "fields": vec["fields"],
            "size": len(binary),
        })

        print(f"  {vec['name']:25s} → {vec['file']:35s} ({len(binary)} bytes)")

    # Read protocol_version from YAML (single source of truth)
    mt = load_yaml("MessageTypes.yaml")
    protocol_version = mt.get("protocol", {}).get("version_major")
    if protocol_version is None:
        print("ERROR: Cannot read protocol.version_major from MessageTypes.yaml")
        sys.exit(1)

    # Write manifest with metadata
    manifest = {
        "spec_format_version": SPEC_FORMAT_VERSION,
        "protocol_version": protocol_version,
        # revision: metadata/generator changes only. Never changes wire bytes.
        # If vectors produce different bytes, bump protocol_version, not this.
        "protocol_revision": PROTOCOL_REVISION,
        "generator_version": GENERATOR_VERSION,
        "git_commit": _get_git_commit(),
        # SHA256 of YAML spec — proves vectors came from this exact spec
        "protocol_sha256": compute_protocol_sha256(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "vector_count": len(vectors_list),
        "vectors": vectors_list,
    }
    manifest_path = VECTORS_DIR / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, default=str)

    # Write SHA256SUMS
    sums_lines = []
    for bin_file in sorted(VECTORS_DIR.glob("*.bin")):
        h = hashlib.sha256(bin_file.read_bytes()).hexdigest()
        sums_lines.append(f"{h}  {bin_file.name}")
    h = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    sums_lines.append(f"{h}  manifest.json")
    sums_path = VECTORS_DIR / "SHA256SUMS"
    sums_path.write_text("\n".join(sums_lines) + "\n")

    print(f"\nGenerated {len(vectors_list)} vectors in {VECTORS_DIR}")
    print(f"Manifest: {manifest_path}")
    print(f"Checksums: {sums_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate golden test vectors")
    parser.add_argument("--force", action="store_true", help="Overwrite existing vectors")
    args = parser.parse_args()
    generate_vectors(force=args.force)
