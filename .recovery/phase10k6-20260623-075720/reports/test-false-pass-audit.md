# False-Pass Path Correction — Audit Report

Date: 2026-06-23T07:57:20+00:00

## Changes Made

### 1. Declaration-count assertions (task 1)

`check_phase` in `test_production_phase_wiring`:

**Before:** Both total and complete counts used `True` as the condition,
always passing.

```python
_test(f"Production: '{ph_name}' total declaration count = {total}", True, ...)
_test(f"Production: '{ph_name}' complete declaration count = {complete}", True, ...)
```

**After:** Real conditions enforce exactly 1 declaration:

```python
_test(f"Production: '{ph_name}' total declaration count = {total}", total == 1, ...)
_test(f"Production: '{ph_name}' complete declaration count = {complete}", complete == 1, ...)
```

Diagnostic behavior confirmed:
- one declaration with Exclusive but no PhaseDurations: total=1 PASS, complete=1 FAIL
- two complete declarations: total=1 FAIL, complete=1 FAIL

### 2. Production ordering prerequisite behavior (task 2)

**Before:** `order_valid = order_failing = order_detail = True` followed by
`order_failing = []` and `order_detail = ""`. When the evaluator was not
called (missing declaration/block/call), `order_valid` remained `True` — a
false PASS.

**After:** `order_valid = False` with explicit per-prerequisite detail
strings:

- `"missing unique declaration"`
- `"missing dedicated block"`
- `"missing '{func}' call position"`
- `"missing FbxPath invocation"`

### 3. Negative fixture prerequisite enforcement (task 3)

`_check_rp_ordering` and `_check_pv_ordering`:

**Before:** Missing declaration or block returned `not expect_valid` — a
negative fixture (`expect_valid=False`) would PASS even when prerequisites
were missing.

**After:** Missing declaration → always FAIL with `"missing declaration"`.
Missing block → always FAIL with `"missing dedicated block"`.

A negative fixture only PASSes when:
- prerequisites are valid
- evaluator is called
- evaluator returns False
- expected failed relation is present

### 4. RP-D corrected (task 4)

**Before:** FbxPath was OUTSIDE the request_parse block, after VV. The
expected failed relation `fbx_path_invocation_end<ValidateVersion_call`
was reachable but with a different arrangement than intended.

**After:** FbxPath is INSIDE the block but AFTER ValidateVersion. The
shared evaluator correctly fails on `fbx_path_invocation_end<
ValidateVersion_call` because VV is before FbxPath in source order.

### 5. RP-G replaced (task 5)

**Before:** Unbalanced C++ syntax:
```cpp
FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT(Request.FbxPath)
}
);
```

**After:** Pure synthetic evaluator fixture with numeric positions:
- `handle_open=0, phase_open=10, decl_start=20, decl_end=30`
- `fbx_start=40, fbx_end=60, vv_pos=70, vps_pos=80`
- `handle_close=90, phase_close=50`
- Fails on `ValidateVersion_call<phase_close` (correct boundary violation).

Plus a separate parser fixture (`RP-G-parse`) proving a normal balanced
FbxPath invocation returns correct start/end.

### 6. PV-E reclassified + invalid-bounds fixture (task 6)

**Before:** PV-E called `_check_pv_ordering` which always FAILed when block
was outside HI (prereq missing).

**After:** PV-E is a dedicated discovery/bounds fixture that manually checks
`_find_ffbxscopephase_decls` returns 0 decls inside HI bounds.  No ordering
evaluator call — PASS when parser correctly rejects outside-HI block.

Added pure evaluator fixture `PV-E-invalid` with `handle_open < phase_open`
(e.g., handle_open=10, phase_open=5).  Shared evaluator returns False with
`handle_open<phase_open` as the failing relation.

### 7. Direct shared-evaluator unit fixtures (task 7)

Added 2 groups of unit tests that call the evaluator directly (no C++ parsing):

**Request_parse evaluator:**
- One complete valid chain → True
- Each of 9 adjacent relations independently inverted → False, with exactly
  the inverted relation in `failing_relations`

**Path_validation evaluator:**
- One complete valid chain → True
- Each of 6 adjacent relations independently inverted → False, with exactly
  the inverted relation in `failing_relations`

These prove the evaluator logic itself, independently from source parsing.

### 8. Missing-production-prerequisite self-tests (task 2)

Added 3 direct evaluator calls demonstrating:
- Missing declaration (decl=-1) → evaluator returns False
- Missing call (vps=-1) → evaluator returns False
- Missing HandleImport bounds (handle_open=-1) → evaluator returns False

## Test Summary

| Metric | Value |
|--------|-------|
| Total tests | 329 |
| Passed | 289 |
| Failed | 40 |
| Expected production/source | 40 |
| Unexpected | 0 |

## Artifacts

All under `.recovery/phase10k6-20260623-075720/`:

- `snapshots/phase10k6_transaction_decomposition.pre-false-pass-fix.py` (2981 lines)
- `snapshots/phase10k6_transaction_decomposition.false-pass-fixed.py` (3117 lines)
- `patches/test-false-pass-restoration.patch` (300 lines)
- `reports/test-false-pass-restoration.txt`
- `reports/test-false-pass-audit.md`

## Conclusion

**All false-pass paths eliminated:** PASS
**Negative fixtures require valid prereqs:** PASS
**Declaration counts use real conditions:** PASS
**Production ordering defaults to FAIL:** PASS
**RP-D/ RP-G/ PV-E corrected:** PASS
**Direct evaluator unit tests added:** PASS

**Ready for request_parse/path_validation implementation:** YES
