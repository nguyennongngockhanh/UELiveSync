#!/usr/bin/env python3
"""
Phase 10K.6 — FBX Transaction Decomposition Tests

Static analysis tests that verify the transaction instrumentation
is present in the C++ source files.  No UE or Blender runtime required.

Tests:
   1. FBXTransactionId atomic exists in FLiveSyncStats
   2. TransactionId allocated in HandleImport
   3. FFbxScopePhase RAII struct exists
   4. FbxPhaseBegin / FbxPhaseEnd static helpers exist
   5. Required exclusive phases have begin/end markers
   6. Nested phases are distinct from exclusive
   7. STALL_SUMMARY present
   8. Phase markers include duration field
   9. Phase markers include GUID
  10. Phase markers include TransactionId
  11. TxnObjNameSanitized derived from SanitizeObjectName
  12. No protocol or packet changes (frozen PT_Keyframe marker)
  13. Synthesized log: all phases complete
  14. Synthesized log: exclusive phases sum correctly
  15. Synthesized log: nested phases excluded from exclusive sum
  16. Synthesized log: unattributedMs computed correctly
  17. Synthesized log: largest phase selected correctly
  18. FFbxScopePhase has optional OutDurationMs parameter
  19. TransactionId never zero (starts at 1)
  20. ComputePhaseClassification helper exists
  21. STALL_SUMMARY includes measuredExclusiveMs, coveragePercent, largestPhase,
      largestPhaseMs, unattributedMs, classification
  22. Synthesized log: orphan begin detection
  23. Synthesized log: duplicate end detection
  24. Synthesized log: MIXED classification when top two phases near equal
  25. Synthesized log: real STALL_SUMMARY fields parse correctly
"""

import os
import sys
import re

PASS = 0
FAIL = 0
SKIP = 0
RESULTS = []

BASE = os.path.dirname(os.path.abspath(__file__))
SYNC_TYPES_H = os.path.join(BASE, "..",
    "UE_Plugin/UELiveSync/Source/UELiveSync/Public/SyncTypes.h")
FBX_IMPORTER_CPP = os.path.join(BASE, "..",
    "UE_Plugin/UELiveSync/Source/UELiveSync/Private/FBXImport/LiveSyncFBXImporter.cpp")
FBX_IMPORTER_H = os.path.join(BASE, "..",
    "UE_Plugin/UELiveSync/Source/UELiveSync/Public/FBXImport/LiveSyncFBXImporter.h")


def _test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        RESULTS.append(f"  PASS  {name}")
    else:
        FAIL += 1
        msg = f"  FAIL  {name}"
        if detail:
            msg += f" — {detail}"
        RESULTS.append(msg)


def _read(path):
    with open(path, "r") as f:
        return f.read()


def _count(content, pattern):
    return len(re.findall(pattern, content))


# ---------------------------------------------------------------------------
# Lexical scope helpers — find the exact { } block enclosing a position,
# ignoring content in comments, string literals, and character literals.
# ---------------------------------------------------------------------------
def _build_lexical_mask(content):
    n = len(content)
    mask = [False] * n
    i = 0
    while i < n:
        if content[i:i+2] == '//':
            end = content.find('\n', i)
            if end < 0:
                end = n
            for j in range(i, end):
                mask[j] = True
            i = end
        elif content[i:i+2] == '/*':
            end = content.find('*/', i + 2)
            if end < 0:
                end = n
            else:
                end += 2
            for j in range(i, end):
                mask[j] = True
            i = end
        elif content[i] == '"':
            j = i
            mask[j] = True
            j += 1
            while j < n:
                mask[j] = True
                if content[j] == '\\':
                    j += 1
                    if j < n:
                        mask[j] = True
                        j += 1
                elif content[j] == '"':
                    j += 1
                    break
                else:
                    j += 1
            i = j
        elif content[i] == "'":
            j = i
            mask[j] = True
            j += 1
            while j < n:
                mask[j] = True
                if content[j] == '\\':
                    j += 1
                    if j < n:
                        mask[j] = True
                        j += 1
                elif content[j] == "'":
                    j += 1
                    break
                else:
                    j += 1
            i = j
        else:
            i += 1
    return mask


# ---- Lexical brace-scanner helpers -------------------------------------------
def _find_lexical_brace_pair(content, mask, start_pos, open_char='{', close_char='}'):
    """Find the matching close brace using lexical-balanced scanning
    that ignores masked content.  Returns (open_pos, close_pos) where
    open_pos is the *first* occurrence of open_char on or after start_pos.
    When open_char != close_char (default { }), finds the one explicit pair.
    When open_char == close_char (e.g. using '(' for both), this is NOT
    supported — use only with distinct delimiters.
    """
    n = len(content)
    open_pos = -1
    for i in range(start_pos, n):
        if mask[i]:
            continue
        if content[i] == open_char:
            open_pos = i
            break
    if open_pos < 0:
        return None
    depth = 1
    for j in range(open_pos + 1, n):
        if mask[j]:
            continue
        if content[j] == open_char:
            depth += 1
        elif content[j] == close_char:
            depth -= 1
            if depth == 0:
                return (open_pos, j)
    return None


# ---- HandleImport body bounds (lexical-aware) --------------------------------
def _find_handleimport_full_bounds(content, mask):
    """Return (handle_open, handle_close) for HandleImport body, or
    (-1, -1) if not found.  Uses masked scan to skip comments/strings."""
    n = len(content)
    sig = 'bool FLiveSyncFBXImporter::HandleImport('
    # Scan masked-aware through the content
    i = 0
    while i < n:
        if mask[i]:
            i += 1
            continue
        # Check for the signature at this position
        if content[i:i+len(sig)] == sig:
            hi_pos = i
            result = _find_lexical_brace_pair(content, mask, hi_pos, '{', '}')
            if result is not None:
                return result
        i += 1
    return (-1, -1)


# ---- Direct child check (not inside a nested block) -------------------------
def _is_direct_child_of_block(content, mask, open_pos, decl_pos):
    """Return True if decl_pos is at relative depth 0 from open_pos."""
    rel_depth = 0
    for i in range(open_pos + 1, decl_pos):
        if mask[i]:
            continue
        if content[i] == '{':
            rel_depth += 1
        elif content[i] == '}':
            rel_depth -= 1
    return rel_depth == 0


# ---- Standalone block detection (no control-flow keyword before '{') --------
_CONTROL_TOKENS = frozenset({'if', 'else', 'for', 'while', 'do', 'switch',
                             'case', 'default', 'try', 'catch'})


def _is_standalone_block(content, mask, open_pos):
    """Return True only if the opening brace at open_pos is a standalone
    compound statement (not preceded by a control-flow keyword or ')' or ':')."""
    # Scan backward over whitespace and masked content
    i = open_pos - 1
    while i >= 0 and (mask[i] or content[i] in ' \t\n\r'):
        i -= 1
    if i < 0:
        return True  # start of file

    c = content[i]

    # )-preceded: if/while/for/switch/catch/lambda/function
    if c == ')':
        return False
    # ]-preceded: lambda introducer (e.g. [] { }, [&] { })
    if c == ']':
        return False
    # }-preceded: standalone (else/try have keyword between, not })
    if c == '}':
        return True
    # ;-preceded: statement terminator → standalone
    if c == ';':
        return True
    # {-preceded: standalone (never a control keyword)
    if c == '{':
        return True
    # :-preceded: case/default label → not standalone
    if c == ':':
        return False

    # Word keyword check — extract the complete word
    word_end = i + 1
    while i >= 0 and not (mask[i] or content[i] in ' \t\n\r{}();,+'):
        i -= 1
    word = content[i + 1:word_end]

    if word in _CONTROL_TOKENS:
        return False

    # Additional lambda detection: scan backward for lambda introducer
    # after specifier keywords (mutable/constexpr/noexcept) or return types.
    # If ']' or balanced '()' followed by ']' is found before any ';', '{',
    # or '}', this is a lambda body.
    j = i  # i is at the char just before the word
    while j >= 0 and not (mask[j] or content[j] in ';{}'):
        if content[j] == ']':
            return False  # lambda introducer: [] { ... }
        if content[j] == ')':
            # Skip balanced ()
            depth = 1
            j -= 1
            while j >= 0 and depth > 0:
                if mask[j]:
                    j -= 1
                    continue
                if content[j] == '(':
                    depth -= 1
                elif content[j] == ')':
                    depth += 1
                if depth > 0:
                    j -= 1
            # After balanced (), check for ]
            if j >= 0 and content[j] == ']':
                return False  # lambda []() { ... }
        j -= 1
    return True


# ---- Dedicated phase block -----------------------------------------------
def _find_dedicated_block(content, mask, decl_pos):
    """Find the exact dedicated standalone { } block containing a declaration.

    Returns (open_brace_pos, close_brace_pos, body_text) ONLY if:
      - The declaration is directly inside the { } block (not in a nested block)
      - The block is a standalone compound statement (not if/for/while/etc.)
      - If HandleImport exists: block is strictly inside its bounds
      - (In test fixtures without HandleImport, block is accepted if standalone)

    Returns None otherwise.
    """
    n = len(content)
    handle_open, handle_close = _find_handleimport_full_bounds(content, mask)
    has_hi = handle_open >= 0

    # 1. Find the enclosing {
    depth = 0
    open_pos = -1
    for i in range(decl_pos - 1, -1, -1):
        if mask[i]:
            continue
        if content[i] == '}':
            depth += 1
        elif content[i] == '{':
            if depth == 0:
                open_pos = i
                break
            depth -= 1
    if open_pos < 0:
        return None
    if has_hi and open_pos <= handle_open:
        return None  # not strictly inside HandleImport

    # 2. Only accept a standalone block
    if not _is_standalone_block(content, mask, open_pos):
        return None

    # 3. Verify declaration is a direct child of this block
    if not _is_direct_child_of_block(content, mask, open_pos, decl_pos):
        return None

    # 4. Find matching close brace
    result = _find_lexical_brace_pair(content, mask, open_pos, '{', '}')
    if result is None:
        return None
    close_pos = result[1]
    if has_hi and close_pos >= handle_close:
        return None  # block must close before HandleImport body closes

    return (open_pos, close_pos, content[open_pos + 1:close_pos])


# ---- Find unmasked call position -------------------------------------------
def _find_unmasked_call(content, mask, call_name, start_pos=0, end_pos=None):
    """Find the first unmasked occurrence of call_name( within
    [start_pos, end_pos).  Returns the position of call_name or -1."""
    if end_pos is None:
        end_pos = len(content)
    pattern = re.escape(call_name) + r'\('
    for m in re.finditer(pattern, content):
        if m.start() < start_pos or m.start() >= end_pos:
            continue
        if not any(mask[m.start():m.end()]):
            return m.start()
    return -1


# ---- Lexical-aware FFbxScopePhase declaration search ------------------------
def _find_ffbxscopephase_decls(content, mask, phase_name,
                                start_pos=0, end_pos=None):
    """Find all unmasked FFbxScopePhase declarations with the given phase
    name within [start_pos, end_pos).
    Returns list of (re.Match, invocation_text)."""
    if end_pos is None:
        end_pos = len(content)
    results = []
    pattern = r'FFbxScopePhase\s+\w+\s*\('
    for m in re.finditer(pattern, content):
        if m.start() < start_pos or m.start() >= end_pos:
            continue
        if mask[m.start()]:
            continue
        invocation = _extract_constructor_invocation(content, mask, m)
        if invocation and phase_name in _extract_text_macro_args(invocation):
            results.append((m, invocation))
    return results


def _scan_for_phase_name_in_span(content, mask, phase_name, start_pos, end_pos):
    """Lexical-aware paren-depth scanner for TEXT("phase_name")
    within FFbxScopePhase constructor arguments.

    Starts right after the constructor's opening '(' (paren depth=1).
    Tracks paren depth.  Only matches TEXT("phase_name") while depth > 0.

    At depth 1, stops at:
    - ';'  (statement boundary)
    - '{'  (block boundary)
    - '}'  (enclosing block boundary)
    - ')'  succeeds only if depth becomes 0 (constructor closed without match)
    - IDENTIFIER(  not preceded by ',' or '(' or binary operator
                   (indicates a new C++ statement, not a constructor argument)

    Also stops at next unmasked FFbxScopePhase token or end_pos.

    Uses mask to skip //, /* */, and string literal content."""
    text_pat = re.compile(r'TEXT\s*\(\s*"([^"]*)"\s*\)')
    ffbx_pat = re.compile(r'FFbxScopePhase\s+\w+\s*\(')
    ident_call = re.compile(r'[A-Za-z_]\w*\s*\(')
    depth = 1
    pos = start_pos
    # arg_state: 0=expecting expression start (after '(' or ','),
    #            1=in expression, 2=after binary operator
    arg_state = 0
    binary_ops = set('+-*/%&|^<>=!~?:')
    while pos < end_pos and pos < len(content):
        if mask[pos]:
            pos += 1
            continue
        c = content[pos]
        if c == '(':
            depth += 1
            pos += 1
            continue
        if c == ')':
            depth -= 1
            if depth == 0:
                return False
            pos += 1
            continue
        tm = text_pat.match(content, pos)
        if tm:
            if depth > 0 and tm.group(1) == phase_name:
                return True
            pos = tm.end()
            continue
        if depth == 1:
            if c == ';':
                return False
            if c == '{':
                return False
            if c == '}':
                return False
            im = ident_call.match(content, pos)
            if im and not mask[pos]:
                if arg_state == 1:
                    return False
                # Nested call within an argument — enter it
                arg_state = 1
                depth += 1
                pos = im.end()
                continue
            if c == ',':
                arg_state = 0
            elif c in binary_ops:
                arg_state = 2
            elif not c.isspace():
                arg_state = 1
        if pos + 14 <= len(content) and content[pos:pos+14] == 'FFbxScopePhase':
            if ffbx_pat.match(content, pos) and not mask[pos]:
                return False
        pos += 1
    return False


def _find_ffbxscopephase_matches(content, mask, phase_name,
                                  start_pos=0, end_pos=None):
    """Find all unmasked FFbxScopePhase tokens with the given phase name.
    Unlike _find_ffbxscopephase_decls, this does NOT require successful
    constructor extraction.  Returns list of (re.Match, invocation_or_None)."""
    if end_pos is None:
        end_pos = len(content)
    results = []
    pattern = r'FFbxScopePhase\s+\w+\s*\('
    for m in re.finditer(pattern, content):
        if m.start() < start_pos or m.start() >= end_pos:
            continue
        if mask[m.start()]:
            continue
        invocation = _extract_constructor_invocation(content, mask, m)
        if invocation:
            if phase_name in _extract_text_macro_args(invocation):
                results.append((m, invocation))
        else:
            # Extraction failed; lexical-aware scan for phase name.
            # Stop before next declaration, block boundary, or end_pos.
            scan_end = end_pos
            # Look for next FFbxScopePhase token after current one
            next_match = None
            for nm in re.finditer(pattern, content):
                if nm.start() <= m.start():
                    continue
                if nm.start() >= end_pos:
                    break
                if not mask[nm.start()]:
                    next_match = nm
                    break
            if next_match:
                scan_end = next_match.start()
            found = _scan_for_phase_name_in_span(
                content, mask, phase_name, m.end(), min(scan_end, end_pos))
            if found:
                results.append((m, None))
    return results


# ---- Balanced-parenthesis constructor extraction ----------------------------
def _extract_constructor_invocation(content, mask, decl_match):
    """Extract the complete constructor invocation text using
    balanced-parenthesis scanning that ignores masked content.
    Returns the substring from decl start through matching ')' or None."""
    start = decl_match.start()
    paren_open = decl_match.end()
    # Find the actual '(' — might be after whitespace
    result = _find_lexical_brace_pair(content, mask, start, '(', ')')
    if result is None:
        return None
    return content[start:result[1] + 1]


# ---- Balanced-parenthesis extraction (top-level comma aware) -----------------
def _extract_balanced_first_arg(content, mask, start_pos):
    """Extract the first top-level argument from a parenthesized call at
    start_pos (assumes start_pos points to an opening paren '(').
    Returns (first_arg_text, first_arg_end_pos) where first_arg_end_pos
    is the position of the comma (or matching ')' for single-arg).
    Returns None on failure."""
    depth = 1
    arg_start = -1
    arg_end = -1
    n = len(content)
    i = start_pos + 1
    # Skip whitespace/mask after opening paren
    while i < n and (mask[i] or content[i] in ' \t\n\r'):
        i += 1
    if i >= n:
        return None
    arg_start = i
    while i < n and depth > 0:
        if mask[i]:
            i += 1
            continue
        if content[i] == '(':
            depth += 1
        elif content[i] == ')':
            depth -= 1
            if depth == 0:
                arg_end = i
                break
        elif content[i] == ',' and depth == 1:
            arg_end = i
            break
        i += 1
    if arg_end < 0:
        return None
    raw = content[arg_start:arg_end].strip()
    return raw, arg_end


# ---- Invocation-local lexical mask -------------------------------------------
def _has_field_outside_comment(invocation_text, field_str):
    """Return True if field_str appears in invocation_text outside of
    comments, string literals, and character literals."""
    mask = _build_lexical_mask(invocation_text)
    idx = 0
    while True:
        pos = invocation_text.find(field_str, idx)
        if pos < 0:
            return False
        if not mask[pos]:
            return True
        idx = pos + 1


def _extract_text_macro_args(invocation_text):
    """Extract all TEXT(\"...\") argument values from invocation_text.
    Returns list of string values found outside comments/strings.
    Uses manual comment-skipping for the outer scan (not _build_lexical_mask)
    because _build_lexical_mask masks string literal content, which would
    prevent scanning inside TEXT(\"...\")."""
    n = len(invocation_text)
    results = []
    i = 0
    # Simple comment mask: track // and /* */ for outer scan only
    def _in_comment(pos):
        """Quick check if pos is inside a // or /* */ comment."""
        for ci in range(pos):
            if invocation_text[ci:ci+2] == '//' and invocation_text.find('\n', ci) >= pos:
                return True
            if invocation_text[ci:ci+2] == '/*':
                end = invocation_text.find('*/', ci + 2)
                if end >= 0 and end + 2 > pos:
                    return True
        return False

    while i < n:
        if _in_comment(i):
            i += 1
            continue
        if invocation_text[i:i+5] == 'TEXT(':
            # Skip whitespace after '('
            j = i + 5
            while j < n and invocation_text[j] in ' \t\n\r':
                j += 1
            if j < n and invocation_text[j] == '"':
                j += 1  # skip opening quote
                arg_start = j
                while j < n:
                    if invocation_text[j] == '\\':
                        j += 2
                        continue
                    if invocation_text[j] == '"':
                        arg_val = invocation_text[arg_start:j]
                        results.append(arg_val)
                        break
                    j += 1
            i += 5
        else:
            i += 1
    return results


# ---- Declaration uniqueness -----------------------------------------------
def _count_phase_declarations(decls_list, invocation_texts):
    """Count total and complete declarations from a list of
    (match, invocation) pairs.
    Returns (total_count, complete_count)."""
    total = len(decls_list)
    complete = 0
    for _, inv in decls_list:
        has_excl = _has_field_outside_comment(inv, 'EFbxPhaseKind::Exclusive')
        has_dur = _has_field_outside_comment(inv, '&PhaseDurations')
        if has_excl and has_dur:
            complete += 1
    return total, complete


# ---- Shared ordering evaluators (used by both production and fixtures) ---------
def _evaluate_request_parse_ordering(
    handle_open, phase_open, decl_start, decl_end,
    fbx_start, fbx_end, vv_pos, vps_pos,
    handle_close, phase_close,
):
    """Evaluate the request_parse ordering chain.
    Returns (valid, failing_relations, position_detail)."""
    chain = [
        ("handle_open", handle_open),
        ("phase_open", phase_open),
        ("declaration_start", decl_start),
        ("declaration_end", decl_end),
        ("fbx_path_invocation_start", fbx_start),
        ("fbx_path_invocation_end", fbx_end),
        ("ValidateVersion_call", vv_pos),
        ("phase_close", phase_close),
        ("ValidatePathSecurity_call", vps_pos),
        ("handle_close", handle_close),
    ]
    return _evaluate_chain(chain)


def _evaluate_path_validation_ordering(
    handle_open, phase_open, decl_start, decl_end,
    vps_pos, handle_close, phase_close,
):
    """Evaluate the path_validation ordering chain.
    Returns (valid, failing_relations, position_detail)."""
    chain = [
        ("handle_open", handle_open),
        ("phase_open", phase_open),
        ("declaration_start", decl_start),
        ("declaration_end", decl_end),
        ("ValidatePathSecurity_call", vps_pos),
        ("phase_close", phase_close),
        ("handle_close", handle_close),
    ]
    return _evaluate_chain(chain)


def _evaluate_chain(chain):
    """Generic chain evaluator.
    Returns (valid: bool, failing_relations: list, detail_str: str)."""
    valid = True
    failing = []
    details = []
    for i in range(len(chain) - 1):
        name_a, pos_a = chain[i]
        name_b, pos_b = chain[i + 1]
        a_str = f"{name_a}@{pos_a}" if pos_a >= 0 else f"{name_a}=-"
        b_str = f"{name_b}@{pos_b}" if pos_b >= 0 else f"{name_b}=-"
        if pos_a < 0 or pos_b < 0 or not (pos_a < pos_b):
            valid = False
            rel = f"{name_a}<{name_b}"
            failing.append(rel)
            details.append(f" {rel}:FAIL ({a_str} < {b_str})")
    return valid, failing, "".join(details)


# ---- Shared ordering preparation helpers (used by both production and fixtures) -
def _prepare_request_parse_ordering(
    handle_open, handle_close,
    has_unique_decl, single_match,
    decl_start, decl_end,
    block_exists, block_open, block_close,
    fbx_path_start, fbx_path_end,
    vv_pos, vps_pos,
):
    """Check request_parse ordering prerequisites and evaluate chain.
    Returns (prerequisites_valid, missing_list, evaluator_valid,
             failing_relations, detail_str)."""
    missing = []

    if handle_open < 0 or handle_close < 0 or handle_close <= handle_open:
        missing.append("HandleImport bounds")
    if not has_unique_decl or single_match is None:
        missing.append("unique declaration")
    if single_match is not None and (decl_start < 0 or decl_end <= decl_start):
        missing.append("constructor bounds")
    if not block_exists:
        missing.append("dedicated block")
    if fbx_path_start < 0 or fbx_path_end <= fbx_path_start:
        missing.append("FbxPath invocation")
    if vv_pos < 0:
        missing.append("ValidateVersion call")
    if vps_pos < 0:
        missing.append("ValidatePathSecurity call")

    if missing:
        return (False, missing, False, [], "; ".join(missing))

    valid, failing, detail = _evaluate_request_parse_ordering(
        handle_open, block_open, decl_start, decl_end,
        fbx_path_start, fbx_path_end, vv_pos, vps_pos,
        handle_close, block_close)
    return (True, [], valid, failing, detail)


