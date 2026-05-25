# Phase 6 Design Constraints — Live Editing System

> Pre-implementation constraint documentation for Phase 6.
> Phase 6 has NOT started. These are unresolved design questions
> that must be answered before implementation begins.

---

## 1. Authority Model: Blender Authoritative vs UE Authoritative

### Current State (Phase 5)
- Blender is authoritative for **transforms** — UE never sends transforms back to Blender
- UE is authoritative for **asset resolution** — UE decides when a mesh/skeletal mesh is resolved

### Unresolved Questions for Phase 6

| Question | Options | Notes |
|----------|---------|-------|
| Who owns **rename**? | Blender → UE only, or bidirectional? | Unreal Editor can rename actors too; conflict resolution needed |
| Who owns **visibility**? | Blender controls viewport visibility? Or UE controls editor visibility? | Hidden in Blender ≠ hidden in UE viewport |
| Who owns **collections**? | Blender collections → UE folders? Or UE folders exist independently? | No 1:1 mapping between Blender collections and UE World Outliner folders |
| Who owns **delete**? | UE deletion should sync back to Blender? | Currently Blender-initiated delete only (PT_DELETE) |
| Who owns **duplicate**? | UE duplicate should create new GUID? Or duplicate re-syncs from Blender? | Currently only Blender-side duplicate produces unique GUID |

### Recommendation
- Transform, delete, create: **Blender authoritative** (preserves existing model)
- Visibility, rename: **Bidirectional with tie-breaking rules** (Phase 6 design required)
- Collections/folders: **Indeterminate** — requires UX prototyping

---

## 2. Rename Conflict Handling

### Problem Space
- Blender object `ue_guid` is **persistent** but the Blender UI name is independent of the GUID
- Unreal Editor can rename actors independently of Blender
- If Blender renames → UE rename triggers → user renames in UE → next Blender sync overwrites again

### Constraints
- GUID is the **identity key**, not the display name
- Display name sync is separate from identity tracking
- Rename storms (bulk renames of many objects) must be coalesced or throttled

### Open Questions
- Should UE actor labels be synchronized from Blender object names?
- If yes, what happens when the user renames in Unreal Editor?
- Should there be a "lock name" flag on UE actors to prevent Blender overwrite?

---

## 3. Collection/Folder Ownership

### Blender Side
- Collections form a hierarchical grouping system
- Objects can belong to multiple collections
- Collection visibility affects viewport rendering

### UE Side
- World Outliner folders are purely organizational (no visibility effect)
- An actor can belong to only one folder at a time
- No native 1:1 mapping to Blender collections

### Open Questions
- Should Blender primary collection map to UE folder structure?
- What happens with multi-collection objects in Blender?
- Should Collection hide/show in Blender affect UE actor visibility?
- Are UE-only folders (created in World Outliner) preserved across sync?

---

## 4. Visibility State Ownership

### Current Behaviour
- No visibility sync exists in Phase 5
- Blender objects can be hidden in viewport independently of UE actors

### Phase 6 Requirements
- Blender viewport hide = UE actor hide in world (or hide in outliner)?
- UE editor hide = Blender viewport hide?
- Game-mode visibility vs editor visibility (separate concepts in UE)

### Invariant to Preserve
- **Interpolation must never feed back** — visibility sync must not trigger transform mutation
- Transient visibility toggles (Alt+H show hidden, etc.) must not cause permanent sync state changes

---

## 5. Transient Actor Handling

### Sources of Transient Actors
- UE editor actor spawning (via Place Actors panel, not from Blender)
- Construction scripts that spawn temporary actors
- Blueprint editor preview actors
- Sequencer spawnables
- Editor utility actors

### Constraints
- Non-Blender actors must be **tagged** or excluded from sync
- A `UELiveSync_Managed` tag or similar should mark Blender-sourced actors
- Unmanaged actors must never be deleted or modified by the sync system

### Open Questions
- How does the sync system distinguish managed vs unmanaged actors?
- Should a tag be applied on creation (recommended)?
- What happens if a managed actor is duplicated in UE (new actor without tag)?

---

## 6. Undo/Redo Interaction

### UE Undo System
- UE has a transactional undo system (UTransactor)
- Actor creation, deletion, rename, and property changes are undoable

### Risk
- Without explicit handling, sync operations that spawn/delete/modify actors will create undo transactions
- User hitting Ctrl+Z could undo a sync-caused spawn, creating desync with Blender

### Open Questions
- Should sync operations be tagged as non-undoable (`NewTransact = nullptr`)?
- Or should undo of a sync operation trigger a revert-sync back to Blender?
- How does the user recover from accidental undo of sync?

