---
name: blender-addon-pro
description: >-
  Use when writing, reviewing, or refactoring Blender addon Python code
  (bpy, bmesh, RNA properties, UI drawing, operators, panels, render
  engines, animation tools, camera rigs, viewport tools). Use ONLY for
  production-grade addons targeting Blender 5.0+, EEVEE workflows,
  and real-time viewport performance. Not for general Python scripting
  outside Blender.
---

# Blender Addon Pro — Engineering Guidelines

## Core Principles

- **Preserve backward compatibility** — prefer additive changes over destructive rewrites. Never break existing operator `bl_idname` values, property paths, or registration lifecycle without explicit version bump.
- **Target Blender 5.x APIs** — use current `bpy.types`, stable RNA paths, and documented panels. Avoid deprecated patterns (`RegisterMenu`, old `UILayout` calls, pre-4.0 registration).
- **Production-safe Python** — strict `except Exception` scoping, no bare `except:`, no mutable default arguments, guard bpy calls against context failures.
- **Surgical refactors** — change only what the task demands. Avoid touching unrelated operators, panels, or modules.

## Architecture & Structure

```
my_addon/
├── __init__.py          # bl_info, register/unregister, class tuple
├── operators.py         # bpy.types.Operator subclasses
├── ui.py                # Panels, menus, header UI
├── properties.py        # PropertyGroup definitions, pointer properties
├── preferences.py       # AddonPreferences
├── engine.py            # RenderEngine subclass (if applicable)
├── utils.py             # Pure helpers (no bpy calls)
```

- `register()` / `unregister()` must be idempotent and re-entrant.
- Register all classes in a single flat tuple at module level.
- Import submodules inside `register()` / `unregister()` to break cycles.

## Registration Lifecycle

```python
bl_info = {
    "name": "My Addon",
    "author": "...",
    "version": (1, 0, 0),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > My Tab",
    "description": "...",
    "category": "3D View",
}

classes = (MyOperator, MyPanel, MyProps, MyPreferences)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
```

- Sort classes topologically: PropertyGroup before any class referencing it, Preferences before panels that read them.
- Use `reversed()` in `unregister()`.
- Never hardcode `bl_idname` — Blender auto-generates from class name + module.

## Operators

```python
class MYADDON_OT_do_thing(bpy.types.Operator):
    bl_idname = "myaddon.do_thing"
    bl_label = "Do Thing"
    bl_options = {'REGISTER', 'UNDO'}

    my_prop: bpy.props.FloatProperty(
        name="Strength",
        default=1.0,
        min=0.0, max=10.0,
    )

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def execute(self, context):
        try:
            # ... work ...
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
```

- Always return `{'FINISHED'}` or `{'CANCELLED'}`.
- Use `poll()` for context guards, not `execute()` checks.
- Wrap fallible work in try/except; report errors to user.
- Include `'UNDO'` in `bl_options` for mutation operators.

## UI Panels

```python
class MYADDON_PT_main(bpy.types.Panel):
    bl_label = "My Tools"
    bl_idname = "MYADDON_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "My Tab"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        props = context.scene.myaddon_props

        row = layout.row(align=True)
        row.operator("myaddon.do_thing")
        row.prop(props, "strength", text="")
```

- Use `layout.use_property_split = True` for clean property display.
- Column packing: `layout.column(align=True)` for grouped controls.
- Use `layout.separator()` sparingly — prefer visual grouping via `layout.box()`.
- Keep draw() methods under 30 lines; extract sub-layouts to helper methods.

## Properties & RNA

```python
class MyAddonProperties(bpy.types.PropertyGroup):
    strength: bpy.props.FloatProperty(
        name="Strength",
        default=1.0,
        min=0.0, max=10.0,
        precision=3,
        description="Effect strength multiplier",
    )

def register():
    bpy.utils.register_class(MyAddonProperties)
    bpy.types.Scene.myaddon_props = bpy.props.PointerProperty(type=MyAddonProperties)

def unregister():
    del bpy.types.Scene.myaddon_props
    bpy.utils.unregister_class(MyAddonProperties)
```

