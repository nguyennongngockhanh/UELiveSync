# Manual E2E — Log Hygiene

## Problem

Runtime validation of UELiveSync suffers from unreliable log reading:

1. **Stale backup logs**: `ProjectTemplate-backup-*.log` files contain old GUIDs from previous runs.
2. **Validator reads wrong file**: Injector/validator may open the newest backup log instead of the current run's log.
3. **Tick-stalled misinterpretation**: When UE game thread/tick stops, NetworkThread still receives/enqueues packets. Queued-but-not-processed packets must not be classified as feature failures.

## Fix: Log Hygiene Rules

### Rule 1 — Do not delete ProjectTemplate.log while UE is running
UE writes to the log file continuously. Deleting it while UE is running causes log rotation to create a backup and start a new file, confusing the validator.

### Rule 2 — Prefer stdout/stderr capture
Launch UE with explicit log redirection:
```bash
UE5.7.4/Build/BatchFiles/Linux/UE5.sh \
  -project=/home/nguyennongngockhanh/Documents/Unreal\ Projects/ProjectTemplate/ProjectTemplate.uproject \
  -log | tee /tmp/uelivesync-manual-e2e-ue.log
```

### Rule 3 — Detect active log by timestamp
If reading from ProjectTemplate.log directory:
1. Record launch timestamp.
2. Find the newest `ProjectTemplate*.log` created **after** the launch timestamp.
3. Ignore any `ProjectTemplate-backup-*.log` files unless explicitly requested.

### Rule 4 — Filter by current-run GUID
Each validator run should:
1. Choose a unique test GUID (e.g., UUID4).
2. Print it in validator output.
3. Grep only for that GUID or for markers appearing **after** the start timestamp.

### Rule 5 — Classify tick-blocked packets separately
When tick/focus is halted:
- Do NOT classify feature failure from queued-but-not-processed packets.
- Use classification: `ENV_RUNTIME_TICK_BLOCKED` instead of feature failure.

## Validator Tool Behavior

| Behavior | Required |
|----------|----------|
| Ignore `ProjectTemplate-backup-*.log` | ✓ |
| Select newest live log after launch | ✓ |
| Filter by current-run GUID | ✓ |
| Pass from stale marker matches | ✗ (must fail) |

## Runtime Evidence Summary

- UE launched and port 57000 listened successfully.
- `ProjectTemplate.log` was manually truncated before launch.
- Log rotation / backup logs caused confusion.
- NetworkThread received/enqueued packets after game thread/tick stopped.
- Tick heartbeat showed frames advancing until a final frame, then packet receive continued without `ProcessQueuedPackets`.
