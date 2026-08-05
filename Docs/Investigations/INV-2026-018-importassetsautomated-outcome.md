# INV-2026-018 — Verify Actual Outcome of `ImportAssetsAutomated` (Sidecar Texture Import)

**Status**: CLOSED
**Priority**: P1 (blocks MIG-009 WS-1 acceptance)
**Owner**: Khanh
**Started**: 2026-08-05
**Closed**: 2026-08-05 (Bug B canonical-name mismatch fixed + verified PASS)

## Context

MIG-009 WS-1 (manifest v3 sidecar resolution, `LiveSyncFBXImporter.cpp`) runtime test
TRY-2 (boundary 08.15.21:44, conn=3, GUID `457BA725DE544FF7800A24FF0BD212EA`) produced a
contradiction:

- Runtime log: `ImportAssetsAutomated` returned **0** objects
  (`SIDECAR_TEXTURE_IMPORT_FAIL reason=import_assets_returned_zero`,
  `SIDECAR_RESULT_MAP_READY expected=12 resolved=0 missing=12 mismatched=0 duplicates=12`).
- No `LogFactory: FactoryCreateFile` for any PNG (only one for the StaticMesh).
- No `.uasset` texture on disk; `/Game/UELiveSync/Imported/Textures` folder absent.
- Tester observation: textures **did** appear in the UE Content Browser during run 2.

The runtime log and the tester observation contradict each other.

## Hypotheses

### A — Textures genuinely never imported
The import call produced zero assets; Content Browser observation was misattributed
(some other asset / transient UI).

### B — Textures imported in-memory but never saved
Assets were created in memory (Content Browser shows them) via a path that does not
populate the synchronous return value, and packages were never saved to disk. After UE
restart the assets are gone — consistent with no `.uasset` on disk.

### C — Import routed elsewhere / silently failed inside a path not reflected in return or FactoryCreateFile

## Engine source analysis (read-only)

- `ImportAssetsAutomated` → `UAssetToolsImpl::ImportAssetsInternal` with default
  `FAssetImportParams` (bAllowAsyncImport=false), AssetTools.cpp:2940-2952.
- Interchange branch: `ImportAssetWithResult` (sync, `bRunSynchronous=true`,
  InterchangeManager.cpp:1562-1568), return array populated from
  `ImportStatus->ImportedObjects` when `!bAllowAsyncImport`, AssetTools.cpp:3979-3987.
- Inference: a **synchronous** successful import must populate the returned array.
  Zero return therefore weakens hypothesis B but does not eliminate it (the sync
  Interchange import may still have produced no objects; the UFactory path is excluded
  because no `FactoryCreateFile` was logged).

## Experiment plan

### EXP-A (current) — Instrument the `ImportAssetsAutomated` call site

Variable changed: temporary logging only (no behavior change).

Observation points (all within `LiveSyncFBXImporter.cpp`, `#if WITH_EDITOR`):

1. `[INV-2026-018][PRE]` — file count, destination path, `bReplaceExisting`.
2. `[INV-2026-018][POST]` — `NewImportedTexs.Num()` and each returned object's class + path.
3. `[INV-2026-018][REGISTRY]` — `IAssetRegistry::GetAssetsByPath` under
   `/Game/UELiveSync/Imported/Textures` after the call (includes in-memory assets).
4. `[INV-2026-018][STATE]` — per expected object: `StaticFindObject` exists, package
   `IsDirty()`, and on-disk `.uasset` existence.

Expected results:
- If A: `POST returned=0`, `REGISTRY assets=0`, all `STATE exists=0 onDisk=0`.
- If B: `POST returned=0` but `REGISTRY assets=12` (in-memory), `STATE exists=1 dirty=1
  onDisk=0`.
- If a different import path was taken, `POST returned>0` but canonical-name matching
  failed → `REGISTRY assets=12`, `STATE exists=1`.

Rollback: `git restore` the single instrumentation hunk; temporary annotation
`TODO(INV-2026-018)`.

### Subsequent experiments (if needed)

- EXP-B: force Interchange off (CVar) to isolate path — requires confirmation of the
  exact CVar; not started.
- EXP-C: save packages after import (behavior change) — only after root cause proven.

## Invariants (unchanged)

- Wire protocol 0x60 frozen.
- Sidecar import lane logic untouched (logs only).
- No Blender addon / adapter / protocol change.

## Outcome to date

- 2026-08-05: INV opened. Engine source read (AssetTools.cpp 2940-2952, 3567-3570,
  3979-3987; InterchangeManager.cpp 708-786, 1547-1574). Instrumentation patch pending
  user approval.

## EXP-A RESULT (2026-08-05, TRY-4, boundary 15:52:07, conn=2, guid `08596465`)

Instrumentation confirmed:

- `[INV-2026-018][POST] returned=78` — 78 objects returned by `ImportAssetsAutomated`,
  resolving to **12 unique Texture2D** assets (duplicate references in the return array).
- `[INV-2026-018][REGISTRY] assets=12` — all 12 registered under
  `/Game/UELiveSync/Imported/Textures` (in-memory).
