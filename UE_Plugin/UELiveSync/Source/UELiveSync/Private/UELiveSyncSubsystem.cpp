#include "UELiveSyncSubsystem.h"

#include "Engine/World.h"

#include "GameFramework/Actor.h"

#include "EngineUtils.h"

#include "Common/TcpSocketBuilder.h"

#include "Sockets.h"

#include "SocketSubsystem.h"

#include "Interfaces/IPv4/IPv4Address.h"

#include "HAL/RunnableThread.h"

#include "Misc/Guid.h"

#include "LiveSyncRunnable.h"

#include "Components/StaticMeshComponent.h"

#include "Engine/StaticMesh.h"

bool UUELiveSyncSubsystem::
    bEnableVerboseSyncLogs =
        false;

bool GEnableVerboseSyncLogs =
    false;

bool UUELiveSyncSubsystem::
ShouldLogVerbose() const
{
    return
        bEnableVerboseSyncLogs &&
        (VerboseFrameCounter % 300 == 0);
}


// =========================================================
// INITIALIZE
// =========================================================

void UUELiveSyncSubsystem::Initialize(
    FSubsystemCollectionBase&
    Collection)
{
    Super::Initialize(Collection);

    StartServer();

    BuildActorCache();

    UWorld* World = GetWorld();

    if (World)
    {
        OnActorSpawnedHandle =

            World->AddOnActorSpawnedHandler(

                FOnActorSpawned::FDelegate::CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::OnActorSpawned
                )
            );

        OnActorDestroyedHandle =

            World->AddOnActorDestroyedHandler(

                FOnActorDestroyed::FDelegate::CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::OnActorDestroyed
                )
            );
    }

    TickHandle =
        FTSTicker::GetCoreTicker().
        AddTicker(

            FTickerDelegate::
            CreateUObject(

                this,
                &UUELiveSyncSubsystem::Tick),

            0.0f
        );

    UE_LOG(
        LogTemp,
        Log,
        TEXT("UE Live Sync Started"));
}


// =========================================================
// DEINITIALIZE
// =========================================================

void UUELiveSyncSubsystem::Deinitialize()
{
    StopNetworkThread();

    FTSTicker::GetCoreTicker().
        RemoveTicker(
            TickHandle);

    UWorld* World = GetWorld();

    if (World)
    {
        if (OnActorSpawnedHandle.IsValid())
        {
            World->RemoveOnActorSpawnedHandler(
                OnActorSpawnedHandle);

            OnActorSpawnedHandle.Reset();
        }

        if (OnActorDestroyedHandle.IsValid())
        {
            World->RemoveOnActorDestroyedHandler(
                OnActorDestroyedHandle);

            OnActorDestroyedHandle.Reset();
        }
    }

    if (ListenerSocket)
    {
        ListenerSocket->Close();

        ISocketSubsystem::
            Get(PLATFORM_SOCKETSUBSYSTEM)
            ->DestroySocket(
                ListenerSocket);

        ListenerSocket =
            nullptr;
    }

    Super::Deinitialize();
}


// =========================================================
// START SERVER
// =========================================================

void UUELiveSyncSubsystem::StartServer()
{
    FIPv4Address Address;

    FIPv4Address::Parse(
        TEXT("0.0.0.0"),
        Address);

    ListenerSocket =

        FTcpSocketBuilder(
            TEXT("UE_LiveSync_Server"))

        .AsReusable()

        .BoundToAddress(
            Address)

        .BoundToPort(
            5000)

        .Listening(8);

    if (!ListenerSocket)
    {
        UE_LOG(
            LogTemp,
            Error,
            TEXT("Failed to start TCP server"));

        return;
    }

    UE_LOG(
        LogTemp,
        Warning,
        TEXT("Live Sync Listening on port 5000"));
}


// =========================================================
// MAIN TICK
// =========================================================

