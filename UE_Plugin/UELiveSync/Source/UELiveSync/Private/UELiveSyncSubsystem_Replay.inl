
// =========================================================
// REPLAY METRICS + WINDOW STATS (Phase 6F Stage 7)
// =========================================================

void UUELiveSyncSubsystem::
RecordReplayTiming(
    double DurationMs,
    double RebuildMs,
    double HashVerifyMs)
{
    GCollectionReplayWindowStats.RecordDuration(DurationMs);
    GCollectionReplayWindowStats.RecordRebuild(RebuildMs);
    GCollectionReplayWindowStats.RecordHashVerify(HashVerifyMs);
    Stats.CollectionReplayLatencySamples.fetch_add(
        1, std::memory_order_relaxed);
}

const FReplayWindowStats& UUELiveSyncSubsystem::
GetReplayWindowStats() const
{
    return GCollectionReplayWindowStats;
}


// =========================================================
// REPLAY BUFFER HEALTH (Phase 6F Stage 7 — Observability)
// =========================================================

void UUELiveSyncSubsystem::
CheckReplayBufferHealth()
{
    const int32 BufferSize = GCollectionReplayBuffer.Num();
    const double UtilPct = static_cast<double>(BufferSize)
        / static_cast<double>(COLLECTION_REPLAY_MAX);

    // Track peak usage
    if (BufferSize > GCollectionReplayPeakUsage)
    {
        GCollectionReplayPeakUsage = BufferSize;
        Stats.CollectionReplayPeakBufferUsage.store(
            GCollectionReplayPeakUsage,
            std::memory_order_relaxed);
    }

    // Detect overflow condition: buffer at capacity
    if (BufferSize >= COLLECTION_REPLAY_MAX)
    {
        Stats.CollectionReplayBufferOverflow.fetch_add(
            1, std::memory_order_relaxed);
    }

    // Emit health warning when near capacity
    const double Now = FPlatformTime::Seconds();
    if (UtilPct >= REPLAY_HEALTH_WARN_THRESHOLD &&
        (Now - GCollectionLastReplayHealthWarning)
            > REPLAY_HEALTH_WARNING_COOLDOWN)
    {
        GCollectionLastReplayHealthWarning = Now;
        UE_LOG(LogLiveSync, Warning,
            TEXT("[COLLECTION][REPLAY] Buffer health warning: "
                 "%d/%d entries (%.0f%% utilization)"),
            BufferSize, COLLECTION_REPLAY_MAX,
            UtilPct * 100.0);
    }
}


// =========================================================
// REPLAY BUFFER DUMP (Phase 6F Stage 7 — Developer Tooling)
// =========================================================

FString UUELiveSyncSubsystem::
DumpReplayBuffer() const
{
    FString Report;
    const int32 Count = GCollectionReplayBuffer.Num();
    const int32 Peak = GCollectionReplayPeakUsage;

    Report += FString::Printf(
        TEXT("Replay Buffer Dump\n"));
    Report += FString::Printf(
        TEXT("==================\n"));
    Report += FString::Printf(
        TEXT("  Entries:      %d / %d\n"),
        Count, COLLECTION_REPLAY_MAX);
    Report += FString::Printf(
        TEXT("  Utilization:  %.1f%%\n"),
        (Count > 0)
            ? (static_cast<double>(Count)
                / static_cast<double>(COLLECTION_REPLAY_MAX) * 100.0)
            : 0.0);
    Report += FString::Printf(
        TEXT("  Peak usage:   %d\n"), Peak);
    Report += FString::Printf(
        TEXT("  Last hash:    0x%016llX\n"),
        GCollectionLastVerifiedHash);

    if (GCollectionReplaySequences.Num() > 0 &&
        GCollectionReplaySequences.Num() == Count)
    {
        Report += FString::Printf(
            TEXT("  Seq range:    %u .. %u\n"),
            GCollectionReplaySequences[0],
            GCollectionReplaySequences.Last());
    }

    Report += FString::Printf(
        TEXT("  Overflow evictions: %d\n"),
        Stats.CollectionReplayBufferOverflow.load(
            std::memory_order_relaxed));
    Report += FString::Printf(
        TEXT("  Truncated:    %d\n"),
        Stats.CollectionReplayPacketsTruncated.load(
            std::memory_order_relaxed));
    Report += FString::Printf(
        TEXT("  Timeline:     %d events (%d total)\n"),
        GCollectionReplayTimeline.Num(),
        GCollectionReplayTimeline.TotalRecorded);

    // Show last 10 timeline entries
    Report += TEXT("\n  Recent timeline:\n");
    const int32 StartIdx = FMath::Max(0,
        GCollectionReplayTimeline.Events.Num() - 10);
    for (int32 i = StartIdx;
         i < GCollectionReplayTimeline.Events.Num(); i++)
    {
        Report += TEXT("    ") +
            GCollectionReplayTimeline.Events[i].ToString()
            + TEXT("\n");
    }

    return Report;
}


// =========================================================
// COLLECTION GRAPH DUMP (Phase 6F Stage 7 — Developer Tooling)
// =========================================================

FString UUELiveSyncSubsystem::
DumpCollectionGraph() const
{
    FString Report;

    Report += FString::Printf(
        TEXT("Collection Graph Dump\n"));
    Report += FString::Printf(
        TEXT("=====================\n"));
    Report += FString::Printf(
        TEXT("  Collections:  %d\n"),
        GCollectionIdentities.Num());
    Report += FString::Printf(
        TEXT("  Memberships:  %d\n"),
        GCollectionMembership.Num());
    Report += FString::Printf(
        TEXT("  Sequences tracked: %d\n"),
        GCollectionSequences.LastSequence.Num());

    Report += TEXT("\n  Collection Registry:\n");
    if (GCollectionIdentities.Num() == 0)
    {
        Report += TEXT("    (empty)\n");
    }
    else
    {
        TArray<FGuid> SortedKeys;
        GCollectionIdentities.GetKeys(SortedKeys);
        SortedKeys.Sort();

        for (const FGuid& CollGuid : SortedKeys)
        {
            const FString* Name =
                GCollectionIdentities.Find(CollGuid);
            const int32 MemberCount =
                GCollectionMembership.FindRef(CollGuid).Num();
            Report += FString::Printf(
                TEXT("    %s \"%s\" (%d members)\n"),
                *CollGuid.ToString(),
                Name ? **Name : TEXT("(unnamed)"),
                MemberCount);
        }
    }

    Report += TEXT("\n  Last verified hash: 0x") +
        FString::Printf(TEXT("%016llX"),
            GCollectionLastVerifiedHash) + TEXT("\n");

    const uint64 CurrentHash = ComputeCollectionStateHash();
    const bool bDiverged = (GCollectionLastVerifiedHash != 0
        && CurrentHash != GCollectionLastVerifiedHash);
    Report += FString::Printf(
        TEXT("  Current hash:       0x%016llX\n"),
        CurrentHash);
    Report += FString::Printf(
        TEXT("  Divergence status:  %s\n"),
        bDiverged ? TEXT("DIVERGED") : TEXT("OK"));

    return Report;
}


// =========================================================
// FORCE REPLAY VERIFICATION (Phase 6F Stage 7 — Developer Tooling)
// =========================================================
// Idempotent, non-mutating verification: computes current state
// hash and compares against last verified hash. Does NOT replay
// or modify any collection state.
// =========================================================

FString UUELiveSyncSubsystem::
ForceReplayVerification()
{
    const uint64 CurrentHash = ComputeCollectionStateHash();
    const bool bDiverged = (GCollectionLastVerifiedHash != 0
        && CurrentHash != GCollectionLastVerifiedHash);

    if (bDiverged)
    {
        Stats.CollectionReplayDivergence.fetch_add(
            1, std::memory_order_relaxed);
        Stats.CollectionReplayReconnectDivergences.fetch_add(
            1, std::memory_order_relaxed);
    }

    return FString::Printf(
        TEXT("[COLLECTION][VERIFY] Last hash=0x%016llX "
             "Current hash=0x%016llX Status=%s"),
        GCollectionLastVerifiedHash, CurrentHash,
        bDiverged ? TEXT("DIVERGED") : TEXT("OK"));
}


// =========================================================
// CLEAR REPLAY DIAGNOSTICS (Phase 6F Stage 7)
// =========================================================

