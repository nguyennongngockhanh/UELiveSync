# Code Examples

## Example 1: Minimal Addon with Preferences

```python
# __init__.py
bl_info = {
    "name": "Quick Render Tool",
    "author": "Example",
    "version": (1, 0, 0),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > Render",
    "category": "Render",
}


class QuickRenderPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    output_path: bpy.props.StringProperty(
        name="Output Path",
        subtype='DIR_PATH',
        default="//renders/",
    )

    def draw(self, context):
        self.layout.prop(self, "output_path")


class QUICKRENDER_OT_render(bpy.types.Operator):
    bl_idname = "quickrender.render"
    bl_label = "Quick Render"

    def execute(self, context):
        prefs = context.preferences.addons[__package__].preferences
        context.scene.render.filepath = prefs.output_path
        bpy.ops.render.render('INVOKE_DEFAULT', write_still=True)
        return {'FINISHED'}


class QUICKRENDER_PT_panel(bpy.types.Panel):
    bl_label = "Quick Render"
    bl_idname = "QUICKRENDER_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Render"

    def draw(self, context):
        self.layout.operator("quickrender.render", icon='RENDER_STILL')


classes = (
    QuickRenderPreferences,
    QUICKRENDER_OT_render,
    QUICKRENDER_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
```

## Example 2: Camera Rig Controller

```python
class CameraRigProperties(bpy.types.PropertyGroup):
    rig_type: bpy.props.EnumProperty(
        name="Rig Type",
        items=[
            ('STANDARD', "Standard", "Simple camera"),
            ('DOLLY', "Dolly", "Camera on dolly track"),
            ('CRANE', "Crane", "Camera on crane arm"),
        ],
        default='STANDARD',
    )


class CAMRIG_OT_create(bpy.types.Operator):
    bl_idname = "camrig.create"
    bl_label = "Create Camera Rig"
    bl_options = {'REGISTER', 'UNDO'}

    rig_type: bpy.props.EnumProperty(
        name="Rig Type",
        items=[
            ('STANDARD', "Standard", ""),
            ('DOLLY', "Dolly", ""),
            ('CRANE', "Crane", ""),
        ],
    )

    def execute(self, context):
        bpy.ops.object.camera_add()
        cam = context.active_object
        cam["camrig_type"] = self.rig_type
        cam.data.display_size = 0.5
        return {'FINISHED'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'
```

## Example 3: Viewport Gizmo

```python
class MYADDON_GT_gizmo(bpy.types.GizmoGroup):
    bl_label = "Transform Gizmo"
    bl_idname = "MYADDON_GT_gizmo"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'3D'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj and obj.type == 'MESH'

    def setup(self, context):
        gizmo = self.gizmos.new("GIZMO_GT_move_3d")
        gizmo.target_set_prop("offset", context.object, "location")
        gizmo.color = (0.8, 0.2, 0.2)
        gizmo.alpha = 0.5
```

## Example 4: EEVEE Quality Preset Toggle

```python
class EEVEE_OT_toggle_bloom(bpy.types.Operator):
    bl_idname = "eevee.toggle_bloom"
    bl_label = "Toggle Bloom"
    bl_options = {'UNDO'}

    def execute(self, context):
        scene = context.scene
        eevee = scene.eevee
        eevee.use_bloom = not eevee.use_bloom
        return {'FINISHED'}
```

## Example 5: Background-Mode Script Runner

```python
#!/usr/bin/env python3
"""
Run an addon function from Blender background mode.
Usage: blender -b --factory-startup --python export_all.py
"""
import bpy
import sys

# Disable render output during batch operations
bpy.context.preferences.view.show_splash = False

# Register addon
sys.path.insert(0, "/path/to/addon")
import my_addon
my_addon.register()

for obj in bpy.data.objects:
    if obj.type == 'MESH':
        obj.select_set(True)
        bpy.ops.export_scene.fbx(
            filepath=f"/output/{obj.name}.fbx",
            use_selection=True,
        )
        obj.select_set(False)

my_addon.unregister()
print("Batch export complete")
```
