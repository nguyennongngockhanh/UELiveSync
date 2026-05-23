---
name: production-plugin-architecture
description: >-
  Use when designing, organizing, or maintaining production-grade
  plugin/addon repositories — Blender addons, UE plugins, editor
  extensions, or SDK-style libraries. Covers semantic versioning,
  phase-based development, repository layout, API stability,
  incremental refactoring, documentation standards, release
  workflows, and changelog discipline. Use ONLY for production
  engineering architecture decisions, not for feature coding.
---

# Production Plugin Architecture — Engineering Standards

## Core Principles

- **Semantic versioning** — `MAJOR.MINOR.PATCH`. Breaking changes → MAJOR. New backward-compatible features → MINOR. Bug fixes → PATCH. Pre-release suffixes (`-alpha.1`, `-rc.1`) for unstable builds.
- **Phase-based development** — organize work into numbered phases with clear scope, entry/exit criteria, and a single deliverable per phase.
- **Clean repository** — flat top-level with obvious entry points. No orphan files, no dead code, no mixed concerns.
- **Stable public APIs** — public surface is minimal and committed. Internal modules are free to refactor.
- **Incremental refactors** — never rewrite in one shot. Ship each intermediate step as a working release.
- **Docs follow code** — architecture docs are frozen at phase boundaries and updated only when verified against the running system.
- **Changelog is truth** — every user-facing change has a changelog entry. Consumers should never need to read git log.

## Semantic Versioning

```
Given version MAJOR.MINOR.PATCH, increment:

MAJOR — breaking protocol or API change, dropped platform support
MINOR — new feature, new CVar, new operator, backward-compatible addition
PATCH — bug fix, performance improvement, docs-only change

Pre-release: 1.0.0-alpha.1 → 1.0.0-beta.1 → 1.0.0-rc.1 → 1.0.0
```

Rules:
- Public API is defined as the union of documented operator `bl_idname`s, Python exports, C++ public headers, protocol wire format, and CVar names.
- Undocumented internals (`_prefixed` Python modules, `Private/` C++ headers, `internal` namespaces) are NOT part of the public API.
- A deprecation window of at least one MINOR version must elapse before removing any public API.
- Protocol wire format changes require MAJOR bump and version field increment in the header.

## Phase-Based Development

Each phase is a self-contained delivery with:

```
Phase N — Short Name ✅ (or 🚧 or ❌)

Goal: one-paragraph objective
Estimate: X days · Risk: Low/Medium/High

Items:
### ✅ Task A — Description
| File(s) | What changed |

### 🚧 Task B — Description (in progress)
...

Exit criteria:
- All items marked ✅
- Tests pass for the phase scope
- Architecture docs frozen for this phase
- Changelog updated
```

Rules:
- One phase = one coherent delivery. Never mix Phase N+1 features into Phase N.
- Each phase has a freeze commit where docs are updated to match.
- Phase numbering is monotonically increasing. Renumbering is a MAJOR event.
- Post-phase review captures lessons before starting the next.

## Repository Organization

```
project-root/
├── README.md               # One-paragraph purpose, badges, quick start
├── CHANGELOG.md            # Reverse chronological, SemVer headers
├── LICENSE                 # SPDX-identified
├── AGENTS.md               # OpenCode agent instructions (this file)
├── Docs/
│   ├── Architecture/       # Frozen per-phase docs (01-system-overview.md)
│   ├── Roadmap/            # Phase definitions
│   └── Issues/             # Known issues organized by category
├── scr/                    # Primary source (rename per domain)
│   ├── __init__.py         # Public API surface
│   ├── _internal/          # Private implementation
│   └── ...
├── tests/                  # One file per phase or feature
│   ├── run_all.py          # Consolidated harness
│   └── phaseX_feature.py
├── .gitignore
└── .opencode/              # OpenCode project configuration
    └── skills/
```

Rules:
- `Docs/` mirrors `src/` structure — one doc per major subsystem.
- `tests/` mirrors phase structure — one test file per phase validation scope.
- No generated files committed (build artifacts, `__pycache__`, `Binaries/`, `Intermediate/`).
- Dotfiles at root only for tool configuration (<5 files).

## Stable Public APIs

