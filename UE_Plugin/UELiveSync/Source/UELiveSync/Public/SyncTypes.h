#pragma once

#include "CoreMinimal.h"
#include "Math/Quat.h"
#include "SyncTypes.generated.h"

USTRUCT()
struct FSyncTransformState
{
    GENERATED_BODY()

    FVector CurrentLocation = FVector::ZeroVector;
    FVector TargetLocation = FVector::ZeroVector;
    FVector Velocity = FVector::ZeroVector;

    FQuat CurrentRotation = FQuat::Identity;
    FQuat TargetRotation = FQuat::Identity;

    FVector CurrentScale = FVector::OneVector;
    FVector TargetScale = FVector::OneVector;

    double LastUpdateTime = 0.0;
    float AdaptiveInterpSpeed = 12.0f;
    bool bInitialized = false;
};

#pragma pack(push, 1)
struct FPacketHeader
{
    // =========================
    // ORIGINAL SYSTEM (KEEP)
    // =========================
    uint32 Magic;

    uint32 PacketSize;
    uint32 ObjectCount;

    double Timestamp;

    // =========================
    // PHASE 3.2 ADDITIONS
    // =========================
    uint16 Version;        // NEW
    uint64 SequenceId;     // NEW
};
#pragma pack(pop)

struct FLiveSyncPacket
{
    TArray<uint8> RawData;
    double ReceiveTime = 0.0;
};

static constexpr uint32 LIVE_SYNC_MAGIC = 0x534E5955;
static constexpr uint16 LIVE_SYNC_VERSION = 1;