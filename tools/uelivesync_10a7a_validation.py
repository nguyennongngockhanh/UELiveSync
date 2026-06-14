#!/usr/bin/env python3
"""Stage 10A.7A — Automated Sequencer Playback State Validation (Log-Based)

Purpose:
  Validate that visibility keyframe channels (9=hide_viewport, 10=hide_render)
  were applied correctly in UE's transient LevelSequence after a full
  CREATE_SEQUENCE + ADD_POSSESSABLE + keyframe packet cycle.

Expected prior runtime condition:
  1. PT_SequencerOp CREATE_SEQUENCE → frames 1-20, 24fps
  2. PT_Create → actor spawned with LiveSync_GUID
  3. PT_SequencerOp ADD_POSSESSABLE → binding created
  4. PT_Keyframe packet → channels 0-8 (transform) + 9 (hide_viewport) + 10 (hide_render)
  5. UE HandleKeyframe() applies all keys with applied=11 miss=0 unsupp=0
  6. No Fatal Error / SIGSEGV / Access Violation in UE log

Expected UE log path:
  /home/nguyennongngockhanh/Documents/Unreal Projects/ProjectTemplate/Saved/Logs/ProjectTemplate.log

Validation criteria and output classification:
  PASS  — All checks passed: sequence exists, binding exists, all keys applied,
          ch9=[0,1,0], ch10=[0,1,0] at frames [1,10,20], no crashes.
  FAIL  — One or more checks failed.

UE Python direct evaluation is currently blocked because LiveSync LevelSequence
is created via NewObject<ULevelSequence>(GetTransientPackage()) — no asset path,
no unreal.load_asset(), and transient object enumeration unavailable from
external scripts. Log-based validation is the accepted method for this stage.

Usage:
  python tools/uelivesync_10a7a_validation.py \
    --log "/home/nguyennongngockhanh/Documents/Unreal Projects/ProjectTemplate/Saved/Logs/ProjectTemplate.log"

If --log is omitted, uses the default ProjectTemplate log path.
"""
import argparse
import datetime
import re
import sys
import os

DEFAULT_LOG = (
    "/home/nguyennongngockhanh/Documents/Unreal "
    "Projects/ProjectTemplate/Saved/Logs/ProjectTemplate.log"
)

# Frame 1/10/20 transform X-inferred values (cm).
# These come from the known-good Blender-side extraction:
#   ch0 (CH0_LOC_X): frame 1 → 0.0m (0cm), frame 10 → 1.0m (100cm),
#                       frame 20 → 2.0m (200cm)
# UE log does not emit per-channel transform key values individually;
# transform evidence comes from the aggregate "applied=N" summary and
# BEGIN/END transform apply convergence.
TRANSFORM_INFERRED = {
    1: 0.0,
    10: 100.0,
    20: 200.0,
}

# Expected visibility values at frames [1, 10, 20]
# channel 9 (hide_viewport): 0=visible, 1=hidden
# channel 10 (hide_render): 0=visible, 1=hidden
EXPECTED_VIS_CH9 = {1: 0, 10: 1, 20: 0}
EXPECTED_VIS_CH10 = {1: 0, 10: 1, 20: 0}

EXPECTED_KEYS_TOTAL = 11  # ch0(3) + ch1(1) + ch2(1) + ch9(3) + ch10(3)


def parse_ue_log(log_path):
    """Parse UE log and extract all 10A.7A evidence."""
    result = {"file_exists": os.path.isfile(log_path)}
    if not result["file_exists"]:
        return result

    with open(log_path, "r") as f:
        lines = f.readlines()

    result["lines"] = len(lines)

    re_seq = re.compile(
        r"\[SEQOP\]\s*CREATE_SEQUENCE:\s*(\d+)\-(\d+)\s+(\d+)/\d+\s+fps"
    )
    re_poss = re.compile(
        r"\[SEQOP\]\s*ADD_POSSESSABLE:\s*(\w+)\s*[→>]\s*guid=([A-F0-9]+)\s+binding=([A-F0-9]+)"
    )
    re_summary = re.compile(
        r"\[KEYFRAME\]\s*Applied\s+seq=(\d+)\s+count=(\d+)\s+"
        r"applied=(\d+)\s+miss=(\d+)\s+unsupp=(\d+)"
    )
    re_vis = re.compile(
        r"\[KEYFRAME\]\[VISIBILITY\]\s+applied\s+channel=(\d+)\s+"
        r"guid=([A-F0-9]+)\s+value=(\d+)\s+frame=(\d+)"
    )
    re_crash = re.compile(r"(?i)\b(Fatal Error|SIGSEGV|Access Violation)\b")

    seq_info = None
    bind_info = None
    summary = None
    vis_ch9, vis_ch10 = [], []
    crash_count = 0

    for line in lines:
        m = re_seq.search(line)
        if m:
            seq_info = (int(m.group(1)), int(m.group(2)))

        m = re_poss.search(line)
        if m:
            bind_info = (m.group(1), m.group(2), m.group(3))

        m = re_summary.search(line)
        if m:
            summary = (
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3)),
                int(m.group(4)),
                int(m.group(5)),
            )

        m = re_vis.search(line)
        if m:
            ch = int(m.group(1))
            guid = m.group(2)
            val = int(m.group(3))
            frame = int(m.group(4))
            if ch == 9:
                vis_ch9.append((frame, val))
            elif ch == 10:
                vis_ch10.append((frame, val))

        if re_crash.search(line):
            crash_count += 1

    result["seq_info"] = seq_info
    result["bind_info"] = bind_info
    result["summary"] = summary
    result["vis_ch9"] = vis_ch9
    result["vis_ch10"] = vis_ch10
    result["crash_count"] = crash_count
    return result


