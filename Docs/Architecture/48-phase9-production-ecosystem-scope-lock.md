# Phase 9 — Production Ecosystem (Scope Lock)

## 1. Purpose

Phase 9 transitions UELiveSync from a feature-complete development tool to a
production-ready plugin ecosystem. The protocol and core sync loop are already
implemented across Phases 1–8. Phase 9 adds the surrounding infrastructure
that makes the tool reliable, discoverable, configurable, and recoverable in
real studio workflows.

**Production-readiness means:**
- Anyone in a studio can install without reading source code
- Blender and UE find each other without manual IP entry
- Version mismatches produce clear messages, not silent failures
- Users can save, load, and share configurations
- Crashes on either side recover without restarting the scene
- Support bundles contain everything needed for debugging
- The tool feels polished, not prototypical

## 2. Current Baseline (End of Phase 8)

### What Phase 8 Already Provides

| Area | Status |
|------|--------|
| Transform streaming (V3+) | Working, 60 Hz, backpressure-aware |
| Object identity (PT_Create/Delete) | Working, GUID-based |
| Mesh streaming (PT_Mesh, zlib compression) | Working, off by default |
| Material streaming (PT_Material) | Working |
| Semantic events (rename, visibility, hierarchy, collection) | Working |
| Backpressure ACK (0x10) | Working, off by default |
| Queue diagnostics | DumpState section + periodic health log |
| Mesh orphan timeout | CVar-gated, 30s default |
| Dirty-flag interest management | Working in Blender |
| Adaptive throttle | Working (Blender timer adapts to ACK) |
| Network thread | MPSC queue, drop-oldest overflow |
| Heartbeat / disconnect detection | 5s Blender → 15s UE timeout |

### What Is Still Deferred or Blocked

| Item | Reason |
|------|--------|
| Stage 2.4 thread offload | Deferred — UE thread-safety risk outweighs benefit |
| Full 500-object / 60s stress run | Blocked by pre-existing UE engine crash (NVIDIA RTX 5080) |
| Performance baseline (EMA rates, queue depth under load) | Requires stable editor session |
| Compression ratio measurement | Requires stable editor + mesh send |

### Known Limitations

| Limitation | Impact |
|------------|--------|
| No installer — must be cloned from git | High entry barrier for non-developers |
| Manual IP/port entry required | Friction for artists |
| No version negotiation — CVar-gated features only | Silent incompatibility |
| Blender preferences exist but are partial | Missing Phase 8 CVars (compression, ACK, mesh timeout) |
| No preset system | Each user must configure manually |
| No crash recovery on Blender side | Lost state on restart |
| No UE-side crash recovery beyond reconnect | Orphaned actors remain |
| No diagnostics export | Debugging requires console access |
| Editor widget is basic | No status, no reconnect button, no stats |

## 3. Phase 9 Lanes

### A. Installer / Packaging

**Problem:** The current install requires `git clone` + manual copy of plugin
files. Non-developer users cannot install without CLI knowledge.

**Proposed solution:**
- Blender addon: standard `.zip` package with `bl_info` version
- UE plugin: `.uplugin` + source, distributable as a zip or via UE marketplace
- Optionally: one-command install script for developers
- Versioned releases with release notes

**Files likely involved:**
- `Blender_Addon/__init__.py` (bl_info already exists)
- New: `scripts/install_blender_addon.sh`
- New: `scripts/install_ue_plugin.sh`
- New: `RELEASE_NOTES.md`

**Dependencies:** None

**Risks:** Low

**Validation:** Manual install test on clean Blender + UE

**Non-goals:**
- Package manager integration (apt, pip)
- UE marketplace submission (out of scope for now)

### B. Auto-discovery / Connection UX

**Problem:** User must type IP and port manually on both sides. No feedback
if the address is wrong.

**Proposed solution:**
- Default to `127.0.0.1:57000` (localhost) — zero-config for local sessions
- Blender: add a "Scan for UE instances" button that probes common ports
- UE: display listener address in editor status widget
- UE: optional UDP broadcast beacon on a secondary port for discovery

**Files likely involved:**
- `Blender_Addon/network.py` (connection logic)
- `Blender_Addon/__init__.py` (UI)
- `UELiveSyncSubsystem.cpp/h` (beacon sender)
- `UELiveSyncEditor/` (status widget)

**Dependencies:** None for localhost. UDP broadcast is additive.

**Risks:** Low (localhost is safe). UDP broadcast may be blocked on some
networks — fall back to manual entry.