bool UUELiveSyncSubsystem::Tick(
    float DeltaTime)
{
    VerboseFrameCounter++;
    GEnableVerboseSyncLogs =
        bEnableVerboseSyncLogs;

    // =====================================================
    // ACCEPT CONNECTION
    // =====================================================

    if (!ConnectionSocket &&
        ListenerSocket)
    {
        bool bPending =
            false;

        if (ListenerSocket->
            HasPendingConnection(
                bPending)
            && bPending)
        {
            FSocket* NewSocket =

                ListenerSocket->
                Accept(
                    TEXT("LiveSyncConnection"));

            if (NewSocket)
            {
                if (NewSocket->
                    GetConnectionState()
                    == SCS_Connected)
                {
                    ConnectionSocket =
                        NewSocket;

                    ConnectionSocket->
                        SetNoDelay(true);

                    UE_LOG(
                        LogTemp,
                        Log,
                        TEXT("Blender Connected"));

                    StartNetworkThread();
                }
                else
                {
                    NewSocket->Close();

                    ISocketSubsystem::
                        Get(
                            PLATFORM_SOCKETSUBSYSTEM)
                        ->DestroySocket(
                            NewSocket);
                }
            }
        }
    }

    // =====================================================
    // STALE CONNECTION SAFETY
    // =====================================================

    if (ConnectionSocket &&
        ConnectionSocket->
        GetConnectionState()
        != SCS_Connected)
    {
        UE_LOG(
            LogTemp,
            Log,
            TEXT("Stale Connection Removed"));

        StopNetworkThread();
    }

    // =====================================================
    // DETECT NETWORK THREAD EXIT
    // (fires immediately when peer disconnects — no
    //  need to wait for heartbeat timeout)
    // =====================================================

    if (ConnectionSocket &&
        NetworkRunnable &&
        NetworkRunnable->bThreadExited)
    {
        UE_LOG(
            LogTemp,
            Log,
            TEXT("Detected thread exit, cleaning up"));

        StopNetworkThread();
    }

    // =====================================================
    // HEARTBEAT TIMEOUT CHECK
    // =====================================================

    if (ConnectionSocket &&
        LastHeartbeatTime > 0.0 &&
        FPlatformTime::Seconds() - LastHeartbeatTime >
        HeartbeatTimeout)
    {
        UE_LOG(
            LogTemp,
            Log,
            TEXT("Heartbeat timeout: closing connection"));

        StopNetworkThread();
    }

    // =====================================================
    // PIPELINE
    // =====================================================

    ProcessQueuedPackets();

    InterpolateTransforms(
        DeltaTime);

    return true;
}


// =========================================================
// START NETWORK THREAD
// =========================================================

void UUELiveSyncSubsystem::
StartNetworkThread()
{
    // =====================================================
    // GUARD: no socket
    // =====================================================

    if (!ConnectionSocket)
    {
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("StartNetworkThread: no socket"));

        return;
    }

    // =====================================================
    // GUARD: prevent double-start
    // =====================================================

    if (NetworkThread ||
        NetworkRunnable)
    {
        UE_LOG(
            LogTemp,
            Log,
            TEXT("StartNetworkThread: already running, stopping old thread"));

        StopNetworkThread();

        // StopNetworkThread nulls the socket.
        // If no new connection was accepted, bail.
        if (!ConnectionSocket)
        {
            UE_LOG(
                LogTemp,
                Warning,
                TEXT("StartNetworkThread: socket was destroyed"));

            return;
        }
    }

    // =====================================================
    // CREATE RUNNABLE
    // =====================================================

    NetworkRunnable =
        new FLiveSyncRunnable(

            ConnectionSocket,

            &PacketQueue
        );

    NetworkThread =

        FRunnableThread::Create(

            NetworkRunnable,

            TEXT("UE_LiveSync_Thread")
        );

    if (NetworkThread)
    {
        UE_LOG(
            LogTemp,
            Log,
            TEXT("Network Thread Created"));
    }
    else
    {
        UE_LOG(
            LogTemp,
            Error,
            TEXT("Failed to create network thread"));
    }
}


