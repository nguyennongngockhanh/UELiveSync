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

    // Removes the cached instance for Id (if any) and returns the
    // previously cached pointer (nullptr if not cached). Does NOT
    // build a new instance; callers re-Resolve after Invalidate.
    UMaterialInterface* Invalidate(const FGuid& Id);

private:
    UMaterialInterface* BuildMaterial(const MaterialDefinition& Def);

    MaterialDefinitionDatabase& Database;
    MaterialFactory Factory;
    TMap<FGuid, TObjectPtr<UMaterialInterface>> Cache;
};
