#include "UELiveSyncSubsystem.h"

DEFINE_LOG_CATEGORY(LogLiveSync);

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

#include "HAL/IConsoleManager.h"


// =========================================================
// CONSOLE VARIABLES
// =========================================================

static TAutoConsoleVariable<int32>
    CVarLiveSyncPort(
        TEXT("UE.LiveSync.Port"),
        57000,
        TEXT("TCP port for Blender live sync connection"),
        ECVF_Default
    );

static TAutoConsoleVariable<float>
    CVarLiveSyncHeartbeatTimeout(
        TEXT("UE.LiveSync.HeartbeatTimeout"),
        15.0f,
        TEXT("Seconds without heartbeat before disconnecting"),
        ECVF_Default
    );

static TAutoConsoleVariable<float>
    CVarLiveSyncStateTTL(
        TEXT("UE.LiveSync.StateTTL"),
        60.0f,
        TEXT("Seconds before inactive transform states are evicted"),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncVerbose(
        TEXT("UE.LiveSync.Verbose"),
        0,
        TEXT("Enable verbose sync logging (1=on, 0=off)"),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncInterpMode(
        TEXT("UE.LiveSync.InterpMode"),
        1,
        TEXT("Interpolation mode: 0=direct-set (zero lag), 1=smooth (default)"),
        ECVF_Default
    );

static TAutoConsoleVariable<float>
    CVarLiveSyncInterpSnap(
        TEXT("UE.LiveSync.InterpSnap"),
        0.1f,
        TEXT("Distance threshold in cm for snapping to target instead of interpolating"),
        ECVF_Default
    );

static TAutoConsoleVariable<float>
    CVarLiveSyncThresholdLocation(
        TEXT("UE.LiveSync.Threshold.Location"),
        0.05f,
        TEXT("Min location change in cm to trigger transform update"),
        ECVF_Default
    );

static TAutoConsoleVariable<float>
    CVarLiveSyncThresholdRotation(
        TEXT("UE.LiveSync.Threshold.Rotation"),
        0.002f,
        TEXT("Min rotation change (angular distance) to trigger transform update"),
        ECVF_Default
    );

static TAutoConsoleVariable<float>
    CVarLiveSyncThresholdScale(
        TEXT("UE.LiveSync.Threshold.Scale"),
        0.001f,
        TEXT("Min scale change to trigger transform update"),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncValidateProtocol(
        TEXT("UE.LiveSync.ValidateProtocol"),
        1,
        TEXT("Validate packet type and flags (1=on, 0=off)"),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncQueueWarnThreshold(
        TEXT("UE.LiveSync.QueueWarnThreshold"),
        64,
        TEXT("Queue depth at which a warning is logged"),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncMaxPacketRate(
        TEXT("UE.LiveSync.MaxPacketRate"),
        200,
        TEXT("Max packets processed per tick (overflow stays queued)"),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncMaxDepth(
        TEXT("UE.LiveSync.MaxHierarchyDepth"),
        64,
        TEXT("Maximum hierarchy depth safeguard (0=disabled)"),
        ECVF_Cheat
    );

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
        LogLiveSync,
        Log,
        TEXT("UE Live Sync Started"));

    // =====================================================
    // REGISTER CONSOLE COMMANDS
    // =====================================================

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.DumpState"),
            TEXT("Print all tracked GUIDs, actors, and queue state"),
            FConsoleCommandDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleDumpState),
            ECVF_Default);

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.Reset"),
            TEXT("Full teardown and restart of live sync"),
            FConsoleCommandDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleReset),
            ECVF_Default);

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.Ping"),
            TEXT("Send test heartbeat to verify connectivity"),
            FConsoleCommandDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsolePing),
            ECVF_Default);

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.Stats"),
            TEXT("Print runtime metrics"),
            FConsoleCommandDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleStats),
            ECVF_Default);
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
    if (ListenerSocket)
    {
        return;
    }

    int32 Port =
        CVarLiveSyncPort.GetValueOnGameThread();

    if (Port < 1024 || Port > 65535)
    {
        Port = 57000;
    }

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
            Port)

        .Listening(8);

    if (!ListenerSocket)
    {
        UE_LOG(
            LogLiveSync,
            Error,
            TEXT("Failed to start TCP server on port %d — "
                 "port may be in use"),
            Port);

        return;
    }

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("Live Sync Listening on port %d"),
        Port);
}


// =========================================================
// MAIN TICK
// =========================================================

