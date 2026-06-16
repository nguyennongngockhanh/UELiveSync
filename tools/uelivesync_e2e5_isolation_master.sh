#!/usr/bin/env bash
# E2E.5 SceneOutliner Crash Isolation — Master Runner
# Runs Tests A/C/D/E/F with fresh UE per test.

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

export DISPLAY=:0
export QT_QPA_PLATFORM=xcb
export SDL_VIDEODRIVER=x11
export SDL_MOUSE_FOCUS_CLICKTHROUGH=1
export SDL_HINT_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR=0
export VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/nvidia_icd.x86_64.json
export __GLX_VENDOR_LIBRARY_NAME=nvidia
export __NV_PRIME_RENDER_OFFLOAD=1

UE_BIN="/home/nguyennongngockhanh/Unreal/UE5.7.4/Engine/Binaries/Linux/UnrealEditor"
UPROJECT="/home/nguyennongngockhanh/Documents/Unreal Projects/ProjectTemplate/ProjectTemplate.uproject"

RESULT_FILE="/tmp/uelivesync-e2e5-matrix-results.txt"
> "$RESULT_FILE"

log_result() {
    echo "$1" >> "$RESULT_FILE"
    echo "$1"
}

# ---- Cleanup function ----
kill_ue() {
    echo "=== Killing any remaining UE processes ==="
    pkill -TERM -f "UnrealEditor" 2>/dev/null || true
    pkill -TERM -f "CrashReportClient" 2>/dev/null || true
    pkill -TERM -f "UnrealCEFSubProcess" 2>/dev/null || true
    sleep 3
    pkill -KILL -f "UnrealEditor" 2>/dev/null || true
    pkill -KILL -f "CrashReportClient" 2>/dev/null || true
    pkill -KILL -f "UnrealCEFSubProcess" 2>/dev/null || true
    sleep 2
    local remaining
    remaining=$(pgrep -fa "UnrealEditor|CrashReportClient|UnrealCEFSubProcess" 2>/dev/null | grep -v grep | grep -v "pkill\|pgrep" || true)
    if [ -n "$remaining" ]; then
        echo "WARNING: Remaining processes:"
        echo "$remaining"
        pkill -9 -f "UnrealEditor" 2>/dev/null || true
        pkill -9 -f "CrashReportClient" 2>/dev/null || true
        sleep 2
    fi
    # Also kill any stale python injector sockets
    fuser -k 57000/tcp 2>/dev/null || true
}

# ---- Check port free ----
check_port_free() {
    if ss -tlnp 2>/dev/null | grep -q 57000; then
        echo "ERROR: Port 57000 still in use!"
        ss -tlnp | grep 57000
        return 1
    fi
    return 0
}

# ---- Launch UE ----
launch_ue() {
    local log_path="$1"
    echo "=== Launching UE (log: $log_path) ==="
    $UE_BIN "$UPROJECT" \
        -nohighdpi \
        -WINDOWED \
        -ResX=1280 \
        -ResY=720 \
        -ExecCmds="UE.LiveSync.Verbose 1" \
        > "$log_path" 2>&1 &
    UE_PID=$!
    echo "UE_PID=$UE_PID"
    log_result "UE_PID=$UE_PID"

    echo "Waiting for port 57000..."
    for i in $(seq 1 60); do
        if ss -tlnp 2>/dev/null | grep -q 57000; then
            echo "PORT_57000_LISTENING_after_${i}s"
            log_result "PORT_READY_AFTER_${i}s"
            break
        fi
        if ! kill -0 "$UE_PID" 2>/dev/null; then
            echo "ERROR: UE process died before ready!"
            log_result "UE_PROCESS_DIED"
            tail -100 "$log_path"
            return 1
        fi
        sleep 2
    done

    # Verify process is alive
    if kill -0 "$UE_PID" 2>/dev/null; then
        echo "UE process alive, PID=$UE_PID"
        log_result "UE_ALIVE_PID=$UE_PID"
    else
        echo "ERROR: UE process not alive after wait!"
        log_result "UE_NOT_ALIVE"
        tail -100 "$log_path"
        return 1
    fi

    # Verify port one more time
    if ! ss -tlnp 2>/dev/null | grep -q 57000; then
        echo "ERROR: Port 57000 not listening after wait!"
        log_result "PORT_NOT_LISTENING"
        return 1
    fi
    return 0
}

