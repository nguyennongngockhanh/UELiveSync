# Workspace Partition Suggestions

Logical subtrees for indexing isolation and task scoping.

---

## `Blender_Addon/`

**Responsibility**: Blender addon — scene scanning, diff detection, serialization, TCP client.

**Key files**: `sync.py` (core loop), `network.py` (protocol + TCP), `__init__.py` (registration)

**Suitable task scope**:
- GUID identity, hash stability, duplicate detection
- Transform serialization, parent detection
- Rename/visibility/hierarchy emission
- TCP client, reconnect, heartbeat
- Pure Blender-side bugs (no UE code)

**Indexing boundary**: Only `Blender_Addon/` + `tests/` + `Docs/ARCHITECTURE.md`

---

## `UE_Plugin/UELiveSync/Source/UELiveSync/`

**Responsibility**: UE5 plugin — binary packet parsing, game-thread orchestration, actor management.

**Key files**:
- `Private/UELiveSyncSubsystem.cpp` — Tick pipeline + all semantic handlers
- `Private/LiveSyncRunnable.cpp` — Network receive thread
- `Public/SyncTypes.h` — All structs, enums, constants
- `Public/LiveSyncQueue.h` — Bounded MPSC queue

**Sub-partitions**:

### `ReplayCore/` (logical subset within `UELiveSyncSubsystem.cpp`)

**Responsibility**: World replay buffer, save/restore/rebuild/verify.

**Entry functions** (all in `UELiveSyncSubsystem.cpp`):
- `SaveWorldState()` (~7800)
- `RestoreWorldState()` (~8100)
- `RebuildWorldFromSnapshot()` (~8400)
- `ComputeWorldStateHash()` (~7940)
- `VerifyWorldReplay()` (~8200)
- `ExportWorldSnapshot()` (~8700)

**Indexing boundary**: `UELiveSyncSubsystem.cpp` (replay section) + `SyncTypes.h` (structs)

### `Diagnostics/` (logical subset)

**Responsibility**: Console commands, CVars, stats panel, status widget.

**Files**: `UELiveSyncSubsystem.cpp` (DumpState, Stats handlers), `SyncTypes.h` (FLiveSyncStats), `SLiveSyncStatusWidget.*`, `SLiveSyncDiagnosticsWidget.*`

**Indexing boundary**: Diagnostics widgets + stats struct

---

## `Tests/`

**Responsibility**: Standalone validation suites (no UE editor required).

**Key files** (phase-organized):
- `phase6g_identity_stability.py` — 121 identity tests
- `phase6e_delete_validation.py` — 308 lifecycle tests
- `phase6b_runtime_audit.py` — 102 audit checks
- `phase6d_hierarchy_validation.py` — hierarchy tests
- `run_phase*_all.py` — consolidated runners

**Note**: Currently gitignored — CI would need to remove from `.gitignore`.

---

## `Docs/`

**Responsibility**: Architecture docs, scope locks, vertical slices, plans.

**Essential** (for fast context):
- `Docs/ARCHITECTURE.md` — **Read first**
- `Docs/BUG_ENTRYPOINTS.md` — **Entrypoint navigation**
- `Docs/PROJECT_INIT.md` — **Project state**
- `Docs/TASK_PROMPT_TEMPLATES.md` — **Task boilerplate**

**Archived** (rarely needed):
- `Docs/Architecture/01-*` through `40-*` — historical plans, scope locks, audits
- Most are safe to exclude from indexing unless specifically researching a Phase 6F/G feature

**Indexing boundary for debugging**:
- Include: `Docs/ARCHITECTURE.md`, `Docs/BUG_ENTRYPOINTS.md`, `Docs/PROJECT_INIT.md`, `Docs/TASK_PROMPT_TEMPLATES.md`
- Exclude: `Docs/Architecture/` subdirectory (index on demand)

---

## Suggested Indexing Strategy

| Task Type | Index These Only |
|-----------|-----------------|
| Blender-side bug | `Blender_Addon/`, `Docs/ARCHITECTURE.md`, `Docs/BUG_ENTRYPOINTS.md` |
| UE-side bug (general) | `UE_Plugin/.../Private/`, `UE_Plugin/.../Public/`, `Docs/ARCHITECTURE.md` |
| Replay/rebuild bug | `UELiveSyncSubsystem.cpp` (replay section), `SyncTypes.h`, `Docs/PROJECT_INIT.md` |
| Packet/protocol bug | `network.py`, `UELiveSyncSubsystem.cpp` (parser), `SyncTypes.h` |
| Hierarchy bug | `sync.py` (parent diff), `UELiveSyncSubsystem.cpp` (hierarchy handlers) |
| Transform/duplicate bug | `sync.py` (serialize), `network.py`, `UELiveSyncSubsystem.cpp` (create/transform) |
| Test/validation | `tests/` only |

---

## Future Partitioning Recommendation

When the codebase grows beyond current size, consider splitting:

1. **`UELiveSyncSubsystem.cpp`** → separate files by domain:
   - `ReplayCore.cpp` (save/restore/rebuild/verify/export)
   - `HierarchyCore.cpp` (handle/process/resolve)
   - `TransformCore.cpp` (interpolate/handle)
   - `LifecycleCore.cpp` (create/delete)
2. **`network.py`** → split by packet type serializer
3. **`sync.py`** → split by detection strategy: `detectors/transform.py`, `detectors/hierarchy.py`, etc.

This is NOT urgent — current monolithic files are manageable while the codebase is under ~15K lines total. Revisit at ~25K+.
