# Changelog

## [unreleased]

### Added
- Stage 10A.7A log-based playback validator (`tools/uelivesync_10a7a_validation.py`).
- Automated playback state validation for visibility channels 9/10 at frames 1/10/20.
- Added Blender 5.1+ slotted Action keyframe extraction (`_iter_action_fcurves_51`).
- Added `action.is_action_layered` detection in `_extract_keyframes()`.
- Added capability gating fallback when UE does not send `PT_CapabilityResponse`.
- Added 81 tests for Blender 5.1 keyframe extraction (transform + visibility channels).
- Added wrapped SequencerOp send path for CREATE_SEQUENCE / ADD_POSSESSABLE runtime setup.
- Added Stage 10A.5 active LevelSequence runtime helper (`tools/uelivesync_stage10a5_active_sequence.py`).
- Added 2 tests for SequencerOp packet wrap and reserved type guard.
- Stage 10B.1: Asset-backed LevelSequence at `/Game/UELiveSync/Sequences/LS_UELiveSync_Runtime` via `GetOrCreateLiveSyncLevelSequenceAsset()`.
- Stage 10B.2: Runtime asset sequence validation with TCP injector (`tools/uelivesync_10b_tcp_client.py`) and validator (`tools/uelivesync_10b_asset_sequence_validation.py`).
- Added `UPackage::SavePackage()` in `GetOrCreateLiveSyncLevelSequenceAsset()` to persist asset to disk.
- Added `[SEQ][ASSET_LOAD / ASSET_CREATE / ASSET_READY / ASSET_FAIL]` diagnostic markers.
- Stage 10B.3: UE Python asset load verification — `unreal.load_asset()` returns valid `LevelSequence` (PASS_LOAD_ONLY).
- Stage 10D.1: Sequencer Editor usability validation — `open_level_sequence()` success, binding/tracks/sections persist (PASS_EDITOR_DATA_ONLY).
- Fixed `NewObject` `NAME_None` → named `FName("LS_UELiveSync_Runtime")` for clean `FSoftObjectPath` resolution.
- Stage 10C.1: Persist applied sequencer data — `SaveLiveSyncLevelSequenceAsset()` called after successful keyframe apply.
- Upgraded UE Python inspection from PASS_LOAD_ONLY to PASS_BINDING_ONLY (binding_count=1, track types detected).
- Added `[SEQ][ASSET_DIRTY / ASSET_SAVE / ASSET_SAVE_FAIL / ASSET_SAVE_SKIP]` diagnostic markers.
- Added MTEX texture metadata sync for material slots.
- Added UE-side texture import/cache from MTEX paths.
- Added generated MID texture parameter application.
- Added UELiveSync master material for texture rendering.
- Added texture pipeline diagnostics and tests.
- FBX temp asset lifecycle diagnostics: `[FBX][TEMP_IMPORT/ASSIGN/CLEANUP/KEEP_PREVIOUS/DELETE_FAIL/UNIT_INVALID/SCALE_INVARIANT]`.
- FBX unit/scale invariant checks — invalid unit imports are rejected/preserved, actor and component scale stay at 1.

### Changed
- FBX mesh sync now uses unique temp StaticMesh asset per sync instead of reimport-over-existing.
- Temp mesh assignment is validated before applying; previous temp mesh cleaned up after success.
- Blender FBX export policy: `global_scale=1.0`, `apply_scale_options='FBX_SCALE_UNITS'`, `bake_space_transform=False`.
- Material generated MID is restored after FBX mesh assignment.
- Phase 10J tests updated for current temp import lifecycle architecture (18/18 PASS).

### Fixed
- Fixed UE 5.7.4 FBX reimport meter-size regression — no longer uses reimport-over-existing path.
- Fixed material sync losing generated MID after FBX mesh refresh.
- Fixed scale invariant regressions — actor/component scale preserved at 1.
- Preserved mesh/scale invariants during material/texture sync.
- Hardened unsupported/missing texture path handling.