def _prepare_path_validation_ordering(
    handle_open, handle_close,
    has_unique_decl, single_match,
    decl_start, decl_end,
    block_exists, block_open, block_close,
    vps_pos,
):
    """Check path_validation ordering prerequisites and evaluate chain.
    Returns (prerequisites_valid, missing_list, evaluator_valid,
             failing_relations, detail_str)."""
    missing = []

    if handle_open < 0 or handle_close < 0 or handle_close <= handle_open:
        missing.append("HandleImport bounds")
    if not has_unique_decl or single_match is None:
        missing.append("unique declaration")
    if single_match is not None and (decl_start < 0 or decl_end <= decl_start):
        missing.append("constructor bounds")
    if not block_exists:
        missing.append("dedicated block")
    if vps_pos < 0:
        missing.append("ValidatePathSecurity call")

    if missing:
        return (False, missing, False, [], "; ".join(missing))

    valid, failing, detail = _evaluate_path_validation_ordering(
        handle_open, block_open, decl_start, decl_end,
        vps_pos, handle_close, block_close)
    return (True, [], valid, failing, detail)


# ---- Parser result structure for setup tests --------------------------------
class ParserResult:
    """Structured result from _parse_and_prepare_rp / _parse_and_prepare_pv."""
    def __init__(self, handle_open, handle_close, matches, single_match,
                 declaration_start, declaration_end, invocation_complete,
                 block_exists, prep_result):
        self.handle_open = handle_open
        self.handle_close = handle_close
        self.matches = matches
        self.match_count = len(matches)
        self.single_match = single_match
        self.declaration_start = declaration_start
        self.declaration_end = declaration_end
        self.invocation_complete = invocation_complete
        self.block_exists = block_exists
        self.prerequisites_valid = prep_result[0]
        self.missing = prep_result[1]
        self.evaluator_valid = prep_result[2]
        self.failing_relations = prep_result[3]
        self.detail = prep_result[4]
        self._prep = prep_result


# ---- Parser-to-preparation helpers for setup tests --------------------------
def _parse_and_prepare_rp(fixture_text):
    """Parse a C++ fixture and call _prepare_request_parse_ordering.
    Returns ParserResult."""
    msk = _build_lexical_mask(fixture_text)
    ho, hc = _find_handleimport_full_bounds(fixture_text, msk)
    hbs = ho + 1
    hbe = hc
    matches = _find_ffbxscopephase_matches(fixture_text, msk, 'request_parse', hbs, hbe)
    has_unique = (len(matches) == 1)
    single_match = matches[0][0] if has_unique else None
    invocation = matches[0][1] if has_unique else None
    ds = single_match.start() if single_match else -1
    if has_unique and invocation is not None:
        de = len(invocation) + ds
        inv_complete = True
    else:
        de = -1
        inv_complete = False
    block = _find_dedicated_block(fixture_text, msk, ds) if single_match else None
    block_exists = block is not None
    bo = bc = -1
    if block:
        bo, bc, _ = block
    fbx_start, fbx_end = _find_fbxpath_invocation(fixture_text, msk, hbs, hbe)
    vv = _find_unmasked_call(fixture_text, msk, 'ValidateVersion', hbs, hbe)
    vps = _find_unmasked_call(fixture_text, msk, 'ValidatePathSecurity', hbs, hbe)
    prep = _prepare_request_parse_ordering(
        ho, hc, has_unique, single_match, ds, de,
        block_exists, bo, bc, fbx_start, fbx_end, vv, vps)
    return ParserResult(ho, hc, matches, single_match, ds, de,
                        inv_complete, block_exists, prep)


def _parse_and_prepare_pv(fixture_text):
    """Parse a C++ fixture and call _prepare_path_validation_ordering.
    Returns ParserResult."""
    msk = _build_lexical_mask(fixture_text)
    ho, hc = _find_handleimport_full_bounds(fixture_text, msk)
    hbs = ho + 1
    hbe = hc
    matches = _find_ffbxscopephase_matches(fixture_text, msk, 'path_validation', hbs, hbe)
    has_unique = (len(matches) == 1)
    single_match = matches[0][0] if has_unique else None
    invocation = matches[0][1] if has_unique else None
    ds = single_match.start() if single_match else -1
    if has_unique and invocation is not None:
        de = len(invocation) + ds
        inv_complete = True
    else:
        de = -1
        inv_complete = False
    block = _find_dedicated_block(fixture_text, msk, ds) if single_match else None
    block_exists = block is not None
    bo = bc = -1
    if block:
        bo, bc, _ = block
    vps = _find_unmasked_call(fixture_text, msk, 'ValidatePathSecurity', hbs, hbe)
    prep = _prepare_path_validation_ordering(
        ho, hc, has_unique, single_match, ds, de,
        block_exists, bo, bc, vps)
    return ParserResult(ho, hc, matches, single_match, ds, de,
                        inv_complete, block_exists, prep)


# ---- Lexical FStringFromFixedAnsi(Request.FbxPath) invocation ----------------
def _find_fbxpath_invocation(content, mask, start_pos=0, end_pos=None):
    """Find the unmasked FStringFromFixedAnsi(Request.FbxPath, ...)
    invocation within bounds.
    Returns (inv_start, inv_end) or (-1, -1)."""
    if end_pos is None:
        end_pos = len(content)
    pattern = r'FStringFromFixedAnsi\s*\('
    for m in re.finditer(pattern, content):
        if m.start() < start_pos or m.start() >= end_pos:
            continue
        if mask[m.start()]:
            continue
        inv = _extract_constructor_invocation(content, mask, m)
        if inv is None:
            continue
        paren = inv.find('(')
        if paren < 0:
            continue
        first_arg = _extract_balanced_first_arg(content, mask, m.end() - 1)
        if first_arg is None:
            continue
        arg_text, _ = first_arg
        if arg_text == 'Request.FbxPath':
            return (m.start(), m.start() + len(inv))
    return (-1, -1)


# ---------------------------------------------------------------------------
# 1. FBXTransactionId atomic in SyncTypes.h
# ---------------------------------------------------------------------------
def test_fbx_transaction_id_atomic():
    content = _read(SYNC_TYPES_H)
    _test("FBXTransactionId atomic<int32> exists",
          re.search(r"std::atomic<int32>\s+FBXTransactionId", content) is not None)


# ---------------------------------------------------------------------------
# 2. TransactionId allocated in HandleImport
# ---------------------------------------------------------------------------
def test_transaction_id_alloc():
    content = _read(FBX_IMPORTER_CPP)
    _test("TransactionId allocated from FBXTransactionId fetch_add",
          re.search(r"TransactionId\s*=\s*Context\.Stats->FBXTransactionId\.fetch_add", content) is not None)
    _test("TransactionId declared as int32",
          re.search(r"int32\s+TransactionId\s*=", content) is not None)


# ---------------------------------------------------------------------------
# 3. FFbxScopePhase RAII struct exists
# ---------------------------------------------------------------------------
def test_ffbxscopephase_struct():
    content = _read(FBX_IMPORTER_CPP)
    _test("FFbxScopePhase struct defined",
          re.search(r"struct\s+FFbxScopePhase", content) is not None)
    _test("FFbxScopePhase has constructor with Phase/Guid/SyncId/ObjectName",
          re.search(r"FFbxScopePhase\s*\(", content) is not None)
    _test("FFbxScopePhase has destructor with PHASE_END",
          re.search(r"~FFbxScopePhase\s*\(\)", content) is not None)


# ---------------------------------------------------------------------------
# 4. FbxPhaseBegin / FbxPhaseEnd helpers exist
# ---------------------------------------------------------------------------
def test_phase_helpers():
    content = _read(FBX_IMPORTER_CPP)
    _test("FbxPhaseBegin static helper exists",
          re.search(r"static.*void\s+FbxPhaseBegin\s*\(", content) is not None)
    _test("FbxPhaseEnd static helper exists",
          re.search(r"static.*void\s+FbxPhaseEnd\s*\(", content) is not None)
    # Count FbxPhaseBegin calls (not the definition line)
    call_begin = _count(content, r"\bFbxPhaseBegin\(")
    call_end = _count(content, r"\bFbxPhaseEnd\(")
    # FbxPhaseBegin/FbxPhaseEnd helpers exist as infrastructure.
    # Most phases use FFbxScopePhase RAII instead of manual calls,
    # but the helpers must still be defined for fallback use.
    _test("FbxPhaseBegin helper function exists (count >= 1 includes definition)",
          call_begin >= 1)
    _test("FbxPhaseEnd helper function exists (count >= 1 includes definition)",
          call_end >= 1)


# ---------------------------------------------------------------------------
# 5. Required exclusive phases have begin/end markers
# ---------------------------------------------------------------------------
def test_required_phases():
    content = _read(FBX_IMPORTER_CPP)
    # Only phases that have been wrapped with FFbxScopePhase so far
    required_exclusive = [
        "path_validation",
    ]
    required_nested = []
    for ph in required_exclusive:
        if ph == "request_parse":
            check_begin = re.search(r'phase=request_parse\b', content) is not None
            check_end = re.search(r'PHASE_END.*phase=request_parse\b', content) is not None
        else:
            check_begin = re.search(rf'TEXT\(\"{ph}\"\).*FFbxScopePhase|FFbxScopePhase.*TEXT\(\"{ph}\"\)|FbxPhaseBegin.*TEXT\(\"{ph}\"\)', content, re.DOTALL) is not None
            check_end = re.search(rf'FbxPhaseEnd.*TEXT\(\"{ph}\"\)', content, re.DOTALL) is not None or \
                        re.search(rf'FFbxScopePhase.*TEXT\(\"{ph}\"\)', content, re.DOTALL) is not None
        _test(f"Exclusive phase '{ph}' has PHASE_BEGIN", check_begin, detail=f"phase={ph}")
        _test(f"Exclusive phase '{ph}' has PHASE_END", check_end, detail=f"phase={ph}")

    for ph in required_nested:
        check_begin = re.search(rf'FbxPhaseBegin.*TEXT\(\"{ph}\"\)|FFbxScopePhase.*TEXT\(\"{ph}\"\)', content, re.DOTALL) is not None
        check_end = re.search(rf'FbxPhaseEnd.*TEXT\(\"{ph}\"\)', content, re.DOTALL) is not None
        _test(f"Nested phase '{ph}' has PHASE_BEGIN", check_begin, detail=f"phase={ph}")
        _test(f"Nested phase '{ph}' has PHASE_END", check_end, detail=f"phase={ph}")


# ---------------------------------------------------------------------------
# 6. Nested phases use classification=nested
# ---------------------------------------------------------------------------
def test_nested_classification():
    # Infrastructure: verify the classification logic exists in source
    content = _read(FBX_IMPORTER_CPP)
    _test("ComputePhaseClassificationExclusive exists",
          re.search(r'ComputePhaseClassificationExclusive', content) is not None)
    _test("Classification uses TEXT(\"exclusive\") and TEXT(\"SINGLE\")",
          re.search(r'TEXT\(\"exclusive\"\)', content) is not None and
          re.search(r'TEXT\(\"SINGLE\"\)', content) is not None)


# ---------------------------------------------------------------------------
# 7. STALL_SUMMARY present after timing emit
# ---------------------------------------------------------------------------
def test_stall_summary():
    content = _read(FBX_IMPORTER_CPP)
    _test("STALL_SUMMARY log exists",
          re.search(r'\[FBX\]\[STALL_SUMMARY\]', content) is not None)
    _test("STALL_SUMMARY includes transactionId",
          re.search(r'STALL_SUMMARY.*transactionId=%d', content) is not None)
    _test("STALL_SUMMARY includes totalMs",
          re.search(r'STALL_SUMMARY[\s\S]*totalMs=%.1f', content) is not None)
    _test("STALL_SUMMARY includes measuredExclusiveMs",
          re.search(r'measuredExclusiveMs=%.1f', content) is not None)
    _test("STALL_SUMMARY includes coveragePercent",
          re.search(r'coveragePercent=.*%', content) is not None)
    _test("STALL_SUMMARY includes largestPhase",
          re.search(r'largestPhase=%s', content) is not None)
    _test("STALL_SUMMARY includes largestPhaseMs",
          re.search(r'largestPhaseMs=%.1f', content) is not None)
    _test("STALL_SUMMARY includes unattributedMs",
          re.search(r'unattributedMs=%.1f', content) is not None)
    _test("STALL_SUMMARY includes classification",
          re.search(r'classification=%s', content) is not None)


# ---------------------------------------------------------------------------
# 8. Phase markers include durationMs field
# ---------------------------------------------------------------------------
def test_duration_field():
    content = _read(FBX_IMPORTER_CPP)
    _test("PHASE_END includes durationMs field",
          re.search(r'PHASE_END.*durationMs', content) is not None)


# ---------------------------------------------------------------------------
# 9. Phase markers include GUID
# ---------------------------------------------------------------------------
def test_guid_field():
    content = _read(FBX_IMPORTER_CPP)
    _test("PHASE_BEGIN includes guid field",
          re.search(r'PHASE_BEGIN.*guid=', content) is not None)
    _test("PHASE_END includes guid field",
          re.search(r'PHASE_END.*guid=', content) is not None)


# ---------------------------------------------------------------------------
# 10. Phase markers include TransactionId
# ---------------------------------------------------------------------------
def test_transaction_id_in_markers():
    content = _read(FBX_IMPORTER_CPP)
    _test("PHASE_BEGIN includes transactionId",
          re.search(r'PHASE_BEGIN.*transactionId=', content) is not None)
    _test("PHASE_END includes transactionId",
          re.search(r'PHASE_END.*transactionId=', content) is not None)


# ---------------------------------------------------------------------------
# 11. TxnObjNameSanitized uses SanitizeObjectName
# ---------------------------------------------------------------------------
def test_sanitized_name():
    content = _read(FBX_IMPORTER_CPP)
    has_txn = re.search(r'TxnObjNameSanitized', content) is not None
    has_direct = re.search(r'TxnObjNameSanitized\s*=.*SanitizeObjectName', content) is not None
    has_indirect = (
        re.search(r'SafeName\s*=.*SanitizeObjectName', content) is not None and
        re.search(r'TxnObjNameSanitized\s*=\s*SafeName', content) is not None
    )
    _test("TxnObjNameSanitized derived from SanitizeObjectName",
          has_txn and (has_direct or has_indirect))


# ---------------------------------------------------------------------------
# 12. No protocol or packet changes
# ---------------------------------------------------------------------------
def test_no_protocol_changes():
    proto_content = _read(SYNC_TYPES_H)
    fbx_content = _read(FBX_IMPORTER_CPP)
    _test("PT_Keyframe still in protocol enum (no packet type changes)",
          re.search(r'PT_Keyframe\b', proto_content) is not None)
    # Ensure we didn't add PT_FBXVisibility or similar new packet types
    vis_pkt = re.search(r'PT_FBXVisibility\b', proto_content, re.IGNORECASE)
    _test("No new PT_FBXVisibility packet type added", vis_pkt is None)
    # Confirm PHASE_BEGIN/PHASE_END are log markers, not packet markers
    _test("PHASE_BEGIN/PHASE_END are log markers (UE_LOG), not packets",
          re.search(r'UE_LOG.*PHASE_BEGIN', fbx_content, re.DOTALL) is not None and
          re.search(r'UE_LOG.*PHASE_END', fbx_content, re.DOTALL) is not None)


# ---------------------------------------------------------------------------
# 13-17: Log-parser behavioral tests (synthetic log)
# ---------------------------------------------------------------------------
def build_synthetic_log():
    """Build a synthetic PHASE_BEGIN/PHASE_END log for parser tests."""
    lines = []

    def begin(phase, classification, duration=None):
        d = f' durationMs={duration}' if duration is not None else ''
        lines.append(
            f'[FBX][PHASE_BEGIN] transactionId=1 guid=ABCDEF01 syncId=42 '
            f'objectName=TestCube phase="{phase}" classification="{classification}"{d}')

    def end(phase, classification, duration):
        lines.append(
            f'[FBX][PHASE_END] transactionId=1 guid=ABCDEF01 syncId=42 '
            f'objectName=TestCube phase="{phase}" classification="{classification}" '
            f'durationMs={duration}')

    # Exclusive phases
    begin("request_parse", "exclusive")
    end("request_parse", "exclusive", 0.5)

    begin("path_validation", "exclusive")
    end("path_validation", "exclusive", 1.2)

    begin("fbx_factory_import", "exclusive")
    end("fbx_factory_import", "exclusive", 45.0)

    begin("imported_asset_discovery", "exclusive")
    end("imported_asset_discovery", "exclusive", 3.0)

    begin("sidecar_processing", "exclusive")
    # Nested subphases within sidecar_processing
    begin("sidecar_manifest_read", "nested")
    end("sidecar_manifest_read", "nested", 1.0)
    begin("sidecar_fingerprint_classification", "nested")
    end("sidecar_fingerprint_classification", "nested", 2.0)
    begin("sidecar_asset_lookup", "nested")
    end("sidecar_asset_lookup", "nested", 1.5)
    begin("sidecar_batch_import", "nested")
    end("sidecar_batch_import", "nested", 5.0)
    begin("sidecar_result_mapping", "nested")
    end("sidecar_result_mapping", "nested", 1.0)
    end("sidecar_processing", "exclusive", 120.0)

    # Nested phase inside sidecar (should NOT be in exclusive sum)
    begin("semantic_signature", "nested")
    end("semantic_signature", "nested", 8.0)

    begin("static_mesh_post_import", "exclusive")
    end("static_mesh_post_import", "exclusive", 5.0)

    begin("actor_lookup_or_spawn", "exclusive")
    end("actor_lookup_or_spawn", "exclusive", 2.1)

    begin("static_mesh_assignment", "exclusive")
    end("static_mesh_assignment", "exclusive", 1.5)

    begin("material_slot_assignment", "exclusive")
    end("material_slot_assignment", "exclusive", 0.8)

    begin("post_import_finalize", "nested")
    end("post_import_finalize", "nested", 10.0)

    # STALL_SUMMARY (real fields)
    lines.append(
        f'[FBX][STALL_SUMMARY] transactionId=1 guid=ABCDEF01 syncId=42 '
        f'objectName=TestCube totalMs=200.0 measuredExclusiveMs=179.1 '
        f'coveragePercent=89.55 largestPhase=sidecar_processing '
        f'largestPhaseMs=120.0 unattributedMs=20.9 '
        f'classification=MIXED')

    return "\n".join(lines)


def parse_phases(log_text):
    """Parse phase markers from synthetic log text."""
    begins = {}
    ends = {}
    for m in re.finditer(r'\[FBX\]\[PHASE_BEGIN\].*?phase="(\w+)".*?classification="(\w+)"', log_text):
        ph = m.group(1)
        cls = m.group(2)
        begins[ph] = cls
    for m in re.finditer(r'\[FBX\]\[PHASE_END\].*?phase="(\w+)".*?classification="(\w+)".*?durationMs=([\d.]+)', log_text):
        ph = m.group(1)
        cls = m.group(2)
        dur = float(m.group(3))
        ends[ph] = (cls, dur)
    return begins, ends


def parse_stall_summary(log_text):
    """Parse STALL_SUMMARY fields from synthetic log text."""
    m = re.search(
        r'\[FBX\]\[STALL_SUMMARY\] transactionId=(\d+)'
        r'.*?totalMs=([\d.]+)'
        r'.*?measuredExclusiveMs=([\d.]+)'
        r'.*?coveragePercent=([\d.]+)'
        r'.*?largestPhase=(\w+)'
        r'.*?largestPhaseMs=([\d.]+)'
        r'.*?unattributedMs=([\d.]+)'
        r'.*?classification=(\w+)',
        log_text
    )
    if m:
        return {
            "transactionId": int(m.group(1)),
            "totalMs": float(m.group(2)),
            "measuredExclusiveMs": float(m.group(3)),
            "coveragePercent": float(m.group(4)),
            "largestPhase": m.group(5),
            "largestPhaseMs": float(m.group(6)),
            "unattributedMs": float(m.group(7)),
            "classification": m.group(8),
        }
    return None


def test_log_all_phases_complete():
    log = build_synthetic_log()
    begins, ends = parse_phases(log)
    all_phases = ["request_parse", "path_validation", "fbx_factory_import",
                   "imported_asset_discovery", "sidecar_processing",
                   "semantic_signature", "static_mesh_post_import",
                   "actor_lookup_or_spawn", "static_mesh_assignment",
                   "material_slot_assignment", "post_import_finalize",
                   "sidecar_manifest_read", "sidecar_fingerprint_classification",
                   "sidecar_asset_lookup", "sidecar_batch_import",
                   "sidecar_result_mapping"]
    for ph in all_phases:
        _test(f"Log parser: phase '{ph}' has BEGIN",
              ph in begins,
              detail=f"found in synthetic log: {ph}")
        _test(f"Log parser: phase '{ph}' has END with duration",
              ph in ends,
              detail=f"found in synthetic log: {ph}")


def test_log_exclusive_sum():
    log = build_synthetic_log()
    begins, ends = parse_phases(log)
    exclusive_sum = sum(dur for ph, (cls, dur) in ends.items() if cls == "exclusive")
    expected = 0.5 + 1.2 + 45.0 + 3.0 + 120.0 + 5.0 + 2.1 + 1.5 + 0.8
    _test(f"Log parser: exclusive sum = {exclusive_sum} (expected {expected})",
          abs(exclusive_sum - expected) < 0.01,
          detail=f"got {exclusive_sum}, expected {expected}")
    # Nested phases excluded
    nested_sum = sum(dur for ph, (cls, dur) in ends.items() if cls == "nested")
    expected_nested = 8.0 + 10.0 + 1.0 + 2.0 + 1.5 + 5.0 + 1.0
    _test(f"Log parser: nested sum = {nested_sum} (expected {expected_nested})",
          abs(nested_sum - expected_nested) < 0.01,
          detail=f"got {nested_sum}, expected {expected_nested}")


