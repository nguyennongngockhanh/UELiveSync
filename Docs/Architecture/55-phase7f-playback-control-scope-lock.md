# Phase 7F — Sequencer Playback Control Scope Lock

**Date**: 2026-06-04
**Status**: SCOPE LOCK
**Depends on**: Phase 7E ✅ (Sequencer + Keyframe Replication)
**Blocks**: Phase 7G (Sequencer Camera Cut Integration)
**Related Docs**: `Docs/Architecture/54-phase7e-sequencer-keyframe-scope-lock.md`

---

## 1. Purpose

This document defines the scope, architecture, and design for remotely controlling UE5 Sequencer playback from Blender via the LiveSync protocol. It establishes:

- How Blender playback commands (play, pause, stop, scrub, set play rate) are sent over the wire
- How UE5 `ULevelSequencePlayer` APIs are used for runtime-safe playback control
- How the existing `PT_PlaybackState` (0x14, Phase 7C) notification flow and the new `PT_PlaybackOp` (0x19) command flow coexist
- The capability negotiation, failure-mode model, and acceptance criteria for each implementation stage

### 1.1 Relationship to Wider Phase 7

| Phase | Scope | Status |
|-------|-------|--------|
| Phase 7B | Timeline state sync (frame range, FPS, current frame) | ✅ IMPLEMENTED |
| Phase 7C | Playback state sync (play/pause/stop notification) | ✅ IMPLEMENTED |
| Phase 7D | Active camera sync (camera GUID) | ✅ IMPLEMENTED |
| Phase 7E | Keyframe replication + sequencer asset creation | ✅ IMPLEMENTED |
| **Phase 7F** | **Sequencer playback control (Blender → UE commands)** | **🔒 SCOPE LOCK** |
| Phase 7G | Sequencer camera cut integration | 🔒 PENDING |

### 1.2 Design Goals

1. **Remote playback control**: Blender sends discrete commands — PLAY, PAUSE, STOP, SCRUB_TO_FRAME, SET_PLAYRATE — that UE executes on the LiveSync LevelSequence via `ULevelSequencePlayer`.
2. **Seamless coexistence with Phase 7C**: `PT_PlaybackState` (0x14) remains the *notification* path (Blender tells UE "I am now playing"). `PT_PlaybackOp` (0x19) is the *command* path (Blender tells UE "execute this playback action"). They are complementary, not conflicting.
3. **Deterministic behavior**: Each command is idempotent, strictly ordered via monotonic sequence counter, and rejected if stale.
4. **Runtime-safe**: All APIs used are available in Runtime modules (no Editor dependency for core playback). Editor-only features (sequencer UI, viewport) are gated by `#if WITH_EDITOR`.

---

## 2. Blender Side Investigation

### 2.1 Existing Playback Detection (Phase 7C)

The current Blender `PT_PlaybackState` flow in `sync.py:1880`:

```python
if is_playback_effective():
    screen = bpy.context.screen
    is_playing = screen.is_animation_playing
    current_state = PLAYBACK_PLAY if is_playing else PLAYBACK_STOP

    if current_state != _last_playback_state and _last_playback_state is not None:
        payload = serialize_playback_state(current_state, sequence, timestamp)
        send_objects([payload], packet_type=PT_PlaybackState, version=5)
```

**What is detected:**
- `screen.is_animation_playing` → boolean (playing or not)
- State transitions only: `STOP → PLAY` or `PLAY → STOP`
- First tick suppressed (`_last_playback_state is None` → no send)

**What is NOT detected (gaps for Phase 7F):**
- Current frame number during playback — only available via Phase 7B timeline (`frame_current`)
- Play rate — not exposed in Blender's `screen` API directly
- Precise pause vs stop distinction — both produce `PLAYBACK_STOP`
- Scrubbing — Blender treats scrub as `PLAYBACK_STOP` + frame change (detected via timeline)

### 2.2 Data Availability for Playback Commands

| Data | Source | Available Now? | For Phase 7F |
|------|--------|----------------|--------------|
| Play state (playing/stopped) | `bpy.context.screen.is_animation_playing` | ✅ Phase 7C | Used for `PLAY`/`STOP` triggers |
| Current frame | `bpy.context.scene.frame_current` | ✅ Phase 7B timeline | Used for `SCRUB_TO_FRAME` |
| Frame range | `bpy.context.scene.frame_start/end` | ✅ Phase 7B timeline | Validation |
| FPS | `bpy.context.scene.render.fps/fps_base` | ✅ Phase 7B timeline | Play rate reference |
| Play rate multiplier | Not directly exposed | ❌ | Default to 1.0 |

### 2.3 Blender Detection Strategy for Phase 7F

