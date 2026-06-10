# UELiveSync v0.2.4 — Private Tester Checklist

**Release:** https://github.com/nguyennongngockhanh/UELiveSync/releases/tag/v0.2.4

---

## Preparation

- [ ] Download `UELiveSync-Blender-Addon-v0.2.4.zip`
- [ ] Download `UELiveSync-UE-Plugin-v0.2.4.zip`
- [ ] Download `UELiveSync-v0.2.4-SHA256SUMS.txt`
- [ ] Run `sha256sum -c UELiveSync-v0.2.4-SHA256SUMS.txt` — confirm both zips report `OK`

## Installation

- [ ] **Blender addon**: unzip → place `ue_live_sync/` in `scripts/addons/` for your platform
  - Linux native: `~/.config/blender/<version>/scripts/addons/`
  - Linux Flatpak: `~/.var/app/org.blender.Blender/config/blender/<version>/scripts/addons/`
  - Windows: `%APPDATA%\Blender Foundation\Blender\<version>\scripts\addons\`
  - macOS: `~/Library/Application Support/Blender/<version>/scripts/addons/`
- [ ] Enable addon in Blender: Edit → Preferences → Add-ons → search "ue live sync"
- [ ] **UE plugin**: unzip → place `UELiveSync/` in `<Project>/Plugins/`
- [ ] Open UE project, confirm plugin loads (no errors in Output Log)

## Launch

- [ ] Launch UE: `./UnrealEditor <Project>.uproject -windowed -ResX=1280 -ResY=720 -nohighdpi -log`
- [ ] Confirm `[LogLiveSync]` appears in UE Output Log
- [ ] Confirm listener starts on port 57000

## Basic Sync Test

- [ ] In Blender, open 3D View sidebar → UELiveSync panel → click **Start Sync**
- [ ] Blender log shows `Connected`
- [ ] Create a cube in Blender → appears in UE
- [ ] Delete the cube in Blender → removed from UE
- [ ] Move the cube in Blender → UE actor follows
- [ ] Rotate the cube in Blender → UE rotation matches
- [ ] Scale the cube in Blender → UE scale matches
- [ ] Rename the cube in Blender → UE actor name updates

## Visibility Test

- [ ] Hide the cube in Blender (Viewport or Render) → UE actor becomes hidden
- [ ] Show it again → UE actor becomes visible

## Hierarchy Test

- [ ] Create a sphere, parent it to the cube in Blender
- [ ] Confirm UE outliner shows child attached to parent
- [ ] Move the parent cube → child follows in UE
- [ ] Unparent the child → hierarchy is removed in UE

## FBX Mesh Handoff Test

- [ ] Select the cube (or any mesh) in Blender
- [ ] In UELiveSync panel, click **Sync Selected Mesh to UE (FBX)**
- [ ] UE log shows `[FBX] Created new imported asset` and a `StaticMeshActor` spawns
- [ ] Deselect and re-select the same mesh, click **Sync Selected Mesh to UE (FBX)** again
- [ ] UE log shows `[FBX] Replaced existing imported asset` — no duplicate actor spawns
- [ ] Confirm `LiveSync_GUID` tag is present on the spawned actor

## Sequencer Keyframe Test

- [ ] In Blender, add a keyframe on the cube (location or rotation, frame 1)
- [ ] Move to frame 25, move the cube, add another keyframe
- [ ] In Blender, toggle hide_viewport at frame 1 (visible) and frame 25 (hidden)
- [ ] Confirm UE Sequencer shows transform and visibility keyframes on the binding track
- [ ] Confirm UE log shows `[KEYFRAME] Applied seq=...` with visibility keys applied

---

## Record Your Environment

| Field | Value |
|-------|-------|
| OS | |
| Blender version | |
| UE version | |
| GPU model | |
| GPU driver version | |
| Window manager (Linux) | |
| Install method (Flatpak/native) | |

## Report Issues

For each issue, provide:

- **Steps to reproduce** (be as specific as possible)
- **Expected result**
- **Actual result**
- **UE log excerpts** (from Output Log or `Project/Saved/Logs/`)
- **Blender console output** (Window → Toggle System Console)
- **Screenshots or video** if applicable
- **Whether the issue is reproducible** across restart

**Send feedback to:** (your support contact)
