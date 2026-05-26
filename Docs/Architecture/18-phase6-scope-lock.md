# Phase 6 Scope Lock — Live Editing System

> **Created**: 2026-05-25
> **Phase 5**: COMPLETE · **Phase 6**: ACTIVE (Rename STABILIZED · Visibility STABILIZED · Hierarchy IN PROGRESS)
> **Runtime core**: FROZEN (`v0.5.0-stabilized`)
>
> This document defines the **hard scope boundaries** for Phase 6.
> Anything outside this scope must be deferred to later phases
> unless explicitly approved via ADR review.

---

## 1. Purpose

Phase 6 (Live Editing System) is the first phase of **editor-side workflow
synchronization** between Blender and Unreal Editor. It extends the Phase 5
runtime foundation with bidirectional awareness — not full bidirectional
authority — enabling the Unreal Editor to reflect and react to editor-level
changes originating in Blender.

This scope lock exists to:

1. **Prevent scope creep** — Phase 6 targets a specific set of
   editor-interaction features. It does NOT rebuild the entire
   synchronization architecture.
2. **Protect the frozen runtime core** — The Phase 5 protocol parser,
   Tick pipeline, queue ownership, and thread lifecycle must remain
   untouched (see `12-core-runtime-invariants.md`).
3. **Force architectural decisions** — Authority model questions
   (see `13-phase6-design-constraints.md`) must be settled before
   implementation, not during it.
4. **Provide a clear "done" definition** — Phase 6 ends when the
   in-scope editor workflows are stable. Everything else waits.

---

## 2. Official Title

**Phase 6 — Live Editing System**

Phase 6 synchronises **editor-level object state** between Blender and
the Unreal Editor. It targets the interactive editing loop: create,
rename, delete, duplicate, organise, show/hide. It does NOT target
artistic content (meshes, materials, animations, sequencer timelines)
or runtime gameplay.

---

## 3. IN-SCOPE Features

Every feature in this section must be implemented as a **new packet type**
(or extension of an existing new packet type) processed by a **new Tick stage**
appended **after** the existing Phase 5 Tick pipeline. No existing
pipeline stage, parser branch, queue, or thread boundary may be modified.

### 3.1 — Object Create/Delete Replication

| Field | Value |
|-------|-------|
| **Description** | When Blender creates or deletes an object, the UE editor spawns or destroys the corresponding actor. When an actor is deleted in the UE editor (by the user), the deletion replicates back to Blender. |
| **Packet types** | `PT_CREATE` (0x03, exists), `PT_DELETE` (0x04, exists) — extend for editor-origin |
| **Authority model** | Blender authoritative for Blender-origin creates/deletes. UE editor creates/deletes replicate back only if the actor is managed (`UELiveSync_Managed` tag). |
| **Challenges** | Distinguishing editor user delete from Blender-sync delete; preventing delete storms from bulk operations; handling undo of delete (re-spawn). |

### 3.2 — Rename Replication

| Field | Value |
|-------|-------|
| **Description** | Blender object rename → UE actor label update. UE editor rename (user renames in World Outliner) → Blender object rename. |
| **New packet type** | `PT_RENAME` (proposed `0x0C`) — fixed-size: GUID (16) + name-length (2) + UTF-8 name (variable, max 256 bytes) |
| **Authority model** | Last-writer-wins with origin tagging (`EChangeOrigin::BlenderSync` / `EChangeOrigin::User`) to prevent feedback loops. |
| **Challenges** | Rename storms (bulk rename → 200 packets/sec); name truncation (UE max 64 chars vs Blender unlimited); special characters (UTF-8 vs UE FName rules); undo/redo interaction. |

### 3.3 — Duplicate Detection

| Field | Value |
|-------|-------|
| **Description** | Detecting duplicated objects on both sides and assigning new GUIDs. Preventing duplicate actors (in UE) that trace back to the same Blender object. |
| **Approach** | Phase 5 already handles Blender-side duplicate via `ensure_unique_guid()` in `sync.py`. Phase 6 adds UE-side duplicate detection: when a managed actor is duplicated in UE (Alt+Drag), the new actor spawns without a GUID → detected → creates new GUID → sends PT_CREATE back to Blender. |
| **Challenges** | Distinguishing intentional duplicate from transient construction-script spawns; handling copy/paste across levels; avoiding GUID collision with existing managed actors. |