---

## 7. Duplicate Detection Rules

### Current State
- Blender `obj.copy()` inherits the source object's `ue_guid` — caught by `ensure_unique_guid()` in sync.py
- UE-side duplicate (Alt+Drag in viewport) currently spawns a non-managed actor

### Phase 6 Requirements
- If UE duplicate creates a new actor, should it generate a new GUID and sync back to Blender?
- Or should UE duplicate be treated as a transient action that gets overwritten on next sync?

### Open Questions
- Should duplicate detection be **Blender-side only** (current model)?
- If UE-side duplicate is supported, how does the new GUID get back to Blender?
- Should duplicate produce a new identity or a copy of the existing identity?

---

## 8. Editor-Only Actor Filtering

### Classes to Filter
- `AInstalledLODActor`
- `ABrush` (BSP)
- `AVolume` subclasses
- `APlayerStart`, `APlayerCameraManager`, `AHUD`
- Any non-Blender-origin actor

### Current Protection
- Inexact — uses `IsA(AActor::StaticClass())` in RecoverMissingActors
- Phase 6 must add explicit class whitelist (only actor types that Blender can produce)

### Open Questions
- Should the whitelist be configurable via CVar or config file?
- What happens to existing non-whitelisted actors when filtering is enabled?
- Should editor-only actors be hidden from diagnostics view?

---

## 9. GUID Persistence Rules

### Current Model
- GUID stored in Blender `obj["ue_guid"]` custom property
- Generated via `uuid.uuid4().hex` on first sync
- Persists across Blender sessions
- Survives Blender file load/save
- Collision detection via `ensure_unique_guid()` in `sync.py`

### Phase 6 Considerations
- If UE rename creates a new actor identity, should it get a new GUID?
- Should UE store GUID in a metadata tag (FGenericProperty or metadata) for persistence?
- On late-join (new UE session connecting to running Blender), how does UE learn existing GUIDs?

### Invariant
- **GUID must be deterministic across Blender sessions** (same datablock → same GUID, except after datablock rename)
- **GUID must NOT depend on object instance** (same datablock across different blend files → same identity)

---

## 10. Hierarchy Ownership Rules

### Current Model
- Blender-parent → UE-attach (parent-child in World Outliner)
- Parent GUID embedded in transform packet (V3+)
- Deferred parent attachment handles out-of-order parents

### Phase 6 Considerations
- UE re-parenting in World Outliner — should it sync back to Blender?
- Multi-parent Blender objects (armature with multiple bone parents) — not supported yet (Phase 7)
- Hierarchy cycles must be prevented (runtime check in ResolvePendingAttachments)

### Open Questions
- Should UE-side re-parenting be reflected in Blender?
- Or is Blender authoritative for hierarchy (current model)?

---

## 11. Late-Join Synchronization Expectations

### Definition
Late-join: A new UE editor session connects to a Blender instance that has been syncing for some time (potentially hours).

### Current Behaviour
- No full state dump on reconnect
- Incremental sync continues from current state
- Missing actors in UE are not recovered until Blender sends their next transform update
- Creates delay in visual synchronization

### Phase 6 Expectation
- Late-join should trigger a **snapshot sync** (list all GUIDs, states, and assets)
- Snapshot protocol already exists (PT_BEGINSNAPSHOT `0x09`, PT_ENDSNAPSHOT `0x0A`)
- Phase 6 must implement the **Blender side** of snapshot generation on reconnect

### Open Questions
- Should snapshot be automatic on reconnect or user-triggered?
- How large can a snapshot be (hundreds of objects)?
- Should snapshot be throttled (e.g., 30 objects per frame to avoid game-thread spikes)?

---

## 12. Editor Event Provenance

Every editor-side mutation in Phase 6 must carry **provenance metadata** internally.
Provenance identifies the origin of a change, enabling correct suppression of
recursive callbacks and making future debugging tractable.

### Provenance Categories

| Category | Tag | Description |
|----------|-----|-------------|
| LocalUserAction | `LOCAL_USER` | Direct user action in the local editor (rename via World Outliner, click eye icon, drag to re-parent). Triggers replication to the remote peer. |
| ReplicatedRemoteAction | `REMOTE_REPLICATED` | Change received from the remote peer over TCP. Must NOT trigger re-replication back to the remote peer. |
| SuppressedRecursiveAction | `RECURSIVE_GUARD` | Change that is a side-effect of a replicated change, now being sup-pressed to prevent a feedback loop. Never replicates. |
| RecoveryAction | `RECOVERY` | Change during actor recovery (Re-spawn, re-link, re-attach). Never replicates. Must not generate undo transactions. |
| ReconnectReplayAction | `REPLAY` | Change during reconnect replay (snapshot sync replaying state). Never replicates. Must be idempotent. |
| UndoRedoAction | `UNDO_REDO` | Change resulting from Ctrl+Z/Ctrl+Y. Must record the pre-undo state for reconciliation with remote peer. |
| DuplicateAction | `DUPLICATE` | Change during duplicate resolution (GUID remap, new actor spawn). Replicates once (the new GUID creation). Does NOT trigger a second duplicate. |

