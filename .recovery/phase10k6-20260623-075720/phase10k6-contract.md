# Phase 10K.6 — FBX Transaction Decomposition Contract (v2)

Extracted from `tests/phase10k6_transaction_decomposition.py` and surviving headers,
with design corrections applied per v1 and v2 review.

---

## Infrastructure Requirements

### R1: FBXTransactionId atomic
- **Source**: `SyncTypes.h`
- **Symbol**: `std::atomic<int32> FBXTransactionId{1}`
- **Behavior**: Auto-incrementing ID starting at 1 per FBX transaction
- **Test**: `test_fbx_transaction_id_atomic()` (L80-83), `test_transaction_id_never_zero()` (L460-464)

### R2: TransactionId allocation in HandleImport
- `int32 TransactionId = Context.Stats->FBXTransactionId.fetch_add(1, std::memory_order_acq_rel)`
- Must appear before any early return (before ValidatePayloadSize)
- **Test**: `test_transaction_id_alloc()` (L89-94)

### R3: FFbxScopePhase RAII struct
- **Symbol**: `struct FFbxScopePhase`
- **Constructor**: `FFbxScopePhase(int32 InTransactionId, const FGuid& InGuid, int32 InSyncId, const FString& InObjectName, const FString& InPhaseName, EFbxPhaseKind InKind, double* InDurationOut = nullptr)`
- **Constructor action**: Emits `PHASE_BEGIN` log. Classification string derived from `EFbxPhaseKind`. Stores start time.
- **Destructor action**: Emits `PHASE_END` log with computed duration. If `DurationOut` non-null, writes `*DurationOut = Ms`.
- **Tests**: `test_ffbxscopephase_struct()` (L100-107), `test_ffbxscopephase_optional_duration()` (L450-454)

### R4: FbxPhaseBegin / FbxPhaseEnd static helpers
- **Symbols**: `static void FbxPhaseBegin(int32, const FGuid&, int32, const FString&, const FString&, EFbxPhaseKind)`, `static void FbxPhaseEnd(int32, const FGuid&, int32, const FString&, const FString&, EFbxPhaseKind, double DurationMs)`
- **Behavior**: Emit PHASE_BEGIN/PHASE_END UE_LOG markers. Used for manual timing in simple branch-free scopes.
- **Tests**: `test_phase_helpers()` (L113-128)

### R5: IsExclusivePhase function
- `static bool IsExclusivePhase(const FString& PhaseName)` — returns true if `GPhaseMetadata[PhaseName].Kind == EFbxPhaseKind::Exclusive`
- **Test**: `test_inclusive_parent_excluded_from_exclusive_sum()` (L935-940), `test_registry_and_parser_do_not_drift()` (L1213)

### R6: ComputePhaseClassificationExclusive function
- `static FString ComputePhaseClassificationExclusive(const TMap<FString, double>& PhaseDurations)` — classifies exclusive-only durations
- Classification: `DOMINANT_{name}` | `MIXED` | `UNRESOLVED`
- **Test**: `test_compute_phase_classification()` (L470-474), `test_nested_classification()` (L165-166), `test_registry_and_parser_do_not_drift()` (L1217-1218)

### R7: PhaseDurations accumulator map
- `TMap<FString, double> PhaseDurations;` — per-transaction, local to HandleImport
- Accumulation: local double → FFbxScopePhase writes to local → `PhaseDurations.FindOrAdd(TEXT("phase")) += LocalDuration` AFTER scope closes
- **Test**: `test_raii_accumulator_write_after_scope()` (L1079-1146)

### R8: GPhaseMetadata — single authoritative registry
- `static const TMap<FString, FFbxPhaseMetadata> GPhaseMetadata` — maps phase name → (EFbxPhaseKind, parent). Single source of truth.
- `GExclusivePhases` derived at init from GPhaseMetadata (satisfies test symbol requirement).

---

## Transaction Identity

### R9: Marker identity fields
- `transactionId=%d`, `guid=%s`, `syncId=%d`, `objectName=%s` — present in every PHASE_BEGIN and PHASE_END

### R10: TxnObjNameSanitized
- `const FString TxnObjNameSanitized = SanitizeObjectName(ANSI_TO_TCHAR(...))`
- **Test**: `test_sanitized_name()` (L231-234)