bool UUELiveSyncSubsystem::Tick(
    float DeltaTime)
{
    VerboseFrameCounter++;

    // =====================================================
    // SYNC CVARS
    // =====================================================

    bEnableVerboseSyncLogs =
        CVarLiveSyncVerbose.GetValueOnGameThread() != 0;

    GEnableVerboseSyncLogs =
        bEnableVerboseSyncLogs;

    // =====================================================
    // RETRY LISTENER IF PREVIOUS BIND FAILED
    // =====================================================

    if (!ListenerSocket)
    {
        static double LastRetryTime = 0.0;

        double Now = FPlatformTime::Seconds();

        if (Now - LastRetryTime >= 5.0)
        {
            LastRetryTime = Now;

            StartServer();
        }
    }

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
                        LogLiveSync,
                        Log,
                        TEXT("Blender Connected"));

                    WatchdogRestartCount = 0;
                    LastWatchdogRestartTime = 0.0;

                    BuildActorCache();

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
            LogLiveSync,
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
            LogLiveSync,
            Log,
            TEXT("Detected thread exit, cleaning up"));

        StopNetworkThread();
    }

    // =====================================================
    // HEARTBEAT TIMEOUT CHECK
    // =====================================================

    float HeartbeatTimeoutVal =
        CVarLiveSyncHeartbeatTimeout.
            GetValueOnGameThread();

    if (ConnectionSocket &&
        LastHeartbeatTime > 0.0 &&
        FPlatformTime::Seconds() - LastHeartbeatTime >
        HeartbeatTimeoutVal)
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("Heartbeat timeout: closing connection"));

        StopNetworkThread();
    }

    // =====================================================
    // NETWORK THREAD WATCHDOG
    // Three distinct signals:
    //   1. Socket starvation — no packets received
    //   2. Thread stall — no thread loop iteration
    //   3. Idle-but-healthy — packets received but no data
    // =====================================================

    if (NetworkRunnable &&
        ConnectionSocket)
    {
        double Now =
            FPlatformTime::Seconds();

        double ThreadLoopTime =
            NetworkRunnable->
                LastThreadLoopTime.load(
                    std::memory_order_relaxed);

        double PacketRecvTime =
            NetworkRunnable->
                LastPacketReceiveTime.load(
                    std::memory_order_relaxed);

        bool bStarvation = false;
        bool bStall = false;

        if (PacketRecvTime > 0.0 &&
            Now - PacketRecvTime > 30.0)
        {
            bStarvation = true;
        }

        if (ThreadLoopTime > 0.0 &&
            Now - ThreadLoopTime > 35.0)
        {
            bStall = true;
        }

        if (bStall || bStarvation)
        {
            double BackoffDelay =
                GetWatchdogBackoff();

            double TimeSinceLastRestart =
                Now - LastWatchdogRestartTime;

            if (TimeSinceLastRestart >=
                BackoffDelay)
            {
                WatchdogRestartCount++;

                LastWatchdogRestartTime =
                    Now;

                UE_LOG(
                    LogLiveSync,
                    Error,
                    TEXT("Network thread watchdog: "
                         "starvation=%d stall=%d "
                         "restartCount=%d backoff=%.1fs"),
                    bStarvation ? 1 : 0,
                    bStall ? 1 : 0,
                    WatchdogRestartCount,
                    BackoffDelay);

                Stats.ReconnectCount.fetch_add(
                    1,
                    std::memory_order_relaxed);

                StopNetworkThread();
            }
        }
    }

    // =====================================================
    // SNAPSHOT TIMEOUT GUARD
    // =====================================================

    if (bInSnapshotBuild)
    {
        double Now =
            FPlatformTime::Seconds();

        bool bNoConnection =
            !ConnectionSocket ||
            ConnectionSocket->
                GetConnectionState()
                != SCS_Connected;

        double Elapsed =
            bNoConnection
                ? 0.0
                : (Now - SnapshotStartTime);

        if (bNoConnection ||
            Elapsed > 5.0)
        {
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("Snapshot timeout: bNoConnection=%d elapsed=%.2fs — aborting"),
                bNoConnection ? 1 : 0,
                Elapsed);

            AbortSnapshot();
        }
    }

    // =====================================================
    // PIPELINE
    // =====================================================

    ProcessQueuedPackets();

    EvictStaleTransformStates();

    InterpolateTransforms(
        DeltaTime);

    ResolvePendingAttachments();

    RecoverMissingActors();

    // =====================================================
    // RUNTIME METRICS (every 60s in verbose mode)
    // =====================================================

    if (bEnableVerboseSyncLogs)
    {
        double Now =
            FPlatformTime::Seconds();

        if (Now - LastMetricsLogTime >=
            MetricsLogInterval)
        {
            LastMetricsLogTime =
                Now;

            LogRuntimeMetrics();
        }
    }

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
            LogLiveSync,
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
            LogLiveSync,
            Log,
            TEXT("StartNetworkThread: already running, stopping old thread"));

        StopNetworkThread();

        // StopNetworkThread nulls the socket.
        // If no new connection was accepted, bail.
        if (!ConnectionSocket)
        {
            UE_LOG(
                LogLiveSync,
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

    NetworkRunnable->SetStats(
        &Stats);

    PacketQueue.SetStats(
        &Stats);

    NetworkThread =

        FRunnableThread::Create(

            NetworkRunnable,

            TEXT("UE_LiveSync_Thread")
        );

    if (NetworkThread)
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("Network Thread Created"));
    }
    else
    {
        UE_LOG(
            LogLiveSync,
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

    PendingAttachments.Empty();

    MissingActorTracker.Empty();

    bInSnapshotBuild = false;
    SnapshotStartTime = 0.0;

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
        LogLiveSync,
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

    int32 MaxRate =
        CVarLiveSyncMaxPacketRate.
            GetValueOnGameThread();

    uint64 ProcessStartCycles =
        FPlatformTime::Cycles64();

    while (
        PacketQueue.Dequeue(
            Packet))
    {
        DequeueCount++;

        if (DequeueCount <=
            MaxRate)
        {
            PacketsThisTick.Add(
                MoveTemp(Packet));
        }
    }

    if (DequeueCount > 0 && ShouldLogVerbose())
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("Dequeued: %d packets (processing %d)"),
            DequeueCount,
            PacketsThisTick.Num());
    }

    if (DequeueCount > MaxRate)
    {
        UE_LOG(
            LogLiveSync,
            Warning,
            TEXT("Packet rate exceeded: %d packets, "
                 "capping at %d, %d deferred to next tick"),
            DequeueCount,
            MaxRate,
            DequeueCount - MaxRate);
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

    // Track processing stats
    int32 Processed =
        PacketsThisTick.Num();

    if (Processed > 0)
    {
        Stats.PacketsProcessed.fetch_add(
            Processed,
            std::memory_order_relaxed);

        uint64 ProcessEndCycles =
            FPlatformTime::Cycles64();

        double ProcessTimeMs =
            FPlatformTime::
            ToMilliseconds64(
                ProcessEndCycles -
                ProcessStartCycles);

        Stats.AvgProcessTimeMs =
            ProcessTimeMs /
            (double)Processed;
    }

    Stats.LastPacketTime =
        FPlatformTime::Seconds();
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
    uint8 PacketFlags = 0x00;

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

        PacketFlags =
            HeaderV3.Flags;

        Ptr =
            PacketData +
            sizeof(FPacketHeaderV3);

        PacketEnd =
            PacketData +
            HeaderV3.PacketSize;

        // =================================================
        // FULL SNAPSHOT FLAG: clear stale state before
        // processing, giving us a clean slate
        // =================================================

        if (PacketFlags &
            PF_FullSnapshot)
        {
            TransformStates.Empty();

            if (SeenThisTick)
            {
                SeenThisTick->Empty();
            }

            if (bEnableVerboseSyncLogs)
            {
                UE_LOG(
                    LogLiveSync,
                    Log,
                    TEXT(
                        "Full snapshot: cleared"
                        " TransformStates + SeenThisTick"));
            }
        }
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
            LogLiveSync,
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
        FMemory::Memcpy(
            &PacketType,
            PacketData +
                sizeof(uint32) +
                sizeof(uint16),
            sizeof(uint8));
    }

    if (ShouldLogVerbose())
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("Header: version=%u type=0x%02x flags=0x%02x "
                 "seq=%llu objects=%u"),
            Version,
            PacketType,
            PacketFlags,
            SequenceId,
            ObjectCount);
    }

    // =====================================================
    // PROTOCOL VALIDATION (V3)
    // =====================================================

    if (Version >= LIVE_SYNC_VERSION_V3 &&
        CVarLiveSyncValidateProtocol.GetValueOnGameThread())
    {
        static constexpr uint8 kValidTypes[] =
            { 0x01, 0x03, 0x04, 0x07, 0x09, 0x0A };

        static constexpr uint8 kValidFlags[] =
            { 0x00, 0x01, 0x02, 0x03 };

        bool bValidType = false;

        for (int32 i = 0; i < 6; i++)
        {
            if (PacketType == kValidTypes[i])
            {
                bValidType = true;
                break;
            }
        }

        if (!bValidType)
        {
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("Invalid packet type 0x%02x, skipping"),
                PacketType);

            return;
        }

        bool bValidFlags = false;

        for (int32 i = 0; i < 4; i++)
        {
            if (PacketFlags == kValidFlags[i])
            {
                bValidFlags = true;
                break;
            }
        }

        if (!bValidFlags)
        {
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("Invalid packet flags 0x%02x, skipping"),
                PacketFlags);

            return;
        }
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
    // SNAPSHOT BOUNDARY MARKERS
    // =====================================================

    if (PacketType == PT_BeginSnapshot)
    {
        HandleBeginSnapshot();
        return;
    }

    if (PacketType == PT_EndSnapshot)
    {
        HandleEndSnapshot();
        return;
    }

    // =====================================================
    // UNKNOWN PACKET TYPE — skip gracefully
    // =====================================================

    if (PacketType != PT_Transform &&
        PacketType != PT_Create &&
        PacketType != PT_Delete)
    {
        UE_LOG(
            LogLiveSync,
            Warning,
            TEXT("Unknown packet type 0x%02X — skipping"),
            PacketType);
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

                // V4 CREATE packets have an extra primitive type byte
                if (Version >=
                    LIVE_SYNC_VERSION_V4 &&
                    PacketType == 0x03)
                {
                    Ptr += 1;
                }
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
        // LOCAL → WORLD — CHILD SPAWN POSITION ONLY
        // =================================================
        // Phase 5B: do NOT convert local transforms to world
        // at ingestion time. Keep original values and pass
        // bIsLocalTransform downstream so UpdateTargetTransform
        // stores local-space values directly.
        //
        // For HandleCreateObject, compute world spawn position
        // separately so the actor starts at the correct world
        // location.

        bool bIsLocalTransform =
            (PacketFlags &
             PF_HasLocalTransform) != 0;

        // Save original values for UpdateTargetTransform
        FVector OriginalLocation = Location;
        FQuat OriginalRotation   = Rotation;
        FVector OriginalScale     = Scale;

        // Compute world spawn position for HandleCreateObject
        FVector SpawnLocation = Location;
        FQuat SpawnRotation   = Rotation;
        FVector SpawnScale     = Scale;

        if (bIsLocalTransform &&
            ParentGuid.IsValid())
        {
            AActor* ParentActor =
                FindActorFast(ParentGuid);

            if (ParentActor)
            {
                FTransform ChildLocal(
                    OriginalRotation,
                    OriginalLocation,
                    OriginalScale);

                FTransform ParentWorld =
                    ParentActor->
                    GetActorTransform();

                FTransform ChildWorld =
                    ChildLocal *
                    ParentWorld;

                SpawnLocation =
                    ChildWorld.GetLocation();

                SpawnRotation =
                    ChildWorld.GetRotation();

                SpawnScale =
                    ChildWorld.GetScale3D();
            }
        }

        // =================================================
        // PRIMITIVE TYPE BYTE (CREATE-only, after parent GUID)
        // =================================================

        uint8 PrimitiveType = LSP_Cube;

        // Only read primitive type byte for V4+ CREATE packets.
        // V3 CREATE packets end after parent GUID (80 bytes total).
        if (PacketType == 0x03 &&
            Version >= LIVE_SYNC_VERSION_V4 &&
            Ptr < PacketEnd)
        {
            PrimitiveType = *Ptr;
            Ptr += 1;
        }

        // =================================================
        // APPLY
        // =================================================

        if (PacketType == 0x03)
        {
            HandleCreateObject(
                Guid,
                SpawnLocation,
                SpawnRotation,
                SpawnScale,
                ParentGuid,
                PrimitiveType,
                bIsLocalTransform);
        }

        UpdateTargetTransform(

            Guid,

            OriginalLocation,

            OriginalRotation,

            OriginalScale,

            ParentGuid,

            bIsLocalTransform
        );

        if (ShouldLogVerbose())
        {
            UE_LOG(
                LogLiveSync,
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

    const FGuid& ParentGuid,

    bool bIsLocalTransform)
{
    FSyncTransformState& State =

        TransformStates.
        FindOrAdd(
            Guid);

    double CurrentTime =
        FPlatformTime::
        Seconds();

    // Save parent-change before overwriting
    bool bParentChanged =
        ParentGuid !=
        State.ParentGuid;

    if (!State.bInitialized)
    {
        if (bIsLocalTransform &&
            ParentGuid.IsValid())
        {
            // Attached child: initialize local-space state
            State.CurrentLocalLocation =
                Location;

            State.CurrentLocalRotation =
                Rotation;

            State.CurrentLocalScale =
                Scale;

            State.LocalTargetLocation =
                Location;

            State.LocalTargetRotation =
                Rotation;

            State.LocalTargetScale =
                Scale;

            State.bHasLocalTarget =
                true;

            // NON-AUTHORITATIVE
            // World-space current state for attached actors
            // is informational only.
            // Attached interpolation authority remains
            // local-space.

            AActor* Parent =
                FindActorFast(ParentGuid);

            if (Parent)
            {
                FTransform LocalXForm(
                    Rotation,
                    Location,
                    Scale);

                FTransform ParentWorld =
                    Parent->
                    GetActorTransform();

                FTransform WorldXForm =
                    LocalXForm *
                    ParentWorld;

                State.CurrentLocation =
                    WorldXForm.
                    GetLocation();

                State.CurrentRotation =
                    WorldXForm.
                    GetRotation();

                State.CurrentScale =
                    WorldXForm.
                    GetScale3D();
            }
        }
        else
        {
            // Root actor: initialize world-space state
            State.CurrentLocation =
                Location;

            State.CurrentRotation =
                Rotation;

            State.CurrentScale =
                Scale;

            State.bHasLocalTarget =
                false;
        }

        // Target* mirrors current for initial state
        if (State.bHasLocalTarget)
        {
            State.TargetLocation =
                State.CurrentLocation;

            State.TargetRotation =
                State.CurrentRotation;

            State.TargetScale =
                State.CurrentScale;
        }
        else
        {
            State.TargetLocation =
                Location;

            State.TargetRotation =
                Rotation;

            State.TargetScale =
                Scale;
        }

        State.ParentGuid =
            ParentGuid;

        State.bHasParent =
            ParentGuid.IsValid();

        State.LastUpdateTime =
            CurrentTime;

        State.bInitialized =
            true;

        State.bPendingSceneGraphWrite =
            true;
    }

    // =====================================================
    // THRESHOLD CHANGE DETECTION
    // =====================================================
    // Compare against authoritative target:
    //   children → LocalTarget* (local space)
    //   roots    → Target* (world space)

    float LocThreshold =
        CVarLiveSyncThresholdLocation.
            GetValueOnGameThread();

    float RotThreshold =
        CVarLiveSyncThresholdRotation.
            GetValueOnGameThread();

    float SclThreshold =
        CVarLiveSyncThresholdScale.
            GetValueOnGameThread();

    float LocationDistance;

    float RotationDistance;

    float ScaleDistance;

    if (State.bHasLocalTarget)
    {
        LocationDistance =
            FVector::Dist(
                State.LocalTargetLocation,
                Location);

        RotationDistance =
            State.LocalTargetRotation.
            AngularDistance(
                Rotation);

        ScaleDistance =
            FVector::Dist(
                State.LocalTargetScale,
                Scale);
    }
    else
    {
        LocationDistance =
            FVector::Dist(
                State.TargetLocation,
                Location);

        RotationDistance =
            State.TargetRotation.
            AngularDistance(
                Rotation);

        ScaleDistance =
            FVector::Dist(
                State.TargetScale,
                Scale);
    }

    bool bLocationChanged =
        LocationDistance >=
        LocThreshold;

    bool bRotationChanged =
        RotationDistance >=
        RotThreshold;

    bool bScaleChanged =
        ScaleDistance >=
        SclThreshold;

    if (!bLocationChanged &&
        !bRotationChanged &&
        !bScaleChanged)
    {
        if (bParentChanged)
        {
            State.ParentGuid =
                ParentGuid;

            State.bHasParent =
                ParentGuid.IsValid();

            if (!State.bHasParent)
            {
                DetachFromParent(Guid);
            }
            else
            {
                AttachToParent(
                    Guid,
                    ParentGuid);
            }

            State.bPendingSceneGraphWrite =
                true;
        }

        return;
    }

    // =====================================================
    // VELOCITY (root prediction only)
    // =====================================================

    if (!State.bHasLocalTarget)
    {
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
    }

    // =====================================================
    // STORE AUTHORITATIVE TARGET
    // =====================================================

    if (State.bHasLocalTarget)
    {
        // Attached child: store local target (authoritative)

        State.LocalTargetLocation =
            Location;

        State.LocalTargetRotation =
            Rotation;

        State.LocalTargetScale =
            Scale;

        // NON-AUTHORITATIVE
        // Derived debug/fallback world-space cache only.
        // May become stale after parent movement.

        {
            AActor* Parent =
                FindActorFast(ParentGuid);

            if (Parent)
            {
                FTransform LocalXForm(
                    Rotation,
                    Location,
                    Scale);

                FTransform ParentWorld =
                    Parent->
                    GetActorTransform();

                FTransform WorldXForm =
                    LocalXForm *
                    ParentWorld;

                State.TargetLocation =
                    WorldXForm.
                    GetLocation();

                State.TargetRotation =
                    WorldXForm.
                    GetRotation();

                State.TargetScale =
                    WorldXForm.
                    GetScale3D();
            }
        }
    }
    else
    {
        // Root actor: store world-space target

        State.TargetLocation =
            Location;

        State.TargetRotation =
            Rotation;

        State.TargetScale =
            Scale;
    }

    State.ParentGuid =
        ParentGuid;

    State.bHasParent =
        ParentGuid.IsValid();

    State.LastUpdateTime =
        CurrentTime;

    State.bPendingSceneGraphWrite =
        true;

    // =====================================================
    // HANDLE PARENT CHANGE
    // =====================================================
    // Uses bParentChanged saved before State was modified.

    if (bParentChanged)
    {
        if (!State.bHasParent)
        {
            DetachFromParent(Guid);
        }
        else
        {
            AttachToParent(
                Guid,
                ParentGuid);
        }
    }
    // NOTE: unconditional AttachToParent on every tick
    // for bHasParent actors is REMOVED.
    // AttachToParent is only called on parent change.
    // Idempotency is maintained by AttachToParent's
    // internal guard against same-parent re-attach.

    // =====================================================
    // VERBOSE AUTHORITY-PATH LOGGING
    // =====================================================

    if (bEnableVerboseSyncLogs)
    {
        if (State.bHasLocalTarget &&
            State.bHasParent)
        {
            UE_LOG(
                LogLiveSync,
                Log,
                TEXT(
                    "Authority: child=%s"
                    " local target updated"),
                *Guid.ToString(
                    EGuidFormats::Digits));
        }
        else
        {
            UE_LOG(
                LogLiveSync,
                Log,
                TEXT(
                    "Authority: root=%s"
                    " world target updated"),
                *Guid.ToString(
                    EGuidFormats::Digits));
        }
    }
}



// =========================================================
// INTERPOLATION
// =========================================================

void UUELiveSyncSubsystem::
InterpolateTransforms(
    float DeltaTime)
{
    // Skip interpolation during snapshot build — all transforms
    // will be bulk-applied when EndSnapshot is received
    if (bInSnapshotBuild)
    {
        return;
    }

    const float PredictionTime =
        0.012f;

    int32 InterpMode =
        CVarLiveSyncInterpMode.
            GetValueOnGameThread();

    float SnapDist =
        CVarLiveSyncInterpSnap.
            GetValueOnGameThread();

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

        // ---------------------------------------------------
        // CONVERGENCE CHECK
        // ---------------------------------------------------

        bool bLocationConverged;
        bool bRotationConverged;
        bool bScaleConverged;

        if (State.bHasLocalTarget && State.bHasParent)
        {
            // Attached child: check local-space convergence
            bLocationConverged =
                FVector::Dist(
                    State.CurrentLocalLocation,
                    State.LocalTargetLocation)
                < KINDA_SMALL_NUMBER;

            bRotationConverged =
                State.CurrentLocalRotation.
                    Equals(
                        State.LocalTargetRotation,
                        0.01f);

            bScaleConverged =
                FVector::Dist(
                    State.CurrentLocalScale,
                    State.LocalTargetScale)
                < KINDA_SMALL_NUMBER;
        }
        else
        {
            // Root actor: check world-space convergence
            bLocationConverged =
                FVector::Dist(
                    State.CurrentLocation,
                    State.TargetLocation)
                < KINDA_SMALL_NUMBER;

            bRotationConverged =
                State.CurrentRotation.
                    Equals(
                        State.TargetRotation,
                        0.01f);

            bScaleConverged =
                FVector::Dist(
                    State.CurrentScale,
                    State.TargetScale)
                < KINDA_SMALL_NUMBER;
        }

        if (bLocationConverged &&
            bRotationConverged &&
            bScaleConverged)
        {
            ConvergedCount++;
            continue;
        }

        // ---------------------------------------------------
        // ATTACHED ACTOR PATH
        // ---------------------------------------------------
        // Attached actors never continuously drive world-space
        // transforms. UE attachment propagation owns child world
        // motion. Local-space interpolation updates internal
        // state only.

        if (State.bHasLocalTarget && State.bHasParent)
        {
            if (InterpMode == 0)
            {
                // Direct-set: snap local state to target
                State.CurrentLocalLocation =
                    State.LocalTargetLocation;

                State.CurrentLocalRotation =
                    State.LocalTargetRotation;

                State.CurrentLocalScale =
                    State.LocalTargetScale;
            }
            else if (FVector::Dist(
                State.CurrentLocalLocation,
                State.LocalTargetLocation) < SnapDist)
            {
                // Snap when close
                State.CurrentLocalLocation =
                    State.LocalTargetLocation;

                State.CurrentLocalRotation =
                    State.LocalTargetRotation;

                State.CurrentLocalScale =
                    State.LocalTargetScale;

                SnapCount++;
            }
            else
            {
                // Smooth interpolation in local space
                State.CurrentLocalLocation =
                    FMath::VInterpTo(
                        State.CurrentLocalLocation,
                        State.LocalTargetLocation,
                        DeltaTime,
                        State.AdaptiveInterpSpeed);

                // Patch 3: Normalize after Slerp to prevent
                // quaternion drift across long-running sessions.
                State.CurrentLocalRotation =
                    FQuat::Slerp(
                        State.CurrentLocalRotation,
                        State.LocalTargetRotation,
                        DeltaTime * 12.0f).
                    GetNormalized();

                // Assumes stable mostly-uniform hierarchical
                // scale behavior. Correct non-uniform
                // hierarchical scale propagation is deferred.
                State.CurrentLocalScale =
                    State.LocalTargetScale;

                InterpCount++;
            }

            // Scene graph write only when pending
            if (State.bPendingSceneGraphWrite)
            {
                AActor* Parent =
                    FindActorFast(
                        State.ParentGuid);

                if (Parent)
                {
                    FTransform LocalXForm(
                        State.CurrentLocalRotation,
                        State.CurrentLocalLocation,
                        State.CurrentLocalScale);

                    FTransform WorldXForm =
                        LocalXForm *
                        Parent->
                        GetActorTransform();

                    Actor->SetActorTransform(
                        WorldXForm);

                    // NON-AUTHORITATIVE
                    // Update debug world cache for diagnostics
                    State.CurrentLocation =
                        WorldXForm.GetLocation();

                    State.CurrentRotation =
                        WorldXForm.GetRotation();

                    State.CurrentScale =
                        WorldXForm.GetScale3D();
                }

                State.bPendingSceneGraphWrite =
                    false;

                InterpCount++;
            }

            // =================================================
            // DRIFT DIAGNOSTICS (verbose-only, thresholded)
            // =================================================

            if (bEnableVerboseSyncLogs)
            {
                AActor* Parent =
                    FindActorFast(
                        State.ParentGuid);

                if (Parent)
                {
                    FTransform LocalXForm(
                        State.CurrentLocalRotation,
                        State.CurrentLocalLocation,
                        State.CurrentLocalScale);

                    FTransform ExpectedWorld =
                        LocalXForm *
                        Parent->
                        GetActorTransform();

                    double Err =
                        FVector::Dist(
                            Actor->
                            GetActorLocation(),
                            ExpectedWorld.
                            GetLocation());

                    if (Err > 0.01)
                    {
                        UE_LOG(
                            LogLiveSync,
                            Verbose,
                            TEXT(
                                "Drift: child=%s"
                                " err=%.4f"),
                            *Guid.ToString(
                                EGuidFormats::
                                Digits),
                            Err);
                    }

                    if (Err >
                        HierarchyDiag.
                        MaxWorldErrorDistance)
                    {
                        HierarchyDiag.
                        MaxWorldErrorDistance =
                            Err;
                    }

                    HierarchyDiag.
                        WorldErrorDistance =
                            Err;
                }
            }

            continue;
        }

        // ---------------------------------------------------
        // ROOT ACTOR PATH
        // ---------------------------------------------------

        // =================================================
        // DIRECT-SET MODE (zero lag)
        // =================================================

        if (InterpMode == 0)
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

            InterpCount++;
            continue;
        }

        // =================================================
        // SNAP WHEN CLOSE
        // =================================================

        float DistToTarget =

            FVector::Dist(

                State.CurrentLocation,

                State.TargetLocation);

        if (DistToTarget < SnapDist)
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

        // =================================================
        // SMOOTH INTERPOLATION (adaptive speed)
        // =================================================

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
                    100.0f),

                FVector2D(
                    16.0f,
                    40.0f),

                Distance);

        State.CurrentLocation =

            FMath::VInterpTo(

                State.CurrentLocation,

                PredictedLocation,

                DeltaTime,

                State.
                AdaptiveInterpSpeed);

        // Patch 3: Normalize after Slerp to prevent
        // quaternion drift across long-running sessions.
        State.CurrentRotation =

            FQuat::Slerp(

                State.CurrentRotation,

                State.TargetRotation,

                DeltaTime *
                12.0f).
            GetNormalized();

        State.CurrentScale =

            State.TargetScale;

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
            LogLiveSync,
            Log,
            TEXT(
                "Transform states: total=%d missing=%d converged=%d snap=%d interp=%d interpMode=%d"),
            Total,
            MissingCount,
            ConvergedCount,
            SnapCount,
            InterpCount,
            InterpMode
        );
    }
}


