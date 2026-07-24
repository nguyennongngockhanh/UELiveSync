/**
 * Standalone tests for MaterialDefinitionDatabase logic.
 *
 * Self-contained: reimplements MaterialDefinition and
 * MaterialDefinitionDatabase inline, no UE includes needed.
 *
 * Build: g++ -std=c++20 -O2 -o test_material_db test_material_db.cpp
 * Run:   ./test_material_db
 */

#include <cstdint>
#include <cstring>
#include <cstdio>
#include <cmath>
#include <string>
#include <vector>
#include <unordered_map>

// =========================================================
// Minimal type stubs
// =========================================================

struct FGuid
{
    uint8_t Bytes[16] = {};
    bool IsValid() const { for (auto b : Bytes) if (b) return true; return false; }
    bool operator==(const FGuid& O) const { return memcmp(Bytes, O.Bytes, 16) == 0; }
};

struct FGuidHash
{
    size_t operator()(const FGuid& G) const
    {
        size_t h = 0;
        for (int i = 0; i < 16; i++) h = h * 31 + G.Bytes[i];
        return h;
    }
};

struct FLinearColor
{
    float R = 0, G = 0, B = 0, A = 1;
    FLinearColor() = default;
    FLinearColor(float r, float g, float b) : R(r), G(g), B(b), A(1) {}
    static FLinearColor White;
    static FLinearColor Black;
};
FLinearColor FLinearColor::White{1,1,1};
FLinearColor FLinearColor::Black{0,0,0};

struct FString
{
    std::string Data;
    FString() = default;
    FString(const char* S) : Data(S ? S : "") {}
    bool IsEmpty() const { return Data.empty(); }
};

// =========================================================
// MaterialDefinition (runtime model, same as header)
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
// MaterialDefinitionDatabase (same logic as header)
// =========================================================

class MaterialDefinitionDatabase
{
public:
    void RegisterDefinition(
        const FGuid& Id,
        const FString& Name,
        const FLinearColor& BaseColor,
        float Metallic,
        float Roughness,
        const FLinearColor& Emission,
        bool bHasTexturePath,
        const FString& TexturePath)
    {
        MaterialDefinition& Def = Definitions[Id];
        Def.Id = Id;
        Def.Name = Name;
        Def.BaseColor = BaseColor;
        Def.Metallic = Metallic;
        Def.Roughness = Roughness;
        Def.Emission = Emission;
        Def.bHasTexturePath = bHasTexturePath;
        Def.TexturePath = TexturePath;
    }

    void UpdateDefinition(
        const FGuid& Id,
        const FLinearColor& BaseColor,
        float Metallic,
        float Roughness,
        const FLinearColor& Emission,
        bool bHasTexturePath,
        const FString& TexturePath)
    {
        auto it = Definitions.find(Id);
        if (it == Definitions.end())
        {
            // UPDATE before CREATE — create placeholder
            MaterialDefinition& Def = Definitions[Id];
            Def.Id = Id;
            Def.BaseColor = BaseColor;
            Def.Metallic = Metallic;
            Def.Roughness = Roughness;
            Def.Emission = Emission;
            Def.bHasTexturePath = bHasTexturePath;
            Def.TexturePath = TexturePath;
        }
        else
        {
            MaterialDefinition& Def = it->second;
            Def.BaseColor = BaseColor;
            Def.Metallic = Metallic;
            Def.Roughness = Roughness;
            Def.Emission = Emission;
            Def.bHasTexturePath = bHasTexturePath;
            Def.TexturePath = TexturePath;
        }
    }

    const MaterialDefinition* Find(const FGuid& Id) const
    {
        auto it = Definitions.find(Id);
        return it != Definitions.end() ? &it->second : nullptr;
    }

    int32_t Num() const { return (int32_t)Definitions.size(); }

private:
    std::unordered_map<FGuid, MaterialDefinition, FGuidHash> Definitions;
};

// =========================================================
// Test framework
// =========================================================

static int passed = 0;
static int failed = 0;

