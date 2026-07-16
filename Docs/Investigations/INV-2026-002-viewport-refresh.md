# INV-2026-002: Viewport Not Refreshed After Actor Spawn During Start UE Sync

## Metadata

- **Status**: Resolved
- **State**: Closed
- **Reason**: Historical Bug C not reproducible under current Build B baseline. Historical triggering conditions remain unknown.
- **Owner**: Khanh
- **Started**: 2026-07-12
- **Closed**: 2026-07-16
- **Classification**: Editor Viewport Update / Editor Tick
- **Parser Fix**: Commit `8444bd9` on `main` — proven, committed, build PASS
- **Investigation Instrumentation**: C11-C15 removed. C13-PATCH (bNeedsRedraw=true) added to plugin.
- **Engine Debug Copy**: `~/Unreal/UE5.8-debug/` — cleaned up
- **Build**: PASS (UE5.8/Linux)

### Reopen Criteria

- Historical Bug C reproduces under current Build B baseline
- Historical commit/environment identified that exhibits the original symptom
- Differential analysis between passing and failing environments yields new evidence

## Problem

After pressing **Start UE Sync**, actor appears in World Outliner but Viewport does not render the actor. Actor only becomes visible when user clicks/switches focus to the UE window.

## Symptoms

- Actor spawns successfully
- World Outliner updates immediately
- Viewport does not show actor until UE window receives focus
- Clicking UE window causes immediate viewport repaint — actor appears

## Timeline (Runtime-Verified)

```
Start UE Sync (Blender)
    ↓
TRANSPORT_ACCEPT_OK (UE)
    ↓
PT_Create (0x03) — Actor_0 + Actor_1 spawned
    ↓
HandleCreateObject() — both GUIDs processed
    ↓
PT_Collection, PT_CollectionOp, PT_Heartbeat
    ↓
PT_Timeline, PT_TimelineState, PT_ActiveCamera
    ↓
PT_Mesh (0x06) — 439KB mesh data applied
    ↓
Promoted ProcMesh to root — mesh render state ready
    ↓
❌ Viewport does NOT show actor
    ↓
User clicks/switches to UE window
    ↓
✅ Actor appears in viewport immediately
```

## Hypotheses

| ID | Hypothesis | Status | Evidence |
|----|-----------|--------|----------|
| H1 | Viewport not invalidated after spawn | **Strong** ⭐⭐⭐⭐☆ | Both actors affected equally → editor-level, not object-level |
| H2 | Slate tick not running when window inactive | **Strong** ⭐⭐⭐⭐☆ | Click/activate fixes immediately → Slate activation related |
| H3 | Component not registered / render state dirty | **Not disproved** | PT_Mesh applied, ProcMesh promoted — but render state submission to viewport unconfirmed |
| H4 | Transport / packet / queue issue | **Disproved** | Full packet sequence received and processed |
| H5 | Actor spawned into wrong World | **Low probability, not yet disproved** | Click makes actor appear — but only proves viewport renders correct World AFTER activation, not at spawn time |
| H6 | One or more operations performed during editor input processing may account for the observed visibility behavior | **Leading** ⭐⭐⭐⭐☆ | Observed: mouse click, keyboard input trigger visibility. Untested: hover, wheel, modifiers |
| H6a | Viewport invalidation during input processing may account for the observed visibility behavior | Alive | Competing explanation for H6 |
| H6b | Deferred editor work flushing during input processing may account for the observed visibility behavior | Alive | Competing explanation for H6 |
| H6c | Render/editor synchronization during input processing may account for the observed visibility behavior | Alive | Competing explanation for H6 |
| H6d | Slate invalidation propagation during input processing may account for the observed visibility behavior | Alive | Competing explanation for H6 |
| H8 | Spawned primitive not in DumpDetailedPrimitives output | **Testable** ⭐⭐⭐☆☆ | EXP-C: observe (no engine state change) |
| H9 | Render state recreation changes the rendering outcome | **Testable** ⭐⭐⭐☆☆ | EXP-D: intervene (forces engine state change) |
| H10 | One or more operations unique to the mouse-click path may account for the observed visibility behavior | **Testable** ⭐⭐⭐☆☆ | Generated from EXP-E: click-specific trigger narrows scope |

## Phase 0A — Verify Actual Runtime Packet Path ✅ COMPLETED

**Runtime evidence collected**: 2026-07-12, UE PID=57977, Blender PID=58904, port 57000

### Actual Packet Sequence After Start Sync

```
TRANSPORT_ACCEPT_OK (generation=1)
    ↓
PT_Create (0x03) seq=2 size=186      ← Actor spawn
    ↓
HandleCreateObject (2 GUIDs: Actor_0 + Actor_1)
    ↓
PT_Collection (0x08) seq=3 size=90
    ↓
PT_CollectionOp (0x05) seq=4 size=447
    ↓
PT_Heartbeat (0x07) seq=5 size=24
    ↓
PT_Timeline (0x13) seq=6 size=60
    ↓
PT_TimelineState (0x19) seq=7 size=44
    ↓
PT_ActiveCamera (0x15) seq=8 size=52
    ↓
PT_Mesh (0x06) seq=9 size=439,130    ← Mesh data applied
    ↓
PT_Heartbeat (0x07) seq=10+          ← Only heartbeats after this
```

### Runtime Observations

| Observation | Result |
|-------------|--------|
| PT_Create received | ✅ |
| HandleCreateObject executed | ✅ |
| Actor_0 in World Outliner | ✅ |
| Actor_1 in World Outliner | ✅ |
| PT_Mesh received | ✅ |
| Mesh applied (Promoted ProcMesh to root) | ✅ |
| Viewport shows actor WITHOUT click | ❌ |
| Viewport shows actor AFTER click | ✅ (immediately) |

### Key Finding

**Both Actor_0 and Actor_1 are affected equally.** This is critical:
- If bug was in HandleCreateObject(), typically one actor would fail
- Both actors failing identically → editor-level refresh issue, not object-level bug

### Conclusion

**Start UE Sync does NOT use PT_FBX (0x16).**

Packet path: `PT_Create → HandleCreateObject() → PT_Mesh`

**FBX importer audit (Phase 0) does not apply to this investigation.**

Future code audit must target:
- `HandleCreateObject()` (line 8074 in UELiveSyncSubsystem.cpp)
- Return path from HandleCreateObject
- Editor notification mechanisms after actor spawn

## Phase 0 — Code Audit ⚠️ IRRELEVANT (Phase 0A confirmed PT_Create, not PT_FBX)

**Audit date**: 2026-07-12
**Files audited**: LiveSyncFBXImporter.cpp, UELiveSyncSubsystem.cpp

**Status**: Phase 0A runtime evidence confirmed Start UE Sync uses PT_Create, not PT_FBX. This FBX audit is hypothesis-only and does not apply to this investigation.

**Historical reference only** — the audit below covers the FBX spawn path, which is NOT the entry point for this bug.

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

## Phase 0B — Execution Trace Audit (Priority: HIGH — DO FIRST)

**Phase 0A confirmed entry point**: `PT_Create → HandleCreateObject() → PT_Mesh`

**Runtime evidence is sufficient** — now audit the code to find the first candidate causal divergence.

### Objective

**Find the first plausible causal divergence before the observable symptom.**

A plausible causal divergence is a point where the observable execution, state transition, callback sequence, or thread scheduling differs from the UE reference implementation and may cause the viewport not to refresh.

Code audit identifies candidate divergences. Only runtime instrumentation can confirm causality.

Do NOT assume the cause is "missing notification" or "missing API". Follow the execution trace and let the evidence reveal the divergence.

### Audit Scope

Full trace from socket to frame presentation:

