#pragma once

// =========================================================
// SyncTypes.h — Protocol Structs, Constants, Packet Definitions
// =========================================================
// PHASE 5 COMPLETE — RUNTIME CORE FROZEN
//
// All packet structures, protocol version dispatch, and binary
// layout constants.  STABLE and FROZEN as of v0.5.0-stabilized.
//
// The 24-byte fixed-size header layout and the 81-byte V4+ object
// payload (with primitive type byte at offset 80) are wire-format
// invariants.  Changing any struct packing requires a protocol
// version bump.
//
// FSyncTransformState must remain POD-only — no FString additions.
// Asset metadata lives in a separate TMap<FGuid, FAssetMetadata>,
// NOT in FSyncTransformState.
//
// See Docs/Architecture/12-core-runtime-invariants.md
// =========================================================

#include "CoreMinimal.h"

#include "Math/Quat.h"

#include "Misc/Guid.h"

#include <atomic>

DECLARE_LOG_CATEGORY_EXTERN(LogLiveSync, Log, All);

#include "AssetIdentityTypes.h"

#include "SyncTypes.generated.h"

// =========================================================
// TRANSFORM STATE
// =========================================================

USTRUCT()
struct FSyncTransformState
{
    GENERATED_BODY()

    // =====================================================
    // WORLD-SPACE CURRENT STATE (root actors)
    // =====================================================
    // NON-AUTHORITATIVE for attached children.
    // Derived debug/fallback world-space cache only.
    // May become stale after parent movement.

    FVector CurrentLocation =
        FVector::ZeroVector;

    // =====================================================
    // WORLD-SPACE TARGET STATE (root actors)
    // =====================================================
    // NON-AUTHORITATIVE for attached children.
    // Derived debug/fallback world-space cache only.
    // May become stale after parent movement.

    FVector TargetLocation =
        FVector::ZeroVector;

    // World-space velocity for root prediction only.
    // Unused for attached children.
    FVector Velocity =
        FVector::ZeroVector;

    // =====================================================
    // WORLD-SPACE CURRENT ROTATION (root actors)
    // =====================================================
    // NON-AUTHORITATIVE for attached children.
    // Derived debug/fallback world-space cache only.
    // May become stale after parent movement.

    FQuat CurrentRotation =
        FQuat::Identity;

    // =====================================================
    // WORLD-SPACE TARGET ROTATION (root actors)
    // =====================================================
    // NON-AUTHORITATIVE for attached children.
    // Derived debug/fallback world-space cache only.
    // May become stale after parent movement.

    FQuat TargetRotation =
        FQuat::Identity;

    // =====================================================
    // WORLD-SPACE CURRENT SCALE (root actors)
    // =====================================================
    // NON-AUTHORITATIVE for attached children.
    // Derived debug/fallback world-space cache only.
    // May become stale after parent movement.

    FVector CurrentScale =
        FVector::OneVector;

    // =====================================================
    // WORLD-SPACE TARGET SCALE (root actors)
    // =====================================================
    // NON-AUTHORITATIVE for attached children.
    // Derived debug/fallback world-space cache only.
    // May become stale after parent movement.

    FVector TargetScale =
        FVector::OneVector;

    // =====================================================
    // LOCAL-SPACE CURRENT STATE (attached children only)
    // =====================================================
    // Authoritative interpolation state for attached children.
    // Local-space: relative to parent's world transform.
    // Root actors do not use these fields.

    FVector CurrentLocalLocation =
        FVector::ZeroVector;

    FQuat CurrentLocalRotation =
        FQuat::Identity;

    // Assumes stable mostly-uniform hierarchical scale behavior.
    // Correct non-uniform hierarchical scale propagation is deferred.
    FVector CurrentLocalScale =
        FVector::OneVector;

    // =====================================================
    // LOCAL-SPACE TARGET STATE (attached children only)
    // =====================================================

    FVector LocalTargetLocation =
        FVector::ZeroVector;

    FQuat LocalTargetRotation =
        FQuat::Identity;

    // Assumes stable mostly-uniform hierarchical scale behavior.
    // Correct non-uniform hierarchical scale propagation is deferred.
    FVector LocalTargetScale =
        FVector::OneVector;

    // True when LocalTarget* fields hold valid authoritative target.
    bool bHasLocalTarget =
        false;

    // =====================================================
    // SCENE GRAPH WRITE PENDING FLAG
    // =====================================================
    // SET when:
    //   - meaningful transform target change received
    //   - parent relationship changes
    //   - deferred attachment successfully resolves
    //   - initialization requires first world push
    //
    // CLEAR when:
    //   - world-space scene graph mutation succeeds
    //   - attachment transition completes successfully
    //
    // Do NOT clear merely because:
    //   - interpolation advanced internally
    //   - CurrentLocal* changed
    //   - actor tick executed

    bool bPendingSceneGraphWrite =
        false;

    // =====================================================
    // PARENT GUID
    // =====================================================

    FGuid ParentGuid;

    bool bHasParent =
        false;

    // =====================================================
    // TIMING
    // =====================================================

    double LastUpdateTime =
         0.0;

    // =====================================================
    // INTERPOLATION
    // =====================================================

    float AdaptiveInterpSpeed =
         12.0f;

    // =====================================================
    // STATE
    // =====================================================

    bool bInitialized =
        false;
};


// =========================================================
// PACKET TYPES (V3)
// =========================================================

enum EPacketType : uint8
{
    PT_Transform = 0x01,
    PT_Reserved_02 = 0x02,  // Legacy — was PT_Hierarchy in early Phase 3; unused
    PT_Create      = 0x03,
    PT_Delete    = 0x04,
    PT_Material  = 0x05,
    PT_Mesh      = 0x06,
    PT_Heartbeat = 0x07,
    PT_AssetDef  = 0x08,  // V5: asset identity definition
    PT_BeginSnapshot = 0x09,
    PT_EndSnapshot   = 0x0A,

    // Phase 6: Semantic editor-event replication
    // See Docs/Architecture/19-phase6-vertical-slice-rename.md
    PT_Visibility    = 0x0B,  // Semantic visibility toggle (discrete, NOT state stream)
    PT_Rename        = 0x0C,  // Semantic rename event (discrete, NOT state stream)

