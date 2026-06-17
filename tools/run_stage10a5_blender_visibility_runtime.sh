#!/usr/bin/env bash
# ============================================================================
# Phase 7E Stage 10A.5 — Blender Runtime Automation Wrapper
# ============================================================================
# Runs the Blender background script and captures output.
#
# Usage:
#   bash tools/run_stage10a5_blender_visibility_runtime.sh
#
# Output log:
#   /tmp/uelivesync-phase7e-10a5-blender-injector.log
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
BLENDED_SCRIPT="$REPO_ROOT/tools/uelivesync_stage10a5_blender_visibility_runtime.py"
LOG="/tmp/uelivesync-phase7e-10a5-blender-injector.log"

# Choose Blender command
BLENDER_CMD=""
if command -v flatpak &>/dev/null; then
    if flatpak list 2>/dev/null | grep -q "org.blender.Blender"; then
        BLENDER_CMD="flatpak run org.blender.Blender"
    fi
fi

if [[ -z "$BLENDER_CMD" ]] && command -v blender &>/dev/null; then
    BLENDER_CMD="blender"
fi

if [[ -z "$BLENDER_CMD" ]]; then
    echo "[ERROR] Blender not found. Install Flatpak org.blender.Blender or system blender." >&2
    exit 1
fi

echo "[$(date)] Running Blender visibility runtime..."
echo "  Blender command: $BLENDER_CMD"
echo "  Script: $BLENDED_SCRIPT"
echo "  Log: $LOG"

$BLENDER_CMD --background --python "$BLENDED_SCRIPT" 2>&1 | tee "$LOG"

EXIT_CODE=${PIPESTATUS[0]}

echo ""
echo "[$(date)] Blender exit code: $EXIT_CODE"
echo "[$(date)] Log: $LOG"

exit $EXIT_CODE
