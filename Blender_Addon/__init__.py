bl_info = {
    "name": "UE Live Sync",
    "author": "Harumaki",
    "version": (0, 2, 3),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar > UE Sync",
    "category": "3D View",
}

import bpy
import os

from bpy.props import (
    IntProperty,
    FloatProperty,
    BoolProperty,
    StringProperty,
    EnumProperty,
)

from . import network
from . import sync


# Phase 10J.5M: dual-logging helper for FBX diagnostics.
# Writes to both Blender console and the shared debug file
# (~/.cache/uelivesync/uelivesync_blender_debug.log).
def _fbx_log(msg):
    """Log msg to console AND to Blender debug file."""
    print(msg)
    try:
        network._append_blender_debug_log(msg)
    except Exception:
        pass


# =========================================================
# PREFERENCE CHANGE CALLBACKS
# =========================================================

def _on_timing_update(self, context):

    # Sync cached config when user changes timing prefs
    sync._sync_runtime_config()


def _on_timeline_sync_update(self, context):

    network.set_timeline_enabled(self.timeline_sync)


def _on_sequencer_ops_update(self, context):

    network.set_sequencer_op_enabled(self.sequencer_ops)


def _on_keyframe_sync_update(self, context):

    network.set_keyframe_enabled(self.keyframe_sync)


def _on_playback_sync_update(self, context):

    network.set_playback_enabled(self.playback_sync)


def _on_active_camera_sync_update(self, context):

    network.set_active_camera_enabled(self.active_camera_sync)


# =========================================================
# ADDON PREFERENCES
# =========================================================

class UELIVESYNC_AP_preferences(
    bpy.types.AddonPreferences
):
    bl_idname = __package__

    server_port: IntProperty(
        name="Server Port",
        default=57000,
        min=1024,
        max=65535,
        description="UE Live Sync server port",
    )

    threshold_location: FloatProperty(
        name="Location Threshold",
        default=0.01,
        min=0.0001,
        max=1.0,
        precision=4,
        description="Minimum location change to trigger sync",
    )

    threshold_rotation: FloatProperty(
        name="Rotation Threshold",
        default=0.0001,
        min=0.00001,
        max=1.0,
        precision=5,
        description="Minimum rotation change to trigger sync",
    )

    threshold_scale: FloatProperty(
        name="Scale Threshold",
        default=0.001,
        min=0.0001,
        max=1.0,
        precision=4,
        description="Minimum scale change to trigger sync",
    )

    verbose_logging: BoolProperty(
        name="Verbose Logging",
        default=False,
        description="Enable verbose sync logs",
    )

    playback_sync: BoolProperty(
        name="Playback Sync",
        default=False,
        description="Sync play/pause/stop state to UE",
        update=_on_playback_sync_update,
    )

    timeline_sync: BoolProperty(
        name="Timeline Sync",
        default=False,
        description="Sync timeline frame range and FPS to UE",
        update=_on_timeline_sync_update,
    )

    active_camera_sync: BoolProperty(
        name="Active Camera Sync",
        default=False,
        description="Sync active scene camera selection to UE",
        update=_on_active_camera_sync_update,
    )

    sequencer_ops: BoolProperty(
        name="Sequencer Ops",
        default=False,
        description="Sync sequencer operations (create sequence, add possessable, etc.) to UE",
        update=_on_sequencer_ops_update,
    )

    keyframe_sync: BoolProperty(
        name="Keyframe Sync",
        default=False,
        description="Sync transform keyframes (location, rotation, scale) to UE",
        update=_on_keyframe_sync_update,
    )

    default_primitive: EnumProperty(
        name="Default Primitive",
        items=[
            ('CUBE', "Cube", "/Engine/BasicShapes/Cube"),
            ('SPHERE', "Sphere", "/Engine/BasicShapes/Sphere"),
            ('CYLINDER', "Cylinder", "/Engine/BasicShapes/Cylinder"),
            ('PLANE', "Plane", "/Engine/BasicShapes/Plane"),
            ('EMPTY', "Empty", "No mesh — root-only actor"),
        ],
        default='CUBE',
        description="Default mesh primitive for actors spawned in UE",
    )

    heartbeat_interval: FloatProperty(
        name="Heartbeat Interval",
        default=5.0,
        min=1.0,
        max=60.0,
        precision=1,
        description="Seconds between heartbeat packets",
        update=_on_timing_update,
    )

    scan_interval: IntProperty(
        name="Scan Interval",
        default=300,
        min=30,
        max=3000,
        description="Frames between periodic scene safety scans",
        update=_on_timing_update,
    )

    def draw(self, context):

        layout = self.layout

        layout.prop(
            self, "server_port"
        )

        layout.separator()

        box = layout.box()
        box.label(
            text="Sync Thresholds"
        )

        box.prop(
            self, "threshold_location"
        )

        box.prop(
            self, "threshold_rotation"
        )

        box.prop(
            self, "threshold_scale"
        )

        layout.separator()

        box = layout.box()
        box.label(
            text="Timing"
        )

        box.prop(
            self, "heartbeat_interval"
        )

        box.prop(
            self, "scan_interval"
        )

        layout.separator()

        box = layout.box()
        box.label(
            text="Actor Spawn Settings"
        )

        box.prop(
            self, "default_primitive"
        )

        layout.separator()

        layout.prop(
            self, "verbose_logging"
        )

        layout.prop(
            self, "timeline_sync"
        )

        layout.prop(
            self, "playback_sync"
        )

        layout.prop(
            self, "active_camera_sync"
        )


# =========================================================
# ERROR REPORTING OPERATOR
# =========================================================

class UELIVESYNC_OT_show_error(
    bpy.types.Operator
):
    bl_idname = \
        "uelivesync.show_error"

    bl_label = \
        "UE Live Sync Error"

    error_message: StringProperty(
        name="Error",
        default="",
    )

    def execute(self, context):

        self.report(
            {'ERROR'},
            self.error_message
        )

        return {'FINISHED'}

    def invoke(
        self,
        context,
        event
    ):

        return context.window_manager.invoke_props_dialog(
            self,
            width=400,
        )

    def draw(self, context):

        layout = self.layout

        col = layout.column()

        col.scale_y = 2.0

        col.label(
            text=self.error_message,
            icon='ERROR',
        )


# =========================================================
# START/STOP OPERATORS
# =========================================================

class UELIVESYNC_OT_start(
    bpy.types.Operator
):
    bl_idname = \
        "uelivesync.start"

    bl_label = "Start UE Sync"

    def execute(self, context):

        import traceback as _tb

        try:

            sync.start_sync()

        except Exception:

            err = _tb.format_exc()

            print(
                "[LiveSync] CRITICAL: Operator start_sync() "
                f"raised exception:\n{err}"
            )

            self.report(
                {'ERROR'},
                f"Start sync failed — see console for details"
            )

        return {'FINISHED'}


class UELIVESYNC_OT_stop(
    bpy.types.Operator
):
    bl_idname = \
        "uelivesync.stop"

    bl_label = "Stop UE Sync"

    def execute(self, context):

        sync.stop_sync()

        return {'FINISHED'}


class UELIVESYNC_OT_rebind_all(
    bpy.types.Operator
):
    bl_idname = \
        "uelivesync.rebind_all"

    bl_label = "Rebind All"

    def execute(self, context):

        count = sync.rebind_all()

        self.report(
            {'INFO'},
            f"Rebound {count} objects"
        )

        return {'FINISHED'}


class UELIVESYNC_OT_dump_diagnostics(
    bpy.types.Operator
):
    bl_idname = \
        "uelivesync.dump_diagnostics"

    bl_label = "Dump Diagnostics"

    def execute(self, context):

        sync.dump_diagnostics()

        self.report(
            {'INFO'},
            "Diagnostics printed to Blender console"
        )

        return {'FINISHED'}


# =========================================================
# DISCOVERY SCAN OPERATOR (Phase 9 Stage 3B)
# =========================================================

class UELIVESYNC_OT_discover_server(
    bpy.types.Operator
):
    bl_idname = \
        "uelivesync.discover_server"

    bl_label = "Discover LiveSync Server"

    bl_description = \
        "Probe default hosts and configured host for " \
        "a running UE LiveSync listener on port 57000"

    def execute(self, context):

        results = network.discover_servers()

        found = [
            r for r in results
            if r["success"]
        ]

        if found:
            hosts = ", ".join(
                f"{r['host']}:{r['port']}"
                for r in found
            )
            self.report(
                {'INFO'},
                f"Found {len(found)} server(s): {hosts}"
            )
        else:
            errors = ", ".join(
                f"{r['host']}:{r['port']} ({r['error']})"
                for r in results
            )
            self.report(
                {'WARNING'},
                f"No server found: {errors}"
            )

        return {'FINISHED'}


# =========================================================
# DISCOVERY AUTO-FILL OPERATORS (Phase 9 Stage 3C)
# =========================================================

class UELIVESYNC_OT_use_discovered_server(
    bpy.types.Operator
):
    bl_idname = \
        "uelivesync.use_discovered_server"

    bl_label = "Use Discovered Server"

    bl_description = \
        "Apply the first discovered server host:port " \
        "to the sync connection"

    def execute(self, context):

        if network.apply_discovery_result():
            host = network._host
            port = network._port
            print(
                f"[DISCOVERY][APPLY] host={host} port={port}"
            )
            self.report(
                {'INFO'},
                f"Applied discovered server {host}:{port}"
            )
        else:
            print("[DISCOVERY][APPLY] no successful discovery result")
            self.report(
                {'WARNING'},
                "No successful discovery result to apply"
            )

        return {'FINISHED'}


class UELIVESYNC_OT_discover_and_connect(
    bpy.types.Operator
):
    bl_idname = \
        "uelivesync.discover_and_connect"

    bl_label = "Discover & Connect"

    bl_description = \
        "Scan for a running UE LiveSync server and " \
        "connect to the first one found"

    def execute(self, context):

        import traceback as _tb

        # Phase 9 Stage 3C: discover + apply + connect
        print("[DISCOVERY][CONNECT] starting discovery")
        results = network.discover_servers()

        best = network.get_best_discovery_result()

        if best is None:
            print("[DISCOVERY][CONNECT] no server found")
            self.report(
                {'WARNING'},
                "No LiveSync server discovered"
            )
            return {'CANCELLED'}

        host = best["host"]
        port = best["port"]

        print(
            f"[DISCOVERY][CONNECT] best result: "
            f"{host}:{port}"
        )

        # Apply to globals
        network.apply_discovery_result()
        print(
            f"[DISCOVERY][APPLY] host={network._host} "
            f"port={network._port}"
        )

        # Disconnect existing if any
        if network.is_connected():
            print("[DISCOVERY][CONNECT] disconnecting existing")
            sync.stop_sync()

        # Connect
        try:
            network.connect(host, port)
        except Exception:
            err = _tb.format_exc()
            print(
                "[DISCOVERY][CONNECT] connect failed:\n"
                f"{err}"
            )
            self.report(
                {'ERROR'},
                f"Connect to {host}:{port} failed"
            )
            return {'CANCELLED'}

        if network.is_connected():
            print(
                f"[DISCOVERY][CONNECT] connected to "
                f"{host}:{port}"
            )
            self.report(
                {'INFO'},
                f"Connected to discovered server "
                f"{host}:{port}"
            )
        else:
            print(
                "[DISCOVERY][CONNECT] connect returned "
                "without error but not connected"
            )
            self.report(
                {'WARNING'},
                f"Could not connect to {host}:{port}"
            )

        return {'FINISHED'}


