# Phase 10K.6 — Implementation Plan (v2)

All corrections applied: honest request_parse, RAII for every scope, EFbxPhaseKind constructor,
single registry, local-duration + `+=` accumulator, no coverage clamping.

---

## Pre-Implementation Invariants

Before and after every slice:
- [ ] Lexical braces balanced (use `python3 tests/brace_balance.py` or manual scan ignoring comments/strings)
- [ ] `git diff --check` clean
- [ ] Persistent snapshot saved to `.recovery/`
- [ ] Persistent patch saved to `.recovery/`

---

## Slice 0: Infrastructure (globals, no HandleImport edit)

**Insert at**: After closing comment block `// =========================================================` (~line 34).

**Step 0a — GPhaseMetadata registry**:
```cpp
static const TMap<FString, FFbxPhaseMetadata> GPhaseMetadata = {
    {TEXT("request_parse"),                     {EFbxPhaseKind::Exclusive,     nullptr}},
    {TEXT("path_validation"),                   {EFbxPhaseKind::Exclusive,     nullptr}},
    {TEXT("semantic_signature"),                {EFbxPhaseKind::Nested,        nullptr}},
    {TEXT("fbx_factory_import"),                {EFbxPhaseKind::Exclusive,     nullptr}},
    {TEXT("imported_asset_discovery"),          {EFbxPhaseKind::Exclusive,     nullptr}},
    {TEXT("sidecar_processing"),                {EFbxPhaseKind::Exclusive,     nullptr}},
    {TEXT("sidecar_manifest_read"),             {EFbxPhaseKind::Nested,        TEXT("sidecar_processing")}},
    {TEXT("sidecar_fingerprint_classification"),{EFbxPhaseKind::Nested,        TEXT("sidecar_processing")}},
    {TEXT("sidecar_asset_lookup"),              {EFbxPhaseKind::Nested,        TEXT("sidecar_processing")}},
    {TEXT("sidecar_batch_import"),              {EFbxPhaseKind::Nested,        TEXT("sidecar_processing")}},
    {TEXT("sidecar_result_mapping"),            {EFbxPhaseKind::Nested,        TEXT("sidecar_processing")}},
    {TEXT("static_mesh_post_import"),           {EFbxPhaseKind::Exclusive,     nullptr}},
    {TEXT("actor_lookup_or_spawn"),             {EFbxPhaseKind::Exclusive,     nullptr}},
    {TEXT("static_mesh_assignment"),            {EFbxPhaseKind::Exclusive,     nullptr}},
    {TEXT("material_slot_assignment"),          {EFbxPhaseKind::Exclusive,     nullptr}},
    {TEXT("post_import_finalize"),              {EFbxPhaseKind::Nested,        TEXT("fbx_transaction")}},
};
```

**Step 0b — Derived GExclusivePhases**:
```cpp
static TSet<FString> BuildExclusivePhaseSet()
{
    TSet<FString> Result;
    for (const auto& KV : GPhaseMetadata)
        if (KV.Value.Kind == EFbxPhaseKind::Exclusive)
            Result.Add(KV.Key);
    return Result;
}
static const TSet<FString> GExclusivePhases = BuildExclusivePhaseSet();
```

**Step 0c — IsExclusivePhase**:
```cpp
static bool IsExclusivePhase(const FString& PhaseName)
{
    const FFbxPhaseMetadata* Meta = GPhaseMetadata.Find(PhaseName);
    return Meta && Meta->Kind == EFbxPhaseKind::Exclusive;
}
```

**Step 0d — ComputePhaseClassificationExclusive**:
```cpp
static FString ComputePhaseClassificationExclusive(const TMap<FString, double>& PhaseDurations)
{
    FString LargestPhase, SecondPhase;
    double LargestMs = 0.0, SecondMs = 0.0;
    for (const auto& KV : PhaseDurations)
    {
        if (!IsExclusivePhase(KV.Key)) continue;
        if (KV.Value > LargestMs)
        {
            SecondMs = LargestMs; SecondPhase = LargestPhase;
            LargestMs = KV.Value; LargestPhase = KV.Key;
        }
        else if (KV.Value > SecondMs)
        {
            SecondMs = KV.Value; SecondPhase = KV.Key;
        }
    }
    if (LargestMs <= 0.0) return TEXT("UNRESOLVED");
    if (SecondMs >= 0.8 * LargestMs) return TEXT("MIXED");
    return FString::Printf(TEXT("DOMINANT_%s"), *LargestPhase);
}
```

