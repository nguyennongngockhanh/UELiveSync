# Reusable Engineering Patterns

## Bounded MPSC Queue

```cpp
class FLiveSyncQueue
{
    static constexpr int32 MaxQueueSize = 128;
    std::atomic<int32> Count{0};
    TQueue<Packet, EQueueMode::Mpsc> Queue;

    void Enqueue(const Packet& P)
    {
        int32 Prev = Count.fetch_add(1);
        if (Prev >= MaxQueueSize)
        {
            Packet Dummy;
            Queue.Dequeue(Dummy);
            Count.fetch_sub(1);
        }
        Queue.Enqueue(P);
    }

    bool Dequeue(Packet& Out)
    {
        if (Queue.Dequeue(Out))
        {
            Count.fetch_sub(1);
            return true;
        }
        return false;
    }

    void Clear()
    {
        Packet Dummy;
        while (Queue.Dequeue(Dummy))
            Count.fetch_sub(1);
    }

    int32 Size() const
    {
        return Count.load(std::memory_order_relaxed);
    }
};
```

## Network Thread Lifecycle

```cpp
void StartNetworkThread()
{
    NetworkRunnable = new FLiveSyncRunnable(Socket, &PacketQueue);
    NetworkThread = FRunnableThread::Create(NetworkRunnable, TEXT("SyncThread"));
}

void StopNetworkThread()
{
    if (NetworkRunnable) NetworkRunnable->Stop();          // atomic bool
    if (ConnectionSocket) ConnectionSocket->Close();        // unblock Recv
    if (NetworkThread)
    {
        NetworkThread->WaitForCompletion();
        delete NetworkThread; NetworkThread = nullptr;
    }
    delete NetworkRunnable; NetworkRunnable = nullptr;
    if (ConnectionSocket)
    {
        ISocketSubsystem::Get()->DestroySocket(ConnectionSocket);
        ConnectionSocket = nullptr;
    }
    PacketQueue.Clear();
    TransformStates.Empty();
    LastHeartbeatTime = 0.0;
    LastSequenceId = 0;
}
```

## V3 Packet Parsing (Network Thread)

```cpp
// Read 24 bytes header
uint8 HeaderBytes[sizeof(FPacketHeaderV3)];
int32 TotalRead = 0;
while (TotalRead < sizeof(FPacketHeaderV3))
{
    int32 BytesRead = 0;
    bool bOk = Socket->Recv(HeaderBytes + TotalRead,
                            sizeof(FPacketHeaderV3) - TotalRead,
                            BytesRead);
    if (!bOk || BytesRead <= 0) { bThreadExited = true; return 0; }
    TotalRead += BytesRead;
}

// Determine version
uint16 Version;
FMemory::Memcpy(&Version, HeaderBytes + 4, sizeof(uint16));

// Dispatch to V3 or V2 parser
if (Version >= LIVE_SYNC_VERSION_V3)
{
    FPacketHeaderV3* Hdr = reinterpret_cast<FPacketHeaderV3*>(HeaderBytes);
    // validate Magic, PacketSize, ObjectCount
    // read payload in chunks
}
```

## Packet Rate Cap + Dedup + Processing (Game Thread)

```cpp
void ProcessQueuedPackets()
{
    int32 MaxRate = CVarLiveSyncMaxPacketRate.GetValueOnGameThread();
    TArray<FLiveSyncPacket> Batch;
    int32 DequeueCount = 0;

    FLiveSyncPacket Pkt;
    while (PacketQueue.Dequeue(Pkt))
    {
        DequeueCount++;
        if (DequeueCount <= MaxRate)
            Batch.Add(MoveTemp(Pkt));
    }
    if (DequeueCount > MaxRate)
        UE_LOG(LogLiveSync, Warning,
            TEXT("Rate exceeded: %d, capping at %d"),
            DequeueCount, MaxRate);

    TSet<FGuid> SeenThisTick;
    for (auto& P : Batch)
        ProcessBinaryPacket(P, &SeenThisTick);
}
```

## Verbose Logging with Rate Limit

```cpp
static bool bEnableVerboseSyncLogs = false;

bool ShouldLogVerbose() const
{
    return bEnableVerboseSyncLogs && (VerboseFrameCounter % 300 == 0);
}

// Usage in hot path:
if (ShouldLogVerbose())
{
    UE_LOG(LogLiveSync, Log, TEXT("..."));
}

// Usage in cold path (delete, metrics):
if (bEnableVerboseSyncLogs)
{
    UE_LOG(LogLiveSync, Log, TEXT("..."));
}
```

## Transform Interpolation (Direct + Smooth)

```cpp
void InterpolateForActor(FSyncTransformState& S, AActor* A, float DT)
{
    if (CVarLiveSyncInterpMode.GetValueOnGameThread() == 0)
    {
        // Direct-set: zero lag
        S.CurrentLocation = S.TargetLocation;
        S.CurrentRotation = S.TargetRotation;
        S.CurrentScale = S.TargetScale;
    }
    else
    {
        float Dist = FVector::Dist(S.CurrentLocation, S.TargetLocation);
        if (Dist < CVarLiveSyncInterpSnap.GetValueOnGameThread())
        {
            // Snap when close
            S.CurrentLocation = S.TargetLocation;
            S.CurrentRotation = S.TargetRotation;
            S.CurrentScale = S.TargetScale;
        }
        else
        {
            // Smooth: VInterpTo + Slerp
            S.CurrentLocation = FMath::VInterpTo(
                S.CurrentLocation,
                S.TargetLocation + S.Velocity * 0.012f,
                DT, 8.0f);
            S.CurrentRotation = FQuat::Slerp(
                S.CurrentRotation, S.TargetRotation, DT * 12.0f);
            S.CurrentScale = S.TargetScale;  // direct snap
        }
    }
    A->SetActorTransform(FTransform(
        S.CurrentRotation, S.CurrentLocation, S.CurrentScale));
}
```

## Blender Background Sender (Non-blocking Enqueue)

```python
class LiveSyncClient:
    def __init__(self, host="127.0.0.1", port=57000):
        self.sock = None
        self._send_queue = queue.Queue(maxsize=256)
        self._thread = threading.Thread(target=self._sender_loop, daemon=True)
        self._thread.start()
        self.connect()

    def _sender_loop(self):
        while self._running:
            try:
                data = self._send_queue.get(timeout=1.0)
                if data is None:
                    break
                with self._lock:
                    if not self.connected:
                        self._connect_internal()
                    if self.connected:
                        self.sock.sendall(data)
            except queue.Empty:
                self._idle_probe()
                continue

    def send_packet(self, objects_data, packet_type=0x01, flags=0x00):
        packet = self._build_packet(objects_data, packet_type=packet_type, flags=flags)
        try:
            self._send_queue.put_nowait(packet)
        except queue.Full:
            self.last_error = "Send queue full"
```
