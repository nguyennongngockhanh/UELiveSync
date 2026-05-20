#pragma once

#include "CoreMinimal.h"

#include "Math/Quat.h"

#include "Misc/Guid.h"

#include "SyncTypes.generated.h"


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
// PACKET HEADER
// MUST EXACTLY MATCH BLENDER:
// <I H Q I I
// =========================================================

#pragma pack(push, 1)

struct FPacketHeader
{
    // =====================================================
    // MAGIC
    // =====================================================

    uint32 Magic;

    // =====================================================
    // PROTOCOL VERSION
    // =====================================================

    uint16 Version;

    // =====================================================
    // SEQUENCE ID
    // =====================================================

    uint64 SequenceId;

    // =====================================================
    // TOTAL PACKET SIZE
    // =====================================================

    uint32 PacketSize;

    // =====================================================
    // OBJECT COUNT
    // =====================================================

    uint32 ObjectCount;
};

#pragma pack(pop)


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


// =========================================================
// OBJECT LAYOUT
// 16 GUID
// 12 LOCATION
// 16 ROTATION
// 12 SCALE
// =========================================================

static constexpr int32
    LIVE_SYNC_OBJECT_SIZE =
        56;