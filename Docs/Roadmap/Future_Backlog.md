# Future Backlog

> Deferred architectural items discovered during Phase 5 review.
> These items are explicitly **not part of Phase 5** and are queued for Phase 6 or later.

---

## 1. Packet Capture & Replay System

**Deferred — Not Part of Phase 5**

### Goal

Allow recording and replaying Live Sync packet streams for debugging, regression testing, and desync investigation.

### Motivation

Realtime networking bugs are difficult to reproduce. Most hierarchy, interpolation, and authority issues depend on precise packet timing and ordering. A replay system would allow deterministic debugging of these issues without requiring both Blender and UE to be running, and without relying on real-time scene manipulation to trigger the bug.

### Requirements

- Record raw packet streams to disk as they arrive on the UE network thread.
- Each packet is timestamped with the local receive time (monotonic clock).
- Per-connection ordering is preserved — each connection's stream is recorded independently.
- Replay sessions offline inside UE using the same `ProcessBinaryPacket()` and `InterpolateTransforms()` pipeline.
- Support deterministic playback speed (1×, 2×, 0.5×, or frame-stepped).
- Replay must work without Blender running — the recorded stream replaces the live socket.

### Potential File Format

The recorded stream could use a simple container format with a `.lspkt` extension:

```
[lspkt header]
  uint32     Magic           = 0x4C535052 ("LSPR")
  uint16     FormatVersion   = 1
  uint32     ConnectionCount

[for each connection:]
  [connection header]
    uint32  ConnectionId
    uint32  PacketCount
    double  StartTime         // first packet timestamp (seconds since epoch)
    double  EndTime           // last packet timestamp

  [for each packet in connection:]
    double  TimestampOffset   // seconds since StartTime
    uint32  PacketLength
    uint8[] RawPacketData     // identical to what the socket received
```

This format is append-friendly (record can be written live) and is self-describing enough for replay without a sidecar manifest.

### Behavior During Replay

- `LiveSyncRunnable` reads from the file instead of the socket.
- `Wait(10ms)` loop is replaced by a timer that releases packets at their original timestamps (scaled by playback speed).
- `FLiveSyncPacket::ReceiveTime` is set to the replay's virtual clock.
- The game thread `Tick()` pipeline (`ProcessQueuedPackets → InterpolateTransforms`) runs identically to live mode.

### Potential Future Features

- **Packet diffing**: Compare two replay recordings side-by-side to identify where a desync diverged.
- **Replay scrubbing**: Jump to any timestamp in the recording (requires building an index on first load).
- **Corruption injection testing**: Programmatically corrupt bytes in replayed packets to verify UE's protocol validation.
- **Network latency simulation**: Artificially delay packets during replay to test interpolation behavior under lag.
- **Export to diagnostic log**: Dump all packets in human-readable form for bug reports.

| File(s) | What |
|---------|------|
| `Public/LiveSyncRecorder.h` (new) | Declare `FLiveSyncRecorder` — thread-safe file writer with per-connection streams |
| `Private/LiveSyncRecorder.cpp` (new) | Implement `.lspkt` writer, flush-on-overflow, rotation |
| `Public/LiveSyncReplay.h` (new) | Declare `FLiveSyncReplay` — file reader with timer-based packet release |
| `Private/LiveSyncReplay.cpp` (new) | Implement playback loop, speed control, frame-step |
| `LiveSyncRunnable.cpp` | Accept either a socket (`FLiveSyncConnectionContext`) or a replay reader as the packet source |
| `UELiveSyncSubsystem.cpp` | Add CVar `UE.LiveSync.ReplayFile` — if set, read from replay instead of listening |
| `UELiveSyncSubsystem.cpp` | Add CVar `UE.LiveSync.RecordFile` — if set, write all received packets to disk |

**Design constraint**: Recording must be zero-overhead when disabled (no file I/O, no allocations). The recorder checks `RecordFile.IsEmpty()` once per packet.

---

## 2. Deterministic Tick Simulation Mode

**Deferred — Not Part of Phase 5**

### Goal

