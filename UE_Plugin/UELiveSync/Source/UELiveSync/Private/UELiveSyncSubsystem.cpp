#include "UELiveSyncSubsystem.h"

DEFINE_LOG_CATEGORY(LogLiveSync);

#include "Engine/Engine.h"
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

#include "HAL/PlatformProcess.h"

#include "HAL/PlatformTLS.h"

#include "ProfilingDebugging/Trace.h"


// =========================================================
// THREAD IDENTITY MACROS
// =========================================================

#define CHECK_GAME_THREAD() \
    check(IsInGameThread())

#define CHECK_NONGAME_THREAD() \
    check(!IsInGameThread())


// =========================================================
// STATIC HELPERS
// =========================================================

static UStaticMesh*
GetPrimitiveMesh(uint8 PrimitiveType)
{
    switch (PrimitiveType)
    {
    case LSP_Sphere:
        return LoadObject<UStaticMesh>(
            nullptr,
            TEXT(
            "/Engine/BasicShapes/"
            "Sphere.Sphere"));

    case LSP_Cylinder:
        return LoadObject<UStaticMesh>(
            nullptr,
            TEXT(
            "/Engine/BasicShapes/"
            "Cylinder.Cylinder"));

    case LSP_Plane:
        return LoadObject<UStaticMesh>(
            nullptr,
            TEXT(
            "/Engine/BasicShapes/"
            "Plane.Plane"));

    case LSP_Cube:
    default:
        return LoadObject<UStaticMesh>(
            nullptr,
            TEXT(
            "/Engine/BasicShapes/"
            "Cube.Cube"));
    }
}


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
    CVarLiveSyncVerboseSyncLogs(
        TEXT("UE.LiveSync.VerboseSyncLogs"),
        0,
        TEXT("Enable verbose sync log messages (1=on, 0=off). "
             "Overrides UE.LiveSync.Verbose for log-message granularity."),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncDebugDraw(
        TEXT("UE.LiveSync.DebugDraw"),
        0,
        TEXT("Enable debug visualization overlay (1=on, 0=off)"),
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

// =====================================================
// SUBSYSTEM ISOLATION CVARS
// Temporarily disable subsystems to narrow freeze root cause.
// Set to 1 to skip the corresponding subsystem entirely.
// =====================================================

static TAutoConsoleVariable<int32>
    CVarLiveSyncDisableSpawning(
        TEXT("UE.LiveSync.DisableSpawning"),
        0,
        TEXT("Skip HandleCreateObject actor spawn (1=on, 0=off). "
             "Set to 1 to test if actor creation causes freeze."),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncDisableTransformApply(
        TEXT("UE.LiveSync.DisableTransformApply"),
        0,
        TEXT("Skip SetActorTransform in InterpolateTransforms (1=on, 0=off). "
             "Set to 1 to test if transform application causes freeze."),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncDisableAttachment(
        TEXT("UE.LiveSync.DisableAttachment"),
        0,
        TEXT("Skip AttachToActor calls (1=on, 0=off). "
             "Set to 1 to test if attachment logic causes freeze."),
        ECVF_Default
    );

bool UUELiveSyncSubsystem::
    bEnableVerboseSyncLogs =
        false;

bool GEnableVerboseSyncLogs =
    false;

bool UUELiveSyncSubsystem::
    bEnableDebugDraw =
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
    // Remove ticker first to prevent any re-entrant
    // Tick() call during teardown
    FTSTicker::GetCoreTicker().
        RemoveTicker(
            TickHandle);

    // Shutdown network thread (closes connection socket)
    StopNetworkThread();

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

    // Shutdown listener socket
    if (ListenerSocket)
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("Deinitialize: closing listener socket"));

        // Shutdown before Close to unblock any pending
        // accept() or poll() on the listener
        ListenerSocket->Shutdown(
            ESocketShutdownMode::ReadWrite);

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

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("StartServer: creating TCP listener on %s:%d"),
        *Address.ToString(),
        Port);

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
            TEXT("StartServer: FAILED to create listener on "
                 "%s:%d — port may be in use, thread may "
                 "have failed, or socket limit reached"),
            *Address.ToString(),
            Port);

        return;
    }

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("StartServer: listening on %s:%d (backlog=8, "
             "reuse=true)"),
        *Address.ToString(),
        Port);
}


// =========================================================
// MAIN TICK
// =========================================================