**Step 0e — Classification derivation helper**:
```cpp
static FString PhaseKindToClassification(EFbxPhaseKind Kind)
{
    switch (Kind)
    {
        case EFbxPhaseKind::Exclusive:       return TEXT("exclusive");
        case EFbxPhaseKind::Nested:          return TEXT("nested");
        case EFbxPhaseKind::InclusiveParent: return TEXT("inclusive_parent");
        case EFbxPhaseKind::Unobservable:    return TEXT("unobservable");
        default:                             return TEXT("unknown");
    }
}
```

**Step 0f — FFbxScopePhase**:
```cpp
struct FFbxScopePhase
{
    int32 TransactionId;
    FGuid Guid;
    int32 SyncId;
    FString ObjectName;
    FString PhaseName;
    EFbxPhaseKind Kind;
    double StartTime;
    double* DurationOut;

    FFbxScopePhase(
        int32 InTransactionId,
        const FGuid& InGuid,
        int32 InSyncId,
        const FString& InObjectName,
        const FString& InPhaseName,
        EFbxPhaseKind InKind,
        double* InDurationOut = nullptr)
        : TransactionId(InTransactionId)
        , Guid(InGuid)
        , SyncId(InSyncId)
        , ObjectName(InObjectName)
        , PhaseName(InPhaseName)
        , Kind(InKind)
        , StartTime(FPlatformTime::Seconds())
        , DurationOut(InDurationOut)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[FBX][PHASE_BEGIN] transactionId=%d guid=%s syncId=%d objectName=%s "
                 "phase=\"%s\" classification=\"%s\""),
            TransactionId, *Guid.ToString(EGuidFormats::Digits), SyncId,
            *ObjectName, *PhaseName, *PhaseKindToClassification(Kind));
    }

    ~FFbxScopePhase()
    {
        const double Ms = (FPlatformTime::Seconds() - StartTime) * 1000.0;
        UE_LOG(LogLiveSync, Log,
            TEXT("[FBX][PHASE_END] transactionId=%d guid=%s syncId=%d objectName=%s "
                 "phase=\"%s\" classification=\"%s\" durationMs=%.1f"),
            TransactionId, *Guid.ToString(EGuidFormats::Digits), SyncId,
            *ObjectName, *PhaseName, *PhaseKindToClassification(Kind), Ms);
        if (DurationOut)
            *DurationOut = Ms;
    }
};
```

**Step 0g — FbxPhaseBegin / FbxPhaseEnd helpers**:
```cpp
static void FbxPhaseBegin(
    int32 TransactionId, const FGuid& Guid, int32 SyncId,
    const FString& ObjectName, const FString& PhaseName, EFbxPhaseKind Kind)
{
    UE_LOG(LogLiveSync, Log,
        TEXT("[FBX][PHASE_BEGIN] transactionId=%d guid=%s syncId=%d objectName=%s "
             "phase=\"%s\" classification=\"%s\""),
        TransactionId, *Guid.ToString(EGuidFormats::Digits), SyncId,
        *ObjectName, *PhaseName, *PhaseKindToClassification(Kind));
}

static void FbxPhaseEnd(
    int32 TransactionId, const FGuid& Guid, int32 SyncId,
    const FString& ObjectName, const FString& PhaseName, EFbxPhaseKind Kind,
    double DurationMs)
{
    UE_LOG(LogLiveSync, Log,
        TEXT("[FBX][PHASE_END] transactionId=%d guid=%s syncId=%d objectName=%s "
             "phase=\"%s\" classification=\"%s\" durationMs=%.1f"),
        TransactionId, *Guid.ToString(EGuidFormats::Digits), SyncId,
        *ObjectName, *PhaseName, *PhaseKindToClassification(Kind), DurationMs);
}
```

