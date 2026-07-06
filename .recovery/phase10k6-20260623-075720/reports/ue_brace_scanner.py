#!/usr/bin/env python3
"""Lexical brace scanner for C++ — ignores comments, strings, raw strings, char literals."""

import sys
import re

def scan_file(filepath):
    with open(filepath, 'r') as f:
        text = f.read()
    lines = text.split('\n')
    state = 'code'
    brace_depth = 0
    max_depth = 0
    unmatched = []

    i = 0
    while i < len(text):
        ch = text[i]

        # -------------------------------------------------------------------
        # State: line comment
        if state == 'line_comment':
            if ch == '\n':
                state = 'code'
            i += 1
            continue

        # State: block comment
        if state == 'block_comment':
            if text[i:i+2] == '*/':
                state = 'code'
                i += 2
                continue
            i += 1
            continue

        # State: string literal "..."
        if state == 'string':
            if ch == '\\':
                i += 2
                continue
            if ch == '"':
                state = 'code'
            i += 1
            continue

        # State: char literal '...'
        if state == 'char':
            if ch == '\\':
                i += 2
                continue
            if ch == "'":
                state = 'code'
            i += 1
            continue

        # State: raw string R"(...)"
        if state == 'raw_string':
            raw_close = ')' + raw_delim + '"'
            if text[i:i+len(raw_close)] == raw_close:
                state = 'code'
                i += len(raw_close)
                continue
            i += 1
            continue

        # -------------------------------------------------------------------
        # State: code — detect lexeme starts
        # Line comment
        if text[i:i+2] == '//':
            state = 'line_comment'
            i += 2
            continue

        # Block comment
        if text[i:i+2] == '/*':
            state = 'block_comment'
            i += 2
            continue

        # Raw string R"delimiter(...)delimiter"
        if text[i:i+2] == 'R"':
            # find opening paren after optional delimiter
            j = i + 2
            start_delim = []
            while j < len(text) and text[j] != '(':
                start_delim.append(text[j])
                j += 1
            if j < len(text) and text[j] == '(':
                state = 'raw_string'
                raw_delim = ''.join(start_delim)
                i = j + 1
                continue
            else:
                # Not a valid raw string; treat as regular char-by-char
                i += 1
                continue

        # String literal
        if ch == '"':
            state = 'string'
            i += 1
            continue

        # Char literal
        if ch == "'":
            state = 'char'
            i += 1
            continue

        # Brace counting
        if ch == '{':
            brace_depth += 1
            if brace_depth > max_depth:
                max_depth = brace_depth
            i += 1
            continue

        if ch == '}':
            brace_depth -= 1
            i += 1
            continue

        i += 1

    # After loop: unmatched detection (re-scan with same logic to record positions)
    # We re-scan to get line:col positions of unmatched braces
    state2 = 'code'
    brace_depth2 = 0
    max_depth2 = 0
    raw_delim2 = ''
    line = 1
    col = 1

    unmatched_opens = []
    unmatched_closes = []

    i = 0
    while i < len(text):
        ch = text[i]
        col_increment = 1

        if state2 == 'line_comment':
            if ch == '\n':
                state2 = 'code'
            if ch == '\n':
                line += 1
                col = 1
            else:
                col += 1
            i += 1
            continue

        if state2 == 'block_comment':
            if text[i:i+2] == '*/':
                state2 = 'code'
                i += 2
                col += 2
                continue
            if ch == '\n':
                line += 1
                col = 1
            else:
                col += 1
            i += 1
            continue

        if state2 == 'string':
            if ch == '\\':
                i += 2
                col += 2
                continue
            if ch == '"':
                state2 = 'code'
            if ch == '\n':
                line += 1
                col = 1
            else:
                col += 1
            i += 1
            continue

        if state2 == 'char':
            if ch == '\\':
                i += 2
                col += 2
                continue
            if ch == "'":
                state2 = 'code'
            if ch == '\n':
                line += 1
                col = 1
            else:
                col += 1
            i += 1
            continue

        if state2 == 'raw_string':
            raw_close = ')' + raw_delim2 + '"'
            if text[i:i+len(raw_close)] == raw_close:
                state2 = 'code'
                i += len(raw_close)
                col += len(raw_close)
                continue
            if ch == '\n':
                line += 1
                col = 1
            else:
                col += 1
            i += 1
            continue

        # code state
        if text[i:i+2] == '//':
            state2 = 'line_comment'
            i += 2
            col += 2
            continue

        if text[i:i+2] == '/*':
            state2 = 'block_comment'
            i += 2
            col += 2
            continue

        if text[i:i+2] == 'R"':
            j = i + 2
            d = []
            while j < len(text) and text[j] != '(':
                d.append(text[j])
                j += 1
            if j < len(text) and text[j] == '(':
                state2 = 'raw_string'
                raw_delim2 = ''.join(d)
                i = j + 1
                col = col + (j - i + 1)  # approximate, fix later
                # simpler: recalc col
                continue
            i += 1
            col += 1
            continue

        if ch == '"':
            state2 = 'string'
            i += 1
            col += 1
            continue

        if ch == "'":
            state2 = 'char'
            i += 1
            col += 1
            continue

        if ch == '{':
            brace_depth2 += 1
            if brace_depth2 > max_depth2:
                max_depth2 = brace_depth2
            i += 1
            col += 1
            continue

        if ch == '}':
            brace_depth2 -= 1
            if brace_depth2 < 0:
                # unmatched close brace
                unmatched_closes.append((line, col - 1))
                brace_depth2 = 0  # reset to avoid double-count
            i += 1
            col += 1
            continue

        if ch == '\n':
            line += 1
            col = 1
        else:
            col += 1
        i += 1

    # After full scan, any remaining positive depth = unmatched opens
    # We need to track them. Simpler: build a stack during scan.
    # Let me redo with a proper stack.
    return scan_file_v2(text)

