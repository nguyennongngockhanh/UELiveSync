# Audit: Phase 10K.6 Preparation State Restoration

## Scope
- Test file: `tests/phase10k6_transaction_decomposition.py`
- Pre-snapshot: `snapshots/phase10k6_transaction_decomposition.pre-preparation-state-fix.py`
- Post-snapshot: `snapshots/phase10k6_transaction_decomposition.preparation-state-fixed.py`
- Unified patch: `patches/test-preparation-state-restoration.patch` (448 lines)

## Changes

### 1. Constructor-bound gate fixed
Both `_prepare_request_parse_ordering` and `_prepare_path_validation_ordering`:
- Before: `decl_start < 0 and decl_end < 0` (AND — missed `decl_end=-1` when `decl_start >= 0`)
- After: `decl_start < 0 or decl_end <= decl_start` (OR — catches any invalid range)

### 2. `_check_prereq` replaced with `_check_preparation`
- Before: used `not pv` / `not ev` assertions (double negatives) and `_test(..., True, ...)` for evaluator results
- After: uses `actual_pv == expect_pv` and `actual_ev == expect_ev` (direct equality), asserts exact missing items and failing relations

### 3. Controlled negative-fixture setup tests rewritten
Call `_parse_and_prepare_rp/_pv` (parser→preparation directly), not `_check_rp_ordering/_check_pv_ordering`:
- Before: `_check_rp_ordering(fixture, label, False)` produced a FAIL result for correct rejection
- After: `_parse_and_prepare_rp(fixture)` → `_test(label, prerequisites_valid == False, ...)` → PASSes

### 4. `_parse_and_prepare_rp/_pv` helpers added
- Inline parsing logic from `_check_rp_ordering`/`_check_pv_ordering` but return preparation result without `_test` call
- Used by the 6 setup tests

### 5. RP-good-order test data fixed
FbxPath end position (48) was after the ValidateVersion position (46), making the chain fail.
Fixed: fS=42, fE=44, vv=46 (strictly increasing).

## Test Results

### Authoritative runner: 336 passed, 40 failed
- 0 unexpected failures
- 40 expected Production/Source failures (not yet implemented in source)

### Pytest: 50 passed, 0 failed

### Preparation state (14 cases)
All prerequisites_valid, evaluator_valid, missing list, and failing relation assertions PASS.

### Parser setup tests (6 cases)
All correctly identify the intended missing prerequisite and PASS.

## Evidence that no false-pass paths remain
| Path | Before (false pass) | After |
|------|---------------------|-------|
| Constructor bounds with decl_start>=0, decl_end=-1 | `decl_start < 0 and decl_end < 0` → `False` → bounds not reported | `decl_start < 0 or decl_end <= decl_start` → `True` → bounds reported |
| `_check_prereq` "missing list non-empty" for empty list | `len(pm) > 0` with `pm=[]` → `False` → FAIL (but reported as expected) | Replaced with `_check_preparation` that asserts exact items |
| Controlled negative fixture uses reporter helper | `_check_rp_ordering(fix, label, False)` → always FAIL | `_parse_and_prepare_rp(fix)` → `_test` with `==` → PASS on correct rejection |

## Readiness for request_parse/path_validation implementation
YES — all prerequisite gates are proven, all state distinctions are proven.
