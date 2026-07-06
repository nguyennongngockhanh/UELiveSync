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


def _copy_textures_sidecar(obj, dest_dir, guid_short="?", fingerprint_map=None, stored_manifest=None):
    """Copy material texture images into dest_dir for UE sidecar import.
    
    Iterates obj material slots, finds TEX_IMAGE nodes, and copies
    referenced images into dest_dir (the FBX cache folder).
    Handles FILE source (direct copy), packed images, and GENERATED images.
    
    When fingerprint_map and stored_manifest are provided, uses per-texture
    fingerprinting to skip unchanged textures (fast path reuse).
    
    Returns (copied_count, sidecar_info_list).
    """
    import shutil
    import uuid as _uuid

    if not obj.material_slots:
        _fbx_log(f"[FBX][TEXTURE_SIDECAR] guid={guid_short} "
                 f"object={obj.name} no_material_slots")
        return 0, []

    # Build canonical_key -> (stored_sidecar_info) lookup from manifest
    stored_sidecar = {}
    stored_fps = {}
    if stored_manifest:
        stored_textures = stored_manifest.get("textures", {})
        for key, entry in stored_textures.items():
            si = entry.get("sidecarInfo")
            if si:
                stored_sidecar[key] = si
            stored_fps[key] = entry

    copied_count = 0
    sidecar_info = []  # list of dicts: {filename, path, source_path, size}
    for slot in obj.material_slots:
        mat = slot.material
        if not mat or not mat.use_nodes or not mat.node_tree:
            continue
        for node in mat.node_tree.nodes:
            if node.type != 'TEX_IMAGE' or not node.image:
                continue
            img = node.image
            filepath = getattr(img, "filepath", "") or ""
            filepath_raw = getattr(img, "filepath_raw", "") or ""
            source = getattr(img, "source", "")
            is_packed = bool(getattr(img, "packed_file", False))

            # Compute canonical key for per-texture fast path
            canonical_key = _get_texture_canonical_key(
                img, filepath_raw or filepath, source, is_packed)

            # Fast path: if fingerprint matches stored, reuse sidecar info
            if fingerprint_map and canonical_key and stored_sidecar:
                fp = fingerprint_map.get(canonical_key)
                sfp = stored_fps.get(canonical_key)
                if fp and sfp and canonical_key in stored_sidecar:
                    if _fingerprint_metadata_matches(fp, sfp):
                        si = stored_sidecar[canonical_key]
                        sidecar_info.append(si)
                        _fbx_log(f"[FBX][TEXTURE_SIDECAR_REUSE] guid={guid_short} "
                                 f"key={canonical_key} reason=metadata_unchanged")
                        continue
                    # Metadata changed — check content hash
                    current_hash = _compute_content_hash_for_fingerprint(fp, img)
                    stored_hash = sfp.get("contentHash")
                    if current_hash and current_hash == stored_hash:
                        si = stored_sidecar[canonical_key]
                        sidecar_info.append(si)
                        _fbx_log(f"[FBX][TEXTURE_SIDECAR_REUSE] guid={guid_short} "
                                 f"key={canonical_key} reason=content_unchanged")
                        continue

            _fbx_log(f"[FBX][TEXTURE_SIDECAR_SCAN] guid={guid_short} "
                     f"object={obj.name} material={mat.name} "
                     f"image={img.name} source={source} "
                     f"packed={1 if is_packed else 0}")

            if source == 'FILE' and not is_packed:
                # --- Task C: non-destructive copy for FILE source ---
                abs_path = bpy.path.abspath(filepath)
                if not os.path.isfile(abs_path):
                    _fbx_log(f"[FBX][TEXTURE_COPY_FAIL] guid={guid_short} "
                             f"object={obj.name} material={mat.name} "
                             f"image={img.name} reason=file_not_found "
                             f"path={abs_path}")
                    continue

                # --- Task A: safety guard — never write to source path ---
                resolved_src = os.path.realpath(abs_path)
                resolved_dst = os.path.realpath(os.path.join(dest_dir, os.path.basename(abs_path)))
                if resolved_dst == resolved_src:
                    _fbx_log(f"[FBX][TEXTURE_SOURCE_WRITE_BLOCKED] path={resolved_dst} reason=destination_equals_source")
                    continue

                dest_name = os.path.basename(abs_path)
                dest_path = os.path.join(dest_dir, dest_name)
                if os.path.exists(dest_path):
                    base, ext = os.path.splitext(dest_name)
                    dest_name = f"{base}_{_uuid.uuid4().hex[:8]}{ext}"
                    dest_path = os.path.join(dest_dir, dest_name)

                # Use shutil.copy2 (never img.save_render) for FILE sources
                try:
                    shutil.copy2(abs_path, dest_path)
                    _fbx_log(f"[FBX][TEXTURE_COPY] guid={guid_short} "
                             f"object={obj.name} material={mat.name} "
                             f"image={img.name} src={abs_path} "
                             f"dst={dest_path}")
                    # Task A: record sidecar info for manifest
                    try:
                        src_size = os.stat(abs_path).st_size
                    except Exception:
                        src_size = 0
                    sidecar_info.append({
                        "filename": os.path.basename(dest_path),
                        "path": dest_path,
                        "size": src_size,
                        "source": abs_path,
                    })
                    _fbx_log(f"[FBX][MANIFEST_SIDECAR_TEXTURE] guid={guid_short} "
                             f"file={os.path.basename(dest_path)} size={src_size}")
                    copied_count += 1
                except Exception as e:
                    _fbx_log(f"[FBX][TEXTURE_COPY_FAIL] guid={guid_short} "
                             f"object={obj.name} material={mat.name} "
                             f"image={img.name} reason=copy_failed "
                             f"error={e}")

            elif is_packed or source == 'GENERATED':
                # --- Task C: temp save must be inside cache folder ---
                ext = ".png"
                try:
                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix=ext, delete=False, dir=dest_dir) as tf:
                        temp_path = tf.name
                    img.save_render(temp_path)
                    _fbx_log(f"[FBX][TEXTURE_TEMP_SAVE] dst={temp_path} source={'PACKED' if is_packed else 'GENERATED'}")
                    # Fix double extension: strip existing extension from img.name
                    base_name = os.path.splitext(img.name)[0]
                    dest_name = f"{base_name}{ext}"
                    dest_path = os.path.join(dest_dir, dest_name)
                    if os.path.exists(dest_path):
                        dest_name = f"{base_name}_{_uuid.uuid4().hex[:8]}{ext}"
                        dest_path = os.path.join(dest_dir, dest_name)
                    shutil.move(temp_path, dest_path)
                    _fbx_log(f"[FBX][TEXTURE_COPY] guid={guid_short} "
                             f"object={obj.name} material={mat.name} "
                             f"image={img.name} "
                             f"source={'packed' if is_packed else 'generated'} "
                             f"dst={dest_path}")
                    # Record sidecar info for manifest (was missing — caused sidecarTextures=0)
                    try:
                        src_size = os.stat(dest_path).st_size
                    except Exception:
                        src_size = 0
                    sidecar_info.append({
                        "filename": os.path.basename(dest_path),
                        "path": dest_path,
                        "size": src_size,
                        "source": temp_path,
                    })
                    _fbx_log(f"[FBX][MANIFEST_SIDECAR_TEXTURE] guid={guid_short} "
                             f"file={os.path.basename(dest_path)} size={src_size}")
                    copied_count += 1
                except Exception as e:
                    _fbx_log(f"[FBX][TEXTURE_COPY_FAIL] guid={guid_short} "
                             f"object={obj.name} material={mat.name} "
                             f"image={img.name} "
                             f"source={'packed' if is_packed else 'generated'} "
                             f"reason=save_failed error={e}")
            else:
                _fbx_log(f"[FBX][TEXTURE_COPY_FAIL] guid={guid_short} "
                         f"object={obj.name} material={mat.name} "
                         f"image={img.name} source={source} "
                         f"reason=unsupported_source")

    _fbx_log(f"[FBX][TEXTURE_SIDECAR_SUMMARY] guid={guid_short} "
             f"object={obj.name} copied={copied_count}")
    return copied_count, sidecar_info


