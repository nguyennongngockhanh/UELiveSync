# Playbook: Safe UE Build

Mode: BUILD only when explicitly requested.

Allowed fixed commands:

Sync plugin:
SRC="/home/nguyennongngockhanh/Projects/UELiveSync/UE_Plugin/UELiveSync"
DST="/home/nguyennongngockhanh/Documents/Unreal Projects/ProjectTemplate/Plugins/UELiveSync"
rm -rf "$DST"
mkdir -p "$DST"
rsync -a --delete \
  --exclude='Binaries/' \
  --exclude='Intermediate/' \
  --exclude='Saved/' \
  "$SRC/" "$DST/"

Build:
"/home/nguyennongngockhanh/Unreal/UE5.7.4/Engine/Build/BatchFiles/Linux/Build.sh" \
  ProjectTemplateEditor Linux Development \
  "/home/nguyennongngockhanh/Documents/Unreal Projects/ProjectTemplate/ProjectTemplate.uproject" \
  -waitmutex

Do not search for UnrealBuildTool.
Do not search for engine paths.
Do not kill unrelated processes unless explicitly requested.
Do not launch UE unless explicitly requested.
Do not commit unless explicitly requested.