### Validated
- Validated active LevelSequence runtime path with PT_Keyframe applied=11, miss=0, unsupp=0.
- Validated visibility channels 9 and 10 apply through UE bool keyframe path.
- Confirmed PT_Transform remains 0x01 and 0x02 remains reserved/invalid.
- Stage 10B.2: Validated asset-backed LevelSequence persistence to disk (4055 bytes).
- Stage 10B.2: All 59/59 regression tests pass (Stage 10A.2 + 10A.5A).
- Stage 10B.3: Validated `unreal.load_asset()` returns non-null `LevelSequence` (PASS_LOAD_ONLY).
- Stage 10B.3: All 64/64 regression tests pass (added 10B.3 with 5/5).
- Stage 10D.1: Validated Sequencer Editor usability — `open_level_sequence()` succeeds, binding + tracks + sections all persist (PASS_EDITOR_DATA_ONLY).
- Stage 10D.1: All 75/75 regression tests pass (added 10D.1 with 9/9).
- Stage 10C.1: Validated `unreal.load_asset()` returns binding_count=1 with TransformTrack + BoolTrack sections (PASS_BINDING_ONLY).
- Stage 10C.1: All 66/66 regression tests pass (added 10C.1 with 7/7).

### Known notes
- Keyframe runtime requires `prefs.keyframe_sync=True`.
- UE keyframe apply requires an active LevelSequence and binding.
- `-NullRHI` should not be used for networking/runtime validation; use normal editor or `-RenderOffScreen`.

### Known limitations
- Packed Blender images are not imported.
- Complex material node graphs are not traversed.
- Alpha/Normal visual support remains limited/deferred.

## [0.2.4] - 2026-06-10

### Added
- Runtime validation for visibility hide/show, MESH-parent hierarchy, real FBX import/reimport, and visibility keyframe channels 9–10.
- Sequencer state reset on reconnect to prevent stale SEQOP rejection after reconnect.
- Buyer documentation pack (quick start, system requirements, known limitations, license FAQ, support policy).
- Updated Linux runtime validation launch profile (bare UE command instead of `CEF_DISABLE_GPU=1`).

### Fixed
- Sequencer reconnect bug where stale sequencer state caused CREATE_SEQUENCE and ADD_POSSESSABLE rejection.
- Visibility keyframe applied counter now correctly reports applied keys.

### Notes
- v0.2.4 is a patch release with runtime validation closure and sequencer reconnect fix.
- Runtime protocol and packet formats are unchanged from v0.2.3.
- v0.2.0, v0.2.1, v0.2.2, and v0.2.3 remain published and untouched.

## [0.2.3] - 2026-06-10

### Fixed
- Added missing `get_object_material_slots` fallback import in `Blender_Addon/sync.py` for standalone/background execution paths.

### Added
- Runtime validation documentation: `Docs/runtime_validation.md`.
- Full runtime validation status entry documenting the stable UE launch profile.

### Notes
- v0.2.3 is a hotfix/docs patch release.
- Runtime protocol and packet formats are unchanged from v0.2.2.
- v0.2.0, v0.2.1, and v0.2.2 remain published and untouched.
- Recommended runtime validation profile is UE windowed mode with `CEF_DISABLE_GPU=1`.
- `-RenderOffScreen -NoCEF` is not recommended for LiveSync runtime validation because Tick/FTSTicker did not execute in that mode.

## [0.2.2] - 2026-06-09

### Added
- Commercial source-available root license.
- Commercial end-user license agreement: `EULA.md`.
- Blender addon GPL-2.0-or-later component license notice: `Blender_Addon/LICENSE`.

### Changed
- README and release notes now document commercial/source-available licensing.
- Release package now includes commercial licensing terms required for paid distribution.

### Notes
- v0.2.0 and v0.2.1 remain published and untouched.
- v0.2.2 is a license/documentation patch release.
- Runtime sync code is unchanged from v0.2.1.

## [0.2.1] - 2026-06-09

