# Changelog

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
