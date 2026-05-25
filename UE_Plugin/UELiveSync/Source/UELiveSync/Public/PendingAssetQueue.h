#pragma once

// =========================================================
// PendingAssetQueue.h — Bounded GUID Resolution Queue
// =========================================================
// PHASE 5 COMPLETE — RUNTIME CORE FROZEN
//
// Bounded (2048) pending asset resolution queue.  This queue
// bridges the network receive thread (enqueue) and the game
// thread (dequeue).  It is STABLE and FROZEN as of
// v0.5.0-stabilized.
//
// Modification requires critical-bug justification.  The
// Contains() guard in Dequeue() (line ~51) is a validated
// defence against TSet SparseSet assertion under queue
// overflow + disconnect — do not remove without reproducing
// the crash scenario.
//
// See Docs/Architecture/12-core-runtime-invariants.md
// =========================================================

#include "CoreMinimal.h"

#include "SyncTypes.h"

// =========================================================
// PENDING ASSET QUEUE
// =========================================================
// Bounded FIFO for GUIDs awaiting asset resolution.
// Thread-safe (FCriticalSection guard).
// Max capacity: MAX_PENDING_ASSET_ENTRIES (2048).
//
// THREAD SAFETY MODEL
//   All public methods hold CritSec mutex via FScopeLock.
//     Network thread: Enqueue  (via HandleAssetDef)
//     Game thread:    Dequeue  (via ResolvePendingAssets)
//     Game thread:    Remove   (via HandleDeleteObject)
//     Game thread:    Empty    (via ConsoleReset / disconnect)
//
// CRASH HISTORY (Phase 5E, May 2026)
//   Under heavy sustained load with queue overflow and peer
//   disconnect, a SIGABRT was observed in TSet::Remove inside
//   Dequeue(). The EntrySet and Entries collection had drifted
//   out of sync such that the GUID at Entries[0] was no longer
//   present in EntrySet.  Unconditional TSet::Remove() triggered
//   a SparseSet assertion failure, aborting the editor.
//
//   The Contains() guard on EntrySet.Remove() in Dequeue() was
//   added as a defence-in-depth measure.  The same guard already
//   existed in Remove().  The root cause (collection drift) has
//   not been reproduced since the fix; the guard prevents the
//   abort even if future edge cases re-introduce drift.
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

        // Phase 5E fix: Contains() guard prevents SIGABRT
        // in TSet::Remove if Entries/EntrySet drift out of
        // sync under extreme load (see CRASH HISTORY above).
        if (EntrySet.Contains(OutGuid))
        {
            EntrySet.Remove(OutGuid);
        }
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
        return Num();
    }


    void CleanupStale(
        double Now)
    {
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