**Validation:**
- Click "Scan" → discover local UE instance within 2s
- Connect via localhost without any configuration
- Manual override still works

### C. Version Compatibility Layer

**Problem:** There is no version negotiation. If Blender sends a V5 packet
that a V3 UE receiver doesn't understand, it's silently dropped or corrupts
state.

**Proposed solution:**
- Handshake on connect: Blender sends `PT_VersionAnnounce = 0x01` (or reuse
  reserved type) with its protocol version and feature flags
- UE responds with its supported version range and feature mask
- If incompatible, both sides log a clear error message
- Blender degrades gracefully: if UE doesn't support compression, don't set
  the flag bit
- Feature flags bitmap (32-bit, extensible):
  - Bit 0: Mesh compression supported
  - Bit 1: Backpressure ACK supported
  - Bit 2: Dirty-flag interest management
  - Bits 3–31: Reserved for future use

**Files likely involved:**
- `Blender_Addon/network.py` (handshake send + response parsing)
- `Blender_Addon/sync.py` (capability tracking)
- `SyncTypes.h` (feature flag constants)
- `UELiveSyncSubsystem.cpp` (handshake receive + response)

**Dependencies:** Protocol extension (new handshake packet type)

**Risks:**
- Medium: Handshake adds latency to first connection (~1 round trip)
- Low: Old Blender / old UE without handshake → no capability info available
  → fall back to lowest-common-denominator (no compression, no ACK)

**Validation:**
- V5 Blender + V3 UE → UE ignores handshake, Blender falls back to V3
- V5 Blender + V5 UE → full capability exchange
- Old Blender + new UE → no handshake sent, UE treats as baseline

### D. UI Polish

**Problem:** Blender UI is functional but sparse. UE editor widget shows
basic info but has no controls. No diagnostics viewer.

**Proposed solution:**
- Blender panel improvements:
  - Connection status indicator (green/red/yellow)
  - Start/Stop button with state-aware label
  - Statistics panel (packets sent, bytes, queue depth, compression ratio)
  - Error log viewer (last N errors, clickable)
- UE editor widget improvements:
  - Connection status (connected/disconnected, client address)
  - Queue depth gauge
  - Packet rate display
  - Reconnect/kick button
  - CVar quick-toggles (compression, ACK, verbose)

**Files likely involved:**
- `Blender_Addon/__init__.py` (panel layout)
- `Blender_Addon/sync.py` (status data)
- `UELiveSyncEditor/` (UE widget C++)

**Dependencies:** None

**Risks:** Low

**Validation:** Visual inspection, no UI tests required

### E. Preset System

**Problem:** Every sync session requires re-entering thresholds, port,
compression settings, and log preferences. No "save config" exists.

**Proposed solution:**
- Blender: save/load presets as JSON files
- Preset fields:
  - Connection: host, port, auto-discovery
  - Thresholds: location, rotation, scale
  - Performance: mesh compression, backpressure ACK, scan interval
  - Diagnostics: verbose logging, log level, [DIRTY] cadence
- UE: read preset from command-line flags or ini file
- Default preset: "Local Development" (localhost, all off)

**Files likely involved:**
- `Blender_Addon/network.py` (preset load/save helpers)
- `Blender_Addon/__init__.py` (UI preset dropdown)
- `UELiveSyncSubsystem.cpp` (command-line CVar parsing)

**Dependencies:** None

**Risks:** Low (JSON is local file, no network)

**Validation:**
- Save preset → restart Blender → load preset → settings restored
- Export preset → share with another user → import works

### F. Project Templates

**Problem:** Setting up a new project requires copying the uplugin, creating
a minimal .uproject, and configuring both sides. No "start here" path.

**Proposed solution:**
- Blender: "New Sync Project" operator that creates:
  - A minimal .blend with tracked_objects initialised
  - A .uproject file with the plugin enabled
  - A README with setup instructions
- UE: Template .uproject as part of plugin distribution

**Files likely involved:**
- `Blender_Addon/__init__.py` (operator)
- `UE_Plugin/UELiveSync/Templates/` (template files)

**Dependencies:** Low

**Risks:** Low

**Validation:** Operator produces a functional project

### G. Crash Recovery / Session Recovery

**Problem:** If Blender crashes, UE is left with stale actors. If UE crashes,
Blender keeps sending to a dead socket. On reconnect, state is inconsistent.

**Proposed solution:**

**Blender crash / UE disconnect recovery:**
- Session GUID: assigned on start_sync, included in heartbeat
- On reconnect: if session GUID changed (Blender restart), UE deletes all
  actors from the old session and re-creates from the reconnection snapshot
