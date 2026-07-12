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

## Phase 0 — Code Audit

**Audit date**: 2026-07-12
**Files audited**: LiveSyncFBXImporter.cpp, UELiveSyncSubsystem.cpp

### Spawn path (what the plugin does)

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
- Audit only covers the FBX spawn path, not the full Start UE Sync execution (Socket → Queue → Tick → HandleCreateObject → Spawn → Tick Return → Slate → Viewport)

### What this audit suggests (hypothesis only)

The spawn path does not explicitly invalidate the editor viewport. Combined with the symptom (actor visible in Outliner but not in Viewport until window focus), this suggests the issue may be related to editor viewport invalidation or Slate tick timing. **This is a hypothesis, not a confirmed root cause.**

**Confidence**: Medium — code audit provides suggestive evidence but runtime validation is required.

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

**v3** (after Phase 0 audit, corrected)

```
Start UE Sync
      │
      ▼
Actor spawns?  →  Yes (Outliner visible)
      │
      ▼
Phase A: Reproduce
      │
      ▼
Phase B0: Alt+Tab to UE (no click)
      │
      ├─ Actor appears → Slate activation / editor tick issue
      │                    → Phase C: audit Tick + Slate
      │
      └─ Actor does not appear → click viewport
            │
            ├─ Actor appears → viewport invalidation issue
            │                   → Phase C: audit viewport notification
            │
            └─ Actor does not appear → spawn issue (different bug)
```

## Root Cause

**Status**: Strong hypothesis — pending runtime confirmation

Code audit shows the spawn path (`LiveSyncFBXImporter.cpp:2570-2683`) does not call any editor viewport invalidation API. Combined with the symptom (actor in Outliner but not in Viewport until window focus), this suggests the issue may be related to editor viewport invalidation or Slate tick timing.

**However:**
- "Not called" ≠ "Needs to be called" — many editor spawn paths don't call RedrawAllViewports()
- Audit only covers FBX spawn path, not the full Start UE Sync execution
- Runtime behavior may differ from what audit suggests

**Confidence**: Medium — hypothesis supported by code audit, requires Phase A/B/C runtime validation.

## Fix

Not applicable. Investigation in progress.

## Regression

| Scenario | Result |
|----------|--------|
| Pending Phase A | — |

## Decision Log

| ID | Decision | Based on | Accepted | Rejected | Reason |
|----|----------|----------|----------|----------|--------|
| D1 | Open investigation | User report: actor in Outliner but not in Viewport | Opened | — | Clear symptom, distinct from INV-2026-001 |
| D2 | Phase 0 code audit before reproduce | Low cost, may identify subsystem quickly | Accepted | Jump to reproduce | Audit provides hypothesis without runtime tests |
| D3 | Phase 0 is hypothesis only, not root cause | "Not called" ≠ "Needs to be called"; many spawn paths don't call RedrawAllViewports() | Accepted | Claim root cause identified | Confirmation bias — must validate with runtime evidence |

## Lessons Learned

- **"Not called" ≠ "Needs to be called"**: Code audit can identify what APIs are absent, but cannot prove they are required. Many UE editor spawn paths don't call viewport invalidation APIs — viewport still updates via Slate tick.
- **Audit must cover the full execution path**: Not just SpawnActor, but Socket → Queue → Tick → HandleCreateObject → Spawn → Tick Return → Slate → Viewport.
- **Distinguish Alt+Tab from click viewport**: Two very different bugs — Slate activation vs viewport invalidation.
- **Avoid confirmation bias**: Don't assume a missing API is the root cause without runtime evidence.
