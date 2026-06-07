# Phase 7C Stage 2C.4 — Runtime Validation: FULL_ATTR v1 Manual Mesh Sync

## Scope
End-to-end validation of Blender→UE mesh sync via the manual operator.
Tests parser → reassembly → ProceduralMesh build pipeline.

## Prerequisites

| Item | Check |
|------|-------|
| UE plugin copy is current at `Unreal/UE5.7.4/Engine/Plugins/UELiveSync/` | `git -C /home/nguyennongngockhanh/Projects/UELiveSync log --oneline -1` matches `git -C "$UEPLUGIN" log --oneline -1` |
| UE build succeeds | UBT: `UnrealEditor Linux Development` 0 errors |
| UE Editor launches | Watch for Level: `LogEditor: Editor initialized.` |
| LiveSync port listening | `ss -tlnp \| grep :51091` (default port) |
| Blender addon enabled | Preferences → Add-ons → "UELiveSync" enabled |

## UE Plugin Source Sync

```bash
# Ensure engine plugin copy matches repo
UEPLUGIN="$HOME/Unreal/UE5.7.4/Engine/Plugins/UELiveSync"
REPO="$HOME/Projects/UELiveSync/UE_Plugin/UELiveSync"

diff -rq "$UEPLUGIN/Source" "$REPO/Source" | head -20
# Expected: no output (identical)

diff -q "$UEPLUGIN/UELiveSync.uplugin" "$REPO/UELiveSync.uplugin"
# Expected: no output
```

If there are differences, re-sync:

```bash
rsync -a --delete "$REPO/" "$UEPLUGIN/"
```

## UE Build

```bash
cd "$HOME/Unreal/UE5.7.4"
./Engine/Build/BatchFiles/Linux/Build.sh UnrealEditor Linux Development \
  -Module=UELiveSync -Module=UELiveSyncEditor
# Expected: 0 errors, exit code 0
```

## Test Sequence

### P1: UE Editor launch with LiveSync listening

1. Launch UE Editor (or use existing session with stable env)
2. Open Output Log (Window → Developer Tools → Output Log)
3. Verify:
   ```
   ss -tlnp | grep :51091
   ```
   Expected: port 51091 is listening (or whatever port is configured)

### P2: Blender connects

1. Launch Blender with UELiveSync addon enabled
2. In Blender's Info editor or system console, confirm connection:
   ```
   [LIVESYNC] Connected to UE at 127.0.0.1:51091
   ```
3. In UE Output Log, confirm:
   ```
   [LIVESYNC] Client connected
   ```

### P3: Sync a simple mesh — positive case

1. In Blender, add a simple mesh (e.g. `Add → Mesh → Cube`, or select existing)
2. In 3D Viewport, press N to open Sidebar → UELiveSync tab → **Sync Selected Mesh to UE**
3. In Blender console/info:
   ```
   [MESH][ATTR] Manual sync: Cube (12 tris, N verts, 1 chunk(s), stride=32)
   Synced 1 mesh object(s) to UE
   ```
4. In UE Output Log, grep for these markers:

   ```bash
   # P3a: v1 parser accepted
   grep "\[MESH\]\[V1\].*GUID" /path/to/ue/log
   # Expected: no log line for acceptance (Parser succeeds silently on success — it only logs on rejection)
   # Instead verify via counter or absence of rejection log

   # P3b: chunk stored
   grep "\[MESH\]\[V1\] Stored chunk" /path/to/ue/log
   # Expected: "[MESH][V1] Stored chunk 0/1 for GUID=xxxxxxxx (received=1/1)"

   # P3c: reassembly complete
   grep "\[MESH\]\[V1\] Reassembly complete" /path/to/ue/log
   # Expected: "[MESH][V1] Reassembly complete for GUID=xxxxxxxx (1/1 chunks)"

   # P3d: section built
   grep "\[MESH\]\[V1\] Built section" /path/to/ue/log
   # Expected: "[MESH][V1] Built section for GUID=xxxxxxxx vhash=xxxx: N verts, M tris, stride=32, hasColor=0"
   ```

   > **Note:** The v1 parser does NOT emit a dedicated "parsed/accepted" log line on success. Acceptance is inferred by the absence of rejection logs and the presence of "Stored chunk" markers.