- Stale actor cache cleanup: actors with no heartbeat for >30s are deleted
  (already partially implemented)

**UE crash recovery:**
- Blender detects disconnect (socket error on send)
- Exponential backoff reconnect: 1s, 2s, 4s, 8s, max 30s
- On reconnect: send full snapshot (already implemented)
- Session GUID changes → old actors cleaned up

**Pending mesh recovery:**
- On disconnect, orphaned `PendingMeshReassembly` entries are cleared
  (the 30s timeout handles this, but on reconnect it should be immediate)

**Files likely involved:**
- `Blender_Addon/sync.py` (session GUID, reconnect backoff)
- `Blender_Addon/network.py` (reconnect logic)
- `UELiveSyncSubsystem.cpp` (session GUID tracking, actor cleanup on
  session change)

**Dependencies:** Session GUID constant, heartbeat extension

**Risks:** Medium — clearing old actors on session change must be careful
not to delete actors that the user created independently in UE.

**Validation:**
- Kill Blender → wait → restart → verify old actors removed, new ones created
- Kill UE → wait for Blender reconnect → verify snapshot re-sent
- Kill both → restart Blender first → start UE → verify connection

### H. Diagnostics Export / Support Bundle

**Problem:** Debugging issues requires asking users to copy-paste console
output. No structured diagnostics export exists.

**Proposed solution:**
- Blender "Export Diagnostics" button:
  - Captures: addon version, Blender version, Python version, OS
  - Captures: preset file, last N log lines, runtime stats
  - Captures: tracked objects count, sync state
  - Outputs: timestamped .zip
- UE "Export Support Bundle" console command:
  - Captures: plugin version, UE version, CVar values, Stats counters
  - Captures: queue depth, connected client, session uptime
  - Captures: `DumpState` output, last N log lines
  - Outputs: saved to Project/Saved/SupportBundle/

**Files likely involved:**
- `Blender_Addon/__init__.py` (button + operator)
- `Blender_Addon/sync.py` (diagnostics collection)
- `UELiveSyncSubsystem.cpp/h` (support bundle writer)

**Dependencies:** Python stdlib `zipfile` module

**Risks:** Low

**Validation:**
- Click "Export Diagnostics" → .zip file produced with expected contents
- Run `UE.LiveSync.DumpSupportBundle` → files appear in Saved directory

## 4. Recommended Stage Plan

| Stage | Scope | Est. Effort |
|-------|-------|-------------|
| **0** | Audit + docs (this document) | 0 days |
| **1** | UI polish + preferences wiring | 2–3 days |
| **2** | Version compatibility layer + handshake | 2–3 days |
| **3** | Auto-discovery (localhost + optional UDP) | 1–2 days |
| **4** | Preset system | 1–2 days |
| **5** | Crash / session recovery | 2–3 days |
| **6** | Diagnostics export / support bundle | 1–2 days |
| **7** | Installer / packaging | 1 day |
| **8** | Production validation + regression | 2–3 days |

**Total estimated effort:** 10–18 days.

## 5. Protocol / Version Policy

### When to Bump Protocol Version

Bump the `LIVE_SYNC_VERSION` only when:
- The packet header layout changes (field added/removed/resized)
- An existing packet type's payload format changes incompatibly
- The magic number changes

Do NOT bump for:
- New packet types in the reserved range (0x10–0x1F)
- New flag bits in existing header or payload flag bytes
- Changes that only affect one side (Blender-only logic, UE-only logic)

### Feature Flags

A 32-bit `CapabilityFlags` exchanged during handshake (Stage 2):

| Bit | Feature | When Used |
|-----|---------|-----------|
| 0 | Mesh compression | Blender sets if it can compress; UE sets if it can decompress |
| 1 | Backpressure ACK | UE sets if it can send ACKs |
| 2 | Dirty-flag interest mgmt | Blender sets if it marks dirty flags |
| 3–31 | Reserved | For future use |

If handshake is not supported by the peer (older version), assume all bits
are 0 — no optional features.

### Old Blender / Old UE Compatibility

| Scenario | Behaviour |
|----------|-----------|
| New Blender + Old UE | No handshake response → Blender assumes no optional features. Falls back to baseline (uncompressed, no ACK). |
| Old Blender + New UE | No handshake sent → UE assumes no optional features. CVar-gated features still work if enabled manually (CVar=1). |
| New Blender + New UE | Full handshake → all supported features enabled. |

## 6. Installer Strategy Comparison