bool UUELiveSyncSubsystem::Tick(
    float DeltaTime)
{
    CHECK_GAME_THREAD();
    VerboseFrameCounter++;

    // =====================================================
    // SYNC CVARS
    // =====================================================

    bEnableVerboseSyncLogs =
        CVarLiveSyncVerbose.GetValueOnGameThread() != 0;

    GEnableVerboseSyncLogs =
        bEnableVerboseSyncLogs;

    bEnableDebugDraw =
        CVarLiveSyncDebugDraw.GetValueOnGameThread() != 0;

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

        // Log periodic "waiting" status every ~600 ticks (10s)
        {
            static int32 AcceptPollCounter = 0;

            if (++AcceptPollCounter % 600 == 1)
            {
                UE_LOG(
                    LogLiveSync,
                    Log,
                    TEXT("Accept: waiting for connection on "
                         "port %d"),
                    CVarLiveSyncPort.GetValueOnGameThread());
            }
        }

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
                    // Log remote address
                    TSharedRef<
                        FInternetAddr>
                    RemoteAddr =
                        ISocketSubsystem::
                            Get(
                                PLATFORM_SOCKETSUBSYSTEM)
                            ->CreateInternetAddr();

                    bool bGotPeerAddr =
                        NewSocket->
                            GetPeerAddress(
                                *RemoteAddr);

                    if (bGotPeerAddr)
                    {
                        UE_LOG(
                            LogLiveSync,
                            Log,
                            TEXT("Accept: Blender connected "
                                 "from %s:%d"),
                            *RemoteAddr->
                                ToString(false),
                            RemoteAddr->
                                GetPort());
                    }
                    else
                    {
                        UE_LOG(
                            LogLiveSync,
                            Log,
                            TEXT("Accept: Blender connected "
                                 "(unknown remote address)"));
                    }

                    ConnectionSocket =
                        NewSocket;

                    ConnectionSocket->
                        SetNoDelay(true);

                    WatchdogRestartCount = 0;
                    LastWatchdogRestartTime = 0.0;

                    BuildActorCache();

                    StartNetworkThread();
                }
                else
                {
                    UE_LOG(
                        LogLiveSync,
                        Warning,
                        TEXT("Accept: connection rejected "
                             "(state=%d)"),
                        static_cast<int32>(
                            NewSocket->
                                GetConnectionState()));

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

                FReconnectEvent Evt;
                Evt.Timestamp =
                    FPlatformTime::Seconds();
                Evt.AttemptNumber =
                    WatchdogRestartCount;
                ReconnectHistory.Insert(Evt, 0);
                if (ReconnectHistory.Num() >
                    MAX_RECONNECT_HISTORY)
                {
                    ReconnectHistory.SetNum(
                        MAX_RECONNECT_HISTORY);
                }

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
    //
    // CRITICAL:
    // The network thread ONLY enqueues packets.
    // ALL runtime processing occurs in the Tick pipeline below.
    // Removing or bypassing these stages will stall the entire sync system.
    // Do not reorder/remove without updating runtime lifecycle assumptions.
    //
    // Pipeline stages:
    //   1. ProcessQueuedPackets   — parse binary packets → update TransformStates
    //   2. EvictStaleTransformStates — TTL-based cleanup of unused state
    //   3. InterpolateTransforms  — drive actor transforms toward targets
    //   4. ResolvePendingAttachments — deferred parent-child attachment retry
    //   5. RecoverMissingActors   — re-spawn actors lost to desync
    //   6. ResolvePendingAssets   — late-binding asset mesh resolution
    // =====================================================

    {
        TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_TickPipeline);

        {
            TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessQueuedPackets);
            ProcessQueuedPackets();
        }

        EvictStaleTransformStates();

        {
            TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_InterpolateTransforms);
            InterpolateTransforms(DeltaTime);
        }

        {
            TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ResolvePendingAttachments);
            ResolvePendingAttachments();
        }

        {
            TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_RecoverMissingActors);
            RecoverMissingActors();
        }

        {
            TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ResolvePendingAssets);
            ResolvePendingAssets();
        }
    }

    // =====================================================
    // HIERARCHY SAFETY VALIDATION
    // =====================================================
    // Periodic check for self-parenting, circular chains, invalid GUIDs.
    // Runs every ~300 ticks (~5s at 60fps) when connected.
    // =====================================================

    if (ConnectionSocket && VerboseFrameCounter % 300 == 0)
    {
        {
            TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ValidateHierarchy);
            ValidateHierarchy();
        }
    }

    // =====================================================
    // ROLLING METRICS (EMA, every tick)
    // =====================================================

    TickMetrics(DeltaTime);

    // =====================================================
    // SAFETY MONITORS (flood detection, queue pressure)
    // =====================================================

    TickSafetyMonitors(DeltaTime);

    // =====================================================
    // RUNTIME METRICS (every 30s in verbose mode)
    // =====================================================

    if (bEnableVerboseSyncLogs)
    {
        double Now =
            FPlatformTime::Seconds();

        if (Now - Stats.LastMetricsLogTime >=
            30.0)
        {
            Stats.LastMetricsLogTime =
                Now;

            LogRuntimeMetricsVerbose();
        }
    }

    // =====================================================
    // DEBUG DRAW OVERLAY (editor only, off by default)
    // =====================================================

    if (bEnableDebugDraw)
    {
#if WITH_EDITOR
        DrawDebugOverlay();
#endif
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

        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("Protocol: sig=0x%08X "
                 "magic=0x%08X LE "
                 "header=%d(V3)/%d(V2) "
                 "obj=%d(V3)/%d(V4+)/%d(del)/%d(asset) "
                 "max_packet=%d"),
            LIVE_SYNC_PROTOCOL_SIG,
            LIVE_SYNC_MAGIC,
            int32(sizeof(FPacketHeaderV3)),
            int32(sizeof(FPacketHeader)),
            LIVE_SYNC_V3_OBJECT_SIZE,
            LIVE_SYNC_V4_OBJECT_SIZE,
            LIVE_SYNC_V3_DELETE_SIZE,
            LIVE_SYNC_V5_ASSET_DEF_SIZE,
            LIVE_SYNC_MAX_PACKET_SIZE);
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

    // CRITICAL: Shutdown BEFORE Close.
    //
    // On Linux, close() does NOT wake a blocked recv()/poll()
    // in another thread — the kernel keeps the socket alive
    // until all fd references are dropped.
    //
    // shutdown(SHUT_RDWR) sends TCP FIN/RST which unblocks
    // any blocked Wait() or Recv() with an error or EOF,
    // allowing the network thread to exit immediately.
    //
    // Without this, WaitForCompletion() below will DEADLOCK
    // the game thread.
    if (ConnectionSocket)
    {
        ConnectionSocket->Shutdown(
            ESocketShutdownMode::ReadWrite);
    }

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
    CHECK_GAME_THREAD();
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessQueuedPackets);
    FLiveSyncPacket Packet;

    TArray<FLiveSyncPacket>
        PacketsThisTick;

    int32 DequeueCount = 0;

    int32 MaxRate =
        CVarLiveSyncMaxPacketRate.
            GetValueOnGameThread();

    uint64 ProcessStartCycles =
        FPlatformTime::Cycles64();

    // Detect new drops since last tick → record overflow event
    int32 CurrentDrops =
        Stats.PacketsDropped.load(
            std::memory_order_relaxed);

    if (CurrentDrops > LastReportedDrops)
    {
        FOverflowEvent Evt;
        Evt.Timestamp =
            FPlatformTime::Seconds();
        Evt.QueueDepth =
            Stats.QueueDepthCurrent;
        OverflowHistory.Insert(Evt, 0);
        if (OverflowHistory.Num() >
            MAX_OVERFLOW_HISTORY)
        {
            OverflowHistory.SetNum(
                MAX_OVERFLOW_HISTORY);
        }

        LastReportedDrops = CurrentDrops;
    }

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

    // Per-packet instrumentation counter
    static uint64 PacketProcessCounter = 0;

    for (const FLiveSyncPacket&
        Pkt : PacketsThisTick)
    {
        PacketProcessCounter++;

        // Inline-read header fields for diagnostics
        int32 PktSize =
            Pkt.RawData.Num();

        uint32 PktMagic = 0;
        uint16 PktVersion = 0;
        uint8 PktType = 0;
        int32 PktObjCount = 0;

        if (PktSize >= 8)
        {
            FMemory::Memcpy(
                &PktMagic,
                Pkt.RawData.GetData(),
                sizeof(uint32));
            FMemory::Memcpy(
                &PktVersion,
                Pkt.RawData.GetData() + 4,
                sizeof(uint16));
        }

        if (PktSize >= 24)
        {
            PktType = *(
                Pkt.RawData.GetData() + 6);

            FMemory::Memcpy(
                &PktObjCount,
                Pkt.RawData.GetData() + 20,
                sizeof(int32));
        }

        uint64 PktBeginCycles =
            FPlatformTime::Cycles64();

        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("BEGIN packet #%llu: magic=0x%08X "
                 "ver=%u type=0x%02X objs=%d size=%d"),
            PacketProcessCounter,
            PktMagic,
            PktVersion,
            PktType,
            PktObjCount,
            PktSize);

        ProcessBinaryPacket(
            Pkt,
            &SeenThisTick);

        double PktElapsedMs =
            FPlatformTime::
            ToMilliseconds64(
                FPlatformTime::Cycles64() -
                PktBeginCycles);

        if (PktElapsedMs > 100.0)
        {
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("STALL: ProcessBinaryPacket took "
                     "%.1fms for packet #%llu"),
                PktElapsedMs,
                PacketProcessCounter);
        }
        else
        {
            UE_LOG(
                LogLiveSync,
                Log,
                TEXT("END packet #%llu: %.1fms"),
                PacketProcessCounter,
                PktElapsedMs);
        }
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
    CHECK_GAME_THREAD();
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessBinaryPacket);
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
            { 0x01, 0x03, 0x04, 0x07, 0x08, 0x09, 0x0A };

        static constexpr uint8 kValidFlags[] =
            { 0x00, 0x01, 0x02, 0x03 };

        bool bValidType = false;

        for (int32 i = 0; i < 7; i++)
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
    // PT_AssetDef (V5) — batch-handle all objects
    //
    // LAYOUT (33 bytes per object):
    //   0-15  GUID (4×uint32 LE)
    //   16-23 Identity Low  (uint64 LE, xxHash64 low)
    //   24-31 Identity High (uint64 LE, xxHash64 high)
    //   32    PrimitiveFallback (uint8)
    //
    // This is a SEPARATE wire format from V3+ transform objects.
    // The 1-byte primitive at offset 32 is part of this 33-byte
    // structure, NOT the V4+ extra byte. Do NOT mix these.
    // =====================================================

    if (PacketType == PT_AssetDef)
    {
        Stats.AssetDefsReceived.fetch_add(
            ObjectCount,
            std::memory_order_relaxed);

        for (uint32 i = 0;
             i < ObjectCount;
             i++)
        {
            if (Ptr + LIVE_SYNC_V5_ASSET_DEF_SIZE
                > PacketEnd)
            {
                return;
            }

            FGuid Guid;
            FMemory::Memcpy(
                &Guid,
                Ptr,
                sizeof(FGuid));
            Ptr += sizeof(FGuid);

            uint64 IdentityHigh;
            uint64 IdentityLow;
            FMemory::Memcpy(
                &IdentityLow,
                Ptr,
                sizeof(uint64));
            Ptr += sizeof(uint64);
            FMemory::Memcpy(
                &IdentityHigh,
                Ptr,
                sizeof(uint64));
            Ptr += sizeof(uint64);

            uint8 PrimitiveFallback;
            FMemory::Memcpy(
                &PrimitiveFallback,
                Ptr,
                sizeof(uint8));
            Ptr += sizeof(uint8);

            HandleAssetDef(
                Guid,
                IdentityHigh,
                IdentityLow,
                PrimitiveFallback);
        }

        Stats.PacketsProcessed.fetch_add(
            1,
            std::memory_order_relaxed);
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
    // OBJECT LOOP (with freeze guard)
    // =====================================================

    const uint8* LoopStartPtr = Ptr;

    uint64 LoopEntryTime =
        FPlatformTime::Cycles64();

    for (uint32 i = 0;
         i < ObjectCount;
         i++)
    {
        // =================================================
        // FREEZE GUARD: detect non-advancing pointer
        // =================================================

        if (i > 0 && Ptr <= LoopStartPtr)
        {
            UE_LOG(
                LogLiveSync,
                Error,
                TEXT("FREEZE GUARD: non-advancing pointer "
                     "at obj=%u/%u — Ptr=%p LoopStart=%p "
                     "type=0x%02X — aborting packet"),
                i,
                ObjectCount,
                (void*)Ptr,
                (void*)LoopStartPtr,
                PacketType);

            return;
        }

        LoopStartPtr = Ptr;

        // =================================================
        // STALL WATCHDOG: abort if loop runs > 5s
        // =================================================

        if (i % 100 == 0)
        {
            uint64 ElapsedCycles =
                FPlatformTime::Cycles64() -
                LoopEntryTime;

            double ElapsedMs =
                FPlatformTime::
                ToMilliseconds64(
                    ElapsedCycles);

            if (ElapsedMs > 5000.0)
            {
                UE_LOG(
                    LogLiveSync,
                    Error,
                    TEXT("FREEZE GUARD: object loop "
                         "exceeded 5s at obj=%u/%u "
                         "type=0x%02X — aborting packet"),
                    i,
                    ObjectCount,
                    PacketType);

                // Stack trace capture for stall root cause analysis
                ensureMsgf(false,
                    TEXT("STALL: object loop froze for %.0fms "
                         "at obj=%u/%u type=0x%02X"),
                    ElapsedMs,
                    i,
                    ObjectCount,
                    PacketType);

                return;
            }
        }

        int32 Remaining =
            static_cast<int32>(
                PacketEnd - Ptr);

        // =====================================================
        // WARNING: V4+ object layout is 81 bytes (80 V3 + 1 prim).
        // Blender always includes the primitive type byte.
        // Changing field order breaks wire compatibility.
        // Blender and UE layouts MUST remain byte-identical.
        // =====================================================

        if (Ptr + 16 > PacketEnd)
        {
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("Parse failure at obj=%u/%.0f%% "
                     "offset=%d remaining=%d "
                     "type=0x%02X ver=%u — "
                     "cannot read GUID (need 16 bytes)"),
                i,
                ObjectCount > 0
                    ? 100.0 * i / ObjectCount
                    : 0.0,
                static_cast<int32>(
                    Ptr - PacketData),
                Remaining,
                PacketType,
                Version);
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
                UE_LOG(
                    LogLiveSync,
                    Warning,
                    TEXT("Parse failure at obj=%u "
                         "offset=%d — invalid V2 GUID hex"),
                    i,
                    static_cast<int32>(
                        Ptr - PacketData));
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

        // =====================================================
        // DEDUP: skip if already processed this tick
        // =====================================================
        // IMPORTANT: skip distance must match V4+ object size
        // including the primitive type byte (81 bytes total).
        // =====================================================

        if (SeenThisTick &&
            SeenThisTick->Contains(Guid))
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

                // V4+: primitive type byte for ALL packet types
                if (Version >=
                    LIVE_SYNC_VERSION_V4)
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
            SeenThisTick->Add(Guid);
        }

        // =================================================
        // LOCATION (12 bytes)
        // =================================================

        Remaining = static_cast<int32>(
            PacketEnd - Ptr);

        if (Ptr + sizeof(FVector3f) >
            PacketEnd)
        {
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("Parse failure at obj=%u "
                     "offset=%d remaining=%d "
                     "type=0x%02X — "
                     "cannot read Location"),
                i,
                static_cast<int32>(
                    Ptr - PacketData),
                Remaining,
                PacketType);
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
        // ROTATION (16 bytes)
        // =================================================

        Remaining = static_cast<int32>(
            PacketEnd - Ptr);

        if (Ptr + sizeof(FQuat4f) >
            PacketEnd)
        {
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("Parse failure at obj=%u "
                     "offset=%d remaining=%d "
                     "type=0x%02X — "
                     "cannot read Rotation"),
                i,
                static_cast<int32>(
                    Ptr - PacketData),
                Remaining,
                PacketType);
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
        // SCALE (12 bytes)
        // =================================================

        Remaining = static_cast<int32>(
            PacketEnd - Ptr);

        if (Ptr + sizeof(FVector3f) >
            PacketEnd)
        {
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("Parse failure at obj=%u "
                     "offset=%d remaining=%d "
                     "type=0x%02X — "
                     "cannot read Scale"),
                i,
                static_cast<int32>(
                    Ptr - PacketData),
                Remaining,
                PacketType);
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
        // V3: Timestamp (8 bytes) + Parent GUID (16 bytes)
        // =================================================

        FGuid ParentGuid;

        if (Version >=
            LIVE_SYNC_VERSION_V3)
        {
            Remaining = static_cast<int32>(
                PacketEnd - Ptr);

            if (Ptr + sizeof(double) >
                PacketEnd)
            {
                UE_LOG(
                    LogLiveSync,
                    Warning,
                    TEXT("Parse failure at obj=%u "
                         "offset=%d remaining=%d "
                         "type=0x%02X — "
                         "cannot read Timestamp"),
                    i,
                    static_cast<int32>(
                        Ptr - PacketData),
                    Remaining,
                    PacketType);
                return;
            }

            Ptr += sizeof(double);

            Remaining = static_cast<int32>(
                PacketEnd - Ptr);

            if (Ptr + 16 >
                PacketEnd)
            {
                UE_LOG(
                    LogLiveSync,
                    Warning,
                    TEXT("Parse failure at obj=%u "
                         "offset=%d remaining=%d "
                         "type=0x%02X — "
                         "cannot read Parent GUID"),
                    i,
                    static_cast<int32>(
                        Ptr - PacketData),
                    Remaining,
                    PacketType);
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

        // V4+: read primitive type byte for all packets.
        // Blender always includes it for V4+ (CREATE, TRANSFORM, etc).
        if (Version >= LIVE_SYNC_VERSION_V4 &&
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
    CHECK_GAME_THREAD();
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_UpdateTargetTransform);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("BEGIN TRACE: UpdateTargetTransform guid=%s"),
        *Guid.ToString(
            EGuidFormats::Digits));

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

        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("END TRACE: UpdateTargetTransform guid=%s (unchanged)"),
            *Guid.ToString(
                EGuidFormats::Digits));

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
    }

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("END TRACE: UpdateTargetTransform guid=%s"),
        *Guid.ToString(
            EGuidFormats::Digits));
}


