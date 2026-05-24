#pragma once

#include "CoreMinimal.h"

#include "SyncTypes.h"

// =========================================================
// PENDING ASSET QUEUE
// =========================================================
// Bounded FIFO for GUIDs awaiting asset resolution.
// Thread-safe (FCriticalSection guard).
// Max capacity: MAX_PENDING_ASSET_ENTRIES (2048).
// =========================================================

class FPendingAssetQueue
{
public:

    bool Enqueue(const FGuid& Guid)
    {
        FScopeLock Lock(&CritSec);

        if (EntrySet.Contains(Guid))
        {
            return true;
        }

        if (Entries.Num() >=
            MAX_PENDING_ASSET_ENTRIES)
        {
            return false;
        }

        Entries.Add(Guid);
        EntrySet.Add(Guid);
        return true;
    }


    bool Dequeue(FGuid& OutGuid)
    {
        FScopeLock Lock(&CritSec);

        if (Entries.Num() == 0)
        {
            return false;
        }

        OutGuid = Entries[0];
        Entries.RemoveAt(0, 1, EAllowShrinking::No);
        EntrySet.Remove(OutGuid);
        return true;
    }


    void Remove(const FGuid& Guid)
    {
        FScopeLock Lock(&CritSec);

        if (!EntrySet.Contains(Guid))
        {
            return;
        }

        Entries.Remove(Guid);
        EntrySet.Remove(Guid);
    }


    bool Contains(const FGuid& Guid) const
    {
        FScopeLock Lock(&CritSec);
        return EntrySet.Contains(Guid);
    }


    int32 Num() const
    {
        FScopeLock Lock(&CritSec);
        return Entries.Num();
    }


    int32 NumUnresolved() const
    {
        return Num();  // All entries in queue are unresolved
    }


    void CleanupStale(
        double Now)
    {
        // Stale entry cleanup is handled by the subsystem
        // via FAssetMetadata timeout checks.
        // This safety-net is intentionally a no-op.
    }


    void Empty()
    {
        FScopeLock Lock(&CritSec);
        Entries.Empty();
        EntrySet.Empty();
    }


    void GetDiagnostics(
        int32& OutPending,
        int32& OutUnresolved) const
    {
        FScopeLock Lock(&CritSec);
        OutPending    = Entries.Num();
        OutUnresolved = Entries.Num();
    }

private:

    TArray<FGuid> Entries;
    TSet<FGuid>   EntrySet;
    mutable FCriticalSection CritSec;
};