### 3.4 — Collection/Folder Sync

| Field | Value |
|-------|-------|
| **Description** | Blender collections (or primary collection) map to UE World Outliner folders. Adding/removing objects from collections in Blender updates UE folder membership. Creating/deleting collections creates/deletes folders. |
| **New packet type** | `PT_COLLECTION` (proposed `0x0E`) — GUID (16) + parent-collection-GUID (16) + flags (1) + name-length (2) + UTF-8 name (variable, max 256) |
| **Authority model** | Blender authoritative for collection structure. UE folder renames (by user) replicate back to Blender. |
| **Challenges** | Blender multi-collection membership per object (no UE equivalent); UE folders are flat (no nesting beyond one level in World Outliner by default); collection visibility does not map 1:1 to folder visibility; objects in multiple Blender collections must choose a "primary" collection for UE folder assignment. |

### 3.5 — Visibility Sync

| Field | Value |
|-------|-------|
| **Description** | Blender viewport hide/unhide → UE actor hidden/show in editor. UE editor hide/unhide → Blender viewport visibility. |
| **New packet type** | `PT_VISIBILITY` (proposed `0x0E`) — GUID (16) + visible (1, bool) |
| **Authority model** | Last-writer-wins with origin tagging. Transient visibility toggles (Alt+H "show hidden", hotkey toggle) must be detected and suppressed. |
| **Challenges** | UE has two visibility concepts: editor-visibility (eye icon in Outliner) and game-visibility (bHidden). Blender has viewport visibility and render visibility. Mapping is non-trivial. Transient toggles must be ignored (detected via rapid toggle rate > 10/sec). |

### 3.6 — Editor-Side Hierarchy Synchronization

| Field | Value |
|-------|-------|
| **Description** | Blender parent-child relationships → UE actor attachments. UE re-parenting (user drags actor in World Outliner) → Blender parent update. |
| **Current state** | Phase 5 implements Blender→UE hierarchy via parent-GUID in transform packets and deferred attachment resolution. Phase 6 adds UE→Blender direction. |
| **Authority model** | Last-writer-wins. Blender parent change → UE re-attach. UE user re-parent → Blender parent update. |
| **Challenges** | Cycle prevention (must check before applying); UE attachment uses AttachToActor vs Blender parenting has different semantics; deferred parent resolution must not reorder. |

### 3.7 — GUID Persistence for Editor Workflows

| Field | Value |
|-------|-------|
| **Description** | Ensuring GUIDs survive editor workflows: save/load, PIE, level transitions, actor copy/paste. Adding UE-side GUID metadata tag for managed actor identification. |
| **Implementation** | Store GUID in an actor metadata tag (`UELiveSync_Guid`) or a `TMap<FGuid, TWeakObjectPtr<AActor>>` persisted via UWorld asset registry tags. On actor load/spawn, check for existing GUID → re-link to sync state. |
| **Challenges** | GUID must survive UE save/load (level persistence); PIE transitions must not stomp GUIDs; level streaming adds lifecycle complexity; actor copy/paste in UE would create actors without GUIDs → must detect and create new GUID + PT_CREATE. |

### 3.8 — Editor-Safe Synchronization Policies

| Field | Value |
|-------|-------|
| **Description** | Policies governing when and how editor-side changes replicate. Origin tagging, rate limiting, coalescing, flood detection extension. |
| **Implementation** | Extend existing Phase 5 flood detection (2-second window) with rename-specific rate limiters (60/sec max). Add coalescing timer (50ms batch window for rename events). Add origin tag to every mutation. |
| **See also** | `14-editor-sync-safety.md` for full policy rules. |

### 3.9 — Replication Suppression Rules

| Field | Value |
|-------|-------|
| **Description** | Explicit rules for when replication must NOT occur: during PIE, during undo/redo transactions, during transient editor actions (drag, resize, construction scripts), during snapshot replay, during reconnect cooldown. |
| **Implementation** | Check `GEditor->PlayWorld != nullptr` for PIE suppression; check `GUndo || UTransactor::IsUndoing()` for undo suppression; tag transient actors with `RF_Transient` for filtering. |
| **See also** | `14-editor-sync-safety.md` section 2. |

