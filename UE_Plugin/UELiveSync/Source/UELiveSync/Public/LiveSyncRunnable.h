#pragma once

#include "CoreMinimal.h"

#include "HAL/Runnable.h"

#include "LiveSyncQueue.h"

class FSocket;

class FLiveSyncRunnable : public FRunnable
{
public:

    FLiveSyncRunnable(
        FSocket* InSocket,
        FLiveSyncQueue* InQueue);

    virtual uint32 Run() override;

    virtual void Stop() override;

private:

    FSocket* Socket;

    FLiveSyncQueue* PacketQueue;

    FThreadSafeBool bRunThread;

public:

    FThreadSafeBool bThreadExited;
};