**Slice 0 acceptance**:
- [ ] GPhaseMetadata, GExclusivePhases, IsExclusivePhase, ComputePhaseClassificationExclusive symbols exist
- [ ] PhaseKindToClassification maps all 4 enum values
- [ ] FFbxScopePhase constructor uses EFbxPhaseKind (not string classification)
- [ ] Single registry (no duplicate phase lists)
- [ ] No HandleImport edits
- [ ] `python3 tests/phase10k6_transaction_decomposition.py` passes
- [ ] Lexical braces balanced
- [ ] `git diff --check` clean
- [ ] Persistent slice-0 snapshot and patch saved

---

## Slice 1: TransactionId + Timer + PhaseDurations + TxnObjNameSanitized

**Insert** after `if (!Context.Stats) { return false; }`:
```cpp
const int32 TransactionId = Context.Stats->FBXTransactionId.fetch_add(1, std::memory_order_acq_rel);
const double HandleImportStart = FPlatformTime::Seconds();
TMap<FString, double> PhaseDurations;
```

**Insert** after `FString SafeName = SanitizeObjectName(...)`:
```cpp
const FString TxnObjNameSanitized = SafeName;
```

---

## Slice 2: request_parse (Exclusive)

**Pattern** (honest: measures payload validation + parsing + version check):
```cpp
const double RequestStart = FPlatformTime::Seconds();

// Lines 893–896: ValidatePayloadSize — early return OK (no timer, no marker)
// Lines 901–906: memcpy
// Lines 908–922: logging
// Lines 924–927: ValidateVersion — early return OK (no timer, no marker)

const double RequestMs = (FPlatformTime::Seconds() - RequestStart) * 1000.0;
PhaseDurations.FindOrAdd(TEXT("request_parse")) += RequestMs;
FbxPhaseBegin(TransactionId, Request.ObjectGUID, Request.SyncId, TxnObjNameSanitized,
    TEXT("request_parse"), EFbxPhaseKind::Exclusive);
FbxPhaseEnd(TransactionId, Request.ObjectGUID, Request.SyncId, TxnObjNameSanitized,
    TEXT("request_parse"), EFbxPhaseKind::Exclusive, RequestMs);
```

**Key**: This goes AFTER the version validation completes (after the closing `}` of the version-validation-if-block). Identity is available from the parsed Request struct.

---

## Slice 3: path_validation (Exclusive)

```cpp
double PathMs = 0.0;
{
    FFbxScopePhase Phase(TransactionId, Request.ObjectGUID, Request.SyncId,
        TxnObjNameSanitized, TEXT("path_validation"), EFbxPhaseKind::Exclusive,
        &PathMs);
    if (!ValidatePathSecurity(FbxPathStr, *Context.Stats))
    {
        return false;  // destructor fires, PathMs written
    }
    // Existing lines 935–956 unchanged
    FString GuidShort = ...; FString AssetBasePath = ...; ...
}
PhaseDurations.FindOrAdd(TEXT("path_validation")) += PathMs;
```

---

## Slice 4: semantic_signature (Nested)

```cpp
{
    FFbxScopePhase Phase(TransactionId, Request.ObjectGUID, Request.SyncId,
        TxnObjNameSanitized, TEXT("semantic_signature"), EFbxPhaseKind::Nested);
    // ALL existing lines 960–1062 unchanged, including:
    //   ComputeFBXSemanticSignature
    //   coalesce actor checks
    //   early return on signature match (line 1054)
    //   fallthrough cache update
    // Destructor fires on early return and on normal exit
}
```

---

## Slice 5: fbx_factory_import (Exclusive)

```cpp
double FbxFactoryMs = 0.0;
{
    FFbxScopePhase Phase(TransactionId, Request.ObjectGUID, Request.SyncId,
        TxnObjNameSanitized, TEXT("fbx_factory_import"), EFbxPhaseKind::Exclusive,
        &FbxFactoryMs);
    // Lines 1064–1109: FullPendingPath, ImportTask, Factory, ImportAssetTasks
}
PhaseDurations.FindOrAdd(TEXT("fbx_factory_import")) += FbxFactoryMs;
```

---

## Slice 6: imported_asset_discovery (Exclusive) — NARROW

```cpp
double ImportDiscMs = 0.0;
{
    FFbxScopePhase Phase(TransactionId, Request.ObjectGUID, Request.SyncId,
        TxnObjNameSanitized, TEXT("imported_asset_discovery"), EFbxPhaseKind::Exclusive,
        &ImportDiscMs);
    // Lines 1111–1137 only: ImportedObjects loop + counting
}
PhaseDurations.FindOrAdd(TEXT("imported_asset_discovery")) += ImportDiscMs;
```

