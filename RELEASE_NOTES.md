# UELiveSync v0.2.1 Pre-release

License: Commercial/source-available. See [LICENSE](LICENSE) and [EULA.md](EULA.md). Blender addon component has GPL-2.0-or-later compatible terms; see [Blender_Addon/LICENSE](Blender_Addon/LICENSE).

## What Changed Since v0.2.0

- Added cross-platform installer helper: `install_uelivesync.py`.
- Added `INSTALL.md`.
- Installer supports Blender addon install paths for Windows, Linux, Linux Flatpak, and macOS.
- Installer supports UE project plugin install to `<Project>/Plugins/UELiveSync/`.
- Installer includes dry-run, backup, and force safety modes.

## Supported Versions

- **Unreal Engine:** 5.7+
- **Blender:** 4.5+
- **Validated with:** Blender 5.1 Flatpak

## Production-Ready Features

- Transform sync
- Create/delete sync
- Visibility sync
- Rename sync
- Hierarchy sync
- Collection sync
- Material slot sync
- FBX mesh handoff
- Camera sync
- Timeline/playback sync
- Sequencer keyframe sync including visibility BoolTracks

## Install

### Blender addon

**Normal path:**
```
~/.config/blender/<version>/scripts/addons/ue_live_sync/
```

**Flatpak path:**
```
~/.var/app/org.blender.Blender/config/blender/<version>/scripts/addons/ue_live_sync/
```

### UE plugin

```
<Project>/Plugins/UELiveSync/
```

## Basic Workflow

1. Launch UE project with plugin enabled.
2. Start Blender addon sync.
3. Move objects for live transform sync.
4. Use **Sync Selected Mesh to UE (FBX)** for production mesh topology sync.

## Important Limitations

- FBX imported assets are not auto-cleaned.
- Rename can orphan previous imported assets.
- User-edited imported StaticMesh/materials may be overwritten on reimport.
- PT_Mesh procedural sync is experimental/debug.
- Do not use NullRHI for runtime LiveSync validation.

## Troubleshooting

- Default port is **57000**.
- If UE logs `NullRHI editor mode DETECTED`, LiveSync networking is disabled.
- For Blender Flatpak, install addon under `.var/app`.
- If object appears 100× too small, verify build includes `bConvertSceneUnit = true`.
