
// =========================================================
// UELiveSyncSubsystem_Phase6I.inl — Performance & Scalability
// =========================================================
// PHASE 6I — Performance + Scalability + Validation
//
// All functions are additive, low-risk, diagnostics-oriented.
// NO runtime behavior is mutated unless explicitly gated by
// CVar or console command.
//
// Goals:
//   A — Transform Burst Optimization (coalescing, latest-wins)
//   B — Replay Buffer Efficiency (memory tracking, metrics)
//   C — Packet Scheduling (per-domain counters, throughput)
//   D — Hot Path Allocation Reduction (diagnostics)
//   E — Tick Scheduling Hardening (adaptive cadence, overload)
//
// All functions in this file compile as part of
// UELiveSyncSubsystem.cpp via #include at the bottom.
// =========================================================

// =========================================================
// PHASE 6I — TICK-INTEGRATED DIAGNOSTICS
// =========================================================
// Runs at configurable cadence (default ~60 ticks = ~1s).
// Tracks per-domain rates, detects overload/long frames,
// adjusts diagnostics cadence adaptively under load.
// =========================================================

void UUELiveSyncSubsystem::
TickPhase6I(float DeltaTime)
{
    CHECK_GAME_THREAD();

    Phase6IFrameCounter++;

    // ── Per-second packet rate tracking ─────────────────
    {
        double Now = FPlatformTime::Seconds();
        if (Now - Phase6ILastPerSecondClear >= 1.0)
        {
            Stats.TransformsPerSecond.store(
                Phase6IPerSecondTransforms, std::memory_order_relaxed);
            Stats.CreatesPerSecond.store(
                Phase6IPerSecondCreates, std::memory_order_relaxed);
            Stats.DeletesPerSecond.store(
                Phase6IPerSecondDeletes, std::memory_order_relaxed);
            Stats.HierarchyPacketsPerSecond.store(
                Phase6IPerSecondHierarchy, std::memory_order_relaxed);
            Stats.RenamePacketsPerSecond.store(
                Phase6IPerSecondRenames, std::memory_order_relaxed);
            Stats.VisibilityPacketsPerSecond.store(
                Phase6IPerSecondVisibility, std::memory_order_relaxed);
            Stats.CollectionPacketsPerSecond.store(
                Phase6IPerSecondCollections, std::memory_order_relaxed);

            Phase6IPerSecondTransforms = 0;
            Phase6IPerSecondCreates = 0;
            Phase6IPerSecondDeletes = 0;
            Phase6IPerSecondHierarchy = 0;
            Phase6IPerSecondRenames = 0;
            Phase6IPerSecondVisibility = 0;
            Phase6IPerSecondCollections = 0;
            Phase6ILastPerSecondClear = Now;
        }
    }

    // ── Long frame detection ────────────────────────────
    {
        double TickElapsed = DeltaTime;
        if (TickElapsed > Phase6ILongFrameThreshold && ConnectionSocket)
        {
            Stats.LongFrameWarnings.fetch_add(1, std::memory_order_relaxed);
            if (bEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[PHASE6I] Long frame: %.1fms (threshold=%.0fms)"),
                    TickElapsed * 1000.0,
                    Phase6ILongFrameThreshold * 1000.0);
            }
        }

        if (TickElapsed > Phase6IOverloadThreshold && ConnectionSocket)
        {
            Stats.OverloadWarnings.fetch_add(1, std::memory_order_relaxed);
        }
    }

    // ── Adaptive diagnostics cadence ───────────────────
    {
        int32 CurrentQueue = PacketQueue.Size();
        bool bUnderLoad = (CurrentQueue > 64)
            || (Stats.TransformsPerSecond.load(std::memory_order_relaxed) > 1000);

        int32 BaseInterval = 60;

        if (bUnderLoad && Phase6IDiagnosticsRunInterval < 300)
        {
            Phase6IDiagnosticsRunInterval = FMath::Min(Phase6IDiagnosticsRunInterval + 30, 300);
            Stats.AdaptiveCadenceAdjusted.fetch_add(1, std::memory_order_relaxed);
        }
        else if (!bUnderLoad && Phase6IDiagnosticsRunInterval > 60)
        {
            Phase6IDiagnosticsRunInterval = FMath::Max(Phase6IDiagnosticsRunInterval - 15, 60);
        }
    }

    // ── Periodic diagnostics at configurable cadence ────
    if (Phase6IFrameCounter % Phase6IDiagnosticsRunInterval != 0)
        return;

    if (bEnableVerboseSyncLogs)
    {
        int32 ReplayMem = EstimateReplayBufferMemory();
        Stats.ReplayMemoryEstimate.store(ReplayMem, std::memory_order_relaxed);
        if (ReplayMem > Stats.ReplayPeakMemoryBytes.load(std::memory_order_relaxed))
        {
            Stats.ReplayPeakMemoryBytes.store(ReplayMem, std::memory_order_relaxed);
        }

        UE_LOG(LogLiveSync, Log,
            TEXT("[PHASE6I] Diagnostics: "
                 "rates(t=%d c=%d d=%d h=%d r=%d v=%d col=%d) "
                 "replayMem=%dKB replayEntries=%d queue=%d frame=%.1fms"),
            Stats.TransformsPerSecond.load(std::memory_order_relaxed),
            Stats.CreatesPerSecond.load(std::memory_order_relaxed),
            Stats.DeletesPerSecond.load(std::memory_order_relaxed),
            Stats.HierarchyPacketsPerSecond.load(std::memory_order_relaxed),
            Stats.RenamePacketsPerSecond.load(std::memory_order_relaxed),
            Stats.VisibilityPacketsPerSecond.load(std::memory_order_relaxed),
            Stats.CollectionPacketsPerSecond.load(std::memory_order_relaxed),
            ReplayMem / 1024,
            GWorldReplayBuffer.Num(),
            PacketQueue.Size(),
            DeltaTime * 1000.0);
    }
}


