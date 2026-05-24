# Hierarchy Implementation Notes — Phase 5B Architectural Contract

**Status**: Pre-implementation planning
**Phase**: 5B (hierarchy interpolation refactor)
**Priority**: Correctness > Stability > Performance
**Supersedes**: Naive world-space interpolation for all actors

---

## Section 1 — Core Hierarchy Invariants

These rules are **hard architectural invariants**. Violating any of them produces
incorrect scene graph behavior, transform drift, or attachment desync.

### 1.1 Parent Owns World-Space

**Rule**: Attached children do NOT directly drive their world transform.
Only the UE attachment system (parent→child propagation) writes world
transforms for attached actors.

**Rationale**: Blender sends local transforms for children. The parent's
world transform is the sum of its own world transform and the child's
local offset. If the interpolation loop writes `SetActorTransform(world)`
on a child every tick while it is also attached to a parent, the two
systems fight — the attachment system applies parent motion, then
interpolation overwrites it with stale world data.

**Implementation consequence**:
- `InterpolateTransforms()` must skip `SetActorTransform()` for actors
  with `bHasParent == true` when the interpolation state has converged
  or when only parent motion (not local change) is the source of change.
- `SetActorTransform(world)` on an attached child is only valid
  immediately after attach, to set the initial world pose. After that,
  local-space interpolation feeds the child's **local** offset relative
  to parent.

### 1.2 Child Owns Local-Space

**Rule**: The incoming Blender transform for an attached child is
authoritative in local space. The subsystem must store and interpolate
the child's transform relative to its parent, not as a world position.

**Rationale**: Blender's hierarchy is authoritative. When a parent moves
in Blender, the child's local transform stays the same; only the world
position changes due to parent motion. If we store the child's transform
as world-space, every parent move looks like a child change, and we
incorrectly lerp the child toward a stale world target.

**Implementation consequence**:
- `FSyncTransformState` must either:
  a) Store the **local** transform for children (preferred), OR
  b) Store world but convert back to local on read using the parent's
     current world transform (lossy — see §2.B).
- `UpdateTargetTransform()` must be told whether the incoming data is
  local or world, and handle each case differently based on `bHasParent`.

### 1.3 World-Space Interpolation is Forbidden for Stable Attached Actors

**Rule**: Once a child actor is attached and its local transform matches
the target, `SetActorTransform(WorldTransform)` must NOT execute every
interpolation tick.

**Permitted**: Immediately after attachment, a single `SetActorTransform`
to establish the initial world pose (converted from local + parent world).

**Permitted**: When a new local transform packet arrives, a single
`SetActorTransform` to snap to the new world position.

**Forbidden**: Continuous world-space `SetActorTransform` per tick while
the child's local transform hasn't changed and only the parent is moving.

### 1.4 Interpolation State != Scene Graph Mutation

**Rule**: `CurrentLocation`, `CurrentRotation`, `CurrentScale` are
internal state and may advance every tick. Scene graph writes
(`SetActorTransform`, `AttachToActor`, `DetachFromActor`) are a
**separate concern** gated by:
- Whether the actor is attached
- Whether the local target actually changed
- Whether it's the initial attachment frame

**Rationale**: Internal interpolation state tracking is pure math —
no engine cost. Scene graph mutation triggers physics, rendering, and
blueprint events. Decoupling the two allows smooth internal advancement
without unnecessary engine work.

### 1.6 Authoritative Transforms Must Never Flow World→Local→World Across Frames

**Rule**: Authoritative transforms must not repeatedly flow
`world → local → world` across multiple runtime frames.

**Rationale**: Repeated reconstruction loops are a primary
source of drift accumulation and hierarchy instability in
realtime scene graph systems.

**Acceptable single conversion**:
- Incoming local transform → multiply by parent world → write world once

**Forbidden**:
- Read actor world → convert to local by subtracting parent world →
  store → next frame: read stored local → convert back to world →
  write → next frame: read world again → convert to local → etc.

### 1.7 Attachment Operations Must Be Idempotent

**Rule**: `AttachToActor()` must only be called when the parent
relationship actually changes (guid diff), not every tick.

**Current bug**: `UpdateTargetTransform()` calls `AttachToParent()`
every time a transform packet arrives for an actor with `bHasParent
&& ParentGuid matches existing` (lines 1820–1824). This causes
attachment churn every tick when the parent is moving.

