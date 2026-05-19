#pragma once

#include "CoreMinimal.h"
#include "Containers/Queue.h"
#include "SyncTypes.h"

class FLiveSyncQueue
{
public:

    void Enqueue(const FLiveSyncPacket& Packet)
    {
        Queue.Enqueue(Packet);
    }

    bool Dequeue(FLiveSyncPacket& OutPacket)
    {
        return Queue.Dequeue(OutPacket);
    }

private:

    TQueue<FLiveSyncPacket, EQueueMode::Mpsc>
        Queue;
};