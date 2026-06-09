# Playbook: Runtime Capture

Mode: RUNTIME VALIDATION only when explicitly requested.

Allowed:
- launch UE using fixed UnrealEditor path
- grep ProjectTemplate.log
- capture screenshot with Spectacle
- kill UnrealEditor only when requested

UE launch command:
env DISPLAY=:0 \
QT_QPA_PLATFORM=xcb \
SDL_VIDEODRIVER=x11 \
SDL_MOUSE_FOCUS_CLICKTHROUGH=1 \
SDL_HINT_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR=0 \
VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/nvidia_icd.x86_64.json \
__GLX_VENDOR_LIBRARY_NAME=nvidia \
__NV_PRIME_RENDER_OFFLOAD=1 \
"/home/nguyennongngockhanh/Unreal/UE5.7.4/Engine/Binaries/Linux/UnrealEditor" \
"/home/nguyennongngockhanh/Documents/Unreal Projects/ProjectTemplate/ProjectTemplate.uproject" \
-nohighdpi

Screenshot command:
DISPLAY=:0 spectacle --background --nonotify --fullscreen --output "<output_png>"

Forbidden:
- edit files
- build
- rsync
- commit
- kill dotnet/MSBuild unless explicitly requested
- debug stale logs by inventing causes

Stop immediately if:
- expected marker is missing
- log timestamp is stale
- UE fails to launch
- screenshot file is not created