**Correct behavior**: Skip `AttachToParent()` when `ParentGuid ==
State.ParentGuid && State.bHasParent`. Only call on:
- First establishment of parent relationship
- ParentGuid change (reparent)
- Recovery after missing-parent resolution

---

## Section 2 — Implementation Watchpoints

### A. Double-Transform Snapping

**Root cause**: InterpolateTransforms sets `SetActorTransform(world)`
every tick for ALL non-converged actors. For an attached child whose
parent is moving, this writes a stale world target on top of the
attachment system's correct parent-propagation.

**Expected safe behavior**:
- Parent moves in UE → attachment system pushes child with parent.
- Child's internal `CurrentLocation` advances toward `TargetLocation`
  in local space. No scene graph write occurs until convergence.

**Forbidden behavior**:
- Child receives world transform from old packet.
- Interpolation calls `SetActorTransform(world)`.
- Parent moves between two child-local updates.
- Child snaps back to stale world position, then attachment system
  corrects it, creating a visible judder.

**Detection**: Look for `SetActorTransform` in the interpolation loop
without a `bHasParent` guard.

### B. Drift Accumulation

**Root cause**: Repeated local↔world conversion without stable
authoritative storage. If the system stores world transforms for
children and recomputes local by subtracting parent world, every
recomputation introduces floating-point error.

**Expected safe behavior**:
- Children store local transforms directly.
- World transform is only computed on-demand via
  `child_local * parent_world`.
- No round-trip conversion chain: world → local → world.

**Forbidden behavior**:
- `CurrentLocation` stores world for attached child.
- Interpolation computes `local = world - parent_world`.
- Applies `SetActorTransform(local * parent_world)`.
- This drift chain accumulates visibly over 100+ frames.

### C. Attachment Churn

**Root cause**: `AttachToActor(KeepWorldTransform)` called every packet
for the same parent pair.

**Expected safe behavior**:
- `AttachToActor` is only called when `ParentGuid` changes.
- Idempotency check: `if (Child->GetAttachParentActor() == Parent) return;`
  already exists in `AttachToParent()` at line 2384 — but the caller
  in `UpdateTargetTransform()` doesn't skip based on this.

**Forbidden behavior**:
- Calling `AttachToParent()` from the "unchanged parent" path
  (line 1820–1824) every tick.
- This causes UE to process attachment rules, re-evaluate constraints,
  and dirty child transforms every frame.

### D. Hierarchy Invalidation During Snapshots

**Root cause**: During snapshot build, children may arrive before
parents. Attachment is deferred, but state is stored as if attached.

**Expected safe behavior**:
- During snapshot: `AttachToParent` defers to `PendingAttachments`.
- On `EndSnapshot`: all pending resolved in a single pass.
- While deferred: `InterpolateTransforms` should NOT attempt
  parent-relative calculations — treat as root temporarily.

**Forbidden behavior**:
- Interpolation reading `ParentActor` during snapshot build when
  the parent might not exist yet.
- Using parent world to compute local offset before attach resolves.

### E. Runtime Reparent Instability

**Root cause**: Attachment state changes while interpolation is
mid-flight. Current internal state (`CurrentLocation`) is in world
space; after reparent, it must be reinterpreted in local space.

**Expected safe behavior**:
- On reparent (ParentGuid changes): reset internal interpolation state
  for the child.
- New parent: compute local target from incoming transform + new
  parent world. Snap internal current to new local on next frame.
- Do NOT lerp from old world state to new local state — the break
  is intentional.

**Forbidden behavior**:
- Lerping from pre-reparent world space to post-reparent local space.
- Gradually interpolating position across a reparent boundary.

---

## Section 3 — Transform Authority Model

### 3.1 Authority Ownership Table

| Actor Type | Authority | Stored As | Written By | Notes |
|------------|-----------|-----------|------------|-------|
| Root actor | World transform | World in `FSyncTransformState` | `InterpolateTransforms` → `SetActorTransform` | No parent involved; direct write |
| Attached child (stable) | Local transform | Local in `FSyncTransformState` | `InterpolateTransforms` → local advance; `SetActorTransform` only on change | Parent propagation handles world |
| Attached child (initial spawn) | Local transform | Local computed from incoming world / local flag | Single `SetActorTransform(initial world)` on attach | World = local × parent_world |
| Attached child (reparent) | Local transform | Reset to incoming local | Single `SetActorTransform` after detach + attach | Interpolation state resets on reparent |
| Derived child world | Physics engine | Computed | UE attachment system | Read-only from sync perspective |
| Interpolation state | Subsystem internal | `CurrentLocation/Rotation/Scale` | `VInterpTo`, `Slerp` | Never directly written to actor when attached |