# =========================================================
# MANUAL MESH SYNC OPERATOR (Phase 7C Stage 2B.3)
# =========================================================

class UELIVESYNC_OT_sync_selected_mesh_to_ue(
    bpy.types.Operator
):
    bl_idname = \
        "uelivesync.sync_selected_mesh_to_ue"

    bl_label = "Sync Selected Mesh to UE"

    bl_description = \
        "Send loop-expanded full-attribute mesh data for " \
        "selected MESH objects to UE via PT_Mesh v1"

    def execute(self, context):

        selected = [
            obj for obj in context.selected_objects
            if obj.type == 'MESH'
        ]

        if not selected:
            self.report(
                {'WARNING'},
                "No MESH objects selected"
            )

            return {'CANCELLED'}

        if not network.is_connected():

            self.report(
                {'WARNING'},
                "Not connected to UE"
            )

            return {'CANCELLED'}

        import uuid

        depsgraph = \
            context.evaluated_depsgraph_get()

        synced_count = 0

        for obj in selected:

            try:

                guid_hex = sync.ensure_guid(obj)
                guid_obj = uuid.UUID(guid_hex)

                evaluated_obj = \
                    obj.evaluated_get(depsgraph)

                if evaluated_obj.type != 'MESH':
                    continue

                mesh = evaluated_obj.to_mesh()

                if mesh is None:
                    continue

                try:

                    mesh.calc_loop_triangles()

                    triangle_count = \
                        len(mesh.loop_triangles)

                    if triangle_count == 0:
                        continue

                    render_vertices, stride, \
                        uv0_fb, diags = \
                        network.extract_loop_expanded_render_vertices(
                            mesh,
                        )

                    if not render_vertices:
                        continue

                    version_hash = \
                        network.compute_render_vertex_version_hash(
                            render_vertices,
                            stride,
                        )

                    chunks = \
                        network.chunk_render_vertices(
                            render_vertices,
                            stride,
                            triangle_count,
                        )

                    chunk_count = len(chunks)

                    for chunk_data in chunks:

                        ci = chunk_data["chunk_index"]

                        chunk_payload = \
                            network.serialize_full_attr_mesh_chunk_v1(
                                guid_obj,
                                version_hash,
                                ci,
                                chunk_count,
                                chunk_data["vertices"],
                                chunk_data["indices"],
                                flags=network.MESH_CHUNK_FLAG_FULL_ATTR,
                                vertex_stride=stride,
                            )

                        network.send_objects(
                            [chunk_payload],
                            packet_type=network.PT_Mesh,
                            version=network.LIVE_SYNC_VERSION_V5,
                        )

                    print(
                        f"[MESH][ATTR] Manual sync: {obj.name} "
                        f"({triangle_count} tris, "
                        f"{len(render_vertices)} verts, "
                        f"{chunk_count} chunk(s), stride={stride})"
                    )

                    synced_count += 1

                finally:

                    evaluated_obj.to_mesh_clear()

            except Exception as e:

                print(
                    f"[MESH][ATTR] ERROR: {obj.name} — {e}"
                )

        if synced_count > 0:

            self.report(
                {'INFO'},
                f"Synced {synced_count} mesh object(s) to UE"
            )

        else:

            self.report(
                {'WARNING'},
                "No mesh objects could be synced"
            )

        return {'FINISHED'}


# =========================================================
# PHASE 7C STAGE 3A.1: FBX MESH HANDOFF OPERATOR
# =========================================================

# =========================================================
# FBX LOCAL-PITCH EXPORT HELPER (Phase 10J)
# =========================================================
# Exports a Blender object as an FBX with geometry baked into
# local-space (identity transform).  This prevents the UE actor
# from seeing a double-offset pivot:
#   - Blender object transform (e.g. X=410)
#   - FBX scene-node transform  (also X=410)
# Result: mesh vertices are relative to object origin,
# UE actor transform aligns the visible geometry correctly.
# =========================================================

def _compute_mesh_bounds_quick(mesh_or_bm):
    """Return (min_corner, max_corner) tuple for a mesh or bmesh."""
    verts = mesh_or_bm.vertices if hasattr(mesh_or_bm, "vertices") else mesh_or_bm.verts
    xs = [v.co.x for v in verts]
    ys = [v.co.y for v in verts]
    zs = [v.co.z for v in verts]
    return ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))


def _export_object_local_fbx(obj, filepath, depsgraph):
    """Export *obj* to *filepath* with identity-transform pivot.

    Phase 10J.5O: unit conversion via FBX_SCALE_UNITS (Blender export)
    with bConvertSceneUnit=true on UE side. Single conversion
    through FBX file unit metadata.
    """
    import bpy
    import bmesh
    import math

    guid_str = ""
    try:
        from . import sync as _sync_mod
        guid_str = _sync_mod.ensure_guid(obj)
    except Exception:
        pass
    guid_short = guid_str[:8] if guid_str else "?" * 8

    _fbx_log(f"[FBX][EXPORT_ENTER] guid={guid_short} obj={obj.name}")

    # Preserve original selection and active object
    orig_selected = [o for o in bpy.context.selected_objects]
    orig_active = bpy.context.active_object

    _fbx_log(f"[FBX][BOUNDS_SRC] guid={guid_short} obj={obj.name} "
             f"mode={'EDIT' if obj.mode == 'EDIT' else 'OBJECT'} "
             f"objectScale=({obj.scale[0]:.4f},{obj.scale[1]:.4f},{obj.scale[2]:.4f})")

    # Create evaluated mesh data-block
    evaluated_obj = obj.evaluated_get(depsgraph)
    mesh = evaluated_obj.to_mesh()
    if mesh is None:
        _fbx_log("[FBX] Cannot evaluate mesh for local-pivot export")
        return False

    try:
        if len(mesh.vertices) > 0:
            bmin, bmax = _compute_mesh_bounds_quick(mesh)
            extent = ((bmax[0] - bmin[0]) / 2, (bmax[1] - bmin[1]) / 2, (bmax[2] - bmin[2]) / 2)
            _fbx_log(f"[FBX][BOUNDS_EVAL] guid={guid_short} "
                     f"bounds=({bmin[0]:.4f},{bmin[1]:.4f},{bmin[2]:.4f})-({bmax[0]:.4f},{bmax[1]:.4f},{bmax[2]:.4f}) "
                     f"halfExtent=({extent[0]:.4f},{extent[1]:.4f},{extent[2]:.4f}) "
                     f"vertCount={len(mesh.vertices)} units=blender_units")
        else:
            _fbx_log(f"[FBX][BOUNDS_EVAL] guid={guid_short} empty_mesh")

        # Create a temporary object with identity transform
        temp_obj_name = f"_UELivesyncFBX_{obj.name}"
        temp_mesh = bpy.data.meshes.new(temp_obj_name + "_mesh")

        bm = bmesh.new()
        bm.from_mesh(mesh)

        if len(bm.verts) > 0:
            pbmin, pbmax = _compute_mesh_bounds_quick(bm)
            pre_max = max(abs(pbmin[0]), abs(pbmin[1]), abs(pbmin[2]), abs(pbmax[0]), abs(pbmax[1]), abs(pbmax[2]))
            _fbx_log(f"[FBX][BOUNDS_PRE_BAKE] guid={guid_short} "
                     f"bounds=({pbmin[0]:.4f},{pbmin[1]:.4f},{pbmin[2]:.4f})-({pbmax[0]:.4f},{pbmax[1]:.4f},{pbmax[2]:.4f}) "
                     f"maxExtent={pre_max:.4f}")
        else:
            pre_max = 0.0

        _fbx_log(f"[FBX][UNIT_BAKE] guid={guid_short} "
                 f"action=fbx_scale_units scale=1.0 reason=fbx_unit_metadata "
                 f"maxCoord={pre_max:.4f}")

        # Phase 10J.5O: NO vertex bake — FBX_SCALE_UNITS handles conversion
        # through FBX file unit metadata.

        if len(bm.verts) > 0:
            post_min, post_max_coord = _compute_mesh_bounds_quick(bm)
            post_max_val = max(abs(post_min[0]), abs(post_min[1]), abs(post_min[2]),
                               abs(post_max_coord[0]), abs(post_max_coord[1]), abs(post_max_coord[2]))
            _fbx_log(f"[FBX][BOUNDS_POST_BAKE] guid={guid_short} "
                     f"bakeScale=1.0 "
                     f"bounds=({post_min[0]:.4f},{post_min[1]:.4f},{post_min[2]:.4f})-("
                     f"{post_max_coord[0]:.4f},{post_max_coord[1]:.4f},{post_max_coord[2]:.4f}) "
                     f"maxExtent={post_max_val:.4f}")

        bm.to_mesh(temp_mesh)
        bm.free()
        temp_mesh.update()

        temp_obj = bpy.data.objects.new(temp_obj_name, temp_mesh)
        bpy.context.collection.objects.link(temp_obj)

        temp_obj.location = (0.0, 0.0, 0.0)
        temp_obj.rotation_euler = (0.0, 0.0, 0.0)
        temp_obj.scale = (1.0, 1.0, 1.0)

        if hasattr(obj, "material_slots") and obj.material_slots:
            for slot_index, slot in enumerate(obj.material_slots):
                if slot.material is not None:
                    if slot_index < len(temp_mesh.materials):
                        temp_mesh.materials[slot_index] = slot.material
                    else:
                        temp_mesh.materials.append(slot.material)

        bpy.ops.object.select_all(action='DESELECT')
        temp_obj.select_set(True)
        bpy.context.view_layer.objects.active = temp_obj

        # Texture image scan diagnostic before FBX export.
        # NOTE: use 'tex_filepath' not 'filepath' — 'filepath' is the
        # FBX export path parameter and MUST NOT be shadowed.
        fbx_export_path = filepath  # preserve before texture loop
        if obj.material_slots:
            for slot in obj.material_slots:
                mat = slot.material
                if mat and mat.use_nodes:
                    for node in mat.node_tree.nodes:
                        if node.type == 'TEX_IMAGE' and node.image:
                            img = node.image
                            tex_filepath = getattr(img, "filepath", "")
                            tex_filepath_raw = getattr(img, "filepath_raw", "")
                            source = getattr(img, "source", "")
                            is_packed = bool(getattr(img, "packed_file", False))
                            tex_exists = os.path.isfile(bpy.path.abspath(tex_filepath)) if tex_filepath else False
                            _fbx_log(
                                f"[FBX][TEXTURE_IMAGE_SCAN] object={obj.name} "
                                f"material={mat.name} image={img.name} "
                                f"source={source} filepath={tex_filepath} "
                                f"filepath_raw={tex_filepath_raw} "
                                f"exists={1 if tex_exists else 0} "
                                f"packed={1 if is_packed else 0}")
        else:
            _fbx_log(f"[FBX][TEXTURE_IMAGE_SCAN] object={obj.name} no_material_slots")

        # --- Task A/B/F/G: safety guard before export ---
        _fbx_log(f"[FBX][EXPORT_CALL] guid={guid_short} filepath={fbx_export_path} is_fbx={1 if fbx_export_path.endswith('.fbx') else 0}")
        if not fbx_export_path.endswith(".fbx"):
            _fbx_log(f"[FBX][EXPORT_ABORT] reason=export_filepath_not_fbx filepath={fbx_export_path}")
            return False

        _fbx_log(f"[FBX][EXPORT_SETTINGS] guid={guid_short} "
                 f"global_scale=1.0 apply_scale_options=FBX_SCALE_UNITS "
                 f"bake_space_transform=0 use_mesh_modifiers=0 use_tspace=0 "
                 f"path_mode=STRIP embed_textures=0 "
                 f"unit_strategy=fbx_scale_units")

        try:
            bpy.ops.export_scene.fbx(
                filepath=fbx_export_path,
                use_selection=True,
                object_types={'MESH'},
                global_scale=1.0,
                apply_scale_options='FBX_SCALE_UNITS',
                bake_space_transform=False,
                mesh_smooth_type='FACE',
                use_mesh_modifiers=False,
                use_tspace=False,
                path_mode='STRIP',
            )
        except Exception as e:
            _fbx_log(f"[FBX] Export failed: {e}")
            return False

        return True

    finally:
        try:
            if 'temp_mesh' in locals():
                bpy.data.meshes.remove(temp_mesh)
            if 'temp_obj' in locals():
                bpy.context.collection.objects.unlink(temp_obj)
                bpy.data.objects.remove(temp_obj)
        except Exception:
            pass

        bpy.ops.object.select_all(action='DESELECT')
        for o in orig_selected:
            try:
                o.select_set(True)
            except Exception:
                pass
        if orig_active:
            bpy.context.view_layer.objects.active = orig_active


