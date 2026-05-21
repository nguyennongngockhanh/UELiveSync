#include "LiveSyncRunnable.h"

#include "Sockets.h"

#include "HAL/PlatformProcess.h"

#include "SyncTypes.h"

extern bool GEnableVerboseSyncLogs;


// =========================================================
// CONSTRUCTOR
// =========================================================

FLiveSyncRunnable::FLiveSyncRunnable(

    FSocket* InSocket,

    FLiveSyncQueue* InQueue)

{
    Socket =
        InSocket;

    PacketQueue =
        InQueue;

    bRunThread =
        true;
}


// =========================================================
// MAIN THREAD LOOP
// =========================================================

uint32 FLiveSyncRunnable::Run()
{
    uint64 ThreadStartCycles =
        FPlatformTime::Cycles64();

    uint64 LastRecvExitCycles = 0;

    while (bRunThread)
    {
        if (!Socket)
        {
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
            continue;
        }

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

                UE_LOG(
                    LogTemp,
                    Log,
                    TEXT("NetworkThread: recv=0 exit after %.2fms"),
                    ThreadLifetimeMs);

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
            static int LogRateLimit = 0;

            if (++LogRateLimit % 300 == 1)
            {
                UE_LOG(
                    LogTemp,
                    Log,
                    TEXT("Raw packet: magic=%x version=%u seq=%llu size=%d obj=%d"),
                    PacketMagic,
                    PacketVersion,
                    PacketSequenceId,
                    PacketSize,
                    ObjectCount);
            }
        }

        // =================================================
        // VALIDATE MAGIC
        // =================================================

        if (PacketMagic !=
            LIVE_SYNC_MAGIC)
        {
            UE_LOG(
                LogTemp,
                Error,
                TEXT("Invalid packet magic"));

            continue;
        }

        // =================================================
        // VALIDATE VERSION
        // =================================================

        if (PacketVersion !=
            LIVE_SYNC_VERSION
            && PacketVersion !=
            LIVE_SYNC_VERSION_V3)
        {
            UE_LOG(
                LogTemp,
                Error,
                TEXT("Protocol version mismatch"));

            continue;
        }

        // =================================================
        // VALIDATE PACKET SIZE
        // =================================================

        if (PacketSize <
            HeaderSize)
        {
            UE_LOG(
                LogTemp,
                Error,
                TEXT("Invalid packet size"));

            continue;
        }

        // =================================================
        // PAYLOAD SIZE
        // =================================================

        int32 PayloadSize =
            PacketSize -
            HeaderSize;

        // =================================================
        // PAYLOAD ALIGNMENT CHECK (V2 only)
        // =================================================

        if (PacketVersion ==
            LIVE_SYNC_VERSION
            && PayloadSize %
            LIVE_SYNC_OBJECT_SIZE
            != 0)
        {
            UE_LOG(
                LogTemp,
                Error,
                TEXT("Payload alignment invalid"));

            continue;
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

                UE_LOG(
                    LogTemp,
                    Log,
                    TEXT("NetworkThread: payload recv=0 exit after %.2fms"),
                    ThreadLifetimeMs);

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

        if (GEnableVerboseSyncLogs)
        {
            static int LogRateLimit = 0;

            if (++LogRateLimit % 300 == 1)
            {
                UE_LOG(
                    LogTemp,
                    Log,
                    TEXT("Enqueued packet"));
            }
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
        LogTemp,
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