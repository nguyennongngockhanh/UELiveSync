# UELiveSync Buyer Quick Start

**Release:** v0.2.4

This guide gets you from download to a working live sync between Blender and Unreal Engine.

---

## 1. Download

Download the three release assets from the [v0.2.4 release page](https://github.com/nguyennongngockhanh/UELiveSync/releases/tag/v0.2.4):

| File | Description |
|------|-------------|
| `UELiveSync-Blender-Addon-v0.2.4.zip` | Blender addon |
| `UELiveSync-UE-Plugin-v0.2.4.zip` | UE plugin |
| `UELiveSync-v0.2.4-SHA256SUMS.txt` | Integrity checksums |

## 2. Verify Integrity

```bash
sha256sum -c UELiveSync-v0.2.4-SHA256SUMS.txt
```

Both zips should report `OK`.

## 3. Install Blender Addon

```bash
unzip UELiveSync-Blender-Addon-v0.2.4.zip
```

Copy the `ue_live_sync/` folder to your Blender addons directory:

| Platform | Path |
|----------|------|
| Linux (native) | `~/.config/blender/<version>/scripts/addons/ue_live_sync/` |
| Linux (Flatpak) | `~/.var/app/org.blender.Blender/config/blender/<version>/scripts/addons/ue_live_sync/` |
| Windows | `%APPDATA%\Blender Foundation\Blender\<version>\scripts\addons\ue_live_sync\` |
| macOS | `~/Library/Application Support/Blender/<version>/scripts/addons/ue_live_sync/` |

Then in Blender: **Edit → Preferences → Add-ons** → search `ue live sync` → enable.

See [INSTALL.md](../INSTALL.md) for detailed instructions and the automated installer.

## 4. Install UE Plugin

```bash
unzip UELiveSync-UE-Plugin-v0.2.4.zip
```

Copy the `UELiveSync/` folder into your UE project's `Plugins/` directory:

```
<YourProject>/Plugins/UELiveSync/
```

Open or restart the project. The plugin loads automatically.

## 5. First Sync Test

1. **Launch UE** with your project. Use windowed mode:
   ```bash
   ./UnrealEditor <Project>.uproject -windowed -ResX=1280 -ResY=720 -nohighdpi -log
   ```
   (On Linux; see [SYSTEM_REQUIREMENTS.md](SYSTEM_REQUIREMENTS.md) for details.)

2. **In Blender**: Click the **Start Sync** button in the 3D View sidebar (tab: UE Sync). The status should show "Connected".

3. **Move the default Cube** in Blender. A matching cube actor should appear and update in the UE viewport.

4. **Add more objects** in Blender. They appear in UE automatically.

---

## Next Steps

- [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) — current product limitations
- [LICENSE_FAQ.md](LICENSE_FAQ.md) — plain-English license answers
- [SUPPORT_POLICY.md](SUPPORT_POLICY.md) — how to get help
- [SYSTEM_REQUIREMENTS.md](SYSTEM_REQUIREMENTS.md) — tested platforms and hardware
- [INSTALL.md](../INSTALL.md) — full install reference