### 3.10 — Stale/Zombie Actor Recovery

| Field | Value |
|-------|-------|
| **Description** | Actors whose base component was lost (Outer destroyed) but GUID persists → re-spawn. Actors deleted in UE but still tracked by Blender → tombstone set to prevent re-spawn loop. |
| **Current state** | Phase 5 implements RecoverMissingActors. Phase 6 adds the tombstone set (bounded 1024 entries, 30-second TTL) and manual recovery via `UE.LiveSync.Reset`. |
| **See also** | `14-editor-sync-safety.md` sections 6–7. |

### 3.11 — Editor Transaction Safety

| Field | Value |
|-------|-------|
| **Description** | Sync operations must not create undo history entires that confuse the user. Undo of a sync operation must not cause desync. |
| **Implementation** | Wrapping sync mutations in `GEditor->BeginTransaction(nullptr, LOCTEXT(...), nullptr)` suppression block, or tagging transactions as non-user-visible. On undo detection, trigger a re-sync of the affected GUID(s). |
| **Challenges** | UE's undo system is pervasive — suppressing it requires careful scoping; undo of actor delete = re-spawn = must re-sync with Blender. |

### 3.12 — Reconnect-Safe Editor Synchronization

| Field | Value |
|-------|-------|
| **Description** | When the UE editor reconnects to Blender (after disconnect or editor restart), the editor sync state (renames, folder changes, visibility changes made during disconnection) must be reconciled. |
| **Implementation** | On reconnect, Blender sends a snapshot of current editor state (renames, folders, visibility). UE merges this with its local state. Conflicts are resolved via last-writer-wins with timestamp comparison. |
| **Challenges** | During disconnection, the user may have renamed actors in UE — these must not be silently overwritten. Timestamp-based conflict resolution is fragile; eventual consistency may be acceptable initially. |

---

## 4. OUT-OF-SCOPE Features (Deferred)

| Feature | Deferred To | Rationale |
|---------|-------------|-----------|
| Sequencer sync | Phase 7 | Requires timeline data model, keyframe serialization |
| Animation sync | Phase 7 | Requires bone/pose serialization, NLA track mapping |
| Timeline sync | Phase 7 | Requires sequencer integration |
| Camera playback sync | Phase 7 | Cinematic-specific feature |
| Multiplayer/network gameplay sync | Phase 8 | Requires runtime replication layer |
| Runtime packaged-game sync | Phase 8 | Requires game-mode integration |
| Mesh streaming | Phase 8 | Requires chunked asset pipeline |
| Material/shader live editing | Backlog | Requires material parameter serialization |
| Geometry node replication | Backlog | Requires geometry node graph serialization |
| Binary compression optimization | Phase 9 | Wire size reduction — not needed at current scale |
| Delta serialization optimization | Phase 9 | Bandwidth optimization — not needed at current scale |
| Interest management | Phase 9 | Large-world scalability |
| Multi-user collaborative editing | Phase 9 | Requires server authority model |
| Bidirectional authority (full) | Phase 9 | Phase 6 uses limited last-writer-wins only |
| Cloud sync | Phase 9 | Requires cloud infrastructure |
| Remote internet sync | Phase 9 | Requires NAT traversal, TLS, auth |
| Mobile support | Phase 9 | Requires platform-specific socket layer |
| Asset cooking/build systems | Phase 9 | Requires UAT/UBT integration |

**Principle**: If a feature's primary benefit is performance, compression,
scalability, or collaboration — and not editor workflow synchronization —
it is deferred to Phase 8 or Phase 9.

---

## 5. Authority Boundaries

