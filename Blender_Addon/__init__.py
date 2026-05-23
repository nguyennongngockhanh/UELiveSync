bl_info = {
    "name": "UE Live Sync",
    "author": "Harumaki",
    "version": (0, 1, 0),
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

from . import sync


# =========================================================
# PREFERENCE CHANGE CALLBACKS
# =========================================================

def _on_timing_update(self, context):

    # Sync cached config when user changes timing prefs
    sync._sync_runtime_config()


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

        sync.start_sync()

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

            tracked = \
                sync.get_tracked_count()

            queue_depth = \
                sync.get_queue_depth()

            reconnect_count = \
                sync.get_reconnect_count()

            uptime = \
                sync.get_uptime()

            uptime_min = int(uptime // 60)
            uptime_sec = int(uptime % 60)

            row = box.row()
            row.label(
                text=f"Objects: {tracked}",
                icon='OBJECT_DATA',
            )

            row = box.row()
            row.label(
                text=f"Queue: {queue_depth}",
                icon='CONSOLE',
            )

            row = box.row()
            row.label(
                text=f"Reconnects: {reconnect_count}",
                icon='ERROR',
            )

            row = box.row()
            row.label(
                text=f"Uptime: {uptime_min}m{uptime_sec:02d}s",
                icon='TIME',
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
