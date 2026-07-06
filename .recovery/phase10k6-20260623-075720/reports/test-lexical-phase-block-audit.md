# Lexical Phase Block Restoration Audit

## Infrastructure changes

### New helpers
- `_find_lexical_brace_pair(content, mask, start)` — balanced brace scan ignoring masked
- `_find_handleimport_full_bounds(content, mask)` → (handle_open, handle_close)
- `_is_standalone_block(content, mask, open_pos)` — checks:
  - `)` → rejected (if/while/for/switch/catch/lambda/function)
  - `}` → accepted
  - `;` → accepted
  - `{` → accepted
  - `:` → rejected (case/default)
  - Keywords `if,else,for,while,do,switch,case,default,try,catch` → rejected
- `_find_unmasked_call(content, mask, name)` — first unmasked call position
- `_find_ffbxscopephase_decl(content, mask, phase_name)` — lexically-aware search
- `_extract_constructor_invocation(content, mask, match)` — balanced-paren extraction
- Removed old `_find_enclosing_scope` and `_find_handleimport_open`

### `_find_dedicated_block` upgraded
- Accepts only standalone blocks (no if/for/while/etc.)
- Requires block strictly inside HandleImport when HandleImport exists
- Falls back to standalone check when no HandleImport (test fixtures)

## Requirements Coverage

### path_validation (7 assertions, all FAIL expected)
1. Unmasked declaration exists — FAIL
2. EFbxPhaseKind::Exclusive in same invocation — FAIL
3. &PhaseDurations in same invocation — FAIL
4. Declared in standalone dedicated block — FAIL
5. Full bounds inside HandleImport — FAIL
6. Decl before ValidatePathSecurity — FAIL
7. Block encloses ValidatePathSecurity (position-based) — FAIL

### request_parse (10 assertions, all FAIL expected)
1-7 same as above (ValidateVersion)
8. Block encloses bounded FbxPath extraction (position-based) — FAIL
9. Block closes before ValidatePathSecurity — FAIL
10. ValidatePathSecurity outside block — FAIL

## Self-test fixtures (all 12 PASS)
| Fixture | Expected | Result |
|---------|----------|--------|
| A: Standalone block inside HandleImport | accepted | PASS |
| B: Decl directly in HandleImport | rejected | PASS |
| C: Decl inside if block | rejected | PASS |
| D: Decl inside for loop | rejected | PASS |
| E: Braces in comments/strings | ignored | PASS |
| F: FFbxScopePhase text in comment | ignored | PASS |
| G: Work call only in comment | masked | PASS |
| H: Two incomplete decls don't combine (1/4) | Alpha has Exclusive | PASS |
| H: Two incomplete decls don't combine (2/4) | Alpha lacks &PhaseDurations | PASS |
| H: Two incomplete decls don't combine (3/4) | Beta lacks Exclusive | PASS |
| H: Two incomplete decls don't combine (4/4) | Beta has &PhaseDurations | PASS |
| I: Nested control-flow after valid decl | accepted | PASS |
| J: Full bounds inside HandleImport | strict | PASS |

## Counts
- Passed: 182
- Failed: 34 (17 production + 17 non-production)
- Unexpected: 0
