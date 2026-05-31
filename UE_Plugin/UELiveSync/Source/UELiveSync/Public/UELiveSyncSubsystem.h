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