// =========================================================
// GOAL A — TRANSFORM COALESCING
// =========================================================
// Latest-transform-wins per tick. When multiple PT_Transform
// packets arrive for the same GUID in a single tick, only
// the most recent is processed. Redundant matches (no change
// vs current state) are suppressed entirely.
// Gated by bPhase6ICoalesceEnabled CVar.
// =========================================================

void UUELiveSyncSubsystem::
CoalesceTransforms(TArray<FLiveSyncPacket>& PacketsThisTick)
{
    if (!bPhase6ICoalesceEnabled || PacketsThisTick.Num() <= 1)
        return;

    // Build map of latest index per GUID for transform packets
    Phase6ICoalesceMap.Reset();
    TArray<bool> KeepMask;
    KeepMask.Init(true, PacketsThisTick.Num());

    for (int32 i = 0; i < PacketsThisTick.Num(); i++)
    {
        const FLiveSyncPacket& Pkt = PacketsThisTick[i];
        if (Pkt.RawData.Num() < 24)
            continue;

        // Read packet type from header (offset 6)
        uint8 PktType = Pkt.RawData[6];
        if (PktType != 0x01) // PT_Transform only
            continue;

        // Extract first GUID (offset 24)
        if (Pkt.RawData.Num() < 40)
            continue;

        uint32 GuidParts[4];
        FMemory::Memcpy(GuidParts, Pkt.RawData.GetData() + 24, 16);
        FGuid Guid(GuidParts[0], GuidParts[1], GuidParts[2], GuidParts[3]);

        int32* PrevIdx = Phase6ICoalesceMap.Find(Guid);
        if (PrevIdx)
        {
            // Mark previous as coalesced, keep latest
            KeepMask[*PrevIdx] = false;
            KeepMask[i] = true;
            Phase6ICoalesceMap[Guid] = i;
            Stats.CoalescedTransforms.fetch_add(1, std::memory_order_relaxed);
        }
        else
        {
            Phase6ICoalesceMap.Add(Guid, i);
        }
    }

    {
        // Count how many were marked for removal
        int32 CoalescedThisTick = 0;
        for (int32 i = 0; i < KeepMask.Num(); i++)
        {
            if (!KeepMask[i])
                CoalescedThisTick++;
        }

        // Remove coalesced entries (keep only the latest per GUID)
        for (int32 i = PacketsThisTick.Num() - 1; i >= 0; i--)
        {
            if (!KeepMask[i])
            {
                PacketsThisTick.RemoveAt(i, 1, EAllowShrinking::No);
            }
        }

        if (CoalescedThisTick > 0 && bEnableVerboseSyncLogs)
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[PHASE6I][COALESCE] Coalesced %d transform packets, "
                     "%d remaining this tick"),
                CoalescedThisTick,
                PacketsThisTick.Num());
        }
    }
}