### Metadata Payload (Conceptual)

```cpp
enum class EChangeOrigin : uint8
{
    LocalUser,
    RemoteReplicated,
    RecursiveGuard,
    Recovery,
    ReconnectReplay,
    UndoRedo,
    Duplicate
};

struct FChangeProvenance
{
    EChangeOrigin   Origin;
    FGuid           ActorGuid;        // GUID of the affected actor
    double          LocalTimestamp;    // Monotonic local timestamp
    uint32          SequenceNumber;    // Per-session monotonically increasing
    uint8           ReplayBatchId;     // Non-zero during reconnect replay only
};
```

### Rules

1. **Every mutation path must set provenance** — No code path should mutate
   editor state without assigning an `EChangeOrigin`. Default/unspecified is
   treated as `LocalUserAction` (most conservative; always replicates).
2. **Provenance must flow through replication** — When a change is serialized
   and sent over TCP, the receiving peer tags its application as
   `REMOTE_REPLICATED`.
3. **REMOTE_REPLICATED must suppress local re-replication** — The handler
   for a replicated change must check provenance; if `REMOTE_REPLICATED`,
   it must NOT generate a return packet.
4. **Recovery/Replay must never replicate** — `RECOVERY`, `REPLAY`, and
   `RECURSIVE_GUARD` changes are purely local and must never be serialized
   for transmission.

---

## 13. Provenance Propagation Rules

### Flow: Blender Rename → UE

```
Blender User         Blender Sync         TCP Wire          UE Network Recv    UE Game Thread
 renames "Cube"      serialize rename     PT_RENAME         parse + enqueue    ProcessRename
     │                     │                  │                   │                 │
     ▼                     ▼                  ▼                   ▼                 ▼
 LOCAL_USER           LOCAL_USER          REMOTE_REPLICATED   REMOTE_REPLICATED  Application
 (origin=LOCAL_USER)  (serialize)         (wire)              (tag on enqueue)   with provenance
                                                                                 REMOTE_REPLICATED
                                                                                       │
                                                                                       ▼
                                                                              UE callback fires
                                                                              (OnActorLabelChanged)
                                                                              → check provenance
                                                                              → REMOTE_REPLICATED
                                                                              → SUPPRESS re-replication
```

### Flow: Reconnect Replay

```
Blender Sync                   UE Network Recv              UE Game Thread
 PT_RENAME × 50 (snapshot)     enqueue × 50                 ProcessBatch
     │                              │                            │
     ▼                              ▼                            ▼
 LOCAL_USER (batch)             REMOTE_REPLICATED            tag each as REPLAY
                                                             (batch non-zero)
                                                                  │
                                                                  ▼
                                                         Apply rename
                                                         → provenance = REPLAY
                                                         → do NOT replicate
                                                         → do NOT generate undo
```

### Flow: Duplicate Action

```
UE User                        UE Game Thread                 Blender
 Alt+Drag actor                Detect duplicate               PT_DUPLICATE? No —
     │                              │                         PT_CREATE with
     ▼                              ▼                         new GUID
 LOCAL_USER                     Generate new GUID                  │
                                Spawn actor (tag=DUPLICATE)       ▼
                                → this spawn is local-only    REMOTE_REPLICATED
                                → send PT_CREATE to Blender   → spawn actor
                                → origin for Blender's        → tag = REMOTE_REPLICATED
                                  receive is REMOTE_REPLICATED
```

### Key Rules

| Rule | Applies To | Rationale |
|------|-----------|-----------|
| REMOTE_REPLICATED never re-replicates | All packet types | Prevents infinite loop |
| REPLAY never re-replicates | Reconnect snapshot | Replay is a read of current state, not a new mutation |
| RECOVERY never re-replicates | Re-spawn, re-attach | Recovery restores existing state |
| RECURSIVE_GUARD never re-replicates | Side-effect suppression | Guard breaks the loop |
| DUPLICATE replicates exactly once (the CREATE) | Duplicate detection | The new GUID and spawn are new state; the duplicate action itself is not re-replicated |
| UNDO_REDO replicates the *inverse* mutation | Delete→Create, Rename→Rename-back | Undo must send the reversal to the remote peer |

