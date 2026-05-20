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
        Warning,
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

    if (ConnectionSocket)
    {
        ConnectionSocket->Close();

        ISocketSubsystem::
            Get(PLATFORM_SOCKETSUBSYSTEM)
            ->DestroySocket(
                ConnectionSocket);

        ConnectionSocket =
            nullptr;
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

                    UE_LOG(
                        LogTemp,
                        Warning,
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
            Warning,
            TEXT("Stale Connection Removed"));

        StopNetworkThread();

        ConnectionSocket->Close();

        ISocketSubsystem::
            Get(PLATFORM_SOCKETSUBSYSTEM)
            ->DestroySocket(
                ConnectionSocket);

        ConnectionSocket =
            nullptr;
    }

    // =====================================================
    // ACTOR CACHE REFRESH
    // =====================================================

    static float CacheTimer =
        0.0f;

    CacheTimer +=
        DeltaTime;

    if (CacheTimer > 5.0f)
    {
        BuildActorCache();

        CacheTimer =
            0.0f;
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
    if (!ConnectionSocket)
    {
        return;
    }

    // =====================================================
    // PREVENT DOUBLE START
    // =====================================================

    if (NetworkThread ||
        NetworkRunnable)
    {
        StopNetworkThread();
    }

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
            Warning,
            TEXT("Network Thread Started"));
    }
}


// =========================================================
// STOP NETWORK THREAD
// =========================================================

void UUELiveSyncSubsystem::
StopNetworkThread()
{
    if (NetworkRunnable)
    {
        NetworkRunnable->Stop();
    }

    if (NetworkThread)
    {
        NetworkThread->
            WaitForCompletion();

        delete NetworkThread;

        NetworkThread =
            nullptr;
    }

    delete NetworkRunnable;

    NetworkRunnable =
        nullptr;

    if (ConnectionSocket)
    {
        ConnectionSocket->Close();

        ISocketSubsystem::
            Get(PLATFORM_SOCKETSUBSYSTEM)
            ->DestroySocket(
                ConnectionSocket);

        ConnectionSocket =
            nullptr;
    }

    UE_LOG(
        LogTemp,
        Warning,
        TEXT("Network Thread Stopped"));
}


// =========================================================
// PROCESS QUEUE
// =========================================================

void UUELiveSyncSubsystem::
ProcessQueuedPackets()
{
    FLiveSyncPacket Packet;

    while (
        PacketQueue.Dequeue(
            Packet))
    {
        ProcessBinaryPacket(
            Packet);
    }
}


// =========================================================
// PROCESS BINARY PACKET
// =========================================================

void UUELiveSyncSubsystem::
ProcessBinaryPacket(
    const FLiveSyncPacket&
    Packet)
{
    if (Packet.RawData.Num() <
        sizeof(FPacketHeader))
    {
        return;
    }

    const uint8* Ptr =
        Packet.RawData.GetData();

    FPacketHeader Header;

    FMemory::Memcpy(
        &Header,
        Ptr,
        sizeof(FPacketHeader));

    // =====================================================
    // DEBUG HEADER
    // =====================================================

    UE_LOG(
        LogTemp,
        Warning,
        TEXT("Packet Received | Magic=%u Version=%u Sequence=%llu Size=%u Objects=%u"),

        Header.Magic,
        Header.Version,
        Header.SequenceId,
        Header.PacketSize,
        Header.ObjectCount
    );

    // =====================================================
    // MAGIC CHECK
    // =====================================================

    if (Header.Magic !=
        LIVE_SYNC_MAGIC)
    {
        return;
    }

    // =====================================================
    // VERSION CHECK
    // =====================================================

    if (Header.Version !=
        LIVE_SYNC_VERSION)
    {
        UE_LOG(
            LogTemp,
            Warning,
            TEXT("Protocol version mismatch"));

        return;
    }

    // =====================================================
    // SEQUENCE CHECK
    // =====================================================

    if (Header.SequenceId <=
        LastSequenceId)
    {
        return;
    }

    LastSequenceId =
        Header.SequenceId;

    // =====================================================
    // SIZE CHECK
    // =====================================================

    if (Header.PacketSize >
        Packet.RawData.Num())
    {
        return;
    }

    Ptr += sizeof(FPacketHeader);

    const uint8* PacketEnd =
        Packet.RawData.GetData()
        + Packet.RawData.Num();

    // =====================================================
    // OBJECT LOOP
    // =====================================================

    for (uint32 i = 0;
         i < Header.ObjectCount;
         i++)
    {
        // =================================================
        // GUID
        // =================================================

        if (Ptr + 16 >
            PacketEnd)
        {
            return;
        }

        FString GuidHex;

        for (int32 b = 0; b < 16; b++)
        {
            GuidHex += FString::Printf(
                TEXT("%02x"),
                Ptr[b]
            );
        }

        FGuid Guid;

        if (!FGuid::ParseExact(

            GuidHex,

            EGuidFormats::Digits,

            Guid))
        {
            return;
        }

        UE_LOG(
            LogTemp,
            Warning,
            TEXT("Received GUID=%s"),

            *Guid.ToString(EGuidFormats::Digits)
        );

        Ptr += 16;

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
        // APPLY
        // =================================================

        UpdateTargetTransform(

            Guid,

            Location,

            Rotation,

            Scale
        );
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

    const FVector& Scale)
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

        State.LastUpdateTime =
            CurrentTime;

        State.bInitialized =
            true;

        UE_LOG(
            LogTemp,
            Warning,
            TEXT("Transform State Initialized")
        );
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
        return;
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

        if (!ActorPtr)
        {
            continue;
        }

        AActor* Actor =
            ActorPtr->Get();

        if (!Actor)
        {
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

        FTransform FinalTransform(

            State.CurrentRotation,

            State.CurrentLocation,

            State.CurrentScale);

        UE_LOG(
            LogTemp,
            Warning,
            TEXT("Applying Transform To %s"),

            *Actor->GetActorLabel()
        );

        Actor->SetActorTransform(
            FinalTransform);
    }
}


// =========================================================
// BUILD ACTOR CACHE
// =========================================================

void UUELiveSyncSubsystem::
BuildActorCache()
{
    ActorCache.Empty();

    UWorld* World =
        GetWorld();

    if (!World)
    {
        return;
    }

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

            FString GuidString =

                TagString.RightChop(
                    Prefix.Len());

            FGuid Guid;

            if (!FGuid::ParseExact(

                GuidString,

                EGuidFormats::Digits,

                Guid))
            {
                continue;
            }

            ActorCache.Add(
                Guid,
                Actor);

            UE_LOG(
                LogTemp,
                Warning,
                TEXT("Cached Actor %s | GUID=%s"),

                *Actor->GetActorLabel(),

                *Guid.ToString(EGuidFormats::Digits)
            );

            break;
        }
    }

    UE_LOG(
        LogTemp,
        Warning,
        TEXT("GUID actor cache built: %d actors"),
        ActorCache.Num());
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