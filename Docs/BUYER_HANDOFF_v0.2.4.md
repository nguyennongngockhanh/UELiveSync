# UELiveSync v0.2.4 — Buyer / Private Tester Handoff

**Status:** Pre-release / private validation build.
**Release:** https://github.com/nguyennongngockhanh/UELiveSync/releases/tag/v0.2.4

---

## Assets

| File | Description |
|------|-------------|
| `UELiveSync-Blender-Addon-v0.2.4.zip` | Blender addon (folder: `ue_live_sync/`) |
| `UELiveSync-UE-Plugin-v0.2.4.zip` | UE plugin (folder: `UELiveSync/`) |
| `UELiveSync-v0.2.4-SHA256SUMS.txt` | SHA-256 checksums for integrity verification |

### Verify Integrity

```bash
sha256sum -c UELiveSync-v0.2.4-SHA256SUMS.txt
```

Both zips should report `OK`.

---

## What Is Included

- **Transform sync** — object position, rotation, scale streamed from Blender to UE in real time.
- **Create/delete sync** — objects added or removed in Blender reflected in UE.
- **Visibility sync** — show/hide state synchronised.
- **Rename sync** — object name changes propagate.
- **Hierarchy/parenting sync** — parent-child relationships maintain in UE (MESH parent validated).
- **Collection membership sync** — Blender collections mapped to UE folders/outliner.
- **Material slot sync** — material slot list synchronised.
- **Timeline/playback sync** — Blender playback controls affect UE Sequencer.
- **Sequencer keyframe sync** — transform and visibility keyframes applied to Sequencer tracks.
- **FBX mesh handoff** — selected Blender meshes exported as FBX and imported as UE `UStaticMesh` assets.

---

## What Has Been Runtime-Validated

All validation performed on **Fedora 44 + UE 5.7.4 + Blender 5.1.2 Flatpak + NVIDIA 595.80**.

| Feature | Status |
|---------|--------|
| Object spawn/create | PASS |
| Transform sync (move/rotate/scale) | PASS |
| Rename sync | PASS |
| Visibility hide/show | PASS |
| MESH-parent hierarchy attach/detach | PASS |
| FBX import (real FBX file) | PASS |
| FBX reimport (same GUID update) | PASS |
| Sequencer keyframes (transform + visibility) | PASS |
| Burst / large scene (up to 500 objects) | PASS |
| No queue backlog, no dropped packets | PASS |

### Not Yet Validated on Other Platforms

- Windows and macOS have **not** been runtime-tested.
- Different GPU vendors (AMD, Intel) have **not** been tested.
- Blender native (non-Flatpak) on Linux has not been separately tested.
- Engine-level UE plugin install (vs project-level) has not been tested.

---

## Recommended UE Launch Profile

```bash
./UnrealEditor <Project>.uproject -windowed -ResX=1280 -ResY=720 -nohighdpi -log
```

- Do **not** use `-NullRHI` — networking is disabled in that mode.
- Do **not** use `-RenderOffScreen -NoCEF` — Tick/FTSTicker did not execute in validation.

---

## Basic Install Flow

1. Download the three release assets from the [v0.2.4 release page](https://github.com/nguyennongngockhanh/UELiveSync/releases/tag/v0.2.4).
2. Verify integrity with `sha256sum -c UELiveSync-v0.2.4-SHA256SUMS.txt`.
3. **Blender addon**: unzip → place `ue_live_sync/` folder in your Blender `scripts/addons/` directory → enable in Preferences.
4. **UE plugin**: unzip → place `UELiveSync/` folder in `<Project>/Plugins/` → enable in Plugins menu.

See [INSTALL.md](../INSTALL.md) for platform-specific paths and the automated installer.

---

## Basic Test Flow

1. Launch UE project with plugin enabled.
2. In Blender, open the UELiveSync panel (3D View sidebar) and start sync.
3. Create a cube in Blender — it should appear in UE.
4. Move, rotate, scale the cube — UE actor should follow.
5. Rename the cube in Blender — UE actor name should update.
6. Hide/show the cube in Blender — UE actor visibility should change.
7. Parent a child object (e.g. sphere) to the cube — hierarchy should appear in UE.
8. **FBX mesh**: Select a mesh in Blender, use **Sync Selected Mesh to UE (FBX)** — UE should import a `UStaticMesh` asset and spawn/update an actor.

---

## Key Known Limitations

See [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) for the full list.

- Imported FBX `UStaticMesh` assets are **not auto-cleaned** on object deletion or rename.
- Renaming a Blender object can orphan previously imported assets (diagnostic warning is emitted).
- Reimport can overwrite user-edited imported StaticMesh/material settings.
- `PT_Mesh` procedural sync is **experimental/debug** — FBX handoff is the production mesh path.
- Runtime validation requires a GPU/RHI UE session — NullRHI disables packet processing.
- Linux only — Windows and macOS install instructions are provided but runtime-untested.

---

## Feedback Requested

Send structured feedback including:

- OS / Blender version / UE version / GPU + driver version
- Steps to reproduce any issue encountered
- UE logs from the session (`Saved/Logs/`) or console output
- Blender console output if relevant
- Screenshots or screen recordings if helpful
- General impressions: missing features, confusing UI, unexpected behaviour

---

## Support Expectations

- This is a **pre-release / private validation build**.
- Bug reports and feature requests are welcome but response time is not guaranteed.
- No SLA or production support is implied.
- Breaking changes between pre-release versions are possible.
- See [SUPPORT_POLICY.md](SUPPORT_POLICY.md) for detailed terms.

---

## Related Documentation

- [BUYER_QUICK_START.md](BUYER_QUICK_START.md) — 5-minute setup guide
- [SYSTEM_REQUIREMENTS.md](SYSTEM_REQUIREMENTS.md) — validated platforms
- [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) — current limitations
- [SUPPORT_POLICY.md](SUPPORT_POLICY.md) — support terms
- [INSTALL.md](../INSTALL.md) — detailed installation
- [RELEASE_NOTES.md](../RELEASE_NOTES.md) — what changed in v0.2.4
