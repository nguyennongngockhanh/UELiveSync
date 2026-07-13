# AGENTS.md — UELiveSync Local Agent Rules

## Playbook History

- **v1.0** — Initial engineering playbook (commit `a70fcc7`)
- **v1.1** — Added Observation Completeness
- **v1.2** — Added Evidence Ownership, Experiment IDs, Rollback Verification
- **v1.3** — Generalized Rollback Verification, added Playbook Evolution

## Project

UELiveSync is a Blender ↔ Unreal Engine live sync system.

Repo root:

/home/nguyennongngockhanh/Projects/UELiveSync

Main folders:

- Blender_Addon/
- UE_Plugin/UELiveSync/
- tests/
- Docs/Architecture/

Actual Unreal plugin source path:

UE_Plugin/UELiveSync/Source/UELiveSync/

Do not invent old paths such as:

- UE/Plugins/LiveSync/
- UE/Plugins/UELiveSync/

Do not mention obsolete/non-evidenced classes unless they exist in inspected repo evidence:

- BlueprintAnimationHandler
- SequencerTrackBuilder

Only mention a file, class, function, counter, or test if it exists in evidence you inspected.

## Current Phase

Current work:

Phase 10A — Incremental Texture Synchronization (COMPLETE, tag v0.6.0)

Delivered:
- Sidecar texture path: SIDECAR_HIT / SIDECAR_EXPORTED / SIDECAR_VERIFIED
- Batch import via single `ImportAssetsAutomated` + `TMap<FString, UObject*>` canonical-name matching
- Commit `6c2b242` — `perf(texture): batch sidecar import with canonical-name matching`
- Commit `7ab3b97` — `docs(perf): record Phase 10A profiling results; fix brace corruption in ApplyUnitScaleGuard`
- Commit `544ef02` — `fix(texture): import sidecars when semantic mesh import is skipped`

Key finding:
- Root cause of N freeze+focus events: per-texture `ImportAssetsAutomated` (commit `b5c098f` changed batch to per-texture to fix slot mismatch bug)
- Fix: single batch call + canonical-name TMap matching (O(n), zero SIDECAR_RESULT_MISMATCH)
- Verified: 6-7 files per batch, zero mismatches, stall time ~230ms vs ~520ms before

Known issues:
- First-sync texture compression/DDC time is a separate concern from the per-texture freeze problem
- `[MATERIAL][COAT_FALLBACK]` log spam demoted to backlog

## Operating Rules

Use evidence-first development.

Before editing:

1. Inspect actual files.
2. Identify exact symbols and file:line anchors.
3. State the smallest safe build scope.
4. Edit only files required for that scope.

For audit tasks:

- Read only.
- Do not edit files.
- Use exact file paths and line evidence.
- If anchors are insufficient, say AUDIT INCOMPLETE.

For build tasks:

- Make the smallest edit possible.
- Do not rewrite whole files.
- Do not refactor unrelated code.
- Do not change packet layout unless explicitly requested.
- Do not change protocol constants unless explicitly requested.
- Do not modify unrelated docs.

For tests:

- Prefer existing test style and nearby test files.
- Add focused tests for changed behavior.
- Run the smallest relevant test scope first.
- Then run broader tests if needed.

## Important Invariants

Protocol:

- Do not change PT_Keyframe wire format for Stage 10A.2.
- Do not add a new packet type for visibility animation.
- Channels 0–8 remain transform-related.
- Channels 9–10 are visibility-related.
- Unsupported channels must be safe.

Unreal:

- Game-thread mutation only.
- Do not mutate stale or non-owned LevelSequence.
- Do not assume a GUID has a Sequencer binding.
- Do not crash on missing actor, missing binding, or unsupported channel.

Blender:

- Do not regress existing extraction behavior.
- Do not rename channel IDs.
- Do not change serialization unless the phase explicitly requires it.

## Local Workflow

Default local agent:

uelive-pi-qwen36

UE-safe explicit profile:

uelive-pi-qwen36-ue-safe

Long audit, no UE open:

uelive-pi-qwen36-ctx64k

Fast local profile, no UE open:

uelive-pi-qwen36-fast

Stop Qwen to free VRAM for UE:

qwen36-pi-stop

Pi shell commands use:

! command

Useful audit helper:

/home/nguyennongngockhanh/bin/uelive-10a2-summary-min

If Pi hides output:

/home/nguyennongngockhanh/bin/uelive-10a2-summary-min > /tmp/uelive-10a2-summary-min.out
sed -n '1,220p' /tmp/uelive-10a2-summary-min.out

## Recommended Audit Prompt Style

Plan/Audit only.
Do not edit files.
Use only the evidence shown above.
Return exact file:line evidence.
Do not mention non-existing paths/classes.
Recommend one small build scope only if anchors are sufficient.
Otherwise return AUDIT INCOMPLETE.

## Response Expectations

Be concise and grounded.