### Added
- Cross-platform installer helper: `install_uelivesync.py`.
- `INSTALL.md` with Windows, Linux, Linux Flatpak, and macOS install instructions.
- Installer source/functional tests for dry-run, backup, force, and destination-exists safety.

### Fixed
- Installer overwrite safety before release: existing destinations now require `--backup` or `--force`.
- `--backup` now preserves existing installs as `.bak-YYYYMMDD-HHMMSS`.
- `--dry-run` performs no filesystem writes.

### Notes
- v0.2.0 remains published and untouched.
- v0.2.1 is a patch release to include the installer helper in the release tag/source archive.
- Runtime sync code is unchanged from v0.2.0.

## [0.2.0] - 2026-06-09

### Added
- FBX mesh handoff production path for selected Blender meshes.
- UE StaticMesh import with StaticMeshActor spawn/update by LiveSync GUID.
- Material slot count logging for FBX imports.
- FBX asset lifecycle diagnostics: created vs replaced imported asset.
- Rename/new asset path diagnostic for possible orphaned imported assets.
- Blender burst packet diagnostics: `burst_packet_count` and `burst_packet_count_peak`.

### Fixed
- Removed redundant `RegisterComponent()` calls in FBX reimport path.
- Fixed FBX scene unit conversion so Blender meters import as UE centimeters.
- Extracted and hardened the FBX importer implementation.

### Validated
- Stage 5 rename/new asset path runtime validation PASS.
- 2m Blender cube imports as 200 × 200 × 200 UE units.
- Large scene streaming benchmark PASS for 50, 100, 250, and 500 objects.
- Queue depth remained 0 and dropped packets remained 0 in Phase 8 benchmark.

### Known limitations
- Imported FBX StaticMesh assets are not automatically deleted.
- Renaming a Blender object can leave the old imported asset orphaned; a diagnostic warning is emitted.
- Reimport may overwrite user-edited imported StaticMesh/material settings.
- PT_Mesh procedural mesh sync remains experimental/debug.
- UE runtime LiveSync requires a GPU/RHI session; NullRHI disables packet processing.

## 2026-05-28 — Decouple Semantic Domains from Transform Gate

### Problem

All semantic event detections (rename, visibility, hierarchy) were inside `if transforms_different(...)` (`sync.py:1068-1180`). This meant these events only emitted when the object's transform also changed.

| Domain | Status Before | Status After |
|--------|--------------|--------------|
| Rename | Only detected on object move | Always detected |
| Visibility | Only detected on object move | Always detected |
| Hierarchy | Only detected on object move | Always detected |
| Collection | Already outside gate | Unchanged |

### Changes

**`Blender_Addon/sync.py`** — moved rename, visibility, hierarchy detection from inside `if transforms_different()` to independent indent-8 scope. Each domain now evaluates every tick:
- Visibility: `obj.hide_get()` diff against `_last_visibility_state`
- Rename: `obj.name` diff against `_last_object_names`
- Hierarchy: `get_parent_guid(obj)` diff against `_last_parent_guid`

Added `[DIAG]` logging for all domains.

**`UE_Plugin/.../UELiveSyncSubsystem.cpp`** — added diagnostic logging:
- `[VISIBILITY][DIAG]` post-apply (actor name + hidden state)
- `[COLLECTION][DIAG]` packet-received + post-apply (registry member count)

**`Docs/KNOWN_BAD_PATTERNS.md`** — added entry #11: "Transform-Gated Semantic Event Detection" documenting the anti-pattern.

### Invariants Preserved

- GI-1 (GUID stable across rename) — unchanged hash derivation
- TF-4/TF-5 (transform authority) — transform path unchanged
- RN-1 (GRenamePersistentLabel authority) — UE side untouched
- HI-1 (parent stable) — hierarchy detection still uses `get_parent_guid()`
- CL-1 (collection idempotent) — collection detection unchanged
- No replay divergence — Blender-side detection only
- No packet format changes
- No networking changes
