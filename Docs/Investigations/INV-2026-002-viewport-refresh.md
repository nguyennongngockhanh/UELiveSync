# INV-2026-002: Viewport Not Refreshed After Actor Spawn During Start UE Sync

## Metadata

- **Status**: Open
- **Owner**: Khanh
- **Started**: 2026-07-12
- **Closed**: —
- **Classification**: Editor Viewport Update / Editor Tick

## Problem

After pressing **Start UE Sync**, actor appears in World Outliner but Viewport does not render the actor. Actor only becomes visible when user clicks/switches focus to the UE window.

## Symptoms

- Actor spawns successfully
- World Outliner updates immediately
- Viewport does not show actor until UE window receives focus
- Clicking UE window causes immediate viewport repaint — actor appears

## Timeline

```
Start UE Sync
    ↓
Actor spawns (visible in Outliner)
    ↓
Viewport does NOT show actor
    ↓
User clicks/switches to UE window
    ↓
Slate tick / viewport invalidate
    ↓
Actor appears in viewport
```

## Hypotheses

| ID | Hypothesis | Status |
|----|-----------|--------|
| H1 | Viewport not invalidated after spawn | Pending — code audit suggestive |
| H2 | Slate tick not running when window inactive | Pending |
| H3 | Component not registered / render state dirty | Pending — less likely |
| H4 | Transport / packet / queue issue | Disproved — actor spawns, Outliner updates |
| H5 | Actor spawned into correct World but LevelViewportClient isn't viewing that World yet | Pending — low probability but cheap to rule out |
| H6 | Spawn occurs during a frame where viewport redraw is deferred or suppressed | Pending |

## Phase 0A — Verify Actual Runtime Packet Path

**Do NOT assume Start UE Sync enters the FBX importer.**

Start UE Sync may use:
- PT_Create → HandleCreateObject() → SpawnActor()
- PT_FBX → HandleImport() → SpawnActor()
- Both

Capture the first packet dispatch sequence after Start UE Sync:

1. Fresh UE session + fresh Blender session
2. Clear UE log
3. Press **Start UE Sync**
4. Check UE log for first packet type after connection:
   - PT_Create = 0x01
   - PT_FBX = 0x16
   - Other
5. Record exact dispatch order

**Only then audit the corresponding execution path.**

If PT_Create drives actor creation, the FBX importer audit (Phase 0) is irrelevant.

## Phase 0 — Code Audit

**Audit date**: 2026-07-12
**Files audited**: LiveSyncFBXImporter.cpp, UELiveSyncSubsystem.cpp

**Condition**: Phase 0 audit covers FBX spawn path (PT_FBX / 0x16). Only relevant if Phase 0A confirms FBX is the entry point.

### FBX spawn path

```
ProcessBinaryPacket (type=0x16)
    ↓
FLiveSyncFBXImporter::HandleImport
    ↓
World->SpawnActor<AStaticMeshActor>
    ↓
SMC->SetStaticMesh(StaticMesh)
    ↓
RefreshFBXStaticMeshComponent(SMC, MeshActor)
    ├─ SMC->SetVisibility(true, true)
    ├─ SMC->SetHiddenInGame(false, true)
    ├─ SMC->UpdateBounds()
    ├─ SMC->MarkRenderStateDirty()
    └─ OwnerActor->SetActorHiddenInGame(false)
    ↓
EnsureFBXMeshRenderable(SMC, StaticMesh, MeshActor, ...)
    ↓
Context.OnRestoreGeneratedMaterials(...)
    ↓
Context.OnActorCached(...)
    ↓
Context.OnMarkFbxAuthority(...)
    ↓
return true
```

### Viewport refresh APIs

| API | Called? |
|-----|---------|
| GEditor->RedrawAllViewports() | NO |
| BroadcastLevelActorAdded() | NO |
| NoteSelectionChange() | NO |
| FEditorSupportDelegates::Invalidate() | NO |
| MarkRenderStateDirty() | YES |

### What this audit does NOT prove

- "Not called" ≠ "Needs to be called"
- Many UE editor spawn paths don't call RedrawAllViewports() — viewport still updates via Slate tick / editor notification
- MarkRenderStateDirty() affects render state, not viewport invalidation
- Audit only covers the FBX spawn path, not the full Start UE Sync execution

### What this audit suggests (hypothesis only)

If Phase 0A confirms FBX is the entry point: the spawn path does not explicitly invalidate the editor viewport. Combined with the symptom, this suggests the issue may be related to editor viewport invalidation or Slate tick timing. **This is a hypothesis, not a confirmed root cause.**

