# Final Evidence Audit — Phase 10K.6
Timestamp: 2026-06-23T14:23:03+07:00

## Exact Declaration Selection

| Fixture | broken_pos | valid_pos | selected | ==valid_pos | >broken_pos |
|---------|-----------|-----------|----------|-------------|-------------|
| RP-LD   | 77        | 204       | 204      | True        | True        |
| RP-LC   | 77        | 204       | —        | True        | True        |
| RP-BC   | 77        | 204       | —        | True        | True        |
| RP-SL   | 77        | 204       | —        | True        | True        |

All exact position assertions confirmed: selected == valid_pos, selected > broken_pos.

## Direct end_pos Fixtures

| Fixture                    | Expected | Result |
|----------------------------|----------|--------|
| RP-cutoff (excludes TEXT)  | False    | True   |
| RP-full (includes TEXT)    | True     | True   |
| PV-cutoff (excludes TEXT)  | False    | True   |
| PV-full (includes TEXT)    | True     | True   |

All direct scanner end_pos assertions confirmed.

## Failure Classification (40 total)

### path_validation production (10)
- Production: 'path_validation' phase declaration uniqueness
- Production: 'path_validation' total declaration count = 0
- Production: 'path_validation' complete declaration count = 0
- Production: 'path_validation' uses EFbxPhaseKind::Exclusive
- Production: 'path_validation' supplies &PhaseDurations
- Production: 'path_validation' declared in standalone dedicated block
- Production: 'path_validation' block bounds inside HandleImport
- Production: 'path_validation' decl before ValidatePathSecurity
- Production: 'path_validation' block encloses ValidatePathSecurity
- Production: 'path_validation' exact ordering chain

### request_parse production (13)
- Production: 'request_parse' phase declaration uniqueness
- Production: 'request_parse' total declaration count = 0
- Production: 'request_parse' complete declaration count = 0
- Production: 'request_parse' uses EFbxPhaseKind::Exclusive
- Production: 'request_parse' supplies &PhaseDurations
- Production: 'request_parse' declared in standalone dedicated block
- Production: 'request_parse' block bounds inside HandleImport
- Production: 'request_parse' decl before ValidateVersion
- Production: 'request_parse' block encloses ValidateVersion
- Production: 'request_parse' exact ordering chain
- Production: 'request_parse' block encloses bounded FbxPath extraction
- Production: 'request_parse' block closes before ValidatePathSecurity
- Production: ValidatePathSecurity outside 'request_parse' block

### STALL_SUMMARY (10)
- STALL_SUMMARY log exists
- STALL_SUMMARY includes transactionId
- STALL_SUMMARY includes totalMs
- STALL_SUMMARY includes measuredExclusiveMs
- STALL_SUMMARY includes coveragePercent
- STALL_SUMMARY includes largestPhase
- STALL_SUMMARY includes largestPhaseMs
- STALL_SUMMARY includes unattributedMs
- STALL_SUMMARY includes classification
- STALL_SUMMARY UE_LOG marker exists

### source contract (2)
- Source: coveragePercent calculation exists
- Source: timing validity check exists

### classification contract (2)
- Classification uses TEXT("exclusive") and TEXT("SINGLE")
- Classification only computed when timing is valid

### Phase 10K.5 (2)
- Phase 10K.5: FBX transaction timing markers present
- Phase 10K.5: STALL_SUMMARY present

### PHASE_END marker (1)
- Exclusive phase 'path_validation' has PHASE_END

Total: 10 + 13 + 10 + 2 + 2 + 2 + 1 = 40 ✓