// =========================================================
// EVICT STALE TRANSFORM STATES (TTL)
// =========================================================

void UUELiveSyncSubsystem::
EvictStaleTransformStates()
{
    double StateTTL =
        CVarLiveSyncStateTTL.
            GetValueOnGameThread();

    double CurrentTime =
        FPlatformTime::Seconds();

    TArray<FGuid> StaleGuids;

    for (const auto& Pair :
        TransformStates)
    {
        if (CurrentTime -
            Pair.Value.LastUpdateTime
            > StateTTL)
        {
            StaleGuids.Add(
                Pair.Key);
        }
    }

    if (StaleGuids.Num() == 0)
    {
        return;
    }

    for (const FGuid& Guid :
        StaleGuids)
    {
        TransformStates.Remove(
            Guid);

        ActorCache.Remove(
            Guid);
    }

    if (ShouldLogVerbose())
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("Evicted %d stale transform states"),
            StaleGuids.Num());
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
        LogLiveSync,
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
                LogLiveSync,
                Warning,
                TEXT("TryCacheActor: %s has bad GUID tag: %s"),
                *Actor->GetName(),
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
            LogLiveSync,
            Log,
            TEXT("Cached Actor %s | GUID=%s"),

            *Actor->GetName(),

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

FGuid UUELiveSyncSubsystem::
FindGuidForActor(
    AActor* Actor) const
{
    if (!Actor)
    {
        return FGuid();
    }

    FString Prefix =
        TEXT("LiveSync_GUID=");

    for (const FName& Tag :
        Actor->Tags)
    {
        FString TagString =
            Tag.ToString();

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

        if (FGuid::ParseExact(

            GuidString,

            EGuidFormats::Digits,

            Guid))
        {
            return Guid;
        }
    }

    return FGuid();
}