// =========================================================
// INTERPOLATION
// =========================================================

void UUELiveSyncSubsystem::
InterpolateTransforms(
    float DeltaTime)
{
    CHECK_GAME_THREAD();
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_InterpolateTransforms);
    // Skip interpolation during snapshot build — all transforms
    // will be bulk-applied when EndSnapshot is received
    if (bInSnapshotBuild)
    {
        return;
    }

    // =====================================================
    // ISOLATION: Skip transform application if disabled
    // =====================================================

    if (CVarLiveSyncDisableTransformApply.GetValueOnGameThread())
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

    uint64 InterpLoopEntryCycles =
        FPlatformTime::Cycles64();

    int32 InterpIterationIndex = 0;

    for (auto& Pair :
        TransformStates)
    {
        InterpIterationIndex++;

        // =================================================
        // WATCHDOG: abort if InterpolateTransforms runs > 5s
        // =================================================

        if (InterpIterationIndex % 100 == 0)
        {
            double InterpElapsedMs =
                FPlatformTime::
                ToMilliseconds64(
                    FPlatformTime::Cycles64() -
                    InterpLoopEntryCycles);

            if (InterpElapsedMs > 5000.0)
            {
                UE_LOG(
                    LogLiveSync,
                    Error,
                    TEXT("FREEZE GUARD: InterpolateTransforms"
                         " exceeded 5s at iter=%d — aborting"),
                    InterpIterationIndex);

                ensureMsgf(false,
                    TEXT("STALL: InterpolateTransforms froze"
                         " for %.0fms at iter=%d"),
                    InterpElapsedMs,
                    InterpIterationIndex);

                return;
            }
        }

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
    CHECK_GAME_THREAD();
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
    CHECK_GAME_THREAD();

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("BEGIN TRACE: AttachToParent child=%s parent=%s"),
        *Guid.ToString(
            EGuidFormats::Digits),
        *ParentGuid.ToString(
            EGuidFormats::Digits));

    if (!ParentGuid.IsValid())
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("END TRACE: AttachToParent child=%s (no parent)"),
            *Guid.ToString(
                EGuidFormats::Digits));
        return;
    }
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

    // =====================================================
    // ISOLATION: Skip attachment if DisableAttachment is set
    // =====================================================

    if (CVarLiveSyncDisableAttachment.GetValueOnGameThread())
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("AttachToParent: DISABLED via CVar "
                 "for child=%s parent=%s"),
            *Guid.ToString(
                EGuidFormats::Digits),
            *ParentGuid.ToString(
                EGuidFormats::Digits));
        return;
    }