---

## Slice 7: sidecar_processing (Exclusive) + 5 nested sub-phases

```cpp
double SidecarMs = 0.0;
{
    FFbxScopePhase Phase(TransactionId, Request.ObjectGUID, Request.SyncId,
        TxnObjNameSanitized, TEXT("sidecar_processing"), EFbxPhaseKind::Exclusive,
        &SidecarMs);

    // === Existing sidecar code lines 1138–1775 ===
    // With nested RAII scopes inserted at each anchor:
}
PhaseDurations.FindOrAdd(TEXT("sidecar_processing")) += SidecarMs;
```

### 7a: sidecar_manifest_read (Nested)
```cpp
{
    FFbxScopePhase Phase(TransactionId, Request.ObjectGUID, Request.SyncId,
        TxnObjNameSanitized, TEXT("sidecar_manifest_read"), EFbxPhaseKind::Nested);
    // Existing manifest read block (lines 1153–1247)
}
```

### 7b: sidecar_fingerprint_classification (Nested)
```cpp
{
    FFbxScopePhase Phase(TransactionId, Request.ObjectGUID, Request.SyncId,
        TxnObjNameSanitized, TEXT("sidecar_fingerprint_classification"), EFbxPhaseKind::Nested);
    // Existing fingerprint + scan code (lines 1259–1446)
}
```

### 7c: sidecar_asset_lookup (Nested)
```cpp
{
    FFbxScopePhase Phase(TransactionId, Request.ObjectGUID, Request.SyncId,
        TxnObjNameSanitized, TEXT("sidecar_asset_lookup"), EFbxPhaseKind::Nested);
    // Existing per-file asset lookup loop (lines 1523–1593)
}
```

### 7d: sidecar_batch_import (Nested)
```cpp
{
    FFbxScopePhase Phase(TransactionId, Request.ObjectGUID, Request.SyncId,
        TxnObjNameSanitized, TEXT("sidecar_batch_import"), EFbxPhaseKind::Nested);
    // Existing batch import block (lines 1598–1669)
}
```

### 7e: sidecar_result_mapping (Nested)
```cpp
{
    FFbxScopePhase Phase(TransactionId, Request.ObjectGUID, Request.SyncId,
        TxnObjNameSanitized, TEXT("sidecar_result_mapping"), EFbxPhaseKind::Nested);
    // Existing result mapping + OnActiveSidecarMapReady (lines 1677–1768)
}
```

---

## Slice 8: static_mesh_post_import (Exclusive)

```cpp
double MeshPostMs = 0.0;
{
    FFbxScopePhase Phase(TransactionId, Request.ObjectGUID, Request.SyncId,
        TxnObjNameSanitized, TEXT("static_mesh_post_import"), EFbxPhaseKind::Exclusive,
        &MeshPostMs);
    // Lines 1777–1935: PendingAsset resolution, bounds validation, cache update
    // Early returns at 1796, 1807, 1913 handled by destructor
}
PhaseDurations.FindOrAdd(TEXT("static_mesh_post_import")) += MeshPostMs;
```

---

## Slice 9: actor_lookup_or_spawn (Exclusive)

```cpp
double ActorLookupMs = 0.0;
{
    FFbxScopePhase Phase(TransactionId, Request.ObjectGUID, Request.SyncId,
        TxnObjNameSanitized, TEXT("actor_lookup_or_spawn"), EFbxPhaseKind::Exclusive,
        &ActorLookupMs);
    // Lines 1937–1955: transform save + FindActor
}
PhaseDurations.FindOrAdd(TEXT("actor_lookup_or_spawn")) += ActorLookupMs;
```

---

## Slice 10: static_mesh_assignment + material_slot_assignment (Exclusive, Sequential)

Both phases use small RAII scopes inside the update branch and spawn branch.

### In update branch (after line 2009 `SMC->SetStaticMesh(StaticMesh);`):