- Use type annotations (`strength: bpy.props.FloatProperty`) for editor autocomplete.
- Always provide `name`, `description`, `default`, and sensible `min`/`max`.
- `precision` controls UI display, not internal precision.
- PropertyGroup pointer on `Scene` for per-scene data; `WindowManager` for transient UI state; `Object`/`Material` for per-data-block data.

## Preferences (AddonPreferences)

```python
class MyAddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    server_host: bpy.props.StringProperty(
        name="Server Host",
        default="127.0.0.1",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "server_host")
```

- Access via `context.preferences.addons[__package__].preferences`.
- `bl_idname` must match `__package__`.
- Keep preferences for settings that rarely change per-scene.

## Camera Systems

- Stabilize camera operator names as `bl_idname` — never rename after release.
- Store camera rig data in `Camera` custom properties (`cam["rig_type"]`), not a separate PropertyGroup.
- Frame camera operator with `'INVOKE_REGION_CHAN'` for interactive placement.
- For real-time camera previews, use `bpy.types.SpaceView3D` draw handlers (`bpy.types.SpaceView3D.draw_handler_add`).
- Animateable camera props: `location`, `rotation_euler`, `scale`, `dof.focus_distance`, `dof.aperture_fstop`.

## Animation Workflows

- Keyframe insertion: use `obj.keyframe_insert(data_path="location", frame=cframe)` — avoids F-curve batch rebuilds.
- NLA operators: prefer `bpy.ops.nla.*` over manual strip manipulation.
- For bulk F-curve work, access `obj.animation_data.action.fcurves` directly instead of operator calls — O(1) vs O(n).
- Driver creation: `driver = fcurve.driver` with `driver.expression` and `driver.variables`.
- Animation playback scrub: `context.scene.frame_set(frame)` — lightweight, no depsgraph rebuild.
- Always check `obj.animation_data and obj.animation_data.action` before accessing F-curves.

## Realtime Viewport

- EEVEE: `context.scene.eevee` properties for bloom, bloom_intensity, SSR, ambient_occlusion, motion_blur.
- Viewport drawing: `bpy.types.SpaceView3D.draw_handler_add` → callback returns GPU shader batch.
- GPU module: prefer `gpu.*` over `bgl` (deprecated in 4.x, removed in 5.x?). Use `gpu.shader.from_builtin`, `gpu.types.GPUBatch`, `gpu.types.GPUVertBuf`.
- For gizmos: `bpy.types.GizmoGroup` — define `draw` and `invoke`; register on `bl_space_type = 'VIEW_3D'`.
- Batch viewport updates: batch coordinate updates into single `GPUVertBuf.attr_fill` calls.

## Rendering

- EEVEE render pass: `bpy.ops.render.render('INVOKE_DEFAULT', write_still=True)`.
- RenderEngine subclass: define `bl_use_postprocess`, `bl_use_eevee_viewport`, `bl_use_gpu_context`.
- Viewport render: `engine.render(scene)` → `result = self.begin_result(0, 0, w, h)` → `result.layers[0].passes["Combined"]`.
- Avoid calling `bpy.ops.render.*` from modal operators — use `bpy.ops.render.view_cancel()` and `bpy.ops.render.render('INVOKE_DEFAULT')`.

## Production Patterns

- **Error scoping**: always `except Exception as e:` — never bare `except:`.
- **Context guards**: check `context.get(key)` before accessing nested attributes.
- **Lazy imports**: import heavy modules (`bmesh`, `mathutils.geometry`) inside functions, not at module level.
- **Timer lifecycle**: store timer ref, unregister on stop, guard with try/except.
- **Property defaults**: never use mutable defaults (lists, dicts) in `bpy.props.*` — they are silently ignored.
- **UI string freeze**: user-facing strings go in a `TEXT = {}` dict at module top for i18n readiness.

## Testing

- Test Blender background mode: `blender -b --python test.py`.
- Isolate tests from user preferences: launch with `--factory-startup`.
- Test registration idempotency: `register()` → `unregister()` → `register()` → check no exceptions.
- Test operator poll: call `operator.poll(context)` with varied contexts.
- Use `bpy.app.timers.register(test_fn, first_interval=0)` for deferred operations in background mode.

## See Also

- `patterns.md` — reusable Blender engineering patterns
- `examples.md` — annotated code examples for common tasks