// =========================================================
// STOP NETWORK THREAD
// =========================================================

void UUELiveSyncSubsystem::
StopNetworkThread()
{
    if (!NetworkRunnable &&
        !NetworkThread)
    {
        return;
    }

    uint64 StartCycles =
        FPlatformTime::Cycles64();

    if (NetworkRunnable)
    {
        NetworkRunnable->Stop();
    }

    uint64 AfterStopCycles =
        FPlatformTime::Cycles64();

    // Close socket FIRST to unblock Wait()/Recv()
    // so the network thread exits immediately
    if (ConnectionSocket)
    {
        ConnectionSocket->Close();
    }

    uint64 AfterCloseCycles =
        FPlatformTime::Cycles64();

    if (NetworkThread)
    {
        NetworkThread->
            WaitForCompletion();

        delete NetworkThread;

        NetworkThread =
            nullptr;
    }

    uint64 AfterJoinCycles =
        FPlatformTime::Cycles64();

    if (NetworkRunnable)
    {
        delete NetworkRunnable;

        NetworkRunnable =
            nullptr;
    }

    // Destroy socket AFTER thread has exited
    if (ConnectionSocket)
    {
        ISocketSubsystem::
            Get(PLATFORM_SOCKETSUBSYSTEM)
            ->DestroySocket(
                ConnectionSocket);

        ConnectionSocket =
            nullptr;
    }

    // Reset stale state
    PacketQueue.Clear();

    TransformStates.Empty();

    LastHeartbeatTime = 0.0;

    LastSequenceId = 0;

    uint64 EndCycles =
        FPlatformTime::Cycles64();

    double StopMs =
        FPlatformTime::
        ToMilliseconds64(
            EndCycles - StartCycles);

    double StopMs2 =
        FPlatformTime::
        ToMilliseconds64(
            AfterStopCycles -
            StartCycles);

    double CloseMs =
        FPlatformTime::
        ToMilliseconds64(
            AfterCloseCycles -
            AfterStopCycles);

    double JoinMs =
        FPlatformTime::
        ToMilliseconds64(
            AfterJoinCycles -
            AfterCloseCycles);

    double CleanupMs =
        FPlatformTime::
        ToMilliseconds64(
            EndCycles -
            AfterJoinCycles);

    UE_LOG(
        LogTemp,
        Log,
        TEXT("StopNetworkThread: stop=%.2fms close=%.2fms join=%.2fms cleanup=%.2fms total=%.2fms"),
        StopMs2,
        CloseMs,
        JoinMs,
        CleanupMs,
        StopMs);
}


// =========================================================
// PROCESS QUEUE
// =========================================================

void UUELiveSyncSubsystem::
ProcessQueuedPackets()
{
    FLiveSyncPacket Packet;

    TArray<FLiveSyncPacket>
        PacketsThisTick;

    int32 DequeueCount = 0;

    while (
        PacketQueue.Dequeue(
            Packet))
    {
        DequeueCount++;
        PacketsThisTick.Add(
            MoveTemp(Packet));
    }

    if (DequeueCount > 0 && ShouldLogVerbose())
    {
        UE_LOG(
            LogTemp,
            Log,
            TEXT("Dequeued: %d packets"),
            DequeueCount);
    }

    TSet<FGuid>
        SeenThisTick;

    for (const FLiveSyncPacket&
        Pkt : PacketsThisTick)
    {
        ProcessBinaryPacket(
            Pkt,
            &SeenThisTick);
    }
}


// =========================================================
// PROCESS BINARY PACKET
// =========================================================