---

## Phase Metadata Table (Final)

| # | Phase | Kind | Parent | Observability |
|---|---|---|---|---|
| 1 | request_parse | Exclusive | — | PHASE_BEGIN + PHASE_END |
| 2 | path_validation | Exclusive | — | PHASE_BEGIN + PHASE_END |
| 3 | semantic_signature | Nested | — | PHASE_BEGIN + PHASE_END |
| 4 | fbx_factory_import | Exclusive | — | PHASE_BEGIN + PHASE_END |
| 5 | imported_asset_discovery | Exclusive | — | PHASE_BEGIN + PHASE_END |
| 6 | sidecar_processing | Exclusive | — | PHASE_BEGIN + PHASE_END |
| 7 | sidecar_manifest_read | Nested | sidecar_processing | PHASE_BEGIN + PHASE_END |
| 8 | sidecar_fingerprint_classification | Nested | sidecar_processing | PHASE_BEGIN + PHASE_END |
| 9 | sidecar_asset_lookup | Nested | sidecar_processing | PHASE_BEGIN + PHASE_END |
| 10 | sidecar_batch_import | Nested | sidecar_processing | PHASE_BEGIN + PHASE_END |
| 11 | sidecar_result_mapping | Nested | sidecar_processing | PHASE_BEGIN + PHASE_END |
| 12 | static_mesh_post_import | Exclusive | — | PHASE_BEGIN + PHASE_END |
| 13 | actor_lookup_or_spawn | Exclusive | — | PHASE_BEGIN + PHASE_END |
| 14 | static_mesh_assignment | Exclusive | — | PHASE_BEGIN + PHASE_END |
| 15 | material_slot_assignment | Exclusive | — | PHASE_BEGIN + PHASE_END |
| 16 | post_import_finalize | Nested | fbx_transaction | PHASE_BEGIN + PHASE_END |

### Exclusive execution order (strictly sequential, no overlap):
```
request_parse → path_validation → [semantic_signature nested]
→ fbx_factory_import → imported_asset_discovery → sidecar_processing
→ static_mesh_post_import → actor_lookup_or_spawn
→ static_mesh_assignment → material_slot_assignment
→ [post_import_finalize nested] → STALL_SUMMARY
```

---

## Required Log Formats

### R11: PHASE_BEGIN
`[FBX][PHASE_BEGIN] transactionId=%d guid=%s syncId=%d objectName=%s phase="%s" classification="%s"`

### R12: PHASE_END
`[FBX][PHASE_END] transactionId=%d guid=%s syncId=%d objectName=%s phase="%s" classification="%s" durationMs=%.1f`

### R13: STALL_SUMMARY
`[FBX][STALL_SUMMARY] transactionId=%d guid=%s syncId=%d objectName=%s totalMs=%.1f measuredExclusiveMs=%.1f coveragePercent=%.2f exclusiveExcessMs=%.1f timingValidity=%s largestPhase=%s largestPhaseMs=%.1f secondLargestPhase=%s secondLargestPhaseMs=%.1f unattributedMs=%.1f classification=%s`

---

## Arithmetic Formulas

### R14: rawCoveragePercent
`rawCoveragePercent = (measuredExclusiveMs / totalMs) * 100.0`
- **NOT clamped**. If > 100.0, observable. Do not use `FMath::Min(100.0, ...)`.

### R15: reported coveragePercent
Same as rawCoveragePercent.

### R16: exclusiveExcessMs
`exclusiveExcessMs = FMath::Max(0.0, measuredExclusiveMs - totalMs)`

### R17: unattributedMs
`unattributedMs = FMath::Max(0.0, totalMs - measuredExclusiveMs)`

### R18: timingValidity
`if (exclusiveExcessMs > 0.5) → "INVALID_OVERLAP" else → "VALID"`

### R19: largestPhase / largestPhaseMs
Exclusive phase with highest PhaseDurations value.

### R20: secondLargestPhase / secondLargestPhaseMs
Exclusive phase with second-highest PhaseDurations value.

### R21: classification
If timingValidity != VALID → `"UNRESOLVED"`
Else if only one exclusive phase → `"DOMINANT_{name}"`
Else if secondLargestMs >= 0.8 * largestMs → `"MIXED"`
Else → `"DOMINANT_{largestPhase}"`