The Phase 7F detection block in `sync.py` `check_updates()` will follow the same pattern as Phase 7E — a new section inserted after the existing Phase 7C playback detection:

```
1. Check is_playback_control_effective() (cap gate)
2. Detect PLAY transition:   is_playing == True AND _last_playback_op_state != PLAYING
3. Detect PAUSE transition:  was_playing == True AND now paused (but not stopped)
4. Detect STOP transition:   is_playing == False AND player at frame_start
5. Detect SCRUB:             frame_current changed while paused AND user scrubbed
6. Send PT_PlaybackOp with appropriate opcode
```

**Key insight**: Blender's `screen.is_animation_playing` does not distinguish PAUSE from STOP natively. Both produce `False`. Strategy:

- Track `_last_frame_before_stop` — if `is_playing` transitions `True → False` and frame is unchanged, it's a PAUSE (user hit Space again or Pause command). If frame resets to `frame_start`, it's a STOP.
- This heuristic works for the standard Blender playback workflow. Advanced timeline interactions may produce ambiguous states; in those cases, we send `STOP` (safe default).

### 2.4 What Blender Sends

For each detected transition, Blender sends a `PT_PlaybackOp` packet (0x19) with:

| Opcode | Trigger Condition | Payload |
|--------|-------------------|---------|
| `PLAYBACK_OP_PLAY` (0) | `is_animation_playing` changed to `True` | None (16-byte header only) |
| `PLAYBACK_OP_PAUSE` (1) | `is_animation_playing` changed to `False` while paused (heuristic) | Current frame (optionally) |
| `PLAYBACK_OP_STOP` (2) | `is_animation_playing` changed to `False`, frame at start | None |
| `PLAYBACK_OP_SCRUB_TO_FRAME` (3) | Frame changed while `is_animation_playing == False` (user scrubbing) | Target frame (int32) |
| `PLAYBACK_OP_SET_PLAYRATE` (4) | User changed play rate (deferred — Blender exposes no direct event) | Play rate (float) |

---

## 3. Unreal Side Investigation

### 3.1 Sequencer Playback APIs (UE5.7)

All APIs are in **Runtime** modules (no Editor dependency for core functionality):

| Class | Header | Module | Purpose |
|-------|--------|--------|---------|
| `ULevelSequencePlayer` | `LevelSequence/Public/LevelSequencePlayer.h` | `LevelSequence` | Playback controller for `ULevelSequence` |
| `UMovieSceneSequencePlayer` | `MovieScene/Public/MovieSceneSequencePlayer.h` | `MovieScene` | Base class (inherited by LevelSequencePlayer) |
| `ULevelSequence` | `LevelSequence/Public/LevelSequence.h` | `LevelSequence` | The sequence asset to play |

**Key `ULevelSequencePlayer` API:**

```cpp
// Create a player for a sequence in a world context
static ULevelSequencePlayer* CreateLevelSequencePlayer(
    UObject* WorldContextObject,     // Usually the World
    ULevelSequence* LevelSequence,   // The sequence to play
    FMovieSceneSequencePlaybackSettings Settings,
    ALevelSequenceActor*& OutActor   // Created actor (can be ignored)
);

// Playback control (from UMovieSceneSequencePlayer)
void Play();
void Pause();
void Stop();
void SetPlayRate(float PlayRate);        // Multiplier (1.0 = normal)
void JumpToFrame(const FFrameNumber& NewFrame);
bool IsPlaying() const;
bool IsPaused() const;
FFrameNumber GetCurrentTime() const;
FFrameNumber GetDuration() const;
FMovieSceneRootEvaluationTemplateInstance& GetEvaluationTemplate();
```

**Playback Settings:**

```cpp
struct FMovieSceneSequencePlaybackSettings {
    bool bAutoPlay = false;           // Start playing immediately on create
    FMovieSceneSequenceLoopCount LoopCount;  // Loop behavior
    float PlayRate = 1.0f;            // Play rate multiplier
    float StartTime = 0.0f;           // Start offset in seconds
    bool bRandomStartTime = false;    // Random start position
    bool bRestoreState = true;        // Restore original state on stop
    bool bDisableMovementInput = false;
    bool bDisableLookAtInput = false;
    bool bHidePlayer = false;
    bool bHideHud = false;
    bool bDisableCameraCuts = false;
};
```

### 3.2 Sequence Player Lifecycle

