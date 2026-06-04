# Phase 7E Stage 10A — Visibility Keyframes Scope Lock

**Date**: 2026-06-04
**Status**: SCOPE LOCK
**Depends on**: Phase 7E Stages 1–9B (Transform Keyframe Pipeline) ✅
**Blocks**: Nothing
**Related Docs**: `Docs/Architecture/54-phase7e-sequencer-keyframe-scope-lock.md`, `Docs/Architecture/21-phase6-vertical-slice-visibility.md`

---

## 1. Purpose

This document defines the scope and implementation plan for replicating Blender visibility animation FCurves (`hide_viewport`, `hide_render`) into UE5 Sequencer bool tracks via the existing PT_Keyframe (0x17) packet.

### 1.1 Relationship to Wider Phase 7E

| Stage | Scope | Status |
|-------|-------|--------|
| 1–9B | Transform keyframe pipeline (Loc/Rot/Scale) | ✅ IMPLEMENTED |
| **10A** | **Visibility keyframes** | **🔒 SCOPE LOCK** |
| 10B | Camera property keyframes | 🔒 PENDING |
| 10C | Interpolation/tangent mapping | 🔒 PENDING |

### 1.2 Key Design Decision: Extend PT_Keyframe vs. New Packet

**Decision: Extend PT_Keyframe (0x17).** Do not create a new packet type.

Rationale:
- PT_Keyframe already supports channels 0–255 (KEYFRAME_MIN_CHANNEL=0, KEYFRAME_MAX_CHANNEL=255).
- The existing 25-byte `FKeyframeEntry` (GUID 16B + Frame 4B + Value 4B + ChannelIndex 1B) carries a float `Value` field, which naturally represents 0.0 (visible) vs 1.0 (hidden).
- No changes to the wire format are required.
- The existing dispatch path, sequence monotonicity check, stale rejection, and `HandleKeyframe()` entry loop are all reused.

---

## 2. Wire Format (Unchanged)

### 2.1 Existing PT_Keyframe (0x17) Layout

```
FKeyframeHeader: 14 bytes
  Sequence[4] + Timestamp[8] + KeyCount[1] + Flags[1]

FKeyframeEntry: 25 bytes (identical to transform key entry)
  ObjectGUID[16] + Frame[4] + Value[4] + ChannelIndex[1]
```

### 2.2 Visibility Channel Assignment

| Channel | Blender FCurve | UE Track | Semantics |
|---------|---------------|----------|-----------|
| 9 | `hide_viewport` | `UMovieSceneBoolTrack` | 0.0 = visible, non-zero = hidden |
| 10 | `hide_render` | `UMovieSceneBoolTrack` | 0.0 = renderable, non-zero = not renderable |

Channel range 0–8 remains reserved for transform. Channels 11–255 reserved for future extension (camera properties, etc.).

### 2.3 Value Semantics

The float `Value` field in `FKeyframeEntry` is interpreted as:
- `0.0f` → `false` (visible, renderable)
- non-zero (`1.0f` expected) → `true` (hidden, not renderable)

This matches Blender's FCurve convention where `hide_viewport = 1` means hidden and `hide_viewport = 0` means visible, and maps directly to `FMovieSceneBoolChannel`'s `bool` values.

---

## 3. Blender Changes

### 3.1 `_KEYFRAME_CHANNEL_MAP` Extension

Add visibility entries to the channel map in `sync.py`:

```python
_KEYFRAME_CHANNEL_MAP = {
    # ... existing transform entries (0-8) ...
    ("hide_viewport", -1): 9,   # viewport visibility (bool)
    ("hide_render", -1): 10,    # render visibility (bool)
}
```

Note: Blender visibility FCurves have `data_path` like `"hide_viewport"` with no array index (or implicitly index 0). The `-1` sentinel indicates "any array index" matching.

### 3.2 Extraction

`_extract_keyframes()` already skips unmapped channels with `if channel is None: continue` at line 893. Adding channel 9 and 10 to the map is sufficient — no new extraction logic is needed.

### 3.3 GT

Visibility keyframe extraction is gated on the same `is_keyframe_effective()` check as transform keyframes:
- Local preference `keyframe_sync` enabled
- Remote capability `CAP_SUPPORTS_KEYFRAME_REPLICATION (0x20)` present
- Client connected

No separate preference or capability is needed for visibility keyframes — they are a sub-channel of the existing keyframe pipeline.

### 3.4 Duplicate Suppression