void UUELiveSyncSubsystem::
ProcessBinaryPacket(
    const FLiveSyncPacket&
    Packet,
    TSet<FGuid>* SeenThisTick)
{
    if (Packet.RawData.Num() <
        sizeof(FPacketHeader))
    {
        return;
    }

    const uint8* PacketData =
        Packet.RawData.GetData();

    uint32 Magic;
    uint16 Version;

    FMemory::Memcpy(
        &Magic,
        PacketData,
        sizeof(uint32));

    FMemory::Memcpy(
        &Version,
        PacketData + sizeof(uint32),
        sizeof(uint16));

    // =====================================================
    // MAGIC CHECK
    // =====================================================

    if (Magic !=
        LIVE_SYNC_MAGIC)
    {
        return;
    }

    // =====================================================
    // VERSION DISPATCH
    // =====================================================

    const uint8* Ptr = nullptr;
    const uint8* PacketEnd = nullptr;
    uint32 ObjectCount = 0;
    uint64 SequenceId = 0;

    if (Version >=
        LIVE_SYNC_VERSION_V3)
    {
        if (Packet.RawData.Num() <
            sizeof(FPacketHeaderV3))
        {
            return;
        }

        FPacketHeaderV3 HeaderV3;

        FMemory::Memcpy(
            &HeaderV3,
            PacketData,
            sizeof(FPacketHeaderV3));

        if (HeaderV3.PacketSize >
            Packet.RawData.Num())
        {
            return;
        }

        SequenceId =
            HeaderV3.SequenceId;

        ObjectCount =
            HeaderV3.ObjectCount;

        Ptr =
            PacketData +
            sizeof(FPacketHeaderV3);

        PacketEnd =
            PacketData +
            HeaderV3.PacketSize;
    }
    else if (Version ==
             LIVE_SYNC_VERSION)
    {
        if (Packet.RawData.Num() <
            sizeof(FPacketHeader))
        {
            return;
        }

        FPacketHeader Header;

        FMemory::Memcpy(
            &Header,
            PacketData,
            sizeof(FPacketHeader));

        if (Header.PacketSize >
            Packet.RawData.Num())
        {
            return;
        }

        SequenceId =
            Header.SequenceId;

        ObjectCount =
            Header.ObjectCount;

        Ptr =
            PacketData +
            sizeof(FPacketHeader);

        PacketEnd =
            PacketData +
            Header.PacketSize;
    }
    else
    {
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("Unsupported protocol version: %u"),
            Version);

        return;
    }

    // =====================================================
    // SEQUENCE CHECK
    // =====================================================

    if (SequenceId <=
        LastSequenceId)
    {
        return;
    }

    LastSequenceId =
        SequenceId;

    // =====================================================
    // V3: PACKET TYPE DISPATCH
    // =====================================================

    uint8 PacketType = 0x01;

    if (Version >=
        LIVE_SYNC_VERSION_V3)
    {
        FPacketHeaderV3* HdrV3 =
            reinterpret_cast<FPacketHeaderV3*>(
                const_cast<uint8*>(PacketData));

        PacketType =
            HdrV3->PacketType;
    }

    if (ShouldLogVerbose())
    {
        UE_LOG(
            LogTemp,
            Log,
            TEXT("Header: version=%u type=0x%02x seq=%llu objects=%u"),
            Version,
            PacketType,
            SequenceId,
            ObjectCount);
    }

    // =====================================================
    // HEARTBEAT: no objects to process
    // =====================================================

    if (PacketType == 0x07)
    {
        LastHeartbeatTime =
            FPlatformTime::Seconds();

        return;
    }

    // =====================================================
    // OBJECT LOOP
    // =====================================================

    for (uint32 i = 0;
         i < ObjectCount;
         i++)
    {
        if (Ptr + 16 >
            PacketEnd)
        {
            return;
        }

        FGuid Guid;

        if (Version >=
            LIVE_SYNC_VERSION_V3)
        {
            uint32 GuidParts[4];

            FMemory::Memcpy(
                GuidParts,
                Ptr,
                16);

            Guid = FGuid(
                GuidParts[0],
                GuidParts[1],
                GuidParts[2],
                GuidParts[3]);
        }
        else
        {
            FString GuidHex;

            for (int32 b = 0; b < 16; b++)
            {
                GuidHex += FString::Printf(
                    TEXT("%02x"),
                    Ptr[b]
                );
            }

            if (!FGuid::ParseExact(

                GuidHex,

                EGuidFormats::Digits,

                Guid))
            {
                return;
            }
        }

        Ptr += 16;

        // =================================================
        // DELETE PACKET: GUID only, no transform data
        // =================================================

        if (PacketType == 0x04)
        {
            HandleDeleteObject(Guid);

            continue;
        }

        // =================================================
        // DEDUP: skip if already processed this tick
        // =================================================

        if (SeenThisTick &&
            SeenThisTick->Contains(
                Guid))
        {
            if (Version >=
                LIVE_SYNC_VERSION_V3)
            {
                Ptr +=
                    sizeof(FVector3f) +
                    sizeof(FQuat4f) +
                    sizeof(FVector3f) +
                    sizeof(double) +
                    16;
            }
            else
            {
                Ptr +=
                    sizeof(FVector3f) +
                    sizeof(FQuat4f) +
                    sizeof(FVector3f);
            }

            continue;
        }

        if (SeenThisTick)
        {
            SeenThisTick->Add(
                Guid);
        }

        // =================================================
        // LOCATION
        // =================================================

        if (Ptr + sizeof(FVector3f) >
            PacketEnd)
        {
            return;
        }

        FVector3f LocationFloat;

        FMemory::Memcpy(
            &LocationFloat,
            Ptr,
            sizeof(FVector3f));

        Ptr += sizeof(FVector3f);

        FVector Location(
            LocationFloat);

        // =================================================
        // ROTATION
        // =================================================

        if (Ptr + sizeof(FQuat4f) >
            PacketEnd)
        {
            return;
        }

        FQuat4f RotationFloat;

        FMemory::Memcpy(
            &RotationFloat,
            Ptr,
            sizeof(FQuat4f));

        Ptr += sizeof(FQuat4f);

        FQuat Rotation(
            RotationFloat);

        Rotation.Normalize();

        // =================================================
        // SCALE
        // =================================================

        if (Ptr + sizeof(FVector3f) >
            PacketEnd)
        {
            return;
        }

        FVector3f ScaleFloat;

        FMemory::Memcpy(
            &ScaleFloat,
            Ptr,
            sizeof(FVector3f));

        Ptr += sizeof(FVector3f);

        FVector Scale(
            ScaleFloat);

        // =================================================
        // V3: Timestamp + Parent GUID
        // =================================================

        FGuid ParentGuid;

        if (Version >=
            LIVE_SYNC_VERSION_V3)
        {
            if (Ptr + sizeof(double) >
                PacketEnd)
            {
                return;
            }

            Ptr += sizeof(double);

            if (Ptr + 16 >
                PacketEnd)
            {
                return;
            }

            uint32 ParentParts[4];

            FMemory::Memcpy(
                ParentParts,
                Ptr,
                16);

            ParentGuid = FGuid(
                ParentParts[0],
                ParentParts[1],
                ParentParts[2],
                ParentParts[3]);

            Ptr += 16;
        }

        // =================================================
        // APPLY
        // =================================================

        if (PacketType == 0x03)
        {
            HandleCreateObject(
                Guid,
                Location,
                Rotation,
                Scale,
                ParentGuid);
        }

        UpdateTargetTransform(

            Guid,

            Location,

            Rotation,

            Scale,

            ParentGuid
        );

        if (ShouldLogVerbose())
        {
            UE_LOG(
                LogTemp,
                Log,
                TEXT("Processed: %s loc=%s"),
                *Guid.ToString(
                    EGuidFormats::Digits),
                *Location.ToString());
        }
    }
}


