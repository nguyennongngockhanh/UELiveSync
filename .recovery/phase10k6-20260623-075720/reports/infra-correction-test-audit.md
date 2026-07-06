# Test-Change Audit (corrected)

## Changes between pre-test-restoration and test-restored

### 1. test_fstring_from_fixed_ansi (modified — added 4 structural boundary checks)
- null Data guard clause
- loop checks Length < Capacity before Data access
- ++Length increments by 1
- ConstructFromPtrSize receives Length, not Capacity
- **Classification**: new structural safety checks

### 2. coveragePercent test (display name corrected)
- Old: "Source: coveragePercent clamped to 100% max" (misleading — implied clamp)
- New: "Source: coveragePercent calculation exists" (matches approved contract: no clamp)
- Assertion changed from regex match on `CoveragePercent.*100\.0 | CoveragePercent.*=.*100`
  to simple `CoveragePercent` existence check
- **Classification**: display name correction to match approved contract

### 3. test_production_phase_wiring (new function)
- 2 assertions: path_validation and request_parse scope with Exclusive+&PhaseDurations
- 1 assertion: request_parse scope begins before ValidateVersion (ordering hint)
- All 3 FAIL as expected (phases not yet implemented)
- **Classification**: restored equivalent production requirements (adapted to new destructor-owner architecture)

### 4. main() — added test_production_phase_wiring() call

## Summary

Requirements removed: 0
Requirements adapted: 2 (path_validation + request_scope => destructor-owner accumulator)
  - OLD: post-scope PhaseDurations.FindOrAdd(TEXT("phase"))
  - NEW: FFbxScopePhase Scope(..., TEXT("phase"), EFbxPhaseKind::Exclusive, ..., &PhaseDurations)
New production requirements: 3 (2 scope+wiring + 1 ordering hint)
New safety requirements: 4 structural bounded-helper boundary checks
Display name corrections: 1 (coverage clamp -> coverage calculation)

No regression in existing infrastructure or log/arithmetic tests.
