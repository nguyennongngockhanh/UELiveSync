# Playbook: Phase 7C Stage 3A.1 — FBX Mesh Handoff Build

Mode: BUILD.

## Objective

Implement the smallest FBX handoff vertical slice:

Blender selected mesh
-> export selected object only as FBX
-> write manifest JSON
-> send PT_FBXImportRequest = 0x16
-> UE parses request
-> UE imports FBX as StaticMesh under /Game/UELiveSync/Imported
-> UE spawns or updates StaticMeshActor by LiveSync GUID tag.

## Absolute Scope Rules

Do not replace or remove the existing procedural mesh operator.

Create a new Blender operator only:
- class UELIVESYNC_OT_sync_selected_mesh_to_ue_fbx
- bl_idname = "uelivesync.sync_selected_mesh_to_ue_fbx"
- bl_label = "Sync Selected Mesh to UE (FBX)"

Keep the old PT_Mesh/procedural path untouched.

Do not continue debugging:
- ProceduralMesh winding
- DynamicMesh backend comparison
- tangent/normal diagnostics
- V1 mesh render backend
- screenshot automation loops

Do not sync plugin.
Do not run UE Build.sh.
Do not launch UE.
Do not commit.
Do not delete old tests or evidence.

## Allowed Edit Paths

Blender:
- /home/nguyennongngockhanh/Projects/UELiveSync/Blender_Addon/__init__.py
- /home/nguyennongngockhanh/Projects/UELiveSync/Blender_Addon/network.py

UE:
- /home/nguyennongngockhanh/Projects/UELiveSync/UE_Plugin/UELiveSync/Source/UELiveSync/Public/SyncTypes.h
- /home/nguyennongngockhanh/Projects/UELiveSync/UE_Plugin/UELiveSync/Source/UELiveSync/Public/UELiveSyncSubsystem.h
- /home/nguyennongngockhanh/Projects/UELiveSync/UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem.cpp
- /home/nguyennongngockhanh/Projects/UELiveSync/UE_Plugin/UELiveSync/Source/UELiveSync/UELiveSync.Build.cs

Optional new helper files:
- /home/nguyennongngockhanh/Projects/UELiveSync/UE_Plugin/UELiveSync/Source/UELiveSync/Public/FBXImport/LiveSyncFBXImporter.h
- /home/nguyennongngockhanh/Projects/UELiveSync/UE_Plugin/UELiveSync/Source/UELiveSync/Private/FBXImport/LiveSyncFBXImporter.cpp

Tests may be added only under:
- /home/nguyennongngockhanh/Projects/UELiveSync/tests

## Allowed UE Header Inspection Paths

Only inspect these exact UE headers for import API signatures:

- /home/nguyennongngockhanh/Unreal/UE5.7.4/Engine/Source/Developer/AssetTools/Public/AssetToolsModule.h
- /home/nguyennongngockhanh/Unreal/UE5.7.4/Engine/Source/Developer/AssetTools/Public/IAssetTools.h
- /home/nguyennongngockhanh/Unreal/UE5.7.4/Engine/Source/Editor/UnrealEd/Classes/Factories/FbxFactory.h
- /home/nguyennongngockhanh/Unreal/UE5.7.4/Engine/Source/Editor/UnrealEd/Classes/Factories/Factory.h
- /home/nguyennongngockhanh/Unreal/UE5.7.4/Engine/Source/Editor/UnrealEd/Public/AssetImportTask.h

Forbidden:
- broad find
- searching for UnrealBuildTool
- searching engine paths
- modifying engine files

## Wire Format

Packet type:
- PT_FBXImportRequest = 0x16

Payload version:
- uint32 Version = 1

Fixed payload:
- ObjectGUID: 16 bytes
- Version: uint32
- FbxPath: 512 bytes, UTF-8 null-padded
- ObjectName: 128 bytes, UTF-8 null-padded
- VertCount: uint32
- TriCount: uint32
- MatSlotCount: uint32
- Timestamp: double

Do not accept AssetPath from Blender.

UE must generate asset destination internally:
- /Game/UELiveSync/Imported/<SanitizedObjectName>_<GuidShort>

## Blender Requirements

1. Add PT_FBXImportRequest = 0x16.
2. Add serialize_fbx_import_request().
3. Payload size must be fixed and testable.
4. Export selected object only.
5. Cache path:
   /home/nguyennongngockhanh/.cache/uelivesync/fbx/<object_guid>/<safe_object_name>.fbx
6. Write manifest JSON beside FBX.
7. Send packet through existing network send path.
8. Add UI button for the new FBX operator.
9. Do not remove the old button.

## UE Requirements

1. Add PT_FBXImportRequest = 0x16 to protocol enum/constant.
2. Add 0x16 to valid packet types.
3. Parse fixed payload safely.
4. Validate FBX path:
   - file exists
   - path starts with /home/nguyennongngockhanh/.cache/uelivesync/fbx
   - path does not contain ..
5. Import only into /Game/UELiveSync/Imported.
6. Use WITH_EDITOR around editor-only import code.
7. On import failure, log warning and return safely.
8. Spawn/update StaticMeshActor by LiveSync GUID tag.
9. Add counters/logs:
   - FBXImportRequestsReceived
   - FBXImportRequestsRejected
   - FBXImportSucceeded
   - FBXImportFailed
   - FBXImportActorsSpawned
   - FBXImportActorsUpdated

## Tests

Add/update tests only if existing test structure supports it.

Minimum useful tests:
- Blender payload size and offsets.
- path/name sanitization.
- manifest fields.
- packet type constant 0x16.
- UE valid type includes 0x16 by source text check.
- existing PT_Mesh path not removed or renamed.

## Required Final Output

After edits, show:

1. Exact changed files.
2. git diff --stat.
3. Tests run and result.
4. Any UE API uncertainty.
5. Explicit statement:
   - plugin not synced
   - UE not built
   - UE not launched
   - no commit made

Stop if:
- UE import API signature is uncertain.
- Required files are missing.
- Build scope would require broad search.
- Existing procedural mesh path would need modification.