    // Phase 6D: Hierarchy replication (semantic attachment event)
    // See Docs/Architecture/24-phase6D-hierarchy-scope-lock.md
    PT_Hierarchy     = 0x0D,  // Semantic attach/detach event (discrete, NOT state stream)

    // Phase 6E: Lifecycle/delete replication (identity-destruction event)
    // See Docs/Architecture/29-phase6E-lifecycle-scope-lock.md
    PT_Delete_V5      = 0x0E,  // V5+ delete with sequence + tombstone semantics

    // Phase 6F: Collection/group replication (metadata-only grouping layer)
    // See Docs/Architecture/38-phase6F-collection-scope-lock.md
    PT_Collection     = 0x0F,  // Collection membership events (metadata, NOT scene graph)
};

// Phase 6: Provenance for editor-originating mutations
// In-memory only — NOT serialized on the wire.
// See Docs/Architecture/13-phase6-design-constraints.md §12
enum class EChangeOrigin : uint8
{
    Unspecified        = 0,  // Bug — every mutation must set provenance
    LocalUser          = 1,  // Direct user action in local editor → replicates
    RemoteReplicated   = 2,  // Received from remote peer → must NOT re-replicate
    Replay             = 3,  // Reconnect snapshot replay → must NOT replicate
    Recovery           = 4,  // Actor recovery (re-spawn, re-link) → must NOT replicate
};

// Primitive type constants (1 byte in CREATE packet payload)
enum ELiveSyncPrimitiveType : uint8
{
    LSP_Cube     = 0x00,
    LSP_Sphere   = 0x01,
    LSP_Cylinder = 0x02,
    LSP_Plane    = 0x03,
    LSP_Empty    = 0x04,
};


// =========================================================
// PACKET FLAGS (V3)
// =========================================================

enum EPacketFlags : uint8
{
    PF_None             = 0x00,
    PF_HasLocalTransform = 0x01,
    PF_FullSnapshot     = 0x02,
    PF_RequestAck       = 0x04
};


// =========================================================
// BINARY PROTOCOL LAYOUT (single source of truth)
// =========================================================
// All values little-endian. All structs packed (no padding).
//
// V3+ HEADER (24 bytes):
//   offset  size  field          Python struct
//   0       4     Magic (0x4C56534D)  I
//   4       2     Version              H
//   6       1     PacketType           B
//   7       1     Flags                B
//   8       8     SequenceId           Q
//   16      4     PacketSize           I
//   20      4     ObjectCount          I
//
// V2 HEADER (22 bytes, legacy):
//   0       4     Magic                I
//   4       2     Version              H
//   6       8     SequenceId           Q
//   14      4     PacketSize           I
//   18      4     ObjectCount          I
//
// V3+ TRANSFORM OBJECT (80 bytes):
//   0       16    GUID (4×uint32)      IIII
//   16      12    Location (3×float)   fff
//   28      16    Rotation (4×float)   ffff
//   44      12    Scale (3×float)      fff
//   56      8     Timestamp (double)   d
//   64      16    Parent GUID          IIII
//
// V4+ adds 1-byte PrimitiveType after Parent GUID (81 bytes total)
// for ALL packet types (Blender always includes it).
//
// V3 DELETE (16 bytes): just GUID (IIII)
// V5 ASSET DEF (33 bytes): IIII QQ B
// =========================================================

// =========================================================
// V2 HEADER (legacy)
// MUST EXACTLY MATCH BLENDER:
// <I H Q I I
// =========================================================

#pragma pack(push, 1)

struct FPacketHeader
{
    uint32 Magic;
    uint16 Version;
    uint64 SequenceId;
    uint32 PacketSize;
    uint32 ObjectCount;
};

#pragma pack(pop)


// =========================================================
// V3 HEADER
// <I H B B Q I I
// =========================================================

#pragma pack(push, 1)

struct FPacketHeaderV3
{
    uint32 Magic;
    uint16 Version;
    uint8  PacketType;
    uint8  Flags;
    uint64 SequenceId;
    uint32 PacketSize;
    uint32 ObjectCount;
};

#pragma pack(pop)


// =========================================================
// REPLAY TRACE CATEGORIES (Phase 6F Stage 7)
// =========================================================

enum class EReplayTraceCategory : uint8
{
    None           = 0,
    ReplayTrace    = 0x01,  // General replay tracing
    ReplayValidate = 0x02,  // Sequence / ordering validation
    ReplayCorrupt  = 0x04,  // Corruption / checksum events
    ReplayRollback = 0x08,  // Rollback / divergence events
    All            = 0x0F,
};

ENUM_CLASS_FLAGS(EReplayTraceCategory);


// =========================================================
// REPLAY TRACE RUNTIME CONFIG (Phase 6F Stage 7)
// =========================================================

struct FReplayTraceConfig
{
    bool bTracingEnabled = false;
    EReplayTraceCategory CategoryMask = EReplayTraceCategory::None;
    bool bStrictDiagnostics = true;  // true=Strict, false=Relaxed
};


// =========================================================
// REPLAY TIMELINE EVENT (Phase 6F Stage 7)
// =========================================================
// A single recorded timeline entry documenting a replay
// operation outcome. Non-mutating — captures results for
// forensic inspection after the fact.
// =========================================================

enum class ELiveSyncReplayResult : uint8
{
    Accepted      = 0,
    Rejected      = 1,
    Corrupted     = 2,
    Diverged      = 3,
    RolledBack    = 4,
    Skipped       = 5,
    SequenceGap   = 6,
    OutOfOrder    = 7,
    BufferOverflow = 8,
};

struct FReplayTimelineEvent
{
    int32      Index           = -1;     // Replay buffer index
    uint32     Sequence        = 0;      // Packet sequence number
    ELiveSyncReplayResult Result = ELiveSyncReplayResult::Rejected;
    double     Timestamp       = 0.0;    // Wall-clock time of event
    uint8      OpType          = 0;      // Collection op type (if applicable)
    int32      PayloadSize     = 0;      // Payload size (30 or 46)
    uint64     StateHashAfter  = 0;      // Collection hash after (if accepted)