# =========================================================
# A3.2 Structured sidecar result
# =========================================================

from dataclasses import dataclass


@dataclass(frozen=True)
class SidecarPreparationResult:
    """One authoritative result per TextureAssetSource.

    One result object per TextureAssetSource — success or failure.
    Callers derive sidecar_copied, sidecar_info, material suppression,
    dirty signatures, and MTEX records from this result, not from
    scattered source fields or positional tuples.
    """
    source: 'TextureAssetSource'
    status: str           # "ready" | "failed"
    action: str           # "copied" | "overwritten" | "exported" | "verified"
                           # | "collision" | "content_collision"
                           # | "file_not_found" | "image_not_found"
                           # | "unsupported_source" | "exception"
                           # | "unsafe_*"
    source_locator: str
    destination_path: str
    filename: str         # basename(destination_path)
    image_name: str
    size: int
    asset_id: str = ""    # 16-char lowercase xxh64 hex of final bytes;
                           # "" for failed results
    error: str = ""


# =========================================================
# A3.1 Texture identity and sidecar preparation
# =========================================================


@dataclass
class TextureAssetSource:
    """One unique TEX_IMAGE node referenced by one or more usages.

    Immutable source input and identity data only.
    Preparation outcomes must never be written back onto this class.
    """
    mat_name: str
    node_name: str
    image_name: str
    source_kind: str
    filepath_raw: str
    filepath: str
    is_packed: bool
    width: int
    height: int
    file_format: str
    colorspace: str


@dataclass
class TextureUsage:
    """One slot+channel reference to a TextureAssetSource."""
    slot_index: int
    channel: int
    source: 'TextureAssetSource'
    flags: int
    sock_name: str


def _get_instance_width_height(img):
    sz = getattr(img, "size", None)
    if sz and len(sz) >= 2:
        return int(sz[0]), int(sz[1])
    return (0, 0)


def _extract_texture_usages_and_sources(obj):
    """Two-pass extraction of all texture usages and unique sources.

    Pass 1: Every TEX_IMAGE node in each material node tree becomes a
    TextureAssetSource (connected or not).

    Pass 2: Principled BSDF socket connections create TextureUsage objects
    referencing those existing sources, with precomputed MTEX flags.

    Returns:
        tuple (list_of_TextureAssetSource, list_of_TextureUsage)
    """
    from collections import OrderedDict

    sources = []
    usages = []
    source_by_key = OrderedDict()

    if not obj.material_slots:
        return sources, usages

    # Pass 1: collect all TEX_IMAGE nodes as TextureAssetSource
    for slot in obj.material_slots:
        mat = slot.material
        if not mat or not mat.use_nodes or not mat.node_tree:
            continue
        for node in mat.node_tree.nodes:
            if getattr(node, "type", None) != "TEX_IMAGE":
                continue
            image = getattr(node, "image", None)
            if image is None:
                continue
            source_key = (mat.name, node.name)
            if source_key in source_by_key:
                continue
            filepath_raw = getattr(image, "filepath_raw", "") or ""
            filepath_val = getattr(image, "filepath", "") or ""
            src_kind = getattr(image, "source", "") or ""
            is_packed = bool(getattr(image, "packed_file", None))
            width, height = _get_instance_width_height(image)
            file_fmt = getattr(image, "file_format", "PNG")
            try:
                cs = getattr(image, "colorspace_settings", None)
                colorspace = getattr(cs, "name", "sRGB") if cs else "sRGB"
            except Exception:
                colorspace = "sRGB"
            src = TextureAssetSource(
                mat_name=mat.name,
                node_name=node.name,
                image_name=getattr(image, "name", "") or "",
                source_kind=src_kind,
                filepath_raw=filepath_raw,
                filepath=filepath_val,
                is_packed=is_packed,
                width=width,
                height=height,
                file_format=file_fmt,
                colorspace=colorspace,
            )
            source_by_key[source_key] = len(sources)
            sources.append(src)

    # Pass 2: trace Principled socket connections to create usages
    for slot_idx, slot in enumerate(obj.material_slots):
        mat = slot.material
        if not mat or not mat.use_nodes or not mat.node_tree:
            continue

        principled = None
        for node in mat.node_tree.nodes:
            if getattr(node, "type", None) == "BSDF_PRINCIPLED":
                principled = node
                break
        if principled is None:
            continue

        target_sockets = {}
        for sock_name, channel in (
            ("Base Color", network.MTEX_CHANNEL_BASECOLOR),
            ("Roughness", network.MTEX_CHANNEL_ROUGHNESS),
            ("Metallic", network.MTEX_CHANNEL_METALLIC),
            ("Alpha", network.MTEX_CHANNEL_ALPHA),
            ("Normal", network.MTEX_CHANNEL_NORMAL),
        ):
            sock = principled.inputs.get(sock_name)
            if sock is not None and sock.is_linked:
                target_sockets[sock_name] = channel

        # Coat fallback for Roughness
        roughness_sock = principled.inputs.get("Roughness")
        coat_roughness = principled.inputs.get("Coat Roughness")
        if "Roughness" not in target_sockets and roughness_sock is not None \
                and coat_roughness is not None and coat_roughness.is_linked:
            target_sockets["Coat Roughness"] = network.MTEX_CHANNEL_ROUGHNESS

        # Coat fallback for Normal
        normal_sock = principled.inputs.get("Normal")
        coat_normal = principled.inputs.get("Coat Normal")
        if "Normal" not in target_sockets:
            ns_linked = normal_sock is not None and normal_sock.is_linked
            cn_linked = coat_normal is not None and coat_normal.is_linked
            if not ns_linked and cn_linked:
                target_sockets["Coat Normal"] = network.MTEX_CHANNEL_NORMAL

        if not target_sockets:
            continue

        for sock_name, channel in target_sockets.items():
            sock = principled.inputs.get(sock_name)
            if sock is None or not sock.is_linked:
                continue

            from_node = sock.links[0].from_node

            if channel == network.MTEX_CHANNEL_NORMAL \
                    and getattr(from_node, "type", None) == "NORMAL_MAP":
                nm_color = from_node.inputs.get("Color")
                if nm_color is not None and nm_color.is_linked:
                    from_node = nm_color.links[0].from_node

            if getattr(from_node, "type", None) != "TEX_IMAGE":
                indirect_types = {
                    "MIX_RGB", "COLOR_RAMP", "INVERT",
                    "GAMMA", "CURVES", "HUE_SATURATION",
                }
                if getattr(from_node, "type", None) in indirect_types:
                    color_input = from_node.inputs.get("Color")
                    if color_input is None:
                        color_input = from_node.inputs.get("Fac")
                    if color_input is None:
                        color_input = from_node.inputs.get("Value")
                    if color_input is not None and color_input.is_linked:
                        from_node = color_input.links[0].from_node

            if getattr(from_node, "type", None) != "TEX_IMAGE":
                continue

            image = getattr(from_node, "image", None)
            if image is None:
                continue

            source_key = (mat.name, from_node.name)
            src_idx = source_by_key.get(source_key)
            if src_idx is None:
                continue
            src = sources[src_idx]

            # Compute MTEX flags once during extraction
            flags = 0
            if src.is_packed:
                flags |= network.MTEX_FLAG_IMAGE_PACKED
            src_is_file = src.source_kind == 'FILE' and not src.is_packed
            if src_is_file and src.filepath:
                _absp = bpy.path.abspath(src.filepath_raw or src.filepath)
                if _absp.startswith("/") or (len(_absp) > 1 and _absp[1] == ":"):
                    flags |= network.MTEX_FLAG_PATH_ABSOLUTE
            _csl = src.colorspace.lower()
            if "non-color" in _csl or "noncolor" in _csl or "raw" in _csl:
                flags |= network.MTEX_FLAG_COLORSPACE_NON_COLOR
            elif "srgb" in _csl:
                flags |= network.MTEX_FLAG_COLORSPACE_SRGB
            if not (flags & (network.MTEX_FLAG_COLORSPACE_SRGB
                             | network.MTEX_FLAG_COLORSPACE_NON_COLOR)):
                if channel in (network.MTEX_CHANNEL_ROUGHNESS,
                               network.MTEX_CHANNEL_METALLIC,
                               network.MTEX_CHANNEL_NORMAL):
                    flags |= network.MTEX_FLAG_COLORSPACE_NON_COLOR

            usage = TextureUsage(
                slot_index=slot_idx,
                channel=channel,
                source=src,
                flags=flags,
                sock_name=sock_name,
            )
            usages.append(usage)

    return sources, usages


def _check_destination_safe(dest_dir, dest_path):
    """Validate that dest_path is safe to write.

    Checks:
        1. dest_path is contained within dest_dir (realpath).
        2. dest_path is not a symlink.
        3. If dest_path exists, it is a regular file.
        4. dest_dir exists and is a directory.

    Returns:
        (is_safe, reason) tuple.
    """
    if not os.path.isdir(dest_dir):
        return False, "dest_dir_not_found"

    real_dest_dir = os.path.realpath(dest_dir)
    real_dest_path = os.path.realpath(dest_path)

    common = os.path.commonpath([real_dest_dir, real_dest_path])
    if common != real_dest_dir:
        return False, "path_escape_detected"

    if os.path.islink(dest_path):
        return False, "path_is_symlink"

    if os.path.exists(dest_path) and not os.path.isfile(dest_path):
        return False, "existing_not_regular_file"

    return True, ""


