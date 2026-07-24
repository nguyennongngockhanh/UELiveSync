#include "MaterialDefinitionDatabase.h"

void MaterialDefinitionDatabase::RegisterDefinition(
    const LiveSyncBridge::MaterialCreateView& View)
{
    FGuid Id;
    FMemory::Memcpy(&Id, View.MaterialId.data(), 16);

    MaterialDefinition& Def = Definitions.FindOrAdd(Id);
    Def.Id = Id;
    Def.Name = UTF8_TO_TCHAR(View.Name.c_str());
    Def.BaseColor = (View.BaseColor.size() >= 3)
        ? FLinearColor(View.BaseColor[0], View.BaseColor[1], View.BaseColor[2])
        : FLinearColor::White;
    Def.Metallic = View.Metallic;
    Def.Roughness = View.Roughness;
    Def.Emission = (View.Emission.size() >= 3)
        ? FLinearColor(View.Emission[0], View.Emission[1], View.Emission[2])
        : FLinearColor::Black;
    Def.bHasTexturePath = View.HasTexturePath;
    Def.TexturePath = View.HasTexturePath
        ? UTF8_TO_TCHAR(View.TexturePath.c_str())
        : FString();
}

void MaterialDefinitionDatabase::UpdateDefinition(
    const LiveSyncBridge::MaterialUpdateView& View)
{
    FGuid Id;
    FMemory::Memcpy(&Id, View.MaterialId.data(), 16);

    MaterialDefinition* Def = Definitions.Find(Id);
    if (!Def)
    {
        // UPDATE before CREATE — create placeholder
        Def = &Definitions.Add(Id);
        Def->Id = Id;
    }

    if (View.BaseColor.size() >= 3)
    {
        Def->BaseColor = FLinearColor(
            View.BaseColor[0], View.BaseColor[1], View.BaseColor[2]);
    }
    Def->Metallic = View.Metallic;
    Def->Roughness = View.Roughness;
    if (View.Emission.size() >= 3)
    {
        Def->Emission = FLinearColor(
            View.Emission[0], View.Emission[1], View.Emission[2]);
    }
    Def->bHasTexturePath = View.HasTexturePath;
    Def->TexturePath = View.HasTexturePath
        ? UTF8_TO_TCHAR(View.TexturePath.c_str())
        : FString();
}

const MaterialDefinition* MaterialDefinitionDatabase::Find(
    const FGuid& Id) const
{
    return Definitions.Find(Id);
}
