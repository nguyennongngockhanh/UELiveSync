# Manual E2E.1 — Camera Frustum Crash Investigation

## Runtime Classification

**PASS_CAMERA_FRUSTUM_CRASH_GUARD** (E2E.2 Runtime Validation)

## Secondary Blockers

- **STALE_LOG_READER_RISK** — Validator may read stale `ProjectTemplate-backup-*.log` files.
- **BLENDER_ADDON_ENABLE_UNVERIFIED** — Flatpak addon file exists but enablement in `bpy.context.preferences.addons` not verified.

---

## Crash Stack

```
AActor::GetAttachParentActor()
AActor::GetSelectionParent()
AActor::IsActorOrSelectionParentSelected()
FPrimitiveSceneProxyDesc::InitializeFromPrimitiveComponent()
FPrimitiveSceneProxy::FPrimitiveSceneProxy()
UDrawFrustumComponent::CreateSceneProxy()
UPrimitiveComponent::CreateRenderState_Concurrent()
UWorld::SendAllEndOfFrameUpdates()
UEditorEngine::Tick()
```

## Root Cause Hypothesis

ACameraActor includes an editor-visualization component (`UDrawFrustumComponent`) that creates a render proxy during editor tick. When LiveSync-spawns an ACameraActor and then immediately calls `AttachToActor()` (which triggers the editor selection/attachment-parent path via `GetSelectionParent()` → `IsActorOrSelectionParentSelected()`), the frustum component's render proxy creation accesses the attach-parent chain before it is fully stabilized — causing a crash in `GetAttachParentActor()`.

The sequence is:
1. `SpawnActor<ACameraActor>` — actor created, frustum component exists.
2. `AttachToParent()` called before frustum is suppressed.
3. During the next editor tick, `UDrawFrustumComponent::CreateSceneProxy()` runs.
4. Frustum's primitive proxy creation calls `FPrimitiveSceneProxyDesc::InitializeFromPrimitiveComponent()` → `IsActorOrSelectionParentSelected()` → `GetSelectionParent()` → `GetAttachParentActor()` on an actor whose attach chain was just modified mid-frame.
5. Crash (SIG 6 — abort/dcheck).

## Runtime Evidence

### UE Launch
- UE launched successfully, port 57000 listened.
- `ProjectTemplate.log` manually truncated before launch.
- Log rotation / backup logs caused confusion during validation.
- Validator/injector may read stale `ProjectTemplate-backup-*.log` containing old GUIDs.

### Tick/Network Thread Behavior
- NetworkThread received/enqueued packets after game thread/tick stopped processing.
- Tick heartbeat showed frames advancing until a final frame, then packet receive continued without `ProcessQueuedPackets`.
- **Conclusion:** Runtime validation must not classify feature failure solely from queued-but-not-processed packets if tick is halted.

### Blender Flatpak
- Deployed file exists:
  `/home/nguyennongngockhanh/.var/app/org.blender.Blender/config/blender/5.1/scripts/addons/UELiveSync/__init__.py`
- File existence ≠ addon enabled.
- Automated Blender Python checks did not find UELiveSync in enabled addon preferences.
- Some Blender Python invocations timed out.
- Unrelated addon warning present:
  `Add-on not loaded: "Blender_Addon", cause: No module named 'Blender_Addon'`
  — Do not confuse with UELiveSync unless source proves relation.

### Log Hygiene
- UE launched and port 57000 listened successfully.
- `ProjectTemplate.log` was manually truncated before launch.
- Log rotation / backup logs caused confusion.
- Validator/injector may read stale `ProjectTemplate-backup-*.log` containing old GUIDs.
- Validator must filter by current-run GUID or timestamp.

---

## Fix: ConfigureLiveSyncCameraActor Helper

### Purpose

Suppress frustum/editor visualization for LiveSync-spawned cameras **without** disabling `UCameraComponent` or the camera actor itself.

### API

```cpp
void UUELiveSyncSubsystem::ConfigureLiveSyncCameraActor(ACameraActor* CameraActor);
```

