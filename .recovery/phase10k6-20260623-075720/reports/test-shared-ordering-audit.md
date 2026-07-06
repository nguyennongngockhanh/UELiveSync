# Shared Ordering Evaluators — Audit Report

Date: 2026-06-23T07:57:20+00:00

## Changes Made

### 1. Shared Ordering Evaluators (new functions)

Added `_evaluate_request_parse_ordering` and `_evaluate_path_validation_ordering`,
both delegating to the generic `_evaluate_chain` helper.

**request_parse chain:**
handle_open < phase_open < declaration_start < declaration_end <
fbx_path_invocation_start < fbx_path_invocation_end < ValidateVersion_call <
phase_close < ValidatePathSecurity_call < handle_close

**path_validation chain:**
handle_open < phase_open < declaration_start < declaration_end <
ValidatePathSecurity_call < phase_close < handle_close

Each returns `(valid, failing_relations, detail_str)`.

### 2. Production test updated

`test_production_phase_wiring`'s `check_phase` now calls the shared evaluators
instead of inline chain logic.  The `_evaluate_*_ordering` functions are used
identically by both production and fixtures.

### 3. Declaration count assertions

Added explicit assertions after the uniqueness check reporting both:
- total declaration count
- complete declaration count (has both Exclusive and PhaseDurations)

### 4. Request_parse ordering fixtures (RP-A through RP-G)

All use `_check_rp_ordering` which calls
`_evaluate_request_parse_ordering`.

| Fixture | Scenario | Expected |
|---------|----------|----------|
| RP-A | Correct full ordering | PASS |
| RP-B | FbxPath before phase declaration | FAIL |
| RP-C | ValidateVersion before FbxPath | FAIL |
| RP-D | FbxPath after ValidateVersion | FAIL |
| RP-E | VPS inside request_parse block | FAIL |
| RP-F | VPS in nested scope before phase_close | FAIL |
| RP-G | FbxPath invocation crosses phase_close | FAIL |

### 5. Path_validation ordering fixtures (PV-A through PV-E)

All use `_check_pv_ordering` which calls
`_evaluate_path_validation_ordering`.

| Fixture | Scenario | Expected |
|---------|----------|----------|
| PV-A | Correct full ordering | PASS |
| PV-B | VPS before declaration_end | FAIL |
| PV-C | VPS before phase declaration | FAIL |
| PV-D | VPS after phase_close (block outside) | FAIL |
| PV-E | Block outside HandleImport bounds | FAIL |

### 6. Fix: no-decl handling in fixture checkers

`_check_rp_ordering` and `_check_pv_ordering` now return `not expect_valid`
when no declaration is found, so PV-E correctly shows PASS (the failure
is expected).

## Remaining Failures (34 — all expected)

- **17 Production failures**: `path_validation` / `request_parse` declarations
  don't exist in source.
- **17 Source marker failures**: STALL_SUMMARY, exclusive phases, timing
  validity, Phase 10K.5 markers not yet added to source.

## Artifacts

All under `.recovery/phase10k6-20260623-075720/`:

- `snapshots/phase10k6_transaction_decomposition.pre-shared-ordering-fix.py` (2695 lines)
- `snapshots/phase10k6_transaction_decomposition.shared-ordering-fixed.py` (2981 lines)
- `patches/test-shared-ordering-restoration.patch` (497 lines)
- `reports/test-shared-ordering-restoration.txt`
- `reports/test-shared-ordering-audit.md`

## Conclusion

**Test migration: PASS**
**Ready for source implementation: YES**

All ordering fixtures use the same shared evaluator as production.
No test-logic duplication.  All authoritative evidence in `.recovery/`.
