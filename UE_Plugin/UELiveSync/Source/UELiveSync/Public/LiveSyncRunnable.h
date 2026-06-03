#pragma once

// =========================================================
// LiveSyncRunnable.h — Network Receive Thread
// =========================================================
// PHASE 5 COMPLETE — RUNTIME CORE FROZEN
//
// Dedicated FRunnable for the network receive thread.
// STABLE and FROZEN as of v0.5.0-stabilized.
//
// Thread safety: This thread must NEVER access UObject
// pointers or mutate game-thread state.  It enqueues
// packets into FLiveSyncQueue and FLiveSyncPendingAssetQueue
// only.  Socket lifecycle is managed via
// StopNetworkThread (shutdown order: Stop → Shutdown →
// Close → WaitForCompletion → delete → DestroySocket).
//
// On Linux, close() alone does NOT wake blocked recv()/poll().
// Failing to call Shutdown(ReadWrite) before Close will
// DEADLOCK the game thread via WaitForCompletion().
//
// See Docs/Architecture/12-core-runtime-invariants.md
// =========================================================

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

    void SetRecvTimeoutMs(
        int32 InMs);

    bool SendPacket(
        uint8 InPacketType,
        const uint8* InPayload,
        int32 InPayloadSize);

    virtual uint32 Run() override;

    virtual void Stop() override;

private:

    FSocket* Socket;

    FLiveSyncQueue* PacketQueue;

    FLiveSyncStats* StatsRef = nullptr;

    int32 RecvTimeoutMs = 5000;

    std::atomic<bool> bRunThread{false};

public:

    std::atomic<bool> bThreadExited{false};

    std::atomic<double> LastThreadLoopTime{0.0};

    std::atomic<double> LastPacketReceiveTime{0.0};
};