def evaluate(result):
    """Run all checks and classify as PASS or FAIL."""
    checks = {}

    # 1. Log file
    checks["log_file_readable"] = result.get("file_exists", False)

    # 2. CREATE_SEQUENCE
    checks["create_sequence"] = result.get("seq_info") is not None

    # 3. ADD_POSSESSABLE binding
    checks["binding"] = result.get("bind_info") is not None

    # 4. Keyframe summary
    summary = result.get("summary")
    checks["summary_present"] = summary is not None
    if summary:
        checks["all_applied"] = summary[2] == summary[1]
        checks["no_miss"] = summary[3] == 0
        checks["no_unsupp"] = summary[4] == 0
        checks["key_count_matches"] = summary[1] == EXPECTED_KEYS_TOTAL
    else:
        checks["all_applied"] = False
        checks["no_miss"] = False
        checks["no_unsupp"] = False
        checks["key_count_matches"] = False

    # 5. Visibility channel 9
    vis9_by = {f: v for f, v in result.get("vis_ch9", [])}
    checks["vis_ch9_complete"] = all(f in vis9_by for f in [1, 10, 20])
    for frame in [1, 10, 20]:
        key = f"vis_ch9_frame{frame}"
        checks[key] = vis9_by.get(frame) == EXPECTED_VIS_CH9[frame]

    # 6. Visibility channel 10
    vis10_by = {f: v for f, v in result.get("vis_ch10", [])}
    checks["vis_ch10_complete"] = all(f in vis10_by for f in [1, 10, 20])
    for frame in [1, 10, 20]:
        key = f"vis_ch10_frame{frame}"
        checks[key] = vis10_by.get(frame) == EXPECTED_VIS_CH10[frame]

    # 7. No crash
    checks["no_crash"] = result.get("crash_count", -1) == 0

    all_pass = all(checks.values())
    return all_pass, checks


def main():
    parser = argparse.ArgumentParser(
        description="Stage 10A.7A — Sequencer playback state validation (log-based)"
    )
    parser.add_argument(
        "--log",
        default=DEFAULT_LOG,
        help="Path to UE ProjectTemplate.log",
    )
    args = parser.parse_args()
    log_path = args.log

    print("[10A7A] Starting Stage 10A.7A validation")
    print(f"[10A7A] timestamp={datetime.datetime.now().isoformat()}")
    print(f"[10A7A] log_path={log_path}")

    result = parse_ue_log(log_path)

    # --- Output evidence ---
    if not result.get("file_exists"):
        print(f"[10A7A] log_not_found path={log_path}")
        print("[10A7A] result=FAIL")
        print("[10A7A] evaluation_supported=0 reason=log_file_not_found")
        sys.exit(1)

    print(f"[10A7A] log_read lines={result['lines']}")

    seq_info = result.get("seq_info")
    bind_info = result.get("bind_info")
    summary = result.get("summary")

    if seq_info:
        print(f"[10A7A] sequence_found=1 frames={seq_info}")
    else:
        print("[10A7A] sequence_found=0")
        print("[10A7A] result=FAIL")
        print("[10A7A] evaluation_supported=0 reason=no_CREATE_SEQUENCE")
        sys.exit(1)

    if bind_info:
        actor, guid, binding = bind_info
        print(f"[10A7A] binding_found=1 actor={actor} guid={guid}")
    else:
        print("[10A7A] binding_found=0")
        print("[10A7A] result=FAIL")
        print("[10A7A] evaluation_supported=0 reason=no_ADD_POSSESSABLE_binding")
        sys.exit(1)

    if summary:
        seq, count, applied, miss, unsupp = summary
        print(
            f"[10A7A] keyframe_applied seq={seq} count={count} "
            f"applied={applied} miss={miss} unsupp={unsupp}"
        )

    # Transform keys — inferred from packet payload + apply summary
    count = summary[1] if summary else 0
    print(f"[10A7A] transform_keys frame_values=inferred(count={count} keys))")

    # Visibility keys
    vis9 = result.get("vis_ch9", [])
    vis10 = result.get("vis_ch10", [])
    print(f"[10A7A] visibility_ch9 keys={vis9}")
    print(f"[10A7A] visibility_ch10 keys={vis10}")

    # Frame evaluations
    vis9_by = {f: v for f, v in vis9}
    vis10_by = {f: v for f, v in vis10}

    for frame in [1, 10, 20]:
        v9 = vis9_by.get(frame, "N/A")
        v10 = vis10_by.get(frame, "N/A")
        x_cm = TRANSFORM_INFERRED.get(frame, "N/A")
        # visible=1 if both 0, visible=0 if both 1, else ?
        if v9 == 0 and v10 == 0:
            visible = "1"
        elif v9 == 1 and v10 == 1:
            visible = "0"
        else:
            visible = "?"
        print(f"[10A7A] eval frame={frame} visible={visible} x_cm={x_cm}")

    # --- Classification ---
    all_pass, checks = evaluate(result)

    print(f"[10A7A] checks_pass={all_pass}")
    for k, v in checks.items():
        if v is not True:
            print(f"[10A7A] check_fail {k}")

    classification = "PASS" if all_pass else "FAIL"
    print(f"[10A7A] result={classification}")
    print(f"[10A7A] evaluation_supported=0 reason=log_based_validation")
    print(f"[10A7A] raw_key_validation={'PASS' if all_pass else 'FAIL'}")

    if not all_pass:
        failed = [k for k, v in checks.items() if not v]
        print(f"[10A7A] failed_checks={failed}")

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
