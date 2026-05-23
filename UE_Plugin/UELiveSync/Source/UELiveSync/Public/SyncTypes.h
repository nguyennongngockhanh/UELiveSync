#pragma once

#include "CoreMinimal.h"

#include "Math/Quat.h"

#include "Misc/Guid.h"

#include <atomic>

#include "SyncTypes.generated.h"

DECLARE_LOG_CATEGORY_EXTERN(LogLiveSync, Log, All);

// =========================================================
// TRANSFORM STATE
// =========================================================

USTRUCT()
struct FSyncTransformState
{
    GENERATED_BODY()

    // =====================================================
    // LOCATION
    // =====================================================

    FVector CurrentLocation =
        FVector::ZeroVector;

    FVector TargetLocation =
        FVector::ZeroVector;

    FVector Velocity =
        FVector::ZeroVector;

    // =====================================================
    // ROTATION
    // =====================================================

    FQuat CurrentRotation =
        FQuat::Identity;

    FQuat TargetRotation =
        FQuat::Identity;

    // =====================================================
    // SCALE
    // =====================================================

    FVector CurrentScale =
        FVector::OneVector;

    FVector TargetScale =
        FVector::OneVector;

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
    PT_Heartbeat = 0x07
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
    std::atomic<int32> PacketsReceived{0};
    std::atomic<int32> PacketsProcessed{0};
    std::atomic<int32> PacketsDropped{0};
    std::atomic<int32> MalformedPackets{0};
    int32 QueueDepthCurrent = 0;
    int32 QueueDepthPeak = 0;
    std::atomic<int32> ReconnectCount{0};
    double LastPacketTime = 0.0;
    double LastThreadLoopTime = 0.0;
    double AvgPacketsPerSecond = 0.0;
    double AvgBytesPerSecond = 0.0;
    double AvgProcessTimeMs = 0.0;
    std::atomic<int64> TotalBytesReceived{0};
};


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