def _register_sidecar_key(registry, dest_dir, sidecar_key, canonical_locator):
    """Register a sidecar key and detect same-cycle collisions.

    Args:
        registry: dict (real_dest_dir, sidecar_key) -> canonical_locator.
        dest_dir: Destination directory.
        sidecar_key: Filename base (without extension).
        canonical_locator: Canonical locator bytes for comparison.

    Returns:
        (registered, existing_locator) where registered is True if new.
    """
    real_dir = os.path.realpath(dest_dir)
    key = (real_dir, sidecar_key)

    if key in registry:
        existing = registry[key]
        if existing != canonical_locator:
            return False, existing
        return False, None

    registry[key] = canonical_locator
    return True, None


def _prepare_source_sidecar(source, dest_dir, collision_registry, guid_short="?"):
    """Prepare sidecar file for one TextureAssetSource.

    Content-keyed: filename derives from xxh64 of final bytes.
    Every ready result has a verified asset_id matching the destination bytes.
    Never returns ready without validated destination.
    NEVER mutates source — all outcomes go through SidecarPreparationResult.

    The collision_registry parameter is retained for API compatibility
    but is no longer used — content-keyed filenames make registry-based
    collision detection redundant.

    Args:
        source: TextureAssetSource to prepare.
        dest_dir: Destination cache directory.
        collision_registry: Unused (retained for API compat).
        guid_short: Short GUID for logging.

    Returns:
        SidecarPreparationResult — always returns one result (success or failure).
    """
    import shutil
    import tempfile as _tempfile

    ext_map = {
        "PNG": ".png", "JPEG": ".jpg", "JPEG2000": ".jp2",
        "TARGA": ".tga", "TIFF": ".tif", "OPEN_EXR": ".exr",
        "BMP": ".bmp", "HDR": ".hdr",
    }
    ext = ext_map.get(source.file_format, ".png")

    # Resolve locator (for logging and failure results)
    if source.source_kind == 'FILE' and not source.is_packed:
        abs_path = bpy.path.abspath(source.filepath_raw or source.filepath)
        locator = abs_path if os.path.isfile(abs_path) else (source.filepath_raw or source.filepath)
    elif source.is_packed:
        locator = source.image_name
    else:
        locator = source.image_name

    dest_path = ""
    basename_result = ""

    def _make_failure(action, error_msg):
        _fbx_log(f"[FBX][A3.1][SIDECAR_{action.upper()}] guid={guid_short} "
                 f"source=({source.mat_name}:{source.node_name}) error={error_msg}")
        return SidecarPreparationResult(
            source=source,
            status="failed",
            action=action,
            source_locator=locator,
            destination_path=dest_path,
            filename=basename_result,
            image_name=source.image_name,
            size=0,
            asset_id="",
            error=error_msg,
        )

    def _make_success(action, asset_id):
        _fbx_log(f"[FBX][A3.3][SIDECAR_{action.upper()}] guid={guid_short} "
                 f"source=({source.mat_name}:{source.node_name}) "
                 f"filename={filename} asset_id={asset_id} image={source.image_name}")
        try:
            if not os.path.isfile(dest_path):
                return _make_failure(
                    "destination_missing",
                    f"destination_missing_after_{action}:{dest_path}",
                )
            actual_size = os.path.getsize(dest_path)
            final_hex = network._xxh64_file_hex(dest_path)
            if final_hex != asset_id:
                return _make_failure(
                    "destination_hash_mismatch",
                    f"destination_hash_mismatch:{final_hex}!={asset_id}",
                )
        except Exception as e:
            return _make_failure(
                "destination_stat_failed",
                f"destination_stat_failed:{e}",
            )
        return SidecarPreparationResult(
            source=source,
            status="ready",
            action=action,
            source_locator=locator,
            destination_path=dest_path,
            filename=basename_result,
            image_name=source.image_name,
            size=actual_size,
            asset_id=asset_id,
        )

    temp_path = None

    try:
        # === Phase 1: Content hash ===
        content_hex = None
        source_file_path = None

        if source.source_kind == 'FILE' and not source.is_packed:
            if not os.path.isfile(abs_path):
                return _make_failure("file_not_found", f"source_file_not_found:{abs_path}")
            content_hex = network._xxh64_file_hex(abs_path)
            if not content_hex:
                return _make_failure("source_hash_failed", f"xxh64_failed:{abs_path}")
            source_file_path = abs_path

        elif source.is_packed or source.source_kind == 'GENERATED':
            img = bpy.data.images.get(source.image_name)
            if img is None:
                return _make_failure("image_not_found", "blender_image_not_found")
            with _tempfile.NamedTemporaryFile(suffix=ext, delete=False, dir=dest_dir) as tf:
                temp_path = tf.name
            try:
                img.save_render(temp_path)
            except Exception:
                if os.path.isfile(temp_path):
                    os.unlink(temp_path)
                    temp_path = None
                raise
            content_hex = network._xxh64_file_hex(temp_path)
            if not content_hex:
                if os.path.isfile(temp_path):
                    os.unlink(temp_path)
                    temp_path = None
                return _make_failure("rendered_hash_failed", f"xxh64_failed:{temp_path}")

        else:
            return _make_failure("unsupported_source", f"unsupported_source_kind:{source.source_kind}")

        # === Phase 2: Filename from content hash ===
        display_prefix = source.image_name
        filename, sidecar_key, _ = network.make_sidecar_key(
            display_prefix, content_hex, ext, dest_dir,
        )
        dest_path = os.path.join(dest_dir, filename)
        basename_result = os.path.basename(dest_path)

        # === Phase 3: Verify existing destination ===
        if os.path.isfile(dest_path):
            existing_hex = network._xxh64_file_hex(dest_path)
            if existing_hex == content_hex:
                return _make_success("verified", content_hex)
            return _make_failure(
                "content_collision",
                f"content_collision:existing={existing_hex} new={content_hex}",
            )

        # === Phase 4: Safety check ===
        is_safe, reason = _check_destination_safe(dest_dir, dest_path)
        if not is_safe:
            return _make_failure(f"unsafe:{reason}", f"destination_unsafe:{reason}")

        # === Phase 5: Write via temp + atomic replace ===
        if source.source_kind == 'FILE' and not source.is_packed:
            with _tempfile.NamedTemporaryFile(suffix=ext, delete=False, dir=dest_dir) as tf:
                copy_temp = tf.name
            try:
                shutil.copy2(source_file_path, copy_temp)
                temp_hex = network._xxh64_file_hex(copy_temp)
                if temp_hex != content_hex:
                    os.unlink(copy_temp)
                    return _make_failure("temporary_hash_mismatch",
                        f"temp_hash:{temp_hex}!=expected:{content_hex}")
                os.replace(copy_temp, dest_path)
            except Exception:
                if os.path.isfile(copy_temp):
                    os.unlink(copy_temp)
                raise
            action_str = "copied"

        elif source.is_packed or source.source_kind == 'GENERATED':
            temp_hex = network._xxh64_file_hex(temp_path)
            if temp_hex != content_hex:
                if os.path.isfile(temp_path):
                    os.unlink(temp_path)
                    temp_path = None
                return _make_failure("temporary_hash_mismatch",
                    f"temp_hash:{temp_hex}!=expected:{content_hex}")
            os.replace(temp_path, dest_path)
            temp_path = None
            action_str = "exported"

        else:
            return _make_failure("unsupported_source", f"unsupported_source_kind:{source.source_kind}")

        return _make_success(action_str, content_hex)

    except Exception as e:
        return _make_failure("exception", str(e))

    finally:
        if temp_path is not None and os.path.isfile(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass


def _result_by_source(results):
    """Build a dict mapping source identity -> SidecarPreparationResult.

    Uses object identity (not name) so that one source shared by
    multiple usages resolves to exactly one result.
    """
    return {id(r.source): r for r in results}


def _sidecar_result_to_manifest_entry(result):
    """Convert one SidecarPreparationResult to a manifest-sidecar dict.

    This is the single compatibility/serialization boundary.
    """
    return {
        "filename": result.filename,
        "path": result.destination_path,
        "size": result.size,
        "source": result.source_locator,
    }


def _should_suppress_material(usages, result_by_source):
    """Return True if any usage has a failed or missing source.

    Every usage with:
    - no result mapping;
    - a failed result;
    - a malformed successful result
    suppresses PT_Material.
    It must never be silently skipped.

    Checks the result object (authoritative) rather than source.status.
    """
    for u in usages:
        r = result_by_source.get(id(u.source))
        if r is None:
            return True  # no result mapping
        if r.status != "ready":
            return True  # failed or malformed result
    return False


class UELIVESYNC_OT_sync_selected_mesh_to_ue_fbx(
    bpy.types.Operator
):
    bl_idname = \
        "uelivesync.sync_selected_mesh_to_ue_fbx"

    bl_label = "Sync Selected Mesh to UE (FBX)"

    bl_description = \
        "Export selected MESH objects to FBX cache and " \
        "send PT_FBXImportRequest to UE for StaticMesh import"

    def _collect_mesh_objects(self, context):
        selected = [
            obj for obj in context.selected_objects
            if obj.type == 'MESH'
        ]
        if selected:
            return selected
        active = context.view_layer.objects.active
        if active and active.type == 'MESH':
            return [active]
        ctx_obj = context.object
        if ctx_obj and ctx_obj.type == 'MESH':
            return [ctx_obj]
        return []

    def execute(self, context):

        mode = context.mode if context.mode else "UNKNOWN"
        sel_names = [o.name for o in context.selected_objects]
        active_obj = context.view_layer.objects.active
        active_desc = f"{active_obj.name}/{active_obj.type}" if active_obj else "None"
        print(f"[FBX][OBJECT_SELECTION] selected={len(sel_names)} "
              f"selected_mesh={sum(1 for o in context.selected_objects if o.type == 'MESH')} "
              f"active={active_desc} mode={mode}")

        selected = self._collect_mesh_objects(context)

        if not selected:
            sel_detail = ", ".join(
                f"{o.name}:{o.type}" for o in context.selected_objects
            ) if context.selected_objects else "(empty)"
            self.report(
                {'WARNING'},
                f"No mesh objects could be FBX-synced. "
                f"Selected: [{sel_detail}], "
                f"active={active_desc}, mode={mode}. "
                f"Select a mesh object in Object Mode and retry."
            )
            return {'CANCELLED'}

        for obj in selected:
            mat_slots = len(obj.material_slots) if hasattr(obj, "material_slots") else 0
            print(f"[FBX][OBJECT_SELECTED] name={obj.name} type=MESH materialSlots={mat_slots}")

        if not network.is_connected():
            self.report(
                {'WARNING'},
                "Not connected to UE"
            )
            return {'CANCELLED'}

        import uuid
        import json
        import time
        import math
        import struct
        import glob as _glob
        import re as _re

        # Phase 10J.5J: monotonic trace sequence for manual sync
        seq = 0
        with network._seq_lock:
            network._sequence_id += 1
            seq = network._sequence_id

        fbx_cache_root = \
            os.path.expanduser(
                "~/.cache/uelivesync/fbx"
            )

        synced_count = 0
        # Phase 10J.5J: collect material data to send alongside FBX
        mat_payloads_to_send = []
        # Phase 10K.1A: total texture map records sent (for user notice)
        total_mtex_records = 0

        for obj in selected:

            try:
                guid_hex = sync.ensure_guid(obj)
                guid_obj = uuid.UUID(guid_hex)

                safe_name = \
                    "".join(
                        c for c in obj.name
                        if c.isalnum() or c in "._- "
                    ).strip().replace(" ", "_")

                if not safe_name:
                    safe_name = "unnamed"

                obj_dir = os.path.join(
                    fbx_cache_root,
                    guid_hex,
                )
                os.makedirs(obj_dir, exist_ok=True)

                fbx_path = os.path.join(
                    obj_dir,
                    f"{safe_name}.fbx",
                )

                # Phase 10J.5I+5J: flush edit mode changes before evaluation
                # obj.update_from_editmode() + view_layer.update() ensures
                # vertex position changes propagate to evaluated depsgraph.
                _flushed = 0
                if obj.type == 'MESH' and obj.data is not None:
                    try:
                        obj.update_from_editmode()
                        _flushed |= 0x1
                    except Exception:
                        pass
                try:
                    context.view_layer.update()
                    _flushed |= 0x2
                except Exception:
                    pass
                print(f"[BLENDER][EDIT_FLUSH] guid={guid_hex[:8]} "
                      f"object={obj.name} update_from_editmode={bool(_flushed & 0x1)} "
                      f"view_layer_update={bool(_flushed & 0x2)}")

                # Phase 10J.5J: log sync request trace
                print(f"[SYNC][REQ] seq={seq} guid={guid_hex[:8]} "
                      f"name={obj.name} mode=FBX reason=manual_sync")

                # Export with local-pivot helper (Phase 10J)
                depsgraph = context.evaluated_depsgraph_get()
                if not _export_object_local_fbx(obj, fbx_path, depsgraph):
                    print(f"[FBX] Export failed — {fbx_path}")
                    continue

                if not os.path.isfile(fbx_path):
                    print(
                        f"[FBX] Export failed — {fbx_path} "
                        "not created"
                    )
                    continue

                # --- Task D: log source texture state BEFORE sidecar ---
                source_stats_before = []
                if obj.material_slots:
                    for slot in obj.material_slots:
                        mat = slot.material
                        if not mat or not mat.use_nodes or not mat.node_tree:
                            continue
                        for node in mat.node_tree.nodes:
                            if node.type != 'TEX_IMAGE' or not node.image:
                                continue
                            img = node.image
                            src_path = getattr(img, "filepath_raw", "") or getattr(img, "filepath", "")
                            if src_path and img.source == 'FILE':
                                abs_src = bpy.path.abspath(src_path)
                                try:
                                    st = os.stat(abs_src)
                                    source_stats_before.append((abs_src, st.st_size, st.st_mtime))
                                except Exception:
                                    pass
                for p, sz, mt in source_stats_before:
                    _fbx_log(f"[FBX][TEXTURE_SOURCE_STAT_BEFORE] path={p} size={sz} mtime={mt:.6f}")

                # Phase 7H.6 + A3.2: structured sidecar preparation
                _a3_sources, _a3_usages = _extract_texture_usages_and_sources(obj)
                _a3_collision_registry = {}
                _a3_results = [
                    _prepare_source_sidecar(_src, obj_dir, _a3_collision_registry, guid_hex[:8])
                    for _src in _a3_sources
                ]
                sidecar_copied = sum(1 for r in _a3_results if r.status == "ready")
                sidecar_info = [
                    _sidecar_result_to_manifest_entry(r)
                    for r in _a3_results
                    if r.status == "ready"
                ]
                # Suppress PT_Material if any connected usage has a failed source
                _results_by_source = _result_by_source(_a3_results)
                _suppress_material = _should_suppress_material(_a3_usages, _results_by_source)
                if _suppress_material:
                    _fbx_log(f"[FBX][A3.1][MATERIAL_SUPPRESS] guid={guid_hex[:8]} "
                             f"object={obj.name} reason=connected_texture_failed")
                print(f"[FBX][SIDECAR_READY] guid={guid_hex[:8]} copied={sidecar_copied}")

                # --- Task D: log source texture state AFTER sidecar ---
                source_modified = False
                for abs_src, before_sz, _mt in source_stats_before:
                    try:
                        st = os.stat(abs_src)
                        after_sz = st.st_size
                        changed = 1 if (after_sz != before_sz) else 0
                        _fbx_log(f"[FBX][TEXTURE_SOURCE_STAT_AFTER] path={abs_src} size={after_sz} mtime={st.st_mtime:.6f} changed={changed}")
                        if changed:
                            _fbx_log(f"[FBX][TEXTURE_SOURCE_MODIFIED_ERROR] path={abs_src} before_size={before_sz} after_size={after_sz}")
                            source_modified = True
                    except Exception:
                        _fbx_log(f"[FBX][TEXTURE_SOURCE_STAT_AFTER] path={abs_src} size=stat_failed changed=1")
                        source_modified = True

                if source_modified:
                    _fbx_log(f"[FBX][SYNC_ABORT] reason=source_texture_modified guid={guid_hex[:8]} object={obj.name}")
                    continue

                # Phase 7H.6: diagnostics after successful FBX export
                cache_files = _glob.glob(os.path.join(obj_dir, "*"))
                _fbx_log(f"[FBX][CACHE_FOLDER_LIST] folder={obj_dir} "
                         f"files=[{', '.join(os.path.basename(f) for f in cache_files)}]")
                try:
                    with open(fbx_path, "rb") as _f:
                        _fbx_data = _f.read()
                    _fbx_text = _fbx_data.decode("latin-1")
                    _tex_pattern = _re.compile(
                        r'[a-zA-Z0-9_\-\.]+\.(png|jpg|jpeg|tga|exr|bmp|tif)',
                        _re.IGNORECASE)
                    _texture_refs = _tex_pattern.findall(_fbx_text)
                    if _texture_refs:
                        _fbx_log(f"[FBX][TEXTURE_REF_CHECK] guid={guid_hex[:8]} "
                                 f"found_in_fbx=1 refs={_texture_refs}")
                    else:
                        _fbx_log(f"[FBX][TEXTURE_REF_CHECK] guid={guid_hex[:8]} found_in_fbx=0")
                except Exception as _fbx_read_err:
                    _fbx_log(f"[FBX][TEXTURE_REF_CHECK] guid={guid_hex[:8]} "
                             f"error={_fbx_read_err}")

                # Compute mesh stats from evaluated mesh
                # (depsgraph already obtained above)
                evaluated_obj = \
                    obj.evaluated_get(depsgraph)

                mesh = evaluated_obj.to_mesh()
                if mesh is None:
                    print(
                        f"[FBX] Cannot evaluate mesh for stats: {obj.name}"
                    )
                    continue

                try:
                    mesh.calc_loop_triangles()
                    vert_count = len(mesh.vertices)
                    tri_count = len(mesh.loop_triangles)
                    mat_slot_count = \
                        len(mesh.materials)
                    geometry_hash = \
                        network.compute_fbx_geometry_hash(mesh)
                    if geometry_hash == 0:
                        geometry_hash = network.xxh64(
                            struct.pack(
                                '<II',
                                vert_count,
                                tri_count,
                            )
                        )
                finally:
                    evaluated_obj.to_mesh_clear()

                # Write manifest JSON (sidecar info attached inline)
                manifest = {
                    "object_guid": guid_hex,
                    "object_name": obj.name,
                    "safe_name": safe_name,
                    "fbx_path": fbx_path,
                    "vert_count": vert_count,
                    "tri_count": tri_count,
                    "mat_slot_count": mat_slot_count,
                    "timestamp": time.time(),
                    "source": "Blender FBX export",
                    "sidecar_textures": sidecar_info,
                }

                manifest_path = os.path.join(
                    obj_dir,
                    f"{safe_name}.manifest.json",
                )
                with open(
                    manifest_path, "w"
                ) as f:
                    json.dump(
                        manifest, f, indent=2
                    )

                # Task A: deterministic ordering log — manifest written before packet send
                print(f"[FBX][MANIFEST_WRITE] guid={guid_hex[:8]} path={manifest_path} sidecarTextures={len(manifest['sidecar_textures'])}")

                # Phase 10J.5L: compute mesh bounds (both meters and expected cm)
                bounds_min_m = (0.0, 0.0, 0.0)
                bounds_max_m = (0.0, 0.0, 0.0)
                try:
                    verts = mesh.vertices
                    if len(verts) > 0:
                        xs = [v.co.x for v in verts]
                        ys = [v.co.y for v in verts]
                        zs = [v.co.z for v in verts]
                        bounds_min_m = (min(xs), min(ys), min(zs))
                        bounds_max_m = (max(xs), max(ys), max(zs))
                except Exception:
                    pass

                # cm bounds = meter bounds * 100 (via FBX_SCALE_UNITS conversion)
                bounds_min_cm = (bounds_min_m[0] * 100.0, bounds_min_m[1] * 100.0, bounds_min_m[2] * 100.0)
                bounds_max_cm = (bounds_max_m[0] * 100.0, bounds_max_m[1] * 100.0, bounds_max_m[2] * 100.0)

                _fbx_log(f"[FBX][UNIT_BAKE] guid={guid_hex[:8]} scale=1.0 "
                         f"source=fbx_scale_units")
                _fbx_log(f"[FBX][EXPORT] seq={seq} guid={guid_hex[:8]} "
                         f"path={fbx_path} geomHash=0x{geometry_hash:x} "
                         f"verts={vert_count} tris={tri_count} mats={mat_slot_count} "
                         f"bounds_m=({bounds_min_m[0]:.3f},{bounds_min_m[1]:.3f},{bounds_min_m[2]:.3f})-({bounds_max_m[0]:.3f},{bounds_max_m[1]:.3f},{bounds_max_m[2]:.3f}) "
                         f"bounds_cm=({bounds_min_cm[0]:.3f},{bounds_min_cm[1]:.3f},{bounds_min_cm[2]:.3f})-({bounds_max_cm[0]:.3f},{bounds_max_cm[1]:.3f},{bounds_max_cm[2]:.3f}) "
                         f"global_scale=1.0 convert_units=1")

                # Phase 10J.5L: update auto-sync geometry version to prevent PT_Mesh emission
                # for this GUID in the same sync cycle. The FBX export is authoritative.
                try:
                    mesh_data_for_hash = network.extract_evaluated_mesh_data(obj)
                    if mesh_data_for_hash is not None:
                        auto_sync_hash = network.compute_geometry_version_hash(
                            mesh_data_for_hash["vertices"],
                            mesh_data_for_hash["triangles"],
                            mesh_data_for_hash["material_indices"],
                        )
                        sync._last_geometry_version[guid_hex] = auto_sync_hash
                        print(f"[FBX][AUTO_SYNC_BLOCK] guid={guid_hex[:8]} "
                              f"autoSyncHash=0x{auto_sync_hash} reason=fbx_authoritative")
                except Exception as _hash_exc:
                    print(f"[FBX][AUTO_SYNC_BLOCK] guid={guid_hex[:8]} "
                          f"failed_to_compute_hash: {_hash_exc}")

                # Phase 10J.5J: log geometry decision
                prev_geom = sync._last_geometry_version.get(guid_hex)
                send_fbx = 1
                if prev_geom is not None and geometry_hash != 0:
                    if prev_geom == geometry_hash:
                        send_fbx = 0
                print(f"[SYNC][DECIDE] seq={seq} guid={guid_hex[:8]} "
                      f"sendFBX={send_fbx} reason={'geometry_changed' if geometry_hash != prev_geom else 'unchanged'} "
                      f"oldGeomHash={prev_geom or 0} newGeomHash={geometry_hash}")

                # Build and send FBX import request packet
                payload = \
                    network.serialize_fbx_import_request(
                        guid_obj=guid_obj,
                        fbx_path=fbx_path,
                        object_name=safe_name,
                        vert_count=vert_count,
                        tri_count=tri_count,
                        mat_slot_count=mat_slot_count,
                        timestamp=time.time(),
                        geometry_hash=geometry_hash,
                    )

                # Task A: deterministic send-ready log after all sidecar/manifest steps
                print(f"[FBX][SEND_READY] guid={guid_hex[:8]} fbx={fbx_path} sidecarTextures={len(manifest['sidecar_textures'])}")

                network.send_objects(
                    [payload],
                    packet_type=network.PT_FBXImportRequest,
                    version=network.LIVE_SYNC_VERSION_V5,
                )

                print(
                    f"[FBX] Synced: {obj.name} → {fbx_path} "
                    f"({tri_count} tri, "
                    f"{vert_count} vert)"
                )

                # Phase 10J.5J: material property dirty detection for Sync FBX
                # Collect current material property signature
                current_prop_sig = None
                try:
                    current_prop_sig = {}
                    for slot_idx, slot in enumerate(obj.material_slots):
                        if slot and slot.material:
                            p = network.get_material_basic_properties(slot.material)
                            if p is not None:
                                current_prop_sig[slot_idx] = (
                                    p.get("BaseColorR", 0.0),
                                    p.get("BaseColorG", 0.0),
                                    p.get("BaseColorB", 0.0),
                                    p.get("Alpha", 1.0),
                                    p.get("Roughness", 0.5),
                                    p.get("Metallic", 0.0),
                                )
                except Exception:
                    current_prop_sig = None

                if current_prop_sig is not None:
                    prev_prop_sig = sync._last_material_property_sig.get(guid_hex)
                    # Phase 7H: include texture hash in dirty detection
                    # Derived from structured results (authoritative), not source.status.
                    current_tex_sigs = {}
                    if not _suppress_material:
                        for _si in range(len(obj.material_slots)):
                            _slot_usages = [u for u in _a3_usages if u.slot_index == _si]
                            if not _slot_usages:
                                continue
                            _a3_maps = []
                            for _u in _slot_usages:
                                _r = _results_by_source.get(id(_u.source))
                                if _r is None or _r.status != "ready":
                                    continue
                                _fp = _r.destination_path
                                _nm = os.path.splitext(_r.filename)[0]
                                _a3_maps.append((_u.channel, _fp, _nm, _u.flags))
                            if _a3_maps:
                                _a3_tex_hash = network.compute_material_texture_hash(_si, _a3_maps)
                                current_tex_sigs[_si] = _a3_tex_hash

                    # Compute dirty hashes for logging
                    scalar_hash_val, tex_hash_val, combined_hash_val = (
                        network.compute_material_dirty_sig(current_prop_sig, current_tex_sigs)
                    )
                    print(f"[MATERIAL][DIRTY_HASH] guid={guid_hex[:8]} "
                          f"scalarHash={scalar_hash_val} textureHash={tex_hash_val} "
                          f"combinedHash={combined_hash_val}")

                    scalar_changed = True
                    if prev_prop_sig is not None:
                        _scalar_len = len(next(iter(current_prop_sig.values())))
                        prev_scalar = {si: vals[:_scalar_len] for si, vals in prev_prop_sig.items()}
                        scalar_changed = current_prop_sig != prev_scalar
                    tex_changed = False
                    if prev_prop_sig is not None and len(prev_prop_sig) == len(current_prop_sig):
                        prev_tex_sigs = {}
                        for si in prev_prop_sig:
                            prev_tex = prev_prop_sig[si][6:] if len(prev_prop_sig[si]) > 6 else ()
                            if si in current_tex_sigs or any(v != 0 for v in prev_tex):
                                prev_tex_sigs[si] = prev_tex
                        tex_changed = (current_tex_sigs != prev_tex_sigs)

                    # Phase 7H: log signature comparison outcome for diagnostics
                    # Only log when something changed or cache missing (suppress noise on unchanged ticks)
                    if scalar_changed or tex_changed or prev_prop_sig is None:
                        print(f"[MATERIAL][SIG_COMPARE] guid={guid_hex[:8]} "
                              f"prevExists={int(prev_prop_sig is not None)} "
                              f"scalarChanged={int(scalar_changed)} "
                              f"texChanged={int(tex_changed)}")

                    # Phase 7H: always extract tex_maps + mat_props so we can send
                    # even when only texture changed (scalars unchanged).
                    mat_props = {}
                    for slot_idx, slot in enumerate(obj.material_slots):
                        if slot and slot.material:
                            p = network.get_material_basic_properties(slot.material)
                            if p is not None:
                                mat_props[slot_idx] = p

                    tex_maps = None
                    try:
                        tex_maps_dict = {}
                        if not _suppress_material:
                            for _u in _a3_usages:
                                _r = _results_by_source.get(id(_u.source))
                                if _r is None or _r.status != "ready":
                                    continue
                                _fp = _r.destination_path
                                _nm = os.path.splitext(_r.filename)[0]
                                if _u.slot_index not in tex_maps_dict:
                                    tex_maps_dict[_u.slot_index] = []
                                tex_maps_dict[_u.slot_index].append((_u.channel, _fp, _nm, _u.flags))
                                _ch_name = {1: "BaseColor", 2: "Roughness", 3: "Metallic",
                                            4: "Alpha", 5: "Normal"}.get(_u.channel, "Unknown")
                                _fe = "1" if os.path.isfile(_fp) else "0"
                                _src_kind = "PACKED" if _r.source.is_packed else "FILE"
                                print(f"[MATERIAL][TEXTURE_CHANNEL_SCAN] "
                                      f"object={obj.name} slot={_u.slot_index} "
                                      f"material={_r.source.mat_name} channel={_ch_name} "
                                      f"hasTexture=1 image={_nm} path={_fp[:200]} "
                                      f"exists={_fe} "
                                      f"source={_src_kind}")
                            if tex_maps_dict:
                                tex_maps = tex_maps_dict
                    except Exception:
                        tex_maps = None

                    # Phase 7H: decide whether to send based on scalar OR texture change
                    if scalar_changed or tex_changed or send_fbx == 1:
                        if send_fbx == 1:
                            reason = "fbx_full_material_snapshot"
                        else:
                            reason = "texture_changed" if (not scalar_changed and tex_changed) else "property_changed"
                        if send_fbx == 1:
                            print(f"[MATERIAL][FBX_FULL_SNAPSHOT] guid={guid_hex[:8]} slots={len(current_prop_sig)} reason=manual_fbx_sync")
                        print(f"[MATERIAL][DIRTY_DECIDE] guid={guid_hex[:8]} "
                              f"property_changed={int(scalar_changed or tex_changed)} reason={reason} "
                              f"slots={list(current_prop_sig.keys())}")
                        print(f"[SYNC][DECIDE] seq={seq} guid={guid_hex[:8]} "
                              f"sendMAT=1 reason={reason} "
                              f"newMatSig={list(current_prop_sig.keys())}")

                        # Log per-slot MATX value/texture send before packet dispatch
                        if tex_maps:
                            for slot_idx, slot_maps in tex_maps.items():
                                for ch, fpath, img_name, flags in slot_maps:
                                    ch_name = {1: "BaseColor", 2: "Roughness", 3: "Metallic", 4: "Alpha", 5: "Normal"}.get(ch, "Unknown")
                                    abs_path = bpy.path.abspath(fpath) if fpath else ""
                                    file_exists = "1" if (abs_path and os.path.isfile(abs_path)) else "0"
                                    print(f"[MATERIAL][MATX_TEXTURE_SEND] guid={guid_hex[:8]} slot={slot_idx} channel={ch_name} path={abs_path[:200] if abs_path else ''} exists={file_exists}")
                            for slot_maps in tex_maps.values():
                                total_mtex_records += len(slot_maps)

                        # Build material packet payload
                        try:
                            # Task 7B: compute real material identity hashes for PT_Material payload
                            fbx_identity_now = {}
                            for slot_idx, slot in enumerate(obj.material_slots):
                                if slot and slot.material:
                                    low, high = network.get_material_identity_hash(slot.material)
                                else:
                                    low, high = (0, 0)
                                fbx_identity_now[slot_idx] = (low, high)

                            mat_payload = network.serialize_material_slots(
                                guid_obj,
                                fbx_identity_now,
                                mat_props,
                                tex_maps
                            )
                            mat_payloads_to_send.append(mat_payload)
                            if send_fbx == 1:
                                total_tex_records = sum(len(v) for v in (tex_maps or {}).values())
                                print(f"[MATERIAL][FBX_FULL_SNAPSHOT_SENT] guid={guid_hex[:8]} slots={len(current_prop_sig)} textureRecords={total_tex_records}")
                            for si in mat_props:
                                pp = mat_props[si]
                                print(f"[MAT][SEND] seq={seq} guid={guid_hex[:8]} "
                                      f"slot={si} matx=1 propertySig=1 "
                                      f"color=({pp.get('BaseColorR',0):.3f},{pp.get('BaseColorG',0):.3f},{pp.get('BaseColorB',0):.3f},{pp.get('Alpha',1):.3f}) "
                                      f"roughness={pp.get('Roughness',0.5):.3f} "
                                      f"metallic={pp.get('Metallic',0):.3f} "
                                      f"alpha={pp.get('Alpha',1):.3f}")
                                print(f"[MATERIAL][MATX_VALUE_SEND] guid={guid_hex[:8]} slot={si} channel=BaseColor value=({pp.get('BaseColorR',0):.3f},{pp.get('BaseColorG',0):.3f},{pp.get('BaseColorB',0):.3f},{pp.get('Alpha',1):.3f})")
                                print(f"[MATERIAL][MATX_VALUE_SEND] guid={guid_hex[:8]} slot={si} channel=Roughness value={pp.get('Roughness',0.5):.3f}")
                                print(f"[MATERIAL][MATX_VALUE_SEND] guid={guid_hex[:8]} slot={si} channel=Metallic value={pp.get('Metallic',0):.3f}")
                                print(f"[MATERIAL][MATX_VALUE_SEND] guid={guid_hex[:8]} slot={si} channel=Alpha value={pp.get('Alpha',1):.3f}")
                                network._append_blender_debug_log(
                                    f"[MAT][SEND] guid={guid_hex[:8]} "
                                    f"slot={si} matx=1 "
                                    f"color=({pp.get('BaseColorR',0):.3f},{pp.get('BaseColorG',0):.3f},{pp.get('BaseColorB',0):.3f},{pp.get('Alpha',1):.3f}) "
                                    f"roughness={pp.get('Roughness',0.5):.3f} "
                                    f"metallic={pp.get('Metallic',0):.3f}"
                                )
                        except Exception as _mat_exc:
                            print(f"[MATERIAL][PACKET_BUILD_ERROR] guid={guid_hex[:8]} object={obj.name} error={_mat_exc}")
                            print(f"[MAT][ERROR] failed to build material payload for {obj.name}: {_mat_exc}")

                        # Phase 7H: update texture-aware property sig
                        merged_sig = {}
                        for si in current_prop_sig:
                            prop_tuple = current_prop_sig[si]
                            tex_tuple = current_tex_sigs.get(si, (0, 0))
                            merged_sig[si] = prop_tuple + tuple(tex_tuple)
                        sync._last_material_property_sig[guid_hex] = merged_sig
                        print(f"[MATERIAL][SIG_CACHE_UPDATE] guid={guid_hex} slots={len(merged_sig)} scalarHash={scalar_hash_val} textureHash={tex_hash_val} combinedHash={combined_hash_val} reason={reason}")
                        sync._last_material_sent_reason[guid_hex] = reason
                    else:
                        print(f"[MATERIAL][DIRTY_DECIDE] guid={guid_hex[:8]} "
                              f"property_changed=False reason=property_unchanged "
                              f"slots={list(current_prop_sig.keys())}")
                        print(f"[SYNC][DECIDE] seq={seq} guid={guid_hex[:8]} "
                              f"sendMAT=0 reason=property_unchanged")
                        # Store merged sig (not scalar-only) to preserve texture info for next tick
                        merged_sig = {}
                        for si in current_prop_sig:
                            prop_tuple = current_prop_sig[si]
                            tex_tuple = current_tex_sigs.get(si, (0, 0))
                            merged_sig[si] = prop_tuple + tuple(tex_tuple)
                        sync._last_material_property_sig[guid_hex] = merged_sig
                        _prev_reason = sync._last_material_sent_reason.pop(guid_hex, None)
                        if _prev_reason is not None:
                            print(f"[MATERIAL][SIG_CACHE_HIT] guid={guid_hex[:8]} reason=property_unchanged")

                if send_fbx == 1 and current_prop_sig is None:
                    print(f"[MATERIAL][FBX_FULL_SNAPSHOT] guid={guid_hex[:8]} slots=0 reason=manual_fbx_sync skipped_no_material_data")

                _audit_mat_slots = len(current_prop_sig) if current_prop_sig is not None else 0
                _audit_cached = int(sync._last_material_property_sig.get(guid_hex) is not None)
                print(f"[MATERIAL][FBX_SNAPSHOT_AUDIT] guid={guid_hex[:8]} sendFBX=1 cachedMaterial={_audit_cached} slots={_audit_mat_slots}")
                if send_fbx == 1:
                    try:
                        # Task 7B: reuse fbx_identity_now if already computed, else compute now
                        if 'fbx_identity_now' not in dir():
                            fbx_identity_now = {}
                            for slot_idx, slot in enumerate(obj.material_slots):
                                if slot and slot.material:
                                    low, high = network.get_material_identity_hash(slot.material)
                                else:
                                    low, high = (0, 0)
                                fbx_identity_now[slot_idx] = (low, high)
                        sync._last_material_identity[guid_hex] = fbx_identity_now
                    except Exception:
                        pass
                synced_count += 1

            except Exception as e:
                print(
                    f"[FBX] ERROR: {obj.name} — {e}"
                )

        # Phase 10J.5J: send PT_Material alongside Sync FBX
        # when material property signature changed for selected objects.
        if mat_payloads_to_send:
            print(f"[MAT][SEND] seq={seq} sending {len(mat_payloads_to_send)} "
                  f"material packet(s) alongside FBX sync")
            # Phase 10J.5L: log to Blender debug file
            network._append_blender_debug_log(
                f"[MAT][SEND] manual_fbx count={len(mat_payloads_to_send)} seq={seq}"
            )
            for payload in mat_payloads_to_send:
                network.send_objects(
                    [payload],
                    packet_type=network.PT_Material,
                    version=network.LIVE_SYNC_VERSION_V5,
                )

        # Phase 10K.1A: user notice when texture maps are detected
        if total_mtex_records > 0:
            notice = (
                f"MTEX: {total_mtex_records} texture map reference(s) sent. "
                f"UE will log/cache them only; texture import/application "
                f"comes in a later phase."
            )
            print(f"[MTEX][USER_NOTICE] records={total_mtex_records} "
                  f"limitation=metadata_only")
            network._append_blender_debug_log(
                f"[MTEX][USER_NOTICE] records={total_mtex_records} "
                f"limitation=metadata_only"
            )
            self.report({'INFO'}, notice)

        if synced_count > 0:
            self.report(
                {'INFO'},
                f"FBX synced {synced_count} mesh object(s) to UE"
            )
        elif len(selected) > 0:
            self.report(
                {'WARNING'},
                f"FBX sync failed for {len(selected)} mesh object(s); "
                "see console/log for [FBX] ERROR"
            )
        else:
            self.report(
                {'WARNING'},
                "No mesh objects could be FBX-synced"
            )

        return {'FINISHED'}


# =========================================================
# PHASE 7H / 7G.5: SYNC ACTIVE CAMERA TO UE
# =========================================================

class UELIVESYNC_OT_sync_active_camera_to_ue(
    bpy.types.Operator
):

    bl_idname = \
        "uelivesync.sync_active_camera_to_ue"

    bl_label = "Sync Active Camera to UE"

    bl_description = \
        "Update camera actor in UE via PT_Transform + " \
        "PT_CameraDef only (no actor spawn, no viewport switch)"

    def execute(self, context):

        import time
        from uuid import UUID

        # --- Find the camera ---
        camera_obj = getattr(
            context.scene, "camera", None
        )

        if camera_obj is None:
            # Fallback: selected active object if it is a camera
            active = getattr(
                context, "active_object", None
            )
            if active is not None and \
               active.type == 'CAMERA':
                camera_obj = active

        if camera_obj is None:
            self.report(
                {'WARNING'},
                "No active camera found: "
                "set scene.camera or select a camera object"
            )
            return {'CANCELLED'}

        # --- Ensure connection ---
        if not sync.is_connected():
            self.report(
                {'WARNING'},
                "Not connected to UE LiveSync server"
            )
            return {'CANCELLED'}

        # --- Ensure GUID ---
        guid_hex = sync.ensure_guid(camera_obj)
        guid_obj = UUID(guid_hex)

        # --- Extract transform ---
        loc = camera_obj.location
        rot = camera_obj.rotation_quaternion
        scl = camera_obj.scale
        transform = {
            "location": (loc.x, loc.y, loc.z),
            "rotation": (rot.x, rot.y, rot.z, rot.w),
            "scale": (scl.x, scl.y, scl.z),
        }
        timestamp = time.time()

        # --- Serialize transform payload ---
        try:
            obj_payload = network.serialize_object_v3(
                guid_obj,
                transform,
                timestamp,
                parent_guid_obj=None,
                primitive_type=network.PRIMITIVE_CAMERA,
            )
        except Exception as e:
            self.report(
                {'ERROR'},
                f"Camera serialization failed: {e}"
            )
            return {'CANCELLED'}

        # --- Serialize camera definition (PT_CameraDef) ---
        cam_data = camera_obj.data
        focal = getattr(cam_data, 'lens', 50.0)
        sensor_width = getattr(
            cam_data, 'sensor_width', 36.0
        )
        sensor_height = getattr(
            cam_data, 'sensor_height', 24.0
        )
        clip_start = getattr(
            cam_data, 'clip_start', 0.1
        )
        clip_end = getattr(
            cam_data, 'clip_end', 1000.0
        )
        is_ortho = getattr(
            cam_data, 'type', 'PERSP'
        ) == 'ORTHO'
        ortho_scale = getattr(
            cam_data, 'ortho_scale', 6.0
        )
        flags = 0
        if is_ortho:
            flags |= network.CAMERA_DEF_FLAG_IS_ORTHO
        flags |= network.CAMERA_DEF_FLAG_HAS_CAMERA_DEF

        try:
            camdef_payload = network.serialize_camera_def(
                guid_obj,
                focal_length_mm=focal,
                sensor_width_mm=sensor_width,
                sensor_height_mm=sensor_height,
                clip_start=clip_start,
                clip_end=clip_end,
                ortho_scale=ortho_scale,
                flags=flags,
            )
        except Exception as e:
            self.report(
                {'ERROR'},
                f"CameraDef serialization failed: {e}"
            )
            return {'CANCELLED'}

        # --- Send packets (no PT_Create — actor spawn is unstable) ---
        try:
            # PT_Transform (0x01, default) for position
            network.send_objects(
                [obj_payload],
            )
            print(
                f"[LiveSync] Sent PT_Transform for "
                f"{camera_obj.name}"
            )
            # PT_CameraDef (0x1B) for lens/sensor/clip
            network.send_objects(
                [camdef_payload],
                packet_type=network.PT_CameraDef,
                version=5,
            )
            print(
                f"[LiveSync] Sent PT_CameraDef for "
                f"{camera_obj.name} (focal={focal:.1f})"
            )
        except Exception as e:
            self.report(
                {'ERROR'},
                f"Failed to send camera packets: {e}"
            )
            return {'CANCELLED'}

        # PT_Create is NOT sent because UE camera actor spawn is
        # currently unstable in editor. Use the auto-detect path
        # (active_camera_sync pref) for actor spawning, or use the
        # experimental debug operator (debug_send_camera_packets).
        # PT_ActiveCamera is NOT sent (viewport switching unsafe).

        self.report(
            {'INFO'},
            f"LiveSync camera def+transform sent to UE "
            f"(spawn disabled for stability): "
            f"{camera_obj.name}"
        )
        print(
            f"[LiveSync][CAMERA] Primary operator sent PT_Transform + "
            f"PT_CameraDef for {camera_obj.name} (guid={guid_obj}). "
            f"No UE camera actor will appear unless one already exists. "
            f"Spawn disabled for stability."
        )
        return {'FINISHED'}


# =========================================================
# PHASE 7H: DEBUG CAMERA PACKET ISOLATION OPERATOR
# =========================================================
# Internal/debug operator for isolating which packet type
# causes the UE freeze. Not registered in the panel UI.
# Call from Blender Python console:
#   bpy.ops.uelivesync.debug_send_camera_packets(
#       send_create=True, send_transform=True,
#       send_cameradef=True)
# =========================================================

class UELIVESYNC_OT_debug_send_camera_packets(
    bpy.types.Operator
):

    bl_idname = \
        "uelivesync.debug_send_camera_packets"

    bl_label = "Debug: Send Camera Packets (Isolation)"

    bl_description = \
        "Send specific camera packet types for freeze isolation"

    send_create: bpy.props.BoolProperty(
        name="Send PT_Create",
        description="Send PT_Create (0x03) to spawn camera actor",
        default=False,
    )

    send_transform: bpy.props.BoolProperty(
        name="Send PT_Transform",
        description="Send PT_Transform (0x01) for position",
        default=False,
    )

    send_cameradef: bpy.props.BoolProperty(
        name="Send PT_CameraDef",
        description="Send PT_CameraDef (0x1B) for lens/sensor/clip",
        default=False,
    )

    def execute(self, context):

        import time
        from uuid import UUID

        # --- Find the camera ---
        camera_obj = getattr(
            context.scene, "camera", None
        )

        if camera_obj is None:
            active = getattr(
                context, "active_object", None
            )
            if active is not None and \
               active.type == 'CAMERA':
                camera_obj = active

        if camera_obj is None:
            self.report(
                {'WARNING'},
                "No active camera found"
            )
            return {'CANCELLED'}

        if not sync.is_connected():
            self.report(
                {'WARNING'},
                "Not connected to UE LiveSync server"
            )
            return {'CANCELLED'}

        guid_hex = sync.ensure_guid(camera_obj)
        guid_obj = UUID(guid_hex)

        loc = camera_obj.location
        rot = camera_obj.rotation_quaternion
        scl = camera_obj.scale
        transform = {
            "location": (loc.x, loc.y, loc.z),
            "rotation": (rot.x, rot.y, rot.z, rot.w),
            "scale": (scl.x, scl.y, scl.z),
        }
        timestamp = time.time()

        sent_packets = []

        # --- PT_Create (0x03) ---
        if self.send_create:
            try:
                obj_payload = network.serialize_object_v3(
                    guid_obj,
                    transform,
                    timestamp,
                    parent_guid_obj=None,
                    primitive_type=network.PRIMITIVE_CAMERA,
                )
                network.send_objects(
                    [obj_payload],
                    packet_type=0x03,
                )
                sent_packets.append("PT_Create")
                print(
                    f"[LiveSync][DEBUG] EXPERIMENTAL: Sent PT_Create for "
                    f"{camera_obj.name} GUID={guid_hex} — "
                    f"if UE freezes, camera actor spawn is the cause"
                )
            except Exception as e:
                self.report(
                    {'ERROR'},
                    f"PT_Create send failed: {e}"
                )
                return {'CANCELLED'}

        # --- PT_Transform (0x01, default) ---
        if self.send_transform and not self.send_create:
            # Rebuild payload if we didn't already create it
            try:
                obj_payload = network.serialize_object_v3(
                    guid_obj,
                    transform,
                    timestamp,
                    parent_guid_obj=None,
                    primitive_type=network.PRIMITIVE_CAMERA,
                )
                network.send_objects(
                    [obj_payload],
                )
                sent_packets.append("PT_Transform")
                print(
                    f"[LiveSync][DEBUG] Sent PT_Transform for "
                    f"{camera_obj.name}"
                )
            except Exception as e:
                self.report(
                    {'ERROR'},
                    f"PT_Transform send failed: {e}"
                )
                return {'CANCELLED'}
        elif self.send_transform:
            # send_create was True, payload already exists
            try:
                network.send_objects(
                    [obj_payload],
                )
                sent_packets.append("PT_Transform")
                print(
                    f"[LiveSync][DEBUG] Sent PT_Transform for "
                    f"{camera_obj.name}"
                )
            except Exception as e:
                self.report(
                    {'ERROR'},
                    f"PT_Transform send failed: {e}"
                )
                return {'CANCELLED'}

        # --- PT_CameraDef (0x1B) ---
        if self.send_cameradef:
            cam_data = camera_obj.data
            focal = getattr(cam_data, 'lens', 50.0)
            sensor_width = getattr(
                cam_data, 'sensor_width', 36.0
            )
            sensor_height = getattr(
                cam_data, 'sensor_height', 24.0
            )
            clip_start = getattr(
                cam_data, 'clip_start', 0.1
            )
            clip_end = getattr(
                cam_data, 'clip_end', 1000.0
            )
            is_ortho = getattr(
                cam_data, 'type', 'PERSP'
            ) == 'ORTHO'
            ortho_scale = getattr(
                cam_data, 'ortho_scale', 6.0
            )
            flags = 0
            if is_ortho:
                flags |= network.CAMERA_DEF_FLAG_IS_ORTHO
            flags |= network.CAMERA_DEF_FLAG_HAS_CAMERA_DEF

            try:
                camdef_payload = network.serialize_camera_def(
                    guid_obj,
                    focal_length_mm=focal,
                    sensor_width_mm=sensor_width,
                    sensor_height_mm=sensor_height,
                    clip_start=clip_start,
                    clip_end=clip_end,
                    ortho_scale=ortho_scale,
                    flags=flags,
                )
                network.send_objects(
                    [camdef_payload],
                    packet_type=network.PT_CameraDef,
                    version=5,
                )
                sent_packets.append("PT_CameraDef")
                print(
                    f"[LiveSync][DEBUG] Sent PT_CameraDef for "
                    f"{camera_obj.name} (focal={focal:.1f})"
                )
            except Exception as e:
                self.report(
                    {'ERROR'},
                    f"PT_CameraDef send failed: {e}"
                )
                return {'CANCELLED'}

        packet_count = len(sent_packets)
        self.report(
            {'INFO'},
            f"Debug: sent {packet_count} packet(s) "
            f"({', '.join(sent_packets)}) for {camera_obj.name} "
            f"GUID={guid_hex}"
        )
        return {'FINISHED'}


# =========================================================
# CONNECTION STATUS PANEL
# =========================================================

class UELIVESYNC_PT_panel(
    bpy.types.Panel
):
    bl_label = "UE Live Sync"
    bl_idname = \
        "UELIVESYNC_PT_panel"

    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'UE Sync'

    def draw(self, context):

        layout = self.layout

        prefs = context.preferences.addons[
            __package__
        ].preferences

        row = layout.row(
            align=True,
        )

        row.operator(
            "uelivesync.start"
        )

        row.operator(
            "uelivesync.stop"
        )

        row.operator(
            "uelivesync.discover_and_connect",
            icon='VIEWZOOM',
        )

        layout.separator()

        # Connection status

        connected = \
            sync.is_connected()

        status_detail = \
            sync.get_status_detail()

        if connected:

            box = layout.box()
            box.label(
                text="Connected",
                icon='CHECKBOX_HLT',
            )

            if status_detail:
                box.label(
                    text=status_detail,
                    icon='INFO',
                )

            box.separator()

            stats = \
                sync.get_runtime_stats()

            tracked = \
                sync.get_tracked_count()

            queue_depth = \
                stats.get(
                    "queue_depth", 0)

            reconnect_count = \
                stats.get(
                    "reconnect_count", 0)

            uptime = \
                sync.get_uptime()

            uptime_min = int(uptime // 60)
            uptime_sec = int(uptime % 60)

            packets_sent = \
                stats.get(
                    "packets_sent", 0)

            bytes_sent = \
                stats.get(
                    "bytes_sent", 0)

            dropped = \
                stats.get(
                    "dropped_packets", 0)

            # --- Core stats ---
            row = box.row()
            row.label(
                text=f"Objects: {tracked}",
                icon='OBJECT_DATA',
            )

            row = box.row()
            row.label(
                text=f"Uptime: {uptime_min}m{uptime_sec:02d}s",
                icon='TIME',
            )

            box.separator()

            # --- Network stats ---
            row = box.row()
            row.label(
                text=f"Queue: {queue_depth}",
                icon='CONSOLE',
            )

            row = box.row()
            row.label(
                text=f"Sent: {packets_sent} pkt / {bytes_sent} B",
                icon='NETWORK_DRIVE',
            )

            row = box.row()
            row.label(
                text=f"Dropped: {dropped}",
                icon='ERROR',
            )

            row = box.row()
            # UI-only diagnostic counter; reconnect behavior lives in network.py.
            row.label(
                text=f"Reconnection count: {reconnect_count}",
                icon='INFO',
            )

        else:

            box = layout.box()
            box.label(
                text="Disconnected",
                icon='ERROR',
            )

            if status_detail:
                box.label(
                    text=status_detail,
                    icon='ERROR',
                )

        # Last error display

        last_error = \
            sync.get_last_error()

        last_error_severity = \
            sync.get_last_error_severity()

        if last_error:

            layout.separator()

            row = layout.row()
            icon = 'ERROR' if \
                last_error_severity == 'CRITICAL' \
                else 'INFO'

            op = row.operator(
                "uelivesync.show_error",
                text="Show Last Error",
                icon=icon,
            )

            op.error_message = \
                f"[{last_error_severity}] {last_error}"

        layout.separator()

        # Diagnostics

        layout.operator(
            "uelivesync.dump_diagnostics",
            icon='CONSOLE',
        )

        layout.operator(
            "uelivesync.discover_server",
            icon='VIEWZOOM',
        )

        # Show "Use Discovered Server" only if results exist
        if any(
            r["success"]
            for r in network.get_discovery_results()
        ):
            layout.operator(
                "uelivesync.use_discovered_server",
                icon='IMPORT',
            )

        layout.operator(
            "uelivesync.rebind_all",
            icon='UV_SYNC_SELECT',
        )

        layout.operator(
            "uelivesync.sync_selected_mesh_to_ue_fbx",
            icon='MESH_DATA',
        )

        layout.operator(
            "uelivesync.sync_active_camera_to_ue",
            icon='CAMERA_DATA',
        )

        layout.separator()

        # Preferences shortcut

        layout.label(
            text="Settings",
            icon='PREFERENCES',
        )

        layout.prop(
            prefs,
            "verbose_logging",
        )


# =========================================================
# REGISTRATION
# =========================================================

classes = (
    UELIVESYNC_AP_preferences,
    UELIVESYNC_OT_show_error,
    UELIVESYNC_OT_start,
    UELIVESYNC_OT_stop,
    UELIVESYNC_OT_rebind_all,
    UELIVESYNC_OT_dump_diagnostics,
    UELIVESYNC_OT_discover_server,
    UELIVESYNC_OT_use_discovered_server,
    UELIVESYNC_OT_discover_and_connect,
    UELIVESYNC_OT_sync_selected_mesh_to_ue,
    UELIVESYNC_OT_sync_selected_mesh_to_ue_fbx,
    UELIVESYNC_OT_sync_active_camera_to_ue,
    UELIVESYNC_OT_debug_send_camera_packets,
    UELIVESYNC_PT_panel,
)


def register():

    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()


# =========================================================
# Phase 7H.5: Material hash helpers readiness check
# =========================================================

try:
    _mat_helper_ready = (
        hasattr(network, "compute_material_texture_hash")
        and hasattr(network, "compute_material_dirty_sig")
    )
    _mat_helper_flags = []
    _mat_helper_flags.append(f"compute_material_texture_hash={1 if hasattr(network, 'compute_material_texture_hash') else 0}")
    _mat_helper_flags.append(f"compute_material_dirty_sig={1 if hasattr(network, 'compute_material_dirty_sig') else 0}")
    if _mat_helper_ready:
        print(f"[MATERIAL][HASH_HELPERS_READY] {' '.join(_mat_helper_flags)}")
    else:
        print(f"[MATERIAL][HASH_HELPERS_MISSING] {' '.join(_mat_helper_flags)} — material texture dirty detection disabled")
except Exception:
    pass