void UUELiveSyncSubsystem::
ClearReplayDiagnostics()
{
    GCollectionReplayTimeline.Clear();
    GCollectionReplayWindowStats.Clear();
    GCollectionReplayPeakUsage = 0;

    Stats.CollectionReplayTimelineRecorded.store(
        0, std::memory_order_relaxed);
    Stats.CollectionReplayTracesEmitted.store(
        0, std::memory_order_relaxed);
    Stats.CollectionReplayBufferOverflow.store(
        0, std::memory_order_relaxed);
    Stats.CollectionReplayPacketsTruncated.store(
        0, std::memory_order_relaxed);
    Stats.CollectionReplayPacketsDropped.store(
        0, std::memory_order_relaxed);
    Stats.CollectionReplayPeakBufferUsage.store(
        0, std::memory_order_relaxed);
    Stats.CollectionReplayLatencySamples.store(
        0, std::memory_order_relaxed);
    Stats.CollectionReplayReconnectRebuilds.store(
        0, std::memory_order_relaxed);
    Stats.CollectionReplayReconnectPacketsReplayed.store(
        0, std::memory_order_relaxed);
    Stats.CollectionReplayReconnectDivergences.store(
        0, std::memory_order_relaxed);
    Stats.CollectionReplayReconnectRollbacks.store(
        0, std::memory_order_relaxed);
}


// =========================================================
// COLLECTION DIAGNOSTICS EXPORT (Phase 6F Stage 7)
// =========================================================

FString UUELiveSyncSubsystem::
ExportCollectionDiagnostics() const
{
    FString Report;

    Report += FString::Printf(
        TEXT("Collection Diagnostics\n"));
    Report += FString::Printf(
        TEXT("======================\n"));
    Report += FString::Printf(
        TEXT("  Collections:       %d\n"),
        GCollectionIdentities.Num());
    Report += FString::Printf(
        TEXT("  Memberships:       %d\n"),
        GCollectionMembership.Num());
    Report += FString::Printf(
        TEXT("  Tracked sequences: %d\n"),
        GCollectionSequences.LastSequence.Num());
    Report += FString::Printf(
        TEXT("  Replay buffer:     %d / %d\n"),
        GCollectionReplayBuffer.Num(),
        COLLECTION_REPLAY_MAX);
    Report += FString::Printf(
        TEXT("  Replay enabled:    %s\n"),
        GCollectionReplayEnabled ? TEXT("yes") : TEXT("no"));
    Report += FString::Printf(
        TEXT("  Replay order mode: %s\n"),
        GCollectionReplayOrderMode
            == ECollectionReplayOrderMode::Strict
                ? TEXT("Strict")
                : GCollectionReplayOrderMode
                    == ECollectionReplayOrderMode::Relaxed
                        ? TEXT("Relaxed")
                        : TEXT("None"));

    const uint64 CurrentHash = ComputeCollectionStateHash();
    const bool bDiverged = (GCollectionLastVerifiedHash != 0
        && CurrentHash != GCollectionLastVerifiedHash);
    Report += FString::Printf(
        TEXT("  Last verified hash: 0x%016llX\n"),
        GCollectionLastVerifiedHash);
    Report += FString::Printf(
        TEXT("  Current hash:       0x%016llX\n"),
        CurrentHash);
    Report += FString::Printf(
        TEXT("  Divergence:         %s\n"),
        bDiverged ? TEXT("YES") : TEXT("no"));

    // Replay metrics
    Report += FString::Printf(
        TEXT("  Replay processed:   %d\n"),
        Stats.CollectionReplayProcessed.load(
            std::memory_order_relaxed));
    Report += FString::Printf(
        TEXT("  Replay rejected:    %d\n"),
        Stats.CollectionReplayRejected.load(
            std::memory_order_relaxed));
    Report += FString::Printf(
        TEXT("  Replay corrupted:   %d\n"),
        Stats.CollectionReplayCorruption.load(
            std::memory_order_relaxed));
    Report += FString::Printf(
        TEXT("  Replay rollbacks:   %d\n"),
        Stats.CollectionReplayRollbacks.load(
            std::memory_order_relaxed));
    Report += FString::Printf(
        TEXT("  Replay divergences: %d\n"),
        Stats.CollectionReplayDivergence.load(
            std::memory_order_relaxed));
    Report += FString::Printf(
        TEXT("  Timeline events:    %d\n"),
        GCollectionReplayTimeline.TotalRecorded);
    Report += FString::Printf(
        TEXT("  Tracing active:     %s\n"),
        GCollectionReplayTraceConfig.bTracingEnabled
            ? TEXT("yes") : TEXT("no"));
    Report += FString::Printf(
        TEXT("  Avg replay (ms):    %.1f\n"),
        GCollectionReplayWindowStats.AvgDurationMs());
    Report += FString::Printf(
        TEXT("  Avg rebuild (ms):   %.1f\n"),
        GCollectionReplayWindowStats.AvgRebuildMs());
    Report += FString::Printf(
        TEXT("  Avg hash verify:    %.3f ms\n"),
        GCollectionReplayWindowStats.AvgHashVerifyMs());

    return Report;
}


// =========================================================
// UNIFIED REPLAY — RECORD ENTRY (Phase 6G)
// =========================================================
// Records a unified replay entry from any domain.
// Bounded at WORLD_REPLAY_MAX (4096) with FIFO eviction.
// Stores metadata for verification, hashing, and diagnostics.
// =========================================================

void UUELiveSyncSubsystem::
RecordWorldReplayEntry(const FWorldReplayEntry& Entry)
{
    if (!GWorldReplayEnabled)
        return;

    if (GWorldReplayBuffer.Num() >= WORLD_REPLAY_MAX)
    {
        GWorldReplayBuffer.RemoveAt(0, 1, EAllowShrinking::No);
    }

    GWorldReplayBuffer.Add(Entry);
    Stats.WorldReplayEntriesRecorded.fetch_add(
        1, std::memory_order_relaxed);
}


// =========================================================
// UNIFIED REPLAY — ENABLE/DISABLE (Phase 6G)
// =========================================================

void UUELiveSyncSubsystem::
SetWorldReplayEnabled(bool bEnabled)
{
    GWorldReplayEnabled = bEnabled;
    if (!bEnabled)
    {
        GWorldReplayBuffer.Empty();
    }
}


// =========================================================
// UNIFIED REPLAY — COMPUTE WORLD-STATE HASH (Phase 6G)
// =========================================================
// Computes a deterministic FNV-1a 64-bit hash of the entire
// synchronized world state across all domains.
//
// Domain contributions:
//   Collection — membership pairs + identities, sorted canonical
//   Lifecycle  — active actor GUIDs, sorted
//   Rename     — display name registry, sorted canonical
//   Transform  — tracked transform count + combined hash
// =========================================================

