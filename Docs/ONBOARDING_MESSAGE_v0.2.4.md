# UELiveSync v0.2.4 — Onboarding Message (Private Tester / Buyer)

---

Hi,

Thank you for your interest in UELiveSync. This is a **pre-release / private validation build** of the Blender ↔ Unreal Engine live sync system.

## Release

**v0.2.4** is available here:
https://github.com/nguyennongngockhanh/UELiveSync/releases/tag/v0.2.4

## What's Included

The release contains three assets:

- `UELiveSync-Blender-Addon-v0.2.4.zip` — Blender addon
- `UELiveSync-UE-Plugin-v0.2.4.zip` — UE plugin
- `UELiveSync-v0.2.4-SHA256SUMS.txt` — checksums to verify integrity

After downloading, verify with:

```bash
sha256sum -c UELiveSync-v0.2.4-SHA256SUMS.txt
```

Both zips should report OK.

## Quick Install

1. **Blender addon**: unzip → place the `ue_live_sync/` folder in your Blender `scripts/addons/` directory → enable in Preferences.
2. **UE plugin**: unzip → place the `UELiveSync/` folder in your project's `Plugins/` directory → enable.

See the [INSTALL.md](https://github.com/nguyennongngockhanh/UELiveSync/blob/main/INSTALL.md) for platform-specific paths and the automated installer.

## Launch UE

Windowed mode is required. Recommended launch:

```bash
./UnrealEditor <Project>.uproject -windowed -ResX=1280 -ResY=720 -nohighdpi -log
```

Do not use `-NullRHI` or `-RenderOffScreen -NoCEF` — these modes disable networking or Tick and will not work with LiveSync.

## What Has Been Tested

Runtime validation has been performed on **Fedora 44 + UE 5.7.4 + Blender 5.1.2 Flatpak + NVIDIA 595.80**. The following features passed end-to-end testing:

- Object spawn, delete, transform, rename
- Visibility show/hide
- MESH-parent hierarchy
- FBX import/reimport (real FBX files imported as `UStaticMesh` assets)
- Sequencer keyframes (transform + visibility BoolTracks)
- Scene load up to 500 objects with no packet loss

**Not every platform or GPU has been tested.** If you encounter issues on your setup, your feedback will help improve compatibility.

## About the Mesh Pipeline

The production mesh sync path is **FBX handoff** — select a mesh in Blender and use the **Sync Selected Mesh to UE (FBX)** operator. The procedural mesh sync (`PT_Mesh`) is experimental/debug and not recommended for production use.

## Known Limitations

- Imported FBX assets are not automatically cleaned up when a Blender object is deleted or renamed.
- Renaming a Blender object can orphan the previous imported asset (a diagnostic warning is shown).
- Reimport can overwrite any user edits made to the imported StaticMesh or its materials.
- See the [KNOWN_LIMITATIONS.md](https://github.com/nguyennongngockhanh/UELiveSync/blob/main/Docs/KNOWN_LIMITATIONS.md) for the full list.

## Feedback

Please send structured feedback including:

- Your OS, Blender version, UE version, GPU model and driver version
- Exact steps to reproduce any issue
- UE log output (from Output Log or `Saved/Logs/`)
- Blender console output if relevant
- Screenshots or video if helpful

## What to Expect

- This is a **pre-release build** — bugs, missing features, and breaking changes between versions are possible.
- Response time for issues is not guaranteed.
- No production SLA or support contract is implied.

Thank you for helping validate UELiveSync. Your feedback is valuable.

— The UELiveSync team