Always separate:

- Evidence
- Risk
- Recommended edit scope
- Tests to run

When unsure, inspect more instead of guessing.

---

# PI AUTOLOAD PLAYBOOK RULES

When Pi starts in this repository, it must treat this section as always active.

## Startup Behavior
At the start of every task, Pi must:
1. Follow AGENTS.md strictly.
2. Identify the requested mode:
   - PLAN/AUDIT ONLY
   - BUILD
   - RUNTIME VALIDATION
   - GIT COMMIT
3. Use the matching playbook rules below.
4. Stop if the task conflicts with the selected mode.

## Auto-Selected Playbooks

### If the task mentions FBX, handoff, import, asset import, StaticMesh import
Use:
- .pi/playbooks/fbx_handoff_plan.md

Mode default:
- PLAN/AUDIT ONLY unless user explicitly says BUILD.

Rules:
- Do not edit.
- Do not build.
- Do not launch UE.
- Do not modify packet format.
- Do not continue procedural mesh debugging.
- Use only fixed paths from AGENTS.md.
- Final output must include file:line evidence.

### If the task mentions build, compile, UBT, Build.sh
Use:
- .pi/playbooks/ue_build.md

Rules:
- Do not search for UnrealBuildTool.
- Do not search for Engine paths.
- Use only:
  /home/nguyennongngockhanh/Unreal/UE5.7.4/Engine/Build/BatchFiles/Linux/Build.sh
- Sync plugin only with the fixed SRC/DST paths in the playbook.
- Do not launch UE unless explicitly requested.

### If the task mentions runtime validation, launch UE, screenshot, capture, viewport
Use:
- .pi/playbooks/runtime_capture.md

Rules:
- Do not edit.
- Do not build.
- Do not rsync.
- Do not commit.
- Use fixed UnrealEditor path only.
- Stop if marker is missing or log timestamp is stale.
- Do not invent causes without direct evidence.

### If the task mentions commit, git add, push
Use:
- .pi/playbooks/git_commit.md

Rules:
- Never commit unless user explicitly asks.
- Before commit, show:
  git status --short --untracked-files=all
  git diff --stat
  git diff --name-only
- Do not include large diagnostic patches unless explicitly approved.
- Check accidental deletions before commit.

## Mandatory Stop Conditions
Pi must stop immediately and ask for instruction if:
- It needs a path not listed in AGENTS.md.
- It wants to run broad find under /home/nguyennongngockhanh.
- It wants to search for UnrealBuildTool.
- It wants to edit during PLAN/AUDIT ONLY.
- It wants to build during PLAN/AUDIT ONLY.
- It wants to launch UE during PLAN/AUDIT ONLY or BUILD.
- It sees stale logs or missing expected markers.
- Runtime evidence contradicts the plan.

## Current Project Direction
Production mesh sync is moving to FBX handoff.

Do not continue debugging:
- ProceduralMesh winding
- DynamicMesh backend comparison
- tangent/normal render diagnostics
- screenshot automation loops

unless the user explicitly requests it.

---

# FBX STAGE 3A.1 BUILD RULE

If the task mentions:
- Stage 3A.1
- FBX Mesh Handoff Import Vertical Slice
- PT_FBXImportRequest
- Sync Selected Mesh to UE (FBX)

Pi must use:
- .pi/playbooks/fbx_stage3a1_build.md

Default mode:
- BUILD

Hard rule:
- Do not replace the existing procedural mesh operator.
- Create a new FBX operator only.
- Do not continue procedural mesh/winding/tangent/backend debugging.
- Do not sync plugin, build UE, launch UE, or commit unless explicitly requested after the diff review.

---

# LOCAL LARGE FILE TOKEN RULES

UELiveSyncSubsystem.cpp is a large file. Pi/OpenCode must not read it wholesale for small tasks.

Before touching UELiveSyncSubsystem.cpp:
1. Run `uelive-subsystem-map`.
2. Use `uelive-subsystem-slice <pattern> <context-lines>` for focused context.
3. Read only the relevant anchor region.
4. Do not broaden into unrelated domains.

Domain anchors:
- FBX: `HandleFBXImport`, `PT_FBXImportRequest`, `LiveSync_GUID`
- Packet dispatch: `ProcessBinaryPacket`, `kValidTypes`
- Actor cache: `FindActorFast`, `BuildActorCache`
- V1 mesh: `BuildV1MeshFromReassembly`, `ParseV1MeshPayload`
- Sequencer/keyframe: `HandleKeyframe`

For FBX Stage 3A.2, inspect only:
- `HandleFBXImport`
- `FindActorFast`
- `BuildActorCache`
- `FBXImportActorsSpawned`
- `FBXImportActorsUpdated`

Do not inspect procedural mesh diagnostics, winding diagnostics, or keyframe code unless explicitly requested.

<!-- CTX64K_AUTO_COMPRESS_PROFILE_RULE -->
## Long Context Profile: uelive-pi-qwen36-ctx64k