```
┌──────────────────────────────────────────────┐
│  CreateLevelSequencePlayer()                  │
│  → Creates ULevelSequencePlayer               │
│  → Creates ALevelSequenceActor (optional)     │
│  → Stores reference in LiveSyncSequencePlayer │
└──────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────┐
│  Play() / Pause() / Stop() / JumpToFrame()   │
│  → Commands executed on game thread          │
│  → Sequence monotonicity checked             │
│  → Idempotent (no-op if already in state)    │
└──────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────┐
│  CLEAR_SEQUENCE / ConsoleReset / Reconnect   │
│  → Stop() + MarkPendingKill()                │
│  → LiveSyncSequencePlayer = nullptr          │
│  → Next PT_PlaybackOp recreates player       │
└──────────────────────────────────────────────┘
```

The player is created lazily — the first PT_PlaybackOp that requires playback (PLAY, SCRUB, SET_PLAYRATE) creates the player if it does not exist. The player is destroyed when:
- `CLEAR_SEQUENCE` sequencer op is received (sequence reset)
- `ConsoleReset` is executed
- Network disconnect / reconnect

### 3.3 Editor vs Runtime Restrictions

| Feature | Editor | Runtime | Notes |
|---------|--------|---------|-------|
| `ULevelSequencePlayer::Play()` | ✅ | ✅ | Both paths work |
| `ULevelSequencePlayer::Pause()` | ✅ | ✅ | |
| `ULevelSequencePlayer::Stop()` | ✅ | ✅ | |
| `ULevelSequencePlayer::JumpToFrame()` | ✅ | ✅ | |
| `SetPlayRate()` | ✅ | ✅ | |
| Sequencer UI opens/focuses | Editor-only ❌ (out of scope) | N/A | Requires `ISequencer` editor API |
| Viewport camera follows | Editor-only ✅ via optional integration | N/A | Gated by `#if WITH_EDITOR` |

**Key finding**: All core playback control APIs are available in both Editor and Runtime builds. The player module (`LevelSequence`) is a Runtime module, already in `Build.cs`. No additional module dependencies needed.

### 3.4 Required Includes

```cpp
#include "LevelSequencePlayer.h"             // ULevelSequencePlayer
#include "MovieSceneSequencePlaybackSettings.h"  // PlaybackSettings (often in LevelSequencePlayer.h)
#include "LevelSequenceActor.h"               // ALevelSequenceActor (optional, for reference)
```

These are in addition to the existing Phase 7E includes:
```cpp
#include "LevelSequence.h"
#include "MovieScene.h"
```

---

## 4. Existing Infrastructure Reuse

### 4.1 Capability Negotiation

A new capability bit `CAP_SUPPORTS_PLAYBACK_CONTROL` is added. Follows the same pattern as Phase 7E and 7D:

| Capability | Bit | Phase | Status |
|------------|-----|-------|--------|
| `CAP_SUPPORTS_TIMELINE_SYNC` | `0x10` (bit 4) | 7B | Used |
| `CAP_SUPPORTS_KEYFRAME_REPLICATION` | `0x20` (bit 5) | 7E | Used |
| `CAP_SUPPORTS_ACTIVE_CAMERA_SYNC` | `0x40` (bit 6) | 7D | Used |
| `CAP_SUPPORTS_SEQUENCER_OPS` | `0x80` (bit 7) | 7E | Used |
| **`CAP_SUPPORTS_PLAYBACK_CONTROL`** | **`0x100` (bit 8)** | **7F** | **Proposed** |

No conflict with any existing capability bits.

### 4.2 Sequence Tracking

Existing `LiveSyncSequence` weak pointer (Phase 7E) is the target sequence for playback. The sequence player is created from this sequence:

```cpp
if (!LiveSyncSequencePlayer.IsValid() && LiveSyncSequence.IsValid())
{
    ALevelSequenceActor* DummyActor = nullptr;
    LiveSyncSequencePlayer = ULevelSequencePlayer::CreateLevelSequencePlayer(
        GetWorld(), LiveSyncSequence.Get(),
        FMovieSceneSequencePlaybackSettings(),
        DummyActor
    );
}
```

### 4.3 Replay/Stale Rejection

Follows the exact same pattern as Phase 7E keyframe packets: each PT_PlaybackOp packet carries a monotonic `Sequence` field. The UE handler rejects packets where `Packet.Sequence <= LastPlaybackOpSequence`. Counter `PlaybackOpPacketsStale` increments on rejection.

### 4.4 Diagnostics Counters Pattern

| Counter | Type | Purpose |
|---------|------|---------|
| `PlaybackOpPacketsReceived` | `std::atomic<int32>` | Total PT_PlaybackOp packets received |
| `PlaybackOpPacketsApplied` | `std::atomic<int32>` | Commands executed successfully |
| `PlaybackOpPacketsStale` | `std::atomic<int32>` | Packets rejected (stale sequence) |
| `PlaybackOpPacketsMalformed` | `std::atomic<int32>` | Packets rejected (bad size or opcode) |
| `PlaybackOpPlayed` | `std::atomic<int32>` | Play() calls executed |
| `PlaybackOpPaused` | `std::atomic<int32>` | Pause() calls executed |
| `PlaybackOpStopped` | `std::atomic<int32>` | Stop() calls executed |
| `PlaybackOpScrubbed` | `std::atomic<int32>` | JumpToFrame() calls executed |
| `PlaybackOpRateChanged` | `std::atomic<int32>` | SetPlayRate() calls executed |

