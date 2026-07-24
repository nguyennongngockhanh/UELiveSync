/**
 * Standalone tests for MaterialRegistry logic.
 *
 * Self-contained: reimplements MaterialDefinitionDatabase,
 * MaterialRegistry, and mock UMaterialInterface inline.
 *
 * Build: g++ -std=c++20 -O2 -o test_material_registry test_material_registry.cpp
 * Run:   ./test_material_registry
 */

#include <cstdint>
#include <cstring>
#include <cstdio>
#include <cmath>
#include <string>
#include <vector>
#include <unordered_map>
#include <functional>
#include <memory>

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

using int32 = int32_t;

// =========================================================
// Mock UMaterialInterface
// =========================================================

static int mock_material_count = 0;

struct MockMaterial
{
    int Id;
    std::string Name;
    FLinearColor BaseColor;
    float Metallic;
    float Roughness;
};

static MockMaterial* make_mock_material(
    const std::string& Name,
    FLinearColor BC, float M, float R)
{
    auto* m = new MockMaterial{++mock_material_count, Name, BC, M, R};
    return m;
}

// =========================================================
// MaterialDefinition (same as MaterialDefinitionDatabase.h)
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
// MaterialDefinitionDatabase (same as .h)
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
// MaterialRegistry (same logic as .h/.cpp)
// =========================================================

class MaterialRegistry
{
public:
    using MaterialFactory = std::function<MockMaterial*(
        const MaterialDefinition&)>;

    explicit MaterialRegistry(
        MaterialDefinitionDatabase& InDatabase,
        MaterialFactory InFactory)
        : Database(InDatabase)
        , Factory(std::move(InFactory))
    {
    }

    MockMaterial* Resolve(const FGuid& Id)
    {
        auto it = Cache.find(Id);
        if (it != Cache.end())
        {
            return it->second;
        }

        const MaterialDefinition* Def = Database.Find(Id);
        if (!Def)
        {
            return nullptr;
        }

        MockMaterial* Material = BuildMaterial(*Def);
        if (Material)
        {
            Cache[Id] = Material;
        }
        return Material;
    }

    int32_t CacheSize() const { return (int32_t)Cache.size(); }

private:
    MockMaterial* BuildMaterial(const MaterialDefinition& Def)
    {
        if (!Factory) return nullptr;
        return Factory(Def);
    }