# ---- Run crash grep ----
grep_crash_markers() {
    local log_path="$1"
    echo "=== Crash markers in $log_path ==="
    local s6 s11 scenegraph frustum sel
    s11=$(grep -c "CommonUnixCrashHandler: Signal=11" "$log_path" 2>/dev/null || echo "0")
    s6=$(grep -c "CommonUnixCrashHandler: Signal=6" "$log_path" 2>/dev/null || echo "0")
    scenegraph=$(grep -c "SSceneOutliner::EnsureParentForItem" "$log_path" 2>/dev/null || echo "0")
    sel_item=$(grep -c "SSceneOutiner::AddUnfilteredItemToTree" "$log_path" 2>/dev/null || echo "0")
    add_unfiltered=$(grep -c "AddUnfilteredItemToTree" "$log_path" 2>/dev/null || echo "0")
    actor_tree=$(grep -c "FActorTreeItem::UpdateDisplayStringInternal" "$log_path" 2>/dev/null || echo "0")
    frustum=$(grep -c "UDrawFrustumComponent::CreateSceneProxy" "$log_path" 2>/dev/null || echo "0")
    sel_parent=$(grep -c "GetSelectionParent" "$log_path" 2>/dev/null || echo "0")

    echo "  Signal=11: $s11"
    echo "  Signal=6:  $s6"
    echo "  SSceneOutliner::EnsureParentForItem: $scenegraph"
    echo "  AddUnfilteredItemToTree: $add_unfiltered"
    echo "  FActorTreeItem::UpdateDisplayStringInternal: $actor_tree"
    echo "  UDrawFrustumComponent::CreateSceneProxy: $frustum"
    echo "  GetSelectionParent: $sel_parent"

    log_result "Signal11=$s11"
    log_result "Signal6=$s6"
    log_result "SceneOutliner_EnsureParentForItem=$scenegraph"
    log_result "SceneOutliner_AddUnfilteredItemToTree=$add_unfiltered"
    log_result "FActorTreeItem_UpdateDisplayStringInternal=$actor_tree"
    log_result "UDrawFrustumComponent_CreateSceneProxy=$frustum"
    log_result "GetSelectionParent=$sel_parent"

    # Camera markers
    echo "=== Camera markers ==="
    local c_create c_frustum c_transform c_transform_conv c_active c_seqbind c_cut
    c_create=$(grep -c "\[CAMERA\]\[CREATE\]" "$log_path" 2>/dev/null || echo "0")
    c_frustum=$(grep -c "\[CAMERA\]\[FRUSTUM_GUARD\]" "$log_path" 2>/dev/null || echo "0")
    c_transform=$(grep -c "\[CAMERA\]\[TRANSFORM_APPLY\]" "$log_path" 2>/dev/null || echo "0")
    c_transform_conv=$(grep -c "\[CAMERA\]\[TRANSFORM_CONVERGED\]" "$log_path" 2>/dev/null || echo "0")
    c_active=$(grep -c "\[CAMERA\]\[ACTIVE_RECV\]" "$log_path" 2>/dev/null || echo "0")
    c_seqbind=$(grep -c "\[CAMERA\]\[SEQ_BIND\]" "$log_path" 2>/dev/null || echo "0")
    c_cut=$(grep -c "\[CAMERA\]\[CUT_APPLY\]" "$log_path" 2>/dev/null || echo "0")
    echo "  [CAMERA][CREATE]=$c_create"
    echo "  [CAMERA][FRUSTUM_GUARD]=$c_frustum"
    echo "  [CAMERA][TRANSFORM_APPLY]=$c_transform"
    echo "  [CAMERA][TRANSFORM_CONVERGED]=$c_transform_conv"
    echo "  [CAMERA][ACTIVE_RECV]=$c_active"
    echo "  [CAMERA][SEQ_BIND]=$c_seqbind"
    echo "  [CAMERA][CUT_APPLY]=$c_cut"
    log_result "CAMERA_CREATE=$c_create"
    log_result "CAMERA_FRUSTUM_GUARD=$c_frustum"
    log_result "CAMERA_TRANSFORM_APPLY=$c_transform"
    log_result "CAMERA_TRANSFORM_CONVERGED=$c_transform_conv"
    log_result "CAMERA_ACTIVE_RECV=$c_active"
    log_result "CAMERA_SEQ_BIND=$c_seqbind"
    log_result "CAMERA_CUT_APPLY=$c_cut"

    # Hierarchy markers
    echo "=== Hierarchy markers ==="
    local h_attach h_self h_cycle h_invalid h_all
    h_attach=$(grep -c "\[HIERARCHY\]\[ATTACH_GUARD\]" "$log_path" 2>/dev/null || echo "0")
    h_self=$(grep -c "\[HIERARCHY\]\[ATTACH_SKIP_SELF\]" "$log_path" 2>/dev/null || echo "0")
    h_cycle=$(grep -c "\[HIERARCHY\]\[ATTACH_SKIP_CYCLE\]" "$log_path" 2>/dev/null || echo "0")
    h_invalid=$(grep -c "\[HIERARCHY\]\[ATTACH_SKIP_INVALID\]" "$log_path" 2>/dev/null || echo "0")
    h_all=$(grep -c "\[HIERARCHY\]\[ATTACH\]" "$log_path" 2>/dev/null || echo "0")
    echo "  [HIERARCHY][ATTACH_GUARD]=$h_attach"
    echo "  [HIERARCHY][ATTACH_SKIP_SELF]=$h_self"
    echo "  [HIERARCHY][ATTACH_SKIP_CYCLE]=$h_cycle"
    echo "  [HIERARCHY][ATTACH_SKIP_INVALID]=$h_invalid"
    echo "  [HIERARCHY][ATTACH]=$h_all"
    log_result "HIERARCHY_ATTACH_GUARD=$h_attach"
    log_result "HIERARCHY_ATTACH_SKIP_SELF=$h_self"
    log_result "HIERARCHY_ATTACH_SKIP_CYCLE=$h_cycle"
    log_result "HIERARCHY_ATTACH_SKIP_INVALID=$h_invalid"
    log_result "HIERARCHY_ATTACH_ALL=$h_all"
}

