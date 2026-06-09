#!/usr/bin/env python3
"""UELiveSync cross-platform installer helper.

Installs the Blender addon and/or UE plugin to their respective targets.
Compatible with Windows, Linux, and macOS. Uses only the Python standard library.
"""

import argparse
import datetime
import json
import os
import pathlib
import platform
import re
import shutil
import sys
import tempfile
import zipfile


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BLENDER_ADDON_SRC_NAME = "ue_live_sync"
UE_PLUGIN_SRC_NAME = "UELiveSync"

VERSION = (0, 2, 0)
VERSION_NAME = "0.2.0"


def _get_blender_config_base() -> pathlib.Path:
    """Return the base Blender config directory for the current OS."""
    system = platform.system()
    if system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        if not appdata:
            print("ERROR: APPDATA environment variable is not set.", file=sys.stderr)
            sys.exit(1)
        return pathlib.Path(appdata) / "Blender Foundation" / "Blender"
    elif system == "Darwin":
        home = os.environ.get("HOME", "")
        if not home:
            print("ERROR: HOME environment variable is not set.", file=sys.stderr)
            sys.exit(1)
        return pathlib.Path(home) / "Library" / "Application Support" / "Blender"
    else:
        home = os.environ.get("HOME", "")
        if not home:
            print("ERROR: HOME environment variable is not set.", file=sys.stderr)
            sys.exit(1)
        return pathlib.Path(home) / ".config" / "blender"


def _detect_blender_versions(config_base: pathlib.Path) -> list[str]:
    """Detect installed Blender versions under config_base."""
    versions: list[str] = []
    if not config_base.is_dir():
        return versions
    for entry in sorted(config_base.iterdir()):
        if entry.is_dir() and re.match(r"^\d+\.\d+$", entry.name):
            versions.append(entry.name)
    return versions


def _get_blender_install_path(
    version: str, use_flatpak: bool = False
) -> pathlib.Path:
    """Return the install path for the Blender addon."""
    system = platform.system()
    if system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        if not appdata:
            print("ERROR: APPDATA environment variable is not set.", file=sys.stderr)
            sys.exit(1)
        base = pathlib.Path(appdata) / "Blender Foundation" / "Blender"
    elif system == "Darwin":
        home = os.environ.get("HOME", "")
        if not home:
            print("ERROR: HOME environment variable is not set.", file=sys.stderr)
            sys.exit(1)
        base = pathlib.Path(home) / "Library" / "Application Support" / "Blender"
    else:
        if use_flatpak:
            home = os.environ.get("HOME", "")
            if not home:
                print("ERROR: HOME environment variable is not set.", file=sys.stderr)
                sys.exit(1)
            base = pathlib.Path(home) / ".var" / "app" / "org.blender.Blender" / "config" / "blender"
        else:
            home = os.environ.get("HOME", "")
            if not home:
                print("ERROR: HOME environment variable is not set.", file=sys.stderr)
                sys.exit(1)
            base = pathlib.Path(home) / ".config" / "blender"
    return base / version / "scripts" / "addons" / BLENDER_ADDON_SRC_NAME


def _resolve_ue_project_path(raw: str) -> tuple[pathlib.Path, pathlib.Path]:
    """Resolve a --ue-project argument to (uproject_path, project_dir)."""
    p = pathlib.Path(raw).resolve()
    if p.suffix == ".uproject":
        if not p.is_file():
            print(f"ERROR: .uproject file does not exist: {p}", file=sys.stderr)
            sys.exit(1)
        return p, p.parent
    else:
        found = [f for f in p.iterdir() if f.is_file() and f.suffix == ".uproject"]
        if len(found) == 0:
            print(f"ERROR: No .uproject file found in {p}", file=sys.stderr)
            sys.exit(1)
        if len(found) > 1:
            lines = ["ERROR: Multiple .uproject files found in " + str(p) + ":"]
            for f in found:
                lines.append("  " + str(f.name))
            for line in lines:
                print(line, file=sys.stderr)
            sys.exit(1)
        return found[0], p