```
Socket receive
    ↓
Packet decode
    ↓
ProcessBinaryPacket()
    ↓
HandleCreateObject()
    ↓
Return to immediate execution context
    ↓
Queue / dispatch
    ↓
Game Thread
    ↓
Tick()
    ↓
Editor
    ↓
Slate
    ↓
Viewport draw
    ↓
Present
```

Trace to frame presentation, not just Tick. Many viewport refresh issues lie beyond Tick.

At each step, record:

```
Step: [name]
Thread: [Game / Slate / Render / Async]
State: [world, actor, component, registration, render state, selection, transaction]
Callback: [delegates, editor callbacks]
Deferred: [immediate / next tick / Slate / render thread / async]
Output: [what changes]
```

Viewport bugs are often not missing APIs but timing differences:
- Expected: A → B → C → (frame)
- Actual: A → (frame) → B → C

### Golden Reference (Current Investigation)

**Engine**: UE 5.8 (clean source checkout)
**Rationale**: Original UE 5.7.4 installation was modified and removed during previous investigations. A clean UE 5.8 source tree is used as the behavioral reference. Any version-specific differences discovered during the audit must be recorded explicitly.
**Source path**: `/home/nguyennongngockhanh/Unreal/UE5.8/`
**Shallow commit**: `6673776aa` ("Localization Automation using CL 55516479")
**Reference path**: The editor code path executed when a StaticMesh asset is dragged from the Content Browser into the Level Editor viewport.

**Version Compatibility Verification**:
All notification chain APIs verified present in UE 5.8 with identical signatures, no deprecation markers, no version guards:
- `PostEditMove(bool)` — Actor.h:2447
- `PostEditChangeProperty` — Actor.h:2394
- `NoteSelectionChange(bool)` — EditorSelectUtils.cpp:526
- `BroadcastLevelActorAdded(AActor*)` — Engine.h:2262
- `RedrawLevelEditingViewports(bool)` — UnrealEdEngine.cpp:1003
- `UWorld::SpawnActor` — LevelActor.cpp
- `UActorFactory::CreateActor` — ActorFactory.cpp:415
- `FEditorDelegates::OnNewActorsPlaced` — LevelEditorViewport.cpp:400
- `FEditorDelegates::OnNewActorsDropped` — LevelEditorViewport.cpp:2128

UE 5.8 release notes: no mention of editor notification API changes, viewport redraw changes, or actor spawn notification changes.
Git shallow clone: cannot diff 5.7→5.8 directly. But all APIs present with identical signatures, no deprecation markers anywhere, no `#if ENGINE_MAJOR_VERSION` guards.

**Notification chain verified unchanged between UE5.7 and UE5.8. Evidence Confidence: High.**

**FROZEN**: Do not replace the golden reference during the investigation. One reference per investigation. Replacing it invalidates all comparisons.

Do NOT compare with:
- Blueprint Spawn (different subsystem)
- Place Actor toolbar (different code path)
- SpawnActor runtime (no editor context)

### Audit Coverage (Inventory)

What files have been examined. This is an inventory, not a timeline. One row per file.

| File | Coverage | Reason |
|------|----------|--------|
| UELiveSyncSubsystem.cpp (lines 8074-8753) | Read | HandleCreateObject — plugin spawn path |
| UELiveSyncSubsystem.cpp (lines 5260-5862) | Read | ProcessBinaryPacket object loop — plugin caller context |
| UELiveSyncSubsystem.cpp (lines 2929, 3120-3200) | Read | ProcessBinaryPacket entry — packet dispatch |
| UELiveSyncSubsystem.cpp (lines 1632-2361) | Read | Tick function — full pipeline |
| LevelActor.cpp (lines 770-800) | Read | UWorld::PostSpawnInitialize — BroadcastLevelActorAdded |
| EditorEngine.cpp (lines 5378-5477) | Read | UEditorEngine::AddActor — legacy spawn path |
| ActorFactory.cpp (lines 342-440) | Read | UActorFactory::CreateActor + PostPlaceAsset — factory spawn path |
| LevelEditorViewport.cpp (lines 253-410) | Read | TryPlacingAssetObject — drop placement |
| LevelEditorViewport.cpp (lines 1174-1230) | Read | DropObjectsOnBackground — background drop |
| LevelEditorViewport.cpp (lines 1797-2140) | Read | DropObjectsAtCoordinates — main drop handler |
| EditorSelectUtils.cpp (lines 526-550) | Read | NoteSelectionChange — selection notification |
| UnrealEdEngine.cpp (lines 1003-1010) | Read | RedrawLevelEditingViewports |
| Engine.h (line 2262) | Read | BroadcastLevelActorAdded declaration |
| Actor.h (lines 2394, 2447) | Read | PostEditChangeProperty, PostEditMove declarations |
| LevelEditor.cpp (lines 693-754) | Read | FLevelEditorModule broadcast methods |

Coverage values:
- **Read** — file examined
- **Skipped** — intentionally not examined (explain why)
- **Deferred** — will examine later if needed

### Observed vs Reference

| Stage | Observed UE (Reference) | Observed Plugin | Difference | Evidence | Evidence Confidence | Causality Confidence |
|-------|------------------------|-----------------|------------|----------|---------------------|----------------------|
| World->SpawnActor | Called inside UActorFactory::CreateActor or GEditor->AddActor | Called directly in HandleCreateObject | Same | LevelActor.cpp:787, UELiveSyncSubsystem.cpp:8358 | High | — |
| PostSpawnInitialize → BroadcastLevelActorAdded | Always fires for editor-world spawns (LevelActor.cpp:787) | Always fires (inside World->SpawnActor) | Same | LevelActor.cpp:787 | High | — |
| PostSpawnActor (factory hook) | Called after spawn (ActorFactory.cpp:432) | NOT called | **Divergence #1** | ActorFactory.cpp:432 vs UELiveSyncSubsystem.cpp:8473 | High | Low |
| PostEditChange() | Called after spawn (ActorFactory.cpp:433, EditorEngine.cpp) | NOT called | **Divergence #2** | ActorFactory.cpp:433 | High | Low |
| PostEditMove(true) | Called after spawn (ActorFactory.cpp:434, EditorEngine.cpp:5457) | NOT called | **Divergence #3** | ActorFactory.cpp:434, EditorEngine.cpp:5457 | High | Low |
| SelectActor (legacy) | Called in GEditor->AddActor (EditorEngine.cpp:5453) | NOT called | **Divergence #4** | EditorEngine.cpp:5453 | High | Low |
| NoteSelectionChange → RedrawLevelEditingViewports | Called in legacy AddActor path (EditorEngine.cpp:5477 → EditorSelectUtils.cpp:544) | NOT called | **Divergence #5** | EditorEngine.cpp:5477, EditorSelectUtils.cpp:544 | High | Low |
| SelectionSet->SelectElements | Called after drop (LevelEditorViewport.cpp:2102) | NOT called | **Divergence #6** | LevelEditorViewport.cpp:2102 | High | Low |
| OnNewActorsPlaced.Broadcast | Called after TryPlacingAssetObject (LevelEditorViewport.cpp:400) | NOT called | **Divergence #7** | LevelEditorViewport.cpp:400 | High | Low |
| OnNewActorsDropped.Broadcast | Called after final drop (LevelEditorViewport.cpp:2128) | NOT called | **Divergence #8** | LevelEditorViewport.cpp:2128 | High | Low |
| Viewport->InvalidateHitProxy | Called before drop trace (LevelEditorViewport.cpp:1827) | NOT called | **Divergence #9** | LevelEditorViewport.cpp:1827 | High | Low |
| MarkPackageDirty / LevelDirtiedEvent | Called in legacy AddActor path | NOT called | **Divergence #10** | EditorEngine.cpp:5471-5472 | High | Low |
| Component registration | Via factory PostSpawnActor or AddActor | RegisterComponent() directly (UELiveSyncSubsystem.cpp:8698) | Different path, same outcome | UELiveSyncSubsystem.cpp:8698 | High | — |
| EndTransaction | Called after drop (LevelEditorViewport.cpp:1216) | NOT called (no BeginTransaction either) | Different context (no undo transaction in plugin) | LevelEditorViewport.cpp:1216 | High | Low |