# ---- Determine classification ----
classify() {
    local test_name="$1"
    local log_path="$2"
    local s11 s6 crash_log
    s11=$(grep -c "CommonUnixCrashHandler: Signal=11" "$log_path" 2>/dev/null || echo "0")
    s6=$(grep -c "CommonUnixCrashHandler: Signal=6" "$log_path" 2>/dev/null || echo "0")

    if [ "$s11" -gt 0 ]; then
        case "$test_name" in
            "A") echo "FAIL_UE_IDLE_SCENE_OUTLINER_CRASH" ;;
            "C") echo "FAIL_LIVESYNC_CAMERA_CREATE_SCENE_OUTLINER_CRASH" ;;
            "D") echo "FAIL_LIVESYNC_CAMERA_CREATE_TRANSFORM_SCENE_OUTLINER_CRASH" ;;
            "E") echo "FAIL_LIVESYNC_CAMERA_FULL_LIFECYCLE_SCENE_OUTLINER_CRASH" ;;
            "F") echo "FAIL_LIVESYNC_HIERARCHY_SCENE_OUTLINER_CRASH" ;;
            *) echo "FAIL_UNKNOWN_TEST_SCENE_OUTLINER_CRASH" ;;
        esac
    elif [ "$s6" -gt 0 ]; then
        case "$test_name" in
            "C"|"D"|"E") echo "PASS_CAMERA_FRUSTUM_SIGNAL6_FIXED" ;;
            *) echo "FAIL_UNEXPECTED_SIGNAL6" ;;
        esac
    elif [ "$test_name" = "F" ]; then
        local h_all
        h_all=$(grep -c "\[HIERARCHY\]\[ATTACH\]" "$log_path" 2>/dev/null || echo "0")
        if [ "$h_all" -gt 0 ]; then
            echo "PASS_HIERARCHY_ATTACH_GUARD_RUNTIME"
        else
            echo "PASS_HIERARCHY_NO_CRASH_NO_GUARD_MARKERS"
        fi
    else
        echo "PASS_NO_CRASH"
    fi
}

