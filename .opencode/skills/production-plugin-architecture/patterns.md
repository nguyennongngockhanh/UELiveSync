# Reusable Engineering Patterns

## Version Increment Decision Tree

```
Is the change backward-compatible?
├── NO  → MAJOR bump (+1), reset MINOR/PATCH to 0
│         Update protocol version field, wire format docs
│         Announce migration guide in CHANGELOG
│
└── YES → Does it add new functionality?
          ├── YES → MINOR bump (+1), reset PATCH to 0
          │         Public API additions, new CVars, new operators
          │         Deprecation window begins for old paths
          │
          └── NO  → PATCH bump (+1)
                    Bug fixes, perf improvements, docs only
                    No new public symbols
```

## Phase Template

```markdown
# Phase N — Short Name

**Status**: ✅ Completed · **Estimate**: X days · **Risk**: Low/Medium/High

---

## Goal

One paragraph describing what this phase delivers and why.

---

## Items

### ✅ Task A — Short description

| File(s) | What |
|---------|------|
| `path/to/file.py` | What changed, why, which functions |
| `path/to/file2.h` | Added `FunctionX()`, updated `StructY` |

### ✅ Task B — Short description

| File(s) | What |
|---------|------|
| `path/to/file.py` | Fixed edge case in Z |

---

## Exit Criteria

- [ ] All items above marked ✅
- [ ] Test suite passes
- [ ] Architecture docs frozen for this phase
- [ ] CHANGELOG updated with all changes
- [ ] Version bumped appropriately
```

## Deprecation Wrapper (Python)

```python
import warnings


def old_function():
    """Legacy function. Use new_function() instead."""
    warnings.warn(
        "old_function() is deprecated, use new_function()",
        DeprecationWarning,
        stacklevel=2,
    )
    return new_function()


def new_function():
    """Replacement for old_function()."""
    ...
```

## Deprecation Macro (UE C++)

```cpp
// In header:
UE_DEPRECATED(5.3, "Use NewFunction() instead.")
void OldFunction();

// In source — keep implementation alive during deprecation window:
void OldFunction()
{
    UE_LOG(LogLiveSync, Warning,
        TEXT("OldFunction() is deprecated, use NewFunction()"));
    NewFunction();
}
```

## Changelog Fragment

```markdown
## [Unreleased]

### Added
- (new features)

### Changed
- (behavior changes, performance)

### Fixed
- (bug fixes)

### Deprecated
- (API deprecations with replacement)

### Removed
- (removed features, only after deprecation window)
```

## Architecture Doc Template

```markdown
# Subsystem Name

## File Structure

```
path/to/Public/Header.h
path/to/Private/Source.cpp
```

## Data Flow

```
[ASCII or Mermaid diagram]
```

## Key Functions

| Function | Role |
|----------|------|
| `Foo()` | Entry point, called from Tick |
| `Bar()` | Worker, processes input |

## Thread Safety

| Thread | Access |
|--------|--------|
| Game | Read/write |
| Network | Read-only via queue |

## Limitations

1. Does not handle X
2. Y is not thread-safe
```

## Release Checklist

```markdown
# Release vX.Y.Z Checklist

- [ ] All phase items completed
- [ ] `CHANGELOG.md` updated with accurate entries
- [ ] Version bumped in canonical location
- [ ] Architecture docs frozen
- [ ] `git tag vX.Y.Z` created
- [ ] GitHub Release published with changelog excerpt
- [ ] (If MAJOR) Migration guide written
```

## Repository Layout Verification

```bash
# Check for orphan files not tracked by git
git ls-files --others --exclude-standard

# Verify no large binary files staged
git diff --cached --stat | grep -E '\.(png|jpg|blend|psd|exe|dll)$'

# Ensure .gitignore covers build artifacts
grep -q 'Binaries/' .gitignore || echo "Missing Binaries/ in .gitignore"
grep -q 'Intermediates/' .gitignore || echo "Missing Intermediate/ in .gitignore"
```