    FString ToString() const
    {
        return FString::Printf(
            TEXT("[idx=%d seq=%u result=%d op=0x%02X size=%d hash=0x%016llX]"),
            Index, Sequence, static_cast<int32>(Result),
            OpType, PayloadSize, StateHashAfter);
    }
};


// =========================================================
// REPLAY TIMELINE (Phase 6F Stage 7)
// =========================================================
// Bounded ring buffer of replay timeline events.
// Capacity: 1024 events (FIFO eviction on overflow).
// Written by game thread during ReplayCollectionStream().
// Read by diagnostics/console dump.
// =========================================================

struct FReplayTimeline
{
    static constexpr int32 MAX_EVENTS = 1024;

    TArray<FReplayTimelineEvent> Events;
    int32 NextIndex = 0;
    int32 TotalRecorded = 0;

    void Record(const FReplayTimelineEvent& Event)
    {
        if (Events.Num() < MAX_EVENTS)
        {
            Events.Add(Event);
        }
        else
        {
            Events[NextIndex] = Event;
        }
        NextIndex = (NextIndex + 1) % MAX_EVENTS;
        TotalRecorded++;
    }

    void Clear()
    {
        Events.Empty();
        NextIndex = 0;
        TotalRecorded = 0;
    }

    int32 Num() const { return Events.Num(); }
};


// =========================================================
// REPLAY WINDOW STATISTICS (Phase 6F Stage 7)
// =========================================================
// Rolling window of recent replay metrics for diagnostics.
// =========================================================

struct FReplayWindowStats
{
    static constexpr int32 WINDOW_SIZE = 120;  // 120 samples

    TArray<double> DurationSamples;     // Replay durations (ms)
    TArray<double> RebuildSamples;      // Rebuild durations (ms)
    TArray<double> HashVerifySamples;   // Hash verify durations (ms)
    int32 NextDurationIndex = 0;
    int32 NextHashIndex = 0;

    void RecordDuration(double Ms)
    {
        if (DurationSamples.Num() < WINDOW_SIZE)
            DurationSamples.Add(Ms);
        else
            DurationSamples[NextDurationIndex] = Ms;
        NextDurationIndex = (NextDurationIndex + 1) % WINDOW_SIZE;
    }

    void RecordRebuild(double Ms)
    {
        if (RebuildSamples.Num() < WINDOW_SIZE)
            RebuildSamples.Add(Ms);
        else
            RebuildSamples[NextDurationIndex] = Ms;
    }

    void RecordHashVerify(double Ms)
    {
        if (HashVerifySamples.Num() < WINDOW_SIZE)
            HashVerifySamples.Add(Ms);
        else
            HashVerifySamples[NextHashIndex] = Ms;
        NextHashIndex = (NextHashIndex + 1) % WINDOW_SIZE;
    }

    double AvgDurationMs() const
    {
        if (DurationSamples.Num() == 0) return 0.0;
        double Sum = 0.0;
        for (double D : DurationSamples) Sum += D;
        return Sum / DurationSamples.Num();
    }

    double AvgRebuildMs() const
    {
        if (RebuildSamples.Num() == 0) return 0.0;
        double Sum = 0.0;
        for (double D : RebuildSamples) Sum += D;
        return Sum / RebuildSamples.Num();
    }

    double AvgHashVerifyMs() const
    {
        if (HashVerifySamples.Num() == 0) return 0.0;
        double Sum = 0.0;
        for (double D : HashVerifySamples) Sum += D;
        return Sum / HashVerifySamples.Num();
    }

    void Clear()
    {
        DurationSamples.Empty();
        RebuildSamples.Empty();
        HashVerifySamples.Empty();
        NextDurationIndex = 0;
        NextHashIndex = 0;
    }
};


// =========================================================
// RUNTIME METRICS (lock-free, atomics)
// =========================================================

struct FLiveSyncStats
{
    // --- Raw counters (atomics, written by any thread) ---
    std::atomic<int32> PacketsReceived{0};
    std::atomic<int32> PacketsProcessed{0};
    std::atomic<int32> PacketsDropped{0};
    std::atomic<int32> MalformedPackets{0};
    std::atomic<int32> ReconnectCount{0};
    std::atomic<int64> TotalBytesReceived{0};

    // --- Queue state (written by game thread only) ---
    int32 QueueDepthCurrent = 0;
    int32 QueueDepthPeak = 0;

    // --- Asset diagnostics (written by game thread) ---
    std::atomic<int32> AssetDefsReceived{0};
    std::atomic<int32> AssetDefsSkipped{0};
    std::atomic<int32> AssetAssignmentsSucceeded{0};
    std::atomic<int32> AssetAssignmentsFailed{0};
    std::atomic<int32> AssetLookupsAttempted{0};
    std::atomic<int32> AssetLookupsFailed{0};
    int32 PendingAssetCount   = 0;
    int32 PendingAssetPeak    = 0;
    int32 StaleEvictions      = 0;

    // --- Rename diagnostics (Phase 6, written by game thread) ---
    std::atomic<int32> RenamesProcessed{0};
    std::atomic<int32> RenameStaleRejections{0};
    std::atomic<int32> RenameReplayApplied{0};
    std::atomic<int32> RenameReplaySkipped{0};

    // --- Visibility diagnostics (Phase 6, written by game thread) ---
    std::atomic<int32> VisibilityProcessed{0};
    std::atomic<int32> VisibilityStaleRejections{0};
    std::atomic<int32> VisibilityReplayApplied{0};
    std::atomic<int32> VisibilityReplaySkipped{0};

    // --- Hierarchy diagnostics (Phase 6D, written by game thread) ---
    std::atomic<int32> HierarchyPackets{0};            // Total PT_Hierarchy packets received
    std::atomic<int32> HierarchyProcessed{0};          // Individual attach/detach events applied (live)
    std::atomic<int32> HierarchyStaleRejections{0};    // Stale/duplicate sequence rejections
    std::atomic<int32> HierarchyReplayApplied{0};      // Events applied from snapshot replay
    std::atomic<int32> HierarchyReplaySkipped{0};      // Events skipped during replay (already up-to-date)
    std::atomic<int32> HierarchyOrphans{0};            // Deferred retries — parent not yet found
    std::atomic<int32> HierarchyCycles{0};             // Cycle detected and rejected
    std::atomic<int32> HierarchyDeferredResolved{0};   // Deferred entries resolved (parent found)

