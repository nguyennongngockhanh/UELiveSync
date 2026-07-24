#pragma once

// =========================================================
// MaterialDefinitionDatabase.h — Protocol material definitions
// =========================================================
// Single source of truth for material definitions received via
// MATERIAL_CREATE and MATERIAL_UPDATE protocol messages.
//
// Responsibility: store and retrieve protocol material definitions.
// NO resolve, NO asset loading, NO MID creation, NO SetMaterial.
//
// Owned by UUELiveSyncSubsystem. Not by Bridge.
// =========================================================

#include "CoreMinimal.h"
#include "LiveSyncViews.h"

// =========================================================
// MaterialDefinition — runtime model (not wire DTO)
// =========================================================

struct MaterialDefinition
{
    FGuid Id;
    FString Name;
    FLinearColor BaseColor = FLinearColor::White;
    float Metallic = 0.0f;
    float Roughness = 0.5f;
    FLinearColor Emission = FLinearColor::Black;
    bool bHasTexturePath = false;
    FString TexturePath;
};

// =========================================================
// MaterialDefinitionDatabase — protocol definition store
// =========================================================

class MaterialDefinitionDatabase
{
public:
    void RegisterDefinition(const LiveSyncBridge::MaterialCreateView& View);
    void UpdateDefinition(const LiveSyncBridge::MaterialUpdateView& View);

    const MaterialDefinition* Find(const FGuid& Id) const;
    int32 Num() const { return Definitions.Num(); }

private:
    TMap<FGuid, MaterialDefinition> Definitions;
};