### Implementation Guidance

- Provenance should be stored as a **thread-local or scoped variable** on the
  game thread during mutation application, NOT added to the wire protocol
  (wire protocol overhead is unacceptable for a debug-only field).
- A `FScopedChangeOrigin` RAII helper sets the current-thread provenance,
  restores on scope exit.
- Mutation callbacks (e.g., `OnActorLabelChanged`) check the current thread's
  provenance before deciding whether to replicate.

---

## 14. Replication Suppression Tokens (Design Note)

> These are **future implementation concepts**, not yet implemented.
> This section documents the intended design space so that ad-hoc recursion
> suppression hacks are not invented later.

### Concept: FScopedReplicationSuppression

```cpp
class FScopedReplicationSuppression
{
public:
    FScopedReplicationSuppression()
    {
        bReplicationEnabled = false;
    }
    ~FScopedReplicationSuppression()
    {
        bReplicationEnabled = true;
    }
};

// Usage:
void OnRenameReceived(const FGuid& Guid, const FString& NewName)
{
    FScopedReplicationSuppression Suppress;
    AActor* Actor = FindActorByGuid(Guid);
    if (Actor)
    {
        Actor->SetActorLabel(NewName);
        // OnActorLabelChanged fires here, checks suppression → no re-replicate
    }
}
```

### Concept: SuppressedGUIDSet

A `TSet<FGuid>` of GUIDs currently being processed by a replicated operation.
If a second mutation for the same GUID arrives while it is in the set, the
second is dropped (or queued for next Tick).

```cpp
static TSet<FGuid> GPendingReplicatedGuids;

void ProcessRename(const FGuid& Guid, ...)
{
    if (GPendingReplicatedGuids.Contains(Guid))
        return; // already processing this GUID this frame

    GPendingReplicatedGuids.Add(Guid);
    // ... apply rename ...
    GPendingReplicatedGuids.Remove(Guid);
}
```

### Concept: ReentrantReplicationGuard

A depth counter that prevents recursion beyond N levels.

```cpp
static int32 GReplicationDepth = 0;
static constexpr int32 MaxReplicationDepth = 3;

struct FReentrantGuard
{
    FReentrantGuard() { ++GReplicationDepth; }
    ~FReentrantGuard() { --GReplicationDepth; }
    bool IsSuppressed() const { return GReplicationDepth >= MaxReplicationDepth; }
};
```

### Evaluation

| Approach | Pros | Cons | Recommendation |
|----------|------|------|---------------|
| `FScopedReplicationSuppression` | Simple, RAII, scoped | Coarse — suppresses ALL replication, not per-GUID | Use for packet-handler scopes |
| `SuppressedGUIDSet` | Per-GUID granularity | Set management overhead; race on multi-thread | Use for per-GUID dedup |
| `ReentrantReplicationGuard` | Catches deep call stacks | Brittle depth threshold | Use as safety net only |

---

## 15. Editor Event Ordering Risks

Editor events do not arrive in a clean sequential order. The following
dangerous orderings must be explicitly handled.

| Ordering | Scenario | Invariant Risk |
|----------|----------|---------------|
| **Rename during delete** | Actor receives PT_RENAME and PT_DELETE in the same Tick batch. | If processed in order (rename then delete), the rename applies to a now-stale state. If delete then rename, the rename may re-create the actor. |
| **Visibility during reconnect replay** | Reconnect replay sends PT_VISIBILITY for an actor that hasn't been spawned yet (spawn replay still queued). | Visibility update targets a non-existent actor → must be queued until spawn completes. |
| **Duplicate before asset resolution** | PT_DUPLICATE arrives for a GUID whose asset hasn't resolved yet. | New actor inherits unresolved fallback primitive. Must be added to PendingAssetQueue. |
| **Collection move during hierarchy rebuild** | PT_COLLECTION re-assigns an actor to a new folder while PT_PARENT re-parents it in the same batch. | Race between folder assignment and parent-child attachment. Must process parent first, then folder. |
| **Undo after reconnect** | User undoes a rename that was part of the reconnect replay. | The replay rename was applied as REPLAY provenance → no undo record created. The user's undo targets a previous local state that may conflict with replay state. |
| **Rapid rename storms** | 500 rename events for the same actor in < 100ms (e.g., scripted rename). | Dropped intermediate renames may cause the final name to be incorrect if the storm coalesces improperly. |
| **Delete + recreate same GUID** | Blender deletes an object, user re-creates it with the same name (Blender may reuse the GUID within a short window). | UE must distinguish "resurrected GUID" from "stale tombstone." Tombstone TTL must be long enough to prevent accidental resurrection but short enough to allow legitimate re-use. |
| **Delete during snapshot replay** | Snapshot replay sends PT_CREATE for GUID X, but the user had already deleted X in UE during disconnection. | Replay must check tombstone set before creating. If GUID is tombstoned, skip creation. |