def _validate_ue_project(uproject_path: pathlib.Path) -> dict:
    """Validate that uproject_path is valid JSON and return its contents."""
    try:
        with open(uproject_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        print("ERROR: " + str(uproject_path) + " is not valid JSON: " + str(exc), file=sys.stderr)
        sys.exit(1)
    return data


def _parse_blender_version_from_source(source_dir: pathlib.Path) -> str:
    """Parse bl_info version from ue_live_sync/__init__.py."""
    init_py = source_dir / BLENDER_ADDON_SRC_NAME / "__init__.py"
    if not init_py.is_file():
        print("ERROR: Source missing: " + str(init_py), file=sys.stderr)
        sys.exit(1)
    text = init_py.read_text(encoding="utf-8")
    match = re.search(r'"version"\s*:\s*\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\)', text)
    if not match:
        print("WARNING: Could not parse bl_info version from " + str(init_py), file=sys.stderr)
        return "(unknown)"
    return match.group(1) + "." + match.group(2) + "." + match.group(3)


def _parse_ue_version_name(source_dir: pathlib.Path) -> str:
    """Parse VersionName from UELiveSync/UELiveSync.uplugin."""
    uplugin = source_dir / UE_PLUGIN_SRC_NAME / "UELiveSync.uplugin"
    if not uplugin.is_file():
        print("ERROR: Source missing: " + str(uplugin), file=sys.stderr)
        sys.exit(1)
    text = uplugin.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
        return str(data.get("VersionName", "(unknown)"))
    except json.JSONDecodeError:
        print("WARNING: Could not parse version from " + str(uplugin), file=sys.stderr)
        return "(unknown)"


def _safety_check_dest(dest: pathlib.Path) -> None:
    """Refuse obviously dangerous destination paths."""
    parts = list(dest.parts)
    if len(parts) <= 1:
        print("ERROR: Destination path is too shallow (root-level): " + str(dest), file=sys.stderr)
        sys.exit(1)
    if dest.resolve() == pathlib.Path("/"):
        print("ERROR: Cannot install to filesystem root.", file=sys.stderr)
        sys.exit(1)


def _backup_existing(dest: pathlib.Path) -> pathlib.Path:
    """Move dest to a timestamped backup and return the backup path."""
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = pathlib.Path(str(dest) + ".bak-" + ts)
    shutil.move(str(dest), str(backup))
    return backup


def _handle_existing_destination(dest, force, backup, dry_run):
    """Handle an existing destination path safely.

    If backup is True: move dest to .bak-YYYYMMDD-HHMMSS.
    If force is True (and not backup): remove dest via shutil.rmtree.
    If neither: print error and exit non-zero.
    If dry_run: print what would happen, perform no writes.
    Backup takes precedence over force when both are set.
    """
    if not dest.exists():
        return

    if dry_run:
        print("[DRY-RUN] Destination exists: " + str(dest))
        print("[DRY-RUN] Would require --backup or --force to replace.")
        return

    _safety_check_dest(dest)

    if backup:
        backup_path = _backup_existing(dest)
        print("[WRITE] Backed up existing destination: " + str(dest) + " -> " + str(backup_path))
    elif force:
        shutil.rmtree(str(dest))
        print("[WRITE] Removed existing destination: " + str(dest))
    else:
        print(
            "ERROR: Destination already exists: " + str(dest) + "\n"
            "Use --backup to preserve it, or --force to replace it.",
            file=sys.stderr,
        )
        sys.exit(1)


def _find_source_root(args) -> pathlib.Path:
    """Determine the source root directory."""
    if args.source_root:
        p = pathlib.Path(args.source_root).resolve()
        if not p.is_dir():
            print("ERROR: --source-root does not point to a directory: " + str(p), file=sys.stderr)
            sys.exit(1)
        return p
    return pathlib.Path(__file__).resolve().parent


def _detect_blender_version(config_base, requested):
    """Detect or validate the Blender version to target. Returns (version, used_request)."""
    if requested:
        candidate = config_base / requested
        if candidate.is_dir():
            return requested, True
        else:
            print(
                "WARNING: Requested Blender version " + str(requested)
                + " not found at " + str(candidate),
                file=sys.stderr,
            )
            print("         Continuing with installed versions only.", file=sys.stderr)

    versions = _detect_blender_versions(config_base)
    if len(versions) == 0:
        print(
            "ERROR: No installed Blender versions found under:\n         " + str(config_base),
            file=sys.stderr,
        )
        print("         Expected paths:", file=sys.stderr)
        system = platform.system()
        if system == "Windows":
            print("           %APPDATA%\\Blender Foundation\\Blender\\<version>\\", file=sys.stderr)
        elif system == "Darwin":
            print("           ~/Library/Application Support/Blender/<version>/", file=sys.stderr)
        else:
            print("           ~/.config/blender/<version>/", file=sys.stderr)
            print("           ~/.var/app/org.blender.Blender/config/blender/<version>/", file=sys.stderr)
        sys.exit(1)
    if len(versions) == 1:
        return versions[0], False
    print("Multiple Blender versions detected: " + ", ".join(versions))
    print("Pass --blender-version to select one.", file=sys.stderr)
    sys.exit(1)


def _do_install_blender_addon(source_root, version, use_flatpak, force, backup, dry_run, verbose):
    """Install the Blender addon."""
    config_base = _get_blender_config_base()
    detected_version, _ = _detect_blender_version(config_base, version)

    dest = _get_blender_install_path(detected_version, use_flatpak)
    print("Blender config base: " + str(config_base))
    print("Detected version: " + detected_version)
    print("Install path: " + str(dest))

    source_dir_path = None
    # Try repo layout first
    repo_candidate = source_root / "Blender_Addon"
    if repo_candidate.is_dir():
        init_py = repo_candidate / "__init__.py"
        sync_py = repo_candidate / "sync.py"
        network_py = repo_candidate / "network.py"
        if init_py.is_file() and sync_py.is_file() and network_py.is_file():
            source_dir_path = repo_candidate
        else:
            source_dir_path = repo_candidate
    else:
        release_candidate = source_root / BLENDER_ADDON_SRC_NAME
        if release_candidate.is_dir():
            source_dir_path = release_candidate
        else:
            print(
                "ERROR: Cannot find Blender addon source.\n"
                "         Looked in:\n"
                "           " + str(repo_candidate) + "\n"
                "           " + str(release_candidate),
                file=sys.stderr,
            )
            sys.exit(1)

    ver = _parse_blender_version_from_source(source_root)
    print("Addon version: " + ver)
    expected = VERSION
    actual = tuple(int(x) for x in ver.split("."))
    if actual != expected:
        print(
            "WARNING: Expected bl_info version " + str(expected) + ", got " + str(actual),
            file=sys.stderr,
        )

    if dry_run:
        _handle_existing_destination(dest, force, backup, dry_run=True)
        _safety_check_dest(dest)
        print("[DRY-RUN] Would create directories and copy:")
        print("           " + str(source_dir_path) + " -> " + str(dest))
        return

    _handle_existing_destination(dest, force, backup, dry_run=False)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(source_dir_path), str(dest))
    print("[WRITE] Installed Blender addon to: " + str(dest))