### 3.2 Authority Change Points

| Event | Old Authority | New Authority | Conversion |
|-------|--------------|---------------|------------|
| Packet arrives for root | (none) | World target updated | Direct store |
| Packet arrives for child | (none) | Local target updated | Compute local = incoming_world × inverse(parent_world) if packet is world; store directly if packet is local |
| AttachToActor called | World interpolation active | Local interpolation begins | Single world snap on attach, then local-only |
| DetachFromActor called | Local interpolation active | World interpolation begins | Internal state is already in local; must convert to world for subsequent root-mode interpolation |
| ParentGuid changes | Old parent space | New parent space | Reset interpolation state to new target; snap, don't lerp |

### 3.3 Conversion Rules

**Permitted conversions**:
- Incoming world packet → local space: at packet ingestion time only,
  using the **current** parent world transform.
- Incoming local packet → local space: direct store, no conversion needed.
- Local space → initial world (on attach): single conversion via
  `local_transform * parent_world_transform` for the initial
  `SetActorTransform`.

**Forbidden conversions**:
- Deriving authoritative local transforms from interpolated world
  transforms after attachment stabilizes. Once attached and stable,
  the local target is the canonical source of truth; do NOT recompute
  it by subtracting parent world from internal CurrentLocation.
- Round-trip: world → local → world → local across frames. Store
  local directly after the initial conversion, never go back to world.

### 3.4 Phase 5B Authority Flow

```
Packet arrives (child, bHasParent = true)
  │
  ├── PF_HasLocalTransform flag set?
  │     YES → use data directly as local target
  │     NO  → convert: local = incoming_world × inverse(parent_world)
  │
  ├── Store local target in FSyncTransformState
  │
  ├── Actor already attached to correct parent?
  │     YES → skip AttachToActor
  │     NO  → AttachToActor(KeepWorldTransform)
  │            SetActorTransform(local_target × parent_world)   [initial snap]
  │
  └── Each tick:
        ├── If local target changed this frame:
        │     SetActorTransform(local_current × parent_world)
        ├── If local target NOT changed:
        │     Do NOT call SetActorTransform
        │     Allow internal local state to advance toward local target
        └── Parent movement is propagated by UE attachment system
```

---

## Section 4 — Attachment Lifecycle

### 4.1 Normal Attachment

```
1. Child packet arrives (CREATE or TRANSFORM)
2. ParentGuid is valid?
   │
   ├── YES:
   │   ├── Look up parent actor in cache
   │   ├── Parent exists?
   │   │   ├── YES:
   │   │   │   ├── If packet is world-space: convert to local
   │   │   │   │   local = world × inverse(parent_world)
   │   │   │   ├── Store local target in state
   │   │   │   ├── AttachToActor(KeepWorldTransform) — once
   │   │   │   ├── SetActorTransform(local × parent_world) — initial snap
   │   │   │   └── Mark bAttachedStable = true
   │   │   └── NO:
   │   │       └── Queue deferred attachment
   │   │           Store world target (parent unknown — can't convert to local yet)
   │   │
   │   └── (handled)
   │
   └── NO:
       └── Treat as root actor (existing flow)
```

### 4.2 Deferred Attachment (Parent Arrives Later)

```
1. Child arrives, parent not yet cached
2. Store world target (cannot compute local without parent)
3. Queue FPendingAttachment { child, parent, retryFrames = 0 }
4. On each retry attempt:
   ├── Parent now cached?
   │   ├── YES:
   │   │   ├── Convert stored world to local
   │   │   ├── AttachToActor
   │   │   ├── SetActorTransform (initial snap)
   │   │   ├── Clear from pending queue
   │   │   └── Mark bAttachedStable
   │   └── NO:
   │       └── Retry or timeout
   │
5. On timeout:
   ├── Log warning
   ├── Evict from pending queue
   └── Leave actor as root with stored world transform
```

### 4.3 Snapshot Interaction

