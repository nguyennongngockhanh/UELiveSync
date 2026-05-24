#pragma once

#include "CoreMinimal.h"

#include "Math/Quat.h"

#include "Misc/Guid.h"

#include <atomic>

#include "SyncTypes.generated.h"

DECLARE_LOG_CATEGORY_EXTERN(LogLiveSync, Log, All);

#include "AssetIdentityTypes.h"

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
    PT_Hierarchy = 0x02,
    PT_Create    = 0x03,
    PT_Delete    = 0x04,
    PT_Material  = 0x05,
    PT_Mesh      = 0x06,
    PT_Heartbeat = 0x07,
    PT_AssetDef  = 0x08,  // V5: asset identity definition
    PT_BeginSnapshot = 0x09,
    PT_EndSnapshot   = 0x0A,
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