void UUELiveSyncSubsystem::
OnActorDestroyed(
    AActor* Actor)
{
    FGuid Guid =
        FindGuidForActor(Actor);

    if (Guid.IsValid())
    {
        TransformStates.Remove(
            Guid);

        if (bEnableVerboseSyncLogs)
        {
            UE_LOG(
                LogLiveSync,
                Log,
                TEXT(
                    "OnActorDestroyed: removed TransformState"
                    " for %s | GUID=%s"),
                *Actor->GetName(),
                *Guid.ToString(
                    EGuidFormats::Digits));
        }
    }

    // Also clean ActorCache (both by pointer and by GUID)
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
// HIERARCHY — ATTACH TO PARENT
// =========================================================

void UUELiveSyncSubsystem::
AttachToParent(
    const FGuid& Guid,
    const FGuid& ParentGuid)
{
    if (!ParentGuid.IsValid())
    {
        return;
    }

    // =====================================================
    // Self-parent rejection
    // =====================================================

    if (Guid == ParentGuid)
    {
        UE_LOG(
            LogLiveSync,
            Warning,
            TEXT(
                "Self-parent rejection:"
                " child=parent=%s"),
            *Guid.ToString(
                EGuidFormats::Digits));
        return;
    }

    AActor* Child =
        FindActorFast(Guid);

    if (!Child)
    {
        return;
    }

    AActor* Parent =
        FindActorFast(
            ParentGuid);

    if (!Parent || bInSnapshotBuild)
    {
        if (bInSnapshotBuild)
        {
            // During snapshot build, defer all attachments
            FPendingAttachment NewEntry;
            NewEntry.Child = Guid;
            NewEntry.Parent = ParentGuid;
            NewEntry.RetryFrames = 0;
            NewEntry.CreatedTime =
                FPlatformTime::Seconds();

            PendingAttachments.Add(
                NewEntry);
        }
        else if (!Parent)
        {
            // Push to deferred retry queue
            FPendingAttachment NewEntry;
            NewEntry.Child = Guid;
            NewEntry.Parent = ParentGuid;
            NewEntry.RetryFrames = 0;
            NewEntry.CreatedTime =
                FPlatformTime::Seconds();

            PendingAttachments.Add(
                NewEntry);

            if (bEnableVerboseSyncLogs)
            {
                UE_LOG(
                    LogLiveSync,
                    Log,
                    TEXT(
                        "AttachToParent: child=%s"
                        " parent=%s not yet cached,"
                        " deferred"),
                    *Guid.ToString(
                        EGuidFormats::Digits),
                    *ParentGuid.ToString(
                        EGuidFormats::Digits));
            }
        }

        return;
    }

    // Guard: already attached to correct parent
    if (Child->GetAttachParentActor()
        == Parent)
    {
        // Attached while already attached to same parent = churn
        HierarchyDiag.AttachmentChurnCount++;
        return;
    }

    // =====================================================
    // Max hierarchy depth walk with cycle detection
    // =====================================================

    int32 MaxDepth =
        CVarLiveSyncMaxDepth.
            GetValueOnGameThread();

    if (MaxDepth > 0)
    {
        int32 Depth = 0;
        AActor* Probe = Parent;

        while (Probe)
        {
            // Patch 4: Explicit cycle detection.
            // Avoid relying solely on depth overflow.
            if (Probe == Child)
            {
                UE_LOG(
                    LogLiveSync,
                    Warning,
                    TEXT(
                        "Hierarchy cycle detected:"
                        " child=%s parent=%s"),
                    *Guid.ToString(
                        EGuidFormats::Digits),
                    *ParentGuid.ToString(
                        EGuidFormats::Digits));
                return;
            }

            Depth++;

            if (Depth > MaxDepth)
            {
                UE_LOG(
                    LogLiveSync,
                    Warning,
                    TEXT(
                        "Exceeded max hierarchy depth"
                        " %d: child=%s parent=%s"),
                    MaxDepth,
                    *Guid.ToString(
                        EGuidFormats::Digits),
                    *ParentGuid.ToString(
                        EGuidFormats::Digits));
                return;
            }

            Probe =
                Probe->
                GetAttachParentActor();
        }
    }

    Child->AttachToActor(
        Parent,
        FAttachmentTransformRules::
            KeepWorldTransform);

    // Verbose authority transition log
    if (bEnableVerboseSyncLogs)
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT(
                "Authority: child=%s"
                " entering local mode"
                " parent=%s"),
            *Guid.ToString(
                EGuidFormats::Digits),
            *ParentGuid.ToString(
                EGuidFormats::Digits));
    }

    HierarchyDiag.ReattachCount++;

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT(
            "Attached child=%s to parent=%s"),
        *Guid.ToString(
            EGuidFormats::Digits),
        *ParentGuid.ToString(
            EGuidFormats::Digits));
}