Provide a deterministic simulation/testing mode for validation and automated regression testing of the UE-side transform pipeline.

### Motivation

As hierarchy (5B), multi-connection (5E), and asset synchronization (5D) are added, the transform pipeline grows in complexity. Manual testing becomes insufficient to catch regressions. A deterministic mode allows automated tests to assert exact actor transforms at specific frame numbers, independent of wall-clock timing, scheduling jitter, or frame rate.

### Requirements

- **Fixed delta time**: The `Tick()` function receives a constant `DeltaTime` (default 1/60 s) regardless of real elapsed time.
- **Deterministic packet ordering**: Packets are dequeued in a fixed, repeatable order. No random tiebreakers.
- **Interpolation jitter disabled**: `FMath::VInterpTo` and `FQuat::Slerp` must produce bit-identical results on every run.
- **Reproducible transform evaluation**: Given the same packet sequence, `InterpolateTransforms()` must produce the same `SetActorTransform()` calls every time.
- **Compatible with automated tests**: The mode is enabled via a CVar or programmatic flag, not a build configuration.

### Implementation Notes

```cpp
// CVar:
static TAutoConsoleVariable<int32>
    CVarLiveSyncDeterministicMode(
        TEXT("UE.LiveSync.DeterministicMode"),
        0,
        TEXT("Enable deterministic tick simulation for testing (1=on, 0=off)"),
        ECVF_Cheat
    );

// In Tick():
float DeltaTime = CVarLiveSyncDeterministicMode.GetValueOnGameThread()
    ? (1.0f / 60.0f)
    : InDeltaTime;
```

Most of the pipeline is already deterministic — the main sources of non-determinism are:

1. **`DeltaTime` variance** — Fixed in the Tick override above.
2. **`FMath::FRand()` or `FMath::Rand()`** — Neither is used in the current pipeline; verify none are introduced in new code.
3. **`FPlatformTime::Seconds()`** — The timestamp field in `FSyncTransformState::LastUpdateTime` must use the deterministic clock when in simulation mode.
4. **Packet dequeue order** — The `FLiveSyncQueue` (MPSC bounded queue) is already deterministic: FIFO within a single connection.

### Potential Usage

- **Hierarchy regression tests**: Feed a recorded packet stream with parent-child transforms; assert child's final world position matches expected value.
- **Transform drift validation**: Run the same packet stream at 30 fps and 60 fps; verify actor positions converge to the same final state.
- **Packet ordering validation**: Deliberately reorder packets in replay; verify the sequence ID deduplication handles them correctly.
- **Automated CI testing**: The replay system (section 1) combined with deterministic mode allows fully automated pipeline tests in CI.

| File(s) | What |
|---------|------|
| `UELiveSyncSubsystem.cpp` | Add `CVarLiveSyncDeterministicMode`; override `DeltaTime` when enabled; use fixed clock |
| `UELiveSyncSubsystem.h` | Add `GetDeterministicTime()` helper that returns tick count × fixed interval |
| `LiveSyncQueue.h` | Verify FIFO order is deterministic (already true for MPSC); document the guarantee |

**Design constraint**: Deterministic mode must never ship enabled by default in a production editor. Mark the CVar as `ECVF_Cheat` to prevent accidental usage.

---

## 3. Asset Dependency Tracking

**Deferred — Not Part of Phase 5**

### Goal

Track relationships between Blender GUIDs and the UE assets they generate, enabling orphan cleanup, dependency validation, and future migration support.

### Motivation

Phase 5D introduces the FBX mesh pipeline (D2) and material parameter instances (D1). Over time, each GUID can accumulate multiple generated UE assets:

```
GUID <ue_guid>
 ├── /Game/LiveSync/Meshes/<GUID>.<GUID>              (mesh asset from FBX export)
 ├── MID_<GUID>                                         (dynamic material instance)
 ├── /Game/LiveSync/Materials/<GUID>_Material.<GUID>    (optional saved material asset)
 ├── /Game/LiveSync/Textures/<GUID>_Albedo.png          (extracted textures)
 ├── <blend_file>.fbx                                    (source FBX on disk)
 └── metadata                                            (export timestamp, version, hash)
```

