#include "FBXImport/LiveSyncFBXImporter.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"

#include "Engine/StaticMeshActor.h"
#include "Engine/StaticMesh.h"
#include "Materials/Material.h"
#include "Engine/Texture.h"
#include "Components/SceneComponent.h"

#if WITH_EDITOR
#include "AssetToolsModule.h"
#include "AssetImportTask.h"
#include "Factories/FbxFactory.h"
#include "Factories/FbxImportUI.h"
#include "Factories/FbxStaticMeshImportData.h"
#include "ObjectTools.h"
#include "AutomatedAssetImportData.h"
#endif

static TAutoConsoleVariable<int32> CVarLiveSyncFBXVerboseLogs(
    TEXT("UE.LiveSync.FBX.VerboseLogs"),
    0,
    TEXT("0=normal, 1=verbose FBX diagnostic logs"));

// =========================================================
// FBX IMPORT REQUEST (Phase 7C Stage 3A.1)
// =========================================================
// Validates the request, imports FBX as StaticMesh under
// /Game/UELiveSync/Imported, then spawns or updates a
// StaticMeshActor by LiveSync GUID tag.
//
// Game-thread only. Safe on missing actor / invalid path.
// =========================================================

// Phase 10K.6: Transaction timing decomposition infrastructure
// =========================================================

static FString PhaseKindToClassification(EFbxPhaseKind Kind)
{
    switch (Kind)
    {
        case EFbxPhaseKind::Exclusive:       return TEXT("exclusive");
        case EFbxPhaseKind::Nested:          return TEXT("nested");
        case EFbxPhaseKind::InclusiveParent: return TEXT("inclusive_parent");
        case EFbxPhaseKind::Unobservable:    return TEXT("unobservable");
        default:                             return TEXT("unknown");
    }
}

// One authoritative phase metadata registry.
// Exclusive phases are strictly sequential and never overlap.
// Nested phases have a named parent; they are excluded from the exclusive sum.
static const TMap<FString, FFbxPhaseMetadata> GPhaseMetadata = {
    {TEXT("request_parse"),                     {EFbxPhaseKind::Exclusive,     nullptr}},
    {TEXT("path_validation"),                   {EFbxPhaseKind::Exclusive,     nullptr}},
    {TEXT("semantic_signature"),                {EFbxPhaseKind::Nested,        nullptr}},
    {TEXT("fbx_factory_import"),                {EFbxPhaseKind::Exclusive,     nullptr}},
    {TEXT("imported_asset_discovery"),          {EFbxPhaseKind::Exclusive,     nullptr}},
    {TEXT("sidecar_processing"),                {EFbxPhaseKind::Exclusive,     nullptr}},
    {TEXT("sidecar_manifest_read"),             {EFbxPhaseKind::Nested,        TEXT("sidecar_processing")}},
    {TEXT("sidecar_fingerprint_classification"),{EFbxPhaseKind::Nested,        TEXT("sidecar_processing")}},
    {TEXT("sidecar_asset_lookup"),              {EFbxPhaseKind::Nested,        TEXT("sidecar_processing")}},
    {TEXT("sidecar_batch_import"),              {EFbxPhaseKind::Nested,        TEXT("sidecar_processing")}},
    {TEXT("sidecar_result_mapping"),            {EFbxPhaseKind::Nested,        TEXT("sidecar_processing")}},
    {TEXT("static_mesh_post_import"),           {EFbxPhaseKind::Exclusive,     nullptr}},
    {TEXT("actor_lookup_or_spawn"),             {EFbxPhaseKind::Exclusive,     nullptr}},
    {TEXT("static_mesh_assignment"),            {EFbxPhaseKind::Exclusive,     nullptr}},
    {TEXT("material_slot_assignment"),          {EFbxPhaseKind::Exclusive,     nullptr}},
    {TEXT("post_import_finalize"),              {EFbxPhaseKind::Nested,        TEXT("fbx_transaction")}},
};

// Derived exclusive-phase set.  Single source of truth is GPhaseMetadata.
// Lambda initializer ensures no manually duplicated list.
static TSet<FString> GExclusivePhases = []()
{
    TSet<FString> Result;
    for (const auto& KV : GPhaseMetadata)
    {
        if (KV.Value.Kind == EFbxPhaseKind::Exclusive)
        {
            Result.Add(KV.Key);
        }
    }
    return Result;
}();

static bool IsExclusivePhase(const FString& PhaseName)
{
    const FFbxPhaseMetadata* Meta = GPhaseMetadata.Find(PhaseName);
    return Meta && Meta->Kind == EFbxPhaseKind::Exclusive;
}

static FString ComputePhaseClassificationExclusive(const TMap<FString, double>& PhaseDurations)
{
    FString LargestPhase, SecondPhase;
    double LargestMs = 0.0, SecondMs = 0.0;
    for (const auto& KV : PhaseDurations)
    {
        if (!IsExclusivePhase(KV.Key))
            continue;
        if (KV.Value > LargestMs)
        {
            SecondMs = LargestMs;
            SecondPhase = LargestPhase;
            LargestMs = KV.Value;
            LargestPhase = KV.Key;
        }
        else if (KV.Value > SecondMs)
        {
            SecondMs = KV.Value;
            SecondPhase = KV.Key;
        }
    }
    if (LargestMs <= 0.0)
        return TEXT("UNRESOLVED");
    if (SecondMs >= 0.8 * LargestMs)
        return TEXT("MIXED");
    return FString::Printf(TEXT("DOMINANT_%s"), *LargestPhase);
}

// Phase marker helpers.  These define the authoritative log format.
static void FbxPhaseBegin(
    int32 TransactionId,
    const FGuid& Guid,
    int32 SyncId,
    const FString& ObjectName,
    const FString& PhaseName,
    EFbxPhaseKind Kind)
{
    UE_LOG(LogLiveSync, Log,
        TEXT("[FBX][PHASE_BEGIN] transactionId=%d guid=%s syncId=%d objectName=%s phase=\"%s\" classification=\"%s\""),
        TransactionId,
        *Guid.ToString(EGuidFormats::Digits),
        SyncId,
        *ObjectName,
        *PhaseName,
        *PhaseKindToClassification(Kind));
}

static void FbxPhaseEnd(
    int32 TransactionId,
    const FGuid& Guid,
    int32 SyncId,
    const FString& ObjectName,
    const FString& PhaseName,
    EFbxPhaseKind Kind,
    double DurationMs)
{
    UE_LOG(LogLiveSync, Log,
        TEXT("[FBX][PHASE_END] transactionId=%d guid=%s syncId=%d objectName=%s phase=\"%s\" classification=\"%s\" durationMs=%.1f"),
        TransactionId,
        *Guid.ToString(EGuidFormats::Digits),
        SyncId,
        *ObjectName,
        *PhaseName,
        *PhaseKindToClassification(Kind),
        DurationMs);
}

// RAII scope phase.  Emits PHASE_BEGIN in the constructor and PHASE_END in
// the destructor.  If OutDurationMs is non-null, the measured duration is
// written through that pointer before PHASE_END.
struct FFbxScopePhase
{
    int32 TransactionId;
    FGuid Guid;
    int32 SyncId;
    FString ObjectName;
    FString PhaseName;
    EFbxPhaseKind Kind;
    double StartTime;
    double* OutDurationMs;
    TMap<FString, double>* PhaseDurations;

    FFbxScopePhase(
        int32 InTransactionId,
        const FGuid& InGuid,
        int32 InSyncId,
        const FString& InObjectName,
        const FString& InPhaseName,
        EFbxPhaseKind InKind,
        double* InOutDurationMs = nullptr,
        TMap<FString, double>* InPhaseDurations = nullptr)
        : TransactionId(InTransactionId)
        , Guid(InGuid)
        , SyncId(InSyncId)
        , ObjectName(InObjectName)
        , PhaseName(InPhaseName)
        , Kind(InKind)
        , StartTime(FPlatformTime::Seconds())
        , OutDurationMs(InOutDurationMs)
        , PhaseDurations(InPhaseDurations)
    {
        FbxPhaseBegin(TransactionId, Guid, SyncId, ObjectName, PhaseName, Kind);
    }

    ~FFbxScopePhase()
    {
        const double Ms = (FPlatformTime::Seconds() - StartTime) * 1000.0;
        if (OutDurationMs)
        {
            *OutDurationMs = Ms;
        }
        if (PhaseDurations &&
            Kind == EFbxPhaseKind::Exclusive)
        {
            PhaseDurations->FindOrAdd(PhaseName) += Ms;
        }
        FbxPhaseEnd(TransactionId, Guid, SyncId, ObjectName, PhaseName, Kind, Ms);
    }
};

// =========================================================
// BOUNDED FIXED-FIELD HELPERS
// =========================================================
// Source encoding: UTF-8 (Blender writes str.encode('utf-8')).
// Current compatibility decode: ANSI (byte-by-byte, no decode).
// Memory over-read fixed: YES.
// Unicode correctness fixed: NO (a later protocol/string slice should
// migrate to bounded UTF-8 decoding and define malformed/truncated UTF-8
// handling).
static FString FStringFromFixedAnsi(
    const uint8* Data,
    int32 Capacity)
{
    if (!Data || Capacity <= 0)
    {
        return FString();
    }

    int32 Length = 0;
    while (Length < Capacity && Data[Length] != 0)
    {
        ++Length;
    }

    return FString::ConstructFromPtrSize(
        reinterpret_cast<const ANSICHAR*>(Data),
        Length);
}

// =========================================================
// VALIDATION HELPERS
// =========================================================

static bool ValidatePayloadSize(int32 PayloadSize, FLiveSyncStats& Stats)
{
    // Phase 10J.5F: accept both old (680) and new (688) payload sizes
    constexpr int32 kFBXPayloadSizeMin = 680;
    if (PayloadSize < kFBXPayloadSizeMin)
    {
        Stats.FBXImportRequestsRejected.fetch_add(
            1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Warning,
            TEXT("[FBX] Truncated request: size %d < %d"),
            PayloadSize, kFBXPayloadSizeMin);
        return false;
    }
    return true;
}

static bool ValidateVersion(uint32 Version, FLiveSyncStats& Stats)
{
    if (Version != 1)
    {
        Stats.FBXImportRequestsRejected.fetch_add(
            1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Warning,
            TEXT("[FBX] Unsupported version %u — rejecting"),
            Version);
        return false;
    }
    return true;
}

static bool ValidatePathSecurity(const FString& FbxPath, FLiveSyncStats& Stats)
{
    if (FbxPath.IsEmpty() || !FPaths::FileExists(FbxPath))
    {
        Stats.FBXImportRequestsRejected.fetch_add(
            1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Warning,
            TEXT("[FBX] File not found: %s"),
            *FbxPath);
        return false;
    }

    const FString AllowedRoot =
        TEXT("/home/nguyennongngockhanh/.cache/uelivesync/fbx");
    if (!FbxPath.StartsWith(AllowedRoot))
    {
        Stats.FBXImportRequestsRejected.fetch_add(
            1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Warning,
            TEXT("[FBX] Path outside allowed root: %s"),
            *FbxPath);
        return false;
    }
    if (FbxPath.Contains(TEXT("..")))
    {
        Stats.FBXImportRequestsRejected.fetch_add(
            1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Warning,
            TEXT("[FBX] Path contains '..': %s"),
            *FbxPath);
        return false;
    }
    return true;
}

static FString SanitizeObjectName(const FString& RawName)
{
    FString Name = RawName;
    if (Name.IsEmpty())
    {
        Name = TEXT("Unnamed");
    }

    FString SafeName;
    for (TCHAR C : Name)
    {
        if (FChar::IsAlnum(C) || C == TEXT('_') || C == TEXT('-'))
        {
            SafeName.AppendChar(C);
        }
        else
        {
            SafeName.AppendChar(TEXT('_'));
        }
    }
    if (SafeName.IsEmpty())
    {
        SafeName = TEXT("Mesh");
    }
    return SafeName;
}

// =========================================================
// Phase 10J.5K: Unit invariant enforcement — NO scale compensation.
// Blender meter → UE centimeter is represented in mesh vertex data,
// NOT in actor/component RelativeScale.
// GBoundsExtentCache tracks raw bounds for diagnostic comparison only.
// No GActiveUnitScaleFix — scale compensation is disabled.
// =========================================================

// Last known good raw mesh bounds extent per GUID (diagnostic only).
static TMap<FGuid, FVector> GBoundsExtentCache;
// Phase 10J.5Q: Tracks last assigned temp mesh path per GUID for cleanup.
static TMap<FGuid, FString> GLastAssignedMeshPath;

static bool IsValidFBXBoundsExtent(const FVector& Extent)
{
    if (Extent.ContainsNaN() || Extent.IsZero())
        return false;
    const float MaxVal = FMath::Max3(Extent.X, Extent.Y, Extent.Z);
    return MaxVal > 10.0f;
}