AActor* Child =
    FindActorFast(Guid);

    if (!Child)
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("END TRACE: AttachToParent child=%s (child not found)"),
            *Guid.ToString(
                EGuidFormats::Digits));
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

        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("END TRACE: AttachToParent child=%s (deferred)"),
            *Guid.ToString(
                EGuidFormats::Digits));
        return;
    }

    // Guard: already attached to correct parent
    if (Child->GetAttachParentActor()
        == Parent)
    {
        // Attached while already attached to same parent = churn
        HierarchyDiag.AttachmentChurnCount++;
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("END TRACE: AttachToParent child=%s (already attached)"),
            *Guid.ToString(
                EGuidFormats::Digits));
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
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("END TRACE: AttachToParent child=%s (cycle detected)"),
            *Guid.ToString(
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
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("END TRACE: AttachToParent child=%s (depth exceeded)"),
            *Guid.ToString(
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

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("END TRACE: AttachToParent child=%s parent=%s"),
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
    CHECK_GAME_THREAD();
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
    CHECK_GAME_THREAD();
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_HandleCreateObject);
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

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("BEGIN TRACE: HandleCreateObject guid=%s"),
        *Guid.ToString(
            EGuidFormats::Digits));

    // =====================================================
    // ISOLATION: Skip spawn if DisableSpawning is set
    // =====================================================

    if (CVarLiveSyncDisableSpawning.GetValueOnGameThread())
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("HandleCreateObject: spawn DISABLED via CVar "
                 "for GUID=%s (location=%s)"),
            *Guid.ToString(
                EGuidFormats::Digits),
            *Location.ToString());
        return;
    }

    FActorSpawnParameters SpawnParams;

    SpawnParams.SpawnCollisionHandlingOverride =
        ESpawnActorCollisionHandlingMethod::
            AlwaysSpawn;

    // =====================================================
    // Step 1: Spawn actor at world position.
    // Location/Rotation/Scale are already world-space
    // (ProcessBinaryPacket computed world for children).
    // =====================================================

    uint64 SpawnBeginCycles =
        FPlatformTime::Cycles64();

    AActor* NewActor =

        World->SpawnActor<AActor>(

            AActor::StaticClass(),

            FTransform(
                Rotation,
                Location,
                Scale),

            SpawnParams);

    double SpawnMs =
        FPlatformTime::
        ToMilliseconds64(
            FPlatformTime::Cycles64() -
            SpawnBeginCycles);

    if (!NewActor)
    {
        UE_LOG(
            LogLiveSync,
            Warning,
            TEXT("HandleCreate: SpawnActor FAILED "
                 "for GUID=%s (spawn took %.1fms)"),
            *Guid.ToString(
                EGuidFormats::Digits),
            SpawnMs);

        return;
    }

    if (SpawnMs > 50.0)
    {
        UE_LOG(
            LogLiveSync,
            Warning,
            TEXT("STALL: SpawnActor took %.1fms "
                 "for GUID=%s"),
            SpawnMs,
            *Guid.ToString(
                EGuidFormats::Digits));
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

        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("END TRACE: HandleCreateObject guid=%s (empty, no mesh)"),
            *Guid.ToString(
                EGuidFormats::Digits));

        return;
    }

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("BEGIN TRACE: HandleCreateObject::RegisterComponent guid=%s"),
        *Guid.ToString(
            EGuidFormats::Digits));

    UStaticMeshComponent* MeshComp =
        NewObject<UStaticMeshComponent>(
            NewActor);

    MeshComp->SetMobility(
        EComponentMobility::Movable);

    MeshComp->SetVisibility(
        true, true);

    UStaticMesh* PrimitiveMesh =
        GetPrimitiveMesh(PrimitiveType);

    if (PrimitiveMesh)
    {
        MeshComp->SetStaticMesh(
            PrimitiveMesh);
    }

    MeshComp->SetCollisionEnabled(
        ECollisionEnabled::NoCollision);

    NewActor->SetRootComponent(
        MeshComp);

    uint64 RegisterBeginCycles =
        FPlatformTime::Cycles64();

    MeshComp->RegisterComponent();

    double RegisterMs =
        FPlatformTime::
        ToMilliseconds64(
            FPlatformTime::Cycles64() -
            RegisterBeginCycles);

    if (RegisterMs > 50.0)
    {
        UE_LOG(
            LogLiveSync,
            Warning,
            TEXT("STALL: RegisterComponent took %.1fms "
                 "for GUID=%s"),
            RegisterMs,
            *Guid.ToString(
                EGuidFormats::Digits));
    }

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("END TRACE: HandleCreateObject::RegisterComponent guid=%s (%.1fms)"),
        *Guid.ToString(
            EGuidFormats::Digits),
        RegisterMs);

    // NOTE: State initialization is handled by the caller
    // (ProcessBinaryPacket or RecoverMissingActors) via
    // an explicit UpdateTargetTransform call with correct
    // (possibly local-space) transform values.
    // This avoids passing world-spawn values as local targets.

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("END TRACE: HandleCreateObject guid=%s"),
        *Guid.ToString(
            EGuidFormats::Digits));
}


// =========================================================
// HANDLE DELETE OBJECT
// =========================================================

void UUELiveSyncSubsystem::
HandleDeleteObject(
    const FGuid& Guid)
{
    CHECK_GAME_THREAD();
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

    // Remove asset metadata and pending resolution
    if (AssetMetadata.Remove(Guid))
    {
        PendingAssetQueue.Remove(Guid);
    }
}


// =========================================================
// HANDLE ASSET DEF (V5)
// =========================================================