The existing `_hash_keyframes()` FNV-1a hash already covers all entry fields including `ChannelIndex`. Visibility keyframe entries with different channel values will produce different hashes, and will not be suppressed by prior transform keyframe hashes.

---

## 4. UE Changes

### 4.1 `HandleKeyframe()` Extension

Current flow (pseudocode):

```
if ChannelIndex > 8 → UnsupportedChannel, skip
FindOrCreateTrack<UMovieScene3DTransformTrack>
FindOrCreateSection
GetChannel<FMovieSceneDoubleChannel>(ChannelIndex)
AddLinearKey(frame, value)
```

New flow for channels 9–10:

```
if ChannelIndex == 9 or ChannelIndex == 10:
    FindOrCreateTrack<UMovieSceneBoolTrack>(Binding)
    FindOrCreateSection (UMovieSceneBoolSection)
    GetChannel<FMovieSceneBoolChannel>(0)  # single channel
    AddKey(frame, bool(value != 0.0f))
    KeysApplied++
    continue  # skip transform track path
```

### 4.2 `UnsupportedChannel` Counter

The `UnsupportedChannel` counter is incremented only for channels outside the total supported range (0–10 after Stage 10A). Channels 0–8 remain transform, channels 9–10 become visibility.

### 4.3 Includes

Required includes (already present or available in Runtime):

```cpp
#include "Tracks/MovieSceneBoolTrack.h"
#include "Sections/MovieSceneBoolSection.h"
#include "Channels/MovieSceneBoolChannel.h"
```

Same `#if WITH_EDITOR` guards as transform keyframe code.

### 4.4 No Changes to `HandleVisibility()` (0x0B)

The existing `HandleVisibility` handler at line 7157 continues to handle **discrete visibility toggle events** (PT_Visibility, 0x0B) which are semantic editor operations (snapshot replay, user toggle sync). Visibility **keyframes** (PT_Keyframe, channel 9–10) are a separate concern — they write to Sequencer bool tracks for animation playback.

Both can coexist: a visibility keyframe write to a bool track does not trigger the `HandleVisibility` path, and a discrete visibility toggle does not write to Sequencer.

---

## 5. Counter Additions

| Counter | Scope | Increment |
|---------|-------|-----------|
| `KeyframeVisibilityKeysApplied` | Per-key | When a visibility keyframe is successfully inserted into a bool channel |
| (reuse `KeyframeMissingBinding`) | Per-key | Already exists — no change |
| (reuse `KeyframeUnsupportedChannel`) | Per-key | Now channels outside 0–10 |

No new malformed/stale/received counters — the existing keyframe counters cover packet-level events.

ConsoleReset zeros the new counter. ConsoleDumpState prints it.

---

## 6. Failure Modes

| Failure Mode | Detection | Recovery |
|-------------|-----------|----------|
| FCurve `data_path` not in channel map | Skip in `_extract_keyframes()` | Silent no-op (existing behavior) |
| Channel 9/10 entry with missing binding | `KeyframeMissingBinding++` | Skip entry, continue loop |
| BoolChannel `AddKey` failure | UE return value check | `KeyframeUnsupportedChannel++` or diagnostic log |
| BoolTrack already exists with different section | `FindTrack` finds existing track | Reuse existing track and section |
| No active LevelSequence | Early return in HandleKeyframe | Silent no-op (existing behavior) |
| float→bool truncation near zero (0.001f) | `FMath::IsNearlyZero` check | Treat as visible |
| Blender hide_viewport/hide_render in same frame | Separate entries, both applied | Same-frame bool channel accepts single key per channel |

---

## 7. Edge Cases

1. **Same-frame toggle**: Visibility FCurve toggles visible→hidden→visible on same frame. BoolChannel `AddKey` overwrites on same frame — last value wins.
2. **No visibility FCurves**: `_extract_keyframes()` returns no entries for visibility channels. No packets sent. No tracks created.
3. **Hide-viewport only, no hide-render FCurve**: Channel 9 populated, channel 10 absent. Only one bool track created.
4. **Existing bool track from previous extraction**: Reused. No duplicate tracks.
5. **Clear sequence → recreate**: Sequence is destroyed and recreated. Tracks/sections are recreated on next keyframe packet.
6. **Channel 9 entry arrives when only transform FCurves animated**: Track creation fails silently (no binding), `KeyframeMissingBinding`.
7. **Duplicate FCurves**: Two FCurves for `hide_viewport` (Blender allows this). Both extract to channel 9. Behavior: duplicate keys added (BoolChannel overwrites duplicates).

