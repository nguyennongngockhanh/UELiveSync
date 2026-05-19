#pragma once

#include "CoreMinimal.h"
#include "Subsystems/WorldSubsystem.h"

#include "SyncTypes.h"

#include "UELiveSyncSubsystem.generated.h"

class FSocket;
class FRunnableThread;
class FLiveSyncRunnable;

UCLASS()
class UELIVESYNC_API UUELiveSyncSubsystem : public UWorldSubsystem
{
    GENERATED_BODY()

public:

    virtual void Initialize(FSubsystemCollectionBase& Collection) override;
    virtual void Deinitialize() override;

    bool Tick(float DeltaTime);

private:

    // =========================================================
    // NETWORK LAYER
    // =========================================================
    void StartServer();
    void StartNetworkThread();
    void StopNetworkThread();

    // =========================================================
    // PIPELINE
    // =========================================================
    void ProcessQueuedPackets();
    void ProcessBinaryPacket(const FLiveSyncPacket& Packet);

    void UpdateTargetTransform(
        const FString& ActorName,
        const FVector& Location,
        const FQuat& Rotation,
        const FVector& Scale);

    void InterpolateTransforms(float DeltaTime);

    // =========================================================
    // ACTOR SYSTEM
    // =========================================================
    void BuildActorCache();
    AActor* FindActorFast(const FString& Name);

private:

    // =========================================================
    // SOCKETS
    // =========================================================
    FSocket* ListenerSocket = nullptr;
    FSocket* ConnectionSocket = nullptr;

    // =========================================================
    // THREADING
    // =========================================================
    FRunnableThread* NetworkThread = nullptr;
    FLiveSyncRunnable* NetworkRunnable = nullptr;

    // =========================================================
    // QUEUE (thread → game)
    // =========================================================
    TQueue<FLiveSyncPacket, EQueueMode::Mpsc> PacketQueue;

    // =========================================================
    // WORLD CACHE
    // =========================================================
    TMap<FString, TWeakObjectPtr<AActor>> ActorCache;
    TMap<FString, FSyncTransformState> TransformStates;

    // =========================================================
    // TICK
    // =========================================================
    FDelegateHandle TickHandle;

    // =========================================================
    // PHASE 3.2 ADDITIONS (REAL HARDENING)
    // =========================================================

    // protocol version guard (prevents Blender mismatch)
    static constexpr uint16 ProtocolVersion = LIVE_SYNC_VERSION;

    // last processed packet sequence (anti-duplicate / reorder)
    uint64 LastSequenceId = 0;

    // connection state tracking (runtime safety)
    bool bThreadRunning = false;
};