// =========================================================
// GOAL B — REPLAY BUFFER EFFICIENCY
// =========================================================
// Memory estimation, duplicate detection, stale entry ratio.
// =========================================================

int32 UUELiveSyncSubsystem::
EstimateReplayBufferMemory() const
{
    // Approximate per-entry cost:
    //   FGuid: 16 bytes x2 = 32
    //   uint32: 4 bytes x2 = 8
    //   double: 8 bytes
    //   uint8: 1 byte
    //   TArray<uint8> overhead: ~24 + payload
    //   FWorldReplayEntry struct overhead: ~64
    static constexpr int32 BASE_ENTRY_SIZE = 64;
    static constexpr int32 GUID_PAIR_SIZE = 32;
    static constexpr int32 PAYLOAD_OVERHEAD = 24;

    int32 Total = 0;
    for (const FWorldReplayEntry& Entry : GWorldReplayBuffer)
    {
        Total += BASE_ENTRY_SIZE + GUID_PAIR_SIZE + PAYLOAD_OVERHEAD + Entry.Payload.Num();
    }
    return Total;
}


int32 UUELiveSyncSubsystem::
CountUniqueReplayEntries() const
{
    TSet<TPair<EWorldReplayDomain, FGuid>> Seen;
    int32 Unique = 0;
    for (const FWorldReplayEntry& Entry : GWorldReplayBuffer)
    {
        TPair<EWorldReplayDomain, FGuid> Key(Entry.Domain, Entry.Guid);
        if (!Seen.Contains(Key))
        {
            Seen.Add(Key);
            Unique++;
        }
    }
    return Unique;
}


// =========================================================
// GOAL C — PER-DOMAIN PACKET TRACKING
// =========================================================
// Called from ProcessBinaryPacket for every non-heartbeat
// packet type. Increments per-domain counters for rate
// tracking.
// =========================================================

void UUELiveSyncSubsystem::
TrackPerDomainPacket(uint8 PacketType)
{
    switch (PacketType)
    {
    case 0x01: // PT_Transform
        Phase6IPerSecondTransforms++;
        break;
    case 0x03: // PT_Create
        Phase6IPerSecondCreates++;
        break;
    case 0x04: // PT_Delete
    case 0x0E: // PT_Delete_V5
        Phase6IPerSecondDeletes++;
        break;
    case 0x0B: // PT_Visibility
        Phase6IPerSecondVisibility++;
        break;
    case 0x0C: // PT_Rename
        Phase6IPerSecondRenames++;
        break;
    case 0x0D: // PT_Hierarchy
        Phase6IPerSecondHierarchy++;
        break;
    case 0x0F: // PT_Collection
        Phase6IPerSecondCollections++;
        break;
    case 0x05: // PT_Material (Phase 7B)
        // No per-second counter yet — added when material throughput tracking is needed
        break;
    case 0x06: // PT_Mesh (Phase 7C)
        // No per-second counter yet — geometry throughput tracking deferred
        break;
    default:
        break;
    }
}


// =========================================================
// GOAL D — HOT PATH REDUCTION DIAGNOSTICS
// =========================================================
// Non-allocating hot-path helpers.
// =========================================================

