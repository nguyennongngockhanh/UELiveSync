#!/usr/bin/env bash
# Phase 7C Stage 2C.4 — Collect runtime validation evidence
# Usage:
#   ./scripts/phase7c_stage2c4_collect_evidence.sh before   # baseline snapshot
#   ./scripts/phase7c_stage2c4_collect_evidence.sh after    # post-test evidence
#
# Output: /tmp/uelive-7c2c4-{before,after}/  (timestamped dirs)

set -euo pipefail

MODE="${1:?Usage: $0 {before|after}}"

REPO="$HOME/Projects/UELiveSync"
UEPLUGIN="$HOME/Unreal/UE5.7.4/Engine/Plugins/UELiveSync"
OUTDIR="/tmp/uelive-7c2c4-${MODE}-$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUTDIR"

echo "=== Phase 7C Stage 2C.4 — Collecting ${MODE} evidence ==="
echo "Output dir: $OUTDIR"

collect_baseline() {
    # Repo commit
    git -C "$REPO" log --oneline -1 > "$OUTDIR/repo_commit.txt"
    echo "Repo HEAD: $(cat "$OUTDIR/repo_commit.txt")"

    # Source sync check
    diff -rq "$UEPLUGIN/Source" "$REPO/UE_Plugin/UELiveSync/Source" \
        > "$OUTDIR/source_sync_diff.txt" 2>&1 || true
    echo "Source sync: $(wc -l < "$OUTDIR/source_sync_diff.txt") lines of diff"

    # Plugin manifest check
    diff -q "$UEPLUGIN/UELiveSync.uplugin" "$REPO/UE_Plugin/UELiveSync/UELiveSync.uplugin" \
        > "$OUTDIR/uplugin_diff.txt" 2>&1 || true

    # UE .so timestamp
    ls -la "$UEPLUGIN/Intermediate/Build/Linux/x64/UnrealEditor/Development/UELiveSync/"*.so \
        > "$OUTDIR/ue_so_timestamps.txt" 2>&1 || true

    # UE Editor running?
    pgrep -xa UnrealEditor > "$OUTDIR/ue_editor_pid.txt" 2>&1 || true

    # LiveSync port
    ss -tlnp 2>/dev/null | grep -E '51091' \
        > "$OUTDIR/livesync_port.txt" 2>&1 || true
}

collect_after() {
    # Re-run baseline checks
    collect_baseline

    # UE log — find most recent log
    UE_LOG_DIR="$HOME/Unreal/UE5.7.4/ProjectTemplate/Saved/Logs"
    UE_LOG_FILE=$(ls -t "$UE_LOG_DIR"/UnrealEditor*.log 2>/dev/null | head -1 || true)

    if [ -n "$UE_LOG_FILE" ]; then
        cp "$UE_LOG_FILE" "$OUTDIR/ue_log_full.txt" 2>/dev/null || true

        # Extract [MESH][V1] markers
        grep -n '\[MESH\]\[V1\]' "$UE_LOG_FILE" \
            > "$OUTDIR/ue_log_mesh_v1.txt" 2>&1 || true

        # Extract all [MESH] markers
        grep -n '\[MESH\]' "$UE_LOG_FILE" \
            > "$OUTDIR/ue_log_mesh_all.txt" 2>&1 || true

        # Extract completion/rejection markers specifically
        grep -n '\[MESH\]\[V1\] Built section' "$UE_LOG_FILE" \
            > "$OUTDIR/ue_log_v1_built.txt" 2>&1 || true
        grep -n '\[MESH\]\[V1\] Build rejected' "$UE_LOG_FILE" \
            > "$OUTDIR/ue_log_v1_rejected.txt" 2>&1 || true
        grep -n '\[MESH\]\[V1\] Missing actor' "$UE_LOG_FILE" \
            > "$OUTDIR/ue_log_v1_missing_actor.txt" 2>&1 || true
        grep -n '\[MESH\]\[V1\] Stored chunk' "$UE_LOG_FILE" \
            > "$OUTDIR/ue_log_v1_stored.txt" 2>&1 || true
        grep -n '\[MESH\]\[V1\] Reassembly complete' "$UE_LOG_FILE" \
            > "$OUTDIR/ue_log_v1_complete.txt" 2>&1 || true
        grep -n '\[MESH\]\[V1\] Duplicate' "$UE_LOG_FILE" \
            > "$OUTDIR/ue_log_v1_duplicate.txt" 2>&1 || true

        echo "UE log: $UE_LOG_FILE"
    else
        echo "No UE log found at $UE_LOG_DIR" > "$OUTDIR/ue_log_mesh_v1.txt"
    fi

    # Also check engine plugin's own log directory (engine plugin, not project)
    UE_LOG_DIR2="$HOME/Unreal/UE5.7.4/Engine/Programs/UnrealEditor/Saved/Logs"
    UE_LOG_FILE2=$(ls -t "$UE_LOG_DIR2"/UnrealEditor*.log 2>/dev/null | head -1 || true)
    if [ -n "$UE_LOG_FILE2" ] && [ ! -f "$OUTDIR/ue_log_full.txt" ]; then
        cp "$UE_LOG_FILE2" "$OUTDIR/ue_log_full.txt" 2>/dev/null || true
        grep -n '\[MESH\]\[V1\]' "$UE_LOG_FILE2" \
            > "$OUTDIR/ue_log_mesh_v1.txt" 2>&1 || true
        echo "UE log (alt): $UE_LOG_FILE2"
    fi
}

# Summary
print_summary() {
    echo ""
    echo "=== Evidence Summary ==="
    echo "Location: $OUTDIR"
    for f in "$OUTDIR"/*.txt; do
        lines=$(wc -l < "$f")
        echo "  $(basename "$f"): $lines lines"
    done

    echo ""
    echo "=== V1 Marker Counts ==="
    for marker in stored complete built rejected missing_actor duplicate; do
        f="$OUTDIR/ue_log_v1_${marker}.txt"
        if [ -f "$f" ]; then
            count=$(wc -l < "$f")
            echo "  [MESH][V1] ${marker}: ${count}"
        fi
    done
}

case "$MODE" in
    before)
        collect_baseline
        ;;
    after)
        collect_after
        ;;
    *)
        echo "Error: mode must be 'before' or 'after'"
        exit 1
        ;;
esac

print_summary
echo ""
echo "Done. Evidence at: $OUTDIR"
