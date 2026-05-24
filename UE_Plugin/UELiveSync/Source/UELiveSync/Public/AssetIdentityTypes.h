#pragma once

#include "CoreMinimal.h"

#include "UObject/SoftObjectPath.h"

// =========================================================
// ASSET IDENTITY (16-byte POD, allocation-free)
// =========================================================
// Used as cache/dedup key for asset resolution.
// xxHash64 of the Blender mesh datablock name.
// NOT a human-facing search key — lookup by naming convention.
// =========================================================

struct FAssetIdentityRef
{
    uint64 High = 0;
    uint64 Low  = 0;

    bool operator==(
        const FAssetIdentityRef& Other) const
    {
        return High == Other.High &&
               Low  == Other.Low;
    }

    bool operator!=(
        const FAssetIdentityRef& Other) const
    {
        return !(*this == Other);
    }

    bool IsValid() const
    {
        return High != 0 || Low != 0;
    }
};


inline uint32 GetTypeHash(
    const FAssetIdentityRef& Ref)
{
    return HashCombine(
        GetTypeHash(Ref.High),
        GetTypeHash(Ref.Low));
}


// =========================================================
// PER-OBJECT ASSET METADATA
// =========================================================
// Lives in a separate TMap<FGuid, FAssetMetadata> —
// NOT inside FSyncTransformState (keeps hot path POD-only).
// =========================================================

struct FAssetMetadata
{
    FAssetIdentityRef Identity;
    FSoftObjectPath   ResolvedPath;
    int32             RetryCount       = 0;
    double            NextRetryTime    = 0.0;
    double            RetryInterval    = 1.0;
    uint8             PrimitiveFallback = 0;  // LSP_Cube
    bool              bResolved        = false;
    bool              bFallbackAssigned = false;

    bool IsPending() const
    {
        return Identity.IsValid() &&
               !bResolved;
    }

    bool HasTimedOut(
        double Now) const
    {
        return IsPending() &&
               Now - NextRetryTime >
                   60.0;
    }
};


// =========================================================
// ASSET DIAGNOSTICS (lock-free, atomics)
// =========================================================

struct FAssetDiagnostics
{
    std::atomic<int32> AssetDefsReceived{0};
    std::atomic<int32> AssetDefsSkipped{0};
    std::atomic<int32> AssetAssignmentsSucceeded{0};
    std::atomic<int32> AssetAssignmentsFailed{0};
    std::atomic<int32> AssetLookupsAttempted{0};
    std::atomic<int32> AssetLookupsFailed{0};
    int32              PendingAssetCount     = 0;
    int32              PendingAssetPeak      = 0;
    int32              StaleEvictions         = 0;
    double             LastAssignmentLatencyMs = 0.0;
    double             LastResolutionTime     = 0.0;
};


// =========================================================
// CONSTANTS
// =========================================================

static constexpr int32
    MAX_ASSET_RESOLUTIONS_PER_TICK = 8;

static constexpr int32
    MAX_PENDING_ASSET_ENTRIES = 2048;

static constexpr int32
    MAX_ASSET_RETRY_ATTEMPTS = 5;

static constexpr double
    ASSET_STALE_TIMEOUT = 60.0;

static constexpr double
    ASSET_RETRY_INTERVAL_INITIAL = 1.0;

static constexpr double
    ASSET_RETRY_INTERVAL_MAX = 16.0;

static constexpr int32
    ASSET_DEF_OBJECT_SIZE = 33;
// GUID(16) + IdentityHash(16, 2×uint64) + PrimitiveFallback(1, uint8)
