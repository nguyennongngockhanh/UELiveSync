# Audit: Phase 10K.6 Final Migration Restoration

## Scope
- Test file: `tests/phase10k6_transaction_decomposition.py`
- Pre-snapshot: `snapshots/phase10k6_transaction_decomposition.pre-final-migration-fix.py`
- Post-snapshot: `snapshots/phase10k6_transaction_decomposition.final-migration-fixed.py`
- Unified patch: `patches/test-final-migration-restoration.patch` (216 lines)
- Report timestamp: 2026-06-23T12:22:28+07:00

## Changes since preparation-state-fix

### 1. `_find_ffbxscopephase_matches` helper added
New function that finds FFbxScopePhase tokens WITHOUT requiring successful
constructor extraction (unlike `_find_ffbxscopephase_decls`).
Returns `(re.Match, invocation_or_None)`.

### 2. `_parse_and_prepare_rp` / `_parse_and_prepare_pv` updated
Both now use `_find_ffbxscopephase_matches` instead of
`_find_ffbxscopephase_decls` + `_count_phase_declarations`.
This enables detection of three declaration states:

| State | has_unique | single_match | decl_end | Missing item |
|-------|-----------|--------------|----------|--------------|
| A. No token found | False | None | -1 | unique declaration |
| B. Token found, extraction fails | True | set | -1 | constructor bounds |
| C. Complete constructor | True | set | > ds | (varies) |

### 3. Setup tests expanded
- **RP-absent-decl**: no FFbxScopePhase → "unique declaration"
- **RP-incomplete-constructor**: token with unmatched parens → "constructor bounds"
- **PV-absent-decl**: no FFbxScopePhase → "unique declaration"
- **PV-incomplete-constructor**: token with unmatched parens → "constructor bounds"
- Previous C-category tests preserved.

### Classification verified
40 failures classified into 7 categories totaling 40.

## Results
- Authoritative runner: 340 passed, 40 failed (0 unexpected)
- Pytest: 50 passed, 0 failed
- All parser-to-preparation tests PASS
- All preparation state tests PASS
- No self-test-generated expected FAIL

## Evidence
- RP absent declaration: missing=["unique declaration", "dedicated block"]
- RP incomplete constructor: missing=["constructor bounds"] (not "unique declaration")
- PV absent declaration: missing=["unique declaration", "dedicated block"]
- PV incomplete constructor: missing=["constructor bounds"] (not "unique declaration")