static bool IsLikelyUnitScaleShrink(const FVector& CurrentExtent, const FVector& LastGoodExtent)
{
    // Do NOT require CurrentExtent to pass IsValidFBXBoundsExtent (>10cm).
    // The shrink pattern means current IS tiny — that's the whole point.
    if (CurrentExtent.ContainsNaN() || LastGoodExtent.ContainsNaN() || CurrentExtent.IsZero())
        return false;
    if (!IsValidFBXBoundsExtent(LastGoodExtent))
        return false;
    const float CurrentMax = FMath::Max3(CurrentExtent.X, CurrentExtent.Y, CurrentExtent.Z);
    const float GoodMax = FMath::Max3(LastGoodExtent.X, LastGoodExtent.Y, LastGoodExtent.Z);
    if (CurrentMax <= 0.001f || GoodMax <= 0.0f)
        return false;
    const float Ratio = GoodMax / CurrentMax;
    return Ratio >= 50.0f && Ratio <= 150.0f;
}

// Returns raw mesh bounds (before component relative scale compensation).
// Prefers direct mesh bounds; falls back to dividing component bounds by relative scale.
static FVector GetRawFBXMeshBoundsExtent(UStaticMeshComponent* SMC)
{
    if (UStaticMesh* Mesh = SMC->GetStaticMesh())
    {
        return Mesh->GetBounds().BoxExtent;
    }
    // Fallback: component bounds / relative scale (if scale is non-zero)
    const FVector CompBounds = SMC->Bounds.BoxExtent;
    const FVector RelScale = SMC->GetRelativeScale3D();
    if (!RelScale.IsZero())
    {
        return FVector(
            (RelScale.X != 0.0f) ? CompBounds.X / RelScale.X : CompBounds.X,
            (RelScale.Y != 0.0f) ? CompBounds.Y / RelScale.Y : CompBounds.Y,
            (RelScale.Z != 0.0f) ? CompBounds.Z / RelScale.Z : CompBounds.Z);
    }
    return CompBounds;
}

static void ApplyUnitScaleGuard(UStaticMeshComponent* SMC, const FGuid& Guid)
{
    if (!SMC)
        return;

    // Phase 10J.5K: Enforce scale invariant — actor and component scale must be 1.
    // No scale compensation is allowed. Unit conversion is in mesh vertex data.
    const FVector ActorScale = SMC->GetOwner() ? SMC->GetOwner()->GetActorScale3D() : FVector::OneVector;
    const FVector CompRelScale = SMC->GetRelativeScale3D();
    const FVector RawExtent = GetRawFBXMeshBoundsExtent(SMC);
    const float RawMax = FMath::Max3(RawExtent.X, RawExtent.Y, RawExtent.Z);

    bool bScaleViolation = false;
    FString ScaleReason = TEXT("ok");

    if (FMath::Abs(ActorScale.X - 1.0f) > 0.001f ||
        FMath::Abs(ActorScale.Y - 1.0f) > 0.001f ||
        FMath::Abs(ActorScale.Z - 1.0f) > 0.001f)
    {
        bScaleViolation = true;
        ScaleReason = TEXT("actor_scale_not_1");
    }
    if (FMath::Abs(CompRelScale.X - 1.0f) > 0.001f ||
        FMath::Abs(CompRelScale.Y - 1.0f) > 0.001f ||
        FMath::Abs(CompRelScale.Z - 1.0f) > 0.001f)
    {
        bScaleViolation = true;
        ScaleReason = TEXT("comp_rel_scale_not_1");
    }

    UE_LOG(LogLiveSync, Log,
        TEXT("[FBX][SCALE_INVARIANT] guid=%s actorScale=(%.3f,%.3f,%.3f) "
             "compRelScale=(%.3f,%.3f,%.3f) rawExtent=(%.1f,%.1f,%.1f) "
             "status=%s reason=%s"),
        *Guid.ToString(EGuidFormats::Digits),
        ActorScale.X, ActorScale.Y, ActorScale.Z,
        CompRelScale.X, CompRelScale.Y, CompRelScale.Z,
        RawExtent.X, RawExtent.Y, RawExtent.Z,
        bScaleViolation ? TEXT("violation") : TEXT("ok"),
        *ScaleReason);

    // Enforce scale invariant — reset to 1 if violated
    if (bScaleViolation)
    {
        const FString ScaleFixLog = FString::Printf(
            TEXT("[FBX][UNIT_INVALID] guid=%s actorScale=(%.3f,%.3f,%.3f) "
                 "compRelScale=(%.3f,%.3f,%.3f) rawExtent=(%.1f,%.1f,%.1f) "
                 "reason=imported_raw_bounds_100x_too_small_no_scale_compensation"),
            *Guid.ToString(EGuidFormats::Digits),
            ActorScale.X, ActorScale.Y, ActorScale.Z,
            CompRelScale.X, CompRelScale.Y, CompRelScale.Z,
            RawExtent.X, RawExtent.Y, RawExtent.Z);
        UE_LOG(LogLiveSync, Warning, TEXT("%s"), *ScaleFixLog);

        if (AActor* Owner = SMC->GetOwner())
        {
            Owner->SetActorScale3D(FVector::OneVector);
        }
        SMC->SetRelativeScale3D(FVector::OneVector);
        SMC->UpdateBounds();
        SMC->MarkRenderStateDirty();
    }

    // Phase 10J.5L: log rawExtent with diagnostic ratio against cached extent
    {
        const FVector* Cached = GBoundsExtentCache.Find(Guid);
        if (Cached)
        {
            const float CachedMax = FMath::Max3(Cached->X, Cached->Y, Cached->Z);
            const float Ratio = (RawMax > 0.001f && CachedMax > 0.0f) ? (CachedMax / RawMax) : 0.0f;
            UE_LOG(LogLiveSync, Log,
                TEXT("[FBX][RAW_EXTENT] guid=%s rawExtent=(%.1f,%.1f,%.1f) "
                     "expectedCmExtent=(%.1f,%.1f,%.1f) ratio=%.1f isInvalid=%s"),
                *Guid.ToString(EGuidFormats::Digits),
                RawExtent.X, RawExtent.Y, RawExtent.Z,
                Cached->X, Cached->Y, Cached->Z,
                Ratio,
                IsValidFBXBoundsExtent(RawExtent) ? TEXT("0") : TEXT("1"));
        }
        else
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[FBX][RAW_EXTENT] guid=%s rawExtent=(%.1f,%.1f,%.1f) "
                     "expectedCmExtent=none ratio=n/a isInvalid=%s"),
                *Guid.ToString(EGuidFormats::Digits),
                RawExtent.X, RawExtent.Y, RawExtent.Z,
                IsValidFBXBoundsExtent(RawExtent) ? TEXT("0") : TEXT("1"));
        }
    }

    // Only update cache with raw mesh bounds that are valid (not tiny)
    if (IsValidFBXBoundsExtent(RawExtent))
    {
        // Phase 10J.5M: first-import oversize gate — reject suspiciously large first-time extent
        const bool bFirstTimeNoCache = (GBoundsExtentCache.Find(Guid) == nullptr);
        if (bFirstTimeNoCache && RawMax > 5000.0f)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[FBX][CACHE_GATE] guid=%s rawExtent=(%.1f,%.1f,%.1f) "
                     "reason=first_import_oversize_rejected maxVal=%.1f threshold=5000"),
                *Guid.ToString(EGuidFormats::Digits),
                RawExtent.X, RawExtent.Y, RawExtent.Z,
                RawMax);
        }
        else
        {
            GBoundsExtentCache.Add(Guid, RawExtent);
        }
    }
    else
    {
        // Phase 10J.5L: log unit-invalid state — do NOT let this overwrite good cache
        const FVector* Cached = GBoundsExtentCache.Find(Guid);
        if (Cached)
        {
            const float CachedMax = FMath::Max3(Cached->X, Cached->Y, Cached->Z);
            const float Ratio = (RawMax > 0.001f && CachedMax > 0.0f) ? (CachedMax / RawMax) : 0.0f;
            if (Ratio >= 50.0f && Ratio <= 150.0f)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[FBX][UNIT_INVALID] guid=%s rawExtent=(%.1f,%.1f,%.1f) "
                         "expectedCmExtent=(%.1f,%.1f,%.1f) ratio=%.1f reason=imported_meter_size"),
                    *Guid.ToString(EGuidFormats::Digits),
                    RawExtent.X, RawExtent.Y, RawExtent.Z,
                    Cached->X, Cached->Y, Cached->Z,
                    Ratio);
            }
        }
    }
}

// =========================================================
// FBX COMPONENT REFRESH (Phase 10J.5B.2)
// =========================================================
// Forces a StaticMeshComponent to fully refresh after a
// repeated FBX reimport, because SetStaticMesh may no-op
// when the mesh pointer is unchanged.  Explicit bounds,
// mark, and visibility invalidation prevents stale render
// state after in-place asset mutation.
//
// Caller is responsible for save/restore of material
// overrides around mesh assignment.
//
// Game-thread only.  Safe on any valid component/actor.
// =========================================================

static void RefreshFBXStaticMeshComponent(
    UStaticMeshComponent* SMC,
    AActor* OwnerActor)
{
    check(SMC);
    check(OwnerActor);

    SMC->SetVisibility(true, true);
    SMC->SetHiddenInGame(false, true);
    SMC->UpdateBounds();
    SMC->MarkRenderStateDirty();
    OwnerActor->SetActorHiddenInGame(false);
}

// =========================================================
// FBX MATERIAL VISIBILITY FALLBACK (Phase 10J.5D / 10J.5D.2)
// =========================================================
// Ensures a StaticMeshComponent remains renderable and
// visible after FBX reimport, even when imported material
// slots are invalid/null/unmapped or contain unsafe FBX-
// generated materials.  Null slots get SafeMaterial; non-
// null slots whose material package originates from
// /Game/UELiveSync/Imported are forced to SafeMaterial.
// Non-imported user/engine materials are preserved.
//
// Game-thread only.  Safe on any valid component/actor.
// =========================================================

// =========================================================
// Phase 10J.5D.4: WorldGrid path detection
// =========================================================
static bool IsWorldGridMaterialPath(const FString& Path)
{
    return Path.Contains(TEXT("/Engine/EngineMaterials/WorldGridMaterial"))
        || Path.Contains(TEXT("MID_WorldGridMaterial"));
}

// =========================================================
// Phase 10J.5D.3: unsafe-material check
// =========================================================
// Returns true when Mat is null or WorldGrid.
// /Game/UELiveSync/Imported materials are valid FBX-imported
// assets and MUST NOT be replaced.
static bool IsUnsafeFBXMaterial(UMaterialInterface* Mat)
{
    if (!Mat)
        return true;

    const FString MatPath = Mat->GetPathName();

    if (IsWorldGridMaterialPath(MatPath))
        return true;

    // Phase 7H/7G.5: /Game/UELiveSync/Imported is now VALID.
    // These are legitimately imported FBX materials that must
    // NOT be replaced by SafeMaterial fallback.
    if (MatPath.StartsWith(TEXT("/Game/UELiveSync/Imported")))
    {
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[MATERIAL][FBX_IMPORTED_KEEP] path=%s"),
            *MatPath);
        return false;
    }

    return false;
}

// =========================================================
// Phase 10J.5D.4: guaranteed-visible material singleton
// =========================================================
// Looks up a non-WorldGrid engine material.  Logs failure
// when no candidate is available and returns nullptr.
static UMaterialInterface* GetSafeFBXVisibleMaterial()
{
    static UMaterialInterface* SafeMat = nullptr;
    if (!SafeMat)
    {
        // Candidate paths ordered by reliability.
        // Must NOT resolve to WorldGridMaterial.
        static const TCHAR* CandidatePaths[] =
        {
            TEXT("/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial"),
            TEXT("/Engine/EngineMaterials/DefaultMaterial.DefaultMaterial"),
            TEXT("/Engine/EngineMaterials/DefaultDiffuse.DefaultDiffuse"),
        };

        UMaterialInterface* Candidate = nullptr;
        for (const TCHAR* Path : CandidatePaths)
        {
            Candidate = LoadObject<UMaterialInterface>(
                nullptr, Path);
            if (Candidate && !IsWorldGridMaterialPath(
                Candidate->GetPathName()))
            {
                SafeMat = Candidate;
                break;
            }
            Candidate = nullptr;
        }

        // Last resort: engine default surface material.
        if (!SafeMat)
        {
            UMaterial* DefaultMat =
                UMaterial::GetDefaultMaterial(MD_Surface);
            if (DefaultMat && !IsWorldGridMaterialPath(
                DefaultMat->GetPathName()))
            {
                SafeMat = DefaultMat;
            }
        }

        // If the matched asset is not an MID, wrap in a MID to
        // make it a runtime-only override (avoids asset writes).
        if (SafeMat && !SafeMat->GetPathName().Contains(
            TEXT("MID_")))
        {
            UMaterial* BaseMat = Cast<UMaterial>(SafeMat);
            if (BaseMat)
            {
                UMaterialInstanceDynamic* MID =
                    UMaterialInstanceDynamic::Create(
                        BaseMat, nullptr);
                if (MID)
                {
                    MID->AddToRoot();
                    SafeMat = MID;
                }
            }
        }
        else if (SafeMat)
        {
            // Already an MID — just protect from GC.
            SafeMat->AddToRoot();
        }

        if (!SafeMat)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[FBX][MAT] safe_material_failed"
                     " reason=all_candidates_worldgrid"));
        }
    }
    return SafeMat;
}

