# Critical Invariants — Hard Rules, Do Not Break

If a change would violate any of these, **stop and redesign**. These are not guidelines — they are architectural safety barriers.

---

## A — GUID Identity Invariants

| # | Invariant | Why | If Violated | Protected Systems |
|---|-----------|-----|-------------|-------------------|
| GI-1 | GUID depends ONLY on `obj.data.name` (excludes `obj.name`) | `_compute_owner_hash()` must be stable across rename | Rename → GUID churn → DELETE+CREATE cycle → actor loses label | ensure_guid, _reconcile_guids_on_load, GRenamePersistentLabel |
| GI-2 | Transform/visibility/collection changes MUST NOT change GUID | Hash only includes datablock name | Unnecessary DELETE+CREATE on every transform | All mutable state handlers |
| GI-3 | Duplication (obj.copy()) MUST regenerate GUID | Copy inherits original GUID — collision guaranteed | Silent identity corruption, two objects share GUID | ensure_unique_guid collision detection |
| GI-4 | Datablock change (mesh swap) MUST regenerate GUID | Underlying identity changed | Stale hash → stale identity mapping on reconnect | _reconcile_guids_on_load |
| GI-5 | Missing/corrupted owner hash MUST NOT silently accept stale mapping | Stored hash is authoritative identity proof | Undetected identity drift, wrong actor gets wrong transform | _reconcile_guids_on_load |

**Forbidden**: Adding `obj.name`, `obj.location`, `obj.hide_viewport`, `obj.users_collection`, or any mutable state to `_compute_owner_hash()`.

---

## B — Replay Determinism Invariants

| # | Invariant | Why | If Violated | Protected Systems |
|---|-----------|-----|-------------|-------------------|
| RD-1 | Same sequence of replay packets → same final world state | Determinism is the core contract | Nondeterministic replay → hash mismatch → rollback | GWorldReplayBuffer, SaveWorldState, RestoreWorldState |
| RD-2 | ComputeWorldStateHash must hash ALL authoritative domains | Hash is truth for divergence detection | Missed domain → divergence undetected → silent corruption | ComputeWorldStateHash |
| RD-3 | Replay order must maintain original packet arrival order | Cross-packet dependencies (create-before-rename) | Wrong order → dependency violation → crash or wrong state | ProcessQueuedPackets, RebuildWorldFromSnapshot |
| RD-4 | Rollback must restore exact prior state | Replay failure must be invisible to game | Corrupt world state after failed replay | RestoreWorldState temp save/restore |
| RD-5 | GWorldReplayBuffer must be cleared on ConsoleReset AND StopNetworkThread | Stale replay entries across connection cycles | Wrong state after reconnect replay | ConsoleReset, StopNetworkThread |

**Forbidden**: Non-deterministic data (random, clock, allocator address) in replay path. Skipping hash domains. Partial rollback.

---

## C — Rename Authority Invariants

| # | Invariant | Why | If Violated | Protected Systems |
|---|-----------|-----|-------------|-------------------|
| RN-1 | GRenamePersistentLabel is the SOLE source of truth for labels | Labels are GUID-bound authoritative state, not UE default | HandleCreateObject spawns with default label → overwrite | HandleRename, HandleCreateObject |
| RN-2 | GRenamePersistentLabel MUST survive StopNetworkThread (reconnect) | Reconnect rebuild must restore authoritative labels | Labels lost on reconnect | StopNetworkThread (line 1868 — intentional skip clear) |
| RN-3 | GRenamePersistentLabel MUST be cleared on ConsoleReset | Full reset must restore clean state | Stale labels after reset | ConsoleReset (line 11908) |
| RN-4 | HandleCreateObject MUST restore from GRenamePersistentLabel before returning | No default-label window after spawn | Brief default label visible, then corrected | HandleCreateObject (~line 5682) |
| RN-5 | RestoreWorldState + RebuildWorldFromSnapshot MUST apply GRenamePersistentLabel overlay | Replay/rebuild must reproduce authoritative labels | Replayed labels lost after restore | RestoreWorldState (~8172, 8196), RebuildWorldFromSnapshot (~8632) |