| Option | Complexity | User Experience | Maintainability |
|--------|------------|----------------|----------------|
| **Manual install (current)** | None | Poor (need git + copy) | None |
| **Zip package** | Low (zip repo) | Good (download + Blender Install) | Low (manual build) |
| **One-command install script** | Medium (bash/Python) | Good (terminal) | Medium |
| **Full GUI installer** | High (NSIS/InnoSetup) | Excellent | High |
| **UE marketplace** | Very high | Excellent | Very high |

**Recommendation:** Start with **zip package** for both sides. Blender addon
zip can be installed via Blender's built-in `Install Add-on` UI. UE plugin
zip can be extracted into a project's `Plugins/` folder or Engine's
`Plugins/` folder. Add a one-command install script as a secondary option.
Full GUI installer and UE marketplace are out of scope for Phase 9.

## 7. Auto-discovery Strategy Comparison

| Option | Complexity | Works Offline | Requires Config | Reliability |
|--------|------------|---------------|-----------------|-------------|
| **Manual IP/port (current)** | None | Yes | Yes | 100% |
| **localhost default** | Trivial | Yes | No | 100% (local only) |
| **UDP broadcast** | Medium | Yes (LAN) | No | High (LAN) |
| **mDNS/zeroconf** | High | Yes | No | High but complex |
| **Project-local config file** | Low | Yes | Yes (one-time) | 100% |

**Recommendation:** Implement in order:
1. Default to `127.0.0.1:57000` (already partially done — port is hardcoded)
2. Blender "Scan for UE" button using TCP connect probes on common addresses
3. Optional UDP broadcast beacon from UE for automatic discovery

## 8. Preset Strategy

Presets are JSON files stored in the Blender addon's config directory:

```
~/.config/blender/4.5/scripts/addons/uelivesync/presets/
  ├── default.json
  ├── low-latency.json
  └── high-quality.json
```

Each preset contains:

```json
{
  "name": "Local Development",
  "connection": {
    "host": "127.0.0.1",
    "port": 57000,
    "auto_discover": true
  },
  "thresholds": {
    "location": 0.01,
    "rotation": 0.0001,
    "scale": 0.001
  },
  "performance": {
    "mesh_compression": false,
    "backpressure_ack": false,
    "scan_interval": 300
  },
  "diagnostics": {
    "verbose_logging": false,
    "dirty_log_interval": 10.0
  }
}
```

UE side reads equivalents from command-line CVars or an optional ini file.

## 9. Crash Recovery Strategy

### Reconnect Policy (Blender)

```
On send error → mark disconnected → _reconnect_internal()
  backoff: 1s, 2s, 4s, 8s, 16s, 30s (cap)
  On success → clear backoff → send full snapshot
  Log each reconnect attempt
```

Already partially implemented. Needs:
- Exponential backoff (currently immediate reconnect)
- Cap at 30s
- Session GUID generation and inclusion in heartbeat

### Stale Actor Cache Cleanup (UE)

```
Each tick:
  for each actor in ActorCache:
    if actor LastHeartbeatTime + HEARTBEAT_TIMEOUT < Now:
      if actor.SessionGUID != CurrentSessionGUID:
        destroy actor
        remove from ActorCache
```

Already partially implemented (heartbeat timeout exists). Needs:
- Session GUID tracking
- Session-aware cleanup (differentiate actor-per-session from UE-native)

### Pending Mesh Cleanup (UE)

On disconnect or session change:
- Clear all `PendingMeshReassembly` entries (immediate, no 30s timeout)

### Session GUID

A random UUID generated by Blender on `start_sync()`. Included in heartbeat
packet. Stored in UE's `CurrentSessionGUID`. On mismatch, old-session actors
are cleaned up.

## 10. Validation Plan

| Test Type | Scope |
|-----------|-------|
| Unit tests | Preset load/save/validate, version parsing, capability bit math |
| Standalone | Existing Phase 7C tests (must remain 135/135 PASS) |
| Runtime (local) | localhost connection, preset load, handshake roundtrip |
| Runtime (LAN) | UDP discovery, reconnect across subnet |
| Compatibility | V3+V4+V5 cross-version, with/without handshake |
| Packaging | Zip install on clean Blender + clean UE |
| Recovery | Kill Blender → restart → verify actor cleanup. Kill UE → restart → verify snapshot |
| Stress | Re-run Phase 8 stress harness — target: stable 60s with 500 objects |
| Upgrade | Install v0.1 → upgrade to v0.2 → verify presets survive |
| Downgrade | Install v0.2 → downgrade to v0.1 → verify graceful fallback |

