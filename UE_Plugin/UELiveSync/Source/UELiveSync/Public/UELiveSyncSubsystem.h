#pragma once

#include "CoreMinimal.h"

#include "Subsystems/WorldSubsystem.h"

#include "Containers/Ticker.h"

#include "Misc/Guid.h"

#include "SyncTypes.h"
#include "LiveSyncQueue.h"

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

        const FGuid& ParentGuid = FGuid()
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

        const FGuid& ParentGuid);

    void HandleDeleteObject(
        const FGuid& Guid);

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
    // METRICS LOGGING
    // =====================================================

    double LastMetricsLogTime =
        0.0;

    static constexpr double
        MetricsLogInterval =
        60.0;

    void LogRuntimeMetrics();

    // =====================================================
    // VERBOSE LOGGING
    // =====================================================

    bool ShouldLogVerbose() const;

    static bool bEnableVerboseSyncLogs;

    int32 VerboseFrameCounter =
        0;
};