```
                           Phase 6 Authority Model
┌─────────────────────────────────────────────────────────┐
│  Blender                                               │
│  ┌─────────────────────────────────────────────┐       │
│  │ Authoritative for:                          │       │
│  │  • Object creation  (PT_CREATE origin)      │       │
│  │  • Object deletion  (PT_DELETE origin)      │       │
│  │  • Collection structure                     │       │
│  │  • Parent hierarchy  (default direction)    │       │
│  │  • GUID generation   (uuid.uuid4().hex)     │       │
│  │  • Transform state   (unchanged from P5)    │       │
│  └─────────────────────────────────────────────┘       │
│                    │                                    │
│              TCP (bidirectional in P6)                  │
│                    │                                    │
│  Unreal Editor                                         │
│  ┌─────────────────────────────────────────────┐       │
│  │ Authoritative for:                          │       │
│  │  • Asset resolution  (unchanged from P5)    │       │
│  │  • Local actor tag    (UELiveSync_Managed)  │       │
│  │  • Local editor-only override filtering     │       │
│  │                                              │       │
│  │ Last-writer-wins for (P6 only):             │       │
│  │  • Rename (tagged origin)                   │       │
│  │  • Visibility (tagged origin)               │       │
│  │  • Re-parent (tagged origin)                │       │
│  └─────────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────┘
```

### Key Restrictions

1. **Blender remains authoritative for all object lifecycle**
   (create, delete, GUID generation) — UE never initiates object
   creation under a Blender-managed GUID.

2. **Bidirectional authority is limited to rename, visibility,
   and hierarchy** — and only via last-writer-wins with origin
   tagging. Full bidirectional editing is OUT OF SCOPE.

3. **Conflict resolution systems are OUT OF SCOPE** — last-writer-wins
   with timestamp comparison is the only conflict strategy. No merge
   algorithms, no conflict UI, no three-way merge.

4. **Multi-user arbitration is OUT OF SCOPE** — the system assumes
   exactly one Blender ↔ one UE editor. Multiple editors or
   collaborative workflows are not supported.

5. **Transform authority remains unchanged** — Blender → UE only.
   UE never sends transforms to Blender.

---

## 6. Non-Goals

Phase 6 explicitly does NOT attempt to:

1. **Build an Omniverse/Perforce/Source-Control clone** — This is a
   real-time editor sync tool, not a collaboration platform.
2. **Replace source control** — Users still need Perforce/Git/SVN for
   asset version management. Phase 6 does not provide branching,
   merging, changelists, or diffing.
3. **Solve collaborative editing** — Phase 6 assumes one editor at a
   time. No locking, no check-in/check-out, no conflict resolution UI.
4. **Solve runtime networking** — No multiplayer support, no
   dedicated server, no client-authoritative gameplay sync.
5. **Support arbitrary UObject replication** — Only specific
   editor-level state (name, visibility, hierarchy, folder) is
   replicated. Not textures, not materials, not blueprints, not
   gameplay properties.
6. **Support every editor subsystem immediately** — Only World
   Outliner (actor-level) operations are in scope. Not the Blueprint
   editor, not the Material editor, not the Sequencer, not the
   Particle editor.

---

## 7. Architectural Preservation Rules

The Phase 5 runtime core is **FROZEN**. Phase 6 must not modify:

### FROZEN — Do Not Touch

| System | File(s) | Risk if Modified |
|--------|---------|-----------------|
| Packet parser (version dispatch, binary header, magic validation) | `UELiveSyncSubsystem.cpp` (ProcessBinaryPacket) | Backward compat breakage, malformed packet crashes |
| Tick pipeline ordering | `UELiveSyncSubsystem.cpp` (main Tick) | Transform-before-spawn races, BEGIN/END imbalance |
| Queue ownership model | `LiveSyncQueue.h`, `PendingAssetQueue.h` | Data races, queue corruption, use-after-free |
| Network thread lifecycle & shutdown order | `LiveSyncRunnable.h/cpp` | Game thread deadlock (Linux: missing Shutdown before Close) |
| Thread ownership (network enqueue only, game thread only) | All runtime files | Cross-thread UObject access crashes |
| FSyncTransformState layout | `SyncTypes.h` | Wire format incompatibility |
| 24-byte header layout | `SyncTypes.h` (implicit) | Protocol breakage across all versions |

### CAUTION — Modify Only in New Files/Stages

| System | Guidance |
|--------|----------|
| New packet types | Add new `case` in ProcessBinaryPacket dispatch; do not modify existing branches |
| New Tick stages | Append after `PurgeStaleActors`; do not reorder existing stages |
| New queues/threads | Phase 6 may add UE→Blender socket sender (new file, new thread); must not modify existing queue ownership |
| UE→Blender TCP channel | New infrastructure only — no changes to existing Blender→UE socket path |