---

## 8. Acceptance Criteria

### 8.1 Blender Extraction
- [ ] 1A: `_KEYFRAME_CHANNEL_MAP` includes `hide_viewport` → channel 9
- [ ] 1B: `_KEYFRAME_CHANNEL_MAP` includes `hide_render` → channel 10
- [ ] 1C: `_extract_keyframes()` produces channel 9/10 entries from Blender visibility FCurves
- [ ] 1D: Non-visibility, non-transform FCurves still skipped
- [ ] 1E: Existing transform channels 0–8 still extracted correctly
- [ ] 1F: Visibility bool value (True/False) serialized as float 1.0/0.0 in Value field

### 8.2 Wire Format
- [ ] 2A: No changes to `FKeyframeHeader` or `FKeyframeEntry` structs
- [ ] 2B: Visibility keyframe entries use existing 25-byte layout
- [ ] 2C: Wire-level monotonicity check still works unchanged
- [ ] 2D: Existing transform keyframe streams are not affected

### 8.3 UE Handling
- [ ] 3A: Channel 9 creates/finds `UMovieSceneBoolTrack` for the binding
- [ ] 3B: Channel 10 creates/finds `UMovieSceneBoolTrack` for the binding
- [ ] 3C: Channel 9 inserts key into `FMovieSceneBoolChannel` at correct frame
- [ ] 3D: Channel 10 inserts key into `FMovieSceneBoolChannel` at correct frame
- [ ] 3E: float 0.0 → bool `false` (visible)
- [ ] 3F: float 1.0 → bool `true` (hidden)
- [ ] 3G: float values near zero (< KINDA_SMALL_NUMBER) → bool `false`
- [ ] 3H: Same binding with both transform and visibility keys: separate tracks created
- [ ] 3I: Missing binding → `KeyframeMissingBinding++` (unchanged behavior)
- [ ] 3J: Channel 11+ → `KeyframeUnsupportedChannel++` (unchanged behavior)

### 8.4 Counter Validation
- [ ] 4A: `KeyframeVisibilityKeysApplied` increments per applied visibility key
- [ ] 4B: ConsoleReset zeros the new counter
- [ ] 4C: ConsoleDumpState prints the new counter
- [ ] 4D: Existing keyframe counters (`KeyframeKeysApplied`, etc.) not affected

### 8.5 Coexistence with Phase 6 Visibility
- [ ] 5A: PT_Visibility (0x0B) still works as a discrete toggle
- [ ] 5B: Visibility keyframes do not trigger HandleVisibility
- [ ] 5C: Visibility toggles do not write to Sequencer bool tracks

---

## 9. Out of Scope

- **Interpolation/tangent mapping** — deferred to Stage 10C. Bool channels do not support tangents.
- **Camera property keyframes** — deferred to Stage 10B.
- **Non-bool visibility** (alpha/opacity) — not supported by Blender hide_viewport/hide_render.
- **Visibility FCurve for collections or view layers** — object-level only.
- **Sequencer UI opening** — belongs in Phase 7F.
- **Live scrubbing** — belongs in Phase 7F.

---

## 10. Implementation Plan

### Stage 10A.1 — Blender Channel Map + Extraction (1 day)
- Add `hide_viewport` and `hide_render` to `_KEYFRAME_CHANNEL_MAP`
- Wire tests: verify extraction produces channel 9/10 entries with correct value semantics

### Stage 10A.2 — UE BoolTrack Handling (2 days)
- Add `#include` for bool track/section/channel
- Extend `HandleKeyframe()` channel dispatch for 9–10
- Add `KeyframeVisibilityKeysApplied` counter
- ConsoleReset/DumpState coverage
- Wire tests: verify track creation, key insertion, counter increment

### Stage 10A.3 — Integration + Edge Cases (1 day)
- Test coexistence with transform keys (same binding, same packet)
- Test float→bool boundary (0.0, 1.0, near-zero)
- Test clear-sequence lifecycle
- Test missing binding for visibility keys
- Test channel 11+ rejection unchanged

### Total: ~4 days

---

## 11. Chronology

```
2026-06-04  Stage 10A scope lock published
2026-06-05  Stage 10A.1 Blender extraction
2026-06-06  Stage 10A.2 UE BoolTrack
2026-06-07  Stage 10A.3 Integration
