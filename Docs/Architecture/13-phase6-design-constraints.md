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