void UUELiveSyncSubsystem::
HandleAssetDef(
    const FGuid& Guid,
    uint64 IdentityHigh,
    uint64 IdentityLow,
    uint8 PrimitiveFallback)
{
    CHECK_GAME_THREAD();
    FAssetIdentityRef Identity;
    Identity.High = IdentityHigh;
    Identity.Low  = IdentityLow;

    if (!Identity.IsValid())
    {
        Stats.AssetDefsSkipped.fetch_add(
            1,
            std::memory_order_relaxed);
        return;
    }

    FAssetMetadata& Meta =
        AssetMetadata.FindOrAdd(Guid);

    if (Meta.bResolved &&
        Meta.Identity == Identity)
    {
        return;
    }

    Meta.Identity = Identity;
    Meta.PrimitiveFallback =
        PrimitiveFallback;
    Meta.RetryCount = 0;
    Meta.NextRetryTime = 0.0;
    Meta.RetryInterval =
        ASSET_RETRY_INTERVAL_INITIAL;
    Meta.bResolved = false;
    Meta.bFallbackAssigned = false;

    PendingAssetQueue.Enqueue(Guid);

    if (bEnableVerboseSyncLogs)
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("[AssetDef] GUID=%s Identity=0x%llx%llx "
                 "Fallback=%d"),
            *Guid.ToString(
                EGuidFormats::Digits),
            Identity.High,
            Identity.Low,
            PrimitiveFallback);
    }
}


// =========================================================
// RESOLVE PENDING ASSETS
// =========================================================

void UUELiveSyncSubsystem::
ResolvePendingAssets()
{
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ResolvePendingAssets);
    double Now =
        FPlatformTime::Seconds();
    int32 ResolvedThisTick = 0;

    FGuid Guid;

    while (
        ResolvedThisTick <
            MAX_ASSET_RESOLUTIONS_PER_TICK &&
        PendingAssetQueue.Dequeue(Guid))
    {
        FAssetMetadata* Meta =
            AssetMetadata.Find(Guid);

        if (!Meta)
        {
            continue;
        }

        if (Meta->bResolved)
        {
            continue;
        }

        if (Now < Meta->NextRetryTime)
        {
            PendingAssetQueue.Enqueue(Guid);
            continue;
        }

        FSoftObjectPath*
            CachedPath =
                AssetPathCache.Find(
                    Meta->Identity);

        if (!CachedPath ||
            CachedPath->IsNull())
        {
            Stats.AssetLookupsAttempted.fetch_add(
    1,
    std::memory_order_relaxed);

            Meta->RetryCount++;
            Meta->RetryInterval =
                FMath::Min(
                    Meta->RetryInterval * 2.0,
                    ASSET_RETRY_INTERVAL_MAX);
            Meta->NextRetryTime =
                Now + Meta->RetryInterval;

            if (Meta->RetryCount <
                MAX_ASSET_RETRY_ATTEMPTS)
            {
                PendingAssetQueue.Enqueue(Guid);
            }
            else
            {
                UE_LOG(
                    LogLiveSync,
                    Warning,
                    TEXT("[Asset] Resolution failed "
                         "for GUID=%s after %d retries "
                         "\u2014 using fallback primitive"),
                    *Guid.ToString(
                        EGuidFormats::Digits),
                    MAX_ASSET_RETRY_ATTEMPTS);

                Stats.AssetLookupsFailed.fetch_add(
                    1,
                    std::memory_order_relaxed);
                AssignFallbackPrimitive(
                    Guid,
                    Meta->PrimitiveFallback);
                Meta->bResolved = true;
                Meta->bFallbackAssigned = true;
            }

            continue;
        }

        AssignStaticMesh(Guid, *CachedPath);
        Meta->bResolved = true;
        Stats.AssetAssignmentsSucceeded.fetch_add(
            1,
            std::memory_order_relaxed);
        ResolvedThisTick++;
    }
}


// =========================================================
// ASSIGN STATIC MESH
// =========================================================

void UUELiveSyncSubsystem::
AssignStaticMesh(
    const FGuid& Guid,
    const FSoftObjectPath& Path)
{
    AActor* Actor =
        FindActorFast(Guid);

    if (!Actor)
    {
        if (bEnableVerboseSyncLogs)
        {
            UE_LOG(
                LogLiveSync,
                Verbose,
                TEXT("[Asset] Assign deferred \u2014 "
                     "actor not yet created for GUID=%s"),
                *Guid.ToString(
                    EGuidFormats::Digits));
        }

        PendingAssetQueue.Enqueue(Guid);
        return;
    }

    UStaticMeshComponent* MeshComp =
        Actor->FindComponentByClass<
            UStaticMeshComponent>();

    if (!MeshComp)
    {
        return;
    }

    UStaticMesh* Mesh = Cast<UStaticMesh>(
        Path.TryLoad());

    if (!Mesh)
    {
        return;
    }

    MeshComp->SetStaticMesh(Mesh);

    Stats.AssetAssignmentsSucceeded++;
}


// =========================================================
// ASSIGN FALLBACK PRIMITIVE
// =========================================================

void UUELiveSyncSubsystem::
AssignFallbackPrimitive(
    const FGuid& Guid,
    uint8 PrimitiveType)
{
    AActor* Actor =
        FindActorFast(Guid);

    if (!Actor)
    {
        return;
    }

    UStaticMeshComponent* MeshComp =
        Actor->FindComponentByClass<
            UStaticMeshComponent>();

    if (!MeshComp)
    {
        MeshComp =
            NewObject<
                UStaticMeshComponent>(
                    Actor);

        if (Actor->GetRootComponent())
        {
            MeshComp->SetupAttachment(
                Actor->GetRootComponent());
        }
        else
        {
            Actor->SetRootComponent(
                MeshComp);
        }
    }

    UStaticMesh* Mesh =
        GetPrimitiveMesh(PrimitiveType);

    if (Mesh)
    {
        MeshComp->SetStaticMesh(Mesh);
        MeshComp->SetMobility(
            EComponentMobility::Movable);

        if (!MeshComp->IsRegistered())
        {
            MeshComp->RegisterComponent();
        }
    }
}


// =========================================================
// CACHE ASSET PATH
// =========================================================

void UUELiveSyncSubsystem::
CacheAssetPath(
    const FAssetIdentityRef& Identity,
    const FSoftObjectPath& Path)
{
    if (Identity.IsValid() &&
        !Path.IsNull())
    {
        AssetPathCache.Add(
            Identity,
            Path);
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
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ResolvePendingAttachments);
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
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_RecoverMissingActors);
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
// ROLLING METRICS (EMA-based, called every tick)
// =========================================================

void UUELiveSyncSubsystem::
TickMetrics(float DeltaTime)
{
    if (DeltaTime <= 0.0f)
        return;

    double Now =
        FPlatformTime::Seconds();

    double Elapsed =
        Now - LastRateSampleTime;

    // Compute instantaneous rate every ~1s
    if (LastRateSampleTime > 0.0 &&
        Elapsed >= 1.0)
    {
        int32 PacketsRecv =
            Stats.PacketsReceived.load(
                std::memory_order_relaxed);

        int64 BytesRecv =
            Stats.TotalBytesReceived.load(
                std::memory_order_relaxed);

        double InstantPktPerSec =
            (double)(PacketsRecv -
                LastRateSamplePackets) /
            Elapsed;

        double InstantBytesPerSec =
            (double)(BytesRecv -
                LastRateSampleBytes) /
            Elapsed;

        // EMA smoothing factor (≈ 4-second half-life)
        const double Alpha = 0.15;

        if (Stats.PacketsPerSecondEMA == 0.0)
        {
            Stats.PacketsPerSecondEMA =
                InstantPktPerSec;
        }
        else
        {
            Stats.PacketsPerSecondEMA =
                Alpha * InstantPktPerSec +
                (1.0 - Alpha) *
                    Stats.PacketsPerSecondEMA;
        }

        if (Stats.BytesPerSecondEMA == 0.0)
        {
            Stats.BytesPerSecondEMA =
                InstantBytesPerSec;
        }
        else
        {
            Stats.BytesPerSecondEMA =
                Alpha * InstantBytesPerSec +
                (1.0 - Alpha) *
                    Stats.BytesPerSecondEMA;
        }

        // Track peaks
        if (Stats.PacketsPerSecondEMA >
            Stats.PeakPacketsPerSecond)
        {
            Stats.PeakPacketsPerSecond =
                Stats.PacketsPerSecondEMA;
        }

        if (Stats.BytesPerSecondEMA >
            Stats.PeakBytesPerSecond)
        {
            Stats.PeakBytesPerSecond =
                Stats.BytesPerSecondEMA;
        }

        // Update process time EMA
        double ProcessMs =
            Stats.AvgProcessTimeMs;

        if (Stats.ProcessTimeMsEMA == 0.0)
        {
            Stats.ProcessTimeMsEMA = ProcessMs;
        }
        else
        {
            Stats.ProcessTimeMsEMA =
                Alpha * ProcessMs +
                (1.0 - Alpha) *
                    Stats.ProcessTimeMsEMA;
        }

        if (Stats.AvgProcessTimeMs >
            Stats.PeakProcessTimeMs)
        {
            Stats.PeakProcessTimeMs =
                Stats.AvgProcessTimeMs;
        }

        LastRateSampleTime = Now;
        LastRateSamplePackets = PacketsRecv;
        LastRateSampleBytes = BytesRecv;
    }
}