// =========================================================
// UPDATE TARGET TRANSFORM
// =========================================================

void UUELiveSyncSubsystem::
UpdateTargetTransform(

    const FGuid& Guid,

    const FVector& Location,

    const FQuat& Rotation,

    const FVector& Scale,

    const FGuid& ParentGuid)
{
    FSyncTransformState& State =

        TransformStates.
        FindOrAdd(
            Guid);

    double CurrentTime =
        FPlatformTime::
        Seconds();

    if (!State.bInitialized)
    {
        State.CurrentLocation =
            Location;

        State.TargetLocation =
            Location;

        State.CurrentRotation =
            Rotation;

        State.TargetRotation =
            Rotation;

        State.CurrentScale =
            Scale;

        State.TargetScale =
            Scale;

        State.ParentGuid =
            ParentGuid;

        State.bHasParent =
            ParentGuid.IsValid();

        State.LastUpdateTime =
            CurrentTime;

        State.bInitialized =
            true;
    }

    float LocationDistance =

        FVector::Dist(

            State.TargetLocation,

            Location);

    float RotationDistance =

        State.TargetRotation.
        AngularDistance(
            Rotation);

    float ScaleDistance =

        FVector::Dist(

            State.TargetScale,

            Scale);

    bool bLocationChanged =
        LocationDistance >=
        0.05f;

    bool bRotationChanged =
        RotationDistance >=
        0.002f;

    bool bScaleChanged =
        ScaleDistance >=
        0.001f;

    if (!bLocationChanged &&
        !bRotationChanged &&
        !bScaleChanged)
    {
        if (ParentGuid !=
            State.ParentGuid)
        {
            State.ParentGuid =
                ParentGuid;

            State.bHasParent =
                ParentGuid.IsValid();
        }
        else
        {
            return;
        }
    }

    double DeltaTime =

        CurrentTime -
        State.LastUpdateTime;

    if (bLocationChanged &&
        DeltaTime >
        SMALL_NUMBER)
    {
        FVector DeltaLocation =

            Location -
            State.TargetLocation;

        FVector NewVelocity =

            DeltaLocation /
            DeltaTime;

        State.Velocity =

            FMath::VInterpTo(

                State.Velocity,

                NewVelocity,

                DeltaTime,

                8.0f);

        State.Velocity =

            State.Velocity.
            GetClampedToMaxSize(
                5000.0f);
    }

    State.ParentGuid =
        ParentGuid;

    State.bHasParent =
        ParentGuid.IsValid();

    State.TargetLocation =
        Location;

    State.TargetRotation =
        Rotation;

    State.TargetScale =
        Scale;

    State.LastUpdateTime =
        CurrentTime;
}



