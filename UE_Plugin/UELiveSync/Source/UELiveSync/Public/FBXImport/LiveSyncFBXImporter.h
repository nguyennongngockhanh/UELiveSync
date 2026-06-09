#pragma once

#include "CoreMinimal.h"
#include "SyncTypes.h"

class AActor;
class UWorld;

struct FFBXImportContext
{
    UWorld* World = nullptr;
    FLiveSyncStats* Stats = nullptr;
    TFunction<AActor*(const FGuid&)> FindActor;
    TFunction<void(const FGuid&, AActor*)> OnActorCached;
};

class FLiveSyncFBXImporter
{
public:
    static bool HandleImport(
        const uint8* PayloadPtr,
        int32 PayloadSize,
        const FFBXImportContext& Context
    );
};
