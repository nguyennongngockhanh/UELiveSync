# Phase 10K.6 — Anchor Map (v2)

Stable source-code anchors only. No line numbers.

---

## Infrastructure Anchors

| # | Insert at | Content |
|---|---|---|
| A0 | After `// =========================================================` (end of Phase 7C comment block, ~line 34) | GPhaseMetadata, derived GExclusivePhases, IsExclusivePhase, ComputePhaseClassificationExclusive, FFbxScopePhase, FbxPhaseBegin, FbxPhaseEnd |

---

## request_parse (Exclusive)

**Timer start**: Before `if (!ValidatePayloadSize(...))` (after stats guard).

**Marker emission**: After `if (!ValidateVersion(...))` block closes, BEFORE `FString FbxPathStr(...)`. Identity (GUID, syncId, SafeName) is available by this point.

**Marker content**: Uses `Request.ObjectGUID`, `Request.SyncId`, `TxnObjNameSanitized`.

**Early return**: Failed payload or version → no phase marker emitted.

**Accumulator**:
```cpp
PhaseDurations.FindOrAdd(TEXT("request_parse")) += RequestMs;
```

---

## path_validation (Exclusive)

**Start anchor**: `if (!ValidatePathSecurity(FbxPathStr, *Context.Stats))`
**End anchor**: The line `#if WITH_EDITOR` (immediately before semantic signature block)

**Method**: FFbxScopePhase RAII wrapping lines 932–956. Destructor handles early return at line 934.

**Accumulator**: Local `double PathMs` → `PhaseDurations.FindOrAdd(TEXT("path_validation")) += PathMs` after scope.

---

## semantic_signature (Nested)

**Start anchor**: `// Phase 10J.5F: Compute semantic signature (incl. geometry hash) and check cache before import.`
**End anchor**: The closing `}` of the semantic-signature block (after coalesce check + early return or cache update)

**Method**: FFbxScopePhase RAII replacing the existing `{...}` block. Destructor handles early return (line 1054).

**No accumulator** (nested).

---

## fbx_factory_import (Exclusive)

**Start anchor**: `const FString FullPendingPath =`
**End anchor**: `AssetTools.ImportAssetTasks({ ImportTask });`

**Method**: FFbxScopePhase RAII. Destructor handles early return at line 1075.

**Accumulator**: Local + `+=` after scope.

---

## imported_asset_discovery (Exclusive) — NARROW

**Start anchor**: `// === Phase 10J.5Q: Check pending import result ===`
**End anchor**: `// Phase 7H.6 / Task 9B.1: sidecar texture import (always runs).`

**Scope**: Lines 1111–1137 only (imported object counting loop). Must CLOSE BEFORE sidecar block.

---

## sidecar_processing (Exclusive)

**Start anchor**: `// Phase 7H.6 / Task 9B.1: sidecar texture import (always runs).`
**End anchor**: `}` closing the main sidecar block, followed by blank line then `UObject* PendingAsset = nullptr;`

---

## Nested sub-phases (within sidecar_processing)

All use RAII FFbxScopePhase with `EFbxPhaseKind::Nested` and `nullptr` duration out.

### sidecar_manifest_read
**Start anchor**: `if (IFileManager::Get().FileExists(*ManifestPath))`
**End anchor**: `}` closing the manifest-read `if` block (before `TArray<FString> SidecarFiles;`)

### sidecar_fingerprint_classification
**Start anchor**: `auto IsImageExtension = [](const FString& ExtLower)`
**End anchor**: `}` after the FindFilesRecursive fallback block

### sidecar_asset_lookup
**Start anchor**: `for (const FString& SourceFile : SidecarFiles)`
**End anchor**: `}` closing the for-loop (before `if (NewFilesForImport.Num() > 0)`)

### sidecar_batch_import
**Start anchor**: `if (NewFilesForImport.Num() > 0)`
**End anchor**: `}` closing that block (before `else` log or result mapping)

### sidecar_result_mapping
**Start anchor**: `// Log final texture results and populate per-GUID sidecar map.`
**End anchor**: `}` closing the outer `if (SidecarFiles.Num() > 0)` block (before PendingAsset)

---

## static_mesh_post_import (Exclusive)

**Start anchor**: `UObject* PendingAsset = nullptr;`
**End anchor**: `}` closing the semantic signature cache update block (before `// Spawn or update...`)

**Method**: FFbxScopePhase RAII. Early returns at lines 1796, 1807, 1913 handled.

---

## actor_lookup_or_spawn (Exclusive)

**Start anchor**: `// Spawn or update StaticMeshActor by LiveSync GUID`
**End anchor**: `}` closing the `bHasExistingTransform` check (before `AStaticMeshActor* MeshActor = nullptr;`)

---

## static_mesh_assignment + material_slot_assignment (Both Exclusive, Sequential)

Each uses a local RAII scope per branch (update and spawn).

### Update branch (lines 1960–2058):

**static_mesh_assignment**:
```
Start: SMC->SetStaticMesh(StaticMesh);
End:   before `// Phase 10J.5B.2: restore non-null material overrides.`
```

**material_slot_assignment**:
```
Start: `// Phase 10J.5B.2: restore non-null material overrides.`
End:   after `LogExtendedFBXValidate(MeshActor, SMC, Request.ObjectGUID);`
```

### Spawn branch (lines 2145–2264):

**static_mesh_assignment**:
```
Start: SMC->SetStaticMesh(StaticMesh);
End:   after RefreshFBXStaticMeshComponent(SMC, MeshActor);
```

**material_slot_assignment**:
```
Start: EnsureFBXMeshRenderable(SMC, StaticMesh, MeshActor, Request.ObjectGUID);
End:   after LogExtendedFBXValidate(MeshActor, SMC, Request.ObjectGUID);
```

---

## post_import_finalize (Nested)

**Start anchor**: `// Destroy old non-FBX actor for same GUID to prevent double ownership.`
**End anchor**: `#else` (the `#else` at column 0, line 2275)

**Method**: FFbxScopePhase RAII with `EFbxPhaseKind::Nested`, `nullptr` duration out. Parent: `fbx_transaction`.

---

## STALL_SUMMARY

**Insert after**: `}` closing post_import_finalize
**Insert before**: `#else` (line 2275)

Uses: `HandleImportStart` (captured at line 891), `PhaseDurations`, `IsExclusivePhase`, `ComputePhaseClassificationExclusive`.
