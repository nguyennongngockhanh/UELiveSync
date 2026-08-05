# MIG-009 — FBX Handoff Hardening (Design)

Status: **Design approved** (2026-08-05, owner decisions locked; implementation may
start with WS-1). This document defines scope and acceptance criteria for hardening the
existing FBX handoff pipeline. It is not an implementation contract and does not change
any code.

Date: 2026-08-05
Owner: Khanh

## 0. Decisions (owner-confirmed 2026-08-05)

1. **Trigger**: manual panel button only for MIG-009. No hotkey, no auto-sync (auto-sync
   pulls in debounce / dirty tracking / undo-redo / batching — UX scope, deferred to a
   separate MIG after the production pipeline is stable).
2. **Manifest fallback**: keep the legacy v2 read as a fallback (v3 → v2 → directory
   scan). Zero-cost, preserves compatibility with old caches; v2 deprecation/removal only
   after all producers emit v3.
3. **Commit strategy**: one commit per workstream (no squash across MIG-009). Squash at
   merge time only if required. Bisect-friendly history.
4. **Implementation order**: WS-1 (manifest) → WS-2 (asset identity) → WS-3 (incremental
   export) → WS-4 (robustness) → WS-5 (PT_Mesh decision). Incremental export depends on
   identity stability; wrong identity would corrupt the geometry-hash cache.
5. **Runtime latency target** (MIG-009 KPI): one complete mesh sync — mesh edit → FBX
   export → send → import → actor updated — must complete in **< 1 second** (stretch goal
   **< 500 ms**). Measured on the runtime checklist; documented per workstream.

## 1. Context and Naming

"Phase 1.5" in this repo currently refers to **Legacy Protocol Elimination**, which is
**COMPLETE** (ADR-81). The work below is a new capability phase: **production mesh sync
hardening** on top of the already-shipped FBX handoff vertical slice.

A fresh audit (2026-08-05, `fbx-handoff-pipeline-audit.md` + runtime evidence) shows the
vertical slice already works end-to-end. MIG-009 therefore **hardens** the existing
pipeline; it does not re-invent it. Writing design docs for "export → import → preserve
actor" would duplicate existing implementation and drift from reality.

## 2. Baseline (already exists — evidence)

| Step | Exists | Evidence |
|---|---|---|
| Blender operator "Sync Selected Mesh to UE (FBX)" | Yes | `Blender_Addon/__init__.py:2052-2077` (`UELIVESYNC_OT_sync_selected_mesh_to_ue_fbx`) |
| Per-object FBX export to `~/.cache/uelivesync/fbx/<guid>/<name>.fbx` | Yes | `Blender_Addon/__init__.py:749-935`, path `__init__.py:2142-2181` |
| Export settings (local pivot, apply scale units, selection only) | Yes | `__init__.py:891-902` |
| Manifest v3 write (`manifest_v3.json`) | Yes | `Blender_Addon/manifest_v3.py:1189`, `MANIFEST_V3_FILENAME` `:192` |
| Manifest-informed sidecar reuse / pruning | Yes | `manifest_reuse.py:1-75`, `manifest_prune.py:1-70` |
| TCP `FBX_IMPORT_REQUEST` (0x60) emission | Yes | `Blender_Addon/fbx_protocol.py:42-91`, opcode `msg_transport.py:80` |
| UE receive + stale rejection | Yes | `UELiveSyncSubsystem.cpp:8939-8965` (`OnFbxImportRequest`, `GFbxImportSequences`) |
| UE import to `/Game/UELiveSync/Imported` | Yes | `LiveSyncFBXImporter.cpp:1314-1490` (AssetImportTask + UFbxFactory) |
| Actor spawn/update, `LiveSync_GUID=` tag, transform preserved | Yes | `LiveSyncFBXImporter.cpp:2312-2665` (scale=(1,1,1) invariant `:2613-2620`) |
| Geometry-hash coalesce/dedup (UE side) | Partial | `LiveSyncFBXImporter.cpp:1331-1437` (`GSemanticSignatureCache`, `FBXImportSkipped`) |

## 3. Gap Analysis (evidence-driven)

| Gap | Evidence | Impact |
|---|---|---|
| G1. UE reads legacy manifest v2; Blender writes v3 | `LiveSyncFBXImporter.cpp:1531-1559` reads `<MeshName>.manifest.json`; zero `manifest_v3` matches in UE_Plugin; runtime `SIDECAR_MANIFEST_NOT_FOUND` `:1625` | Manifest read is dead; sidecar texture resolution broken (`ADR-70:139 expected=12 resolved=0`) |
| G2. No incremental export gate | `__init__.py:2384-2402` geometry-hash is log-only; `_last_geometry_version` overwritten before compare (`:2369-2377`) | Every button press re-exports and re-sends; only UE coalesces |
| G3. Robustness unverified | No dedicated handling found in FBX path | rename / duplicate / shared mesh / multi-material / undo-redo risk |
| G4. Asset identity consistency unverified | Identity hops (Object → Mesh → FBX → Manifest → UE Asset → Actor) never tested as a chain | rename/duplicate/reimport may break mapping |
| G5. PT_Mesh (0x06) decision pending | ADR-81 `:84-85` "await the FBX handoff mesh path" | 0x06 is the only working non-FBX mesh channel; decommission decision open |

## 4. Workstreams

### WS-1 — Manifest v3 compatibility (fix G1) — HIGHEST VALUE