static void check(bool cond, const char* name)
{
    if (cond) { printf("  PASS  %s\n", name); ++passed; }
    else      { printf("  FAIL  %s\n", name); ++failed; }
}

static void check_float(float got, float expected, const char* name)
{
    if (fabsf(got - expected) < 0.001f) { printf("  PASS  %s\n", name); ++passed; }
    else { printf("  FAIL  %s (got %.4f, expected %.4f)\n", name, got, expected); ++failed; }
}

// =========================================================
// Helper: build FGuid from hex string
// =========================================================

static FGuid hex_to_guid(const char* hex)
{
    FGuid g;
    for (int i = 0; i < 16; i++)
    {
        unsigned byte;
        sscanf(hex + i * 2, "%2x", &byte);
        g.Bytes[i] = static_cast<uint8_t>(byte);
    }
    return g;
}

// =========================================================
// Tests
// =========================================================

static void test_register_definition()
{
    printf("\n--- test_register_definition ---\n");
    MaterialDefinitionDatabase db;
    FGuid id = hex_to_guid("0102030405060708090a0b0c0d0e0f10");

    db.RegisterDefinition(id, "WoodFloor",
        {0.6f, 0.4f, 0.2f}, 0.0f, 0.8f, FLinearColor::Black,
        true, "/Game/Textures/Wood");

    check(db.Num() == 1, "Num() == 1 after register");

    const MaterialDefinition* def = db.Find(id);
    check(def != nullptr, "Find() returns non-null");
    check(def->Name.Data == "WoodFloor", "Name matches");
    check_float(def->BaseColor.R, 0.6f, "BaseColor.R");
    check_float(def->BaseColor.G, 0.4f, "BaseColor.G");
    check_float(def->BaseColor.B, 0.2f, "BaseColor.B");
    check_float(def->Metallic, 0.0f, "Metallic");
    check_float(def->Roughness, 0.8f, "Roughness");
    check(def->bHasTexturePath == true, "HasTexturePath true");
    check(def->TexturePath.Data == "/Game/Textures/Wood", "TexturePath matches");
}

static void test_update_definition()
{
    printf("\n--- test_update_definition ---\n");
    MaterialDefinitionDatabase db;
    FGuid id = hex_to_guid("AABBCCDD00112233AABBCCDD00112233");

    db.RegisterDefinition(id, "MetalPlate",
        {0.9f, 0.9f, 0.9f}, 1.0f, 0.1f, FLinearColor::Black,
        false, "");

    db.UpdateDefinition(id, {0.5f, 0.5f, 0.5f}, 0.8f, 0.3f,
        FLinearColor::Black, false, "");

    const MaterialDefinition* def = db.Find(id);
    check(def != nullptr, "Find() after update");
    check_float(def->BaseColor.R, 0.5f, "Updated BaseColor.R");
    check_float(def->BaseColor.G, 0.5f, "Updated BaseColor.G");
    check_float(def->BaseColor.B, 0.5f, "Updated BaseColor.B");
    check_float(def->Metallic, 0.8f, "Updated Metallic");
    check_float(def->Roughness, 0.3f, "Updated Roughness");
}

static void test_create_then_update()
{
    printf("\n--- test_create_then_update ---\n");
    MaterialDefinitionDatabase db;
    FGuid id = hex_to_guid("11111111222222223333333344444444");

    db.RegisterDefinition(id, "Grass",
        {0.1f, 0.8f, 0.1f}, 0.0f, 0.9f, FLinearColor::Black,
        false, "");
    check(db.Num() == 1, "Num==1 after create");

    db.UpdateDefinition(id, {0.2f, 0.7f, 0.2f}, 0.0f, 0.7f,
        FLinearColor::Black, false, "");
    check(db.Num() == 1, "Num==1 still after update");

    const MaterialDefinition* def = db.Find(id);
    check(def != nullptr, "Find after create+update");
    check(def->Name.Data == "Grass", "Name preserved after update");
    check_float(def->BaseColor.R, 0.2f, "Updated R");
    check_float(def->Roughness, 0.7f, "Updated roughness");
}

