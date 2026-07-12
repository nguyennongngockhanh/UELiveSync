# Commit Guidelines

*Version: 1.0*
*Last updated: 2026-07-12*

## Commit Guideline

Every commit must represent one logical concern and be:

- **Independently reviewable** — reviewable without reading other commits in the sequence
- **Independently buildable** — when applicable, the commit alone compiles/passes
- **Independently revertable** — `git revert` does not break other commits
- **Bisect-friendly** — `git bisect` can isolate this commit as a valid bisection point

File boundaries do not define a concern. A single concern may legitimately span multiple files if they implement the same logical change.

## Diagnostic Commit Guideline

Each `diag(<subsystem>): ...` commit must instrument exactly one subsystem or investigation concern.

Diagnostic commits must be independently revertable without breaking:

- Production behavior
- Other diagnostic instrumentation
- Buildability
- Ongoing investigations

Example split for runtime instrumentation:

```
diag(queue): packet queue lifecycle
diag(transport): connection lifecycle
diag(transform): transform pipeline
diag(interp): interpolation decisions
diag(actor): actor destruction
diag(tick): periodic runtime probes
```

When an investigation ends, individual subsystem diagnostics can be reverted independently:

```bash
git revert diag(interp)   # remove interpolation probes
# queue, transport, transform, actor, tick remain
```

## Working Tree Safety

Never overwrite an uncommitted working tree to isolate production changes.

If production and diagnostic changes are mixed:

1. Split them with `git add -p` whenever possible.
2. If they cannot be separated safely, create a temporary backup branch or stash before rewriting the working tree.
3. Do not rely on reflog or editor recovery as a backup strategy.

This is a process invariant. Violating it risks losing uncommitted instrumentation that was never stashed or committed.

## Commit Naming Convention

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
| `diag` | Diagnostic instrumentation |
| `docs` | Documentation only |
| `test` | Test addition or correction |
| `debug` | Temporary debugging (must not be merged to main) |

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

## Branch Policy

| Branch | Purpose |
|--------|---------|
| `main` | Production-ready commits only |
| `debug/*` | Temporary debugging and investigation branches |
| `investigation/*` | Root-cause analysis |
| `feature/*` | New feature development |
| `hotfix/*` | Emergency fixes |
