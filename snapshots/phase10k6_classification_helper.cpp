// Phase 10K.6 — FFbxExclusiveClassification struct and ComputeExclusiveClassification helper
// Source: LiveSyncFBXImporter.cpp (lines ~94-156)

// Deterministic phase classification for STALL_SUMMARY.
// Returns largest/second-largest exclusive phase metadata plus the
// classification label (UNRESOLVED / DOMINANT_*/MIXED).
struct FFbxExclusiveClassification
{
    FString LargestPhase = TEXT("UNRESOLVED");
    double LargestPhaseMs = 0.0;
    FString SecondLargestPhase;
    double SecondLargestPhaseMs = 0.0;
    FString Classification = TEXT("UNRESOLVED");
};

static FFbxExclusiveClassification ComputeExclusiveClassification(
    const TMap<FString, double>& PhaseDurations)
{
    FString LargestPhase = TEXT("UNRESOLVED");
    double LargestMs = 0.0;
    FString SecondPhase;
    double SecondMs = 0.0;

    for (const auto& KV : PhaseDurations)
    {
        if (!IsExclusivePhase(KV.Key))
            continue;
        if (KV.Value <= 0.0)
            continue;
        // Deterministic ranking:
        //   1) larger duration sorts first
        //   2) equal duration sorts by phase name lexically ascending
        if (KV.Value > LargestMs ||
            (KV.Value == LargestMs && KV.Key < LargestPhase))
        {
            // Shift current largest to second
            if (LargestMs > 0.0)
            {
                SecondMs = LargestMs;
                SecondPhase = LargestPhase;
            }
            LargestMs = KV.Value;
            LargestPhase = KV.Key;
        }
        else if (KV.Value > SecondMs ||
                 (KV.Value == SecondMs && KV.Key < SecondPhase))
        {
            SecondMs = KV.Value;
            SecondPhase = KV.Key;
        }
    }

    if (LargestMs <= 0.0)
        return { TEXT("UNRESOLVED"), 0.0, FString(), 0.0, TEXT("UNRESOLVED") };

    // Exactly one positive phase => DOMINANT
    if (SecondMs <= 0.0)
        return { LargestPhase, LargestMs, SecondPhase, SecondMs,
                 FString::Printf(TEXT("DOMINANT_%s"), *LargestPhase) };

    // Two or more positive phases: 80 % gate
    if (SecondMs >= LargestMs * 0.8)
        return { LargestPhase, LargestMs, SecondPhase, SecondMs,
                 TEXT("MIXED") };

    return { LargestPhase, LargestMs, SecondPhase, SecondMs,
             FString::Printf(TEXT("DOMINANT_%s"), *LargestPhase) };
}