Goal: UE consumes the v3 manifest that Blender writes; sidecar texture resolution works.

- Add v3 manifest read to the UE importer (schema `manifest_v3.py:10-19`:
  `schemaVersion:3`, `guid`, `generation`, `semanticContentDigest`, `occurrences[]`,
  `assets[]`).
- Resolve sidecar textures via `occurrences[].assetId` / `assets[].destinationBasename` +
  `destinationHash`; keep the existing directory-scan fallback.
- **Keep the legacy v2 `.manifest.json` read as a fallback** (Decision 2): lookup order is
  v3 manifest → v2 manifest → directory scan. Do NOT remove the v2 path in MIG-009; only
  deprecate it later once every producer emits v3.
- Must NOT change the 0x60 wire format.

Acceptance (runtime): a real material + texture scene syncs via FBX; UE logs manifest
found (not `SIDECAR_MANIFEST_NOT_FOUND`); textures imported
(`resolved == expected`, no `import_assets_returned_zero`); mesh + actor update
unchanged.

### WS-2 — Asset identity consistency (close G4)

Goal: identity is stable across the full chain
`Blender Object → Mesh datablock → FBX → Manifest → UE Asset → Level Actor`.

- Define the identity key at each hop (GUID from `ensure_guid`; content hash; asset
  identity from MIG-007 `OBJECT_ASSET_IDENTITY`).
- Rename/duplicate/reimport must not silently re-target the wrong UE asset/actor.
- Cross-check vs MIG-007/ADR-82 semantics; document the canonical mapping.
- Runs BEFORE incremental export: the geometry-hash gate is only trustworthy when
  identity is already stable.

Acceptance (runtime): rename object → same UE asset updated in place; duplicate → a
distinct asset/actor; no cross-wiring.

### WS-3 — Incremental export (fix G2)

Goal: only export/send when the selected mesh actually changed.

- Fix the ordering bug: compute `send_fbx` decision against the PREVIOUS stored hash,
  then update `_last_geometry_version` after the decision.
- Gate export + TCP send on geometry change (skip when hash unchanged).
- Keep the manifest-durability gate as the secondary condition.
- Depends on WS-2 (identity stable before hashing).

Acceptance (runtime): press the button twice with no mesh edit → second press performs no
FBX export and no send; edit mesh → re-export + send; unchanged behavior for changed mesh.

### WS-4 — Robustness matrix (close G3)

Verify and fix (as discovered):

- Mesh rename
- Object duplicate
- Two objects sharing one mesh datablock
- Multiple material slots on one mesh
- Undo / redo

Each scenario: expected behavior defined, run through the runtime checklist, fixed if
broken. Output: a verification matrix in this ADR's update.

### WS-5 — PT_Mesh (0x06) decision (close G5)

- If the FBX handoff covers every production mesh case → start the decommission
  (per ADR-81 criteria: semantic parity + runtime acceptance + emitter switched to
  semantic-only).
- Otherwise enumerate the cases that still need 0x06.
- Output: a dedicated ADR (decision), no code in this workstream.

## 5. Original design questions — resolved vs open

| Question | Status |
|---|---|
| Trigger sync như thế nào? | Resolved (Decision 1): manual panel button (`__init__.py:3430`) only. Hotkey / auto on mesh change deferred to a separate MIG. |
| Export sang đâu? | Resolved: `~/.cache/uelivesync/fbx/<guid>/<name>.fbx`. |
| Mapping Blender Object ↔ UE Asset? | Resolved (GUID tag + manifest); consistency hardening in WS-2. |
| Khi nào Reimport? | Resolved: on button press; incremental gating in WS-3. |
| Làm sao giữ Actor? | Resolved: spawn/update path preserves actor (`LiveSyncFBXImporter.cpp:2312-2665`). |
| Failure recovery? | Partial: prune + retry exist; WS-1/WS-4 harden it. |
| Cần protocol mới không? | No new packet. 0x60 stays; 0x06 decision in WS-5. |

## 6. Sequencing (Decision 4)

1. WS-1 — Manifest v3 compatibility (runtime-broken sidecar lane — highest value)
2. WS-2 — Asset identity consistency (identity must be stable before hashing)
3. WS-3 — Incremental export (depends on WS-2)
4. WS-4 — Robustness matrix (verification; fixes fed back into WS-1..3 code)
5. WS-5 — PT_Mesh decision (ADR, last — depends on WS-1..4 results)

Each workstream: audit → contract → implement → build → runtime regression → ADR →
commit. Commits are per-workstream (Decision 3), each with its own conventional message.

## 7. Risks and Mitigations

- **Regressing a working pipeline**: keep the existing spawn/update path untouched;
  changes isolated to manifest read + send gate. Scale-unit regression guard already
  exists (`GBoundsExtentCache` ratio `LiveSyncFBXImporter.cpp:2191-2289`).
- **Wire-format drift**: 0x60 layout frozen for this MIG.
- **Identity regressions**: WS-2 runs before WS-3 and any 0x06 decommission.

## 8. Decisions Record

All owner decisions for MIG-009 are captured in **Section 0** (2026-08-05) and applied
throughout: manual trigger only, v3→v2→directory-scan manifest fallback, per-workstream
commits, WS-2-before-WS-3 ordering, and the `< 1 s` (stretch `< 500 ms`) latency KPI.
No open questions remain at design level.