void FLiveSyncFBXImporter::EnsureFBXMeshRenderable(
    UStaticMeshComponent* SMC,
    UStaticMesh* StaticMesh,
    AActor* OwnerActor,
    const FGuid& Guid,
    bool bGeometryHashChanged)
{
    check(SMC);
    check(StaticMesh);
    check(OwnerActor);

    if (SMC->GetStaticMesh() != StaticMesh)
    {
        SMC->SetStaticMesh(StaticMesh);
    }

    SMC->SetVisibility(true, true);
    SMC->SetHiddenInGame(false, true);
    OwnerActor->SetActorHiddenInGame(false);

    SMC->UpdateBounds();
    SMC->MarkRenderStateDirty();

    const int32 RawNumSlots = SMC->GetNumMaterials();
    const int32 NumSlots = FMath::Max(1, RawNumSlots);
    int32 FallbackCount = 0;
    int32 ForcedCount = 0;
    UMaterialInterface* SafeMaterial = GetSafeFBXVisibleMaterial();

    if (RawNumSlots == 0)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[FBX][MAT] fallback_zero_slots guid=%s"),
            *Guid.ToString(EGuidFormats::Digits));
    }

    for (int32 i = 0; i < NumSlots; ++i)
    {
        UMaterialInterface* Current = SMC->GetMaterial(i);
        if (IsUnsafeFBXMaterial(Current))
        {
            if (!SafeMaterial)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[FBX][MAT] force_visible_failed guid=%s slot=%d"
                         " reason=no_safe_material"),
                    *Guid.ToString(EGuidFormats::Digits), i);
                continue;
            }
            SMC->SetMaterial(i, SafeMaterial);
            if (!Current)
            {
                ++FallbackCount;
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MATERIAL][MID_FALLBACK_APPLY] guid=%s slot=%d reason=null_material "
                         "fallback=%s"),
                    *Guid.ToString(EGuidFormats::Digits), i,
                    *SafeMaterial->GetPathName());
            }
            else
            {
                ++ForcedCount;
                FString Reason = Current->GetPathName().Contains(
                    TEXT("WorldGrid")) ? TEXT("worldgrid") : TEXT("unsafe_or_imported");
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MATERIAL][MID_FALLBACK_APPLY] guid=%s slot=%d reason=%s "
                         "old=%s fallback=%s"),
                    *Guid.ToString(EGuidFormats::Digits), i, *Reason,
                    *Current->GetPathName(),
                    *SafeMaterial->GetPathName());
            }
        }
        // Phase 7H/7G.5: log when imported FBX material is kept
        else if (Current)
        {
            const FString MatPath = Current->GetPathName();
            if (MatPath.StartsWith(TEXT("/Game/UELiveSync/Imported")))
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MATERIAL][FBX_IMPORTED_APPLY] guid=%s slot=%d path=%s"),
                    *Guid.ToString(EGuidFormats::Digits), i, *MatPath);
            }
        }
    }

    UMaterialInterface* Mat0 = SMC->GetMaterial(0);
    FString Mat0Name = Mat0 ? Mat0->GetPathName() : TEXT("null");
    bool bIsWorldGrid = Mat0Name.Contains(TEXT("WorldGrid"));

    UE_LOG(LogLiveSync, Log,
        TEXT("[FBX][VALIDATE] guid=%s actor=%s mesh=%s visible=%d actorHidden=%d slots=%d forcedMaterials=%d fallbackApplied=%d meshValid=%d material0=%s worldGrid=%d"),
        *Guid.ToString(EGuidFormats::Digits),
        *OwnerActor->GetName(),
        *StaticMesh->GetName(),
        SMC->IsVisible() ? 1 : 0,
        OwnerActor->IsHidden() ? 1 : 0,
        NumSlots,
        ForcedCount,
        FallbackCount,
        SMC->GetStaticMesh() != nullptr ? 1 : 0,
        *Mat0Name,
        bIsWorldGrid ? 1 : 0);

    // Phase 10J.5D.6: detect and repair unit-scale shrink on this component
    ApplyUnitScaleGuard(SMC, Guid);
}

// =========================================================
// Phase 10J.5D.5: Extended FBX visibility validation
// =========================================================
// Logs detailed actor/component/material/bounds state for
// diagnosing intermittent disappearance after FBX reimport.
// VIS_WARN is emitted for each suspicious condition found.
static void LogExtendedFBXValidate(
    AActor* OwnerActor,
    UStaticMeshComponent* SMC,
    const FGuid& Guid)
{
    if (!OwnerActor || !SMC)
        return;

    const FString ActorName = OwnerActor->GetName();
    const FVector ActorLoc = OwnerActor->GetActorLocation();
    const FRotator ActorRot = OwnerActor->GetActorRotation();
    const FVector ActorScl = OwnerActor->GetActorScale3D();

    AActor* RootActor = OwnerActor->GetParentActor();
    const FString RootName = RootActor ? RootActor->GetName() : TEXT("self");

    const bool bCompReg = SMC->IsRegistered();
    const bool bCompVis = SMC->IsVisible();
    const bool bCompHidden = SMC->bHiddenInGame;
    const bool bHiddenEd = OwnerActor->IsHiddenEd();
    const EComponentMobility::Type Mobility = SMC->Mobility;
    const FVector CompScl = SMC->GetComponentScale();
    const bool bPendingKill = !IsValid(OwnerActor);
    const UStaticMesh* Mesh = SMC->GetStaticMesh();
    const FString MeshName = Mesh ? Mesh->GetName() : TEXT("null");
    const FString MeshPath = Mesh ? Mesh->GetPathName() : TEXT("null");

    const FBoxSphereBounds Bounds = SMC->Bounds;
    const FVector BndExt = Bounds.BoxExtent;
    const float BndSphere = Bounds.SphereRadius;

    const FString FlagsStr = FString::Printf(TEXT("0x%08X"),
        OwnerActor->GetFlags());

    const int32 NumSlots = SMC->GetNumMaterials();
    const int32 NumOverrides = SMC->OverrideMaterials.Num();
    FString Mat0Path;
    if (UMaterialInterface* M0 = SMC->GetMaterial(0))
        Mat0Path = M0->GetPathName();

    FString Mat1Path;
    if (NumSlots > 1)
    {
        if (UMaterialInterface* M1 = SMC->GetMaterial(1))
            Mat1Path = M1->GetPathName();
    }

    // Render state diagnostic
    bool bRenderStateCreated = false;
#if !(UE_BUILD_SHIPPING || UE_BUILD_TEST)
    bRenderStateCreated = SMC->IsRenderStateCreated();
#endif

    const FVector RelScale = SMC->GetRelativeScale3D();
    const bool bUnitFixApplied = !RelScale.Equals(FVector::OneVector, 0.001f);
    FString LastGoodExtentStr;
    {
        const FVector* Cached = GBoundsExtentCache.Find(Guid);
        LastGoodExtentStr = Cached ? Cached->ToString() : TEXT("none");
    }
    const FVector RawExtent = GetRawFBXMeshBoundsExtent(SMC);
    // Phase 10J.5K: GActiveUnitScaleFix removed — no scale compensation.
    const float ActiveFix = 0.0f;

    if (CVarLiveSyncFBXVerboseLogs.GetValueOnGameThread() != 0)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[FBX][VALIDATE2] guid=%s actor=%s loc=(%s) rot=(%s) scl=(%s)"
                 " root=%s compReg=%d compVis=%d compHidden=%d"
                 " hiddenEd=%d mobility=%d"
                 " boundsExtent=(%s) sphere=%.2f"
                 " flags=%s slots=%d overrides=%d"
                 " mat0=%s mat1=%s"
                 " mesh=%s meshPath=%s"
                 " compScl=(%s) pendingKill=%d"
                 " renderCreated=%d"
                 " relScale=(%s) unitFix=%d lastGood=(%s)"
                 " rawExtent=(%s) activeFix=%.4f"),
            *Guid.ToString(EGuidFormats::Digits),
            *ActorName,
            *ActorLoc.ToString(), *ActorRot.ToString(), *ActorScl.ToString(),
            *RootName,
            bCompReg ? 1 : 0, bCompVis ? 1 : 0, bCompHidden ? 1 : 0,
            bHiddenEd ? 1 : 0, static_cast<int32>(Mobility),
            *BndExt.ToString(), BndSphere,
            *FlagsStr, NumSlots, NumOverrides,
            *Mat0Path, *Mat1Path,
            *MeshName, *MeshPath,
            *CompScl.ToString(), bPendingKill ? 1 : 0,
            bRenderStateCreated ? 1 : 0,
            *RelScale.ToString(), bUnitFixApplied ? 1 : 0,
            *LastGoodExtentStr,
            *RawExtent.ToString(), ActiveFix);

        // Log all material slots for full diagnostic
        FString AllSlotMats;
        for (int32 i = 0; i < NumSlots; ++i)
        {
            if (UMaterialInterface* M = SMC->GetMaterial(i))
            {
                AllSlotMats += FString::Printf(TEXT("slot%d=%s "), i, *M->GetPathName());
            }
            else
            {
                AllSlotMats += FString::Printf(TEXT("slot%d=null "), i);
            }
        }
        if (!AllSlotMats.IsEmpty())
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[FBX][VALIDATE2] guid=%s all_materials: %s"),
                *Guid.ToString(EGuidFormats::Digits),
                *AllSlotMats);
        }
    }

    // VIS_WARN: detect suspicious conditions
    if (BndSphere < KINDA_SMALL_NUMBER + 0.01f)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[FBX][VIS_WARN] guid=%s reason=zero_bounds sphere=%.2f extent=(%s)"),
            *Guid.ToString(EGuidFormats::Digits), BndSphere,
            *BndExt.ToString());
    }

    if (!bCompReg)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[FBX][VIS_WARN] guid=%s reason=not_registered"),
            *Guid.ToString(EGuidFormats::Digits));
    }

    if (bHiddenEd)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[FBX][VIS_WARN] guid=%s reason=hidden_ed"),
            *Guid.ToString(EGuidFormats::Digits));
    }

    if (!bCompVis || bCompHidden)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[FBX][VIS_WARN] guid=%s reason=hidden compVisible=%d compHidden=%d"),
            *Guid.ToString(EGuidFormats::Digits), bCompVis ? 1 : 0,
            bCompHidden ? 1 : 0);
    }

    if (Mat0Path.Contains(TEXT("WorldGrid")))
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[FBX][VIS_WARN] guid=%s reason=worldgrid material0=%s"),
            *Guid.ToString(EGuidFormats::Digits), *Mat0Path);
    }

    if (FMath::Abs(ActorScl.X) < KINDA_SMALL_NUMBER ||
        FMath::Abs(ActorScl.Y) < KINDA_SMALL_NUMBER ||
        FMath::Abs(ActorScl.Z) < KINDA_SMALL_NUMBER)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[FBX][VIS_WARN] guid=%s reason=zero_scale scl=(%s)"),
            *Guid.ToString(EGuidFormats::Digits), *ActorScl.ToString());
    }

    if (bPendingKill)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[FBX][VIS_WARN] guid=%s reason=pending_kill_or_unreachable"),
            *Guid.ToString(EGuidFormats::Digits));
    }

    if (!Mesh)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[FBX][VIS_WARN] guid=%s reason=staticmesh_null"),
            *Guid.ToString(EGuidFormats::Digits));
    }

    if (Mobility == EComponentMobility::Static)
    {
        if (CVarLiveSyncFBXVerboseLogs.GetValueOnGameThread() != 0)
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[FBX][VIS_NOTE] guid=%s reason=static_mobility mobility=%d"),
                *Guid.ToString(EGuidFormats::Digits), static_cast<int32>(Mobility));
        }
    }

    if (!bRenderStateCreated)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[FBX][VIS_WARN] guid=%s reason=render_state_not_created"),
            *Guid.ToString(EGuidFormats::Digits));
    }

    // Zero-bounds repair (requirement 5): if bounds are zero but mesh is valid,
    // call UpdateBounds and MarkRenderStateDirty.
    if (BndSphere < KINDA_SMALL_NUMBER + 0.01f && Mesh)
    {
        SMC->UpdateBounds();
        SMC->MarkRenderStateDirty();
        UE_LOG(LogLiveSync, Log,
            TEXT("[FBX][VALIDATE2] guid=%s reason=zero_bounds_repair bounds_repaired=1"),
            *Guid.ToString(EGuidFormats::Digits));
    }

    // Phase 10J.5D.6: warn on unit-scale shrink pattern (small bounds when good known)
    if (Mesh && BndSphere > 0.0f && BndSphere < 5.0f)
    {
        const FVector* Cached = GBoundsExtentCache.Find(Guid);
        if (Cached && IsLikelyUnitScaleShrink(BndExt, *Cached))
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[FBX][UNIT_WARN] guid=%s reason=unit_shrink_potential"
                     " extent=(%s) sphere=%.2f lastGood=(%s)"),
                *Guid.ToString(EGuidFormats::Digits),
                *BndExt.ToString(), BndSphere,
                *Cached->ToString());
        }
    }
}