```cpp
// === static_mesh_assignment ===
{
    double LocalAssignMs = 0.0;
    {
        FFbxScopePhase Phase(TransactionId, Request.ObjectGUID, Request.SyncId,
            TxnObjNameSanitized, TEXT("static_mesh_assignment"), EFbxPhaseKind::Exclusive,
            &LocalAssignMs);
        SMC->SetStaticMesh(StaticMesh);                    // line 2009
    }
    PhaseDurations.FindOrAdd(TEXT("static_mesh_assignment")) += LocalAssignMs;
}

// === material_slot_assignment ===
{
    double LocalMatMs = 0.0;
    {
        FFbxScopePhase Phase(TransactionId, Request.ObjectGUID, Request.SyncId,
            TxnObjNameSanitized, TEXT("material_slot_assignment"), EFbxPhaseKind::Exclusive,
            &LocalMatMs);
        // Phase 10J.5B.2: restore non-null material overrides.  (lines 2011–2021)
        const int32 NumMatSlots = SMC->GetNumMaterials();
        for (int32 i = 0; i < FMath::Min(SavedOverrides.Num(), NumMatSlots); ++i)
            if (SavedOverrides[i]) SMC->SetMaterial(i, SavedOverrides[i]);
        RefreshFBXStaticMeshComponent(SMC, MeshActor);         // 2024
        EnsureFBXMeshRenderable(SMC, StaticMesh, MeshActor,
            Request.ObjectGUID);                                // 2026–2028
        if (Context.OnRestoreGeneratedMaterials)
            Context.OnRestoreGeneratedMaterials(Request.ObjectGUID, SMC);  // 2030–2033
        LogExtendedFBXValidate(MeshActor, SMC, Request.ObjectGUID);        // 2034
    }
    PhaseDurations.FindOrAdd(TEXT("material_slot_assignment")) += LocalMatMs;
}
```

### In spawn branch (after line 2195 `SMC->SetStaticMesh(StaticMesh);`):

```cpp
// === static_mesh_assignment ===
{
    double LocalAssignMs = 0.0;
    {
        FFbxScopePhase Phase(TransactionId, Request.ObjectGUID, Request.SyncId,
            TxnObjNameSanitized, TEXT("static_mesh_assignment"), EFbxPhaseKind::Exclusive,
            &LocalAssignMs);
        SMC->SetStaticMesh(StaticMesh);                  // line 2195
        RefreshFBXStaticMeshComponent(SMC, MeshActor);   // line 2198
    }
    PhaseDurations.FindOrAdd(TEXT("static_mesh_assignment")) += LocalAssignMs;
}

// === material_slot_assignment ===
{
    double LocalMatMs = 0.0;
    {
        FFbxScopePhase Phase(TransactionId, Request.ObjectGUID, Request.SyncId,
            TxnObjNameSanitized, TEXT("material_slot_assignment"), EFbxPhaseKind::Exclusive,
            &LocalMatMs);
        EnsureFBXMeshRenderable(SMC, StaticMesh, MeshActor,
            Request.ObjectGUID);                                         // 2200–2202
        if (Context.OnRestoreGeneratedMaterials)
            Context.OnRestoreGeneratedMaterials(Request.ObjectGUID, SMC); // 2204–2206
        LogExtendedFBXValidate(MeshActor, SMC, Request.ObjectGUID);      // 2208
        if (Context.OnScheduleRepair)
            Context.OnScheduleRepair(Request.ObjectGUID);                 // 2210–2212
    }
    PhaseDurations.FindOrAdd(TEXT("material_slot_assignment")) += LocalMatMs;
}
```

---

## Slice 11: post_import_finalize (Nested)

```cpp
{
    FFbxScopePhase Phase(TransactionId, Request.ObjectGUID, Request.SyncId,
        TxnObjNameSanitized, TEXT("post_import_finalize"), EFbxPhaseKind::Nested);
    // Existing lines 2266–2274: ActorToDestroy cleanup
}
```

---

## Slice 12: STALL_SUMMARY