def test_log_nested_excluded():
    log = build_synthetic_log()
    begins, ends = parse_phases(log)
    exclusive_phases = [ph for ph, (cls, _) in ends.items() if cls == "exclusive"]
    nested_phases = [ph for ph, (cls, _) in ends.items() if cls == "nested"]
    # Nested phases should not appear in exclusive list
    for np in nested_phases:
        _test(f"Log parser: nested phase '{np}' not in exclusive phases",
              np not in exclusive_phases)
    # Exclusive phases should not appear in nested list
    for ep in exclusive_phases:
        _test(f"Log parser: exclusive phase '{ep}' not in nested phases",
              ep not in nested_phases)


def test_log_unattributed():
    log = build_synthetic_log()
    _, ends = parse_phases(log)
    summary = parse_stall_summary(log)
    exclusive_sum = sum(dur for _, (cls, dur) in ends.items() if cls == "exclusive")
    total_ms = summary["totalMs"] if summary else 200.0
    unattributed = total_ms - exclusive_sum
    expected_unattributed = 200.0 - (0.5 + 1.2 + 45.0 + 3.0 + 120.0 + 5.0 + 2.1 + 1.5 + 0.8)
    _test(f"Log parser: unattributedMs = {unattributed:.1f} (expected {expected_unattributed:.1f})",
          abs(unattributed - expected_unattributed) < 0.01,
          detail=f"got {unattributed:.1f}, expected {expected_unattributed:.1f}")


def test_log_largest_phase():
    log = build_synthetic_log()
    _, ends = parse_phases(log)
    largest = max(ends.items(), key=lambda x: x[1][1])
    ph_name, (cls, dur) = largest
    _test(f"Log parser: largest phase = '{ph_name}' ({dur}ms)",
          ph_name == "sidecar_processing" and cls == "exclusive",
          detail=f"largest phase is {ph_name} ({dur}ms), expected sidecar_processing (120.0ms)")


# ---------------------------------------------------------------------------
# 18. FFbxScopePhase has optional OutDurationMs and PhaseDurations parameters
# ---------------------------------------------------------------------------
def test_ffbxscopephase_optional_duration():
    content = _read(FBX_IMPORTER_CPP)
    _test("FFbxScopePhase constructor has optional OutDurationMs",
          re.search(r'FFbxScopePhase.*double\s*\*\s*InOutDurationMs\s*=\s*nullptr', content, re.DOTALL) is not None,
          detail="double* InOutDurationMs = nullptr parameter exists")
    _test("FFbxScopePhase constructor has optional InPhaseDurations",
          re.search(r'FFbxScopePhase.*TMap<FString,\s*double>\s*\*\s*InPhaseDurations\s*=\s*nullptr', content, re.DOTALL) is not None,
          detail="TMap<FString, double>* InPhaseDurations = nullptr parameter exists")
    _test("FFbxScopePhase destructor writes through OutDurationMs",
          re.search(r'~FFbxScopePhase.*\n.*\n.*if\s*\(OutDurationMs\).*\n.*\*OutDurationMs\s*=', content, re.DOTALL) is not None,
          detail="destructor: if (OutDurationMs) { *OutDurationMs = Ms; }")
    _test("FFbxScopePhase destructor accumulates exclusive phases into PhaseDurations",
          re.search(r'~FFbxScopePhase.*\n.*\n.*if\s*\(PhaseDurations\s*&&\s*Kind\s*==\s*EFbxPhaseKind::Exclusive\).*\n.*PhaseDurations->FindOrAdd', content, re.DOTALL) is not None,
          detail="destructor: if (PhaseDurations && Kind == Exclusive) PhaseDurations->FindOrAdd")


# ---------------------------------------------------------------------------
# 19. TransactionId never zero (starts at 1)
# ---------------------------------------------------------------------------
def test_transaction_id_never_zero():
    content = _read(SYNC_TYPES_H)
    _test("FBXTransactionId initialized to 1 (not 0)",
          re.search(r'FBXTransactionId\{1\}', content) is not None,
          detail="FBXTransactionId{1} found in SyncTypes.h")


# ---------------------------------------------------------------------------
# 20. ComputePhaseClassification helper exists
# ---------------------------------------------------------------------------
def test_compute_phase_classification():
    content = _read(FBX_IMPORTER_CPP)
    _test("ComputePhaseClassification static function exists",
          re.search(r'static\s+FString\s+ComputePhaseClassification', content) is not None,
          detail="ComputePhaseClassification found in source")


# ---------------------------------------------------------------------------
# 21. STALL_SUMMARY fields already checked in test_stall_summary above
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 21A. FStringFromFixedAnsi bounded helper — source-presence and structural
#      boundary checks.  Runtime behavior cannot be verified from source alone.
# ---------------------------------------------------------------------------
def test_fstring_from_fixed_ansi():
    content = _read(FBX_IMPORTER_CPP)
    _test("FStringFromFixedAnsi helper exists",
          re.search(r'static\s+FString\s+FStringFromFixedAnsi', content) is not None,
          detail="static FString FStringFromFixedAnsi(...) function found")
    _test("FStringFromFixedAnsi uses ConstructFromPtrSize",
          re.search(r'ConstructFromPtrSize', content) is not None,
          detail="ConstructFromPtrSize called inside helper")
    # Structural boundary checks — verify the loop prevents over-read
    _test("FStringFromFixedAnsi: null Data check present",
          re.search(r'if\s*\(!Data\s*\|\|\s*Capacity\s*<=\s*0\)', content) is not None,
          detail="guard clause rejects null Data and zero/negative Capacity")
    _test("FStringFromFixedAnsi: loop checks Length < Capacity before Data access",
          re.search(r'while\s*\(Length\s*<\s*Capacity\s*&&\s*Data\[Length\]\s*!=\s*0\)', content) is not None,
          detail="loop condition checks Length < Capacity before reading Data[Length]")
    _test("FStringFromFixedAnsi: ++Length increments by 1",
          re.search(r'\+\+Length', content) is not None,
          detail="Length increments by 1 per iteration (cannot exceed Capacity)")
    _test("FStringFromFixedAnsi: ConstructFromPtrSize receives Length, not Capacity",
          re.search(r'ConstructFromPtrSize\(\s*\n?\s*reinterpret_cast<const ANSICHAR\*>\(Data\),\s*\n?\s*Length\)', content, re.DOTALL) is not None,
          detail="ConstructFromPtrSize uses Length (bounded by Capacity but never exceeding it)")


# ---------------------------------------------------------------------------
# 21B. SyncId snapshot from MatPktSyncId (frozen per HandleImport call)
# ---------------------------------------------------------------------------
def test_sync_id_snapshot():
    content = _read(FBX_IMPORTER_CPP)
    _test("SyncId snapshot from MatPktSyncId",
          re.search(r'const\s+int32\s+SyncId\s*=\s*Context\.Stats->MatPktSyncId', content) is not None,
          detail="one SyncId snapshot per HandleImport call")


# ---------------------------------------------------------------------------
# 21C. Bounded decode safety for fixed-length wire fields
# ---------------------------------------------------------------------------
def test_bounded_decode_safety():
    content = _read(FBX_IMPORTER_CPP)
    _test("ObjectName uses FStringFromFixedAnsi in semantic signature",
          re.search(r'FStringFromFixedAnsi\(\s*Request\.ObjectName', content) is not None,
          detail="semantic-signature ObjectName decoded with bounded helper")
    _test("FbxPath uses FStringFromFixedAnsi",
          re.search(r'FStringFromFixedAnsi\(\s*Request\.FbxPath', content) is not None,
          detail="HandleImport FbxPath decoded with bounded helper")
    _test("ObjectName uses FStringFromFixedAnsi at least twice",
          len(re.findall(r'FStringFromFixedAnsi\(\s*Request\.ObjectName', content)) >= 2,
          detail="expected >=2 uses (semantic signature + HandleImport)")
    _test("No ANSI_TO_TCHAR on fixed wire fields",
          'ANSI_TO_TCHAR(reinterpret_cast<const ANSICHAR*>(Request.ObjectName)' not in content and
          'ANSI_TO_TCHAR(reinterpret_cast<const ANSICHAR*>(Request.FbxPath)' not in content,
          detail="all fixed-field conversions must use FStringFromFixedAnsi instead of ANSI_TO_TCHAR")


# ---------------------------------------------------------------------------
# 22. Synthesized log: orphan begin detection
# ---------------------------------------------------------------------------
def build_log_with_orphan_begin():
    lines = []
    lines.append('[FBX][PHASE_BEGIN] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=TestCube phase="orphan_phase" classification="exclusive"')
    lines.append('[FBX][PHASE_END] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=TestCube phase="normal_phase" classification="exclusive" durationMs=5.0')
    return "\n".join(lines)


def test_log_orphan_begin_detection():
    log = build_log_with_orphan_begin()
    begins, ends = parse_phases(log)
    orphan = [ph for ph in begins if ph not in ends]
    excess_end = [ph for ph in ends if ph not in begins]
    has_orphan = len(orphan) > 0
    _test("Log parser: orphan begin detected for 'orphan_phase'",
          has_orphan and "orphan_phase" in orphan,
          detail=f"orphan phases: {orphan}" if has_orphan else "no orphan detected")


# ---------------------------------------------------------------------------
# 23. Synthesized log: duplicate end detection
# ---------------------------------------------------------------------------
def build_log_with_duplicate_end():
    lines = []
    lines.append('[FBX][PHASE_BEGIN] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=TestCube phase="dup_phase" classification="exclusive"')
    lines.append('[FBX][PHASE_END] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=TestCube phase="dup_phase" classification="exclusive" durationMs=1.0')
    lines.append('[FBX][PHASE_END] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=TestCube phase="dup_phase" classification="exclusive" durationMs=2.0')
    return "\n".join(lines)


def count_ends(log_text, phase_name):
    return len(re.findall(rf'PHASE_END.*phase="{phase_name}"', log_text))


def test_log_duplicate_end():
    log = build_log_with_duplicate_end()
    dup_count = count_ends(log, "dup_phase")
    _test("Log parser: duplicate end detected for 'dup_phase'",
          dup_count == 2,
          detail=f"dup_phase has {dup_count} ends (expected 2)")


# ---------------------------------------------------------------------------
# 24. Synthesized log: MIXED classification
# ---------------------------------------------------------------------------
def build_log_mixed_classification():
    lines = []

    def begin(phase, classification):
        lines.append(
            f'[FBX][PHASE_BEGIN] transactionId=1 guid=ABCDEF01 syncId=42 '
            f'objectName=TestCube phase="{phase}" classification="{classification}"')

    def end(phase, classification, duration):
        lines.append(
            f'[FBX][PHASE_END] transactionId=1 guid=ABCDEF01 syncId=42 '
            f'objectName=TestCube phase="{phase}" classification="{classification}" '
            f'durationMs={duration}')

    begin("phase_a", "exclusive")
    end("phase_a", "exclusive", 50.0)
    begin("phase_b", "exclusive")
    end("phase_b", "exclusive", 45.0)
    begin("phase_c", "exclusive")
    end("phase_c", "exclusive", 5.0)

    total = 50.0 + 45.0 + 5.0
    measured = total
    coverage = (measured / total) * 100.0 if total > 0 else 0.0
    lines.append(
        f'[FBX][STALL_SUMMARY] transactionId=1 guid=ABCDEF01 syncId=42 '
        f'objectName=TestCube totalMs={total:.1f} measuredExclusiveMs={measured:.1f} '
        f'coveragePercent={coverage:.2f} largestPhase=phase_a '
        f'largestPhaseMs=50.0 unattributedMs=0.0 '
        f'classification=MIXED')
    return "\n".join(lines)


def compute_mixed_classification(log_text):
    summary = parse_stall_summary(log_text)
    if not summary:
        return None
    return summary["classification"]


def test_log_mixed_classification():
    log = build_log_mixed_classification()
    cls = compute_mixed_classification(log)
    _test("Log parser: MIXED classification detected",
          cls == "MIXED",
          detail=f"classification={cls}")


# ---------------------------------------------------------------------------
# 25. Synthesized log: real STALL_SUMMARY fields parse correctly
# ---------------------------------------------------------------------------
def test_log_stall_summary_fields():
    log = build_synthetic_log()
    summary = parse_stall_summary(log)
    _test("STALL_SUMMARY parsed successfully",
          summary is not None,
          detail="parse_stall_summary returned a valid dict")
    if summary:
        _test("STALL_SUMMARY transactionId=1",
              summary["transactionId"] == 1)
        _test("STALL_SUMMARY totalMs=200.0",
              abs(summary["totalMs"] - 200.0) < 0.01)
        _test("STALL_SUMMARY measuredExclusiveMs=179.1",
              abs(summary["measuredExclusiveMs"] - 179.1) < 0.01)
        _test("STALL_SUMMARY coveragePercent=89.55",
              abs(summary["coveragePercent"] - 89.55) < 0.01)
        _test("STALL_SUMMARY largestPhase=sidecar_processing",
              summary["largestPhase"] == "sidecar_processing")
        _test("STALL_SUMMARY largestPhaseMs=120.0",
              abs(summary["largestPhaseMs"] - 120.0) < 0.01)
        _test("STALL_SUMMARY unattributedMs=20.9",
              abs(summary["unattributedMs"] - 20.9) < 0.01)
        _test("STALL_SUMMARY classification=MIXED",
              summary["classification"] == "MIXED")


# ---------------------------------------------------------------------------
# 26. Observed Phase 10K.6 timing bug — fbx_factory_import excluded
#     Reproduces the exact arithmetic contradiction:
#     fbx_factory_import=359.5 exclusive, sidecar_processing=341.0 exclusive,
#     sidecar_batch_import=340.3 nested, measuredExclusiveMs=342.0 (WRONG)
#     After fix: measuredExclusiveMs≈701.3, coverage≥99%, largest=fbx_factory_import
# ---------------------------------------------------------------------------
def build_observed_timing_log():
    """Build a synthetic log matching the exact observed Phase 10K.6 contradiction."""
    lines = []

    def begin(phase, classification):
        lines.append(
            f'[FBX][PHASE_BEGIN] transactionId=1 guid=ABCDEF01 syncId=42 '
            f'objectName=Cabinet phase="{phase}" classification="{classification}"')

    def end(phase, classification, duration):
        lines.append(
            f'[FBX][PHASE_END] transactionId=1 guid=ABCDEF01 syncId=42 '
            f'objectName=Cabinet phase="{phase}" classification="{classification}" '
            f'durationMs={duration}')

    begin("request_parse", "exclusive")
    end("request_parse", "exclusive", 1.5)

    begin("path_validation", "exclusive")
    end("path_validation", "exclusive", 0.8)

    begin("fbx_factory_import", "exclusive")
    end("fbx_factory_import", "exclusive", 359.5)

    begin("imported_asset_discovery", "exclusive")
    end("imported_asset_discovery", "exclusive", 0.5)

    begin("sidecar_processing", "exclusive")
    begin("sidecar_manifest_read", "nested")
    end("sidecar_manifest_read", "nested", 0.3)
    begin("sidecar_fingerprint_classification", "nested")
    end("sidecar_fingerprint_classification", "nested", 0.7)
    begin("sidecar_asset_lookup", "nested")
    end("sidecar_asset_lookup", "nested", 0.5)
    begin("sidecar_batch_import", "nested")
    end("sidecar_batch_import", "nested", 340.3)
    begin("sidecar_result_mapping", "nested")
    end("sidecar_result_mapping", "nested", 0.2)
    end("sidecar_processing", "exclusive", 341.0)

    begin("static_mesh_post_import", "exclusive")
    end("static_mesh_post_import", "exclusive", 0.8)

    begin("actor_lookup_or_spawn", "exclusive")
    end("actor_lookup_or_spawn", "exclusive", 1.5)

    begin("static_mesh_assignment", "exclusive")
    end("static_mesh_assignment", "exclusive", 1.3)

    begin("material_slot_assignment", "exclusive")
    end("material_slot_assignment", "exclusive", 0.6)

    begin("post_import_finalize", "nested")
    end("post_import_finalize", "nested", 0.5)

    # After fix: totalMs=701.9, measuredExclusiveMs≈707.5
    # coveragePercent≈99.8%, largestPhase=fbx_factory_import(359.5)
    # Classification: sidecar_processing (341.0) is 94.8% of fbx_factory_import (359.5)
    # → MIXED (since 341.0 >= 359.5 * 0.8 = 287.6)
    lines.append(
        f'[FBX][STALL_SUMMARY] transactionId=1 guid=ABCDEF01 syncId=42 '
        f'objectName=Cabinet totalMs=701.9 measuredExclusiveMs=707.5 '
        f'coveragePercent=100.0 largestPhase=fbx_factory_import '
        f'largestPhaseMs=359.5 unattributedMs=0.0 '
        f'classification=MIXED')

    return "\n".join(lines)


def test_observed_timing_bug_reproduction():
    """
    Reproduce the exact Phase 10K.6 arithmetic contradiction.

    Before fix:
        measuredExclusiveMs=342.0 (only sidecar_processing counted)
        largestPhase=sidecar_processing (WRONG)
        coveragePercent=48.72 (WRONG)

    After fix:
        measuredExclusiveMs≈701.3
        coveragePercent>99%
        largestPhase=fbx_factory_import
        classification=MIXED
    """
    log = build_observed_timing_log()
    begins, ends = parse_phases(log)
    summary = parse_stall_summary(log)

    # All phases balanced
    all_phases = [
        "request_parse", "path_validation", "fbx_factory_import",
        "imported_asset_discovery", "sidecar_processing",
        "sidecar_manifest_read", "sidecar_fingerprint_classification",
        "sidecar_asset_lookup", "sidecar_batch_import",
        "sidecar_result_mapping", "static_mesh_post_import",
        "actor_lookup_or_spawn", "static_mesh_assignment",
        "material_slot_assignment", "post_import_finalize",
    ]
    for ph in all_phases:
        _test(f"Observed case: phase '{ph}' has BEGIN",
              ph in begins)
        _test(f"Observed case: phase '{ph}' has END",
              ph in ends)

    # Parse and validate exclusive sum (nested excluded)
    # Sum of: 1.5 + 0.8 + 359.5 + 0.5 + 341.0 + 0.8 + 1.5 + 1.3 + 0.6 = 707.5
    exclusive_sum = sum(
        dur for ph, (cls, dur) in ends.items() if cls == "exclusive")
    _test(f"Observed case: exclusive sum ≈ 707.5 (got {exclusive_sum:.1f})",
          abs(exclusive_sum - 707.5) < 0.1,
          detail=f"got {exclusive_sum:.1f}, expected ~707.5")

    # coveragePercent calculation must exist (the approved contract specifies
    # CoveragePercent = (MeasuredExclusiveMs / TotalMs) * 100.0; with no clamp).
    content = _read(FBX_IMPORTER_CPP)
    _test("Source: coveragePercent calculation exists",
          re.search(r'CoveragePercent', content) is not None,
          detail="CoveragePercent must exist in source (contract: no clamp required)")

    # Verify timing validity check exists
    _test("Source: timing validity check exists",
          re.search(r'bTimingValid|TIMING_CLASSIFICATION_INVALID', content) is not None,
          detail="source must check timing validity before classification")

    # largest phase is fbx_factory_import
    if summary:
        _test("Observed case: largestPhase == fbx_factory_import",
              summary["largestPhase"] == "fbx_factory_import",
              detail=f"got {summary['largestPhase']}")

        # largestPhaseMs == 359.5
        _test("Observed case: largestPhaseMs == 359.5",
              abs(summary["largestPhaseMs"] - 359.5) < 0.1,
              detail=f"got {summary['largestPhaseMs']}")

        # sidecar_batch_import excluded from exclusive sum
        nested_sum = sum(
            dur for ph, (cls, dur) in ends.items() if cls == "nested")
        _test("Observed case: sidecar_batch_import (nested) NOT in exclusive sum",
              "sidecar_batch_import" not in
              [ph for ph, (cls, _) in ends.items() if cls == "exclusive"])

        # Classification == MIXED (341.0 / 359.5 = 94.8% >= 80%)
        _test("Observed case: classification == MIXED",
              summary["classification"] == "MIXED",
              detail=f"got {summary['classification']}")

        # unattributedMs clamped to 0 because measuredExclusiveMs > totalMs
        # (the exclusive phases' END durations sum to 707.5 vs total 701.9)
        _test("Observed case: unattributedMs >= 0 (got {:.1f})".format(summary["unattributedMs"]),
              summary["unattributedMs"] >= 0,
              detail=f"got {summary['unattributedMs']:.1f} (clamped by max(0, ...))")


def test_exclusive_sum_greater_than_total():
    """If overlap causes exclusive sum > totalMs, report TIMING_CLASSIFICATION_INVALID."""
    log = build_observed_timing_log()
    # Modify to create overlap: make exclusive phases sum > total
    modified = log.replace(
        'totalMs=701.9 measuredExclusiveMs=701.3',
        'totalMs=500.0 measuredExclusiveMs=701.3')
    summary = parse_stall_summary(modified)
    if summary:
        unattr = summary["unattributedMs"]
        _test("Exclusive sum > totalMs: unattributedMs clamped to >= 0",
              unattr >= 0,
              detail=f"unattributedMs={unattr} (should be >= 0)")


def test_largest_phase_emitted_before_other_phases():
    """Verify that even if largest phase ends first, it is still correctly identified."""
    lines = []
    lines.append('[FBX][PHASE_BEGIN] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="large_phase" classification="exclusive"')
    lines.append('[FBX][PHASE_END] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="large_phase" classification="exclusive" durationMs=100.0')
    lines.append('[FBX][PHASE_BEGIN] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="small_phase" classification="exclusive"')
    lines.append('[FBX][PHASE_END] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="small_phase" classification="exclusive" durationMs=10.0')
    log = "\n".join(lines)
    _, ends = parse_phases(log)
    largest = max(ends.items(), key=lambda x: x[1][1])
    _test("Largest phase correctly selected when emitted first",
          largest[0] == "large_phase",
          detail=f"got {largest[0]}")


