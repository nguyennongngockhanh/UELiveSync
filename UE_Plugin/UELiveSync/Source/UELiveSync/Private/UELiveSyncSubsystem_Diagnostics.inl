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

    // ── Phase 6I: Performance & Scalability ────────────
    UE_LOG(LogLiveSync, Log,
        TEXT("  [Phase 6I] Performance & Scalability"));

    UE_LOG(LogLiveSync, Log,
        TEXT("    CoalescedTransforms:        %d"),
        Stats.CoalescedTransforms.load(std::memory_order_relaxed));

    UE_LOG(LogLiveSync, Log,
        TEXT("    SuppressedRedundant:       %d"),
        Stats.RedundantTransformsSuppressed.load(std::memory_order_relaxed));

    UE_LOG(LogLiveSync, Log,
        TEXT("    ReplayDuplicateEntries:    %d"),
        Stats.ReplayDuplicateEntries.load(std::memory_order_relaxed));

    UE_LOG(LogLiveSync, Log,
        TEXT("    ReplayStaleEntryRatio:     %d"),
        Stats.ReplayStaleEntryRatio.load(std::memory_order_relaxed));

    UE_LOG(LogLiveSync, Log,
        TEXT("    ReplayMemoryEstimate:      %d bytes"),
        Stats.ReplayMemoryEstimate.load(std::memory_order_relaxed));

    UE_LOG(LogLiveSync, Log,
        TEXT("    ReplayPeakMemoryBytes:     %d bytes"),
        Stats.ReplayPeakMemoryBytes.load(std::memory_order_relaxed));

    UE_LOG(LogLiveSync, Log,
        TEXT("    TransformsPerSecond:       %d"),
        Stats.TransformsPerSecond.load(std::memory_order_relaxed));

    UE_LOG(LogLiveSync, Log,
        TEXT("    CreatesPerSecond:          %d"),
        Stats.CreatesPerSecond.load(std::memory_order_relaxed));

    UE_LOG(LogLiveSync, Log,
        TEXT("    DeletesPerSecond:          %d"),
        Stats.DeletesPerSecond.load(std::memory_order_relaxed));

    UE_LOG(LogLiveSync, Log,
        TEXT("    HierarchyPacketsPerSecond: %d"),
        Stats.HierarchyPacketsPerSecond.load(std::memory_order_relaxed));

    UE_LOG(LogLiveSync, Log,
        TEXT("    RenamePacketsPerSecond:    %d"),
        Stats.RenamePacketsPerSecond.load(std::memory_order_relaxed));

    UE_LOG(LogLiveSync, Log,
        TEXT("    VisibilityPacketsPerSecond:%d"),
        Stats.VisibilityPacketsPerSecond.load(std::memory_order_relaxed));

    UE_LOG(LogLiveSync, Log,
        TEXT("    CollectionPacketsPerSecond:%d"),
        Stats.CollectionPacketsPerSecond.load(std::memory_order_relaxed));

    UE_LOG(LogLiveSync, Log,
        TEXT("    TickProcessTime:           %d us (peak: %d us)"),
        Stats.TickProcessTimeUs.load(std::memory_order_relaxed),
        Stats.TickPeakProcessTimeUs.load(std::memory_order_relaxed));

    UE_LOG(LogLiveSync, Log,
        TEXT("    QueuePeakDepth:            %d"),
        Stats.QueuePeakDepth.load(std::memory_order_relaxed));

    UE_LOG(LogLiveSync, Log,
        TEXT("    LongFrameWarnings:         %d"),
        Stats.LongFrameWarnings.load(std::memory_order_relaxed));

    UE_LOG(LogLiveSync, Log,
        TEXT("    OverloadWarnings:          %d"),
        Stats.OverloadWarnings.load(std::memory_order_relaxed));

    UE_LOG(LogLiveSync, Log,
        TEXT("    AdaptiveCadenceAdjusted:   %d"),
        Stats.AdaptiveCadenceAdjusted.load(std::memory_order_relaxed));

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
// INGRESS HEALTH
// =========================================================