// =========================================================
// FBX SEMANTIC SIGNATURE CACHE (Phase 10J.5C.2)
// =========================================================
// Per-GUID semantic signature that tracks FBX path, object
// name, and mesh properties (vert/tri/mat counts).  File
// timestamp and size are stored for diagnostics only and
// are NOT part of equality.  When a repeated import has
// the same semantic signature, the import is skipped and
// the existing actor is refreshed, avoiding redundant FBX
// reimport that can cause visibility/material loss.
//
// Game-thread only.  Safe on missing actor / invalid GUID.
// =========================================================

struct FFBXImportSemanticSignature
{
    FString     FbxPath;           // Normalized FBX path (part of equality)
    FString     ObjectName;        // Object display name (part of equality)
    int32       VertCount = 0;     // Vertex count (part of equality)
    int32       TriCount = 0;      // Triangle count (part of equality)
    int32       MatSlotCount = 0;  // Material slot count (part of equality)
    uint64      GeometryHash = 0;  // Geometry content signature (part of equality, Phase 10J.5F)

    // Diagnostic fields — NOT part of equality
    FDateTime   Timestamp;         // File mtime (diagnostic only)
    int64       FileSize = 0;      // File size (diagnostic only)

    bool operator==(const FFBXImportSemanticSignature& Other) const
    {
        return FbxPath == Other.FbxPath
            && ObjectName == Other.ObjectName
            && VertCount == Other.VertCount
            && TriCount == Other.TriCount
            && MatSlotCount == Other.MatSlotCount
            && GeometryHash == Other.GeometryHash;
    }

    bool operator!=(const FFBXImportSemanticSignature& Other) const
    {
        return !(*this == Other);
    }
};

static TMap<FGuid, FFBXImportSemanticSignature> GSemanticSignatureCache;

static FFBXImportSemanticSignature ComputeFBXSemanticSignature(
    const FString& FbxPath,
    const FFBXImportRequestPayload& Request)
{
    FFBXImportSemanticSignature Sig;
    Sig.FbxPath = FbxPath;
    Sig.VertCount = Request.VertCount;
    Sig.TriCount = Request.TriCount;
    Sig.MatSlotCount = Request.MatSlotCount;
    Sig.ObjectName = FStringFromFixedAnsi(
        Request.ObjectName,
        UE_ARRAY_COUNT(Request.ObjectName));
    Sig.GeometryHash = Request.GeometryHash;

    IFileManager& FileManager = IFileManager::Get();
    Sig.Timestamp = FileManager.GetTimeStamp(*FbxPath);
    Sig.FileSize = FileManager.FileSize(*FbxPath);

    return Sig;
}

bool FLiveSyncFBXImporter::HandleImport(
    const uint8* PayloadPtr,
    int32 PayloadSize,
    const FFBXImportContext& Context)
{
    check(IsInGameThread());

    if (!Context.Stats)
    {
        return false;
    }

    const int32 TransactionId =
        Context.Stats->FBXTransactionId.fetch_add(
            1,
            std::memory_order_acq_rel);
    const int32 SyncId =
        Context.Stats->MatPktSyncId;
    const double HandleImportStart =
        FPlatformTime::Seconds();
    TMap<FString, double> PhaseDurations;

    if (!ValidatePayloadSize(PayloadSize, *Context.Stats))
    {
        return false;
    }

    // Phase 10J.5F: Backward-compatible payload reading.
    // Accept old (680) and new (688) payload sizes. GeometryHash defaults to 0
    // for old protocol payloads.
    FFBXImportRequestPayload Request;
    FMemory::Memzero(&Request, sizeof(FFBXImportRequestPayload));
    {
        const int32 CopySize = FMath::Min(PayloadSize, (int32)sizeof(FFBXImportRequestPayload));
        FMemory::Memcpy(&Request, PayloadPtr, CopySize);
    }

    if (Request.GeometryHash != 0)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[FBX] Request guid=%s verts=%d tris=%d mats=%d geomHash=%llu"),
            *Request.ObjectGUID.ToString(EGuidFormats::Digits),
            Request.VertCount, Request.TriCount, Request.MatSlotCount,
            Request.GeometryHash);
    }
    else
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[FBX] Request guid=%s verts=%d tris=%d mats=%d geomHash=0 (old protocol)"),
            *Request.ObjectGUID.ToString(EGuidFormats::Digits),
            Request.VertCount, Request.TriCount, Request.MatSlotCount);
    }

    FString FbxPathStr;
    // request_parse exclusive phase
    {
        FFbxScopePhase RequestPhase(
            TransactionId,
            Request.ObjectGUID,
            SyncId,
            FString(),
            TEXT("request_parse"),
            EFbxPhaseKind::Exclusive,
            nullptr,
            &PhaseDurations);
        if (!ValidateVersion(Request.Version, *Context.Stats))
        {
            return false;
        }
        FbxPathStr = FStringFromFixedAnsi(
            Request.FbxPath,
            UE_ARRAY_COUNT(Request.FbxPath));
    }

    // path_validation exclusive phase
    {
        FFbxScopePhase PathPhase(
            TransactionId,
            Request.ObjectGUID,
            SyncId,
            FString(),
            TEXT("path_validation"),
            EFbxPhaseKind::Exclusive,
            nullptr,
            &PhaseDurations);
        if (!ValidatePathSecurity(FbxPathStr, *Context.Stats))
        {
            return false;
        }
    }

    // ObjectName conversion (after both validation blocks)
    FString SafeName = SanitizeObjectName(
        FStringFromFixedAnsi(
            Request.ObjectName,
            UE_ARRAY_COUNT(Request.ObjectName)));
    const FString TxnObjNameSanitized = SafeName;

    const FString GuidShort =
        Request.ObjectGUID.ToString().Left(8);
    const FString AssetBasePath =
        TEXT("/Game/UELiveSync/Imported");
    const FString AssetName =
        FString::Printf(TEXT("%s_%s"), *SafeName, *GuidShort);
    const FString AssetPackagePath =
        AssetBasePath / AssetName;
    // Phase 10J.5Q: Import to unique-per-sync path.
    // Each sync creates a fresh asset so bReplaceExisting=false never mutates
    // an existing mesh. Assign directly to SMC — no rename needed.
    const FString SyncSuffix =
        FString::Printf(TEXT("_%s"), *FGuid::NewGuid().ToString().Left(8));
    const FString PendingAssetName =
        AssetName + SyncSuffix;
    const FString PendingPackagePath =
        AssetBasePath / PendingAssetName;