All reset via `ConsoleReset` in `Diagnostics.inl`. Printed in `ConsoleDumpState`.

### 4.5 Support Bundle Integration

Existing `dump_diagnostics()` pattern in `sync.py` extended with:
```python
playback_control_packets_sent
playback_control_commands
```

---

## 5. Packet Design

### 5.1 PT_PlaybackOp = 0x19

New packet type for discrete playback commands.

### 5.2 Wire Format

**Fixed-size header**: 16 bytes

```
Offset  Size  Field          Type      Description
------  ----  -----          ----      -----------
[0]     1     Opcode         uint8     PLAY(0), PAUSE(1), STOP(2), SCRUB_TO_FRAME(3), SET_PLAYRATE(4)
[1]     1     Flags          uint8     Reserved (0)
[2-5]   4     Sequence       uint32    Monotonic counter (LE)
[6-13]  8     Timestamp      double    Time of detection (UE time, unused in first pass)
[14-15] 2     Reserved       uint16    Padding (0)
```

**Fixed-size payload** (when opcode requires it): 8 bytes

For `SCRUB_TO_FRAME` (opcode 3):
```
Offset  Size  Field          Type      Description
------  ----  -----          ----      -----------
[0-3]   4     Frame          int32     Target frame number
[4-7]   4     Reserved       int32     0
```

For `SET_PLAYRATE` (opcode 4):
```
Offset  Size  Field          Type      Description
------  ----  -----          ----      -----------
[0-3]   4     PlayRate       float     Play rate multiplier (e.g. 1.0, 2.0, 0.5)
[4-7]   4     Reserved       int32     0
```

For `PLAY`/`PAUSE`/`STOP` (opcodes 0–2): Zero-byte payload (16 bytes total).

**Maximum total packet size**: 24 bytes (16 header + 8 payload).

### 5.3 Opcode Table

| Opcode | Value | Name | Payload Size | Description |
|--------|-------|------|-------------|-------------|
| `PLAYBACK_OP_PLAY` | 0 | Play | 0 | Start/resume playback |
| `PLAYBACK_OP_PAUSE` | 1 | Pause | 0 | Pause at current frame |
| `PLAYBACK_OP_STOP` | 2 | Stop | 0 | Stop and return to start |
| `PLAYBACK_OP_SCRUB_TO_FRAME` | 3 | Scrub | 8 | Jump to specific frame |
| `PLAYBACK_OP_SET_PLAYRATE` | 4 | Set Play Rate | 8 | Change playback speed |

### 5.4 Header Struct (C++)

```cpp
// Phase 7F: PT_PlaybackOp (0x19) header
USTRUCT()
struct FPlaybackOpHeader
{
    GENERATED_BODY()

    uint8 Opcode = 0;           // PLAYBACK_OP_PLAY/PAUSE/STOP/SCRUB_TO_FRAME/SET_PLAYRATE
    uint8 Flags = 0;            // Reserved
    uint32 Sequence = 0;        // Monotonic packet sequence
    double Timestamp = 0.0;     // Detection timestamp
    uint16 Reserved = 0;        // Padding

    // Payload union (not in header — separate after size check)
};

static_assert(sizeof(FPlaybackOpHeader) == 16,
    "FPlaybackOpHeader must be exactly 16 bytes");

// Opcodes
enum EPlaybackOpcode : uint8
{
    PLAYBACK_OP_PLAY           = 0,
    PLAYBACK_OP_PAUSE          = 1,
    PLAYBACK_OP_STOP           = 2,
    PLAYBACK_OP_SCRUB_TO_FRAME = 3,
    PLAYBACK_OP_SET_PLAYRATE   = 4,
};
```

### 5.5 Serialization (Blender `network.py`)