// =========================================================
// SAFETY MONITORS (flood detection, queue pressure)
// =========================================================

void UUELiveSyncSubsystem::
TickSafetyMonitors(float DeltaTime)
{
    double Now =
        FPlatformTime::Seconds();

    // --- Flood detection ---
    if (FloodWindowStart == 0.0)
    {
        FloodWindowStart = Now;
    }

    double WindowElapsed =
        Now - FloodWindowStart;

    if (WindowElapsed >=
        FloodDetectionWindow)
    {
        int32 PacketsInWindow =
            Stats.PacketsReceived.load(
                std::memory_order_relaxed) -
            FloodPacketCount;

        double RatePerSec =
            (double)PacketsInWindow /
            WindowElapsed;

        if (RatePerSec >
            FloodThresholdPacketsPerSec)
        {
            Stats.FloodWarnings++;

            Stats.LastFloodWarningTime =
                Now;

            if (ShouldLogVerbose())
            {
                UE_LOG(
                    LogLiveSync,
                    Warning,
                    TEXT("[Safety] Flood detected: "
                         "%.0f pkt/s (threshold %d)"),
                    RatePerSec,
                    (int32)
                        FloodThresholdPacketsPerSec);
            }
        }

        FloodPacketCount =
            Stats.PacketsReceived.load(
                std::memory_order_relaxed);

        FloodWindowStart = Now;
    }

    // --- Queue pressure ---
    if (Stats.QueueDepthCurrent >= 0)
    {
        QueuePressureAccumulator +=
            (double)Stats.QueueDepthCurrent *
            DeltaTime;

        double AvgDepth =
            QueuePressureAccumulator /
            (Now - FloodWindowStart +
             0.001);

        if (AvgDepth >
            QueuePressureThreshold)
        {
            Stats.QueuePressureWarnings++;

            Stats.LastQueuePressureTime =
                Now;

            if (ShouldLogVerbose())
            {
                UE_LOG(
                    LogLiveSync,
                    Warning,
                    TEXT("[Safety] Queue pressure: "
                         "avg depth %.0f/%d"),
                    AvgDepth,
                    (int32)
                        QueuePressureThreshold);
            }
        }
    }

    // --- Packet age watchdog ---
    // Warn if packets are sitting in the queue too long
    // (indicates Tick pipeline is not keeping up).
    {
        int32 QueueDepth =
            PacketQueue.Size();

        if (QueueDepth > 0)
        {
            double AgeEstimate =
                (double)QueueDepth *
                (Stats.AvgProcessTimeMs / 1000.0 + 0.016);

            if (AgeEstimate > PacketAgeWarnThreshold &&
                Now - LastPacketAgeWarnTime > 10.0)
            {
                LastPacketAgeWarnTime = Now;

                UE_LOG(
                    LogLiveSync,
                    Warning,
                    TEXT("[Safety] Packet age watchdog: "
                         "%d queued, estimated oldest age %.1fs "
                         "(threshold %.1fs)"),
                    QueueDepth,
                    AgeEstimate,
                    PacketAgeWarnThreshold);
            }

            if (AgeEstimate > PacketAgeHardLimit)
            {
                UE_LOG(
                    LogLiveSync,
                    Error,
                    TEXT("[Safety] Packet age HARD LIMIT: "
                         "%d queued, estimated oldest age %.1fs "
                         "(limit %.1fs) — flushing queue"),
                    QueueDepth,
                    AgeEstimate,
                    PacketAgeHardLimit);

                PacketQueue.Clear();

                Stats.PacketsDropped.fetch_add(
                    QueueDepth,
                    std::memory_order_relaxed);
            }
        }
    }

    // --- Queue depth spike warning ---
    {
        int32 QueueDepth =
            PacketQueue.Size();

        if (QueueDepth >=
            FLiveSyncQueue::MaxQueueSize * 0.9)
        {
            static double LastSpikeWarnTime = 0.0;

            if (Now - LastSpikeWarnTime > 5.0)
            {
                LastSpikeWarnTime = Now;

                UE_LOG(
                    LogLiveSync,
                    Warning,
                    TEXT("[Safety] Queue depth spike: %d/%d "
                         "(90%% capacity) — processing may be saturated"),
                    QueueDepth,
                    FLiveSyncQueue::MaxQueueSize);
            }
        }
    }
}


// =========================================================
// SET QUEUE DEPTH PEAK (called from LiveSyncQueue)
// =========================================================

void UUELiveSyncSubsystem::
SetQueueDepthPeak(int32 Depth)
{
    if (Depth >
        Stats.QueueDepthPeak)
    {
        Stats.QueueDepthPeak = Depth;
    }
}


#if WITH_EDITOR
// =========================================================
// DEBUG DRAW OVERLAY
// =========================================================

void UUELiveSyncSubsystem::
DrawDebugOverlay()
{
    UWorld* World =
        GetWorld();

    if (!World)
        return;

    // Build a compact one-line status string
    FString Status;

    bool bConnected =
        ConnectionSocket &&
        ConnectionSocket->
            GetConnectionState()
            == SCS_Connected;

    Status +=
        bConnected
            ? TEXT("Connected")
            : TEXT("Disconnected");

    Status +=
        FString::Printf(
            TEXT(" | Queue:%d/%d"),
            Stats.QueueDepthCurrent,
            Stats.QueueDepthPeak);

    Status +=
        FString::Printf(
            TEXT(" | Pkt/s:%.0f"),
            Stats.PacketsPerSecondEMA);

    Status +=
        FString::Printf(
            TEXT(" | Recv:%d Drp:%d"),
            Stats.PacketsReceived.load(
                std::memory_order_relaxed),
            Stats.PacketsDropped.load(
                std::memory_order_relaxed));

    int32 PendingAssets = 0;
    int32 Unused = 0;
    PendingAssetQueue.GetDiagnostics(
        PendingAssets, Unused);

    if (PendingAssets > 0)
    {
        Status +=
            FString::Printf(
                TEXT(" | Assets:%d"),
                PendingAssets);
    }

    GEngine->AddOnScreenDebugMessage(
        984531,                    // unique key
        0.0f,                      // duration (0=persistent)
        bConnected
            ? FColor::Green
            : FColor::Red,
        TEXT("LiveSync: ") + Status);
}


// =========================================================
// DIAGNOSTICS TEXT (polled by Slate widget)
// =========================================================