When this session is launched from `uelive-pi-qwen36-ctx64k`, follow the additional policy in:

`.pi/CTX64K_AUTO_COMPRESS.md`

For long audits, logs, build output, or multi-step implementation reviews:
- compress after each substantial prompt/task;
- keep durable evidence and decisions;
- discard raw repeated context;
- never let old investigation noise dominate the active context.
<!-- /CTX64K_AUTO_COMPRESS_PROFILE_RULE -->

<!-- UELIVE_CTX64K_COMPACTION_RULE -->
## uelive-pi-qwen36-ctx64k context policy

When launched via `uelive-pi-qwen36-ctx64k`, follow:

`.pi/CTX64K_COMPACTION_POLICY.md`

For long audit/log/code-review tasks, keep summaries compact and evidence-grounded. Prefer durable compressed state over retaining raw repeated transcript. Use Pi compaction when context becomes large, and preserve exact UELiveSync phase, files, functions, counters, tests, and next action.
<!-- /UELIVE_CTX64K_COMPACTION_RULE -->

<!-- LARGE_FILE_SAFE_WRITE_POLICY -->
## Large file write safety

Before writing or rewriting files larger than 40KB or 1500 lines, follow:

`.pi/LARGE_FILE_SAFE_WRITE_POLICY.md`

Important:
- Pi tool output may truncate around 50KB or 2000 lines.
- Do not treat truncated displayed output as proof that disk content was truncated.
- Do not use one-shot write for large files.
- Use chunked write/temp file/targeted patch and verify with stat, wc, tail, checksum, syntax checks, and tests.
<!-- /LARGE_FILE_SAFE_WRITE_POLICY -->

<!-- PI_CTX96K_COMPACTION_RULE -->
## Pi ctx96k compaction rule

The launcher name may still be `uelive-pi-qwen36-ctx64k`, but the intended runtime profile is now ctx96k.

Actual runtime settings are authoritative and must be read from:

`.pi/settings.json`

Expected settings:
- compaction.enabled = true
- compaction.reserveTokens = 24576
- compaction.keepRecentTokens = 12000

Expected behavior:
- If the Pi backend context window is about 96k/98k, native auto-compaction should trigger around 72k/74k.
- Use manual `/compact topic=<task>` after each large phase/log/audit if the context meter keeps growing.
- Do not rely on `/smart-compact` output alone as proof that active context was reduced.
- Confirm real compaction by watching the Pi footer/context meter drop.

Important:
- Do not use stale DCP32 values: reserveTokens=32768, keepRecentTokens=8000.
- Do not run shell commands named `compact` or `compress`.
- Pi compaction is a slash-command/session behavior.

Manual Opencode-like command:
`/compact topic=<short exact task name>`
<!-- /PI_CTX96K_COMPACTION_RULE -->

---

# Mandatory User-Launched Runtime and Log-Boundary Rule

## 1. Pi/OpenCode must not launch Blender or Unreal Editor

For UELiveSync foreground runtime tests, the user owns application launch.

Pi/OpenCode must not execute:

```bash
flatpak run org.blender.Blender
UnrealEditor ...
nohup ...
setsid ...
```

Pi/OpenCode must not launch, relaunch, restart, close or kill Blender or Unreal Editor unless the user explicitly requests that exact action in the current conversation.

Reason:
The user's manually launched UE and Blender sessions have the correct display, environment, plugin state and connection behavior. Agent-launched sessions have repeatedly failed Start Sync despite manually launched sessions working.

Required workflow:

1. Ask the user to open Unreal Editor manually.
2. Ask the user to open Flatpak Blender 5.1 manually.
3. Wait for the user to state that both are open.
4. Pi verifies processes and TCP port only.
5. The user presses Start Sync in Blender.
6. Pi verifies fresh connection evidence from log files.
7. Only then continue the runtime test.

Do not attempt to replace this workflow by launching the applications yourself.

## 2. Never ask the user to paste scrolling terminal output

Pi/OpenCode must not ask:

```
What does the terminal show?
Paste the Blender terminal output.
Copy the latest material lines.
```

The Blender terminal contains continuous material logs and is not a reliable evidence source for manual copying.

Pi must collect evidence directly from log files using shell commands and fresh log boundaries.

The user should only perform visible UI actions such as:

```
select object
press Start Sync
press Sync Selected Mesh to UE (FBX)
change material value
change texture
```

Pi is responsible for reading and filtering logs.

## 3. Authoritative log files

UE log:

```
/home/nguyennongngockhanh/Documents/Unreal Projects/ProjectTemplate/Saved/Logs/ProjectTemplate.log
```

Blender addon debug log:

```
/home/nguyennongngockhanh/.cache/uelivesync/uelivesync_blender_debug.log
```

If the Blender addon is not currently writing all required runtime markers to the debug log, update logging so the same important markers printed to the Blender console are also appended to this file.