### Mitigation Strategy

| Risk | Mitigation |
|------|-----------|
| Rename during delete | Stage mutations per-GUID within a Tick batch; execute in dependency order (delete → skip all following mutations for same GUID) |
| Visibility during replay | Queue visibility mutations per-GUID until spawn confirmed; or batch all replay mutations into a single snapshot-apply pass |
| Duplicate before asset resolution | Route all new actors through the existing ResolvePendingAssets pipeline; no special handling needed |
| Collection during hierarchy rebuild | Order Tick pipeline: hierarchy → folder assignments. Document ordering in pipeline stage comments |
| Undo after reconnect | Tag reconnect replay mutations as "no-undo" (bypass UTransactor entirely). Replayed state is baseline, not a transaction |
| Rapid rename storms | Coalescing timer (50ms window); final name per-GUID is the last event in the window |
| Delete + recreate same GUID | Tombstone TTL = 60 seconds (not 30). Require explicit GUID release from tombstone set |
| Delete during snapshot replay | Tombstone check before spawn. If GUID is in tombstone set, skip this actor |

---

## 16. Phase 6 Failure Modes

Catalog of expected failure classes for debugging vocabulary.

| Failure Mode | Description | Detection | Recovery |
|-------------|-------------|-----------|----------|
| **Recursive rename loop** | Rename bounces B→UE→B→UE indefinitely. | Provenance check: alternating LOCAL_USER/REMOTE_REPLICATED for the same GUID. | ReentrantGuard fires at depth 3 → suppress. Log warning. Manual UE.LiveSync.Reset may be needed. |
| **Stale GUID resurrection** | GUID from a deleted actor is treated as alive by one peer after the other has deleted it. | Tombstone set mismatch between peers. | Reconnect full-snapshot resync. The authoritative GUID set comes from Blender's current scene. |
| **Orphan hierarchy** | Actor A is parented to actor B, but B is deleted (or never spawned). | PendingAttachmentQueue growing unboundedly. | Periodic orphan sweep: actors whose parent GUID has no mapping after 300 frames → attach to root. |
| **Zombie actor replication** | Actor is deleted in Blender but re-spawns in UE via Recover-MissingActors because UE still has stale state. | Actor count desync: Blender count < UE count. | Tombstone set prevents re-spawn for 60s. After TTL, re-spawn is allowed (Blender may have legitimately re-created). |
| **Duplicate GUID ownership** | Two actors claim the same GUID (Blender-side `obj.copy()` collision). | `ensure_unique_guid()` in sync.py catches it on Blender side before packet is sent. | New GUID assigned to the duplicate. UE receives two distinct GUIDs. |
| **Reconnect replay duplication** | Snapshot replay on reconnect re-creates actors that already exist in UE. | Pre-spawn check: does actor with this GUID already exist? | Skip spawn for existing GUIDs. Replay only creates missing actors. |
| **Visibility desync** | One peer thinks actor is visible, the other thinks it is hidden. | Periodic visibility state assertion (every 300 Tick frames). | Re-send PT_VISIBILITY for any actor whose local/remote state diverges. |
| **Collection divergence** | Blender collection membership differs from UE folder membership. | Periodic collection state assertion. | Re-send full collection state for diverging collections. |
| **Transaction replay divergence** | Undo in UE reverts a synced state, causing divergence from Blender. | Undo triggers a reconciliation check: compare actor state with last-known Blender state for affected GUIDs. | If divergence detected, send current state to Blender (UE authoritative for undo recovery), then re-sync from Blender. |

---

## 17. Lifecycle-State Diagram

Conceptual editor object lifecycle states for Phase 6.