```python
PT_PlaybackOp = 0x19  # Phase 7F: playback command

PLAYBACK_OP_PLAY           = 0
PLAYBACK_OP_PAUSE          = 1
PLAYBACK_OP_STOP           = 2
PLAYBACK_OP_SCRUB_TO_FRAME = 3
PLAYBACK_OP_SET_PLAYRATE   = 4

PLAYBACK_OP_HEADER_SIZE = 16
PLAYBACK_OP_PAYLOAD_SIZE = 8  # For SCRUB_TO_FRAME and SET_PLAYRATE

def serialize_playback_op(opcode, sequence, timestamp, payload=b''):
    """Serialize a PT_PlaybackOp packet.

    Header: 16 bytes (opcode + flags + sequence + timestamp + reserved)
    Payload: 0 bytes for PLAY/PAUSE/STOP, 8 bytes for SCRUB/SET_PLAYRATE
    """
    header = struct.pack('<BBIdH',
        opcode & 0xFF,
        0,                     # Flags
        sequence & 0xFFFFFFFF,
        timestamp,
        0)                     # Reserved
    return header + payload

def serialize_scrub_op(frame, sequence, timestamp):
    """Serialize SCRUB_TO_FRAME with int32 target frame."""
    payload = struct.pack('<i', frame) + b'\x00\x00\x00\x00'
    return serialize_playback_op(PLAYBACK_OP_SCRUB_TO_FRAME, sequence, timestamp, payload)

def serialize_playrate_op(rate, sequence, timestamp):
    """Serialize SET_PLAYRATE with float multiplier."""
    payload = struct.pack('<f', rate) + b'\x00\x00\x00\x00'
    return serialize_playback_op(PLAYBACK_OP_SET_PLAYRATE, sequence, timestamp, payload)
```

### 5.6 Capability Bit

```python
# In network.py:
CAP_SUPPORTS_PLAYBACK_CONTROL = 0x100  # Bit 8: PT_PlaybackOp (0x19) supported

_local_capabilities = (
    CAP_SUPPORTS_TIMELINE_SYNC |
    CAP_SUPPORTS_KEYFRAME_REPLICATION |
    CAP_SUPPORTS_ACTIVE_CAMERA_SYNC |
    CAP_SUPPORTS_SEQUENCER_OPS |
    CAP_SUPPORTS_PLAYBACK_CONTROL  # NEW
)
```

```cpp
// In SyncTypes.h:
constexpr uint32 CAP_SUPPORTS_PLAYBACK_CONTROL = 0x100;  // Bit 8

// In UE_LOCAL_CAPABILITIES:
CAP_SUPPORTS_TIMELINE_SYNC |
CAP_SUPPORTS_KEYFRAME_REPLICATION |
CAP_SUPPORTS_ACTIVE_CAMERA_SYNC |
CAP_SUPPORTS_SEQUENCER_OPS |
CAP_SUPPORTS_PLAYBACK_CONTROL;
```

### 5.7 Protocol Signature

Add to signature computation:
- Include size `16` (header) and `8` (payload)
- Include packet type `0x19`

---

## 6. Failure-Mode Analysis

### 6.1 Failure Mode Table

| # | Failure Mode | Symptom | Severity | Detection | Mitigation |
|---|-------------|---------|----------|-----------|------------|
| F1 | Playback command received before sequence exists | No sequence to play | Medium | `LiveSyncSequence` invalid check | Log warning, increment `PlaybackOpPacketsStale`, discard |
| F2 | Scrub frame outside sequence range | Frame clamped or invalid | Low | Compare against `MovieScene->GetPlaybackRange()` | Clamp frame to valid range, log verbose |
| F3 | Invalid play rate (≤ 0 or extreme value) | Playback speed incorrect | Low | Check `PlayRate > 0 && PlayRate < 100` | Clamp to `[0.01, 10.0]`, log warning |
| F4 | Duplicate packet (same sequence number) | Repeated command execution | Low | Sequence monotonicity check | Increment `PlaybackOpPacketsStale`, discard |
| F5 | Reconnect during playback | Sequence player stale | High | Clear player on reconnection | `StopNetworkThread` already clears `LiveSyncSequence`; player auto-invalidated |
| F6 | Sequence recreated (CLEAR_SEQUENCE) | Old player bound to old sequence | High | Reset player when sequence changes | On `CLEAR_SEQUENCE` op: `Stop()` + destroy player + null `LiveSyncSequencePlayer` |
| F7 | Blender sends PAUSE but UE cannot distinguish from STOP | Wrong state | Low | Heuristic (frame position) | Document limitation. If ambiguous, send STOP (safe). |
| F8 | `CreateLevelSequencePlayer()` fails | Cannot control playback | High | Player pointer null after create call | Log error, increment malformed, skip command |

### 6.2 Blender-Side Failure Modes

| # | Failure Mode | Detection | Mitigation |
|---|-------------|-----------|------------|
| B1 | No active screen (rendering, background mode) | `bpy.context.screen` is None | Skip playback detection block |
| B2 | Rapid repeated PAUSE/PLAY toggles | Sends duplicate commands | Sequence counter ensures dedup on UE side |
| B3 | User scrubs very fast | Many SCRUB packets | Sequence counter + timestamp debounce (optional) |

### 6.3 Recovery Paths

