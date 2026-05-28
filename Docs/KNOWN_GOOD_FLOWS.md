# Known Good Flows — Canonical Execution Paths

Each flow documents the **correct** working path. If behavior diverges, compare against this.

---

## A — Connection Bootstrap

**Trigger**: Start UE listener → Blender enable sync
**Blender**: `network.py.connect()`, background daemon thread `_sender_loop()`, timer `_sync_timer` in `sync.py`
**UE**: `LiveSyncRunnable.cpp` `StartNetworkThread()`, `BuildActorCache()`, heartbeat timeout 15s
**Expected diagnostics**:
```
[UE] UELiveSyncSubsystem: BeginPlay → Building ActorCache (N actors)
[UE] LiveSyncRunnable: Waiting for connection on port 57000
[BLENDER] [NETWORK] Connected to 127.0.0.1:57000
[UE] LiveSyncRunnable: Client connected → starting receive loop
[BLENDER] [SYNC] Sync started (heartbeat every 5s)
```
**Failure signature**: `[UE] Heartbeat timeout (15s) — no packet received` — port mismatch or Blender not sending
**Replay implication**: StopNetworkThread clears GWorldReplayBuffer; sequence trackers reset; GRenamePersistentLabel survives

---

## B — Create Object Flow

**Trigger**: New Blender object detected in `scan_scene()`
**Blender entry**: `sync.py:698` → `ensure_guid()` → `ensure_unique_guid()` → `serialize_create()` (network.py)
**Packet**: `PT_Create` (0x03), 81-byte V5 payload (GUID + transform + parent GUID)
**UE entry**: `UELiveSyncSubsystem.cpp:5400` `HandleCreateObject()`
**Registries touched**: ActorCache.Add, TransformStates.Add, tracked_objects (Blender)
**Expected diagnostics**:
```
[CREATE][DIAG] ENTRY world=... type=... ActorCache=N
[CREATE][DIAG] SPAWN guid=XXXX class=...
[CREATE][DIAG] REGISTRY guid=XXXX ActorCache check=PRESENT
```
**Replay implication**: Recorded as EWorldReplayDomain::Lifecycle. RestoreWorldState re-spawns on replay.
**Success criteria**: Actor visible in UE, correct transform, correct mesh type

---

## C — Transform Update Flow

**Trigger**: Blender object moved, rotated, or scaled
**Blender entry**: `sync.py` diff against `_last_sent_transforms` → `serialize_transform()` (network.py)
**Packet**: `PT_Transform` (0x01), 81 bytes V5
**UE entry**: `UELiveSyncSubsystem.cpp:2600` `HandleTransformPacket()` → FSyncTransformState update → `InterpolateTransforms()` at ~line 4029
**Registries touched**: TransformStates (FSyncTransformState per GUID)
**Expected diagnostics**: `[TRANSFORM] guid=XXXX loc=(x,y,z) rot=(...) scl=(...)`
**Replay implication**: Recorded as EWorldReplayDomain::Transform. Replayed in original order during restore.
**Success criteria**: Actor moves smoothly to correct position, no jitter or pop
**Failure signature**: Actor at origin (transform lost) or wrong position (parent-relative vs world mixup)

---

## D — Rename Flow

**Trigger**: Blender object renamed (F2 or Properties panel)
**Blender entry**: `sync.py` name diff → `serialize_rename()` (network.py)
**Packet**: `PT_Rename` (0x0C), variable-length (GUID + oldName + newName + seq + ts)
**UE entry**: `UELiveSyncSubsystem.cpp:5992` `HandleRename()` → `SetActorLabel()` + `GRenamePersistentLabel.Add(Guid, NewName)`
**Registries touched**: GRenamePersistentLabel
**Expected diagnostics**:
```
[RENAME][DIAG] APPLY guid=XXXX name_before="Cube" name_after="MyBox"
[RENAME][DIAG] GRenamePersistentLabel: guid=XXXX label="MyBox"
```
**Replay implication**: Recorded as EWorldReplayDomain::Rename. `RestoreWorldState` applies with suppression scope. `HandleCreateObject` restores from persistent label on spawn.
**Success criteria**: Label changes immediately, survives reconnect/rebuild
**Failure signature**: Label reverts to default after new object creation or reconnect

---

## E — Hierarchy Parenting Flow

