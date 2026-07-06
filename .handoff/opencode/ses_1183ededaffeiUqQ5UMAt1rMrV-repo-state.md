# Repository state for OpenCode session

Session: ses_1183ededaffeiUqQ5UMAt1rMrV
Captured: 2026-06-21T10:20:51+07:00

## Current branch
main

## Git status
 M Blender_Addon/__init__.py
 M Blender_Addon/network.py
 M Blender_Addon/sync.py
 M UE_Plugin/UELiveSync/Source/UELiveSync/Private/FBXImport/LiveSyncFBXImporter.cpp
 M UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem.cpp
 M UE_Plugin/UELiveSync/Source/UELiveSync/Public/FBXImport/LiveSyncFBXImporter.h
 M UE_Plugin/UELiveSync/Source/UELiveSync/Public/SyncTypes.h
 M UE_Plugin/UELiveSync/Source/UELiveSync/Public/UELiveSyncSubsystem.h
?? .handoff/
?? Blender_Addon/_measure_9b5.py

## Recent commits
f370a8f (HEAD -> main) fix(material): persist full-snapshot texture transitions
3acd718 fix(material): enforce normal sampler contract and MIC readback
5a2296d Task 9B.3: Normal material contract — Lerp+MP_Normal tangent-space, TC_Normalmap, MIC readback
06f2c35 Task 9B.2: sidecar texture namespace isolation
b5c098f Task 9B.1: Fix 6 FBX material/texture defects
912cbc6 fix(material): map exact sidecar textures to persistent MICs
10e2800 feat(material): Task 9A - persistent material authority via MIC per slot
93ef694 Revert "fix(material): resolve relative MATX textures without duplicate replay"
4bb7aac fix(material): resolve relative MATX textures without duplicate replay
d799685 fix(material): repair MATX resolved-state build (Task 8B.1)

## Modified files
Blender_Addon/__init__.py
Blender_Addon/network.py
Blender_Addon/sync.py
UE_Plugin/UELiveSync/Source/UELiveSync/Private/FBXImport/LiveSyncFBXImporter.cpp
UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem.cpp
UE_Plugin/UELiveSync/Source/UELiveSync/Public/FBXImport/LiveSyncFBXImporter.h
UE_Plugin/UELiveSync/Source/UELiveSync/Public/SyncTypes.h
UE_Plugin/UELiveSync/Source/UELiveSync/Public/UELiveSyncSubsystem.h

## Diff stat
 Blender_Addon/__init__.py                          | 438 ++++++++++++++++++++-
 Blender_Addon/network.py                           |  13 +
 Blender_Addon/sync.py                              |  11 +
 .../Private/FBXImport/LiveSyncFBXImporter.cpp      |  19 +
 .../UELiveSync/Private/UELiveSyncSubsystem.cpp     | 374 +++++++++++++++++-
 .../Public/FBXImport/LiveSyncFBXImporter.h         |   2 +
 .../Source/UELiveSync/Public/SyncTypes.h           |  23 ++
 .../Source/UELiveSync/Public/UELiveSyncSubsystem.h |   4 +
 8 files changed, 869 insertions(+), 15 deletions(-)