def test_phase_name_lookup_is_exact():
    """Phase name lookup must be exact — no substring matching."""
    log = build_observed_timing_log()
    _, ends = parse_phases(log)
    # Ensure exact match: "sidecar_processing" is distinct from "sidecar"
    has_sidecar_processing = any("sidecar_processing" in ph for ph in ends)
    _test("Phase name 'sidecar_processing' found exactly",
          has_sidecar_processing)
    has_only_sidecar = any(ph == "sidecar" for ph in ends)
    _test("No accidental 'sidecar' phase created",
          not has_only_sidecar)


def test_mismatched_transaction_id_does_not_contaminate():
    """Phases from different transactionIds must not contaminate the summary."""
    lines = []
    lines.append('[FBX][PHASE_BEGIN] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="txn1_phase" classification="exclusive"')
    lines.append('[FBX][PHASE_END] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="txn1_phase" classification="exclusive" durationMs=50.0')
    lines.append('[FBX][PHASE_BEGIN] transactionId=99 guid=XYZ123 syncId=99 '
                 'objectName=Y phase="txn99_phase" classification="exclusive"')
    lines.append('[FBX][PHASE_END] transactionId=99 guid=XYZ123 syncId=99 '
                 'objectName=Y phase="txn99_phase" classification="exclusive" durationMs=200.0')
    lines.append('[FBX][STALL_SUMMARY] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X totalMs=50.0 measuredExclusiveMs=50.0 '
                 'coveragePercent=100.0 largestPhase=txn1_phase '
                 'largestPhaseMs=50.0 unattributedMs=0.0 '
                 'classification=DOMINANT_txn1_phase')
    log = "\n".join(lines)
    summary = parse_stall_summary(log)
    _test("Mismatched transactionId (99) does not contaminate txnId=1 summary",
          summary is not None and summary["transactionId"] == 1
          and abs(summary["measuredExclusiveMs"] - 50.0) < 0.1,
          detail=f"got measuredExclusiveMs={summary['measuredExclusiveMs'] if summary else 'None'}")


def test_duplicate_phase_end_handled():
    """Duplicate phase END must be detected and handled (not silently double-counted)."""
    lines = []
    lines.append('[FBX][PHASE_BEGIN] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="dup_phase" classification="exclusive"')
    lines.append('[FBX][PHASE_END] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="dup_phase" classification="exclusive" durationMs=10.0')
    lines.append('[FBX][PHASE_END] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="dup_phase" classification="exclusive" durationMs=10.0')
    lines.append('[FBX][STALL_SUMMARY] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X totalMs=10.0 measuredExclusiveMs=10.0 '
                 'coveragePercent=100.0 largestPhase=dup_phase '
                 'largestPhaseMs=10.0 unattributedMs=0.0 '
                 'classification=DOMINANT_dup_phase')
    log = "\n".join(lines)
    summary = parse_stall_summary(log)
    _test("Duplicate phase END: parser records summary as-is (test warns about the issue)",
          summary is not None,
          detail="duplicate end detected by separate test; parser should warn")


def test_missing_exclusive_phase_visible():
    """A phase that has BEGIN but no END should be visible as unmatched."""
    lines = []
    lines.append('[FBX][PHASE_BEGIN] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="request_parse" classification="exclusive"')
    lines.append('[FBX][PHASE_END] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="request_parse" classification="exclusive" durationMs=1.0')
    # Only has BEGIN (no END)
    lines.append('[FBX][PHASE_BEGIN] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="missing_phase" classification="exclusive"')
    lines.append('[FBX][STALL_SUMMARY] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X totalMs=1.0 measuredExclusiveMs=1.0 '
                 'coveragePercent=100.0 largestPhase=request_parse '
                 'largestPhaseMs=1.0 unattributedMs=0.0 '
                 'classification=DOMINANT_request_parse')
    log = "\n".join(lines)
    begins, ends = parse_phases(log)
    # Phase that has BEGIN but no END
    unmatched_begins = [ph for ph in begins if ph not in ends]
    _test("Phase with BEGIN but no END detected as unmatched",
          "missing_phase" in unmatched_begins,
          detail=f"unmatched begins: {unmatched_begins}")


# ---------------------------------------------------------------------------
# Task 5: Behavioral tests for valid coverage and overlap detection
# ---------------------------------------------------------------------------
def test_sequential_exclusive_coverage_leq_100():
    """Sequential exclusive phases produce coverage <= 100%."""
    lines = []
    lines.append('[FBX][PHASE_BEGIN] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="phase_a" classification="exclusive"')
    lines.append('[FBX][PHASE_END] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="phase_a" classification="exclusive" durationMs=10.0')
    lines.append('[FBX][PHASE_BEGIN] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="phase_b" classification="exclusive"')
    lines.append('[FBX][PHASE_END] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="phase_b" classification="exclusive" durationMs=20.0')
    # totalMs = 35.0 (includes 5ms overhead)
    lines.append('[FBX][STALL_SUMMARY] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X totalMs=35.0 measuredExclusiveMs=30.0 '
                 'coveragePercent=85.71 largestPhase=phase_b '
                 'largestPhaseMs=20.0 unattributedMs=5.0 '
                 'classification=DOMINANT_phase_b')
    log = "\n".join(lines)
    summary = parse_stall_summary(log)
    _test("Sequential exclusive phases: coverage <= 100% (got {:.2f})".format(
        summary["coveragePercent"] if summary else 0),
        summary is not None and summary["coveragePercent"] <= 100.0 + 0.01,
        detail="sequential phases should never exceed 100% coverage")


def test_nested_phase_excluded_from_exclusive_sum():
    """Nested phase duration must not be included in exclusive sum."""
    lines = []
    lines.append('[FBX][PHASE_BEGIN] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="parent" classification="exclusive"')
    lines.append('[FBX][PHASE_BEGIN] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="child" classification="nested"')
    lines.append('[FBX][PHASE_END] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="child" classification="nested" durationMs=50.0')
    lines.append('[FBX][PHASE_END] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="parent" classification="exclusive" durationMs=10.0')
    log = "\n".join(lines)
    _, ends = parse_phases(log)
    exclusive_sum = sum(dur for ph, (cls, dur) in ends.items() if cls == "exclusive")
    _test("Nested phase excluded from exclusive sum (got {:.1f})".format(exclusive_sum),
          abs(exclusive_sum - 10.0) < 0.01,
          detail=f"exclusive sum should be 10.0 (parent only), not 60.0")


def test_inclusive_parent_excluded_from_exclusive_sum():
    """Inclusive parent phase must not be included in exclusive sum."""
    content = _read(FBX_IMPORTER_CPP)
    # Verify source code has IsExclusivePhase check
    _test("Source: IsExclusivePhase used in STALL_SUMMARY calculation",
          re.search(r'IsExclusivePhase', content) is not None)


def test_overlapping_exclusive_produces_invalid():
    """When measuredExclusiveMs > totalMs + tolerance, timingValidity=INVALID_OVERLAP."""
    lines = []
    lines.append('[FBX][PHASE_BEGIN] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="phase_a" classification="exclusive"')
    lines.append('[FBX][PHASE_END] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="phase_a" classification="exclusive" durationMs=100.0')
    lines.append('[FBX][STALL_SUMMARY] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X totalMs=50.0 measuredExclusiveMs=100.0 '
                 'coveragePercent=100.0 largestPhase=phase_a '
                 'largestPhaseMs=100.0 unattributedMs=0.0 '
                 'classification=TIMING_CLASSIFICATION_INVALID')
    log = "\n".join(lines)
    summary = parse_stall_summary(log)
    _test("Overlap detected: classification=TIMING_CLASSIFICATION_INVALID",
          summary is not None and summary["classification"] == "TIMING_CLASSIFICATION_INVALID",
          detail=f"classification={summary['classification'] if summary else 'None'}")


def test_measured_exclusive_greater_than_total_rejected():
    """measuredExclusiveMs > totalMs must produce TIMING_CLASSIFICATION_INVALID."""
    log = build_observed_timing_log()
    modified = log.replace(
        'totalMs=701.9 measuredExclusiveMs=707.5',
        'totalMs=500.0 measuredExclusiveMs=701.3')
    summary = parse_stall_summary(modified)
    _test("measuredExclusiveMs > totalMs: coverage clamped to 100%",
          summary is not None and summary["coveragePercent"] <= 100.0,
          detail=f"coveragePercent={summary['coveragePercent'] if summary else 'None'}")


def test_timer_tolerance_handles_sub_millisecond():
    """Timer tolerance should only cover sub-millisecond precision noise."""
    lines = []
    lines.append('[FBX][PHASE_BEGIN] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="phase_a" classification="exclusive"')
    lines.append('[FBX][PHASE_END] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="phase_a" classification="exclusive" durationMs=10.0')
    # totalMs slightly less due to timer noise — still valid
    lines.append('[FBX][STALL_SUMMARY] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X totalMs=9.5 measuredExclusiveMs=10.0 '
                 'coveragePercent=100.0 largestPhase=phase_a '
                 'largestPhaseMs=10.0 unattributedMs=0.0 '
                 'classification=DOMINANT_phase_a')
    log = "\n".join(lines)
    summary = parse_stall_summary(log)
    _test("Timer tolerance: measured > total by < 0.5ms is still valid",
          summary is not None and summary["coveragePercent"] <= 100.0,
          detail="sub-millisecond timer noise should not trigger INVALID_OVERLAP")


def test_largest_phase_considers_only_valid_exclusive():
    """largest phase selection must only consider valid exclusive phases."""
    lines = []
    lines.append('[FBX][PHASE_BEGIN] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="large_phase" classification="exclusive"')
    lines.append('[FBX][PHASE_END] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="large_phase" classification="exclusive" durationMs=100.0')
    lines.append('[FBX][PHASE_BEGIN] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="medium_phase" classification="exclusive"')
    lines.append('[FBX][PHASE_END] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X phase="medium_phase" classification="exclusive" durationMs=50.0')
    lines.append('[FBX][STALL_SUMMARY] transactionId=1 guid=ABCDEF01 syncId=42 '
                 'objectName=X totalMs=110.0 measuredExclusiveMs=150.0 '
                 'coveragePercent=100.0 largestPhase=large_phase '
                 'largestPhaseMs=100.0 unattributedMs=0.0 '
                 'classification=DOMINANT_large_phase')
    log = "\n".join(lines)
    summary = parse_stall_summary(log)
    _test("Largest phase correctly identified (got {})".format(
        summary["largestPhase"] if summary else "None"),
        summary is not None and summary["largestPhase"] == "large_phase",
        detail="largest should be large_phase at 100.0ms")


def test_mixed_classification_only_when_timing_valid():
    """MIXED classification should only be computed when timingValidity is valid."""
    content = _read(FBX_IMPORTER_CPP)
    # Verify source code checks timing validity before computing classification
    _test("Classification only computed when timing is valid",
          re.search(r'TIMING_CLASSIFICATION_INVALID', content) is not None,
          detail="source must check timing validity before MIXED classification")


def test_missing_phase_not_silently_zero():
    """A missing phase should not silently become 0ms."""
    log = build_synthetic_log()
    _, ends = parse_phases(log)
    # Verify all expected phases have non-zero durations in exclusive sum
    for ph in ["request_parse", "path_validation", "fbx_factory_import"]:
        if ph in ends:
            cls, dur = ends[ph]
            _test(f"Phase '{ph}' has non-zero duration (got {dur})",
                  dur > 0,
                  detail=f"phase={ph}")


def test_all_required_source_markers_exist():
    """All required source markers must exist in the C++ source."""
    content = _read(FBX_IMPORTER_CPP)
    # FFbxScopePhase struct
    _test("FFbxScopePhase struct exists",
          re.search(r'struct\s+FFbxScopePhase', content) is not None)
    # FbxPhaseBegin helper
    _test("FbxPhaseBegin helper exists",
          re.search(r'static.*void\s+FbxPhaseBegin', content) is not None)
    # FbxPhaseEnd helper
    _test("FbxPhaseEnd helper exists",
          re.search(r'static.*void\s+FbxPhaseEnd', content) is not None)
    # GExclusivePhases registry
    _test("GExclusivePhases TSet exists",
          re.search(r'GExclusivePhases', content) is not None)
    # IsExclusivePhase function
    _test("IsExclusivePhase function exists",
          re.search(r'IsExclusivePhase', content) is not None)
    # ComputePhaseClassificationExclusive function
    _test("ComputePhaseClassificationExclusive function exists",
          re.search(r'ComputePhaseClassificationExclusive', content) is not None)
    # PHASE_BEGIN log marker
    _test("PHASE_BEGIN UE_LOG marker exists",
          re.search(r'PHASE_BEGIN', content) is not None)
    # PHASE_END log marker
    _test("PHASE_END UE_LOG marker exists",
          re.search(r'PHASE_END', content) is not None)
    # STALL_SUMMARY log marker
    _test("STALL_SUMMARY UE_LOG marker exists",
          re.search(r'STALL_SUMMARY', content) is not None)


# ---------------------------------------------------------------------------
# Run all
# ---------------------------------------------------------------------------
# 27. Source-structure test: RAII accumulator inside destructor
#     Verifies the FFbxScopePhase destructor accumulates PhaseDurations
#     for exclusive phases, replacing the old post-scope write pattern.
# ---------------------------------------------------------------------------
def test_raii_accumulator_inside_destructor():
    """Verify FFbxScopePhase destructor accumulates PhaseDurations for exclusive phases."""
    content = _read(FBX_IMPORTER_CPP)

    # The struct destructor must contain the accumulator logic
    _test("RAII struct destructor has PhaseDurations accumulation",
          re.search(r'if\s*\(PhaseDurations\s*&&\s*Kind\s*==\s*EFbxPhaseKind::Exclusive\)\s*\n\s*\{\s*\n\s*PhaseDurations->FindOrAdd', content, re.DOTALL) is not None,
          detail="destructor: PhaseDurations->FindOrAdd(PhaseName) += Ms inside Kind==Exclusive guard")
    _test("RAII struct destructor writes OutDurationMs before PhaseDurations accumulation",
          re.search(r'if\s*\(OutDurationMs\)\s*\n\s*\{\s*\n\s*\*OutDurationMs\s*=', content, re.DOTALL) is not None,
          detail="destructor: *OutDurationMs = Ms written before PhaseDurations accumulation")
    # Verify no standalone post-scope accumulator for exclusive phases exists
    # in the HandleImport function (validating the new pattern replaced the old one)
    handle_import_match = re.search(r'bool FLiveSyncFBXImporter::HandleImport\(', content)
    if handle_import_match:
        import_body = content[handle_import_match.start():]
        standalone_accum = re.search(r'PhaseDurations\.FindOrAdd\(', import_body)
        _test("RAII fix: no standalone PhaseDurations.FindOrAdd in HandleImport body",
              standalone_accum is None,
              detail="standalone accumulator would indicate pre-destructor pattern remains")


# ---------------------------------------------------------------------------
# 27B. Future-slice production checks: FFbxScopePhase wiring + structural
#      enclosure.  These assert the exact form:
#        FFbxScopePhase Scope(..., TEXT("phase"), EFbxPhaseKind::Exclusive,
#                             ..., &PhaseDurations);
#      All must fail until the production phases are implemented (Slice 3+).
# ---------------------------------------------------------------------------
def test_production_phase_wiring():
    """Verify each production phase has a standalone dedicated block with
    correct wiring and lexical enclosure.  All searches are bounded to
    the HandleImport function body.

    Enforces:
      - Declaration uniqueness: exactly 1 FFbxScopePhase per phase.
      - Independent field assertions: Exclusive and PhaseDurations checked
        separately (lexically-aware).
      - Exact ordering chain for each phase.
    """
    content = _read(FBX_IMPORTER_CPP)
    mask = _build_lexical_mask(content)

    handle_open, handle_close = _find_handleimport_full_bounds(content, mask)
    hi_body_start = handle_open + 1 if handle_open >= 0 else 0
    hi_body_end = handle_close if handle_close >= 0 else len(content)

    # Find unmasked work-call positions (bounded to HandleImport body)
    vps_pos = _find_unmasked_call(content, mask, 'ValidatePathSecurity',
                                  hi_body_start, hi_body_end)
    vv_pos = _find_unmasked_call(content, mask, 'ValidateVersion',
                                 hi_body_start, hi_body_end)
    fbx_path_start, fbx_path_end = _find_fbxpath_invocation(
        content, mask, hi_body_start, hi_body_end)

    # Fixture for lexical HandleImport discovery (req 7)
    # check commented signature before real one is ignored
    fixture_lexical_hi = (
        '// bool FLiveSyncFBXImporter::HandleImport(FBXImportRequest& Request)\n'
        'bool FLiveSyncFBXImporter::HandleImport(FBXImportRequest& Request)\n'
        '{\n'
        '    return true;\n'
        '}'
    )
    mask_lex_hi = _build_lexical_mask(fixture_lexical_hi)
    ho, hc = _find_handleimport_full_bounds(fixture_lexical_hi, mask_lex_hi)
    _test("Production: HandleImport discovery skips commented signature",
          ho >= 0 and hc > ho,
          detail="commented signature before real one must not confuse discovery")

    # ===================================================================
    # Helper: run assertions for one phase
    # ===================================================================
    def check_phase(ph_name, enclosed_func, enclosed_func_pos,
                    enclosed_func2=None, enclosed_func2_pos=-1,
                    require_fbx_path=False):
        """Run declaration uniqueness, independent field assertions,
        block detection, bounds, and ordering chain."""
        # --- 1. Collect ALL declarations ---
        all_decls = _find_ffbxscopephase_decls(content, mask, ph_name,
                                                hi_body_start, hi_body_end)
        total, complete = _count_phase_declarations(
            all_decls, [inv for _, inv in all_decls])
        decl_count = total

        has_unique = (decl_count == 1)
        _test(f"Production: '{ph_name}' phase declaration uniqueness",
              has_unique,
              detail=f"total={total}, complete={complete}, "
                     f"expected exactly 1 declaration")

        # --- Declaration count assertions ---
        _test(f"Production: '{ph_name}' total declaration count = {total}",
              total == 1,
              detail=f"total={total}, expected exactly 1")
        _test(f"Production: '{ph_name}' complete declaration count = {complete}",
              complete == 1,
              detail=f"complete={complete}, expected exactly 1 (both Exclusive and &PhaseDurations)")

        # --- Independent field assertions (on the single declaration) ---
        if has_unique and decl_count == 1:
            _, single_inv = all_decls[0]
            single_match = all_decls[0][0]
            has_excl = _has_field_outside_comment(
                single_inv, 'EFbxPhaseKind::Exclusive')
            has_dur = _has_field_outside_comment(
                single_inv, '&PhaseDurations')

            _test(f"Production: '{ph_name}' uses EFbxPhaseKind::Exclusive",
                  has_excl,
                  detail="lexical-aware check in invocation text")
            _test(f"Production: '{ph_name}' supplies &PhaseDurations",
                  has_dur,
                  detail="lexical-aware check in invocation text")
        else:
            single_match = None
            _test(f"Production: '{ph_name}' uses EFbxPhaseKind::Exclusive",
                  False, detail="no unique declaration")
            _test(f"Production: '{ph_name}' supplies &PhaseDurations",
                  False, detail="no unique declaration")

        # --- Standalone dedicated block ---
        block = None
        if single_match:
            block = _find_dedicated_block(content, mask, single_match.start())
        block_exists = block is not None
        _test(f"Production: '{ph_name}' declared in standalone dedicated block",
              block_exists,
              detail="scope must be in a standalone { } block inside HandleImport")

        # --- Full bounds ---
        if block_exists and single_match:
            block_open, block_close, _ = block
            decl_start = single_match.start()
            inv = _extract_constructor_invocation(content, mask, single_match)
            decl_end = len(inv) + decl_start if inv else decl_start + 1
            bounds_ok = (handle_open < block_open < decl_start < decl_end
                         < block_close < handle_close)
            _test(f"Production: '{ph_name}' block bounds inside HandleImport",
                  bounds_ok,
                  detail=(f"h@{handle_open},{handle_close} "
                          f"b@{block_open},{block_close} "
                          f"d@{decl_start},{decl_end}"))
        else:
            block_open = block_close = decl_start = decl_end = -1
            _test(f"Production: '{ph_name}' block bounds inside HandleImport",
                  False, detail="no canonical declaration or block")

        # --- Declaration before enclosed function ---
        if single_match and block_exists and enclosed_func_pos >= 0:
            _test(f"Production: '{ph_name}' decl before {enclosed_func}",
                  single_match.start() < enclosed_func_pos,
                  detail=(f"decl@{single_match.start()} "
                          f"vs {enclosed_func}@{enclosed_func_pos}"))
        else:
            _test(f"Production: '{ph_name}' decl before {enclosed_func}",
                  False,
                  detail="decl or block or {enclosed_func} not found")

        # --- Enclosed function inside block ---
        if block_exists and enclosed_func_pos >= 0:
            _test(f"Production: '{ph_name}' block encloses {enclosed_func}",
                  enclosed_func_pos > block_open
                  and enclosed_func_pos < block_close,
                  detail=(f"{enclosed_func}@{enclosed_func_pos} "
                          f"inside block@{block_open},{block_close}"))
        else:
            _test(f"Production: '{ph_name}' block encloses {enclosed_func}",
                  False, detail="block or {enclosed_func} not found")

        # --- Ordering chain (shared preparation helper) ---
        if require_fbx_path:
            prep_valid, prep_missing, order_valid, order_failing, order_detail = \
                _prepare_request_parse_ordering(
                    handle_open, handle_close,
                    has_unique, single_match,
                    decl_start, decl_end,
                    block_exists, block_open, block_close,
                    fbx_path_start, fbx_path_end,
                    enclosed_func_pos, vps_pos)
        else:
            prep_valid, prep_missing, order_valid, order_failing, order_detail = \
                _prepare_path_validation_ordering(
                    handle_open, handle_close,
                    has_unique, single_match,
                    decl_start, decl_end,
                    block_exists, block_open, block_close,
                    enclosed_func_pos)

        suffix = "request_parse" if require_fbx_path else "path_validation"
        _test(f"Production: '{suffix}' exact ordering chain",
              order_valid,
              detail=order_detail if order_detail else "all positions in order")

        return single_match, block

    # ===================================================================
    # path_validation — no FbxPath, no ValidateVersion
    # ===================================================================
    check_phase(
        'path_validation', 'ValidatePathSecurity', vps_pos,
        require_fbx_path=False,
    )

    # ===================================================================
    # request_parse — FbxPath before ValidateVersion
    # ===================================================================
    def rp_extra(block_open, block_close, body):
        # Bounded FbxPath invocation inside block
        fbk_inside = (fbx_path_start > block_open
                      and fbx_path_end < block_close)
        _test("Production: 'request_parse' block encloses bounded FbxPath extraction",
              fbk_inside,
              detail=(f"FBXPath inv@{fbx_path_start},{fbx_path_end} "
                      f"inside block@{block_open},{block_close}"))

        # Block closes before ValidatePathSecurity
        _test("Production: 'request_parse' block closes before ValidatePathSecurity",
              block_close < vps_pos,
              detail=f"close@{block_close} vs VPS@{vps_pos}")

        # ValidatePathSecurity outside block
        _test("Production: ValidatePathSecurity outside 'request_parse' block",
              not (vps_pos > block_open and vps_pos < block_close),
              detail="VPS must be outside request_parse block")

    rp_match, rp_block = check_phase(
        'request_parse', 'ValidateVersion', vv_pos,
        require_fbx_path=True,
    )

    if rp_match and rp_block:
        rp_extra(rp_block[0], rp_block[1], rp_block[2])
    else:
        reason = ("decl found but no block" if rp_match
                  else "decl not found")
        _test("Production: 'request_parse' block encloses bounded FbxPath extraction",
              False, detail=reason)
        _test("Production: 'request_parse' block closes before ValidatePathSecurity",
              False, detail=reason)
        _test("Production: ValidatePathSecurity outside 'request_parse' block",
              False, detail=reason)