**Confidence**: Low (runtime evidence pending) — code audit provides suggestive evidence but no runtime validation has been performed yet.

## Phase 0B — Audit Tick Lifecycle

After Phase 0A confirms the entry point, audit the full execution path:

```
Socket thread
    ↓
PacketQueue
    ↓
Tick()
    ↓
Spawn
    ↓
return
```

Verify:
- Spawn runs on Game Thread?
- Spawn happens inside Tick()?
- Tick returns normally?
- Any async task holding Editor thread?

Do NOT assume Tick doesn't complete — if Tick truly doesn't return, many other subsystems would stall too.

## Phase A — Reproduce

### Steps

1. Fresh UE session
2. Fresh Blender session
3. Press **Start UE Sync**
4. Check: Actor appears in World Outliner?
5. Check: Viewport shows actor WITHOUT clicking UE window?
6. If NO to step 5: click UE window
7. Check: Actor appears immediately?

### Observation Matrix

| Observation | Expected (bug present) |
|-------------|----------------------|
| World Outliner | Actor appears |
| Details Panel | Actor selectable, properties update |
| Transform | Correct |
| Viewport | NOT visible |
| Click UE window | Appears immediately |

### Success Criteria

- 100% reproduce rate under controlled conditions
- Actor in Outliner = YES
- Viewport without focus = NO
- Viewport after focus = YES

## Phase B — Distinguish Redraw vs Spawn

### B0 — Window Activation Test (critical)

Do NOT click viewport. Only:

- Alt+Tab to UE, or
- Ctrl+Tab to UE

| Result | Interpretation |
|--------|---------------|
| Actor appears on window activate (no click) | Slate activation / editor tick issue — viewport updates when Slate ticks |
| Actor does NOT appear on window activate | Need click in viewport → different issue (viewport invalidation) |

**These are two very different bugs.** Must test before any conclusion.

### B0.5 — Viewport Configuration Check

Before deeper investigation, verify basic viewport configuration:

- [ ] Viewport Realtime enabled (not throttled)
- [ ] Game View off (not in game view mode)
- [ ] Correct viewport (not a secondary/preview viewport)

If Realtime is disabled, this is not a plugin bug — it's a user configuration issue.

### B6' — Details Panel Update Test

After actor spawns but BEFORE clicking viewport:

1. Click actor in World Outliner
2. Check: Details panel updates immediately?

| Result | Interpretation |
|--------|---------------|
| Details Panel updates | Editor transaction running normally → issue is viewport-specific, not systemic |
| Details Panel does NOT update | Systemic editor issue |

This narrows the scope: if Outliner + Details Panel work but Viewport doesn't, the problem is viewport client redraw/realtime — not Slate or editor tick globally.

### B-F — Frame Selected Test (low-cost validation)

1. Ctrl+Shift+P or click actor in World Outliner
2. Details panel updates?
3. Press F (Frame Selected)

| Result | Interpretation |
|--------|---------------|
| Camera frames actor immediately | Actor fully spawned and registered → viewport update issue |
| F does not frame actor | Spawn path incomplete — different bug |

### B1-B5 — Additional Tests

| Test | Action | If YES → | If NO → |
|------|--------|----------|---------|
| B1 | Press F (Frame Selected) | Redraw issue | Spawn issue |
| B2 | Pilot actor | Actor exists | Actor missing |
| B3 | Toggle Game View | Redraw issue | Other |
| B4 | Toggle Realtime OFF/ON | Redraw issue | Other |
| B5 | Move camera | Redraw issue | Other |

## Phase C — Audit Tick + Slate (after Phase A/B)

If runtime confirms the symptom, audit the full Start UE Sync execution path:

```
Socket thread
    ↓
PacketQueue
    ↓
Tick()
    ↓
HandleCreateObject() / FBX import
    ↓
Spawn actor
    ↓
Post spawn processing
    ↓
Tick return
    ↓
Slate
    ↓
Editor viewport
```

Identify:
- Is Tick() running when the window is inactive?
- Does Slate tick after Tick() returns?
- Does the editor viewport receive any notification after actor spawn?
- Is there a deferred redraw or notification queue?

## Phase D — Find Appropriate API (after Phase C)

If Phase C confirms the subsystem, find the appropriate API. Avoid `GEditor->RedrawAllViewports()` unless runtime evidence demonstrates it is required — many editor spawn paths don't call it.

