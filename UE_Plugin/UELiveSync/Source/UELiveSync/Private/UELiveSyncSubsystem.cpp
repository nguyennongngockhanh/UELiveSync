#include "UELiveSyncSubsystem.h"

#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "EngineUtils.h"

#include "Common/TcpSocketBuilder.h"

#include "Sockets.h"
#include "SocketSubsystem.h"

#include "Interfaces/IPv4/IPv4Address.h"

#include "Containers/Ticker.h"

#include "HAL/RunnableThread.h"

#include "LiveSyncRunnable.h"

void UUELiveSyncSubsystem::Initialize(
    FSubsystemCollectionBase& Collection)
{
    Super::Initialize(Collection);

    StartServer();

    BuildActorCache();

    TickHandle =
        FTSTicker::GetCoreTicker().AddTicker(
            FTickerDelegate::CreateUObject(
                this,
                &UUELiveSyncSubsystem::Tick),
            0.0f
        );

    UE_LOG(LogTemp, Warning,
        TEXT("UE Live Sync Started"));
}

void UUELiveSyncSubsystem::Deinitialize()
{
    StopNetworkThread();

    FTSTicker::GetCoreTicker()
        .RemoveTicker(TickHandle);

    if (ConnectionSocket)
    {
        ConnectionSocket->Close();

        ISocketSubsystem::Get(
            PLATFORM_SOCKETSUBSYSTEM)
            ->DestroySocket(
                ConnectionSocket);

        ConnectionSocket = nullptr;
    }

    if (ListenerSocket)
    {
        ListenerSocket->Close();

        ISocketSubsystem::Get(
            PLATFORM_SOCKETSUBSYSTEM)
            ->DestroySocket(
                ListenerSocket);

        ListenerSocket = nullptr;
    }

    Super::Deinitialize();
}

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
        .BoundToAddress(Address)
        .BoundToPort(5000)
        .Listening(8);

    if (!ListenerSocket)
    {
        UE_LOG(LogTemp, Error,
            TEXT("Failed to start TCP server"));

        return;
    }

    UE_LOG(LogTemp, Warning,
        TEXT("Live Sync Listening on port 5000"));
}

bool UUELiveSyncSubsystem::Tick(float DeltaTime)
{
    // =========================================================
    // 1. ACCEPT CONNECTION (SESSION-ATOMIC SAFE)
    // =========================================================
    if (ListenerSocket)
    {
        bool bPending = false;

        if (ListenerSocket->HasPendingConnection(bPending) && bPending)
        {
            FSocket* NewSocket =
                ListenerSocket->Accept(TEXT("LiveSyncConnection"));

            if (NewSocket)
            {
                if (NewSocket->GetConnectionState() == SCS_Connected)
                {
                    UE_LOG(LogTemp, Warning,
                        TEXT("Blender Connected (New Session)"));

                    // 🔴 CRITICAL: ensure old session is fully destroyed first
                    StopNetworkThread();

                    ConnectionSocket = NewSocket;

                    StartNetworkThread();
                }
                else
                {
                    NewSocket->Close();
                    ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM)
                        ->DestroySocket(NewSocket);
                }
            }
        }
    }

    // =========================================================
    // 2. STALE CONNECTION CLEANUP (SAFETY NET)
    // =========================================================
    if (ConnectionSocket)
    {
        const ESocketConnectionState State =
            ConnectionSocket->GetConnectionState();

        if (State != SCS_Connected)
        {
            UE_LOG(LogTemp, Warning,
                TEXT("Stale Connection Detected -> Reset Session"));

            StopNetworkThread();

            ConnectionSocket->Close();

            ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM)
                ->DestroySocket(ConnectionSocket);

            ConnectionSocket = nullptr;
        }
    }

    // =========================================================
    // 3. ACTOR CACHE REFRESH
    // =========================================================
    static float CacheTimer = 0.0f;
    CacheTimer += DeltaTime;

    if (CacheTimer > 5.0f)
    {
        BuildActorCache();
        CacheTimer = 0.0f;
    }

    // =========================================================
    // 4. MAIN PIPELINE
    // =========================================================
    ProcessQueuedPackets();
    InterpolateTransforms(DeltaTime);

    return true;
}