// =========================================================
// INTERPOLATION
// =========================================================

void UUELiveSyncSubsystem::
InterpolateTransforms(
    float DeltaTime)
{
    const float PredictionTime =
        0.012f;

    int MissingCount = 0;
    int ConvergedCount = 0;
    int SnapCount = 0;
    int InterpCount = 0;

    for (auto& Pair :
        TransformStates)
    {
        const FGuid& Guid =
            Pair.Key;

        FSyncTransformState&
            State =
            Pair.Value;

        TWeakObjectPtr<AActor>*
            ActorPtr =

            ActorCache.Find(
                Guid);

        if (!ActorPtr ||
            !ActorPtr->IsValid())
        {
            MissingCount++;
            continue;
        }

        AActor* Actor =
            ActorPtr->Get();

        bool bLocationConverged =
            FVector::Dist(
                State.CurrentLocation,
                State.TargetLocation)
            < KINDA_SMALL_NUMBER;

        bool bRotationConverged =
            State.CurrentRotation.
                Equals(
                    State.TargetRotation,
                    0.01f);

        bool bScaleConverged =
            FVector::Dist(
                State.CurrentScale,
                State.TargetScale)
            < KINDA_SMALL_NUMBER;

        if (bLocationConverged &&
            bRotationConverged &&
            bScaleConverged)
        {
            ConvergedCount++;
            continue;
        }

        float DistToTarget =

            FVector::Dist(

                State.CurrentLocation,

                State.TargetLocation);

        if (DistToTarget < 0.5f)
        {
            State.CurrentLocation =
                State.TargetLocation;

            State.CurrentRotation =
                State.TargetRotation;

            State.CurrentScale =
                State.TargetScale;

            Actor->SetActorTransform(

                FTransform(
                    State.CurrentRotation,
                    State.CurrentLocation,
                    State.CurrentScale)
            );

            SnapCount++;
            continue;
        }

        FVector PredictedLocation =

            State.TargetLocation +

            (State.Velocity *
             PredictionTime);

        float Distance =

            FVector::Dist(

                State.CurrentLocation,

                PredictedLocation);

        State.AdaptiveInterpSpeed =

            FMath::
            GetMappedRangeValueClamped(

                FVector2D(
                    0.0f,
                    300.0f),

                FVector2D(
                    8.0f,
                    24.0f),

                Distance);

        State.CurrentLocation =

            FMath::VInterpTo(

                State.CurrentLocation,

                PredictedLocation,

                DeltaTime,

                State.
                AdaptiveInterpSpeed);

        State.CurrentRotation =

            FQuat::Slerp(

                State.CurrentRotation,

                State.TargetRotation,

                DeltaTime *
                12.0f);

        State.CurrentScale =

            FMath::VInterpTo(

                State.CurrentScale,

                State.TargetScale,

                DeltaTime,
                12.0f);

        Actor->SetActorTransform(

            FTransform(
                State.CurrentRotation,
                State.CurrentLocation,
                State.CurrentScale));

        InterpCount++;
    }

    if (ShouldLogVerbose())
    {
        int Total = TransformStates.Num();

        UE_LOG(
            LogTemp,
            Log,
            TEXT(
                "Transform states: total=%d missing=%d converged=%d snap=%d interp=%d"),
            Total,
            MissingCount,
            ConvergedCount,
            SnapCount,
            InterpCount
        );
    }
}

