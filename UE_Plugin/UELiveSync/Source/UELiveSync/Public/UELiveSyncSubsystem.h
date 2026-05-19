#pragma once

#include "CoreMinimal.h"
#include "Subsystems/WorldSubsystem.h"

#include "Sockets.h"
#include "SyncTypes.h"

#include "Containers/Ticker.h"

#include "GameFramework/Actor.h"

#include "LiveSyncQueue.h"

#include "UELiveSyncSubsystem.generated.h"

class FLiveSyncRunnable;
class FRunnableThread;

UCLASS()
class UELIVESYNC_API UUELiveSyncSubsystem
    : public UWorldSubsystem
{
    GENERATED_BODY()

public:

    virtual void Initialize(
        FSubsystemCollectionBase& Collection) override;

    virtual void Deinitialize() override;

private:

    // transform interpolation states
    TMap<FString, FSyncTransformState>
        TransformStates;

    // fast actor lookup cache
    TMap<FString, TWeakObjectPtr<AActor>>
        ActorCache;

    // listener socket
    FSocket* ListenerSocket = nullptr;

    // active TCP connection
    FSocket* ConnectionSocket = nullptr;

    // legacy buffer (can remove later)
    TArray<uint8> Buffer;

    FTSTicker::FDelegateHandle TickHandle;

    /* =============================
       PHASE 3.1 THREADING
    ============================= */

    TQueue<
        FLiveSyncPacket,
        EQueueMode::Mpsc> PacketQueue;

    FLiveSyncRunnable* NetworkRunnable = nullptr;

    FRunnableThread* NetworkThread = nullptr;

    bool bIsConnected = false;

    /* ============================= */

    bool Tick(float DeltaTime);

    void StartServer();

    void ReceiveData();

    void StartNetworkThread();

    void StopNetworkThread();

    void ProcessQueuedPackets();

    void ProcessPacket(
        const FLiveSyncPacket& Packet);

    void ProcessBinaryPacket(
        const FLiveSyncPacket& Packet);

    void UpdateTargetTransform(
        const FString& ActorName,
        const FVector& Location,
        const FQuat& Rotation,
        const FVector& Scale);

    void InterpolateTransforms(
        float DeltaTime);

    // actor cache
    void BuildActorCache();

    AActor* FindActorFast(
        const FString& Name);
};