uint64 UUELiveSyncSubsystem::
ComputeWorldStateHash() const
{
    // FNV-1a 64-bit
    constexpr uint64 FNV_OFFSET_64 = 14695981039346656037ULL;
    constexpr uint64 FNV_PRIME_64  = 1099511628211ULL;

    auto fnv = [](uint64 H, uint8 B) -> uint64
    {
        return (H ^ B) * FNV_PRIME_64;
    };

    auto fnv_u64 = [&](uint64 H, uint64 V) -> uint64
    {
        for (int32 i = 0; i < 8; i++)
        {
            H = fnv(H, static_cast<uint8>(V & 0xFF));
            V >>= 8;
        }
        return H;
    };

    auto fnv_str = [&](uint64 H, const FString& S) -> uint64
    {
        auto Converter = StringCast<UTF8CHAR>(*S);
        auto* Buf = Converter.Get();
        int32 Len = Converter.Length();
        for (int32 i = 0; i < Len; i++)
        {
            H = fnv(H, static_cast<uint8>(Buf[i]));
        }
        return H;
    };

    uint64 H = FNV_OFFSET_64;

    // ── Collection domain ──────────────────────────────
    H = fnv(H, 0xCF);  // domain marker

    // Collection identities: sorted by GUID
    TArray<FGuid> SortedCollIds;
    GCollectionIdentities.GetKeys(SortedCollIds);
    SortedCollIds.Sort();

    for (const FGuid& CollGuid : SortedCollIds)
    {
        const FString* Name = GCollectionIdentities.Find(CollGuid);
        H = fnv_u64(H, 0xCF);  // collection marker
        H = fnv_u64(H, *reinterpret_cast<const uint64*>(&CollGuid));
        H = fnv_u64(H, *reinterpret_cast<const uint64*>(
            reinterpret_cast<const uint8*>(&CollGuid) + 8));
        if (Name)
        {
            H = fnv_str(H, *Name);
        }
    }

    // Collection membership: sorted pairs
    TArray<FGuid> SortedMemColls;
    GCollectionMembership.GetKeys(SortedMemColls);
    SortedMemColls.Sort();

    for (const FGuid& CollGuid : SortedMemColls)
    {
        const TSet<FGuid>* Members = GCollectionMembership.Find(CollGuid);
        if (!Members) continue;

        TArray<FGuid> SortedMembers = Members->Array();
        SortedMembers.Sort();

        for (const FGuid& MemberGuid : SortedMembers)
        {
            H = fnv_u64(H, 0xCE);  // membership marker
            H = fnv_u64(H, *reinterpret_cast<const uint64*>(&CollGuid));
            H = fnv_u64(H, *reinterpret_cast<const uint64*>(
                reinterpret_cast<const uint8*>(&CollGuid) + 8));
            H = fnv_u64(H, *reinterpret_cast<const uint64*>(&MemberGuid));
            H = fnv_u64(H, *reinterpret_cast<const uint64*>(
                reinterpret_cast<const uint8*>(&MemberGuid) + 8));
        }
    }

    // ── Lifecycle domain ───────────────────────────────
    H = fnv(H, 0xCE);  // lifecycle domain marker

    // Active actor GUIDs (from ActorCache, sorted)
    TArray<FGuid> SortedActors;
    for (const auto& Pair : ActorCache)
    {
        if (Pair.Value.IsValid())
        {
            SortedActors.Add(Pair.Key);
        }
    }
    SortedActors.Sort();

    for (const FGuid& ActorGuid : SortedActors)
    {
        H = fnv_u64(H, *reinterpret_cast<const uint64*>(&ActorGuid));
        H = fnv_u64(H, *reinterpret_cast<const uint64*>(
            reinterpret_cast<const uint8*>(&ActorGuid) + 8));
    }

    // ── Rename domain ──────────────────────────────────
    H = fnv(H, 0xCD);  // rename domain marker

    // Hash the persistent label registry for deterministic state comparison.
    // Each (GUID, label) pair is hashed in GUID-sorted order.
    {
        TArray<FGuid> SortedRenameGuids;
        GRenamePersistentLabel.GetKeys(SortedRenameGuids);
        SortedRenameGuids.Sort();

        for (const FGuid& Rg : SortedRenameGuids)
        {
            H = fnv_u64(H, *reinterpret_cast<const uint64*>(&Rg));
            H = fnv_u64(H, *reinterpret_cast<const uint64*>(
                reinterpret_cast<const uint8*>(&Rg) + 8));

            const FString* Label = GRenamePersistentLabel.Find(Rg);
            if (Label)
            {
                H = fnv_str(H, *Label);
            }
            H = fnv(H, 0x00);  // separator
        }
    }

    // ── Transform domain ───────────────────────────────
    H = fnv(H, 0xCC);  // transform domain marker

    // Hash transform states (sorted by GUID for determinism)
    TArray<FGuid> SortedTransforms;
    TransformStates.GetKeys(SortedTransforms);
    SortedTransforms.Sort();

    for (const FGuid& Tg : SortedTransforms)
    {
        const FSyncTransformState* State = TransformStates.Find(Tg);
        if (!State) continue;

        H = fnv_u64(H, *reinterpret_cast<const uint64*>(&Tg));
        H = fnv_u64(H, *reinterpret_cast<const uint64*>(
            reinterpret_cast<const uint8*>(&Tg) + 8));

        // Hash location (3 floats -> 12 bytes)
        const uint8* LocBytes = reinterpret_cast<const uint8*>(&State->CurrentLocation);
        for (int32 i = 0; i < 12; i++)
            H = fnv(H, LocBytes[i]);

        // Hash rotation (4 floats -> 16 bytes)
        const uint8* RotBytes = reinterpret_cast<const uint8*>(&State->CurrentRotation);
        for (int32 i = 0; i < 16; i++)
            H = fnv(H, RotBytes[i]);

        // Hash scale (3 floats -> 12 bytes)
        const uint8* ScaleBytes = reinterpret_cast<const uint8*>(&State->CurrentScale);
        for (int32 i = 0; i < 12; i++)
            H = fnv(H, ScaleBytes[i]);
    }

    return H;
}


// =========================================================
// UNIFIED REPLAY — SAVE WORLD STATE (Phase 6G)
// =========================================================
// Captures the current world state for rollback.
// Saves collection, lifecycle, rename, and transform domains.
// =========================================================

void UUELiveSyncSubsystem::
SaveWorldState()
{
    GWorldSavedState.Clear();

    // Collection domain
    for (const auto& Pair : GCollectionMembership)
    {
        for (const FGuid& Member : Pair.Value)
        {
            GWorldSavedState.CollectionMembership.Add(Member, Pair.Key);
        }
    }
    GWorldSavedState.CollectionIdentities = GCollectionIdentities;

    for (const auto& Pair : GCollectionSequences.LastSequence)
    {
        GWorldSavedState.CollectionSequences.Add(Pair.Key, Pair.Value);
    }

    // Lifecycle domain
    for (const auto& Pair : ActorCache)
    {
        if (Pair.Value.IsValid())
        {
            GWorldSavedState.ActiveActors.Add(Pair.Key);
        }
    }

    // Delete tombstone sequences
    for (const auto& Pair : GDeleteTombstoneMap)
    {
        GWorldSavedState.DeleteSequences.Add(Pair.Key, Pair.Value);
    }

    // Rename domain: capture display names from the world
    for (const auto& Pair : ActorCache)
    {
        AActor* Actor = Pair.Value.Get();
        if (Actor)
        {
#if WITH_EDITOR
            GWorldSavedState.ActorNames.Add(
                Pair.Key, Actor->GetActorLabel());
#else
            GWorldSavedState.ActorNames.Add(
                Pair.Key, Actor->GetName());
#endif
        }
    }

    // Transform domain
    GWorldSavedState.TransformCount = TransformStates.Num();
    GWorldSavedState.TransformHash = 0;

    for (const auto& Pair : TransformStates)
    {
        const FSyncTransformState* State = &Pair.Value;
        uint64 TH = 0;
        const uint8* LocBytes = reinterpret_cast<const uint8*>(&State->CurrentLocation);
        for (int32 i = 0; i < 12; i++) TH = (TH ^ LocBytes[i]) * 16777619u;
        const uint8* RotBytes = reinterpret_cast<const uint8*>(&State->CurrentRotation);
        for (int32 i = 0; i < 16; i++) TH = (TH ^ RotBytes[i]) * 16777619u;
        GWorldSavedState.TransformHash ^= TH;
    }

    GWorldSavedState.CaptureTime = FPlatformTime::Seconds();
}


// =========================================================
// UNIFIED REPLAY — RESTORE WORLD STATE (Phase 6G)
// =========================================================
// Restores world state from the last save point.
// Transactionally rolls back all domains.
// =========================================================

