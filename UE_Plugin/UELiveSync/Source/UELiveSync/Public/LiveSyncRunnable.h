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

    void SetStats(
        FLiveSyncStats* InStats);

    virtual uint32 Run() override;

    virtual void Stop() override;

private:

    FSocket* Socket;

    FLiveSyncQueue* PacketQueue;

    FLiveSyncStats* StatsRef = nullptr;

    std::atomic<bool> bRunThread{false};

public:

    std::atomic<bool> bThreadExited{false};

    std::atomic<double> LastThreadLoopTime{0.0};

    std::atomic<double> LastPacketReceiveTime{0.0};
};