| Failure | Immediate Action | Recovery |
|---------|-----------------|----------|
| Sequence does not exist | Discard command, log warning | Retry on next PT_PlaybackOp (sequence may be created later) |
| Player creation fails | Log error, skip command | User triggers ConsoleReset to rebuild |
| Scrub out of range | Clamp to valid range, execute | Normal operation continues |
| Reconnect | Destroy player, clear state | Sequence recreated via Phase 7E, player recreated on next command |

---

## 7. Acceptance Criteria

### 7.1 Wire Format (Stage 1)

| # | Criterion | Verification |
|---|-----------|-------------|
| AC1 | `PT_PlaybackOp = 0x19` defined in both Blender `network.py` and UE `SyncTypes.h` | Static constant check |
| AC2 | `FPlaybackOpHeader` = 16 bytes, no padding | `static_assert` check |
| AC3 | Payload = 0 bytes for PLAY/PAUSE/STOP | Size check test |
| AC4 | Payload = 8 bytes for SCRUB_TO_FRAME (int32 frame + 4 reserved) | Round-trip: serialize → parse → compare |
| AC5 | Payload = 8 bytes for SET_PLAYRATE (float rate + 4 reserved) | Round-trip test |
| AC6 | Opcode 0–4 defined, opcodes 5–255 rejected as malformed | Bounds test |
| AC7 | `CAP_SUPPORTS_PLAYBACK_CONTROL = 0x100` (bit 8) defined both sides | Constant check |
| AC8 | Protocol signature FNV hash includes 0x19, 16, 8 | Cross-check signatures match |
| AC9 | `serialize_playback_op()` produces correct bytes for all 5 opcodes | 5 unit tests |

### 7.2 UE Handler (Stage 2)

| # | Criterion | Verification |
|---|-----------|-------------|
| AC10 | `0x19` dispatch case in `ProcessBinaryPacket` | Dispatch correctly routes to `HandlePlaybackOp` |
| AC11 | Total size < 16 → malformed (reject) | Size check test |
| AC12 | Total size = 16 for opcode 0/1/2 (no payload) | Pass |
| AC13 | Total size = 24 for opcode 3/4 (16 header + 8 payload) | Pass |
| AC14 | Opcode > 4 → malformed | Reject |
| AC15 | `Sequence <= LastPlaybackOpSequence` → stale (reject) | Sequence monotonicity test |
| AC16 | `LiveSyncSequence` invalid → discard command | No-op test |
| AC17 | `PLAY` calls `LiveSyncSequencePlayer->Play()` | Integration test |
| AC18 | `PAUSE` calls `LiveSyncSequencePlayer->Pause()` | Integration test |
| AC19 | `STOP` calls `LiveSyncSequencePlayer->Stop()` | Integration test |
| AC20 | `SCRUB_TO_FRAME` calls `LiveSyncSequencePlayer->JumpToFrame(frame)` | Frame verified |
| AC21 | `SET_PLAYRATE` calls `LiveSyncSequencePlayer->SetPlayRate(rate)` | Rate verified |
| AC22 | Player created lazily if null on first command | Create on first PLAY |
| AC23 | Player destroyed on CLEAR_SEQUENCE | Reset on sequence clear |

### 7.3 Blender Detection (Stage 3)

| # | Criterion | Verification |
|---|-----------|-------------|
| AC24 | `is_playback_control_effective()` gated on pref + remote cap + connected | Unit test |
| AC25 | PLAY sent when `is_animation_playing` transitions False→True | Detection test |
| AC26 | PAUSE sent when `is_animation_playing` transitioning True→False (heuristic) | Detection test |
| AC27 | STOP sent when `is_animation_playing` True→False, frame at start | Detection test |
| AC28 | SCRUB sent when frame changes while paused | Detection test |
| AC29 | First-tick suppression (no PLAY sent on initial connect if already playing) | Same pattern as Phase 7C |
| AC30 | Reconnect resets `_last_playback_op_state` | Reconnect lifecycle test |

### 7.4 Reconnect & Diagnostics (Stage 4)

| # | Criterion | Verification |
|---|-----------|-------------|
| AC31 | `ConsoleReset` zeros all 9 PlaybackOp counters | ConsoleReset test |
| AC32 | `ConsoleDumpState` prints all 9 counters | DumpState test |
| AC33 | Reconnect destroys sequence player | Player null check |
| AC34 | Sequence recreation (CLEAR_SEQUENCE) destroys player | Player null check |
| AC35 | `StopNetworkThread` clears player state | Lifecycle test |

---

## 8. Implementation Stages

### Stage 0 — Scope Lock (THIS DOCUMENT)