**Trigger**: Blender parent assignment (Ctrl+P) or parent change
**Blender entry**: `sync.py:1202-1220` parent diff in main loop → `serialize_hierarchy()` (network.py)
**Packet**: `PT_Hierarchy` (0x0D), 44 bytes (child GUID + parent GUID + seq + ts)
**UE entry**: `UELiveSyncSubsystem.cpp:6200` `HandleHierarchy()` → `AttachToActor()` with KeepRelative transforms (line ~6250)
**Registries touched**: _last_parent_guid (Blender), hierarchy sequence trackers, GReplayHierarchyAttachments (UE deferred queue)
**Expected diagnostics**:
```
[HIERARCHY] ENTRY child=XXXX parent=YYYY
[HIERARCHY] APPLY AttachToActor child=XXXX parent=YYYY
```
**Replay implication**: Recorded as EWorldReplayDomain::Lifecycle (Create) + hierarchy attachment during replay. Deferred queue retries 10 fast + 10 slow, 60-frame hard timeout.
**Success criteria**: Child follows parent transforms; detach (zero parent GUID) works; nesting resolves
**Failure signature**: Child stays at world position after parent move (see Problem B) or child spawns detached

---

## F — Collection Sync Flow

**Trigger**: Blender collection membership change (M or drag)
**Blender entry**: `sync.py` collection diff → `serialize_collection()` (network.py)
**Packet**: `PT_Collection` (0x0F), 30/46 bytes
**UE entry**: `UELiveSyncSubsystem.cpp` `HandleCollection()` → `ApplyCollectionMembership()`
**Registries touched**: GCollectionMembership, GCollectionIdentities, collection sequence tracker
**Replay implication**: Recorded as EWorldReplayDomain::Collection. Append-only ring buffer (2048 entries). Rebuild has deterministic sorted-GUID ordering.
**Success criteria**: Object appears in correct UE collection folder after assignment

---

## G — Replay Rebuild Flow

**Trigger**: Console command `UE.LiveSync.VerifyWorldReplay` or `RestoreWorldState()`
**UE entry**: `UELiveSyncSubsystem.cpp:8100` `RestoreWorldState()`
**Steps**:
1. `SaveWorldState()` — temp snapshot of current state (all domains)
2. Clear + replay all GWorldReplayBuffer entries in order
3. `ComputeWorldStateHash()` — FNV-1a across ActorCache + GRenamePersistentLabel + collection registries
4. Compare against expected hash from SaveWorldState
5. If mismatch → restore temp state → increment CollectionReplayRollbacks
**Expected diagnostics**:
```
[REPLAY] BEGIN restore
[REPLAY] Replaying N entries
[REPLAY] Hash match: expected=XXXX actual=XXXX
[REPLAY] OK — state verified (or ROLLBACK if mismatch)
```
**Failure signature**: `[REPLAY] HASH MISMATCH expected=XXXX actual=YYYY` → state divergence detected

---

## H — Snapshot Rebuild Flow

**Trigger**: Console command `UE.LiveSync.ExportWorldSnapshot` or `UE.LiveSync.RebuildWorldFromSnapshot`
**UE entry**: `UELiveSyncSubsystem.cpp:8700` `ExportWorldSnapshot()` / `RebuildWorldFromSnapshot()` at ~line 8400
**Steps**:
1. Export: serialize ActorCache + GRenamePersistentLabel + collection membership
2. Clear: ActorCache, TransformStates, GWorldReplayBuffer, sequence trackers (except GRenamePersistentLabel)
3. Rebuild: spawn actors from export → apply rename labels → apply collections → BuildActorCache
**Expected diagnostics**:
```
[SNAPSHOT] EXPORT: N actors, M rename entries, K collection entries
[REBUILD] SPAWN guid=XXXX label="..."
[REBUILD] ActorCache size=N after rebuild
```
**Success criteria**: World state identical before export and after rebuild (all GUIDs, labels, hierarchy, collections preserved)
**Failure signature**: Missing labels, wrong positions, lost hierarchy, collection membership wrong

---

## I — Delete Lifecycle Flow

**Trigger**: Blender object deletion (X key)
**Blender entry**: `sync.py:980` `detect_deleted_objects()` → `serialize_delete()` (network.py)
**Packet**: `PT_Delete_V5` (0x0E), 28 bytes fixed
**UE entry**: `UELiveSyncSubsystem.cpp:6900` `HandleDelete_V5()` — three-barrier stale check (seq + tombstone + ActorCache)
**Registries touched**: Tombstone map, _delete_sequences (Blender), ActorCache.Remove
**Expected diagnostics**:
```
[DELETE] V5 guid=XXXX seq=N (via PT_Delete_V5)
[DELETE] DESTROY guid=XXXX
```
**Replay implication**: Recorded as EWorldReplayDomain::Lifecycle. Tombstone gates re-delete during replay. ActorCache.Remove + child detach cascade. Deferred snapshot queue (bounded 2048 FIFO).
**Success criteria**: Actor removed, no crash, no false delete on replay, child actors detached
**Failure signature**: Actor lingering (delete not processed) or false delete of wrong actor (GUID collision)