- Disk: no `Textures/` folder and no `.uasset` → assets exist **in memory only**,
  never saved to disk.
- `SIDECAR_RESULT_MAP_READY resolved=0 missing=12` despite successful import.

### Conclusions

- **Hypothesis A (textures never imported): ELIMINATED.** Textures ARE imported.
  The tester's Content Browser observation was correct.
- **Hypothesis B (in-memory, not saved): CONFIRMED** for the persistence part —
  assets are in-memory only, disappear after restart (no disk evidence).
- **NEW ROOT CAUSE found:** the `import_assets_returned_zero` / `resolved=0` result is
  NOT an import failure. It is a **canonical-name mismatch** in the per-GUID result
  matching: the source base filename keeps dots
  (`wooden_basecolor.png__d10cc716c14ee9ca`) while UE sanitizes dots → underscores in
  the created asset name (`wooden_basecolor_png__d10cc716c14ee9ca`). The importer's
  `Canonical = FPaths::GetBaseFilename(SourceFile).ToLower()` never equals
  `Obj->GetName().ToLower()` → all 12 `no_matching_asset` → `EffectiveTexCount=0` →
  misleading `import_assets_returned_zero` log.

### Evidence log (TRY-4)

```
[INV-2026-018][POST] returned=78
[INV-2026-018][REGISTRY] root=/Game/UELiveSync/Imported/Textures assets=12
[INV-2026-018][RETURNED] class=Texture2D path=/Game/UELiveSync/Imported/Textures/wooden_basecolor_png__d10cc716c14ee9ca.wooden_basecolor_png__d10cc716c14ee9ca
[FBX][SIDECAR_RESULT_MISMATCH] source=.../wooden_basecolor.png__d10cc716c14ee9ca.png key=wooden_basecolor.png__d10cc716c14ee9ca reason=no_matching_asset
```

### Open questions (next investigation)

1. Canonical-name matching must apply the same sanitization as UE's import naming
   (`ObjectTools::SanitizeObjectName`: dots → underscores) so resolved=12.
2. Whether sidecar textures (and the StaticMesh) should be **saved to disk** — the
   whole import lane is currently in-memory only (assets lost on restart). Needs a
   product decision (design intent vs bug).

## BUG B FIX + VERIFICATION (2026-08-05)

**Fix** (`LiveSyncFBXImporter.cpp`): added helper `CanonicalSidecarTextureName(SourceFile)`
= `SanitizeObjectName(FPaths::GetBaseFilename(SourceFile)).ToLower()` — the exact asset
name UE assigns on import (dots → underscores). Replaced the raw `GetBaseFilename` keys at
4 sites: object-path construction for existing-texture lookup, the INV-2026-018 state
lookup, the `ImportedByCanonicalName` map key, and the result verification key.

**Verification** (fresh session, UE PID 117534, Blender PID 119354, boundary 16:26:01,
GUID `80EC5D6F574D484984BFD9782D8414ED`, export 13.6 s):

```
[FBX][SIDECAR_RESULT_MAP_READY] expected=12 resolved=12 missing=0 mismatched=0 duplicates=0
[FBX][IMPORTED_ASSET_SUMMARY] textures=12 (after_sidecar)
[INV-2026-018][STATE] ... objectPath=/Game/UELiveSync/Imported/Textures/wooden_basecolor_png__d10cc716c14ee9ca... exists=1 dirty=1 onDisk=0
[MATERIAL][PERSISTENT_MIC_CHANNEL] slot=0 channel=[BaseColor] action=set_texture texture=.../wooden_basecolor_png__d10cc716c14ee9ca... useAfter=1.0
[MATERIAL][PERSISTENT_MIC_CHANNEL] slot=0 channel=[Roughness] action=set_texture texture=.../wooden_roughness_png__db0a14a654add5e1... useAfter=1.0
[MATERIAL][PERSISTENT_MIC_CHANNEL] slot=1 channel=[BaseColor] action=set_texture texture=.../sagegreenvelvet_basecolor_png__b0ab341f53413712... useAfter=1.0
[MATERIAL][PERSISTENT_MIC_CHANNEL] slot=1 channel=[Roughness] action=set_texture texture=.../sagegreenvelvet_roughness_png__58a39628f348ff34... useAfter=1.0
[MATERIAL][PERSISTENT_SLOT_OK] slot=0 textures_applied=2 misses=0
[MATERIAL][PERSISTENT_SLOT_OK] slot=1 textures_applied=2 misses=0
[MATERIAL][MATX_FULL_SNAPSHOT_APPLY] effectiveSlots=2 meshSlots=2 appliedSlots=2 persistent=2 mid_fallback=0 texturesApplied=4 textureMisses=0
```

No `import_assets_returned_zero`, no `no_matching_asset`, no `textureMisses>0`. Result:
**PASS** — textures match, register in the per-GUID sidecar map, and bind to material
BaseColor/Roughness slots (acceptance "textures bind material" reached).

**Open question 1 RESOLVED** by this fix. **Open question 2** (in-memory only, not saved
to disk — assets lost on restart) remains a product decision, out of scope for WS-1.

