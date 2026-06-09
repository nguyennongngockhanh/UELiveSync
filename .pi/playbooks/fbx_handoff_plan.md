# Playbook: FBX Mesh Handoff Plan

Mode: PLAN/AUDIT ONLY.

Do not edit files.
Do not build.
Do not launch UE.
Do not run broad find/search over home directory.
Do not touch existing V1 runtime mesh/procedural diagnostics.
Do not modify packet format yet.
Do not commit.

Use only the paths listed in AGENTS.md.

Task goal:
Design the smallest vertical slice:
Blender selected cube -> export FBX to cache folder -> send request to UE -> UE Editor imports FBX into /Game/UELiveSync/Imported -> spawn/update StaticMeshActor with LiveSync GUID tag.

Production strategy:
- Keep TCP LiveSync for transform/visibility/rename/keyframes.
- Stop using ProceduralMesh/DynamicMesh runtime mesh data path for production mesh import.
- Add on-demand FBX handoff path.

Audit checklist:
1. Find the current Blender operator for “Sync Selected Mesh to UE”.
2. Find the current Blender network packet sending path.
3. Find the current UE receive handler location.
4. Check whether UELiveSyncEditor module should host editor-only FBX import code.
5. Propose exact files to edit for Stage 3A.
6. Propose request/manifest schema.
7. Propose packet strategy:
   - Option A: new packet type PT_FBXImportRequest.
   - Option B: reuse existing text/json command packet if one exists.
   - Option C: temporary editor command file polling only if TCP packet change is too risky.
8. Identify UE 5.7 import API options available from source/includes:
   - UAssetImportTask
   - AssetToolsModule / IAssetTools
   - UFbxFactory
   - Interchange import if already present in the project
9. Recommend the simplest safe implementation path.
10. Propose tests:
   - Blender FBX export path generation
   - manifest JSON content
   - request serialization
   - UE request parse validation
   - import path sanitization
   - GUID tag spawn/update
   - no packet format regression for old packets

Expected final report:
1. Current entry points found with file:line evidence.
2. Proposed Stage 3A architecture.
3. Exact file edit list.
4. Proposed request/manifest schema.
5. UE import API recommendation.
6. Risks and mitigations.
7. One small implementation plan for next Build prompt.
