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
| OS | Fedora KDE Linux |
| GPU | NVIDIA / Vulkan |
| UE mode | Windowed, `CEF_DISABLE_GPU=1` |
| Network | Local TCP 127.0.0.1:57000 |

## Not Yet Validated

- Windows runtime
- macOS runtime
- Non-NVIDIA Linux GPUs
- UE headless / NullRHI / `-RenderOffScreen -NoCEF` modes

## Recommended UE Launch (Linux)

```bash
CEF_DISABLE_GPU=1 ./UnrealEditor <Project>.uproject
```

Use windowed mode. Do not use `-NullRHI` or `-RenderOffScreen -NoCEF` — LiveSync networking and/or Tick will not function in those modes.

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
