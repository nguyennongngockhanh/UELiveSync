#include "LiveSyncRunnable.h"

#include "Sockets.h"

#include "HAL/PlatformProcess.h"

#include "SyncTypes.h"

#include "HAL/PlatformProcess.h"

#define CHECK_NONGAME_THREAD() \
    check(!IsInGameThread())

extern bool GEnableVerboseSyncLogs;


// =========================================================
// CONSTRUCTOR
// =========================================================

void FLiveSyncRunnable::SetStats(
    FLiveSyncStats* InStats)
{
    StatsRef =
        InStats;
}


FLiveSyncRunnable::FLiveSyncRunnable(

    FSocket* InSocket,

    FLiveSyncQueue* InQueue)

{
    Socket =
        InSocket;

    PacketQueue =
        InQueue;

    bRunThread.store(
        true);
}


// =========================================================
// MAIN THREAD LOOP
// =========================================================

uint32 FLiveSyncRunnable::Run()
{
    uint64 ThreadStartCycles =
        FPlatformTime::Cycles64();

    uint64 LastRecvExitCycles = 0;

    int32 ConsecutiveIdleWaits = 0;

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("NetworkThread: started"));

    // Thread identity assertion: this MUST NOT be the game thread
    CHECK_NONGAME_THREAD();

    while (bRunThread)
    {
        LastThreadLoopTime.store(
            FPlatformTime::Seconds(),
            std::memory_order_relaxed);

        if (!Socket)
        {
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("NetworkThread: Socket became null, exiting"));

            break;
        }

        // =================================================
        // WAIT FOR DATA
        // =================================================

        if (!Socket->Wait(

            ESocketWaitConditions::
            WaitForRead,

            FTimespan::
            FromMilliseconds(
                10)))
        {
            ConsecutiveIdleWaits++;

            // Log periodic idle status every ~1000 waits (10s)
            if (ConsecutiveIdleWaits % 1000 == 1)
            {
                UE_LOG(
                    LogLiveSync,
                    Log,
                    TEXT("NetworkThread: waiting for data "
                         "(idleWaits=%d)"),
                    ConsecutiveIdleWaits);
            }

            // Short-circuit: if Wait has returned false
            // 3× in a row and thread is being asked to
            // stop, exit immediately instead of spinning
            if (ConsecutiveIdleWaits >= 3 &&
                !bRunThread)
            {
                break;
            }

            continue;
        }

        ConsecutiveIdleWaits = 0;

        // =================================================
        // READ HEADER RAW (24 bytes for max V3 header)
        // =================================================

        uint8 HeaderBytes[
            sizeof(FPacketHeaderV3)];

        int32 TotalHeaderRead =
            0;

        while (
            TotalHeaderRead <
            (int32)sizeof(
                FPacketHeaderV3))
        {
            if (!bRunThread)
            {
                UE_LOG(
                    LogLiveSync,
                    Log,
                    TEXT("NetworkThread: header read interrupted "
                         "by stop signal"));
                bThreadExited = true;
                return 0;
            }

            int32 BytesRead =
                0;

            bool bOk =

                Socket->Recv(

                    HeaderBytes +
                    TotalHeaderRead,

                    sizeof(FPacketHeaderV3) -
                    TotalHeaderRead,

                    BytesRead);

            if (!bOk ||
                BytesRead <= 0)
            {
                LastRecvExitCycles =
                    FPlatformTime::Cycles64();

                double ThreadLifetimeMs =
                    FPlatformTime::
                    ToMilliseconds64(
                        LastRecvExitCycles -
                        ThreadStartCycles);

                if (BytesRead == 0)
                {
                    UE_LOG(
                        LogLiveSync,
                        Log,
                        TEXT("NetworkThread: peer disconnected "
                             "(recv=0) after %.2fms"),
                        ThreadLifetimeMs);
                }
                else
                {
                    UE_LOG(
                        LogLiveSync,
                        Warning,
                        TEXT("NetworkThread: socket error during "
                             "header recv (ok=%d bytes=%d) "
                             "after %.2fms"),
                        bOk ? 1 : 0,
                        BytesRead,
                        ThreadLifetimeMs);
                }

                bThreadExited = true;

                return 0;
            }

            TotalHeaderRead +=
                BytesRead;
        }

        // =================================================
        // DETERMINE VERSION
        // =================================================

        uint16 PacketVersion;

        FMemory::Memcpy(
            &PacketVersion,
            HeaderBytes + 4,
            sizeof(uint16));

        // =================================================
        // PARSE HEADER BASED ON VERSION
        // =================================================

        uint32 PacketMagic;
        uint64 PacketSequenceId;
        int32 PacketSize;
        int32 ObjectCount;
        int32 HeaderSize;

        if (PacketVersion >=
            LIVE_SYNC_VERSION_V3)
        {
            FPacketHeaderV3* V3Hdr =
                reinterpret_cast<
                    FPacketHeaderV3*>(
                    HeaderBytes);

            PacketMagic =
                V3Hdr->Magic;

            PacketSequenceId =
                V3Hdr->SequenceId;

            PacketSize =
                V3Hdr->PacketSize;

            ObjectCount =
                V3Hdr->ObjectCount;

            HeaderSize =
                sizeof(FPacketHeaderV3);
        }
        else
        {
            FPacketHeader* V2Hdr =
                reinterpret_cast<
                    FPacketHeader*>(
                    HeaderBytes);

            PacketMagic =
                V2Hdr->Magic;

            PacketSequenceId =
                V2Hdr->SequenceId;

            PacketSize =
                V2Hdr->PacketSize;

            ObjectCount =
                V2Hdr->ObjectCount;

            HeaderSize =
                sizeof(FPacketHeader);
        }

        // =================================================
        // RAW PACKET RECEIVED
        // =================================================

        if (GEnableVerboseSyncLogs)
        {
            FString HexDump;
            for (int32 i = 0; i < HeaderSize; i++)
            {
                HexDump += FString::Printf(
                    TEXT("%02x "), HeaderBytes[i]);
            }

            UE_LOG(
                LogLiveSync,
                Verbose,
                TEXT("Header: %s"), *HexDump);

            UE_LOG(
                LogLiveSync,
                Verbose,
                TEXT("Parsed: magic=0x%08x version=%u "
                     "seq=%llu size=%d obj=%d hdr=%d"),
                PacketMagic,
                PacketVersion,
                PacketSequenceId,
                PacketSize,
                ObjectCount,
                HeaderSize);
        }

        // =================================================
        // VALIDATE MAGIC
        // =================================================

        if (PacketMagic !=
            LIVE_SYNC_MAGIC)
        {
            UE_LOG(
                LogLiveSync,
                Error,
                TEXT("Invalid packet magic"));

            if (StatsRef)
            {
                StatsRef->MalformedPackets.fetch_add(
                    1,
                    std::memory_order_relaxed);
            }

            continue;
        }

        // =================================================
        // VALIDATE VERSION
        // =================================================

        if (PacketVersion !=
            LIVE_SYNC_VERSION
            && PacketVersion !=
            LIVE_SYNC_VERSION_V3
            && PacketVersion !=
            LIVE_SYNC_VERSION_V4
            && PacketVersion !=
            LIVE_SYNC_VERSION_V5)
        {
            UE_LOG(
                LogLiveSync,
                Error,
                TEXT("Protocol version mismatch: "
                     "got %u, expected %u/%u/%u/%u"),
                PacketVersion,
                LIVE_SYNC_VERSION,
                LIVE_SYNC_VERSION_V3,
                LIVE_SYNC_VERSION_V4,
                LIVE_SYNC_VERSION_V5);

            if (StatsRef)
            {
                StatsRef->MalformedPackets.fetch_add(
                    1,
                    std::memory_order_relaxed);
            }

            continue;
        }

        // =================================================
        // VALIDATE PACKET SIZE
        // =================================================

        if (PacketSize <
            HeaderSize)
        {
            UE_LOG(
                LogLiveSync,
                Error,
                TEXT("Invalid packet size: "
                     "got %d, min %d"),
                PacketSize,
                HeaderSize);

            if (StatsRef)
            {
                StatsRef->MalformedPackets.fetch_add(
                    1,
                    std::memory_order_relaxed);
            }

            continue;
        }

        if (PacketSize >
            LIVE_SYNC_MAX_PACKET_SIZE)
        {
            UE_LOG(
                LogLiveSync,
                Error,
                TEXT("Packet too large: "
                     "%d > %d"),
                PacketSize,
                LIVE_SYNC_MAX_PACKET_SIZE);

            if (StatsRef)
            {
                StatsRef->MalformedPackets.fetch_add(
                    1,
                    std::memory_order_relaxed);
            }

            continue;
        }

        // =================================================
        // PAYLOAD SIZE
        // =================================================

        int32 PayloadSize =
            PacketSize -
            HeaderSize;

        // =================================================
        // PAYLOAD SIZE VALIDATION
        // =================================================

        if (PacketVersion ==
            LIVE_SYNC_VERSION)
        {
            int32 ExpectedV2Size =
                ObjectCount *
                LIVE_SYNC_OBJECT_SIZE;

            if (PayloadSize !=
                ExpectedV2Size)
            {
                UE_LOG(
                    LogLiveSync,
                    Warning,
                    TEXT("V2 payload size mismatch: "
                         "got %d, expected %d "
                         "(%d objects × %d)"),
                    PayloadSize,
                    ExpectedV2Size,
                    ObjectCount,
                    LIVE_SYNC_OBJECT_SIZE);

                if (StatsRef)
                {
                    StatsRef->MalformedPackets.fetch_add(
                        1,
                        std::memory_order_relaxed);
                }

                continue;
            }
        }
        else if (PacketVersion >=
                 LIVE_SYNC_VERSION_V3)
        {
            int32 MinV3Size =
                ObjectCount *
                LIVE_SYNC_V3_DELETE_SIZE;

            if (PayloadSize <
                MinV3Size)
            {
                UE_LOG(
                    LogLiveSync,
                    Warning,
                    TEXT("V3 payload too small: "
                         "got %d, need at least %d "
                         "(%d objects × %d)"),
                    PayloadSize,
                    MinV3Size,
                    ObjectCount,
                    LIVE_SYNC_V3_DELETE_SIZE);

                if (StatsRef)
                {
                    StatsRef->MalformedPackets.fetch_add(
                        1,
                        std::memory_order_relaxed);
                }

                continue;
            }
        }

        // =================================================
        // READ PAYLOAD
        // =================================================

        TArray<uint8> Payload;

        Payload.SetNumUninitialized(
            PayloadSize);

        int32 TotalPayloadRead =
            0;

        while (
            TotalPayloadRead <
            PayloadSize)
        {
            if (!bRunThread)
            {
                UE_LOG(
                    LogLiveSync,
                    Log,
                    TEXT("NetworkThread: payload read interrupted "
                         "by stop signal"));
                bThreadExited = true;
                return 0;
            }

            int32 BytesRead =
                0;

            bool bOk =

                Socket->Recv(

                    Payload.GetData() +
                    TotalPayloadRead,

                    PayloadSize -
                    TotalPayloadRead,

                    BytesRead);

            if (!bOk ||
                BytesRead <= 0)
            {
                LastRecvExitCycles =
                    FPlatformTime::Cycles64();

                double ThreadLifetimeMs =
                    FPlatformTime::
                    ToMilliseconds64(
                        LastRecvExitCycles -
                        ThreadStartCycles);

                if (BytesRead == 0)
                {
                    UE_LOG(
                        LogLiveSync,
                        Log,
                        TEXT("NetworkThread: peer disconnected "
                             "(recv=0) during payload after %.2fms"),
                        ThreadLifetimeMs);
                }
                else
                {
                    UE_LOG(
                        LogLiveSync,
                        Warning,
                        TEXT("NetworkThread: socket error during "
                             "payload recv (ok=%d bytes=%d) "
                             "after %.2fms"),
                        bOk ? 1 : 0,
                        BytesRead,
                        ThreadLifetimeMs);
                }

                bThreadExited = true;

                return 0;
            }

            TotalPayloadRead +=
                BytesRead;
        }

        // =================================================
        // BUILD FINAL PACKET
        // =================================================

        FLiveSyncPacket Packet;

        Packet.RawData.
            SetNumUninitialized(
                PacketSize);

        // =================================================
        // COPY FULL RAW HEADER
        // =================================================

        FMemory::Memcpy(
            Packet.RawData.GetData(),
            HeaderBytes,
            HeaderSize);

        // =================================================
        // COPY PAYLOAD
        // =================================================

        FMemory::Memcpy(
            Packet.RawData.GetData() +
            HeaderSize,
            Payload.GetData(),
            PayloadSize);

        // =================================================
        // VERBOSE PAYLOAD DUMP
        // =================================================

        if (GEnableVerboseSyncLogs)
        {
            int32 DumpLen = FMath::Min(
                PayloadSize, 64);

            FString PayloadHex;

            for (int32 i = 0;
                 i < DumpLen; i++)
            {
                PayloadHex +=
                    FString::Printf(
                        TEXT("%02x "),
                        Payload[i]);
            }

            UE_LOG(
                LogLiveSync,
                Verbose,
                TEXT("Payload (%d bytes, "
                     "showing %d): %s"),
                PayloadSize,
                DumpLen,
                *PayloadHex);
        }

        // =================================================
        // RECEIVE TIME
        // =================================================

        Packet.ReceiveTime =

            FPlatformTime::
            Seconds();

        // =================================================
        // ENQUEUE
        // =================================================

        PacketQueue->Enqueue(
            MoveTemp(Packet));

        LastPacketReceiveTime.store(
            FPlatformTime::Seconds(),
            std::memory_order_relaxed);

        if (StatsRef)
        {
            StatsRef->PacketsReceived.fetch_add(
                1,
                std::memory_order_relaxed);

            StatsRef->TotalBytesReceived.fetch_add(
                PacketSize,
                std::memory_order_relaxed);
        }

        // Log first packet and then every 300th packet
        static int PacketLogCounter = 0;

        if (++PacketLogCounter <= 3 ||
            PacketLogCounter % 300 == 1)
        {
            UE_LOG(
                LogLiveSync,
                Log,
                TEXT("NetworkThread: enqueued packet "
                     "(#%d, type=0x%02X, ver=%u, "
                     "size=%d, objs=%d)"),
                PacketLogCounter,
                *((uint8*)(HeaderBytes + 6)),
                PacketVersion,
                PacketSize,
                ObjectCount);
        }
    }

    LastRecvExitCycles =
        FPlatformTime::Cycles64();

    double ThreadLifetimeMs =
        FPlatformTime::
        ToMilliseconds64(
            LastRecvExitCycles -
            ThreadStartCycles);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("NetworkThread: clean exit after %.2fms"),
        ThreadLifetimeMs);

    bThreadExited = true;

    return 0;
}


// =========================================================
// STOP THREAD
// =========================================================

void FLiveSyncRunnable::Stop()
{
    bRunThread =
        false;
}