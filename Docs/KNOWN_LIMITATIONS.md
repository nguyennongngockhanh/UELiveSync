# UELiveSync Known Limitations

**Version:** v0.2.4 — early-access pre-release.

---

## Platform Validation

UELiveSync has been fully validated only on:

- **OS:** Fedora KDE Linux
- **UE:** 5.7.4
- **Blender:** 5.1.2 (Flatpak)
- **GPU:** NVIDIA / Vulkan

The following platforms have **not** been validated with a full runtime test:

- Windows runtime validation
- macOS runtime validation
- Non-NVIDIA Linux GPUs
- UE headless / NullRHI / `-RenderOffScreen -NoCEF` modes

## Launch Limitation (UE)

- **Do not** use `-NullRHI` for LiveSync — networking is disabled in that mode.
- **Do not** use `-RenderOffScreen -NoCEF` — Tick() and FTSTicker did not execute in validation.
- **Use** windowed UE mode for runtime validation on Linux. The stable launch profile is the bare UE command without the old SDL/CEF env vars:

  ```
  ./UnrealEditor <Project>.uproject -windowed -ResX=1280 -ResY=720 -nohighdpi -log
  ```

- The old validation profile (`CEF_DISABLE_GPU=1` + SDL/X11 env vars) is **no longer recommended** — it caused CEF GPU crash cascades on Fedora 44 / NVIDIA 595.80.

See [SYSTEM_REQUIREMENTS.md](SYSTEM_REQUIREMENTS.md) for the recommended launch profile.

## Feature Validation Status

Phase 10D runtime gaps are **closed** (commit `1ef954a`):

| Feature | Runtime Validated |
|---------|-------------------|
| Interactive visibility sync | ✅ PASS |
| MESH-parent hierarchy sync | ✅ PASS |
| Keyframe visibility channels 9–10 | ✅ PASS |
| Real FBX import/reimport | ✅ PASS |

## FBX Mesh Handoff

- FBX handoff is the **production mesh path**. Procedural `PT_Mesh` sync is experimental/debug.
- Imported FBX StaticMeshes are **not auto-cleaned** on object deletion or rename.
- Renaming a Blender object can orphan the previous imported asset (diagnostic warning is emitted).
- Reimport can overwrite user-edited StaticMesh/material settings.

## Early-Access Notes

- v0.2.4 is an early-access pre-release.
- Buyer should expect continued improvements in asset lifecycle, cleanup, and platform coverage.
- Not yet recommended for production pipelines without prior testing on the target setup.

## Performance (Phase 10A Investigation — 2026-07-06)

Profile instrumentation added to `HandleImport()` in `LiveSyncFBXImporter.cpp` with `FFbxScopePhase` RAII wrappers around each pipeline stage. Tested on Wood_Marble_Premium_Console_Storage_Unit (4206 verts, 7992 tris, 3 material slots, 6 textures) under UE 5.7.4 / Linux / Vulkan.

### Warm-cache results (textures exist under /Game/UELiveSync/Imported/Textures/)

| Phase | Type | Duration |
|-------|------|----------|
| fbx_factory_import | exclusive | ~172 ms |
| sidecar_batch_import | nested | ~350 ms |
| semantic_signature | nested | ~0.1 ms |
| sidecar_manifest_read | nested | ~0.1 ms |
| sidecar_asset_lookup | nested | ~0.0 ms |
| sidecar_result_mapping | nested | ~0.0 ms |
| imported_asset_discovery | exclusive | ~0.0 ms |
| request_parse / path_validation | exclusive | ~0.0 ms |
| **Total (STALL_SUMMARY)** | | **~520 ms** |

Cold DDC clear + sidecache wipe produced identical results (textures persisted in UE Content directory).

### Key finding

The previously reported ~30 s editor stall **only occurs** when UE imports brand-new texture assets that do not yet exist under `/Game/UELiveSync/Imported/Textures/`. This is a one-time cost (texture compression + DDC + asset registry) on the very first sync for each texture. All subsequent incremental syncs — even after clearing DDC — are fast (~520 ms) because the imported texture assets persist in the Content directory.

### Bottleneck at warm

- `sidecar_batch_import` (~350 ms): synchronous `ImportAssetsAutomated` with texture compression. Dominant cost. Each `ImportAssetsAutomated` call blocks the Game Thread.
- `fbx_factory_import` (~172 ms): synchronous FBX reimport. Significant but smaller.

Both are acceptable for incremental sync. Optimization (if desired) would target per-texture compression time or async import.

### Instrumentation note

`FFbxScopePhase` wrappers were added temporarily for this investigation and have been removed. See commit `xxx` for the cleanup. No behavior change from instrumentation.

## Related Docs

- [SYSTEM_REQUIREMENTS.md](SYSTEM_REQUIREMENTS.md)
- [SUPPORT_POLICY.md](SUPPORT_POLICY.md)
- [runtime_validation.md](runtime_validation.md)