**Forbidden**: Reading `AActor::GetActorLabel()` as authoratative. Default UE label generation without persistent registry check. Clearing GRenamePersistentLabel on disconnect.

---

## D — Transform Authority Invariants

| # | Invariant | Why | If Violated | Protected Systems |
|---|-----------|-----|-------------|-------------------|
| TF-1 | Blender is authoritative for all transforms | UE is interpolation-only client | Feedback loop → drift → permanent desync | InterpolateTransforms |
| TF-2 | UE must NOT send transforms back to Blender | Unidirectional protocol | Blender state overwritten → data loss | network.py (send-only) |
| TF-3 | FSyncTransformState must remain POD-only | No UE-GC-tracked members in transform state | GC crash or memory corruption | SyncTypes.h FSyncTransformState |
| TF-4 | Child transforms attached to parent must use KeepRelative | Parent movement must propagate to child | Child world transform baked → child floats at world position | AttachToActor in HandleHierarchy |
| TF-5 | Transform authority domain must explicitly migrate on root↔child transition | Incoming packet domain (local vs world) takes precedence over stale cached `bHasLocalTarget` | Parent-attach jumps, origin snapping, cumulative offset drift, stale local transforms, child transform corruption, replay divergence after parenting | UpdateTargetTransform, InterpolateTransforms, HandleHierarchy, replay rebuild hierarchy restore, local/global reconciliation |

**TF-5 details**:
When transitioning:
- **root → child**: `bHasLocalTarget` must be set to `true`; `CurrentLocalLocation/Rotation/Scale` must be initialized from the incoming local transform; world-space cache (`TargetLocation/Rotation/Scale`) must be recomputed as `Local × ParentWorld`; `ParentGuid` and `bHasParent` must be reconciled.
- **child → root**: `bHasLocalTarget` must be set to `false`; local-state fields must be invalidated; world-space `TargetLocation/Rotation/Scale` must be set directly; `ParentGuid` cleared; `bHasParent` set to `false`.
- The interpolation path in `InterpolateTransforms` must match the authority domain: local path for children (`bHasLocalTarget && bHasParent`), world path for roots.

**Forbidden**: Sending transforms from UE to Blender. Adding UObject* pointers to FSyncTransformState. Using SnapToTarget or KeepWorld for parented children. Applying local-transform packets through the world-authority path. Retaining stale root `bHasLocalTarget` state after parenting. Leaving stale `bHasLocalTarget` after detach.

---

## E — Hierarchy Invariants

| # | Invariant | Why | If Violated | Protected Systems |
|---|-----------|-----|-------------|-------------------|
| HI-1 | Parent GUID must remain stable across renames | Name change must not break hierarchy | Child orphaned on rename | _last_parent_guid stores GUID, not name |
| HI-2 | Hierarchy must survive replay rebuild | Replay must reproduce identical attachment graph | Orphaned children after replay | ResolveHierarchyAttachments, GReplayHierarchyAttachments |
| HI-3 | Hierarchy must survive reconnects | Reconnect must not lose parent relationships | Children detached after reconnect | BuildActorCache, HandleCreateObject |
| HI-4 | Transform updates MUST NOT detach children | Moving one object must not break another's attachment | Child floats at world position after unrelated transform | InterpolateTransforms, HandleTransformPacket |
| HI-5 | Deferred parent resolution must retry (10 fast + 10 slow, 60-frame cap) | Parent may not exist yet during snapshot burst | Child permanently orphaned | ResolveHierarchyAttachments |
| HI-6 | Cycle detection (WouldCreateHierarchyCycle) must prevent infinite parent chains | Self-cycle, 2-cycle, N-cycle all possible | Stack overflow or freeze | WouldCreateHierarchyCycle, HandleHierarchy |

**Forbidden**: Clearing hierarchy registry on unrelated transform. Using SnapToTarget for parent attachment. Skipping cycle detection.

---

## F — Collection Invariants