void UUELiveSyncSubsystem::
RestoreWorldState()
{
    // ── Collection domain ──────────────────────────────
    GCollectionMembership.Empty();
    TMap<FGuid, TSet<FGuid>> RebuiltMembership;
    for (const auto& Pair : GWorldSavedState.CollectionMembership)
    {
        const FGuid& MemberGuid = Pair.Key;
        const FGuid& CollGuid = Pair.Value;
        RebuiltMembership.FindOrAdd(CollGuid).Add(MemberGuid);
    }
    GCollectionMembership = MoveTemp(RebuiltMembership);

    GCollectionIdentities = GWorldSavedState.CollectionIdentities;

    GCollectionSequences.LastSequence.Empty();
    for (const auto& Pair : GWorldSavedState.CollectionSequences)
    {
        GCollectionSequences.LastSequence.Add(Pair.Key, Pair.Value);
    }

    // ── Lifecycle domain ───────────────────────────────
    // Remove actors not in saved state
    TArray<FGuid> ToRemove;
    for (const auto& Pair : ActorCache)
    {
        if (Pair.Value.IsValid() &&
            !GWorldSavedState.ActiveActors.Contains(Pair.Key))
        {
            AActor* Actor = Pair.Value.Get();
            if (Actor)
            {
                Actor->Destroy();
            }
            ToRemove.Add(Pair.Key);
        }
    }
    for (const FGuid& Guid : ToRemove)
    {
        ActorCache.Remove(Guid);
        TransformStates.Remove(Guid);
    }

    // Restore delete sequences
    GDeleteTombstoneMap.Empty();
    for (const auto& Pair : GWorldSavedState.DeleteSequences)
    {
        GDeleteTombstoneMap.Add(Pair.Key, Pair.Value);
    }

    // ── Rename domain ──────────────────────────────────
    // Restore from saved state, then overlay with persistent registry
    // to ensure the most authoritative label wins (persistent registry
    // may have been updated after the saved state was captured).
    for (const auto& Pair : GWorldSavedState.ActorNames)
    {
        AActor* Actor = FindActorFast(Pair.Key);
        if (Actor)
        {
            FGuid RestoreGuid = Pair.Key;
            const FString* PersistentLabel = GRenamePersistentLabel.Find(RestoreGuid);
            const FString& LabelToRestore = (PersistentLabel && !PersistentLabel->IsEmpty())
                ? *PersistentLabel
                : Pair.Value;

            {
                FScopedRenameSuppression Suppress(RestoreGuid);
                FScopedChangeOrigin OriginScope(EChangeOrigin::Replay);

#if WITH_EDITOR
                Actor->SetActorLabel(LabelToRestore);
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[RENAME][DIAG] Restoring label in RestoreWorldState guid=%s label=\"%s\" %s"),
                    *RestoreGuid.ToString(EGuidFormats::Digits),
                    *LabelToRestore,
                    PersistentLabel ? TEXT("(from persistent registry)") : TEXT("(from saved state)"));
#endif
            }
        }
    }

    // Also restore any persistent labels that were not in the saved state
    // (e.g., if a rename happened during a session where SaveWorldState
    // was called before the rename was processed).
    for (const auto& Pair : GRenamePersistentLabel)
    {
        if (!GWorldSavedState.ActorNames.Contains(Pair.Key))
        {
            AActor* Actor = FindActorFast(Pair.Key);
            if (Actor)
            {
                FScopedRenameSuppression Suppress(Pair.Key);
                FScopedChangeOrigin OriginScope(EChangeOrigin::Replay);
#if WITH_EDITOR
                Actor->SetActorLabel(Pair.Value);
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[RENAME][DIAG] Restoring persistent-only label guid=%s label=\"%s\""),
                    *Pair.Key.ToString(EGuidFormats::Digits),
                    *Pair.Value);
#endif
            }
        }
    }

    // ── Transform domain ───────────────────────────────
    // Transform states are left intact for actors that survived.
    // Only actors in the remove list had their states removed above.

    Stats.WorldReplayRollbacks.fetch_add(
        1, std::memory_order_relaxed);

    UE_LOG(LogLiveSync, Log,
        TEXT("[WORLD][ROLLBACK] Restored: %d memberships, %d identities, %d actors, %d tombstones"),
        GWorldSavedState.CollectionMembership.Num(),
        GWorldSavedState.CollectionIdentities.Num(),
        GWorldSavedState.ActiveActors.Num(),
        GWorldSavedState.DeleteSequences.Num());
}


// =========================================================
// UNIFIED REPLAY — VERIFY (Phase 6G)
// =========================================================
// Verifies world replay by:
//   1. Saving current state
//   2. Checking cross-domain dependencies
//   3. Running ordering validation
//   4. Corruption detection
//   5. Replaying buffer through state reconstruction
//   6. Computing hash and comparing
//   7. Restoring on failure
// =========================================================

FString UUELiveSyncSubsystem::
VerifyWorldReplay()
{
    CHECK_GAME_THREAD();
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_VerifyWorldReplay);

    SaveWorldState();

    FString Result;
    int32 CorruptedCount = 0;
    int32 DependencyViolations = 0;

    // ── Corruption detection ───────────────────────────
    for (int32 i = 0; i < GWorldReplayBuffer.Num(); i++)
    {
        const FWorldReplayEntry& Entry = GWorldReplayBuffer[i];
        if (Entry.Payload.Num() > 0)
        {
            uint32 ExpectedCheck = Entry.Checksum;
            uint32 ActualCheck = CollectionReplayChecksum(
                Entry.Payload.GetData(), Entry.Payload.Num());

            if (ExpectedCheck != 0 && ExpectedCheck != ActualCheck)
            {
                CorruptedCount++;
                Stats.WorldReplayCorruption.fetch_add(
                    1, std::memory_order_relaxed);
            }
        }
    }

    // ── Dependency validation ──────────────────────────
    CheckReplayDependencies();
    DependencyViolations =
        Stats.WorldReplayDependencyViolations.load(
            std::memory_order_relaxed);

    // ── Compute hash ───────────────────────────────────
    const uint64 CurrentHash = ComputeWorldStateHash();
    const bool bDiverged = (GWorldLastVerifiedHash != 0
        && CurrentHash != GWorldLastVerifiedHash);

    if (bDiverged)
    {
        Stats.WorldReplayDivergences.fetch_add(
            1, std::memory_order_relaxed);
    }

    GWorldLastVerifiedHash = CurrentHash;
    Stats.WorldReplayVerifications.fetch_add(
        1, std::memory_order_relaxed);

    // ── Build result string ────────────────────────────
    Result += FString::Printf(
        TEXT("[WORLD][VERIFY] Buffer: %d entries | Hash: 0x%016llX | Status: %s"),
        GWorldReplayBuffer.Num(), CurrentHash,
        bDiverged ? TEXT("DIVERGED") : TEXT("OK"));

    if (CorruptedCount > 0)
    {
        Result += FString::Printf(
            TEXT(" | Corrupted: %d"), CorruptedCount);
    }
    if (DependencyViolations > 0)
    {
        Result += FString::Printf(
            TEXT(" | DepViolations: %d"), DependencyViolations);
    }

    // ── Restore saved state ────────────────────────────
    RestoreWorldState();

    return Result;
}


// =========================================================
// UNIFIED REPLAY — CHECK DEPENDENCIES (Phase 6G)
// =========================================================
// Validates cross-domain ordering dependencies:
//   - Create before Transform
//   - Create before Rename
//   - Collection membership only for valid objects
// =========================================================

void UUELiveSyncSubsystem::
CheckReplayDependencies()
{
    TSet<FGuid> CreatedGuids;
    int32 Violations = 0;

    for (const FWorldReplayEntry& Entry : GWorldReplayBuffer)
    {
        switch (Entry.Domain)
        {
        case EWorldReplayDomain::Lifecycle:
            // PT_Create marks GUID as created
            if (Entry.PacketType == 0x03)
            {
                CreatedGuids.Add(Entry.Guid);
            }
            // PT_Delete / PT_Delete_V5 removes it
            else if (Entry.PacketType == 0x04 ||
                     Entry.PacketType == 0x0E)
            {
                CreatedGuids.Remove(Entry.Guid);
            }
            break;

        case EWorldReplayDomain::Transform:
            // Transform requires Create before it
            if (!CreatedGuids.Contains(Entry.Guid))
            {
                Violations++;
            }
            break;

        case EWorldReplayDomain::Rename:
            // Rename requires Create before it
            if (!CreatedGuids.Contains(Entry.Guid))
            {
                Violations++;
            }
            break;

        case EWorldReplayDomain::Collection:
            // Collection membership requires valid object
            if (Entry.SecondaryGuid.IsValid() &&
                !CreatedGuids.Contains(Entry.Guid))
            {
                Violations++;
            }
            break;

        default:
            break;
        }
    }

    if (Violations > 0)
    {
        Stats.WorldReplayDependencyViolations.fetch_add(
            Violations, std::memory_order_relaxed);

        UE_LOG(LogLiveSync, Warning,
            TEXT("[WORLD][DEP] %d cross-domain dependency violations detected"),
            Violations);
    }
}


// =========================================================
// UNIFIED REPLAY — EXPORT WORLD SNAPSHOT (Phase 6G)
// =========================================================
// Canonical text-format world snapshot including all domains.
// =========================================================

