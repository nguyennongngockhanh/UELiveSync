#pragma once

#include "CoreMinimal.h"
#include "Containers/Queue.h"
#include "SyncTypes.h"

#include <atomic>

class FLiveSyncQueue
{
public:

    static constexpr int32 MaxQueueSize = 128;

    void Enqueue(const FLiveSyncPacket& Packet)
    {
        int32 PrevCount =
            Count.fetch_add(
                1);

        if (PrevCount >=
            MaxQueueSize)
        {
            FLiveSyncPacket Dummy;

            Queue.Dequeue(Dummy);

            Count.fetch_sub(1);
        }

        Queue.Enqueue(Packet);
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
};
