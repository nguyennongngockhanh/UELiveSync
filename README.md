# UELiveSync

Blender ↔ Unreal Engine lightweight live sync over direct TCP.

**Latest release: [v0.2.3](https://github.com/nguyennongngockhanh/UELiveSync/releases/tag/v0.2.3)** — early-access pre-release.

> **main branch** includes post-v0.2.3 runtime validation updates and sequencer reconnect fixes. See [RELEASE_NOTES.md](RELEASE_NOTES.md) for unreleased changes.

## Supported Versions

- **Unreal Engine:** 5.7+
- **Blender:** 4.5+ (validated with Blender 5.1 Flatpak)

## Feature Summary

- Transform sync
- Object create/delete sync
- Rename sync
- Visibility sync
- Hierarchy/parenting sync
- Collection membership sync
- Material slot sync
- Camera sync
- Timeline/playback sync
- Sequencer keyframe sync, including visibility BoolTracks
- FBX mesh handoff for production mesh topology

## Production Mesh Path

- **FBX handoff** is the production mesh topology path.
- **PT_Mesh procedural** geometry sync remains experimental/debug.

## Quick Install

1. Download the [v0.2.3 release](https://github.com/nguyennongngockhanh/UELiveSync/releases/tag/v0.2.3) or follow [INSTALL.md](INSTALL.md).
2. Use the install helper for automated setup:

```bash
python install_uelivesync.py --dry-run --all --ue-project "/path/to/Project.uproject" --blender-version 5.1
```

See [INSTALL.md](INSTALL.md) for full Windows, Linux, Flatpak, and macOS instructions.

## Quick Workflow

1. Install UE plugin to `<Project>/Plugins/UELiveSync/`
2. Install Blender addon as `ue_live_sync`
3. Open UE project with plugin enabled
4. Start Sync from Blender View3D sidebar
5. Move objects for live transform sync
6. Use **Sync Selected Mesh to UE (FBX)** for production mesh topology sync

## Performance Validation

- Large scene benchmark up to 500 objects.
- Create burst peak: 3 packets/tick.
- Move burst peak: 1 packet/tick.
- Queue depth: 0. Dropped packets: 0.

## Known Limitations

- Imported FBX StaticMesh assets are not auto-cleaned.
- Renaming Blender objects can orphan previous imported assets; diagnostics warn.
- Reimport can overwrite user-edited imported StaticMesh/material settings.
- Runtime LiveSync validation requires a GPU/RHI UE session; NullRHI disables packet processing.
- PT_Mesh procedural path is experimental/debug.

## Documentation

- [INSTALL.md](INSTALL.md) — Installation guide
- [RELEASE_NOTES.md](RELEASE_NOTES.md) — Feature details and known issues
- [CHANGELOG.md](CHANGELOG.md) — Release history
- [v0.2.3 Release](https://github.com/nguyennongngockhanh/UELiveSync/releases/tag/v0.2.3)

## Buyer Documentation

- [BUYER_QUICK_START.md](Docs/BUYER_QUICK_START.md) — Get started in 5 minutes
- [SYSTEM_REQUIREMENTS.md](Docs/SYSTEM_REQUIREMENTS.md) — Tested platforms and launch profile
- [KNOWN_LIMITATIONS.md](Docs/KNOWN_LIMITATIONS.md) — Current product limitations
- [LICENSE_FAQ.md](Docs/LICENSE_FAQ.md) — Plain-English license answers
- [SUPPORT_POLICY.md](Docs/SUPPORT_POLICY.md) — Support terms and contact

## License

License: Commercial/source-available. See [LICENSE](LICENSE) and [EULA.md](EULA.md).

The Blender addon component is distributed with GPL-2.0-or-later compatible terms due to Blender integration; see [Blender_Addon/LICENSE](Blender_Addon/LICENSE). The UE plugin and commercial package are governed by the commercial EULA.