def scan_file_v2(text):
    """Returns (balanced:bool, max_depth:int, unmatched_opens:list, unmatched_closes:list)."""
    state = 'code'
    brace_depth = 0
    max_depth = 0
    stack = []  # list of (line, col, type) for opens
    unmatched_opens = []
    unmatched_closes = []
    raw_delim = ''
    line = 1
    col = 1

    i = 0
    while i < len(text):
        ch = text[i]

        if state == 'line_comment':
            if ch == '\n':
                state = 'code'
                line += 1
                col = 1
            else:
                col += 1
            i += 1
            continue

        if state == 'block_comment':
            if text[i:i+2] == '*/':
                state = 'code'
                i += 2
                col += 2
                continue
            if ch == '\n':
                line += 1
                col = 1
            else:
                col += 1
            i += 1
            continue

        if state == 'string':
            if ch == '\\' and i + 1 < len(text):
                i += 2
                col += 2
                continue
            if ch == '"':
                state = 'code'
            if ch == '\n':
                line += 1
                col = 1
            else:
                col += 1
            i += 1
            continue

        if state == 'char':
            if ch == '\\' and i + 1 < len(text):
                i += 2
                col += 2
                continue
            if ch == "'":
                state = 'code'
            if ch == '\n':
                line += 1
                col = 1
            else:
                col += 1
            i += 1
            continue

        if state == 'raw_string':
            close_seq = ')' + raw_delim + '"'
            if text[i:i+len(close_seq)] == close_seq:
                state = 'code'
                i += len(close_seq)
                col += len(close_seq)
                continue
            if ch == '\n':
                line += 1
                col = 1
            else:
                col += 1
            i += 1
            continue

        # --- code state ---
        if text[i:i+2] == '//':
            state = 'line_comment'
            i += 2
            col += 2
            continue

        if text[i:i+2] == '/*':
            state = 'block_comment'
            i += 2
            col += 2
            continue

        if text[i:i+2] == 'R"':
            j = i + 2
            d = []
            while j < len(text) and text[j] != '(':
                d.append(text[j])
                j += 1
            if j < len(text) and text[j] == '(':
                state = 'raw_string'
                raw_delim = ''.join(d)
                # advance past the paren
                i = j + 1
                col += (j - i + 1)  # approximate
                # recalc better:
                col = 1 + sum(1 for c in text[:j+1].split('\n')[-1])
                continue
            i += 1
            col += 1
            continue

        if ch == '"':
            state = 'string'
            i += 1
            col += 1
            continue

        if ch == "'":
            state = 'char'
            i += 1
            col += 1
            continue

        if ch == '{':
            brace_depth += 1
            if brace_depth > max_depth:
                max_depth = brace_depth
            stack.append((line, col, '{'))
            i += 1
            col += 1
            continue

        if ch == '}':
            if brace_depth <= 0:
                unmatched_closes.append((line, col, '}'))
            else:
                stack.pop()
            brace_depth -= 1
            if brace_depth < 0:
                brace_depth = 0
            i += 1
            col += 1
            continue

        if ch == '\n':
            line += 1
            col = 1
        else:
            col += 1
        i += 1

    # Any remaining on stack are unmatched opens
    for (l, c, _) in stack:
        unmatched_opens.append((l, c, '{'))

    balanced = (len(unmatched_opens) == 0 and len(unmatched_closes) == 0)
    return balanced, max_depth, unmatched_opens, unmatched_closes


def main():
    files = sys.argv[1:] if len(sys.argv) > 1 else []
    results = {}
    for fp in files:
        balanced, max_depth, opens, closes = scan_file_v2(open(fp, 'r').read())
        status = "BRACES_BALANCED" if balanced else "BRACES_UNBALANCED"
        unmatched = []
        for l, c, ch in opens:
            unmatched.append(f"{ch} at {l}:{c}")
        for l, c, ch in closes:
            unmatched.append(f"{ch} at {l}:{c}")
        results[fp] = (status, max_depth, unmatched)

    for fp, (status, max_depth, unmatched) in results.items():
        print(f"=== {fp} ===")
        print(status)
        print(f"Depth: {max_depth}")
        if unmatched:
            for u in unmatched:
                print(f"Unmatched: {u}")
        else:
            print("Unmatched: (none)")
        print()

if __name__ == '__main__':
    main()