| # | Invariant | Why | If Violated | Protected Systems |
|---|-----------|-----|-------------|-------------------|
| CL-1 | Collection replay must be idempotent | Replaying same collection packet twice must not corrupt | Duplicate membership or double-add | GCollectionMembership, ApplyCollectionMembership |
| CL-2 | Collection rebuild must use deterministic sorted-GUID ordering | Rebuild must produce identical state across runs | Nondeterministic collection assignment | ComputeCollectionStateHash |
| CL-3 | Collection replay hash must include all membership state | Hash is divergence detection truth | Undetected drift | ComputeCollectionStateHash |

**Forbidden**: Non-deterministic iteration over collection membership. Skipping membership in hash.

---

## G — Snapshot Invariants

| # | Invariant | Why | If Violated | Protected Systems |
|---|-----------|-----|-------------|-------------------|
| SN-1 | ExportWorldSnapshot must include ALL authoritative domains | Missing domain → rebuild loses state | Lost labels, lost hierarchy | ExportWorldSnapshot |
| SN-2 | RebuildWorldFromSnapshot must restore GUIDs, labels, hierarchy, collections identically | Rebuild must mirror original | Post-rebuild world state differs | RebuildWorldFromSnapshot |
| SN-3 | Snapshot rebuild must preserve GRenamePersistentLabel | Labels survive rebuild or they're unreliable | Label loss after rebuild | RebuildWorldFromSnapshot rename domain |

**Forbidden**: Omitting a domain from export. Using non-canonical GUID ordering in export.

---

## H — Rollback Safety Invariants

| # | Invariant | Why | If Violated | Protected Systems |
|---|-----------|-----|-------------|-------------------|
| RB-1 | Replay failure must fully restore pre-replay state | No half-applied state | Corrupt world, crash | RestoreWorldState temp save/restore |
| RB-2 | Diagnostics must NEVER mutate runtime state | Observability must not affect behavior | Debug-only code changes behavior | All diagnostic counters, logging |
| RB-3 | FScopedChangeOrigin must guard all replay mutations | Provenance tracking prevents observer confusion | Operations misidentified as user actions | HandleCreate, HandleDelete, HandleRename, HandleHierarchy |

**Forbidden**: Side effects in diagnostic code paths. Logging that allocates or mutates state. Missing EChangeOrigin scopes on replay operations.

---

## I — Networking Invariants

| # | Invariant | Why | If Violated | Protected Systems |
|---|-----------|-----|-------------|-------------------|
| NW-1 | Packet magic must always be validated before parsing | Magic mismatch → wrong protocol | Crash on corrupted stream | ProcessBinaryPacket |
| NW-2 | Packet version must be checked for backward compat | V2/V3/V4/V5 all accepted | Old Blender → UE incompatibility | ProcessBinaryPacket version dispatch |
| NW-3 | All PT_* constants must appear in kValidTypes and LIVE_SYNC_PROTOCOL_SIG | Protocol signature drift detection | Undetected protocol drift | FNV signature verification |
| NW-4 | TCP header is always 24 bytes fixed, little-endian | Header format must never change without major version bump | Wrong header parse → corrupted packet stream | build_v5_header, header parse |

**Forbidden**: Changing header layout. Adding PT constants without updating FNV hash. Removing backward compat for V2/V3/V4.

---

## J — Diagnostic Invariants

| # | Invariant | Why | If Violated | Protected Systems |
|---|-----------|-----|-------------|-------------------|
| DG-1 | TRACE_CPUPROFILER_EVENT_SCOPE and UE_LOG traces are NOT comments | They are intentional observability infrastructure | Performance debugging loss | All profiler scopes |
| DG-2 | All FLiveSyncStats counters use std::memory_order_relaxed | Display values only — no ordering guarantees | Incorrect assumptions about counter precision | FLiveSyncStats |
| DG-3 | Diagnostics panel refresh rate max 250ms | Must not tick every frame | Performance regression | SLiveSyncDiagnosticsWidget |
| DG-4 | Verbose logging behind UE.LiveSync.Verbose CVar | Production path must not alloc for logging | Performance regression in production | All Verbose logs |
| DG-5 | FDiagnosticsHistory bounded at 32 entries | Never unbounded event lists | Memory leak | Event history arrays |

**Forbidden**: Removing profiler scopes. Using counters for synchronization. Removing CVar gates on verbose logs.
