# Audit: Phase 10K.6 Prerequisite Gate Restoration

## Scope
- Test file: `tests/phase10k6_transaction_decomposition.py`
- 50 tests, 50 passed.
- Unified patch: 536 lines.

## False-pass paths eliminated
| Path | Before | After |
|------|--------|-------|
| zero declarations | `assert total` (hardcoded True) | `total == 1` |
| missing prerequisite → `_check_rp_ordering` | `evaluator_valid == expect_valid` → PASS on `not expect_valid` | FAIL with explicit missing-prerequisite message |
| missing prerequisite → `_check_pv_ordering` | same | same |
| production chain `order_valid` initialization | `order_valid = True` | `order_valid = False` with detail |
| malformed RP-G fixture | uncompilable C++ with stale FbxPath location | synthetic evaluator fixture + separate C++ parse fixture |
| PV-E | unimplemented phase claiming "no ded inside" | discovery-bounds fixture; pure-evaluator fixture added |

## Shared preparation helpers
- `_prepare_request_parse_ordering`: gates handle_open, handle_close, has_unique_decl, single_match, decl_start, decl_end, block_exists, block_open, block_close, fbx_path_start, fbx_path_end, vv_pos, vps_pos.
- `_prepare_path_validation_ordering`: gates handle_open, handle_close, has_unique_decl, single_match, decl_start, decl_end, block_exists, block_open, block_close, vps_pos.

Both return `(prerequisites_valid, missing_list, evaluator_valid, failing_relations, detail_str)`.

## Production `check_phase`
- Now calls `_prepare_request_parse_ordering` / `_prepare_path_validation_ordering` instead of inline evaluator.

## New negative-fixture setup tests
- RP: no-FbxPath, no-ValidateVersion, no-ValidatePathSecurity, malformed-invocation
- PV: no-ValidatePathSecurity, malformed-invocation

## New production-prerequisite self-tests
- 8 RP scenarios (no decl, no block, no decl end, no FbxPath, no VV, no VPS, bad order, good order)
- 6 PV scenarios (no decl, no block, no decl end, no VPS, bad order, good order)

## Preserved evaluator tests
- 15 direct evaluator unit tests: 1 valid + 9 inverted-adjacent RP, 1 valid + 6 inverted-adjacent PV.

## Artifacts
- Pre-snapshot: snapshots/phase10k6_transaction_decomposition.pre-prerequisite-gate-fix.py
- Post-snapshot: snapshots/phase10k6_transaction_decomposition.post-prerequisite-gate-fix.py
- Unified patch: patches/test-prerequisite-gate-restoration.patch (536 lines)
- Report: reports/test-prerequisite-gate-restoration.txt
- This audit: reports/test-prerequisite-gate-restoration.audit.md