    // --- Lifecycle/delete diagnostics (Phase 6E, written by game thread) ---
    std::atomic<int32> DeletePackets{0};               // Total PT_Delete_V5 packets received
    std::atomic<int32> DeleteProcessed{0};             // Delete events accepted and applied (live)
    std::atomic<int32> DeleteReplayApplied{0};         // Delete events applied from snapshot replay
    std::atomic<int32> DeleteReplaySkipped{0};         // Delete events skipped during replay
    std::atomic<int32> DeleteStaleRejections{0};       // Stale/duplicate sequence rejections
    std::atomic<int32> DeleteTombstoneRejections{0};   // Packet blocked by tombstone check
    std::atomic<int32> DeleteMissingActor{0};          // Delete for GUID not in ActorCache
    std::atomic<int32> DeleteDeferredDuringSnapshot{0};// Delete deferred during snapshot replay (CREATE not yet processed)

    // --- Collection diagnostics (Phase 6F, written by game thread) ---
    std::atomic<int32> CollectionPacketsReceived{0};    // Total PT_Collection packets received
    std::atomic<int32> CollectionStaleRejected{0};       // Stale/duplicate sequence rejections
    std::atomic<int32> CollectionDuplicateRejected{0};   // Exact duplicate sequence rejections
    std::atomic<int32> CollectionAddsApplied{0};         // ADD membership mutations applied
    std::atomic<int32> CollectionRemovesApplied{0};      // REMOVE membership mutations applied
    std::atomic<int32> CollectionMovesApplied{0};        // MOVE membership mutations applied
    std::atomic<int32> CollectionClearsApplied{0};       // CLEAR membership mutations applied
    std::atomic<int32> CollectionReplayProcessed{0};     // Packets replayed from ring buffer
    std::atomic<int32> CollectionReplayRejected{0};      // Replay packets rejected (stale/malformed)
    std::atomic<int32> CollectionSnapshotHashMismatch{0};// Snapshot hash divergence detected
    std::atomic<int32> CollectionSnapshotRebuilds{0};    // Full snapshot rebuilds completed
    std::atomic<int32> CollectionReplaySequenceGap{0};   // Replay sequence gap detected
    std::atomic<int32> CollectionReplayOutOfOrder{0};    // Replay out-of-order insertion detected
    std::atomic<int32> CollectionReplayDivergence{0};     // Replay divergence detected
    std::atomic<int32> CollectionReplayCorruption{0};     // Replay corruption detected
    std::atomic<int32> CollectionReplayRollbacks{0};      // Replay rollbacks performed

    // --- Collection observability (Phase 6F Stage 7, written by game thread) ---
    std::atomic<int32> CollectionReplayTimelineRecorded{0};    // Timeline events recorded
    std::atomic<int32> CollectionReplayTracesEmitted{0};       // Verbose trace lines emitted
    std::atomic<int32> CollectionReplayBufferOverflow{0};      // Replay buffer overflow count
    std::atomic<int32> CollectionReplayPacketsTruncated{0};    // Replay packets truncated
    std::atomic<int32> CollectionReplayPacketsDropped{0};      // Replay packets dropped (FIFO)
    std::atomic<int32> CollectionReplayPeakBufferUsage{0};     // Peak replay buffer entries
    std::atomic<int32> CollectionReplayLatencySamples{0};      // Replay latency metric samples
    std::atomic<int32> CollectionReplayReconnectRebuilds{0};   // Reconnects requiring rebuild
    std::atomic<int32> CollectionReplayReconnectPacketsReplayed{0};  // Packets replayed on reconnect
    std::atomic<int32> CollectionReplayReconnectDivergences{0};     // Divergences detected on reconnect
    std::atomic<int32> CollectionReplayReconnectRollbacks{0};       // Rollbacks on reconnect

    // --- Unified world replay (Phase 6G, written by game thread) ---
    std::atomic<int32> WorldReplayEntriesRecorded{0};            // Total unified entries recorded
    std::atomic<int32> WorldReplayVerifications{0};              // World replay verification runs
    std::atomic<int32> WorldReplayDivergences{0};                // World-state hash mismatches
    std::atomic<int32> WorldReplayRollbacks{0};                  // Cross-domain rollbacks
    std::atomic<int32> WorldReplayCorruption{0};                 // Corrupted entries detected
    std::atomic<int32> WorldReplayDependencyViolations{0};       // Cross-domain dependency violations
    std::atomic<int32> WorldReplaySnapshotExports{0};            // World snapshot exports
    std::atomic<int32> WorldReplaySnapshotRebuilds{0};           // World snapshot rebuilds
    std::atomic<int32> WorldReplayReconnectRebuilds{0};          // Reconnect world rebuilds
    std::atomic<int32> WorldReplayReconnectDivergences{0};       // Reconnect world divergences

    // --- Per-frame timing (written by game thread) ---
    double LastPacketTime = 0.0;
    double LastThreadLoopTime = 0.0;
    double AvgProcessTimeMs = 0.0;    // instantaneous per-packet, feeds EMA

    // --- Rolling averages (EMA, updated by game thread tick) ---
    double PacketsPerSecondEMA = 0.0;
    double BytesPerSecondEMA = 0.0;
    double ProcessTimeMsEMA = 0.0;

    // --- Peak tracking (game thread) ---
    double PeakProcessTimeMs = 0.0;
    double PeakPacketsPerSecond = 0.0;
    double PeakBytesPerSecond = 0.0;

    // --- Safety monitors (game thread) ---
    int32 FloodWarnings = 0;
    int32 QueuePressureWarnings = 0;
    double LastFloodWarningTime = 0.0;
    double LastQueuePressureTime = 0.0;
    double LastMetricsLogTime = 0.0;
};

