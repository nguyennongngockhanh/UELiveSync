# UELiveSync System Requirements

**Version:** v0.2.3

---

## Required Software

| Component | Minimum | Validated |
|-----------|---------|-----------|
| Blender | 4.5+ | 5.1.2 (Flatpak) |
| Unreal Engine | 5.7+ | 5.7.4 |
| Python | 3.6+ | (for installer script only) |

## Validated Test Platform

| Item | Detail |
|------|--------|
| OS | Fedora KDE Linux (44+ validated) |
| GPU | NVIDIA / Vulkan (driver 595.80 validated) |
| UE mode | Windowed, bare command (no SDL/X11/CEF env vars) |
| Network | Local TCP 127.0.0.1:57000 |

## Not Yet Validated

- Windows runtime
- macOS runtime
- Non-NVIDIA Linux GPUs
- UE headless / NullRHI / `-RenderOffScreen -NoCEF` modes

## Recommended UE Launch (Linux)

The stable runtime validation profile on Fedora 44 / NVIDIA 595.80:

```bash
./UnrealEditor <Project>.uproject -windowed -ResX=1280 -ResY=720 -nohighdpi -log
```

Do not use `-NullRHI` or `-RenderOffScreen -NoCEF` — LiveSync networking and/or Tick will not function in those modes.

The previous validation profile (`CEF_DISABLE_GPU=1` with SDL/X11 env vars) is **no longer recommended** — it caused CEF GPU crash cascades. The bare UE command above is the validated launch method for current runtime tests.

## Network / Security

- UELiveSync uses a direct local TCP connection (`127.0.0.1:57000`).
- No cloud service, no external telemetry, no internet access required.
- The default port is **57000**. Ensure it is not blocked by a firewall.
- Do not expose port 57000 to public networks.

## Hardware

- No strict minimum hardware requirements have been established.
- UE and Blender should run comfortably on the target machine (UE editor requires a GPU with RHI support).
- Large scenes benefit from more RAM and GPU memory.

## Related Docs

- [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md)
- [BUYER_QUICK_START.md](BUYER_QUICK_START.md)
- [INSTALL.md](../INSTALL.md)
- [runtime_validation.md](runtime_validation.md)