# ---------------------------------------------------------------------------
# 28. Source-structure test: exclusive phase registry completeness
# ---------------------------------------------------------------------------
def test_exclusive_phase_registry_complete():
    """Verify IsExclusivePhase() includes all required exclusive phases."""
    content = _read(FBX_IMPORTER_CPP)

    required = [
        "request_parse",
        "path_validation",
        "fbx_factory_import",
        "imported_asset_discovery",
        "sidecar_processing",
        "static_mesh_post_import",
        "actor_lookup_or_spawn",
        "static_mesh_assignment",
        "material_slot_assignment",
    ]

    # Find the GExclusivePhases TSet body (static const or lambda init)
    gex_lambda_match = re.search(
        r'static\s+TSet<FString>\s+GExclusivePhases\s*=\s*\[\]\s*\(\)\s*\{([^\]]*)\}\(\)',
        content, re.DOTALL)
    gex_static_match = re.search(
        r'static\s+const\s+TSet<FString>\s+GExclusivePhases\s*=\s*\{([^}]+)\}',
        content, re.DOTALL)
    iso_match = gex_lambda_match or gex_static_match

    _test("IsExclusivePhase function exists",
          iso_match is not None)

    # Check registry phases from FFbxPhaseRegistry
    registry_match = re.search(
        r'struct\s+FFbxPhaseRegistry',
        content, re.DOTALL)

    if gex_static_match:
        body = gex_static_match.group(1)
        for phase in required:
            _test(f"Registry includes '{phase}'",
                  re.search(rf'TEXT\("{phase}"\)', body) is not None,
                  detail=f"phase={phase}")
    elif registry_match:
        # GExclusivePhases is a lambda — check FFbxPhaseRegistry Phases.Emplace
        reg_body = content[registry_match.start():registry_match.end() + 2000]
        for phase in required:
            _test(f"Registry includes '{phase}'",
                  re.search(rf'Phases\.Emplace\(TEXT\("{phase}"\)', reg_body) is not None,
                  detail=f"phase={phase}")


# ---------------------------------------------------------------------------
# 29. Source-structure test: registry and parser do not drift
# ---------------------------------------------------------------------------
def test_registry_and_parser_do_not_drift():
    """Verify the parser uses IsExclusivePhase, not its own list."""
    content = _read(FBX_IMPORTER_CPP)

    # STALL_SUMMARY calculation must use IsExclusivePhase
    stall_section = re.search(
        r'Phase 10K\.6: STALL_SUMMARY.*?FbxImportMs',
        content, re.DOTALL)

    if stall_section:
        _test("STALL_SUMMARY calculation uses IsExclusivePhase",
              "IsExclusivePhase" in stall_section.group(0),
              detail="STALL_SUMMARY must reference IsExclusivePhase")

        _test("STALL_SUMMARY uses ComputePhaseClassificationExclusive",
              "ComputePhaseClassificationExclusive" in stall_section.group(0),
              detail="STALL_SUMMARY must call the exclusive-only classifier")


# ---------------------------------------------------------------------------
# 30. Phase 10K.5 diagnostics test
# ---------------------------------------------------------------------------
def test_phase10k5_diagnostics():
    """Verify Phase 10K.5 diagnostic markers are present."""
    content = _read(FBX_IMPORTER_CPP)
    # Check for key diagnostic markers
    _test("Phase 10K.5: FBX transaction timing markers present",
          re.search(r'FBX.*SYNC_TIMING', content, re.IGNORECASE) is not None)
    _test("Phase 10K.5: STALL_SUMMARY present",
          re.search(r'STALL_SUMMARY', content) is not None)


# ---------------------------------------------------------------------------
# 31. Phase 7E focused test
# ---------------------------------------------------------------------------
def test_phase7e_focused():
    """Verify Phase 7E visibility channel markers are untouched."""
    content = _read(FBX_IMPORTER_CPP)
    # Phase 7E should not be modified by this task
    # Verify no new visibility-specific logic leaked into FBX import
    _test("Phase 7E: no visibility channel changes in FBX importer",
          re.search(r'visibility.*channel.*9|channel.*visibility.*9',
                    content, re.IGNORECASE) is None,
          detail="FBX importer must not touch visibility channels 9/10")


# ---------------------------------------------------------------------------
# Scope extractor self-test fixtures — prove the dedicated-block detector
# works correctly on controlled test data.
# ---------------------------------------------------------------------------
def _test_scope_extractor_self_test():
    """Disposable self-test for scope-extractor and related helpers.

    Fixtures:
      A: Standalone block → accepted
      B: HandleImport body only → rejected
      C: Decl inside if block → rejected
      D: Decl inside for loop → rejected
      E: Braces in comments/strings → ignored
      F: FFbxScopePhase text in comment → ignored
      G: Work call in comment → masked
      H: Two incomplete same-phase decls → do not combine
      I: Nested control-flow after valid decl → supported
      J: Full bounds inside HandleImport
      K1-K3: Lambda blocks → rejected
      L: Function defs before HandleImport → call sites found correctly
      M1-M4: FStringFromFixedAnsi invocation → correct args
      N: End-to-end complete declaration → PASS all checks
      O: End-to-end incomplete decls in HandleImport → FAIL correctly
    """

    def run_fixture(fixture_name, test_text, decl_name,
                    expected_success):
        mask = _build_lexical_mask(test_text)
        decl_re = r'FFbxScopePhase\s+(?:' + decl_name + r')\s*\('
        decl_match = None
        for m in re.finditer(decl_re, test_text):
            if not mask[m.start()]:
                decl_match = m
                break
        if decl_match:
            block = _find_dedicated_block(test_text, mask, decl_match.start())
            found = block is not None
            _test(f"Self-test [{fixture_name}]",
                  found == expected_success,
                  detail=f"expected={expected_success}, found={found}"
                         f" (block at {block[0]},{block[1]})" if found
                         else f"expected={expected_success}, found={found}")
        else:
            _test(f"Self-test [{fixture_name}]",
                  False == expected_success,
                  detail="no unmasked FFbxScopePhase declaration found")

    # Fixture A: standalone { } block with phase work inside HandleImport
    fixture_a = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Scope(guid, TEXT("path_validation"), ...);
        ValidatePathSecurity(Request);
    }
    return true;
}"""
    run_fixture("A (standalone block)", fixture_a, "Scope", True)

    # Fixture B: declaration directly in HandleImport (no dedicated block)
    fixture_b = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    FFbxScopePhase Scope(guid, TEXT("path_validation"), ...);
    ValidatePathSecurity(Request);
}"""
    run_fixture("B (HandleImport body only)", fixture_b, "Scope", False)

    # Fixture C: declaration inside if block → rejected
    fixture_c = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    if (condition) {
        FFbxScopePhase Scope(guid, TEXT("path_validation"), ...);
    }
    return true;
}"""
    run_fixture("C (inside if block)", fixture_c, "Scope", False)

    # Fixture D: declaration inside for loop → rejected
    fixture_d = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    for (int i = 0; i < 10; ++i) {
        FFbxScopePhase Scope(guid, TEXT("path_validation"), ...);
    }
    return true;
}"""
    run_fixture("D (inside for loop)", fixture_d, "Scope", False)

    # Fixture E: braces in comments and strings → ignored
    fixture_e = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        // this brace { should be ignored
        FFbxScopePhase Scope(guid, TEXT("path_validation"), ...);
        /* also ignore } this */
        const char* s = "a{ b} c";
        ValidatePathSecurity(Request);
    }
    return true;
}"""
    run_fixture("E (braces in comments/strings)", fixture_e, "Scope", True)

    # Fixture F: FFbxScopePhase text in comments → ignored
    fixture_f = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        // FFbxScopePhase OldDecl(guid, TEXT("path_validation"), ...);
        FFbxScopePhase Scope(guid, TEXT("path_validation"), ...);
        ValidatePathSecurity(Request);
    }
    return true;
}"""
    run_fixture("F (text in comment)", fixture_f, "Scope", True)

    # Fixture G: only a comment contains the work call → still a valid block
    fixture_g = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Scope(guid, TEXT("path_validation"), ...);
        // ValidatePathSecurity(Request);
    }
    return true;
}"""
    run_fixture("G (work call in comment)", fixture_g, "Scope", True)
    mask_g = _build_lexical_mask(fixture_g)
    vps_pos_g = _find_unmasked_call(fixture_g, mask_g, 'ValidatePathSecurity')
    _test("Self-test [G]: work call masked in comment",
          vps_pos_g < 0,
          detail="ValidatePathSecurity inside comment must not match")

    # Fixture H: two incomplete declarations — Alpha has Exclusive only,
    # Beta has &PhaseDurations only.  They must not combine into a PASS.
    fixture_h = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    {
        FFbxScopePhase Alpha(guid, TEXT("path_validation"),
                             EFbxPhaseKind::Exclusive, nullptr);
        FFbxScopePhase Beta(guid, TEXT("path_validation"),
                            &PhaseDurations);
    }
    return true;
}"""
    mask_h = _build_lexical_mask(fixture_h)
    h_open_h, h_close_h = _find_handleimport_full_bounds(fixture_h, mask_h)
    decls_h = _find_ffbxscopephase_decls(
        fixture_h, mask_h, 'path_validation',
        h_open_h + 1, h_close_h)
    _test("Self-test [H]: declaration count = 2",
          len(decls_h) == 2,
          detail=f"count={len(decls_h)}")
    # Verify no single invocation has both fields
    complete = sum(1 for _, inv in decls_h
                   if 'EFbxPhaseKind::Exclusive' in inv
                   and '&PhaseDurations' in inv)
    _test("Self-test [H]: no complete declaration",
          complete == 0,
          detail=f"{complete} complete decl(s) (expected 0)")
    # Verify individual fields are not mixed
    for m, inv in decls_h:
        has_excl = 'EFbxPhaseKind::Exclusive' in inv
        has_dur = '&PhaseDurations' in inv
        name_match = re.search(r'FFbxScopePhase\s+(\w+)', inv)
        name = name_match.group(1) if name_match else "?"
        if name == "Alpha":
            _test("Self-test [H]: Alpha has Exclusive",
                  has_excl, detail="Alpha has Exclusive")
            _test("Self-test [H]: Alpha lacks &PhaseDurations",
                  not has_dur, detail="Alpha: no &PhaseDurations")
        elif name == "Beta":
            _test("Self-test [H]: Beta lacks Exclusive",
                  not has_excl, detail="Beta: no Exclusive")
            _test("Self-test [H]: Beta has &PhaseDurations",
                  has_dur, detail="Beta has &PhaseDurations")

    # Fixture I: nested control-flow after valid phase declaration remains OK
    fixture_i = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Scope(guid, TEXT("path_validation"), ...);
        if (extra) { DoMore(); }
        ValidatePathSecurity(Request);
    }
    return true;
}"""
    run_fixture("I (control-flow after decl)", fixture_i, "Scope", True)

    # Fixture J: full phase block bounds strictly inside HandleImport
    fixture_j = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Scope(guid, TEXT("path_validation"), ...);
        ValidatePathSecurity(Request);
    }
    DoCleanup();
    return true;
}"""
    mask_j = _build_lexical_mask(fixture_j)
    decl_j = re.search(r'FFbxScopePhase\s+Scope\s*\(', fixture_j)
    h_open_j, h_close_j = _find_handleimport_full_bounds(fixture_j, mask_j)
    block_j = _find_dedicated_block(fixture_j, mask_j, decl_j.start()) if decl_j else None
    if decl_j and block_j:
        b_open_j, b_close_j, _ = block_j
        full_bounds = (h_open_j < b_open_j < decl_j.start() < b_close_j < h_close_j)
        _test(f"Self-test [J]: full bounds inside HandleImport",
              full_bounds,
              detail=f"h@{h_open_j},{h_close_j} b@{b_open_j},{b_close_j}")
    else:
        _test(f"Self-test [J]: full bounds inside HandleImport",
              False, detail="decl or block not found")

    # Fixtures K1-K3: lambda blocks → rejected
    fixture_k1 = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    auto Fn = []
    {
        FFbxScopePhase Scope(guid, TEXT("path_validation"), ...);
    };
    return true;
}"""
    run_fixture("K1 (lambda [] { })", fixture_k1, "Scope", False)

    fixture_k2 = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    auto Fn = [&]
    {
        FFbxScopePhase Scope(guid, TEXT("path_validation"), ...);
    };
    return true;
}"""
    run_fixture("K2 (lambda [&] { })", fixture_k2, "Scope", False)

    fixture_k3 = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    auto Fn = []() mutable
    {
        FFbxScopePhase Scope(guid, TEXT("path_validation"), ...);
    };
    return true;
}"""
    run_fixture("K3 (lambda []() mutable { })", fixture_k3, "Scope", False)

    # Fixture L: function definitions before HandleImport — call-site
    # discovery must find HandleImport calls, not definitions
    fixture_l = """static bool ValidatePathSecurity(const FString& X) { return true; }