### P4: Counter validation via UE.LiveSync.Stats

Open UE console (`` ` `` or Tab) and run:

```
UE.LiveSync.Stats
```

**Known limitation:** The current `ConsoleStats()` output does NOT include MeshSchemaV1 counters. The counters (listed below) are increment-only atomics that can only be verified via their UE_LOG side effects.

Counter expectations for a clean first sync:

| Counter | Expected | Verified via |
|---------|----------|-------------|
| `MeshSchemaV1PacketsParsed` | > 0 | Log marker "Stored chunk" implies parsed |
| `MeshSchemaV1ChunksStored` | > 0 | `grep "\[MESH\]\[V1\] Stored chunk"` |
| `MeshSchemaV1MeshesCompleted` | > 0 | `grep "\[MESH\]\[V1\] Reassembly complete"` |
| `MeshSchemaV1SectionsBuilt` | > 0 | `grep "\[MESH\]\[V1\] Built section"` |
| `MeshSchemaV1BuildRejected` | == 0 | Absence of `grep "\[MESH\]\[V1\] Build rejected"` |
| `MeshSchemaV1MissingActor` | == 0 | Absence of `grep "\[MESH\]\[V1\] Missing actor"` |

### P5: Visual result

1. Check the corresponding UE actor in the viewport
2. Verify:
   - The chosen mesh object has a ProceduralMeshComponent attached
   - Mesh geometry is visible and approximately correct shape
   - Normals look correct (not inside-out)
   - UV0 texture coordinates are usable (if a material is assigned)

### P6: Negative case — disconnected Blender

1. Disconnect Blender from UE (or stop UE server)
2. Run operator again
3. Expected: Operator reports `{'WARNING'}: "Not connected to UE"` and returns `CANCELLED`
4. No crash in Blender or UE

### P7: Negative case — non-MESH selected

1. Select a non-MESH object (Camera, Empty, Light, etc.) with no MESH objects selected
2. Run operator
3. Expected: Operator reports `{'WARNING'}: "No MESH objects selected"` and returns `CANCELLED`

### P8: Negative case — no selection

1. Deselect all objects (`A` to select all, then `A` again to deselect)
2. Run operator
3. Expected: Operator reports `{'WARNING'}: "No MESH objects selected"` and returns `CANCELLED`

## Helper Script

A collection script at `scripts/phase7c_stage2c4_collect_evidence.sh` can be used to:

```bash
# Before test: capture baseline
./scripts/phase7c_stage2c4_collect_evidence.sh before

# After test: capture evidence
./scripts/phase7c_stage2c4_collect_evidence.sh after
```

It captures:
- `git log --oneline -1` (repo commit)
- `diff -rq "$UEPLUGIN/Source" "$REPO/Source"` (source sync check)
- UE log extract (last N lines with `[MESH][V1]` marker grep)
- UE log extract for `[MESH]` markers (rejection counters)
- File timestamps for `.so` artifact

## Pass/Fail Criteria

**PASS** if all conditions hold:
1. P3a–P3d all produce expected log markers
2. P6, P7, P8 all produce expected operator warnings (no crash)
3. No `[MESH][V1] Build rejected` log markers appear
4. No `[MESH][V1] Missing actor` log markers appear for valid existing actor

**FAIL** if any:
1. `[MESH][V1] Build rejected` or `[MESH][V1] Missing actor` appears when a matching actor exists
2. Operator crashes Blender or UE
3. No log markers appear at all after operator execution
4. UE Editor or Blender crashes during any step

## References

- Baseline commits: `78af2b7` (parser), `0771734` (reassembly), `bf23da4` (build)
- UE_LOG markers are defined in `UELiveSyncSubsystem.cpp` lines 3949–4035 (reassembly) and 12060–12259 (build)
- Counter atomics in `SyncTypes.h` lines 1046–1135
- Blender operator in `__init__.py` lines 396–547
