# Parser Evidence Audit — Phase 10K.6

## Problem

"Setup [RP-incomplete-constructor]: single_match is not None" used
`_rp_inccon[3] is not None and True`. In the old 5-tuple return,
`[3]` was a bool (evaluator_valid), making the expression always True
even when `declaration_end` was -1. This was a false pass.

## Fix

1. Declared `ParserResult` class with named fields.
2. `_parse_and_prepare_rp` / `_parse_and_prepare_pv` return
   `ParserResult` instead of raw 5-tuple.
3. `_find_ffbxscopephase_matches` uses lexical-aware
   `_scan_for_phase_name_in_span` for the extraction-failed fallback,
   bounded by the next `FFbxScopePhase` token.
4. All setup assertions use named fields.
5. Explicit state assertions prove three declaration states:

   | State | match_count | declaration_end | invocation_complete | missing contains |
   |-------|-------------|-----------------|---------------------|------------------|
   | Absent decl | 0 | -2 | False | "unique declaration" |
   | Incomplete constructor | 1 | -1 | False | "constructor bounds" |
   | Complete constructor | 1 | >=0 | True | (other) |

6. Lexical false-positive fixtures prove masked phase names in
   comments, strings, and later declarations are not falsely matched.

## Proof

- Authoritative: python3 tests/phase10k6_transaction_decomposition.py
  → 340 passed, 40 failed (all expected production/source failures)
- Pytest: python3 -m pytest tests/phase10k6_transaction_decomposition.py -v
  → 50 passed, 0 failed
- No false passes in setup tests.
- No self-test-generated expected FAIL values.

## Artifacts

- Pre-snapshot: snapshots/phase10k6_transaction_decomposition.pre-parser-evidence-fix.py
- Post-snapshot: snapshots/phase10k6_transaction_decomposition.parser-evidence-fixed.py
- Patch: patches/test-parser-evidence-restoration.patch
- Report: reports/test-parser-evidence-restoration.txt
- This audit: reports/test-parser-evidence-audit.md