// =========================================================
// HIERARCHY — DETACH FROM PARENT
// =========================================================

void UUELiveSyncSubsystem::
DetachFromParent(
    const FGuid& Guid)
{
    AActor* Actor =
        FindActorFast(Guid);

    if (!Actor)
    {
        return;
    }

    if (!Actor->GetAttachParentActor())
    {
        return;
    }

    Actor->DetachFromActor(
        FDetachmentTransformRules::
            KeepWorldTransform);

    // Patch 2: Child -> root transition.
    // Local-authority interpolation no longer valid.
    if (FSyncTransformState* State =
        TransformStates.Find(Guid))
    {
        State->bHasLocalTarget =
            false;

        // Re-seed authoritative world state from actor.
        // Prevents frozen transforms after detach.
        if (AActor* DetachedActor =
            FindActorFast(Guid))
        {
            State->CurrentLocation =
                DetachedActor->
                GetActorLocation();

            State->CurrentRotation =
                DetachedActor->
                GetActorQuat();

            State->CurrentScale =
                DetachedActor->
                GetActorScale3D();
        }

        State->bPendingSceneGraphWrite =
            true;
    }

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT(
            "Detached actor=%s from parent"),
        *Guid.ToString(
            EGuidFormats::Digits));
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

    const FGuid& ParentGuid,

    uint8 PrimitiveType,

    bool bIsLocalTransform)
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
            LogLiveSync,
            Log,
            TEXT("GUID match %s: found existing actor %s, skip spawn"),
            *Guid.ToString(
                EGuidFormats::Digits),
            *Existing->GetName());

        return;
    }

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("GUID match %s: NOT found in cache, spawning new actor"),
        *Guid.ToString(
            EGuidFormats::Digits));

    FActorSpawnParameters SpawnParams;

    SpawnParams.SpawnCollisionHandlingOverride =
        ESpawnActorCollisionHandlingMethod::
            AlwaysSpawn;

    // =====================================================
    // Step 1: Spawn actor at world position.
    // Location/Rotation/Scale are already world-space
    // (ProcessBinaryPacket computed world for children).
    // =====================================================

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

    // =====================================================
    // Tag and cache
    // =====================================================

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

    // =====================================================
    // Step 2: Attach to parent (initial attach).
    // Attach BEFORE initializing local state so attachment
    // is established before the first interpolation tick.
    // KeepWorldTransform preserves the initial world pose.
    // =====================================================

    AttachToParent(
        Guid,
        ParentGuid);

    // =====================================================
    // Validate primitive type
    // =====================================================

    if (PrimitiveType > LSP_Empty)
    {
        UE_LOG(
            LogLiveSync,
            Warning,
            TEXT("Unknown primitive type 0x%02X, defaulting to Cube"),
            PrimitiveType);

        PrimitiveType =
            LSP_Cube;
    }

    if (PrimitiveType == LSP_Empty)
    {
        // NOTE: State initialization is handled by the caller
        // (ProcessBinaryPacket or RecoverMissingActors) via
        // an explicit UpdateTargetTransform call with correct
        // (possibly local-space) transform values.
        // This avoids passing world-spawn values as local targets.

        return;
    }

    UStaticMeshComponent* MeshComp =
        NewObject<UStaticMeshComponent>(
            NewActor);

    MeshComp->SetMobility(
        EComponentMobility::Movable);

    MeshComp->SetVisibility(
        true, true);

    UStaticMesh* PrimitiveMesh = nullptr;

    switch (PrimitiveType)
    {
    case LSP_Sphere:
        PrimitiveMesh = LoadObject<UStaticMesh>(
            nullptr,
            TEXT("/Engine/BasicShapes/Sphere.Sphere"));
        break;

    case LSP_Cylinder:
        PrimitiveMesh = LoadObject<UStaticMesh>(
            nullptr,
            TEXT("/Engine/BasicShapes/Cylinder.Cylinder"));
        break;

    case LSP_Plane:
        PrimitiveMesh = LoadObject<UStaticMesh>(
            nullptr,
            TEXT("/Engine/BasicShapes/Plane.Plane"));
        break;

    case LSP_Cube:
    default:
        PrimitiveMesh = LoadObject<UStaticMesh>(
            nullptr,
            TEXT("/Engine/BasicShapes/Cube.Cube"));
        break;
    }

    if (PrimitiveMesh)
    {
        MeshComp->SetStaticMesh(
            PrimitiveMesh);
    }

    MeshComp->SetCollisionEnabled(
        ECollisionEnabled::NoCollision);

    NewActor->SetRootComponent(
        MeshComp);

    MeshComp->RegisterComponent();

    // NOTE: State initialization is handled by the caller
    // (ProcessBinaryPacket or RecoverMissingActors) via
    // an explicit UpdateTargetTransform call with correct
    // (possibly local-space) transform values.
    // This avoids passing world-spawn values as local targets.
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

    bool bCacheHadEntry =
        ActorCache.Find(Guid) != nullptr;

    bool bTransformHadState =
        TransformStates.Find(Guid) != nullptr;

    // Remove from pending attachments if queued
    PendingAttachments.RemoveAll(
        [&Guid](
            const FPendingAttachment&
            Entry)
        {
            return Entry.Child == Guid ||
                   Entry.Parent == Guid;
        });

    // Remove from missing actor tracker
    MissingActorTracker.Remove(
        Guid);

    if (!bInSnapshotBuild)
    {
        if (Actor)
        {
            Actor->Destroy();
        }

        ActorCache.Remove(Guid);
    }

    TransformStates.Remove(
        Guid);

    if (bEnableVerboseSyncLogs)
    {
        FString ActorName =
            Actor
                ? Actor->GetName()
                : TEXT("nullptr");

        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("[Delete] GUID=%s Actor=%s Removed=%d StaleCache=%d"),
            *Guid.ToString(
                EGuidFormats::Digits),
            *ActorName,
            Actor ? 1 : 0,
            bCacheHadEntry ? 1 : 0);
    }
}


