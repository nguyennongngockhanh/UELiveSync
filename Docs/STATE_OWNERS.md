# State Ownership — Authority & Replay Matrix

MAX 120 lines. Table-heavy. Prevents replay corruption.

## Rule
Mutable state MUST NOT participate in identity derivation.

## Ownership Matrix

| Domain | Authority | Replication | Replay Owner | Rollback Owner | Snapshot Owner | Hash? | Rebuild Source |
|--------|-----------|-------------|--------------|----------------|----------------|-------|----------------|
| Transform | Blender matrix_local/world | PT_Transform (0x01) | FSyncTransformState | Full state save | FTransform[] | Yes | TransformStates |
| Label | Blender PT_Rename | PT_Rename (0x0C) | GRenamePersistentLabel | GRenamePersistentLabel map | TMap<FGuid,FString> | Yes | GRenamePersistentLabel |
| Hierarchy | Blender obj.parent | PT_Hierarchy (0x0D) | GHierarchySequences | ActorCache + AttachParent | FPendingHierarchyAttachment[] | Yes | ActorCache + AttachToParent |
| Visibility | Blender hide_viewport | PT_Visibility (0x0B) | GVisibilitySequences | ActorCache + bHidden | TMap<FGuid,bool> | No | ActorCache restore |
| Collection | Blender users_collection | PT_Collection (0x0F) | GCollectionReplayBuffer | GCollectionMembership map | TMap<FGuid,TSet<FGuid>> | Yes | Replay rebuild |
| ActorRef | UE ActorCache | N/A (local) | N/A | ActorCache map | TMap<FGuid,AActor*> | No | HandleCreate + Cache build |
| Delete | Blender _known_guids | PT_Delete_V5 (0x0E) | GDeleteTombstoneMap | Tombstone map + sequences | Tombstone set | Yes (tombstone) | Tombstone rebuild |
| ReplayTimeline | UE runtime | N/A | GCollectionReplayTimeline | Cleared on rollback | N/A | No | N/A |
| ReplayBuffer | UE runtime | N/A | GWorldReplayBuffer | N/A (read-only for replay) | Saved per snapshot | No | Cleared on Stop/Reset |
| SnapshotHash | ComputeWorldStateHash | N/A | N/A | Computed on save/restore | uint64 per domain | No | Re-computed on rebuild |
| GUID identity | _compute_owner_hash | obj.data.name only | N/A | N/A | N/A | No | N/A |
| CVar state | UE CVar system | N/A | N/A | N/A | N/A | No | N/A |

## Replay Write Access (Who Can Mutate What)

| Domain | Replay Applies | Diagnostics Reads | ConsoleReset Clears | StopNetworkThread Clears |
|--------|----------------|-------------------|---------------------|--------------------------|
| Transform | RestoreWorldState | DumpState | TransformStates.Empty() | Rebuilt on reconnect |
| Label | RestoreWorldState + RebuildFromSnapshot | DumpState | GRenamePersistentLabel.Empty() | NOT cleared |
| Hierarchy | RestoreWorldState (via re-apply) | DumpState | GHierarchySequences.Clear() | Cleared |
| Visibility | RestoreWorldState (via re-apply) | DumpState | GVisibilitySequences.Clear() | Cleared |
| Collection | RestoreWorldState (via replay buffer) | DumpState | GCollectionMembership.Empty() | Cleared |
| ActorRef | HandleCreateObject in RebuildFromSnapshot | FindActorFast | BuildActorCache scan | NOT cleared |
| Delete | RestoreWorldState (tombstone restore) | DumpState | GDeleteTombstoneMap.Empty() | Cleared |
| ReplayBuffer | N/A | DumpReplayBuffer | GWorldReplayBuffer.Empty() | Cleared |
| Timeline | N/A | DumpReplayTimeline | N/A | N/A |

## Immutable State Derivation

| State | Derived From | Never Includes |
|-------|-------------|----------------|
| GUID | _compute_owner_hash(obj.data.name) | obj.name, location, rotation, scale |
| Owner hash | SHA-256(datablock_name)[:16] | Any mutable property |
| World replay hash | FNV-1a(sorted GUIDs, domains) | Timestamps, sequence numbers |