def _do_install_ue_plugin(source_root, uproject_path, project_dir, force, backup, dry_run, verbose):
    """Install the UE plugin."""
    _validate_ue_project(uproject_path)
    print("UE project: " + str(uproject_path))

    dest = project_dir / "Plugins" / UE_PLUGIN_SRC_NAME
    print("Install path: " + str(dest))

    source_dir_path = None
    # Try repo layout first
    repo_candidate = source_root / "UE_Plugin" / UE_PLUGIN_SRC_NAME
    if repo_candidate.is_dir():
        uplugin = repo_candidate / "UELiveSync.uplugin"
        src_sub = repo_candidate / "Source"
        if uplugin.is_file() and src_sub.is_dir():
            source_dir_path = repo_candidate
    if source_dir_path is None:
        release_candidate = source_root / UE_PLUGIN_SRC_NAME
        if release_candidate.is_dir():
            uplugin = release_candidate / "UELiveSync.uplugin"
            src_sub = release_candidate / "Source"
            if uplugin.is_file() and src_sub.is_dir():
                source_dir_path = release_candidate
        if source_dir_path is None:
            print(
                "ERROR: Cannot find UE plugin source.\n"
                "         Looked in:\n"
                "           " + str(repo_candidate) + "\n"
                "           " + str(release_candidate),
                file=sys.stderr,
            )
            sys.exit(1)

    ver = _parse_ue_version_name(source_root)
    print("Plugin version: " + ver)
    if ver != VERSION_NAME:
        print(
            "WARNING: Expected VersionName " + VERSION_NAME + ", got " + ver,
            file=sys.stderr,
        )

    if dry_run:
        _handle_existing_destination(dest, force, backup, dry_run=True)
        _safety_check_dest(dest)
        print("[DRY-RUN] Would create directories and copy:")
        print("           " + str(source_dir_path) + " -> " + str(dest))
        return

    _handle_existing_destination(dest, force, backup, dry_run=False)
    _safety_check_dest(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(source_dir_path), str(dest))
    print("[WRITE] Installed UE plugin to: " + str(dest))