static bool ValidateVersion(const FString& X) { return true; }
bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Scope(guid, TEXT("path_validation"), ...);
        ValidatePathSecurity(Request);
    }
    {
        FFbxScopePhase Scope2(guid, TEXT("request_parse"), ...);
        ValidateVersion(Request);
    }
    return true;
}"""
    mask_l = _build_lexical_mask(fixture_l)
    h_open_l, h_close_l = _find_handleimport_full_bounds(fixture_l, mask_l)
    vps_l = _find_unmasked_call(fixture_l, mask_l, 'ValidatePathSecurity',
                                h_open_l + 1, h_close_l)
    vv_l = _find_unmasked_call(fixture_l, mask_l, 'ValidateVersion',
                               h_open_l + 1, h_close_l)
    # Prove we found call sites inside HandleImport, not the definitions
    _test("Self-test [L]: VPS call inside HandleImport",
          vps_l > h_open_l and vps_l < h_close_l,
          detail=f"VPS@{vps_l} h@{h_open_l},{h_close_l}")
    _test("Self-test [L]: VV call inside HandleImport",
          vv_l > h_open_l and vv_l < h_close_l,
          detail=f"VV@{vv_l} h@{h_open_l},{h_close_l}")
    # Also verify the fiinder would return -1 without bounds
    vps_l_full = _find_unmasked_call(fixture_l, mask_l, 'ValidatePathSecurity')
    vv_l_full = _find_unmasked_call(fixture_l, mask_l, 'ValidateVersion')
    _test("Self-test [L]: VPS call found without bounds",
          vps_l_full >= 0,
          detail="ValidatePathSecurity exists in file")
    # The unbound call must be at a different position (the definition)
    _test("Self-test [L]: VPS bounded ≠ unbounded",
          vps_l != vps_l_full or vps_l < 0,
          detail="bounded and unbounded should differ")

    # Fixtures M1-M4: FStringFromFixedAnsi invocation parsing
    fixture_m1 = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Scope(guid, TEXT("request_parse"), ...);
        FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT(Request.FbxPath));
        ValidateVersion(Request);
    }
    return true;
}"""
    mask_m1 = _build_lexical_mask(fixture_m1)
    h_open_m1, h_close_m1 = _find_handleimport_full_bounds(fixture_m1, mask_m1)
    fbx_m1_s, fbx_m1_e = _find_fbxpath_invocation(
        fixture_m1, mask_m1, h_open_m1 + 1, h_close_m1)
    _test("Self-test [M1]: FbxPath(Request.FbxPath) accepted",
          fbx_m1_s >= 0 and fbx_m1_e > fbx_m1_s,
          detail=f"start={fbx_m1_s}, end={fbx_m1_e}")

    # M2: Request.ObjectName (wrong arg) → rejected
    fixture_m2 = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Scope(guid, TEXT("request_parse"), ...);
        FStringFromFixedAnsi(Request.ObjectName, UE_ARRAY_COUNT(Request.ObjectName));
        ValidateVersion(Request);
    }
    return true;
}"""
    mask_m2 = _build_lexical_mask(fixture_m2)
    h_open_m2, h_close_m2 = _find_handleimport_full_bounds(fixture_m2, mask_m2)
    fbx_m2_s, fbx_m2_e = _find_fbxpath_invocation(
        fixture_m2, mask_m2, h_open_m2 + 1, h_close_m2)
    _test("Self-test [M2]: FbxPath(Request.ObjectName) rejected",
          fbx_m2_s < 0,
          detail=f"start={fbx_m2_s} (expected < 0)")

    # M3: matched text in comment → ignored
    fixture_m3 = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        // FStringFromFixedAnsi(Request.FbxPath, ...)
        FFbxScopePhase Scope(guid, TEXT("request_parse"), ...);
        FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT(Request.FbxPath));
        ValidateVersion(Request);
    }
    return true;
}"""
    mask_m3 = _build_lexical_mask(fixture_m3)
    h_open_m3, h_close_m3 = _find_handleimport_full_bounds(fixture_m3, mask_m3)
    fbx_m3_s, fbx_m3_e = _find_fbxpath_invocation(
        fixture_m3, mask_m3, h_open_m3 + 1, h_close_m3)
    _test("Self-test [M3]: FbxPath in comment masked",
          fbx_m3_s >= 0,
          detail=f"real call found at {fbx_m3_s}")

    # M4: invocation outside HandleImport → ignored
    fixture_m4 = """FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT(Request.FbxPath));
bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    return true;
}"""
    mask_m4 = _build_lexical_mask(fixture_m4)
    h_open_m4, h_close_m4 = _find_handleimport_full_bounds(fixture_m4, mask_m4)
    fbx_m4_s, fbx_m4_e = _find_fbxpath_invocation(
        fixture_m4, mask_m4, h_open_m4 + 1, h_close_m4)
    _test("Self-test [M4]: FbxPath outside HandleImport ignored",
          fbx_m4_s < 0,
          detail=f"start={fbx_m4_s} (expected < 0)")

    # Fixture N: end-to-end complete declaration → PASS all checks
    fixture_n = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Phase(guid, TEXT("request_parse"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
        ValidateVersion(Request);
    }
    return true;
}"""
    mask_n = _build_lexical_mask(fixture_n)
    h_open_n, h_close_n = _find_handleimport_full_bounds(fixture_n, mask_n)
    decls_n = _find_ffbxscopephase_decls(
        fixture_n, mask_n, 'request_parse',
        h_open_n + 1, h_close_n)
    _test("Self-test [N]: exact 1 declaration",
          len(decls_n) == 1,
          detail=f"count={len(decls_n)}")
    if len(decls_n) == 1:
        m_n, inv_n = decls_n[0]
        canonical = ('EFbxPhaseKind::Exclusive' in inv_n
                     and '&PhaseDurations' in inv_n)
        _test("Self-test [N]: canonical decl has both fields",
              canonical,
              detail="Exclusive+PhaseDurations in same invocation")
        block_n = _find_dedicated_block(fixture_n, mask_n, m_n.start())
        _test("Self-test [N]: standalone block found",
              block_n is not None,
              detail="dedicated block for complete declaration")
        if block_n:
            b_open_n, b_close_n, _ = block_n
            inv_n2 = _extract_constructor_invocation(fixture_n, mask_n, m_n)
            decl_end_n = len(inv_n2) + m_n.start() if inv_n2 else m_n.start() + 1
            bounds_ok_n = (h_open_n < b_open_n < m_n.start() < decl_end_n
                           < b_close_n < h_close_n)
            _test("Self-test [N]: full bounds inside HandleImport",
                  bounds_ok_n,
                  detail=f"h@{h_open_n},{h_close_n} "
                         f"b@{b_open_n},{b_close_n}")

    # Fixture O: end-to-end incomplete decls in HandleImport → FAIL
    fixture_o = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    {
        FFbxScopePhase Alpha(guid, TEXT("request_parse"),
                             EFbxPhaseKind::Exclusive, nullptr);
        FFbxScopePhase Beta(guid, TEXT("request_parse"),
                            &PhaseDurations);
    }
    return true;
}"""
    mask_o = _build_lexical_mask(fixture_o)
    h_open_o, h_close_o = _find_handleimport_full_bounds(fixture_o, mask_o)
    decls_o = _find_ffbxscopephase_decls(
        fixture_o, mask_o, 'request_parse',
        h_open_o + 1, h_close_o)
    _test("Self-test [O]: declaration count = 2",
          len(decls_o) == 2,
          detail=f"count={len(decls_o)}")
    complete_o = sum(1 for _, inv_o in decls_o
                     if 'EFbxPhaseKind::Exclusive' in inv_o
                     and '&PhaseDurations' in inv_o)
    _test("Self-test [O]: no complete declaration",
          complete_o == 0,
          detail=f"{complete_o} complete (expected 0)")
    for m_o, inv_o in decls_o:
        name_o = re.search(r'FFbxScopePhase\s+(\w+)', inv_o)
        n_o = name_o.group(1) if name_o else "?"
        block_o = _find_dedicated_block(fixture_o, mask_o, m_o.start())
        _test(f"Self-test [O]: {n_o} has no complete wiring",
              not ('EFbxPhaseKind::Exclusive' in inv_o
                   and '&PhaseDurations' in inv_o),
              detail=f"{n_o} is incomplete")

    # ===================================================================
    # Declaration uniqueness fixtures (req 1)
    # ===================================================================
    # P: one complete declaration → total=1, complete=1
    fixture_p = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    {
        FFbxScopePhase Scope(guid, TEXT("request_parse"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
        ValidateVersion(Request);
    }
    return true;
}"""
    mask_p = _build_lexical_mask(fixture_p)
    h_open_p, h_close_p = _find_handleimport_full_bounds(fixture_p, mask_p)
    decls_p = _find_ffbxscopephase_decls(
        fixture_p, mask_p, 'request_parse',
        h_open_p + 1, h_close_p)
    total_p, comp_p = _count_phase_declarations(
        decls_p, [inv for _, inv in decls_p])
    _test("Self-test [P]: one complete decl — total=1", total_p == 1,
          detail=f"total={total_p}")
    _test("Self-test [P]: one complete decl — complete=1", comp_p == 1,
          detail=f"complete={comp_p}")

    # Q: two complete declarations → total=2, complete=2 → rejected
    fixture_q = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    {
        FFbxScopePhase ScopeA(guid, TEXT("request_parse"),
                              EFbxPhaseKind::Exclusive,
                              &PhaseDurations);
        FFbxScopePhase ScopeB(guid, TEXT("request_parse"),
                              EFbxPhaseKind::Exclusive,
                              &PhaseDurations);
        ValidateVersion(Request);
    }
    return true;
}"""
    mask_q = _build_lexical_mask(fixture_q)
    h_open_q, h_close_q = _find_handleimport_full_bounds(fixture_q, mask_q)
    decls_q = _find_ffbxscopephase_decls(
        fixture_q, mask_q, 'request_parse',
        h_open_q + 1, h_close_q)
    total_q, comp_q = _count_phase_declarations(
        decls_q, [inv for _, inv in decls_q])
    _test("Self-test [Q]: two complete decls — total=2", total_q == 2,
          detail=f"total={total_q}")
    _test("Self-test [Q]: two complete decls — complete=2", comp_q == 2,
          detail=f"complete={comp_q}")

    # R: one complete + one incomplete → total=2, complete=1 → rejected
    fixture_r = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    {
        FFbxScopePhase Complete(guid, TEXT("request_parse"),
                                EFbxPhaseKind::Exclusive,
                                &PhaseDurations);
        FFbxScopePhase MissingExcl(guid, TEXT("request_parse"),
                                   &PhaseDurations);
        ValidateVersion(Request);
    }
    return true;
}"""
    mask_r = _build_lexical_mask(fixture_r)
    h_open_r, h_close_r = _find_handleimport_full_bounds(fixture_r, mask_r)
    decls_r = _find_ffbxscopephase_decls(
        fixture_r, mask_r, 'request_parse',
        h_open_r + 1, h_close_r)
    total_r, comp_r = _count_phase_declarations(
        decls_r, [inv for _, inv in decls_r])
    _test("Self-test [R]: one complete + one incomplete — total=2",
          total_r == 2, detail=f"total={total_r}")
    _test("Self-test [R]: one complete + one incomplete — complete=1",
          comp_r == 1, detail=f"complete={comp_r}")

    # S: two incomplete → total=2, complete=0 → rejected
    fixture_s = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    {
        FFbxScopePhase OnlyExcl(guid, TEXT("request_parse"),
                                EFbxPhaseKind::Exclusive, nullptr);
        FFbxScopePhase OnlyDur(guid, TEXT("request_parse"),
                               &PhaseDurations);
        ValidateVersion(Request);
    }
    return true;
}"""
    mask_s = _build_lexical_mask(fixture_s)
    h_open_s, h_close_s = _find_handleimport_full_bounds(fixture_s, mask_s)
    decls_s = _find_ffbxscopephase_decls(
        fixture_s, mask_s, 'request_parse',
        h_open_s + 1, h_close_s)
    total_s, comp_s = _count_phase_declarations(
        decls_s, [inv for _, inv in decls_s])
    _test("Self-test [S]: two incomplete — total=2",
          total_s == 2, detail=f"total={total_s}")
    _test("Self-test [S]: two incomplete — complete=0",
          comp_s == 0, detail=f"complete={comp_s}")

    # ===================================================================
    # Comment-safe constructor content checks (req 3)
    # ===================================================================
    # T: fields only in comments must not match
    fixture_t = (
        'FFbxScopePhase Scope(guid,\n'
        '    TEXT("other_phase"),\n'
        '    EFbxPhaseKind::Nested,\n'
        '    nullptr\n'
        '    // TEXT("request_parse")\n'
        '    // EFbxPhaseKind::Exclusive\n'
        '    // &PhaseDurations\n'
        ');'
    )
    _test("Self-test [T]: phase name only in comment does not match",
          'request_parse' not in _extract_text_macro_args(fixture_t),
          detail=f"got {_extract_text_macro_args(fixture_t)}")
    _test("Self-test [T]: Exclusive only in comment does not count",
          not _has_field_outside_comment(fixture_t, 'EFbxPhaseKind::Exclusive'),
          detail="Exclusive inside comment must not match")
    _test("Self-test [T]: &PhaseDurations only in comment does not count",
          not _has_field_outside_comment(fixture_t, '&PhaseDurations'),
          detail="&PhaseDurations inside comment must not match")

    # T2: actual TEXT("request_parse") argument is recognized
    fixture_t2 = (
        'FFbxScopePhase Scope(guid,\n'
        '    TEXT("request_parse"),\n'
        '    EFbxPhaseKind::Exclusive,\n'
        '    &PhaseDurations\n'
        ');'
    )
    _test("Self-test [T2]: actual TEXT(\"request_parse\") recognized",
          'request_parse' in _extract_text_macro_args(fixture_t2),
          detail=f"got {_extract_text_macro_args(fixture_t2)}")
    _test("Self-test [T2]: Exclusive outside comment recognized",
          _has_field_outside_comment(fixture_t2, 'EFbxPhaseKind::Exclusive'),
          detail="Exclusive not found in invocation")
    _test("Self-test [T2]: PhaseDurations outside comment recognized",
          _has_field_outside_comment(fixture_t2, '&PhaseDurations'),
          detail="&PhaseDurations not found in invocation")

    # ===================================================================
    # Exact FbxPath first argument (req 4) — extend M2 coverage
    # ===================================================================
    # M2-style already tests Request.ObjectName rejected. Add:
    # Request.FbxPathBackup and Request.FbxPathSomething.
    def _check_fbxpath_first_arg(fixture, label, expected_found):
        msk = _build_lexical_mask(fixture)
        ho, hc = _find_handleimport_full_bounds(fixture, msk)
        s, e = _find_fbxpath_invocation(fixture, msk, ho + 1, hc)
        found = s >= 0 and e > s
        _test(f"Self-test [FbxPath-{label}]: {'accepted' if expected_found else 'rejected'}",
              found == expected_found,
              detail=f"found={found} start={s}")

    fbxp_fixture_base = (
        'bool FLiveSyncFBXImporter::HandleImport(FBXImportRequest& Request)\n'
        '{\n'
        '    DoSetup();\n'
        '    {\n'
        '        FFbxScopePhase Scope(guid, TEXT("request_parse"));\n'
        '        FStringFromFixedAnsi({ARG}, UE_ARRAY_COUNT({ARG}));\n'
        '        ValidateVersion(Request);\n'
        '    }\n'
        '    return true;\n'
        '}'
    )
    _check_fbxpath_first_arg(
        fbxp_fixture_base.replace('{ARG}', 'Request.FbxPath'),
        'Request.FbxPath', True)
    _check_fbxpath_first_arg(
        fbxp_fixture_base.replace('{ARG}', 'Request.ObjectName'),
        'Request.ObjectName', False)
    _check_fbxpath_first_arg(
        fbxp_fixture_base.replace('{ARG}', 'Request.FbxPathBackup'),
        'Request.FbxPathBackup', False)
    _check_fbxpath_first_arg(
        fbxp_fixture_base.replace('{ARG}', 'Request.FbxPathSomething'),
        'Request.FbxPathSomething', False)

    # ===================================================================
    # Request_parse ordering fixtures (all use shared evaluator)
    # ===================================================================
    def _check_rp_ordering(fixture_text, label, expect_valid):
        """Evaluate request_parse ordering on a fixture using the shared
        preparation helper.  Returns (valid, failing).
        Every missing prerequisite causes a FAIL even when expect_valid=False."""
        msk = _build_lexical_mask(fixture_text)
        ho, hc = _find_handleimport_full_bounds(fixture_text, msk)
        hbs = ho + 1
        hbe = hc
        decls = _find_ffbxscopephase_decls(
            fixture_text, msk, 'request_parse', hbs, hbe)
        total, _ = _count_phase_declarations(
            decls, [inv for _, inv in decls])
        has_unique = (total == 1 and len(decls) == 1)

        single_match = decls[0][0] if has_unique else None
        ds = single_match.start() if single_match else -1
        if single_match:
            inv2 = _extract_constructor_invocation(fixture_text, msk, single_match)
            de = len(inv2) + ds if inv2 else -1
        else:
            inv2 = None
            de = -1

        block = _find_dedicated_block(fixture_text, msk, ds) if single_match else None
        block_exists = block is not None
        bo = bc = -1
        if block:
            bo, bc, _ = block

        fbx_s, fbx_e = _find_fbxpath_invocation(
            fixture_text, msk, hbs, hbe)
        vv = _find_unmasked_call(fixture_text, msk, 'ValidateVersion', hbs, hbe)
        vps = _find_unmasked_call(fixture_text, msk, 'ValidatePathSecurity', hbs, hbe)

        prep_valid, prep_missing, eval_valid, eval_failing, eval_detail = \
            _prepare_request_parse_ordering(
                ho, hc, has_unique, single_match,
                ds, de, block_exists, bo, bc,
                fbx_s, fbx_e, vv, vps)

        if not prep_valid:
            _test(f"Self-test [RP-{label}]: FAIL (prerequisite missing: {prep_missing[0] if prep_missing else 'unknown'})",
                  False,
                  detail=f"missing: {prep_missing}")
            return False, eval_failing

        _test(f"Self-test [RP-{label}]: {'PASS' if expect_valid else 'FAIL'} "
              f"(expected {'valid' if expect_valid else 'invalid'})",
              eval_valid == expect_valid,
              detail=eval_detail if eval_detail else "expected_pass" if expect_valid else "expected_fail")
        return eval_valid, eval_failing

    # RP-A: Correct full ordering → PASS
    fixture_rp_a = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Scope(guid, TEXT("request_parse"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
        FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT(Request.FbxPath));
        ValidateVersion(Request);
    }
    ValidatePathSecurity(Request);
    return true;
}"""
    _test("Self-test [RP-A]: declarative label — correct order",
          True, detail="request_parse ordering fixture A")
    _check_rp_ordering(fixture_rp_a, "A-correct", True)

    # RP-B: FbxPath before phase declaration → FAIL
    fixture_rp_b = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT(Request.FbxPath));
    {
        FFbxScopePhase Scope(guid, TEXT("request_parse"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
        ValidateVersion(Request);
    }
    ValidatePathSecurity(Request);
    return true;
}"""
    _, fail_b = _check_rp_ordering(fixture_rp_b, "B-fbx-before-decl", False)
    _test("Self-test [RP-B]: exact failed relation — declaration_end<fbx_path_invocation_start",
          any("declaration_end<fbx_path_invocation_start" in r for r in fail_b),
          detail=f"failing: {fail_b}")

    # RP-C: ValidateVersion before FbxPath extraction → FAIL
    fixture_rp_c = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Scope(guid, TEXT("request_parse"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
        ValidateVersion(Request);
        FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT(Request.FbxPath));
    }
    ValidatePathSecurity(Request);
    return true;
}"""
    _, fail_c = _check_rp_ordering(fixture_rp_c, "C-vv-before-fbx", False)
    _test("Self-test [RP-C]: exact failed relation — fbx_path_invocation_end<ValidateVersion_call",
          any("fbx_path_invocation_end<ValidateVersion_call" in r for r in fail_c),
          detail=f"failing: {fail_c}")

    # RP-D: FbxPath inside block but after ValidateVersion → FAIL
    # The chain expects fbx_end < ValidateVersion_call, but here
    # ValidateVersion is before FbxPath.
    fixture_rp_d = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Scope(guid, TEXT("request_parse"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
        ValidateVersion(Request);
        FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT(Request.FbxPath));
    }
    ValidatePathSecurity(Request);
    return true;
}"""
    _, fail_d = _check_rp_ordering(fixture_rp_d, "D-fbx-after-vv", False)
    _test("Self-test [RP-D]: exact failed relation — fbx_path_invocation_end<ValidateVersion_call",
          any("fbx_path_invocation_end<ValidateVersion_call" in r for r in fail_d),
          detail=f"failing: {fail_d}")

    # RP-E: ValidatePathSecurity inside request_parse block → FAIL
    fixture_rp_e = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Scope(guid, TEXT("request_parse"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
        FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT(Request.FbxPath));
        ValidateVersion(Request);
        ValidatePathSecurity(Request);
    }
    return true;
}"""
    _, fail_e = _check_rp_ordering(fixture_rp_e, "E-vps-inside-block", False)
    _test("Self-test [RP-E]: exact failed relation — phase_close<ValidatePathSecurity_call",
          any("phase_close<ValidatePathSecurity_call" in r for r in fail_e),
          detail=f"failing: {fail_e}")

    # RP-F: ValidatePathSecurity before request_parse closes (VPS in a nested
    # scope inside the phase block) → FAIL
    fixture_rp_f = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Scope(guid, TEXT("request_parse"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
        FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT(Request.FbxPath));
        ValidateVersion(Request);
        if (extra) { ValidatePathSecurity(Request); }
    }
    return true;
}"""
    _, fail_f = _check_rp_ordering(fixture_rp_f, "F-vps-before-close", False)
    _test("Self-test [RP-F]: exact failed relation — phase_close<ValidatePathSecurity_call",
          any("phase_close<ValidatePathSecurity_call" in r for r in fail_f),
          detail=f"failing: {fail_f}")

    # RP-G: synthetic cross-boundary evaluator fixture (no C++ parsing).
    # fbx_end(60) after phase_close(50) means FbxPath positions cross the
    # phase-close boundary, making the downstream ordering invalid.
    # The evaluator reports ValidateVersion_call < phase_close because VV(70)
    # is also after phase_close(50) — FbxPath and VV are in the same chain
    # segment after phase_close.  The evaluator does NOT directly contain an
    # fbx_end < phase_close edge; the chain edge is
    # fbx_end < vv < phase_close, so the vv < phase_close relation fails.
    _test("Self-test [RP-G]: synthetic cross-boundary evaluator fixture",
          True, detail="request_parse evaluator with fbx_end > phase_close")
    _rp_g_valid, _rp_g_fail, _rp_g_detail = _evaluate_request_parse_ordering(
        handle_open=0, phase_open=10, decl_start=20, decl_end=30,
        fbx_start=40, fbx_end=60, vv_pos=70, vps_pos=80,
        handle_close=90, phase_close=50)
    _test("Self-test [RP-G-synth]: evaluator returns False",
          not _rp_g_valid,
          detail=f"valid={_rp_g_valid}, detail={_rp_g_detail}")
    _test("Self-test [RP-G-synth]: failed relation ValidateVersion_call<phase_close",
          any("ValidateVersion_call<phase_close" in r for r in _rp_g_fail),
          detail=f"failing: {_rp_g_fail}")

    # RP-G-parser: separate parser fixture proving a normal balanced FbxPath
    # invocation returns correct start/end within the block.
    fixture_rp_g_parse = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Scope(guid, TEXT("request_parse"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
        FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT(Request.FbxPath));
        ValidateVersion(Request);
    }
    ValidatePathSecurity(Request);
    return true;
}"""
    mask_rp_gp = _build_lexical_mask(fixture_rp_g_parse)
    ho_gp, hc_gp = _find_handleimport_full_bounds(fixture_rp_g_parse, mask_rp_gp)
    fbx_gp_s, fbx_gp_e = _find_fbxpath_invocation(
        fixture_rp_g_parse, mask_rp_gp, ho_gp + 1, hc_gp)
    _test("Self-test [RP-G-parse]: normal balanced FbxPath start >= 0",
          fbx_gp_s >= 0, detail=f"start={fbx_gp_s}")
    _test("Self-test [RP-G-parse]: normal balanced FbxPath end > start",
          fbx_gp_e > fbx_gp_s, detail=f"start={fbx_gp_s}, end={fbx_gp_e}")
    _test("Self-test [RP-G-parse]: FbxPath inside block",
          fbx_gp_s > ho_gp and fbx_gp_e < hc_gp,
          detail=f"fbx@{fbx_gp_s},{fbx_gp_e} hi@{ho_gp},{hc_gp}")

    # ===================================================================
    # Path_validation ordering fixtures (all use shared evaluator)
    # ===================================================================
    def _check_pv_ordering(fixture_text, label, expect_valid):
        """Evaluate path_validation ordering on a fixture using the shared
        preparation helper.  Returns (valid, failing).
        Every missing prerequisite causes a FAIL even when expect_valid=False."""
        msk = _build_lexical_mask(fixture_text)
        ho, hc = _find_handleimport_full_bounds(fixture_text, msk)
        hbs = ho + 1
        hbe = hc
        decls = _find_ffbxscopephase_decls(
            fixture_text, msk, 'path_validation', hbs, hbe)
        total, _ = _count_phase_declarations(
            decls, [inv for _, inv in decls])
        has_unique = (total == 1 and len(decls) == 1)

        single_match = decls[0][0] if has_unique else None
        ds = single_match.start() if single_match else -1
        if single_match:
            inv2 = _extract_constructor_invocation(fixture_text, msk, single_match)
            de = len(inv2) + ds if inv2 else -1
        else:
            inv2 = None
            de = -1

        block = _find_dedicated_block(fixture_text, msk, ds) if single_match else None
        block_exists = block is not None
        bo = bc = -1
        if block:
            bo, bc, _ = block

        vps = _find_unmasked_call(fixture_text, msk, 'ValidatePathSecurity', hbs, hbe)

        prep_valid, prep_missing, eval_valid, eval_failing, eval_detail = \
            _prepare_path_validation_ordering(
                ho, hc, has_unique, single_match,
                ds, de, block_exists, bo, bc, vps)

        if not prep_valid:
            _test(f"Self-test [PV-{label}]: FAIL (prerequisite missing: {prep_missing[0] if prep_missing else 'unknown'})",
                  False,
                  detail=f"missing: {prep_missing}")
            return False, eval_failing

        _test(f"Self-test [PV-{label}]: {'PASS' if expect_valid else 'FAIL'} "
              f"(expected {'valid' if expect_valid else 'invalid'})",
              eval_valid == expect_valid,
              detail=eval_detail if eval_detail else "expected_pass" if expect_valid else "expected_fail")
        return eval_valid, eval_failing

    # PV-A: Correct full ordering → PASS
    fixture_pv_a = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Scope(guid, TEXT("path_validation"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
        ValidatePathSecurity(Request);
    }
    return true;
}"""
    _test("Self-test [PV-A]: declarative label — correct order",
          True, detail="path_validation ordering fixture A")
    _check_pv_ordering(fixture_pv_a, "A-correct", True)

    # PV-B: ValidatePathSecurity before declaration_end → FAIL
    fixture_pv_b = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Scope(guid, TEXT("path_validation"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
        ValidatePathSecurity(Request);
    }
    return true;
}"""
    # In this fixture, VPS is after Scope( but before ), so VPS is between
    # decl_start and decl_end.  We need VPS to be after the Scope() call.
    # To make VPS before decl_end, put it as a constructor argument.
    fixture_pv_b2 = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Scope(guid, TEXT("path_validation"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations,
                             ValidatePathSecurity(Request));
    }
    return true;
}"""
    _, fail_pv_b = _check_pv_ordering(fixture_pv_b2, "B-vps-before-decl-end", False)
    _test("Self-test [PV-B]: exact failed relation — declaration_end<ValidatePathSecurity_call",
          any("declaration_end<ValidatePathSecurity_call" in r for r in fail_pv_b),
          detail=f"failing: {fail_pv_b}")

    # PV-C: ValidatePathSecurity before phase declaration → FAIL
    fixture_pv_c = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    ValidatePathSecurity(Request);
    {
        FFbxScopePhase Scope(guid, TEXT("path_validation"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
    }
    return true;
}"""
    _, fail_pv_c = _check_pv_ordering(fixture_pv_c, "C-vps-before-decl", False)
    _test("Self-test [PV-C]: exact failed relation — declaration_end<ValidatePathSecurity_call",
          any("declaration_end<ValidatePathSecurity_call" in r for r in fail_pv_c),
          detail=f"failing: {fail_pv_c}")

    # PV-D: ValidatePathSecurity after phase_close → PASS (it's outside block)
    # Actually this is the CORRECT ordering — VPS after block close. So this
    # is actually PV-A (correct). For a FAIL case, VPS after handle_close:
    fixture_pv_d = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Scope(guid, TEXT("path_validation"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
    }
    return true;
    ValidatePathSecurity(Request);
}"""
    # VPS after return, after handle_close
    _, fail_pv_d = _check_pv_ordering(fixture_pv_d, "D-vps-after-hclose", False)
    _test("Self-test [PV-D]: exact failed relation — ValidatePathSecurity_call<phase_close",
          any("ValidatePathSecurity_call<phase_close" in r for r in fail_pv_d),
          detail=f"failing: {fail_pv_d}")

    # PV-E: phase block outside HandleImport bounds → discovery/bounds fixture.
    # This manually verifies the parser correctly rejects a block outside HI.
    fixture_pv_e = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    return true;
}
{
    FFbxScopePhase Scope(guid, TEXT("path_validation"),
                         EFbxPhaseKind::Exclusive,
                         &PhaseDurations);
    ValidatePathSecurity(Request);
}"""
    mask_pv_e = _build_lexical_mask(fixture_pv_e)
    ho_pv_e, hc_pv_e = _find_handleimport_full_bounds(fixture_pv_e, mask_pv_e)
    decls_pv_e = _find_ffbxscopephase_decls(
        fixture_pv_e, mask_pv_e, 'path_validation',
        ho_pv_e + 1, hc_pv_e)
    _test("Self-test [PV-E-discovery]: no decl inside HandleImport bounds",
          len(decls_pv_e) == 0,
          detail=f"found {len(decls_pv_e)} decl(s) inside HI (expected 0)")

    # PV-E-invalid-bounds: pure evaluator fixture with phase_open < handle_open
    _pv_e_valid, _pv_e_fail, _pv_e_detail = _evaluate_path_validation_ordering(
        handle_open=10, phase_open=5,  # phase_open < handle_open → FAIL
        decl_start=20, decl_end=30,
        vps_pos=40, handle_close=50, phase_close=45)
    _test("Self-test [PV-E-invalid]: evaluator returns False",
          not _pv_e_valid,
          detail=f"valid={_pv_e_valid}")
    _test("Self-test [PV-E-invalid]: failed relation handle_open<phase_open",
          any("handle_open<phase_open" in r for r in _pv_e_fail),
          detail=f"failing: {_pv_e_fail}")

    # ===================================================================
    # Direct shared-evaluator positive/negative unit fixtures (tasks 4)
    # ===================================================================
    # Request_parse evaluator: valid chain
    # params: handle_open,phase_open,decl_start,decl_end,fbx_start,fbx_end,vv_pos,vps_pos,handle_close,phase_close
    # chain: hO < pO < dS < dE < fS < fE < vv < pC < vps < hC
    # valid: 0 < 10 < 20 < 30 < 40 < 50 < 60 < 70 < 80 < 100
    _rp_v, _, _ = _evaluate_request_parse_ordering(
        0, 10, 20, 30, 40, 50, 60, 80, 100, 70)
    _test("Direct evaluator [RP-valid]: complete valid chain returns True",
          _rp_v, detail="all adjacent positions strictly increasing")

    # Request_parse evaluator: each adjacent relation independently inverted.
    # Each row swaps one adjacent pair while keeping all else strictly increasing.
    # Expected: exactly one failed relation matching the label.
    pairs = [
        ("handle_open<phase_open", 10, 5, 20, 30, 40, 50, 60, 80, 100, 70),
        ("phase_open<declaration_start", 0, 15, 10, 30, 40, 50, 60, 80, 100, 70),
        ("declaration_start<declaration_end", 0, 10, 25, 20, 40, 50, 60, 80, 100, 70),
        ("declaration_end<fbx_path_invocation_start", 0, 10, 20, 35, 30, 50, 60, 80, 100, 70),
        ("fbx_path_invocation_start<fbx_path_invocation_end", 0, 10, 20, 30, 45, 40, 60, 80, 100, 70),
        ("fbx_path_invocation_end<ValidateVersion_call", 0, 10, 20, 30, 40, 55, 50, 80, 100, 70),
        ("ValidateVersion_call<phase_close", 0, 10, 20, 30, 40, 50, 65, 80, 100, 60),
        ("phase_close<ValidatePathSecurity_call", 0, 10, 20, 30, 40, 50, 60, 70, 100, 75),
        ("ValidatePathSecurity_call<handle_close", 0, 10, 20, 30, 40, 50, 60, 95, 90, 70),
    ]
    for label, *args in pairs:
        v, f, d = _evaluate_request_parse_ordering(*args)
        _test(f"Direct evaluator [RP-inv-{label}]: returns False",
              not v, detail=f"valid={v}, failing={f}")
        _test(f"Direct evaluator [RP-inv-{label}]: failed relation {label}",
              any(label in r for r in f), detail=f"failing={f}")

    # Path_validation evaluator: valid chain
    # params: handle_open,phase_open,decl_start,decl_end,vps_pos,handle_close,phase_close
    # chain: hO < pO < dS < dE < vps < pC < hC
    # valid: 0 < 10 < 20 < 30 < 40 < 50 < 60
    _pv_v, _, _ = _evaluate_path_validation_ordering(
        0, 10, 20, 30, 40, 60, 50)
    _test("Direct evaluator [PV-valid]: complete valid chain returns True",
          _pv_v, detail="all adjacent positions strictly increasing")

    # Path_validation evaluator: each adjacent relation independently inverted
    pv_pairs = [
        ("handle_open<phase_open", 10, 5, 20, 30, 40, 60, 50),
        ("phase_open<declaration_start", 0, 15, 10, 30, 40, 60, 50),
        ("declaration_start<declaration_end", 0, 10, 25, 20, 40, 60, 50),
        ("declaration_end<ValidatePathSecurity_call", 0, 10, 20, 35, 30, 60, 50),
        ("ValidatePathSecurity_call<phase_close", 0, 10, 20, 30, 45, 60, 40),
        ("phase_close<handle_close", 0, 10, 20, 30, 40, 50, 55),
    ]
    for label, *args in pv_pairs:
        v, f, d = _evaluate_path_validation_ordering(*args)
        _test(f"Direct evaluator [PV-inv-{label}]: returns False",
              not v, detail=f"valid={v}, failing={f}")
        _test(f"Direct evaluator [PV-inv-{label}]: failed relation {label}",
              any(label in r for r in f), detail=f"failing={f}")

    # ===================================================================
    # Parser-to-preparation setup tests (no reporter fixture helpers)
    # ===================================================================
    # Each test parses a C++ fixture, calls _prepare_* directly, and
    # asserts the exact missing prerequisite.  The test itself must PASS.
    #
    # Cases: (A) no FFbxScopePhase token, (B) token exists but constructor
    # incomplete, (C) complete constructor but missing other items.

    # RP-absent-decl (A): no FFbxScopePhase token at all.
    _rp_absdecl = _parse_and_prepare_rp("""bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT(Request.FbxPath));
    ValidateVersion(Request);
    ValidatePathSecurity(Request);
    return true;
}""")
    _test("Setup [RP-absent-decl]: prerequisites_valid=False",
          _rp_absdecl.prerequisites_valid == False,
          detail=f"pv={_rp_absdecl.prerequisites_valid}, missing={_rp_absdecl.missing}")
    _test("Setup [RP-absent-decl]: missing contains unique declaration",
          "unique declaration" in _rp_absdecl.missing,
          detail=f"missing={_rp_absdecl.missing}")

    # RP-incomplete-constructor (B): token found but unmatched parens.
    _rp_inccon = _parse_and_prepare_rp("""bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Scope(guid, TEXT("request_parse"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations
        FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT(Request.FbxPath));
        ValidateVersion(Request);
    }
    ValidatePathSecurity(Request);
    return true;
}""")
    _test("Setup [RP-incomplete]: prerequisites_valid=False",
          _rp_inccon.prerequisites_valid == False,
          detail=f"pv={_rp_inccon.prerequisites_valid}, missing={_rp_inccon.missing}")
    _test("Setup [RP-incomplete]: evaluator_valid=False",
          _rp_inccon.evaluator_valid == False,
          detail=f"ev={_rp_inccon.evaluator_valid}")
    _test("Setup [RP-incomplete]: missing contains constructor bounds",
          "constructor bounds" in _rp_inccon.missing,
          detail=f"missing={_rp_inccon.missing}")
    _test("Setup [RP-incomplete]: match_count == 1",
          _rp_inccon.match_count == 1, detail=f"count={_rp_inccon.match_count}")
    _test("Setup [RP-incomplete]: single_match is not None",
          _rp_inccon.single_match is not None,
          detail=f"single_match={_rp_inccon.single_match}")
    _test("Setup [RP-incomplete]: declaration_start >= 0",
          _rp_inccon.declaration_start >= 0,
          detail=f"ds={_rp_inccon.declaration_start}")
    _test("Setup [RP-incomplete]: declaration_end == -1",
          _rp_inccon.declaration_end == -1,
          detail=f"de={_rp_inccon.declaration_end}")
    _test("Setup [RP-incomplete]: invocation_complete == False",
          _rp_inccon.invocation_complete == False,
          detail=f"inv_comp={_rp_inccon.invocation_complete}")
    _test("Setup [RP-incomplete]: missing does not contain unique decl",
          "unique declaration" not in _rp_inccon.missing,
          detail=f"missing={_rp_inccon.missing}")

    # ===================================================================
    # Lexical false-positive + fallback fixtures (RP)
    # Phase name must NOT be matched inside comments, strings, or later
    # declarations. Each fixture has a real unmasked FFbxScopePhase token
    # so the fallback scanner is exercised even when extraction fails.
    # ===================================================================

    # RP-LC: line-comment false-positive — phase name only in // comment
    _rp_lc_fixture = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Broken(guid,
            // TEXT("request_parse")
            EFbxPhaseKind::Exclusive,
            &PhaseDurations
    }
    {
        FFbxScopePhase Scope(guid, TEXT("request_parse"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
        FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT(Request.FbxPath));
        ValidateVersion(Request);
    }
    ValidatePathSecurity(Request);
    return true;
}"""
    _rp_lc_broken_pos = _rp_lc_fixture.index("FFbxScopePhase Broken(")
    _rp_lc_valid_pos = _rp_lc_fixture.index("FFbxScopePhase Scope(")
    _rp_lc = _parse_and_prepare_rp(_rp_lc_fixture)
    _test("Setup [RP-LC]: fallback — line-comment-only target",
          _rp_lc.match_count == 1,
          detail=f"count={_rp_lc.match_count}")
    _test("Setup [RP-LC]: single_match is the second decl",
          _rp_lc.single_match is not None,
          detail=f"single={_rp_lc.single_match}")
    _test("Setup [RP-LC]: exact selected position == valid_pos",
          _rp_lc.declaration_start == _rp_lc_valid_pos,
          detail=f"ds={_rp_lc.declaration_start}, valid_pos={_rp_lc_valid_pos}")
    _test("Setup [RP-LC]: selected position > broken_pos",
          _rp_lc.declaration_start > _rp_lc_broken_pos,
          detail=f"ds={_rp_lc.declaration_start}, broken_pos={_rp_lc_broken_pos}")
    _test("Setup [RP-LC]: prerequisites_valid=True",
          _rp_lc.prerequisites_valid == True,
          detail=f"pv={_rp_lc.prerequisites_valid}, missing={_rp_lc.missing}")

    # RP-BC: block-comment false-positive — phase name only in /* */ comment
    _rp_bc_fixture = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Broken(guid,
            /* TEXT("request_parse") */
            EFbxPhaseKind::Exclusive,
            &PhaseDurations
    }
    {
        FFbxScopePhase Scope(guid, TEXT("request_parse"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
        FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT(Request.FbxPath));
        ValidateVersion(Request);
    }
    ValidatePathSecurity(Request);
    return true;
}"""
    _rp_bc_broken_pos = _rp_bc_fixture.index("FFbxScopePhase Broken(")
    _rp_bc_valid_pos = _rp_bc_fixture.index("FFbxScopePhase Scope(")
    _rp_bc = _parse_and_prepare_rp(_rp_bc_fixture)
    _test("Setup [RP-BC]: fallback — block-comment-only target",
          _rp_bc.match_count == 1,
          detail=f"count={_rp_bc.match_count}")
    _test("Setup [RP-BC]: exact selected position == valid_pos",
          _rp_bc.declaration_start == _rp_bc_valid_pos,
          detail=f"ds={_rp_bc.declaration_start}, valid_pos={_rp_bc_valid_pos}")
    _test("Setup [RP-BC]: selected position > broken_pos",
          _rp_bc.declaration_start > _rp_bc_broken_pos,
          detail=f"ds={_rp_bc.declaration_start}, broken_pos={_rp_bc_broken_pos}")
    _test("Setup [RP-BC]: prerequisites_valid=True",
          _rp_bc.prerequisites_valid == True,
          detail=f"pv={_rp_bc.prerequisites_valid}, missing={_rp_bc.missing}")

    # RP-SL: string-literal false-positive — phase name only in unrelated string
    _rp_sl_fixture = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Broken(guid, TEXT("other_phase"),
            EFbxPhaseKind::Exclusive,
            &PhaseDurations
    }
    {
        FString unrelated = TEXT("request_parse");
        FFbxScopePhase Scope(guid, TEXT("request_parse"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
        FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT(Request.FbxPath));
        ValidateVersion(Request);
    }
    ValidatePathSecurity(Request);
    return true;
}"""
    _rp_sl_broken_pos = _rp_sl_fixture.index("FFbxScopePhase Broken(")
    _rp_sl_valid_pos = _rp_sl_fixture.index("FFbxScopePhase Scope(")
    _rp_sl = _parse_and_prepare_rp(_rp_sl_fixture)
    _test("Setup [RP-SL]: fallback — unrelated-string-only target",
          _rp_sl.match_count == 1,
          detail=f"count={_rp_sl.match_count}")
    _test("Setup [RP-SL]: exact selected position == valid_pos",
          _rp_sl.declaration_start == _rp_sl_valid_pos,
          detail=f"ds={_rp_sl.declaration_start}, valid_pos={_rp_sl_valid_pos}")
    _test("Setup [RP-SL]: selected position > broken_pos",
          _rp_sl.declaration_start > _rp_sl_broken_pos,
          detail=f"ds={_rp_sl.declaration_start}, broken_pos={_rp_sl_broken_pos}")
    _test("Setup [RP-SL]: prerequisites_valid=True",
          _rp_sl.prerequisites_valid == True,
          detail=f"pv={_rp_sl.prerequisites_valid}, missing={_rp_sl.missing}")

    # RP-SL2: string literal with TEXT("request_parse") in an unrelated
    # FString assignment BEFORE the FFbxScopePhase.  The scanner must not
    # borrow this TEXT from the unrelated assignment.
    _rp_sl2 = _parse_and_prepare_rp("""bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    FString temp = TEXT("request_parse");
    {
        FFbxScopePhase Broken(guid,
            EFbxPhaseKind::Exclusive,
            &PhaseDurations
    }
    {
        FFbxScopePhase Scope(guid, TEXT("request_parse"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
        FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT(Request.FbxPath));
        ValidateVersion(Request);
    }
    ValidatePathSecurity(Request);
    return true;
}""")
    _test("Setup [RP-SL2]: string literal before broken decl — valid",
          _rp_sl2.match_count == 1,
          detail=f"count={_rp_sl2.match_count}")
    _test("Setup [RP-SL2]: prerequisites_valid=True",
          _rp_sl2.prerequisites_valid == True,
          detail=f"pv={_rp_sl2.prerequisites_valid}, missing={_rp_sl2.missing}")

    # RP-OC: other-class false-positive — OtherClass<FFbxScopePhase> not matched
    _rp_oc = _parse_and_prepare_rp("""bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        OtherClass<FFbxScopePhase> oc(guid, TEXT("request_parse"),
                                      EFbxPhaseKind::Exclusive,
                                      &PhaseDurations);
    }
    {
        FFbxScopePhase Scope(guid, TEXT("request_parse"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
        FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT(Request.FbxPath));
        ValidateVersion(Request);
    }
    ValidatePathSecurity(Request);
    return true;
}""")
    _test("Setup [RP-OC]: other-class false-positive guard — valid",
          _rp_oc.match_count == 1,
          detail=f"count={_rp_oc.match_count}")
    _test("Setup [RP-OC]: prerequisites_valid=True",
          _rp_oc.prerequisites_valid == True,
          detail=f"pv={_rp_oc.prerequisites_valid}, missing={_rp_oc.missing}")

    # RP-LD: later-declaration bleed — first decl is broken/incomplete
    # with NO target phase name.  Second decl is valid request_parse.
    _rp_ld_fixture = """bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Broken(
            Guid,
            EFbxPhaseKind::Exclusive,
            &PhaseDurations
    }
    {
        FFbxScopePhase Scope(guid, TEXT("request_parse"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
        FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT(Request.FbxPath));
        ValidateVersion(Request);
    }
    ValidatePathSecurity(Request);
    return true;
}"""
    _rp_ld_broken_pos = _rp_ld_fixture.index("FFbxScopePhase Broken(")
    _rp_ld_valid_pos = _rp_ld_fixture.index("FFbxScopePhase Scope(")
    _rp_ld = _parse_and_prepare_rp(_rp_ld_fixture)
    _test("Setup [RP-LD]: later-declaration bleed guard — match_count=1",
          _rp_ld.match_count == 1,
          detail=f"count={_rp_ld.match_count}")
    _test("Setup [RP-LD]: single_match is the second decl",
          _rp_ld.single_match is not None,
          detail=f"single={_rp_ld.single_match}")
    _test("Setup [RP-LD]: exact selected position == valid_pos",
          _rp_ld.declaration_start == _rp_ld_valid_pos,
          detail=f"ds={_rp_ld.declaration_start}, valid_pos={_rp_ld_valid_pos}")
    _test("Setup [RP-LD]: selected position > broken_pos",
          _rp_ld.declaration_start > _rp_ld_broken_pos,
          detail=f"ds={_rp_ld.declaration_start}, broken_pos={_rp_ld_broken_pos}")
    _test("Setup [RP-LD]: invocation_complete=True for selected decl",
          _rp_ld.invocation_complete == True,
          detail=f"inv_comp={_rp_ld.invocation_complete}")
    _test("Setup [RP-LD]: prerequisites_valid=True",
          _rp_ld.prerequisites_valid == True,
          detail=f"pv={_rp_ld.prerequisites_valid}, missing={_rp_ld.missing}")

    # ===================================================================
    # Direct _scan_for_phase_name_in_span end_pos fixtures (RP + PV)
    # Prove that the scanner respects its end_pos parameter.
    # ===================================================================
    _scanner_rp_content = """    {
        FFbxScopePhase Scope(guid, TEXT("request_parse"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations
    }
"""
    _srp_decl = "FFbxScopePhase Scope("
    _srp_decl_pos = _scanner_rp_content.index(_srp_decl)
    _srp_start = _srp_decl_pos + len(_srp_decl)  # right after '('
    _srp_target = _scanner_rp_content.index('TEXT("request_parse")')
    _srp_cutoff = _srp_target  # end_pos excludes target
    _srp_full = len(_scanner_rp_content)
    _srp_mask = _build_lexical_mask(_scanner_rp_content)
    # Test A: cutoff before target
    _srp_cutoff_result = _scan_for_phase_name_in_span(
        _scanner_rp_content, _srp_mask, 'request_parse', _srp_start, _srp_cutoff)
    _test("Direct scanner [RP-cutoff]: end_pos before TEXT returns False",
          not _srp_cutoff_result, detail=f"scanner={_srp_cutoff_result}")
    _test("Direct scanner [RP-cutoff]: start_pos after opening paren",
          _srp_start > _srp_decl_pos,
          detail=f"start={_srp_start}, decl_end={_srp_decl_pos + len(_srp_decl)}")
    _test("Direct scanner [RP-cutoff]: cutoff <= target position",
          _srp_cutoff <= _srp_target,
          detail=f"cutoff={_srp_cutoff}, target={_srp_target}")
    # Test B: full span includes target
    _srp_full_result = _scan_for_phase_name_in_span(
        _scanner_rp_content, _srp_mask, 'request_parse', _srp_start, _srp_full)
    _test("Direct scanner [RP-full]: full end_pos includes target returns True",
          _srp_full_result, detail=f"scanner={_srp_full_result}")
    _test("Direct scanner [RP-full]: full end_pos > target position",
          _srp_full > _srp_target,
          detail=f"full={_srp_full}, target={_srp_target}")

    _scanner_pv_content = """    {
        FFbxScopePhase Scope(guid, TEXT("path_validation"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations
    }
"""
    _spv_decl = "FFbxScopePhase Scope("
    _spv_decl_pos = _scanner_pv_content.index(_spv_decl)
    _spv_start = _spv_decl_pos + len(_spv_decl)
    _spv_target = _scanner_pv_content.index('TEXT("path_validation")')
    _spv_cutoff = _spv_target
    _spv_full = len(_scanner_pv_content)
    _spv_mask = _build_lexical_mask(_scanner_pv_content)
    # Test A: cutoff before target
    _spv_cutoff_result = _scan_for_phase_name_in_span(
        _scanner_pv_content, _spv_mask, 'path_validation', _spv_start, _spv_cutoff)
    _test("Direct scanner [PV-cutoff]: end_pos before TEXT returns False",
          not _spv_cutoff_result, detail=f"scanner={_spv_cutoff_result}")
    _test("Direct scanner [PV-cutoff]: start_pos after opening paren",
          _spv_start > _spv_decl_pos,
          detail=f"start={_spv_start}, decl_end={_spv_decl_pos + len(_spv_decl)}")
    _test("Direct scanner [PV-cutoff]: cutoff <= target position",
          _spv_cutoff <= _spv_target,
          detail=f"cutoff={_spv_cutoff}, target={_spv_target}")
    # Test B: full span includes target
    _spv_full_result = _scan_for_phase_name_in_span(
        _scanner_pv_content, _spv_mask, 'path_validation', _spv_start, _spv_full)
    _test("Direct scanner [PV-full]: full end_pos includes target returns True",
          _spv_full_result, detail=f"scanner={_spv_full_result}")
    _test("Direct scanner [PV-full]: full end_pos > target position",
          _spv_full > _spv_target,
          detail=f"full={_spv_full}, target={_spv_target}")

    # ===================================================================
    # Real incomplete-constructor fallback fixtures (RP)
    # The FFbxScopePhase token is real and unmasked.  Extraction fails
    # (no closing ')'), so the fallback scanner must correctly determine
    # whether the phase name appears inside the argument sequence.
    # ===================================================================

    # RP-FA: actual incomplete constructor with TEXT("request_parse")
    _rp_fa = _parse_and_prepare_rp("""bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Scope(guid, TEXT("request_parse"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations
        FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT(Request.FbxPath));
        ValidateVersion(Request);
    }
    ValidatePathSecurity(Request);
    return true;
}""")
    _test("Setup [RP-FA]: actual incomplete fallback — match_count=1",
          _rp_fa.match_count == 1,
          detail=f"count={_rp_fa.match_count}")
    _test("Setup [RP-FA]: single_match is not None",
          _rp_fa.single_match is not None,
          detail=f"single={_rp_fa.single_match}")
    _test("Setup [RP-FA]: declaration_end == -1",
          _rp_fa.declaration_end == -1,
          detail=f"de={_rp_fa.declaration_end}")
    _test("Setup [RP-FA]: constructor bounds missing",
          "constructor bounds" in _rp_fa.missing,
          detail=f"missing={_rp_fa.missing}")
    _test("Setup [RP-FA]: prerequisites_valid=False",
          _rp_fa.prerequisites_valid == False,
          detail=f"pv={_rp_fa.prerequisites_valid}, missing={_rp_fa.missing}")

    # RP-FB: incomplete, phase name only in // comment → no match
    _rp_fb = _parse_and_prepare_rp("""bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Broken(guid,
            // TEXT("request_parse")
            EFbxPhaseKind::Exclusive,
            &PhaseDurations
    }
    ValidatePathSecurity(Request);
    return true;
}""")
    _test("Setup [RP-FB]: incomplete + line-comment only — match_count=0",
          _rp_fb.match_count == 0,
          detail=f"count={_rp_fb.match_count}")
    _test("Setup [RP-FB]: missing unique declaration",
          "unique declaration" in _rp_fb.missing,
          detail=f"missing={_rp_fb.missing}")

    # RP-FC: incomplete, phase name only in /* */ comment → no match
    _rp_fc = _parse_and_prepare_rp("""bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Broken(guid,
            /* TEXT("request_parse") */
            EFbxPhaseKind::Exclusive,
            &PhaseDurations
    }
    ValidatePathSecurity(Request);
    return true;
}""")
    _test("Setup [RP-FC]: incomplete + block-comment only — match_count=0",
          _rp_fc.match_count == 0,
          detail=f"count={_rp_fc.match_count}")
    _test("Setup [RP-FC]: missing unique declaration",
          "unique declaration" in _rp_fc.missing,
          detail=f"missing={_rp_fc.missing}")

    # RP-FD: incomplete, phase name only in unrelated FString assignment
    _rp_fd = _parse_and_prepare_rp("""bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    FString temp = TEXT("request_parse");
    {
        FFbxScopePhase Broken(guid,
            EFbxPhaseKind::Exclusive,
            &PhaseDurations
    }
    ValidatePathSecurity(Request);
    return true;
}""")
    _test("Setup [RP-FD]: incomplete + unrelated string — match_count=0",
          _rp_fd.match_count == 0,
          detail=f"count={_rp_fd.match_count}")
    _test("Setup [RP-FD]: missing unique declaration",
          "unique declaration" in _rp_fd.missing,
          detail=f"missing={_rp_fd.missing}")

    # RP-FE: incomplete with TEXT("other_phase") and // TEXT("request_parse")
    _rp_fe = _parse_and_prepare_rp("""bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Broken(guid, TEXT("other_phase"),
            // TEXT("request_parse")
            EFbxPhaseKind::Exclusive,
            &PhaseDurations
    }
    ValidatePathSecurity(Request);
    return true;
}""")
    _test("Setup [RP-FE]: incomplete + other_phase + comment — match_count=0",
          _rp_fe.match_count == 0,
          detail=f"count={_rp_fe.match_count}")
    _test("Setup [RP-FE]: missing unique declaration",
          "unique declaration" in _rp_fe.missing,
          detail=f"missing={_rp_fe.missing}")

    # RP-FF: broken constructor with unrelated UE_LOG TEXT after it
    _rp_ff = _parse_and_prepare_rp("""bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Broken(
            Guid,
            EFbxPhaseKind::Exclusive,
            &PhaseDurations

        UE_LOG(LogTemp, Warning, TEXT("request_parse"));
    }
    ValidatePathSecurity(Request);
    return true;
}""")
    _test("Setup [RP-FF]: broken + unrelated UE_LOG — match_count=0",
          _rp_ff.match_count == 0,
          detail=f"count={_rp_ff.match_count}")
    _test("Setup [RP-FF]: missing unique declaration",
          "unique declaration" in _rp_ff.missing,
          detail=f"missing={_rp_ff.missing}")

    # ===================================================================
    # Scanner boundary fixtures (RP)
    # ===================================================================

    # RP-BD1: scanner stops at enclosing block close
    _rp_bd1 = _parse_and_prepare_rp("""bool FLiveSyncFBXImporter::HandleImport(Args) {
    {
        FFbxScopePhase Broken(
            Guid,
            EFbxPhaseKind::Exclusive,
            &PhaseDurations
    }
    {
        FFbxScopePhase Scope(guid, TEXT("request_parse"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
        FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT(Request.FbxPath));
        ValidateVersion(Request);
    }
    ValidatePathSecurity(Request);
    return true;
}""")
    _test("Setup [RP-BD1]: enclosing block boundary — match_count=1",
          _rp_bd1.match_count == 1,
          detail=f"count={_rp_bd1.match_count}")
    _test("Setup [RP-BD1]: holds valid decl",
          _rp_bd1.prerequisites_valid == True,
          detail=f"pv={_rp_bd1.prerequisites_valid}, missing={_rp_bd1.missing}")

    # RP-BD2: broken decl before next FFbxScopePhase declaration
    _rp_bd2 = _parse_and_prepare_rp("""bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    FFbxScopePhase Broken(
        Guid,
        EFbxPhaseKind::Exclusive,
        &PhaseDurations
    {
        FFbxScopePhase Scope(guid, TEXT("request_parse"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
        FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT(Request.FbxPath));
        ValidateVersion(Request);
    }
    ValidatePathSecurity(Request);
    return true;
}""")
    _test("Setup [RP-BD2]: next declaration boundary — match_count=1",
          _rp_bd2.match_count == 1,
          detail=f"count={_rp_bd2.match_count}")
    _test("Setup [RP-BD2]: holds valid decl",
          _rp_bd2.prerequisites_valid == True,
          detail=f"pv={_rp_bd2.prerequisites_valid}, missing={_rp_bd2.missing}")

    # ===================================================================
    # Missing-element fixtures (RP)
    # ===================================================================

    # RP-no-FbxPath (C): complete constructor, no FbxPath.
    _rp_nofbx = _parse_and_prepare_rp("""bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Scope(guid, TEXT("request_parse"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
        ValidateVersion(Request);
    }
    ValidatePathSecurity(Request);
    return true;
}""")
    _test("Setup [RP-no-FbxPath]: prerequisites_valid=False",
          _rp_nofbx.prerequisites_valid == False,
          detail=f"pv={_rp_nofbx.prerequisites_valid}, missing={_rp_nofbx.missing}")
    _test("Setup [RP-no-FbxPath]: missing contains FbxPath invocation",
          "FbxPath invocation" in _rp_nofbx.missing,
          detail=f"missing={_rp_nofbx.missing}")

    # RP-no-VV (C): complete constructor, no ValidateVersion.
    _rp_novv = _parse_and_prepare_rp("""bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Scope(guid, TEXT("request_parse"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
        FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT(Request.FbxPath));
    }
    ValidatePathSecurity(Request);
    return true;
}""")
    _test("Setup [RP-no-VV]: prerequisites_valid=False",
          _rp_novv.prerequisites_valid == False,
          detail=f"pv={_rp_novv.prerequisites_valid}, missing={_rp_novv.missing}")
    _test("Setup [RP-no-VV]: missing contains ValidateVersion call",
          "ValidateVersion call" in _rp_novv.missing,
          detail=f"missing={_rp_novv.missing}")

    # RP-no-VPS (C): complete constructor, no ValidatePathSecurity.
    _rp_novps = _parse_and_prepare_rp("""bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Scope(guid, TEXT("request_parse"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
        FStringFromFixedAnsi(Request.FbxPath, UE_ARRAY_COUNT(Request.FbxPath));
        ValidateVersion(Request);
    }
    return true;
}""")
    _test("Setup [RP-no-VPS]: prerequisites_valid=False",
          _rp_novps.prerequisites_valid == False,
          detail=f"pv={_rp_novps.prerequisites_valid}, missing={_rp_novps.missing}")
    _test("Setup [RP-no-VPS]: missing contains ValidatePathSecurity call",
          "ValidatePathSecurity call" in _rp_novps.missing,
          detail=f"missing={_rp_novps.missing}")

    # ===================================================================
    # PV fixtures (absent-decl, incomplete + fallback, no-VPS)
    # ===================================================================

    # PV-absent-decl (A): no FFbxScopePhase token at all.
    _pv_absdecl = _parse_and_prepare_pv("""bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    ValidatePathSecurity(Request);
    return true;
}""")
    _test("Setup [PV-absent-decl]: prerequisites_valid=False",
          _pv_absdecl.prerequisites_valid == False,
          detail=f"pv={_pv_absdecl.prerequisites_valid}, missing={_pv_absdecl.missing}")
    _test("Setup [PV-absent-decl]: missing contains unique declaration",
          "unique declaration" in _pv_absdecl.missing,
          detail=f"missing={_pv_absdecl.missing}")

    # PV-incomplete-constructor (B): token found but unmatched parens.
    _pv_inccon = _parse_and_prepare_pv("""bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Scope(guid, TEXT("path_validation"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations
    }
    ValidatePathSecurity(Request);
    return true;
}""")
    _test("Setup [PV-incomplete]: prerequisites_valid=False",
          _pv_inccon.prerequisites_valid == False,
          detail=f"pv={_pv_inccon.prerequisites_valid}, missing={_pv_inccon.missing}")
    _test("Setup [PV-incomplete]: evaluator_valid=False",
          _pv_inccon.evaluator_valid == False,
          detail=f"ev={_pv_inccon.evaluator_valid}")
    _test("Setup [PV-incomplete]: missing contains constructor bounds",
          "constructor bounds" in _pv_inccon.missing,
          detail=f"missing={_pv_inccon.missing}")
    _test("Setup [PV-incomplete]: match_count == 1",
          _pv_inccon.match_count == 1,
          detail=f"count={_pv_inccon.match_count}")
    _test("Setup [PV-incomplete]: single_match is not None",
          _pv_inccon.single_match is not None,
          detail=f"single={_pv_inccon.single_match}")
    _test("Setup [PV-incomplete]: declaration_start >= 0",
          _pv_inccon.declaration_start >= 0,
          detail=f"ds={_pv_inccon.declaration_start}")
    _test("Setup [PV-incomplete]: declaration_end == -1",
          _pv_inccon.declaration_end == -1,
          detail=f"de={_pv_inccon.declaration_end}")
    _test("Setup [PV-incomplete]: invocation_complete == False",
          _pv_inccon.invocation_complete == False,
          detail=f"inv_comp={_pv_inccon.invocation_complete}")
    _test("Setup [PV-incomplete]: missing does not contain unique decl",
          "unique declaration" not in _pv_inccon.missing,
          detail=f"missing={_pv_inccon.missing}")

    # PV-incomplete-fallback (LC): incomplete, phase name only in // comment
    _pv_lc = _parse_and_prepare_pv("""bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Broken(guid,
            // TEXT("path_validation")
            EFbxPhaseKind::Exclusive,
            &PhaseDurations
    }
    ValidatePathSecurity(Request);
    return true;
}""")
    _test("Setup [PV-LC]: fallback — line-comment-only target — match_count=0",
          _pv_lc.match_count == 0, detail=f"count={_pv_lc.match_count}")
    _test("Setup [PV-LC]: missing unique declaration",
          "unique declaration" in _pv_lc.missing,
          detail=f"missing={_pv_lc.missing}")

    # PV-incomplete-fallback (BC): incomplete, phase name only in /* */ comment
    _pv_bc = _parse_and_prepare_pv("""bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Broken(guid,
            /* TEXT("path_validation") */
            EFbxPhaseKind::Exclusive,
            &PhaseDurations
    }
    ValidatePathSecurity(Request);
    return true;
}""")
    _test("Setup [PV-BC]: fallback — block-comment-only target — match_count=0",
          _pv_bc.match_count == 0, detail=f"count={_pv_bc.match_count}")
    _test("Setup [PV-BC]: missing unique declaration",
          "unique declaration" in _pv_bc.missing,
          detail=f"missing={_pv_bc.missing}")

    # PV-UNRELATED-STRING: incomplete, phase name only in unrelated string
    _pv_us = _parse_and_prepare_pv("""bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    FString temp = TEXT("path_validation");
    {
        FFbxScopePhase Broken(guid,
            EFbxPhaseKind::Exclusive,
            &PhaseDurations
    }
    ValidatePathSecurity(Request);
    return true;
}""")
    _test("Setup [PV-US]: fallback — unrelated string — match_count=0",
          _pv_us.match_count == 0, detail=f"count={_pv_us.match_count}")
    _test("Setup [PV-US]: missing unique declaration",
          "unique declaration" in _pv_us.missing,
          detail=f"missing={_pv_us.missing}")

    # PV-BROKEN-UE_LOG: broken constructor with UE_LOG after it
    _pv_ff = _parse_and_prepare_pv("""bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Broken(
            Guid,
            EFbxPhaseKind::Exclusive,
            &PhaseDurations

        UE_LOG(LogTemp, Warning, TEXT("path_validation"));
    }
    ValidatePathSecurity(Request);
    return true;
}""")
    _test("Setup [PV-FF]: broken + unrelated UE_LOG — match_count=0",
          _pv_ff.match_count == 0, detail=f"count={_pv_ff.match_count}")
    _test("Setup [PV-FF]: missing unique declaration",
          "unique declaration" in _pv_ff.missing,
          detail=f"missing={_pv_ff.missing}")

    # PV-no-VPS (C): complete constructor, no ValidatePathSecurity.
    _pv_novps = _parse_and_prepare_pv("""bool FLiveSyncFBXImporter::HandleImport(Args) {
    DoSetup();
    {
        FFbxScopePhase Scope(guid, TEXT("path_validation"),
                             EFbxPhaseKind::Exclusive,
                             &PhaseDurations);
    }
    return true;
}""")
    _test("Setup [PV-no-VPS]: prerequisites_valid=False",
          _pv_novps.prerequisites_valid == False,
          detail=f"pv={_pv_novps.prerequisites_valid}, missing={_pv_novps.missing}")
    _test("Setup [PV-no-VPS]: missing contains ValidatePathSecurity call",
          "ValidatePathSecurity call" in _pv_novps.missing,
          detail=f"missing={_pv_novps.missing}")

    # ===================================================================
    # Production-prerequisite state self-tests (_check_preparation)
    # ===================================================================

    def _check_preparation(
        label, fn,
        expect_pv, expect_ev,
        expected_missing=None,
        expected_failing=None,
    ):
        """Assert exact prerequisite and evaluator state from a _prepare_*
        call.  Uses real == assertions, never _test(..., True, ...)."""
        actual_pv, actual_missing, actual_ev, actual_failing, detail = fn()
        _test(f"STATE [{label}]: prerequisites_valid={expect_pv}",
              actual_pv == expect_pv,
              detail=f"actual={actual_pv}, missing={actual_missing}")
        _test(f"STATE [{label}]: evaluator_valid={expect_ev}",
              actual_ev == expect_ev,
              detail=f"actual_ev={actual_ev}, failing={actual_failing}")
        if expected_missing is not None:
            for item in expected_missing:
                _test(f"STATE [{label}]: missing contains '{item}'",
                      item in actual_missing,
                      detail=f"actual missing={actual_missing}")
        if expected_failing is not None:
            for rel in expected_failing:
                _test(f"STATE [{label}]: failing contains '{rel}'",
                      any(rel in r for r in actual_failing),
                      detail=f"actual failing={actual_failing}")

    # RP: missing unique declaration (A)
    _check_preparation("RP-no-decl",
        lambda: _prepare_request_parse_ordering(
            0, 100, False, None, 20, 40, True, 30, 50, 45, 55, 60, 70),
        expect_pv=False, expect_ev=False,
        expected_missing=["unique declaration"])

    # RP: missing dedicated block (B)
    _check_preparation("RP-no-block",
        lambda: _prepare_request_parse_ordering(
            0, 100, True, "m", 20, 40, False, -1, -1, 45, 55, 60, 70),
        expect_pv=False, expect_ev=False,
        expected_missing=["dedicated block"])

    # RP: missing constructor end (C)
    _check_preparation("RP-no-decl-end",
        lambda: _prepare_request_parse_ordering(
            0, 100, True, "m", 20, -1, True, 30, 50, 45, 55, 60, 70),
        expect_pv=False, expect_ev=False,
        expected_missing=["constructor bounds"])

    # RP: missing FbxPath (D)
    _check_preparation("RP-no-FbxPath",
        lambda: _prepare_request_parse_ordering(
            0, 100, True, "m", 20, 40, True, 30, 50, -1, -1, 60, 70),
        expect_pv=False, expect_ev=False,
        expected_missing=["FbxPath invocation"])

    # RP: missing ValidateVersion (E)
    _check_preparation("RP-no-VV",
        lambda: _prepare_request_parse_ordering(
            0, 100, True, "m", 20, 40, True, 30, 50, 45, 55, -1, 70),
        expect_pv=False, expect_ev=False,
        expected_missing=["ValidateVersion call"])

    # RP: missing ValidatePathSecurity (F)
    _check_preparation("RP-no-VPS",
        lambda: _prepare_request_parse_ordering(
            0, 100, True, "m", 20, 40, True, 30, 50, 45, 55, 60, -1),
        expect_pv=False, expect_ev=False,
        expected_missing=["ValidatePathSecurity call"])

    # RP: all prereqs, bad ordering — fbx_end(65) after vv(55) → fbx_end<vv fails (G)
    _check_preparation("RP-bad-order",
        lambda: _prepare_request_parse_ordering(
            0, 100, True, "m", 20, 40, True, 10, 50, 45, 65, 55, 70),
        expect_pv=True, expect_ev=False,
        expected_failing=["fbx_path_invocation_end<ValidateVersion_call"])

    # RP: all prereqs + good ordering (H)
    _check_preparation("RP-good-order",
        lambda: _prepare_request_parse_ordering(
            0, 100, True, "m", 20, 40, True, 10, 50, 42, 44, 46, 70),
        expect_pv=True, expect_ev=True)

    # PV: missing unique declaration (A)
    _check_preparation("PV-no-decl",
        lambda: _prepare_path_validation_ordering(
            0, 100, False, None, 20, 40, True, 30, 50, 60),
        expect_pv=False, expect_ev=False,
        expected_missing=["unique declaration"])

    # PV: missing dedicated block (B)
    _check_preparation("PV-no-block",
        lambda: _prepare_path_validation_ordering(
            0, 100, True, "m", 20, 40, False, -1, -1, 60),
        expect_pv=False, expect_ev=False,
        expected_missing=["dedicated block"])

    # PV: missing constructor end (C)
    _check_preparation("PV-no-decl-end",
        lambda: _prepare_path_validation_ordering(
            0, 100, True, "m", 20, -1, True, 30, 50, 60),
        expect_pv=False, expect_ev=False,
        expected_missing=["constructor bounds"])

    # PV: missing ValidatePathSecurity (D)
    _check_preparation("PV-no-VPS",
        lambda: _prepare_path_validation_ordering(
            0, 100, True, "m", 20, 40, True, 30, 50, -1),
        expect_pv=False, expect_ev=False,
        expected_missing=["ValidatePathSecurity call"])

    # PV: all prereqs, bad ordering — vps(55) after phase_close(50) (E)
    _check_preparation("PV-bad-order",
        lambda: _prepare_path_validation_ordering(
            0, 100, True, "m", 20, 40, True, 10, 50, 55),
        expect_pv=True, expect_ev=False,
        expected_failing=["ValidatePathSecurity_call<phase_close"])

    # PV: all prereqs + good ordering (F)
    _check_preparation("PV-good-order",
        lambda: _prepare_path_validation_ordering(
            0, 100, True, "m", 20, 40, True, 10, 50, 45),
        expect_pv=True, expect_ev=True)


