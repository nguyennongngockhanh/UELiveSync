bl_info = {
    "name": "UE Live Sync",
    "author": "Harumaki",
    "version": (0, 2, 3),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar > UE Sync",
    "category": "3D View",
}

import bpy

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

        _fbx_log(f"[FBX][EXPORT_SETTINGS] guid={guid_short} "
                 f"global_scale=1.0 apply_scale_options=FBX_SCALE_UNITS "
                 f"bake_space_transform=0 use_mesh_modifiers=0 use_tspace=0 "
                 f"unit_strategy=fbx_scale_units")

        try:
            bpy.ops.export_scene.fbx(
                filepath=filepath,
                use_selection=True,
                object_types={'MESH'},
                global_scale=1.0,
                apply_scale_options='FBX_SCALE_UNITS',
                bake_space_transform=False,
                mesh_smooth_type='FACE',
                use_mesh_modifiers=False,
                use_tspace=False,
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


class UELIVESYNC_OT_sync_selected_mesh_to_ue_fbx(
    bpy.types.Operator
):
    bl_idname = \
        "uelivesync.sync_selected_mesh_to_ue_fbx"

    bl_label = "Sync Selected Mesh to UE (FBX)"

    bl_description = \
        "Export selected MESH objects to FBX cache and " \
        "send PT_FBXImportRequest to UE for StaticMesh import"

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

        import os
        import uuid
        import json
        import time
        import math
        import struct

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

                # Write manifest JSON
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
                    if prev_prop_sig is None or current_prop_sig != prev_prop_sig:
                        print(f"[SYNC][DECIDE] seq={seq} guid={guid_hex[:8]} "
                              f"sendMAT=1 reason=property_changed oldMatSig={'None'} "
                              f"newMatSig={list(current_prop_sig.keys())}")
                        # Extract material slots with MATX for send
                        mat_props = {}
                        for slot_idx, slot in enumerate(obj.material_slots):
                            if slot and slot.material:
                                p = network.get_material_basic_properties(slot.material)
                                if p is not None:
                                    mat_props[slot_idx] = p
                        # Phase 10K.1A: extract texture map references
                        tex_maps = None
                        try:
                            tex_maps_dict = {}
                            for slot_idx, slot in enumerate(obj.material_slots):
                                if slot and slot.material:
                                    maps = network.extract_texture_maps_for_slot(slot.material)
                                    if maps:
                                        tex_maps_dict[slot_idx] = maps
                            if tex_maps_dict:
                                tex_maps = tex_maps_dict
                        except Exception:
                            tex_maps = None

                        if tex_maps:
                            for slot_maps in tex_maps.values():
                                total_mtex_records += len(slot_maps)

                        # Build material packet payload
                        try:
                            mat_payload = network.serialize_material_slots(
                                guid_obj, 
                                {i: (0, 0) for i in range(len(mat_props))},  # identity placeholder
                                mat_props,
                                tex_maps
                            )
                            mat_payloads_to_send.append(mat_payload)
                            # Log MATX send details
                            for si in mat_props:
                                pp = mat_props[si]
                                print(f"[MAT][SEND] seq={seq} guid={guid_hex[:8]} "
                                      f"slot={si} matx=1 propertySig=1 "
                                      f"color=({pp.get('BaseColorR',0):.3f},{pp.get('BaseColorG',0):.3f},{pp.get('BaseColorB',0):.3f},{pp.get('Alpha',1):.3f}) "
                                      f"roughness={pp.get('Roughness',0.5):.3f} "
                                      f"metallic={pp.get('Metallic',0):.3f} "
                                      f"alpha={pp.get('Alpha',1):.3f}")
                                network._append_blender_debug_log(
                                    f"[MAT][SEND] guid={guid_hex[:8]} "
                                    f"slot={si} matx=1 "
                                    f"color=({pp.get('BaseColorR',0):.3f},{pp.get('BaseColorG',0):.3f},{pp.get('BaseColorB',0):.3f},{pp.get('Alpha',1):.3f}) "
                                    f"roughness={pp.get('Roughness',0.5):.3f} "
                                    f"metallic={pp.get('Metallic',0):.3f}"
                                )
                        except Exception as _mat_exc:
                            print(f"[MAT][ERROR] failed to build material payload for {obj.name}: {_mat_exc}")
                    else:
                        print(f"[SYNC][DECIDE] seq={seq} guid={guid_hex[:8]} "
                              f"sendMAT=0 reason=property_unchanged")
                    sync._last_material_property_sig[guid_hex] = current_prop_sig

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
        "Send the active Blender camera to UE via " \
        "existing camera protocol paths (PT_Create, " \
        "PT_Transform, PT_CameraDef)"

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

        # --- Serialize create/transform payload (PT_Create + PT_Transform) ---
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

        # --- Send packets ---
        try:
            # PT_Create (0x03) to spawn camera actor
            network.send_objects(
                [obj_payload],
                packet_type=0x03,
            )
            # PT_Transform (0x01, default) for position
            network.send_objects(
                [obj_payload],
            )
            # PT_CameraDef (0x1B) for lens/sensor/clip
            network.send_objects(
                [camdef_payload],
                packet_type=network.PT_CameraDef,
                version=5,
            )
            # PT_ActiveCamera deliberately NOT sent — viewport
            # switching is unsafe and handled by auto-detect path
            # when the user enables active_camera_sync preference.
        except Exception as e:
            self.report(
                {'ERROR'},
                f"Failed to send camera packets: {e}"
            )
            return {'CANCELLED'}

        self.report(
            {'INFO'},
            f"LiveSync camera sync sent: {camera_obj.name}"
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