// =========================================================
// HANDLE BEGIN SNAPSHOT
// =========================================================

void UUELiveSyncSubsystem::
HandleBeginSnapshot()
{
    bInSnapshotBuild = true;
    SnapshotStartTime =
        FPlatformTime::Seconds();

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("Snapshot build started — entering accumulation mode"));
}


// =========================================================
// ABORT SNAPSHOT
// =========================================================

void UUELiveSyncSubsystem::
AbortSnapshot()
{
    if (!bInSnapshotBuild)
    {
        return;
    }

    bInSnapshotBuild = false;
    SnapshotStartTime = 0.0;

    int32 PendingCount =
        PendingAttachments.Num();

    PendingAttachments.Empty();

    UE_LOG(
        LogLiveSync,
        Warning,
        TEXT("Snapshot aborted — flushed %d pending attachments"),
        PendingCount);
}


// =========================================================
// HANDLE END SNAPSHOT
// =========================================================

void UUELiveSyncSubsystem::
HandleEndSnapshot()
{
    bInSnapshotBuild = false;

    // Resolve all deferred hierarchy attachments
    ResolvePendingAttachments();

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("Snapshot build ended — flushed %d pending attachments"),
        PendingAttachments.Num());
}


// =========================================================
// RESOLVE PENDING ATTACHMENTS
// =========================================================