Do not rely on terminal scrollback as the authoritative record.

Do not create temporary simulation logs.

## 4. Mandatory fresh-log boundary before every test

Pi must record boundaries BEFORE the user performs a runtime action.

Correct sequencing:

1. record UE and Blender log inode and byte offset;
2. persist boundary data (offsets, wall-clock time, inode, PID);
3. only then ask the user to press the action button (e.g. Sync Selected Mesh to UE);
4. wait for the user's reply before reading any logs;
5. slice only bytes appended after the recorded offset.

Do not ask the user to press the button before boundaries are recorded.

Use byte offsets:

```bash
UE_LOG="/home/nguyennongngockhanh/Documents/Unreal Projects/ProjectTemplate/Saved/Logs/ProjectTemplate.log"
BL_LOG="/home/nguyennongngockhanh/.cache/uelivesync/uelivesync_blender_debug.log"

UE_OFFSET=$(stat -c %s "$UE_LOG" 2>/dev/null || echo 0)
BL_OFFSET=$(stat -c %s "$BL_LOG" 2>/dev/null || echo 0)

printf 'UE_OFFSET=%s\nBL_OFFSET=%s\n' "$UE_OFFSET" "$BL_OFFSET"
```

Also record the wall-clock boundary and inode/PID:

```bash
date --iso-8601=seconds
stat -c 'ue_inode=%i ue_size=%s' "$UE_LOG"
stat -c 'bl_inode=%i bl_size=%s' "$BL_LOG"
```

After the user performs the requested action, read only bytes appended after those offsets.

Example:

```bash
tail -c +$((UE_OFFSET + 1)) "$UE_LOG"
tail -c +$((BL_OFFSET + 1)) "$BL_LOG"
```

Save fresh slices outside the repository:

```bash
mkdir -p /tmp/uelivesync-current-test

tail -c +$((UE_OFFSET + 1)) "$UE_LOG" \
  > /tmp/uelivesync-current-test/ue_fresh.log

tail -c +$((BL_OFFSET + 1)) "$BL_LOG" \
  > /tmp/uelivesync-current-test/blender_fresh.log
```

All runtime conclusions must use these fresh slices.

Do not grep the entire historical log and treat old lines as current evidence.

## 5. Handle log rotation and replacement safely

Before reading from an old offset, check whether the file became smaller or changed identity.

Record:

```bash
stat -c 'inode=%i size=%s mtime=%y' "$UE_LOG"
stat -c 'inode=%i size=%s mtime=%y' "$BL_LOG"
```

If:

* inode changed;
* file size became smaller than the recorded offset;
* UE created a new `ProjectTemplate.log`;
* Blender recreated its debug log;

then reset the offset to zero and clearly label the new file as the current session log.

Do not silently read a backup log or a previous session log.

## 6. Current-session identity verification

Before testing, record active processes:

```bash
pgrep -a -f "UnrealEditor|org.blender.Blender|blender"
```

Record:

* UE PID;
* Blender PID;
* process start times.

Example:

```bash
ps -o pid,lstart,cmd -p <UE_PID>,<BLENDER_PID>
```

Use only logs whose modification times are later than the current application process start times.

If a log is older than the current process, it is stale and must not be used as runtime evidence.

## 7. Connection verification must use fresh evidence

After the user presses Start Sync, Pi must:

1. create fresh boundaries before the button press;
2. wait for the user to say Start Sync was pressed;
3. read only appended log data;
4. verify port 57000;
5. verify a fresh Blender connection marker;
6. verify a fresh UE connection or packet marker.

Port check:

```bash
ss -tlnp | grep ':57000'
```

Do not report connection failure based on historical lines such as:

* old disconnect;
* old timeout;
* previous failed launch;
* previous port error.

A current connection result must come from:

* current process session;
* fresh log slice;
* timestamp after the test boundary.

## 8. Feature tests must use action-specific markers

Before the user presses `Sync Selected Mesh to UE (FBX)`, create a new fresh boundary.

After the action, extract only relevant markers from the fresh slices.

Blender example:

```bash
grep -E \
'FBX\[OBJECT_SELECTION|FBX\[TEXTURE_SIDECAR|FBX\[SIDECAR_READY|MATERIAL\[SYNC_TIMING_BLENDER|SIDECAR_PREP|SIDECAR_SKIP|ERROR|Traceback' \
/tmp/uelivesync-current-test/blender_fresh.log
```

UE example:

```bash
grep -E \
'FBX\[AUTH|FBX\[SIDECAR_RESULT|SIDECAR_RESULT_MAP_READY|MTEX|PERSISTENT_MIC_CHANNEL|PERSISTENT_MIC_READBACK|MATX_FULL_SNAPSHOT_APPLY|SYNC_TIMING|Error|Warning' \
/tmp/uelivesync-current-test/ue_fresh.log
```