### R22: measuredExclusiveMs
Sum of PhaseDurations[phase] where IsExclusivePhase(phase) is true. Nested excluded.

---

## request_parse Design (Corrected)

- **Strategy**: Local timer + deferred marker emission.
- Timer starts before ValidatePayloadSize (line 893).
- Work measured: ValidatePayloadSize, memcpy+parsing, field extraction/logging, ValidateVersion.
- On early return from payload or version failure: no marker emitted (failed parse, no identity).
- On success: identity available from parsed Request struct.
- PHASE_BEGIN + PHASE_END + PhaseDurations write after identity is valid.

```cpp
const double RequestStart = FPlatformTime::Seconds();
// Lines 893–927 unchanged (ValidatePayloadSize, memcpy, logging, ValidateVersion)
// If early return: no phase marker
const double RequestMs = (FPlatformTime::Seconds() - RequestStart) * 1000.0;
PhaseDurations.FindOrAdd(TEXT("request_parse")) = RequestMs;
FbxPhaseBegin(TransactionId, ..., TEXT("request_parse"), EFbxPhaseKind::Exclusive);
FbxPhaseEnd(TransactionId, ..., TEXT("request_parse"), EFbxPhaseKind::Exclusive, RequestMs);
```

---

## Phase Marker Rules (All 16 phases)

Every observable phase MUST:
1. Have PHASE_BEGIN before work begins
2. Have PHASE_END after work ends
3. Use RAII (FFbxScopePhase) in any scope with early returns or multiple branches
4. Use manual (FbxPhaseBegin/FbxPhaseEnd) only in simple branch-free scopes

---

## Duration Accumulation Pattern

```cpp
double LocalDuration = 0.0;
{
    FFbxScopePhase Scope(TransactionId, Guid, SyncId, ObjName,
        TEXT("phase_name"), EFbxPhaseKind::Exclusive, &LocalDuration);
    // work
}
PhaseDurations.FindOrAdd(TEXT("phase_name")) += LocalDuration;
```

Do NOT pass `&PhaseDurations.FindOrAdd(...)` to the constructor. Use a local and `+=` after scope.

---

## Forbidden Patterns

| Pattern | Reason |
|---|---|
| Hard-coded duration | Every phase must measure real time |
| `FMath::Min(100.0, coveragePercent)` | Coverage must be raw |
| `total - measured` without `FMath::Max(0.0, ...)` | unattributedMs must be >= 0 |
| Classification = phase name | Must be `DOMINANT_{name}` or `MIXED` or `UNRESOLVED` |
| Two overlapping exclusive phases | Invalid — exclusive sum accuracy |
| Orphan begin/end | All observable phases must have balanced pairs |
| Manual begin/end in early-return scope | Use FFbxScopePhase RAII instead |
| `&PhaseDurations.FindOrAdd(...)` passed to RAII | Use local + `+=` after scope |
| Mutable duplicate phase lists | One registry (GPhaseMetadata), one derived set (GExclusivePhases) |

---

## STALL_SUMMARY Field Status

### Test-Required (existing test assertions)
| Field | Test location | Assertion |
|---|---|---|
| transactionId | test_stall_summary L179-180 | regex `STALL_SUMMARY.*transactionId=%d` |
| totalMs | test_stall_summary L181-182 | regex `STALL_SUMMARY[\s\S]*totalMs=%.1f` |
| measuredExclusiveMs | test_stall_summary L183-184 | regex `measuredExclusiveMs=%.1f` |
| coveragePercent | test_stall_summary L185-186 | regex `coveragePercent=.*%` |
| largestPhase | test_stall_summary L187-188 | regex `largestPhase=%s` |
| largestPhaseMs | test_stall_summary L189-190 | regex `largestPhaseMs=%.1f` |
| unattributedMs | test_stall_summary L191-192 | regex `unattributedMs=%.1f` |
| classification | test_stall_summary L193-194 | regex `classification=%s` |

### DESIGN_REQUIREMENT (added by corrected contract, no existing test)
| Field | Rationale |
|---|---|
| exclusiveExcessMs | Required by overlap detection design |
| timingValidity | Required by overlap/validity design |
| secondLargestPhase | Required by classification algorithm design |
| secondLargestPhaseMs | Required by classification algorithm design |
