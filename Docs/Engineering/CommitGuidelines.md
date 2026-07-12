# Commit Guidelines

*Version: 1.2*
*Last updated: 2026-07-12*

## Mandatory Rules

These rules apply to every commit. No exceptions.

### One Concern Per Commit

Every commit must represent one logical concern.

A concern is a single logical change that can be understood, reviewed, tested, and reverted independently.

Every commit must be:

- **Independently reviewable** — reviewable without reading other commits in the sequence
- **Independently buildable** — when applicable, the commit alone compiles/passes
- **Independently revertable** — `git revert` does not break other commits
- **Bisect-friendly** — `git bisect` can isolate this commit as a valid bisection point

File boundaries do not define a concern. A single concern may legitimately span multiple files if they implement the same logical change.

### Diagnostic Isolation

Each `diag(<subsystem>): ...` commit must instrument exactly one subsystem or investigation concern.

Diagnostic commits must:

- Be independently revertable without breaking production behavior, other diagnostics, buildability, or ongoing investigations
- **Never intentionally change observable production behavior** — allowed: log, counter, timer, marker, tracing. Forbidden: logic changes, timing changes, queue behavior changes, retry policy changes

### Working Tree Safety

Never overwrite an uncommitted working tree to isolate production changes.

If production and diagnostic changes are mixed:

0. Check `git status` to understand the full state.
1. Split them with `git add -p` whenever possible.
2. If they cannot be separated safely, create a temporary backup branch or stash before rewriting the working tree.
3. Do not rely on reflog or editor recovery as a backup strategy.

This is a process invariant. Violating it risks losing uncommitted instrumentation that was never stashed or committed.

## Recommended Conventions

These conventions improve consistency but may be adapted per project needs.

### Commit Naming

```
<type>(<scope>): <description>
```

Types:

| Type | Purpose |
|------|---------|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `perf` | Performance improvement |
| `refactor` | Code restructuring without behavior change |
| `diag` | Structured instrumentation intended to aid investigation. May remain in the codebase if useful. |
| `docs` | Documentation only |
| `test` | Test addition or correction |
| `debug` | Temporary debugging changes. Must be removed or squashed before merging to main. |

Scopes for this project:

| Scope | Subsystem |
|-------|-----------|
| `fbx` | FBX import/export pipeline |
| `material` | Material extraction and sync |
| `network` | TCP transport, connection lifecycle |
| `queue` | Packet queue (push/pop/dispatch) |
| `transport` | Socket, heartbeat, connection generation |
| `transform` | Transform state update pipeline |
| `interp` | Interpolation and convergence |
| `actor` | Actor spawn, cache, destroy |
| `tick` | Runtime tick loop |
| `timer` | Blender timer/scheduler |
| `importer` | FBX importer (UE side) |
| `ui` | Editor UI/widgets |
| `texture` | Texture/sidecar pipeline |
| `blender` | Blender addon (general) |
| `camera` | Camera synchronization |
| `sequence` | Sequencer/keyframe pipeline |
| `investigation` | Investigation-specific |

### Branch Naming

| Branch | Purpose |
|--------|---------|
| `main` | Production-ready commits only |
| `debug/*` | Temporary debugging and investigation branches |
| `investigation/*` | Root-cause analysis |
| `feature/*` | New feature development |
| `hotfix/*` | Emergency fixes |

Additional prefixes may be added as the project evolves (e.g. `release/*`, `experiment/*`, `perf/*`, `spike/*`).

## Investigation Lifecycle

Every investigation follows a consistent lifecycle:

```
Investigation
    ↓
Diagnostic commits (diag)
    ↓
Root cause confirmed
    ↓
Production fix (feat/fix)
    ↓
Regression tests (test)
    ↓
Remove temporary diagnostics (if no longer needed)
```

Diagnostic commits that remain useful after the investigation may stay in the codebase. Remove only what no longer serves a purpose.

## Revision History

| Version | Changes |
|---------|---------|
| v1.0 | Initial commit guidelines |
| v1.1 | Separate mandatory rules from recommended conventions. Add diagnostic behavior invariant. Add Working Tree step 0. |
| v1.2 | Define "concern" explicitly. Refine diagnostic invariant to "observable behavior". Add investigation lifecycle. Clarify diag vs debug semantics. |