### Audit Log (Timeline)

What was found during audit. This is a timeline, not an inventory. A file may appear multiple times.

| # | Timestamp | File | Function | Finding | Evidence | Status |
|---|-----------|------|----------|---------|----------|--------|
| #1 | 2026-07-13 | UELiveSyncSubsystem.cpp:8074-8753 | HandleCreateObject | Plugin calls World->SpawnActor but does NOT call PostEditMove, PostEditChange, NoteSelectionChange, RedrawLevelEditingViewports | Code audit | Continue |
| #2 | 2026-07-13 | UELiveSyncSubsystem.cpp:8358 | HandleCreateObject | World->SpawnActor<AActor> called directly (not via factory) | Code line 8358 | Continue |
| #3 | 2026-07-13 | UELiveSyncSubsystem.cpp:8698 | HandleCreateObject | RegisterComponent called directly (not via factory PostSpawnActor) | Code line 8698 | Continue |
| #4 | 2026-07-13 | UELiveSyncSubsystem.cpp:8753 | HandleCreateObject | Function returns — NO editor notification after RegisterComponent | Code line 8753 | Continue |
| #5 | 2026-07-13 | UELiveSyncSubsystem.cpp:5824-5862 | ProcessBinaryPacket | After HandleCreateObject returns: Phase6HCreatedThisTick.Add + UpdateTargetTransform — NO editor notification | Code line 5834 | Continue |
| #6 | 2026-07-13 | UELiveSyncSubsystem.cpp:1632-2361 | Tick | Full pipeline after ProcessQueuedPackets: EvictStale, Interpolate, ResolvePending, Recover, Phase6H/6I — NO editor notification anywhere | Code audit | Continue |
| #7 | 2026-07-13 | LevelActor.cpp:787 | PostSpawnInitialize | BroadcastLevelActorAdded fires inside World->SpawnActor — both plugin and UE reference trigger this | Code line 787 | Continue |
| #8 | 2026-07-13 | Engine.h:2262 | BroadcastLevelActorAdded | Listeners: FUnrealEdMisc::CB_LevelActorsAdded (marks dirty), UActorEditorContextSubsystem::ApplyContext (applies context) — NEITHER causes viewport redraw | Code audit | Continue |
| #9 | 2026-07-13 | ActorFactory.cpp:415-434 | CreateActor | UE reference: PreSpawnActor → SpawnActor → PostSpawnActor → PostEditChange → PostEditMove | Code lines 415-434 | Reference |
| #10 | 2026-07-13 | EditorEngine.cpp:5378-5477 | AddActor | UE legacy path: SelectNone → SpawnActor → SelectActor → PostEditMove → MarkPackageDirty → LevelDirtiedEvent → NoteSelectionChange | Code lines 5378-5477 | Reference |
| #11 | 2026-07-13 | EditorSelectUtils.cpp:526-544 | NoteSelectionChange | Calls RedrawLevelEditingViewports at line 544 — explicit viewport redraw | Code line 544 | Reference |
| #12 | 2026-07-13 | LevelEditorViewport.cpp:400 | TryPlacingAssetObject | OnNewActorsPlaced.Broadcast fires after all placement paths | Code line 400 | Reference |
| #13 | 2026-07-13 | LevelEditorViewport.cpp:2102,2128 | DropObjectsAtCoordinates | SelectionSet->SelectElements + OnNewActorsDropped.Broadcast | Code lines 2102, 2128 | Reference |
| #14 | 2026-07-13 | UE5.8 source | All APIs | No deprecation markers, no version guards on any notification API | Code audit | Reference |
| #15 | 2026-07-13 | UE 5.8 release notes | — | No mention of editor notification API changes between 5.7 and 5.8 | Web search | Reference |

Status values:
- **Continue** — keep tracing this branch
- **Candidate** — plausible divergence found, worth investigating
- **Eliminated** — ruled out by evidence
- **Deferred** — will check later if needed
- **Reference** — this is the UE reference path

### After Phase 0B

**10 candidate divergences identified.** All have Evidence Confidence: High (code audit clear). Causality Confidence: Low (not yet instrumented).