## 11. Non-Goals

| Feature | Reason |
|---------|--------|
| Sequencer / animation work | Different data model (keyframes, curves, timing) |
| Thread offload (Stage 2.4) | Deferred — UE thread-safety risk |
| Full cloud sync | Requires server infrastructure, authentication, conflict resolution |
| External dependency-heavy installer | Python GUI installer (NSIS/InnoSetup) is out of scope |
| Omniverse-like asset database | Out of scope for a sync tool |
| Multi-client support | Single Blender → single UE only |
| UE marketplace submission | Legal/packaging complexity out of scope |
| CI/CD pipeline | Would speed development but is not a user-facing feature |

## 12. Final Recommendation

**Start with Stage 1: UI polish + preferences wiring.**

Rationale:
- UI polish is the most visible improvement for the least effort (~2–3 days)
- Stage 1 does not require protocol changes — safe to implement immediately
- Wiring Phase 8 CVars (compression, ACK, mesh timeout) into the Blender
  preferences panel closes a known gap (features exist but can't be configured
  from the UI)
- The preference system is a dependency for Stage 4 (presets) — getting it
  right now makes presets easier later

Stage 1 scope:
- Add Blender preference toggles for: mesh compression, backpressure ACK,
  mesh compression stats display, dirty-flag stats display
- Wire `set_mesh_compression()` to the preferences toggle
- Add connection status indicator (green/yellow/red dot)
- Add packet/byte count display in the Blender panel
- UE side: add CVar quick-toggles to the editor widget

**Stage 1 Complete (2026-06-01):**
- Blender preferences added: `mesh_compression` (default off, calls `network.set_mesh_compression()`), `backpressure_aware` (default on, calls `network.set_backpressure_aware()`), `verbose_diagnostics` (default off)
- Sidebar panel: shows compression ratio, sync interval, ACK state when connected. Shows compression config when disconnected.
- Backpressure toggle: when disabled, `get_suggested_interval()` returns 0.016 regardless of ACK state.
- UE CVars remain console-only: `UE.LiveSync.MeshCompression`, `UE.LiveSync.EnableBackpressureACK`, `UE.LiveSync.MeshReassemblyTimeoutSec`. No editor widget changes in this stage.

**Stage 2B Complete (2026-06-01):**
- Packet types: `PT_CapabilityAnnounce` (0x11), `PT_CapabilityResponse` (0x12)
- Capability bits: `CAP_SUPPORTS_BACKPRESSURE_ACK`, `CAP_SUPPORTS_MESH_COMPRESSION`, `CAP_SUPPORTS_SESSION_GUID`, `CAP_SUPPORTS_DIRTY_ITERATION`
- Payload structs: `FCapabilityAnnouncePayload` (52 bytes total), `FCapabilityResponsePayload` (40 bytes total)
- Constants documented in `SyncTypes.h` and `network.py`
- No runtime behavior change. No packets sent yet.

**Subsequent stages after Stage 2B:**
- Stage 2C Complete (2026-06-01): session GUID, announce send after connect, 3× retry, timeout → baseline
- Stage 2D Complete (2026-06-01): UE parses PT_CapabilityAnnounce (0x11), stores RemoteCapabilityFlags + SessionGUID, sends PT_CapabilityResponse (0x12) with local caps. Both 0x11/0x12 in kValidTypes.
- Stage 2E Complete (2026-06-01): Blender parses PT_CapabilityResponse (0x12), stores UE caps. `is_compression_effective()` = local AND remote AND pref. `is_backpressure_effective()` = remote AND pref. Serialization and interval functions use effective gates. Cap state in runtime stats. Capability state reset on reconnect.
- Stage 2F Complete (2026-06-01): 37/37 compatibility matrix tests pass in `tests/phase9_stage2f_compat_matrix.py`. 11 scenarios covering all Blender/UE capability combinations, timeout, reconnect, malformed, partial caps.
- Stage 3B Complete (2026-06-01): TCP discovery scan + UI picker. 20/20 tests. Fixed missing `LIVE_SYNC_PROTOCOL_VERSION = 5`.
- Stage 3C (UDP beacon) because
  capability flags affect what auto-discovery should report
- Stage 4 (presets) after Stage 1 (UI) and Stage 2 (capabilities)
- Stage 5 (crash recovery) after Stage 2 (session GUID)
- Stage 6 (diagnostics export) independent — can be done anytime
- Stage 7 (packaging) last — everything else is validated