static void test_update_before_create()
{
    printf("\n--- test_update_before_create ---\n");
    MaterialDefinitionDatabase db;
    FGuid id = hex_to_guid("DEADBEEF000000001111111122222222");

    // UPDATE arrives before CREATE
    db.UpdateDefinition(id, {0.3f, 0.3f, 0.3f}, 0.5f, 0.5f,
        FLinearColor::Black, true, "/Game/Textures/Fallback");

    check(db.Num() == 1, "Num==1 after update-before-create");

    const MaterialDefinition* def = db.Find(id);
    check(def != nullptr, "Find placeholder after update-before-create");
    check_float(def->BaseColor.R, 0.3f, "Placeholder has updated R");
    check_float(def->Metallic, 0.5f, "Placeholder has updated Metallic");
    check(def->Name.IsEmpty(), "Placeholder name is empty");
    check(def->bHasTexturePath == true, "Placeholder has texture path");

    // CREATE arrives — fills in the name
    db.RegisterDefinition(id, "FallbackMat",
        {0.8f, 0.8f, 0.8f}, 0.0f, 0.5f, FLinearColor::Black,
        false, "");

    check(db.Num() == 1, "Num==1 after create completes placeholder");
    def = db.Find(id);
    check(def != nullptr, "Find after placeholder filled");
    check(def->Name.Data == "FallbackMat", "Name filled by CREATE");
    check_float(def->BaseColor.R, 0.8f, "CREATE overwrote placeholder R");
    check_float(def->Metallic, 0.0f, "CREATE overwrote placeholder metallic");
}

static void test_find_unknown_uuid()
{
    printf("\n--- test_find_unknown_uuid ---\n");
    MaterialDefinitionDatabase db;

    FGuid unknown;
    memset(unknown.Bytes, 0xFF, 16);

    const MaterialDefinition* def = db.Find(unknown);
    check(def == nullptr, "Find unknown UUID returns nullptr");
    check(db.Num() == 0, "Num==0 for empty database");
}

static void test_multiple_definitions()
{
    printf("\n--- test_multiple_definitions ---\n");
    MaterialDefinitionDatabase db;

    FGuid id1 = hex_to_guid("00000000000000000000000000000001");
    FGuid id2 = hex_to_guid("00000000000000000000000000000002");
    FGuid id3 = hex_to_guid("00000000000000000000000000000003");

    db.RegisterDefinition(id1, "Mat1", {1,0,0}, 0, 0.5f,
        FLinearColor::Black, false, "");
    db.RegisterDefinition(id2, "Mat2", {0,1,0}, 0.5f, 0.5f,
        FLinearColor::Black, false, "");
    db.RegisterDefinition(id3, "Mat3", {0,0,1}, 1.0f, 0.5f,
        FLinearColor::Black, false, "");

    check(db.Num() == 3, "Num==3 after 3 registers");

    const MaterialDefinition* d1 = db.Find(id1);
    const MaterialDefinition* d2 = db.Find(id2);
    const MaterialDefinition* d3 = db.Find(id3);

    check(d1 != nullptr && d1->Name.Data == "Mat1", "Mat1 found");
    check(d2 != nullptr && d2->Name.Data == "Mat2", "Mat2 found");
    check(d3 != nullptr && d3->Name.Data == "Mat3", "Mat3 found");

    check_float(d1->BaseColor.R, 1.0f, "Mat1 is red");
    check_float(d2->BaseColor.G, 1.0f, "Mat2 is green");
    check_float(d3->BaseColor.B, 1.0f, "Mat3 is blue");
}

// =========================================================
// Main
// =========================================================

int main()
{
    printf("=== MaterialDefinitionDatabase Tests ===\n");

    test_register_definition();
    test_update_definition();
    test_create_then_update();
    test_update_before_create();
    test_find_unknown_uuid();
    test_multiple_definitions();

    printf("\n=== Results: %d passed, %d failed ===\n", passed, failed);
    return failed > 0 ? 1 : 0;
}
