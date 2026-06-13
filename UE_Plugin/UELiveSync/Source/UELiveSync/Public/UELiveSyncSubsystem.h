#pragma once

#include "CoreMinimal.h"

#include "Subsystems/WorldSubsystem.h"

#include "Containers/Ticker.h"

#include "Misc/Guid.h"

#include "SyncTypes.h"
#include "LiveSyncQueue.h"
#include "PendingAssetQueue.h"

#include "Containers/Set.h"

#include "UELiveSyncSubsystem.generated.h"


class FSocket;
class ULevelSequence;
class UTexture2D;

class FRunnableThread;

class FLiveSyncRunnable;


// =========================================================
// LIVE SYNC SUBSYSTEM
// =========================================================

UCLASS()
class UELIVESYNC_API UUELiveSyncSubsystem
    : public UWorldSubsystem
{
    GENERATED_BODY()

public:

    // =====================================================
    // LIFECYCLE
    // =====================================================

    virtual void Initialize(
        FSubsystemCollectionBase&
        Collection) override;

    virtual void Deinitialize()
        override;

    // =====================================================
    // TICK
    // =====================================================

    bool Tick(
        float DeltaTime);

#if WITH_EDITOR
    // =====================================================
    // EDITOR STATE (polled by Slate widget)
    // =====================================================

    FText GetConnectionStatusText() const;

    FText GetUptimeText() const;

    FText GetObjectsTrackedText() const;

    FText GetQueueDepthText() const;

    FText GetLastPacketTimeText() const;

    FText GetDiagnosticsText();
#endif

    // =====================================================
    // DEFERRED FBX REPAIR (Phase 10J.5D.5)
    // =====================================================
    void RepairAllFBXActors();

private:

    // =====================================================
    // NETWORK LAYER
    // =====================================================

    void StartServer();

    void StartNetworkThread();

    void StopNetworkThread();

    // =====================================================
    // PACKET PIPELINE
    // =====================================================

    void ProcessQueuedPackets();

    void ProcessBinaryPacket(
        const FLiveSyncPacket&
        Packet,
        TSet<FGuid>* SeenThisTick = nullptr);

    // =====================================================
    // TRANSFORM PIPELINE
    // =====================================================

    void UpdateTargetTransform(

        const FGuid& Guid,

        const FVector& Location,

        const FQuat& Rotation,

        const FVector& Scale,

        const FGuid& ParentGuid = FGuid(),

        bool bIsLocalTransform = false
    );

    void InterpolateTransforms(
        float DeltaTime);

    void EvictStaleTransformStates();

    // =====================================================
    // HIERARCHY
    // =====================================================

    void AttachToParent(
        const FGuid& Guid,
        const FGuid& ParentGuid);

    void DetachFromParent(
        const FGuid& Guid);

    // =====================================================
    // PACKET TYPE HANDLERS
    // =====================================================

    void HandleCreateObject(

        const FGuid& Guid,

        const FVector& Location,

        const FQuat& Rotation,

        const FVector& Scale,

        const FGuid& ParentGuid,

        uint8 PrimitiveType = LSP_Cube,

        bool bIsLocalTransform = false);

    void HandleDeleteObject(
        const FGuid& Guid);

    void HandleBeginSnapshot();

    void HandleEndSnapshot();

    void AbortSnapshot();

    // =====================================================
    // RENAME REPLICATION (Phase 6 — Semantic Event)
    // See Docs/Architecture/19-phase6-vertical-slice-rename.md
    // =====================================================

    void HandleRename(
        const FGuid& Guid,
        const FString& OldName,
        const FString& NewName,
        uint32 SequenceNumber,
        double Timestamp,
        EChangeOrigin Origin);

    // =====================================================
    // VISIBILITY REPLICATION (Phase 6 — Semantic Event)
    // See Docs/Architecture/21-phase6-vertical-slice-visibility.md
    // =====================================================

    void HandleVisibility(
        const FGuid& Guid,
        bool bHidden,
        uint32 SequenceNumber,
        double Timestamp,
        EChangeOrigin Origin);

    // =====================================================
    // HIERARCHY REPLICATION (Phase 6D — Semantic Event)
    // See Docs/Architecture/24-phase6D-hierarchy-scope-lock.md
    // =====================================================

    // Stage 8: Orphan lifecycle formalization
    enum class EOrphanState : uint8
    {
        DEFERRED       = 0,  // Just enqueued — intent recorded
        RETRYING       = 1,  // Active retry (fast or slow phase)
        RESOLVED       = 2,  // Parent found, attachment applied
        EVICTED        = 3,  // Timeout or overflow — dropped
        STALE_REJECTED = 4,  // Tracker advanced while deferred (FINDING-001)
    };

    struct FPendingHierarchyAttachment
    {
        FGuid ChildGuid;
        FGuid ParentGuid;
        uint32 Sequence;
        double CreatedTime;
        int32 RetryCount;
        EChangeOrigin Origin;
        EOrphanState State;  // Stage 8: explicit orphan lifecycle state
    };

    void HandleHierarchy(
        const FGuid& ChildGuid,
        const FGuid& ParentGuid,
        uint32 SequenceNumber,
        double Timestamp,
        EChangeOrigin Origin);

    void ResolveHierarchyAttachments();

    // Stage 9: Explicit cycle detection — bounded parent-chain walk
    bool WouldCreateHierarchyCycle(
        const FGuid& ChildGuid,
        const FGuid& ParentGuid);

    // =====================================================
    // LIFECYCLE/DELETE REPLICATION (Phase 6E)
    // =====================================================

    void HandleDelete(
        const FGuid& TargetGuid,
        uint32 SequenceNumber,
        double Timestamp,
        EChangeOrigin Origin);

    // =====================================================
    // COLLECTION REPLICATION (Phase 6F)
    // =====================================================

    void HandleCollection(
        const FGuid& TargetGuid,
        uint8 OpType,
        uint8 OpFlags,
        uint32 SequenceNumber,
        double Timestamp,
        const FGuid* CollectionGuid = nullptr);

    // =====================================================
    // COLLECTION REPLAY + SNAPSHOT (Phase 6F Stage 5)
    // =====================================================

    /** Record a raw collection payload to the replay ring buffer. */
    void RecordCollectionReplayPayload(const uint8* Payload, int32 PayloadSize, uint32 SequenceNumber = 0);

    /** Enable/disable collection replay recording. */
    void SetCollectionReplayEnabled(bool bEnabled);

    /** Clear and replay the recorded collection packet stream. */
    void ReplayCollectionStream();

    /** Export the entire collection state as a canonical snapshot. */
    FString ExportCollectionSnapshot() const;

    /** Rebuild collection state from a canonical snapshot string. */
    bool RebuildCollectionFromSnapshot(const FString& Snapshot);

    /** Compute deterministic hash of current collection state. */
    uint64 ComputeCollectionStateHash() const;

    // =====================================================
    // COLLECTION OBSERVABILITY (Phase 6F Stage 7)
    // =====================================================

    /** Record a timeline event during replay. */
    void RecordReplayTimelineEvent(const FReplayTimelineEvent& Event);

    /** Clear replay timeline. */
    void ClearReplayTimeline();

    /** Get replay timeline (const ref). */
    const FReplayTimeline& GetReplayTimeline() const;

    /** Emit a verbose replay trace if tracing is enabled. */
    void EmitReplayTrace(
        EReplayTraceCategory Category,
        const FString& Message);

    /** Check if replay tracing is active for a given category. */
    bool IsReplayTracingActive(EReplayTraceCategory Category) const;

    /** Toggle replay tracing at runtime. */
    void SetReplayTracingEnabled(bool bEnabled, EReplayTraceCategory CategoryMask = EReplayTraceCategory::All);

    /** Record replay timing sample. */
    void RecordReplayTiming(double DurationMs, double RebuildMs, double HashVerifyMs);

    /** Get rolling replay window stats. */
    const FReplayWindowStats& GetReplayWindowStats() const;

    /** Export replay buffer state as text. */
    FString DumpReplayBuffer() const;

    /** Export collection membership graph as text. */
    FString DumpCollectionGraph() const;

    /** Force a replay verification run (idempotent, non-mutating). */
    FString ForceReplayVerification();

    /** Clear all replay observability diagnostics. */
    void ClearReplayDiagnostics();

    /** Check replay buffer health; emit warnings near capacity. */
    void CheckReplayBufferHealth();

    /** Export current collection state diagnostics. */
    FString ExportCollectionDiagnostics() const;

    // =====================================================
    // UNIFIED WORLD REPLAY (Phase 6G)
    // =====================================================

    /** Record a world replay entry from any domain. */
    void RecordWorldReplayEntry(const FWorldReplayEntry& Entry);

    /** Enable/disable world replay recording. */
    void SetWorldReplayEnabled(bool bEnabled);

    /** Compute deterministic world-state hash across all domains. */
    uint64 ComputeWorldStateHash() const;

    /** Save current world state for rollback. */
    void SaveWorldState();

    /** Restore world state from last save point. */
    void RestoreWorldState();

    /** Verify world replay by replaying buffer and comparing hash. */
    FString VerifyWorldReplay();

    /** Export unified world snapshot as canonical text. */
    FString ExportWorldSnapshot() const;

    /** Rebuild world state from canonical snapshot text. */
    bool RebuildWorldFromSnapshot(const FString& Snapshot);

    /** Dump world replay state for developer diagnostics. */
    FString DumpWorldReplayState() const;

    /** Check cross-domain dependency ordering in the replay buffer. */
    void CheckReplayDependencies();


    // =====================================================
    // ASSET RESOLUTION (Phase 5D)
    // =====================================================

    void HandleAssetDef(
        const FGuid& Guid,
        uint64 IdentityHigh,
        uint64 IdentityLow,
        uint8 PrimitiveFallback);

    void ResolvePendingAssets();

    void AssignStaticMesh(
        const FGuid& Guid,
        const FSoftObjectPath& Path);

    void AssignFallbackPrimitive(
        const FGuid& Guid,
        uint8 PrimitiveType);

    void CacheAssetPath(
        const FAssetIdentityRef& Identity,
        const FSoftObjectPath& Path);

    // =====================================================
    // MATERIAL RESOLUTION (Phase 7B Stage 1C/1D)
    // =====================================================

    /** Parse and store material slot metadata from PT_Material. */
    void HandleMaterialDef(
        const FGuid& Guid,
        const TArray<FMaterialSlotRef>& Slots,
        uint32 ObjectCount);

    /** Resolve pending material identities to UMaterialInterface paths
     *  and call SetMaterial on component slots. Runs per tick.
     *  Does NOT maintain a retry queue — each tick iterates all
     *  unresolved entries. Resolution is path-cache driven.
     */
    void ResolvePendingMaterials();

    /** Register a material identity → path mapping in MaterialPathCache.
     *  Logs a warning on identity collision (same identity, different path).
     */
    void CacheMaterialPath(
        const FMaterialIdentityRef& Identity,
        const FSoftObjectPath& Path);

    /** Parse MATX extension block after old identity data and apply
     *  generated material to the resolved FBX actor/component.
     */
    bool ParseAndApplyGeneratedMaterial(
        const FGuid& Guid,
        const TArray<FMaterialSlotBasicProperties>& BasicProps);

    /** Create or update a UMaterialInstanceDynamic from BasicShapeMaterial
     *  with the given basic properties.
     */
    UMaterialInstanceDynamic* GetOrCreateGeneratedMID(
        const FGuid& Guid,
        int32 SlotIndex,
        const FMaterialSlotBasicProperties& Props);

    /** Build a consistent cache key for generated materials. */
    FString MakeGeneratedMaterialKey(
        const FGuid& Guid,
        int32 SlotIndex) const;

    /** Phase 10K.2: import textures from MTEX records.
     *  Iterates TexMaps, checks cache/skip conditions, imports
     *  UTexture2D under /Game/UELiveSync/Textures/, sets sRGB
     *  policy per channel, populates TextureImportCache.
     *  Does NOT apply textures to materials.
     */
    void ImportTexturesFromMtexRecs(
        const FGuid& Guid,
        const TArray<FMaterialTextureMapRef>& TexMaps);

    /** Phase 10K.3: apply imported/cached MTEX textures to a
     *  generated material MID. Looks up MaterialTextureMapCache
     *  and TextureImportCache for the given GUID + SlotIndex.
     *  Sets texture parameters on the MID. Logs per-channel.
     */
    bool ApplyImportedTexturesToGeneratedMID(
        const FGuid& Guid,
        int32 SlotIndex,
        class UMaterialInstanceDynamic* MID);

    // =====================================================
    // MESH CHUNK REASSEMBLY (Phase 7C Stage 1B/1C)
    // =====================================================

    /** Parse, validate, and store a FULL_ATTR v1 mesh chunk payload.
     *  Returns true if the payload is valid and OutParsedChunk is filled.
     *  On false the chunk is rejected (no data stored).
     */
    bool ParseV1MeshPayload(
        const FGuid& Guid,
        uint32 ChunkIndex,
        uint32 ChunkCount,
        const TArrayView<const uint8>& Payload,
        FV1MeshParsedChunk& OutParsedChunk);

    /** Build ProceduralMeshComponent sections from completed v1 reassemblies.
     *  Called from ReconstructCompletedMeshes after the V5 loop.
     *  Iterates PendingV1MeshReassembly and builds one section per
     *  completed reassembly. Clears successfully built entries.
     */
    void BuildV1MeshFromReassembly();

    /** Parse, validate, and store one PT_Mesh chunk. */
    void HandleMeshChunk(
        const FGuid& Guid,
        const FString& VersionHash,
        uint32 ChunkIndex,
        uint32 ChunkCount,
        uint8 Flags,
        const TArrayView<const uint8>& Payload);

    /** Tick handler: consumes completed mesh reassemblies and
     *  builds UProceduralMeshComponent sections.  Iterates
     *  PendingMeshReassembly and processes IsComplete() entries
     *  that have not yet been reconstructed.
     */
    void ReconstructCompletedMeshes();

    // =====================================================
    // TIMELINE STATE (Phase 7B)
    // =====================================================

    void HandleTimeline(
        const FTimelinePayload& Payload);

    // =====================================================
    // PLAYBACK STATE (Phase 7C)
    // =====================================================

    void HandlePlaybackState(
        const FPlaybackStatePayload& Payload);

    // =====================================================
    // ACTIVE CAMERA (Phase 7D)
    // =====================================================

    void HandleActiveCamera(
        const FActiveCameraPayload& Payload);

    // =====================================================
    // SEQUENCER OP (Phase 7E)
    // =====================================================

    void HandleSequencerOp(
        const FSequencerOpHeader& Header,
        const uint8* PayloadPtr,
        int32 PayloadSize);

    // Phase 7E Stage 7: Keyframe replication — validate and store, no sequencer mutation
    void HandleKeyframe(
        const FKeyframeHeader& Header,
        const uint8* PayloadPtr,
        int32 PayloadSize);

    // =====================================================
    // ACTOR CACHE
    // =====================================================

    void BuildActorCache();

    void TryCacheActor(
        AActor* Actor);

    UFUNCTION()
    void OnActorSpawned(
        AActor* Actor);

    UFUNCTION()
    void OnActorDestroyed(
        AActor* Actor);

    AActor* FindActorFast(
        const FGuid& Guid);

    FGuid FindGuidForActor(
        AActor* Actor) const;

private:

    // =====================================================
    // SOCKETS
    // =====================================================

    FSocket* ListenerSocket =
        nullptr;

    FSocket* ConnectionSocket =
        nullptr;

    // =====================================================
    // THREADING
    // =====================================================

    FRunnableThread* NetworkThread =
        nullptr;

    FLiveSyncRunnable* NetworkRunnable =
        nullptr;

    // Phase 6I.1 Stage 2: guards against concurrent StartNetworkThread calls
    std::atomic<bool> bNetworkThreadStarting{false};

    // =====================================================
    // THREAD → GAME QUEUE
    // =====================================================

    FLiveSyncQueue
        PacketQueue;

    // =====================================================
    // GUID ACTOR CACHE
    // =====================================================

    TMap<
        FGuid,
        TWeakObjectPtr<AActor>>

        ActorCache;

    // =====================================================
    // GUID TRANSFORM STATES
    // =====================================================

    TMap<
        FGuid,
        FSyncTransformState>

        TransformStates;

    // =====================================================
    // TICK HANDLE
    // =====================================================

    FTSTicker::FDelegateHandle
        TickHandle;

    // =====================================================
    // PROTOCOL STATE
    // =====================================================

    static constexpr uint16
        ProtocolVersion =
        LIVE_SYNC_VERSION;

    // =====================================================
    // ANTI-REORDER / DUPLICATE
    // =====================================================

    uint64 LastSequenceId =
        0;

    // =====================================================
    // ACTOR LIFECYCLE BINDING
    // =====================================================

    FDelegateHandle
        OnActorSpawnedHandle;

    FDelegateHandle
        OnActorDestroyedHandle;

    // =====================================================
    // HEARTBEAT
    // (timeout sourced from CVar UE.LiveSync.HeartbeatTimeout)
    // =====================================================

    double LastHeartbeatTime =
        0.0;

    // =====================================================
    // METRICS
    // =====================================================

    FLiveSyncStats Stats;

    void LogRuntimeMetrics();

    void LogRuntimeMetricsVerbose();

    // Rate tracking state
    double LastRateSampleTime =
        0.0;

    int64 LastRateSampleBytes =
        0;

    int32 LastRateSamplePackets =
        0;

    // =====================================================
    // INGRESS HEALTH
    // =====================================================
    // Lightweight check that the Tick-driven ingress
    // pipeline is functioning. Checks:
    //   - Tick has executed recently
    //   - Network thread is alive
    //   - Listener socket is valid
    //   - Not in NullRHI mode (known blocker)
    // =====================================================

    struct FIngressHealthResult
    {
        bool   bTickActive = false;
        bool   bNetworkThreadAlive = false;
        bool   bListenerValid = false;
        bool   bNullRHI = false;
        double SecondsSinceLastTick = -1.0;
        double SecondsSinceLastThreadLoop = -1.0;
        FString ToString() const;
    };

    FIngressHealthResult IsIngressHealthy() const;

    // =====================================================
    // CONSOLE COMMANDS
    // =====================================================

    void ConsoleDumpState();

    void ConsoleReset();

    void ConsolePing();

    void ConsoleStats();

    // Phase 6F Stage 7 — Observability console commands
    void ConsoleDumpReplayBuffer();

    void ConsoleDumpCollectionGraph();

    void ConsoleVerifyCollectionReplay();

    void ConsoleClearReplayDiagnostics();

    void ConsoleToggleReplayTracing();

    // Phase 6G — Unified world replay console commands
    void ConsoleDumpWorldReplayState();

    void ConsoleVerifyWorldReplay();

    void ConsoleDumpReplayTimeline();

    void ConsoleExportWorldSnapshot();

    // =====================================================
    // PHASE 6H — SEMANTIC CONSISTENCY HARDENING
    // =====================================================
    // Stabilization + determinism + replay-hardening phase.
    // All functions are additive, low-risk, diagnostics-oriented.
    // See Docs/CRITICAL_INVARIANTS.md for guarded invariants.
    // =====================================================

    // ── Goal A: Packet Ordering Validation ───────────────
    void ConsoleValidatePacketOrdering();
    void ValidatePacketOrdering(const FLiveSyncPacket& Packet);

    // ── Goal B: Semantic Authority Audit ─────────────────
    void ConsoleVerifySemanticState();
    FString VerifySemanticState();
    void ConsoleDumpAuthorityState();
    FString DumpAuthorityState();
    bool CheckParentAuthority(const FGuid& Guid);
    bool CheckVisibilityAuthority(const FGuid& Guid);
    bool CheckRenameAuthority(const FGuid& Guid);
    bool CheckCollectionAuthority(const FGuid& Guid);

    // ── Goal C: Replay Fuzz / Stress Harness ─────────────
    void ConsoleRunReplayFuzz(const TArray<FString>& Args);
    void RunReplayFuzz(int32 Seed, int32 Iterations);
    void ConsoleRunHierarchyStress(const TArray<FString>& Args);
    void RunHierarchyStress(int32 ObjectCount, int32 Operations);
    void ConsoleRunReconnectStress(const TArray<FString>& Args);
    void RunReconnectStress(int32 CycleCount);

    // ── Goal D: Burst Operation Metrics ──────────────────
    struct FBurstMetrics
    {
        int32 PeakPacketsPerTick = 0;
        double ReplayQueueGrowthRate = 0.0;
        int32 RollbackCount = 0;
        int32 DivergenceCount = 0;
    };
    FBurstMetrics GetBurstMetrics() const;

    // ── Goal E: Semantic Replay Verification ─────────────
    void ConsoleVerifyReplayDeterminism();
    FString VerifyReplayDeterminism();

    // ── Goal F: Known-Bad-Pattern Enforcement ────────────
    void EnforceKnownBadPatterns();
    void ConsoleEnforceKnownBadPatterns();
    void CheckTransformGateSemanticEvents();
    void CheckStaleLocalAuthority();

    // =====================================================
    // ASSET RESOLUTION DATA (Phase 5D)
    // =====================================================

    // Per-object asset metadata (outside hot transform path)
    TMap<FGuid, FAssetMetadata> AssetMetadata;

    // Asset identity → resolved path cache (dedup)
    TMap<FAssetIdentityRef, FSoftObjectPath> AssetPathCache;

    // Pending resolution queue
    FPendingAssetQueue PendingAssetQueue;

    // =====================================================
    // MATERIAL METADATA (Phase 7B Stage 1C/1D)
    // =====================================================

    // Per-GUID material slot metadata: GUID → array of FMaterialSlotRef.
    TMap<FGuid, TArray<FMaterialSlotRef>> MaterialMetadata;

    // Material identity → resolved path cache (dedup)
    TMap<FMaterialIdentityRef, FSoftObjectPath> MaterialPathCache;

    // Count of PT_Material packets received and processed this session
    int32 MaterialDefsReceived = 0;

    // Count of successful SetMaterial() calls this session
    int32 MaterialAssignmentsSucceeded = 0;

    // =====================================================
    // GENERATED MATERIAL CACHE (Phase 10J.5H)
    // =====================================================
    // Cache key: "GUID8_SlotIndex" (see MakeGeneratedMaterialKey).
    // Stores runtime MID created from Blender material properties.
    TMap<FString, TObjectPtr<UMaterialInstanceDynamic>> GeneratedMaterialCache;

    // Count of generated material applications.
    int32 MaterialGeneratedApplied = 0;

    // =====================================================
    // TEXTURE MAP METADATA CACHE (Phase 10K.1)
    // =====================================================
    // Per-GUID texture map references: GUID → array of FMaterialTextureMapRef.
    // Phase 10K.1: diagnostic/logging only — no texture importing or applying.
    TMap<FGuid, TArray<FMaterialTextureMapRef>> MaterialTextureMapCache;

    // Count of MTEX packets parsed
    int32 MtexBlocksParsed = 0;
    // Count of MTEX records parsed
    int32 MtexRecordsParsed = 0;
    // Count of malformed MTEX blocks rejected
    int32 MtexMalformed = 0;

    // Phase 10K.2: texture import counters
    int32 TextureImportRequested = 0;
    int32 TextureImportSkipped = 0;
    int32 TextureCacheHit = 0;
    int32 TextureResolveSkipped = 0;
    int32 TextureImportFailed = 0;

    // Path → imported texture cache (Phase 10K.2)
    TMap<FString, TSoftObjectPtr<UTexture2D>> TextureImportCache;

    // Phase 10K.3: texture material apply counters
    int32 TextureMaterialApplyRequests = 0;
    int32 TextureMaterialApplySucceeded = 0;
    int32 TextureMaterialApplySkipped = 0;
    int32 TextureMaterialApplyFailed = 0;

    // =====================================================
    // FBX AUTHORITY (Phase 10J.5E)
    // =====================================================
    // Per-GUID set of FBX-authoritative GUIDs. Once a GUID has
    // been promoted to FBX/StaticMeshAuthority, PT_Mesh packets
    // for that GUID must not spawn/update a procedural mesh.
    TSet<FGuid> FBXAuthoritativeGuids;

    // Phase 10J.5K: Per-GUID set of FBX-pending GUIDs. While pending,
    // PT_Mesh for that GUID is rejected to prevent race between
    // PT_FBXImportRequest and PT_Mesh.
    TSet<FGuid> FBXPendingGuids;

    // =====================================================
    // DEFERRED FBX REPAIR (Phase 10J.5D.5)
    // =====================================================
    struct FDeferredFBXRepairEntry
    {
        FGuid  Guid;
        int32  PassNumber   = 0;   // 1=next-tick, 2=delayed
        double ScheduleTime = 0.0;
    };
    TArray<FDeferredFBXRepairEntry> DeferredFBXRepairs;

    void ProcessDeferredRepairs();

    // =====================================================
    // MESH CHUNK REASSEMBLY DATA (Phase 7C Stage 1B)
    // =====================================================

    /** Tracks accumulated chunks for one mesh reconstruction. */
    struct FMeshReassemblyState
    {
        FString    VersionHash;
        uint32     ChunkCount     = 0;
        uint32     ChunksReceived = 0;
        uint8      Flags          = 0;
        double     FirstChunkTime = 0.0;
        bool       bReconstructed = false;  // Stage 1C: set after mesh sections built

        // ChunkIndex → raw payload bytes
        TMap<uint32, TArray<uint8>> Chunks;

        bool IsComplete() const
        {
            return ChunkCount > 0 && ChunksReceived >= ChunkCount;
        }
    };

    // GUID → reassembly state
    TMap<FGuid, FMeshReassemblyState> PendingMeshReassembly;

    // (Guid, VersionHash) → v1 reassembly state
    TMap<FV1MeshReassemblyKey, FV1MeshReassemblyState> PendingV1MeshReassembly;

    // Total PT_Mesh chunks received this session
    uint32 MeshChunksReceived = 0;

    // Total reassemblies completed this session
    uint32 MeshReassembliesCompleted = 0;

    // Total mesh sections built successfully this session
    uint32 MeshSectionsBuilt = 0;

    // Mesh schema counters live on FLiveSyncStats (Stats.MeshSchema*)
    // =====================================================
    // PLAYBACK STATE (Phase 7C)
    // =====================================================

    // Most recently received and applied playback state (PLAY=0/PAUSE=1/STOP=2)
    uint8 LastPlaybackState = 0;

    // Whether a playback state packet has been received at least once
    bool bHasPlaybackState = false;

    // Sequence number of the last applied playback packet
    uint32 LastPlaybackSequence = 0;

    // Timestamp of the last applied playback packet
    double LastPlaybackTimestamp = 0.0;

    // =====================================================
    // TIMELINE STATE (Phase 7B)
    // =====================================================

    // Most recently received and applied timeline frame state
    FTimelinePayload LastTimelineState;

    // Whether a timeline packet has been received at least once
    bool bHasTimelineState = false;

    // Sequence number of the last applied timeline packet
    uint32 LastTimelineSequence = 0;

    // Timestamp of the last applied timeline packet
    double LastTimelineTimestamp = 0.0;

    // =====================================================
    // CAPABILITY NEGOTIATION (Phase 9)
    // =====================================================

    // Capability bitmask received from Blender via PT_CapabilityAnnounce
    uint32 RemoteCapabilities = 0;

    // Whether a PT_CapabilityResponse has been sent back to Blender
    // (reset on each new announce)
    bool bCapabilityResponseSent = false;

    // =====================================================
    // ACTIVE CAMERA STATE (Phase 7D)
    // =====================================================

    // Whether the most recent applied GUID is a non-null camera
    bool bHasActiveCamera = false;

    // Whether any active camera packet has ever been received (used for stale-check gating)
    bool bHasEverReceivedActiveCamera = false;

    // GUID of the last applied active camera (all-zero = no active camera)
    FGuid LastActiveCameraGUID;

    // Sequence number of the last applied active camera packet
    uint32 LastActiveCameraSequence = 0;

    // Timestamp of the last applied active camera packet
    double LastActiveCameraTimestamp = 0.0;

    // =====================================================
    // SEQUENCER OP STATE (Phase 7E)
    // =====================================================

    // Whether a PT_SequencerOp packet has been received at least once
    bool bHasSequencerOpState = false;

    // Opcode of the last applied sequencer op
    uint8 LastSequencerOpOpcode = 0;

    // Flags of the last applied sequencer op
    uint8 LastSequencerOpFlags = 0;

    // Sequence number of the last applied sequencer op
    uint32 LastSequencerOpSequence = 0;

    // Timestamp of the last applied sequencer op
    double LastSequencerOpTimestamp = 0.0;

    // =====================================================
    // KEYFRAME STATE (Phase 7E Stage 7)
    // =====================================================

    // Whether a PT_Keyframe packet has been received at least once
    bool bHasKeyframeState = false;

    // Sequence number of the last applied keyframe packet
    uint32 LastKeyframeSequence = 0;

    // Timestamp of the last applied keyframe packet
    double LastKeyframeTimestamp = 0.0;

    // Transient ULevelSequence owned by this subsystem
    TWeakObjectPtr<ULevelSequence> LiveSyncSequence;

    // Whether LiveSyncSequence has been created and is valid
    bool bHasLiveSyncSequence = false;

    // Frame range of the live sync sequence
    int32 LiveSyncSequenceFrameStart = 0;
    int32 LiveSyncSequenceFrameEnd   = 0;

    // Display rate of the live sync sequence
    int32 LiveSyncSequenceFPSNum = 0;
    int32 LiveSyncSequenceFPSDen = 1;

    // LiveSync object GUID → MovieScene binding GUID mapping
    TMap<FGuid, FGuid> LiveSyncGuidToSequencerBinding;

    // Pending bindings for ADD_POSSESSABLE where actor was not yet in ActorCache
    struct FPendingSequencerBinding
    {
        FGuid LiveSyncGuid;
        uint8 BindingType;
        double Timestamp;
    };
    TArray<FPendingSequencerBinding> PendingSequencerBindings;

    // =====================================================
    // HIERARCHY DIAGNOSTICS (verbose-only, temporary)
    // =====================================================

    struct FHierarchyDiagnostics
    {
        // Current and peak world-space error for attached children
        double WorldErrorDistance = 0.0;
        double MaxWorldErrorDistance = 0.0;

        // Current and peak local-space error for attached children
        double RelativeErrorDistance = 0.0;
        double MaxRelativeErrorDistance = 0.0;

        // Incremented when AttachToActor() called while actor is
        // already attached to the SAME parent.
        // NOT incremented for:
        //   - valid first attachment
        //   - valid reparent
        //   - deferred attach resolution
        int32 AttachmentChurnCount = 0;

        // Incremented on every valid reparent operation
        int32 ReattachCount = 0;

        // Incremented when stored ParentGuid != actor's actual parent
        int32 ParentMismatchCount = 0;
    };

    FHierarchyDiagnostics HierarchyDiag;

    // =====================================================
    // DEFERRED ATTACHMENTS
    // =====================================================

    struct FPendingAttachment
    {
        FGuid Child;
        FGuid Parent;
        int32 RetryFrames = 0;
        double CreatedTime = 0.0;
    };

    void ResolvePendingAttachments();

    TArray<FPendingAttachment>
        PendingAttachments;

    // =====================================================
    // SEMANTIC HIERARCHY DEFERRED QUEUE (Phase 6D, Stage 7)
    // See Docs/Architecture/26-phase6D-hierarchy-implementation-plan.md §4
    // =====================================================
    // Bounded deferred retry buffer for hierarchy attach events
    // whose parent actor does not yet exist. NOT a hidden graph
    // state machine — only stores unresolved semantic intent.
    //
    // Cleared on reconnect/ConsoleReset/EndSnapshot.
    // =====================================================

    TArray<FPendingHierarchyAttachment>
        PendingHierarchyAttachments;

    FPendingHierarchyAttachment*
        FindPendingHierarchyAttachment(
            const FGuid& ChildGuid);

    // =====================================================
    // LIFECYCLE/DELETE DEFERRED QUEUE (Phase 6E, Stage 9)
    // =====================================================
    // Delete packets received during snapshot replay whose
    // target GUID's CREATE has not yet been processed.
    // Processed in HandleEndSnapshot(), cleared on reconnect/reset.
    // =====================================================

    struct FDeferredDelete
    {
        FGuid TargetGuid;
        uint32 Sequence;
        double Timestamp;
    };

    TArray<FDeferredDelete>
        DeferredDeleteQueue;

    // =====================================================
    // MISSING ACTOR RECOVERY
    // =====================================================

    struct FMissingActorState
    {
        int32 MissingFrames = 0;
        bool bRecoveryAttempted = false;
        double LastWarningTime = 0.0;
        int32 RecoveryAttempts = 0;
    };

    void RecoverMissingActors();

    TMap<
        FGuid,
        FMissingActorState>
        MissingActorTracker;

    // =====================================================
    // SNAPSHOT BATCHING
    // =====================================================

    bool bInSnapshotBuild = false;

    double SnapshotStartTime = 0.0;

    // =====================================================
    // VERBOSE LOGGING
    // =====================================================

    bool ShouldLogVerbose() const;

    void TickMetrics(float DeltaTime);

    void TickSafetyMonitors(float DeltaTime);

    void SetQueueDepthPeak(int32 Depth);

    // Event histories
    TArray<FReconnectEvent> ReconnectHistory;
    TArray<FOverflowEvent> OverflowHistory;

    // Overflow tracking helper
    int32 LastReportedDrops = 0;

    static bool bEnableVerboseSyncLogs;

    static bool bEnableTransportVerbose;

    int32 VerboseFrameCounter =
        0;

    double LastTickExecutionTime =
        0.0;

    // =====================================================
    // WATCHDOG RESTART BACKOFF
    // =====================================================

    int32 WatchdogRestartCount =
        0;

    double LastWatchdogRestartTime =
        0.0;

    static constexpr double
        WatchdogBackoffDelays[5] =
            { 1.0, 2.0, 5.0, 10.0, 30.0 };
    double GetWatchdogBackoff() const;

    // =====================================================
    // HIERARCHY SAFETY VALIDATION
    // =====================================================

    void ValidateHierarchy();

    // =====================================================
    // PHASE 6H — TICK-INTEGRATED DIAGNOSTICS
    // =====================================================
    // Lightweight non-mutating checks integrated into the
    // tick pipeline. Runs at reduced frequency to avoid
    // performance impact.
    // =====================================================

    void TickPhase6H(float DeltaTime);

    // Phase 6H diagnostics state
    int32 Phase6HFrameCounter = 0;
    int32 Phase6HRunInterval = 300;    // Every ~300 ticks (~5s)
    bool  bPhase6HVerbose = false;

    // Phase 6H packet ordering state (Goal A)
    TSet<FGuid> Phase6HCreatedThisTick;

    // Phase 6H burst tracking (Goal D)
    int32 Phase6HBurstTickPacketCount = 0;
    int32 Phase6HBurstTickPeak = 0;

    // =====================================================
    // PHASE 6I — PERFORMANCE & SCALABILITY HARDENING
    // =====================================================
    // Transform burst optimization, replay buffer efficiency,
    // packet scheduling metrics, hot path reduction, tick
    // scheduling hardening. All additive, no protocol changes.
    // =====================================================

    void TickPhase6I(float DeltaTime);
    void ConsolePhase6IStats();
    void ConsoleToggleCoalesce(const TArray<FString>& Args);
    void ConsoleSetDiagnosticsCadence(const TArray<FString>& Args);

    // Per-domain packet counters for rate tracking
    mutable int32 Phase6IPerSecondTransforms = 0;
    mutable int32 Phase6IPerSecondCreates = 0;
    mutable int32 Phase6IPerSecondDeletes = 0;
    mutable int32 Phase6IPerSecondHierarchy = 0;
    mutable int32 Phase6IPerSecondRenames = 0;
    mutable int32 Phase6IPerSecondVisibility = 0;
    mutable int32 Phase6IPerSecondCollections = 0;
    double Phase6ILastPerSecondClear = 0.0;

    int32 Phase6IFrameCounter = 0;
    int32 Phase6IDiagnosticsRunInterval = 60;   // ~1s at 60fps
    double Phase6ILongFrameThreshold = 0.033;   // 33ms warning
    double Phase6IOverloadThreshold = 0.050;    // 50ms overload
    bool   bPhase6ICoalesceEnabled = true;      // CVar-gated coalescing

    // Transform coalescing: map GUID -> latest transform packet index
    // Cleared each tick in ProcessQueuedPackets
    TMap<FGuid, int32> Phase6ICoalesceMap;

    // Internal helpers (used via Phase6I.inl, included at bottom of .cpp)
    void CoalesceTransforms(TArray<FLiveSyncPacket>& PacketsThisTick);
    void TrackPerDomainPacket(uint8 PacketType);
    int32 EstimateReplayBufferMemory() const;
    int32 CountUniqueReplayEntries() const;
    int32 CountActiveGUIDs() const;
    void CheckOverloadCondition();

    // =====================================================
    // SAFETY MONITORS (Phase 5C)
    // =====================================================

    // Flood detection: rate in packets/sec over a 2-second window
    static constexpr double
        FloodDetectionWindow = 2.0;

    static constexpr int32
        FloodThresholdPacketsPerSec = 500;

    double FloodAccumulator = 0.0;
    int32 FloodPacketCount = 0;
    double FloodWindowStart = 0.0;

    // Queue pressure: running average depth trigger
    static constexpr double
        QueuePressureThreshold = 96.0;  // 75% of capacity (128)

    // Packet age watchdog: warn if oldest queued packet exceeds this (seconds)
    static constexpr double
        PacketAgeWarnThreshold = 5.0;

    // Packet age watchdog: max allowed packet age before forced flush (seconds)
    static constexpr double
        PacketAgeHardLimit = 30.0;

    double LastPacketAgeWarnTime = 0.0;

    double QueuePressureAccumulator = 0.0;

    // Visualization
    static bool bEnableDebugDraw;

#if WITH_EDITOR
    void DrawDebugOverlay();
#endif
};

// Extern for global verbose flag (read by LiveSyncRunnable.cpp)
extern bool GEnableVerboseSyncLogs;