| Step | Description | Deliverable |
|------|-------------|-------------|
| 0.1 | Investigate Blender playback detection | Section 2 |
| 0.2 | Investigate UE Sequencer playback APIs | Section 3 |
| 0.3 | Investigate existing infrastructure reuse | Section 4 |
| 0.4 | Design PT_PlaybackOp packet format | Section 5 |
| 0.5 | Define failure-mode matrix | Section 6 |
| 0.6 | Define acceptance criteria | Section 7 |
| 0.7 | Define implementation stages | Section 8 |

**Validation gate**: Document reviewed and approved. Zero source files modified.

### Stage 1 — Wire Format + Parser

| Step | Description | File | Deliverable |
|------|-------------|------|-------------|
| 1.1 | Add `PT_PlaybackOp = 0x19` to `network.py` | `network.py` | Constant |
| 1.2 | Add opcode constants (PLAY/PAUSE/STOP/SCRUB/SET_PLAYRATE) | `network.py` | Constants |
| 1.3 | Add `serialize_playback_op()` and helpers | `network.py` | Serializers |
| 1.4 | Add `CAP_SUPPORTS_PLAYBACK_CONTROL = 0x100` | `network.py` | Capability |
| 1.5 | Update `_local_capabilities` bitmask | `network.py` | Bitmask |
| 1.6 | Add `is_playback_control_effective()` gating function | `network.py` | Gate |
| 1.7 | Add `set_playback_control_enabled()` pref setter | `network.py` | Pref setter |
| 1.8 | Add playback control globals + reset | `network.py` | State |
| 1.9 | Add `PT_PlaybackOp = 0x19` to `SyncTypes.h` EPacketType | `SyncTypes.h` | Constant |
| 1.10 | Add `FPlaybackOpHeader` struct | `SyncTypes.h` | Struct |
| 1.11 | Add opcode enum `EPlaybackOpcode` | `SyncTypes.h` | Enum |
| 1.12 | Add `CAP_SUPPORTS_PLAYBACK_CONTROL = 0x100` | `SyncTypes.h` | Capability |
| 1.13 | Update `UE_LOCAL_CAPABILITIES` bitmask | `SyncTypes.h` | Bitmask |
| 1.14 | Update protocol signature (add 0x19, 16, 8) | Both sides | Signature |
| 1.15 | Write wire format tests | `tests/phase7f_stage1_wire.py` | 20+ tests |

**Validation gate**: 20+ wire format tests PASS. All constants match both sides. Round-trip serialization/deserialization verified.

### Stage 2 — UE Handler

| Step | Description | File | Deliverable |
|------|-------------|------|-------------|
| 2.1 | Add `HandlePlaybackOp()` declaration to `.h` | `.h` | Declaration |
| 2.2 | Add playback control state members (player, sequence, flags) | `.h` | State |
| 2.3 | Add `0x19` dispatch case in `ProcessBinaryPacket` | `.cpp` | Dispatch |
| 2.4 | Add `0x19` to `kValidTypes[]` | `.cpp` | Whitelist |
| 2.5 | Implement `HandlePlaybackOp()`: header parse, opcode switch | `.cpp` | Handler |
| 2.6 | Implement lazy `ULevelSequencePlayer` creation | `.cpp` | Player lifecycle |
| 2.7 | Implement PLAY/PAUSE/STOP command execution | `.cpp` | Playback |
| 2.8 | Implement SCRUB_TO_FRAME with frame clamping | `.cpp` | Scrub |
| 2.9 | Implement SET_PLAYRATE with rate clamping | `.cpp` | Rate |
| 2.10 | Wire player destruction into CLEAR_SEQUENCE path | `.cpp` | Cleanup |
| 2.11 | Wire player destruction into `StopNetworkThread` / `ConsoleReset` | `.cpp` | Cleanup |
| 2.12 | Add `#include "LevelSequencePlayer.h"` | `.cpp` | Include |
| 2.13 | Add 9 counters + ConsoleReset/DumpState to `Diagnostics.inl` | `.inl` | Diagnostics |
| 2.14 | Write UE handler tests | `tests/phase7f_stage2_ue_handler.py` | 30+ tests |

**Validation gate**: 30+ handler tests PASS. All 5 opcodes execute correctly. Stale rejection, missing sequence, invalid opcode, frame clamping all verified.

### Stage 3 — Blender Detection