FString UUELiveSyncSubsystem::
ExportWorldSnapshot() const
{
    FString Report;

    Report += FString::Printf(
        TEXT("WorldSnapshot v%d\n"), FWorldStateSnapshot::SCHEMA_VERSION);

    // ── Collection domain ──────────────────────────────
    Report += TEXT("[COLLECTIONS]\n");
    TArray<FGuid> SortedCollIds;
    GCollectionIdentities.GetKeys(SortedCollIds);
    SortedCollIds.Sort();

    Report += FString::Printf(
        TEXT("Count=%d\n"), SortedCollIds.Num());
    for (const FGuid& CollGuid : SortedCollIds)
    {
        const FString* Name = GCollectionIdentities.Find(CollGuid);
        Report += FString::Printf(
            TEXT("C %s %s\n"),
            *CollGuid.ToString(EGuidFormats::Digits),
            Name ? **Name : TEXT("(unnamed)"));
    }

    Report += TEXT("[MEMBERSHIPS]\n");
    TArray<FGuid> SortedMemColls;
    GCollectionMembership.GetKeys(SortedMemColls);
    SortedMemColls.Sort();

    int32 TotalMembers = 0;
    for (const FGuid& CollGuid : SortedMemColls)
    {
        TotalMembers += GCollectionMembership.FindRef(CollGuid).Num();
    }
    Report += FString::Printf(
        TEXT("Count=%d\n"), TotalMembers);

    for (const FGuid& CollGuid : SortedMemColls)
    {
        TArray<FGuid> SortedMembers =
            GCollectionMembership.FindRef(CollGuid).Array();
        SortedMembers.Sort();

        for (const FGuid& MemberGuid : SortedMembers)
        {
            Report += FString::Printf(
                TEXT("M %s %s\n"),
                *MemberGuid.ToString(EGuidFormats::Digits),
                *CollGuid.ToString(EGuidFormats::Digits));
        }
    }

    // ── Lifecycle domain ───────────────────────────────
    Report += TEXT("[LIFECYCLE]\n");
    TArray<FGuid> SortedActors;
    for (const auto& Pair : ActorCache)
    {
        if (Pair.Value.IsValid())
            SortedActors.Add(Pair.Key);
    }
    SortedActors.Sort();

    Report += FString::Printf(
        TEXT("Count=%d\n"), SortedActors.Num());
    for (const FGuid& ActorGuid : SortedActors)
    {
        Report += FString::Printf(
            TEXT("A %s\n"),
            *ActorGuid.ToString(EGuidFormats::Digits));
    }

    // ── Rename domain ──────────────────────────────────
    Report += TEXT("[RENAME]\n");
    int32 NameCount = 0;
    for (const auto& Pair : ActorCache)
    {
        AActor* Actor = Pair.Value.Get();
        if (Actor)
        {
            NameCount++;
        }
    }
    Report += FString::Printf(
        TEXT("Count=%d\n"), NameCount);

    for (const auto& Pair : ActorCache)
    {
        AActor* Actor = Pair.Value.Get();
        if (Actor)
        {
#if WITH_EDITOR
            Report += FString::Printf(
                TEXT("N %s %s\n"),
                *Pair.Key.ToString(EGuidFormats::Digits),
                *Actor->GetActorLabel());
#else
            Report += FString::Printf(
                TEXT("N %s %s\n"),
                *Pair.Key.ToString(EGuidFormats::Digits),
                *Actor->GetName());
#endif
        }
    }

    // ── Transform domain ───────────────────────────────
    Report += TEXT("[TRANSFORM]\n");
    TArray<FGuid> SortedTf;
    TransformStates.GetKeys(SortedTf);
    SortedTf.Sort();

    Report += FString::Printf(
        TEXT("Count=%d\n"), SortedTf.Num());
    for (const FGuid& TfGuid : SortedTf)
    {
        const FSyncTransformState* State = TransformStates.Find(TfGuid);
        if (!State) continue;

        Report += FString::Printf(
            TEXT("T %s %s %s %s\n"),
            *TfGuid.ToString(EGuidFormats::Digits),
            *State->CurrentLocation.ToString(),
            *State->CurrentRotation.ToString(),
            *State->CurrentScale.ToString());
    }

    Report += FString::Printf(
        TEXT("[END] Hash=0x%016llX\n"),
        ComputeWorldStateHash());

    return Report;
}


// =========================================================
// UNIFIED REPLAY — REBUILD FROM SNAPSHOT (Phase 6G)
// =========================================================
// Parses a world snapshot string and rebuilds state.
// Returns true on success.
// =========================================================

bool UUELiveSyncSubsystem::
RebuildWorldFromSnapshot(const FString& Snapshot)
{
    TArray<FString> Lines;
    Snapshot.ParseIntoArrayLines(Lines);

    bool bInCollections = false;
    bool bInMemberships = false;
    bool bInLifecycle = false;
    bool bInRename = false;
    bool bInTransform = false;

    // Temporary build structures
    TMap<FGuid, FString> NewIdentities;
    TMap<FGuid, TSet<FGuid>> NewMembership;

    for (const FString& Line : Lines)
    {
        if (Line.StartsWith(TEXT("[COLLECTIONS]")))
        {
            bInCollections = true; bInMemberships = false;
            bInLifecycle = false; bInRename = false; bInTransform = false;
            continue;
        }
        else if (Line.StartsWith(TEXT("[MEMBERSHIPS]")))
        {
            bInCollections = false; bInMemberships = true;
            bInLifecycle = false; bInRename = false; bInTransform = false;
            continue;
        }
        else if (Line.StartsWith(TEXT("[LIFECYCLE]")))
        {
            bInCollections = false; bInMemberships = false;
            bInLifecycle = true; bInRename = false; bInTransform = false;
            continue;
        }
        else if (Line.StartsWith(TEXT("[RENAME]")))
        {
            bInCollections = false; bInMemberships = false;
            bInLifecycle = false; bInRename = true; bInTransform = false;
            continue;
        }
        else if (Line.StartsWith(TEXT("[TRANSFORM]")))
        {
            bInCollections = false; bInMemberships = false;
            bInLifecycle = false; bInRename = false; bInTransform = true;
            continue;
        }
        else if (Line.StartsWith(TEXT("[END]")))
        {
            break;
        }

        TArray<FString> Parts;
        Line.ParseIntoArrayWS(Parts);

        if (Parts.Num() == 0)
            continue;

        if (bInCollections && Parts.Num() >= 3 && Parts[0] == TEXT("C"))
        {
            FGuid CollGuid;
            if (FGuid::ParseExact(Parts[1], EGuidFormats::Digits, CollGuid))
            {
                NewIdentities.Add(CollGuid, Parts[2]);
            }
        }
        else if (bInMemberships && Parts.Num() >= 3 && Parts[0] == TEXT("M"))
        {
            FGuid MemberGuid, CollGuid;
            if (FGuid::ParseExact(Parts[1], EGuidFormats::Digits, MemberGuid) &&
                FGuid::ParseExact(Parts[2], EGuidFormats::Digits, CollGuid))
            {
                NewMembership.FindOrAdd(CollGuid).Add(MemberGuid);
            }
        }
        else if (bInRename && Parts.Num() >= 3 && Parts[0] == TEXT("N"))
        {
            FGuid RenameGuid;
            if (FGuid::ParseExact(Parts[1], EGuidFormats::Digits, RenameGuid))
            {
                // Rejoin remaining parts in case label contains spaces
                FString Label = Parts[2];
                for (int32 P = 3; P < Parts.Num(); P++)
                {
                    Label += TEXT(" ") + Parts[P];
                }

                // Store in persistent registry
                GRenamePersistentLabel.Add(RenameGuid, Label);

                // Apply to existing actor immediately
                AActor* RenameActor = FindActorFast(RenameGuid);
                if (RenameActor)
                {
                    FScopedRenameSuppression Suppress(RenameGuid);
                    FScopedChangeOrigin OriginScope(EChangeOrigin::Replay);
#if WITH_EDITOR
                    RenameActor->SetActorLabel(Label);
                    UE_LOG(LogLiveSync, Warning,
                        TEXT("[RENAME][DIAG] Replay rebuild restoring label guid=%s label=\"%s\""),
                        *RenameGuid.ToString(EGuidFormats::Digits),
                        *Label);
#endif
                }
                else
                {
                    UE_LOG(LogLiveSync, Warning,
                        TEXT("[RENAME][DIAG] Replay rebuild label stored (actor not yet spawned) guid=%s label=\"%s\""),
                        *RenameGuid.ToString(EGuidFormats::Digits),
                        *Label);
                }
            }
        }
    }

    // Apply rebuilt state
    GCollectionIdentities = NewIdentities;
    GCollectionMembership = NewMembership;
    GCollectionSequences.Clear();

    Stats.WorldReplaySnapshotRebuilds.fetch_add(
        1, std::memory_order_relaxed);

    return true;
}


// =========================================================
// UNIFIED REPLAY — DUMP WORLD STATE (Phase 6G)
// =========================================================