Do not conclude "FBX did not export" merely because a specific optional marker is missing.

Use all relevant evidence:

* Blender operator completion;
* FBX file mtime;
* sidecar files;
* packet send marker;
* UE receive/import marker;
* asset/result-map marker.

## 9. Verify generated files by timestamp

For FBX and sidecar operations, verify current transaction files directly.

FBX cache root:

```
/home/nguyennongngockhanh/.cache/uelivesync/fbx
```

After a sync, inspect files modified after the test boundary:

```bash
find "/home/nguyennongngockhanh/.cache/uelivesync/fbx" \
  -type f \
  -newermt "$TEST_START_ISO" \
  -printf '%TY-%Tm-%Td %TH:%TM:%TS %s %p\n' \
  | sort
```

A fresh FBX or sidecar file timestamp is valid evidence that export/copy occurred.

Do not claim export failure when current files were created or updated successfully.

## 10. Never mix log sessions

Forbidden behavior:

* grepping the full `ProjectTemplate.log` and using the last matching line without checking timestamp;
* using `ProjectTemplate-backup-*.log` for the current test;
* using a Blender log from before the current Blender PID started;
* mixing syncId values from different runs;
* mixing GUIDs from different Blender sessions;
* using cold-run timing in a warm-run report;
* carrying a previous packet's timing value into a new scenario;
* reporting old texture misses after a later successful sync.

Every report must identify:

```
Test boundary timestamp:
UE PID:
Blender PID:
GUID:
syncId:
UE fresh-log byte range:
Blender fresh-log byte range:
```

If these cannot be identified, report:

```
Result: BLOCKED — fresh log correlation unavailable
```

Do not guess.

## 11. User interaction must be minimal

Pi should issue one precise action at a time, for example:

```
In the visible Blender window:
1. Select the cabinet object.
2. Press Start Sync.
3. Reply only: done.
```

After the user replies `done`, Pi reads the logs itself.

Do not ask the user to:

* open a terminal for Blender;
* copy terminal output;
* search for log markers;
* paste long logs;
* manually interpret sync status.

## 12. Current test scratch files

Use:

```
/tmp/uelivesync-current-test/
```

Allowed files:

```
ue_fresh.log
blender_fresh.log
boundary.txt
processes.txt
files_changed.txt
```

These are evidence slices only.

Do not create:

* fake addon implementations;
* packet simulators;
* reconstructed Blender scenes;
* background Blender scripts.

Clear the scratch directory before a new scenario:

```bash
rm -rf /tmp/uelivesync-current-test
mkdir -p /tmp/uelivesync-current-test
```

## 13. Required runtime checklist

Before every runtime action:

```
[RUNTIME TEST CHECKLIST]
[ ] UE was opened manually by the user
[ ] Blender was opened manually by the user
[ ] Current UE PID recorded
[ ] Current Blender PID recorded
[ ] Current log files are newer than process start
[ ] Port 57000 listening
[ ] Fresh UE log offset recorded
[ ] Fresh Blender log offset recorded
[ ] Start Sync pressed in visible Blender
[ ] Fresh connection evidence found
[ ] No historical log evidence used
[ ] No terminal output requested from user
[ ] No Blender --background or -b
[ ] No fake addon simulation
```

## 14. Required report format

```
Runtime ownership: user-launched GUI applications
UE PID:
Blender PID:
UE process start:
Blender process start:
Test boundary:
UE log path:
Blender log path:
UE fresh byte range:
Blender fresh byte range:
Port 57000:
Start Sync fresh evidence:
Feature action:
Blender fresh evidence:
UE fresh evidence:
Generated-file evidence:
GUID:
syncId:
Result: PASS|FAIL|BLOCKED
Reason:
```

## 15. Immediate rule for the current test

Stop attempting to launch UE or Blender.

Ask the user only to:

```
1. Open UE manually.
2. Open the .blend file manually in Flatpak Blender.
3. Press Start Sync.
4. Reply: ready.
```

After `ready`:

* record PIDs;
* verify port;
* establish fresh byte offsets;
* ask for one feature action;
* read fresh logs directly;
* never ask for terminal output.

## 16. Documentation-only change

For adding this rule:

* do not launch UE;
* do not launch Blender;
* do not modify production code;
* do not create simulation tests;
* update only the authoritative instruction file;
* commit the instruction change separately.

Suggested commit:

```
docs(testing): require user-launched runtime and fresh log boundaries
```

Do not push.

Report:

* instruction file changed;
* commit hash;
* confirmation Pi may not launch Blender/UE;
* confirmation user terminal output is never required;
* confirmation fresh byte-offset log slicing is mandatory;
* confirmation historical logs cannot be used as current evidence.

---

# MANDATORY PROJECT RULES

## Rule: Diagnostic Logging

Không thêm `print()` hoặc `UE_LOG` trong hot path (Tick, timer, per-object loop, packet loop).