```
                    ┌──────────────┐
                    │   Unknown    │  GUID not yet seen by this peer
                    └──────┬───────┘
                           │ PT_CREATE received
                           ▼
                    ┌──────────────┐
                    │ SpawnPending │  Actor creation queued (awaiting game thread Tick)
                    └──────┬───────┘
                           │ Actor spawned
                           ▼
                    ┌──────────────┐
             ┌──────│   Active     │────────────────────┐
             │      └──────┬───────┘                    │
             │             │                            │
             │   PT_RENAME │                    PT_DELETE received
             │    received │                            │
             ▼             ▼                            ▼
     ┌────────────┐ ┌──────────────┐           ┌──────────────┐
     │  Renaming  │ │   Active     │           │  Tombstoned  │  60s TTL
     └──────┬─────┘ │ (renamed)    │           └──────┬───────┘
            │       └──────────────┘                  │ TTL expires
            └──────────────────>                       │ or GUID re-created
                                                       ▼
                                                ┌──────────────┐
                                                │  Unknown     │ (or SpawnPending if re-created)
                                                └──────────────┘

     ┌──────────────────────────────────────────────────────────┐
     │                  Reconnect Path                          │
     │                                                          │
     │  Active ──(disconnect)──► Reconnecting ──(replay)──► Active │
     │                           │                               │
     │                           │ GUID not in snapshot          │
     │                           ▼                               │
     │                       Orphaned ──(60s)──► Tombstoned      │
     │                           │                               │
     │                           │ GUID re-appears               │
     │                           ▼                               │
     │                       Recovered ───► Active                │
     └──────────────────────────────────────────────────────────┘
```

### State Descriptions

| State | Meaning | Allowed Transitions |
|-------|---------|---------------------|
| **Unknown** | GUID not yet seen. No actor exists. | → SpawnPending (on PT_CREATE) |
| **SpawnPending** | PT_CREATE received but actor not yet spawned (game-thread queue). | → Active (on successful spawn) |
| **Active** | Actor exists, GUID tracked, sync active. | → Renaming, Tombstoned, Reconnecting, Duplicate |
| **Renaming** | Rename in progress (coalesce window open). | → Active (after coalesce timer) |
| **Deleting** | PT_DELETE received, actor destroy queued. | → Tombstoned (after destroy confirmed) |
| **Tombstoned** | Actor deleted. GUID is in tombstone set for 60s. | → Unknown (TTL expire), SpawnPending (re-create) |
| **Reconnecting** | Connection lost, awaiting replay snapshot. | → Active (after replay), Orphaned (GUID not in replay) |
| **Orphaned** | GUID existed before disconnect but not in reconnect snapshot. | → Tombstoned (60s), Recovered (GUID re-appears) |
| **Recovered** | Orphaned actor re-linked to Blender state. | → Active |

---

## 18. Reconnect Replay Design Notes

### Replay Ordering Guarantees

1. **All CREATE events before RENAME before VISIBILITY before COLLECTION**
   — Within a snapshot batch, the Blender side orders packets to ensure
   dependent mutations appear after their prerequisites.
2. **If out-of-order packets arrive** (e.g., PT_VISIBILITY before
   PT_CREATE), the UE side must queue the dependent mutation until
   the prerequisite is satisfied.
3. **Snapshot replay must be atomic per-GUID** — Either all mutations
   for a GUID in the replay are applied, or none are. Partial application
   is not idempotent.

### Stale-Object Pruning

1. After snapshot replay completes, any actor whose GUID was not in the
   snapshot is moved to the **Orphaned** state.
2. Orphaned actors receive a 60-second grace period during which they can
   be **Recovered** (if Blender sends a PT_CREATE for the GUID).
3. After 60 seconds, Orphaned → Tombstoned, and the actor is destroyed.

### Tombstone Handling

1. Tombstoned GUIDs are kept in a `TSet<FGuid>` (bounded at 1024 entries).
2. If a PT_CREATE arrives for a Tombstoned GUID within the 60-second TTL:
   - The tombstone is removed
   - The actor is re-spawned (this is a legitimate re-creation)
3. After TTL expires, the GUID is removed from the tombstone set.
   A subsequent PT_CREATE is treated as a fresh spawn.

### Duplicate Suppression

1. During replay, the UE side maintains a `TSet<FGuid>` of GUIDs already
   processed in this replay batch.
2. If a second mutation for the same GUID arrives within the same batch:
   - If it is the same mutation type (e.g., second PT_RENAME), the later
     one wins (last-writer-wins within the batch).
   - If it is a different type (e.g., PT_RENAME then PT_DELETE), the
     delete wins (deletion is terminal within a batch).

### Replay Idempotency

1. **CREATE is idempotent** — If the actor already exists (matched by GUID),
   skip the spawn. Update transform and metadata instead.
2. **RENAME is idempotent** — If the actor label already matches the
   replayed name, skip the rename call.
3. **VISIBILITY is idempotent** — If the visibility state already matches,
   skip the setter.
4. **DELETE is idempotent** — If the actor is already Tombstoned, skip.
5. **COLLECTION is idempotent** — If the folder already matches, skip.
6. **Snapshot END marker** — The final packet in a replay batch
   (`PT_ENDSNAPSHOT` = `0x0A`) triggers the stale-object pruning pass.