Possible APIs:
- `GEditor->RedrawAllViewports(false)` — simplest, invalidates all viewports (heavy)
- `BroadcastLevelActorAdded(Actor)` — notifies level editor of new actor
- `FEditorSupportDelegates::BroadcastLevelActorAdded(Actor)` — delegate version
- Other editor notification mechanisms

## Expected Diagnostic Markers (if needed)

Only add after Phase C confirms the subsystem:

| Marker | Location | Purpose |
|--------|----------|---------|
| VIEWPORT_REDRAW_REQUEST | UELiveSyncSubsystem.cpp | Confirm redraw requested |
| VIEWPORT_REDRAW_DONE | UELiveSyncSubsystem.cpp | Confirm redraw completed |

Do NOT add markers until Phase C identifies the missing API.

## Decision Tree

**v6** (after H6/H7, B6' replacing B6)

```
Start UE Sync
      │
      ▼
Phase 0A: First packet dispatch?
      │
      ├─ PT_Create → audit HandleCreateObject path
      │
      ├─ PT_FBX → audit FBXImporter path (Phase 0)
      │
      └─ Both → audit both paths
      │
      ▼
Phase A: Reproduce
      │
      ▼
Phase B0: Alt+Tab to UE (no click)
      │
      ├─ Actor appears → Slate activation / editor tick issue
      │                    → Phase 0B: audit Tick lifecycle
      │
      └─ Actor does not appear → click viewport
            │
            ├─ Actor appears → viewport invalidation issue
            │                   → Phase C: audit viewport notification
            │
            └─ Actor does not appear → spawn issue (different bug)
      │
      ▼
Phase B6': Click actor in Outliner → Details Panel updates?
      │
      ├─ YES → editor transaction normal → viewport-specific issue
      │          → Phase C: audit viewport client / realtime
      │
      └─ NO → systemic editor issue → Phase 0B: audit Tick lifecycle
      │
      ▼
Phase B-F: Select actor in Outliner → Press F
      │
      ├─ Camera frames actor → actor fully spawned → viewport update issue
      │
      └─ F does not frame → spawn path incomplete
```

## Root Cause

**Status**: Immediate rendering failure mechanism confirmed (2026-07-19)

Bug C's immediate mechanism is an interaction between the fixed 250 ms visibility threshold in `SEditorViewport::IsVisible()` and the background viewport tick interval (~331 ms). When the tick interval exceeds the visibility threshold, `IsVisible()` consistently returns false, causing Gate1 to reject viewport ticking until another event refreshes `LastTickTime`.

The origin of the ~331 ms tick interval remains unexplained. Possible sources include Background Process override, Slate throttle, editor idle scheduler, or realtime override stack. This is a separate investigation.

### Causal Chain

```
Background mode
    ↓
Viewport tick interval ≈331 ms
    ↓
LastTickTime becomes older than VisibilityTimeThreshold (250 ms)
    ↓
SEditorViewport::IsVisible() == false
    ↓
Gate1 (UEditorEngine::Tick) rejects viewport tick
    ↓
Viewport does not render
```

### Key Source Evidence

- `SEditorViewport.cpp:344-347` — `Tick()` updates `LastTickTime = FPlatformTime::Seconds()`
- `SEditorViewport.cpp:1029-1046` — `IsVisible()` returns `Delta <= VisibilityTimeThreshold` (0.25f)
- `EditorEngine.cpp:2258-2268` — Gate1 checks `ViewportClient->IsVisible()` before allowing viewport tick

### Runtime Evidence

- `INV-VISIBLE` instrumentation confirms `delta=0.331` at background tick rate
- `INV-Gate1` instrumentation confirms `visible=0 pass=0` after Background Process fires
- Background tick interval fluctuates between 0.329–0.332 s

### Causal Intervention Test (2026-07-19)

Single variable changed: `VisibilityTimeThreshold` in `SEditorViewport.cpp:1031`.

| Threshold | delta (background) | visible=0 | visible=1 | Actors in viewport? | Verdict |
|-----------|-------------------|-----------|-----------|---------------------|---------|
| 0.25 | 0.331 | 1,774 | 0 | No | Bug C present |
| 0.30 | 0.331 | — | — | No | Bug C present |
| 0.32 | 0.331 | — | — | No | Bug C present |
| **0.33** | **0.329–0.332** | **334** | **955** | **Yes (intermittent)** | **Crossover** |
| 0.35 | 0.331 | 0 | 1,378 | Yes | Bug C absent |
| 10.0 | 0.331 | 0 | 1,378 | Yes | Bug C absent |

All other variables held constant: same project, same mesh (Cabinet), same plugin, same build configuration, same Start UE Sync procedure, same Background Process behavior.

Crossover occurs at approximately the measured background tick interval (~0.331 s), consistent with the proposed mechanism.

### What remains unexplained

- Why the background viewport tick interval is ~331 ms (Background Process override, Slate throttle, editor idle scheduler — separate investigation)

**Confidence**: High (supported by source analysis, runtime instrumentation, and causal intervention with dose-response boundary test).

## Fix

Not yet implemented. Possible directions:
- Increase `VisibilityTimeThreshold` (simple but hardcodes a magic number)
- Fix `IsVisible()` to use a different mechanism (e.g., check whether the viewport client is actively displaying content rather than time-based threshold)
- Prevent Background Process from throttling the active viewport below the visibility threshold

## Regression

| Scenario | Result |
|----------|--------|
| Pending fix implementation | — |

## Decision Log

| ID | Decision | Based on | Accepted | Rejected | Reason |
|----|----------|----------|----------|----------|--------|
| D1 | Open investigation | User report: actor in Outliner but not in Viewport | Opened | — | Clear symptom, distinct from INV-2026-001 |
| D2 | Phase 0 code audit before reproduce | Low cost, may identify subsystem quickly | Accepted | Jump to reproduce | Audit provides hypothesis without runtime tests |
| D3 | Phase 0 is hypothesis only, not root cause | "Not called" ≠ "Needs to be called" | Accepted | Claim root cause identified | Confirmation bias |
| D4 | Add Phase 0A: verify entry point | Don't assume Start UE Sync enters FBX importer | Accepted | — | Wrong entry point → wrong audit |
| D5 | Add H5: wrong World hypothesis | Cheap to rule out, known UE issue | Accepted | — | PIE/Editor/Preview World confusion |
| D6 | Add F test: cheap spawn validation | Select in Outliner + Press F | Accepted | — | Confirms actor fully spawned vs viewport issue |
| D7 | Add H6: viewport redraw deferred | Spawn occurs during a frame where viewport redraw is deferred | Accepted | — | Explains Outliner update without viewport redraw |
| D8 | Add Phase 0B: audit Tick lifecycle | Verify Spawn runs on Game Thread, inside Tick, Tick returns normally | Accepted | — | Don't assume Tick doesn't complete |
| D9 | Add H7: realtime viewport disabled | Viewport client may not be marked realtime — known UE issue | Accepted | — | Cheap to check, common cause of this symptom |
| D10 | Add B6': Details Panel update test | Distinguishes viewport-specific from systemic editor issue | Accepted | — | Cheaper and more reliable than stat fps |
| D11 | Causal intervention test: change VisibilityTimeThreshold | Strongest evidence method — change one variable, observe dose-response | Accepted | — | Observational evidence insufficient for proof |
| D12 | Revert threshold to 0.25f after test | 0.33f is diagnostic, not a fix | Accepted | Keep 0.33f | Fix should address mechanism, not hardcode value |

## Lessons Learned

- **Verify entry point before auditing**: Don't assume Start UE Sync uses FBX importer — it may use PT_Create. Audit the wrong path = wasted effort.
- **"Not called" ≠ "Needs to be called"**: Code audit can identify what APIs are absent, but cannot prove they are required.
- **Distinguish Alt+Tab from click viewport**: Two very different bugs — Slate activation vs viewport invalidation.
- **Cheap tests first**: Ctrl+Shift+P + F + Details Panel update costs nothing and immediately distinguishes spawn issue from viewport issue.
- **H5 (wrong World)**: Unlikely but cheap to rule out — actor may exist in a different editor world than the active viewport.
- **H6 (viewport redraw deferred)**: Spawn may occur during a frame where viewport redraw is deferred or suppressed — not the same as Tick not completing.
- **H7 (realtime disabled)**: Viewport client may not be marked realtime — known cause of this exact symptom pattern.
- **B6' > B6**: Details Panel update test is cheaper and more reliable than stat fps — it directly tests whether editor transactions are running.
- **Don't use RedrawAllViewports() as hammer fix**: If the issue is viewport client realtime state or World context, adding RedrawAllViewports() only masks symptoms.
- **Causal intervention > observational evidence**: Changing one variable (VisibilityTimeThreshold) and observing the dose-response curve provided far stronger evidence than any amount of log analysis. The crossover point (0.33 ≈ 0.331) directly fingerprinted the mechanism.
- **Distinguish correlation from causation**: `AddRealtimeOverride(0)` preceding the tick interval change was correlation, not causation. The actual causal mechanism was the threshold/interval mismatch.
