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
// CONSOLE: DUMP REPLAY BUFFER (Phase 6F Stage 7)
// =========================================================

void UUELiveSyncSubsystem::
ConsoleDumpReplayBuffer()
{
    UE_LOG(LogLiveSync, Log,
        TEXT("=== Collection Replay Buffer ==="));

    const FString Dump = DumpReplayBuffer();
    TArray<FString> Lines;
    Dump.ParseIntoArrayLines(Lines);

    for (const FString& Line : Lines)
    {
        UE_LOG(LogLiveSync, Log, TEXT("%s"), *Line);
    }

    UE_LOG(LogLiveSync, Log,
        TEXT("=== End Replay Buffer ==="));
}


// =========================================================
// CONSOLE: DUMP COLLECTION GRAPH (Phase 6F Stage 7)
// =========================================================

void UUELiveSyncSubsystem::
ConsoleDumpCollectionGraph()
{
    UE_LOG(LogLiveSync, Log,
        TEXT("=== Collection Graph ==="));

    const FString Dump = DumpCollectionGraph();
    TArray<FString> Lines;
    Dump.ParseIntoArrayLines(Lines);

    for (const FString& Line : Lines)
    {
        UE_LOG(LogLiveSync, Log, TEXT("%s"), *Line);
    }

    UE_LOG(LogLiveSync, Log,
        TEXT("=== End Collection Graph ==="));
}


// =========================================================
// CONSOLE: VERIFY COLLECTION REPLAY (Phase 6F Stage 7)
// =========================================================

void UUELiveSyncSubsystem::
ConsoleVerifyCollectionReplay()
{
    const FString Result = ForceReplayVerification();
    UE_LOG(LogLiveSync, Log, TEXT("%s"), *Result);
}


// =========================================================
// CONSOLE: CLEAR REPLAY DIAGNOSTICS (Phase 6F Stage 7)
// =========================================================

void UUELiveSyncSubsystem::
ConsoleClearReplayDiagnostics()
{
    ClearReplayDiagnostics();
    UE_LOG(LogLiveSync, Log,
        TEXT("[COLLECTION] Replay diagnostics cleared"));
}


// =========================================================
// CONSOLE: TOGGLE REPLAY TRACING (Phase 6F Stage 7)
// =========================================================

void UUELiveSyncSubsystem::
ConsoleToggleReplayTracing()
{
    const bool bNewState =
        !GCollectionReplayTraceConfig.bTracingEnabled;

    SetReplayTracingEnabled(bNewState);

    UE_LOG(LogLiveSync, Log,
        TEXT("[COLLECTION] Replay tracing %s"),
        bNewState ? TEXT("ENABLED") : TEXT("DISABLED"));
}


// =========================================================
// CONSOLE: DUMP WORLD REPLAY STATE (Phase 6G)
// =========================================================

void UUELiveSyncSubsystem::
ConsoleDumpWorldReplayState()
{
    UE_LOG(LogLiveSync, Log,
        TEXT("=== World Replay State ==="));

    const FString Dump = DumpWorldReplayState();
    TArray<FString> Lines;
    Dump.ParseIntoArrayLines(Lines);

    for (const FString& Line : Lines)
    {
        UE_LOG(LogLiveSync, Log, TEXT("%s"), *Line);
    }

    UE_LOG(LogLiveSync, Log,
        TEXT("=== End World Replay State ==="));
}


// =========================================================
// CONSOLE: VERIFY WORLD REPLAY (Phase 6G)
// =========================================================

void UUELiveSyncSubsystem::
ConsoleVerifyWorldReplay()
{
    const FString Result = VerifyWorldReplay();
    UE_LOG(LogLiveSync, Log, TEXT("%s"), *Result);
}


// =========================================================
// CONSOLE: DUMP REPLAY TIMELINE (Phase 6G)
// =========================================================