---

## 19. Observability Requirements for Phase 6

Every new Phase 6 editor workflow system must include observability hooks
before being considered complete. No feature ships without diagnostics.

### Required Per-System Instrumentation

| System | Trace Scope | BEGIN/END | Provenance Log | Diagnostics Counter |
|--------|-------------|-----------|----------------|---------------------|
| Rename replication | `UELiveSync_Rename` | BEGIN/END around rename callback | Log origin (LOCAL_USER / REMOTE_REPLICATED / RECOVERY / REPLAY / UNDO_REDO) | `RenamesPerSecond`, `RenameCollisions`, `RenameSuppressions` |
| Visibility sync | `UELiveSync_Visibility` | BEGIN/END around visibility setter | Log origin + new visibility state | `VisibilityChangesPerSecond`, `VisibilitySuppressions` |
| Collection sync | `UELiveSync_Collection` | BEGIN/END around folder create/move | Log origin + collection name + object count | `CollectionsSynced`, `CollectionCollisions` |
| Hierarchy sync | `UELiveSync_Hierarchy` | BEGIN/END around parent change | Log origin + parent-GUID mapping | `ReparentsPerSecond`, `OrphanCount`, `PendingAttachments` |
| Delete replication | `UELiveSync_Delete` | BEGIN/END around actor destroy | Log origin + GUID | `DeletesPerSecond`, `TombstoneCount` |
| Duplicate detection | `UELiveSync_Duplicate` | BEGIN/END around duplicate resolution | Log source GUID → new GUID mapping | `DuplicatesDetected`, `DuplicateCollisions` |
| Reconnect replay | `UELiveSync_Replay` | BEGIN/END around full replay batch | Log batch size + GUIDs processed + skipped (tombstone) + created | `ReplayBatchSize`, `ReplaySkipped`, `ReplayCreated`, `ReplayOrphans` |
| Undo/redo interaction | `UELiveSync_UndoRedo` | BEGIN/END around undo reconciliation | Log pre/post state for affected GUIDs | `UndoEvents`, `UndoReconciliations` |
| Flood detection | `UELiveSync_FloodGuard` | BEGIN/END around flood window | Log current rate + threshold | `FloodTriggeredCount`, `PeakEventRate` |

### Observability Patterns

1. **Always use TRACE_CPUPROFILER_EVENT_SCOPE** for CPU profiling scopes.
2. **Always pair UE_LOG(LogLiveSync, Log, TEXT("BEGIN/END ..."))** markers
   at the entry/exit of each observable operation.
3. **Always log provenance** when processing a mutation. Format:
   `UE_LOG(LogLiveSync, Verbose, TEXT("[%s] %s: GUID=%s, Origin=%s"), ...)`
4. **Always update a counter** via `FLiveSyncStats` using
   `std::memory_order_relaxed`. Counters are O(1) display values only.
5. **Flood detection must log before suppressing** — When flood detection
   triggers, log a warning with the current event rate and threshold
   before dropping packets.

### Diagnostics Panel Additions

The existing diagnostics panel (`SLiveSyncDiagnosticsWidget`) should display:

- Current provenance of the last processed mutation (per-type)
- Reconnect replay status (batch progress, remaining, skipped)
- Tombstone set size
- Orphan count
- Rename coalescing window status (active count, timer remaining)
- Duplicate resolution status (pending resolutions)

---

## 20. Editor Safety Rules

Explicit rules for Phase 6 implementation. Breaking any of these is
a design error.

### Thread Safety

| Rule | Violation Risk |
|------|---------------|
| **Never mutate UObject state from the network thread.** ALL actor mutations (rename, visibility, parent, folder, spawn, destroy) must run on the game thread Tick. | Cross-thread UObject access → crash (UE assertion or use-after-free). |
| **Never read UObject state from the network thread.** Socket recv is on the network thread. GUID lookup, actor iteration, and world queries are game-thread-only. | Same as above. |

### Callback Suppression

| Rule | Violation Risk |
|------|---------------|
| **Never trigger editor callbacks without suppression.** Every mutation that fires an editor callback (`OnActorLabelChanged`, `OnActorVisibilityChanged`, etc.) must be wrapped in `FScopedReplicationSuppression` or equivalent. | Recursive feedback loop — rename ping-pong between peers. |
| **Never suppress without provenance.** Suppression alone is not enough — provenance must identify WHAT is being suppressed and WHY. | Cannot debug suppression logic; cannot distinguish RECOVERY from REPLAY. |

### Identity Rules