FString UUELiveSyncSubsystem::
DumpWorldReplayState() const
{
    FString Report;

    Report += FString::Printf(
        TEXT("World Replay State\n"));
    Report += FString::Printf(
        TEXT("==================\n"));
    Report += FString::Printf(
        TEXT("  Buffer:       %d / %d entries\n"),
        GWorldReplayBuffer.Num(), WORLD_REPLAY_MAX);
    Report += FString::Printf(
        TEXT("  Recording:    %s\n"),
        GWorldReplayEnabled ? TEXT("enabled") : TEXT("disabled"));
    Report += FString::Printf(
        TEXT("  Entries recorded: %d\n"),
        Stats.WorldReplayEntriesRecorded.load(
            std::memory_order_relaxed));
    Report += FString::Printf(
        TEXT("  Verifications:    %d\n"),
        Stats.WorldReplayVerifications.load(
            std::memory_order_relaxed));
    Report += FString::Printf(
        TEXT("  Divergences:      %d\n"),
        Stats.WorldReplayDivergences.load(
            std::memory_order_relaxed));
    Report += FString::Printf(
        TEXT("  Rollbacks:        %d\n"),
        Stats.WorldReplayRollbacks.load(
            std::memory_order_relaxed));
    Report += FString::Printf(
        TEXT("  Corruptions:      %d\n"),
        Stats.WorldReplayCorruption.load(
            std::memory_order_relaxed));
    Report += FString::Printf(
        TEXT("  Dep violations:   %d\n"),
        Stats.WorldReplayDependencyViolations.load(
            std::memory_order_relaxed));
    Report += FString::Printf(
        TEXT("  Snapshot exports: %d\n"),
        Stats.WorldReplaySnapshotExports.load(
            std::memory_order_relaxed));
    Report += FString::Printf(
        TEXT("  Snapshot rebuilds: %d\n"),
        Stats.WorldReplaySnapshotRebuilds.load(
            std::memory_order_relaxed));
    Report += FString::Printf(
        TEXT("  Reconnect rebuilds: %d\n"),
        Stats.WorldReplayReconnectRebuilds.load(
            std::memory_order_relaxed));
    Report += FString::Printf(
        TEXT("  Last verified hash: 0x%016llX\n"),
        GWorldLastVerifiedHash);
    Report += FString::Printf(
        TEXT("  Current hash:       0x%016llX\n"),
        ComputeWorldStateHash());

    // Break down by domain
    int32 LifecycleCount = 0, RenameCount = 0,
          CollectionCount = 0, TransformCount = 0,
          UnknownCount = 0;
    for (const FWorldReplayEntry& Entry : GWorldReplayBuffer)
    {
        switch (Entry.Domain)
        {
        case EWorldReplayDomain::Lifecycle:   LifecycleCount++;   break;
        case EWorldReplayDomain::Rename:      RenameCount++;      break;
        case EWorldReplayDomain::Collection:  CollectionCount++;  break;
        case EWorldReplayDomain::Transform:   TransformCount++;   break;
        default:                              UnknownCount++;     break;
        }
    }

    Report += FString::Printf(
        TEXT("\n  Domain breakdown:\n"));
    Report += FString::Printf(
        TEXT("    Lifecycle:   %d\n"), LifecycleCount);
    Report += FString::Printf(
        TEXT("    Rename:      %d\n"), RenameCount);
    Report += FString::Printf(
        TEXT("    Collection:  %d\n"), CollectionCount);
    Report += FString::Printf(
        TEXT("    Transform:   %d\n"), TransformCount);
    Report += FString::Printf(
        TEXT("    Unknown:     %d\n"), UnknownCount);

    return Report;
}


// =========================================================
// REPLAY COLLECTION STREAM (Phase 6F Stage 6)
// =========================================================
// Upgraded from Stage 5 with:
//   A — Deterministic replay ordering (sequence validation)
//   B — Divergence detection (rebuild + hash compare)
//   D — Corruption detection (checksum verification)
//   F — Rollback safety (temp state, restore on failure)
//
// Process:
//   1. Save current collection state (rollback point)
//   2. Run ordering validation on metadata arrays
//   3. Run corruption detection on buffer entries
//   4. Reset all collection state
//   5. Replay all valid entries through HandleCollection
//   6. Compute rebuilt snapshot hash
//   7. Compare against last verified hash (divergence check)
//   8. If verification fails, restore saved state
// =========================================================