    MaterialDefinitionDatabase& Database;
    MaterialFactory Factory;
    std::unordered_map<FGuid, MockMaterial*, FGuidHash> Cache;
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

// Cleanup tracking
static std::vector<MockMaterial*> all_materials;
static void cleanup()
{
    for (auto* m : all_materials) delete m;
    all_materials.clear();
}

// =========================================================
// Tests
// =========================================================

static void test_cache_miss()
{
    printf("\n--- test_cache_miss ---\n");
    MaterialDefinitionDatabase db;
    FGuid id = hex_to_guid("0102030405060708090a0b0c0d0e0f10");

    db.RegisterDefinition(id, "Wood", {0.6f, 0.4f, 0.2f}, 0.0f, 0.8f,
        FLinearColor::Black, false, "");

    int factory_calls = 0;
    MaterialRegistry reg(db, [&](const MaterialDefinition& Def)
        -> MockMaterial*
    {
        ++factory_calls;
        auto* m = make_mock_material(
            Def.Name.Data, Def.BaseColor, Def.Metallic, Def.Roughness);
        all_materials.push_back(m);
        return m;
    });

    MockMaterial* mat = reg.Resolve(id);
    check(mat != nullptr, "Resolve returns non-null on cache miss");
    check(factory_calls == 1, "Factory called once");
    check(reg.CacheSize() == 1, "Cache size == 1 after resolve");
    check_float(mat->BaseColor.R, 0.6f, "Material has correct BaseColor.R");
    check_float(mat->Roughness, 0.8f, "Material has correct Roughness");
}

static void test_cache_hit()
{
    printf("\n--- test_cache_hit ---\n");
    MaterialDefinitionDatabase db;
    FGuid id = hex_to_guid("AABBCCDD00112233AABBCCDD00112233");

    db.RegisterDefinition(id, "Metal", {0.9f, 0.9f, 0.9f}, 1.0f, 0.1f,
        FLinearColor::Black, false, "");

    int factory_calls = 0;
    MaterialRegistry reg(db, [&](const MaterialDefinition& Def)
        -> MockMaterial*
    {
        ++factory_calls;
        auto* m = make_mock_material(
            Def.Name.Data, Def.BaseColor, Def.Metallic, Def.Roughness);
        all_materials.push_back(m);
        return m;
    });

    MockMaterial* first = reg.Resolve(id);
    MockMaterial* second = reg.Resolve(id);

    check(first == second, "Cache hit returns same instance");
    check(factory_calls == 1, "Factory called only once");
    check(reg.CacheSize() == 1, "Cache size still 1");
}

static void test_unknown_uuid()
{
    printf("\n--- test_unknown_uuid ---\n");
    MaterialDefinitionDatabase db;

    MaterialRegistry reg(db, [](const MaterialDefinition&) -> MockMaterial*
    {
        return make_mock_material("should_not_exist",
            FLinearColor::White, 0, 0);
    });

    FGuid unknown;
    memset(unknown.Bytes, 0xFF, 16);

    MockMaterial* mat = reg.Resolve(unknown);
    check(mat == nullptr, "Resolve unknown UUID returns nullptr");
    check(reg.CacheSize() == 0, "Cache empty for unknown UUID");
}

static void test_build_failure_no_cache()
{
    printf("\n--- test_build_failure_no_cache ---\n");
    MaterialDefinitionDatabase db;
    FGuid id = hex_to_guid("11111111222222223333333344444444");

    db.RegisterDefinition(id, "Broken", {0.5f, 0.5f, 0.5f}, 0.0f, 0.5f,
        FLinearColor::Black, false, "");

    int factory_calls = 0;
    MaterialRegistry reg(db, [&](const MaterialDefinition&) -> MockMaterial*
    {
        ++factory_calls;
        return nullptr;  // simulate build failure
    });

    MockMaterial* mat = reg.Resolve(id);
    check(mat == nullptr, "Resolve returns nullptr on build failure");
    check(factory_calls == 1, "Factory called once");
    check(reg.CacheSize() == 0, "nullptr NOT cached");

    // Try again — should reattempt
    mat = reg.Resolve(id);
    check(mat == nullptr, "Second resolve also returns nullptr");
    check(factory_calls == 2, "Factory called again (no cache)");
}

static void test_repeated_resolve_same_instance()
{
    printf("\n--- test_repeated_resolve_same_instance ---\n");
    MaterialDefinitionDatabase db;
    FGuid id1 = hex_to_guid("AAAAAAAA00000000BBBBBBBB00000000");
    FGuid id2 = hex_to_guid("CCCCCCCC00000000DDDDDDDD00000000");

    db.RegisterDefinition(id1, "Mat1", {1,0,0}, 0, 0.5f,
        FLinearColor::Black, false, "");
    db.RegisterDefinition(id2, "Mat2", {0,1,0}, 0.5f, 0.5f,
        FLinearColor::Black, false, "");

    MaterialRegistry reg(db, [](const MaterialDefinition& Def)
        -> MockMaterial*
    {
        auto* m = make_mock_material(
            Def.Name.Data, Def.BaseColor, Def.Metallic, Def.Roughness);
        all_materials.push_back(m);
        return m;
    });

    MockMaterial* a1 = reg.Resolve(id1);
    MockMaterial* a2 = reg.Resolve(id2);
    MockMaterial* a3 = reg.Resolve(id1);
    MockMaterial* a4 = reg.Resolve(id2);

    check(a1 == a3, "Repeated resolve of id1 returns same instance");
    check(a2 == a4, "Repeated resolve of id2 returns same instance");
    check(a1 != a2, "Different IDs return different instances");
    check(reg.CacheSize() == 2, "Cache size == 2 for 2 IDs");
}

// =========================================================
// Main
// =========================================================

int main()
{
    printf("=== MaterialRegistry Tests ===\n");

    test_cache_miss();
    test_cache_hit();
    test_unknown_uuid();
    test_build_failure_no_cache();
    test_repeated_resolve_same_instance();

    cleanup();

    printf("\n=== Results: %d passed, %d failed ===\n", passed, failed);
    return failed > 0 ? 1 : 0;
}