void UUELiveSyncSubsystem::
ConsoleDumpReplayTimeline()
{
    UE_LOG(LogLiveSync, Log,
        TEXT("=== Replay Timeline (%d events) ==="),
        GCollectionReplayTimeline.Num());

    // Show last 25 entries
    const int32 StartIdx = FMath::Max(0,
        GCollectionReplayTimeline.Events.Num() - 25);

    for (int32 i = StartIdx;
         i < GCollectionReplayTimeline.Events.Num(); i++)
    {
        const FReplayTimelineEvent& Evt =
            GCollectionReplayTimeline.Events[i];
        UE_LOG(LogLiveSync, Log, TEXT("  %s"), *Evt.ToString());
    }

    UE_LOG(LogLiveSync, Log,
        TEXT("=== End Replay Timeline ==="));
}


// =========================================================
// CONSOLE: EXPORT WORLD SNAPSHOT (Phase 6G)
// =========================================================

void UUELiveSyncSubsystem::
ConsoleExportWorldSnapshot()
{
    UE_LOG(LogLiveSync, Log,
        TEXT("=== World Snapshot ==="));

    const FString Snapshot = ExportWorldSnapshot();
    TArray<FString> Lines;
    Snapshot.ParseIntoArrayLines(Lines);

    for (const FString& Line : Lines)
    {
        UE_LOG(LogLiveSync, Log, TEXT("%s"), *Line);
    }

    UE_LOG(LogLiveSync, Log,
        TEXT("=== End World Snapshot ==="));
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

    // Rename diagnostics reset (Phase 6)
    Stats.RenamesProcessed.store(0, std::memory_order_relaxed);
    Stats.RenameStaleRejections.store(0, std::memory_order_relaxed);
    Stats.RenameReplayApplied.store(0, std::memory_order_relaxed);
    Stats.RenameReplaySkipped.store(0, std::memory_order_relaxed);
    GRenameSequences.LastSequence.Empty();
    GRenamePersistentLabel.Empty();

    UE_LOG(LogLiveSync, Log,
        TEXT("[RENAME] Replay tracker + label registry reset (ConsoleReset)"));

    // Visibility diagnostics reset (Phase 6)
    Stats.VisibilityProcessed.store(0, std::memory_order_relaxed);
    Stats.VisibilityStaleRejections.store(0, std::memory_order_relaxed);
    Stats.VisibilityReplayApplied.store(0, std::memory_order_relaxed);
    Stats.VisibilityReplaySkipped.store(0, std::memory_order_relaxed);
    GVisibilitySequences.LastSequence.Empty();

    UE_LOG(LogLiveSync, Log,
        TEXT("[VISIBILITY] Replay tracker reset (ConsoleReset)"));

    // Hierarchy diagnostics reset (Phase 6D)
    Stats.HierarchyPackets.store(0, std::memory_order_relaxed);
    Stats.HierarchyProcessed.store(0, std::memory_order_relaxed);
    Stats.HierarchyStaleRejections.store(0, std::memory_order_relaxed);
    Stats.HierarchyReplayApplied.store(0, std::memory_order_relaxed);
    Stats.HierarchyReplaySkipped.store(0, std::memory_order_relaxed);
    Stats.HierarchyOrphans.store(0, std::memory_order_relaxed);
    Stats.HierarchyCycles.store(0, std::memory_order_relaxed);
    Stats.HierarchyDeferredResolved.store(0, std::memory_order_relaxed);
    GHierarchySequences.LastSequence.Empty();
    PendingHierarchyAttachments.Empty();

    UE_LOG(LogLiveSync, Log,
        TEXT("[HIERARCHY] Replay tracker reset (ConsoleReset)"));

    UE_LOG(LogLiveSync, Log,
        TEXT("[HIERARCHY] PendingHierarchyAttachments cleared (ConsoleReset)"));

    // Lifecycle/delete diagnostics reset (Phase 6E)
    Stats.DeletePackets.store(0, std::memory_order_relaxed);
    Stats.DeleteProcessed.store(0, std::memory_order_relaxed);
    Stats.DeleteReplayApplied.store(0, std::memory_order_relaxed);
    Stats.DeleteReplaySkipped.store(0, std::memory_order_relaxed);
    Stats.DeleteStaleRejections.store(0, std::memory_order_relaxed);
    Stats.DeleteTombstoneRejections.store(0, std::memory_order_relaxed);
    Stats.DeleteMissingActor.store(0, std::memory_order_relaxed);
    Stats.DeleteDeferredDuringSnapshot.store(0, std::memory_order_relaxed);
    GDeleteSequences.Clear();
    GDeleteTombstoneMap.Empty();
    GDeleteTombstoneOrder.Empty();
    DeferredDeleteQueue.Empty();

    // Collection diagnostics reset (Phase 6F)
    Stats.CollectionPacketsReceived.store(0, std::memory_order_relaxed);
    Stats.CollectionStaleRejected.store(0, std::memory_order_relaxed);
    Stats.CollectionDuplicateRejected.store(0, std::memory_order_relaxed);
    Stats.CollectionAddsApplied.store(0, std::memory_order_relaxed);
    Stats.CollectionRemovesApplied.store(0, std::memory_order_relaxed);
    Stats.CollectionMovesApplied.store(0, std::memory_order_relaxed);
    Stats.CollectionClearsApplied.store(0, std::memory_order_relaxed);
    Stats.CollectionReplayProcessed.store(0, std::memory_order_relaxed);
    Stats.CollectionReplayRejected.store(0, std::memory_order_relaxed);
    Stats.CollectionSnapshotHashMismatch.store(0, std::memory_order_relaxed);
    Stats.CollectionSnapshotRebuilds.store(0, std::memory_order_relaxed);
    Stats.CollectionReplaySequenceGap.store(0, std::memory_order_relaxed);
    Stats.CollectionReplayOutOfOrder.store(0, std::memory_order_relaxed);
    Stats.CollectionReplayDivergence.store(0, std::memory_order_relaxed);
    Stats.CollectionReplayCorruption.store(0, std::memory_order_relaxed);
    Stats.CollectionReplayRollbacks.store(0, std::memory_order_relaxed);
    Stats.CollectionReplayTimelineRecorded.store(0, std::memory_order_relaxed);
    Stats.CollectionReplayTracesEmitted.store(0, std::memory_order_relaxed);
    Stats.CollectionReplayBufferOverflow.store(0, std::memory_order_relaxed);
    Stats.CollectionReplayPacketsTruncated.store(0, std::memory_order_relaxed);
    Stats.CollectionReplayPacketsDropped.store(0, std::memory_order_relaxed);
    Stats.CollectionReplayPeakBufferUsage.store(0, std::memory_order_relaxed);
    Stats.CollectionReplayLatencySamples.store(0, std::memory_order_relaxed);
    Stats.CollectionReplayReconnectRebuilds.store(0, std::memory_order_relaxed);
    Stats.CollectionReplayReconnectPacketsReplayed.store(0, std::memory_order_relaxed);
    Stats.CollectionReplayReconnectDivergences.store(0, std::memory_order_relaxed);
    Stats.CollectionReplayReconnectRollbacks.store(0, std::memory_order_relaxed);
    Stats.WorldReplayEntriesRecorded.store(0, std::memory_order_relaxed);
    Stats.WorldReplayVerifications.store(0, std::memory_order_relaxed);
    Stats.WorldReplayDivergences.store(0, std::memory_order_relaxed);
    Stats.WorldReplayRollbacks.store(0, std::memory_order_relaxed);
    Stats.WorldReplayCorruption.store(0, std::memory_order_relaxed);
    Stats.WorldReplayDependencyViolations.store(0, std::memory_order_relaxed);
    Stats.WorldReplaySnapshotExports.store(0, std::memory_order_relaxed);
    Stats.WorldReplaySnapshotRebuilds.store(0, std::memory_order_relaxed);
    Stats.WorldReplayReconnectRebuilds.store(0, std::memory_order_relaxed);
    Stats.WorldReplayReconnectDivergences.store(0, std::memory_order_relaxed);
    GWorldReplayBuffer.Empty();
    GWorldSavedState.Clear();
    GWorldLastVerifiedHash = 0;
    GCollectionSequences.Clear();
    GCollectionMembership.Empty();
    GCollectionIdentities.Empty();
    GCollectionReplayBuffer.Empty();
    GCollectionReplaySequences.Empty();
    GCollectionReplayChecksums.Empty();
    GCollectionReplayTimeline.Clear();
    GCollectionReplayWindowStats.Clear();
    GCollectionReplayPeakUsage = 0;

    UE_LOG(LogLiveSync, Log,
        TEXT("[COLLECTION][RESET] Sequence tracker cleared (ConsoleReset)"));

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
