# UELiveSync Installation Guide

## Supported Platforms

- **Windows**
- **Linux** (native and Flatpak)
- **macOS**

## Minimum Versions

- **Unreal Engine:** 5.7+
- **Blender:** 4.5+ (validated with Blender 5.1 Flatpak)

---

## Manual Install

### Blender Addon

Unzip `UELiveSync-Blender-Addon-v0.2.0.zip` and place the `ue_live_sync/` folder in the appropriate path for your platform:

**Windows:**
```
%APPDATA%\Blender Foundation\Blender\<version>\scripts\addons\ue_live_sync\
```

**Linux (native):**
```
~/.config/blender/<version>/scripts/addons/ue_live_sync/
```

**Linux (Flatpak):**
```
~/.var/app/org.blender.Blender/config/blender/<version>/scripts/addons/ue_live_sync/
```

**macOS:**
```
~/Library/Application Support/Blender/<version>/scripts/addons/ue_live_sync/
```

> **Important:** The folder name **must** be `ue_live_sync` (with underscore).
> The addon will not appear in Blender if the folder name is wrong.

### UE Plugin

Unzip `UELiveSync-UE-Plugin-v0.2.0.zip` and place the `UELiveSync/` folder in your project:

```
<Project>/Plugins/UELiveSync/
```

> **Do not** install into `<Engine>/Plugins/` unless you intentionally want an engine-level plugin.
> Engine-level install is advanced and manual only.

---

## Python Installer

The installer (`install_uelivesync.py`) automates installation for Blender and UE plugin.

### Prerequisites

- Python 3.6+ (no external dependencies)
- Access to the `UELiveSync-Blender-Addon-v0.2.0.zip` or the source repo

### Basic Usage

```bash
# Install Blender addon only
python install_uelivesync.py --blender-addon

# Install UE plugin to a specific project
python install_uelivesync.py --ue-project "/path/to/MyProject.uproject"

# Install both (requires --ue-project)
python install_uelivesync.py --all --ue-project "/path/to/MyProject.uproject"

# Dry run (no writes)
python install_uelivesync.py --dry-run --all --ue-project "/path/to/MyProject.uproject"

# Specify Blender version
python install_uelivesync.py --blender-addon --blender-version 5.1
```

### Flatpak

```bash
python install_uelivesync.py --blender-addon --flatpak --blender-version 5.1
```

### CLI Options

| Flag | Description |
|------|-------------|
| `--blender-addon` | Install the Blender addon |
| `--ue-project PATH` | Path to `.uproject` or project directory |
| `--all` | Install both Blender addon and UE plugin |
| `--blender-version VER` | Target Blender version (e.g. `5.1`) |
| `--dry-run` | Show planned operations without writing |
| `--force` | Overwrite existing destination without prompt |
| `--backup` | Create `.bak-YYYYMMDD-HHMMSS` backup before overwriting |
| `--source-root PATH` | Directory containing `Blender_Addon/` and `UE_Plugin/UELiveSync/` |
| `--flatpak` | Use Flatpak Blender config path on Linux |
| `--verbose` | Enable verbose output |

---

## Safety Notes

- **Backup:** With `--backup`, existing destination is moved to `<path>.bak-YYYYMMDD-HHMMSS` before overwriting.
- **Force:** With `--force`, existing destination is removed without backup. Use cautiously.
- **Dry-run:** `--dry-run` performs zero filesystem writes and prints planned operations.
- **No Engine-level install:** The installer never installs to `<Engine>/Plugins/` by default.
- **No forced deletion:** Parent directories (e.g. `Plugins/`) are never deleted or moved.

---

## Troubleshooting

| Issue | Resolution |
|-------|-----------|
| Addon not visible in Blender | Verify folder name is `ue_live_sync` (with underscore). Check you placed it in the correct `scripts/addons/` directory for your platform. |
| UE cannot find plugin | Verify the path is `<Project>/Plugins/UELiveSync/` and that `UELiveSync.uplugin` is present. |
| NullRHI error in UE logs | LiveSync requires a GPU/RHI session. Disable NullRHI in your UE project. |
| Object appears 100x too small | Verify the UE plugin build includes `bConvertSceneUnit = true`. |
| Default port issue | LiveSync uses port `57000` by default. Ensure the port is not blocked by a firewall. |

---

## Release Assets

| Asset | Description |
|-------|-------------|
| `UELiveSync-Blender-Addon-v0.2.0.zip` | Blender addon (folder: `ue_live_sync/`) |
| `UELiveSync-UE-Plugin-v0.2.0.zip` | UE plugin (folder: `UELiveSync/`) |
| `UELiveSync-v0.2.0-SHA256SUMS.txt` | SHA-256 checksums for verification |

GitHub Release: https://github.com/nguyennongngockhanh/UELiveSync/releases/tag/v0.2.0