### SAFE — Modify Freely

| System | Guidance |
|--------|----------|
| CVars | Add new CVars for rate limits, coalescing timers, feature toggles |
| Console commands | New Exec() handlers for diagnostics, manual recovery |
| Diagnostics/metrics | New display-only counters (`std::memory_order_relaxed`, O(1) update) |
| UI panels | Status widget, diagnostics widget, new editor UI elements |
| Editor module registration | New menus, toolbars, tab spawners |

### Reference Documents

- `12-core-runtime-invariants.md` — Full invariant documentation
- `16-known-safe-modification-zones.md` — Detailed risk-level mapping

---

## 8. Escalation Rules

If a requested feature or bug fix requires modification of a **FROZEN**
or **HIGH RISK** system (as defined in `16-known-safe-modification-zones.md`),
the following process must be followed:

### Step 1: Pause Implementation

Stop all code changes to the affected system. Do not "quick-fix" a frozen
system without review.

### Step 2: ADR Review Required

Create or update an Architecture Decision Record (`15-architecture-decision-records.md`) documenting:

- The exact change proposed
- Why the frozen system must be modified (vs. working around it)
- The risk assessment (what existing invariants break)
- The mitigation plan (how each broken invariant is restored)

### Step 3: Roadmap Reassessment

Evaluate whether the change:

1. Is a **critical bug fix** — crash/data-loss scenario with reproduction steps
2. Is a **required Phase 6 feature** that cannot be implemented without touching frozen code
3. Is a **nice-to-have optimization** that should be deferred

Only category (1) may proceed immediately. Category (2) requires sign-off
from the project lead. Category (3) is deferred.

### Examples

| Scenario | Escalation | Outcome |
|----------|-----------|---------|
| "We need to increase FLiveSyncQueue from 128 to 256" | Queue capacity is in a FROZEN zone | Must reproduce queue overflow scenario first; if validated, ADR + lead sign-off |
| "The packet parser returns wrong error for truncated packets" | Parser is HIGH RISK | Reproduction required; minimal fix only |
| "We want to add a new field to FSyncTransformState" | Object layout is FROZEN | Must bump protocol version to V6; ADR required |
| "Rename replication would be easier if we modify ProcessBinaryPacket" | Parser is HIGH RISK | No — implement as new packet type with new case branch; no existing branch modification allowed |

---

## 9. "Done" Criteria for Phase 6

Phase 6 is complete when all of the following are verified:

### Feature Completion

| # | Criterion | Verification |
|---|-----------|-------------|
| 1 | Blender object create → UE actor spawn (editor-only, managed tag) | Test: create 100 objects in Blender, verify 100 actors in UE |
| 2 | UE editor actor delete → Blender object delete | Test: delete managed actor in UE outliner, verify Blender sync |
| 3 | Blender rename → UE actor label update | Test: rename 50 objects, verify labels updated within 500ms |
| 4 | UE editor rename → Blender object rename | Test: rename managed actor in UE, verify Blender updates |
| 5 | Blender duplicate → unique GUID → new UE actor | Test: Ctrl+D in Blender, verify new actor in UE with new GUID |
| 6 | UE duplicate (Alt+Drag) → new GUID → Blender PT_CREATE | Test: Alt+Drag managed actor, verify Blender receives new object |
| 7 | Blender collection create/delete → UE folder create/delete | Test: create/delete collection in Blender, verify UE folder follows |
| 8 | Blender collection membership → UE folder hierarchy | Test: assign objects to collection, verify folder assignment in UE |
| 9 | Blender hide/unhide → UE actor visibility | Test: toggle viewport visibility on 30 objects, verify UE matches |
| 10 | UE editor hide/unhide → Blender visibility | Test: toggle eye icon in UE outliner, verify Blender viewport |
| 11 | Blender parent change → UE re-attachment | Test: re-parent objects in Blender, verify UE attachment chain |
| 12 | UE re-parent → Blender parent update | Test: drag actor in UE World Outliner, verify Blender parent change |

### Stability & Safety