```
Snapshot Begin (0x09):
  ├── All new AttachToParent calls → deferred to PendingAttachments
  ├── Children spawned but not attached
  └── Local→world conversion deferred (parent might not exist yet)

During Snapshot Build:
  ├── CREATE packets: actors spawned, attach deferred
  ├── TRANSFORM packets: target stored but interpolation skipped
  └── DELETE packets: skipped

Snapshot End (0x0A):
  ├── ResolvePendingAttachments: single pass
  │   For each pending entry with both actors cached:
  │     ├── Convert world target to local (parent now exists)
  │     ├── AttachToActor
  │     ├── SetActorTransform (initial snap)
  │     └── Clear from pending queue
  ├── Remaining (still missing parent): re-queued with retry
  └── Interpolation resumes
```

### 4.4 Reconnect Interaction

```
Reconnect event:
  ├── ActorCache rebuilt from existing world actors
  ├── TransformStates persist (if subsystem not recreated)
  ├── PendingAttachments cleared
  ├── MissingActorTracker cleared
  ├── bInSnapshotBuild reset to false
  │
  └── Normal operation resumes:
        ├── Incoming packets re-establish transforms
        ├── Reparent operations re-attach as packets arrive
        └── No special reconnect cascade needed —
            incremental sync converges
```

### 4.5 Detach (Parent Deleted or Removed)

```
Parent removal detected:
  ├── ParentGuid becomes invalid
  ├── DetachFromActor(KeepWorldTransform)
  ├── bHasParent = false
  ├── Internal state remains in local space
  │
  └── On next packet arrival:
        ├── Incoming transform is now world-space (no parent)
        ├── Overwrite internal state with world target
        └── Resume root-mode interpolation

Note: Between detach and next packet, the actor's world position
is held constant (KeepWorldTransform on detach). No drift occurs.
```

### 4.6 Initialization Order for Attached Children

Every newly created attached child MUST follow this exact order:

1. **Apply initial world transform once**
   - Spawn actor at world position computed from `local × parent_world`
2. **AttachToActor(KeepWorldTransform)**
   - Parent-child relationship established before any interpolation runs
3. **Switch to local-authority interpolation mode**
   - `UpdateTargetTransform` initializes local-space state

**Rationale**: Attaching before initial world placement may cause UE
to recompute incorrect relative transforms. Placing the world
transform first ensures the initial pose is correct. `KeepWorldTransform`
on `AttachToActor` preserves this pose while establishing the
parent-child relationship for future propagation.

---

## Section 5 — Validation Priority Order

Tests must pass in this order. Each level depends on previous levels
being correct.

### 5.1 Static Parent-Child Hierarchy

| Aspect | Expected | Forbidden |
|--------|----------|-----------|
| Child spawns at correct offset from parent | Local offset preserved exactly | Child at world origin |
| Parent rotation propagates to child | Child orbits parent pivot | Child stays in world space |
| Parent scale propagates to child | Child scales with parent | Child scale independent |
| All three transforms (T/R/S) are correct | Matrix composition matches | Any single component wrong |

**Primary regression risk**: Initial `SetActorTransform` after attach
uses wrong conversion (world instead of local, or vice versa).

### 5.2 Moving Parent

| Aspect | Expected | Forbidden |
|--------|----------|-----------|
| Parent translates | Child moves with parent, same offset | Child stays behind, snaps later |
| Parent rotates | Child orbits parent with correct radius | Child rotates around world origin |
| Parent scales | Child scales uniformly with parent | Child scale unchanged |
| Multiple frames of parent motion | Smooth continuous child motion | Child judders or lags behind |

**Primary regression risk**: Interpolation loop on child calls
`SetActorTransform` with stale world position, fighting attachment
system. Must skip `SetActorTransform` for stable attached children.

### 5.3 Moving Grandparent

| Aspect | Expected | Forbidden |
|--------|----------|-----------|
| Two-level hierarchy (grandparent→parent→child) | Both levels propagate correctly | Only direct parent propagates |
| Grandparent moves | Child and parent both follow | Child detaches or drifts |
| Deep chain (N levels) | All N levels propagate | Exponential error accumulation |

**Primary regression risk**: Only the first attachment level is tested.
Multi-level propagation only works if the loop correctly skips
`SetActorTransform` at ALL attached levels, not just children of root.

### 5.4 Runtime Reparent

| Aspect | Expected | Forbidden |
|--------|----------|-----------|
| Child reparented from A to B | Child instantly moves to new parent space | Gradual lerp across reparent |
| Child changed from root→child | New attachment established cleanly | World→local conversion error |
| Child changed from child→root | Detach, keep world position | Position snap or drift |