// =========================================================
// METRICS HELPER — Event history ring buffers
// =========================================================

struct FReconnectEvent
{
    double Timestamp = 0.0;
    int32 AttemptNumber = 0;
};

struct FOverflowEvent
{
    double Timestamp = 0.0;
    int32 QueueDepth = 0;
};

static constexpr int32
    MAX_RECONNECT_HISTORY = 32;

static constexpr int32
    MAX_OVERFLOW_HISTORY = 32;


// =========================================================
// QUEUED NETWORK PACKET
// =========================================================

struct FLiveSyncPacket
{
    TArray<uint8> RawData;

    double ReceiveTime =
        0.0;
};


// =========================================================
// RENAME PACKET (Phase 6, PT_Rename = 0x0C)
// =========================================================
// Discrete semantic editor-event payload.
// NOT a state-stream packet — rename is a lifecycle-sensitive,
// ordering-sensitive, provenance-carrying mutation.
//
// Wire format (variable length):
//   offset  size  field
//   0       16    GUID (4 × uint32 LE)
//   16      2     old_name_length (uint16 LE)
//   18      N     old_name (UTF-8)
//   18+N    2     new_name_length (uint16 LE)
//   20+N    M     new_name (UTF-8)
//   20+N+M  4     sequence_number (uint32 LE, monotonic per-GUID)
//   24+N+M  8     timestamp (double LE)
//
// Provenance (EChangeOrigin) is in-memory only — NOT on the wire.
// See Docs/Architecture/19-phase6-vertical-slice-rename.md §5
//
// Max total payload: 16 + 2 + 256 + 2 + 256 + 4 + 8 = 544 bytes
// =========================================================

struct FLiveSyncRenamePacket
{
    FGuid    Guid;
    FString  OldName;
    FString  NewName;
    uint32   SequenceNumber = 0;   // Monotonic per-GUID (replay dedup)
    double   Timestamp      = 0.0;

    // In-memory provenance (not serialized)
    EChangeOrigin Origin = EChangeOrigin::Unspecified;
};


// =========================================================
// RENAME SEQUENCE TRACKER (Phase 6)
// =========================================================
// Tracks the last-applied rename sequence number per GUID
// for stale/duplicate replay rejection.
// =========================================================

struct FRenameSequenceTracker
{
    TMap<FGuid, uint32> LastSequence;
    static constexpr uint32 MAX_TRACKED_GUIDS = 2048;

    bool IsStaleOrDuplicate(const FGuid& Guid, uint32 IncomingSeq)
    {
        if (const uint32* LastSeq = LastSequence.Find(Guid))
        {
            return IncomingSeq <= *LastSeq;
        }
        return false; // first sequence for this GUID — always process
    }

    void Update(const FGuid& Guid, uint32 AppliedSeq)
    {
        if (LastSequence.Num() >= MAX_TRACKED_GUIDS)
        {
            // Evict oldest entry if at capacity
            LastSequence.Remove(LastSequence.CreateIterator().Key());
        }
        LastSequence.Add(Guid, AppliedSeq);
    }
};


// =========================================================
// VISIBILITY PACKET (Phase 6, PT_Visibility = 0x0B)
// =========================================================
// Discrete semantic editor-event payload.
// NOT a state-stream packet — visibility is a discrete toggle,
// not a continuously sampled value.
//
// Wire format (fixed 29 bytes per object):
//   offset  size  field
//   0       16    GUID (4 × uint32 LE)
//   16      1     bHidden (uint8: 0=visible, 1=hidden in editor)
//   17      4     sequence_number (uint32 LE, monotonic per-GUID)
//   21      8     timestamp (double LE)
//
// Provenance (EChangeOrigin) is in-memory only — NOT on the wire.
// See Docs/Architecture/21-phase6-vertical-slice-visibility.md §2
//
// Fixed payload: 16 + 1 + 4 + 8 = 29 bytes
// =========================================================


// =========================================================
// VISIBILITY SEQUENCE TRACKER (Phase 6)
// =========================================================
// Tracks the last-applied visibility sequence number per GUID
// for stale/duplicate replay rejection.
// =========================================================

struct FVisibilitySequenceTracker
{
    TMap<FGuid, uint32> LastSequence;
    static constexpr uint32 MAX_TRACKED_GUIDS = 2048;

    bool IsStaleOrDuplicate(const FGuid& Guid, uint32 IncomingSeq)
    {
        if (const uint32* LastSeq = LastSequence.Find(Guid))
        {
            return IncomingSeq <= *LastSeq;
        }
        return false;
    }

    void Update(const FGuid& Guid, uint32 AppliedSeq)
    {
        if (LastSequence.Num() >= MAX_TRACKED_GUIDS)
        {
            LastSequence.Remove(LastSequence.CreateIterator().Key());
        }
        LastSequence.Add(Guid, AppliedSeq);
    }
};


// =========================================================
// HIERARCHY PACKET (Phase 6D, PT_Hierarchy = 0x0D)
// =========================================================
// Discrete semantic editor-event payload for attachment intent.
// NOT a state-stream packet — hierarchy changes are discrete
// editor mutations, not continuously sampled values.
//
// Wire format (fixed 44 bytes per object):
//   offset  size  field
//   0       16    Child GUID (4 × uint32 LE)
//   16      16    Parent GUID (4 × uint32 LE, all-zero = detach-to-root)
//   32       4    sequence_number (uint32 LE, monotonic per-GUID)
//   36       8    timestamp (double LE)
//
// Provenance (EChangeOrigin) is in-memory only — NOT on the wire.
// See Docs/Architecture/24-phase6D-hierarchy-scope-lock.md
//
// Fixed payload: 16 + 16 + 4 + 8 = 44 bytes
// =========================================================


// =========================================================
// HIERARCHY SEQUENCE TRACKER (Phase 6D)
// =========================================================
// Tracks the last-applied hierarchy sequence number per child
// GUID for stale/duplicate replay rejection.
//
// Identity is child GUID (the object whose parent changes).
// Bounded at 2048 entries, evicts oldest on overflow.
// See Docs/Architecture/24-phase6D-hierarchy-scope-lock.md §5.1
// =========================================================

