# Exact Scope Restoration Audit

## Changes from pre-exact-scope-fix → exact-scope-fixed

### New helper: `_find_dedicated_block(content, mask, decl_pos)`
- Finds the exact `{ }` block ONLY if the declaration is a **direct child** of it
- Rejects declarations inside nested blocks (rel_depth != 0)
- Rejects declarations directly in HandleImport body (hi_open == open_pos)
- Returns (open, close, body) tuple on success, None on failure

### New helper: `_is_direct_child_of_block(content, mask, open_pos, decl_pos)`
- Verifies no unbalanced `{ }` exist between open_pos and decl_pos

### New helper: `_find_handleimport_open(content, mask)`
- Finds `bool FLiveSyncFBXImporter::HandleImport(` and its opening `{`

### `test_production_phase_wiring` rewritten
- Independent assertion for each requirement (declaration exists, Exclusive, &PhaseDurations)
- Uses `_test_phase_wiring()` helper for per-phase shared assertions (7 assertions each)
- Uses `_find_dedicated_block()` for exact explicit-block verification
- Phase-specific assertions (request_parse checks 8-10) done separately

### Self-test fixture `_test_scope_extractor_self_test()` added
7 fixtures, all PASS:
| Fixture | Expected | Result |
|---------|----------|--------|
| A: Dedicated block `{ FFbxScopePhase ... }` | recognized | PASS |
| B: HandleImport body only (no dedicated block) | rejected | PASS |
| C: Dedicated block with nested `if { }` after decl | recognized | PASS |
| C2: Dedicated block with `if { }` before decl | recognized | PASS |
| C3: Scope inside nested `if { ... Scope ... }` block | recognized | PASS |
| D: Braces inside comments/strings | recognized | PASS |
| HandleImport body as scope (full signature) | rejected | PASS |

## Requirements Coverage

### path_validation (7 assertions, all FAIL as expected)
1. FFbxScopePhase declaration exists — FAIL
2. Uses EFbxPhaseKind::Exclusive — FAIL
3. Supplies &PhaseDurations — FAIL
4. Declared in dedicated explicit block — FAIL
5. Dedicated block narrower than HandleImport body — FAIL
6. Declaration before ValidatePathSecurity — FAIL
7. Block encloses ValidatePathSecurity — FAIL

### request_parse (10 assertions, all FAIL as expected)
1. FFbxScopePhase declaration exists — FAIL
2. Uses EFbxPhaseKind::Exclusive — FAIL
3. Supplies &PhaseDurations — FAIL
4. Declared in dedicated explicit block — FAIL
5. Dedicated block narrower than HandleImport body — FAIL
6. Declaration before ValidateVersion — FAIL
7. Block encloses ValidateVersion — FAIL
8. Block encloses bounded FbxPath extraction — FAIL
9. Block closes before ValidatePathSecurity — FAIL
10. ValidatePathSecurity outside block — FAIL

## Non-production failures (17, unchanged)
All expected future-slice gaps (STALL_SUMMARY, SINGLE, Phase 10K.5, etc.)

## Final Count
- Passed: 175
- Failed: 34 (17 production + 17 non-production)
- Unexpected: 0
