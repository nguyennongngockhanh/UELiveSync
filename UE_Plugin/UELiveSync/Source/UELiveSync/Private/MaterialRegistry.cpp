#include "MaterialRegistry.h"

MaterialRegistry::MaterialRegistry(
    MaterialDefinitionDatabase& InDatabase,
    MaterialFactory InFactory)
    : Database(InDatabase)
    , Factory(MoveTemp(InFactory))
{
}

UMaterialInterface* MaterialRegistry::Resolve(const FGuid& Id)
{
    if (auto* Existing = Cache.Find(Id))
    {
        return Existing->Get();
    }

    const MaterialDefinition* Def = Database.Find(Id);
    if (!Def)
    {
        return nullptr;
    }

    UMaterialInterface* Material = BuildMaterial(*Def);
    if (Material)
    {
        Cache.Add(Id, Material);
    }
    return Material;
}

UMaterialInterface* MaterialRegistry::BuildMaterial(
    const MaterialDefinition& Def)
{
    if (!Factory)
    {
        return nullptr;
    }
    return Factory(Def);
}

UMaterialInterface* MaterialRegistry::Invalidate(const FGuid& Id)
{
    TObjectPtr<UMaterialInterface>* Existing = Cache.Find(Id);
    if (!Existing)
    {
        return nullptr;
    }
    UMaterialInterface* Result = *Existing;
    Cache.Remove(Id);
    return Result;
}