struct FHierarchySequenceTracker
{
    TMap<FGuid, uint32> LastSequence;
    static constexpr uint32 MAX_TRACKED_GUIDS = 2048;

    bool IsStaleOrDuplicate(const FGuid& ChildGuid, uint32 IncomingSeq)
    {
        if (const uint32* LastSeq = LastSequence.Find(ChildGuid))
        {
            return IncomingSeq <= *LastSeq;
        }
        return false;
    }

    void Update(const FGuid& ChildGuid, uint32 AppliedSeq)
    {
        if (LastSequence.Num() >= MAX_TRACKED_GUIDS)
        {
            LastSequence.Remove(LastSequence.CreateIterator().Key());
        }
        LastSequence.Add(ChildGuid, AppliedSeq);
    }
};


// =========================================================
// DELETE SEQUENCE TRACKER (Phase 6E, PT_Delete_V5 = 0x0E)
// =========================================================
// Tracks the last-applied delete sequence number per GUID
// for stale/duplicate replay rejection.
//
// Identity is the target GUID (the object being deleted).
// Bounded at 2048 entries, evicts oldest on overflow.
// Cleared on StopNetworkThread and ConsoleReset.
//
// Three-barrier stale rejection:
//   1. Sequence tracker (intra-connection)
//   2. Tombstone map (intra-connection, after first delete)
//   3. ActorCache existence check (cross-connection)
//
// See Docs/Architecture/29-phase6E-lifecycle-scope-lock.md §3.4
// =========================================================

struct FDeleteSequenceTracker
{
    TMap<FGuid, uint32> LastSequence;
    static constexpr uint32 MAX_TRACKED_GUIDS = 2048;

    bool IsStaleOrDuplicate(const FGuid& Guid, uint32 IncomingSeq)
    {
        if (const uint32* LastSeq = LastSequence.Find(Guid))
        {
            return IncomingSeq <= *LastSeq;
        }
        return false;
    }

    void Update(const FGuid& Guid, uint32 AppliedSeq)
    {
        if (LastSequence.Num() >= MAX_TRACKED_GUIDS)
        {
            LastSequence.Remove(LastSequence.CreateIterator().Key());
        }
        LastSequence.Add(Guid, AppliedSeq);
    }

    void Clear()
    {
        LastSequence.Empty();
    }
};


// =========================================================
// COLLECTION SEQUENCE TRACKER (Phase 6F, PT_Collection = 0x0F)
// =========================================================
// Tracks the last-applied collection sequence number per GUID
// for stale/duplicate replay rejection.
//
// Per-GUID monotonic sequence tracking. Bounded at 2048 entries,
// evicts oldest on overflow. Cleared on StopNetworkThread and
// ConsoleReset.
//
// See Docs/Architecture/38-phase6F-collection-scope-lock.md
// and 39-phase6F-vertical-slice-collection.md
// =========================================================

struct FCollectionSequenceTracker
{
    TMap<FGuid, uint32> LastSequence;
    static constexpr uint32 MAX_TRACKED_GUIDS = 2048;

    bool IsStaleOrDuplicate(const FGuid& Guid, uint32 IncomingSeq)
    {
        if (const uint32* LastSeq = LastSequence.Find(Guid))
        {
            return IncomingSeq <= *LastSeq;
        }
        return false;
    }

    void Update(const FGuid& Guid, uint32 AppliedSeq)
    {
        if (LastSequence.Num() >= MAX_TRACKED_GUIDS)
        {
            LastSequence.Remove(LastSequence.CreateIterator().Key());
        }
        LastSequence.Add(Guid, AppliedSeq);
    }

    void Clear()
    {
        LastSequence.Empty();
    }
};


// =========================================================
// COLLECTION PACKET CONSTANTS (Phase 6F)
// =========================================================
// Wire format (30 bytes base per operation):
//   offset  size  field
//   0       16    TargetGuid (4 × uint32 LE)
//   16       1    OpType (uint8: ADD/REMOVE/MOVE/etc.)
//   17       1    OpFlags (uint8: bitmask)
//   18       4    sequence_number (uint32 LE, monotonic per-(TargetGuid,CollectionGuid))
//   22       8    timestamp (double LE)
//
// Membership operations (ADD/REMOVE/MOVE/CLEAR) append an
// additional CollectionGuid(16) for 46 bytes total.
// Collection-identity operations (COLLECTION_CREATE/DELETE/
// RENAME/REPARENT) use the TargetGuid as the collection GUID
// and are 30 bytes total.
//
// Stage 1-3 implementation: parse base 30 bytes only.
// Extended parsing deferred to later stages.
//
// See Docs/Architecture/39-phase6F-vertical-slice-collection.md §1.2
// =========================================================

static constexpr int32
    LIVE_SYNC_COLLECTION_BASE_SIZE =
        30;

static constexpr int32
    LIVE_SYNC_COLLECTION_MEMBERSHIP_SIZE =
        46;

// Collection operation types (OpType field)
static constexpr uint8
    COLLECTION_OP_ADD            = 0x01,
    COLLECTION_OP_REMOVE         = 0x02,
    COLLECTION_OP_MOVE           = 0x03,
    COLLECTION_OP_CLEAR          = 0x04,
    COLLECTION_OP_RENAME_REF     = 0x05,
    COLLECTION_OP_COLLECTION_CREATE    = 0x06,
    COLLECTION_OP_COLLECTION_DELETE    = 0x07,
    COLLECTION_OP_COLLECTION_REPARENT  = 0x08;

// Collection packet versioning (Phase 6F Stage 5)
static constexpr uint8
    COLLECTION_PACKET_VERSION_V1 = 0x01;  // Stage 4+ wire format

// Collection packet header flag: bit 0 = sub-header present
static constexpr uint8
    COLLECTION_PACKET_FLAG_HAS_SUBHEADER = 0x01;

// Collection packet payload sub-header size (version + reserved)
static constexpr int32
    LIVE_SYNC_COLLECTION_SUBHEADER_SIZE =
        2;

// Collection replay: max recorded packets in ring buffer (2048)
static constexpr int32
    LIVE_SYNC_COLLECTION_MAX_REPLAY =
        2048;