def main():
    """Entry point for the installer."""
    parser = argparse.ArgumentParser(
        description="UELiveSync installer helper (Blender addon + UE plugin).",
        epilog="Examples:\n"
        "  python install_uelivesync.py --blender-addon\n"
        "  python install_uelivesync.py --ue-project /path/to/Project.uproject\n"
        "  python install_uelivesync.py --all --ue-project /path/to/Project.uproject\n"
        "  python install_uelivesync.py --dry-run --all --ue-project /path/to/Project.uproject\n",
    )
    parser.add_argument("--blender-addon", action="store_true", help="Install Blender addon")
    parser.add_argument("--ue-project", default=None, help="Path to .uproject or project directory")
    parser.add_argument("--all", action="store_true", help="Install both Blender addon and UE plugin")
    parser.add_argument("--blender-version", default=None, help="Blender version to target (e.g. '5.1')")
    parser.add_argument("--dry-run", action="store_true", help="Show planned operations without writing")
    parser.add_argument("--force", action="store_true", help="Overwrite existing destination without prompt")
    parser.add_argument("--backup", action="store_true", help="Backup existing destination before overwriting")
    parser.add_argument("--source-root", default=None, help="Directory containing Blender_Addon/ and UE_Plugin/")
    parser.add_argument("--flatpak", action="store_true", help="Use Flatpak Blender config path on Linux")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")

    args = parser.parse_args()

    # Validate at least one target
    if not args.blender_addon and not args.ue_project and not args.all:
        parser.print_help()
        print("\nERROR: No install target specified. Use --blender-addon, --ue-project, or --all.", file=sys.stderr)
        sys.exit(1)

    source_root = _find_source_root(args)
    print("Source root: " + str(source_root))
    print("Python: " + sys.version.split()[0])
    print("Platform: " + platform.system() + " " + platform.release())
    print()

    # Blender addon install
    if args.blender_addon or args.all:
        print("=== Blender Addon Install ===")
        _do_install_blender_addon(source_root, args.blender_version, args.flatpak, args.force, args.backup, args.dry_run, args.verbose)
        print()

    # UE plugin install
    if args.ue_project or args.all:
        print("=== UE Plugin Install ===")
        uproject_path, project_dir = _resolve_ue_project_path(args.ue_project)
        _do_install_ue_plugin(source_root, uproject_path, project_dir, args.force, args.backup, args.dry_run, args.verbose)
        print()

    print("Done.")


if __name__ == "__main__":
    main()