// =========================================================
// BUILD ACTOR CACHE
// =========================================================

void UUELiveSyncSubsystem::
BuildActorCache()
{
    UWorld* World =
        GetWorld();

    if (!World)
    {
        return;
    }

    int ScannedActors = 0;

    for (TActorIterator<AActor>
        It(World);
        It;
        ++It)
    {
        AActor* Actor =
            *It;

        if (!Actor)
        {
            continue;
        }

        ScannedActors++;
        TryCacheActor(Actor);
    }

    UE_LOG(
        LogTemp,
        Log,
        TEXT("BuildActorCache: scanned %d actors, found %d with GUID tags"),
        ScannedActors,
        ActorCache.Num());
}

void UUELiveSyncSubsystem::
TryCacheActor(
    AActor* Actor)
{
    if (!Actor)
    {
        return;
    }

    bool bFoundTag = false;

    for (const FName& Tag :
        Actor->Tags)
    {
        FString TagString =
            Tag.ToString();

        FString Prefix =
            TEXT("LiveSync_GUID=");

        if (!TagString.
            StartsWith(
                Prefix))
        {
            continue;
        }

        bFoundTag = true;

        FString GuidString =

            TagString.RightChop(
                Prefix.Len());

        FGuid Guid;

        if (!FGuid::ParseExact(

            GuidString,

            EGuidFormats::Digits,

            Guid))
        {
            UE_LOG(
                LogTemp,
                Warning,
                TEXT("TryCacheActor: %s has bad GUID tag: %s"),
                *Actor->GetActorLabel(),
                *GuidString);

            bFoundTag = false;

            continue;
        }

        if (ActorCache.Contains(
            Guid))
        {
            return;
        }

        ActorCache.Add(
            Guid,
            Actor);

        UE_LOG(
            LogTemp,
            Log,
            TEXT("Cached Actor %s | GUID=%s"),

            *Actor->GetActorLabel(),

            *Guid.ToString(EGuidFormats::Digits)
        );

        break;
    }
}