void UUELiveSyncSubsystem::
ResolvePendingAttachments()
{
    double Now =
        FPlatformTime::Seconds();

    TArray<FPendingAttachment>
        Remaining;

    static constexpr int32
        MaxRetries = 60;

    static constexpr int32
        FastWindow = 10;

    for (const FPendingAttachment&
        Entry : PendingAttachments)
    {
        int32 Retries =
            Entry.RetryFrames + 1;

        // Timeout: 60 retry attempts or 5s wall-clock
        if (Retries >= MaxRetries ||
            (Now - Entry.CreatedTime) > 5.0)
        {
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT(
                    "Deferred attach timeout: "
                    "child=%s parent=%s"),
                *Entry.Child.ToString(
                    EGuidFormats::Digits),
                *Entry.Parent.ToString(
                    EGuidFormats::Digits));

            continue;
        }

        // Determine if this frame is a retry frame
        bool bRetryFrame = (
            Retries <= FastWindow ||
            Retries % 5 == 0
        );

        FPendingAttachment Updated =
            Entry;

        Updated.RetryFrames = Retries;

        if (bRetryFrame)
        {
            AActor* Parent =
                FindActorFast(
                    Entry.Parent);

            if (Parent)
            {
                AActor* Child =
                    FindActorFast(
                        Entry.Child);

                if (Child)
                {
                    Child->AttachToActor(
                        Parent,
                        FAttachmentTransformRules::
                            KeepWorldTransform);

                    // Patch 1: Force one world-space recompute
                    // on next interp tick. Child may have
                    // advanced local interpolation while waiting
                    // for parent resolution.
                    if (FSyncTransformState*
                        State =
                        TransformStates.Find(
                            Entry.Child))
                    {
                        State->
                            bPendingSceneGraphWrite =
                            true;
                    }

                    if (
                        bEnableVerboseSyncLogs
                    )
                    {
                        UE_LOG(
                            LogLiveSync,
                            Log,
                            TEXT(
                                "Authority: deferred"
                                " attach resolved"
                                " child=%s parent=%s"),
                            *Entry.Child.ToString(
                                EGuidFormats::Digits),
                            *Entry.Parent.ToString(
                                EGuidFormats::Digits));
                    }

                    // Resolved — don't requeue
                    continue;
                }
            }
        }

        // Not resolved yet — keep for next frame
        Remaining.Add(Updated);
    }

    PendingAttachments =
        MoveTemp(Remaining);
}


// =========================================================
// RECOVER MISSING ACTORS
// =========================================================

void UUELiveSyncSubsystem::
RecoverMissingActors()
{
    double Now =
        FPlatformTime::Seconds();

    static constexpr int32
        MaxRecoveryAttempts = 3;

    TArray<FGuid> Resolved;

    for (auto& Pair :
        MissingActorTracker)
    {
        const FGuid& Guid =
            Pair.Key;

        FMissingActorState&
            State =
            Pair.Value;

        AActor* Existing =
            FindActorFast(Guid);

        if (Existing)
        {
            // Actor reappeared naturally
            Resolved.Add(Guid);
            continue;
        }

        FSyncTransformState*
            TransformState =
            TransformStates.Find(
                Guid);

        if (!TransformState)
        {
            // No transform state — nothing to recover
            Resolved.Add(Guid);
            continue;
        }

        State.MissingFrames++;

        if (State.MissingFrames >= 10 &&
            (Now - State.LastWarningTime) > 30.0)
        {
            State.LastWarningTime = Now;

            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("Missing actor GUID=%s — %d frames"),
                *Guid.ToString(
                    EGuidFormats::Digits),
                State.MissingFrames);
        }

        if (State.MissingFrames >= 30 &&
            !State.bRecoveryAttempted &&
            State.RecoveryAttempts < MaxRecoveryAttempts)
        {
            State.bRecoveryAttempted = true;
            State.RecoveryAttempts++;

            UE_LOG(
                LogLiveSync,
                Log,
                TEXT("Recovering missing actor GUID=%s (attempt %d/%d)"),
                *Guid.ToString(
                    EGuidFormats::Digits),
                State.RecoveryAttempts,
                MaxRecoveryAttempts);

            if (TransformState->bHasLocalTarget &&
                TransformState->ParentGuid.IsValid())
            {
                // Child recovery: pass local values directly.
                // HandleCreateObject computes world for spawn.
                HandleCreateObject(
                    Guid,
                    TransformState->
                        LocalTargetLocation,
                    TransformState->
                        LocalTargetRotation,
                    TransformState->
                        LocalTargetScale,
                    TransformState->ParentGuid,
                    LSP_Cube,
                    true);

                // Initialize state with stored local values
                UpdateTargetTransform(
                    Guid,
                    TransformState->
                        LocalTargetLocation,
                    TransformState->
                        LocalTargetRotation,
                    TransformState->
                        LocalTargetScale,
                    TransformState->ParentGuid,
                    true);
            }
            else
            {
                // Root recovery: pass world-space values.
                HandleCreateObject(
                    Guid,
                    TransformState->
                        TargetLocation,
                    TransformState->
                        TargetRotation,
                    TransformState->
                        TargetScale,
                    TransformState->ParentGuid,
                    LSP_Cube,
                    false);

                // Initialize state with stored world values
                UpdateTargetTransform(
                    Guid,
                    TransformState->
                        TargetLocation,
                    TransformState->
                        TargetRotation,
                    TransformState->
                        TargetScale,
                    TransformState->ParentGuid,
                    false);
            }
        }
        else if (State.MissingFrames >= 30 &&
                 State.RecoveryAttempts >= MaxRecoveryAttempts)
        {
            // Suppress repeated warning logs — only log once per frame
            if (State.MissingFrames == 30)
            {
                UE_LOG(
                    LogLiveSync,
                    Warning,
                    TEXT("Missing actor GUID=%s — max recovery attempts (%d) reached, giving up"),
                    *Guid.ToString(
                        EGuidFormats::Digits),
                    MaxRecoveryAttempts);
            }
        }

        if (State.MissingFrames > 60)
        {
            UE_LOG(
                LogLiveSync,
                Log,
                TEXT("Giving up recovery for GUID=%s — evicting"),
                *Guid.ToString(
                    EGuidFormats::Digits));

            TransformStates.Remove(Guid);

            Resolved.Add(Guid);
        }
    }

    for (const FGuid& Guid :
        Resolved)
    {
        MissingActorTracker.Remove(
            Guid);
    }
}


// =========================================================
// LOG RUNTIME METRICS
// =========================================================

void UUELiveSyncSubsystem::
LogRuntimeMetrics()
{
    int32 StateCount =
        TransformStates.Num();

    int32 CacheCount =
        ActorCache.Num();

    int32 QueueSize =
        PacketQueue.Size();

    int32 Connected =
        ConnectionSocket &&
        ConnectionSocket->
            GetConnectionState()
            == SCS_Connected
        ? 1 : 0;

    int32 PacketsRecv =
        Stats.PacketsReceived.load(
            std::memory_order_relaxed);

    int32 PacketsProc =
        Stats.PacketsProcessed.load(
            std::memory_order_relaxed);

    int32 PacketsDrop =
        Stats.PacketsDropped.load(
            std::memory_order_relaxed);

    int32 Malformed =
        Stats.MalformedPackets.load(
            std::memory_order_relaxed);

    int64 BytesRecv =
        Stats.TotalBytesReceived.load(
            std::memory_order_relaxed);

    // Compute rates since last sample
    double Now =
        FPlatformTime::Seconds();

    double Elapsed =
        Now - LastRateSampleTime;

    if (LastRateSampleTime > 0.0 &&
        Elapsed > 0.001)
    {
        Stats.AvgPacketsPerSecond =
            (double)(PacketsRecv -
                LastRateSamplePackets) /
            Elapsed;

        Stats.AvgBytesPerSecond =
            (double)(BytesRecv -
                LastRateSampleBytes) /
            Elapsed;
    }

    LastRateSampleTime = Now;
    LastRateSamplePackets = PacketsRecv;
    LastRateSampleBytes = BytesRecv;

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("[Metrics] States=%d Cache=%d Queue=%d "
             "Connected=%d Recv=%d Proc=%d Drop=%d "
             "Malformed=%d Bytes=%lld "
             "Pkt/s=%.1f B/s=%.1f Process=%.2fms"),
        StateCount,
        CacheCount,
        QueueSize,
        Connected,
        PacketsRecv,
        PacketsProc,
        PacketsDrop,
        Malformed,
        BytesRecv,
        Stats.AvgPacketsPerSecond,
        Stats.AvgBytesPerSecond,
        Stats.AvgProcessTimeMs);
}


