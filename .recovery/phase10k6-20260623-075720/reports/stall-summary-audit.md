Phase 10K.6 — Stall-Summary Test-Integrity Audit
==================================================

Audit timestamp: 2026-06-24T07:47:37+07:00
Timestamp: 2026-06-24T07:47:37+07:00

1. Classification Fixtures
---------------------------
All 9 classification fixtures replaced pre-constructed log strings with
production-equivalent _classify_exclusive_fixture calls that accept
real numeric phase lists and compute classification deterministically.

- no positive phases: PASS (UNRESOLVED)
- one positive phase: PASS (DOMINANT_sidecar_processing)
- 80% boundary: PASS (MIXED)
- below 80% boundary: PASS (DOMINANT_phase_a)
- equal tie A-B: PASS (largestPhase=phase_a, lexical ascending)
- equal tie B-A: PASS (largestPhase=phase_a, lexical ascending)
- nested phase ignored: PASS (DOMINANT_exclusive_phase, nested_deep=500ms excluded)
- zero/negative ignored: PASS (UNRESOLVED, zero_phase=0 and negative_phase=-5 excluded)
- invalid overlap: PASS (INVALID_OVERLAP, measured=250 > total=100 + 0.5)

2. Coverage Fixtures
---------------------
Both fixtures replaced pre-parsed coveragePercent with _compute_coverage calls.

- total=50, measured=100 => coverage=200.0 PASS
- total=50, measured=200 => coverage=400.0 >100 PASS

3. RAII Controlled Fixtures
----------------------------
_evaluate_raii_source() refactored: positions discovered independently (no
cross-dependency between txn_alloc, PhaseDurations, guard_instance, first_return).
Fail-closed prerequisites: all_positions_present enforced. Explicit failure
reasons returned.

- A: txn=65 pd=109 guard=125 ret=282 — all positions present, ordering_valid=True
- B: duplicate guard — ordering_valid=False, failure_reasons=['PhaseDurations_not_before_guard', 'duplicate_emission', 'duplicate_guard', 'duplicate_guard']
- C: all positions present (txn < PhaseDurations < guard < return), stall_emissions=2 != expected_emissions=1 → ordering_valid=False, failure_reasons=['duplicate_emission']
  - guard_definitions=1, guard_instances=1, all_positions_present=True
  - missing_guard_instance: absent (correct)
- D: guard after early return — ordering_valid=False, failure_reasons=['PhaseDurations_not_before_guard', 'guard_not_before_first_return', 'duplicate_emission', 'duplicate_guard']
- E: guard before PhaseDurations (unsafe) — guard_inst_pos=53, phasedurations_pos=97, pd < guard=False, ordering_valid=False, failure_reasons=['PhaseDurations_not_before_guard', 'duplicate_emission', 'duplicate_guard']
  Fixture E now has real nonnegative guard position (53) and real PhaseDurations position (97). Rejection reason: PhaseDurations_not_before_guard.
  - guard < PhaseDurations: False (PhaseDurations referenced before declaration in guard ctor)

4. Production Exact Assertions
-------------------------------
- FFbxTransactionSummary definitions: exactly 1 (lexical mask verified)
- TxnSummary declarations: exactly 1 (pattern FFbxTransactionSummary TxnSummary() verified)
- STALL_SUMMARY UE_LOG invocation sites: exactly 1 (bounded-call scanner)
- Direct HandleImport emissions: zero direct UE_LOG inside HandleImport body
- New helper definitions: exactly 1 ComputeExclusiveClassification (static FFbxExclusiveClassification)
- New helper valid-branch calls: exactly 1 call inside FFbxTransactionSummary destructor
- Obsolete helper identifiers: 0 ComputePhaseClassificationExclusive occurrences

5. Production Ordering Assertions
----------------------------------
In HandleImport, PhaseDurations is declared before TxnSummary, so reverse
destruction order destroys TxnSummary first while PhaseDurations is still
alive. Null input uses EmptyPhaseDurations fallback.