**Primary regression risk**: Internal interpolation state carries
old parent-relative values across the reparent boundary.

### 5.5 Snapshot Rebuild Hierarchy

| Aspect | Expected | Forbidden |
|--------|----------|-----------|
| Full snapshot creates parent+child | Both spawn, child attached | Child at origin or detached |
| Parent arrives before child | Immediate attach, no deferral | Deferred queue used unnecessarily |
| Child arrives before parent | Deferred, resolved on EndSnapshot | Crash or missing attachment |
| Mixed arrival order in snapshot | All resolved correctly | Partial resolution |

**Primary regression risk**: Local→world conversion during snapshot
when parent exists vs. when parent doesn't exist yet (deferred).

### 5.6 Reconnect Hierarchy Rebuild

| Aspect | Expected | Forbidden |
|--------|----------|-----------|
| Existing actors in UE, incremental sync reconnects | Attachments restored as packets arrive | Actors remain detached |
| Full rebuild after reconnect | Hierarchy identical to pre-disconnect | Missing or wrong parent links |

**Primary regression risk**: `AttachToParent` is not called because
`UpdateTargetTransform` thinks parent hasn't changed — but on reconnect,
the parent relationship must be re-established because the engine
may have cleared it.

### 5.7 Orphan Child Recovery

| Aspect | Expected | Forbidden |
|--------|----------|-----------|
| Child's parent never arrives | Child exists as root, timeout logged | Child invisible or crashing |
| Parent arrives after timeout | Child should NOT auto-attach (too late) | Attachment after timeout |
| Parent arrives within retry window | Child attaches successfully | Missed due to stale retry state |

**Primary regression risk**: Deferred attachment queue correctly
cleans up on timeout but doesn't leave dangling references.

### 5.8 Parent Deletion During Movement

| Aspect | Expected | Forbidden |
|--------|----------|-----------|
| Parent destroyed while child attached | Child detaches, keeps world pos | Child destroyed with parent |
| Child continues receiving transforms after detach | Child moves independently | Child attempts parent-relative calc |
| Parent re-created later | Child can re-attach on new packet | Stale parent pointer access |

**Primary regression risk**: `DetachFromActor` called but internal
state still has `bHasParent = true`, causing local→world conversion
to use non-existent parent.

### 5.9 Large Hierarchy Chain Stress Test

| Aspect | Expected | Forbidden |
|--------|----------|-----------|
| 10-level chain | All levels propagate | Any level breaks |
| 50-level chain | All levels propagate; frame time acceptable | Frame time spike from recursive eval |

**Primary regression risk**: Cost of computing `local × parent_world`
for deep chains. Each level reads parent's world transform, which may
trigger engine transform update.

### 5.10 Mixed Root + Attached Scene

| Aspect | Expected | Forbidden |
|--------|----------|-----------|
| Roots interpolate independently | Root actors move per their own targets | Attached-child logic applied to roots |
| Children interpolate locally | Children maintain offset | Children drift from parent |
| Mixed updates (roots and children same tick) | Both paths correct | One path broken by the other |

**Primary regression risk**: The `bHasParent` branching in
`InterpolateTransforms` correctly distinguishes root vs child code
paths without code duplication errors.

---

## Section 6 — Performance Watchpoints

**Explicit statement**: Performance optimization is deferred until
correctness stabilizes. These metrics are monitoring points only —
no optimization work begins in Phase 5B.

### 6.1 Metrics to Monitor

| Metric | When to Check | Warning Signal |
|--------|---------------|----------------|
| `SetActorTransform` calls per frame for attached children | Every interpolation tick | Count should drop to ~0 for stable attached children; current behavior calls it for every active child |
| `AttachToActor` calls per second | During parent motion | Should be 0 after initial attach; current behavior calls it every packet |
| Transform dirtiness callbacks (UE internal) | During parent motion | Indirectly visible via frame time increase in large hierarchies |
| PendingAttachments queue size | 100ms after EndSnapshot | Should be 0; non-zero means unresolved deferred attachments |
| Queue amplification factor (deferred entries / total children) | End of each retry batch | >1.0 means entries are being re-queued without resolution |
| Interpolation loop cost per attached actor | Per tick in InterpolateTransforms | Should not increase with hierarchy depth — local state advance is O(1) per actor regardless of parent |
| Deep hierarchy SetActorTransform cascade | When grandparent moves | UE engine may cascade N transforms for N-level chain; this is engine overhead, not sync overhead |

