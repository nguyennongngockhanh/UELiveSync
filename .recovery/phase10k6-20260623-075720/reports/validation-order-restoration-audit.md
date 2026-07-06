# Validation-order Restoration Audit

**Timestamp:** 2026-06-23T14:53:36+07:00

## Exact Source Positions (byte offsets in LiveSyncFBXImporter.cpp)

| Element | Position | Verified context |
|---------|----------|-----------------|
| ValidateVersion | 41436 | `ValidateVersion(Request.Version, ...)` inside request_parse |
| FbxPath start | 41553 | `FStringFromFixedAnsi(Request.FbxPath,` inside request_parse |
| FbxPath end | 41648 | closing of FbxPath invocation |
| request_parse close | 41654 | closing `}` of request_parse block |
| path_validation decl | 41710 | `FFbxScopePhase PathPhase(` with TEXT("path_validation") |
| ValidatePathSecurity | 41977 | `ValidatePathSecurity(Request)` inside path_validation |
| path_validation close | 42077 | closing `}` of path_validation block |
| ObjectName conversion | 42191 | `FStringFromFixedAnsi(Request.ObjectName,` after path_validation |

## Ordering Chain

ValidateVersion < FbxPath start < FbxPath end < request_parse close
< path_validation decl < ValidatePathSecurity < path_validation close
< ObjectName conversion

All verified: 41436 < 41553 < 41648 < 41654 < 41710 < 41977 < 42077 < 42191 — **CORRECT**

## request_parse
- declaration count: 1
- complete declaration count: 1
- ValidateVersion inside: YES
- FbxPath extraction inside: YES
- ValidateVersion before FbxPath: YES (41436 < 41553)
- exact ordering: PASS

## path_validation
- declaration count: 1
- complete declaration count: 1
- ValidatePathSecurity inside: YES (41710 < 41977 < 42077)
- exact ordering: PASS

## Semantic source guards
- ValidateVersion count: 1
- FbxPath conversion count: 1
- ValidatePathSecurity count: 1
- ObjectName conversion count: >= 1
- pre-version ObjectName absent: YES (no ObjectName before 41436)
- ObjectName absent from request_parse: YES
- ObjectName absent from path_validation: YES
- ObjectName after path_validation close: YES (42077 < 42191)
- **PASS**

## Session
- request_parse chain: declaration_end < ValidateVersion < fbx_start < fbx_end < rp_close < ValidatePathSecurity
- direct evaluator inversions: all 9 relations tested
- RP-C valid fixture: PASS (ValidateVersion before FbxPath)
- FbxPath-before-ValidateVersion invalid: PASS
- RP-C-inv: PASS
- semantic call-count assertions: PASS
- lexical/prerequisite hardening: all PASS
- ObjectName-after-path_validation: all 5 new assertions PASS