If candidate divergence identified → instrument → reproduce → confirm causality → fix.
If inconclusive → proceed to Phase B tests (B0, B0.5, B6', B-F).

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

**v11** (after Phase 0A runtime confirmation, Phase 0B methodology finalized)

```
Start UE Sync
      │
      ▼
Phase 0A: First packet dispatch?
      │
      ✅ COMPLETED — PT_Create → HandleCreateObject → PT_Mesh
      │
      ▼
Phase 0B: Find first candidate causal divergence
      │
      ├─ Candidate identified → instrument → reproduce → confirm causality → fix
      │
      ├─ Hypothesis falsified → reformulate → re-audit or Phase B tests
      │
      └─ Inconclusive (stopping condition met) → Phase B tests
            │
            ▼
      Phase B0: Alt+Tab to UE (no click)
            │
            ├─ Actor appears → Slate activation issue
            │
            └─ Actor does not appear → click viewport
                  │
                  ├─ Actor appears → viewport invalidation issue
                  │
                  └─ Actor does not appear → spawn issue (different bug)
            │
            ▼
      Phase B6': Click actor in Outliner → Details Panel updates?
            │
            ├─ YES → viewport-specific issue
            │
            └─ NO → systemic editor issue
            │
            ▼
      Phase B-F: Select actor → Press F
            │
            ├─ Camera frames actor → actor fully spawned → viewport issue
            │
            └─ F does not frame → spawn path incomplete
```
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

## Level 3 — CVar/Console Experiments (Observation → Intervention)

Principle: Observation first, intervention second. Never intervene before observing.

### EXP-C: DumpDetailedPrimitives (Infeasible — current constraints)

```
Experiment ID: EXP-C
Hypothesis: Primitive exists but is not in the viewport's visibility map.
Observable: Primitive presence in ViewDebug's primitive visibility map.
Observation point: FScene::AddPrimitive() or ViewDebug data.
Status: INFEASIBLE — observable is valid but current acquisition methods cannot reach it.
```

**Lifecycle**:
```
Preparation
───────────
C0  Source audit ✅ (no equivalent API)
C1  API feasibility ✅ (NO — private gate)

Decision gate: C1 = No → S1

Fallback strategies
───────────────────
S1  Startup ExecCmds — S1.1 ✅ (timing unsuitable)
S2  Automation framework — S2.0 NOT DONE
    (C1 already proved private gate; S2 is duplicate of C1)

S3  Engine instrumentation — not pursued
    (engine immutable per policy; would be last resort)
```

**Conclusion**: DumpDetailedPrimitives is a valid observable.
Infeasible because:
- Private ViewDebug gate (`bShouldUpdate`, `CaptureNextFrame()`, `EnableLiveCapture()`)
- Observer effect (running command requires clicking UE)
- ExecCmds timing unsuitable (runs before plugin actors exist)

Observable remains valid. Acquisition methods infeasible under current constraints.

**Observation Strategy**

The investigation is no longer tied to DumpDetailedPrimitives.
The objective is to find the least-perturbing observable with sufficient discriminating power to separate the remaining hypotheses.
DumpDetailedPrimitives is a valid observable but infeasible under current constraints (C0, C1, S1.1).

**Observation selection**

Question: Which observation provides the highest discrimination between the remaining hypotheses with the least perturbation?

Prefer observations that eliminate multiple hypotheses simultaneously.

**Key observation**

Observed keyboard and mouse input events trigger the actor becoming visible.
Untested: hover, wheel, modifiers, mouse move.

**Remaining hypotheses**

| ID | Hypothesis | Observable that would eliminate it | Current status |
|----|-----------|-------------------------------------|----------------|
| H3 | Component not registered / render state dirty | SceneProxy exists? Primitive registered? | **Eliminated** — `proxy=1 registered=1 renderState=1` (Runtime log) |
| H6 | One or more operations performed during editor input processing may account for the observed visibility behavior | Input event type discrimination — which events trigger it? | **Leading** |
| H6a | Viewport invalidation during input processing may account for the observed visibility behavior | (need to distinguish from H6b-H6d) | Alive — competing explanation |
| H6b | Deferred editor work flushing during input processing may account for the observed visibility behavior | (need to distinguish from H6a, H6c-H6d) | Alive — competing explanation |
| H6c | Render/editor synchronization during input processing may account for the observed visibility behavior | (need to distinguish from H6a-H6b, H6d) | Alive — competing explanation |
| H6d | Slate invalidation propagation during input processing may account for the observed visibility behavior | (need to distinguish from H6a-H6c) | Alive — competing explanation |
| H9 | Render state recreation changes rendering outcome | EXP-D intervention test | Alive — intervention, not observation |
| H10 | One or more operations unique to the mouse-click path may account for the observed visibility behavior | EXP-F series: isolate click path operations | Alive — generated from EXP-E |

**Alternative observation targets**

Possible observations:
- Does the click path trigger selection, and does selection change rendering behavior?
- Does the click path activate the viewport in a way that `RedrawLevelEditingViewports()` doesn't?
- Does `r.RecreateRenderStateContext` change the outcome?

Possible observation points (where to instrument):
- `UPrimitiveComponent::CreateRenderState_Concurrent()`
- `FScene::AddPrimitive()`
- `MarkRenderStateDirty()`

Observable ≠ observation point. An observable is what you measure. An observation point is where you instrument to get the measurement.

**Flow**

```
Remaining hypotheses
        │
        ▼
Observation selection (highest discrimination, least perturbation)
        │
        ▼
Observable
        │
        ▼
Observation point
        │
        ▼
Instrumentation
        │
        ▼
Evidence
```

### EXP-D: r.RecreateRenderStateContext (Intervention)

```
Experiment ID: EXP-D
Hypothesis: Render state recreation changes the rendering outcome.
Type: Intervention (forces engine state change — Level 3)
Variable changed: Console command forces render state recreation on all components.
Expected observation if correct:
  Before command: actor invisible.
  After command: actor visible.
Expected observation if wrong:
  Still invisible. EXP-D eliminated.
Conclusion scope: EXP-D confirms recreating state is SUFFICIENT.
  Does NOT identify WHY (multiple causes could produce same result:
  stale state, scene registration timing, deferred updates,
  editor viewport cache, render thread synchronization).
Alternative explanations if EXP-D positive:
  1. Render state was stale after spawn
  2. Deferred editor update was flushed by the command
  3. Viewport cache was invalidated
  4. Scene registration timing was corrected
  5. Render thread synchronization was triggered
```

**Status**: Deferred — command never executed; incidental observation during baseline setup produced new evidence
**Type**: Intervention (changes engine state — Level 3)
**Perturbation level**: 3 (state-changing — alters renderer behavior)

EXP-D was deferred before executing `r.RecreateRenderStateContext`.
During baseline setup, incidental observation revealed that certain input events
(keyboard typing, mouse click) make the actor appear. This is new evidence that
superseded the original EXP-D design. EXP-E later refined: only click triggers.

### Post-EXP-D Decision Tree

```
EXP-D
   │
   ├── Negative
   │      │
   │      └── H9 eliminated
   │              ↓
   │         Investigate H6
   │         (what does click do that RedrawLevelEditingViewports doesn't?)
   │
   └── Positive
          │
          ▼
          Competing explanations:
          - render state stale after spawn?
          - viewport cache not invalidated?
          - redraw suppression in editor path?
          - render-thread sync gap?
          - scene update ordering?
```

If EXP-D is eventually run and positive → multiple alternatives remain alive. Next experiment must distinguish between them.

Note: EXP-D is currently deferred. Input event type discrimination is the immediate next step.

### Observation Selection — Current State

```
Remaining hypotheses: H6 (+ H6a-H6d), H9 (H3 eliminated)

Key observation:
  keyboard/mouse input → actor appears
  (hover, wheel, modifiers untested — NOW TESTED: all negative)
  Only mouse click triggers visibility.

H6 competing explanations:
  H6a — viewport invalidation
  H6b — deferred editor work flushed
  H6c — render/editor sync point advanced
  H6d — Slate invalidation propagates to viewport

All H6a-H6d remain alive after EXP-E.
EXP-E narrows trigger to click path but does not distinguish explanations.

DumpDetailedPrimitives: INFEASIBLE

Current direction:
  Isolate individual operations in click path.
  One operation per experiment (Single-Variable Experiment Rule).
```

## Bug C — Viewport Refresh after PMC Creation

- Status: **Current reference environment does not reproduce the original issue. Regression point unknown.**

### Historical Root Cause Hypothesis (SUPERSeded by C14/C15 evidence)

**Bug C root cause chain** (historical hypothesis, now superseded):

```
EditorEngine::Tick() (line 1807):
  const bool bShouldDisableRendering = !FApp::HasFocus() && PerformanceSettings->bThrottleCPUWhenNotForeground;
  ViewportClient->AddRealtimeOverride(false, "Background Process");
    ↓
IsRealtime() → false
    ↓
UpdateSingleViewportClient (line 2656):
  if (IsRealtime()) → Draw()     ← SKIP (realtime=false)
  else if (bNeedsRedraw) → Draw() ← plugin doesn't set this flag
  else → NO DRAW
    ↓
No C11 (viewport draw) → No C8 (visibility) → No mesh
```

**When user clicks/returns focus to UE**:
- `FApp::HasFocus()` → true
- `AddRealtimeOverride(true, ...)` clears the false override
- `IsRealtime()` → true
- `UpdateSingleViewportClient` → `Draw()` executes
- Viewport renders → actor appears

### Current Behavior

- Current reference test environment: actors are visible after Start Sync without user interaction.
- C15: non-realtime draw branch entered at frame 522.
- C11: Draw() executed at frame 522.
- C8: Actor_0/Actor_1 present in visibility dump at frame 522.

### What C15 Proves

- The non-realtime draw branch in `UpdateSingleViewportClient` works correctly.
- When `bNeedsRedraw=1` and `AllowNonRT=1`, the viewport draws even when `IsRealtime()=0`.

### What C15 Does NOT Prove

- C15 does not prove the original bug was fixed.
- C15 does not prove this was the path that failed during the original reproduction.
- C15 does not establish causal relationship between any code change and the disappearance of the issue.

### Regression Point Unknown

- Which change made the issue stop reproducing: **unknown**.
- Whether commit `8444bd9` (parser fix) is related: **unknown**.
- Whether any uncommitted runtime patch is related: **unknown**.
- Whether the issue still exists under different conditions: **unknown**.

### Action

No rollback/bisect unless bug reappears or historical logs prove old state.

### What Has Been Proven

**Bug A and B (RESOLVED)**:
- Parser fix (Bug A): Commit `8444bd9` — PT_Mesh multi-object parsing correct. ✅
- GUID→Actor→PMC (Bug B): Proven NOT an issue — different PMC pointers per actor. ✅

**Engine pipeline (C2-C10)**:
- SceneProxy exists: C2 log shows `proxy!=0 renderState=1 registered=1`. ✅
- CreateMeshSection() calls MarkRenderStateDirty(): Engine source confirmed. ✅
- Deferred update chain completes: C2 confirms proxy present by next Tick. ✅
- AddPrimitive() IS called: C3 engine instrumentation confirms. ✅
- GetDynamicMeshElements() called: C4 confirms mesh elements submitted when viewport draws. ✅
- GetViewRelevance() called: C5 confirms relevance computed when viewport draws. ✅
- FPrimitiveSceneInfo::AddToScene() called: C6 confirms primitive enters scene. ✅
- ComputeRelevance considers our primitive: C7 confirms. ✅
- Primitive appears in FrustumCull: C8 full range dump shows Actor_0/Actor_1. ✅

**Historical investigation (C11-C13, superseded)**:
- Viewport stops drawing when editor backgrounded: C11 historical observation. ✅
- Background throttling disables realtime: C12 historical observation. ✅
- Plugin completes mesh rebuild while editor backgrounded: C13 historical observation. ✅
- Bug C root cause hypothesis: **Superseded by C14/C15 evidence**.

**Latest runtime validation (C14-C15)**:
- Non-realtime draw branch works: C15 confirms branch entered at frame 522. ✅
- Viewport draws when backgrounded: C11 confirms Draw() at frame 522. ✅
- Actors visible in current test: Current reference test environment — actors appear after Start Sync. ✅

### What Has NOT Been Proven

- Regression point (which change made the issue stop reproducing)
- Whether the original bug was fixed by any code change
- Whether commit `8444bd9` (parser fix) is related to the regression
- Whether the issue still exists under different conditions

### Engine Source Findings (Read-Only)

**Viewport redraw gate** (EditorEngine.cpp:2656-2703):
```
Line 2673: if (IsRealtime()) → Draw()           ← fails when backgrounded
Line 2681: else if (IsOrtho()) → Draw()         ← fails for perspective viewport
Line 2698: else if (bNeedsRedraw && bInAllowNonRealtimeViewportToDraw) → Draw()
  bInAllowNonRealtimeViewportToDraw = true (line 2340, hardcoded)
```

**`RedrawAllViewports()` implementation** (EditorServer.cpp:306-316):
- Calls `ViewportClient->Invalidate(false, bInvalidateHitProxies)` (line 313)
- `Invalidate()` calls `Viewport->InvalidateDisplay()` (EditorViewportClient.cpp:6642)
- `InvalidateDisplay()` marks Slate widget dirty but does **NOT** set `bNeedsRedraw`
- `bNeedsRedraw` is only set by constructor (line 525) and `RedrawRequested()` callback (line 827)

**A/B test result**: `RedrawAllViewports(false)` **FAILED** — zero C11/C8 during frames 309-330 (HasFocus=0). See "A/B Test" section.

### Hypotheses for Bug C

| ID | Hypothesis | Status | Evidence |
|----|-----------|--------|----------|
| H-C1 | Deferred update not yet completed when user clicks | **Eliminated** | C2 confirms proxy present by next Tick |
| H-C2 | Deferred update completed, primitive in scene, but no viewport draw occurred | **Confirmed** | C11 shows no draws after editor loses focus; C12 shows IsRealtime()=0 |
| H-C3 | Deferred update processing did not recreate the render state | **Eliminated** | C2 confirms renderState=1; C3 confirms AddPrimitive() called |

### A/B Test — RedrawAllViewports(false) (FAILED)

```
Experiment ID: A/B Test
Hypothesis: Calling GEditor->RedrawAllViewports(false) after BuildV1MeshFromReassembly()
            will trigger viewport draw while editor is backgrounded.
Type: Intervention (engine state change — Level 3)
Perturbation level: 3
```

**Patch applied**: `GEditor->RedrawAllViewports(false)` after `BuildV1MeshFromReassembly()` with `IsInGameThread()` and `FApp::HasFocus()` probes.

**Test result**: **FAILED** — zero C11/C8 during frames 331-442 (HasFocus=0).

**Timeline evidence**:

| Time | Frame | Marker | What happened |
|------|-------|--------|---------------|
| 14:33:37:280 | 308 | C12 | `Realtime=0 NeedsRedraw=1 HasFocus=0` — editor loses focus |
| 14:33:37:280 | 308 | C11 | Last viewport Draw #310 |
| 14:33:37→44 | 309-330 | C13-PATCH | RedrawAllViewports called every tick, HasFocus=0 |
| — | 309-330 | C11 | **NO viewport Draw** |
| 14:33:44:292 | 330 | C6 | Actor_0/Actor_1 AddToScene |
| 14:33:44:948 | 331 | C11 | Viewport Draw #311 — **user returned focus** |
| 14:33:44:948 | 331 | C8 | Actor_0/Actor_1 appear (indices 17, 18) |

**Why it failed** (engine source proof):
- `RedrawAllViewports(false)` → `Invalidate()` → `InvalidateDisplay()` — marks Slate dirty but does NOT set `bNeedsRedraw`
- Draw gate at line 2698 requires `bNeedsRedraw=true`
- `bNeedsRedraw` only set by `RedrawRequested()` callback (line 827), not by `Invalidate()`/`InvalidateDisplay()`

**Conclusion**: `RedrawAllViewports(false)` is INSUFFICIENT to trigger viewport draw when editor is backgrounded. Must set `bNeedsRedraw=true` directly on viewport clients.

### Patch v2 — Direct bNeedsRedraw (PROVEN INEFFECTIVE)

**Approach**: Set `bNeedsRedraw = true` on each `FLevelEditorViewportClient` via `GEditor->GetLevelViewportClients()`, then call `RedrawAllViewports(false)` for Slate invalidation.

```cpp
if (GEditor)
{
    int32 ViewportCount = 0;
    for (FLevelEditorViewportClient* LevelVC : GEditor->GetLevelViewportClients())
    {
        if (LevelVC)
        {
            LevelVC->bNeedsRedraw = true;
            ViewportCount++;
        }
    }
    GEditor->RedrawAllViewports(false);
}
```

**Rationale**: The draw gate at line 2698 checks `bNeedsRedraw && bInAllowNonRealtimeViewportToDraw`. Since `bInAllowNonRealtimeViewportToDraw` is always true (hardcoded at line 2340), setting `bNeedsRedraw=true` should make the gate pass even when `IsRealtime()=false`.

**Risk**: The IsRealtime() gate at line 2673 runs FIRST. If `IsRealtime()=false`, execution falls through to line 2698 (bNeedsRedraw check). This is the intended path. But if there's an earlier gate that blocks execution before line 2673, bNeedsRedraw won't help.

### Engine Source Findings (Read-Only)

**CreateMeshSection() end** (ProceduralMeshComponent.cpp:633-635):
```cpp
UpdateLocalBounds(); // → UpdateBounds() + MarkRenderTransformDirty()
UpdateCollision();   // → RecreatePhysicsState()
MarkRenderStateDirty(); // → bRenderStateDirty = true → queue
```

**MarkRenderStateDirty()** (ActorComponent.cpp:2693-2708):
- Sets `bRenderStateDirty = true`
- Calls `MarkForNeededEndOfFrameRecreate()`
- Component goes to `ComponentsThatNeedEndOfFrameUpdate_OnGameThread`

**Queue flush** (LevelTick.cpp:1120):
- `SendAllEndOfFrameUpdatesInternal()` processes queue
- Called from `UGameEngine::Tick()` and `BeginRenderViewFamily()`

**DoDeferredRenderUpdates_Concurrent()** (ActorComponent.cpp:2645-2690):
- If `bRenderStateDirty` → `RecreateRenderState_Concurrent()`
- Skip if `!IsRegistered()`

**EditorEngine::Tick() background throttling** (EditorEngine.cpp:1807):
```cpp
const bool bShouldDisableRendering = !FApp::HasFocus() && PerformanceSettings->bThrottleCPUWhenNotForeground;
ViewportClient->AddRealtimeOverride(false, "Background Process");
```

**UpdateSingleViewportClient decision gate** (EditorEngine.cpp:2656-2703):
```
if (IsRealtime()) → Draw()                    ← line 2673
else if (IsOrtho()) → Draw()                  ← line 2681
else if (bNeedsRedraw && bInAllowNonRealtimeViewportToDraw) → Draw()  ← line 2698
```

**RedrawAllViewports implementation** (EditorServer.cpp:306-316):
- Iterates AllViewportClients
- Calls ViewportClient->Invalidate(false, bInvalidateHitProxies)
- Calls Viewport->InvalidateDisplay() — marks Slate dirty, does NOT set bNeedsRedraw

**bNeedsRedraw assignment** (EditorViewportClient.cpp):
- Constructor (line 525): `bNeedsRedraw = true`
- RedrawRequested() callback (line 827): `bNeedsRedraw = true`
- NOT set by Invalidate() or InvalidateDisplay()

### Confirmed by Runtime Evidence

1. **Entry point**: `PT_Create → HandleCreateObject() → PT_Mesh` (NOT PT_FBX)
2. **Both actors affected**: Actor_0 + Actor_1 both fail to render → editor-level issue, not object-level
3. **Click fixes immediately**: IsRealtime() restored when user returns focus
4. **Mesh applied successfully**: "Promoted ProcMesh to root" confirms render state is ready
5. **AddPrimitive() called**: C3 engine instrumentation confirms
6. **Visibility pipeline works**: C7/C8 confirm ComputeRelevance and FrustumCull process our primitive
7. **Viewport stops drawing**: C11 confirms no draws when editor backgrounded
8. **Root cause**: Background throttling disables IsRealtime() → no viewport Draw

### Phase 0B Findings

**UE Reference flow** (Drag StaticMesh from Content Browser):
- `World->SpawnActor()` → `BroadcastLevelActorAdded` (same as plugin)
- `PostEditChange()` → `PostEditMove(true)` → `NoteSelectionChange()` → `RedrawLevelEditingViewports()`
- `SelectionSet->SelectElements()` → viewport redraw

**Plugin flow** (HandleCreateObject):
- `World->SpawnActor()` → `BroadcastLevelActorAdded` (same)
- `RegisterComponent()` → return (NO further notifications)

**10 candidate divergences identified.** All have Evidence Confidence: High. Causality Confidence: Low.

Most significant for viewport refresh:
- `PostEditMove(true)` — tells editor actor was placed/moved
- `NoteSelectionChange()` → `RedrawLevelEditingViewports()` — explicit viewport redraw
- `SelectionSet->SelectElements()` — selection change → viewport redraw

**NOTE**: These 10 divergences are from Phase 0B code audit. Later investigation shifted focus toward background throttling, but that explanation is no longer considered established because the issue is currently not reproducible.

### Investigation Journal

```
EXP-A: Eliminated — correct world/level (Runtime log)
EXP-B: Eliminated — not editor-hidden (Runtime log)
EXP-C: Infeasible — valid observable, no acquisition method (C0, C1, S1.1)
EXP-D: Deferred — command never executed; incidental observation superseded
EXP-E: Completed — only click triggers visibility. Hover/wheel/modifiers do not.
EXP-F: Superseded — historical background-throttling hypothesis (C11–C13).
C1 (SceneProxy): Confirmed — proxy!=0, renderState=1, registered=1
C2 (Proxy check): Confirmed — proxy present by next Tick
C3 (AddPrimitive): Confirmed — FScene::AddPrimitive() called
C4 (GetDynamicMeshElements): Confirmed — called when viewport draws
C5 (GetViewRelevance): Confirmed — called when viewport draws
C6 (AddToScene): Confirmed — primitive enters scene with valid bounds
C7 (ComputeRelevance): Confirmed — considers our primitive
C8 (FrustumCull): Confirmed — primitive appears in visibility map after user interaction
C8-TASKCONFIG: Confirmed — Primitives.Num=17, Tested=17, AlwaysVisibleOffset=~0u
C11 (Viewport Draw): Confirmed — stops when editor backgrounded, resumes on focus
C12 (Decision Gate): Confirmed — IsRealtime()=0 when HasFocus()=0
C13 (Plugin Pipeline): Confirmed — mesh rebuild completes while editor backgrounded
A/B Test (RedrawAllViewports): FAILED — does NOT set bNeedsRedraw, insufficient
Patch v2 (bNeedsRedraw=true): PROVEN INEFFECTIVE (true→true)
```

### Next Step

**Test patch v2**: Set `bNeedsRedraw=true` directly on viewport clients after mesh push.

Same test procedure:
1. Close UE + Blender
2. UE Editor GUI → open project
3. Blender → open test `.blend`
4. **Alt+Tab away from UE**
5. Blender → **Start Sync**
6. Wait 5 seconds
7. **Alt+Tab back to UE**
8. Reply: **done**

If C11 appears after setting bNeedsRedraw while HasFocus=0 → direct flag set bypasses IsRealtime gate.
If still fails → IsRealtime() gate at line 2673 is absolute blocker → need to temporarily override IsRealtime or bypass the gate.

## Fix

**Bug A**: Commit `8444bd9` — PT_Mesh multi-object parser fix. ✅
**Bug B**: Runtime confirmed — different PMC pointers per actor. ✅ (no code fix needed)
**Bug C**: See Bug C section — current reference environment does not reproduce the original issue.

## Regression

| Scenario | Result |
|----------|--------|
| Phase 0A runtime test | ✅ COMPLETED — PT_Create → HandleCreateObject → PT_Mesh confirmed |
| Phase A (reproduce) | ✅ COMPLETED — bug reproduced, both actors affected |
| Phase 0B (execution trace audit) | ✅ COMPLETED — 10 candidate divergences identified |
| Phase C2 (SceneProxy check) | ✅ COMPLETED — proxy present by next Tick |
| Phase C3 (AddPrimitive) | ✅ COMPLETED — FScene::AddPrimitive() called |
| Phase C4 (GetDynamicMeshElements) | ✅ COMPLETED — called when viewport draws |
| Phase C5 (GetViewRelevance) | ✅ COMPLETED — called when viewport draws |
| Phase C6 (AddToScene) | ✅ COMPLETED — primitive enters scene |
| Phase C7 (ComputeRelevance) | ✅ COMPLETED — considers our primitive |
| Phase C8 (FrustumCull) | ✅ COMPLETED — primitive in visibility map |
| Phase C11 (Viewport Draw) | ✅ COMPLETED — stops when editor backgrounded |
| Phase C12 (Decision Gate) | ✅ COMPLETED — IsRealtime()=0 when HasFocus()=0 |
| Phase C13 (Plugin Pipeline) | ✅ COMPLETED — mesh rebuild completes while backgrounded |
| A/B Test (RedrawAllViewports) | ✅ COMPLETED — FAILED, insufficient |
| Patch v2 (bNeedsRedraw=true) | ✅ COMPLETED — proven ineffective (true→true) |
| C14/C15 (Full gate state) | ✅ COMPLETED — non-realtime draw path works correctly |
| Issue reproduction | **NOT REPRODUCIBLE** — current reference test environment: actors visible after Start Sync |

## Decision Log

| ID | Decision | Based on | Accepted | Rejected | Reason |
|----|----------|----------|----------|----------|--------|
| D1 | Open investigation | User report: actor in Outliner but not in Viewport | Opened | — | Clear symptom, distinct from INV-2026-001 |
| D2 | Phase 0 code audit before reproduce | Low cost, may identify subsystem quickly | Accepted | Jump to reproduce | Audit provides hypothesis without runtime tests |
| D3 | Phase 0 is hypothesis only, not root cause | "Not called" ≠ "Needs to be called" | Accepted | Claim root cause identified | Confirmation bias |
| D4 | Add Phase 0A: verify entry point | Don't assume Start UE Sync enters FBX importer | Accepted | — | Wrong entry point → wrong audit |
| D5 | Add H5: wrong World hypothesis | Cheap to rule out, known UE issue | Accepted | — | PIE/Editor/Preview World confusion |
| D6 | Add F test: cheap spawn validation | Select in Outliner + Press F | Accepted | — | Confirms actor fully spawned vs viewport issue |
| D7 | Add H6: editor input triggers visibility | Observed keyboard/mouse input events trigger actor visibility | Accepted | — | Consistent with input event trigger pattern |
| D8 | Add Phase 0B: audit Tick lifecycle | Verify Spawn runs on Game Thread, inside Tick, Tick returns normally | Accepted | — | Don't assume Tick doesn't complete |
| D9 | Add H7: realtime viewport disabled | Viewport client may not be marked realtime — known UE issue | Accepted | — | Cheap to check, common cause of this symptom |
| D10 | Add B6': Details Panel update test | Distinguishes viewport-specific from systemic editor issue | Accepted | — | Cheaper and more reliable than stat fps |
| D11 | Phase 0A COMPLETED | Runtime evidence confirms PT_Create → HandleCreateObject → PT_Mesh | Accepted | — | FBX importer audit irrelevant for this bug |
| D12 | Phase 0B priority: HandleCreateObject audit | Entry point confirmed, audit return path for missing editor notification | Accepted | — | Direct path to candidate root cause |
| D13 | H3, H5 not yet disproved | PT_Mesh applied but render state submission unconfirmed; click proves viewport renders correct World AFTER activation only | Accepted | — | Premature to claim disproved |
| D14 | Phase 0B approach: execution trace, not API grep | Neutral audit — follow call flow, don't assume "missing notification" | Accepted | — | Avoid confirmation bias during audit |
| D15 | Golden reference updated to UE 5.8 | Original UE 5.7.4 installation modified/removed. Clean UE 5.8 source used. Notification chain verified unchanged between 5.7 and 5.8. | Accepted | — | Evidence-based reference with version compatibility documented |
| D16 | Phase 0B: UE reference trace completed | Drag StaticMesh from Content Browser → Level Viewport call chain fully traced | Accepted | — | Both plugin and UE reference now have complete traces |
| D17 | Phase 0B: 10 candidate divergences identified | All have Evidence Confidence: High, Causality Confidence: Low | Accepted | — | Instrument to confirm causality before fix |
| D18 | Level 3 ordering: observation before intervention | EXP-C (DumpDetailedPrimitives) before EXP-D (r.RecreateRenderStateContext) | Accepted | Run intervention first | Scientific method: observe state before changing it. Intervention alone cannot identify cause. |
| D19 | EXP-D scope: "changes outcome" not "identifies cause" | r.RecreateRenderStateContext confirming behavior change does not prove why | Accepted | Claim MarkRenderStateDirty was missing | Multiple causes could produce same result (stale state, registration timing, deferred updates, cache, sync) |
| D20 | INV-2026-002: current reference environment does not reproduce the issue | All recent tests show actors visible after Start Sync. Regression point unknown. | Accepted | Continue rollback/bisect | Current reference test environment does not reproduce the issue. Rolling back would break a working state without evidence the bug still exists. |
| D21 | No rollback without evidence | Don't rollback patches to "find what fixed the bug" when bug is not reproducible | Accepted | Rollback to find regression point | Would break working state. Only rollback if bug reappears or historical logs prove old state. |

## Lessons Learned

- **Verify entry point before auditing**: Don't assume Start UE Sync uses FBX importer — it may use PT_Create. Audit the wrong path = wasted effort.
- **"Not called" ≠ "Needs to be called"**: Code audit can identify what APIs are absent, but cannot prove they are required.
- **Distinguish Alt+Tab from click viewport**: Two very different bugs — Slate activation vs viewport invalidation.
- **Cheap tests first**: Ctrl+Shift+P + F + Details Panel update costs nothing and immediately distinguishes spawn issue from viewport issue.
- **H5 (wrong World)**: Not disproved but cheap to rule out — actor may exist in a different editor world than the active viewport.
- **H6 (editor input triggers visibility)**: Observed keyboard/mouse input events trigger actor visibility — not the same as Tick not completing. Competing explanations: H6a (viewport invalidation), H6b (deferred work flushed), H6c (sync point), H6d (Slate invalidation).
- **H7 (realtime disabled)**: Viewport client may not be marked realtime — known cause of this exact symptom pattern.
- **B6' > B6**: Details Panel update test is cheaper and more reliable than stat fps — it directly tests whether editor transactions are running.
- **Don't use RedrawAllViewports() as hammer fix**: If the issue is viewport client realtime state or World context, adding RedrawAllViewports() only masks symptoms.
- **Both actors affected = editor-level issue**: If both Actor_0 and Actor_1 fail identically, the bug is not in object-specific code but in editor-level refresh机制.
- **Phase 0A eliminates FBX audit**: Runtime evidence confirmed PT_Create, not PT_FBX — Phase 0 FBX audit was hypothesis-only and now irrelevant.
- **HandleCreateObject is the entry point**: Future code audit must focus on HandleCreateObject() return path and editor notification mechanisms.
- **Entry point confirmed ≠ Root cause confirmed**: Identifying the execution path is step one; finding the missing notification is step two.
- **"Promoted ProcMesh to root" ≠ render state submitted**: Mesh data applied doesn't prove viewport received render state.
- **Click proves viewport AFTER activation, not World correctness at spawn**: H5 requires World comparison at spawn time, not after activation.
- **Runtime sufficient → code audit next**: When runtime evidence identifies the execution path, prioritize code audit over additional UI tests.
- **Don't lock into hypotheses during audit**: Phase 0B should be neutral — follow execution trace, don't assume "missing notification" or target specific APIs.
- **Execution trace > API grep**: Following call flow step by step is more reliable than grepping for familiar API names.
- **Compare with standard flow**: Identify what UE Editor does during normal spawn that plugin doesn't do.
- **One golden reference**: Compare with Drag StaticMesh from Content Browser — closest flow to plugin. Don't compare with multiple flows (Blueprint, Place Actor, runtime) — different subsystems, noise.
- **Audit caller, not just function**: Many UE bugs lie in the caller. If ProcessBinaryPacket should notify editor after HandleCreateObject() returns, only auditing HandleCreateObject() will miss it.
- **Expected vs Actual checklist**: Track what UE normally does vs what plugin does at each stage — prevents reasoning while reading code.
- **Golden reference is engine code, not UI action**: "Drag StaticMesh from Content Browser" is the user action; the reference is the exact call chain in engine code.
- **Trace to frame presentation**: Don't stop at Tick — viewport refresh may happen after Tick in Slate or compositor.
- **"First behavioral divergence" > "missing step"**: Divergence is neutral; "missing step" implies you know what should be there.
- **Don't hard-code checklist steps**: Leave Expected vs Actual blank until you read the code — assuming steps creates confirmation bias.
- **Immediate execution context, not "caller"**: In UE, return context may be lambda, AsyncTask, FTSTicker, delegate, or queue dispatcher.
- **Define behavioral divergence**: First point where observable execution, state transition, callback sequence, or thread scheduling differs from reference.
- **Track timing at each step**: Immediate vs deferred (next tick, Slate, render thread, async). Viewport bugs are often timing, not missing APIs.
- **Freeze golden reference**: One reference per investigation. Replacing it invalidates all Expected/Actual comparisons.
- **Freeze by engine version**: Golden reference must match engine version (UE 5.7.4). Comparing across versions is invalid.
- **Causal divergence, not just behavioral**: Not all divergences cause the symptom. Stop at the first causal divergence before the observable symptom.
- **State snapshot at each step**: Record thread, state, callback, deferred, output — enables replay and prevents losing context.
- **Observed, not expected**: UE has multiple branches. Record what is observed, not an idealized "expected".
- **Stopping condition**: Stop tracing when first candidate causal divergence is isolated AND further tracing unlikely to produce higher-leverage candidate. Do not trace indefinitely. (Note: "unlikely" here refers to expected information gain, not a proven conclusion.)
- **Candidate causal, not causal**: Code audit identifies candidates. Only runtime instrumentation confirms causality. Don't call a divergence "causal" before runtime confirmation.
- **Confidence per divergence**: Not all divergences are equal. Track confidence (Low/Medium/High) based on evidence type (code only / partial runtime / fully confirmed).
- **Freeze by source tree**: UE 5.7.4 Launcher may differ from UE 5.7.4 source. Lock the exact source tree for golden reference.
- **Freeze by commit/tag**: "UE 5.7.4 source" is not enough — hotfixes, local patches, cherry-picks may differ. Lock by commit hash or tag.
- **Two confidence types**: Evidence Confidence (difference exists?) vs Causality Confidence (difference causes bug?). They evolve independently through investigation.
- **Golden reference can be updated with rationale**: When original reference is unavailable, a clean alternative can be used if version compatibility is verified and documented.
- **Version compatibility verification is essential**: Before using UE 5.8 as reference for UE 5.7.4 investigation, verify all notification chain APIs are unchanged (signatures, deprecation markers, version guards).
- **Shallow clone limits git diff**: Cannot diff 5.7→5.8 directly with shallow clone. Rely on API signature comparison and release notes instead.
- **UE reference flow has extensive notification chain**: Drag StaticMesh triggers BroadcastLevelActorAdded, PostEditChange, PostEditMove, NoteSelectionChange, RedrawLevelEditingViewports, SelectionSet->SelectElements, OnNewActorsPlaced, OnNewActorsDropped.
- **BroadcastLevelActorAdded alone does NOT cause viewport refresh**: Its listeners (FUnrealEdMisc::CB_LevelActorsAdded, UActorEditorContextSubsystem::ApplyContext) do not trigger viewport redraw.
- **Plugin has 10 candidate divergences from UE reference**: All are missing editor notifications after actor spawn. Most significant: PostEditMove, NoteSelectionChange→RedrawLevelEditingViewports, SelectionSet->SelectElements.
- **Freeze file set**: Golden reference includes files audited. If audit shifts to other files, record the shift. File set is part of the reference fingerprint.
- **Audit Log**: Track what was read, what was skipped, and why. Prevents re-reading and losing context during multi-hour audits.
- **Audit Coverage ≠ Golden Reference**: Files audited is an artifact of investigation, not part of the reference fingerprint. Reference is frozen; coverage expands as audit progresses.
- **Audit Coverage is inventory, Audit Log is timeline**: Coverage answers "what files have I read?". Log answers "what did I find?". A file appears once in Coverage, potentially many times in Log.
- **Reference Resolution**: Lock Module/File/Function/Commit to avoid reading wrong overload. UE has many overloads — ambiguous function names cause wasted audit time.
- **Audit Log Status**: Use Continue/Candidate/Eliminated/Deferred/Reference to distinguish "read" from "ruled out" from "worth pursuing".
- **Observation before intervention**: Never run an intervention (console command, code change) before observing the current state. DumpDetailedPrimitives (observation) before r.RecreateRenderStateContext (intervention).
- **Intervention confirms sufficiency, not cause**: If a console command changes behavior, it confirms the intervention is sufficient to change outcome — not that the original cause is identified. Multiple causes could produce the same result.
- **Don't over-interpret from survey results**: A CVar survey identifies candidates and their mechanisms. It does not prove which mechanism the current bug uses. Keep hypotheses humble until runtime evidence confirms.
- **Evidence Before Conclusion in practice**: The gap between "this command could change behavior" and "therefore X was missing" is exactly where Evidence Before Conclusion matters. State the observation, not the inferred cause.
- **Golden reference is read-only**: The frozen engine source is used only for code audit and comparison. Any engine instrumentation, logging, assertions, or experimental modifications must be performed in a separate debug working copy (or dedicated debug branch if a separate working copy is impractical). The frozen reference must remain identical throughout the investigation to preserve a stable baseline. Hard links (`cp -al`) do not preserve immutability — modifying a hard-linked file modifies the original.
- **"Issue not reproducible" ≠ "bug fixed"**: When an issue stops reproducing, the correct conclusion is "current reference test environment does not reproduce the issue." Concluding "bug is fixed" requires identifying which change caused the regression. Without that evidence, the regression point is unknown.
- **Don't rollback to find regression point without evidence**: Rolling back patches to "find what fixed the bug" breaks a working state. Only rollback when: (a) bug reappears, or (b) historical logs prove the old state was different, or (c) git bisect is feasible and the regression window is narrow.
- **Build target matters for engine instrumentation**: Engine source changes (EditorEngine.cpp) must be compiled into the correct target. Project-level builds may not pick up engine source changes if the engine .so is not loaded by the project editor.

---

## Current Confidence

**Bug A (Parser)**
- Status: RESOLVED
- Evidence: Runtime confirmed — both objects decoded, PMC created, CreateMeshSection called
- Commit: `8444bd9`

---

**Bug B (PMC ownership)**
- Status: RESOLVED
- Evidence: Runtime confirmed — different PMC pointers per actor, no shared PMC, no last-writer-wins

---

**Bug C (Viewport refresh)**

Resolved:
- PT_Mesh parser fixed.
- Renderer pipeline verified end-to-end.
- Build A demonstrates the observed effect of missing viewport redraw.
- Build B passes 3/3 independent reproductions with immediate rendering.

Evidence established:
- Missing viewport redraw can prevent continued visibility evaluation in the tested non-realtime editor viewport configuration.
- Restoring viewport redraw consistently restores normal rendering behavior under the current baseline.

Open:
- Historical Bug C could not be reproduced under the current Build B baseline.
- The historical triggering conditions remain unknown and would require differential analysis against the original failing environment if future evidence warrants reopening the investigation.

Removed from active investigation (not supported by current evidence):
- UE 5.8 regression
- Slate invalidation issue
- SceneProxy creation failure
- Frustum culling failure
- RenderState registration failure
- Visibility pipeline failure

Investigation direction:
- Previous: Why doesn't UE render?
- Current: What was different in the historical environment?

---

## Open Questions

1. Under what exact conditions did the original viewport refresh issue reproduce?
2. Which source revision was the last known state exhibiting the issue?
3. Did commit `8444bd9` or any later uncommitted runtime change eliminate the original behavior?
4. Until the issue reproduces again or historical evidence narrows the regression window, these questions remain unanswered.