// Collection snapshot schema version (Stage 6)
static constexpr int32
    COLLECTION_SNAPSHOT_SCHEMA_VERSION = 1;

// Replay ordering validation modes
enum class ECollectionReplayOrderMode : uint8
{
    None    = 0,  // No ordering validation (Stage 5 compat)
    Strict  = 1,  // Strict monotonic sequence order
    Relaxed = 2   // Allow same-sequence duplicates (merge)
};


// =========================================================
// UNIFIED REPLAY DOMAIN (Phase 6G)
// =========================================================
// Domain classification for replay packet entries.
// Used by the unified replay system to categorize and
// process replay entries across all synchronization domains.
// =========================================================

enum class EWorldReplayDomain : uint8
{
    Unknown      = 0,
    Collection   = 1,  // PT_Collection (collection membership/identity)
    Lifecycle    = 2,  // PT_Create, PT_Delete, PT_Delete_V5
    Rename       = 3,  // PT_Rename
    Transform    = 4,  // PT_Transform (state-sampled, not raw-stream)
};


// =========================================================
// UNIFIED REPLAY ENTRY (Phase 6G)
// =========================================================
// A single replay entry for the unified replay buffer.
// Stores all metadata needed for deterministic replay
// verification across all domains.
// =========================================================

struct FWorldReplayEntry
{
    EWorldReplayDomain Domain   = EWorldReplayDomain::Unknown;
    uint8              PacketType = 0;    // Original PT_* constant
    FGuid              Guid;              // Primary GUID (target/child)
    FGuid              SecondaryGuid;     // Secondary GUID (parent/collection)
    uint32             Sequence  = 0;     // Domain-level sequence number
    double             Timestamp = 0.0;   // Packet timestamp
    TArray<uint8>      Payload;           // Canonical payload bytes
    uint32             Checksum  = 0;     // FNV-1a of payload

    bool IsValid() const
    {
        return Domain != EWorldReplayDomain::Unknown
            && (Guid.IsValid() || Payload.Num() > 0);
    }
};


// =========================================================
// UNIFIED WORLD-STATE SNAPSHOT (Phase 6G)
// =========================================================
// Captures the synchronized world state at a point in time.
// Used for rollback save/restore, hash verification, and
// deterministic snapshot export/rebuild.
// =========================================================

struct FWorldStateSnapshot
{
    // Collection domain state
    TMap<FGuid, FGuid>     CollectionMembership;  // object → collection (flat map)
    TMap<FGuid, FString>   CollectionIdentities;  // collection → name
    TMap<FGuid, uint32>    CollectionSequences;   // per-GUID last sequence

    // Lifecycle domain state
    TSet<FGuid>            ActiveActors;          // GUIDs with live actors
    TMap<FGuid, uint32>    DeleteSequences;       // per-GUID delete sequence

    // Rename domain state
    TMap<FGuid, FString>   ActorNames;            // GUID → display name

    // Transform domain state (lightweight)
    int32                  TransformCount   = 0;  // Number of tracked transforms
    uint64                 TransformHash    = 0;  // Combined hash of all transforms

    // Metadata
    double                 CaptureTime = 0.0;
    static constexpr int32 SCHEMA_VERSION = 1;

    bool operator==(const FWorldStateSnapshot& Other) const
    {
        // TMap<FGuid,FGuid>
        if (CollectionMembership.Num() != Other.CollectionMembership.Num()) return false;
        for (const auto& KV : CollectionMembership)
        {
            const FGuid* V = Other.CollectionMembership.Find(KV.Key);
            if (!V || *V != KV.Value) return false;
        }
        // TMap<FGuid,FString>
        if (CollectionIdentities.Num() != Other.CollectionIdentities.Num()) return false;
        for (const auto& KV : CollectionIdentities)
        {
            const FString* V = Other.CollectionIdentities.Find(KV.Key);
            if (!V || *V != KV.Value) return false;
        }
        // TMap<FGuid,uint32>
        if (CollectionSequences.Num() != Other.CollectionSequences.Num()) return false;
        for (const auto& KV : CollectionSequences)
        {
            const uint32* V = Other.CollectionSequences.Find(KV.Key);
            if (!V || *V != KV.Value) return false;
        }
        // TSet<FGuid>
        if (ActiveActors.Num() != Other.ActiveActors.Num()) return false;
        for (const FGuid& K : ActiveActors)
        {
            if (!Other.ActiveActors.Contains(K)) return false;
        }
        // TMap<FGuid,uint32>
        if (DeleteSequences.Num() != Other.DeleteSequences.Num()) return false;
        for (const auto& KV : DeleteSequences)
        {
            const uint32* V = Other.DeleteSequences.Find(KV.Key);
            if (!V || *V != KV.Value) return false;
        }
        // TMap<FGuid,FString>
        if (ActorNames.Num() != Other.ActorNames.Num()) return false;
        for (const auto& KV : ActorNames)
        {
            const FString* V = Other.ActorNames.Find(KV.Key);
            if (!V || *V != KV.Value) return false;
        }
        return TransformCount == Other.TransformCount
            && TransformHash  == Other.TransformHash;
    }

    bool operator!=(const FWorldStateSnapshot& Other) const
    {
        return !(*this == Other);
    }

    void Clear()
    {
        CollectionMembership.Empty();
        CollectionIdentities.Empty();
        CollectionSequences.Empty();
        ActiveActors.Empty();
        DeleteSequences.Empty();
        ActorNames.Empty();
        TransformCount = 0;
        TransformHash = 0;
        CaptureTime = 0.0;
    }
};


// =========================================================
// PROTOCOL CONSTANTS
// =========================================================

static constexpr uint32
    LIVE_SYNC_MAGIC =
    0x4C56534D;

static constexpr uint16
    LIVE_SYNC_VERSION =
    2;

static constexpr uint16
    LIVE_SYNC_VERSION_V3 =
    3;

static constexpr uint16
    LIVE_SYNC_VERSION_V4 =
    4;

static constexpr uint16
    LIVE_SYNC_VERSION_V5 =
    5;


