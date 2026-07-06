# SyncId Provenance and Ordering Audit

## Selected source
```cpp
const int32 SyncId = Context.Stats->MatPktSyncId;
```
at LiveSyncFBXImporter.cpp line 1105.

## Assignment site
```cpp
int32 FbxSyncId = ++Stats.MatPktSyncId;
```
at UELiveSyncSubsystem.cpp line 5007.

## Call site
```cpp
FLiveSyncFBXImporter::HandleImport(Ptr, ObjSize, Ctx);
```
at UELiveSyncSubsystem.cpp line 5314.

## Enclosing function
`UUELiveSyncSubsystem::ProcessBinaryPacket` (line 2822)
- Game-thread only (`CHECK_GAME_THREAD()` at line 2827)
- Single-threaded within this context

## Same enclosing function
YES — both assignment (5007) and call (5314) are within ProcessBinaryPacket

## Same branch/transaction
YES — both under `if (PacketType == 0x16)` (line 4984 — FBX Import branch)
The FBX import branch enters a nested block at line 5005 where the assignment occurs.
HandleImport is called within that same block (block closes at line 5315 after call).

## Intervening MatPktSyncId writes
NONE between line 5007 and line 5314.
Only one read access: line 5306 (Stats.MatPktSyncId in a logging lambda, read-only).

## Intervening ++MatPktSyncId on other code paths
Line 4229: `ThisPktSyncId = ++Stats.MatPktSyncId;` — This is inside a different packet type
handler (MATX keyframe path, NOT the FBX path). Not reachable from the FBX branch.

## Thread executing assignment
Game thread (UUELiveSyncSubsystem::ProcessBinaryPacket)

## Thread executing HandleImport
Game thread (same function call chain)

## Race risk
NONE — both operations are on the same thread, same function, same code path.
MatPktSyncId is plain int32, but all accesses are within a single-threaded context.
C++ sequenced-before relationship guarantees the increment is visible to the subsequent read.

## Confidence
RESOLVED — the frozen SyncId snapshot is provably correct and safe.
No atomic, no race, no interleaving.
