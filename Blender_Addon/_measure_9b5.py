"""Measurement helper for Task 9B.5 runtime measurement."""
import sys
import time
import json

def run_scenario(blend_path, scenario_name, ue_host="127.0.0.1", ue_port=57000):
    """Run a sync measurement scenario.
    
    Must be called from within Blender's Python context (bpy available).
    """
    import bpy
    
    print(f"\n{'='*60}")
    print(f"[MEASURE_9B5][{scenario_name}] Starting measurement")
    print(f"{'='*60}")
    
    # Check/addon state
    try:
        import UELiveSync
        addon = UELiveSync
    except ImportError:
        addon_path = "/home/nguyennongngockhanh/.var/app/org.blender.Blender/config/blender/5.1/scripts/addons"
        sys.path.insert(0, addon_path)
        import UELiveSync
        addon = UELiveSync
    
    print(f"[MEASURE_9B5][{scenario_name}] Addon version: {getattr(addon, '__version__', 'unknown')}")
    
    # Check connection
    connected = addon.network.is_connected()
    print(f"[MEASURE_9B5][{scenario_name}] Connected: {connected}")
    
    if not connected:
        print(f"[MEASURE_9B5][{scenario_name}] Attempting connect...")
        result = bpy.ops.uelivesync.discover_and_connect('INVOKE_DEFAULT')
        time.sleep(2)
        connected = addon.network.is_connected()
        print(f"[MEASURE_9B5][{scenario_name}] Connected after discover: {connected}")
    
    if not connected:
        print(f"[MEASURE_9B5][{scenario_name}] FAILED: Cannot connect to UE")
        return None
    
    # Check scene
    meshes = [obj for obj in bpy.data.objects if obj.type == 'MESH']
    print(f"[MEASURE_9B5][{scenario_name}] Objects: {len(bpy.data.objects)}, Meshes: {len(meshes)}")
    
    # Select all meshes for the cabinet
    if meshes:
        for obj in meshes:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = meshes[0]
        selected_names = [m.name for m in meshes]
        print(f"[MEASURE_9B5][{scenario_name}] Selected: {selected_names}")
    else:
        print(f"[MEASURE_9B5][{scenario_name}] FAILED: No meshes in scene")
        return None
    
    # Check materials
    mats = list(bpy.data.materials)
    print(f"[MEASURE_9B5][{scenario_name}] Materials: {len(mats)}")
    for mat in mats:
        tex_count = 0
        tex_names = []
        if mat.node_tree:
            for node in mat.node_tree.nodes:
                if node.bl_idname == 'ShaderNodeTexImage' and node.image:
                    tex_count += 1
                    tex_names.append(node.image.name)
        print(f"[MEASURE_9B5][{scenario_name}]   Mat: {mat.name} ({tex_count} textures) {tex_names}")
    
    # Now invoke Sync FBX - this will print timing markers
    print(f"[MEASURE_9B5][{scenario_name}] Invoking Sync FBX...")
    result = bpy.ops.uelivesync.sync_selected_mesh_to_ue_fbx('INVOKE_DEFAULT')
    print(f"[MEASURE_9B5][{scenario_name}] Operator result: {result}")
    
    # Wait for completion
    time.sleep(3)
    
    print(f"\n{'='*60}")
    print(f"[MEASURE_9B5][{scenario_name}] Measurement complete")
    print(f"{'='*60}\n")
    
    return result