int32 UUELiveSyncSubsystem::
CountActiveGUIDs() const
{
    // Non-allocating count (iterate TMap without temp copies)
    int32 Count = 0;
    for (const auto& Pair : ActorCache)
    {
        if (Pair.Value.IsValid())
            Count++;
    }
    return Count;
}


// =========================================================
// GOAL E — TICK SCHEDULING HARDENING
// =========================================================

void UUELiveSyncSubsystem::
CheckOverloadCondition()
{
    int32 QueueDepth = PacketQueue.Size();
    int32 TransformsPerSec = Stats.TransformsPerSecond.load(std::memory_order_relaxed);
    int32 ReplayEntries = GWorldReplayBuffer.Num();

    // Queue pressure overload
    if (QueueDepth > 96)
    {
        Stats.OverloadWarnings.fetch_add(1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Warning,
            TEXT("[PHASE6I][OVERLOAD] Queue pressure: %d/%d (threshold=96)"),
            QueueDepth, 128);
    }

    // Transform rate overload
    if (TransformsPerSec > 2000)
    {
        Stats.OverloadWarnings.fetch_add(1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Warning,
            TEXT("[PHASE6I][OVERLOAD] Transform rate: %d/s (threshold=2000)"),
            TransformsPerSec);
    }

    // Replay buffer near capacity
    if (ReplayEntries > 3500)
    {
        Stats.OverloadWarnings.fetch_add(1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Warning,
            TEXT("[PHASE6I][OVERLOAD] Replay buffer: %d/4096 (threshold=3500)"),
            ReplayEntries);
    }
}


// =========================================================
// CONSOLE: PHASE 6I STATS
// =========================================================

