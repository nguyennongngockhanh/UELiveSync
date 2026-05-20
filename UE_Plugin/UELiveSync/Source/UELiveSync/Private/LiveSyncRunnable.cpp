#include "LiveSyncRunnable.h"

#include "Sockets.h"

#include "HAL/PlatformProcess.h"

#include "SyncTypes.h"


// =========================================================
// CONSTRUCTOR
// =========================================================

FLiveSyncRunnable::FLiveSyncRunnable(

    FSocket* InSocket,

    TQueue<
        FLiveSyncPacket,
        EQueueMode::Mpsc>* InQueue)

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
                100)))
        {
            continue;
        }

        // =================================================
        // READ HEADER
        // =================================================

        FPacketHeader Header;

        int32 TotalHeaderRead =
            0;

        uint8* HeaderPtr =

            reinterpret_cast<uint8*>(
                &Header);

        while (
            TotalHeaderRead <
            sizeof(FPacketHeader))
        {
            int32 BytesRead =
                0;

            bool bOk =

                Socket->Recv(

                    HeaderPtr +
                    TotalHeaderRead,

                    sizeof(FPacketHeader) -
                    TotalHeaderRead,

                    BytesRead);

            if (!bOk ||
                BytesRead <= 0)
            {
                return 0;
            }

            TotalHeaderRead +=
                BytesRead;
        }

        // =================================================
        // DEBUG HEADER SIZE
        // =================================================

        UE_LOG(
            LogTemp,
            Warning,
            TEXT("Header Size = %d"),
            sizeof(FPacketHeader)
        );

        // =================================================
        // VALIDATE MAGIC
        // =================================================

        if (Header.Magic !=
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

        if (Header.Version !=
            LIVE_SYNC_VERSION)
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

        if (Header.PacketSize <
            sizeof(FPacketHeader))
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

            Header.PacketSize -
            sizeof(FPacketHeader);

        // =================================================
        // PAYLOAD ALIGNMENT CHECK
        // =================================================

        if (PayloadSize %
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

                Header.PacketSize);

        // =================================================
        // COPY HEADER
        // =================================================

        FMemory::Memcpy(

            Packet.RawData.GetData(),

            &Header,

            sizeof(FPacketHeader));

        // =================================================
        // COPY PAYLOAD
        // =================================================

        FMemory::Memcpy(

            Packet.RawData.GetData() +
            sizeof(FPacketHeader),

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
    }

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