Diagnostic phải là opt-in:
- Thông qua CVar/debug flag, hoặc
- One-shot, hoặc
- Rate-limited.

Mặc định build phải không sinh diagnostic spam.

## Rule: Evidence-First Instrumentation

Quy trình bắt buộc:

1. Reproduce
2. Collect existing evidence
3. Analyze
4. Identify missing evidence
5. Add minimal instrumentation
6. Collect evidence
7. Remove instrumentation

Không implement fix khi root cause chưa được chứng minh.

Không thêm hàng chục log "để phòng hờ".

## Rule: Instrumentation Lifetime

Mọi instrumentation tạm thời phải có annotation (ví dụ `TODO(Phase10A): Remove after FOV investigation`).

Không merge instrumentation tạm thời vào baseline nếu không có lý do rõ ràng.

Sau khi bug đóng, xóa luôn instrumentation. Không để tồn tại nhiều tháng.

## Rule: One Bug, One Change

Mỗi bug chỉ được sửa trong phạm vi subsystem liên quan.

Không sửa đồng thời camera transform, camera basis, viewport rendering, packet protocol... trong cùng một thay đổi nếu chưa chứng minh chúng liên quan.

Mỗi root cause → một commit độc lập.

Nếu một proposed fix yêu cầu sửa subsystem khác, dừng lại và chứng minh subsystem đó có liên quan nhân quả trước khi thực hiện bất kỳ thay đổi nào.

## Rule: Baseline-First Investigation

Khi có regression:

1. Xác định commit baseline cuối cùng hoạt động.
2. Mọi điều tra phải dựa trên baseline đó.
3. Bỏ toàn bộ bằng chứng thu được từ các bản đã sửa nếu chúng không còn tương ứng với baseline.
4. Không xây giả thuyết dựa trên log của code đã thay đổi.

Một kết luận chỉ được rút ra từ đúng một revision của source code.
Trộn dữ liệu từ nhiều revision là lỗi phương pháp.

## Rule: Implementation Contract

Đừng để Implementation Contract trở thành một mẫu quá dài. Nó hiệu quả nhất khi luôn trả lời được 7 câu hỏi cốt lõi:

```
Baseline:
Root cause:
Evidence:

Runtime invariants:
- ...

Will change:
Will NOT change:

Allowed files:
Forbidden files:

Estimated diff:

Waiting for approval.
```

Chỉ viết code sau khi được phê duyệt.

Nếu Pi phải điền đầy đủ những mục này trước mỗi implementation, bạn sẽ phát hiện rất sớm các dấu hiệu như:
- Chưa chứng minh được root cause.
- Định sửa sang subsystem khác.
- Diff lớn bất thường.
- Phạm vi sửa không khớp với bug.

Đối với UELiveSync, nơi Blender addon, UE plugin, protocol và runtime gắn kết chặt chẽ, đây là lớp bảo vệ cuối cùng trước khi bất kỳ dòng code nào được viết.

## Rule: Regression Guard

Mọi fix phải nêu rõ:

1. Behavior nào sẽ thay đổi.
2. Behavior nào phải giữ nguyên.
3. File/hàm nào được phép sửa.
4. File/hàm nào cấm sửa.

Ví dụ:

Goal:
✓ Synchronize FOV.
✓ Disable automatic viewport switch.

Must remain unchanged:
✓ Camera transform.
✓ Camera basis.
✓ Quaternion conversion.
✓ GetCameraView().
✓ UpdateTargetTransform().
✓ Interpolation.
✓ Packet format.

Allowed files:
- HandleCameraDef
- Active camera synchronization
- Viewport lock gating

Forbidden:
- Camera transform pipeline
- Basis math
- Quaternion conversion

Mọi báo cáo điều tra phải nêu rõ:
- Commit hash chính xác
- Runtime build
- Log timestamp

Trước khi rút ra kết luận. Bằng chứng không có provenance của revision là vô hiệu.

## Rule: Invariant Protection

Mỗi subsystem phải khai báo rõ các runtime invariants của nó trước khi sửa.

Quy tắc này khác với "Regression Guard" ở chỗ:
- **Regression Guard** nói được sửa file nào, không được sửa file nào.
- **Invariant Protection** nói được phép thay đổi hành vi nào, hành vi nào tuyệt đối không được đổi, kể cả trong cùng một file.

Ví dụ với Camera (FOV bug):

Must remain unchanged:
- Camera transform pipeline
- Packet format
- Transform interpolation
- Quaternion conversion
- Basis conversion
- Spawn lifecycle

Only this bug may change:
- FOV synchronization

Success criteria:
- Camera transform identical to baseline
- Viewport orientation identical to baseline
- Only FOV behavior changes

Ví dụ với Auto Viewport (viewport switch bug):

Must remain unchanged:
- Camera transform
- Camera basis
- FOV
- Spawn/despawn
- Sequencer ownership

Only change:
- Automatic viewport switching behavior