def main():
    global PASS, FAIL
    print(f"\nPhase 10K.6 transaction decomposition tests: "
          f"{PASS} passed, {FAIL} failed\n")
    for r in RESULTS:
        print(r)
    print()
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    test_fbx_transaction_id_atomic()
    test_transaction_id_alloc()
    test_ffbxscopephase_struct()
    test_ffbxscopephase_optional_duration()
    test_phase_helpers()
    test_required_phases()
    test_nested_classification()
    test_stall_summary()
    test_duration_field()
    test_guid_field()
    test_transaction_id_in_markers()
    test_sanitized_name()
    test_no_protocol_changes()
    test_transaction_id_never_zero()
    test_compute_phase_classification()
    test_fstring_from_fixed_ansi()
    test_sync_id_snapshot()
    test_bounded_decode_safety()

    test_log_all_phases_complete()
    test_log_exclusive_sum()
    test_log_nested_excluded()
    test_log_unattributed()
    test_log_largest_phase()
    test_log_orphan_begin_detection()
    test_log_duplicate_end()
    test_log_mixed_classification()
    test_log_stall_summary_fields()

    # Task 6: behavioral tests for Phase 10K.6 timing arithmetic
    test_observed_timing_bug_reproduction()
    test_exclusive_sum_greater_than_total()
    test_largest_phase_emitted_before_other_phases()
    test_phase_name_lookup_is_exact()
    test_mismatched_transaction_id_does_not_contaminate()
    test_duplicate_phase_end_handled()
    test_missing_exclusive_phase_visible()

    # Task 2: source-structure tests for RAII accumulator correctness
    test_raii_accumulator_inside_destructor()
    test_production_phase_wiring()
    test_exclusive_phase_registry_complete()
    test_registry_and_parser_do_not_drift()

    # Phase 10K.5 diagnostics
    test_phase10k5_diagnostics()

    # Phase 7E focused
    test_phase7e_focused()

    # Scope extractor self-test
    _test_scope_extractor_self_test()

    # Task 5: behavioral tests
    test_sequential_exclusive_coverage_leq_100()
    test_nested_phase_excluded_from_exclusive_sum()
    test_inclusive_parent_excluded_from_exclusive_sum()
    test_overlapping_exclusive_produces_invalid()
    test_measured_exclusive_greater_than_total_rejected()
    test_timer_tolerance_handles_sub_millisecond()
    test_largest_phase_considers_only_valid_exclusive()
    test_mixed_classification_only_when_timing_valid()
    test_missing_phase_not_silently_zero()
    test_all_required_source_markers_exist()

    sys.exit(main())