#if WITH_EDITOR
    // Phase 10J.5F: Compute semantic signature (incl. geometry hash) and check cache before import.
    {
        FFBXImportSemanticSignature CurrentSig = ComputeFBXSemanticSignature(FbxPathStr, Request);
        const FFBXImportSemanticSignature* CachedSig = GSemanticSignatureCache.Find(Request.ObjectGUID);

        AActor* CoalesceCheckActor = Context.FindActor(Request.ObjectGUID);
        if (!CoalesceCheckActor)
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[FBX][COALESCE] import guid=%s reason=actor_missing"),
                *Request.ObjectGUID.ToString(EGuidFormats::Digits));
        }
        else
        {
            AStaticMeshActor* SMA = Cast<AStaticMeshActor>(CoalesceCheckActor);
            if (!SMA)
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("[FBX][COALESCE] import guid=%s reason=non_static_actor"),
                    *Request.ObjectGUID.ToString(EGuidFormats::Digits));
            }
            else
            {
                UStaticMeshComponent* SMC = SMA->GetStaticMeshComponent();
                if (!SMC || !SMC->GetStaticMesh())
                {
                    UE_LOG(LogLiveSync, Log,
                        TEXT("[FBX][COALESCE] import guid=%s reason=mesh_missing"),
                        *Request.ObjectGUID.ToString(EGuidFormats::Digits));
                }
                else if (CachedSig && CurrentSig.GeometryHash != 0 && *CachedSig == CurrentSig)
                {
                    // Semantic signature (including geometry hash) matches — skip redundant import.
                    RefreshFBXStaticMeshComponent(SMC, SMA);

                    // Phase 10J.5D.2: repair visibility even without import.
                    UStaticMesh* ExistingMesh = SMC->GetStaticMesh();
                    if (ExistingMesh)
                    {
                        EnsureFBXMeshRenderable(SMC, ExistingMesh, SMA,
                            Request.ObjectGUID);
                        LogExtendedFBXValidate(SMA, SMC, Request.ObjectGUID);
                        if (Context.OnScheduleRepair)
                        {
                            Context.OnScheduleRepair(Request.ObjectGUID);
                        }
                    }

                    if (Context.OnActorCached)
                    {
                        Context.OnActorCached(Request.ObjectGUID, SMA);
                    }
                    if (Context.OnMarkFbxAuthority)
                    {
                        Context.OnMarkFbxAuthority(Request.ObjectGUID);
                    }
                    UE_LOG(LogLiveSync, Log,
                        TEXT("[FBX][SKIP] duplicate semantic guid=%s asset=%s verts=%d tris=%d mats=%d geomHash=%llu reason=same_semantic_signature"),
                        *Request.ObjectGUID.ToString(EGuidFormats::Digits),
                        *AssetName,
                        CurrentSig.VertCount,
                        CurrentSig.TriCount,
                        CurrentSig.MatSlotCount,
                        CurrentSig.GeometryHash);
                    Context.Stats->FBXImportSkipped.fetch_add(1, std::memory_order_relaxed);
                    return true;
                }
                else if (CachedSig)
                {
                    // Determine specific reason for non-skip
                    if (CurrentSig.GeometryHash != 0 && CurrentSig.GeometryHash != CachedSig->GeometryHash)
                    {
                        UE_LOG(LogLiveSync, Log,
                            TEXT("[FBX][COALESCE] import guid=%s reason=geometry_hash_changed old_hash=%llu new_hash=%llu"),
                            *Request.ObjectGUID.ToString(EGuidFormats::Digits),
                            CachedSig->GeometryHash,
                            CurrentSig.GeometryHash);
                    }
                    else if (CurrentSig.GeometryHash == 0)
                    {
                        UE_LOG(LogLiveSync, Log,
                            TEXT("[FBX][COALESCE] import guid=%s reason=geometry_hash_missing old=(%d/%d/%d/%s) new=(%d/%d/%d/%s)"),
                            *Request.ObjectGUID.ToString(EGuidFormats::Digits),
                            CachedSig->VertCount, CachedSig->TriCount, CachedSig->MatSlotCount, *CachedSig->ObjectName,
                            CurrentSig.VertCount, CurrentSig.TriCount, CurrentSig.MatSlotCount, *CurrentSig.ObjectName);
                    }
                    else
                    {
                        UE_LOG(LogLiveSync, Log,
                            TEXT("[FBX][COALESCE] import guid=%s reason=signature_changed old=(%d/%d/%d/%s) new=(%d/%d/%d/%s)"),
                            *Request.ObjectGUID.ToString(EGuidFormats::Digits),
                            CachedSig->VertCount, CachedSig->TriCount, CachedSig->MatSlotCount, *CachedSig->ObjectName,
                            CurrentSig.VertCount, CurrentSig.TriCount, CurrentSig.MatSlotCount, *CurrentSig.ObjectName);
                    }
                }
                else
                {
                    UE_LOG(LogLiveSync, Log,
                        TEXT("[FBX][COALESCE] import guid=%s reason=no_cache"),
                        *Request.ObjectGUID.ToString(EGuidFormats::Digits));
                }
            }
        }
    }

    const FString FullPendingPath =
        FString::Printf(TEXT("%s.%s"), *PendingPackagePath, *PendingAssetName);

    // === Phase 10J.5Q: Import to pending path ===
    UAssetImportTask* ImportTask = NewObject<UAssetImportTask>();
    if (!ImportTask)
    {
        Context.Stats->FBXImportFailed.fetch_add(
            1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Error,
            TEXT("[FBX] Failed to create AssetImportTask"));
        return false;
    }

    ImportTask->Filename = FbxPathStr;
    ImportTask->DestinationPath = AssetBasePath;
    // Phase 10J.5Q: Import to pending path with bReplaceExisting=false
    // so the existing asset is never mutated in-place.
    ImportTask->DestinationName = PendingAssetName;
    ImportTask->bReplaceExisting = false;
    ImportTask->bReplaceExistingSettings = false;
    ImportTask->bAutomated = true;
    ImportTask->bSave = false;
    ImportTask->bAsync = false;

    UFbxFactory* FbxFactory = NewObject<UFbxFactory>();
    if (FbxFactory)
    {
        UFbxImportUI* ImportUI = FbxFactory->ImportUI;
        ImportUI->bAutomatedImportShouldDetectType = true;
        ImportUI->bImportMaterials = true;
        // Task 9B: disable FBX texture import — sidecar lane is sole texture authority.
        ImportUI->bImportTextures = false;
        // Phase 10J.5O: Blender exports vertex data in meters; FBX_SCALE_UNITS
        // sets the FBX file unit metadata. UE converts via bConvertSceneUnit=true.
        ImportUI->StaticMeshImportData->bConvertSceneUnit = true;
        ImportTask->Factory = FbxFactory;

        UE_LOG(LogLiveSync, Log,
            TEXT("[FBX][IMPORT_OPTIONS] guid=%s bImportMaterials=1 bImportTextures=0 bConvertSceneUnit=1 importScale=1"),
            *Request.ObjectGUID.ToString(EGuidFormats::Digits));
    }

    IAssetTools& AssetTools =
        FAssetToolsModule::GetModule().Get();
    AssetTools.ImportAssetTasks({ ImportTask });

    // === Phase 10J.5Q: Check pending import result ===
    TArray<UObject*> ImportedObjects = ImportTask->GetObjects();

    {
        int32 MeshCount = 0, MatCount = 0, TexCount = 0;
        for (UObject* Obj : ImportedObjects)
        {
            if (Obj->IsA<UStaticMesh>()) ++MeshCount;
            else if (Obj->IsA<UMaterial>()) ++MatCount;
            else if (Obj->IsA<UTexture>()) ++TexCount;
        }
        UE_LOG(LogLiveSync, Log,
            TEXT("[FBX][IMPORTED_ASSET_SUMMARY] guid=%s meshes=%d materials=%d textures=%d"),
            *Request.ObjectGUID.ToString(EGuidFormats::Digits),
            MeshCount, MatCount, TexCount);

        for (UObject* Obj : ImportedObjects)
        {
            if (Obj->IsA<UTexture>())
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("[FBX][IMPORTED_TEXTURE] guid=%s path=%s"),
                    *Request.ObjectGUID.ToString(EGuidFormats::Digits),
                    *Obj->GetPathName());
            }
        }

        // Phase 7H.6 / Task 9B.1: sidecar texture import (always runs).
        // Even when FBX import produces textures (embedded media), sidecar
        // lane is the sole authority — it reimports with canonical naming.
        const FString FbxDir = FPaths::GetPath(FbxPathStr);
            TArray<FString> ManifestSidecarFiles;

            // Task C/D: read manifest.json for deterministic sidecar texture discovery
            FString MeshName = FPaths::GetCleanFilename(FbxPathStr);
            const FString FbxExt = TEXT(".fbx");
            if (MeshName.EndsWith(FbxExt))
            {
                MeshName = MeshName.Left(MeshName.Len() - FbxExt.Len());
            }
            const FString ManifestPath = FbxDir / (MeshName + TEXT(".manifest.json"));

            if (IFileManager::Get().FileExists(*ManifestPath))
            {
                FString ManifestContent;
                if (FFileHelper::LoadFileToString(ManifestContent, *ManifestPath))
                {
                    TSharedPtr<FJsonObject> ManifestJson;
                    TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(ManifestContent);
                    if (FJsonSerializer::Deserialize(Reader, ManifestJson) && ManifestJson.IsValid())
                    {
                        if (ManifestJson->HasTypedField<EJson::Array>(TEXT("sidecar_textures")))
                        {
                            const TArray<TSharedPtr<FJsonValue>>& SidecarArray =
                                ManifestJson->GetArrayField(TEXT("sidecar_textures"));
                            FString LogStr = TEXT("[FBX][SIDECAR_MANIFEST_READ] path=") + ManifestPath +
                                TEXT(" textureCount=") + FString::FromInt(SidecarArray.Num());
                            UE_LOG(LogLiveSync, Log, TEXT("%s"), *LogStr);

                            for (const TSharedPtr<FJsonValue>& Val : SidecarArray)
                            {
                                if (Val->Type != EJson::Object)
                                    continue;

                                const TSharedPtr<FJsonObject>* SidecarObj = nullptr;
                                if (Val->TryGetObject(SidecarObj) && SidecarObj)
                                {
                                    FString SidecarFile = (*SidecarObj)->GetStringField(TEXT("filename"));
                                    FString SidecarPath = (*SidecarObj)->GetStringField(TEXT("path"));
                                    int64 SidecarSize = (*SidecarObj)->GetNumberField(TEXT("size"));

                                    FString ExpLog = TEXT("[FBX][SIDECAR_EXPECTED] file=") + SidecarFile +
                                        TEXT(" path=") + SidecarPath +
                                        TEXT(" size=") + FString::FromInt(SidecarSize);
                                    UE_LOG(LogLiveSync, Log, TEXT("%s"), *ExpLog);

                                    int32 WaitAttempts = 0;
                                    const int32 MaxWaitAttempts = 10;
                                    const float WaitMs = 0.1f;
                                    bool bFound = false;

                                    while (WaitAttempts < MaxWaitAttempts)
                                    {
                                        if (IFileManager::Get().FileExists(*SidecarPath))
                                        {
                                            int64 ActualSize = IFileManager::Get().FileSize(*SidecarPath);
                                            if (ActualSize > 0)
                                            {
                                                FString FoundLog = TEXT("[FBX][SIDECAR_EXPECTED_FOUND] file=") + SidecarFile +
                                                    TEXT(" path=") + SidecarPath +
                                                    TEXT(" size=") + FString::FromInt(ActualSize);
                                                UE_LOG(LogLiveSync, Log, TEXT("%s"), *FoundLog);
                                                ManifestSidecarFiles.Add(SidecarPath);
                                                bFound = true;
                                                break;
                                            }
                                        }
                                        WaitAttempts++;
                                        FString WaitLog = TEXT("[FBX][SIDECAR_EXPECTED_WAIT] file=") + SidecarFile +
                                            TEXT(" attempt=") + FString::FromInt(WaitAttempts) +
                                            TEXT(" exists=0 size=0");
                                        UE_LOG(LogLiveSync, Log, TEXT("%s"), *WaitLog);
                                        FPlatformProcess::Sleep(WaitMs);
                                    }

                                    if (!bFound)
                                    {
                                        FString MissingLog = TEXT("[FBX][SIDECAR_EXPECTED_MISSING] file=") + SidecarFile +
                                            TEXT(" attempts=") + FString::FromInt(WaitAttempts);
                                        UE_LOG(LogLiveSync, Log, TEXT("%s"), *MissingLog);
                                    }
                                }
                            }
                        }
                        else
                        {
                            FString NoTexLog = TEXT("[FBX][SIDECAR_MANIFEST_NO_TEXTURES_FIELD] path=") + ManifestPath;
                            UE_LOG(LogLiveSync, Log, TEXT("%s"), *NoTexLog);
                        }
                    }
                    else
                    {
                        FString ParseFailLog = TEXT("[FBX][SIDECAR_MANIFEST_PARSE_FAIL] path=") + ManifestPath;
                        UE_LOG(LogLiveSync, Log, TEXT("%s"), *ParseFailLog);
                    }
                }
                else
                {
                    FString ReadFailLog = TEXT("[FBX][SIDECAR_MANIFEST_READ_FAIL] path=") + ManifestPath;
                    UE_LOG(LogLiveSync, Log, TEXT("%s"), *ReadFailLog);
                }
            }
            else
            {
                FString NotFoundLog = TEXT("[FBX][SIDECAR_MANIFEST_NOT_FOUND] path=") + ManifestPath;
                UE_LOG(LogLiveSync, Log, TEXT("%s"), *NotFoundLog);
            }

            TArray<FString> SidecarFiles;

            // Canonical extension list (lowercase, no leading dot).
            static const TCHAR* AcceptedExtensions[] = {
                TEXT("png"), TEXT("jpg"), TEXT("jpeg"),
                TEXT("tga"), TEXT("exr"), TEXT("bmp"),
                TEXT("tif"), TEXT("tiff"), TEXT("hdr")
            };
            constexpr int32 NumAcceptedExtensions = 9;

            auto IsImageExtension = [](const FString& ExtLower)
            {
                for (int32 i = 0; i < NumAcceptedExtensions; ++i)
                {
                    if (ExtLower == AcceptedExtensions[i])
                        return true;
                }
                return false;
            };

            // Scan a single folder via IterateDirectory.
            // NOTE: On Linux, IterateDirectory visitor receives the FULL PATH (Directory / d_name),
            // not just the basename. Must detect and handle this correctly.
            auto ScanFolder = [&SidecarFiles, &IsImageExtension, &FbxDir](const FString& FolderPath, const FString& FolderLabel)
            {
                bool bIterOk = IFileManager::Get().IterateDirectory(
                    *FolderPath,
                    [&SidecarFiles, &IsImageExtension, &FolderPath, &FolderLabel](const TCHAR* Filename, bool bIsFolder)
                    {
                        // Normalize entry path: detect if Filename is already absolute
                        FString EntryPath = Filename;
                        FPaths::NormalizeFilename(EntryPath);

                        FString FullPath;
                        if (FPaths::IsRelative(EntryPath))
                        {
                            FullPath = FPaths::Combine(FolderPath, EntryPath);
                        }
                        else
                        {
                            FullPath = EntryPath;
                        }
                        FPaths::NormalizeFilename(FullPath);

                        const FString FileName = FPaths::GetCleanFilename(FullPath);
                        const FString ExtLower = FPaths::GetExtension(FullPath).ToLower();
                        const bool bExists = IFileManager::Get().FileExists(*FullPath);
                        const bool bIsImage = IsImageExtension(ExtLower);

                        // Task E.1: raw directory entry log with normalized values
                        const bool bIsImageCheck = !bIsFolder;
                        if (bIsImageCheck)
                        {
                            FString EntryLog = TEXT("[FBX][SIDECAR_TEXTURE_DIR_ENTRY] folder=") + FolderPath +
                                TEXT(" raw=") + Filename +
                                TEXT(" full=") + FullPath +
                                TEXT(" file=") + FileName +
                                TEXT(" isDir=") + FString::FromInt(bIsFolder ? 1 : 0) +
                                TEXT(" exists=") + FString::FromInt(bExists ? 1 : 0);
                            UE_LOG(LogLiveSync, Log, TEXT("%s"), *EntryLog);
                        }

                        if (bIsFolder)
                            return true;

                        // Task E.2: candidate log with normalized values
                        FString CandidateLog = TEXT("[FBX][SIDECAR_TEXTURE_CANDIDATE] folder=") + FolderPath +
                            TEXT(" file=") + FileName +
                            TEXT(" full=") + FullPath +
                            TEXT(" ext=") + ExtLower +
                            TEXT(" isImage=") + FString::FromInt(bIsImage ? 1 : 0) +
                            TEXT(" exists=") + FString::FromInt(bExists ? 1 : 0);
                        UE_LOG(LogLiveSync, Log, TEXT("%s"), *CandidateLog);

                        if (bIsImage)
                        {
                            SidecarFiles.Add(FullPath);
                        }
                        return true;
                    });

                if (!bIterOk)
                {
                    // Task E.3: distinguish missing optional subfolder from real failure
                    const bool bExistsDir = IFileManager::Get().DirectoryExists(*FolderPath);
                    if (bExistsDir)
                    {
                        UE_LOG(LogLiveSync, Warning,
                            TEXT("[FBX][SIDECAR_TEXTURE_SCAN_FOLDER_FAIL] folder=%s reason=directory_iter_failed"),
                            *FolderPath);
                    }
                    else if (FolderLabel == TEXT("textures"))
                    {
                        // Missing optional subfolder — log as info, not failure
                        UE_LOG(LogLiveSync, Log,
                            TEXT("[FBX][SIDECAR_TEXTURE_SCAN_FOLDER_SKIP] folder=%s reason=missing_optional_subfolder"),
                            *FolderPath);
                    }
                    else
                    {
                        // Base folder missing — real error
                        UE_LOG(LogLiveSync, Error,
                            TEXT("[FBX][SIDECAR_TEXTURE_SCAN_FOLDER_FAIL] folder=%s reason=directory_does_not_exist"),
                            *FolderPath);
                    }
                }
            };

            // Scan base FBX folder.
            ScanFolder(FbxDir, TEXT("base"));

            // Also scan textures/ subfolder.
            {
                const FString TexturesPath = FbxDir / TEXT("textures");
                ScanFolder(TexturesPath, TEXT("textures"));
            }

            // Task C: Single bounded retry — Blender may not have finished writing sidecar files.
            if (SidecarFiles.Num() == 0)
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("[FBX][SIDECAR_TEXTURE_RETRY] reason=no_images_first_scan delay_ms=50"));
                FPlatformProcess::Sleep(0.05f);

                // Re-scan base folder only
                IFileManager::Get().IterateDirectory(
                    *FbxDir,
                    [&SidecarFiles, &IsImageExtension, &FbxDir](const TCHAR* Filename, bool bIsFolder)
                    {
                        FString EntryPath = Filename;
                        FPaths::NormalizeFilename(EntryPath);
                        if (!FPaths::IsRelative(EntryPath))
                        {
                            // Already absolute from Linux IterateDirectory — skip combine
                        }
                        else
                        {
                            // Relative path (shouldn't happen on Linux, but be safe)
                        }
                        FString CleanPath = FPaths::IsRelative(EntryPath)
                            ? FPaths::Combine(FbxDir, EntryPath)
                            : EntryPath;
                        FPaths::NormalizeFilename(CleanPath);

                        if (bIsFolder) return true;
                        const FString FileName = FPaths::GetCleanFilename(CleanPath);
                        const FString ExtLower = FPaths::GetExtension(CleanPath).ToLower();
                        const bool bExists = IFileManager::Get().FileExists(*CleanPath);
                        const bool bIsImage = IsImageExtension(ExtLower);

                        FString CandidateLog = TEXT("[FBX][SIDECAR_TEXTURE_CANDIDATE] folder=") + FbxDir +
                            TEXT(" file=") + FileName +
                            TEXT(" full=") + CleanPath +
                            TEXT(" ext=") + ExtLower +
                            TEXT(" isImage=") + FString::FromInt(bIsImage ? 1 : 0) +
                            TEXT(" exists=") + FString::FromInt(bExists ? 1 : 0);
                        UE_LOG(LogLiveSync, Log, TEXT("%s"), *CandidateLog);

                        if (bIsImage)
                        {
                            SidecarFiles.Add(CleanPath);
                        }
                        return true;
                    });
            }

            // Task B: Fallback scan using FindFilesRecursive if IterateDirectory found nothing.
            if (SidecarFiles.Num() == 0)
            {
                TArray<FString> AllFiles;
                IFileManager::Get().FindFilesRecursive(AllFiles, *FbxDir, TEXT("*"), true, false);
                int32 RawCount = AllFiles.Num();

                FString FallbackLog = TEXT("[FBX][SIDECAR_TEXTURE_FALLBACK_SCAN] folder=") + FbxDir +
                    TEXT(" rawCount=") + FString::FromInt(RawCount) +
                    TEXT(" found=") + FString::FromInt(RawCount);
                UE_LOG(LogLiveSync, Log, TEXT("%s"), *FallbackLog);

                for (const FString& FullPath : AllFiles)
                {
                    const FString FileName = FPaths::GetCleanFilename(FullPath);
                    const FString ExtLower = FPaths::GetExtension(FullPath).ToLower();
                    const bool bIsImage = IsImageExtension(ExtLower);

                    FString CandidateLog = TEXT("[FBX][SIDECAR_TEXTURE_CANDIDATE] folder=") + FbxDir +
                        TEXT(" file=") + FileName +
                        TEXT(" full=") + FullPath +
                        TEXT(" ext=") + ExtLower +
                        TEXT(" isImage=") + FString::FromInt(bIsImage ? 1 : 0) +
                        TEXT(" exists=") + FString::FromInt(1);  // Found by FindFilesRecursive so exists
                    UE_LOG(LogLiveSync, Log, TEXT("%s"), *CandidateLog);

                    if (bIsImage)
                    {
                        SidecarFiles.Add(FullPath);
                    }
                }
            }

            // Task D: merge manifest-declared sidecar files into scan list
            for (const FString& M : ManifestSidecarFiles)
            {
                SidecarFiles.AddUnique(M);
                FString ImportLog = TEXT("[FBX][SIDECAR_TEXTURE_IMPORT] src=") + M +
                    TEXT(" dst=") + AssetBasePath;
                UE_LOG(LogLiveSync, Log, TEXT("%s"), *ImportLog);
            }

            // Deduplicate by normalized path — folder scans (base + textures/)
            // can produce the same file in both directories.
            if (SidecarFiles.Num() > 1)
            {
                int32 PreDedup = SidecarFiles.Num();
                SidecarFiles.Sort();
                TArray<FString> UniqueFiles;
                for (const FString& P : SidecarFiles)
                {
                    if (!UniqueFiles.Contains(P))
                    {
                        UniqueFiles.Add(P);
                    }
                }
                SidecarFiles = UniqueFiles;
                UE_LOG(LogLiveSync, Log,
                    TEXT("[FBX][SIDECAR_DEDUP] pre=%d post=%d"),
                    PreDedup, SidecarFiles.Num());
            }

            // Build per-file list for summary log.
            TArray<FString> FileNames;
            for (const FString& P : SidecarFiles)
            {
                FileNames.Add(FPaths::GetCleanFilename(P));
            }

            if (SidecarFiles.Num() > 0)
            {
                FString FileListStr;
                for (int32 Fi = 0; Fi < FileNames.Num(); ++Fi)
                {
                    if (Fi > 0) FileListStr += TEXT(", ");
                    FileListStr += FileNames[Fi];
                }
                UE_LOG(LogLiveSync, Log,
                    TEXT("[FBX][SIDECAR_TEXTURE_SCAN] folder=%s count=%d files=[%s]"),
                    *FbxDir, SidecarFiles.Num(), *FileListStr);

                // Task 7A: Automated sidecar texture import — no modal dialogs.
                // Original code called IAssetTools::ImportAssets() which has
                // bForceOverrideExisting hardcoded to false, causing the modal
                // "This import will override asset, do you want to convert to
                // re-import?" dialog.
                //
                // New policy:
                // 1. Compute deterministic destination object path per file.
                // 2. Check if UTexture2D already exists at that path.
                // 3. If exists and unchanged → reuse.
                // 4. If exists and changed → programmatic reimport.
                // 5. If not exists → automated import.
                // All paths use ImportAssetsAutomated with bReplaceExisting=true,
                // which sets bForceOverrideExisting internally, preventing the
                // interactive dialog.

                // Task 9B.2: sidecar textures import under a dedicated namespace
                // to avoid collision with FBX material assets.
                const FString SidecarAssetBasePath =
                    TEXT("/Game/UELiveSync/Imported/Textures");

                // Per-file lookup for existing assets.
                TArray<UObject*> ImportedTexs;
                ImportedTexs.Reserve(SidecarFiles.Num());
                TArray<FString> NewFilesForImport;
                NewFilesForImport.Reserve(SidecarFiles.Num());

                for (const FString& SourceFile : SidecarFiles)
                {
                    FString BaseName = FPaths::GetBaseFilename(SourceFile);
                    // Task 9B.2: textures go to /Game/UELiveSync/Imported/Textures/
                    // to prevent /Game/UELiveSync/Imported/Wood.Wood (material) vs
                    // /Game/UELiveSync/Imported/Textures/Wood.Wood (texture) collision.
                    FString DestPackagePath = SidecarAssetBasePath / BaseName;
                    FString ObjectPath = DestPackagePath + TEXT(".") + BaseName;

                    // Task 9B.2: in-memory existence check only — no StaticLoadObject probe.
                    UObject* ExistingTexture = nullptr;
                    UObject* MaybeAsset = StaticFindObject(
                        UTexture2D::StaticClass(), nullptr, *ObjectPath, EFindObjectFlags::ExactClass);
                    if (MaybeAsset && MaybeAsset->IsA<UTexture2D>())
                    {
                        ExistingTexture = MaybeAsset;
                    }

                    UE_LOG(LogLiveSync, Log,
                        TEXT("[FBX][SIDECAR_ASSET_LOOKUP] source=%s objectPath=%s exists=%d"),
                        *SourceFile, *ObjectPath,
                        ExistingTexture ? 1 : 0);

                    if (ExistingTexture && !ImportedTexs.Contains(ExistingTexture))
                    {
                        // Task 7A: Detect if file changed by comparing source file
                        // mtime to the texture's cached source timestamp.
                        bool bFileChanged = true;
                        int64 SrcSize = 0;
                        if (FPlatformFileManager::Get().GetPlatformFile()
                                .FileExists(*SourceFile))
                        {
                            SrcSize = FPlatformFileManager::Get()
                                .GetPlatformFile()
                                .FileSize(*SourceFile);
                            // For sidecar textures, the file is rewritten on every
                            // Blender export, so mtime always changes. Use a simpler
                            // heuristic: if the object path exists and the asset was
                            // just loaded, assume it needs reimport (bFileChanged=true).
                            // Optimization: skip reimport if mtime matches last import.
                            bFileChanged = true;
                        }

                        if (!bFileChanged)
                        {
                            // File unchanged — reuse existing texture.
                            ImportedTexs.Add(ExistingTexture);
                            UE_LOG(LogLiveSync, Log,
                                TEXT("[FBX][SIDECAR_TEXTURE_REUSE] source=%s texture=%s"),
                                *SourceFile, *ObjectPath);
                        }
                        else
                        {
                            // File changed — programmatic reimport.
                            ImportedTexs.Add(nullptr);
                            NewFilesForImport.Add(SourceFile);
                            UE_LOG(LogLiveSync, Log,
                                TEXT("[FBX][SIDECAR_TEXTURE_REIMPORT] source=%s texture=%s"),
                                *SourceFile, *ObjectPath);
                        }
                    }
                    else
                    {
                        // No existing asset — new import.
                        ImportedTexs.Add(nullptr);
                        NewFilesForImport.Add(SourceFile);
                        UE_LOG(LogLiveSync, Log,
                            TEXT("[FBX][SIDECAR_TEXTURE_IMPORT_NEW] source=%s destination=%s"),
                            *SourceFile, *DestPackagePath);
                    }
                }

                // Task 9B.1: per-source import and canonical-name matching.
                // ImportAssetsAutomated may return results in any order;
                // do not assume input index == returned asset index.
                if (NewFilesForImport.Num() > 0)
                {
                    UE_LOG(LogLiveSync, Log,
                        TEXT("[FBX][SIDECAR_IMPORT_AUTOMATED] automated=1 replaceExisting=1"));

                    TArray<UObject*> NewImportedTexs;
                    for (const FString& ImportFile : NewFilesForImport)
                    {
                        UAutomatedAssetImportData* SingleImport =
                            NewObject<UAutomatedAssetImportData>();
                        SingleImport->GroupName = TEXT("UELiveSync-Sidecar");
                        SingleImport->Filenames = { ImportFile };
                        SingleImport->DestinationPath = SidecarAssetBasePath;
                        SingleImport->bReplaceExisting = true;
                        SingleImport->bSkipReadOnly = false;

                        TArray<UObject*> SingleResult =
                            AssetTools.ImportAssetsAutomated(SingleImport);
                        if (SingleResult.Num() > 0 && SingleResult[0] &&
                            SingleResult[0]->IsA<UTexture2D>())
                        {
                            NewImportedTexs.Add(SingleResult[0]);
                        }
                    }

                    // Match each imported texture to its source by canonical name.
                    for (int32 Fi = 0; Fi < ImportedTexs.Num(); ++Fi)
                    {
                        if (ImportedTexs[Fi] != nullptr)
                            continue; // existing texture reuse — already set

                        FString SourceFile = SidecarFiles.IsValidIndex(Fi)
                            ? SidecarFiles[Fi] : FString();
                        if (SourceFile.IsEmpty())
                            continue;

                        FString SourceKey = FPaths::GetBaseFilename(SourceFile).ToLower();

                        for (UObject* ImportedObj : NewImportedTexs)
                        {
                            if (!ImportedObj || !ImportedObj->IsA<UTexture2D>())
                                continue;

                            FString ObjName = ImportedObj->GetName();
                            FString AssetKey = ObjName.ToLower();

                            if (AssetKey == SourceKey)
                            {
                                ImportedTexs[Fi] = ImportedObj;
                                break;
                            }

                            // Handle _ncl1_1 suffix from UE FBX import naming.
                            const FString NclSuffix = TEXT("_ncl1_1");
                            if (AssetKey.EndsWith(NclSuffix))
                            {
                                FString Stripped = AssetKey.LeftChop(NclSuffix.Len());
                                if (Stripped == SourceKey)
                                {
                                    ImportedTexs[Fi] = ImportedObj;
                                    break;
                                }
                            }
                        }

                        if (!ImportedTexs[Fi])
                        {
                            UE_LOG(LogLiveSync, Warning,
                                TEXT("[FBX][SIDECAR_RESULT_MISMATCH] source=%s key=%s reason=no_matching_asset"),
                                *SourceFile, *SourceKey);
                        }
                    }
                }
                else
                {
                    UE_LOG(LogLiveSync, Log,
                        TEXT("[FBX][SIDECAR_IMPORT_AUTOMATED] automated=1 replaceExisting=0 all_reused"));
                }

                // Log final texture results and populate per-GUID sidecar map.
                int32 EffectiveTexCount = 0;
                int32 MismatchCount = 0;
                int32 DuplicateTexCount = 0;
                const FGuid& CurGuid = Request.ObjectGUID;

                for (int32 Fi = 0; Fi < ImportedTexs.Num(); ++Fi)
                {
                    UObject* Tex = ImportedTexs[Fi];
                    if (Tex && Tex->IsA<UTexture>())
                    {
                        // Task 9B.1: verify canonical source key matches canonical asset name.
                        FString SourceFile = SidecarFiles.IsValidIndex(Fi) ? SidecarFiles[Fi] : FString();
                        FString BaseName = FPaths::GetBaseFilename(SourceFile);
                        FString CanonicalKey = BaseName.ToLower();
                        FString TexturePath = Tex->GetPathName();
                        FString AssetKey = Tex->GetName().ToLower();

                        // Strip _ncl1_1 suffix for comparison.
                        FString CompareAssetKey = AssetKey;
                        const FString NclSuffix = TEXT("_ncl1_1");
                        if (CompareAssetKey.EndsWith(NclSuffix))
                        {
                            CompareAssetKey = CompareAssetKey.LeftChop(NclSuffix.Len());
                        }

                        if (CanonicalKey != CompareAssetKey)
                        {
                            UE_LOG(LogLiveSync, Warning,
                                TEXT("[FBX][SIDECAR_RESULT] guid=%s source=%s key=%s asset=%s assetKey=%s result=mismatch"),
                                *CurGuid.ToString(EGuidFormats::Digits),
                                *SourceFile, *CanonicalKey, *TexturePath, *AssetKey);
                            MismatchCount++;
                            continue; // Do not register mismatched mapping.
                        }

                        EffectiveTexCount++;

                        // Task 9B: register per-GUID sidecar map entry.
                        UE_LOG(LogLiveSync, Log,
                            TEXT("[FBX][SIDECAR_RESULT] guid=%s source=%s key=%s asset=%s result=ok"),
                            *CurGuid.ToString(EGuidFormats::Digits),
                            *SourceFile, *CanonicalKey, *TexturePath);

                        // Call sidecar registration callback if bound.
                        if (Context.OnSidecarTextureImported)
                        {
                            TSoftObjectPtr<UTexture2D> TexPtr = Cast<UTexture2D>(Tex);
                            Context.OnSidecarTextureImported(CurGuid, SourceFile, TexPtr);
                        }
                    }
                    else if (Tex)
                    {
                        UE_LOG(LogLiveSync, Warning,
                            TEXT("[FBX][SIDECAR_TEXTURE_IMPORT_FAIL] src=%s reason=not_a_texture"),
                            *Tex->GetPathName());
                    }
                }

                // Count duplicates (same canonical key mapping to different assets).
                // SidecarFiles dedup ensures one file → one import → one texture.
                // Any mismatch indicates a sidecar scan issue, not import issue.
                if (SidecarFiles.Num() > EffectiveTexCount)
                {
                    DuplicateTexCount = SidecarFiles.Num() - EffectiveTexCount;
                }

                // Per-GUID sidecar map ready summary.
                {
                    // Check how many entries the subsystem map has.
                    // We cannot access it directly here — log expected/actual counts.
                    int32 Expected = SidecarFiles.Num();
                    int32 Resolved = EffectiveTexCount;
                    int32 Missing = Expected - Resolved - MismatchCount;
                    UE_LOG(LogLiveSync, Log,
                        TEXT("[FBX][SIDECAR_RESULT_MAP_READY] guid=%s expected=%d resolved=%d missing=%d mismatched=%d duplicates=%d"),
                        *CurGuid.ToString(EGuidFormats::Digits), Expected, Resolved, Missing, MismatchCount, DuplicateTexCount);
                }

                if (EffectiveTexCount > 0)
                {
                    UE_LOG(LogLiveSync, Log,
                        TEXT("[FBX][IMPORTED_ASSET_SUMMARY] guid=%s meshes=%d materials=%d textures=%d (after_sidecar)"),
                        *Request.ObjectGUID.ToString(EGuidFormats::Digits),
                        MeshCount, MatCount, TexCount + EffectiveTexCount);
                }
                else
                {
                    UE_LOG(LogLiveSync, Warning,
                        TEXT("[FBX][SIDECAR_TEXTURE_IMPORT_FAIL] src=all reason=import_assets_returned_zero"));
                }
            }
            else
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("[FBX][SIDECAR_TEXTURE_SCAN] folder=%s count=0 reason=no_image_files_found"),
                    *FbxDir);
            }
    }

    UObject* PendingAsset = nullptr;
    if (ImportedObjects.Num() > 0)
    {
        PendingAsset = ImportedObjects[0];
    }

    if (!PendingAsset)
    {
        PendingAsset = StaticLoadObject(
            UObject::StaticClass(), nullptr, *FullPendingPath);
    }

    if (!PendingAsset)
    {
        Context.Stats->FBXImportFailed.fetch_add(
            1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Warning,
            TEXT("[FBX] Pending import failed — no asset created for %s"),
            *FbxPathStr);
        return false;
    }

    UStaticMesh* PendingMesh = Cast<UStaticMesh>(PendingAsset);
    if (!PendingMesh)
    {
        Context.Stats->FBXImportFailed.fetch_add(
            1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Warning,
            TEXT("[FBX] Pending asset is not a StaticMesh: %s"),
            *PendingAsset->GetName());
        return false;
    }

    // Phase 10J.6: TEMP_IMPORT log at confirmed temp asset.
    UE_LOG(LogLiveSync, Log,
        TEXT("[FBX][TEMP_IMPORT] guid=%s path=%s"),
        *Request.ObjectGUID.ToString(EGuidFormats::Digits),
        *PendingMesh->GetPathName());

    // === Phase 10J.5Q: Validate pending mesh bounds BEFORE any rename/swap ===
    // Cache-based only: the FBX request payload does not carry Blender bounds,
    // so no current expected cm extent is available without a protocol change.
    const FVector PendingExtent = PendingMesh->GetBounds().BoxExtent;
    constexpr float MeterSizeMinRatio = 50.0f;
    constexpr float MeterSizeMaxRatio = 250.0f;
    bool bAcceptPending = false;
    {
        const float NewMax = FMath::Max3(PendingExtent.X, PendingExtent.Y, PendingExtent.Z);
        const FVector* CachedExtent = GBoundsExtentCache.Find(Request.ObjectGUID);
        if (CachedExtent && IsValidFBXBoundsExtent(*CachedExtent))
        {
            const float CachedMax = FMath::Max3(CachedExtent->X, CachedExtent->Y, CachedExtent->Z);
            if (NewMax > 0.001f && CachedMax > 0.0f)
            {
                const float RatioSmall = CachedMax / NewMax;
                const float RatioLarge = NewMax / CachedMax;

                if (RatioSmall >= MeterSizeMinRatio && RatioSmall <= MeterSizeMaxRatio)
                {
                    UE_LOG(LogLiveSync, Warning,
                        TEXT("[FBX][UNIT_INVALID] guid=%s rawExtent=(%.1f,%.1f,%.1f) "
                             "expectedCmExtent=(%.1f,%.1f,%.1f) ratio=%.1f "
                             "reason=reimport_meter_size_regression action=reject_keep_previous"),
                        *Request.ObjectGUID.ToString(EGuidFormats::Digits),
                        PendingExtent.X, PendingExtent.Y, PendingExtent.Z,
                        CachedExtent->X, CachedExtent->Y, CachedExtent->Z,
                        RatioSmall);

                    bAcceptPending = false;
                }
                else if (RatioLarge >= MeterSizeMinRatio && RatioLarge <= MeterSizeMaxRatio)
                {
                    UE_LOG(LogLiveSync, Warning,
                        TEXT("[FBX][UNIT_INVALID] guid=%s rawExtent=(%.1f,%.1f,%.1f) "
                             "expectedCmExtent=(%.1f,%.1f,%.1f) ratio=%.2f "
                             "reason=imported_100x_too_large action=reject_keep_previous"),
                        *Request.ObjectGUID.ToString(EGuidFormats::Digits),
                        PendingExtent.X, PendingExtent.Y, PendingExtent.Z,
                        CachedExtent->X, CachedExtent->Y, CachedExtent->Z,
                        RatioLarge);

                    bAcceptPending = false;
                }
                else
                {
                    bAcceptPending = true;
                }
            }
            else
            {
                bAcceptPending = true;
            }
        }
        else if (!CachedExtent && IsValidFBXBoundsExtent(PendingExtent))
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[FBX][FIRST_IMPORT] guid=%s pendingExtent=(%.1f,%.1f,%.1f) "
                     "action=accept_first_import"),
                *Request.ObjectGUID.ToString(EGuidFormats::Digits),
                PendingExtent.X, PendingExtent.Y, PendingExtent.Z);
            bAcceptPending = true;
        }
        else if (!CachedExtent && !IsValidFBXBoundsExtent(PendingExtent))
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[FBX][UNIT_FAIL_NO_GOOD_MESH] guid=%s pendingExtent=(%.1f,%.1f,%.1f) "
                     "reason=initial_import_oversized_or_tiny action=accept_anyway_no_cache"),
                *Request.ObjectGUID.ToString(EGuidFormats::Digits),
                PendingExtent.X, PendingExtent.Y, PendingExtent.Z);
            bAcceptPending = true;
        }
        else
        {
            bAcceptPending = true;
        }
    }

    if (!bAcceptPending)
    {
        // Delete pending mesh — never reaches the visible component.
        {
            FFBXImportSemanticSignature RejectSig = ComputeFBXSemanticSignature(FbxPathStr, Request);
            GSemanticSignatureCache.Add(Request.ObjectGUID, RejectSig);
        }

        TArray<UObject*> ToDelete = { PendingMesh };
        ObjectTools::DeleteObjects(ToDelete, false);

        Context.Stats->FBXImportSkipped.fetch_add(1, std::memory_order_relaxed);
        Context.Stats->FBXImportFailed.fetch_add(1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Log,
            TEXT("[FBX][UNIT_INVALID] guid=%s pending rejected — deleted, keeping previous good mesh"),
            *Request.ObjectGUID.ToString(EGuidFormats::Digits));
        UE_LOG(LogLiveSync, Log,
            TEXT("[FBX][TEMP_KEEP_PREVIOUS] guid=%s reason=validation_failed action=keep_existing_mesh"),
            *Request.ObjectGUID.ToString(EGuidFormats::Digits));
        return true;
    }

    // Phase 10J.5Q: Pending mesh is valid. Use it directly — no rename needed.
    // Each sync creates a unique-path asset; assigning it to the SMC is safe.
    UStaticMesh* StaticMesh = PendingMesh;

    Context.Stats->FBXImportSucceeded.fetch_add(
        1, std::memory_order_relaxed);
    UE_LOG(LogLiveSync, Log,
        TEXT("[FBX] Imported StaticMesh: %s (%d verts, %d tris, %d mat slots) "
             "pendingExtent=(%.1f,%.1f,%.1f)"),
        *StaticMesh->GetName(),
        Request.VertCount,
        Request.TriCount,
        Request.MatSlotCount,
        PendingExtent.X, PendingExtent.Y, PendingExtent.Z);

    // Phase 10J.5F: Update semantic signature cache after successful import.
    {
        FFBXImportSemanticSignature UpdatedSig = ComputeFBXSemanticSignature(FbxPathStr, Request);
        GSemanticSignatureCache.Add(Request.ObjectGUID, UpdatedSig);
    }

    // Spawn or update StaticMeshActor by LiveSync GUID
    // Save existing transform BEFORE any actor changes.
    FVector ExistingLocation(ForceInit);
    FRotator ExistingRotation(ForceInit);
    FVector ExistingScale(ForceInit);
    bool bHasExistingTransform = false;
    AActor* PreExistingActor = nullptr;

    {
        AActor* FoundActor = Context.FindActor(Request.ObjectGUID);
        PreExistingActor = FoundActor;
        if (FoundActor)
        {
            ExistingLocation = FoundActor->GetActorLocation();
            ExistingRotation = FoundActor->GetActorRotation();
            ExistingScale = FoundActor->GetActorScale3D();
            bHasExistingTransform = true;
        }
    }

    AStaticMeshActor* MeshActor = nullptr;
    bool bIsUpdate = false;

    if (PreExistingActor)
    {
        MeshActor = Cast<AStaticMeshActor>(PreExistingActor);
        if (MeshActor)
        {
            UStaticMeshComponent* SMC =
                MeshActor->GetStaticMeshComponent();

            // Phase 10J.5B.2: save material overrides before mesh
            // mutation.  Repeated FBX reimport may reset the mesh
            // data in-place while leaving OverrideMaterials cleared.
            TArray<UMaterialInterface*> SavedOverrides =
                SMC->OverrideMaterials;

            const UStaticMesh* CurrentMesh = SMC->GetStaticMesh();
            const bool bSameMeshPointer =
                (CurrentMesh == StaticMesh);

            if (bSameMeshPointer)
            {
                // Force the component to observe a pointer change
                // even when the imported UStaticMesh asset was
                // reimported in place with the same address.
                SMC->SetStaticMesh(nullptr);
                RefreshFBXStaticMeshComponent(SMC, MeshActor);
            }

            // Phase 10J.6: TEMP_ASSIGN log before final assignment.
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("[FBX][TEMP_ASSIGN] guid=%s newMesh=%s previousMesh=%s"),
                    *Request.ObjectGUID.ToString(EGuidFormats::Digits),
                    *StaticMesh->GetName(),
                    CurrentMesh ? *CurrentMesh->GetName() : TEXT("none"));
            }

            // Phase 10J.5Q: VISIBLE_EXTENT_FINAL — log the mesh extent that
            // the player will actually see after this mesh assignment.
            {
                const FVector AssignedExtent = StaticMesh->GetBounds().BoxExtent;
                UE_LOG(LogLiveSync, Log,
                    TEXT("[FBX][VISIBLE_EXTENT_FINAL] guid=%s mesh=%s extent=(%.1f,%.1f,%.1f) "
                         "pendingExtent=(%.1f,%.1f,%.1f)"),
                    *Request.ObjectGUID.ToString(EGuidFormats::Digits),
                    *StaticMesh->GetName(),
                    AssignedExtent.X, AssignedExtent.Y, AssignedExtent.Z,
                    PendingExtent.X, PendingExtent.Y, PendingExtent.Z);
            }

            SMC->SetStaticMesh(StaticMesh);

            // Phase 10J.5B.2: restore non-null material overrides.
            const int32 NumMatSlots = SMC->GetNumMaterials();
            for (int32 i = 0;
                 i < FMath::Min(SavedOverrides.Num(), NumMatSlots);
                 ++i)
            {
                if (SavedOverrides[i])
                {
                    SMC->SetMaterial(i, SavedOverrides[i]);
                }
            }

            // Full render/bounds/visibility refresh.
            RefreshFBXStaticMeshComponent(SMC, MeshActor);

            // Phase 10J.5D: ensure renderable with material fallback.
            EnsureFBXMeshRenderable(SMC, StaticMesh, MeshActor,
                Request.ObjectGUID);
            // Phase 10J.5L: restore generated MIDs after fallback (authoritative override)
            if (Context.OnRestoreGeneratedMaterials)
            {
                Context.OnRestoreGeneratedMaterials(Request.ObjectGUID, SMC);
            }
            LogExtendedFBXValidate(MeshActor, SMC, Request.ObjectGUID);

            if (Context.OnScheduleRepair)
            {
                Context.OnScheduleRepair(Request.ObjectGUID);
            }

            UE_LOG(LogLiveSync, Log,
                TEXT("[FBX][REFRESH] update guid=%s samePtr=%d"
                     " mesh=%s slots=%d overridesBefore=%d"
                     " overridesAfter=%d"),
                *Request.ObjectGUID.ToString(EGuidFormats::Digits),
                bSameMeshPointer ? 1 : 0,
                *StaticMesh->GetName(),
                NumMatSlots,
                SavedOverrides.Num(),
                SMC->OverrideMaterials.Num());

            Context.Stats->FBXImportActorsUpdated.fetch_add(
                 1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Log,
                TEXT("[FBX] Updated StaticMeshActor: %s"),
                *MeshActor->GetName());
            bIsUpdate = true;
        }
    }

    // Ensure ActorCache maps GUID → FBX actor (update path fix).
    if (MeshActor && Context.OnActorCached)
    {
        Context.OnActorCached(Request.ObjectGUID, MeshActor);
    }

    // Phase 10J.5E: Mark GUID as FBX-authoritative (update path).
    if (MeshActor && Context.OnMarkFbxAuthority)
    {
        Context.OnMarkFbxAuthority(Request.ObjectGUID);
    }

    // If the existing actor for this GUID was NOT a StaticMeshActor,
    // it was likely a procedural mesh actor. Destroy it and use
    // its transform for the new FBX actor.
    AActor* ActorToDestroy = nullptr;
    if (!bIsUpdate && PreExistingActor)
    {
        ActorToDestroy = PreExistingActor;
    }

    // Phase 10J.6: Cleanup previous temp mesh for this GUID.
    // Each sync creates a unique-path mesh; delete the last one to prevent
    // orphan accumulation.  Only delete after the new mesh is safely assigned
    // to the SMC — never delete the mesh currently in use.
    {
        FString* PrevPath = GLastAssignedMeshPath.Find(Request.ObjectGUID);
        if (PrevPath && MeshActor)
        {
            UStaticMeshComponent* CheckSMC = MeshActor->GetStaticMeshComponent();
            if (!CheckSMC)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[FBX][TEMP_DELETE_FAIL] guid=%s reason=no_smc path=%s"),
                    *Request.ObjectGUID.ToString(EGuidFormats::Digits),
                    **PrevPath);
            }
            else
            {
                UStaticMesh* PrevMesh = Cast<UStaticMesh>(
                    StaticLoadObject(UStaticMesh::StaticClass(), nullptr, **PrevPath));
                if (!PrevMesh)
                {
                    UE_LOG(LogLiveSync, Log,
                        TEXT("[FBX][TEMP_DELETE_FAIL] guid=%s reason=load_failed path=%s"),
                        *Request.ObjectGUID.ToString(EGuidFormats::Digits),
                        **PrevPath);
                }
                else if (PrevMesh == StaticMesh)
                {
                    UE_LOG(LogLiveSync, Log,
                        TEXT("[FBX][TEMP_DELETE_FAIL] guid=%s reason=same_mesh path=%s"),
                        *Request.ObjectGUID.ToString(EGuidFormats::Digits),
                        **PrevPath);
                }
                else if (CheckSMC->GetStaticMesh() != StaticMesh)
                {
                    UE_LOG(LogLiveSync, Warning,
                        TEXT("[FBX][TEMP_DELETE_FAIL] guid=%s reason=smc_mismatch path=%s"),
                        *Request.ObjectGUID.ToString(EGuidFormats::Digits),
                        **PrevPath);
                }
                else
                {
                    TArray<UObject*> ToDelete = { PrevMesh };
                    ObjectTools::DeleteObjects(ToDelete, false);
                    UE_LOG(LogLiveSync, Log,
                        TEXT("[FBX][TEMP_CLEANUP] guid=%s previous temp mesh deleted: %s"),
                        *Request.ObjectGUID.ToString(EGuidFormats::Digits),
                        **PrevPath);
                }
            }
        }
        else if (PrevPath && !MeshActor)
        {
            // Spawn path — no previous mesh to clean up; still record for future.
            UE_LOG(LogLiveSync, Log,
                TEXT("[FBX][TEMP_CLEANUP] guid=%s reason=spawn_path_nothing_to_cleanup path=%s"),
                *Request.ObjectGUID.ToString(EGuidFormats::Digits),
                **PrevPath);
        }
        GLastAssignedMeshPath.Add(Request.ObjectGUID, StaticMesh->GetPathName());
    }

    if (!MeshActor)
    {
        // Spawn new StaticMeshActor
        UWorld* World = Context.World;
        if (!World)
        {
            Context.Stats->FBXImportFailed.fetch_add(
                1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Error,
                TEXT("[FBX] No world — cannot spawn actor"));
            return false;
        }

        FActorSpawnParameters SpawnParams;
        SpawnParams.NameMode = FActorSpawnParameters::ESpawnActorNameMode::Requested;
        SpawnParams.Name = *FString::Printf(
            TEXT("LS_FBX_%s"), *GuidShort);

        MeshActor = World->SpawnActor<AStaticMeshActor>(
            SpawnParams);
        if (!MeshActor)
        {
            Context.Stats->FBXImportFailed.fetch_add(
                1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Error,
                TEXT("[FBX] Failed to spawn StaticMeshActor"));
            return false;
        }

        {
            UStaticMeshComponent* SMC =
                MeshActor->GetStaticMeshComponent();

            // Phase 10J.6: TEMP_ASSIGN on spawn
            UE_LOG(LogLiveSync, Log,
                TEXT("[FBX][TEMP_ASSIGN] guid=%s newMesh=%s action=spawn"),
                *Request.ObjectGUID.ToString(EGuidFormats::Digits),
                *StaticMesh->GetName());

            // Phase 10J.5Q: VISIBLE_EXTENT_FINAL on spawn
            UE_LOG(LogLiveSync, Log,
                TEXT("[FBX][VISIBLE_EXTENT_FINAL] guid=%s mesh=%s extent=(%.1f,%.1f,%.1f) "
                     "pendingExtent=(%.1f,%.1f,%.1f) action=spawn"),
                *Request.ObjectGUID.ToString(EGuidFormats::Digits),
                *StaticMesh->GetName(),
                StaticMesh->GetBounds().BoxExtent.X,
                StaticMesh->GetBounds().BoxExtent.Y,
                StaticMesh->GetBounds().BoxExtent.Z,
                PendingExtent.X, PendingExtent.Y, PendingExtent.Z);

            SMC->SetStaticMesh(StaticMesh);

            // Phase 10J.5B.2: refresh after initial mesh assignment.
            RefreshFBXStaticMeshComponent(SMC, MeshActor);

            // Phase 10J.5D: ensure renderable with material fallback.
            EnsureFBXMeshRenderable(SMC, StaticMesh, MeshActor,
                Request.ObjectGUID);
            // Phase 10J.5L: restore generated MIDs after fallback (authoritative override)
            if (Context.OnRestoreGeneratedMaterials)
            {
                Context.OnRestoreGeneratedMaterials(Request.ObjectGUID, SMC);
            }
            LogExtendedFBXValidate(MeshActor, SMC, Request.ObjectGUID);

            if (Context.OnScheduleRepair)
            {
                Context.OnScheduleRepair(Request.ObjectGUID);
            }

            const int32 NumMatSlots = SMC->GetNumMaterials();

            UE_LOG(LogLiveSync, Log,
                TEXT("[FBX][REFRESH] spawn guid=%s mesh=%s"
                     " slots=%d overrides=%d"),
                *Request.ObjectGUID.ToString(EGuidFormats::Digits),
                *StaticMesh->GetName(),
                NumMatSlots,
                SMC->OverrideMaterials.Num());
        }

        // Apply existing transform if available (fix for spawn at 0,0,0).
        // Phase 10J.5K: NEVER restore actor scale — always enforce scale=1.
        if (bHasExistingTransform)
        {
            MeshActor->SetActorLocation(ExistingLocation);
            MeshActor->SetActorRotation(ExistingRotation);
            // Do NOT restore ExistingScale — invariant requires scale=1
            MeshActor->SetActorScale3D(FVector::OneVector);
            UE_LOG(LogLiveSync, Log,
                TEXT("[FBX] Applied existing transform to spawned actor for GUID %s"),
                *Request.ObjectGUID.ToString(EGuidFormats::Digits));
        }

        // Tag with LiveSync GUID (matching HandleCreateObject pattern)
        FString TagString =
            FString::Printf(
                TEXT("LiveSync_GUID=%s"),
                *Request.ObjectGUID.ToString(
                    EGuidFormats::Digits));
        MeshActor->Tags.Add(
            FName(*TagString));

        if (Context.OnActorCached)
        {
            Context.OnActorCached(Request.ObjectGUID, MeshActor);
        }

        // Phase 10J.5E: Mark GUID as FBX-authoritative (spawn path).
        if (Context.OnMarkFbxAuthority)
        {
            Context.OnMarkFbxAuthority(Request.ObjectGUID);
        }

        Context.Stats->FBXImportActorsSpawned.fetch_add(
            1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Log,
            TEXT("[FBX] Spawned StaticMeshActor: LS_FBX_%s"),
            *GuidShort);
    }

    // Destroy old non-FBX actor for same GUID to prevent double ownership.
    if (ActorToDestroy && ActorToDestroy != MeshActor)
    {
        ActorToDestroy->Destroy();
        UE_LOG(LogLiveSync, Log,
            TEXT("[FBX][AUTH] cleanup_stale_procedural guid=%s actor=%s"),
            *Request.ObjectGUID.ToString(EGuidFormats::Digits),
            *ActorToDestroy->GetName());
    }
#else
    UE_LOG(LogLiveSync, Warning,
        TEXT("[FBX] Import only supported in editor — rejecting"));
    Context.Stats->FBXImportRequestsRejected.fetch_add(
        1, std::memory_order_relaxed);
#endif

    return true;
}
