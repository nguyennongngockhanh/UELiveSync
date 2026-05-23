# Reusable Engineering Patterns

## Singleton PropertyGroup per Domain

Each domain gets exactly one PropertyGroup pointer on the relevant data block.
Avoid scattering properties across unrelated types.

```python
class MyAddonSceneProps(bpy.types.PropertyGroup): pass   # bpy.types.Scene
class MyAddonObjectProps(bpy.types.PropertyGroup): pass   # bpy.types.Object
class MyAddonMaterialProps(bpy.types.PropertyGroup): pass  # bpy.types.Material
```

## Operator → PropertyGroup Data Flow

Pass bulk data through PropertyGroup, not operator properties.

```python
class MYADDON_OT_batch_op(bpy.types.Operator):
    bl_idname = "myaddon.batch_op"
    bl_label = "Batch Operation"

    def execute(self, context):
        props = context.scene.myaddon_props
        # Read from props, not operator-level FloatProperty
        ...
```

## Timer Lifecycle

```python
_timer = None

def start_timer():
    global _timer
    if _timer is not None:
        try:
            bpy.app.timers.unregister(_timer)
        except ValueError:
            pass
    _timer = bpy.app.timers.register(my_callback)

def stop_timer():
    global _timer
    if _timer is not None:
        try:
            bpy.app.timers.unregister(_timer)
        except ValueError:
            pass
        _timer = None
```

## Modal Operator

```python
class MYADDON_OT_modal(bpy.types.Operator):
    bl_idname = "myaddon.modal"
    bl_label = "Modal Operator"

    def modal(self, context, event):
        if event.type in {'RIGHTMOUSE', 'ESC'}:
            return {'CANCELLED'}
        if event.type == 'LEFTMOUSE':
            return {'FINISHED'}
        # ... handle event ...
        return {'RUNNING_MODAL'}

    def invoke(self, context, event):
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}
```

## Scene-Safe depsgraph Access

```python
def get_objects_in_scene(context):
    depsgraph = context.evaluated_depsgraph_get()
    for obj_instance in depsgraph.object_instances:
        obj = obj_instance.object
        if obj.type != 'MESH':
            continue
        yield obj_instance, obj
```

## Handling bpy Context Failures

```python
def get_active_object(context):
    try:
        return context.active_object
    except AttributeError:
        return None
```

## Batch F-Curve Update (avoids operator overhead)

```python
def set_keyframe(obj, data_path, value, frame):
    obj.keyframe_insert(data_path=data_path, frame=frame)

def bulk_set_keyframes(obj, data_path, values):
    action = obj.animation_data.action if obj.animation_data else None
    if not action:
        return
    fcurve = next((fc for fc in action.fcurves if fc.data_path == data_path), None)
    if not fcurve:
        return
    for frame, value in values:
        obj.keyframe_insert(data_path=data_path, frame=frame)
```

## UI Split Layout

```python
def draw_property_with_button(layout, label, prop_owner, prop_name, operator_id):
    split = layout.split(factor=0.6)
    split.prop(prop_owner, prop_name, text=label)
    split.operator(operator_id, text="", icon='TOOL_SETTINGS')
```

## Idempotent Registration Guard

```python
def safe_register_class(cls):
    try:
        bpy.utils.register_class(cls)
    except ValueError:
        pass  # already registered
```

## Check for RNA Path Validity

```python
def has_rna_prop(obj, prop_name):
    rna = obj.bl_rna.properties.get(prop_name)
    return rna is not None
```