### Operations

1. Tag actor as LiveSync camera (`Tags` prefix).
2. Keep `UCameraComponent` enabled and visible.
3. Keep transform sync intact.
4. Keep `CameraDef` path intact.
5. Keep Sequencer binding/camera cut path intact.
6. Suppress only frustum/editor visualization:
   - Cast frustum components to `USceneComponent`, call `SetHiddenInGame(true)`, `SetVisibility(false, true)`, `SetComponentTickEnabled(false)`.
   - Do NOT destroy `UCameraComponent`.
   - Do NOT disable camera component.
   - Do NOT disable camera actor.

### E2E.2 Runtime Evidence

- UE process: alive (PID 204513)
- Port 57000: listening
- `[CAMERA][CREATE]`: 7543 occurrences
- `[CAMERA][FRUSTUM_GUARD]`: 94264 occurrences (frustum suppressed on every camera spawn)
- `[CAMERA][TRANSFORM_APPLY]`: 94213 occurrences
- `[CAMERA][ACTIVE_RECV]`: 8002 occurrences
- `[CAMERA][SEQ_BIND]`: 94076 occurrences
- `[CAMERA][CUT_APPLY]`: 7596 occurrences
- No "Caught signal" / "UDrawFrustumComponent::CreateSceneProxy" crash
- No "GetSelectionParent" crash
- Log: `/tmp/uelivesync-manual-e2e-ue.log`

### Diagnostics

- `[CAMERA][FRUSTUM_GUARD]` — frustum suppressed successfully
- `[CAMERA][FRUSTUM_GUARD_SKIP]` — frustum not found or already hidden
- `[CAMERA][FRUSTUM_GUARD_FAIL]` — guard failed (null actor, etc.)

### Integration Points

Called from:
1. `HandleCreateObject()` — camera spawn path (line ~7821).
2. `HandleActiveCamera()` — auto-spawn path (line ~10965).

---

## Validation Strategy

### Code Tests

- `tests/manual_e2e_camera_crash_guard.py` — static analysis tests.
- Verify helper exists.
- Verify both camera spawn paths call the helper.
- Verify `UCameraComponent` is not disabled/destroyed.
- Verify `CameraDef` path remains intact.
- Verify Sequencer binding/camera cut path remains intact.
- Verify no protocol change.
- Verify `0x02` remains reserved/invalid.
- Verify `0x10` remains unused.

### Build

```bash
/home/nguyennongngockhanh/Unreal/UE5.7.4/Engine/Build/BatchFiles/Linux/Build.sh \
  ProjectTemplateEditor Linux Development \
  "/home/nguyennongngockhanh/Documents/Unreal Projects/ProjectTemplate/ProjectTemplate.uproject"
```

### Runtime

1. Launch UE editor/windowed on `DISPLAY=:0`.
2. Do not use `-NullRHI`.
3. Capture output/log to `/tmp/uelivesync-manual-e2e-ue.log`.
4. Run camera lifecycle injector: `tools/uelivesync_7g_camera_transform_client.py`.
5. Confirm no crash.
6. Confirm markers:
   - `[CAMERA][CREATE]`
   - `[CAMERA][FRUSTUM_GUARD]`
   - `[CAMERA][TRANSFORM_APPLY]`
   - `[CAMERA][ACTIVE_RECV]`
   - `[CAMERA][SEQ_BIND]`
   - `[CAMERA][CUT_APPLY]`
7. If tick/focus blocks processing: classify as `PASS_CAMERA_FRUSTUM_GUARD_CODE_ONLY + ENV_RUNTIME_TICK_BLOCKED`.

### Regression Tests

- `tests/manual_e2e_camera_crash_guard.py`
- `tests/phase7g_stage4_camera_transform_sync.py`
- `tests/phase7g_stage5_camera_sequencer_binding.py` (if exists)
- `tests/phase7g_stage5a_camera_cut_capture.py` (if exists)
- `tests/phase9_stage3b_discovery_scan.py`
- `tests/phase9_stage3c_discovery_connect_ux.py`
- `tests/e2e_runtime_validation_suite_audit.py`

