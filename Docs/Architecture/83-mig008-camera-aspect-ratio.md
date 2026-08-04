# MIG-008: Camera Aspect Ratio Schema Completion (0x1B PT_CameraDef)

## Status: COMPLETE (runtime PASS)

MIG-008 closes the sole remaining schema gap between the semantic `CAMERA_CREATE`
(0x50) / `CAMERA_UPDATE` (0x51) messages and the legacy `PT_CameraDef` (0x1B) wire
packet: the `aspect_ratio` field (Blender render aspect ratio). After this migration,
the semantic camera schema achieves full field parity with PT_CameraDef. Legacy is
retained in dual-emission until a separate decommission ADR. Runtime acceptance:
**PASS**.

## Summary

ADR-81 claimed CAMERA_CREATE/UPDATE were missing "4 fields: clip planes, ortho".
Investigation proved this stale — the current codebase already includes `clip_start`,
`clip_end`, `ortho_scale`, and `camera_flags` in both semantic messages. The sole
genuinely missing field was **`aspect_ratio`** (float64): Blender render aspect ratio
computed as `(resolution_x * pixel_aspect_x) / (resolution_y * pixel_aspect_y)`.

`HandleCameraDef` applies aspect ratio to `UCameraComponent::AspectRatio` + sets
`bConstrainAspectRatio = true`. `ApplyCameraParams` (called by `OnCameraCreate`) and
`OnCameraUpdate` did not — a comment explicitly stated "Aspect ratio is owned by
PT_CameraDef (single source of truth)." MIG-008 adds the field to the semantic
schema, applies it in `ApplyCameraParams` and `OnCameraUpdate`, and removes the
ownership comment. `ApplyCameraParams` is the single point of application (user-approved
design constraint).

## Runtime Acceptance

```
UE PID: 124747 | Blender PID: 126506
Port 57000: LISTEN

CAMERA_CREATE:
  [BRIDGE][CAMERA_CREATE] aspect=1.7778 seq=1
  [CAMERA][DEF_APPLY] persp: FOV=39.6 aspect=1.78 — legacy path applied ✓
  Semantic path: OnCameraCreate → ApplyCameraParams → CamComp->AspectRatio ✓

CAMERA_UPDATE (focal length changed):
  [BRIDGE][CAMERA_UPDATE] has_aspect=1 aspect=1.7778 seq=35
  [CAMERA][UPDATE] Applied delta aspect=1 seq=35 — semantic applied ✓
  [CAMERA][DEF_RECV] aspect=1.7778 — legacy path carried identical value ✓

Dual-emission: both paths carry aspect=1.7778 ✓
Result: PASS
```

## What Was Changed

### Blender addon (`Blender_Addon/`)

- `object_protocol.py`: Added `aspect_ratio: float = 0.0` parameter to
  `build_camera_create()` and `build_camera_update()`. Packed as `float64 LE` after
  `timestamp` (the last field) — backward compatible with old deserializers.
- `sync.py`: Pass `aspect_ratio=render_aspect_ratio(bpy.context)` to both builders
  (CAMERA_CREATE at active camera change, CAMERA_UPDATE at parameter dirty detection).

### Shared protocol (`Shared/`)

- `Protocol/MessageTypes.yaml`: Added `aspect_ratio` (float64, optional: true) to both
  CAMERA_CREATE and CAMERA_UPDATE specs, appended after timestamp. Marked optional for
  backward compat with old senders.
- `Serializer/livesync_messages.h`: Added `aspect_ratio` parameter (default 0.0) to
  `serialize_body_camera_create` and `serialize_body_camera_update`. Packed after
  timestamp.
- `Serializer/livesync_deserializer.h`: Added `aspect_ratio` deserialization using
  `state.offset < msg.total_size` guard — old messages without the field are handled
  gracefully. Both CAMERA_CREATE and CAMERA_UPDATE cases updated.

### UE plugin (`UE_Plugin/UELiveSync/Source/UELiveSync/`)

- `Public/LiveSyncViews.h`: Added `double AspectRatio` to `CameraCreateView`; added
  `bool HasAspectRatio` + `double AspectRatio` to `CameraUpdateView`.
- `Public/LiveSyncProtocolBridge.h`: Updated `BuildCameraCreateView` and
  `BuildCameraUpdateView` to read `aspect_ratio` via `TryGetField<double>` (backward
  compat). Updated `LogCameraCreate` and `LogCameraUpdate` to include aspect ratio.
- `Private/UELiveSyncSubsystem.cpp`:
  - `ApplyCameraParams`: Now applies `AspectRatio` + `bConstrainAspectRatio = true`
    for both projection modes. Removed "owned by PT_CameraDef" comment. Single point
    of application (user-approved).
  - `OnCameraUpdate`: Added aspect ratio delta block — applies when `HasAspectRatio &&
    AspectRatio > 0.0`. Updated summary log to include `aspect=%d`.

### Tests

- `test_property.cpp`: Added `aspect_ratio` random value and assertion to both
  `test_property_camera_create` and `test_property_camera_update`.
- `test_serialization.py`: Added `aspect_ratio: 1.7778` to camera default fields and
  `TestCameraOptionalParent` test.
- `test_cross_language.cpp`: Pass `aspect_ratio` from manifest fields to camera
  serializers.
- `generate_vectors.py`: Added `aspect_ratio` to CAMERA_CREATE (1.7778) and
  CAMERA_UPDATE (2.3333) golden vectors.
- pytest: **58 passed**.
- `validate_protocol.py`: **10/10 PASS**.
- `cross_language_verify.py`: **33/33 PASS**.
- Vectors regenerated (`--force`).

## Design Decisions

- **D1** — `aspect_ratio` appended after `timestamp` (the last field). Both C++ and
  Python deserializers do not validate full body consumption — trailing bytes from old
  messages are absent, new field is gracefully absent for old senders. No field shift,
  no version bump needed.
- **D2** — `aspect_ratio` is `optional: true` in YAML for both messages. New senders
  always include it; old senders omit it. The `TryGetField` / `state.offset < size`
  guard handles both cases.
- **D3** — `ApplyCameraParams` is the single point of application (user-approved).
  Both `OnCameraCreate` and `OnCameraUpdate` reach it via their respective paths.
  `HandleCameraDef` applies it independently via the legacy path.
- **D4** — `aspect_ratio` is `float64` (not `float32`) to match the precision of
  `timestamp` and the existing `render_aspect_ratio()` Python function which returns
  a Python float (native double).
- **D5** — No changes to `HandleCameraDef` or the legacy path — it already applies
  aspect ratio correctly.

## Invariants Preserved

- Legacy `PT_CameraDef` (0x1B) path unchanged: `serialize_camera_def`,
  `HandleCameraDef`, `kValidTypes`, dispatch block.
- `HandleCameraDef` remains independent — does not call `ApplyCameraParams`.
- FNV handshake, protocol signature, `kValidTypes`, `kValidFlags` all unchanged.
- Dual-emission pattern preserved: legacy and semantic fire in same if-block.
- Camera stale-rejection sequences unchanged (`GCameraCreateSequences` /
  `GCameraUpdateSequences` inline static TMaps).
- All existing tests pass (58 pytest + 10 validate + 33 cross-language).

## Next Steps

- Legacy `PT_CameraDef` decommission requires a separate ADR after:
  - Semantic path proven stable over multiple sessions.
  - Blender emitter switched to semantic-only.
  - `kValidTypes` / dispatcher dropped.
- MIG order: 007 (done) → 008 (done) → 010 Material → 009 Mesh.