void UUELiveSyncSubsystem::
OnActorSpawned(
    AActor* Actor)
{
    TryCacheActor(Actor);
}

void UUELiveSyncSubsystem::
OnActorDestroyed(
    AActor* Actor)
{
    for (auto It =
        ActorCache.CreateIterator();
        It;
        ++It)
    {
        if (!It.Value().IsValid() ||
            It.Value().Get() == Actor)
        {
            It.RemoveCurrent();
        }
    }
}


// =========================================================
// FIND ACTOR
// =========================================================

AActor* UUELiveSyncSubsystem::
FindActorFast(
    const FGuid& Guid)
{
    TWeakObjectPtr<AActor>*
        Found =

        ActorCache.Find(
            Guid);

    if (!Found)
    {
        return nullptr;
    }

    return Found->Get();
}


// =========================================================
// HANDLE CREATE OBJECT
// =========================================================

void UUELiveSyncSubsystem::
HandleCreateObject(

    const FGuid& Guid,

    const FVector& Location,

    const FQuat& Rotation,

    const FVector& Scale,

    const FGuid& ParentGuid)
{
    UWorld* World = GetWorld();

    if (!World)
    {
        return;
    }

    AActor* Existing =
        FindActorFast(Guid);

    if (Existing)
    {
        UE_LOG(
            LogTemp,
            Log,
            TEXT("GUID match %s: found existing actor %s, skip spawn"),
            *Guid.ToString(
                EGuidFormats::Digits),
            *Existing->GetActorLabel());

        return;
    }

    UE_LOG(
        LogTemp,
        Log,
        TEXT("GUID match %s: NOT found in cache, spawning new actor"),
        *Guid.ToString(
            EGuidFormats::Digits));

    FActorSpawnParameters SpawnParams;

    SpawnParams.SpawnCollisionHandlingOverride =
        ESpawnActorCollisionHandlingMethod::
            AlwaysSpawn;

    AActor* NewActor =

        World->SpawnActor<AActor>(

            AActor::StaticClass(),

            FTransform(
                Rotation,
                Location,
                Scale),

            SpawnParams);

    if (!NewActor)
    {
        return;
    }

    UStaticMeshComponent* MeshComp =
        NewObject<UStaticMeshComponent>(
            NewActor);

    MeshComp->SetMobility(
        EComponentMobility::Movable);

    MeshComp->SetVisibility(
        true, true);

    static UStaticMesh* CubeMesh =
        LoadObject<UStaticMesh>(
            nullptr,
            TEXT(
                "/Engine/BasicShapes/"
                "Cube.Cube"));

    if (CubeMesh)
    {
        MeshComp->SetStaticMesh(
            CubeMesh);
    }

    MeshComp->SetCollisionEnabled(
        ECollisionEnabled::NoCollision);

    NewActor->SetRootComponent(
        MeshComp);

    MeshComp->RegisterComponent();

    FString TagString =
        FString::Printf(
            TEXT("LiveSync_GUID=%s"),
            *Guid.ToString(
                EGuidFormats::Digits));

    NewActor->Tags.Add(
        FName(*TagString));

    ActorCache.Add(
        Guid,
        NewActor);

    UpdateTargetTransform(
        Guid,
        Location,
        Rotation,
        Scale,
        ParentGuid);
}


// =========================================================
// HANDLE DELETE OBJECT
// =========================================================

void UUELiveSyncSubsystem::
HandleDeleteObject(
    const FGuid& Guid)
{
    AActor* Actor =
        FindActorFast(Guid);

    if (Actor)
    {
        Actor->Destroy();
    }

    ActorCache.Remove(
        Guid);

    TransformStates.Remove(
        Guid);

    LastHeartbeatTime =
        FPlatformTime::Seconds();
}