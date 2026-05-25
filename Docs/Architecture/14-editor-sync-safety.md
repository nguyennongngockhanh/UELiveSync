# Editor Synchronization Safety Rules

> Pre-Phase-6 documentation of explicit safety rules for
> editor-side change replication.

---

## 1. When Editor Changes Are Allowed to Replicate

Editor-side changes must replicate TO Blender only when all the following conditions are met:

1. **Actor is managed** — has the `UELiveSync_Managed` tag (or equivalent marker)
2. **Change is non-transient** — not part of a drag operation, resize handle, or mouse-down preview
3. **Change is not undo-redo related** — not triggered by Ctrl+Z/Ctrl+Y
4. **Change is not a construction-script side-effect** — not spawned or modified by a blueprint construction script during a non-user action
5. **Change originates from a user action** — keyboard, mouse, menu, or console command
6. **Sync is not in flood-detection cooldown** — within 2-second window since last bulk change

### Implementation Constraint
Phase 5 has NO Blender-side socket listener. Phase 6 must add UE→Blender TCP to enable editor-side replication. This direction currently has zero infrastructure.

---

## 2. When Replication Must Be Suppressed

Replication from UE to Blender must be suppressed during:

| Condition | Rationale |
|-----------|-----------|
| During snapshot replay | Snapshot bursts would trigger false positive change detection |
| During initial sync | Spawn events not yet complete |
| During reconnect cooldown | Connection not yet stable |
| During undo/redo transactions | Undo would create cycle |
| During editor loading | World not fully initialized |
| During PIE (Play In Editor) | Game-mode state ≠ editor state |
| During construction script execution | Temporary actors and modifications |
| During bulk operations (CVar-triggered) | Explicit "do not sync" mode |
| For non-Blender-origin actors | Actor missing managed tag |

---

## 3. Preventing Recursive Feedback Loops

### The Problem
```
Blender renames object "Cube" → UE renames actor "Cube"
→ UE detects rename → sends rename back to Blender
→ Blender receives rename → sends rename confirmation to UE
→ UE receives confirmation → detects as user change
→ sends rename again...
```

### Required Safeguards

1. **Change origin tagging** — Every mutation must carry an origin marker:
   - `EChangeOrigin::BlenderSync` — ignore for replication back to Blender
   - `EChangeOrigin::UESync` — ignore for replication back to UE
   - `EChangeOrigin::User` — triggers replication to other peer

2. **Re-entrancy guard** — While processing an incoming packet, suppress outgoing replication for the same mutation type

3. **Idempotency check** — Compare before/after state; if no effective change, don't replicate

4. **Depth counter** — Maximum 3 levels of recursion; at depth 3, suppress and log warning

---

## 4. Transient Editor Actions to Ignore

The following editor actions must be detected and suppressed:

| Action | Detection Method | Suppression |
|--------|-----------------|-------------|
| Alt+Drag duplicate | Track spawn during viewport drag | Suppress spawn replication until drag ends |
| Temporary visibility toggles (Alt+H) | No permanent selection change | Suppress visibility sync when toggled via hotkey |
| Construction script ghost actors | IsEditorPreviewActor or HasAnyFlags(AActor::RF_Transient) | Never replicate |
| Viewport selection highlight | No actor state change | Already handled (selection ≠ sync state) |
| Mouse-down while dragging resize handle | No final position yet | Suppress transform update until mouse-up |
| Outliner drag-reorder (re-parent) | Drag in progress | Suppress until drag completes |
| Actor preview mode | Only exists when "Preview" button toggled | Never replicate |
| Undo transaction construction | UTransactor::IsUndoing() | Suppress all sync during undo transaction |
| Copy/paste in editor | No GUID yet during paste preview | Suppress until paste finalizes |

---

## 5. Rename Storm Prevention

A rename storm occurs when a bulk rename operation (e.g., renaming 200 actors via Python script) generates 200 individual rename packets in rapid succession.

### Preventative Measures

1. **Coalescing timer** — Collect rename events for 50ms; send a single batch packet
2. **Max rename rate** — No more than 60 rename events per second (configurable via CVar)
3. **Idempotency dedup** — If actor X is renamed from "A" → "B" → "C" within the coalesce window, send only final state ("X → C")
4. **Throttle on overload** — If rename queue exceeds 256 entries, drop intermediate renames and send only latest state per actor
5. **Log warning** — If rename rate exceeds 120/sec, emit UE_LOG(LogLiveSync, Warning) to diagnose spam

---

## 6. Deleted Actor Handling

### Blender-side Delete
- Object deleted in Blender → `PT_DELETE` (0x04) packet sent → UE removes actor
- Current: Immediate removal in ProcessQueuedPackets
- Phase 6: Should use a fade-out or timer to prevent accidental loss (undo window)

### UE-side Delete
- Currently unsupported (UE deletion of Blender-managed actor does not sync back)
- Phase 6: If supported, must:
  1. Confirm user intent (not accidental delete from construction script)
  2. Send `PT_DELETE` packet to Blender
  3. Wait for Blender acknowledgment or timeout
  4. If Blender object re-appears (e.g., user undid delete), re-spawn actor

### Stale GUID Recovery
- Actor was deleted in UE but Blender still thinks it exists
- Detection: On next `PT_TRANSFORM` for a missing actor, either:
  - Re-spawn the actor (current behaviour via RecoverMissingActors)
  - Or if GUID is in a "recently deleted" list, acknowledge the GUID as dead and send PT_DELETE back to Blender
- Phase 6 must implement a **deleted-GUID tombstone** set to prevent re-spawn loops

---

## 7. Stale GUID Recovery

### Tombstone Set
- When a `PT_DELETE` is processed, the GUID is added to a `TombstoneSet<std::string>` (bounded at 1024 entries)
- If the same GUID is received within 30 seconds, the packet is silently dropped (actor was already deleted)
- After 30 seconds, the tombstone expires and a new `PT_CREATE` for that GUID is allowed

### Zombie Detection
- If an actor's base component is lost (Outer destroyed, etc.) but the GUID persists:
  - RecoverMissingActors re-spawns the actor
  - If re-spawn fails 3 consecutive times, the GUID is moved to a dead-GUID list and logged

### Manual Recovery
- Console command `UE.LiveSync.Reset` clears all tracked state (GUIDs, actors, queues)
- Intended for manual recovery when sync state is corrupted
- Warning: Will cause full re-sync from Blender (may need Phase 6 snapshot trigger)

---

## Appendix: Summary of All Safety Rules

| Rule | Category | Enforced | Phase |
|------|----------|----------|-------|
| Managed actor tag check | Filtering | Planned | 6 |
| Change origin tagging | Feedback loop | Planned | 6 |
| Re-entrancy depth counter | Feedback loop | Planned | 6 |
| Flood detection | Rate limiting | Phase 5 | 5 |
| Rename coalescing | Rate limiting | Planned | 6 |
| Tombstone set | Stale GUID | Planned | 6 |
| Transient actor filtering | Filtering | Planned | 6 |
| Undo transaction suppression | Transient | Planned | 6 |
| PIE mode suppression | Transient | Planned | 6 |
| Construction script filtering | Transient | Planned | 6 |
