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

    // NOTE: ResolvedPath is currently stored but NOT consumed by the
    // runtime resolution path.  ResolvePendingAssets() looks up the
    // path from AssetPathCache instead.  This field is reserved for
    // future Phase 7B asset registry integration where per-GUID
    // resolved path tracking becomes necessary (e.g. material mapping
    // or mesh variant fallback).  Currently populated with the default
    // empty FSoftObjectPath and never written.
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

// =========================================================
// MATERIAL IDENTITY (Phase 7B)
// =========================================================
// 16-byte POD key for Blender material datablock identity.
// xxHash64 of the Blender material name (slot.material.name).
// Deterministic across sessions. NOT stable across material renames.
// =========================================================

struct FMaterialIdentityRef
{
    uint64 High = 0;
    uint64 Low  = 0;

    bool operator==(
        const FMaterialIdentityRef& Other) const
    {
        return High == Other.High &&
               Low  == Other.Low;
    }

    bool operator!=(
        const FMaterialIdentityRef& Other) const
    {
        return !(*this == Other);
    }

    bool IsValid() const
    {
        return High != 0 || Low != 0;
    }
};


inline uint32 GetTypeHash(
    const FMaterialIdentityRef& Ref)
{
    return HashCombine(
        GetTypeHash(Ref.High),
        GetTypeHash(Ref.Low));
}


// =========================================================
// MATERIAL SLOT REF (Phase 7B)
// =========================================================
// Ordered pair: slot index + material identity.
// Slot index maps 1:1 from Blender material_slots[slot_index]
// to UStaticMeshComponent material slots.
// =========================================================

struct FMaterialSlotRef
{
    int8                SlotIndex   = -1;  // -1 = unassigned
    FMaterialIdentityRef Identity;

    bool IsValid() const
    {
        return SlotIndex >= 0 &&
               Identity.IsValid();
    }
};


// =========================================================
// MATERIAL DIAGNOSTICS (Phase 7B, reserved)
// =========================================================

static constexpr int32
    MAX_MATERIAL_SLOTS = 8;


// =========================================================
// MATERIAL BASIC PROPERTIES EXTENSION (Phase 10J.5H)
// =========================================================
// Optional per-slot basic material properties carried in the
// MATX extension block appended after the old identity block.
// =========================================================

static constexpr uint32
    MATX_MAGIC        = 0x4D415458;  // 'MATX' LE
static constexpr uint8
    MATX_VERSION_CURRENT = 1;
static constexpr int32
    MATX_HEADER_SIZE  = 6;         // Magic(4) + Version(1) + SlotCount(1)
static constexpr int32
    MATX_PROP_SLOT_SIZE = 25;     // SlotIndex(1) + RGBA(16) + Roughness(4) + Metallic(4)
// Total extension for N slots: MATX_HEADER_SIZE + N * MATX_PROP_SLOT_SIZE

struct FMaterialSlotBasicProperties
{
    bool        bHasProperties = false;
    FLinearColor BaseColor      = FLinearColor::White;
    float       Roughness       = 0.5f;
    float       Metallic        = 0.0f;
    float       Alpha           = 1.0f;
};

// =========================================================
// TEXTURE MAP IDENTITY EXTENSION (Phase 10K.1)
// =========================================================
// Optional MTEX extension block appended after MATX (or after
// the old identity block if MATX is absent). Carries texture
// map image paths per material slot for diagnostic/logging.
// No texture importing or applying in this stage.
// =========================================================

static constexpr uint32
    MTEX_MAGIC        = 0x4D544558;  // 'MTEX' LE
static constexpr uint8
    MTEX_VERSION_CURRENT = 1;
static constexpr int32
    MTEX_HEADER_SIZE  = 6;         // Magic(4) + Version(1) + RecordCount(1)

// MTEX channel enum
enum class EMTEXChannel : uint8
{
    BaseColor = 1,
    Roughness = 2,
    Metallic  = 3,
    Alpha     = 4,
    Normal    = 5
};

// MTEX flags
static constexpr uint8
    MTEX_FLAG_PATH_ABSOLUTE      = 0x01;
static constexpr uint8
    MTEX_FLAG_IMAGE_PACKED       = 0x02;
static constexpr uint8
    MTEX_FLAG_COLORSPACE_SRGB    = 0x04;
static constexpr uint8
    MTEX_FLAG_COLORSPACE_NONCOLOR = 0x08;

// Size bounds
static constexpr int32
    MTEX_MAX_PATH_LEN = 2048;
static constexpr int32
    MTEX_MAX_IMAGE_NAME_LEN = 255;

// Minimum per-record wire size: SlotIndex(1) + Channel(1) + Flags(1) + PathLen(2) + ImageNameLen(1) = 6
// Plus minimal path(0) + name(0) = 0, so total 6 minimum.
static constexpr int32
    MTEX_RECORD_MIN_SIZE = 6;

// Maximum per-record wire size: 6 + max_path(2048) + max_name(255) = 2309
static constexpr int32
    MTEX_RECORD_MAX_SIZE = 6 + MTEX_MAX_PATH_LEN + MTEX_MAX_IMAGE_NAME_LEN;

struct FMaterialTextureMapRef
{
    int8    SlotIndex   = -1;
    uint8   Channel     = 0;
    uint8   Flags       = 0;
    FString Path;
    FString ImageName;

    bool IsValid() const
    {
        return SlotIndex >= 0 && Channel >= 1 && Channel <= 5;
    }
};

// =========================================================
// MESH CHUNK CONSTANTS (Phase 7C)
// =========================================================
// PT_Mesh chunk header layout:
//   GUID(16) + VersionHash(64) + ChunkIndex(4) + ChunkCount(4) + Flags(1) = 89 bytes

static constexpr int32
    LIVE_SYNC_V5_MESH_CHUNK_HEADER_SIZE = 89;

static constexpr int32
    LIVE_SYNC_V5_MESH_VERSION_HASH_SIZE = 64;

static constexpr uint32
    MAX_CONCURRENT_MESH_REASSEMBLIES = 16;

// Chunk flags (bitmask)
static constexpr uint8
    MESH_CHUNK_FLAG_HAS_POSITIONS     = 0x01;

static constexpr uint8
    MESH_CHUNK_FLAG_HAS_TRIANGLES     = 0x02;

static constexpr uint8
    MESH_CHUNK_FLAG_HAS_MATERIAL_IDX  = 0x04;

static constexpr uint8
    MESH_CHUNK_FLAG_HAS_NORMALS       = 0x08;

static constexpr uint8
    MESH_CHUNK_FLAG_HAS_UVS           = 0x10;

static constexpr uint8
    MESH_CHUNK_FLAG_FIRST_CHUNK       = 0x20;

static constexpr uint8
    MESH_CHUNK_FLAG_LAST_CHUNK        = 0x40;

// Phase 7C Stage 2A: full-attribute schema gate flag
static constexpr uint8
    MESH_CHUNK_FLAG_FULL_ATTR         = 0x80;

static constexpr int32
    LIVE_SYNC_V5_MATERIAL_SLOT_SIZE = 17;
// SlotIndex(1) + MaterialLow(8) + MaterialHigh(8) = 17 bytes per slot

static constexpr int32
    LIVE_SYNC_V5_MATERIAL_OBJECT_BASE_SIZE = 17;
// GUID(16) + SlotCount(1) = 17 bytes base, then N × SLOT_SIZE per slot