```cpp
// Phase 10K.6: STALL_SUMMARY
{
    const double TotalMs = (FPlatformTime::Seconds() - HandleImportStart) * 1000.0;
    double MeasuredExclusiveMs = 0.0;
    FString LargestPhase, SecondLargestPhase;
    double LargestPhaseMs = 0.0, SecondLargestPhaseMs = 0.0;
    for (const auto& KV : PhaseDurations)
    {
        if (!IsExclusivePhase(KV.Key)) continue;
        MeasuredExclusiveMs += KV.Value;
        if (KV.Value > LargestPhaseMs)
        {
            SecondLargestPhaseMs = LargestPhaseMs;
            SecondLargestPhase = LargestPhase;
            LargestPhaseMs = KV.Value;
            LargestPhase = KV.Key;
        }
        else if (KV.Value > SecondLargestPhaseMs)
        {
            SecondLargestPhaseMs = KV.Value;
            SecondLargestPhase = KV.Key;
        }
    }

    const double RawCoveragePercent = (TotalMs > 0.0)
        ? (MeasuredExclusiveMs / TotalMs) * 100.0 : 0.0;
    const double ExclusiveExcessMs = FMath::Max(0.0, MeasuredExclusiveMs - TotalMs);
    const double UnattributedMs = FMath::Max(0.0, TotalMs - MeasuredExclusiveMs);

    FString TimingValidity = TEXT("VALID");
    FString Classification;
    if (ExclusiveExcessMs > 0.5)
    {
        TimingValidity = TEXT("INVALID_OVERLAP");
        Classification = TEXT("UNRESOLVED");
    }
    else
    {
        Classification = ComputePhaseClassificationExclusive(PhaseDurations);
    }

    UE_LOG(LogLiveSync, Log,
        TEXT("[FBX][STALL_SUMMARY] transactionId=%d guid=%s syncId=%d objectName=%s "
             "totalMs=%.1f measuredExclusiveMs=%.1f coveragePercent=%.2f "
             "exclusiveExcessMs=%.1f timingValidity=%s "
             "largestPhase=%s largestPhaseMs=%.1f "
             "secondLargestPhase=%s secondLargestPhaseMs=%.1f "
             "unattributedMs=%.1f classification=%s"),
        TransactionId, *Request.ObjectGUID.ToString(EGuidFormats::Digits),
        Request.SyncId, *TxnObjNameSanitized,
        TotalMs, MeasuredExclusiveMs, RawCoveragePercent,
        ExclusiveExcessMs, *TimingValidity,
        *LargestPhase, LargestPhaseMs,
        *SecondLargestPhase, SecondLargestPhaseMs,
        UnattributedMs, *Classification);
}
```

**Insert before**: `#else` (line 2275).

---

## Post-All-Slices Verification

- [ ] `python3 tests/phase10k6_transaction_decomposition.py` passes
- [ ] Lexical braces balanced
- [ ] `git diff --check` clean
- [ ] No `FMath::Min(100.0, ` coverage clamping
- [ ] No overlapping exclusive phase intervals (visual trace through each branch)
- [ ] All 16 phases have exactly one PHASE_BEGIN and one PHASE_END
- [ ] Persistent final snapshot and patch saved

---

## Slice Order Summary

| Step | What | Lines affected | Early return safe |
|---|---|---|---|
| 0 | Infrastructure (globals) | After line 34 | N/A |
| 1 | TransactionId + timer | After stats guard | N/A |
| 2 | request_parse (manual timer + deferred marker) | After version validation | Yes (no marker on fail) |
| 3 | path_validation (RAII) | 932–956 | Yes (destructor) |
| 4 | semantic_signature (RAII) | 960–1062 | Yes (destructor) |
| 5 | fbx_factory_import (RAII) | 1064–1109 | Yes (destructor) |
| 6 | imported_asset_discovery (RAII) | 1111–1137 | Yes (destructor) |
| 7 | sidecar_processing (RAII) + 5 nests | 1138–1775 | Yes (destructor) |
| 8 | static_mesh_post_import (RAII) | 1777–1935 | Yes (destructor) |
| 9 | actor_lookup_or_spawn (RAII) | 1937–1955 | Yes (destructor) |
| 10 | static_mesh_assignment (RAII, 2 branches) | 2009 + 2195 | No early returns in region |
| 10b | material_slot_assignment (RAII, 2 branches) | 2011–2034 + 2200–2208 | No early returns in region |
| 11 | post_import_finalize (RAII) | 2266–2274 | Yes (destructor) |
| 12 | STALL_SUMMARY | Before #else (2275) | N/A |