// =========================================================
// V2 OBJECT LAYOUT
// 16 GUID (hex)
// 12 LOCATION
// 16 ROTATION
// 12 SCALE
// =========================================================

static constexpr int32
    LIVE_SYNC_OBJECT_SIZE =
        56;


// =========================================================
// V3 TRANSFORM OBJECT LAYOUT
// 16 GUID (4 × uint32)
// 12 LOCATION
// 16 ROTATION
// 12 SCALE
//  8 TIMESTAMP (double)
// 16 PARENT GUID (4 × uint32)
// =========================================================

static constexpr int32
    LIVE_SYNC_V3_OBJECT_SIZE =
        80;

// V4+ object size: 80 (V3) + 1 (primitive type byte) = 81
static constexpr int32
    LIVE_SYNC_V4_OBJECT_SIZE =
        81;

static constexpr int32
    LIVE_SYNC_V3_DELETE_SIZE =
        16;

// =========================================================
// V5 ASSET DEF OBJECT LAYOUT
// 16 GUID
// 16 IDENTITY HASH (2 × uint64)
//  1 PRIMITIVE FALLBACK (uint8)
// =========================================================

static constexpr int32
    LIVE_SYNC_V5_ASSET_DEF_SIZE =
        33;

// Maximum total packet size (header + payload) — 512 KB
static constexpr int32
    LIVE_SYNC_MAX_PACKET_SIZE =
        512 * 1024;

// =========================================================
// PROTOCOL SIGNATURE
// =========================================================
// Deterministic FNV-1a hash of protocol constants.
// Logged at startup on both Blender and UE.
// If Blender and UE show different signatures, the protocol
// has drifted and binary compatibility is broken.
// =========================================================

static constexpr uint32
    LIVE_SYNC_PROTOCOL_SIG =
        []() constexpr -> uint32
{
    // FNV-1a 32-bit
    constexpr uint32 FNV_OFFSET = 2166136261u;
    constexpr uint32 FNV_PRIME  = 16777619u;

    auto fnv = [](uint32 h, uint8 b) constexpr
    {
        return (h ^ b) * FNV_PRIME;
    };

    auto fnv_u32 = [&](uint32 h, uint32 v) constexpr
    {
        h = fnv(h,  v        & 0xFF);
        h = fnv(h, (v >>  8) & 0xFF);
        h = fnv(h, (v >> 16) & 0xFF);
        h = fnv(h, (v >> 24) & 0xFF);
        return h;
    };

    auto fnv_u16 = [&](uint32 h, uint16 v) constexpr
    {
        h = fnv(h,  v        & 0xFF);
        h = fnv(h, (v >>  8) & 0xFF);
        return h;
    };

    uint32 H = FNV_OFFSET;

    // Magic
    H = fnv_u32(H, 0x4C56534D);
    // Versions
    H = fnv_u16(H, 2); H = fnv_u16(H, 3);
    H = fnv_u16(H, 4); H = fnv_u16(H, 5);
    // Header sizes
    H = fnv(H, 24); H = fnv(H, 22);
    // Object sizes
    H = fnv(H, 80); H = fnv(H, 81);
    H = fnv(H, 16); H = fnv(H, 33);
    H = fnv(H, 28); // LIVE_SYNC_DELETE_V5_SIZE
    H = fnv(H, 30); // LIVE_SYNC_COLLECTION_BASE_SIZE
    H = fnv(H, 46); // LIVE_SYNC_COLLECTION_MEMBERSHIP_SIZE
    H = fnv(H, COLLECTION_PACKET_VERSION_V1); // collection packet version
    // Packet types
    H = fnv(H, 0x01); H = fnv(H, 0x03);
    H = fnv(H, 0x04); H = fnv(H, 0x07);
    H = fnv(H, 0x08); H = fnv(H, 0x09);
    H = fnv(H, 0x0A); H = fnv(H, 0x0B); H = fnv(H, 0x0C); H = fnv(H, 0x0D);
    H = fnv(H, 0x0E); // PT_Delete_V5
    H = fnv(H, 0x0F); // PT_Collection

    return H;
}();

// Compile-time size checks for packet headers
static_assert(
    sizeof(FPacketHeader) == 22,
    "FPacketHeader must be exactly 22 bytes (V2 layout)");

static_assert(
    sizeof(FPacketHeaderV3) == 24,
    "FPacketHeaderV3 must be exactly 24 bytes (V3+ layout)");

// Object size checks
static_assert(
    LIVE_SYNC_OBJECT_SIZE == 56,
    "V2 object must be exactly 56 bytes");

static_assert(
    LIVE_SYNC_V3_OBJECT_SIZE == 80,
    "V3 object must be exactly 80 bytes (without V4+ prim type)");

static_assert(
    LIVE_SYNC_V4_OBJECT_SIZE == 81,
    "V4+ object must be exactly 81 bytes (80 V3 + 1 prim type)");

static_assert(
    LIVE_SYNC_V3_DELETE_SIZE == 16,
    "V3 delete must be exactly 16 bytes");

// =========================================================
// V5+ DELETE (PT_Delete_V5) OBJECT LAYOUT
// 16 GUID (4 × uint32)
//  4 SEQUENCE NUMBER (uint32 LE, monotonic per-GUID)
//  8 TIMESTAMP (double LE)
// =========================================================
//
// Wire format (fixed 28 bytes per object):
//   offset  size  field
//   0       16    Target GUID (4 × uint32 LE)
//   16       4    sequence_number (uint32 LE, monotonic per-GUID)
//   20       8    timestamp (double LE)
//
// Discrete terminal semantic mutation — NOT reversible, NOT a state stream.
// See Docs/Architecture/29-phase6E-lifecycle-scope-lock.md §3.1
// =========================================================

static constexpr int32
    LIVE_SYNC_DELETE_V5_SIZE =
        28;

static_assert(
    LIVE_SYNC_V5_ASSET_DEF_SIZE == 33,
    "V5 asset def must be exactly 33 bytes");

static_assert(
    LIVE_SYNC_DELETE_V5_SIZE == 28,
    "V5+ delete must be exactly 28 bytes");