void UUELiveSyncSubsystem::StartNetworkThread()
{
    if (!ConnectionSocket)
    {
        return;
    }

    // 🔴 HARD GUARANTEE
    if (NetworkThread || NetworkRunnable)
    {
        StopNetworkThread();
    }

    NetworkRunnable = new FLiveSyncRunnable(
        ConnectionSocket,
        &PacketQueue);

    NetworkThread = FRunnableThread::Create(
        NetworkRunnable,
        TEXT("UE_LiveSync_Thread"));

    UE_LOG(LogTemp, Warning, TEXT("Network Thread Started"));
}

void UUELiveSyncSubsystem::StopNetworkThread()
{
    if (NetworkRunnable)
    {
        NetworkRunnable->Stop();
    }

    if (NetworkThread)
    {
        NetworkThread->WaitForCompletion();
        delete NetworkThread;
        NetworkThread = nullptr;
    }

    delete NetworkRunnable;
    NetworkRunnable = nullptr;

    // 🔴 flush queue FIRST
    FLiveSyncPacket Dummy;
    while (PacketQueue.Dequeue(Dummy)) {}

    // 🔴 THEN destroy socket
    if (ConnectionSocket)
    {
        ConnectionSocket->Close();
        ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM)
            ->DestroySocket(ConnectionSocket);

        ConnectionSocket = nullptr;
    }
}

void UUELiveSyncSubsystem::ProcessQueuedPackets()
{
    FLiveSyncPacket Packet;

    while (PacketQueue.Dequeue(
        Packet))
    {
        ProcessBinaryPacket(
            Packet);
    }
}

void UUELiveSyncSubsystem::
ProcessBinaryPacket(
    const FLiveSyncPacket& Packet)
{
    if (Packet.RawData.Num() <
        sizeof(FPacketHeader))
    {
        return;
    }

    const uint8* PacketStart =
        Packet.RawData.GetData();

    const uint8* PacketEnd =
        PacketStart +
        Packet.RawData.Num();

    const uint8* Ptr =
        PacketStart;

    FPacketHeader Header;

    FMemory::Memcpy(
        &Header,
        Ptr,
        sizeof(FPacketHeader));

    if (Header.Magic !=
        LIVE_SYNC_MAGIC)
    {
        UE_LOG(LogTemp, Warning,
            TEXT("Invalid packet magic"));

        return;
    }

    if (Header.PacketSize >
        Packet.RawData.Num())
    {
        UE_LOG(LogTemp, Warning,
            TEXT("Invalid packet size"));

        return;
    }

    Ptr += sizeof(FPacketHeader);

    for (uint32 i = 0;
        i < Header.ObjectCount;
        i++)
    {
        if (Ptr + sizeof(uint16) >
            PacketEnd)
        {
            return;
        }

        uint16 NameLength = 0;

        FMemory::Memcpy(
            &NameLength,
            Ptr,
            sizeof(uint16));

        Ptr += sizeof(uint16);

        if (Ptr + NameLength >
            PacketEnd)
        {
            return;
        }

        FUTF8ToTCHAR Converter(
            reinterpret_cast<
                const ANSICHAR*>(Ptr),
            NameLength);

        FString ActorName(
            Converter.Length(),
            Converter.Get());

        Ptr += NameLength;

        // LOCATION

        float LocX;
        float LocY;
        float LocZ;

        FMemory::Memcpy(
            &LocX,
            Ptr,
            sizeof(float));

        Ptr += sizeof(float);

        FMemory::Memcpy(
            &LocY,
            Ptr,
            sizeof(float));

        Ptr += sizeof(float);

        FMemory::Memcpy(
            &LocZ,
            Ptr,
            sizeof(float));

        Ptr += sizeof(float);

        FVector Location(
            (double)LocX,
            (double)LocY,
            (double)LocZ
        );

        // ROTATION

        float RotX;
        float RotY;
        float RotZ;
        float RotW;

        FMemory::Memcpy(
            &RotX,
            Ptr,
            sizeof(float));

        Ptr += sizeof(float);

        FMemory::Memcpy(
            &RotY,
            Ptr,
            sizeof(float));

        Ptr += sizeof(float);

        FMemory::Memcpy(
            &RotZ,
            Ptr,
            sizeof(float));

        Ptr += sizeof(float);

        FMemory::Memcpy(
            &RotW,
            Ptr,
            sizeof(float));

        Ptr += sizeof(float);

        FQuat Rotation(
            (double)RotX,
            (double)RotY,
            (double)RotZ,
            (double)RotW
        );

        Rotation.Normalize();

        // SCALE

        float ScaleX;
        float ScaleY;
        float ScaleZ;

        FMemory::Memcpy(
            &ScaleX,
            Ptr,
            sizeof(float));

        Ptr += sizeof(float);

        FMemory::Memcpy(
            &ScaleY,
            Ptr,
            sizeof(float));

        Ptr += sizeof(float);

        FMemory::Memcpy(
            &ScaleZ,
            Ptr,
            sizeof(float));

        Ptr += sizeof(float);

        FVector Scale(
            (double)ScaleX,
            (double)ScaleY,
            (double)ScaleZ
        );

        UpdateTargetTransform(
            ActorName,
            Location,
            Rotation,
            Scale);
    }
}

