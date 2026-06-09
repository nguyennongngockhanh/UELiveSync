#include "FBXImport/LiveSyncFBXImporter.h"

#if WITH_EDITOR
#include "AssetToolsModule.h"
#include "AssetImportTask.h"
#include "Factories/FbxFactory.h"
#include "Factories/FbxImportUI.h"
#include "Factories/FbxStaticMeshImportData.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/StaticMesh.h"
#endif

// =========================================================
// FBX IMPORT REQUEST (Phase 7C Stage 3A.1)
// =========================================================
// Validates the request, imports FBX as StaticMesh under
// /Game/UELiveSync/Imported, then spawns or updates a
// StaticMeshActor by LiveSync GUID tag.
//
// Game-thread only. Safe on missing actor / invalid path.
// =========================================================

// =========================================================
// VALIDATION HELPERS
// =========================================================

static bool ValidatePayloadSize(int32 PayloadSize, FLiveSyncStats& Stats)
{
    if (PayloadSize < static_cast<int32>(sizeof(FFBXImportRequestPayload)))
    {
        Stats.FBXImportRequestsRejected.fetch_add(
            1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Warning,
            TEXT("[FBX] Truncated request: size %d < %d"),
            PayloadSize, sizeof(FFBXImportRequestPayload));
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

    if (!ValidatePayloadSize(PayloadSize, *Context.Stats))
    {
        return false;
    }

    FFBXImportRequestPayload Request;
    FMemory::Memcpy(&Request, PayloadPtr, sizeof(FFBXImportRequestPayload));

    if (!ValidateVersion(Request.Version, *Context.Stats))
    {
        return false;
    }

    FString FbxPathStr(
        ANSI_TO_TCHAR(reinterpret_cast<const ANSICHAR*>(Request.FbxPath)));

    if (!ValidatePathSecurity(FbxPathStr, *Context.Stats))
    {
        return false;
    }

    FString SafeName = SanitizeObjectName(
        ANSI_TO_TCHAR(reinterpret_cast<const ANSICHAR*>(Request.ObjectName)));

    const FString GuidShort =
        Request.ObjectGUID.ToString().Left(8);
    const FString AssetBasePath =
        TEXT("/Game/UELiveSync/Imported");
    const FString AssetName =
        FString::Printf(TEXT("%s_%s"), *SafeName, *GuidShort);
    const FString AssetPackagePath =
        AssetBasePath / AssetName;

#if WITH_EDITOR
    // Check if the target asset already exists (for lifecycle diagnostics)
    const FString FullAssetPath =
        FString::Printf(TEXT("%s.%s"), *AssetPackagePath, *AssetName);
    const bool bReplacingExistingAsset =
        StaticLoadObject(UStaticMesh::StaticClass(), nullptr, *FullAssetPath) != nullptr;

    // Import FBX as StaticMesh using AssetImportTask
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
    ImportTask->DestinationName = AssetName;
    ImportTask->bReplaceExisting = true;
    ImportTask->bReplaceExistingSettings = true;
    ImportTask->bAutomated = true;
    ImportTask->bSave = false;
    ImportTask->bAsync = false;

    UFbxFactory* FbxFactory = NewObject<UFbxFactory>();
    if (FbxFactory)
    {
        FbxFactory->ImportUI->bAutomatedImportShouldDetectType = true;
        FbxFactory->ImportUI->StaticMeshImportData->bConvertSceneUnit = true;
        ImportTask->Factory = FbxFactory;
    }

    IAssetTools& AssetTools =
        FAssetToolsModule::GetModule().Get();
    AssetTools.ImportAssetTasks({ ImportTask });

    // Check import result
    TArray<UObject*> ImportedObjects = ImportTask->GetObjects();
    UObject* ImportedAsset = nullptr;
    if (ImportedObjects.Num() > 0)
    {
        ImportedAsset = ImportedObjects[0];
    }

    if (!ImportedAsset)
    {
        // Try finding by package path as fallback
        ImportedAsset = StaticLoadObject(
            UObject::StaticClass(), nullptr,
            *FString::Printf(TEXT("%s.%s"), *AssetPackagePath, *AssetName));
    }

    if (!ImportedAsset)
    {
        Context.Stats->FBXImportFailed.fetch_add(
            1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Warning,
            TEXT("[FBX] Import failed — no asset created for %s"),
            *FbxPathStr);
        return false;
    }

    UStaticMesh* StaticMesh = Cast<UStaticMesh>(ImportedAsset);
    if (!StaticMesh)
    {
        Context.Stats->FBXImportFailed.fetch_add(
            1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Warning,
            TEXT("[FBX] Imported asset is not a StaticMesh: %s"),
            *ImportedAsset->GetName());
        return false;
    }

    Context.Stats->FBXImportSucceeded.fetch_add(
        1, std::memory_order_relaxed);
    UE_LOG(LogLiveSync, Log,
        TEXT("[FBX] Imported StaticMesh: %s (%d verts, %d tris, %d mat slots)"),
        *StaticMesh->GetName(),
        Request.VertCount,
        Request.TriCount,
        Request.MatSlotCount);

    // Lifecycle diagnostics: new vs replaced asset
    if (bReplacingExistingAsset)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[FBX] Replaced existing imported asset: %s"),
            *AssetPackagePath);
    }
    else
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[FBX] Created new imported asset: %s"),
            *AssetPackagePath);
    }

    // Spawn or update StaticMeshActor by LiveSync GUID
    AActor* ExistingActor = Context.FindActor(Request.ObjectGUID);
    AStaticMeshActor* MeshActor = nullptr;

    if (ExistingActor)
    {
        MeshActor = Cast<AStaticMeshActor>(ExistingActor);
        if (MeshActor)
        {
            MeshActor->GetStaticMeshComponent()->SetStaticMesh(StaticMesh);
            Context.Stats->FBXImportActorsUpdated.fetch_add(
                1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Log,
                TEXT("[FBX] Updated StaticMeshActor: %s"),
                *MeshActor->GetName());
        }
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

        MeshActor->GetStaticMeshComponent()->SetStaticMesh(StaticMesh);

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

        Context.Stats->FBXImportActorsSpawned.fetch_add(
            1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Log,
            TEXT("[FBX] Spawned StaticMeshActor: LS_FBX_%s"),
            *GuidShort);
    }
#else
    UE_LOG(LogLiveSync, Warning,
        TEXT("[FBX] Import only supported in editor — rejecting"));
    Context.Stats->FBXImportRequestsRejected.fetch_add(
        1, std::memory_order_relaxed);
#endif

    return true;
}
