#pragma once

#include "CoreMinimal.h"

#include "HAL/Runnable.h"

#include "Containers/Queue.h"

#include "SyncTypes.h"

class FSocket;

class FLiveSyncRunnable : public FRunnable
{
public:

    FLiveSyncRunnable(
        FSocket* InSocket,
        TQueue<
            FLiveSyncPacket,
            EQueueMode::Mpsc>* InQueue);

    virtual uint32 Run() override;

    virtual void Stop() override;

private:

    FSocket* Socket;

    TQueue<
        FLiveSyncPacket,
        EQueueMode::Mpsc>* PacketQueue;

    FThreadSafeBool bRunThread;
};