FText UUELiveSyncSubsystem::
GetDiagnosticsText()
{
    FString Report;

    bool bConnected =
        ConnectionSocket &&
        ConnectionSocket->
            GetConnectionState()
            == SCS_Connected;

    // Connection
    Report +=
        FString::Printf(
            TEXT("Connection: %s\n"),
            bConnected
                ? TEXT("Connected")
                : TEXT("Disconnected"));

    if (bConnected)
    {
        Report +=
            FString::Printf(
                TEXT("  Uptime: %s\n"),
                *GetUptimeText().ToString());
    }

    // Objects
    Report +=
        FString::Printf(
            TEXT("Objects Tracked: %d\n"),
            TransformStates.Num());

    // Queue
    Report +=
        FString::Printf(
            TEXT("Queue: %d current / %d peak\n"),
            Stats.QueueDepthCurrent,
            Stats.QueueDepthPeak);

    // Pipeline counters
    Report +=
        FString::Printf(
            TEXT("Packets: %d recv / %d proc / %d drop / %d malformed\n"),
            Stats.PacketsReceived.load(
                std::memory_order_relaxed),
            Stats.PacketsProcessed.load(
                std::memory_order_relaxed),
            Stats.PacketsDropped.load(
                std::memory_order_relaxed),
            Stats.MalformedPackets.load(
                std::memory_order_relaxed));

    Report +=
        FString::Printf(
            TEXT("Bytes: %lld recv\n"),
            Stats.TotalBytesReceived.load(
                std::memory_order_relaxed));

    // Performance (EMA)
    Report +=
        FString::Printf(
            TEXT("Performance (EMA):\n"));

    Report +=
        FString::Printf(
            TEXT("  %.0f pkt/s (peak %.0f)\n"),
            Stats.PacketsPerSecondEMA,
            Stats.PeakPacketsPerSecond);

    Report +=
        FString::Printf(
            TEXT("  %.0f B/s (peak %.0f)\n"),
            Stats.BytesPerSecondEMA,
            Stats.PeakBytesPerSecond);

    Report +=
        FString::Printf(
            TEXT("  %.2f ms/pkt (peak %.2f)\n"),
            Stats.ProcessTimeMsEMA,
            Stats.PeakProcessTimeMs);

    // Event history summaries
    Report +=
        FString::Printf(
            TEXT("Events: %d reconnects, %d overflow events\n"),
            Stats.ReconnectCount.load(
                std::memory_order_relaxed),
            OverflowHistory.Num());

    // Safety
    if (Stats.FloodWarnings > 0 ||
        Stats.QueuePressureWarnings > 0)
    {
        Report +=
            FString::Printf(
                TEXT("Safety: %d flood warnings, %d queue pressure\n"),
                Stats.FloodWarnings,
                Stats.QueuePressureWarnings);
    }

    // Asset resolution (Phase 5D)
    int32 PendingAssets = 0;
    int32 UnresolvedAssets = 0;
    PendingAssetQueue.GetDiagnostics(
        PendingAssets,
        UnresolvedAssets);

    Stats.PendingAssetCount = PendingAssets;
    if (PendingAssets > Stats.PendingAssetPeak)
    {
        Stats.PendingAssetPeak = PendingAssets;
    }

    Report +=
        FString::Printf(
            TEXT("Asset:\n"));

    Report +=
        FString::Printf(
            TEXT("  Defs Received: %d (skipped %d)\n"),
            Stats.AssetDefsReceived.load(
                std::memory_order_relaxed),
            Stats.AssetDefsSkipped.load(
                std::memory_order_relaxed));

    Report +=
        FString::Printf(
            TEXT("  Assignments: %d ok / %d fail\n"),
            Stats.AssetAssignmentsSucceeded.load(
                std::memory_order_relaxed),
            Stats.AssetAssignmentsFailed.load(
                std::memory_order_relaxed));

    Report +=
        FString::Printf(
            TEXT("  Lookups: %d attempt / %d fail\n"),
            Stats.AssetLookupsAttempted.load(
                std::memory_order_relaxed),
            Stats.AssetLookupsFailed.load(
                std::memory_order_relaxed));

    Report +=
        FString::Printf(
            TEXT("  Pending: %d resolved (queue: %d)\n"),
            AssetMetadata.Num() - PendingAssets,
            PendingAssets);

    return FText::FromString(Report);
}
#endif


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

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("[Metrics] States=%d Cache=%d Queue=%d "
             "Connected=%d Recv=%d Proc=%d Drop=%d "
             "Malformed=%d Bytes=%lld "
             "Pkt/s=%.0f(EMA) pk=%.0f "
             "B/s=%.0f(EMA) pk=%.0f "
             "Process=%.2fms(EMA) pk=%.2f "
             "FloodW=%d QPress=%d "
             "Reconn=%d Overflows=%d"),
        StateCount,
        CacheCount,
        QueueSize,
        Connected,
        PacketsRecv,
        PacketsProc,
        PacketsDrop,
        Malformed,
        BytesRecv,
        Stats.PacketsPerSecondEMA,
        Stats.PeakPacketsPerSecond,
        Stats.BytesPerSecondEMA,
        Stats.PeakBytesPerSecond,
        Stats.ProcessTimeMsEMA,
        Stats.PeakProcessTimeMs,
        Stats.FloodWarnings,
        Stats.QueuePressureWarnings,
        Stats.ReconnectCount.load(
            std::memory_order_relaxed),
        OverflowHistory.Num());

    int32 PendingAssets = 0;
    int32 Unused = 0;
    PendingAssetQueue.GetDiagnostics(
        PendingAssets, Unused);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("[Metrics-Asset] Defs=%d Skip=%d "
             "Asgn=%dok/%dfail Lkup=%datm/%dfail "
             "Pending=%d Stale=%d"),
        Stats.AssetDefsReceived.load(
            std::memory_order_relaxed),
        Stats.AssetDefsSkipped.load(
            std::memory_order_relaxed),
        Stats.AssetAssignmentsSucceeded.load(
            std::memory_order_relaxed),
        Stats.AssetAssignmentsFailed.load(
            std::memory_order_relaxed),
        Stats.AssetLookupsAttempted.load(
            std::memory_order_relaxed),
        Stats.AssetLookupsFailed.load(
            std::memory_order_relaxed),
        PendingAssets,
        Stats.StaleEvictions);
}


// =========================================================
// RUNTIME METRICS DASHBOARD (compact verbose diagnostics)
// =========================================================
// Logs a one-line LiveSync Stats summary every 30s when verbose is enabled.
// Designed for quick health assessment in the UE Output Log.
// =========================================================

void UUELiveSyncSubsystem::
LogRuntimeMetricsVerbose()
{
    int32 StateCount =
        TransformStates.Num();

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

    int32 Reconnects =
        Stats.ReconnectCount.load(
            std::memory_order_relaxed);

    int32 PendingAssets = 0;
    int32 Unused = 0;
    PendingAssetQueue.GetDiagnostics(
        PendingAssets, Unused);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("=== LiveSync Stats Dashboard ==="));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  Connection: %s | Objects: %d | Queue: %d/%d | Assets: %d"),
        Connected ? TEXT("Connected") : TEXT("Disconnected"),
        StateCount,
        QueueSize,
        FLiveSyncQueue::MaxQueueSize,
        PendingAssets);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  Packets: %d recv / %d proc / %d drop / %d malformed"),
        PacketsRecv,
        PacketsProc,
        PacketsDrop,
        Malformed);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  Rates: %.0f pkt/s (peak %.0f) | %.2f ms/pkt (peak %.2f) | %.0f B/s"),
        Stats.PacketsPerSecondEMA,
        Stats.PeakPacketsPerSecond,
        Stats.ProcessTimeMsEMA,
        Stats.PeakProcessTimeMs,
        Stats.BytesPerSecondEMA);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  Safety: FloodW=%d QPress=%d Reconn=%d Overflows=%d"),
        Stats.FloodWarnings,
        Stats.QueuePressureWarnings,
        Reconnects,
        OverflowHistory.Num());

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("=== End Dashboard ==="));
}