void UUELiveSyncSubsystem::
UpdateTargetTransform(
    const FString& ActorName,
    const FVector& Location,
    const FQuat& Rotation,
    const FVector& Scale)
{
    FSyncTransformState& State =
        TransformStates.FindOrAdd(
            ActorName);

    double CurrentTime =
        FPlatformTime::Seconds();

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

        State.bInitialized = true;

        return;
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
        LocationDistance >= 0.05f;

    bool bRotationChanged =
        RotationDistance >= 0.002f;

    bool bScaleChanged =
        ScaleDistance >= 0.001f;

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
        DeltaTime > SMALL_NUMBER)
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

void UUELiveSyncSubsystem::
InterpolateTransforms(
    float DeltaTime)
{
    const float PredictionTime =
        0.012f;

    for (auto& Pair : TransformStates)
    {
        const FString& ActorName =
            Pair.Key;

        FSyncTransformState& State =
            Pair.Value;

        TWeakObjectPtr<AActor>* ActorPtr =
            ActorCache.Find(
                ActorName);

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
            FMath::GetMappedRangeValueClamped(
                FVector2D(0.0f, 300.0f),
                FVector2D(8.0f, 24.0f),
                Distance);

        State.CurrentLocation =
            FMath::VInterpTo(
                State.CurrentLocation,
                PredictedLocation,
                DeltaTime,
                State.AdaptiveInterpSpeed);

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

        Actor->SetActorTransform(
            FinalTransform);
    }
}

void UUELiveSyncSubsystem::BuildActorCache()
{
    ActorCache.Empty();

    UWorld* World = GetWorld();

    if (!World)
    {
        return;
    }

    for (TActorIterator<AActor>
        It(World);
        It;
        ++It)
    {
        AActor* Actor = *It;

        if (!Actor)
        {
            continue;
        }

        FString Name =
            Actor->GetActorLabel();

        ActorCache.Add(
            Name,
            Actor);
    }

    UE_LOG(LogTemp, Warning,
        TEXT("Actor cache built: %d actors"),
        ActorCache.Num());
}

AActor* UUELiveSyncSubsystem::FindActorFast(
    const FString& Name)
{
    TWeakObjectPtr<AActor>* Found =
        ActorCache.Find(Name);

    if (!Found)
    {
        return nullptr;
    }

    return Found->Get();
}