Đây là lớp bảo vệ rất hữu ích vì nhiều bug camera xảy ra trong cùng một file (UELiveSyncSubsystem.cpp). Chỉ giới hạn theo file là chưa đủ; cần giới hạn theo behavior.

Nếu kết hợp lại, workflow sẽ thành:

```
Baseline
Root cause
Evidence
Runtime invariants
Will change
Will NOT change
Allowed/Forbidden files
Estimated diff
Approval
```

Như vậy gần như mọi lần sửa đều sẽ bị "khóa" trong đúng phạm vi của bug, giảm đáng kể nguy cơ lặp lại tình trạng sửa FOV nhưng làm hỏng transform hoặc viewport camera.

---

# Engine Immutability Policy (Mandatory)

UE5.8 is the project's golden reference.

From this point forward:

- Never modify anything under `Engine/` (Source, Binaries, or otherwise).
- Never rebuild any Engine module.
- Never manually copy `.so`/`.dll`/`.dylib` between build targets. Only use Unreal Build Tool outputs.
- Never instrument Unreal Engine source code unless the user explicitly approves it first.

All investigation must stay inside:

- UELiveSync plugin
- Blender addon
- test project
- external logging/instrumentation

If engine instrumentation is absolutely necessary, stop immediately and ask for approval. Do not proceed automatically.

These are hard constraints, not preferences. Any violation is a blocking error.

---

# Investigation Escalation Ladder

When investigating bugs, follow this escalation order. Do not skip levels.

1. Add logs or instrumentation inside the plugin
2. Add diagnostics inside the test project (when plugin instrumentation is insufficient)
3. Use console commands / CVars / Unreal Insights / Trace
4. Analyze Engine source (read-only)
5. If all above are insufficient, propose Engine instrumentation and require explicit user approval

Each level must be attempted or explicitly ruled out before moving to the next.

---

# Approval Gate

Default writable scope:

- `Blender_Addon/`
- `UE_Plugin/`

Everything else is read-only unless explicitly approved by the user.

Documentation updates (Docs/, STATUS.md, AGENTS.md, README.md) do not require approval.

---

# Single-Variable Experiment Rule

Never investigate more than one hypothesis in a single code change.

Each experiment must:

- Change one variable
- Have one expected observation
- Have one rollback point

Before applying an experiment patch, state:

```
Variable changed:
Expected observation if hypothesis is correct:
Expected observation if hypothesis is wrong:
Rollback:
```

Do not combine multiple instrumentation points in one patch unless they measure the same variable.

---

# Build Policy

Never perform a build unless:

- The code has changed
- The user requested a build
- The current experiment explicitly requires it

Never rebuild unrelated targets.

Only build the smallest target affected by the current change.

Never perform a build merely to "be safe." Every build must have a stated purpose.

Example: plugin change → build `UELiveSync_Test58Editor` only. Do not build `UnrealEditor` unless engine source was modified (which requires approval per Engine Immutability Policy).

---

# Rollback Policy

Every experiment must have an immediate rollback procedure.

If the experiment fails or is abandoned:

- Restore modified files (`git restore` or equivalent)
- Never manually restore binaries by copying artifacts
- Always regenerate binaries through the appropriate Unreal Build Tool target
- Verify `git status` is clean
- Verify runtime behavior matches the pre-experiment baseline
- Verify experiment-specific instrumentation is absent
- Verify no new warnings/errors introduced by the rollback
- Report rollback completion before continuing

Do not leave experimental patches in the codebase without explicit user instruction to keep them.

---

# Artifact Ownership

Each build artifact has an owning target.

- `Engine/Binaries/` — owner: Epic (read-only)
- `Binaries/` (project) — owner: project build target
- `UE_Plugin/` — owner: UELiveSync plugin build
- `Blender_Addon/` — owner: addon distribution
- Experiment logs / scratch — disposable

Never overwrite an artifact owned by another target.

Examples:

- `cp Engine .so into project` — VIOLATION
- `cp project binary into Engine` — VIOLATION
- `rebuild using the owning build target` — CORRECT

---

# Runtime Invariants

The following runtime subsystems are considered stable.

Changes require explicit justification and user approval:

- Networking (TCP connection, packet send/receive)
- Protocol serialization (packet layout, PT_* types, wire format)
- Threading (game thread mutation, async operations)
- Queue implementation (packet queue, reorder buffer)
- Tick scheduling (Tick interval, timer callbacks)
- Asset import pipeline
- Object identity / GUID mapping
- Interpolation (transform smoothing, easing)

Investigations should prefer adding diagnostics rather than changing runtime behavior.

If a proposed fix touches any of these subsystems, the Implementation Contract must explicitly justify why the change is safe and scoped.

Crossing a Runtime Invariant boundary requires a separate Implementation Contract.

---

# Investigation Journal

Every investigation must maintain a running journal.

Each experiment records:

```
Experiment ID:
Hypothesis:
Variable changed:
Expected result:
Observed result:
Conclusion:
Confidence:
Rollback:
```

Experiment IDs follow the pattern: EXP-A, EXP-B, EXP-C, ...

Keep a summary at the top of the journal:

```
EXP-A: Eliminated — wrong level
EXP-B: Pending — visibility flags
EXP-C: Not started
```

This enables quick status review without reading full entries.

Confidence values:

- `Low` — initial hypothesis, no direct evidence yet
- `High` — supported by direct observation, not yet conclusive
- `Eliminated` — disproved by direct evidence
- `Confirmed` — proven by direct evidence

The journal is the authoritative record of what was tried and what was eliminated.

Do not remove eliminated hypotheses from the journal. Keep them as historical evidence.

Before starting a new experiment, review the journal to confirm the hypothesis has not already been tested or eliminated.

Advantages:

- Prevents retesting eliminated hypotheses
- Provides provenance for every conclusion
- Enables context transfer between sessions
- Makes developer notes and handoff trivial

---

# Implementation Contract (Mandatory)

Before any code modification, the agent must state:

```
Problem:
Hypothesis:
Scope:
Files to modify:
Files guaranteed not to change:
Variable changed:
Expected observation:
Rollback plan:
```

Do not modify code until this contract is accepted by the user.

---

# Scope Freeze

Once an investigation scope is established:

- Do not expand scope without user approval
- Do not fix unrelated issues encountered during investigation
- Do not improve code quality while investigating a bug
- Record unrelated findings separately
- One investigation = one primary bug

Refactoring is a separate task.

---

# Experiment vs Fix

Experiment:

- Temporary
- Diagnostic
- Expected to be removed

Fix:

- Intended to remain
- Requires evidence from completed experiments

Do not convert an experiment into a permanent fix without explicit justification.

---

# Evidence Before Conclusion

Do not state that a hypothesis is confirmed or eliminated until there is direct evidence.

Use the following terminology accurately:

- **Observation**: what the log/measurement directly shows
- **Inference**: what can be deduced from observations
- **Hypothesis**: a proposed explanation not yet proven
- **Conclusion**: a verified statement supported by direct evidence

Clearly distinguish between them. Avoid language implying certainty when only an inference exists.

Bad: "Selection is likely the cause."
Good: "Observation: SelectActor() runs in the click path. Inference: selection may trigger visibility. Not yet confirmed."

---

# Evidence Ownership

Every observation must identify its source.

Possible sources:

- Runtime log
- Debugger
- Engine source (read-only)
- Documentation
- UE API contract
- User observation

Do not mix evidence from different sources without stating it.

Example:

```
Observation (Runtime log): proxy != nullptr
Observation (Engine source): SceneProxy is created during CreateRenderState_Concurrent()
Inference: Primitive probably entered scene initialization.
Conclusion: Not yet established.
```

This prevents reasoning drift from a single runtime log to conclusions about engine internals.

---

# Read Before Modify

Before modifying a function:

- Read the entire function
- Identify all exit paths
- Identify ownership and lifetime boundaries
- Identify threading context
- Only then propose modifications

UE code is full of early returns, macros, and conditional compilation. Partial reads lead to misplaced patches.

---

# Root Cause Policy

Do not ship fixes that only hide the symptom.

Temporary mitigations are acceptable only as experiments.

Permanent fixes must explain the underlying root cause.

Every permanent fix must identify:

- Root cause
- Triggering condition
- Why the previous implementation failed

If a fix does not explain why the bug occurred, it is not ready to ship.

---

# Playbook Evolution

New rules may only be added when:

- A real incident exposed a missing safeguard, or
- An existing rule proved insufficient

Do not add speculative rules.

Each new rule should reference the incident that motivated it.

The playbook grows by learning, not by anticipation.

---

# Verification Contract

A fix is not complete until:

- The original bug is no longer reproducible
- Existing behavior has been regression-tested
- Expected logs match the hypothesis
- Unexpected side effects are documented
- Temporary instrumentation has been removed (unless explicitly kept)

State explicitly before marking complete:

```
Verification:
Regression:
Instrumentation removed:
Ready to merge: Yes/No
```

Do not consider a fix done merely because "build succeeded" or "no more crashes."

---

# Observation Completeness

Every experiment must collect all observations required to prove or disprove its stated hypothesis.

If the hypothesis requires comparing two states (before/after, click/no-click, frame N/frame N+1), the instrumentation must capture both states.

Do not claim an experiment can validate a comparison if only one side of the comparison is observed.

Bad: "Expected: before != after" when the patch only logs "before."

Good: "Expected: HiddenEd == true" when the patch logs HiddenEd at one point in time.

---

## Commit Policy

Follow the repository engineering policy documented in:

`Docs/Engineering/CommitGuidelines.md`

These rules are mandatory for all code changes and take precedence over any default agent behavior.

