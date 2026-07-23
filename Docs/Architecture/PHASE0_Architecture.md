# LiveSync v2 — System Architecture

> **Date:** 2026-07-22
> **Status:** APPROVED
> **Constraint:** PLUGIN ONLY — Zero engine source modifications on UE side.

---

## Table of Contents

1. [Constraints & Rules](#1-constraints--rules)
2. [Research Summary](#2-research-summary)
3. [System Overview](#3-system-overview)
4. [Directory Structure](#4-directory-structure)
5. [Networking Protocol](#5-networking-protocol)
6. [Serialization](#6-serialization)
7. [Session State Machine](#7-session-state-machine)
8. [Threading Model](#8-threading-model)
9. [Object Identity Model](#9-object-identity-model)
10. [Asset Identity Model](#10-asset-identity-model)
11. [Update Pipeline](#11-update-pipeline)
12. [State Ownership](#12-state-ownership)
13. [Conflict Resolution](#13-conflict-resolution)
14. [Reconnect Strategy](#14-reconnect-strategy)
15. [Feature Design](#15-feature-design)
16. [Risk Analysis](#16-risk-analysis)
17. [Phase Roadmap](#17-phase-roadmap)
18. [Testing Strategy](#18-testing-strategy)

---

## 1. Constraints & Rules

### Hard Rules (Absolute)

**UE Side:**
- **No engine source modifications.** Not in Runtime, Renderer, Launch, Slate, Editor, RenderCore, Scene, or any other engine module.
- **Plugin-only APIs.** Every capability must be demonstrably available through public UE plugin APIs.
- **If a feature requires engine modification:** STOP. Document why, report to user, wait for decision.

**Blender Side:**
- **No Blender source modifications.** Only use public Python API.
- **No C extension dependencies.** Pure Python + stdlib + approved pip packages.
- **Thread safety:** All `bpy.*` calls on main thread only. Background threads via `threading.Thread` + `queue.Queue` + `bpy.app.timers`.

**Shared:**
- **No legacy compatibility.** Do not port old UELiveSync code. Design from scratch.
- **Protocol is independent.** Does not belong to either UE or Blender. Future DCC tools (Maya, Houdini, 3ds Max) can implement the same protocol.

### Design Principles

1. **Stability > Features > Performance** — make it work first, then stable, then fast.
2. **Explicit > Implicit** — every class has a clear single responsibility. No god objects.
3. **Fail-safe** — every network error, parse error, and API failure is handled gracefully. No crashes.
4. **Observable** — every state transition is logged. Every error has context. Every metric is available.
5. **Minimal footprint** — no polling if event-driven works. No allocation if reuse works. No copy if reference works.
6. **Equal peers** — UE plugin and Blender addon are equal implementers of a shared protocol. Neither is "primary."

---

## 2. Research Summary

### 2.1 UE Plugin API Capabilities (All Confirmed — No Engine Mods)

| Capability | API | Confidence |
|---|---|---|
| Runtime mesh creation | `UProceduralMeshComponent::CreateMeshSection()` | HIGH |
| Topology changes (vertex/face count changes) | `CreateMeshSection()` with different-sized arrays (full section replace) | HIGH |
| Vertex-only updates (same topology) | `UpdateMeshSection()` (in-place, lightweight) | HIGH |
| Static mesh at runtime | `UStaticMesh::BuildFromMeshDescriptions()` | HIGH |
| Dynamic material creation | `UMaterialInstanceDynamic::Create()` | HIGH |
| Material parameter update | `SetScalarParameterValue()`, `SetVectorParameterValue()`, `SetTextureParameterValue()` | HIGH |
| Material slot assignment | `Component->SetMaterial(Index, Material)` | HIGH |
| Camera spawn | `World->SpawnActor<ACameraActor>()` | HIGH |
| Camera intrinsics | `UCineCameraComponent::SetCurrentFocalLength()`, `Filmback` property | HIGH |
| Camera active | `PlayerController->SetViewTargetWithBlend(Camera, 0)` | HIGH |
| Actor spawn/destroy | `World->SpawnActor()`, `Actor->Destroy()` | HIGH |
| Actor rename | `AActor::SetActorLabel()` | HIGH |
| Actor hierarchy | `AActor::AttachToActor()`, `DetachFromActor()` | HIGH |
| Viewport redraw (editor) | `GEditor->RedrawAllViewports()` — but **NOT NEEDED** for v2; component changes auto-trigger render update via `MarkRenderStateDirty()` | HIGH |
| CPU throttle suppression | `ShouldDisableCPUThrottlingDelegates` registration | HIGH |

### 2.2 Old UELiveSync Findings

- **Never required engine patches.** All features implemented via public plugin APIs.
- **Architecture problem:** Single 18,500-line class (`UUELiveSyncSubsystem`) as god object.
- **Threading problem:** All packet processing on game thread — bottleneck under load.
- **Protocol problem:** 4 coexisting protocol versions (V2-V5), complex backward compat logic.
- **Dependency problem:** FBX import, Sequencer, LevelSequence — editor-only heavy dependencies.
- **Single connection only.** Multiple Blender instances not supported.

### 2.3 Blender Python API Capabilities

| Capability | API | Confidence |
|---|---|---|
| Bulk vertex access | `mesh.vertices.foreach_get("co", np_array)` | HIGH |
| Bulk UV access | `mesh.uv_layers[i].data.foreach_get("uv", np_array)` | HIGH |
| Bulk normal access | `mesh.loops.foreach_get("normal", np_array)` | HIGH |
| World-space transform | `obj.matrix_world @ v.co` or numpy bulk | HIGH |
| Modifier-applied geometry | `obj.evaluated_get(depsgraph).to_mesh()` | HIGH |
| Change detection | `bpy.app.handlers.depsgraph_update_post` | HIGH |
| Transform-only detection | `DepsgraphUpdate.is_updated_transform` | MEDIUM (false positives possible) |
| Geometry change detection | `DepsgraphUpdate.is_updated_geometry` | HIGH (but cannot distinguish topology vs vertex-only) |
| Material parameter reading | `node.inputs["X"].default_value` | HIGH |
| Texture path extraction | `bpy.path.abspath(image.filepath)` | HIGH |
| Camera data | `cam_data.lens`, `.sensor_width`, `.clip_start`, `.clip_end` | HIGH |
| Persistent IDs | `obj["sync_id"] = uuid_string` (custom property) | HIGH |
| Background threading | `threading.Thread` + `queue.Queue` + `bpy.app.timers` | HIGH |

### 2.4 Networking

- **TCP** is the correct choice for LAN (near-zero loss eliminates HoL blocking concern, simpler than UDP reliability layer, Blender's stdlib supports it directly).
- **`TCP_NODELAY`** is important but UE's `FSocket` does not expose it — we will document this as a known limitation and use large-send batching to mitigate.
- **Length-prefixed framing** is the simplest and most robust binary framing for variable-size messages.

---

## 3. System Overview

### 3.1 Equal Peers

UE plugin and Blender addon are **equal peers** implementing a shared protocol. Neither is "primary." Future DCC tools (Maya, Houdini, 3ds Max) can implement the same protocol.

```
┌──────────────────────────────────────────────────────────────┐
│                    SHARED PROTOCOL                            │
│          (independent artifact, not owned by either side)     │
│                                                               │
│  Schemas, message types, wire format, capability negotiation, │
│  heartbeat, error codes, test vectors, binary examples        │
└──────────────┬───────────────────────────────┬───────────────┘
               │                               │
    ┌──────────▼──────────┐         ┌──────────▼──────────┐
    │    BLENDER ADDON     │         │     UE PLUGIN        │
    │    (Python)          │         │     (C++)             │
    │                      │         │                       │
    │  ┌─ Scene Layer ──┐  │         │  ┌─ Scene Layer ───┐  │
    │  │ Scene Observer  │  │         │  │ MessageRouter   │  │
    │  │ Event Queue     │  │         │  │ MeshManager     │  │
    │  │ Delta Builder   │  │         │  │ MaterialManager │  │
    │  │ ObjectRegistry  │  │         │  │ CameraManager   │  │
    │  └─────────────────┘  │         │  │ HierarchyManager│  │
    │                      │         │  │ ObjectRegistry  │  │
    │  ┌─ Protocol Layer ┐ │         │  └─────────────────┘  │
    │  │ MessageBuilder   │ │   TCP   │                       │
    │  │ Serializer       │◄────────►│  ┌─ Session Layer ──┐ │
    │  └─────────────────┘ │         │  │ Session Manager  │ │
    │                      │         │  │ State Machine    │ │
    │  ┌─ Network Layer ─┐ │         │  └─────────────────┘ │
    │  │ NetworkClient    │ │         │                       │
    │  │ SessionManager   │ │         │  ┌─ Network Layer ──┐│
    │  └─────────────────┘ │         │  │ NetworkServer    ││
    └──────────────────────┘         │  │ MessageQueue     ││
                                     │  └─────────────────┘│
                                     └───────────────────────┘
```

### 3.2 Layered Architecture

Each side has three clean layers. Layers communicate only downward (outbound) or upward (inbound). No layer knows about layers it doesn't directly depend on.

```
                    ┌─────────────────────┐
                    │    SCENE LAYER      │  ← Business logic
                    │  (what to sync)     │
                    └─────────┬───────────┘
                              │
                    ┌─────────▼───────────┐
                    │  PROTOCOL LAYER     │  ← Serialization
                    │  (how to serialize) │
                    └─────────┬───────────┘
                              │
                    ┌─────────▼───────────┐
                    │  NETWORK LAYER      │  ← Transport
                    │  (how to send)      │
                    └─────────────────────┘
```

**Critical rule:** A layer must NOT call upward or skip layers.

### 3.3 Data Flow (Full Pipeline)

```
Blender Side (outbound):
    Scene Change
        │
        ▼
    [Scene Observer] ─── depsgraph_update_post handler
        │
        ▼
    [Event Queue] ─── buffered, deduplicated
        │
        ▼
    [Delta Builder] ─── computes minimal delta per object
        │                   OUTPUT: semantic events (TransformChanged, MeshChanged, MaterialChanged)
        │                   KNOWS: scene objects, bpy types
        │                   DOES NOT KNOW: packets, bytes, network
        ▼
    [Message Builder] ─── converts deltas to protocol messages
        │                   INPUT: semantic events
        │                   OUTPUT: typed messages (OBJECT_UPDATE, MESH_DATA, ...)
        │                   KNOWS: protocol message format
        │                   DOES NOT KNOW: scene objects, bpy types
        ▼
    [Serializer] ─── binary framing (length prefix + payload)
        │
        ▼
    [Session Manager] ─── attach session ID, validate state
        │
        ▼
    [Network Client] ─── TCP send (background thread)

                    ═══════ TCP (binary, length-prefixed) ═══════

UE Side (inbound):
    [Network Server] ─── TCP receive (background thread)
        │
        ▼
    [Session Manager] ─── validate session, heartbeat, state check
        │
        ▼
    [Message Queue] ─── lock-free MPSC
        │
        ▼ (Game Thread Tick)
    [Message Router] ─── routes by message type
        │                   INPUT: typed messages
        │                   OUTPUT: calls Apply() on managers
        │                   KNOWS: message type → manager routing
        │                   DOES NOT KNOW: how managers work internally
        │
        ├──→ [ObjectRegistry] ─── create/update/delete actor mappings
        │       └── calls Apply() on managers below
        ├──→ [MeshManager] ─── .Apply(MeshUpdate)  ← receives typed update, no socket awareness
        ├──→ [MaterialManager] ─── .Apply(MaterialUpdate)
        ├──→ [CameraManager] ─── .Apply(CameraUpdate)
        └──→ [HierarchyManager] ─── .Apply(HierarchyUpdate)
```

### 3.4 Component Constraints

#### MessageRouter

**MessageRouter MUST:**
- Decode message type.
- Dispatch to the correct manager via `Apply(Update)`.

**MessageRouter MUST NOT:**
- Allocate actors.
- Update meshes.
- Touch materials.
- Modify world state.
- Import or reference UObject types.

MessageRouter is a pure dispatch layer. If it needs to do business logic, the logic belongs in the target manager.

#### Managers (MeshManager, MaterialManager, CameraManager, HierarchyManager)

**Every manager MUST:**
- Receive only an `Apply(Update)` call.
- Know nothing about the network, socket, session, or protocol.
- Be independently testable with mock updates.
- Never import or reference network types.

**Every manager MUST NOT:**
- Read from or write to the network socket.
- Access the session manager.
- Import protocol types (MessageBuilder, Serializer, etc.).

This ensures managers are pure scene-update logic. They can be tested, replaced, or extended without touching the network layer.

---

## 4. Directory Structure

```
Projects/UELiveSync/
├── Shared/                              # Independent protocol artifact
│   ├── Protocol/
│   │   ├── Protocol.md                  # Wire format spec (human-readable)
│   │   ├── MessageTypes.yaml            # Canonical: opcodes + payload layouts
│   │   ├── Types.yaml                   # Canonical: composite types (uuid, transform3d, etc.)
│   │   ├── Capabilities.yaml            # Canonical: capability bit definitions
│   │   ├── Errors.yaml                  # Canonical: error code definitions
│   │   ├── Versioning.md                # Protocol versioning rules (major/minor/patch)
│   │   ├── MessageTypes.py              # GENERATED — do not hand-edit
│   │   ├── MessageTypes.h               # GENERATED — do not hand-edit
│   │   ├── TestVectors/
│   │   │   └── Handshake/               # HELLO.bin, ACK.bin, REJECT.bin + .json
│   │   └── Schemas/
│   │       ├── transform.schema.json    # JSON Schema for transform3d
│   │       ├── mesh.schema.json         # JSON Schema for mesh data
│   │       └── material.schema.json     # JSON Schema for material data
│   └── Docs/
│       └── Glossary.md                  # Shared terminology
│   │
│   ├── Schemas/                         # Machine-readable schemas
│   │   ├── protocol_v1.json             # JSON Schema for protocol messages
│   │   └── capability_v1.json           # JSON Schema for capability bitmask
│   │
│   ├── TestVectors/                     # Binary test vectors for validation
│   │   ├── handshake/
│   │   │   ├── hello_valid.bin
│   │   │   ├── hello_valid.bin.meta     # {type, session_id, capabilities, ...}
│   │   │   ├── ack_valid.bin
│   │   │   ├── reject_version.bin
│   │   │   └── ...
│   │   ├── transform/
│   │   ├── mesh/
│   │   └── material/
│   │
│   └── Docs/
│       └── Glossary.md                  # Shared terminology
│
├── UE_Plugin/                           # Unreal Engine plugin
│   └── UELiveSync/
│       ├── UELiveSync.uplugin
│       └── Source/
│           ├── UELiveSyncRuntime/       # Runtime module
│           │   ├── UELiveSyncRuntime.Build.cs
│           │   ├── Public/
│           │   │   ├── UELiveSyncRuntime.h
│           │   │   ├── LiveSyncTypes.h
│           │   │   ├── SyncObjectId.h
│           │   │   └── SyncMessage.h
│           │   └── Private/
│           │       ├── Networking/
│           │       │   ├── NetworkServer.h/.cpp
│           │       │   ├── MessageQueue.h
│           │       │   ├── MessageParser.h/.cpp
│           │       │   └── MessageSerializer.h/.cpp
│           │       ├── Session/
│           │       │   ├── SessionManager.h/.cpp     # Session lifecycle, state machine
│           │       │   └── CapabilityNegotiator.h/.cpp
│           │       ├── Sync/
│           │       │   ├── SyncSubsystem.h/.cpp
│           │       │   ├── MessageRouter.h/.cpp      # Routes messages to managers
│           │       │   ├── ObjectRegistry.h/.cpp
│           │       │   ├── MeshManager.h/.cpp
│           │       │   ├── MaterialManager.h/.cpp
│           │       │   ├── CameraManager.h/.cpp
│           │       │   ├── HierarchyManager.h/.cpp
│           │       │   └── TransformManager.h/.cpp
│           │       └── Diagnostics/
│           │           ├── LiveSyncStats.h/.cpp
│           │           └── LiveSyncLog.h
│           └── UELiveSyncEditor/        # Editor module
│               ├── UELiveSyncEditor.Build.cs
│               └── Private/
│                   ├── UI/
│                   │   ├── LiveSyncStatusBar.h/.cpp
│                   │   └── LiveSyncSettings.h/.cpp
│                   └── Settings/
│                       └── LiveSyncSettings.h/.cpp
│
├── Blender_Addon/                       # Blender addon (Python)
│   ├── bl_info                          # Addon metadata
│   ├── __init__.py                      # Registration, entry point
│   ├── Runtime/
│   │   ├── AddonManager.py              # Lifecycle, enable/disable
│   │   └── TimerManager.py              # bpy.app.timers management
│   ├── Network/
│   │   ├── NetworkClient.py             # TCP client (background thread)
│   │   ├── SessionManager.py            # Session lifecycle, state machine
│   │   └── MessageParser.py             # Binary framing + parse
│   ├── Scene/
│   │   ├── SceneObserver.py             # depsgraph_update_post handler
│   │   ├── EventQueue.py                # Buffered, deduplicated events
│   │   ├── DeltaBuilder.py              # Computes minimal deltas (scene-aware, network-unaware)
│   │   └── ObjectRegistry.py            # UUID → bpy.types.Object mapping
│   ├── Protocol/
│   │   ├── MessageBuilder.py            # Converts deltas to protocol messages
│   │   ├── Serializer.py                # Binary serialization
│   │   └── MessageTypes.py              # Protocol constants (mirrors Shared/)
│   ├── Mesh/
│   │   ├── MeshExtractor.py             # Bulk vertex/UV/normal extraction
│   │   └── MeshDelta.py                 # Topology vs vertex-only detection
│   ├── Material/
│   │   ├── MaterialExtractor.py         # Principled BSDF param reading
│   │   └── TextureResolver.py           # Path/UUID resolution
│   ├── Camera/
│   │   └── CameraExtractor.py           # Camera param extraction
│   └── UI/
│       ├── Panel.py                     # N-panel UI
│       └── Preferences.py               # Addon preferences
│
├── Tests/
│   ├── protocol/                        # Protocol roundtrip tests
│   │   ├── compatibility/               # Cross-version compatibility tests
│   │   └── test_vectors/                # Generated from Shared/TestVectors/
│   ├── integration/                     # Blender ↔ UE integration tests
│   └── stress/                          # Long-running session tests
│
└── Docs/
    ├── Architecture/
    │   └── SystemArchitecture.md        # This document
    ├── Roadmap/
    ├── Decisions/
    └── Investigations/
```

### 4.1 Key Design Decisions

**Shared Protocol is a real artifact:**
- Lives in `Shared/` at the project root.
- Contains: `Protocol.md` (source of truth), `Schemas/` (machine-readable), `TestVectors/` (binary validation).
- Neither UE nor Blender "owns" it.
- Future DCC tools implement the same spec.
- Test vectors validate both sides independently.

**Canonical schema is the single source of truth:**
- `Shared/Protocol/MessageTypes.yaml` defines all message types, fields, and types.
- `Shared/Protocol/Schemas/` contains JSON Schema definitions for validation.
- **All language implementations MUST be generated from or validated against these canonical sources.**
- `MessageTypes.py` and `MessageTypes.h` are generated files — never hand-edit.
- If codegen is not yet implemented, both sides MUST be validated against `MessageTypes.yaml` in CI.
- Python and C++ must NOT define their own independent struct layouts.

**No FBX import (UE side):**
- The old UELiveSync used FBX import → heavy editor dependencies.
- v2 uses `UProceduralMeshComponent` for ALL runtime mesh.
- Works in editor and packaged builds. No asset pipeline dependency.

**No legacy code port:**
- Both UE plugin and Blender addon are written from scratch.
- Only reuse proven concepts (MPSC queue, depsgraph handler pattern), not code.
- Old UELiveSync is archived, not referenced.

**Blender addon is layered (three clean layers):**
```
Scene Layer:   SceneObserver → EventQueue → DeltaBuilder → ObjectRegistry
Protocol Layer: MessageBuilder → Serializer
Network Layer:  NetworkClient → SessionManager
```
- DeltaBuilder produces semantic events (TransformChanged, MeshChanged), NOT packets.
- MessageBuilder converts events to protocol messages, NOT scene objects.
- Each layer has a single responsibility. No god files.

**Managers are pure scene-update logic:**
- MeshManager, MaterialManager, CameraManager, HierarchyManager each expose only `Apply(Update)`.
- They know nothing about network, socket, session, or protocol.
- Independently testable with mock updates.

---

## 5. Networking Protocol

All wire-level specifications are defined in `Shared/Protocol/` YAML files. This section provides a human-readable overview. The YAML files are the authoritative source.

### 5.1 Transport

- **TCP** with length-prefixed binary framing.
- **Port:** Configurable, default `14285`.
- **Direction:** UE listens (server), Blender connects (client).
- **Single connection** per session (v1 scope).
- **Byte order:** All multi-byte fields are **little-endian** unless explicitly stated.
- **Float encoding:** IEEE 754 single-precision (float32), little-endian.

### 5.2 Wire Format

Every message on the wire:

```
[4 bytes: uint32 LE] payload length N (excluding this 4-byte length prefix)
[N bytes: payload]
```

**Before session established** (HELLO, HELLO_ACK, REJECT only) — 6-byte header:

```
[1 byte:  MsgType    (uint8)        — message type opcode]
[1 byte:  Flags      (uint8)        — flags bitmask]
[4 bytes: SequenceId (uint32 LE)    — monotonic counter]
```

**After session established** (all other messages) — 14-byte header:

```
[1 byte:  MsgType    (uint8)        — message type opcode]
[1 byte:  Flags      (uint8)        — flags bitmask]
[4 bytes: SequenceId (uint32 LE)    — monotonic counter]
[8 bytes: SessionId  (uint64 LE)    — session identifier]
```

**Version is NOT in the regular header.** Version is exchanged ONLY in HELLO/HELLO_ACK/REJECT during session establishment. Rationale: keeps header small; version mismatch is caught at session start.

**Header invariant:** Pre-session messages (HELLO, HELLO_ACK, REJECT) MUST NOT contain SessionId. Post-session messages MUST contain SessionId. Both serializer and deserializer MUST enforce this.

### 5.3 Flags Bitmask

| Bit | Name | Description |
|-----|------|-------------|
| 0 | compressed | Payload is zlib-compressed. Receiver must decompress before parsing. |
| 1 | encrypted | Reserved for future use. MUST be zero in v1. If non-zero, receiver MUST treat as INVALID_MESSAGE and handle per Errors.yaml. |
| 2 | ack_required | Sender expects SYNC_ACK for this message. |
| 3 | fragmented | Message is part of a fragmented transfer. More chunks follow. |
| 4–7 | reserved | Must be 0. Do not use without protocol version bump. |

### 5.4 SequenceId Rules

- **Type:** uint32
- **Per sender:** Each side (client, server) has its own independent counter.
- **Initialized:** To 0 when a new transport connection is established.
- **Increments:** +1 per message sent.
- **Wraps around:** `0xFFFFFFFF → 0x00000000`.
- **Never reset during:** Session negotiation (HELLO/HELLO_ACK/REJECT).
- **Comparison:** Modular arithmetic — `(a - b)` interpreted as signed int32.

```
def is_newer(a, b):
    diff = (int32)(a - b)
    return diff > 0

def is_older(a, b):
    return is_newer(b, a)

def is_duplicate(a, b):
    return a == b
```

- **Used for:** Stale message detection, HELLO_ACK matching, duplicate detection.
- **Note:** Works correctly across wraparound within a window of 2^31 messages.

**Per-sender example:**

```
Client (Blender)          Server (UE)
─────────────────         ─────────────────
HELLO          seq=0      HELLO_ACK      seq=0
SCENE_HASH     seq=1      SYNC_ACK       seq=1
OBJECT_CREATE  seq=2      SYNC_ACK       seq=2
OBJECT_UPDATE  seq=3      SYNC_ACK       seq=3
```

One SequenceId counter is maintained per sender per transport connection. Counters are NOT paired across sides.

### 5.5 Message Types

| Code | Name | Direction | Header | Description |
|------|------|-----------|--------|-------------|
| 0x10 | HELLO | B→U | Before session | Client initiates session |
| 0x11 | HELLO_ACK | U→B | Before session | Server accepts session |
| 0x12 | REJECT | U→B | Before session | Server rejects session |
| 0x00 | HEARTBEAT | Both | Post-session | Keepalive (empty body) |
| 0x01 | HEARTBEAT_ACK | Both | Post-session | Keepalive response |
| 0x02 | SCENE_HASH | B↔U | Post-session | Scene registry hash |
| 0x03 | SCENE_FULL | B→U | Post-session | Full scene state |
| 0x04 | SCENE_DELTA | B→U | Post-session | Changed objects only |
| 0x20 | OBJECT_CREATE | B→U | Post-session | New object |
| 0x21 | OBJECT_UPDATE | B→U | Post-session | Property changes |
| 0x22 | OBJECT_DELETE | B→U | Post-session | Remove object |
| 0x23 | OBJECT_RENAME | B→U | Post-session | Rename object |
| 0x24 | OBJECT_REPARENT | B→U | Post-session | Change parent |
| 0x25 | OBJECT_VISIBILITY | B→U | Post-session | Show/hide |
| 0x30 | MESH_DATA | B→U | Post-session | Full mesh data |
| 0x31 | MESH_DELTA | B→U | Post-session | Vertex-only updates |
| 0x32 | MESH_START | B→U | Post-session | Begin chunked transfer |
| 0x33 | MESH_CHUNK | B→U | Post-session | Mesh data chunk |
| 0x34 | MESH_END | B→U | Post-session | End chunked transfer |
| 0x40 | MATERIAL_CREATE | B→U | Post-session | New material |
| 0x41 | MATERIAL_UPDATE | B→U | Post-session | Parameter changes |
| 0x42 | MATERIAL_ASSIGN | B→U | Post-session | Assign material to slot |
| 0x50 | CAMERA_CREATE | B→U | Post-session | New camera |
| 0x51 | CAMERA_UPDATE | B→U | Post-session | Transform + params |
| 0x52 | CAMERASETACTIVE | B→U | Post-session | Set active camera |
| 0xF0 | SYNC_ACK | U→B | Post-session | Acknowledge sync |
| 0xFE | ERROR | Both | Post-session | Error with code + message |
| 0xFF | DISCONNECT | Both | Post-session | Graceful disconnect |

Full payload layouts are defined in `Shared/Protocol/MessageTypes.yaml`.

### 5.6 Capability Negotiation + Session Establishment

On connect, after TCP handshake:

```
Blender → UE:  HELLO     { protocol_version_major: u8, protocol_version_minor: u8, capabilities: u64 }
UE → Blender:  HELLO_ACK { protocol_version_major: u8, protocol_version_minor: u8, accepted_capabilities: u64, max_chunk_size: u32, session_id: u64 }
```

Session ID is generated by UE (server) during HELLO_ACK. 64-bit random value. All subsequent messages carry this Session ID in the header (§5.2). If Session ID doesn't match → drop silently.

If protocol_version is unsupported:
```
UE → Blender:  REJECT { error_code: 0x0001, reason: utf8_string, min_version_major: u8, min_version_minor: u8, max_version_major: u8, max_version_minor: u8 }
```

**Capability bits** (defined in `Shared/Protocol/Capabilities.yaml`):

| Bit | Name | Description |
|-----|------|-------------|
| 0 | mesh_sync | Supports mesh data transfer |
| 1 | material_sync | Supports material sync |
| 2 | camera_sync | Supports camera sync |
| 3 | mesh_delta | Supports vertex-only mesh updates |
| 4 | chunked_transfer | Supports chunked mesh transfer |
| 5 | texture_uuid_resolution | Supports texture UUID resolution |
| 6 | scene_hash | Supports SCENE_HASH for incremental reconnect |
| 7–31 | reserved | Reserved for future use |

Session operates only within the **intersection** of both sides' accepted capabilities.

**Normative authority:** `Shared/Protocol/*.yaml` files are the authoritative source. `Protocol.md` is informative. If this document contradicts the YAML files, the YAML files win.

**Uniqueness invariants (enforced by code generator):**
- Every opcode in `MessageTypes.yaml` MUST appear exactly once.
- Every capability bit in `Capabilities.yaml` MUST appear exactly once.
- Every error code in `Errors.yaml` MUST appear exactly once.

### 5.7 SessionId Lifecycle

| Event | Action |
|---|---|
| HELLO received | UE validates version, generates session_id |
| HELLO_ACK sent | session_id included in HELLO_ACK, both sides store it |
| Any message after HELLO_ACK | session_id in header, validated by receiver |
| Disconnect | session_id invalidated on both sides |
| Reconnect | New session_id generated (never reused) |
| Stale message | session_id mismatch → drop silently |

### 5.8 Scene Hash (Incremental Reconnect)

After session establishment, both sides exchange SCENE_HASH:

```
Blender → UE:  SCENE_HASH { hash: uint64, object_count: uint32 }
UE → Blender:  SCENE_HASH { hash: uint64, object_count: uint32 }
```

**Algorithm** (defined in `Shared/Protocol/MessageTypes.yaml`):

1. Sort all tracked object UUIDs lexicographically (by raw 16 bytes).
2. For each UUID in order: append position(x,y,z), rotation(x,y,z,w), scale(x,y,z) as float32 LE bytes.
3. Hash the concatenated byte sequence with **xxHash64** (seed=0).

**Canonical float rules** (both sides MUST apply):
- All floats are IEEE 754 float32 little-endian.
- Negative zero (-0.0) MUST be converted to positive zero (+0.0) before hashing.
- NaN values MUST be rejected. If any float is NaN, hash computation fails → full scene sync triggered.
- Quaternions MUST be normalized: `q = q / |q|`. If `|q|` is zero or near-zero (< 1e-7), use identity quaternion (0, 0, 0, 1).
- No rounding or truncation beyond float32 precision.

If hashes match → no sync needed. If hashes differ → Blender sends SCENE_FULL.

### 5.9 Message Ordering

- **Transport:** TCP guarantees in-order delivery.
- **No application-level reordering** needed in v1.
- MessageRouter processes messages in arrival order.
- SequenceId is used for duplicate/stale detection, not ordering.

### 5.10 UUID Byte Order

- **Format:** RFC 4122, network byte order (big-endian in the UUID itself, stored as 16 raw bytes).
- **Wire transmission:** The canonical 16-byte RFC 4122 byte sequence is transmitted WITHOUT field-wise endian conversion.
- **NOT** Windows GUID mixed-endian layout (which swaps time_low, time_mid, time_hi).
- **NOT** platform-specific byte order.
- Example: UUID `00112233-4455-6677-8899-aabbccddeeff` stored as: `00 11 22 33 44 55 66 77 88 99 aa bb cc dd ee ff` — transmitted as-is, no byte swapping.

### 5.11 String Encoding

- **Encoding:** UTF-8.
- **Length prefix:** uint16 (16-bit), little-endian.
- **Length unit:** **Bytes**, not characters.
- **Max length:** 65,535 bytes.
- **No null terminator** on wire.

### 5.12 Chunk Transfer Rules

- **Timeout:** 30 seconds without a new chunk → discard entire transfer.
- **Timer resets:** Timer resets to 30s every time a new MESH_CHUNK is received for the same transfer.
- **On timeout:** Clean up partial data, increment `CHUNK_TIMEOUT` error counter.
- **Max chunks:** 65,535 per transfer.
- **Ordering:** Chunks must be sent sequentially (no parallel chunks per object).
- **Verification:** MESH_END includes checksum for integrity check.

### 5.13 Heartbeat

- **Interval:** 10 seconds.
- **Timeout:** 30 seconds (no message of any type received).
- **On timeout:** Close connection, trigger reconnect logic.

---

## 6. Serialization

### 6.1 Format Decision

**Custom binary** for all messages. No JSON, no MessagePack, no external dependencies.

Rationale:
- Binary is the simplest format for both Python (`struct.pack`) and C++ (`FArchive`/`FMemoryWriter`).
- No external library dependency on either side.
- Full control over alignment, padding, endianness.
- The protocol is designed once and stable — schema evolution is handled by adding fields at the end.

### 6.2 Endianness

All multi-byte values are **little-endian** (matches x86/x64 and ARM).

### 6.3 Primitive Types

| Type | Size | Notes |
|------|------|-------|
| u8 | 1 byte | uint8 |
| u16 | 2 bytes | uint16 LE |
| u32 | 4 bytes | uint32 LE |
| u64 | 8 bytes | uint64 LE |
| f32 | 4 bytes | IEEE 754 float LE |
| f64 | 8 bytes | IEEE 754 double LE |
| uuid | 16 bytes | Raw UUID bytes (no dash formatting) |
| string | u16 + bytes | Length-prefixed UTF-8 |
| vec3f | 12 bytes | 3 × f32 (x, y, z) |
| quatf | 16 bytes | 4 × f32 (x, y, z, w) |

### 6.4 Mesh Data Layout

```
MESH_DATA message body:
  [uuid: object_id]
  [u32: vertex_count]
  [u32: index_count]        (always divisible by 3)
  [u8:  format_flags]       (bit 0: has_normals, bit 1: has_uv0, bit 2: has_colors, bit 3: has_tangents)
  [f32 × 3 × vertex_count: positions]
  [if has_normals: f32 × 3 × vertex_count]
  [if has_uv0: f32 × 2 × vertex_count]
  [if has_colors: u32 × vertex_count]   (PackedABGR)
  [if has_tangents: f32 × 4 × vertex_count]
  [u32 × index_count: triangle indices]
```

For large meshes (>64KB payload), use chunked transfer:
```
MESH_START:
  [uuid: object_id]
  [u32: total_vertices]
  [u32: total_indices]
  [u16: total_chunks]
  [u8: format_flags]

MESH_CHUNK:
  [uuid: object_id]
  [u16: chunk_index]
  [u16: vertex_offset]
  [u32: vertex_count]      (in this chunk)
  [u32: index_count]       (in this chunk)
  [vertex data...]
  [index data...]

MESH_END:
  [uuid: object_id]
  [u32: crc32]              (of complete mesh data)
```

### 6.5 Material Data Layout

```
MATERIAL_UPDATE message body:
  [uuid: object_id]
  [u32: slot_index]
  [uuid: material_id]
  [u8:  param_count]
  For each param:
    [u8: param_type]        (0=scalar, 1=vector, 2=texture)
    [string: param_name]
    [if scalar: f32 value]
    [if vector: f32 × 4 value (RGBA)]
    [if texture: string path]
```

### 6.6 Camera Data Layout

```
CAMERA_UPDATE message body:
  [uuid: object_id]
  [vec3f: position]
  [quatf: rotation]
  [f32: focal_length]
  [f32: sensor_width]
  [f32: sensor_height]
  [f32: clip_start]
  [f32: clip_end]
  [f32: fov_horizontal]     (for validation)
```

---

## 7. Session State Machine

Every session follows an explicit state machine. Both sides (UE and Blender) maintain their own session state. The state determines what messages are valid at any point.

### 7.1 States

```
                    ┌─────────────────────────────────┐
                    │                                 │
                    ▼                                 │
            ┌──────────────┐                          │
            │ Disconnected │                          │
            └──────┬───────┘                          │
                   │ connect()                        │
                   ▼                                  │
            ┌──────────────┐                          │
            │  Connecting  │──── timeout ─────────────┤
            └──────┬───────┘                          │
                   │ TCP connected                    │
                   ▼                                  │
            ┌──────────────┐                          │
            │ Negotiating  │──── version mismatch ────┤
            │ (HELLO/HELLO_ACK) │──── REJECT ─────────────┤
            └──────┬───────┘                          │
                   │ HELLO_ACK received               │
                   ▼                                  │
            ┌──────────────┐                          │
            │ Synchronizing│──── full scene sync      │
            │ (initial)    │                          │
            └──────┬───────┘                          │
                   │ sync complete                    │
                   ▼                                  │
            ┌──────────────┐                          │
            │    Ready     │◄──── incremental sync ───┤
            │ (steady)     │                          │
            └──────┬───────┘                          │
                   │ error / disconnect / timeout     │
                   └──────────────────────────────────┘
```

### 7.2 State Descriptions

| State | Entry Condition | Valid Messages | Behavior |
|---|---|---|---|
| **Disconnected** | Initial state, or after fatal error | None | No network activity. |
| **Connecting** | `connect()` called | None | TCP handshake in progress. Timeout after 5s → back to Disconnected. |
| **Negotiating** | TCP connected | HELLO, HELLO_ACK, REJECT | Exchange capabilities. Version mismatch → REJECT → Disconnected. |
| **Synchronizing** | HELLO_ACK received | Full scene data (OBJECT_CREATE, MESH_DATA, MATERIAL_CREATE) | Full scene sync. Blender sends all tracked objects. UE creates all actors. |
| **Ready** | Sync complete | Incremental updates (OBJECT_UPDATE, MESH_DATA, etc.) | Steady-state. Only deltas sent. |

### 7.3 State Transitions

| From | To | Trigger |
|---|---|---|
| Disconnected | Connecting | User initiates connection |
| Connecting | Negotiating | TCP socket connected |
| Connecting | Disconnected | Timeout (5s) or TCP error |
| Negotiating | Synchronizing | HELLO_ACK received with compatible capabilities |
| Negotiating | Disconnected | REJECT received, or timeout (5s) |
| Synchronizing | Ready | Full sync complete (all objects sent) |
| Synchronizing | Disconnected | Error during sync, or timeout |
| Ready | Disconnected | TCP error, heartbeat timeout (30s), or user disconnect |
| Ready | Synchronizing | Reconnect after disconnect (re-sync full scene) |

### 7.4 Session ID

- Generated by UE (server) during HELLO_ACK (see §5.4).
- 64-bit random value.
- Carried in every message header after session establishment (see §5.2 wire format).
- Used to detect stale messages from previous sessions.
- If session_id doesn't match → drop message silently.
- On reconnect, a new session ID is generated.

### 7.5 Heartbeat

- Both sides send HEARTBEAT every 10 seconds.
- If no message (any type) received for 30 seconds → assume disconnected → transition to Disconnected.
- HEARTBEAT is the only message valid in all states after Negotiating.

---

## 8. Threading Model

### 8.1 UE Side

```
┌─────────────────────────────────────────────────────────────┐
│                    THREAD MODEL (UE)                         │
│                                                             │
│  ┌─────────────────────────┐   ┌─────────────────────────┐  │
│  │ Network Thread          │   │ Game Thread              │  │
│  │ (FRunnable)             │   │ (UWorldSubsystem::Tick) │  │
│  │                         │   │                          │  │
│  │ - TCP accept            │   │ - Dequeue messages       │  │
│  │ - TCP recv              │   │ - Session Manager        │  │
│  │ - Validate header       │   │   (state check, route)   │  │
│  │ - Deserialize message   │◄──│ - Message Router         │  │
│  │ - Session Manager       │MPSC│   (switch → manager)    │  │
│  │   (validate session)    │   │ - Update actors          │  │
│  │ - Enqueue to queue      │   │ - Update materials       │  │
│  │ - TCP send (responses)  │──►│ - Update camera          │  │
│  │                         │   │ - Enqueue responses      │  │
│  └─────────────────────────┘   └─────────────────────────┘  │
│                                                             │
│  RULES:                                                     │
│  - Network thread NEVER touches UObject*                     │
│  - Network thread NEVER reads/writes scene state             │
│  - Game thread NEVER does blocking network I/O               │
│  - All actor creation/modification on game thread only       │
│  - Managers receive only Apply(Update), no socket awareness  │
└─────────────────────────────────────────────────────────────┘
```

### 8.2 Blender Side

```
┌─────────────────────────────────────────────────────────────┐
│                    THREAD MODEL (Blender)                    │
│                                                             │
│  ┌─────────────────────────┐   ┌─────────────────────────┐  │
│  │ Main Thread (Blender)   │   │ Network Thread          │  │
│  │                         │   │ (Python threading.Thread)│  │
│  │ - depsgraph_update_post │   │                          │  │
│  │ - Read scene state      │   │ - socket.recv()          │  │
│  │ - Delta Builder         │──►│ - Session Manager        │  │
│  │ - Message Builder       │Queue│   (validate, heartbeat) │  │
│  │ - Serializer            │   │ - socket.send()          │  │
│  │ - bpy.app.timers        │   │ - parse/validate         │  │
│  │   (periodic poll)       │◄──│                          │  │
│  │                         │   │                          │  │
│  └─────────────────────────┘   └─────────────────────────┘  │
│                                                             │
│  RULES:                                                     │
│  - Network thread NEVER calls bpy.* (crashes Blender)        │
│  - Main thread NEVER does blocking socket I/O                │
│  - All scene reads/writes on main thread only                │
│  - queue.Queue bridges the two safely                        │
│  - DeltaBuilder produces events, NOT packets                 │
│  - MessageBuilder produces packets, NOT scene objects        │
└─────────────────────────────────────────────────────────────┘
```

### 8.3 Tick Budget

The UE game thread Tick processes messages. Budget per frame:

| Phase | Budget | Action |
|-------|--------|--------|
| Dequeue | <0.5ms | Dequeue up to N messages from MPSC queue |
| Process | <5ms | Route and apply messages to scene |
| Transform interp | <1ms | Interpolate transforms for smooth display |
| Total | <7ms | Must complete within frame budget (16ms @ 60fps) |

If queue depth exceeds threshold (1000 messages), drop oldest transform messages (latest-wins semantics).

---

## 9. Object Identity Model

### 9.1 Identity Types

```cpp
// Persistent identifier — survives rename, reparent, reconnect
// Generated once in Blender, stored as custom property
struct FSyncObjectId
{
    FGuid PersistentId;    // UUID, generated in Blender
    FString BlenderName;   // Human-readable name (for debug)
    FString BlenderPath;   // Full Blender path (e.g., "Collection/Character")
};
```

### 9.2 Identity Storage

**Blender side:**
```python
# On object creation, generate and store:
import uuid
obj["sync_id"] = str(uuid.uuid4())  # stored as custom property

# Retrieved as:
sync_id = obj.get("sync_id", None)
```

**UE side:**
```cpp
// UActorComponent attached to each synced actor
UCLASS()
class ULiveSyncIdentityComponent : public UActorComponent
{
    GENERATED_BODY()
public:
    UPROPERTY()
    FGuid PersistentId;

    UPROPERTY()
    FString BlenderName;

    UPROPERTY()
    FString BlenderPath;
};
```

### 9.3 Object Registry

Both sides maintain an ObjectRegistry. The registry is the single lookup for all synced objects. Implementation is hidden behind a formal API — consumers never access the underlying dict/map directly.

**Blender-side API:**

```python
class ObjectRegistry:
    """UUID ↔ bpy.types.Object mapping. Single source of truth for Blender side."""

    def register(uuid: str, obj: bpy.types.Object) -> None:
        """Register an object with its persistent UUID. Called on OBJECT_CREATE."""

    def unregister(uuid: str) -> None:
        """Remove UUID mapping. Called on OBJECT_DELETE."""

    def find_by_uuid(uuid: str) -> Optional[bpy.types.Object]:
        """Look up Blender object by UUID. Returns None if not found."""

    def find_by_object(obj: bpy.types.Object) -> Optional[str]:
        """Look up UUID by Blender object. Returns None if not tracked."""

    def rename(uuid: str, new_name: str) -> None:
        """Update name in registry. Called on OBJECT_RENAME."""

    def rebuild() -> None:
        """Full rebuild from scene. Called on reconnect to ensure consistency."""

    def clear() -> None:
        """Clear all entries. Called on disconnect."""
```

**UE-side API:**

```cpp
class FObjectRegistry
{
public:
    void Register(FGuid Id, AActor* Actor);
    void Unregister(FGuid Id);
    AActor* FindById(FGuid Id) const;
    FGuid FindByActor(AActor* Actor) const;
    void Rename(FGuid Id, FString NewName);
    void Rebuild();  // Full rebuild from world. Called on reconnect.
    void Clear();    // Clear all entries. Called on disconnect.

private:
    TMap<FGuid, FSyncObjectEntry> Entries;
    TMap<AActor*, FGuid> ActorToId;

    struct FSyncObjectEntry
    {
        FGuid Identity;
        AActor* Actor;
        UProceduralMeshComponent* MeshComp;
        UMaterialInstanceDynamic* Materials[MAX_SLOTS];
        uint64 LastModified;
        ESyncObjectState State;  // Pending, Active, Destroying
    };
};
```

**Key rule:** All registry access goes through the API. No consumer should directly iterate the underlying map/dict. This allows changing the implementation (e.g., adding spatial indexing, thread safety) without updating consumers.

### 9.4 Lifecycle

1. **Blender creates object** → generates `sync_id`, stores as custom property.
2. **Blender sends OBJECT_CREATE** with `persistent_id + initial transform + metadata`.
3. **UE receives OBJECT_CREATE** → checks `ObjectRegistry`:
   - If `PersistentId` already exists: update (handles re-send).
   - If new: spawn actor, attach `ULiveSyncIdentityComponent`, register in `ObjectRegistry`.
4. **Blender sends OBJECT_UPDATE** with `persistent_id + changed properties`.
5. **UE receives OBJECT_UPDATE** → looks up by `PersistentId`, applies changes.
6. **Blender sends OBJECT_DELETE** with `persistent_id`.
7. **UE receives OBJECT_DELETE** → destroys actor, removes from `ObjectRegistry`.

---

## 10. Asset Identity Model

### 10.1 Material Identity

Materials are identified by a combination of:
- **Material slot index** on the object (0-based).
- **Material parameters** (the actual content).

There is no persistent material UUID in v1. Materials are defined inline with each object.

On reconnect: materials are re-sent with the object (SCENE_FULL).

### 10.2 Texture Identity

Textures are identified by **Asset ID (UUID)**, NOT by file path.

**Why UUID over file path:**
- File path is a **resolution hint**, not an identity.
- UUID survives file renames, moves, and reorganization.
- Enables future upgrade to: Asset Registry, Content Browser assets, remote asset server, streaming, package-based asset management.
- File path as identity would require protocol changes for any of the above.

**TextureReference structure:**
```
TextureReference {
    UUID: u64              // Persistent identity (Blender-generated)
    RelativePath: string   // Resolution hint (Blender-relative path)
    Filename: string       // Base filename
    Hash: u64 (optional)   // Content hash for dedup
    Timestamp: u64 (optional) // Modification time
}
```

**UE-side resolution:**
```
UUID → cache lookup → path resolution → load asset
```

**Fallback:** If texture cannot be resolved (path doesn't exist, asset not found), use a default grey material. Log warning.

**v1 scope:** No texture asset transfer. UUID + path reference only. If texture file exists on UE machine at the referenced path, it is loaded. If not, fallback material.

**Future scope (v2+):** Texture transfer via asset import, Asset Registry integration, remote asset server.

---

## 11. Update Pipeline

### 11.1 Change Detection (Blender — Scene Layer)

The Scene Layer detects changes and produces **semantic events**. It knows about scene objects but NOT about packets or network.

```
depsgraph_update_post fires
    │
    ├── iterate depsgraph.updates
    │
    ├── For each update:
    │   ├── Classify by update.id.type:
    │   │   ├── 'OBJECT' → check flags
    │   │   │   ├── is_updated_transform → emit TransformChanged(object_id, transform)
    │   │   │   ├── is_updated_geometry → emit MeshChanged(object_id, mesh_data)
    │   │   │   └── is_updated_shading → emit MaterialChanged(object_id, material_data)
    │   │   │
    │   │   ├── 'MATERIAL' → emit MaterialChanged(material_id, params)
    │   │   │
    │   │   └── 'CAMERA' → emit CameraChanged(camera_id, camera_data)
    │   │
    │   └── Skip if: not in tracked set, or disabled by user
    │
    └── Events queued to EventQueue (deduplicated, batched)
```

**OUTPUT of this layer:** Semantic events (TransformChanged, MeshChanged, MaterialChanged, CameraChanged).
**DOES NOT KNOW:** Packets, bytes, protocol, network.

### 11.2 Message Building (Blender — Protocol Layer)

The Protocol Layer converts semantic events into protocol messages. It knows about message format but NOT about scene objects.

```
EventQueue
    │
    ▼
[Message Builder]
    │   INPUT: TransformChanged, MeshChanged, MaterialChanged, ...
    │   OUTPUT: OBJECT_UPDATE, MESH_DATA, MATERIAL_UPDATE, ...
    │   KNOWS: protocol message format, field layout
    │   DOES NOT KNOW: bpy types, scene graph, depsgraph
    │
    ▼
[Serializer]
    │   OUTPUT: binary frames (length prefix + payload)
    │
    ▼
[Session Manager → Network Client]
```

### 11.3 Change Classification

| Blender Change | Message Type | UE Action |
|---|---|---|
| Object moved/rotated/scaled | OBJECT_UPDATE (transform) | `SetActorTransform()` |
| Mesh vertices edited | MESH_DATA (full) or MESH_DELTA | `UpdateMeshSection()` (vertex-only) or `CreateMeshSection()` (topology) |
| Mesh topology changed | MESH_DATA (full) | `CreateMeshSection()` |
| Material parameter changed | MATERIAL_UPDATE | `MID->SetScalarParameterValue()` etc. |
| Material slot reassigned | MATERIAL_ASSIGN | `Component->SetMaterial()` |
| Object renamed | OBJECT_RENAME | `SetActorLabel()` |
| Object parent changed | OBJECT_REPARENT | `AttachToActor()` / `DetachFromActor()` |
| Object hidden/shown | OBJECT_VISIBILITY | `SetActorHiddenInGame()` |
| Object created | OBJECT_CREATE | `SpawnActor<>()` |
| Object deleted | OBJECT_DELETE | `Actor->Destroy()` |
| Camera transform changed | CAMERA_UPDATE | `Camera->SetActorTransform()` |
| Camera parameters changed | CAMERA_UPDATE | `CineCamera->SetCurrentFocalLength()` etc. |
| Active camera changed | CAMERASetActive | `SetViewTargetWithBlend()` |

### 11.4 Mesh Update Strategy

Two-tier approach for efficiency:

**Tier 1: Vertex-only update (fast path)**
- When: vertex count unchanged, topology unchanged, only positions/normals/UVs changed.
- How: `UProceduralMeshComponent::UpdateMeshSection()`.
- Performance: ~0.1ms for 10K vertices. In-place vertex buffer update.

**Tier 2: Full topology update (slow path)**
- When: vertex count changed, or face count changed, or new UV layer, or structural change.
- How: `UProceduralMeshComponent::CreateMeshSection()` (full section replace).
- Performance: ~1-5ms for 10K vertices. Full scene proxy recreation.

**Detection:** Blender side tracks `len(mesh.vertices)` and `len(mesh.polygons)`. If either changed → Tier 2. Otherwise → Tier 1.

### 11.5 Material Update Strategy

When a material parameter changes in Blender:

1. Blender serializes only changed parameters (delta).
2. UE receives MATERIAL_UPDATE.
3. If MID doesn't exist for this slot: create via `CreateDynamicMaterialInstance()`.
4. Apply only changed parameters: `SetScalarParameterValue()` / `SetVectorParameterValue()` / `SetTextureParameterValue()`.
5. Material change takes effect on next render frame automatically.

### 11.6 Camera Update Strategy

Camera is treated as a special actor. Two levels:

**Level 1 (v1 mandatory) — Data sync:**
- Camera transform + intrinsics (focal length, sensor size).
- No editor viewport dependency.

**Level 2 (optional) — Viewport behavior:**
- Set active viewport camera.
- Prototype in Phase 6, not MVP blocker.

Camera intrinsics mapping:
| Blender | UE |
|---|---|
| `cam_data.lens` (mm) | `CineCamera->SetCurrentFocalLength()` |
| `cam_data.sensor_width` (mm) | `CineCamera->Filmback.SensorWidth` |
| `cam_data.sensor_height` (mm) | `CineCamera->Filmback.SensorHeight` |
| `cam_data.clip_start` | **DEFERRED** — no public API |
| `cam_data.clip_end` | **DEFERRED** — no public API |

---

## 12. State Ownership

### 12.1 Authority Model

**Blender is the single authority.** UE is a passive viewer.

- All state originates in Blender.
- UE never generates sync messages to Blender (unidirectional: B→U only).
- UE never modifies synced objects locally (no UE-side editing propagated back).
- If user edits an actor in UE, the edit is overwritten on next Blender update.

### 12.2 State Ownership Table

| State | Owner | UE Access |
|---|---|---|
| Object transform | Blender | Read-only (overwritten) |
| Object name | Blender | Read-only (overwritten) |
| Object parent | Blender | Read-only (overwritten) |
| Object visibility | Blender | Read-only (overwritten) |
| Mesh geometry | Blender | Read-only (overwritten) |
| Material params | Blender | Read-only (overwritten) |
| Material assignment | Blender | Read-only (overwritten) |
| Camera transform | Blender | Read-only (overwritten) |
| Camera intrinsics | Blender | Read-only (overwritten) |
| Active camera | Blender | Read-only (overwritten) |
| Connection state | UE (server) | UE manages |

### 12.3 UE-Side Local State

The following are UE-only, not synced:
- Actor selection state
- Editor viewport position
- Undo/redo history
- PIE/standalone state
- Editor preferences

---

## 13. Conflict Resolution

### 13.1 No Conflicts

Since Blender is the single authority and UE never sends changes back, there are **no conflicts** in v1.

### 13.2 Concurrent Local Edit Handling

If a user edits an actor in UE while Blender is syncing:
- The edit is temporary.
- On next Blender update for that object, the UE edit is overwritten.
- This is by design (Blender authority model).
- No warning is needed (consistent with expected behavior).

---

## 14. Reconnect Strategy

Reconnect follows the Session State Machine (Section 7). On disconnect, both sides transition to **Disconnected** state and restart the full handshake sequence.

### 14.1 Connection Loss Detection

- 30 seconds with no message (any type) → assume disconnected (per §7.5 heartbeat rules).
- Socket error / EOF → immediate disconnect.

### 14.2 Reconnect Flow

```
Connection lost
    │
    ▼
Both sides transition to Disconnected (§7)
    │
    ├── UE: Destroy all synced actors, clear ObjectRegistry
    ├── UE: Log "Connection lost. Waiting for Blender to reconnect..."
    │
    ▼
Blender detects disconnect (socket error / timeout)
    │
    ├── Log "UE disconnected. Reconnecting..."
    ├── Wait 0.5s
    ├── Attempt TCP connect
    │
    ├── If success → state: Connecting → Negotiating
    │   ├── Send HELLO (with capabilities)
    │   ├── Wait for HELLO_ACK (with accepted capabilities + session_id)
    │   ├── state: Synchronizing
    │   │
    │   ├── Exchange SCENE_HASH (§5.5)
    │   │   ├── If hashes match → state: Ready (no sync needed)
    │   │   └── If hashes differ → send SCENE_FULL → state: Ready
    │   │
    │   └── If HELLO_ACK not received within 5s:
    │       └── Close, retry with backoff
    │
    └── If failure:
        ├── Wait with exponential backoff: [0.5, 1, 2, 4, 8] seconds
        ├── Max 10 attempts
        └── Give up after 10 failures, log error
```

### 14.3 Reconnect Data Strategy

On reconnect, SCENE_HASH exchange determines sync strategy:
- **Hashes match:** No data transfer needed. Both sides resume immediately.
- **Hashes differ:** Blender sends complete scene state (all OBJECT_CREATE messages).

This is simpler than delta-since-last-sequence (no need to track what UE has seen) and more reliable (no risk of stale deltas). The hash check avoids full scene transfer when nothing changed (common case: UE crashed and restarted).

SCENE_FULL contains: all objects, all meshes, all materials, all camera data. Sent as a batch of messages (OBJECT_CREATE, MESH_DATA, MATERIAL_UPDATE, CAMERA_CREATE).

---

## 15. Feature Design

### 15.1 FEATURE 1: Realtime Mesh Sync

**Scope:**
- Create/Delete/Rename actors for mesh objects
- Transform sync (position, rotation, scale)
- Parent-child hierarchy
- Visibility toggle
- Mesh topology sync (vertex/face changes)
- Vertex movement (position edits)
- UV update
- Normal update

**NOT in scope (v1):**
- Animation
- Skeleton
- Modifiers baking (beyond what depsgraph evaluates)
- LOD system
- Collision (can be added later)

**Implementation:**

Blender side:
```python
# In depsgraph_update_post handler:
for update in depsgraph.updates:
    if update.id.type == 'OBJECT':
        obj = update.id
        if obj.type != 'MESH':
            continue

        if update.is_updated_geometry:
            # Topology or vertex change — full mesh re-serialize
            serialize_mesh_full(obj, depsgraph)
        elif update.is_updated_transform:
            # Transform only — lightweight update
            serialize_transform_only(obj)
```

UE side:
```cpp
void FMeshManager::ApplyMeshData(const FSyncObjectId& Id, const FMeshData& Data)
{
    FObjectRegistry::FSyncObjectEntry* Entry = Registry->Find(Id);
    if (!Entry || !Entry->MeshComp) return;

    if (Data.bTopologyChanged)
    {
        // Full section replace
        Entry->MeshComp->CreateMeshSection(
            0, Data.Vertices, Data.Triangles, Data.Normals,
            Data.UV0, Data.Colors, Data.Tangents, true /*collision*/);
    }
    else
    {
        // Vertex-only in-place update
        Entry->MeshComp->UpdateMeshSection(
            0, Data.Vertices, Data.Normals, Data.UV0,
            Data.Colors, Data.Tangents);
    }
}
```

**Performance target:**
- Transform: <1 frame latency (~16ms at 60fps)
- Mesh edit (vertex-only): <2 frames (~33ms)
- Mesh topology change: <2 frames

### 15.2 FEATURE 2: Realtime Material Sync

**Scope:**
- Material slot assignment
- Scalar parameter updates (roughness, metallic, etc.)
- Vector parameter updates (base color, etc.)
- Texture path reference (file path — not asset transfer)

**NOT in scope (v1):**
- Texture asset transfer (v2+)
- Procedural texture nodes
- Complex node graph replication
- Shader compilation

**Implementation:**

Blender side:
```python
# Extract Principled BSDF parameters
def extract_material(obj, slot_index):
    mat = obj.material_slots[slot_index].material
    if not mat or not mat.use_nodes:
        return None

    bsdf = find_principled_bsdf(mat)
    params = {}
    for input_name in PRINCIPLED_INPUTS:
        if input_name in bsdf.inputs:
            inp = bsdf.inputs[input_name]
            if inp.is_linked:
                # Connected to texture node
                tex_node = follow_link(inp)
                if tex_node and tex_node.type == 'TEX_IMAGE' and tex_node.image:
                    params[input_name] = {
                        'type': 'texture',
                        'path': bpy.path.abspath(tex_node.image.filepath)
                    }
            else:
                # Static value
                params[input_name] = {
                    'type': get_param_type(inp),
                    'value': inp.default_value
                }
    return params
```

UE side:
```cpp
void FMaterialManager::ApplyMaterialUpdate(
    const FSyncObjectId& ObjectId, uint32 SlotIndex,
    const FMaterialUpdate& Update)
{
    FObjectRegistry::FSyncObjectEntry* Entry = Registry->Find(ObjectId);
    if (!Entry || !Entry->MeshComp) return;

    // Create MID if not exists
    if (!Entry->Materials[SlotIndex])
    {
        Entry->Materials[SlotIndex] =
            Entry->MeshComp->CreateDynamicMaterialInstance(SlotIndex);
    }

    UMaterialInstanceDynamic* MID = Entry->Materials[SlotIndex];
    for (const auto& Param : Update.Params)
    {
        switch (Param.Type)
        {
        case EMaterialParamType::Scalar:
            MID->SetScalarParameterValue(FName(*Param.Name), Param.ScalarValue);
            break;
        case EMaterialParamType::Vector:
            MID->SetVectorParameterValue(FName(*Param.Name), Param.VectorValue);
            break;
        case EMaterialParamType::Texture:
            // Load texture from path (if exists on disk)
            UTexture2D* Tex = LoadTextureFromPath(Param.TexturePath);
            if (Tex) MID->SetTextureParameterValue(FName(*Param.Name), Tex);
            break;
        }
    }
}
```

### 15.3 FEATURE 3: Realtime Camera Sync

**Scope:**

Level 1 (v1 mandatory) — Data sync:
- Camera transform (position + rotation)
- Focal length
- Sensor size (width/height)

Level 2 (optional, not v1 blocker) — Behavior:
- Automatic viewport control (SetViewTargetWithBlend)
- Active camera switching

**NOT in scope (v1):**
- Camera near/far clip planes — **DEFERRED** from v1. `UCameraComponent` does not expose public setters. Deferred to future version pending public Plugin API support.
- Camera animation/keyframes
- Multiple cameras (only active camera synced)
- Camera effects (depth of field, motion blur)
- Projection mode switching

**Deferred items:**

| Item | Reason | Future |
|---|---|---|
| Near/far clip planes | No public `UCameraComponent` API | Revisit when UE exposes clip plane setters |
| Viewport control | Editor API dependency, PIE-only risk | Prototype in Phase 6, not MVP blocker |

**Implementation:**

Blender side:
```python
def serialize_camera(scene):
    cam_obj = scene.camera
    if not cam_obj or cam_obj.type != 'CAMERA':
        return None

    cam_data = cam_obj.data
    return {
        'position': cam_obj.location,
        'rotation': cam_obj.rotation_quaternion,
        'focal_length': cam_data.lens,
        'sensor_width': cam_data.sensor_width,
        'sensor_height': cam_data.sensor_height,
        'clip_start': cam_data.clip_start,
        'clip_end': cam_data.clip_end,
    }
```

UE side:
```cpp
void FCameraManager::ApplyCameraUpdate(const FCameraUpdate& Update)
{
    if (!CameraActor)
    {
        // Spawn camera actor
        FActorSpawnParameters Params;
        CameraActor = GetWorld()->SpawnActor<ACameraActor>(
            ACameraActor::StaticClass(), FTransform::Identity, Params);
    }

    // Level 1 (v1 mandatory): Transform + intrinsics
    CameraActor->SetActorLocation(Update.Position);
    CameraActor->SetActorRotation(Update.Rotation);

    // Intrinsics (via CineCameraComponent if available)
    if (UCineCameraComponent* CineCam = CameraActor->GetCineCameraComponent())
    {
        CineCam->SetCurrentFocalLength(Update.FocalLength);
        CineCam->Filmback.SensorWidth = Update.SensorWidth;
        CineCam->Filmback.SensorHeight = Update.SensorHeight;
    }

    // Level 2 (optional, Phase 6 prototype): Viewport control
    // DEFERRED — not v1 blocker. See Risk 3.
    // if (bSetActive && GEditor) { ... }
}
```

---

## 16. Risk Analysis

### 16.1 All Core Capabilities — Confirmed Available

Every feature in v1 scope has been verified as available through public UE plugin APIs. **No engine modifications are required for the core feature set.**

| Feature | Required API | Available? | Engine Mod Needed? |
|---|---|---|---|
| Mesh creation/update | `UProceduralMeshComponent` | YES | NO |
| Topology changes | `CreateMeshSection()` | YES | NO |
| Vertex-only updates | `UpdateMeshSection()` | YES | NO |
| Material creation | `UMaterialInstanceDynamic::Create()` | YES | NO |
| Material parameter update | `SetScalarParameterValue()` etc. | YES | NO |
| Camera spawn | `World->SpawnActor<ACameraActor>()` | YES | NO |
| Camera intrinsics | `UCineCameraComponent` properties | YES | NO |
| Active camera | `SetViewTargetWithBlend()` | YES | NO |
| Actor spawn/destroy | `World->SpawnActor()`, `Destroy()` | YES | NO |
| Actor rename | `SetActorLabel()` | YES | NO |
| Actor hierarchy | `AttachToActor()` | YES | NO |
| TCP networking | `FSocket`, `FTcpSocketBuilder` | YES | NO |

### 16.2 Potential Risk Areas

#### Risk 1: `UpdateMeshSection` Fails Silently on Vertex Count Mismatch

**Evidence:** UE source at `ProceduralMeshComponent.cpp:655` — `UpdateMeshSection` logs error and returns no-op if vertex count differs.

**Mitigation:** Blender side always checks `len(mesh.vertices)` before sending. If changed → use `CreateMeshSection` (Tier 2 path). This is already designed into the update pipeline (Section 11.4).

**Severity:** LOW — handled by design.

#### Risk 2: ProceduralMeshComponent Not Loaded in All Build Configurations

**Evidence:** PMC is an engine plugin that must be enabled. Standard UE builds include it, but custom builds might not.

**Mitigation:** Plugin descriptor declares dependency. Build will fail with clear error if PMC is unavailable.

**Severity:** LOW — standard engine plugin, always included in default builds.

#### Risk 3: `SetViewTargetWithBlend` Requires PIE World Context

**Evidence:** In editor, `GetWorld()->GetFirstPlayerController()` may return null if no PIE is running.

**Decision:** Viewport control is **not a v1 blocker**. It is a prototype/spike task deferred to Phase 6.

**Rationale:** Camera sync has two levels:
- **Level 1 (data):** Transform + focal length — pure data sync, no editor dependency. **Mandatory for v1.**
- **Level 2 (behavior):** Viewport control — depends on editor API. **Prototype in Phase 6, not MVP requirement.**

If the prototype reveals Editor API is insufficient or PIE-only, viewport control is excluded from v1 without blocking other camera features.

**Severity:** LOW — deferred to Phase 6 prototype.

#### Risk 4: Camera Near/Far Clip Cannot Be Set via Public API

**Evidence:** `UCameraComponent` does not expose `SetNearClipPlane()` / `SetFarClipPlane()` as public methods. These are internal properties.

**Decision:** **DEFERRED from v1.** Near/far clip planes are excluded from v1 scope.

**Rationale:**
- Clip planes do not affect the majority of Blender ↔ UE workflows.
- No public Plugin API supports this.
- Per project rules: do not patch Engine to support a secondary feature.
- Deferred to future version pending public Plugin API support.

**Severity:** N/A — deferred, not a v1 concern.

#### Risk 5: CineCameraComponent May Not Be Available in All Builds

**Evidence:** `CinematicCamera` module is an engine plugin. May not be present in minimal builds.

**Mitigation:** Use `#if WITH_EDITOR` or runtime module check. Fall back to base `UCameraComponent` with `SetFieldOfView()` if CineCamera unavailable.

**Severity:** LOW — CineCamera is standard in editor builds.

#### Risk 6: No TCP_NODELAY on UE Sockets

**Evidence:** UE's `FSocket` API does not expose `SetNoDelay()`. The underlying BSD socket supports it, but accessing it requires platform-specific code.

**Mitigation:** Mitigate by batching all per-frame changes into a single large `send()` call. This avoids the Nagle buffering issue naturally.

**Severity:** LOW for LAN usage. MEDIUM for WAN. v1 targets LAN.

### 16.3 Blender-Side Risks

#### Risk 7: `bpy` Calls from Non-Main Thread Cause Crash

**Evidence:** Blender's Python API is not thread-safe. All `bpy.*` calls must happen on the main thread.

**Mitigation:** Network runs on `threading.Thread`. `queue.Queue` bridges network → main thread. `bpy.app.timers` polls the queue on the main thread. No `bpy.*` call ever crosses to the network thread.

**Severity:** LOW — handled by architecture design.

#### Risk 8: `depsgraph_update_post` Fires Too Frequently

**Evidence:** Moving a mesh in viewport fires `depsgraph_update_post` every frame (60Hz+). Each event creates work.

**Mitigation:** Event queue deduplicates within a configurable window (default 33ms ≈ 30Hz). Transform-only changes batched. Geometry changes batched separately. Only the latest state per object is sent.

**Severity:** LOW — handled by Event Queue deduplication.

#### Risk 9: Blender Addon Hot-Reload During Development

**Evidence:** Blender caches Python modules. Editing addon files while Blender is running requires manual reload.

**Mitigation:** During development, use `bpy.ops.preferences.addon_refresh()` or restart Blender. In production, addon is stable and doesn't hot-reload.

**Severity:** LOW — development-only concern, not production.

#### Risk 10: Blender Version Compatibility

**Evidence:** Blender's Python API changes between major versions (4.x, 5.x). Some APIs are deprecated or removed.

**Mitigation:** Target Blender 5.1+. Use `bpy.app.version` checks for critical API differences. Keep Blender-specific code isolated in `Scene/` and `Mesh/` layers.

**Severity:** MEDIUM — must test on target Blender version. Not a blocker but requires validation.

### 16.4 No Engine Modification Required — Summary

**Evidence:** All features in v1 scope use public plugin APIs confirmed in research (Section 2.1, 2.3, 2.4).

**Conclusion:** The entire LiveSync v1 feature set can be implemented as a pure plugin with zero engine source modifications.

---

## 17. Phase Roadmap

Each phase has deliverables on **both** UE and Blender sides. No phase is complete until both sides work.

### Phase 0 — System Architecture (APPROVED)
- **Deliverable:** This document — system architecture covering both sides, shared protocol, threading, identity, ownership.
- **Status:** APPROVED. Ready for Phase 1.

### Phase 1 — Networking Foundation (Split into 4 milestones)

Each milestone is independently testable. Order is strict: 1.1 → 1.2 → 1.3 → 1.4.

#### Phase 1.1 — Protocol Spec
| Deliverable |
|---|
| `Shared/Protocol/Protocol.md` — complete wire format spec |
| `Shared/Protocol/MessageTypes.yaml` — canonical message type definitions (single source of truth) |
| `Shared/Protocol/Schemas/` — JSON Schema for each message type |
| `Shared/Protocol/TestVectors/Handshake/` — HELLO.bin, ACK.bin, REJECT.bin + .json metadata |
| `Shared/Protocol/CapabilityBitmask.md` — capability bits defined |
| Codegen validation: Python and C++ constants derived from `MessageTypes.yaml` |
- **Test:** Byte-by-byte comparison of serialized messages against test vectors.
- **Duration estimate:** 1-2 days.

#### Phase 1.2 — Serializer Interop
| Side | Deliverable |
|---|---|
| **Python** | `MessageSerializer.py` — serialize/deserialize all message types per spec |
| **C++** | `MessageSerializer.h/.cpp` — serialize/deserialize all message types per spec |
| **Test vectors** | Python serializes HELLO → compare with `HELLO.bin`. C++ serializes HELLO_ACK → compare with `HELLO_ACK.bin`. Cross-verify. |
- **Test:** Python serializes → C++ deserializes → equal. C++ serializes → Python deserializes → equal. Byte-exact match with test vectors.
- **Duration estimate:** 2-3 days.

#### Phase 1.3 — Transport (TCP + Session)
| Side | Deliverable |
|---|---|
| **UE** | `NetworkServer` (FRunnable), `SessionManager` (state machine), `MessageQueue` (MPSC) |
| **Blender** | `NetworkClient` (threading.Thread), `SessionManager` (state machine) |
| **Test** | Connect → HELLO → HELLO_ACK (with session_id) → heartbeat → disconnect |
- **Test:** Both sides connect, negotiate capabilities, exchange session_id, heartbeat works. Kill UE → Blender detects disconnect.
- **Duration estimate:** 2-3 days.

#### Phase 1.4 — Reconnect + SCENE_HASH
| Side | Deliverable |
|---|---|
| **Shared** | SCENE_HASH message spec |
| **UE** | SCENE_HASH computation, reconnect logic |
| **Blender** | SCENE_HASH computation, reconnect with backoff |
| **Test** | Kill UE → Blender reconnects → SCENE_HASH exchange → resumes. Hashes match → no full sync. Hashes differ → SCENE_FULL. |
- **Test:** Full reconnect cycle. Mismatched version → REJECT. Hash match → instant resume. Hash mismatch → full resync.
- **Duration estimate:** 1-2 days.

**Total Phase 1 estimate:** 6-10 days.

### Phase 2 — Object Identity + Transform Sync
| Side | Deliverable |
|---|---|
| **Shared** | OBJECT_CREATE, OBJECT_UPDATE, OBJECT_DELETE, OBJECT_RENAME message specs. |
| **UE** | ObjectRegistry (FGuid → actor map), actor spawn/destroy/rename, transform apply. |
| **Blender** | ObjectRegistry (UUID → bpy.types.Object), persistent IDs via custom properties, Scene Observer, Delta Builder (transform extraction). |
- **Test:** Create cube in Blender → appears in UE. Move it → moves in UE. Rename → renamed in UE. Delete → destroyed in UE.
- **Duration estimate:** 2-3 days.

### Phase 3 — Mesh Sync
| Side | Deliverable |
|---|---|
| **Shared** | MESH_DATA, MESH_DELTA, MESH_START/CHUNK/END message specs. |
| **UE** | MeshManager (PMC section management, two-tier: vertex-only vs topology). |
| **Blender** | MeshExtractor (bulk vertex/UV/normal extraction), MeshDelta (topology vs vertex-only detection). |
- **Test:** Edit mesh in Blender → see changes in UE. Vertex move → fast path. Add face → topology path.
- **Duration estimate:** 3-4 days.

### Phase 4 — Material Sync
| Side | Deliverable |
|---|---|
| **Shared** | MATERIAL_CREATE, MATERIAL_UPDATE, MATERIAL_ASSIGN message specs. |
| **UE** | MaterialManager (MID creation, parameter apply, texture path resolution). |
| **Blender** | MaterialExtractor (Principled BSDF param reading), TextureResolver (path/UUID resolution). |
- **Test:** Assign material in Blender → see it in UE. Change color/roughness → updates in UE.
- **Duration estimate:** 2-3 days.

### Phase 5 — Camera Sync (Level 1: Data Only)
| Side | Deliverable |
|---|---|
| **Shared** | CAMERA_CREATE, CAMERA_UPDATE message specs. |
| **UE** | CameraManager (camera actor spawn, transform + intrinsics apply). |
| **Blender** | CameraExtractor (focal length, sensor size, transform extraction). |
- **Test:** Move camera in Blender → UE camera actor follows. Change focal length → updates.
- **Duration estimate:** 1-2 days.

### Phase 6 — Production Hardening + Viewport Prototype
| Side | Deliverable |
|---|---|
| **UE** | Viewport control prototype (SetViewTargetWithBlend). If clean → include in v1. If not → exclude. Stress tests, memory checks, thread safety. |
| **Blender** | Stress tests, long-running session, edge cases. |
| **Shared** | Full test suite, stress test vectors. |
- **Test:** 1-hour session, 1000 objects, rapid changes. Viewport prototype decision documented.
- **Duration estimate:** 3-4 days.

**Total estimated duration:** 13-19 days (excluding review/approval time).

---

## 18. Testing Strategy

### 18.1 Per-Phase Testing

Each phase delivers deliverables on **both** UE and Blender sides. Testing covers both:

- **UE-side tests:** Build verification, runtime behavior, API calls, actor lifecycle.
- **Blender-side tests:** Addon loads, scene extraction, serialization, network send.
- **Integration tests:** Blender → UE roundtrip for each feature.
- **Regression tests:** Previous phase features still work on both sides.

### 18.2 Test Categories

| Category | Method | When |
|---|---|---|
| **Protocol roundtrip** | Serialize on Python side → deserialize on C++ side → verify match | Phase 1 |
| **UE unit test** | Build plugin, verify subsystem startup, no crash | Phase 1 |
| **Blender unit test** | Load addon, verify Scene Observer fires, no crash | Phase 1 |
| **Integration test** | Blender creates object → UE receives and spawns actor | Phase 2 |
| **Mesh roundtrip** | Blender mesh → UE PMC section → verify vertex count | Phase 3 |
| **Material roundtrip** | Blender Principled BSDF → UE MID parameters | Phase 4 |
| **Reconnect test** | Kill/restart UE, verify Blender reconnects and resyncs | Phase 1 |
| **Stress test** | 1000 objects, rapid changes, 1-hour session | Phase 6 |
| **Memory test** | Valgrind / AddressSanitizer on UE side, tracemalloc on Blender side | Phase 6 |
| **Thread safety test** | Thread Sanitizer (UE), concurrent access patterns | Phase 6 |
| **Protocol compatibility** | Cross-version tests (see §18.5) | Phase 1+ |

### 18.3 Manual Test Checklist (All Phases)

- [ ] Start UE, start Blender addon, verify connection.
- [ ] Verify capability negotiation (version mismatch → REJECT).
- [ ] Create object in Blender → appears in UE.
- [ ] Move object in Blender → moves in UE.
- [ ] Rename object in Blender → renamed in UE.
- [ ] Delete object in Blender → destroyed in UE.
- [ ] Change hierarchy in Blender → reflected in UE.
- [ ] Edit mesh in Blender → updated in UE.
- [ ] Change material in Blender → updated in UE.
- [ ] Move camera in Blender → UE camera follows.
- [ ] Kill UE → Blender reports disconnect.
- [ ] Restart UE → Blender reconnects and resyncs.
- [ ] Verify no crash after 1-hour session.
- [ ] Verify no memory growth over time (both sides).

### 18.4 Blender-Side Validation

During development, Blender-side behavior is validated by:
1. Running addon in Blender with console open.
2. Checking debug log at `~/.cache/uelivesync/uelivesync_blender_debug.log`.
3. Verifying serialization produces valid binary messages.
4. Verifying TCP connection and heartbeat work.
5. Verifying scene extraction produces correct data.

### 18.5 Protocol Compatibility Tests

Cross-version compatibility is critical for a system where UE plugin and Blender addon may update independently. These tests validate that different version combinations work correctly.

| Test Case | Blender Version | UE Plugin Version | Expected Result |
|---|---|---|---|
| Same version | v1.0 | v1.0 | Full sync — all features |
| Blender newer | v1.1 | v1.0 | Full sync — new Blender features ignored by old UE |
| UE newer | v1.0 | v1.1 | Full sync — new UE features unavailable, no crash |
| Major version gap | v2.0 | v1.0 | Graceful downgrade via capability negotiation |
| Version mismatch | v99.0 | v1.0 | REJECT — incompatible versions |

**How it works:**
- HELLO message includes version + capabilities bitmask.
- HELLO_ACK message includes accepted capabilities (subset of requested).
- Both sides only use features the other side supports.
- If minimum version requirement not met → REJECT → Disconnected.

**Test implementation:**
- Use `Shared/TestVectors/handshake/` for binary test vectors.
- Mock both sides with different version parameters.
- Verify correct ACK/REJECT behavior for each combination.

---

## Appendix A: API Reference Summary (Both Sides)

### UE APIs Used (All Public Plugin APIs)

| Category | API | Module |
|---|---|---|
| Mesh | `UProceduralMeshComponent::CreateMeshSection()` | ProceduralMeshComponent |
| Mesh | `UProceduralMeshComponent::UpdateMeshSection()` | ProceduralMeshComponent |
| Mesh | `UProceduralMeshComponent::ClearMeshSection()` | ProceduralMeshComponent |
| Material | `UMaterialInstanceDynamic::Create()` | Engine |
| Material | `UMID::SetScalarParameterValue()` | Engine |
| Material | `UMID::SetVectorParameterValue()` | Engine |
| Material | `UMID::SetTextureParameterValue()` | Engine |
| Material | `UMeshComponent::CreateDynamicMaterialInstance()` | Engine |
| Camera | `World->SpawnActor<ACameraActor>()` | Engine |
| Camera | `UCineCameraComponent::SetCurrentFocalLength()` | CinematicCamera |
| Camera | `UCineCameraComponent::Filmback` | CinematicCamera |
| Camera | `APlayerController::SetViewTargetWithBlend()` | Engine |
| Actor | `World->SpawnActor<>()` | Engine |
| Actor | `AActor::Destroy()` | Engine |
| Actor | `AActor::SetActorLabel()` | Engine |
| Actor | `AActor::AttachToActor()` | Engine |
| Actor | `AActor::DetachFromActor()` | Engine |
| Actor | `AActor::SetActorTransform()` | Engine |
| Actor | `AActor::SetActorHiddenInGame()` | Engine |
| Networking | `FTcpSocketBuilder` | Sockets/Networking |
| Networking | `FSocket::Wait()`, `Recv()`, `Send()` | Sockets |
| Networking | `ISocketSubsystem::Get()` | Sockets |
| Threading | `FRunnable`, `FRunnableThread` | Core |
| Threading | `TQueue<T, EQueueMode::Mpsc>` | Core |
| Subsystem | `UWorldSubsystem` | Engine |

### Blender APIs Used

| Category | API |
|---|---|
| Mesh data | `mesh.vertices.foreach_get("co", arr)` |
| Mesh data | `mesh.loops.foreach_get("normal", arr)` |
| Mesh data | `mesh.uv_layers[i].data.foreach_get("uv", arr)` |
| Mesh data | `mesh.polygons` (face indices) |
| Mesh evaluated | `obj.evaluated_get(depsgraph).to_mesh()` |
| Material | `obj.material_slots[i].material` |
| Material | `node.inputs["X"].default_value` |
| Material | `bpy.path.abspath(image.filepath)` |
| Camera | `cam_data.lens`, `.sensor_width`, `.clip_start`, `.clip_end` |
| Camera | `cam_obj.location`, `cam_obj.rotation_quaternion` |
| Change detection | `bpy.app.handlers.depsgraph_update_post` |
| Change flags | `update.is_updated_geometry`, `.is_updated_transform` |
| Threading | `threading.Thread`, `queue.Queue` |
| Timers | `bpy.app.timers.register()` |
| Networking | `socket.socket(AF_INET, SOCK_STREAM)` |
| Persistent ID | `obj["sync_id"] = uuid_string` |
| Serialization | `struct.pack()`, `struct.unpack()` |

---

*End of System Architecture Document. Phase 0 APPROVED. Ready for Phase 1.*