```python
# Public API — stable, documented, versioned
def public_function(): ...

# Internal API — unstable, undocumented, free to change
def _internal_function(): ...
```

```cpp
// Public API — exposed in Public/ headers
class /*UELIVESYNC_API*/ UUELiveSyncSubsystem : public UWorldSubsystem { ... };

// Internal API — in Private/ headers or .cpp only
class /*no DLL export*/ FLiveSyncRunnable : public FRunnable { ... };
```

Rules:
- Public API surface is documented in `Docs/Architecture/` with file/function references.
- Every public symbol has a docstring/comment explaining contract, not mechanics.
- Internal modules begin with `_` (Python) or live in `Private/` (C++).
- Never export internal classes from UE module `.Build.cs`.
- Protocol wire format is ALWAYS public API — consumers implement parsers against it.

## Incremental Refactor Strategy

1. **Identify boundary** — isolate the module or subsystem to change. Draw a box.
2. **Write adapter** — create a compatibility shim that preserves the old public API while delegating to new internals.
3. **Ship adapter** — release as MINOR version bump. Old API still works.
4. **Deprecate old path** — mark old symbols `@deprecated` (Python) or with `UE_DEPRECATED` (UE5 C++) or `[[deprecated]]` (C++17). Log a warning at runtime.
5. **Remove after window** — one MINOR version later, remove old path → MAJOR bump.
6. **Clean up** — remove adapter, rename internals if needed.

Never attempt steps 4–6 in the same release as step 2.

## Documentation Standards

| Doc | Audience | Format | Update cadence |
|-----|----------|--------|----------------|
| `README.md` | End users | Markdown | Every release |
| `Docs/Architecture/*` | Developers | Markdown | Frozen at phase boundaries |
| `Docs/Roadmap/*` | Stakeholders | Markdown | Per phase definition |
| `Docs/Issues/*` | Maintainers | Markdown | As discovered |
| `CHANGELOG.md` | End users | Markdown (keepachangelog) | Every PR/commit |
| Docstrings/comments | Developers | Language-native | With code changes |

Architecture docs must:
- Use diagrams (ASCII or Mermaid) for data flow.
- Reference exact file paths and line numbers for key logic.
- Include a "Limitations" section at the bottom.
- Be frozen (no edits) once the phase ships. Corrections go in the next phase's doc.

## Release Workflow

```
Development branch (main)
  │
  ├── Commit feature work
  ├── Update CHANGELOG.md per change
  ├── Update version string in code
  ├── Run test suite
  ├── Freeze architecture docs
  └── Tag: v1.2.3
       │
       └── GitHub Release with changelog excerpt
```

- Version is stored in ONE canonical location: `bl_info["version"]` (Blender), `.uplugin` `"Version"` (UE), or a `VERSION` constant (generic).
- Tags are `vMAJOR.MINOR.PATCH` with optional `-PRERELEASE` suffix.
- Release notes are the CHANGELOG section for that version — never duplicate.
- CI runs tests before allowing a release tag.
- Hotfix branches are `vMAJOR.MINOR.x` branched from the release tag.

## Changelog Discipline

```
# Changelog

## [1.2.0] — 2026-05-23

### Added
- New feature X with CVar `UE.LiveSync.X` (#42)

### Changed
- Improved performance of Y by 30% (#40)

### Fixed
- Edge case in Z when connection drops during snapshot (#38)

### Deprecated
- Old API `foo()` — use `bar()` instead. Will be removed in 2.0.

### Removed
- Legacy V1 protocol support (#35)

### Security
- [none]

## [1.1.0] — 2026-04-15
...
```

Rules:
- Reverse chronological order. Latest version at top.
- Every version has a date in `YYYY-MM-DD` format.
- Entries are grouped by type: `Added`, `Changed`, `Fixed`, `Deprecated`, `Removed`, `Security`.
- Each entry references an issue/PR number if tracked.
- Unreleased changes live under a `[Unreleased]` header at the top.
- First release is `[0.1.0]` (pre-production) until API is stable → `[1.0.0]`.

## See Also

- `patterns.md` — reusable versioning, layout, and lifecycle patterns
- `examples.md` — changelog, roadmap, and release examples