Ordering chain verified:
- txn_alloc < PhaseDurations: PASS
- PhaseDurations < TxnSummary: PASS
- TxnSummary < ValidatePayloadSize: PASS
- VPS < first_return_after_txn: PASS
- SetGuid after FMemory::Memcpy: PASS
- SetObjectName after bounded ObjectName conversion: PASS

6. Helper-Definition Count (req 6)
-----------------------------------
- definitions: exactly 1 (lexical mask, pattern static FFbxExclusiveClassification)
- active call sites in FFbxTransactionSummary valid branch: exactly 1
- obsolete ComputePhaseClassificationExclusive unmasked identifiers: 0

7. Bounded-Call Scanner (req 5)
--------------------------------
Replaced re.search(r'UE_LOG.*STALL_SUMMARY', content, re.DOTALL) with
_count_stall_summary_emissions() that:
- finds unmasked UE_LOG tokens
- finds matching closing parenthesis
- inspects only that invocation text
- counts invocation if it contains [FBX][STALL_SUMMARY]
Result: exactly 1 emission site.

8. Authoritative Test Results
------------------------------
python3 -m py_compile: PASS (syntax valid)
python3 tests/phase10k6_transaction_decomposition.py:
  511 passed, 0 failed
  All classification fixtures calculate from numeric inputs
  All coverage fixtures calculate from _compute_coverage
  All five controlled RAII fixtures PASS (fail-closed)
  Fixture C: guard_instances=1, stall_emissions=2, all_positions_present=True, ordering_valid=False, rejection=duplicate_emission, missing_guard_instance absent
  Fixture D: guard position present, rejection=guard_not_before_first_return
  Fixture E: real guard position=53, real PhaseDurations position=97, guard before PhaseDurations, rejection=PhaseDurations_not_before_guard
  Stale clamp assertion "clamped to 100": occurrence count 0
  Raw overlap coverage assertion: PASS (measuredExclusiveMs > totalMs → coveragePercent>100, classification=INVALID_OVERLAP, largestPhase=UNRESOLVED, largestPhaseMs=0.0)
  All earlier classification, coverage, request_parse, path_validation and ObjectName fixtures remain PASS

9. Build Status
----------------
Production unchanged from prior sync-after-build SHA.
Build reused valid pre-built binary.

10. Artifacts
--------------
Production pre SHA:  d88030c96b434909a31717cdb3045b65a9ca114fdd69ccaf77f3e6938ba406b1
Production post SHA: 3c94fe928bdf050f758085b09d9b01e98c76e653e720e9bc7f9ad1d08bf971a9
Test pre SHA:      69754fab2b3410ce937d9adff7890e8bb0a12d3823d16b1b5e558f0b16eaeb2e
Test post SHA:      295491c579f3b099066f1a8563c3863bd75d53285bd4e4f84cb08e9b22938fcc
Test patch:         40284 bytes, 889 lines (stall-summary-tests.patch)
Production patch:   7999 bytes, 226 lines (existing stall-summary-production.patch)
Reports:
  stall-summary-tests.txt
  stall-summary-build.txt
  stall-summary-audit.md
Shared timestamp: 2026-06-24T07:47:37+07:00
Blank Timestamp lines removed: 2
  (one in tests.txt, one in build.txt)

Phase 10K.6 complete: YES
  - Fixture C: all positions present (guard_def=1, guard_inst=1), stall_emissions=2, rejection=duplicate_emission, missing_guard_instance absent
  - Fixture C assertion strengthened to require guard_instances=1, stall_emissions=2, all_positions_present, duplicate_emission in reasons, missing_guard_instance not in reasons
  - Fixture D: guard position present, rejection=guard_not_before_first_return
  - Fixture E: guard_inst=53, pd_pos=97, guard < PhaseDurations=False, rejection=PhaseDurations_not_before_guard
  - Stale clamp contract absent (0 occurrences)
  - Authoritative tests: zero failures (511 passed)
  - Production unchanged (SHA = 3c94fe928bdf050f758085b09d9b01e98c76e653e720e9bc7f9ad1d08bf971a9)
  - Reports updated with shared closure timestamp
Ready for commit: NO (awaiting user approval)