// =========================================================
// WATCHDOG BACKOFF
// =========================================================

double UUELiveSyncSubsystem::
GetWatchdogBackoff() const
{
    int32 Index =
        FMath::Clamp(
            WatchdogRestartCount,
            0,
            4);

    return
        WatchdogBackoffDelays[Index];
}


// =========================================================
// CONSOLE: STATS
// =========================================================

void UUELiveSyncSubsystem::
ConsoleStats()
{
    int32 PacketsRecv =
        Stats.PacketsReceived.load(
            std::memory_order_relaxed);

    int32 PacketsProc =
        Stats.PacketsProcessed.load(
            std::memory_order_relaxed);

    int32 PacketsDrop =
        Stats.PacketsDropped.load(
            std::memory_order_relaxed);

    int32 Malformed =
        Stats.MalformedPackets.load(
            std::memory_order_relaxed);

    int64 BytesRecv =
        Stats.TotalBytesReceived.load(
            std::memory_order_relaxed);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("=== UE LiveSync Stats ==="));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  [Pipeline]"));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  PacketsReceived:     %d"),
        PacketsRecv);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  PacketsProcessed:    %d"),
        PacketsProc);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  PacketsDropped:      %d"),
        PacketsDrop);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  MalformedPackets:    %d"),
        Malformed);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  BytesReceived:       %lld"),
        BytesRecv);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  [Queue]"));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  QueueDepthCurrent:   %d"),
        Stats.QueueDepthCurrent);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  QueueDepthPeak:      %d"),
        Stats.QueueDepthPeak);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  [Performance]"));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  AvgPacketsPerSecond: %.1f"),
        Stats.AvgPacketsPerSecond);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  AvgBytesPerSecond:   %.1f"),
        Stats.AvgBytesPerSecond);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  AvgProcessTimeMs:    %.2f"),
        Stats.AvgProcessTimeMs);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  [Watchdog]"));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  ReconnectCount:      %d"),
        Stats.ReconnectCount.load(
            std::memory_order_relaxed));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  WatchdogRestartCount: %d"),
        WatchdogRestartCount);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("=== End Stats ==="));
}


// =========================================================
// CONSOLE: DUMP STATE
// =========================================================

void UUELiveSyncSubsystem::
ConsoleDumpState()
{
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("=== UE LiveSync State Dump ==="));

    // =====================================================
    // CONNECTION
    // =====================================================

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  [Connection]"));

    int32 Connected =
        ConnectionSocket &&
        ConnectionSocket->
            GetConnectionState()
            == SCS_Connected
        ? 1 : 0;

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  Connected:           %d"),
        Connected);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  HasListener:         %d"),
        ListenerSocket ? 1 : 0);

    double ThreadLoopTime =
        NetworkRunnable
            ? NetworkRunnable->
                LastThreadLoopTime.load(
                    std::memory_order_relaxed)
            : 0.0;

    double PacketRecvTime =
        NetworkRunnable
            ? NetworkRunnable->
                LastPacketReceiveTime.load(
                    std::memory_order_relaxed)
            : 0.0;

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  LastThreadLoop:      %.2f"),
        ThreadLoopTime);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  LastPacketRecv:      %.2f"),
        PacketRecvTime);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  LastHeartbeatTime:   %.2f"),
        LastHeartbeatTime);

    // =====================================================
    // STATE
    // =====================================================

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  [State]"));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  TransformStates:     %d"),
        TransformStates.Num());

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  ActorCache:          %d"),
        ActorCache.Num());

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  PacketQueue:         %d"),
        PacketQueue.Size());

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  SeqId:               %llu"),
        LastSequenceId);

    // =====================================================
    // WATCHDOG
    // =====================================================

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  [Watchdog]"));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  RestartCount:        %d"),
        WatchdogRestartCount);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  LastRestartTime:     %.2f"),
        LastWatchdogRestartTime);

    // =====================================================
    // VERBOSE: PER-GUID
    // =====================================================

    if (bEnableVerboseSyncLogs)
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("  [Objects]"));

        for (const auto& Pair :
            TransformStates)
        {
            AActor* Actor =
                FindActorFast(
                    Pair.Key);

            UE_LOG(
                LogLiveSync,
                Log,
                TEXT("    GUID=%s Actor=%s"),
                *Pair.Key.ToString(
                    EGuidFormats::Digits),
                Actor
                    ? *Actor->GetName()
                    : TEXT("nullptr"));
        }
    }

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("=== End Dump ==="));
}


// =========================================================
// CONSOLE: RESET
// =========================================================

void UUELiveSyncSubsystem::
ConsoleReset()
{
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("ConsoleReset: tearing down and restarting"));

    StopNetworkThread();

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

    ActorCache.Empty();
    TransformStates.Empty();
    PendingAttachments.Empty();
    MissingActorTracker.Empty();

    bInSnapshotBuild = false;
    SnapshotStartTime = 0.0;

    WatchdogRestartCount = 0;
    LastWatchdogRestartTime = 0.0;

    StartServer();
    BuildActorCache();

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("ConsoleReset: complete"));
}


// =========================================================
// CONSOLE: PING
// =========================================================

void UUELiveSyncSubsystem::
ConsolePing()
{
    bool bIsConnected =
        ConnectionSocket &&
        ConnectionSocket->
            GetConnectionState()
            == SCS_Connected;

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("Ping: connected=%d queue=%d states=%d"),
        bIsConnected ? 1 : 0,
        PacketQueue.Size(),
        TransformStates.Num());
}


#if WITH_EDITOR

#define LOCTEXT_NAMESPACE "UELiveSyncSubsystem"

// =========================================================
// EDITOR STATE ACCESSORS
// =========================================================

FText UUELiveSyncSubsystem::
GetConnectionStatusText() const
{
    bool bConnected =
        ConnectionSocket &&
        ConnectionSocket->
            GetConnectionState()
            == SCS_Connected;

    if (bConnected)
    {
        return
            FText::FromString(
                TEXT("Connected"));
    }

    return
        FText::FromString(
            TEXT("Disconnected"));
}


FText UUELiveSyncSubsystem::
GetUptimeText() const
{
    if (LastHeartbeatTime <= 0.0)
    {
        return
            FText::FromString(
                TEXT("\u2014"));
    }

    double UptimeSeconds =
        FPlatformTime::Seconds() -
        LastHeartbeatTime;

    int32 Minutes =
        (int32)UptimeSeconds / 60;

    int32 Seconds =
        (int32)UptimeSeconds % 60;

    return
        FText::Format(
            LOCTEXT(
                "UptimeFormat",
                "{0}m{1:02d}s"),
            Minutes,
            Seconds);
}


FText UUELiveSyncSubsystem::
GetObjectsTrackedText() const
{
    int32 Count =
        TransformStates.Num();

    return
        FText::AsNumber(Count);
}


FText UUELiveSyncSubsystem::
GetQueueDepthText() const
{
    int32 Depth =
        PacketQueue.Size();

    return
        FText::AsNumber(Depth);
}


FText UUELiveSyncSubsystem::
GetLastPacketTimeText() const
{
    double RecvTime =
        NetworkRunnable
            ? NetworkRunnable->
                LastPacketReceiveTime.load(
                    std::memory_order_relaxed)
            : 0.0;

    if (RecvTime <= 0.0)
    {
        return
            FText::FromString(
                TEXT("\u2014"));
    }

    double SecondsAgo =
        FPlatformTime::Seconds() -
        RecvTime;

    if (SecondsAgo < 1.0)
    {
        return
            FText::FromString(
                TEXT("now"));
    }

    return
        FText::Format(
            LOCTEXT(
                "LastPacketFormat",
                "{0}s ago"),
            FText::AsNumber(
                (int32)SecondsAgo));
}

#undef LOCTEXT_NAMESPACE

#endif