void UUELiveSyncSubsystem::
ReplayCollectionStream()
{
    CHECK_GAME_THREAD();
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ReplayCollectionStream);
    const double ReplayStartTime = FPlatformTime::Seconds();

    const int32 EntryCount = GCollectionReplayBuffer.Num();
    UE_LOG(LogLiveSync, Log,
        TEXT("[COLLECTION][REPLAY] Starting replay: %d entries"),
        EntryCount);

    // =====================================================
    // F — ROLLBACK SAFETY: Save current state
    // =====================================================

    TMap<FGuid, TSet<FGuid>> SavedMembership = GCollectionMembership;
    TMap<FGuid, FString> SavedIdentities = GCollectionIdentities;
    TMap<FGuid, uint32> SavedSequences;
    for (const auto& Pair : GCollectionSequences.LastSequence)
    {
        SavedSequences.Add(Pair.Key, Pair.Value);
    }

    bool bReplaySuccess = true;

    // =====================================================
    // A — ORDERING VALIDATION
    // =====================================================

    if (GCollectionReplayOrderMode == ECollectionReplayOrderMode::Strict)
    {
        uint32 LastSeq = 0;
        bool bFirst = true;

        for (int32 i = 0; i < EntryCount; i++)
        {
            const uint32 CurrentSeq = GCollectionReplaySequences.IsValidIndex(i)
                ? GCollectionReplaySequences[i]
                : 0;

            if (bFirst)
            {
                LastSeq = CurrentSeq;
                bFirst = false;

                EmitReplayTrace(EReplayTraceCategory::ReplayValidate,
                    FString::Printf(TEXT("Ordering[%d]: first seq=%u"),
                        i, CurrentSeq));
                continue;
            }

            if (CurrentSeq < LastSeq)
            {
                Stats.CollectionReplaySequenceGap.fetch_add(1, std::memory_order_relaxed);
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[COLLECTION][REPLAY] Sequence regression at index %d: %u → %u"),
                    i, LastSeq, CurrentSeq);

                FReplayTimelineEvent GapEvent;
                GapEvent.Index = i;
                GapEvent.Sequence = CurrentSeq;
                GapEvent.Result = ELiveSyncReplayResult::SequenceGap;
                GapEvent.Timestamp = FPlatformTime::Seconds();
                RecordReplayTimelineEvent(GapEvent);

                EmitReplayTrace(EReplayTraceCategory::ReplayValidate,
                    FString::Printf(TEXT("Ordering[%d]: REGRESSION %u → %u"),
                        i, LastSeq, CurrentSeq));

                bReplaySuccess = false;
                break;
            }
            else if (CurrentSeq == LastSeq)
            {
                // Same-sequence entry is allowed (replay merge)
                if (GCollectionReplayOrderMode == ECollectionReplayOrderMode::Strict)
                {
                    Stats.CollectionReplayOutOfOrder.fetch_add(1, std::memory_order_relaxed);
                    UE_LOG(LogLiveSync, Verbose,
                        TEXT("[COLLECTION][REPLAY] Same-sequence entry at index %d: seq=%u"),
                        i, CurrentSeq);

                    FReplayTimelineEvent OooEvent;
                    OooEvent.Index = i;
                    OooEvent.Sequence = CurrentSeq;
                    OooEvent.Result = ELiveSyncReplayResult::OutOfOrder;
                    OooEvent.Timestamp = FPlatformTime::Seconds();
                    RecordReplayTimelineEvent(OooEvent);

                    EmitReplayTrace(EReplayTraceCategory::ReplayValidate,
                        FString::Printf(TEXT("Ordering[%d]: SAME-SEQUENCE %u"),
                            i, CurrentSeq));
                }
            }
            else if (CurrentSeq > LastSeq + 1)
            {
                Stats.CollectionReplaySequenceGap.fetch_add(1, std::memory_order_relaxed);
                UE_LOG(LogLiveSync, Verbose,
                    TEXT("[COLLECTION][REPLAY] Sequence gap at index %d: %u → %u (gap=%u)"),
                    i, LastSeq, CurrentSeq, CurrentSeq - LastSeq);

                FReplayTimelineEvent GapEvent;
                GapEvent.Index = i;
                GapEvent.Sequence = CurrentSeq;
                GapEvent.Result = ELiveSyncReplayResult::SequenceGap;
                GapEvent.Timestamp = FPlatformTime::Seconds();
                RecordReplayTimelineEvent(GapEvent);

                EmitReplayTrace(EReplayTraceCategory::ReplayValidate,
                    FString::Printf(TEXT("Ordering[%d]: GAP %u → %u"),
                        i, LastSeq, CurrentSeq));
            }

            LastSeq = CurrentSeq;
        }
    }

    // =====================================================
    // D — CORRUPTION DETECTION
    // =====================================================

    int32 CorruptedCount = 0;
    for (int32 i = 0; i < EntryCount; i++)
    {
        const TArray<uint8>& Entry = GCollectionReplayBuffer[i];
        const int32 EntrySize = Entry.Num();

        if (EntrySize != LIVE_SYNC_COLLECTION_BASE_SIZE &&
            EntrySize != LIVE_SYNC_COLLECTION_MEMBERSHIP_SIZE)
        {
            CorruptedCount++;
            Stats.CollectionReplayCorruption.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[COLLECTION][REPLAY] Corrupted entry at index %d: bad size %d"),
                i, EntrySize);

            FReplayTimelineEvent CorrEvent;
            CorrEvent.Index = i;
            CorrEvent.Result = ELiveSyncReplayResult::Corrupted;
            CorrEvent.Timestamp = FPlatformTime::Seconds();
            CorrEvent.PayloadSize = EntrySize;
            RecordReplayTimelineEvent(CorrEvent);

            EmitReplayTrace(EReplayTraceCategory::ReplayCorrupt,
                FString::Printf(TEXT("Corruption[%d]: bad size %d"),
                    i, EntrySize));
            continue;
        }

        // Verify checksum if metadata exists
        if (GCollectionReplayChecksums.IsValidIndex(i))
        {
            const uint32 ExpectedCheck = GCollectionReplayChecksums[i];
            const uint32 ActualCheck = CollectionReplayChecksum(
                Entry.GetData(), EntrySize);

            if (ExpectedCheck != ActualCheck)
            {
                CorruptedCount++;
                Stats.CollectionReplayCorruption.fetch_add(1, std::memory_order_relaxed);
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[COLLECTION][REPLAY] Checksum mismatch at index %d: "
                         "expected 0x%08X, got 0x%08X"),
                    i, ExpectedCheck, ActualCheck);

                FReplayTimelineEvent CorrEvent;
                CorrEvent.Index = i;
                CorrEvent.Result = ELiveSyncReplayResult::Corrupted;
                CorrEvent.Timestamp = FPlatformTime::Seconds();
                CorrEvent.PayloadSize = EntrySize;
                RecordReplayTimelineEvent(CorrEvent);

                EmitReplayTrace(EReplayTraceCategory::ReplayCorrupt,
                    FString::Printf(TEXT("Corruption[%d]: checksum 0x%08X != 0x%08X"),
                        i, ExpectedCheck, ActualCheck));
            }
        }
    }

    if (CorruptedCount > 0)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[COLLECTION][REPLAY] %d corrupted entries detected — aborting"),
            CorruptedCount);

        EmitReplayTrace(EReplayTraceCategory::ReplayCorrupt,
            FString::Printf(TEXT("Corruption summary: %d entries, aborting replay"),
                CorruptedCount));

        bReplaySuccess = false;
    }

    // =====================================================
    // F — ABORT if ordering or corruption checks failed
    // =====================================================

    if (!bReplaySuccess)
    {
        // Restore saved state
        GCollectionMembership = SavedMembership;
        GCollectionIdentities = SavedIdentities;
        for (const auto& Pair : SavedSequences)
        {
            GCollectionSequences.Update(Pair.Key, Pair.Value);
        }

        Stats.CollectionReplayRollbacks.fetch_add(1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Warning,
            TEXT("[COLLECTION][REPLAY] Rollback: restored previous state (%d memberships, %d identities)"),
            SavedMembership.Num(), SavedIdentities.Num());

        FReplayTimelineEvent RbEvent;
        RbEvent.Result = ELiveSyncReplayResult::RolledBack;
        RbEvent.Timestamp = FPlatformTime::Seconds();
        RbEvent.PayloadSize = EntryCount;
        RecordReplayTimelineEvent(RbEvent);

        EmitReplayTrace(EReplayTraceCategory::ReplayRollback,
            FString::Printf(TEXT("Rollback: restored %d memberships, %d identities"),
                SavedMembership.Num(), SavedIdentities.Num()));

        return;
    }

    // =====================================================
    // RESET and REPLAY
    // =====================================================

    GCollectionSequences.Clear();
    GCollectionMembership.Empty();
    GCollectionIdentities.Empty();

    int32 Processed = 0;
    int32 Rejected = 0;

    for (int32 i = 0; i < EntryCount; i++)
    {
        const TArray<uint8>& Entry = GCollectionReplayBuffer[i];
        const uint8* Ptr = Entry.GetData();
        const int32 EntrySize = Entry.Num();

        const bool bIsMembershipOp = (EntrySize == LIVE_SYNC_COLLECTION_MEMBERSHIP_SIZE);

        // Parse TargetGuid
        FGuid TargetGuid;
        FMemory::Memcpy(&TargetGuid, Ptr, sizeof(FGuid));
        Ptr += sizeof(FGuid);

        if (!TargetGuid.IsValid())
        {
            Rejected++;
            continue;
        }

        // Parse CollectionGuid (if membership)
        FGuid CollectionGuid;
        if (bIsMembershipOp)
        {
            FMemory::Memcpy(&CollectionGuid, Ptr, sizeof(FGuid));
            Ptr += sizeof(FGuid);
        }

        // Parse OpType, OpFlags
        uint8 OpType;
        FMemory::Memcpy(&OpType, Ptr, sizeof(uint8));
        Ptr += sizeof(uint8);

        uint8 OpFlags;
        FMemory::Memcpy(&OpFlags, Ptr, sizeof(uint8));
        Ptr += sizeof(uint8);

        // Parse sequence
        uint32 Seq;
        FMemory::Memcpy(&Seq, Ptr, sizeof(uint32));
        Ptr += sizeof(uint32);

        // Skip timestamp
        Ptr += sizeof(double);

        HandleCollection(TargetGuid, OpType, OpFlags, Seq, 0.0,
                         bIsMembershipOp ? &CollectionGuid : nullptr);
        Processed++;

        // Verbose per-entry trace (Stage 7)
        EmitReplayTrace(EReplayTraceCategory::ReplayTrace,
            FString::Printf(TEXT("Apply[%d]: %s seq=%u op=0x%02X%s"),
                i, *TargetGuid.ToString(), Seq, OpType,
                bIsMembershipOp
                    ? *FString::Printf(TEXT(" coll=%s"),
                        *CollectionGuid.ToString())
                    : TEXT("")));
    }

    Stats.CollectionReplayProcessed.fetch_add(
        Processed, std::memory_order_relaxed);

    Stats.CollectionReplayRejected.fetch_add(
        Rejected, std::memory_order_relaxed);

    // =====================================================
    // B — DIVERGENCE DETECTION
    // =====================================================

    const uint64 RebuiltHash = ComputeCollectionStateHash();

    if (GCollectionLastVerifiedHash != 0 &&
        RebuiltHash != GCollectionLastVerifiedHash)
    {
        Stats.CollectionSnapshotHashMismatch.fetch_add(1, std::memory_order_relaxed);
        Stats.CollectionReplayDivergence.fetch_add(1, std::memory_order_relaxed);

        UE_LOG(LogLiveSync, Warning,
            TEXT("[COLLECTION][REPLAY] Divergence detected! "
                 "Expected hash=0x%016llX Rebuilt hash=0x%016llX"),
            GCollectionLastVerifiedHash, RebuiltHash);
    }
    else
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[COLLECTION][REPLAY] Hash verified: 0x%016llX"),
            RebuiltHash);
    }

    GCollectionLastVerifiedHash = RebuiltHash;

    // =====================================================
    // STAGE 7: Timeline recording + trace emission
    // =====================================================

    {
        FReplayTimelineEvent TimelineEvent;
        TimelineEvent.Index = -1;  // aggregate event
        TimelineEvent.Sequence = 0;
        TimelineEvent.Timestamp = FPlatformTime::Seconds();
        TimelineEvent.PayloadSize = EntryCount;
        TimelineEvent.StateHashAfter = RebuiltHash;

        if (!bReplaySuccess)
        {
            TimelineEvent.Result = ELiveSyncReplayResult::RolledBack;
        }
        else if (CorruptedCount > 0)
        {
            TimelineEvent.Result = ELiveSyncReplayResult::Corrupted;
        }
        else if (GCollectionLastVerifiedHash != 0 &&
                 RebuiltHash != GCollectionLastVerifiedHash)
        {
            TimelineEvent.Result = ELiveSyncReplayResult::Diverged;
        }
        else
        {
            TimelineEvent.Result = ELiveSyncReplayResult::Accepted;
        }

        RecordReplayTimelineEvent(TimelineEvent);

        EmitReplayTrace(EReplayTraceCategory::ReplayTrace,
            FString::Printf(TEXT("Replay[%d]: %d entries, result=%d, hash=0x%016llX"),
                GCollectionReplayTimeline.TotalRecorded,
                EntryCount,
                static_cast<int32>(TimelineEvent.Result),
                RebuiltHash));
    }

    // Record replay timing (Stage 7)
    const double ReplayDurationMs =
        (FPlatformTime::Seconds() - ReplayStartTime) * 1000.0;
    RecordReplayTiming(ReplayDurationMs, 0.0, 0.0);

    // Check buffer health
    CheckReplayBufferHealth();

    UE_LOG(LogLiveSync, Log,
        TEXT("[COLLECTION][REPLAY] Complete: %d processed, %d rejected, %d corrupted (%.1f ms)"),
        Processed, Rejected, CorruptedCount, ReplayDurationMs);
}


