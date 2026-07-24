#pragma once

// =========================================================
// MaterialRegistry.h — Runtime material cache/resolver
// =========================================================
// Lazy resolver: Resolve(UUID) → UMaterialInterface*
//
// Reads definition from MaterialDefinitionDatabase.
// Caches built UMaterialInterface* on first successful build.
// Does NOT store definitions, does NOT call SetMaterial,
// does NOT know about actors or MaterialMetadata.
//
// Ownership: UMaterialInterface* owned by UE GC, not by Registry.
// =========================================================

#include "CoreMinimal.h"
#include "MaterialDefinitionDatabase.h"

class MaterialRegistry
{
public:
    using MaterialFactory = TFunction<UMaterialInterface*(
        const MaterialDefinition&)>;

    explicit MaterialRegistry(
        MaterialDefinitionDatabase& InDatabase,
        MaterialFactory InFactory);

    UMaterialInterface* Resolve(const FGuid& Id);

    // TODO Phase 2: Invalidate(UUID), Clear(), OnAssetReload()

private:
    UMaterialInterface* BuildMaterial(const MaterialDefinition& Def);

    MaterialDefinitionDatabase& Database;
    MaterialFactory Factory;
    TMap<FGuid, TObjectPtr<UMaterialInterface>> Cache;
};