| Step | Description | File | Deliverable |
|------|-------------|------|-------------|
| 3.1 | Add playback control detection block to `check_updates()` | `sync.py` | Detection |
| 3.2 | Implement PLAY transition detection | `sync.py` | Detection |
| 3.3 | Implement PAUSE/STOP heuristic detection | `sync.py` | Detection |
| 3.4 | Implement SCRUB detection (frame change while not playing) | `sync.py` | Detection |
| 3.5 | Add `_last_playback_op_state` and reset on connect/disconnect | `sync.py` | State |
| 3.6 | Add `playback_control_packets_sent` and `commands` runtime stats | `sync.py` | Stats |
| 3.7 | Add `dump_diagnostics()` playback control section | `sync.py` | Diagnostics |
| 3.8 | Add `playback_control` BoolProperty to `__init__.py` (default OFF) | `__init__.py` | UI pref |
| 3.9 | Add `_on_playback_control_update` callback | `__init__.py` | Pref callback |
| 3.10 | Wire UI display in preferences `draw()` | `__init__.py` | UI draw |
| 3.11 | Write Blender detection tests | `tests/phase7f_stage3_detection.py` | 20+ tests |

**Validation gate**: 20+ detection tests PASS. All 5 transitions detected correctly. First-tick suppression, reconnect reset, and capability gating verified.

### Stage 4 — Validation & Closeout

| Step | Description | Deliverable |
|------|-------------|-------------|
| 4.1 | Run full wire format test suite (Stage 1) | 20+ PASS |
| 4.2 | Run full UE handler test suite (Stage 2) | 30+ PASS |
| 4.3 | Run full Blender detection test suite (Stage 3) | 20+ PASS |
| 4.4 | Run Phase 7E regression | 496/496 PASS |
| 4.5 | Run Phase 7B/C/D regression | ~544 PASS |
| 4.6 | Update STATUS.md Phase 7F entry | Documented |
| 4.7 | Update arch doc status to IMPLEMENTED | Documented |

**Validation gate**: 70+ Phase 7F tests PASS. All Phase 7E/7B/7C/7D regression tests PASS. STATUS.md updated.

---

## 9. Explicitly Out of Scope

| Feature | Reason | Deferred To |
|---------|--------|-------------|
| Sequencer UI opens/focuses | Requires `ISequencer` editor API | Out of scope (Phase 7 may revisit) |
| Viewport camera follows playback | Requires viewport integration (like Phase 7D) | Optional post-Phase 7F |
| Bidirectional sync (UE → Blender frame sync) | Reverse sync adds significant complexity | Out of scope |
| Real-time scrub sync during playback | Would require high-frequency frame updates | Deferred (Phase 7B timeline handles coarse sync) |
| Looping/play mode control | `bLoop` flag trivial to add; defer for scope control | Post-Phase 7F |
| Audio scrub/preview | Not related to Sequencer replication | Out of scope |
| Runtime PIE playback | Works automatically via `ULevelSequencePlayer` | Tested in Stage 4 |

---

## Appendix A — References

| Document | Purpose |
|----------|---------|
| `Docs/Architecture/54-phase7e-sequencer-keyframe-scope-lock.md` | Phase 7E architecture (parent document) |
| `Docs/Architecture/52-phase7-animation-sequencer-scope-lock.md` | Parent Phase 7 architecture |
| `STATUS.md` | Project phase tracking and regression status |
| `Blender_Addon/network.py` | Blender protocol definitions and serialization |
| `Blender_Addon/sync.py` | Blender detection loop and state machines |
| `UE_Plugin/.../Public/SyncTypes.h` | UE protocol definitions and payload structs |
| `UE_Plugin/.../Private/UELiveSyncSubsystem.cpp` | UE packet dispatch and handler implementations |

## Appendix B — Estimated Effort

| Stage | Description | Estimated Days |
|-------|-------------|----------------|
| 0 | Scope lock (this document) | 1 |
| 1 | Wire format + parser | 1 |
| 2 | UE handler | 2 |
| 3 | Blender detection | 1 |
| 4 | Validation + closeout | 1 |
| **Total** | | **6 days** |

## Appendix C — Comparison with Phase 7C (PT_PlaybackState)

| Aspect | PT_PlaybackState (0x14) | PT_PlaybackOp (0x19) |
|--------|------------------------|----------------------|
| Role | **Notification** ("I am playing") | **Command** ("execute play") |
| Trigger | State transition detected in Blender | User action or automated playback command |
| UE action | Storage only (stores `LastPlaybackState`) | Active (calls `ULevelSequencePlayer::Play()`) |
| Capability | None (implicit with pref) | `CAP_SUPPORTS_PLAYBACK_CONTROL = 0x100` |
| Payload | 14 bytes (state + loop + sequence + timestamp) | 16 bytes header + 0 or 8 bytes payload |
| Opcodes | 3 (PLAY, PAUSE, STOP) | 5 (PLAY, PAUSE, STOP, SCRUB, SET_PLAYRATE) |

Both coexist and serve different purposes:

1. `PT_PlaybackState` tells UE **what state Blender is in** (observability/storage)
2. `PT_PlaybackOp` tells UE **what action to perform** (playback control)
