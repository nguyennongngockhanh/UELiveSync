#pragma once

// =========================================================
// LiveSyncQueue.h — Bounded MPSC Transform Queue
// =========================================================
// PHASE 5 COMPLETE — RUNTIME CORE FROZEN
//
// Bounded (128 entry) multi-producer single-consumer queue
// bridging network receive thread → game thread.  Drop-oldest
// on overflow.  STABLE and FROZEN as of v0.5.0-stabilized.
//
// Thread ownership: Enqueue from network thread; DequeueBatch
// from game thread only.  Queue size and overflow behaviour are
// validated invariants — changing either requires CVar/visibility
// gate and protocol-version awareness.
//
// See Docs/Architecture/12-core-runtime-invariants.md
// =========================================================

#include "CoreMinimal.h"
#include "Containers/Queue.h"
#include "HAL/IConsoleManager.h"
#include "SyncTypes.h"

#include <atomic>

class FLiveSyncQueue
{
public:

    static constexpr int32 MaxQueueSize = 128;

    void SetStats(
        FLiveSyncStats* InStats)
    {
        Stats = InStats;
    }

    void Enqueue(const FLiveSyncPacket& Packet)
    {
        int32 PrevCount =
            Count.fetch_add(1);

        if (PrevCount >= MaxQueueSize)
        {
            FLiveSyncPacket Dummy;
            Queue.Dequeue(Dummy);
            Count.fetch_sub(1);

            // Track drop
            if (Stats)
            {
                Stats->PacketsDropped.fetch_add(
                    1,
                    std::memory_order_relaxed);
            }

            // Log when queue depth exceeds warn threshold
            // with cooldown to avoid log spam
            static double LastWarnLogTime = 0.0;
            double Now = FPlatformTime::Seconds();

            if (Now - LastWarnLogTime > 5.0)
            {
                LastWarnLogTime = Now;

                static int32 WarnThreshold = 64;

                static IConsoleVariable* WarnCVar =
                    IConsoleManager::Get().
                    FindConsoleVariable(
                        TEXT("UE.LiveSync.QueueWarnThreshold"));

                if (WarnCVar)
                {
                    WarnThreshold =
                        WarnCVar->GetInt();
                }

                if (PrevCount >= WarnThreshold)
                {
                    UE_LOG(
                        LogLiveSync,
                        Warning,
                        TEXT("Packet queue depth %d: dropping oldest packet"),
                        PrevCount);
                }
            }
        }

        Queue.Enqueue(Packet);

        // Track peak queue depth
        if (Stats)
        {
            int32 CurrentSize =
                Count.load(
                    std::memory_order_relaxed);

            Stats->QueueDepthCurrent =
                CurrentSize;

            if (CurrentSize >
                Stats->QueueDepthPeak)
            {
                Stats->QueueDepthPeak =
                    CurrentSize;
            }
        }
    }

    bool Dequeue(FLiveSyncPacket& OutPacket)
    {
        if (Queue.Dequeue(OutPacket))
        {
            Count.fetch_sub(1);

            return true;
        }

        return false;
    }

    void Clear()
    {
        FLiveSyncPacket Dummy;

        while (Queue.Dequeue(Dummy))
        {
            Count.fetch_sub(1);
        }
    }

    int32 Size() const
    {
        return Count.load(
            std::memory_order_relaxed
        );
    }

private:

    TQueue<
        FLiveSyncPacket,
        EQueueMode::Mpsc>

        Queue;

    std::atomic<int32>
        Count{0};

    FLiveSyncStats* Stats = nullptr;
};