FString UUELiveSyncSubsystem::FIngressHealthResult::ToString() const
{
    return FString::Printf(TEXT(
        "  IngressHealth: tick=%s (%.1fs ago) "
        "thread=%s listener=%s nullRHI=%s"),
        bTickActive ? TEXT("OK") : TEXT("STALLED"),
        SecondsSinceLastTick,
        bNetworkThreadAlive ? TEXT("ALIVE") : TEXT("DEAD"),
        bListenerValid ? TEXT("VALID") : TEXT("MISSING"),
        bNullRHI ? TEXT("YES") : TEXT("no"));
}

UUELiveSyncSubsystem::FIngressHealthResult UUELiveSyncSubsystem::IsIngressHealthy() const
{
    FIngressHealthResult Result;
    double Now = FPlatformTime::Seconds();

    Result.bNullRHI = FParse::Param(FCommandLine::Get(), TEXT("NullRHI"));

    // Tick activity: if LastTickExecutionTime is zero, Tick never ran
    if (LastTickExecutionTime > 0.0)
    {
        Result.SecondsSinceLastTick = Now - LastTickExecutionTime;
        // Tick is "active" if it ran within the last 10 seconds
        Result.bTickActive = (Result.SecondsSinceLastTick < 10.0);
    }
    else
    {
        Result.SecondsSinceLastTick = -1.0;
        Result.bTickActive = false;
    }

    // Listener validity
    Result.bListenerValid = (ListenerSocket != nullptr);

    // Network thread activity
    if (NetworkRunnable)
    {
        double LastLoop = NetworkRunnable->LastThreadLoopTime.load(std::memory_order_relaxed);
        if (LastLoop > 0.0)
        {
            Result.SecondsSinceLastThreadLoop = Now - LastLoop;
            Result.bNetworkThreadAlive = (Result.SecondsSinceLastThreadLoop < 10.0);
        }
        else
        {
            Result.SecondsSinceLastThreadLoop = -1.0;
            Result.bNetworkThreadAlive = false;
        }
    }
    else
    {
        Result.SecondsSinceLastThreadLoop = -1.0;
        Result.bNetworkThreadAlive = false;
    }

    return Result;
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
    // INGRESS HEALTH
    // =====================================================

    {
        FIngressHealthResult Health = IsIngressHealthy();
        UE_LOG(LogLiveSync, Log, TEXT("  [Ingress Health]"));
        UE_LOG(LogLiveSync, Log, TEXT("  %s"), *Health.ToString());
    }

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
        TEXT("  AssetMetadata:       %d"),
        AssetMetadata.Num());

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  AssetPathCache:      %d"),
        AssetPathCache.Num());

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  PendingAssetQueue:   %d"),
        PendingAssetQueue.Num());

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  MaterialMetadata:    %d"),
        MaterialMetadata.Num());

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  MaterialPathCache:   %d"),
        MaterialPathCache.Num());

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  MaterialTexMapCache: %d"),
        MaterialTextureMapCache.Num());

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  MtexBlocksParsed:    %d"),
        MtexBlocksParsed);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  MtexRecordsParsed:   %d"),
        MtexRecordsParsed);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  MtexMalformed:       %d"),
        MtexMalformed);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  TextureImportReq:    %d"),
        TextureImportRequested);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  TextureImportSkip:   %d"),
        TextureImportSkipped);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  TextureCacheHit:     %d"),
        TextureCacheHit);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  TextureResolveSkip:  %d"),
        TextureResolveSkipped);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  TextureImportFail:   %d"),
        TextureImportFailed);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  TextureImportCache:  %d"),
        TextureImportCache.Num());

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  TexMatApplyReq:      %d"),
        TextureMaterialApplyRequests);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  TexMatApplySucceed:  %d"),
        TextureMaterialApplySucceeded);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  TexMatApplySkip:     %d"),
        TextureMaterialApplySkipped);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  TexMatApplyFail:     %d"),
        TextureMaterialApplyFailed);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  MatAssignments:      %d"),
        MaterialAssignmentsSucceeded);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  PendingMeshReasm:    %d"),
        PendingMeshReassembly.Num());

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  MeshChunksRcv:       %d"),
        MeshChunksReceived);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  MeshReasmCmpl:       %d"),
        MeshReassembliesCompleted);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  MeshSectionsBuilt:   %d"),
        MeshSectionsBuilt);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  [Playback] (Phase 7C)"));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  LastPlaybackState:   %d"),
        LastPlaybackState);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  LastPlaybackSeq:     %u"),
        LastPlaybackSequence);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  LastPlaybackTs:      %.3f"),
        LastPlaybackTimestamp);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  PlaybackRcv:         %d"),
        Stats.PlaybackPacketsReceived.load(std::memory_order_relaxed));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  PlaybackApplied:     %d"),
        Stats.PlaybackPacketsApplied.load(std::memory_order_relaxed));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  PlaybackStale:       %d"),
        Stats.PlaybackPacketsStale.load(std::memory_order_relaxed));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  PlaybackMalformed:   %d"),
        Stats.PlaybackPacketsMalformed.load(std::memory_order_relaxed));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  [Timeline] (Phase 7B)"));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  bHasTimelineState:    %d"),
        bHasTimelineState);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  LastTimelineFC:       %d"),
        LastTimelineState.FrameCurrent);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  LastTimelineFS:       %d"),
        LastTimelineState.FrameStart);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  LastTimelineFE:       %d"),
        LastTimelineState.FrameEnd);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  LastTimelineFPS:      %d/%d"),
        LastTimelineState.FPSNum,
        LastTimelineState.FPSDen);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  LastTimelineSeq:      %u"),
        LastTimelineSequence);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  LastTimelineTs:       %.3f"),
        LastTimelineTimestamp);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  TimelineRcv:          %d"),
        Stats.TimelinePacketsReceived.load(std::memory_order_relaxed));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  TimelineApplied:      %d"),
        Stats.TimelinePacketsApplied.load(std::memory_order_relaxed));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  TimelineStale:        %d"),
        Stats.TimelinePacketsStale.load(std::memory_order_relaxed));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  TimelineMalformed:    %d"),
        Stats.TimelinePacketsMalformed.load(std::memory_order_relaxed));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  [ActiveCamera] (Phase 7D)"));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  bHasActiveCamera:     %d"),
        bHasActiveCamera);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  bHasEverRcvCamera:    %d"),
        bHasEverReceivedActiveCamera);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  LastActiveCamGUID:    %s"),
        *LastActiveCameraGUID.ToString());

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  LastActiveCamSeq:     %u"),
        LastActiveCameraSequence);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  LastActiveCamTs:      %.3f"),
        LastActiveCameraTimestamp);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  ActiveCamRcv:         %d"),
        Stats.ActiveCameraPacketsReceived.load(std::memory_order_relaxed));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  ActiveCamApplied:     %d"),
        Stats.ActiveCameraPacketsApplied.load(std::memory_order_relaxed));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  ActiveCamStale:       %d"),
        Stats.ActiveCameraPacketsStale.load(std::memory_order_relaxed));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  ActiveCamMalformed:   %d"),
        Stats.ActiveCameraPacketsMalformed.load(std::memory_order_relaxed));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  ActiveCamViewport:    %d"),
        Stats.ActiveCameraPacketsAppliedToViewport.load(std::memory_order_relaxed));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  ActiveCamMissGUID:    %d"),
        Stats.ActiveCameraPacketsMissingGUID.load(std::memory_order_relaxed));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  ActiveCamNotCam:      %d"),
        Stats.ActiveCameraPacketsNotCamera.load(std::memory_order_relaxed));

    // =====================================================
    // SEQUENCER OP (Phase 7E)
    // =====================================================

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  SequencerOp:"));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    HasState:          %d"),
        bHasSequencerOpState ? 1 : 0);
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    LastOpcode:        %d"),
        static_cast<int32>(LastSequencerOpOpcode));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    LastFlags:         %d"),
        static_cast<int32>(LastSequencerOpFlags));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    LastSequence:      %u"),
        LastSequencerOpSequence);
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    LastTimestamp:     %.3f"),
        LastSequencerOpTimestamp);
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    PacketsReceived:   %d"),
        Stats.SequencerOpPacketsReceived.load(std::memory_order_relaxed));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    PacketsApplied:    %d"),
        Stats.SequencerOpPacketsApplied.load(std::memory_order_relaxed));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    PacketsStale:      %d"),
        Stats.SequencerOpPacketsStale.load(std::memory_order_relaxed));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    PacketsMalformed:  %d"),
        Stats.SequencerOpPacketsMalformed.load(std::memory_order_relaxed));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    HasLiveSeq:        %d"),
        bHasLiveSyncSequence ? 1 : 0);
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    LiveSeqStart:      %d"),
        LiveSyncSequenceFrameStart);
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    LiveSeqEnd:        %d"),
        LiveSyncSequenceFrameEnd);
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    LiveSeqFPS:        %d/%d"),
        LiveSyncSequenceFPSNum, LiveSyncSequenceFPSDen);
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    PossessAdded:      %d"),
        Stats.SequencerPossessablesAdded.load(std::memory_order_relaxed));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    PossessRemoved:    %d"),
        Stats.SequencerPossessablesRemoved.load(std::memory_order_relaxed));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    PossessMissing:    %d"),
        Stats.SequencerPossessablesMissingActor.load(std::memory_order_relaxed));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    PossessDupes:      %d"),
        Stats.SequencerPossessablesDuplicate.load(std::memory_order_relaxed));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    BindingCount:      %d"),
        LiveSyncGuidToSequencerBinding.Num());
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    PendingBindings:   %d"),
        PendingSequencerBindings.Num());
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    CutsAdded:        %d"),
        Stats.SequencerCameraCutsAdded.load(std::memory_order_relaxed));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    CutsMissingBind:  %d"),
        Stats.SequencerCameraCutsMissingBinding.load(std::memory_order_relaxed));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    CutsMalformedRng: %d"),
        Stats.SequencerCameraCutsMalformedRange.load(std::memory_order_relaxed));

    // Keyframe replication (Phase 7E Stage 7)
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    KFReceived:       %d"),
        Stats.KeyframePacketsReceived.load(std::memory_order_relaxed));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    KFApplied:        %d"),
        Stats.KeyframePacketsApplied.load(std::memory_order_relaxed));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    KFStale:          %d"),
        Stats.KeyframePacketsStale.load(std::memory_order_relaxed));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    KFMalformed:      %d"),
        Stats.KeyframePacketsMalformed.load(std::memory_order_relaxed));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    KFLastSequence:   %u"),
        LastKeyframeSequence);
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    KFLastTimestamp:  %.3f"),
        LastKeyframeTimestamp);
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    KFHasState:       %d"),
        bHasKeyframeState ? 1 : 0);

    // Keyframe apply (Phase 7E Stage 9)
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    KFKeysApplied:    %d"),
        Stats.KeyframeKeysApplied.load(std::memory_order_relaxed));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    KFMissingBind:    %d"),
        Stats.KeyframeMissingBinding.load(std::memory_order_relaxed));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    KFUnsuppChan:     %d"),
        Stats.KeyframeUnsupportedChannel.load(std::memory_order_relaxed));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    KFTracksCreated:  %d"),
        Stats.KeyframeTrackCreated.load(std::memory_order_relaxed));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    KFSectionsCre:    %d"),
        Stats.KeyframeSectionCreated.load(std::memory_order_relaxed));

    // Visibility keyframe counters (Phase 7E Stage 10A)
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    KFBVisibility:    %d"),
        Stats.KeyframeVisibilityKeysApplied.load(std::memory_order_relaxed));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    KFBTrackCre:      %d"),
        Stats.KeyframeVisibilityTrackCreated.load(std::memory_order_relaxed));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    KFBSectionCre:    %d"),
        Stats.KeyframeVisibilitySectionCreated.load(std::memory_order_relaxed));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("    KFBUnsupp:        %d"),
        Stats.KeyframeVisibilityUnsupported.load(std::memory_order_relaxed));

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

    // Phase 6H — Semantic Consistency Hardening counters reset
    Stats.PacketHierarchyBeforeCreate.store(0, std::memory_order_relaxed);
    Stats.PacketRenameBeforeCreate.store(0, std::memory_order_relaxed);
    Stats.PacketVisibilityBeforeCreate.store(0, std::memory_order_relaxed);
    Stats.PacketCollectionBeforeCreate.store(0, std::memory_order_relaxed);
    Stats.PacketDuplicateAttachDetected.store(0, std::memory_order_relaxed);
    Stats.PacketDuplicateDetachDetected.store(0, std::memory_order_relaxed);
    Stats.PacketStaleReplayOrder.store(0, std::memory_order_relaxed);
    Stats.PacketReplaySequenceGap.store(0, std::memory_order_relaxed);
    Stats.AuthorityParentMismatch.store(0, std::memory_order_relaxed);
    Stats.AuthorityVisibilityMismatch.store(0, std::memory_order_relaxed);
    Stats.AuthorityRenameMismatch.store(0, std::memory_order_relaxed);
    Stats.AuthorityCollectionDivergence.store(0, std::memory_order_relaxed);
    Stats.AuthorityStaleLocalFlag.store(0, std::memory_order_relaxed);
    Stats.AuthorityStaleRootFlag.store(0, std::memory_order_relaxed);
    Stats.BurstPeakPacketsPerTick.store(0, std::memory_order_relaxed);
    Stats.BurstReplayQueueGrowthPeak.store(0, std::memory_order_relaxed);
    Stats.BurstRollbackFrequency.store(0, std::memory_order_relaxed);
    Stats.BurstDivergenceFrequency.store(0, std::memory_order_relaxed);
    Stats.BurstReconnectCycles.store(0, std::memory_order_relaxed);
    Stats.ReplayDeterminismVerifyCount.store(0, std::memory_order_relaxed);
    Stats.ReplayDeterminismPassCount.store(0, std::memory_order_relaxed);
    Stats.ReplayDeterminismFailCount.store(0, std::memory_order_relaxed);
    Stats.ReplayDomainCollectionHash.store(0, std::memory_order_relaxed);
    Stats.ReplayDomainLifecycleHash.store(0, std::memory_order_relaxed);
    Stats.ReplayDomainRenameHash.store(0, std::memory_order_relaxed);
    Stats.ReplayDomainTransformHash.store(0, std::memory_order_relaxed);
    Stats.KBPTransformGatedSemantic.store(0, std::memory_order_relaxed);
    Stats.KBPStaleLocalAfterDetach.store(0, std::memory_order_relaxed);
    Stats.KBPWorldLocalAuthorityMixing.store(0, std::memory_order_relaxed);
    Stats.KBPReplayRollbackIncomplete.store(0, std::memory_order_relaxed);
    Stats.KBPHierarchyOverwriteFromTransform.store(0, std::memory_order_relaxed);

    Phase6HFrameCounter = 0;
    Phase6HBurstTickPacketCount = 0;
    Phase6HBurstTickPeak = 0;
    Phase6HCreatedThisTick.Empty();

    UE_LOG(LogLiveSync, Log,
        TEXT("[PHASE6H] All Phase 6H counters reset (ConsoleReset)"));

    // Phase 6I: reset all performance & scalability counters
    Stats.CoalescedTransforms.store(0, std::memory_order_relaxed);
    Stats.RedundantTransformsSuppressed.store(0, std::memory_order_relaxed);
    Stats.ReplayDuplicateEntries.store(0, std::memory_order_relaxed);
    Stats.ReplayStaleEntryRatio.store(0, std::memory_order_relaxed);
    Stats.ReplayMemoryEstimate.store(0, std::memory_order_relaxed);
    Stats.ReplayPeakMemoryBytes.store(0, std::memory_order_relaxed);
    Stats.TransformsPerSecond.store(0, std::memory_order_relaxed);
    Stats.CreatesPerSecond.store(0, std::memory_order_relaxed);
    Stats.DeletesPerSecond.store(0, std::memory_order_relaxed);
    Stats.HierarchyPacketsPerSecond.store(0, std::memory_order_relaxed);
    Stats.RenamePacketsPerSecond.store(0, std::memory_order_relaxed);
    Stats.VisibilityPacketsPerSecond.store(0, std::memory_order_relaxed);
    Stats.CollectionPacketsPerSecond.store(0, std::memory_order_relaxed);
    Stats.TickProcessTimeUs.store(0, std::memory_order_relaxed);
    Stats.TickPeakProcessTimeUs.store(0, std::memory_order_relaxed);
    Stats.QueuePeakDepth.store(0, std::memory_order_relaxed);
    Stats.LongFrameWarnings.store(0, std::memory_order_relaxed);
    Stats.OverloadWarnings.store(0, std::memory_order_relaxed);
    Stats.AdaptiveCadenceAdjusted.store(0, std::memory_order_relaxed);

    UE_LOG(LogLiveSync, Log,
        TEXT("[PHASE6I] All Phase 6I counters reset (ConsoleReset)"));

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
    MaterialMetadata.Empty();
    MaterialPathCache.Empty();
    MaterialDefsReceived = 0;
    MaterialAssignmentsSucceeded = 0;
    MaterialTextureMapCache.Empty();
    MtexBlocksParsed = 0;
    MtexRecordsParsed = 0;
    MtexMalformed = 0;
    TextureImportRequested = 0;
    TextureImportSkipped = 0;
    TextureCacheHit = 0;
    TextureResolveSkipped = 0;
    TextureImportFailed = 0;
    TextureImportCache.Empty();

    TextureMaterialApplyRequests = 0;
    TextureMaterialApplySucceeded = 0;
    TextureMaterialApplySkipped = 0;
    TextureMaterialApplyFailed = 0;

    PendingMeshReassembly.Empty();
    MeshChunksReceived = 0;
    MeshReassembliesCompleted = 0;
    MeshSectionsBuilt = 0;

    // Phase 7C: playback state reset
    Stats.PlaybackPacketsReceived.store(0, std::memory_order_relaxed);
    Stats.PlaybackPacketsApplied.store(0, std::memory_order_relaxed);
    Stats.PlaybackPacketsStale.store(0, std::memory_order_relaxed);
    Stats.PlaybackPacketsMalformed.store(0, std::memory_order_relaxed);
    LastPlaybackState = 0;
    LastPlaybackSequence = 0;
    LastPlaybackTimestamp = 0.0;
    bHasPlaybackState = false;

    // Phase 7B: timeline state reset
    Stats.TimelinePacketsReceived.store(0, std::memory_order_relaxed);
    Stats.TimelinePacketsApplied.store(0, std::memory_order_relaxed);
    Stats.TimelinePacketsStale.store(0, std::memory_order_relaxed);
    Stats.TimelinePacketsMalformed.store(0, std::memory_order_relaxed);
    LastTimelineState = FTimelinePayload();
    LastTimelineSequence = 0;
    LastTimelineTimestamp = 0.0;
    bHasTimelineState = false;

    // Phase 7D: active camera state reset
    Stats.ActiveCameraPacketsReceived.store(0, std::memory_order_relaxed);
    Stats.ActiveCameraPacketsApplied.store(0, std::memory_order_relaxed);
    Stats.ActiveCameraPacketsStale.store(0, std::memory_order_relaxed);
    Stats.ActiveCameraPacketsMalformed.store(0, std::memory_order_relaxed);
    Stats.ActiveCameraPacketsAppliedToViewport.store(0, std::memory_order_relaxed);
    Stats.ActiveCameraPacketsMissingGUID.store(0, std::memory_order_relaxed);
    Stats.ActiveCameraPacketsNotCamera.store(0, std::memory_order_relaxed);
    LastActiveCameraGUID = FGuid();
    LastActiveCameraSequence = 0;
    LastActiveCameraTimestamp = 0.0;
    bHasActiveCamera = false;
    bHasEverReceivedActiveCamera = false;

    // Sequencer op (Phase 7E)
    Stats.SequencerOpPacketsReceived.store(0, std::memory_order_relaxed);
    Stats.SequencerOpPacketsApplied.store(0, std::memory_order_relaxed);
    Stats.SequencerOpPacketsStale.store(0, std::memory_order_relaxed);
    Stats.SequencerOpPacketsMalformed.store(0, std::memory_order_relaxed);
    Stats.SequencerPossessablesAdded.store(0, std::memory_order_relaxed);
    Stats.SequencerPossessablesRemoved.store(0, std::memory_order_relaxed);
    Stats.SequencerPossessablesMissingActor.store(0, std::memory_order_relaxed);
    Stats.SequencerPossessablesDuplicate.store(0, std::memory_order_relaxed);
    Stats.SequencerCameraCutsAdded.store(0, std::memory_order_relaxed);
    Stats.SequencerCameraCutsMissingBinding.store(0, std::memory_order_relaxed);
    Stats.SequencerCameraCutsMalformedRange.store(0, std::memory_order_relaxed);
    LastSequencerOpOpcode = 0;
    LastSequencerOpFlags = 0;
    LastSequencerOpSequence = 0;
    LastSequencerOpTimestamp = 0.0;
    bHasSequencerOpState = false;
    LiveSyncSequence = nullptr;
    bHasLiveSyncSequence = false;
    LiveSyncSequenceFrameStart = 0;
    LiveSyncSequenceFrameEnd = 0;
    LiveSyncSequenceFPSNum = 0;
    LiveSyncSequenceFPSDen = 1;
    LiveSyncGuidToSequencerBinding.Empty();
    PendingSequencerBindings.Empty();

    // Keyframe state (Phase 7E Stage 7)
    Stats.KeyframePacketsReceived.store(0, std::memory_order_relaxed);
    Stats.KeyframePacketsApplied.store(0, std::memory_order_relaxed);
    Stats.KeyframePacketsStale.store(0, std::memory_order_relaxed);
    Stats.KeyframePacketsMalformed.store(0, std::memory_order_relaxed);
    Stats.KeyframeKeysApplied.store(0, std::memory_order_relaxed);
    Stats.KeyframeMissingBinding.store(0, std::memory_order_relaxed);
    Stats.KeyframeUnsupportedChannel.store(0, std::memory_order_relaxed);
    Stats.KeyframeTrackCreated.store(0, std::memory_order_relaxed);
    Stats.KeyframeSectionCreated.store(0, std::memory_order_relaxed);
    // Visibility counters (Phase 7E Stage 10A)
    Stats.KeyframeVisibilityKeysApplied.store(0, std::memory_order_relaxed);
    Stats.KeyframeVisibilityTrackCreated.store(0, std::memory_order_relaxed);
    Stats.KeyframeVisibilitySectionCreated.store(0, std::memory_order_relaxed);
    Stats.KeyframeVisibilityUnsupported.store(0, std::memory_order_relaxed);
    LastKeyframeSequence = 0;
    LastKeyframeTimestamp = 0.0;
    bHasKeyframeState = false;

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
