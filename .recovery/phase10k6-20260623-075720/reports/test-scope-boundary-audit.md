# Scope Boundary Restoration Audit

## Changes from pre-test-restoration → scope-boundary-fixed

### 1. Lexical scanner helpers added (new)
- `_build_lexical_mask(content)` — marks positions inside line comments,
  block comments, string literals, and character literals
- `_find_enclosing_scope(content, mask, position)` — finds the exact { } block
  containing a position using comment/string-aware brace counting
- **Classification**: new infrastructure required for enclosure verification

### 2. test_production_phase_wiring (rewritten)
- 12 independent assertions (was 3 combined assertions)
- Each requirement has its own assertion with specific detail message
- Uses scope extractor for lexical enclosure verification

### 3. coveragePercent display name corrected
- Already done in previous slice — carried forward

### 4. ValidateVersion/ValidatePathSecurity position lookup fixed
- Now searches inside HandleImport body only, avoiding false match on function
  definitions at the top of the file

## Requirements Count

### path_validation (5 assertions)
1. FFbxScopePhase instance exists with TEXT("path_validation") — FAIL (expected)
2. Uses EFbxPhaseKind::Exclusive — FAIL (expected)
3. Supplies &PhaseDurations — FAIL (expected)
4. Scope declared before ValidatePathSecurity — FAIL (expected)
5. Scope lexically encloses ValidatePathSecurity — FAIL (expected)

### request_parse (7 assertions)
1. FFbxScopePhase instance exists with TEXT("request_parse") — FAIL (expected)
2. Uses EFbxPhaseKind::Exclusive — FAIL (expected)
3. Supplies &PhaseDurations — FAIL (expected)
4. Scope declared before ValidateVersion — FAIL (expected)
5. Scope lexically encloses ValidateVersion — FAIL (expected)
6. Scope lexically encloses bounded FbxPath extraction — FAIL (expected)
7. Scope closes before ValidatePathSecurity — FAIL (expected)

### Non-production failures unchanged (17)
- path_validation PHASE_END, SINGLE, STALL_SUMMARY × 9, coverage calculation,
  timing validity, Phase 10K.5 × 2, MIXED validity, STALL_SUMMARY marker

## Total
- 168 passed
- 29 failed (17 pre-existing + 12 new production requirements)
- 0 unexpected failures