# Phase 9B.6B.3: persistent sidecar state file name
SIDECAR_STATE_FILENAME = "sidecar_state.json"
TEXTURE_MANIFEST_FILENAME = "texture_manifest.json"
TEXTURE_MANIFEST_SCHEMA_VERSION = 2


def _get_texture_canonical_key(img, filepath, source, is_packed):
    """Derive the canonical key (lowercased basename without extension).

    Must match network.py extract_texture_maps_for_slot logic:
    - FILE source: use filepath basename without extension
    - packed/generated: use image.name without extension
    """
    if source == 'FILE' and not is_packed and filepath:
        base = os.path.basename(filepath)
        if base:
            return os.path.splitext(base)[0].lower()
    return os.path.splitext(getattr(img, "name", ""))[0].lower()


def _get_texture_channel_name(node, mat):
    """Determine which material channel a TEX_IMAGE node feeds into."""
    if not mat or not mat.node_tree:
        return "Unknown"
    for n in mat.node_tree.nodes:
        if n.type == 'BSDF_PRINCIPLED':
            for sock in n.inputs:
                if sock.is_linked:
                    for link in getattr(sock, "links", []):
                        if link.from_node == node:
                            return sock.name
    return "Unknown"


def _compute_texture_fingerprints(obj):
    """Compute per-texture fingerprints for change detection.

    Returns dict: canonical_key -> fingerprint dict with sourceKind-specific fields.
    Fingerprint fields are metadata-only (no content hash) for fast path.
    """
    fps = {}
    if not obj.material_slots:
        return fps
    for slot_idx, slot in enumerate(obj.material_slots):
        mat = slot.material
        if not mat or not mat.use_nodes or not mat.node_tree:
            continue
        for node in mat.node_tree.nodes:
            if node.type != 'TEX_IMAGE' or not node.image:
                continue
            img = node.image
            filepath = getattr(img, "filepath", "") or ""
            filepath_raw = getattr(img, "filepath_raw", "") or ""
            source = getattr(img, "source", "")
            is_packed = bool(getattr(img, "packed_file", False))

            key = _get_texture_canonical_key(img, filepath_raw or filepath, source, is_packed)
            if not key:
                continue

            fp = {
                "canonicalKey": key,
                "slotIndex": slot_idx,
                "channel": _get_texture_channel_name(node, mat),
            }

            # Colorspace
            try:
                cs = getattr(img, "colorspace_settings", None)
                fp["colorspace"] = getattr(cs, "name", "sRGB") if cs else "sRGB"
            except Exception:
                fp["colorspace"] = "sRGB"

            if source == 'FILE' and not is_packed and (filepath_raw or filepath):
                fp["sourceKind"] = "external_file"
                abs_path = bpy.path.abspath(filepath_raw or filepath)
                fp["sourcePath"] = abs_path
                if abs_path and os.path.isfile(abs_path):
                    try:
                        st = os.stat(abs_path)
                        fp["sourceSize"] = st.st_size
                        fp["sourceMtimeNs"] = st.st_mtime_ns
                    except Exception:
                        fp["sourceSize"] = 0
                        fp["sourceMtimeNs"] = 0
                else:
                    fp["sourceSize"] = 0
                    fp["sourceMtimeNs"] = 0
                fp["contentHash"] = None
                fp["fileFormat"] = getattr(img, "file_format", "PNG")
            elif is_packed:
                fp["sourceKind"] = "packed_image"
                fp["sourcePath"] = ""
                fp["sourceSize"] = 0
                fp["sourceMtimeNs"] = 0
                fp["contentHash"] = None
                fp["width"] = getattr(img, "size", [0, 0])[0]
                fp["height"] = getattr(img, "size", [0, 0])[1]
                fp["fileFormat"] = getattr(img, "file_format", "PNG")
            elif source == 'GENERATED':
                fp["sourceKind"] = "generated_image"
                fp["sourcePath"] = ""
                fp["sourceSize"] = 0
                fp["sourceMtimeNs"] = 0
                fp["contentHash"] = None
                fp["width"] = getattr(img, "size", [0, 0])[0]
                fp["height"] = getattr(img, "size", [0, 0])[1]
                fp["fileFormat"] = getattr(img, "file_format", "PNG")
                fp["isFloat"] = getattr(img, "is_float", False)
                fp["generatedType"] = source
            else:
                continue

            fps[key] = fp

    return fps


