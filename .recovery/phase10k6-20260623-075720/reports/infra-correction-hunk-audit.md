# Infrastructure Correction Delta Hunk Audit

## Hunk 1 — PhaseDurations member (line 171)
Classification: FFbxScopePhase optional map accumulator
Content: Added `TMap<FString, double>* PhaseDurations;` member

## Hunk 2 — InPhaseDurations parameter (line 179)
Classification: FFbxScopePhase optional map accumulator
Content: Added `TMap<FString, double>* InPhaseDurations = nullptr` constructor parameter

## Hunk 3 — PhaseDurations initializer (line 188)
Classification: FFbxScopePhase optional map accumulator
Content: Added `, PhaseDurations(InPhaseDurations)` to initializer list

## Hunk 4 — Destructor accumulation (line 199)
Classification: FFbxScopePhase optional map accumulator
Content: Added `if (PhaseDurations && Kind == Exclusive) PhaseDurations->FindOrAdd(PhaseName) += Ms;`

## Hunk 5 — FStringFromFixedAnsi helper (new function)
Classification: FStringFromFixedAnsi helper
Content: static FString with bounded null-terminated read via ConstructFromPtrSize

## Hunk 6 — Semantic-signature ObjectName conversion
Classification: bounded-conversion substitution
Content: Replaced ANSI_TO_TCHAR with FStringFromFixedAnsi(Request.ObjectName, UE_ARRAY_COUNT)

## Hunk 7 — SyncId snapshot
Classification: one frozen SyncId snapshot
Content: Added `const int32 SyncId = Context.Stats->MatPktSyncId;` after TransactionId allocation

## Hunk 8 — HandleImport FbxPath conversion
Classification: bounded-conversion substitution
Content: Replaced ANSI_TO_TCHAR with FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT)

## Hunk 9 — HandleImport ObjectName conversion
Classification: bounded-conversion substitution
Content: Replaced ANSI_TO_TCHAR with FStringFromFixedAnsi(Request.ObjectName, UE_ARRAY_COUNT)

## Summary
- FFbxScopePhase optional map accumulator: 4 hunks
- FStringFromFixedAnsi helper: 1 hunk
- Bounded-conversion substitutions: 3 hunks
- One frozen SyncId snapshot: 1 hunk
- Unexpected production changes: NONE
