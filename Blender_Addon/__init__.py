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

        fbx_cache_root = \
            os.path.expanduser(
                "~/.cache/uelivesync/fbx"
            )

        synced_count = 0

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

                # Export selected object only as FBX
                import bpy
                bpy.ops.export_scene.fbx(
                    filepath=fbx_path,
                    use_selection=True,
                    object_types={'MESH'},
                    apply_scale_options='FBX_SCALE_UNITS',
                    bake_space_transform=False,
                    mesh_smooth_type='FACE',
                    use_mesh_modifiers=True,
                    use_tspace=False,
                )

                if not os.path.isfile(fbx_path):
                    print(
                        f"[FBX] Export failed — {fbx_path} "
                        "not created"
                    )
                    continue

                # Compute mesh stats from evaluated mesh
                depsgraph = \
                    context.evaluated_depsgraph_get()
                evaluated_obj = \
                    obj.evaluated_get(depsgraph)

                mesh = evaluated_obj.to_mesh()
                if mesh is None:
                    continue

                try:
                    mesh.calc_loop_triangles()
                    vert_count = len(mesh.vertices)
                    tri_count = len(mesh.loop_triangles)
                    mat_slot_count = \
                        len(mesh.materials)
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

                synced_count += 1

            except Exception as e:
                print(
                    f"[FBX] ERROR: {obj.name} — {e}"
                )

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
            "uelivesync.rebind_all",
            icon='UV_SYNC_SELECT',
        )

        layout.operator(
            "uelivesync.sync_selected_mesh_to_ue",
            icon='MESH_DATA',
        )

        layout.operator(
            "uelivesync.sync_selected_mesh_to_ue_fbx",
            icon='MESH_DATA',
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
    UELIVESYNC_OT_sync_selected_mesh_to_ue,
    UELIVESYNC_OT_sync_selected_mesh_to_ue_fbx,
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
