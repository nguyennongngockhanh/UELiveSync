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
    // CONSOLE COMMANDS
    // =====================================================

    void ConsoleDumpState();

    void ConsoleReset();

    void ConsolePing();

    void ConsoleStats();

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

    int32 VerboseFrameCounter =
        0;

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