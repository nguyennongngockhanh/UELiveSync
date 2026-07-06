# Validation-Order Semantic Closure — Audit Report

**REPORT_TS:** 2026-06-23T15:14:09+07:00

## ObjectName Guard

- Exact expected count: 1
- Actual production count: 1
- Zero case rejected: YES (self-test B: count != 1)
- Duplicate case rejected: YES (self-test B: count != 1)
- Comment ignored: YES (self-test C: count == 1)
- Unrelated field ignored: YES (self-test D: count == 1)

## Ordering Fail-Closed Guard

- All production ordering positions present: PASS
- Production ordering valid: PASS
- Missing-position fixture `all_positions_present`: False
- Missing-position fixture `order_ok`: False

## Production Integrity

- Expected SHA: `d88030c96b434909a31717cdb3045b65a9ca114fdd69ccaf77f3e6938ba406b1`
- Actual SHA: `d88030c96b434909a31717cdb3045b65a9ca114fdd69ccaf77f3e6938ba406b1`
- Production unchanged: YES

## Test SHA-256

- Pre snapshot: `ba317a4a17c15334946df2907b75f50b5ba56e9687ab9aa744055c0047b1c03a`
- Post snapshot: `dfab565e9551e3ab0a0992b0ab55a3a2c732b50f92c47836913355bb853fc5bf`
- Current test file: `dfab565e9551e3ab0a0992b0ab55a3a2c732b50f92c47836913355bb853fc5bf`
- Post equals current: YES
- Pre differs from post: YES

## Patch

- Path: `patches/validation-order-semantic-closure.patch`
- Size: 6319 bytes
- Lines: 121
- Unified diff markers: `---` YES, `+++` YES, `@@` YES

## Authoritative Runner

- Passed: 459
- Failed: 16
- Unexpected failures: 0
- Remaining FAIL lines: 16 (all pre-existing STALL_SUMMARY, classification, and Phase 10K.5)

## Decision

- Production unchanged: YES
- Semantic closure implementation slice accepted: YES
- Ready for next Phase 10K.6 slice: YES
