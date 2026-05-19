bl_info = {
    "name": "UE Live Sync",
    "author": "Harumaki",
    "version": (0, 1, 0),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar > UE Sync",
    "category": "3D View",
}

import bpy

from . import sync


class UELIVESYNC_OT_start(bpy.types.Operator):
    bl_idname = "uelivesync.start"
    bl_label = "Start UE Sync"

    def execute(self, context):

        sync.start_sync()

        return {'FINISHED'}


class UELIVESYNC_OT_stop(bpy.types.Operator):
    bl_idname = "uelivesync.stop"
    bl_label = "Stop UE Sync"

    def execute(self, context):

        sync.stop_sync()

        return {'FINISHED'}


class UELIVESYNC_PT_panel(bpy.types.Panel):
    bl_label = "UE Live Sync"
    bl_idname = "UELIVESYNC_PT_panel"

    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'UE Sync'

    def draw(self, context):

        layout = self.layout

        layout.operator("uelivesync.start")
        layout.operator("uelivesync.stop")


classes = (
    UELIVESYNC_OT_start,
    UELIVESYNC_OT_stop,
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