### 6.2 Cost Budget (Monitoring Only, Not Enforced)

| Operation | Budget per Actor | Notes |
|-----------|------------------|-------|
| Local state advancement (interp) | < 0.001ms | Pure math, no engine calls |
| Initial attach + snap | < 0.05ms | One-time cost per attachment |
| Per-frame scene graph write (attached, stable) | 0 | Should be zero — only internal state advances |
| Per-frame scene graph write (root) | < 0.01ms | Current baseline: `SetActorTransform` |
| Deferred attachment retry pass | < 0.1ms per 100 entries | Iteration over array with `FindActorFast` lookups |

---

## Section 7 — Non-Goals

The following are explicitly **out of scope for Phase 5B**:

| Feature | Reason for Deferral |
|---------|---------------------|
| Skeletal hierarchy | Requires bone map, pose format, blend shapes — entirely new protocol |
| Pose-space transforms | Requires FBX/asset pipeline integration; not needed for basic transform sync |
| Constraint systems | UE constraints are not replicated from Blender; manual setup expected |
| Non-uniform scale correction | Rare in practice; adds significant complexity for edge case |
| Network compression | Throughput is adequate for current object counts (~500) |
| Transform SIMD optimization | Pre-mature; profile first |
| Multithreaded hierarchy evaluation | Single-threaded game tick model; threading would require sync points that don't exist |
| Custom mesh streaming | Phase 5D handles material/mesh params separately |
| Bidirectional sync (UE→Blender) | Phase 6 scope; requires ACK protocol |
| Physics-driven transform sync | Would require lockstep or state synchronization beyond scope |

---

## Appendix A — Phase 5B Fixes Applied

The following fixes from Appendix A in the Phase 5B planning document
have been implemented:

1. **`InterpolateTransforms` — attached child guard**
   - Attached actors no longer continuously drive world-space transforms.
   - Local-space interpolation updates internal state only.
   - Scene graph write only when `bPendingSceneGraphWrite` is set.

2. **`UpdateTargetTransform` — unconditional `AttachToParent` removed**
   - Parent-change detection now uses a pre-overwrite snapshot.
   - `AttachToParent` is only called on actual parent GUID change.
   - Old unconditional `else if (State.bHasParent)` path deleted.

3. **Local-space storage for attached children**
   - `LocalTargetLocation/Rotation/Scale` store authoritative local values.
   - `CurrentLocalLocation/Rotation/Scale` store advancing local state.
   - `bHasLocalTarget` discriminates child vs root interpolation path.
   - World-space fields are marked `NON-AUTHORITATIVE` for children.

4. **`bPendingSceneGraphWrite` flag**
   - Per-state flag set by `UpdateTargetTransform` on meaningful change.
   - Cleared only after successful scene graph write or attachment transition.
   - Lifecycle rules documented in `SyncTypes.h`.

5. **`PF_HasLocalTransform` passed as parameter**
   - No longer converted to world at ingestion time.
   - `bIsLocalTransform` passed to `HandleCreateObject` and `UpdateTargetTransform`.
   - Local values stored directly; world computed on-demand for scene graph writes.

---

## Appendix B — Terminology

| Term | Definition |
|------|------------|
| Root actor | Actor with no parent (ParentGuid is zero) |
| Attached child | Actor with valid ParentGuid, attached via UE `AttachToActor` |
| Local transform | The transform of a child relative to its parent |
| World transform | The absolute transform in world space |
| Authority | Which system owns the canonical value of a transform component |
| Churn | Repeated attachment operations on a stable relationship |
| Drift | Accumulated floating-point error from repeated conversion |
| Reparent | Changing a child's parent from one actor to another |
| Deferred attachment | Child's attachment queued because parent hasn't arrived yet |

---

## Revision History

| Date | Version | Author | Changes |
|------|---------|--------|---------|
| 2026-05-23 | 1.0 | Phase 5B planning | Initial architectural contract document |
| 2026-05-24 | 2.0 | Phase 5B implementation | Implemented: local-space storage, attached-child guard, idempotent attachment, hierarchy safety (self-parent, depth limit, cycle detection), drift diagnostics, quaternion normalization, deferred world rewrite, detach re-seed |