def _compute_fingerprint_metadata_digest(fingerprints):
    """Compute a single int digest from all fingerprint metadata fields.

    Used as a fast gate: if this matches stored, no texture has changed.
    Only uses stat-level metadata (no content hash).
    """
    parts = []
    for key in sorted(fingerprints.keys()):
        fp = fingerprints[key]
        src_size = fp.get("sourceSize", 0)
        src_mtime = fp.get("sourceMtimeNs", 0)
        src_path = fp.get("sourcePath", "")
        kinds = fp.get("sourceKind", "")
        cspace = fp.get("colorspace", "sRGB")
        fmt = fp.get("fileFormat", "PNG")
        parts.append(f"{key}:{kinds}:{src_size}:{src_mtime}:{src_path}:{cspace}:{fmt}")
    raw = "|".join(parts)
    return network.xxh64(raw.encode("utf-8"))


def _fingerprint_metadata_matches(fp, stored_fp):
    """Check if metadata fields match between current and stored fingerprint.

    Returns True if all stat-level fields are identical (fast path eligible).
    Does NOT compare contentHash.
    """
    if stored_fp is None:
        return False
    for field in ("sourceKind", "sourcePath", "sourceSize", "sourceMtimeNs",
                  "slotIndex", "channel", "colorspace", "fileFormat",
                  "width", "height", "isFloat", "generatedType"):
        if fp.get(field) != stored_fp.get(field):
            return False
    return True