// =========================================================
// HIERARCHY SAFETY VALIDATION
// =========================================================
// Runs periodically to detect:
//   - Self-parenting (child == parent)
//   - Circular attachment chains (parent is descendant of child)
//   - Invalid parent GUID (parent Guid.IsValid() but no actor exists)
//   - Orphaned pending attachments (parent never appeared)
// =========================================================

void UUELiveSyncSubsystem::
ValidateHierarchy()
{
    for (const auto& Pair :
        TransformStates)
    {
        const FGuid& Guid =
            Pair.Key;

        const FSyncTransformState&
            State =
            Pair.Value;

        if (!State.bHasParent)
        {
            continue;
        }

        const FGuid& ParentGuid =
            State.ParentGuid;

        // =====================================================
        // SELF-PARENT CHECK
        // =====================================================

        if (Guid == ParentGuid)
        {
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("[HierarchySafety] Self-parent detected: "
                     "GUID=%s is its own parent — detaching"),
                *Guid.ToString(
                    EGuidFormats::Digits));

            DetachFromParent(Guid);
            continue;
        }

        // =====================================================
        // PARENT VALIDITY CHECK
        // =====================================================

        if (!ParentGuid.IsValid())
        {
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("[HierarchySafety] Invalid parent GUID: "
                     "GUID=%s has invalid parent — detaching"),
                *Guid.ToString(
                    EGuidFormats::Digits));

            DetachFromParent(Guid);
            continue;
        }

        // =====================================================
        // PARENT EXISTS IN CACHE CHECK
        // =====================================================

        AActor* ParentActor =
            FindActorFast(ParentGuid);

        if (!ParentActor)
        {
            // This is normal during deferred attachment —
            // only warn if pending for a long time
            continue;
        }

        AActor* ChildActor =
            FindActorFast(Guid);

        if (!ChildActor)
        {
            continue;
        }

        // =====================================================
        // CIRCULAR CHAIN DETECTION
        // =====================================================
        // Walk up the parent chain and verify we never
        // encounter the child as an ancestor.

        int32 Depth = 0;
        AActor* Probe =
            ParentActor;

        while (Probe && Depth < 128)
        {
            if (Probe == ChildActor)
            {
                FGuid ProbeGuid =
                    FindGuidForActor(Probe);

                UE_LOG(
                    LogLiveSync,
                    Error,
                    TEXT("[HierarchySafety] Circular chain detected: "
                         "GUID=%s -> parent=%s chain contains child"),
                    *Guid.ToString(
                        EGuidFormats::Digits),
                    ProbeGuid.IsValid()
                        ? *ProbeGuid.ToString(
                            EGuidFormats::Digits)
                        : TEXT("unknown"));

                UE_LOG(
                    LogLiveSync,
                    Log,
                    TEXT("[HierarchySafety] Aborting attachment "
                         "for GUID=%s to prevent recursion stall"),
                    *Guid.ToString(
                        EGuidFormats::Digits));

                DetachFromParent(Guid);
                break;
            }

            Probe =
                Probe->
                GetAttachParentActor();
            Depth++;
        }

        if (Depth >= 128)
        {
            UE_LOG(
                LogLiveSync,
                Error,
                TEXT("[HierarchySafety] Excessive hierarchy depth "
                     "(%d levels) for GUID=%s — detaching"),
                Depth,
                *Guid.ToString(
                    EGuidFormats::Digits));

            DetachFromParent(Guid);
        }
    }
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
        TEXT("  [Performance (EMA)]"));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  PacketsPerSecond:    %.0f"),
        Stats.PacketsPerSecondEMA);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  PeakPacketsPerSecond:%.0f"),
        Stats.PeakPacketsPerSecond);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  BytesPerSecond:      %.0f"),
        Stats.BytesPerSecondEMA);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  PeakBytesPerSecond:  %.0f"),
        Stats.PeakBytesPerSecond);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  AvgProcessTimeMs:    %.2f"),
        Stats.ProcessTimeMsEMA);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  PeakProcessTimeMs:   %.2f"),
        Stats.PeakProcessTimeMs);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  [Safety]"));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  FloodWarnings:       %d"),
        Stats.FloodWarnings);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  QueuePressureWarnings:%d"),
        Stats.QueuePressureWarnings);

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
        TEXT("  Event History:"));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    ReconnectEvents:   %d"),
        ReconnectHistory.Num());

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    OverflowEvents:    %d"),
        OverflowHistory.Num());

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  [Asset] (Phase 5D)"));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    AssetDefsReceived: %d"),
        Stats.AssetDefsReceived.load(
            std::memory_order_relaxed));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    AssetDefsSkipped:  %d"),
        Stats.AssetDefsSkipped.load(
            std::memory_order_relaxed));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    Assignments:       %d ok / %d fail"),
        Stats.AssetAssignmentsSucceeded.load(
            std::memory_order_relaxed),
        Stats.AssetAssignmentsFailed.load(
            std::memory_order_relaxed));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    Lookups:           %d attempt / %d fail"),
        Stats.AssetLookupsAttempted.load(
            std::memory_order_relaxed),
        Stats.AssetLookupsFailed.load(
            std::memory_order_relaxed));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    Pending:           %d / %d peak"),
        Stats.PendingAssetCount,
        Stats.PendingAssetPeak);

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

    // Reset metrics
    Stats.PacketsReceived.store(0, std::memory_order_relaxed);
    Stats.PacketsProcessed.store(0, std::memory_order_relaxed);
    Stats.PacketsDropped.store(0, std::memory_order_relaxed);
    Stats.MalformedPackets.store(0, std::memory_order_relaxed);
    Stats.ReconnectCount.store(0, std::memory_order_relaxed);
    Stats.TotalBytesReceived.store(0, std::memory_order_relaxed);
    Stats.QueueDepthCurrent = 0;
    Stats.QueueDepthPeak = 0;
    Stats.PacketsPerSecondEMA = 0.0;
    Stats.BytesPerSecondEMA = 0.0;
    Stats.ProcessTimeMsEMA = 0.0;
    Stats.PeakProcessTimeMs = 0.0;
    Stats.PeakPacketsPerSecond = 0.0;
    Stats.PeakBytesPerSecond = 0.0;
    Stats.FloodWarnings = 0;
    Stats.QueuePressureWarnings = 0;
    Stats.LastFloodWarningTime = 0.0;
    Stats.LastQueuePressureTime = 0.0;
    Stats.LastMetricsLogTime = 0.0;
    Stats.LastPacketTime = 0.0;
    Stats.LastThreadLoopTime = 0.0;
    Stats.AvgProcessTimeMs = 0.0;
    LastRateSampleTime = 0.0;
    LastRateSamplePackets = 0;
    LastRateSampleBytes = 0;
    FloodAccumulator = 0.0;
    FloodPacketCount = 0;
    FloodWindowStart = 0.0;
    QueuePressureAccumulator = 0.0;
    // Asset diagnostics reset (Phase 5D)
    Stats.AssetDefsReceived.store(0, std::memory_order_relaxed);
    Stats.AssetDefsSkipped.store(0, std::memory_order_relaxed);
    Stats.AssetAssignmentsSucceeded.store(0, std::memory_order_relaxed);
    Stats.AssetAssignmentsFailed.store(0, std::memory_order_relaxed);
    Stats.AssetLookupsAttempted.store(0, std::memory_order_relaxed);
    Stats.AssetLookupsFailed.store(0, std::memory_order_relaxed);
    Stats.PendingAssetCount = 0;
    Stats.PendingAssetPeak = 0;
    Stats.StaleEvictions = 0;

    AssetMetadata.Empty();
    AssetPathCache.Empty();
    PendingAssetQueue.Empty();

    ReconnectHistory.Empty();
    OverflowHistory.Empty();
    LastReportedDrops = 0;

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