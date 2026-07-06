// Phase 10K.6 — FFbxTransactionSummary destructor (updated)
// Source: LiveSyncFBXImporter.cpp (lines ~287-345)

    ~FFbxTransactionSummary()
    {
        if (!Stats) return;

        const double Now = FPlatformTime::Seconds();
        const double TotalMs = (Now - HandleImportStart) * 1000.0;

        // Safe fallback for null PhaseDurations
        static const TMap<FString, double> EmptyPhaseDurations;
        const TMap<FString, double>& SafePhaseDurations =
            PhaseDurations ? *PhaseDurations : EmptyPhaseDurations;

        double MeasuredExclusiveMs = 0.0;
        for (const auto& KV : SafePhaseDurations)
        {
            if (IsExclusivePhase(KV.Key))
            {
                MeasuredExclusiveMs += KV.Value;
            }
        }

        const double CoveragePercent =
            TotalMs > 0.0
                ? (MeasuredExclusiveMs / TotalMs) * 100.0
                : 0.0;
        const double UnattributedMs =
            FMath::Max(0.0, TotalMs - MeasuredExclusiveMs);
        const double ExcessMs =
            FMath::Max(0.0, MeasuredExclusiveMs - TotalMs);
        const bool bTimingValid =
            ExcessMs <= 0.5;

        FString LargestPhase = TEXT("UNRESOLVED");
        double LargestPhaseMs = 0.0;
        FString Classification;

        if (!bTimingValid)
        {
            Classification = TEXT("INVALID_OVERLAP");
            LargestPhase = TEXT("UNRESOLVED");
            LargestPhaseMs = 0.0;
        }
        else
        {
            const FFbxExclusiveClassification Result =
                ComputeExclusiveClassification(SafePhaseDurations);
            Classification = Result.Classification;
            LargestPhase = Result.LargestPhase;
            LargestPhaseMs = Result.LargestPhaseMs;
        }

        UE_LOG(LogLiveSync, Log,
            TEXT("[FBX][STALL_SUMMARY] transactionId=%d guid=%s syncId=%d objectName=%s totalMs=%.1f measuredExclusiveMs=%.1f coveragePercent=%.2f largestPhase=%s largestPhaseMs=%.1f unattributedMs=%.1f classification=%s"),
            TransactionId,
            *Guid.ToString(EGuidFormats::Digits),
            SyncId,
            *ObjectName,
            TotalMs,
            MeasuredExclusiveMs,
            CoveragePercent,
            *LargestPhase,
            LargestPhaseMs,
            UnattributedMs,
            *Classification);
    }