---

## Commit / Tag

- **Commit message:** `fix(camera): guard LiveSync camera frustum rendering`
- **Tag (only if crash resolved):** `manual-e2e-camera-crash-guard-stable`

---

## Classification Criteria

| Condition | Classification |
|-----------|---------------|
| Crash fixed + camera lifecycle validates | `PASS_CAMERA_FRUSTUM_CRASH_GUARD` |
| Source/build passes, runtime blocked by tick/focus/log | `PASS_CAMERA_FRUSTUM_GUARD_CODE_ONLY + ENV_RUNTIME_TICK_BLOCKED` |
| Crash remains | `FAIL_CAMERA_FRUSTUM_CRASH_GUARD` |

---

## Manual E2E.3 — SceneOutliner Parent Recursion Crash

### Runtime Classification

**FAIL_MANUAL_E2E_SCENE_OUTLINER_PARENT_RECURSION**

### Crash Evidence

```
Caught signal 11 Segmentation fault

Stack:
- FActorTreeItem::UpdateDisplayStringInternal()
- FActorTreeItem::FActorTreeItem()
- SSceneOutliner::CreateItemFor<FActorTreeItem, AActor*>()
- FActorHierarchy::CreateItemForActor()
- FActorHierarchy::FindOrCreateParentItem()
- SSceneOutliner::EnsureParentForItem()
- SSceneOutliner::AddUnfilteredItemToTree()
- SSceneOutliner::EnsureParentForItem()
- SSceneOutliner::AddUnfilteredItemToTree()
- repeated many times
```

### Interpretation

The frustum crash (E2E.2) appears fixed. This is a **new, separate crash** in the UE Scene Outliner's actor hierarchy tree building.

Likely root cause: LiveSync-created actor attachment hierarchy contains:
- A cycle in the attach-parent chain (Actor A → Actor B → Actor A)
- A stale/invalid parent pointer
- Self-parenting (Actor attached to itself)
- Repeated/duplicate attach calls creating corrupted parent chain
- Parent actor pending kill / invalid during attach

The SceneOutliner walks the parent chain of each actor to build the tree. A cycle or invalid pointer causes infinite recursion → stack overflow → SIG 11.

### Classification

| Condition | Classification |
|-----------|---------------|
| Crash fixed + SceneOutliner stable | `PASS_MANUAL_E2E_SCENE_OUTLINER_PARENT_GUARD` |
| Source/build passes, runtime cannot reproduce | `PASS_SCENE_OUTLINER_PARENT_GUARD_CODE_ONLY` |
| Crash persists | `FAIL_MANUAL_E2E_SCENE_OUTLINER_PARENT_GUARD` |

### Tag Policy

- Do not create a new stable tag until runtime confirms no Signal 6 and no Signal 11.
- Existing tag `manual-e2e-camera-crash-guard-stable` is **provisional/superseded** by E2E.3.
- Do not delete remote tag without explicit approval. Create a follow-up docs note that it was superseded by E2E.3.
- If local-only and not pushed, consider deleting/replacing only after asking.

### E2E.3 Tasks

1. Document this crash (this file).
2. Audit actor attachment / hierarchy code in `UELiveSyncSubsystem.cpp`.
3. Identify all LiveSync attachment paths (normal, camera, deferred, reparent, stale cleanup).
4. Add `WouldCreateAttachmentCycle(AActor*, AActor*)` actor-pointer-level guard.
5. Add `SafeAttachLiveSyncActor(AActor*, AActor*, FGuid, FGuid)` wrapper.
6. Replace direct `AttachToActor` calls with guard.
7. Camera-specific rule: never attach camera to itself or to any actor whose parent chain includes the camera.
8. Add static tests in `tests/manual_e2e_scene_outliner_parent_guard.py`.
9. Build and runtime revalidation.
10. Commit: `fix(hierarchy): guard LiveSync actor attachment cycles`.
