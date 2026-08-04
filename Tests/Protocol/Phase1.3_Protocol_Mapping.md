# Phase 1.3 — Protocol Migration Mapping

## Summary

- **15 old PT_* types** map cleanly to new MsgType
- **2 replaced** by SCENE_FULL framing (BeginSnapshot, EndSnapshot)
- **9 MISSING** from new protocol — need design decisions
- **20 new MsgTypes** have no old equivalent (pure new features)
- **1 dead code** (PT_Reserved_02)

## Complete Cross-Reference Matrix

| # | Old (PT_*) | Opcode | New (MsgType) | Opcode | Map Type | Status |
|---|---|---|---|---|---|---|
| 1 | PT_Transform | 0x01 | OBJECT_UPDATE | 0x21 | Partial 1:1 | MAPPED (parent separated, primitive type lost) |
| 2 | PT_Reserved_02 | 0x02 | -- | -- | None | DEAD CODE |
| 3 | PT_Create | 0x03 | OBJECT_CREATE | 0x20 | 1:1 | MAPPED (Phase 1.5: legacy 0x03 DECOMMISSIONED; semantic OBJECT_CREATE only) |
| 4 | PT_Delete | 0x04 | OBJECT_DELETE | 0x22 | 2:1 (with V5) | MAPPED (full Phase 6E semantic: seq+ts+tombstone) |
| 5 | PT_Material | 0x05 | MATERIAL_CREATE/UPDATE/ASSIGN | 0x40/41/42 | 1:3 | MAPPED (cleaner separation) |
| 6 | PT_Mesh | 0x06 | MESH_DATA/DELTA/START/CHUNK/END | 0x30-34 | 1:5 | MAPPED (chunked transfer new) |
| 7 | PT_Heartbeat | 0x07 | HEARTBEAT + HEARTBEAT_ACK | 0x00/01 | 1:2 | MAPPED (ack added) |
| 8 | PT_AssetDef | 0x08 | -- | -- | NONE | **MISSING** |
| 9 | PT_BeginSnapshot | 0x09 | (implicit in SCENE_FULL) | 0x03 | Conceptual | REPLACED |
| 10 | PT_EndSnapshot | 0x0A | (implicit in SCENE_FULL) | 0x03 | Conceptual | REPLACED |
| 11 | PT_Visibility | 0x0B | OBJECT_VISIBILITY | 0x25 | 1:1 | MAPPED |
| 12 | PT_Rename | 0x0C | OBJECT_RENAME | 0x23 | 1:1 | MAPPED |
| 13 | PT_Hierarchy | 0x0D | OBJECT_REPARENT | 0x24 | 1:1 | MAPPED |
| 14 | PT_Delete_V5 | 0x0E | OBJECT_DELETE | 0x22 | 2:1 (with V3) | MAPPED (full Phase 6E semantic: seq+ts+tombstone) |
| 15 | PT_Collection | 0x0F | -- | -- | NONE | **MISSING** |
| 16 | PT_CapabilityAnnounce | 0x11 | HELLO | 0x10 | Conceptual | MAPPED |
| 17 | PT_CapabilityResponse | 0x12 | HELLO_ACK | 0x11 | Conceptual | MAPPED |
| 18 | PT_Timeline | 0x13 | -- | -- | NONE | **MISSING** |
| 19 | PT_PlaybackState | 0x14 | -- | -- | NONE | **MISSING** |
| 20 | PT_ActiveCamera | 0x15 | CAMERASETACTIVE | 0x52 | 1:1 | MAPPED |
| 21 | PT_FBXImportRequest | 0x16 | FBX_IMPORT_REQUEST | 0x60 | 1:1 | MAPPED (Phase 1.5: legacy 0x16 DECOMMISSIONED; semantic 0x60 only) |
| 22 | PT_Keyframe | 0x17 | -- | -- | NONE | **MISSING** |
| 23 | PT_SequencerOp | 0x18 | -- | -- | NONE | **MISSING** |
| 24 | PT_TimelineState | 0x19 | -- | -- | NONE | **MISSING** |
| 25 | PT_PlaybackTransport | 0x1A | -- | -- | NONE | **MISSING** |
| 26 | PT_CameraDef | 0x1B | CAMERA_CREATE/UPDATE | 0x50/51 | 1:2 | PARTIAL (clip planes, ortho missing) |

## 9 Missing Message Types (Need Design)

| # | Old Packet | Payload | Complexity | Recommendation |
|---|---|---|---|---|
| 1 | PT_AssetDef | 33 bytes | Low | Design ASSET_DEFINE or embed in MATERIAL_CREATE/MESH_DATA |
| 2 | PT_Collection | Variable | High | Design COLLECTION_UPDATE or defer to application layer |
| 3 | PT_Timeline | 36 bytes | Medium | Design TIMELINE_STATE |
| 4 | PT_PlaybackState | 14 bytes | Low | Design PLAYBACK_STATE |
| 5 | PT_Keyframe | 14+N*25 bytes | High | Design KEYFRAME_BATCH |
| 6 | PT_SequencerOp | 16+opcode-specific | High | Design SEQUENCE_OP with sub-opcodes |
| 7 | PT_TimelineState | 20 bytes | Low | Design TIMELINE_UPDATE or merge with #3 |
| 8 | PT_PlaybackTransport | 6 bytes | Low | Design PLAYBACK_COMMAND |
| 9 | PT_CameraDef | 44 bytes | Medium | Extend CAMERA_CREATE/UPDATE with optional fields |

## Header Incompatibilities

| Issue | Old | New | Migration Impact |
|---|---|---|---|
| UUID encoding | Windows GUID mixed-endian | RFC 4122 network order | ALL GUIDs must be byte-swapped |
| SequenceId | uint64 (8 bytes) | uint32 (4 bytes) | Wraparound handling differs |
| ObjectCount | In header (per-packet) | Per-message where needed | Receiver cannot pre-allocate from header |
| PacketSize | In header | Length-prefixed framing | Frame parser change |
| Version | In every header | Only in HELLO/HELLO_ACK | Cannot detect version mid-stream |
| Magic | 0x4C56534D per packet | No magic; framing-based | Transport-level validation |
| Transform payload | 80/81 bytes (includes parent+timestamp+primitive) | 40 bytes (pos+rot+scale only) | Parent, timestamp, primitive type from other sources |