void UUELiveSyncSubsystem::
ConsolePhase6IStats()
{
    UE_LOG(LogLiveSync, Log,
        TEXT("=== Phase 6I: Performance & Scalability Stats ==="));

    UE_LOG(LogLiveSync, Log, TEXT("  [Goal A: Coalescing]"));
    UE_LOG(LogLiveSync, Log, TEXT("  Coalesced:             %d"),
        Stats.CoalescedTransforms.load(std::memory_order_relaxed));
    UE_LOG(LogLiveSync, Log, TEXT("  Suppressed:            %d"),
        Stats.RedundantTransformsSuppressed.load(std::memory_order_relaxed));
    UE_LOG(LogLiveSync, Log, TEXT("  CoalesceEnabled:       %d"),
        (int32)bPhase6ICoalesceEnabled);

    UE_LOG(LogLiveSync, Log, TEXT("  [Goal B: Replay Buffer]"));
    UE_LOG(LogLiveSync, Log, TEXT("  Entries:               %d"),
        GWorldReplayBuffer.Num());
    UE_LOG(LogLiveSync, Log, TEXT("  UniqueEntries:         %d"),
        CountUniqueReplayEntries());
    UE_LOG(LogLiveSync, Log, TEXT("  MemoryEstimate:        %d KB"),
        EstimateReplayBufferMemory() / 1024);
    UE_LOG(LogLiveSync, Log, TEXT("  PeakMemory:            %d KB"),
        Stats.ReplayPeakMemoryBytes.load(std::memory_order_relaxed) / 1024);
    UE_LOG(LogLiveSync, Log, TEXT("  DuplicateEntries:      %d"),
        Stats.ReplayDuplicateEntries.load(std::memory_order_relaxed));
    UE_LOG(LogLiveSync, Log, TEXT("  StaleEntryRatio:       %d/1000"),
        Stats.ReplayStaleEntryRatio.load(std::memory_order_relaxed));

    UE_LOG(LogLiveSync, Log, TEXT("  [Goal C: Per-Domain Rates]"));
    UE_LOG(LogLiveSync, Log, TEXT("  Transforms/s:          %d"),
        Stats.TransformsPerSecond.load(std::memory_order_relaxed));
    UE_LOG(LogLiveSync, Log, TEXT("  Creates/s:             %d"),
        Stats.CreatesPerSecond.load(std::memory_order_relaxed));
    UE_LOG(LogLiveSync, Log, TEXT("  Deletes/s:             %d"),
        Stats.DeletesPerSecond.load(std::memory_order_relaxed));
    UE_LOG(LogLiveSync, Log, TEXT("  Hierarchy/s:           %d"),
        Stats.HierarchyPacketsPerSecond.load(std::memory_order_relaxed));
    UE_LOG(LogLiveSync, Log, TEXT("  Renames/s:             %d"),
        Stats.RenamePacketsPerSecond.load(std::memory_order_relaxed));
    UE_LOG(LogLiveSync, Log, TEXT("  Visibility/s:          %d"),
        Stats.VisibilityPacketsPerSecond.load(std::memory_order_relaxed));
    UE_LOG(LogLiveSync, Log, TEXT("  Collections/s:         %d"),
        Stats.CollectionPacketsPerSecond.load(std::memory_order_relaxed));

    UE_LOG(LogLiveSync, Log, TEXT("  [Goal D: Tick Timing]"));
    UE_LOG(LogLiveSync, Log, TEXT("  TickProcessTimeUs:     %d"),
        Stats.TickProcessTimeUs.load(std::memory_order_relaxed));
    UE_LOG(LogLiveSync, Log, TEXT("  TickPeakTimeUs:        %d"),
        Stats.TickPeakProcessTimeUs.load(std::memory_order_relaxed));

    UE_LOG(LogLiveSync, Log, TEXT("  [Goal E: Safety]"));
    UE_LOG(LogLiveSync, Log, TEXT("  LongFrameWarnings:     %d"),
        Stats.LongFrameWarnings.load(std::memory_order_relaxed));
    UE_LOG(LogLiveSync, Log, TEXT("  OverloadWarnings:      %d"),
        Stats.OverloadWarnings.load(std::memory_order_relaxed));
    UE_LOG(LogLiveSync, Log, TEXT("  AdaptiveAdj:           %d"),
        Stats.AdaptiveCadenceAdjusted.load(std::memory_order_relaxed));
    UE_LOG(LogLiveSync, Log, TEXT("  DiagnosticsInterval:   %d frames"),
        Phase6IDiagnosticsRunInterval);
    UE_LOG(LogLiveSync, Log, TEXT("  QueueDepth:            %d"),
        PacketQueue.Size());

    UE_LOG(LogLiveSync, Log,
        TEXT("=== End Phase 6I Stats ==="));
}


// =========================================================
// CONSOLE: TOGGLE TRANSFORM COALESCING
// =========================================================

void UUELiveSyncSubsystem::
ConsoleToggleCoalesce(const TArray<FString>& Args)
{
    if (Args.Num() >= 2)
    {
        bPhase6ICoalesceEnabled = (FCString::Atoi(*Args[1]) != 0);
    }
    else
    {
        bPhase6ICoalesceEnabled = !bPhase6ICoalesceEnabled;
    }

    UE_LOG(LogLiveSync, Log,
        TEXT("[PHASE6I] Transform coalescing: %s"),
        bPhase6ICoalesceEnabled ? TEXT("ENABLED") : TEXT("DISABLED"));
}


// =========================================================
// CONSOLE: SET DIAGNOSTICS CADENCE
// =========================================================

void UUELiveSyncSubsystem::
ConsoleSetDiagnosticsCadence(const TArray<FString>& Args)
{
    if (Args.Num() >= 2)
    {
        int32 NewInterval = FMath::Clamp(FCString::Atoi(*Args[1]), 10, 600);
        Phase6IDiagnosticsRunInterval = NewInterval;
        UE_LOG(LogLiveSync, Log,
            TEXT("[PHASE6I] Diagnostics cadence set to %d frames (~%.1fs at 60fps)"),
            NewInterval, NewInterval / 60.0);
    }
    else
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[PHASE6I] Current diagnostics cadence: %d frames (~%.1fs)"),
            Phase6IDiagnosticsRunInterval, Phase6IDiagnosticsRunInterval / 60.0);
    }
}
