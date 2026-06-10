# UELiveSync Known Limitations

**Version:** v0.2.3 — early-access pre-release.

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
- **Use** windowed UE mode with `CEF_DISABLE_GPU=1` for runtime validation on Linux.

See [SYSTEM_REQUIREMENTS.md](SYSTEM_REQUIREMENTS.md) for the recommended launch profile.

## Feature Gaps (Not Yet Validated at Runtime)

| Feature | Status |
|---------|--------|
| Interactive visibility sync | Validated in Blender driver (not interactive) |
| MESH-parent hierarchy sync | Not tested with EMPTY parent |
| Keyframe visibility channels 9–10 | Not tested at runtime |
| Real FBX import/reimport | Not tested with an actual FBX file |

## FBX Mesh Handoff

- FBX handoff is the **production mesh path**. Procedural `PT_Mesh` sync is experimental/debug.
- Imported FBX StaticMeshes are **not auto-cleaned** on object deletion or rename.
- Renaming a Blender object can orphan the previous imported asset (diagnostic warning is emitted).
- Reimport can overwrite user-edited StaticMesh/material settings.

## Early-Access Notes

- v0.2.3 is an early-access pre-release.
- Buyer should expect continued improvements in asset lifecycle, cleanup, and platform coverage.
- Not yet recommended for production pipelines without prior testing on the target setup.

## Related Docs

- [SYSTEM_REQUIREMENTS.md](SYSTEM_REQUIREMENTS.md)
- [SUPPORT_POLICY.md](SUPPORT_POLICY.md)
- [runtime_validation.md](runtime_validation.md)