| # | Criterion | Verification |
|---|-----------|-------------|
| 13 | No editor feedback loops (rename/visibility/parent cycling) | Test: rename 200 objects in Blender, verify 0 bounce-back renames |
| 14 | PIE mode suppresses all Editor→Blender replication | Test: enter PIE, verify no packets sent to Blender for editor actions |
| 15 | Undo/redo does not cause desync | Test: create actor in Blender → undo in UE → verify re-sync consistency |
| 16 | Transient editor actions (Alt+H show hidden) ignored | Test: rapidly toggle visibility with hotkey, verify no packet storm |
| 17 | Reconnect storm test passes (20 cycles) | Extend phase5e reconnect test with editor-state verification on reconnect |
| 18 | Mass rename (500 objects) does not cause packet loss | Test: batch rename 500 Blender objects, verify UE receives all renames |
| 19 | Mass delete (500 objects) does not drop packets | Test: batch delete 500 Blender objects, verify UE removes all actors |
| 20 | Managed actor tag correctly filters editor-only actors | Test: spawn unmanaged actor in UE, verify it is never touched by sync |

### Runtime Invariant Preservation

| # | Criterion | Verification |
|---|-----------|-------------|
| 21 | Phase 5 validation tests (5/5) still pass | Run `python3 tests/phase5e_validation.py` |
| 22 | Phase 5C fuzz tests still pass | Run `python3 tests/phase5c_fuzz_protocol.py` |
| 23 | Phase 5C stress tests still pass | Run `python3 tests/phase5c_stress_protocol.py` |
| 24 | Phase 5D asset identity tests still pass | Run `python3 tests/phase5d_validation_A_asset_identity.py` |
| 25 | No modifications to FROZEN runtime systems (parser, queues, threads, layout) | Git diff review — only new files and CAUTION/SAFE zones modified |
| 26 | BEGIN/END trace pairs remain balanced across all Tick stages | Pipeline health check (existing phase5e test #4) |
| 27 | No addition of UObject fields to FSyncTransformState | Code review — asset metadata still in separate TMap |

---

## 10. What Phase 6 Is NOT

```
Phase 6 IS:                         Phase 6 IS NOT:
┌──────────────────────────┐       ┌──────────────────────────┐
│ Editor workflow sync     │       │ Animation system         │
│ • Rename replication     │       │ • No bone/pose sync      │
│ • Visibility sync        │       │ • No NLA track mapping   │
│ • Collection sync        │       │ • No sequencer timeline  │
│ • Hierarchy sync         │       │                          │
│ • Object lifecycle sync  │       ├──────────────────────────┤
│ • Duplicate handling     │       │ Performance phase        │
│                          │       │ • No compression         │
├──────────────────────────┤       │ • No delta serialization │
│ Blender ↔ UE Editor      │       │ • No interest management │
│ (exactly one peer each)  │       │                          │
│                          │       ├──────────────────────────┤
├──────────────────────────┤       │ Multiplayer phase        │
│ Extends Phase 5 runtime  │       │ • No dedicated server    │
│ without modifying it     │       │ • No game-mode sync      │
│                          │       │ • No client authority    │
├──────────────────────────┤       │                          │
│ Last-writer-wins only    │       ├──────────────────────────┤
│ (no merge, no conflict   │       │ Cloud collaboration      │
│  resolution, no undo     │       │ • No multi-user          │
│  coordination beyond     │       │ • No locking/check-in    │
│  basic suppression)      │       │ • No cloud storage       │
│                          │       │                          │
└──────────────────────────┘       ├──────────────────────────┤
                                   │ Cinematic sync phase     │
                                   │ • No camera anim         │
                                   │ • No sequencer events    │
                                   │ • No take system         │
                                   └──────────────────────────┘
```

---

## 11. Revision History

| Date | Version | Author | Changes |
|------|---------|--------|---------|
| 2026-05-25 | 1.0 | System | Initial scope lock — created before Phase 6 implementation begins |
| 2026-05-26 | 1.1 | System | Terminology consolidation: updated status from NOT STARTED to ACTIVE (Rename STABILIZED · Visibility STABILIZED · Hierarchy IN PROGRESS). Fixed PT_COLLECTION proposed value from 0x0D to 0x0E (0x0D now assigned to PT_HIERARCHY). |