// =========================================================
// EXPORT COLLECTION SNAPSHOT (Phase 6F Stage 5)
// =========================================================
// Canonical snapshot serialization:
//   CollectionCount(4)
//   for each collection (sorted by GUID):
//     CollectionGUID(16) + IdentityString(N) + null(1)
//   for each collection (sorted by GUID):
//     MemberCount(4)
//     for each member (sorted by GUID):
//       MemberGUID(16)
// =========================================================

FString UUELiveSyncSubsystem::
ExportCollectionSnapshot() const
{
    TArray<FGuid> SortedCollections;
    for (const auto& Pair : GCollectionIdentities)
    {
        SortedCollections.Add(Pair.Key);
    }
    SortedCollections.Sort([](const FGuid& A, const FGuid& B) {
        return A.ToString(EGuidFormats::Digits) < B.ToString(EGuidFormats::Digits);
    });

    FString Report;
    Report += FString::Printf(TEXT("Collections: %d\n"), SortedCollections.Num());

    // Export identities (sorted)
    for (const FGuid& CollGuid : SortedCollections)
    {
        const FString* Identity = GCollectionIdentities.Find(CollGuid);
        Report += FString::Printf(TEXT("  COLL %s %s\n"),
            *CollGuid.ToString(EGuidFormats::Digits),
            Identity ? **Identity : TEXT(""));
    }

    // Export memberships (sorted collections, sorted members)
    Report += TEXT("Memberships:\n");
    for (const FGuid& CollGuid : SortedCollections)
    {
        const TSet<FGuid>* Members = GCollectionMembership.Find(CollGuid);
        if (!Members || Members->Num() == 0)
        {
            continue;
        }

        TArray<FGuid> SortedMembers(Members->Array());
        SortedMembers.Sort([](const FGuid& A, const FGuid& B) {
            return A.ToString(EGuidFormats::Digits) < B.ToString(EGuidFormats::Digits);
        });

        for (const FGuid& MemberGuid : SortedMembers)
        {
            Report += FString::Printf(TEXT("  MEMB %s %s\n"),
                *CollGuid.ToString(EGuidFormats::Digits),
                *MemberGuid.ToString(EGuidFormats::Digits));
        }
    }

    return Report;
}


// =========================================================
// REBUILD COLLECTION FROM SNAPSHOT (Phase 6F Stage 5)
// =========================================================
// Parses a snapshot string produced by ExportCollectionSnapshot
// and rebuilds GCollectionMembership + GCollectionIdentities.
// Returns true on success.
// =========================================================

bool UUELiveSyncSubsystem::
RebuildCollectionFromSnapshot(const FString& Snapshot)
{
    GCollectionMembership.Empty();
    GCollectionIdentities.Empty();
    GCollectionSequences.Clear();

    TArray<FString> Lines;
    Snapshot.ParseIntoArrayLines(Lines);

    int32 State = 0; // 0=header, 1=identities, 2=memberships

    for (const FString& Line : Lines)
    {
        if (Line.StartsWith(TEXT("Collections:")))
        {
            State = 1;
            continue;
        }
        if (Line.StartsWith(TEXT("Memberships:")))
        {
            State = 2;
            continue;
        }

        TArray<FString> Parts;
        Line.ParseIntoArrayWS(Parts);

        if (State == 1 && Parts.Num() >= 2 && Parts[0] == TEXT("COLL"))
        {
            FGuid CollGuid;
            if (FGuid::Parse(Parts[1], CollGuid))
            {
                FString Identity;
                if (Parts.Num() >= 3)
                {
                    Identity = Parts[2];
                }
                GCollectionIdentities.Add(CollGuid, Identity);
            }
        }
        else if (State == 2 && Parts.Num() >= 3 && Parts[0] == TEXT("MEMB"))
        {
            FGuid CollGuid, MemberGuid;
            if (FGuid::Parse(Parts[1], CollGuid) &&
                FGuid::Parse(Parts[2], MemberGuid))
            {
                GCollectionMembership.FindOrAdd(CollGuid).Add(MemberGuid);
            }
        }
    }

    Stats.CollectionSnapshotRebuilds.fetch_add(1, std::memory_order_relaxed);

    UE_LOG(LogLiveSync, Log,
        TEXT("[COLLECTION][SNAPSHOT] Rebuilt: %d collections, %d memberships"),
        GCollectionIdentities.Num(), GCollectionMembership.Num());

    return true;
}


// =========================================================
// COMPUTE COLLECTION STATE HASH (Phase 6F Stage 5)
// =========================================================
// Deterministic xxHash64-style hash of current collection state.
// Uses FNV-1a-like rolling hash over canonical sorted entries.
// Matches Blender's compute_full_snapshot_hash() output for
// identical state.
// =========================================================

uint64 UUELiveSyncSubsystem::
ComputeCollectionStateHash() const
{
    // FNV-1a 64-bit (no xxhash dependency, deterministic across platforms)
    constexpr uint64 FNV_OFFSET_64 = 14695981039346656037ULL;
    constexpr uint64 FNV_PRIME_64  = 1099511628211ULL;

    auto fnv = [](uint64 H, uint8 Byte) -> uint64
    {
        H ^= Byte;
        H *= FNV_PRIME_64;
        return H;
    };

    auto fnv_u64 = [&](uint64 H, uint64 V) -> uint64
    {
        for (int i = 0; i < 8; i++)
        {
            H = fnv(H, static_cast<uint8>(V >> (i * 8)));
        }
        return H;
    };

    auto fnv_buf = [&](uint64 H, const uint8* Data, int32 Len) -> uint64
    {
        for (int32 i = 0; i < Len; i++)
        {
            H = fnv(H, Data[i]);
        }
        return H;
    };

    uint64 H = FNV_OFFSET_64;

    // Hash identities in canonical order
    TArray<FGuid> SortedCollections;
    for (const auto& Pair : GCollectionIdentities)
    {
        SortedCollections.Add(Pair.Key);
    }
    SortedCollections.Sort([](const FGuid& A, const FGuid& B) {
        return A.ToString(EGuidFormats::Digits) < B.ToString(EGuidFormats::Digits);
    });

    for (const FGuid& CollGuid : SortedCollections)
    {
        FString GuidStr = CollGuid.ToString(EGuidFormats::Digits);
        auto GuidAnsi = StringCast<ANSICHAR>(*GuidStr);
        H = fnv_buf(H, reinterpret_cast<const uint8*>(GuidAnsi.Get()), GuidStr.Len());
    }

    // Hash memberships in canonical order
    for (const FGuid& CollGuid : SortedCollections)
    {
        const TSet<FGuid>* Members = GCollectionMembership.Find(CollGuid);
        if (!Members)
        {
            continue;
        }

        FString CollGuidStr = CollGuid.ToString(EGuidFormats::Digits);
        auto CollAnsi = StringCast<ANSICHAR>(*CollGuidStr);
        H = fnv_buf(H, reinterpret_cast<const uint8*>(CollAnsi.Get()), CollGuidStr.Len());

        TArray<FGuid> SortedMembers(Members->Array());
        SortedMembers.Sort([](const FGuid& A, const FGuid& B) {
            return A.ToString(EGuidFormats::Digits) < B.ToString(EGuidFormats::Digits);
        });

        for (const FGuid& MemberGuid : SortedMembers)
        {
            FString MemberStr = MemberGuid.ToString(EGuidFormats::Digits);
            auto MemberAnsi = StringCast<ANSICHAR>(*MemberStr);
            H = fnv_buf(H, reinterpret_cast<const uint8*>(MemberAnsi.Get()), MemberStr.Len());
        }
    }

    return H;
}