def _compute_content_hash_for_fingerprint(fp, img):
    """Compute content hash for a texture based on its sourceKind.

    For external files: xxh64 of file content (only called when metadata changes).
    For packed images: xxh64 of raw packed bytes or encoded content.
    For generated images: xxh64 of pixel buffer or deterministic description.
    Returns hex string or empty string on failure.
    """
    kind = fp.get("sourceKind", "")
    try:
        if kind == "external_file":
            src = fp.get("sourcePath", "")
            if src and os.path.isfile(src):
                with open(src, "rb") as f:
                    return network.xxh64(f.read())
            return ""

        elif kind == "packed_image":
            if img and getattr(img, "packed_file", None):
                try:
                    packed_bytes = img.packed_file.data
                    return network.xxh64(packed_bytes)
                except Exception:
                    pass
            return network.xxh64(f"{fp.get('width',0)}:{fp.get('height',0)}:{fp.get('fileFormat','PNG')}")

        elif kind == "generated_image":
            return network.xxh64(
                f"{fp.get('width',0)}:{fp.get('height',0)}:"
                f"{fp.get('fileFormat','PNG')}:{fp.get('isFloat',False)}:"
                f"{fp.get('generatedType','')}"
            )
    except Exception:
        pass
    return ""


def _load_texture_manifest(obj_dir):
    """Load the versioned texture manifest from disk.

    Returns manifest dict or None if missing/schema-mismatch/corrupt.
    Schema mismatch causes safe invalidation (fresh prepare).
    """
    import json
    path = os.path.join(obj_dir, TEXTURE_MANIFEST_FILENAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        sv = data.get("schemaVersion", 0)
        if sv != TEXTURE_MANIFEST_SCHEMA_VERSION:
            return None
        return data
    except Exception:
        return None


def _save_texture_manifest(obj_dir, manifest):
    """Atomically write the versioned texture manifest to disk."""
    import json
    import tempfile
    import shutil
    manifest["schemaVersion"] = TEXTURE_MANIFEST_SCHEMA_VERSION
    path = os.path.join(obj_dir, TEXTURE_MANIFEST_FILENAME)
    try:
        fd, tmp = tempfile.mkstemp(suffix=".tmp", prefix="txman_", dir=obj_dir)
        with os.fdopen(fd, "w") as f:
            json.dump(manifest, f, indent=2)
        shutil.move(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass


def _load_sidecar_state(obj_dir):
    """Load persistent sidecar state from disk (legacy v1 format).

    Returns (digest, sidecar_info) or (None, None) if no state file exists.
    Legacy format — used for backward compat during transition.
    """
    import json
    state_path = os.path.join(obj_dir, SIDECAR_STATE_FILENAME)
    if not os.path.isfile(state_path):
        return None, None
    try:
        with open(state_path, "r") as f:
            data = json.load(f)
        digest = data.get("sidecar_digest")
        info = data.get("sidecar_info", [])
        return digest, info
    except Exception:
        return None, None


def _save_sidecar_state(obj_dir, digest, sidecar_info):
    """Atomically write persistent sidecar state to disk (legacy v1 format).

    Uses temp file + rename to prevent partial write corruption.
    """
    import json
    import tempfile
    import shutil
    data = {
        "sidecar_digest": digest,
        "sidecar_info": sidecar_info,
        "timestamp": __import__("time").time(),
    }
    state_path = os.path.join(obj_dir, SIDECAR_STATE_FILENAME)
    try:
        fd, tmp_path = tempfile.mkstemp(
            suffix=".tmp",
            prefix="sidecar_state_",
            dir=obj_dir,
        )
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        shutil.move(tmp_path, state_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


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

        # Task 9B.5: Blender-side sync timing measurement
        import time as _time
        _bl_timer_total_start = _time.perf_counter()
        _bl_timer_fbx_start = 0.0
        _bl_timer_sidecar_start = 0.0
        _bl_timer_matx_start = 0.0
        _bl_timer_serialize_start = 0.0
        _bl_timer_send_start = 0.0
        _fbx_exported = 0
        _sidecars_prepared = 0
        _total_ms = 0.0
        _guid_for_log = ""

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
                _guid_for_log = guid_hex
                guid_obj = uuid.UUID(guid_hex)
                # Task 9B.6B.11: GUID persistence diagnostic
                if obj.get("ue_guid_persisted", False) is not True:
                    print(f"[IDENTITY][GUID_NOT_PERSISTED] object={obj.name} guid={guid_hex[:8]} reason=blend_not_saved_after_guid_assignment")

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

                # Task 9B.5: FBX export timing
                _bl_timer_fbx_start = _time.perf_counter()
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

                # Phase 9B.6B.4: per-texture fingerprint change detection
                _fingerprints = _compute_texture_fingerprints(obj)
                _combined_digest = _compute_fingerprint_metadata_digest(_fingerprints)
                # Phase 9B.6B.3: load persistent state from disk if not in memory
                _prev_sidecar_digest = sync._last_sidecar_digest.get(guid_hex)
                _stored_manifest = None
                if _prev_sidecar_digest is None:
                    _loaded_digest, _loaded_info = _load_sidecar_state(obj_dir)
                    if _loaded_digest is not None:
                        sync._last_sidecar_digest[guid_hex] = _loaded_digest
                        sync._last_sidecar_info[guid_hex] = _loaded_info
                        _prev_sidecar_digest = _loaded_digest
                        print(f"[FBX][SIDECAR_STATE_LOADED] guid={guid_hex[:8]} digest={_loaded_digest} textures={len(_loaded_info)}")
                    # Also try loading v2 texture manifest
                    _stored_manifest = _load_texture_manifest(obj_dir)
                    if _stored_manifest:
                        print(f"[FBX][MANIFEST_V2_LOADED] guid={guid_hex[:8]} textures={len(_stored_manifest.get('textures', {}))}")
                else:
                    _stored_manifest = _load_texture_manifest(obj_dir)
                if _prev_sidecar_digest is not None and _prev_sidecar_digest == _combined_digest:
                    sidecar_copied = 0
                    sidecar_info = sync._last_sidecar_info.get(guid_hex, [])
                    print(f"[FBX][SIDECAR_SKIP] guid={guid_hex[:8]} reason=textures_unchanged digest={_combined_digest}")
                else:
                    # Per-texture granular sidecar copy
                    _bl_timer_sidecar_start = _time.perf_counter()
                    sidecar_copied, sidecar_info = _copy_textures_sidecar(
                        obj, obj_dir, guid_hex[:8],
                        fingerprint_map=_fingerprints,
                        stored_manifest=_stored_manifest)
                    sync._last_sidecar_digest[guid_hex] = _combined_digest
                    sync._last_sidecar_info[guid_hex] = sidecar_info
                    # Build and save v2 texture manifest
                    _textures_manifest = {"textures": {}, "guid": guid_hex}
                    _stored_textures = _stored_manifest.get("textures", {}) if _stored_manifest else {}
                    _si_idx = 0
                    for _slot in obj.material_slots:
                        _mat = _slot.material
                        if not _mat or not _mat.use_nodes or not _mat.node_tree:
                            continue
                        for _node in _mat.node_tree.nodes:
                            if _node.type != 'TEX_IMAGE' or not _node.image:
                                continue
                            _img = _node.image
                            _filepath = getattr(_img, "filepath", "") or ""
                            _filepath_raw = getattr(_img, "filepath_raw", "") or ""
                            _source = getattr(_img, "source", "")
                            _is_packed = bool(getattr(_img, "packed_file", False))
                            _key = _get_texture_canonical_key(
                                _img, _filepath_raw or _filepath, _source, _is_packed)
                            if not _key:
                                continue
                            _fp = _fingerprints.get(_key, {})
                            _entry = dict(_fp)
                            # Reuse stored content hash if metadata unchanged
                            _stored_entry = _stored_textures.get(_key)
                            if _stored_entry and _fingerprint_metadata_matches(_fp, _stored_entry):
                                _entry["contentHash"] = _stored_entry.get("contentHash", "")
                            else:
                                _entry["contentHash"] = _compute_content_hash_for_fingerprint(_fp, _img)
                            _si = sidecar_info[_si_idx] if _si_idx < len(sidecar_info) else None
                            if _si:
                                _entry["sidecarInfo"] = _si
                                _entry["destinationFilename"] = _si.get("filename", "")
                                _entry["destinationSize"] = _si.get("size", 0)
                            _entry["destinationFilename"] = _entry.get("destinationFilename", "")
                            _entry["destinationSize"] = _entry.get("destinationSize", 0)
                            _textures_manifest["textures"][_key] = _entry
                            _si_idx += 1
                    _save_texture_manifest(obj_dir, _textures_manifest)
                    print(f"[FBX][MANIFEST_V2_SAVED] guid={guid_hex[:8]} textures={len(_textures_manifest['textures'])}")
                    # Also persist legacy sidecar state for backward compat
                    _save_sidecar_state(obj_dir, _combined_digest, sidecar_info)
                    print(f"[FBX][SIDECAR_STATE_SAVED] guid={guid_hex[:8]} digest={_combined_digest} textures={len(sidecar_info)}")
                _sidecars_prepared += sidecar_copied if isinstance(sidecar_copied, int) else 1
                # Task A: deterministic ordering log for sidecar readiness
                _reused_count = len(sidecar_info) - (sidecar_copied if isinstance(sidecar_copied, int) else 0)
                print(f"[FBX][SIDECAR_READY] guid={guid_hex[:8]} copied={sidecar_copied} reused={_reused_count} total={len(sidecar_info)}")

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
                    # Task 9B.6B.13: start collecting before first extraction call
                    network._mtex_start_collecting(seq, guid_hex)
                    # Phase 7H: include texture hash in dirty detection
                    current_tex_sigs = {}
                    for slot_idx, slot in enumerate(obj.material_slots):
                        if slot and slot.material:
                            maps = network.extract_texture_maps_for_slot(slot.material, slot.material.name, slot_idx, _collect=True)
                            if maps:
                                tex_hash = network.compute_material_texture_hash(slot_idx, maps)
                                current_tex_sigs[slot_idx] = tex_hash

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
                    # Task 9B.6B.14: collect material basic properties for transaction summary
                    network._mt_basic_start_collecting(seq, guid_hex)
                    for slot_idx, slot in enumerate(obj.material_slots):
                        if slot and slot.material:
                            p = network.get_material_basic_properties(slot.material)
                            if p is not None:
                                mat_props[slot_idx] = p
                                # Task 9B.6B.14: collect for summary
                                network._mt_basic_collect_slot(slot_idx, p)

                    tex_maps = None
                    try:
                        tex_maps_dict = {}
                        for slot_idx, slot in enumerate(obj.material_slots):
                            if slot and slot.material:
                                maps = network.extract_texture_maps_for_slot(slot.material, slot.material.name, slot_idx, _collect=True)
                                if maps:
                                    tex_maps_dict[slot_idx] = maps
                                    for ch, fpath, img_name, flags in maps:
                                        abs_path = bpy.path.abspath(fpath) if fpath else ""
                                        file_exists = os.path.isfile(abs_path) if abs_path else False
                                        source = "PACKED" if (flags & network.MTEX_FLAG_IMAGE_PACKED) else "FILE"
                                        ch_name = {1: "BaseColor", 2: "Roughness", 3: "Metallic", 4: "Alpha", 5: "Normal"}.get(ch, "Unknown")
                                        has_tex = "1" if abs_path else "0"
                                        exists_str = "1" if file_exists else "0"
                                        print(f"[MATERIAL][TEXTURE_CHANNEL_SCAN] object={obj.name} slot={slot_idx} material={slot.material.name} channel={ch_name} hasTexture={has_tex} image={img_name} path={abs_path[:200] if abs_path else ''} exists={exists_str} source={source}")
                        if tex_maps_dict:
                            tex_maps = tex_maps_dict
                    except Exception:
                        tex_maps = None

                    # Task 9B.6B.13: emit MTEX extraction summary after collecting records.
                    _mtex_records = network._mtex_collect_records
                    if _mtex_records:
                        unique_keys = set()
                        for slot_idx, ch, img_name, fpath, flags, is_packed in _mtex_records:
                            ch_name = {1: "BaseColor", 2: "Roughness", 3: "Metallic", 4: "Alpha", 5: "Normal"}.get(ch, "Unknown")
                            unique_keys.add((guid_hex, seq, slot_idx, ch, img_name))
                        unique_count = len(unique_keys)
                        print(
                            f"[MTEX][EXTRACT_SUMMARY] syncId={seq} guid={guid_hex[:8]} "
                            f"object={obj.name} slots={len(_mtex_records)} records={len(_mtex_records)} "
                            f"uniqueRecords={unique_count}"
                        )
                        # Emit per-record lines only in verbose mode (one per unique record)
                        if network.material_verbose_logging:
                            for slot_idx, ch, img_name, fpath, flags, is_packed in _mtex_records:
                                ch_name = {1: "BaseColor", 2: "Roughness", 3: "Metallic", 4: "Alpha", 5: "Normal"}.get(ch, "Unknown")
                                _mtex_key = (guid_hex, seq, slot_idx, ch, img_name)
                                print(
                                    f"[MTEX][EXTRACT_RECORD] syncId={seq} slot={slot_idx} "
                                    f"channel={ch_name} key={img_name} image={img_name} "
                                    f"packed={int(is_packed)}"
                                )
                    network._mtex_clear_dedup_state()

                    # Task 9B.6B.14: emit material basic property summary (FBX path)
                    _mat_records = network.mat_basic_collect_records
                    if _mat_records and network._mt_basic_collecting:
                        total_slots = len(_mat_records)
                        # Count changed fields by comparing with stored property signatures
                        total_changed = 0
                        all_changed = []
                        for si, props in _mat_records:
                            prev = sync._last_material_property_sig.get(si)
                            if prev:
                                for field in props:
                                    if field in prev and prev[field] != props[field]:
                                        total_changed += 1
                                        all_changed.append(f"slot{si}+{field}")
                        _mat_collect_guid = guid_hex[:8]
                        _mt_basic_changed_fields_str = ",".join(all_changed[:5]) if all_changed else ""
                        _append_blender_debug_log(
                            f"[MATERIAL][BASIC_EXTRACT_SUMMARY] syncId={seq} "
                            f"guid={_mat_collect_guid} object={obj.name} "
                            f"materialSlots={total_slots} materialsExamined={total_slots} "
                            f"materialsChanged={total_changed} fields={_mt_basic_changed_fields_str}"
                        )
                        # Emit changed-record lines only when changes exist (one per changed field, limited)
                        for _ch in all_changed[:5]:
                            _append_blender_debug_log(
                                f"[MATERIAL][BASIC_CHANGED] syncId={seq} "
                                f"guid={_mat_collect_guid} {_ch}"
                            )
                    network._mt_basic_clear_state()

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

                        # Task 9B.5: MATX extraction timing
                        _bl_timer_matx_start = _time.perf_counter()
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
            # Task 9B.5: start send timing
            _bl_timer_send_start = _time.perf_counter()
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

        # Task 9B.5: end send timing
        _bl_timer_send_end = _time.perf_counter()

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

        # Task 9B.5: emit Blender-side sync timing marker
        try:
            _bl_timer_total_ms = (_time.perf_counter() - _bl_timer_total_start) * 1000.0
            _bl_fbx_ms = (_bl_timer_fbx_start > 0) * (_time.perf_counter() - _bl_timer_fbx_start) * 1000.0 if _bl_timer_fbx_start > 0 else 0.0
            _bl_sidecar_ms = (_bl_timer_sidecar_start > 0) * (_time.perf_counter() - _bl_timer_sidecar_start) * 1000.0 if _bl_timer_sidecar_start > 0 else 0.0
            _bl_matx_ms = (_bl_timer_matx_start > 0) * (_time.perf_counter() - _bl_timer_matx_start) * 1000.0 if _bl_timer_matx_start > 0 else 0.0
            _bl_serialize_ms = 0.0
            _bl_send_ms = (_bl_timer_send_start > 0) * (_bl_timer_send_end - _bl_timer_send_start) * 1000.0
            print(f"[MATERIAL][SYNC_TIMING_BLENDER] syncId={seq} guid={_guid_for_log} fbxExportMs={_bl_fbx_ms:.1f} sidecarPrepareMs={_bl_sidecar_ms:.1f} matxExtractMs={_bl_matx_ms:.1f} serializeMs={_bl_serialize_ms:.1f} sendMs={_bl_send_ms:.1f} totalMs={_bl_timer_total_ms:.1f} sidecarsPrepared={_sidecars_prepared}")
        except Exception:
            pass

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

    # Task 9B.6B.13/14: clear MTEX dedup and material basic property collection on reload
    network._mtex_clear_dedup_state()
    network._mt_basic_clear_state()

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
