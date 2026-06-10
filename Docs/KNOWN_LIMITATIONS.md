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

## Related Docs

- [SYSTEM_REQUIREMENTS.md](SYSTEM_REQUIREMENTS.md)
- [SUPPORT_POLICY.md](SUPPORT_POLICY.md)
- [runtime_validation.md](runtime_validation.md)