Without tracking, the `Content/LiveSync/` directory fills with orphaned assets when:
- A Blender object is deleted but its FBX remains.
- A GUID is regenerated (collision recovery) and the old GUID's assets become unreferenced.
- A `.blend` file is renamed and re-exported, creating a duplicate set of assets.

### Requirements

- **Orphan asset cleanup** — Detect assets in `/Game/LiveSync/` whose GUID no longer exists in any active `TransformStates` entry, and offer to delete them.
- **Stale asset detection** — Detect FBX files on disk whose embedded hash differs from the current Blender mesh, indicating a re-export is needed.
- **Dependency validation** — On load, verify that each GUID's mesh asset exists. If missing, log a warning and fall back to the default cube.
- **Future package/export support** — The tracking table is the prerequisite for a "Package LiveSync Assets" operation that collects all dependencies into a standalone content directory.
- **Asset migration support** — When a GUID changes (regenerated), update all dependent asset filenames to match the new GUID.

### Potential Data Structure

```cpp
struct FLiveSyncAssetDependency
{
    FGuid Guid;

    // Mesh
    FString MeshAssetPath;          // /Game/LiveSync/Meshes/<GUID>
    FString SourceFBXPath;          // absolute path on disk
    FString SourceFBXHash;          // MD5 of FBX at export time
    double  LastMeshExportTime;

    // Material
    bool    bHasMaterialParams;
    FString MaterialAssetPath;      // empty if using MID only
    double  LastMaterialUpdateTime;

    // Metadata
    int32   ExportCount;
    double  FirstExportTime;
    bool    bOrphan;                // true if GUID not in TransformStates
};
```

### Potential Future Features

- **Automatic cleanup of abandoned assets**: Background scan every N minutes; delete orphans with a configurable age threshold. Log all deletions.
- **Dependency visualization**: A UE editor panel showing the dependency graph for each GUID, with status indicators (green = up-to-date, yellow = stale, red = missing).
- **Asset health diagnostics**: Console command `UE.LiveSync.AssetHealth` that prints a report of all tracked dependencies, orphans, and staleness.

| File(s) | What |
|---------|------|
| `Public/LiveSyncAssetTracker.h` (new) | Declare `FLiveSyncAssetTracker` — maps GUID to dependency record; serializes to disk |
| `Private/LiveSyncAssetTracker.cpp` (new) | Implement CRUD for dependency records; orphan scan; staleness check |
| `UELiveSyncSubsystem.cpp` | Call `AssetTracker.OnGuidCreated()`, `AssetTracker.OnGuidDeleted()`, `AssetTracker.OnMeshExported()` at appropriate points in the pipeline |
| `UELiveSyncSubsystem.h` | Add `FLiveSyncAssetTracker AssetTracker;` member |
| `UELiveSyncSubsystem.cpp` | Add `UE.LiveSync.CleanOrphans` console command |
| `sync.py` | (future) Send a manifest of active GUIDs on connect so UE can cross-reference its asset table |

**Design constraint**: The asset tracker is a pure data structure with no file I/O of its own during normal tick. Serialization to disk happens only on explicit save or at a configurable interval (default 60s). This prevents the tracker from adding I/O pressure to the game thread.

---

## Deferred Summary

| Feature | Phase | Why Deferred |
|---------|-------|-------------|
| Packet capture & replay | 6+ | No production-critical regressions to justify the investment yet |
| Deterministic tick simulation | 6+ | Needed only once hierarchy and multi-connection tests become automated |
| Asset dependency tracking | 6+ | FBX pipeline (5D) must ship first so there are assets to track |
| Binary mesh streaming over TCP | 6+ | FBX pipeline covers Phase 5 needs |
| Armature / skeletal mesh sync | 6+ | Requires pose-space transforms and bone remapping |
| Bidirectional handshake / ACK | 6+ | Blender receive thread adds threading risk |
| First-writer authority model | 6+ | Not needed until multi-connection usage data exists |
| Packet compression (zlib) | 6+ | Bandwidth not yet a bottleneck |
