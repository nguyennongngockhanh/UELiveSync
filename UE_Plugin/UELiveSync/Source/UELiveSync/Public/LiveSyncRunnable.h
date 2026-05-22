#pragma once

#include "CoreMinimal.h"

#include "HAL/Runnable.h"

#include "LiveSyncQueue.h"

#include <atomic>

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

    std::atomic<bool> bRunThread{false};

public:

    std::atomic<bool> bThreadExited{false};

    std::atomic<double> LastActivityTime{0.0};
};