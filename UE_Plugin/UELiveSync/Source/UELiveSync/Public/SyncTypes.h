#pragma once

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
    // Packet types
    H = fnv(H, 0x01); H = fnv(H, 0x03);
    H = fnv(H, 0x04); H = fnv(H, 0x07);
    H = fnv(H, 0x08); H = fnv(H, 0x09);
    H = fnv(H, 0x0A);

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

static_assert(
    LIVE_SYNC_V5_ASSET_DEF_SIZE == 33,
    "V5 asset def must be exactly 33 bytes");
