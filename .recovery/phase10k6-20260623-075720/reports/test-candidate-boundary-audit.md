# Candidate Boundary Audit — Phase 10K.6 Final

## Scanner

_scan_for_phase_name_in_span uses arg_state (0=expecting, 1=in expr, 2=after op):

- Stops at arg_state==1 + IDENTIFIER( → blocks UE_LOG after broken constructor
- Enters nested calls at arg_state==0/2
- Stops at depth==0, ';', '{', '}', next FFbxScopePhase, end_pos

## Fixture Evidence

| Fixture | match_count | declaration_end | invocation_complete |
|---------|-------------|-----------------|---------------------|
| RP-absent-decl | 0 | -1 | False |
| RP-incomplete | 1 | -1 | False |
| RP-FA (actual) | 1 | -1 | False |
| RP-LD (fixed) | 1 | >=0 | True |
| RP-FB (// comment) | 0 | -1 | False |
| RP-FC (/* */ comment) | 0 | -1 | False |
| RP-FD (unrelated string) | 0 | -1 | False |
| RP-FE (other_phase + comment) | 0 | -1 | False |
| RP-FF (UE_LOG) | 0 | -1 | False |
| PV-incomplete | 1 | -1 | False |
| PV-LC (// comment) | 0 | -1 | False |
| PV-BC (/* */ comment) | 0 | -1 | False |
| PV-US (unrelated string) | 0 | -1 | False |
| PV-FF (UE_LOG) | 0 | -1 | False |

## Failure Classification (40 total)

All 40 are expected production/source failures. Zero unexpected.