| Rule | Violation Risk |
|------|---------------|
| **Never trust actor names as stable identity.** Names change. GUIDs do not. All actor lookups must be by GUID, not by name. | Rename breaks actor tracking; multiple actors with same name cause ambiguity. |
| **GUID remains authoritative identity.** No Phase 6 feature may introduce a secondary identity system. GUID is the single source of truth for object identity. | Identity fragmentation; actor tracking desync; duplicate resolution failure. |
| **Never generate GUIDs on the UE side** except during explicit UE-initiated duplicate (Alt+Drag). GUID generation is a Blender authority operation. | GUID collision; Blender cannot reconcile UE-generated GUIDs. |

### Mutation Rules

| Rule | Violation Risk |
|------|---------------|
| **Never process editor mutations without provenance.** Every mutation code path must set provenance before applying state. | Cannot distinguish user actions from replicated/recovery actions; feedback loops undetectable. |
| **Reconnect replay must be idempotent.** Applying the same replay twice must produce the same final state. | Duplicate actors, duplicate renames, duplicate folder creations on reconnect. |
| **Never replicate during PIE.** `GEditor->PlayWorld != nullptr` → suppress all Editor→Blender replication. | Editor state bleeds into game simulation; PIE actors are transient. |
| **Never replicate during undo/redo transactions.** `GUndo != nullptr` → suppress replication. Apply reconciliation after undo completes. | Undo creates divergence; redo of a sync operation causes desync. |

### Reconnect Rules

| Rule | Violation Risk |
|------|---------------|
| **Never replay without tombstone filtering.** Check tombstone set before every PT_CREATE during replay. | Zombie actors re-spawn after user deleted them during disconnection. |
| **Replay must not overwrite user changes made during disconnection** for in-scope editor workflows (rename, visibility, hierarchy). | User renames 50 actors during disconnection → replay silently overwrites them. Use timestamp comparison or last-writer-wins. |

---

## 21. Deferred Complexity Registry

Intentionally deferred future complexities. Do NOT implement partially.

| Complexity | Description | Deferred To | Risk of Partial Implementation |
|-----------|-------------|-------------|-------------------------------|
| **Bidirectional authority** (full) | Both Blender and UE can fully author all object state with conflict resolution. | Phase 9 | Partial implementation would create asymmetric authority where some features are bidirectional and others are not, leading to confusing user experience |
| **Collaborative editing** | Multiple editors syncing to the same Blender session. | Phase 9 | Requires server authority model, locking, conflict resolution UI — none of which exist |
| **Distributed ownership** | Different objects owned by different peers. | Phase 9 | Requires ownership metadata in wire protocol, ACL system |
| **Transaction merging** | Automatic merge of concurrent edits from different peers. | Phase 9 | Requires three-way merge, diff algorithm, conflict UI |
| **Semantic conflict resolution** | Merge based on edit semantics (not last-writer-wins). | Phase 9 | Requires edit history, semantic analysis, domain-specific merge rules |
| **Editor history synchronization** | Sync of undo/redo stacks between peers. | Phase 9 | Requires serialization of undo transactions |
| **Multi-user arbitration** | Voting/priority system for resolving conflicting edits. | Phase 9 | Requires server, identity system, priority model |
| **Bidirectional transform** | UE sends transforms back to Blender. | Explicitly NOT planned | Breaks the Phase 5 interpolation invariant; would require fundamental architecture change |
| **Runtime packaged-game sync** | Shipping game connects to Blender. | Phase 8 | Requires different transport layer, security, runtime actor lifecycle |

### Guardrail

If a Phase 6 implementation discussion mentions any of the above complexities
as "we could also add..." or "it would be easy to extend this to...", the
discussion must be redirected to this registry. Partial implementations of
deferred complexities will be rejected during code review.

---

## Appendix: Decision Matrix for Phase 6 Start

Before Phase 6 implementation begins, the following must be decided:

| Decision | Options | Deadline |
|----------|---------|----------|
| Authority model for rename | Blender-only / Bidirectional | Before first rename feature |
| Authority model for visibility | Blender-only / Bidirectional / Separate | Before first visibility feature |
| Collection → Folder mapping | Primary collection only / Multi-collection / None | Before collection sync |
| Managed actor tag scheme | Name prefix / FTag / Metadata | Before spawn refactor |
| Undo interaction | Suppress / Revert-sync / Ignore | Before first undoable operation |
| Late-join snapshot | Automatic / Manual / Both | Before reconnect refactor |
| Duplicate detection scope | Blender-only / Bidirectional | Before first UE-duplicate feature |
| Editor actor whitelist | Config file / CVars / Hardcoded | Before Phase 6 production use |
