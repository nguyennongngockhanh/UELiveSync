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
| H6 | Tick starvation — Editor update loop delayed, SpawnActor happens in Tick but Tick doesn't complete full editor update cycle | Pending — more likely than H5 |

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

**Confidence**: Medium — code audit provides suggestive evidence but runtime validation is required.

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
- Does Tick() complete the full editor update cycle?

If Tick doesn't complete, Slate has no opportunity to redraw.

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

### B-F — Frame Selected Test (low-cost validation)

1. Ctrl+Shift+P or click actor in World Outliner
2. Details panel updates?
3. Press F (Frame Selected)

| Result | Interpretation |
|--------|---------------|
| Camera frames actor immediately | Actor fully spawned and registered → viewport update issue |
| F does not frame actor | Spawn path incomplete — different bug |

### B6 — stat fps / stat unit Test

After actor spawns but BEFORE clicking UE, type in UE console:

```
stat fps
```

or

```
stat unit
```

| Result | Interpretation |
|--------|---------------|
| Overlay appears immediately | Viewport is still redrawing — issue is actor-specific invalidation |
| Overlay does NOT appear | Entire viewport/Slate loop is not repainting — systemic issue |

This distinguishes "viewport invalidate" from "entire Slate/Viewport loop stalled."

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
- Is Tick() completing the full editor update cycle?

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

**v5** (after H6 + Phase 0B + B6)

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
Phase B6: stat fps / stat unit (no click)
      │
      ├─ Overlay appears → viewport redrawing, actor-specific issue
      │
      └─ Overlay missing → entire Slate/Viewport loop stalled
      │
      ▼
Phase B-F: Select actor in Outliner → Press F
      │
      ├─ Camera frames actor → actor fully spawned → viewport update issue
      │
      └─ F does not frame → spawn path incomplete
```

## Root Cause

**Status**: Strong hypothesis — pending runtime confirmation

Code audit shows the FBX spawn path does not call any editor viewport invalidation API. However:
- "Not called" ≠ "Needs to be called"
- Phase 0A may reveal the entry point is PT_Create, not PT_FBX — making the FBX audit irrelevant
- H5 (wrong World) is possible but low probability
- H6 (Tick starvation) is more likely — SpawnActor may happen in Tick but Tick doesn't complete full editor update cycle
- Runtime behavior may differ from what audit suggests

**Confidence**: Medium — hypothesis supported by code audit, requires Phase 0A/0B/A/B/C runtime validation.

## Fix

Not applicable. Investigation in progress.

## Regression

| Scenario | Result |
|----------|--------|
| Pending Phase 0A + Phase A | — |

## Decision Log

| ID | Decision | Based on | Accepted | Rejected | Reason |
|----|----------|----------|----------|----------|--------|
| D1 | Open investigation | User report: actor in Outliner but not in Viewport | Opened | — | Clear symptom, distinct from INV-2026-001 |
| D2 | Phase 0 code audit before reproduce | Low cost, may identify subsystem quickly | Accepted | Jump to reproduce | Audit provides hypothesis without runtime tests |
| D3 | Phase 0 is hypothesis only, not root cause | "Not called" ≠ "Needs to be called" | Accepted | Claim root cause identified | Confirmation bias |
| D4 | Add Phase 0A: verify entry point | Don't assume Start UE Sync enters FBX importer | Accepted | — | Wrong entry point → wrong audit |
| D5 | Add H5: wrong World hypothesis | Cheap to rule out, known UE issue | Accepted | — | PIE/Editor/Preview World confusion |
| D6 | Add F test: cheap spawn validation | Select in Outliner + Press F | Accepted | — | Confirms actor fully spawned vs viewport issue |
| D7 | Add H6: Tick starvation | More likely than H5 — SpawnActor in Tick but Tick doesn't complete full cycle | Accepted | — | Explains Outliner update without viewport redraw |
| D8 | Add Phase 0B: audit Tick lifecycle | Verify Spawn runs on Game Thread, inside Tick, Tick returns normally | Accepted | — | If Tick doesn't complete, Slate can't redraw |
| D9 | Add B6: stat fps/unit test | Distinguishes actor-specific invalidation from systemic viewport stall | Accepted | — | Cheap, high diagnostic value |

## Lessons Learned

- **Verify entry point before auditing**: Don't assume Start UE Sync uses FBX importer — it may use PT_Create. Audit the wrong path = wasted effort.
- **"Not called" ≠ "Needs to be called"**: Code audit can identify what APIs are absent, but cannot prove they are required.
- **Distinguish Alt+Tab from click viewport**: Two very different bugs — Slate activation vs viewport invalidation.
- **Cheap tests first**: Ctrl+Shift+P + F + stat fps costs nothing and immediately distinguishes spawn issue from viewport issue.
- **H5 (wrong World)**: Unlikely but cheap to rule out — actor may exist in a different editor world than the active viewport.
- **H6 (Tick starvation)**: SpawnActor may happen in Tick but Tick doesn't complete full editor update cycle. This is more likely than viewport invalidation and explains why Outliner updates but viewport doesn't.
- **Don't use RedrawAllViewports() as hammer fix**: If the issue is Tick/Slate lifecycle or World context, adding RedrawAllViewports() only masks symptoms.
