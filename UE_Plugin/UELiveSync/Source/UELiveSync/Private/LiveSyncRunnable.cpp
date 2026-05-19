#include "LiveSyncRunnable.h"

#include "Sockets.h"

#include "HAL/PlatformProcess.h"

FLiveSyncRunnable::FLiveSyncRunnable(
    FSocket* InSocket,
    TQueue<FLiveSyncPacket,
    EQueueMode::Mpsc>* InQueue)

{
    Socket = InSocket;

    PacketQueue = InQueue;

    bRunThread = true;
}

uint32 FLiveSyncRunnable::Run()
{
    while (bRunThread)
    {
        if (!Socket)
        {
            break;
        }

        // wait for data
        if (!Socket->Wait(
            ESocketWaitConditions::WaitForRead,
            FTimespan::FromMilliseconds(100)))
        {
            continue;
        }

        // =========================
        // READ HEADER
        // =========================

        FPacketHeader Header;

        int32 TotalHeaderRead = 0;

        uint8* HeaderPtr =
            reinterpret_cast<uint8*>(
                &Header);

        while (TotalHeaderRead <
            sizeof(FPacketHeader))
        {
            int32 BytesRead = 0;

            bool bOk =
                Socket->Recv(
                    HeaderPtr +
                    TotalHeaderRead,

                    sizeof(FPacketHeader) -
                    TotalHeaderRead,

                    BytesRead);

            if (!bOk || BytesRead <= 0)
            {
                return 0;
            }

            TotalHeaderRead +=
                BytesRead;
        }

        // validate magic
        if (Header.Magic !=
            LIVE_SYNC_MAGIC)
        {
            UE_LOG(LogTemp, Error,
                TEXT("Invalid packet magic"));

            continue;
        }

        // validate packet size
        if (Header.PacketSize <
            sizeof(FPacketHeader))
        {
            UE_LOG(LogTemp, Error,
                TEXT("Invalid packet size"));

            continue;
        }

        int32 PayloadSize =
            Header.PacketSize -
            sizeof(FPacketHeader);

        // =========================
        // READ PAYLOAD
        // =========================

        TArray<uint8> Payload;

        Payload.SetNumUninitialized(
            PayloadSize);

        int32 TotalPayloadRead = 0;

        while (TotalPayloadRead <
            PayloadSize)
        {
            int32 BytesRead = 0;

            bool bOk =
                Socket->Recv(
                    Payload.GetData() +
                    TotalPayloadRead,

                    PayloadSize -
                    TotalPayloadRead,

                    BytesRead);

            if (!bOk || BytesRead <= 0)
            {
                return 0;
            }

            TotalPayloadRead +=
                BytesRead;
        }

        // =========================
        // BUILD FINAL PACKET
        // =========================

        FLiveSyncPacket Packet;

        Packet.RawData.SetNumUninitialized(
            Header.PacketSize);

        FMemory::Memcpy(
            Packet.RawData.GetData(),
            &Header,
            sizeof(FPacketHeader));

        FMemory::Memcpy(
            Packet.RawData.GetData() +
            sizeof(FPacketHeader),

            Payload.GetData(),
            PayloadSize);

        PacketQueue->Enqueue(
            MoveTemp(Packet));
    }

    return 0;
}

void FLiveSyncRunnable::Stop()
{
    bRunThread = false;
}