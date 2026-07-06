#pragma once

#include "CoreMinimal.h"
#include "SyncTypes.h"

// Phase 10K.6: Phase metadata for timing hierarchy.
enum class EFbxPhaseKind : uint8
{
    Exclusive,
    Nested,
    InclusiveParent,
    Unobservable,
};

struct FFbxPhaseMetadata
{
    EFbxPhaseKind Kind;
    const TCHAR* Parent;

    FFbxPhaseMetadata(EFbxPhaseKind InKind, const TCHAR* InParent)
        : Kind(InKind), Parent(InParent)
    {}
};

class AActor;
class UWorld;
class AActor;
class UStaticMeshComponent;
class UStaticMesh;

struct FFBXImportContext
{
    UWorld* World = nullptr;
    FLiveSyncStats* Stats = nullptr;
    TFunction<AActor*(const FGuid&)> FindActor;
    TFunction<void(const FGuid&, AActor*)> OnActorCached;
    // Phase 10J.5E: Called when a GUID is promoted to FBX authority.
    TFunction<void(const FGuid&)> OnMarkFbxAuthority;
    // Phase 10J.5D.5: Called to schedule deferred visibility repair.
    TFunction<void(const FGuid&)> OnScheduleRepair;
    // Phase 10J.5L: Called after EnsureFBXMeshRenderable to restore generated MIDs.
    TFunction<void(const FGuid&, UStaticMeshComponent*)> OnRestoreGeneratedMaterials;
    // Task 9B: Called to register a sidecar texture import result.
    TFunction<void(const FGuid&, const FString&, const TSoftObjectPtr<UTexture2D>&)>
        OnSidecarTextureImported;
    // Task 9B.6B.8: Called when FBX import is skipped (geomHash dedup) to
    // ensure the sidecar result map is updated even without a new import.
    // Called before the early-return. FbxDir is the directory containing the
    // FBX file and its manifest.json.
    TFunction<void(const FGuid&, const FString&, const FString&)> OnSkipFbxImport;
    // Task 10K.3: Called when the active sidecar map is ready for the current
    // sync. Contains only current manifest entries — separate from the
    // persistent ImportedSidecarTexturesByGuid cache.
    TFunction<void(const FGuid&, const TMap<FString, TSoftObjectPtr<UTexture2D>>&)> OnActiveSidecarMapReady;
    // Task 9B.5B: FBX timing output (set by HandleImport)
    double* FbxImportMsOut = nullptr;
};

// Phase 10K.6: Exclusive phase registry for timing hierarchy.
// (defined internally in LiveSyncFBXImporter.cpp)

class FLiveSyncFBXImporter
{
public:
    static bool HandleImport(
        const uint8* PayloadPtr,
        int32 PayloadSize,
        const FFBXImportContext& Context
    );

    // Phase 10J.5D.5: Re-apply visibility/bounds/material on FBX actor.
    static void EnsureFBXMeshRenderable(
        UStaticMeshComponent* SMC,
        UStaticMesh* StaticMesh,
        AActor* OwnerActor,
        const FGuid& Guid,
        bool bGeometryHashChanged = false
    );
};