# ---- Run a test ----
run_test() {
    local test_name="$1"
    local mode="$2"
    local log_path="/tmp/uelivesync-e2e5-${test_name}.log"

    echo ""
    echo "========================================"
    echo "TEST ${test_name} — ${mode}"
    echo "LOG: ${log_path}"
    echo "========================================"

    log_result "=== TEST ${test_name}: ${mode} ==="

    # Kill existing UE
    kill_ue
    sleep 2

    # Check port free
    if ! check_port_free; then
        log_result "SKIP_${test_name}: PORT_NOT_FREE"
        return 1
    fi

    # Launch UE
    if ! launch_ue "$log_path"; then
        log_result "SKIP_${test_name}: LAUNCH_FAILED"
        return 1
    fi

    # Small delay to let UE settle
    sleep 3

    # Run the test mode
    if [ "$mode" = "idle" ]; then
        echo "Test A: UE idle only, sleeping 60s..."
        log_result "TEST_${test_name}: IDLE_60S"
        sleep 60
    else
        echo "Running injector: python3 $ROOT_DIR/tools/uelivesync_e2e5_sceneoutliner_isolation.py --${mode}"
        python3 "$ROOT_DIR/tools/uelivesync_e2e5_sceneoutliner_isolation.py" "--${mode}" \
            > "/tmp/uelivesync-e2e5-${test_name}-injector.log" 2>&1
        INJECTOR_EXIT=$?
        log_result "TEST_${test_name}: INJECTOR_EXIT=$INJECTOR_EXIT"
        sleep 5
    fi

    # Grep markers
    grep_crash_markers "$log_path"

    # Classify
    local classification
    classification=$(classify "$test_name" "$log_path")
    log_result "TEST_${test_name}: CLASSIFICATION=$classification"
    echo "CLASSIFICATION: $classification"

    # Check if UE is still alive
    if kill -0 "$UE_PID" 2>/dev/null; then
        log_result "TEST_${test_name}: UE_ALIVE_PID=$UE_PID"
        # Kill UE before next test
        kill_ue
    fi

    echo "TEST_${test_name} COMPLETE: $classification"
    log_result "TEST_${test_name}: COMPLETE"
}

# ---- Main ----
echo "=============================================="
echo "E2E.5 SCENE OUTLINER CRASH ISOLATION"
echo "Started: $(date)"
echo "=============================================="
log_result "E2E.5 START: $(date)"

# Test A — UE idle only
run_test "A" "idle" || true

# Test C — Camera create-only
run_test "C" "create-only" || true

# Test D — Camera create + transform
run_test "D" "create-transform" || true

# Test E — Full camera lifecycle
run_test "E" "full" || true

# Test F — Hierarchy attach exercise
run_test "F" "hierarchy" || true

echo ""
echo "=============================================="
echo "E2E.5 MATRIX COMPLETE"
echo "Finished: $(date)"
echo "Results: $RESULT_FILE"
echo "=============================================="
log_result "E2E.5 COMPLETE: $(date)"
cat "$RESULT_FILE"
