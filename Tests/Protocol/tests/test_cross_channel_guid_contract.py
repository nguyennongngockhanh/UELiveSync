"""
Cross-channel Object-GUID contract regression test (MIG-006 / INV-2026-016).

The `persistent_id` field of FBX_IMPORT_REQUEST and MATERIAL_ASSIGN is an
Object-GUID reference. It MUST use the same LE/FGuid wire layout as the OBJECT
channel, so that all three channels decode to the same FGuid actor identity.

INV-2026-016 root cause: the material channel encoded `persistent_id` in RFC
4122 order while the object channel used LE/FGuid; the two decoded to different
FGuid identities and the material assign missed the actor. This test asserts
the invariant that would have caught that divergence: the same uuid.UUID must
produce byte-identical `persistent_id` sequences in OBJECT_CREATE,
FBX_IMPORT_REQUEST, and MATERIAL_ASSIGN, using the production addon builders.

Canonical layout (D1): Object-GUID references = LE/FGuid
(`Blender_Addon/protocol_guid.py:uuid_to_fguid_bytes`); material-namespace
`material_id` stays RFC 4122 (`uuid_to_rfc4122_bytes`).
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import types
import uuid

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
ADDON_DIR = REPO_ROOT / "Blender_Addon"

# Fixed Object-GUID (same style as the runtime evidence in INV-2026-016).
OBJECT_GUID = uuid.UUID("1dfb38b4-1227-41ce-9d3c-2dbe4953ac8a")
MATERIAL_ID = uuid.UUID("00000000-0000-0000-1d82-7b3fadd03b00")

# Expected LE/FGuid layout of OBJECT_GUID (mirrors Unreal FGuid A,B,C,D as LE
# uint32 after a raw memcpy of the 16 wire bytes).
OBJECT_GUID_FGUID_BYTES = bytes.fromhex("b438fb1dce412712be2d3c9d8aac5349")


def _load_addon_module(package_name: str, module_name: str):
    """Load a Blender_Addon submodule without importing bpy (__init__.py).

    The addon's __init__.py imports bpy, so we cannot import the package
    normally. These protocol modules are pure Python (struct/uuid only) and are
    loaded through a synthetic package so their relative imports resolve.
    """
    full_name = f"{package_name}.{module_name}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    path = ADDON_DIR / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(full_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = package_name
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def production_builders():
    pkg_name = "uelivesync_guid_contract_under_test"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(ADDON_DIR)]
        sys.modules[pkg_name] = pkg
    return (
        _load_addon_module(pkg_name, "object_protocol"),
        _load_addon_module(pkg_name, "fbx_protocol"),
        _load_addon_module(pkg_name, "material_protocol"),
    )


class TestCrossChannelPersistentIdContract:
    """Same Object-GUID must be byte-identical across all three channels."""

    def test_three_channels_share_persistent_id_bytes(self, production_builders):
        object_protocol, fbx_protocol, material_protocol = production_builders

        object_create = object_protocol.build_object_create(
            persistent_id=OBJECT_GUID,
            name="Cube",
            location=(0.0, 0.0, 0.0),
            rotation=(0.0, 0.0, 0.0, 1.0),
            scale=(1.0, 1.0, 1.0),
        )
        fbx_req = fbx_protocol.build_fbx_import_request(
            persistent_id=OBJECT_GUID,
            fbx_path="/tmp/test.fbx",
            object_name="Cube",
            vert_count=8,
            tri_count=12,
            mat_slot_count=1,
            geometry_hash=1,
        )
        material_assign = material_protocol.build_material_assign(
            persistent_id=OBJECT_GUID,
            material_id=MATERIAL_ID,
            slot_index=0,
            sequence_number=1,
            timestamp=0.0,
        )

        pid_create = object_create[:16]
        pid_fbx = fbx_req[:16]
        pid_assign = material_assign[:16]

        assert pid_create == OBJECT_GUID_FGUID_BYTES, (
            "OBJECT channel drifted from LE/FGuid layout"
        )
        assert pid_fbx == pid_create, (
            "FBX_IMPORT_REQUEST.persistent_id diverges from OBJECT channel "
            "(would miss the actor in Unreal)"
        )
        assert pid_assign == pid_create, (
            "MATERIAL_ASSIGN.persistent_id diverges from OBJECT channel "
            "(INV-2026-016 root cause)"
        )

    def test_material_id_namespace_stays_rfc(self, production_builders):
        _, _, material_protocol = production_builders

        material_assign = material_protocol.build_material_assign(
            persistent_id=OBJECT_GUID,
            material_id=MATERIAL_ID,
            slot_index=0,
            sequence_number=1,
            timestamp=0.0,
        )
        material_id_bytes = material_assign[16:32]
        assert material_id_bytes == MATERIAL_ID.bytes, (
            "material_id must stay RFC 4122 (material-namespace identity)"
        )

    def test_all_cross_channel_object_references_use_same_fguid(self, production_builders):
        """Every Object-GUID reference across the semantic protocol must encode
        to the same LE/FGuid bytes (D1). This extends the INV-2026-016 guard to
        the full reference set, not just the three normalized messages."""
        object_protocol, _, _ = production_builders

        builders = {
            "OBJECT_CREATE": object_protocol.build_object_create(
                persistent_id=OBJECT_GUID, name="Cube",
                location=(0, 0, 0), rotation=(0, 0, 0, 1), scale=(1, 1, 1)),
            "OBJECT_UPDATE": object_protocol.build_object_update(
                persistent_id=OBJECT_GUID,
                location=(0, 0, 0), rotation=(0, 0, 0, 1), scale=(1, 1, 1),
                name="Cube", visibility=1),
            "OBJECT_DELETE": object_protocol.build_object_delete(
                persistent_id=OBJECT_GUID),
            "OBJECT_RENAME": object_protocol.build_object_rename(
                persistent_id=OBJECT_GUID, new_name="Cube2"),
            "OBJECT_VISIBILITY": object_protocol.build_object_visibility(
                persistent_id=OBJECT_GUID, visible=True),
            "OBJECT_REPARENT": object_protocol.build_object_reparent(
                persistent_id=OBJECT_GUID, new_parent_id=OBJECT_GUID),
            "CAMERA_CREATE": object_protocol.build_camera_create(
                camera_id=OBJECT_GUID, name="Cam"),
            "CAMERA_UPDATE": object_protocol.build_camera_update(
                camera_id=OBJECT_GUID, location=(0, 0, 0),
                rotation=(0, 0, 0, 1), scale=(1, 1, 1),
                focal_length=50.0, sensor_width=36.0, sensor_height=24.0,
                clip_start=0.1, clip_end=1000.0, ortho_scale=6.0),
            "CAMERA_SETACTIVE": object_protocol.build_camera_setactive(
                camera_id=OBJECT_GUID),
        }

        for msg_name, body in builders.items():
            assert body[:16] == OBJECT_GUID_FGUID_BYTES, (
                f"{msg_name} reference diverges from the canonical LE/FGuid "
                "Object-GUID encoding (would miss the actor in Unreal)"
            )

        # OBJECT_REPARENT.new_parent_id is also an Object-GUID reference.
        reparent = builders["OBJECT_REPARENT"]
        assert reparent[16:32] == OBJECT_GUID_FGUID_BYTES, (
            "OBJECT_REPARENT.new_parent_id diverges from canonical LE/FGuid"
        )

        # CAMERA_CREATE.parent_id (when present) is an Object-GUID reference too.
        camera_create_with_parent = object_protocol.build_camera_create(
            camera_id=OBJECT_GUID, name="Cam", parent_id=OBJECT_GUID)
        assert camera_create_with_parent[
            # 16 (camera_id) + 2 (name len) + 3 (name) = parent_id offset
            16 + 2 + 3:16 + 2 + 3 + 16
        ] == OBJECT_GUID_FGUID_BYTES, (
            "CAMERA_CREATE.parent_id diverges from canonical LE/FGuid"
        )
