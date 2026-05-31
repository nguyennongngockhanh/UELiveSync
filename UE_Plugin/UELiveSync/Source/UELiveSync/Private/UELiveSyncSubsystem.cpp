// =========================================================
// UELiveSyncSubsystem.cpp — Runtime Orchestrator
// =========================================================
// PHASE 5 COMPLETE — RUNTIME CORE FROZEN
//
// This file implements the game-thread Tick pipeline that is
// the central orchestrator of the UELiveSync runtime.  It is
// considered STABLE and FROZEN as of v0.5.0-stabilized.
//
// Modification of the following subsystems without explicit
// justification (critical bug fix only) risks destabilizing
// the entire runtime:
//
//   - Tick pipeline ordering (ProcessQueuedPackets →
//     InterpolateTransforms → ResolvePendingAttachments →
//     RecoverMissingActors → ResolvePendingAssets)
//   - Packet binary parser (ProcessBinaryPacket + version
//     dispatch)
//   - Queue dequeue/metadata lifecycle (ProcessQueuedPackets)
//   - Reconnect lifecycle (StopNetworkThread / StartNetworkThread)
//   - Transform interpolation (InterpolateTransforms)
//   - BEGIN/END tracing instrumentation
//
// Profile/debug instrumentation (TRACE_CPUPROFILER_EVENT_SCOPE,
// UE_LOG BEGIN/END markers, runtime metrics) is retained
// INTENTIONALLY for future scalability debugging.  Do not remove.
//
// See Docs/Architecture/12-core-runtime-invariants.md for
// complete invariant documentation.
// =========================================================

#include "UELiveSyncSubsystem.h"

DEFINE_LOG_CATEGORY(LogLiveSync);

#include "Engine/Engine.h"
#include "Engine/World.h"

#include "GameFramework/Actor.h"

#include "EngineUtils.h"

#include "Common/TcpSocketBuilder.h"

#include "Sockets.h"

#include "SocketSubsystem.h"

#include "Interfaces/IPv4/IPv4Address.h"

#include "HAL/RunnableThread.h"

#include "Misc/Guid.h"

#include "LiveSyncRunnable.h"

#include "Components/StaticMeshComponent.h"

#include "Engine/StaticMesh.h"

// =========================================================
// PHASE 6 — SEMANTIC EDITOR-EVENT HELPERS
// =========================================================
//
// These helpers enable provenance tracking, scoped callback
// suppression, and replay-rename dedup for the rename
// vertical slice (PT_Rename = 0x0C).
//
// See Docs/Architecture/19-phase6-vertical-slice-rename.md
//
// =========================================================

// --- Provenance (in-memory only, not on the wire) ---
// Every editor-originating mutation must carry a provenance tag
// so that replicated changes can suppress recursive callbacks.
thread_local EChangeOrigin GCurrentChangeOrigin = EChangeOrigin::Unspecified;

// RAII guard — sets current-thread provenance, restores on exit
struct FScopedChangeOrigin
{
    EChangeOrigin Previous;
    FScopedChangeOrigin(EChangeOrigin NewOrigin)
        : Previous(GCurrentChangeOrigin)
    {
        GCurrentChangeOrigin = NewOrigin;
    }
    ~FScopedChangeOrigin()
    {
        GCurrentChangeOrigin = Previous;
    }
};

// --- Rename suppression scope ---
// Wrapping SetActorLabel in this guard prevents OnActorLabelChanged
// from re-replicating the rename back to Blender.
struct FScopedRenameSuppression
{
    FString GuidStr;
    FScopedRenameSuppression(const FGuid& InGuid)
        : GuidStr(InGuid.ToString(EGuidFormats::Digits))
    {
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[RENAME] Enter suppression scope (GUID=%s)"),
            *GuidStr);
    }
    ~FScopedRenameSuppression()
    {
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[RENAME] Exit suppression scope (GUID=%s)"),
            *GuidStr);
    }
};

// --- Visibility suppression scope ---
// Wrapping SetIsTemporarilyHiddenInEditor in this guard prevents
// editor callbacks from re-replicating visibility back to Blender.
// Pattern follows FScopedRenameSuppression for architectural consistency.
struct FScopedVisibilitySuppression
{
    FString GuidStr;
    FScopedVisibilitySuppression(const FGuid& InGuid)
        : GuidStr(InGuid.ToString(EGuidFormats::Digits))
    {
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[VISIBILITY] Enter suppression scope (GUID=%s)"),
            *GuidStr);
    }
    ~FScopedVisibilitySuppression()
    {
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[VISIBILITY] Exit suppression scope (GUID=%s)"),
            *GuidStr);
    }
};

// --- Delete suppression scope ---
// RAII guard following semantic event conventions. Added for
// architectural completeness — delete has no recursive callback
// risk currently, but all semantic lanes require suppression
// infrastructure for future-proofing.
struct FScopedDeleteSuppression
{
    FString GuidStr;
    FScopedDeleteSuppression(const FGuid& InGuid)
        : GuidStr(InGuid.ToString(EGuidFormats::Digits))
    {
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[DELETE] Enter suppression scope (GUID=%s)"),
            *GuidStr);
    }
    ~FScopedDeleteSuppression()
    {
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[DELETE] Exit suppression scope (GUID=%s)"),
            *GuidStr);
    }
};

// --- Collection suppression RAII guard (Phase 6F) ---
// Scoped log marker for collection mutation boundaries.
// Used to identify collection mutations that originated from
// Blender sync, enabling diagnostic traceability.
struct FScopedCollectionSuppression
{
    FString GuidStr;
    FScopedCollectionSuppression(const FGuid& InGuid)
        : GuidStr(InGuid.ToString(EGuidFormats::Digits))
    {
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[COLLECTION] Enter suppression scope (GUID=%s)"),
            *GuidStr);
    }
    ~FScopedCollectionSuppression()
    {
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[COLLECTION] Exit suppression scope (GUID=%s)"),
            *GuidStr);
    }
};

// --- Rename sequence tracker (single subsystem-level instance) ---
FRenameSequenceTracker GRenameSequences;

// --- Persistent rename label registry (GUID → authoritative actor label) ---
// Stores the authoritative Blender-sourced label for each renamed GUID.
// Survives snapshot rebuilds, reconnect, CreateObject spawn relabeling,
// and RestoreWorldState rollback. Populated by HandleRename.
// Game-thread only. Cleared on StopNetworkThread and ConsoleReset.
TMap<FGuid, FString> GRenamePersistentLabel;

// --- Visibility sequence tracker (single subsystem-level instance) ---
FVisibilitySequenceTracker GVisibilitySequences;

// --- Hierarchy sequence tracker (single subsystem-level instance) ---
FHierarchySequenceTracker GHierarchySequences;

// --- Delete sequence tracker (Phase 6E, single subsystem-level instance) ---
FDeleteSequenceTracker GDeleteSequences;

// --- Collection sequence tracker (Phase 6F, single subsystem-level instance) ---
FCollectionSequenceTracker GCollectionSequences;

// --- Collection membership registry (Phase 6F, GUID → set of collection GUIDs) ---
// Maps actor GUID → set of collection GUIDs that the actor belongs to.
// Game-thread only. Cleared on StopNetworkThread and ConsoleReset.
TMap<FGuid, TSet<FGuid>> GCollectionMembership;

// --- Collection identity registry (Phase 6F, collection GUID → metadata) ---
// Maps collection GUID → optional metadata (name, etc.). Empty until populated.
// Game-thread only. Cleared on StopNetworkThread and ConsoleReset.
TMap<FGuid, FString> GCollectionIdentities;

// --- Collection replay ring buffer (Phase 6F Stage 5) ---
// Bounded at 2048 entries, FIFO eviction on overflow.
// Stores raw per-object payloads (30 or 46 bytes) for deterministic replay.
// Game-thread only. Cleared on StopNetworkThread and ConsoleReset.
static constexpr int32 COLLECTION_REPLAY_MAX = 2048;
static TArray<TArray<uint8>> GCollectionReplayBuffer;

// --- Collection replay recording enabled flag ---
static bool GCollectionReplayEnabled = true;

// --- Collection replay ordering metadata (Stage 6) ---
// Parallel arrays: sequence number + FNV-1a checksum for each entry.
// Used by ReplayCollectionStream for ordering validation + corruption detection.
static TArray<uint32> GCollectionReplaySequences;
static TArray<uint32> GCollectionReplayChecksums;

// --- Collection replay ordering mode (Stage 6) ---
static ECollectionReplayOrderMode GCollectionReplayOrderMode = ECollectionReplayOrderMode::Strict;

// --- Last verified replay hash (Stage 6) ---
// Stores the canonical snapshot hash from the last successful replay
// verification. Zero on first run / after clear.
static uint64 GCollectionLastVerifiedHash = 0;

// --- Collection replay timeline (Phase 6F Stage 7) ---
// Bounded ring buffer (1024) of replay timeline events for forensic
// inspection. Written by game thread during ReplayCollectionStream().
// Cleared on ConsoleReset.
static FReplayTimeline GCollectionReplayTimeline;

// --- Collection replay trace config (Phase 6F Stage 7) ---
// Runtime toggle for verbose replay tracing.
static FReplayTraceConfig GCollectionReplayTraceConfig;

// --- Collection replay rolling window stats (Phase 6F Stage 7) ---
// Rolling statistics for replay/rebuild/hash-verify durations.
// Game-thread only. Cleared on ConsoleReset.
static FReplayWindowStats GCollectionReplayWindowStats;

// --- Peak replay buffer usage tracking (Phase 6F Stage 7) ---
static int32 GCollectionReplayPeakUsage = 0;

// --- Last replay buffer health warning time (Phase 6F Stage 7) ---
static double GCollectionLastReplayHealthWarning = 0.0;
static constexpr double REPLAY_HEALTH_WARNING_COOLDOWN = 5.0; // seconds
static constexpr double REPLAY_HEALTH_WARN_THRESHOLD = 0.80;  // 80% utilization

// --- Unified world replay buffer (Phase 6G) ---
// Bounded ring buffer storing replay entries from all domains.
// Each entry captures domain type, GUIDs, sequence, and payload
// for deterministic world-state verification.
static constexpr int32 WORLD_REPLAY_MAX = 4096;
static TArray<FWorldReplayEntry> GWorldReplayBuffer;
static bool GWorldReplayEnabled = true;
static uint64 GWorldLastVerifiedHash = 0;  // Last verified world hash
static FWorldStateSnapshot GWorldSavedState;  // Rollback save point

// --- FNV-1a 32-bit helper for replay checksums ---
static uint32 CollectionReplayChecksum(const uint8* Data, int32 Len)
{
    uint32 H = 2166136261u;
    for (int32 i = 0; i < Len; i++)
    {
        H ^= Data[i];
        H *= 16777619u;
    }
    return H;
}

// --- Delete tombstone map (Phase 6E, GUID → last delete sequence) ---
// Bounded at 2048 entries with FIFO eviction on overflow.
// Game-thread only. Cleared on StopNetworkThread and ConsoleReset.
TMap<FGuid, uint32> GDeleteTombstoneMap;

// Maximum number of tombstone entries before eviction
static constexpr uint32 MAX_TOMBSTONE_ENTRIES = 2048;

// Insertion-order FIFO queue for tombstone eviction
static TArray<FGuid> GDeleteTombstoneOrder;

// Phase 6E Stage 4: tombstone helper APIs
// =========================================================
// IsTombstoned: O(1) check — used by all semantic handlers to
//   reject packets for deleted GUIDs.
// AddTombstone: insert with bounded FIFO eviction at 2048.
//   Called only after successful actor destruction (Stage 5).
// RemoveTombstone: remove entry (for future use, e.g., undo).
// =========================================================
static bool IsTombstoned(const FGuid& Guid)
{
    return GDeleteTombstoneMap.Contains(Guid);
}

static void AddTombstone(const FGuid& Guid, uint32 SequenceNumber)
{
    if (GDeleteTombstoneMap.Num() >= MAX_TOMBSTONE_ENTRIES)
    {
        if (GDeleteTombstoneOrder.Num() == 0)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[TOMBSTONE] FIFO eviction failed — order queue empty "
                     "(map has %d entries but order is empty)"),
                GDeleteTombstoneMap.Num());
            return;
        }
        FGuid EvictGuid = GDeleteTombstoneOrder[0];
        GDeleteTombstoneOrder.RemoveAt(0);
        GDeleteTombstoneMap.Remove(EvictGuid);
        UE_LOG(LogLiveSync, Log,
            TEXT("[TOMBSTONE] FIFO evict: GUID=%s — max entries (%u) reached"),
            *EvictGuid.ToString(EGuidFormats::Digits),
            MAX_TOMBSTONE_ENTRIES);
    }
    GDeleteTombstoneOrder.Add(Guid);
    GDeleteTombstoneMap.Add(Guid, SequenceNumber);
    UE_LOG(LogLiveSync, Verbose,
        TEXT("[TOMBSTONE] Added: GUID=%s Seq=%u"),
        *Guid.ToString(EGuidFormats::Digits),
        SequenceNumber);
}

static void RemoveTombstone(const FGuid& Guid)
{
    GDeleteTombstoneOrder.Remove(Guid);
    GDeleteTombstoneMap.Remove(Guid);
}

#include "HAL/IConsoleManager.h"

#include "HAL/PlatformProcess.h"

#include "HAL/PlatformTLS.h"

#include "ProfilingDebugging/CpuProfilerTrace.h"


// =========================================================
// THREAD IDENTITY MACROS
// =========================================================

#define CHECK_GAME_THREAD() \
    check(IsInGameThread())

#define CHECK_NONGAME_THREAD() \
    check(!IsInGameThread())


// =========================================================
// STATIC HELPERS
// =========================================================

static UStaticMesh*
GetPrimitiveMesh(uint8 PrimitiveType)
{
    // Diagnostic: attempt alternate paths for UE5.4+ content
    UStaticMesh* Mesh = nullptr;

    switch (PrimitiveType)
    {
    case LSP_Sphere:
        Mesh = LoadObject<UStaticMesh>(
            nullptr,
            TEXT(
            "/Engine/BasicShapes/"
            "Sphere.Sphere"));
        if (!Mesh)
        {
            Mesh = LoadObject<UStaticMesh>(
                nullptr,
                TEXT("/Engine/BasicShapes/BasicSphere.BasicSphere"));
        }
        if (!Mesh)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[DIAG][PRIMITIVE] Sphere: all paths failed"));
        }
        return Mesh;

    case LSP_Cylinder:
        Mesh = LoadObject<UStaticMesh>(
            nullptr,
            TEXT(
            "/Engine/BasicShapes/"
            "Cylinder.Cylinder"));
        if (!Mesh)
        {
            Mesh = LoadObject<UStaticMesh>(
                nullptr,
                TEXT("/Engine/BasicShapes/BasicCylinder.BasicCylinder"));
        }
        if (!Mesh)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[DIAG][PRIMITIVE] Cylinder: all paths failed"));
        }
        return Mesh;

    case LSP_Plane:
        Mesh = LoadObject<UStaticMesh>(
            nullptr,
            TEXT(
            "/Engine/BasicShapes/"
            "Plane.Plane"));
        if (!Mesh)
        {
            Mesh = LoadObject<UStaticMesh>(
                nullptr,
                TEXT("/Engine/BasicShapes/BasicPlane.BasicPlane"));
        }
        if (!Mesh)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[DIAG][PRIMITIVE] Plane: all paths failed"));
        }
        return Mesh;

    case LSP_Cube:
    default:
        Mesh = LoadObject<UStaticMesh>(
            nullptr,
            TEXT(
            "/Engine/BasicShapes/"
            "Cube.Cube"));
        if (!Mesh)
        {
            Mesh = LoadObject<UStaticMesh>(
                nullptr,
                TEXT("/Engine/BasicShapes/BasicCube.BasicCube"));
        }
        if (!Mesh)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[DIAG][PRIMITIVE] Cube: all paths failed — actor will be invisible!"));
        }
        return Mesh;
    }
}


// =========================================================
// TRANSFORM VALIDATION
// =========================================================
// Validates a transform before application to catch NaN/Inf
// quaternion drift, or degenerate values that could freeze
// the physics or rendering system.
// Returns true if the transform is safe to apply.
// =========================================================

static bool ValidateTransform(
    const FTransform& XForm,
    const FGuid& Guid,
    const TCHAR* Context)
{
    bool bValid = true;

    const FVector& Loc = XForm.GetLocation();
    if (Loc.ContainsNaN())
    {
        UE_LOG(LogLiveSync, Error,
            TEXT("INVALID TRANSFORM [%s]: guid=%s location=NaN"),
            Context, *Guid.ToString(EGuidFormats::Digits));
        bValid = false;
    }

    if (!FMath::IsFinite(Loc.X) || !FMath::IsFinite(Loc.Y) || !FMath::IsFinite(Loc.Z))
    {
        UE_LOG(LogLiveSync, Error,
            TEXT("INVALID TRANSFORM [%s]: guid=%s location=Inf"),
            Context, *Guid.ToString(EGuidFormats::Digits));
        bValid = false;
    }

    const FQuat& Rot = XForm.GetRotation();
    if (Rot.ContainsNaN())
    {
        UE_LOG(LogLiveSync, Error,
            TEXT("INVALID TRANSFORM [%s]: guid=%s rotation=NaN"),
            Context, *Guid.ToString(EGuidFormats::Digits));
        bValid = false;
    }

    if (!FMath::IsFinite(Rot.W) || !FMath::IsFinite(Rot.X) ||
        !FMath::IsFinite(Rot.Y) || !FMath::IsFinite(Rot.Z))
    {
        UE_LOG(LogLiveSync, Error,
            TEXT("INVALID TRANSFORM [%s]: guid=%s rotation=Inf"),
            Context, *Guid.ToString(EGuidFormats::Digits));
        bValid = false;
    }

    float RotNorm = Rot.SizeSquared();
    if (FMath::Abs(RotNorm - 1.0f) > 0.01f)
    {
        UE_LOG(LogLiveSync, Error,
            TEXT("INVALID TRANSFORM [%s]: guid=%s rotation=non-unit quaternion (len^2=%.4f)"),
            Context, *Guid.ToString(EGuidFormats::Digits), RotNorm);
        bValid = false;
    }

    const FVector& Scale = XForm.GetScale3D();
    if (Scale.ContainsNaN())
    {
        UE_LOG(LogLiveSync, Error,
            TEXT("INVALID TRANSFORM [%s]: guid=%s scale=NaN"),
            Context, *Guid.ToString(EGuidFormats::Digits));
        bValid = false;
    }

    if (!FMath::IsFinite(Scale.X) || !FMath::IsFinite(Scale.Y) || !FMath::IsFinite(Scale.Z))
    {
        UE_LOG(LogLiveSync, Error,
            TEXT("INVALID TRANSFORM [%s]: guid=%s scale=Inf"),
            Context, *Guid.ToString(EGuidFormats::Digits));
        bValid = false;
    }

    if (Scale.X > 1e6f || Scale.Y > 1e6f || Scale.Z > 1e6f ||
        Scale.X < -1e6f || Scale.Y < -1e6f || Scale.Z < -1e6f)
    {
        UE_LOG(LogLiveSync, Error,
            TEXT("INVALID TRANSFORM [%s]: guid=%s scale=extreme (%.2f, %.2f, %.2f)"),
            Context, *Guid.ToString(EGuidFormats::Digits),
            Scale.X, Scale.Y, Scale.Z);
        bValid = false;
    }

    return bValid;
}

// =========================================================
// CONSOLE VARIABLES
// =========================================================

static TAutoConsoleVariable<int32>
    CVarLiveSyncPort(
        TEXT("UE.LiveSync.Port"),
        57000,
        TEXT("TCP port for Blender live sync connection"),
        ECVF_Default
    );

static TAutoConsoleVariable<float>
    CVarLiveSyncHeartbeatTimeout(
        TEXT("UE.LiveSync.HeartbeatTimeout"),
        15.0f,
        TEXT("Seconds without heartbeat before disconnecting"),
        ECVF_Default
    );

static TAutoConsoleVariable<float>
    CVarLiveSyncStateTTL(
        TEXT("UE.LiveSync.StateTTL"),
        60.0f,
        TEXT("Seconds before inactive transform states are evicted"),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncVerbose(
        TEXT("UE.LiveSync.Verbose"),
        0,
        TEXT("Enable verbose sync logging (1=on, 0=off)"),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncInterpMode(
        TEXT("UE.LiveSync.InterpMode"),
        1,
        TEXT("Interpolation mode: 0=direct-set (zero lag), 1=smooth (default)"),
        ECVF_Default
    );

static TAutoConsoleVariable<float>
    CVarLiveSyncInterpSnap(
        TEXT("UE.LiveSync.InterpSnap"),
        0.1f,
        TEXT("Distance threshold in cm for snapping to target instead of interpolating"),
        ECVF_Default
    );

static TAutoConsoleVariable<float>
    CVarLiveSyncThresholdLocation(
        TEXT("UE.LiveSync.Threshold.Location"),
        0.05f,
        TEXT("Min location change in cm to trigger transform update"),
        ECVF_Default
    );

static TAutoConsoleVariable<float>
    CVarLiveSyncThresholdRotation(
        TEXT("UE.LiveSync.Threshold.Rotation"),
        0.002f,
        TEXT("Min rotation change (angular distance) to trigger transform update"),
        ECVF_Default
    );

static TAutoConsoleVariable<float>
    CVarLiveSyncThresholdScale(
        TEXT("UE.LiveSync.Threshold.Scale"),
        0.001f,
        TEXT("Min scale change to trigger transform update"),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncVerboseSyncLogs(
        TEXT("UE.LiveSync.VerboseSyncLogs"),
        0,
        TEXT("Enable verbose sync log messages (1=on, 0=off). "
             "Overrides UE.LiveSync.Verbose for log-message granularity."),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncDebugDraw(
        TEXT("UE.LiveSync.DebugDraw"),
        0,
        TEXT("Enable debug visualization overlay (1=on, 0=off)"),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncValidateProtocol(
        TEXT("UE.LiveSync.ValidateProtocol"),
        1,
        TEXT("Validate packet type and flags (1=on, 0=off)"),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncTransportVerbose(
        TEXT("UE.LiveSync.TransportVerbose"),
        0,
        TEXT("Enable transport-layer diagnostics "
             "(1=on, 0=off). "
             "When enabled, logs per-packet transport "
             "events at Verbose level."),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncQueueWarnThreshold(
        TEXT("UE.LiveSync.QueueWarnThreshold"),
        64,
        TEXT("Queue depth at which a warning is logged"),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncMaxPacketRate(
        TEXT("UE.LiveSync.MaxPacketRate"),
        200,
        TEXT("Max packets processed per tick (overflow stays queued)"),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncMaxDepth(
        TEXT("UE.LiveSync.MaxHierarchyDepth"),
        64,
        TEXT("Maximum hierarchy depth safeguard (0=disabled)"),
        ECVF_Cheat
    );

// =====================================================
// SUBSYSTEM ISOLATION CVARS
// Temporarily disable subsystems to narrow freeze root cause.
// Set to 1 to skip the corresponding subsystem entirely.
// =====================================================

static TAutoConsoleVariable<int32>
    CVarLiveSyncDisableSpawning(
        TEXT("UE.LiveSync.DisableSpawning"),
        0,
        TEXT("Skip HandleCreateObject actor spawn (1=on, 0=off). "
             "Set to 1 to test if actor creation causes freeze."),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncDisableTransformApply(
        TEXT("UE.LiveSync.DisableTransformApply"),
        0,
        TEXT("Skip SetActorTransform in InterpolateTransforms (1=on, 0=off). "
             "Set to 1 to test if transform application causes freeze."),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncDisableAttachment(
        TEXT("UE.LiveSync.DisableAttachment"),
        0,
        TEXT("Skip AttachToActor calls (1=on, 0=off). "
             "Set to 1 to test if attachment logic causes freeze."),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncDisableInterpolation(
        TEXT("UE.LiveSync.DisableInterpolation"),
        0,
        TEXT("Skip InterpolateTransforms entirely (1=on, 0=off). "
             "Set to 1 to test if interpolation causes freeze."),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncDisableAttachmentResolution(
        TEXT("UE.LiveSync.DisableAttachmentResolution"),
        0,
        TEXT("Skip ResolvePendingAttachments entirely (1=on, 0=off). "
             "Set to 1 to test if attachment resolution causes freeze."),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncDisableAssetResolution(
        TEXT("UE.LiveSync.DisableAssetResolution"),
        0,
        TEXT("Skip ResolvePendingAssets entirely (1=on, 0=off). "
             "Set to 1 to test if asset resolution causes freeze."),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncDisableRecovery(
        TEXT("UE.LiveSync.DisableRecovery"),
        0,
        TEXT("Skip RecoverMissingActors entirely (1=on, 0=off). "
             "Set to 1 to test if recovery loop causes freeze."),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncBypassSetActorTransform(
        TEXT("UE.LiveSync.BypassSetActorTransform"),
        0,
        TEXT("Skip SetActorTransform calls but keep internal state updates (1=on, 0=off). "
             "Set to 1 to isolate if SetActorTransform itself causes freeze."),
        ECVF_Default
    );

// Phase 6I.1 Stage 2: socket receive timeout (ms). 0 = no timeout (blocking).
static TAutoConsoleVariable<int32>
    CVarLiveSyncRecvTimeoutMs(
        TEXT("UE.LiveSync.RecvTimeoutMs"),
        5000,
        TEXT("Socket receive timeout in milliseconds "
             "(default=5000). 0 = infinite (blocking)."),
        ECVF_Default
    );

bool UUELiveSyncSubsystem::
    bEnableVerboseSyncLogs =
        false;

bool UUELiveSyncSubsystem::
    bEnableTransportVerbose =
        false;

bool GEnableVerboseSyncLogs =
    false;

bool UUELiveSyncSubsystem::
    bEnableDebugDraw =
        false;

bool UUELiveSyncSubsystem::
ShouldLogVerbose() const
{
    return
        bEnableVerboseSyncLogs &&
        (VerboseFrameCounter % 300 == 0);
}


// =========================================================
// INITIALIZE
// =========================================================

void UUELiveSyncSubsystem::Initialize(
    FSubsystemCollectionBase&
    Collection)
{
    Super::Initialize(Collection);

    StartServer();

    BuildActorCache();

    UWorld* World = GetWorld();

    if (World)
    {
        OnActorSpawnedHandle =

            World->AddOnActorSpawnedHandler(

                FOnActorSpawned::FDelegate::CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::OnActorSpawned
                )
            );

        OnActorDestroyedHandle =

            World->AddOnActorDestroyedHandler(

                FOnActorDestroyed::FDelegate::CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::OnActorDestroyed
                )
            );
    }

    TickHandle =
        FTSTicker::GetCoreTicker().
        AddTicker(

            FTickerDelegate::
            CreateUObject(

                this,
                &UUELiveSyncSubsystem::Tick),

            0.0f
        );

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("UE Live Sync Started"));

    // =====================================================
    // NULLRHI GUARD — injected ingress block detection
    // =====================================================
    // -NullRHI suppresses the engine tick loop, preventing
    // Tick() from executing, which blocks accept(),
    // network thread startup, and packet ingress.
    // See Docs/KNOWN_BAD_PATTERNS.md for details.
    {
        bool bIsNullRHI = FParse::Param(FCommandLine::Get(), TEXT("NullRHI"));
        if (bIsNullRHI)
        {
            UE_LOG(LogLiveSync, Error,
                TEXT("[LIFECYCLE][ERROR] ============================================================"));
            UE_LOG(LogLiveSync, Error,
                TEXT("[LIFECYCLE][ERROR] NullRHI editor mode DETECTED."));
            UE_LOG(LogLiveSync, Error,
                TEXT("[LIFECYCLE][ERROR] Tick-driven ingress will NOT execute."));
            UE_LOG(LogLiveSync, Error,
                TEXT("[LIFECYCLE][ERROR] LiveSync networking is DISABLED."));
            UE_LOG(LogLiveSync, Error,
                TEXT("[LIFECYCLE][ERROR] Remove -NullRHI from launch arguments."));
            UE_LOG(LogLiveSync, Error,
                TEXT("[LIFECYCLE][ERROR] Use -RenderOffScreen instead of -NullRHI."));
            UE_LOG(LogLiveSync, Error,
                TEXT("[LIFECYCLE][ERROR] ============================================================"));
        }
    }

    // =====================================================
    // REGISTER CONSOLE COMMANDS
    // =====================================================

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.DumpState"),
            TEXT("Print all tracked GUIDs, actors, and queue state"),
            FConsoleCommandDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleDumpState),
            ECVF_Default);

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.Reset"),
            TEXT("Full teardown and restart of live sync"),
            FConsoleCommandDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleReset),
            ECVF_Default);

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.Ping"),
            TEXT("Send test heartbeat to verify connectivity"),
            FConsoleCommandDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsolePing),
            ECVF_Default);

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.Stats"),
            TEXT("Print runtime metrics"),
            FConsoleCommandDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleStats),
            ECVF_Default);

    // =====================================================
    // STAGE 7 — COLLECTION OBSERVABILITY CONSOLE COMMANDS
    // =====================================================

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.DumpReplayBuffer"),
            TEXT("Dump collection replay buffer state and timeline"),
            FConsoleCommandDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleDumpReplayBuffer),
            ECVF_Default);

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.DumpCollectionGraph"),
            TEXT("Dump collection membership graph and hashes"),
            FConsoleCommandDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleDumpCollectionGraph),
            ECVF_Default);

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.VerifyCollectionReplay"),
            TEXT("Force collection replay hash verification (non-mutating)"),
            FConsoleCommandDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleVerifyCollectionReplay),
            ECVF_Default);

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.ClearReplayDiagnostics"),
            TEXT("Clear all collection replay observability counters"),
            FConsoleCommandDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleClearReplayDiagnostics),
            ECVF_Default);

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.ToggleReplayTracing"),
            TEXT("Toggle verbose replay tracing on/off"),
            FConsoleCommandDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleToggleReplayTracing),
            ECVF_Default);

    // =====================================================
    // STAGE 6G — UNIFIED WORLD REPLAY CONSOLE COMMANDS
    // =====================================================

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.DumpWorldReplayState"),
            TEXT("Dump unified world replay buffer state and domain breakdown"),
            FConsoleCommandDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleDumpWorldReplayState),
            ECVF_Default);

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.VerifyWorldReplay"),
            TEXT("Run full world replay verification with rollback safety"),
            FConsoleCommandDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleVerifyWorldReplay),
            ECVF_Default);

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.DumpReplayTimeline"),
            TEXT("Dump the last 25 collection replay timeline events"),
            FConsoleCommandDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleDumpReplayTimeline),
            ECVF_Default);

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.ExportWorldSnapshot"),
            TEXT("Export canonical world snapshot (all domains)"),
            FConsoleCommandDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleExportWorldSnapshot),
            ECVF_Default);

    // =====================================================
    // PHASE 6H — SEMANTIC CONSISTENCY HARDENING
    // =====================================================

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.ValidatePacketOrdering"),
            TEXT("Phase 6H: Dump packet ordering validation counters"),
            FConsoleCommandDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleValidatePacketOrdering),
            ECVF_Default);

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.VerifySemanticState"),
            TEXT("Phase 6H: Run semantic authority audit (non-mutating)"),
            FConsoleCommandDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleVerifySemanticState),
            ECVF_Default);

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.DumpAuthorityState"),
            TEXT("Phase 6H: Dump per-actor authority state"),
            FConsoleCommandDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleDumpAuthorityState),
            ECVF_Default);

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.RunReplayFuzz"),
            TEXT("Phase 6H: Replay fuzz test [seed] [iterations]"),
            FConsoleCommandWithArgsDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleRunReplayFuzz),
            ECVF_Default);

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.RunHierarchyStress"),
            TEXT("Phase 6H: Hierarchy stress test [objects] [ops]"),
            FConsoleCommandWithArgsDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleRunHierarchyStress),
            ECVF_Default);

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.RunReconnectStress"),
            TEXT("Phase 6H: Reconnect stress test [cycles]"),
            FConsoleCommandWithArgsDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleRunReconnectStress),
            ECVF_Default);

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.VerifyReplayDeterminism"),
            TEXT("Phase 6H: Full replay determinism verification (snapshot+rebuild+compare)"),
            FConsoleCommandDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleVerifyReplayDeterminism),
            ECVF_Default);

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.EnforceKnownBadPatterns"),
            TEXT("Phase 6H: Run known-bad-pattern detection diagnostics"),
            FConsoleCommandDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleEnforceKnownBadPatterns),
            ECVF_Default);

    // ── Phase 6I: Performance & Scalability ──────────────
    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.Phase6IStats"),
            TEXT("Phase 6I: Show performance and scalability stats"),
            FConsoleCommandDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsolePhase6IStats),
            ECVF_Default);

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.Coalesce"),
            TEXT("Phase 6I: Toggle transform coalescing [0=off, 1=on]"),
            FConsoleCommandWithArgsDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleToggleCoalesce),
            ECVF_Default);

    IConsoleManager::Get().
        RegisterConsoleCommand(
            TEXT("UE.LiveSync.SetDiagnosticsCadence"),
            TEXT("Phase 6I: Set diagnostics cadence in frames [10-600]"),
            FConsoleCommandWithArgsDelegate::
                CreateUObject(
                    this,
                    &UUELiveSyncSubsystem::
                        ConsoleSetDiagnosticsCadence),
            ECVF_Default);
}

// =========================================================
// DEINITIALIZE
// =========================================================

void UUELiveSyncSubsystem::Deinitialize()
{
    // Remove ticker first to prevent any re-entrant
    // Tick() call during teardown
    FTSTicker::GetCoreTicker().
        RemoveTicker(
            TickHandle);

    // Shutdown network thread (closes connection socket)
    StopNetworkThread();

    UWorld* World = GetWorld();

    if (World)
    {
        if (OnActorSpawnedHandle.IsValid())
        {
            World->RemoveOnActorSpawnedHandler(
                OnActorSpawnedHandle);

            OnActorSpawnedHandle.Reset();
        }

        if (OnActorDestroyedHandle.IsValid())
        {
            World->RemoveOnActorDestroyedHandler(
                OnActorDestroyedHandle);

            OnActorDestroyedHandle.Reset();
        }
    }

    // Shutdown listener socket
    if (ListenerSocket)
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("Deinitialize: closing listener socket"));

        // Shutdown before Close to unblock any pending
        // accept() or poll() on the listener
        ListenerSocket->Shutdown(
            ESocketShutdownMode::ReadWrite);

        ListenerSocket->Close();

        ISocketSubsystem::
            Get(PLATFORM_SOCKETSUBSYSTEM)
            ->DestroySocket(
                ListenerSocket);

        ListenerSocket =
            nullptr;
    }

    Super::Deinitialize();
}


// =========================================================
// START SERVER
// =========================================================

void UUELiveSyncSubsystem::StartServer()
{
    if (ListenerSocket)
    {
        return;
    }

    int32 Port =
        CVarLiveSyncPort.GetValueOnGameThread();

    if (Port < 1024 || Port > 65535)
    {
        Port = 57000;
    }

    FIPv4Address Address;

    FIPv4Address::Parse(
        TEXT("0.0.0.0"),
        Address);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("StartServer: creating TCP listener on %s:%d"),
        *Address.ToString(),
        Port);

    ListenerSocket =

        FTcpSocketBuilder(
            TEXT("UE_LiveSync_Server"))

        .AsReusable()

        .BoundToAddress(
            Address)

        .BoundToPort(
            Port)

        .Listening(8);

    if (!ListenerSocket)
    {
        UE_LOG(
            LogLiveSync,
            Error,
            TEXT("StartServer: FAILED to create listener on "
                 "%s:%d — port may be in use, thread may "
                 "have failed, or socket limit reached"),
            *Address.ToString(),
            Port);

        return;
    }

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("StartServer: listening on %s:%d (backlog=8, "
             "reuse=true)"),
        *Address.ToString(),
        Port);
}


// =========================================================
// MAIN TICK
// =========================================================

bool UUELiveSyncSubsystem::Tick(
    float DeltaTime)
{
    CHECK_GAME_THREAD();
    VerboseFrameCounter++;
    LastTickExecutionTime = FPlatformTime::Seconds();

    // =====================================================
    // SYNC CVARS
    // =====================================================

    bEnableVerboseSyncLogs =
        CVarLiveSyncVerbose.GetValueOnGameThread() != 0;

    GEnableVerboseSyncLogs =
        bEnableVerboseSyncLogs;

    bEnableTransportVerbose =
        CVarLiveSyncTransportVerbose.GetValueOnGameThread() != 0;

    bEnableDebugDraw =
        CVarLiveSyncDebugDraw.GetValueOnGameThread() != 0;

    // =====================================================
    // PERIODIC TICK DIAGNOSTICS (every ~100 frames)
    // =====================================================

    if (VerboseFrameCounter % 100 == 1)
    {
        const int32 CacheSize = ActorCache.Num();
        const int32 StateSize = TransformStates.Num();
        int32 AliveActors = 0;
        int32 DeadActors = 0;
        for (const auto& Pair : ActorCache)
        {
            if (Pair.Value.IsValid()) AliveActors++;
            else DeadActors++;
        }

        UE_LOG(LogLiveSync, Warning,
            TEXT("[TICK][DIAG] frame=%lld ActorCache=%d (alive=%d dead=%d) TransformStates=%d"),
            (long long)VerboseFrameCounter,
            CacheSize, AliveActors, DeadActors,
            StateSize);
    }

    // =====================================================
    // RETRY LISTENER IF PREVIOUS BIND FAILED
    // =====================================================

    if (!ListenerSocket)
    {
        static double LastRetryTime = 0.0;

        double Now = FPlatformTime::Seconds();

        if (Now - LastRetryTime >= 5.0)
        {
            LastRetryTime = Now;

            StartServer();
        }
    }

    // =====================================================
    // TICK HEARTBEAT — always-on proof of life
    // =====================================================
    // If this never appears in the log, Tick is not
    // executing (e.g. -NullRHI mode, scheduler stall).
    // Fires every ~300 ticks (~5s at 60fps).
    if (VerboseFrameCounter % 300 == 1)
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("[TICK][HEARTBEAT] Tick is executing "
                 "(frame=%d)"),
            VerboseFrameCounter);
    }

    // =====================================================
    // ACCEPT CONNECTION
    // =====================================================

    if (!ConnectionSocket &&
        ListenerSocket)
    {
        bool bPending =
            false;

        // Log periodic "waiting" status every ~600 ticks (10s)
        {
            static int32 AcceptPollCounter = 0;

            if (++AcceptPollCounter % 600 == 1)
            {
                UE_LOG(
                    LogLiveSync,
                    Log,
                    TEXT("Accept: waiting for connection on "
                         "port %d"),
                    CVarLiveSyncPort.GetValueOnGameThread());
            }
        }

        if (ListenerSocket->
            HasPendingConnection(
                bPending)
            && bPending)
        {
            FSocket* NewSocket =

                ListenerSocket->
                Accept(
                    TEXT("LiveSyncConnection"));

            if (NewSocket)
            {
                if (NewSocket->
                    GetConnectionState()
                    == SCS_Connected)
                {
                    // Log remote address
                    TSharedRef<
                        FInternetAddr>
                    RemoteAddr =
                        ISocketSubsystem::
                            Get(
                                PLATFORM_SOCKETSUBSYSTEM)
                            ->CreateInternetAddr();

                    bool bGotPeerAddr =
                        NewSocket->
                            GetPeerAddress(
                                *RemoteAddr);

                    if (bGotPeerAddr)
                    {
                        UE_LOG(
                            LogLiveSync,
                            Log,
                            TEXT("Accept: Blender connected "
                                 "from %s:%d"),
                            *RemoteAddr->
                                ToString(false),
                            RemoteAddr->
                                GetPort());
                    }
                    else
                    {
                        UE_LOG(
                            LogLiveSync,
                            Log,
                            TEXT("Accept: Blender connected "
                                 "(unknown remote address)"));
                    }

                    ConnectionSocket =
                        NewSocket;

                    ConnectionSocket->
                        SetNoDelay(true);

                    WatchdogRestartCount = 0;
                    LastWatchdogRestartTime = 0.0;

                    BuildActorCache();

                    StartNetworkThread();
                }
                else
                {
                    UE_LOG(
                        LogLiveSync,
                        Warning,
                        TEXT("Accept: connection rejected "
                             "(state=%d)"),
                        static_cast<int32>(
                            NewSocket->
                                GetConnectionState()));

                    NewSocket->Close();

                    ISocketSubsystem::
                        Get(
                            PLATFORM_SOCKETSUBSYSTEM)
                        ->DestroySocket(
                            NewSocket);
        }
    }
    }
    }

    // =====================================================
    // STALE CONNECTION SAFETY
    // =====================================================

    if (ConnectionSocket &&
        ConnectionSocket->
        GetConnectionState()
        != SCS_Connected)
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("Stale Connection Removed"));

        StopNetworkThread();
    }

    // =====================================================
    // DETECT NETWORK THREAD EXIT
    // =====================================================

    if (ConnectionSocket &&
        NetworkRunnable &&
        NetworkRunnable->bThreadExited)
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("Detected thread exit, cleaning up"));

        StopNetworkThread();
    }

    // =====================================================
    // HEARTBEAT TIMEOUT CHECK
    // =====================================================

    float HeartbeatTimeoutVal =
        CVarLiveSyncHeartbeatTimeout.
            GetValueOnGameThread();

    if (ConnectionSocket &&
        LastHeartbeatTime > 0.0 &&
        FPlatformTime::Seconds() - LastHeartbeatTime >
        HeartbeatTimeoutVal)
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("Heartbeat timeout: closing connection"));

        StopNetworkThread();
    }

    // =====================================================
    // NETWORK THREAD WATCHDOG
    // =====================================================

    if (NetworkRunnable &&
        ConnectionSocket)
    {
        double Now =
            FPlatformTime::Seconds();

        double ThreadLoopTime =
            NetworkRunnable->
                LastThreadLoopTime.load(
                    std::memory_order_relaxed);

        double PacketRecvTime =
            NetworkRunnable->
                LastPacketReceiveTime.load(
                    std::memory_order_relaxed);

        bool bStarvation = false;
        bool bStall = false;

        if (PacketRecvTime > 0.0 &&
            Now - PacketRecvTime > 30.0)
        {
            bStarvation = true;
        }

        if (ThreadLoopTime > 0.0 &&
            Now - ThreadLoopTime > 35.0)
        {
            bStall = true;
        }

        if (bStall || bStarvation)
        {
            double BackoffDelay =
                GetWatchdogBackoff();

            double TimeSinceLastRestart =
                Now - LastWatchdogRestartTime;

            if (TimeSinceLastRestart >=
                BackoffDelay)
            {
                WatchdogRestartCount++;

                LastWatchdogRestartTime =
                    Now;

                UE_LOG(
                    LogLiveSync,
                    Error,
                    TEXT("Network thread watchdog: "
                         "starvation=%d stall=%d "
                         "restartCount=%d backoff=%.1fs"),
                    bStarvation ? 1 : 0,
                    bStall ? 1 : 0,
                    WatchdogRestartCount,
                    BackoffDelay);

                Stats.ReconnectCount.fetch_add(
                    1,
                    std::memory_order_relaxed);

                FReconnectEvent Evt;
                Evt.Timestamp =
                    FPlatformTime::Seconds();
                Evt.AttemptNumber =
                    WatchdogRestartCount;
                ReconnectHistory.Insert(Evt, 0);
                if (ReconnectHistory.Num() >
                    MAX_RECONNECT_HISTORY)
                {
                    ReconnectHistory.SetNum(
                        MAX_RECONNECT_HISTORY);
                }

                StopNetworkThread();
            }
        }
    }

    // =====================================================
    // SNAPSHOT TIMEOUT GUARD
    // =====================================================

    if (bInSnapshotBuild)
    {
        double Now =
            FPlatformTime::Seconds();

        bool bNoConnection =
            !ConnectionSocket ||
            ConnectionSocket->
                GetConnectionState()
                != SCS_Connected;

        double Elapsed =
            bNoConnection
                ? 0.0
                : (Now - SnapshotStartTime);

        if (bNoConnection ||
            Elapsed > 5.0)
        {
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("Snapshot timeout: bNoConnection=%d elapsed=%.2fs — aborting"),
                bNoConnection ? 1 : 0,
                Elapsed);

            AbortSnapshot();
        }
    }

    // =====================================================
    // PIPELINE
    // =====================================================
    //
    // CRITICAL:
    // The network thread ONLY enqueues packets.
    // ALL runtime processing occurs in the Tick pipeline below.
    // Removing or bypassing these stages will stall the entire sync system.
    // Do not reorder/remove without updating runtime lifecycle assumptions.
    //
    // Pipeline stages:
    //   1. ProcessQueuedPackets   — parse binary packets → update TransformStates
    //   2. EvictStaleTransformStates — TTL-based cleanup of unused state
    //   3. InterpolateTransforms  — drive actor transforms toward targets
    //   4. ResolvePendingAttachments — deferred parent-child attachment retry
    //   5. RecoverMissingActors   — re-spawn actors lost to desync
    //   6. ResolvePendingAssets   — late-binding asset mesh resolution
    // =====================================================

    // =====================================================
    // PROFILING / DEBUG INFRASTRUCTURE — PRESERVED INTENTIONALLY
    //
    // The TRACE_CPUPROFILER_EVENT_SCOPE markers and paired
    // UE_LOG(LogLiveSync, Log, TEXT("BEGIN/END ...")) traces
    // below are retained for future scalability debugging.
    //
    //   • TRACE_CPUPROFILER_EVENT_SCOPE — UE5 CPU profiler scopes
    //     (Unreal Insights / stat UE_LiveSync)
    //   • BEGIN/END UE_LOG markers — pipeline health validation
    //     (detect unbalanced stages, infinite loops, stuck frames)
    //
    // These are NOT dead code.  Do NOT remove.
    // =====================================================
    {
        TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_TickPipeline);

        UE_LOG(LogLiveSync, Log, TEXT("BEGIN Pipeline: ProcessQueuedPackets"));
        {
            TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessQueuedPackets);
            ProcessQueuedPackets();
        }
        UE_LOG(LogLiveSync, Log, TEXT("END   Pipeline: ProcessQueuedPackets"));

        EvictStaleTransformStates();

        if (!CVarLiveSyncDisableInterpolation.GetValueOnGameThread())
        {
            UE_LOG(LogLiveSync, Log, TEXT("BEGIN Pipeline: InterpolateTransforms"));
            {
                TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_InterpolateTransforms);
                InterpolateTransforms(DeltaTime);
            }
            UE_LOG(LogLiveSync, Log, TEXT("END   Pipeline: InterpolateTransforms"));
        }
        else
        {
            UE_LOG(LogLiveSync, Log, TEXT("SKIP  Pipeline: InterpolateTransforms (disabled by CVar)"));
        }

        if (!CVarLiveSyncDisableAttachmentResolution.GetValueOnGameThread())
        {
            UE_LOG(LogLiveSync, Log, TEXT("BEGIN Pipeline: ResolvePendingAttachments"));
            {
                TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ResolvePendingAttachments);
                ResolvePendingAttachments();
            }
            UE_LOG(LogLiveSync, Log, TEXT("END   Pipeline: ResolvePendingAttachments"));
        }
        else
        {
            UE_LOG(LogLiveSync, Log, TEXT("SKIP  Pipeline: ResolvePendingAttachments (disabled by CVar)"));
        }

        // =================================================
        // SEMANTIC HIERARCHY DEFERRED RESOLUTION (Phase 6D)
        // Runs AFTER runtime ResolvePendingAttachments so the
        // runtime graph is settled before semantic attachements
        // are applied (FINDING-009).
        // =================================================

        UE_LOG(LogLiveSync, Log, TEXT("BEGIN Pipeline: ResolveHierarchyAttachments"));
        {
            TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ResolveHierarchyAttachments);
            ResolveHierarchyAttachments();
        }
        UE_LOG(LogLiveSync, Log, TEXT("END   Pipeline: ResolveHierarchyAttachments"));

        if (!CVarLiveSyncDisableRecovery.GetValueOnGameThread())
        {
            UE_LOG(LogLiveSync, Log, TEXT("BEGIN Pipeline: RecoverMissingActors"));
            {
                TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_RecoverMissingActors);
                RecoverMissingActors();
            }
            UE_LOG(LogLiveSync, Log, TEXT("END   Pipeline: RecoverMissingActors"));
        }
        else
        {
            UE_LOG(LogLiveSync, Log, TEXT("SKIP  Pipeline: RecoverMissingActors (disabled by CVar)"));
        }

        if (!CVarLiveSyncDisableAssetResolution.GetValueOnGameThread())
        {
            UE_LOG(LogLiveSync, Log, TEXT("BEGIN Pipeline: ResolvePendingAssets"));
            {
                TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ResolvePendingAssets);
                ResolvePendingAssets();
            }
            UE_LOG(LogLiveSync, Log, TEXT("END   Pipeline: ResolvePendingAssets"));
        }
        else
        {
            UE_LOG(LogLiveSync, Log, TEXT("SKIP  Pipeline: ResolvePendingAssets (disabled by CVar)"));
        }
    }

    // =====================================================
    // HIERARCHY SAFETY VALIDATION
    // =====================================================
    // Periodic check for self-parenting, circular chains, invalid GUIDs.
    // Runs every ~300 ticks (~5s at 60fps) when connected.
    // =====================================================

    if (ConnectionSocket && VerboseFrameCounter % 300 == 0)
    {
        UE_LOG(LogLiveSync, Log, TEXT("BEGIN Periodic: ValidateHierarchy"));
        {
            TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ValidateHierarchy);
            ValidateHierarchy();
        }
        UE_LOG(LogLiveSync, Log, TEXT("END   Periodic: ValidateHierarchy"));
    }

    // =====================================================
    // PHASE 6H — SEMANTIC CONSISTENCY DIAGNOSTICS
    // =====================================================

    UE_LOG(LogLiveSync, Log, TEXT("BEGIN Periodic: TickPhase6H"));
    {
        TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_TickPhase6H);
        TickPhase6H(DeltaTime);
    }
    UE_LOG(LogLiveSync, Log, TEXT("END   Periodic: TickPhase6H"));

    // =====================================================
    // PHASE 6I — PERFORMANCE & SCALABILITY DIAGNOSTICS
    // =====================================================

    {
        TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_TickPhase6I);
        TickPhase6I(DeltaTime);
        CheckOverloadCondition();
    }

    // =====================================================
    // ROLLING METRICS (EMA, every tick)
    // =====================================================

    UE_LOG(LogLiveSync, Log, TEXT("BEGIN TickMetrics"));
    TickMetrics(DeltaTime);
    UE_LOG(LogLiveSync, Log, TEXT("END   TickMetrics"));

    // =====================================================
    // SAFETY MONITORS (flood detection, queue pressure)
    // =====================================================

    UE_LOG(LogLiveSync, Log, TEXT("BEGIN TickSafetyMonitors"));
    TickSafetyMonitors(DeltaTime);
    UE_LOG(LogLiveSync, Log, TEXT("END   TickSafetyMonitors"));

    // =====================================================
    // RUNTIME METRICS (every 30s in verbose mode)
    // =====================================================

    if (bEnableVerboseSyncLogs)
    {
        double Now =
            FPlatformTime::Seconds();

        if (Now - Stats.LastMetricsLogTime >=
            30.0)
        {
            Stats.LastMetricsLogTime =
                Now;

            UE_LOG(LogLiveSync, Log, TEXT("BEGIN LogRuntimeMetricsVerbose"));
            LogRuntimeMetricsVerbose();
            UE_LOG(LogLiveSync, Log, TEXT("END   LogRuntimeMetricsVerbose"));
        }
    }

    // =====================================================
    // COLLECTION REPLAY BUFFER HEALTH (Phase 6F Stage 7)
    // =====================================================

    CheckReplayBufferHealth();

    // =====================================================
    // DEBUG DRAW OVERLAY (editor only, off by default)
    // =====================================================

    if (bEnableDebugDraw)
    {
#if WITH_EDITOR
        UE_LOG(LogLiveSync, Log, TEXT("BEGIN DrawDebugOverlay"));
        DrawDebugOverlay();
        UE_LOG(LogLiveSync, Log, TEXT("END   DrawDebugOverlay"));
#endif
    }

    UE_LOG(LogLiveSync, Log, TEXT("END TRACE: Tick complete frame=%d"), VerboseFrameCounter);
    return true;
}


// =========================================================
// START NETWORK THREAD
// =========================================================

void UUELiveSyncSubsystem::
StartNetworkThread()
{
    // =====================================================
    // GUARD: prevent concurrent entry (atomic exchange)
    // =====================================================

    bool Expected = false;
    if (!bNetworkThreadStarting.
        compare_exchange_strong(
            Expected, true))
    {
        UE_LOG(
            LogLiveSync,
            Warning,
            TEXT("StartNetworkThread: already starting, "
                 "rejected"));
        return;
    }

    // =====================================================
    // GUARD: no socket
    // =====================================================

    if (!ConnectionSocket)
    {
        bNetworkThreadStarting = false;
        UE_LOG(
            LogLiveSync,
            Warning,
            TEXT("StartNetworkThread: no socket"));

        return;
    }

    // =====================================================
    // GUARD: prevent double-start
    // =====================================================

    if (NetworkThread ||
        NetworkRunnable)
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("StartNetworkThread: already running, "
                 "stopping old thread"));

        // Save socket before StopNetworkThread destroys it
        FSocket* SavedSocket =
            ConnectionSocket;

        StopNetworkThread();

        // Restore socket for the new thread
        ConnectionSocket =
            SavedSocket;
    }

    // =====================================================
    // CREATE RUNNABLE
    // =====================================================

    NetworkRunnable =
        new FLiveSyncRunnable(

            ConnectionSocket,

            &PacketQueue
        );

    NetworkRunnable->SetStats(
        &Stats);

    // Phase 6I.1 Stage 2: configurable recv poll timeout
    NetworkRunnable->SetRecvTimeoutMs(
        CVarLiveSyncRecvTimeoutMs.
            GetValueOnGameThread());

    PacketQueue.SetStats(
        &Stats);

    NetworkThread =

        FRunnableThread::Create(

            NetworkRunnable,

            TEXT("UE_LiveSync_Thread")
        );

    if (NetworkThread)
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("Network Thread Created"));

        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("Protocol: sig=0x%08X "
                 "magic=0x%08X LE "
                 "header=%d(V3)/%d(V2) "
                 "obj=%d(V3)/%d(V4+)/%d(del)/%d(asset) "
                 "max_packet=%d"),
            LIVE_SYNC_PROTOCOL_SIG,
            LIVE_SYNC_MAGIC,
            int32(sizeof(FPacketHeaderV3)),
            int32(sizeof(FPacketHeader)),
            LIVE_SYNC_V3_OBJECT_SIZE,
            LIVE_SYNC_V4_OBJECT_SIZE,
            LIVE_SYNC_V3_DELETE_SIZE,
            LIVE_SYNC_V5_ASSET_DEF_SIZE,
            LIVE_SYNC_MAX_PACKET_SIZE);

        bNetworkThreadStarting = false;
    }
    else
    {
        bNetworkThreadStarting = false;

        UE_LOG(
            LogLiveSync,
            Error,
            TEXT("Failed to create network thread"));
    }
}


// =========================================================
// STOP NETWORK THREAD
// =========================================================

void UUELiveSyncSubsystem::
StopNetworkThread()
{
    bNetworkThreadStarting = false;

    if (!NetworkRunnable &&
        !NetworkThread)
    {
        return;
    }

    uint64 StartCycles =
        FPlatformTime::Cycles64();

    if (NetworkRunnable)
    {
        NetworkRunnable->Stop();
    }

    uint64 AfterStopCycles =
        FPlatformTime::Cycles64();

    // CRITICAL: Shutdown BEFORE Close.
    //
    // On Linux, close() does NOT wake a blocked recv()/poll()
    // in another thread — the kernel keeps the socket alive
    // until all fd references are dropped.
    //
    // shutdown(SHUT_RDWR) sends TCP FIN/RST which unblocks
    // any blocked Wait() or Recv() with an error or EOF,
    // allowing the network thread to exit immediately.
    //
    // Without this, WaitForCompletion() below will DEADLOCK
    // the game thread.
    if (ConnectionSocket)
    {
        ConnectionSocket->Shutdown(
            ESocketShutdownMode::ReadWrite);
    }

    if (ConnectionSocket)
    {
        ConnectionSocket->Close();
    }

    uint64 AfterCloseCycles =
        FPlatformTime::Cycles64();

    if (NetworkThread)
    {
        NetworkThread->
            WaitForCompletion();

        delete NetworkThread;

        NetworkThread =
            nullptr;
    }

    uint64 AfterJoinCycles =
        FPlatformTime::Cycles64();

    if (NetworkRunnable)
    {
        delete NetworkRunnable;

        NetworkRunnable =
            nullptr;
    }

    // Destroy socket AFTER thread has exited
    if (ConnectionSocket)
    {
        ISocketSubsystem::
            Get(PLATFORM_SOCKETSUBSYSTEM)
            ->DestroySocket(
                ConnectionSocket);

        ConnectionSocket =
            nullptr;
    }

    // Reset stale state
    PacketQueue.Clear();

    TransformStates.Empty();

    PendingAttachments.Empty();

    MissingActorTracker.Empty();

    bInSnapshotBuild = false;
    SnapshotStartTime = 0.0;

    LastHeartbeatTime = 0.0;

    LastSequenceId = 0;

    GRenameSequences.LastSequence.Empty();
    // NOTE: GRenamePersistentLabel intentionally NOT cleared on disconnect/reconnect.
    // Persistent labels survive network restarts so HandleCreateObject can restore
    // authoritative labels immediately after actor spawn during snapshot rebuild.
    // Cleared only on ConsoleReset (full state reset).
    GVisibilitySequences.LastSequence.Empty();
    GHierarchySequences.LastSequence.Empty();
    PendingHierarchyAttachments.Empty();

    // Phase 6E: clear delete state — tombstones MUST NOT survive reconnect.
    // Replay after reconnect starts with clean tracker + empty tombstone map.
    GDeleteSequences.Clear();
    GDeleteTombstoneMap.Empty();
    GDeleteTombstoneOrder.Empty();
    DeferredDeleteQueue.Empty();

    // Phase 6F: clear collection sequence tracker — collections
    // Replay buffer: MUST NOT survive reconnect. Replay rebuilds from snapshot.
    GCollectionSequences.Clear();
    GCollectionMembership.Empty();
    GCollectionIdentities.Empty();
    GCollectionReplayBuffer.Empty();
    GCollectionReplaySequences.Empty();
    GCollectionReplayChecksums.Empty();

    // Phase 6G: clear unified world replay buffer
    GWorldReplayBuffer.Empty();
    GWorldSavedState.Clear();

    UE_LOG(LogLiveSync, Log,
        TEXT("[HIERARCHY] PendingHierarchyAttachments cleared (StopNetworkThread)"));

    UE_LOG(LogLiveSync, Log,
        TEXT("[DELETE][RECONNECT] Sequence tracker, tombstone map, order queue,"
             " and deferred queue cleared — tombstones do NOT survive reconnect"));

    uint64 EndCycles =
        FPlatformTime::Cycles64();

    double StopMs =
        FPlatformTime::
        ToMilliseconds64(
            EndCycles - StartCycles);

    double StopMs2 =
        FPlatformTime::
        ToMilliseconds64(
            AfterStopCycles -
            StartCycles);

    double CloseMs =
        FPlatformTime::
        ToMilliseconds64(
            AfterCloseCycles -
            AfterStopCycles);

    double JoinMs =
        FPlatformTime::
        ToMilliseconds64(
            AfterJoinCycles -
            AfterCloseCycles);

    double CleanupMs =
        FPlatformTime::
        ToMilliseconds64(
            EndCycles -
            AfterJoinCycles);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("StopNetworkThread: stop=%.2fms close=%.2fms join=%.2fms cleanup=%.2fms total=%.2fms"),
        StopMs2,
        CloseMs,
        JoinMs,
        CleanupMs,
        StopMs);
}


// =========================================================
// PROCESS QUEUE
// =========================================================

void UUELiveSyncSubsystem::
ProcessQueuedPackets()
{
    CHECK_GAME_THREAD();
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessQueuedPackets);
    FLiveSyncPacket Packet;

    TArray<FLiveSyncPacket>
        PacketsThisTick;

    int32 DequeueCount = 0;

    int32 MaxRate =
        CVarLiveSyncMaxPacketRate.
            GetValueOnGameThread();

    uint64 ProcessStartCycles =
        FPlatformTime::Cycles64();

    // Detect new drops since last tick → record overflow event
    int32 CurrentDrops =
        Stats.PacketsDropped.load(
            std::memory_order_relaxed);

    if (CurrentDrops > LastReportedDrops)
    {
        FOverflowEvent Evt;
        Evt.Timestamp =
            FPlatformTime::Seconds();
        Evt.QueueDepth =
            Stats.QueueDepthCurrent;
        OverflowHistory.Insert(Evt, 0);
        if (OverflowHistory.Num() >
            MAX_OVERFLOW_HISTORY)
        {
            OverflowHistory.SetNum(
                MAX_OVERFLOW_HISTORY);
        }

        LastReportedDrops = CurrentDrops;
    }

    while (
        PacketQueue.Dequeue(
            Packet))
    {
        DequeueCount++;

        if (DequeueCount <=
            MaxRate)
        {
            PacketsThisTick.Add(
                MoveTemp(Packet));
        }
    }

    if (DequeueCount > 0 && ShouldLogVerbose())
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("Dequeued: %d packets (processing %d)"),
            DequeueCount,
            PacketsThisTick.Num());
    }

    if (DequeueCount > MaxRate)
    {
        UE_LOG(
            LogLiveSync,
            Warning,
            TEXT("Packet rate exceeded: %d packets, "
                 "capping at %d, %d deferred to next tick"),
            DequeueCount,
            MaxRate,
            DequeueCount - MaxRate);
    }

    TSet<FGuid>
        SeenThisTick;

    // Phase 6H: reset per-tick creation tracking
    Phase6HCreatedThisTick.Empty();

    // Phase 6H: track burst peak
    Phase6HBurstTickPacketCount = PacketsThisTick.Num();
    if (Phase6HBurstTickPacketCount > Phase6HBurstTickPeak)
    {
        Phase6HBurstTickPeak = Phase6HBurstTickPacketCount;
        Stats.BurstPeakPacketsPerTick.store(
            Phase6HBurstTickPeak, std::memory_order_relaxed);
    }

    // Phase 6I: transform coalescing (latest-transform-wins per tick)
    // Counter is maintained inside CoalesceTransforms.
    {
        CoalesceTransforms(PacketsThisTick);
    }

    // Per-packet instrumentation counter
    static uint64 PacketProcessCounter = 0;

    for (const FLiveSyncPacket&
        Pkt : PacketsThisTick)
    {
        PacketProcessCounter++;

        // Phase 6H: validate packet ordering (Goal A)
        if (ConnectionSocket)
        {
            ValidatePacketOrdering(Pkt);
        }

        // Inline-read header fields for diagnostics
        int32 PktSize =
            Pkt.RawData.Num();

        uint32 PktMagic = 0;
        uint16 PktVersion = 0;
        uint8 PktType = 0;
        int32 PktObjCount = 0;

        if (PktSize >= 8)
        {
            FMemory::Memcpy(
                &PktMagic,
                Pkt.RawData.GetData(),
                sizeof(uint32));
            FMemory::Memcpy(
                &PktVersion,
                Pkt.RawData.GetData() + 4,
                sizeof(uint16));
        }

        if (PktSize >= 24)
        {
            PktType = *(
                Pkt.RawData.GetData() + 6);

            FMemory::Memcpy(
                &PktObjCount,
                Pkt.RawData.GetData() + 20,
                sizeof(int32));
        }

        uint64 PktBeginCycles =
            FPlatformTime::Cycles64();

        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("BEGIN packet #%llu: magic=0x%08X "
                 "ver=%u type=0x%02X objs=%d size=%d"),
            PacketProcessCounter,
            PktMagic,
            PktVersion,
            PktType,
            PktObjCount,
            PktSize);

        ProcessBinaryPacket(
            Pkt,
            &SeenThisTick);

        double PktElapsedMs =
            FPlatformTime::
            ToMilliseconds64(
                FPlatformTime::Cycles64() -
                PktBeginCycles);

        if (PktElapsedMs > 100.0)
        {
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("STALL: ProcessBinaryPacket took "
                     "%.1fms for packet #%llu"),
                PktElapsedMs,
                PacketProcessCounter);
        }
        else
        {
            UE_LOG(
                LogLiveSync,
                Log,
                TEXT("END packet #%llu: %.1fms"),
                PacketProcessCounter,
                PktElapsedMs);
        }
    }

    // Track processing stats
    int32 Processed =
        PacketsThisTick.Num();

    if (Processed > 0)
    {
        Stats.PacketsProcessed.fetch_add(
            Processed,
            std::memory_order_relaxed);

        uint64 ProcessEndCycles =
            FPlatformTime::Cycles64();

        double ProcessTimeMs =
            FPlatformTime::
            ToMilliseconds64(
                ProcessEndCycles -
                ProcessStartCycles);

        Stats.AvgProcessTimeMs =
            ProcessTimeMs /
            (double)Processed;
    }

    Stats.LastPacketTime =
        FPlatformTime::Seconds();
}


// =========================================================
// PROCESS BINARY PACKET
// =========================================================

void UUELiveSyncSubsystem::
ProcessBinaryPacket(
    const FLiveSyncPacket&
    Packet,
    TSet<FGuid>* SeenThisTick)
{
    CHECK_GAME_THREAD();
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessBinaryPacket);
    if (Packet.RawData.Num() <
        sizeof(FPacketHeader))
    {
        Stats.MalformedPackets.fetch_add(
            1, std::memory_order_relaxed);
        return;
    }

    // Re-check max packet size at game-thread boundary.
    // The network thread checks this before enqueue, but
    // this provides defense-in-depth against code path
    // bypasses.
    if (Packet.RawData.Num() >
        LIVE_SYNC_MAX_PACKET_SIZE)
    {
        Stats.MalformedPackets.fetch_add(
            1, std::memory_order_relaxed);
        return;
    }

    const uint8* PacketData =
        Packet.RawData.GetData();

    uint32 Magic;
    uint16 Version;

    FMemory::Memcpy(
        &Magic,
        PacketData,
        sizeof(uint32));

    FMemory::Memcpy(
        &Version,
        PacketData + sizeof(uint32),
        sizeof(uint16));

    // =====================================================
    // MAGIC CHECK
    // =====================================================

    if (Magic !=
        LIVE_SYNC_MAGIC)
    {
        Stats.MalformedPackets.fetch_add(
            1, std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // VERSION DISPATCH
    // =====================================================

    const uint8* Ptr = nullptr;
    const uint8* PacketEnd = nullptr;
    uint32 ObjectCount = 0;
    uint64 SequenceId = 0;
    uint8 PacketFlags = 0x00;

    if (Version >=
        LIVE_SYNC_VERSION_V3)
    {
        if (Packet.RawData.Num() <
            sizeof(FPacketHeaderV3))
        {
            Stats.MalformedPackets.fetch_add(
                1, std::memory_order_relaxed);
            return;
        }

        FPacketHeaderV3 HeaderV3;

        FMemory::Memcpy(
            &HeaderV3,
            PacketData,
            sizeof(FPacketHeaderV3));

        if (HeaderV3.PacketSize >
            Packet.RawData.Num())
        {
            Stats.MalformedPackets.fetch_add(
                1, std::memory_order_relaxed);
            return;
        }

        SequenceId =
            HeaderV3.SequenceId;

        ObjectCount =
            HeaderV3.ObjectCount;

        PacketFlags =
            HeaderV3.Flags;

        Ptr =
            PacketData +
            sizeof(FPacketHeaderV3);

        PacketEnd =
            PacketData +
            HeaderV3.PacketSize;

        // =================================================
        // FULL SNAPSHOT FLAG: clear stale state before
        // processing, giving us a clean slate
        // =================================================

        if (PacketFlags &
            PF_FullSnapshot)
        {
            TransformStates.Empty();

            if (SeenThisTick)
            {
                SeenThisTick->Empty();
            }

            if (bEnableVerboseSyncLogs)
            {
                UE_LOG(
                    LogLiveSync,
                    Log,
                    TEXT(
                        "Full snapshot: cleared"
                        " TransformStates + SeenThisTick"));
            }
        }
    }
    else if (Version ==
             LIVE_SYNC_VERSION)
    {
        if (Packet.RawData.Num() <
            sizeof(FPacketHeader))
        {
            Stats.MalformedPackets.fetch_add(
                1, std::memory_order_relaxed);
            return;
        }

        FPacketHeader Header;

        FMemory::Memcpy(
            &Header,
            PacketData,
            sizeof(FPacketHeader));

        if (Header.PacketSize >
            Packet.RawData.Num())
        {
            Stats.MalformedPackets.fetch_add(
                1, std::memory_order_relaxed);
            return;
        }

        SequenceId =
            Header.SequenceId;

        ObjectCount =
            Header.ObjectCount;

        Ptr =
            PacketData +
            sizeof(FPacketHeader);

        PacketEnd =
            PacketData +
            Header.PacketSize;
    }
    else
    {
        Stats.MalformedPackets.fetch_add(
            1, std::memory_order_relaxed);
        UE_LOG(
            LogLiveSync,
            Warning,
            TEXT("Unsupported protocol version: %u"),
            Version);

        return;
    }

    // =====================================================
    // SEQUENCE CHECK
    // =====================================================

    if (SequenceId <=
        LastSequenceId)
    {
        return;
    }

    LastSequenceId =
        SequenceId;

    // =====================================================
    // V3: PACKET TYPE DISPATCH
    // =====================================================

    uint8 PacketType = 0x01;

    if (Version >=
        LIVE_SYNC_VERSION_V3)
    {
        FMemory::Memcpy(
            &PacketType,
            PacketData +
                sizeof(uint32) +
                sizeof(uint16),
            sizeof(uint8));
    }

    if (ShouldLogVerbose())
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("Header: version=%u type=0x%02x flags=0x%02x "
                 "seq=%llu objects=%u"),
            Version,
            PacketType,
            PacketFlags,
            SequenceId,
            ObjectCount);
    }

    // =====================================================
    // PROTOCOL VALIDATION (V3)
    // =====================================================

    if (Version >= LIVE_SYNC_VERSION_V3 &&
        CVarLiveSyncValidateProtocol.GetValueOnGameThread())
    {
        static constexpr uint8 kValidTypes[] =
            { 0x01, 0x03, 0x04, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F };

        static constexpr uint8 kValidFlags[] =
            { 0x00, 0x01, 0x02, 0x03 };

        bool bValidType = false;

        for (int32 i = 0; i < sizeof(kValidTypes); i++)
        {
            if (PacketType == kValidTypes[i])
            {
                bValidType = true;
                break;
            }
        }

        if (!bValidType)
        {
            Stats.MalformedPackets.fetch_add(
                1, std::memory_order_relaxed);
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("Invalid packet type 0x%02x, skipping"),
                PacketType);

            return;
        }

        bool bValidFlags = false;

        for (int32 i = 0; i < sizeof(kValidFlags); i++)
        {
            if (PacketFlags == kValidFlags[i])
            {
                bValidFlags = true;
                break;
            }
        }

        if (!bValidFlags)
        {
            Stats.MalformedPackets.fetch_add(
                1, std::memory_order_relaxed);
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("Invalid packet flags 0x%02x, skipping"),
                PacketFlags);

            return;
        }
    }

    // =====================================================
    // HEARTBEAT: no objects to process
    // =====================================================

    if (PacketType == 0x07)
    {
        LastHeartbeatTime =
            FPlatformTime::Seconds();

        return;
    }

    // Phase 6I: track per-domain packet rates
    TrackPerDomainPacket(PacketType);

    // =====================================================
    // SNAPSHOT BOUNDARY MARKERS
    // =====================================================

    if (PacketType == PT_BeginSnapshot)
    {
        HandleBeginSnapshot();
        return;
    }

    if (PacketType == PT_EndSnapshot)
    {
        HandleEndSnapshot();
        return;
    }

    // =====================================================
    // PT_AssetDef (V5) — batch-handle all objects
    //
    // LAYOUT (33 bytes per object):
    //   0-15  GUID (4×uint32 LE)
    //   16-23 Identity Low  (uint64 LE, xxHash64 low)
    //   24-31 Identity High (uint64 LE, xxHash64 high)
    //   32    PrimitiveFallback (uint8)
    //
    // This is a SEPARATE wire format from V3+ transform objects.
    // The 1-byte primitive at offset 32 is part of this 33-byte
    // structure, NOT the V4+ extra byte. Do NOT mix these.
    // =====================================================

    if (PacketType == PT_AssetDef)
    {
        Stats.AssetDefsReceived.fetch_add(
            ObjectCount,
            std::memory_order_relaxed);

        for (uint32 i = 0;
             i < ObjectCount;
             i++)
        {
            if (Ptr + LIVE_SYNC_V5_ASSET_DEF_SIZE
                > PacketEnd)
            {
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                return;
            }

            FGuid Guid;
            FMemory::Memcpy(
                &Guid,
                Ptr,
                sizeof(FGuid));
            Ptr += sizeof(FGuid);

            uint64 IdentityHigh;
            uint64 IdentityLow;
            FMemory::Memcpy(
                &IdentityLow,
                Ptr,
                sizeof(uint64));
            Ptr += sizeof(uint64);
            FMemory::Memcpy(
                &IdentityHigh,
                Ptr,
                sizeof(uint64));
            Ptr += sizeof(uint64);

            uint8 PrimitiveFallback;
            FMemory::Memcpy(
                &PrimitiveFallback,
                Ptr,
                sizeof(uint8));
            Ptr += sizeof(uint8);

            HandleAssetDef(
                Guid,
                IdentityHigh,
                IdentityLow,
                PrimitiveFallback);
        }

        Stats.PacketsProcessed.fetch_add(
            1,
            std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // VISIBILITY PACKET (Phase 6 — Semantic Event)
    // =====================================================
    // Wire format (fixed 29 bytes per object):
    //   GUID(16) + bHidden(1) + seq(4) + ts(8)
    //
    // This is a SEPARATE handler from the transform object
    // loop below. Visibility is a discrete semantic editor
    // event, NOT a state-stream packet.
    //
    // Multiple visibility objects may be batched in one packet.
    // =====================================================

    if (PacketType == PT_Visibility)
    {
        TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessVisibilityPackets);

        for (uint32 i = 0; i < ObjectCount; i++)
        {
            if (Ptr + 29 > PacketEnd)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[VISIBILITY] Truncated packet: needs 29 bytes but only %lld available"),
                    (int64)(PacketEnd - Ptr));
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                return;
            }

            FGuid VisGuid;
            FMemory::Memcpy(&VisGuid, Ptr, sizeof(FGuid));
            Ptr += sizeof(FGuid);

            uint8 bHiddenRaw;
            FMemory::Memcpy(&bHiddenRaw, Ptr, sizeof(uint8));
            Ptr += sizeof(uint8);

            uint32 VisSequence;
            FMemory::Memcpy(&VisSequence, Ptr, sizeof(uint32));
            Ptr += sizeof(uint32);

            double VisTimestamp;
            FMemory::Memcpy(&VisTimestamp, Ptr, sizeof(double));
            Ptr += sizeof(double);

            EChangeOrigin Origin = EChangeOrigin::RemoteReplicated;
            if (bInSnapshotBuild)
            {
                Origin = EChangeOrigin::Replay;
            }

            HandleVisibility(
                VisGuid,
                bHiddenRaw != 0,
                VisSequence,
                VisTimestamp,
                Origin);
        }

        Stats.PacketsProcessed.fetch_add(
            1,
            std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // RENAME PACKET (Phase 6 — Semantic Event)
    // =====================================================
    // Wire format (variable length per object):
    //   GUID(16) + oldNameLen(2) + oldName(N) +
    //   newNameLen(2) + newName(M) + seq(4) + ts(8)
    //
    // This is a SEPARATE handler from the transform object
    // loop below. Rename is a semantic editor event, NOT a
    // state-stream packet. See 19-phase6-vertical-slice-rename.md
    //
    // Multiple rename objects may be batched in one packet.
    // =====================================================

    if (PacketType == PT_Rename)
    {
        TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessRenamePackets);

        for (uint32 i = 0; i < ObjectCount; i++)
        {
            if (Ptr + 18 > PacketEnd)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[RENAME] Truncated packet: cannot read GUID + old_name_length"));
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                return;
            }

            FGuid RenameGuid;
            FMemory::Memcpy(&RenameGuid, Ptr, sizeof(FGuid));
            Ptr += sizeof(FGuid);

            uint16 OldNameLen;
            FMemory::Memcpy(&OldNameLen, Ptr, sizeof(uint16));
            Ptr += 2;

            if (OldNameLen >
                LIVE_SYNC_MAX_NAME_LENGTH)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[RENAME] Old name too long: "
                         "%u > %u"),
                    OldNameLen,
                    (uint16)LIVE_SYNC_MAX_NAME_LENGTH);
                Stats.MalformedPackets.fetch_add(
                    1, std::memory_order_relaxed);
                return;
            }

            if (Ptr + OldNameLen > PacketEnd)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[RENAME] Truncated packet: old_name needs %u bytes"),
                    OldNameLen);
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                return;
            }

            FString OldName(OldNameLen,
                reinterpret_cast<const ANSICHAR*>(Ptr));
            Ptr += OldNameLen;

            if (Ptr + 2 > PacketEnd)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[RENAME] Truncated packet: cannot read new_name_length"));
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                return;
            }

            uint16 NewNameLen;
            FMemory::Memcpy(&NewNameLen, Ptr, sizeof(uint16));
            Ptr += 2;

            if (NewNameLen >
                LIVE_SYNC_MAX_NAME_LENGTH)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[RENAME] New name too long: "
                         "%u > %u"),
                    NewNameLen,
                    (uint16)LIVE_SYNC_MAX_NAME_LENGTH);
                Stats.MalformedPackets.fetch_add(
                    1, std::memory_order_relaxed);
                return;
            }

            if (Ptr + NewNameLen > PacketEnd)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[RENAME] Truncated packet: new_name needs %u bytes"),
                    NewNameLen);
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                return;
            }

            FString NewName(NewNameLen,
                reinterpret_cast<const ANSICHAR*>(Ptr));
            Ptr += NewNameLen;

            if (Ptr + 12 > PacketEnd)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[RENAME] Truncated packet: cannot read sequence+timestamp"));
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                return;
            }

            uint32 RenameSequence;
            FMemory::Memcpy(&RenameSequence, Ptr, sizeof(uint32));
            Ptr += 4;

            double RenameTimestamp;
            FMemory::Memcpy(&RenameTimestamp, Ptr, sizeof(double));
            Ptr += 8;

            EChangeOrigin Origin = EChangeOrigin::RemoteReplicated;
            if (bInSnapshotBuild)
            {
                Origin = EChangeOrigin::Replay;
            }

            HandleRename(RenameGuid, OldName, NewName,
                         RenameSequence, RenameTimestamp, Origin);
        }

        Stats.PacketsProcessed.fetch_add(
            1,
            std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // HIERARCHY PACKET (Phase 6D — Semantic Event)
    // =====================================================
    // Wire format (fixed 44 bytes per object):
    //   ChildGuid(16) + ParentGuid(16) + seq(4) + ts(8)
    //
    // This is a SEPARATE handler from the transform object
    // loop below. Hierarchy is a discrete semantic editor
    // event (attachment intent), NOT a state-stream packet.
    //
    // All-zero ParentGuid = detach-to-root semantic mutation.
    // See Docs/Architecture/24-phase6D-hierarchy-scope-lock.md
    //
    // Multiple hierarchy objects may be batched in one packet.
    // =====================================================

    if (PacketType == PT_Hierarchy)
    {
        TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessHierarchyPackets);

        for (uint32 i = 0; i < ObjectCount; i++)
        {
            if (Ptr + 44 > PacketEnd)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[HIERARCHY] Truncated packet: needs 44 bytes but only %lld available"),
                    (int64)(PacketEnd - Ptr));
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                return;
            }

            FGuid ChildGuid;
            FMemory::Memcpy(&ChildGuid, Ptr, sizeof(FGuid));
            Ptr += sizeof(FGuid);

            FGuid ParentGuid;
            FMemory::Memcpy(&ParentGuid, Ptr, sizeof(FGuid));
            Ptr += sizeof(FGuid);

            uint32 HierarchySequence;
            FMemory::Memcpy(&HierarchySequence, Ptr, sizeof(uint32));
            Ptr += sizeof(uint32);

            double HierarchyTimestamp;
            FMemory::Memcpy(&HierarchyTimestamp, Ptr, sizeof(double));
            Ptr += sizeof(double);

            EChangeOrigin Origin = EChangeOrigin::RemoteReplicated;
            if (bInSnapshotBuild)
            {
                Origin = EChangeOrigin::Replay;
            }

            HandleHierarchy(
                ChildGuid,
                ParentGuid,
                HierarchySequence,
                HierarchyTimestamp,
                Origin);
        }

        Stats.HierarchyPackets.fetch_add(
            1,
            std::memory_order_relaxed);
        Stats.PacketsProcessed.fetch_add(
            1,
            std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // PHASE 6E: LIFECYCLE/DELETE REPLICATION (PT_Delete_V5)
    // =====================================================
    // First identity-destruction semantic lane. Fixed 28-byte
    // payload per object: TargetGuid(16) + seq(4) + ts(8).
    //
    // Stage 3 implementation: parser isolation + log-only handler.
    // NO actor destruction, NO tombstone insertion, NO graph mutation.
    //
    // See Docs/Architecture/29-phase6E-lifecycle-scope-lock.md
    // =====================================================

    if (PacketType == 0x0E)
    {
        TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessDeletePackets);

        constexpr int32 DeleteObjSize = 28;
        const int32 DeleteCount = ObjectCount;
        const int32 PayloadSize = static_cast<int32>(PacketEnd - Ptr);

        // ---- BOUNDARY CHECK: payload multiple of 28 ----
        if (PayloadSize % DeleteObjSize != 0)
        {
            Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[DELETE] Malformed packet — payload %d bytes (expected multiple of %d)"),
                PayloadSize, DeleteObjSize);
            return;
        }

        // ---- PER-OBJECT PARSE LOOP ----
        for (uint32 i = 0; i < DeleteCount; i++)
        {
            if (Ptr + DeleteObjSize > PacketEnd)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[DELETE] Truncated packet: needs %d bytes but only %lld available"),
                    DeleteObjSize, (int64)(PacketEnd - Ptr));
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                return;
            }

            FGuid TargetGuid;
            FMemory::Memcpy(&TargetGuid, Ptr, sizeof(FGuid));
            Ptr += sizeof(FGuid);

            // ---- ALL-ZERO GUID CHECK ----
            if (!TargetGuid.IsValid())
            {
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[DELETE] Malformed packet — all-zero GUID at object index %d"), i);
                continue;
            }

            uint32 DeleteSequence;
            FMemory::Memcpy(&DeleteSequence, Ptr, sizeof(uint32));
            Ptr += sizeof(uint32);

            double DeleteTimestamp;
            FMemory::Memcpy(&DeleteTimestamp, Ptr, sizeof(double));
            Ptr += sizeof(double);

            EChangeOrigin DeleteOrigin = EChangeOrigin::RemoteReplicated;
            if (bInSnapshotBuild)
            {
                DeleteOrigin = EChangeOrigin::Replay;
            }

            // Stage 7: defer delete during snapshot rebuild;
            // processed after EndSnapshot in insertion order.
            if (bInSnapshotBuild)
            {
                if (DeferredDeleteQueue.Num() >= MAX_TOMBSTONE_ENTRIES)
                {
                    DeferredDeleteQueue.RemoveAt(0);
                    UE_LOG(LogLiveSync, Warning,
                        TEXT("[DELETE] Deferred queue overflow — "
                             "evicting oldest entry (maximum %u)"),
                        MAX_TOMBSTONE_ENTRIES);
                }
                DeferredDeleteQueue.Add({TargetGuid, DeleteSequence, DeleteTimestamp});
                Stats.DeleteDeferredDuringSnapshot.fetch_add(1, std::memory_order_relaxed);

                UE_LOG(LogLiveSync, Verbose,
                    TEXT("[DELETE] Deferred during snapshot: GUID=%s Seq=%u"),
                    *TargetGuid.ToString(EGuidFormats::Digits),
                    DeleteSequence);
            }
            else
            {
                HandleDelete(
                    TargetGuid,
                    DeleteSequence,
                    DeleteTimestamp,
                    DeleteOrigin);
            }
        }

        Stats.DeletePackets.fetch_add(
            1,
            std::memory_order_relaxed);
        Stats.PacketsProcessed.fetch_add(
            1,
            std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // PHASE 6F: COLLECTION/GROUP REPLICATION (PT_Collection)
    // =====================================================
    // Fifth semantic-event vertical slice: metadata-only grouping.
    // Stage 1–3 implementation: parser isolation + sequence tracking
    // + log-only handler. NO UE object mutation. NO actor lookup.
    // NO interaction with hierarchy, lifecycle, visibility, or rename.
    //
    // Wire format (30 bytes base per operation):
    //   TargetGuid(16) + OpType(1) + OpFlags(1) + seq(4) + ts(8)
    //
    // Membership operations append a CollectionGuid(16) for 46 bytes total.
    // Stage 1-3 parses base 30 bytes only — extended parsing deferred.
    //
    // Stage 4: Parse both variants.
    //   Identity ops (0x05-0x08): 30 bytes, no CollectionGuid
    //   Membership ops (0x01-0x04): 46 bytes, includes CollectionGuid
    //
    // See Docs/Architecture/38-phase6F-collection-scope-lock.md
    // and 39-phase6F-vertical-slice-collection.md
    // =====================================================

    if (PacketType == 0x0F)
    {
        TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessCollectionPackets);

        Stats.CollectionPacketsReceived.fetch_add(
            1,
            std::memory_order_relaxed);

        UE_LOG(LogLiveSync, Log,
            TEXT("[COLLECTION][DIAG] Packet received: ObjectCount=%u Flags=0x%02X"),
            ObjectCount, PacketFlags);

        // ---- PHASE 6F STAGE 5: Read collection sub-header if present ----
        // Backward-compatible: Stage 4 packets lack the sub-header.
        // The packet header flags byte indicates presence via bit 0.
        if (PacketFlags & COLLECTION_PACKET_FLAG_HAS_SUBHEADER)
        {
            if (Ptr + LIVE_SYNC_COLLECTION_SUBHEADER_SIZE > PacketEnd)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[COLLECTION] Truncated sub-header: needs %d bytes but only %lld available"),
                    LIVE_SYNC_COLLECTION_SUBHEADER_SIZE, (int64)(PacketEnd - Ptr));
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                return;
            }

            uint8 SubVersion;
            uint8 SubReserved;
            FMemory::Memcpy(&SubVersion, Ptr, sizeof(uint8));
            Ptr += sizeof(uint8);
            FMemory::Memcpy(&SubReserved, Ptr, sizeof(uint8));
            Ptr += sizeof(uint8);

            // Validate known version; reject unknown future versions
            if (SubVersion != COLLECTION_PACKET_VERSION_V1)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[COLLECTION] Unsupported packet version 0x%02X — rejecting"),
                    SubVersion);
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                return;
            }

            if (bEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Verbose,
                    TEXT("[COLLECTION][SUBHEADER] Version=0x%02X Reserved=0x%02X"),
                    SubVersion, SubReserved);
            }
        }

        const int32 CollectionCount = ObjectCount;

        for (uint32 i = 0; i < CollectionCount; i++)
        {
            // ---- Save object start for replay recording ----
            const uint8* ObjStart = Ptr;

            // ---- BOUNDARY CHECK: Can we read OpType? ----
            if (Ptr + LIVE_SYNC_COLLECTION_BASE_SIZE > PacketEnd)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[COLLECTION] Truncated packet: needs %d bytes but only %lld available (obj %u)"),
                    LIVE_SYNC_COLLECTION_BASE_SIZE, (int64)(PacketEnd - Ptr), i);
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                return;
            }

            // ---- Read TargetGuid ----
            FGuid TargetGuid;
            FMemory::Memcpy(&TargetGuid, Ptr, sizeof(FGuid));
            Ptr += sizeof(FGuid);

            // ---- ALL-ZERO GUID CHECK ----
            if (!TargetGuid.IsValid())
            {
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[COLLECTION] Malformed packet — all-zero GUID at object index %u"), i);
                continue;
            }

            // ---- Read OpType to determine variant ----
            uint8 OpType;
            FMemory::Memcpy(&OpType, Ptr, sizeof(uint8));
            Ptr += sizeof(uint8);

            // ---- VALIDATE OP-TYPE RANGE ----
            // Valid range: COLLECTION_OP_ADD (0x01) through
            // COLLECTION_OP_COLLECTION_REPARENT (0x08).
            if (OpType < 0x01 || OpType > 0x08)
            {
                Stats.MalformedPackets.fetch_add(
                    1, std::memory_order_relaxed);
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[COLLECTION] Invalid op-type "
                         "0x%02X at object index %u"),
                    OpType, i);
                return;
            }

            uint8 OpFlags;
            FMemory::Memcpy(&OpFlags, Ptr, sizeof(uint8));
            Ptr += sizeof(uint8);

            uint32 CollectionSequence;
            FMemory::Memcpy(&CollectionSequence, Ptr, sizeof(uint32));
            Ptr += sizeof(uint32);

            double CollectionTimestamp;
            FMemory::Memcpy(&CollectionTimestamp, Ptr, sizeof(double));
            Ptr += sizeof(double);

            // ---- Determine variant and parse CollectionGuid if needed ----
            // Membership ops (ADD/REMOVE/MOVE/CLEAR = 0x01-0x04) carry
            // an additional 16-byte CollectionGuid after the base 30 bytes.
            const bool bIsMembershipOp = (OpType >= 0x01 && OpType <= 0x04);
            FGuid CollectionGuid;

            if (bIsMembershipOp)
            {
                if (Ptr + sizeof(FGuid) > PacketEnd)
                {
                    UE_LOG(LogLiveSync, Warning,
                        TEXT("[COLLECTION] Truncated membership packet: needs 46 bytes but only %lld available (obj %u)"),
                        (int64)(PacketEnd - (Ptr - LIVE_SYNC_COLLECTION_BASE_SIZE)), i);
                    Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                    return;
                }

                FMemory::Memcpy(&CollectionGuid, Ptr, sizeof(FGuid));
                Ptr += sizeof(FGuid);
            }

            // ---- Record to replay ring buffer (Stage 5/6) ----
            const int32 ObjSize = bIsMembershipOp
                ? LIVE_SYNC_COLLECTION_MEMBERSHIP_SIZE
                : LIVE_SYNC_COLLECTION_BASE_SIZE;
            RecordCollectionReplayPayload(ObjStart, ObjSize, CollectionSequence);

            // ── Unified world replay recording (Phase 6G) ──
            {
                FWorldReplayEntry WorldEntry;
                WorldEntry.Domain = EWorldReplayDomain::Collection;
                WorldEntry.PacketType = 0x0F;
                WorldEntry.Guid = TargetGuid;
                WorldEntry.SecondaryGuid = CollectionGuid;
                WorldEntry.Sequence = CollectionSequence;
                WorldEntry.Timestamp = CollectionTimestamp;
                WorldEntry.Payload.Append(ObjStart, ObjSize);
                WorldEntry.Checksum = CollectionReplayChecksum(ObjStart, ObjSize);
                RecordWorldReplayEntry(WorldEntry);
            }

            HandleCollection(TargetGuid, OpType, OpFlags,
                             CollectionSequence, CollectionTimestamp,
                             bIsMembershipOp ? &CollectionGuid : nullptr);
        }

        Stats.PacketsProcessed.fetch_add(
            1,
            std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // UNKNOWN PACKET TYPE — skip gracefully
    // =====================================================

    if (PacketType != PT_Transform &&
        PacketType != PT_Create &&
        PacketType != PT_Delete)
    {
        Stats.MalformedPackets.fetch_add(
            1, std::memory_order_relaxed);
        UE_LOG(
            LogLiveSync,
            Warning,
            TEXT("Unknown packet type 0x%02X — skipping"),
            PacketType);
        return;
    }

    // =====================================================
    // OBJECT LOOP (with freeze guard)
    // =====================================================

    const uint8* LoopStartPtr = Ptr;

    uint64 LoopEntryTime =
        FPlatformTime::Cycles64();

    for (uint32 i = 0;
         i < ObjectCount;
         i++)
    {
        // =================================================
        // FREEZE GUARD: detect non-advancing pointer
        // =================================================

        if (i > 0 && Ptr <= LoopStartPtr)
        {
            UE_LOG(
                LogLiveSync,
                Error,
                TEXT("FREEZE GUARD: non-advancing pointer "
                     "at obj=%u/%u — Ptr=%p LoopStart=%p "
                     "type=0x%02X — aborting packet"),
                i,
                ObjectCount,
                (void*)Ptr,
                (void*)LoopStartPtr,
                PacketType);

            return;
        }

        LoopStartPtr = Ptr;

        // =================================================
        // STALL WATCHDOG: abort if loop runs > 5s
        // =================================================

        if (i % 100 == 0)
        {
            uint64 ElapsedCycles =
                FPlatformTime::Cycles64() -
                LoopEntryTime;

            double ElapsedMs =
                FPlatformTime::
                ToMilliseconds64(
                    ElapsedCycles);

            if (ElapsedMs > 5000.0)
            {
                UE_LOG(
                    LogLiveSync,
                    Error,
                    TEXT("FREEZE GUARD: object loop "
                         "exceeded 5s at obj=%u/%u "
                         "type=0x%02X — aborting packet"),
                    i,
                    ObjectCount,
                    PacketType);

                // Stack trace capture for stall root cause analysis
                ensureMsgf(false,
                    TEXT("STALL: object loop froze for %.0fms "
                         "at obj=%u/%u type=0x%02X"),
                    ElapsedMs,
                    i,
                    ObjectCount,
                    PacketType);

                return;
            }
        }

        int32 Remaining =
            static_cast<int32>(
                PacketEnd - Ptr);

        // =====================================================
        // WARNING: V4+ object layout is 81 bytes (80 V3 + 1 prim).
        // Blender always includes the primitive type byte.
        // Changing field order breaks wire compatibility.
        // Blender and UE layouts MUST remain byte-identical.
        // =====================================================

        if (Ptr + 16 > PacketEnd)
        {
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("Parse failure at obj=%u/%.0f%% "
                     "offset=%d remaining=%d "
                     "type=0x%02X ver=%u — "
                     "cannot read GUID (need 16 bytes)"),
                i,
                ObjectCount > 0
                    ? 100.0 * i / ObjectCount
                    : 0.0,
                static_cast<int32>(
                    Ptr - PacketData),
                Remaining,
                PacketType,
                Version);
            return;
        }

        FGuid Guid;

        if (Version >=
            LIVE_SYNC_VERSION_V3)
        {
            uint32 GuidParts[4];

            FMemory::Memcpy(
                GuidParts,
                Ptr,
                16);

            Guid = FGuid(
                GuidParts[0],
                GuidParts[1],
                GuidParts[2],
                GuidParts[3]);
        }
        else
        {
            FString GuidHex;

            for (int32 b = 0; b < 16; b++)
            {
                GuidHex += FString::Printf(
                    TEXT("%02x"),
                    Ptr[b]
                );
            }

            if (!FGuid::ParseExact(
                GuidHex,
                EGuidFormats::Digits,
                Guid))
            {
                UE_LOG(
                    LogLiveSync,
                    Warning,
                    TEXT("Parse failure at obj=%u "
                         "offset=%d — invalid V2 GUID hex"),
                    i,
                    static_cast<int32>(
                        Ptr - PacketData));
                return;
            }
        }

        Ptr += 16;

        // =================================================
        // DELETE PACKET: GUID only, no transform data
        // =================================================

        if (PacketType == 0x04)
        {
            HandleDeleteObject(Guid);
            continue;
        }

        // =====================================================
        // DEDUP: skip if already processed this tick
        // =====================================================
        // IMPORTANT: skip distance must match V4+ object size
        // including the primitive type byte (81 bytes total).
        // =====================================================

        if (SeenThisTick &&
            SeenThisTick->Contains(Guid))
        {
            if (Version >=
                LIVE_SYNC_VERSION_V3)
            {
                Ptr +=
                    sizeof(FVector3f) +
                    sizeof(FQuat4f) +
                    sizeof(FVector3f) +
                    sizeof(double) +
                    16;

                // V4+: primitive type byte for ALL packet types
                if (Version >=
                    LIVE_SYNC_VERSION_V4)
                {
                    Ptr += 1;
                }
            }
            else
            {
                Ptr +=
                    sizeof(FVector3f) +
                    sizeof(FQuat4f) +
                    sizeof(FVector3f);
            }

            continue;
        }

        if (SeenThisTick)
        {
            SeenThisTick->Add(Guid);
        }

        // =================================================
        // LOCATION (12 bytes)
        // =================================================

        Remaining = static_cast<int32>(
            PacketEnd - Ptr);

        if (Ptr + sizeof(FVector3f) >
            PacketEnd)
        {
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("Parse failure at obj=%u "
                     "offset=%d remaining=%d "
                     "type=0x%02X — "
                     "cannot read Location"),
                i,
                static_cast<int32>(
                    Ptr - PacketData),
                Remaining,
                PacketType);
            return;
        }

        FVector3f LocationFloat;

        FMemory::Memcpy(
            &LocationFloat,
            Ptr,
            sizeof(FVector3f));

        Ptr += sizeof(FVector3f);

        FVector Location(
            LocationFloat);

        if (Location.ContainsNaN())
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("Parse failure at obj=%u "
                     "offset=%d type=0x%02X — "
                     "Location contains NaN/Inf"),
                i,
                static_cast<int32>(
                    Ptr - PacketData - sizeof(FVector3f)),
                PacketType);
            Stats.MalformedPackets.fetch_add(
                1, std::memory_order_relaxed);
            return;
        }

        // =================================================
        // ROTATION (16 bytes)
        // =================================================

        Remaining = static_cast<int32>(
            PacketEnd - Ptr);

        if (Ptr + sizeof(FQuat4f) >
            PacketEnd)
        {
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("Parse failure at obj=%u "
                     "offset=%d remaining=%d "
                     "type=0x%02X — "
                     "cannot read Rotation"),
                i,
                static_cast<int32>(
                    Ptr - PacketData),
                Remaining,
                PacketType);
            return;
        }

        FQuat4f RotationFloat;

        FMemory::Memcpy(
            &RotationFloat,
            Ptr,
            sizeof(FQuat4f));

        Ptr += sizeof(FQuat4f);

        FQuat Rotation(
            RotationFloat);

        Rotation.Normalize();

        if (!FMath::IsFinite(Rotation.X) ||
            !FMath::IsFinite(Rotation.Y) ||
            !FMath::IsFinite(Rotation.Z) ||
            !FMath::IsFinite(Rotation.W))
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("Parse failure at obj=%u "
                     "offset=%d type=0x%02X — "
                     "Rotation contains NaN/Inf"),
                i,
                static_cast<int32>(
                    Ptr - PacketData - sizeof(FQuat4f)),
                PacketType);
            Stats.MalformedPackets.fetch_add(
                1, std::memory_order_relaxed);
            return;
        }

        // =================================================
        // SCALE (12 bytes)
        // =================================================

        Remaining = static_cast<int32>(
            PacketEnd - Ptr);

        if (Ptr + sizeof(FVector3f) >
            PacketEnd)
        {
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("Parse failure at obj=%u "
                     "offset=%d remaining=%d "
                     "type=0x%02X — "
                     "cannot read Scale"),
                i,
                static_cast<int32>(
                    Ptr - PacketData),
                Remaining,
                PacketType);
            return;
        }

        FVector3f ScaleFloat;

        FMemory::Memcpy(
            &ScaleFloat,
            Ptr,
            sizeof(FVector3f));

        Ptr += sizeof(FVector3f);

        FVector Scale(
            ScaleFloat);

        if (Scale.ContainsNaN())
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("Parse failure at obj=%u "
                     "offset=%d type=0x%02X — "
                     "Scale contains NaN/Inf"),
                i,
                static_cast<int32>(
                    Ptr - PacketData - sizeof(FVector3f)),
                PacketType);
            Stats.MalformedPackets.fetch_add(
                1, std::memory_order_relaxed);
            return;
        }

        // =================================================
        // V3: Timestamp (8 bytes) + Parent GUID (16 bytes)
        // =================================================

        FGuid ParentGuid;

        if (Version >=
            LIVE_SYNC_VERSION_V3)
        {
            Remaining = static_cast<int32>(
                PacketEnd - Ptr);

            if (Ptr + sizeof(double) >
                PacketEnd)
            {
                UE_LOG(
                    LogLiveSync,
                    Warning,
                    TEXT("Parse failure at obj=%u "
                         "offset=%d remaining=%d "
                         "type=0x%02X — "
                         "cannot read Timestamp"),
                    i,
                    static_cast<int32>(
                        Ptr - PacketData),
                    Remaining,
                    PacketType);
                return;
            }

            Ptr += sizeof(double);

            Remaining = static_cast<int32>(
                PacketEnd - Ptr);

            if (Ptr + 16 >
                PacketEnd)
            {
                UE_LOG(
                    LogLiveSync,
                    Warning,
                    TEXT("Parse failure at obj=%u "
                         "offset=%d remaining=%d "
                         "type=0x%02X — "
                         "cannot read Parent GUID"),
                    i,
                    static_cast<int32>(
                        Ptr - PacketData),
                    Remaining,
                    PacketType);
                return;
            }

            uint32 ParentParts[4];

            FMemory::Memcpy(
                ParentParts,
                Ptr,
                16);

            ParentGuid = FGuid(
                ParentParts[0],
                ParentParts[1],
                ParentParts[2],
                ParentParts[3]);

            Ptr += 16;
        }

        // =================================================
        // LOCAL → WORLD — CHILD SPAWN POSITION ONLY
        // =================================================
        // Phase 5B: do NOT convert local transforms to world
        // at ingestion time. Keep original values and pass
        // bIsLocalTransform downstream so UpdateTargetTransform
        // stores local-space values directly.
        //
        // For HandleCreateObject, compute world spawn position
        // separately so the actor starts at the correct world
        // location.

        bool bIsLocalTransform =
            (PacketFlags &
             PF_HasLocalTransform) != 0;

        // Save original values for UpdateTargetTransform
        FVector OriginalLocation = Location;
        FQuat OriginalRotation   = Rotation;
        FVector OriginalScale     = Scale;

        // Compute world spawn position for HandleCreateObject
        FVector SpawnLocation = Location;
        FQuat SpawnRotation   = Rotation;
        FVector SpawnScale     = Scale;

        if (bIsLocalTransform &&
            ParentGuid.IsValid())
        {
            AActor* ParentActor =
                FindActorFast(ParentGuid);

            if (ParentActor)
            {
                FTransform ChildLocal(
                    OriginalRotation,
                    OriginalLocation,
                    OriginalScale);

                FTransform ParentWorld =
                    ParentActor->
                    GetActorTransform();

                FTransform ChildWorld =
                    ChildLocal *
                    ParentWorld;

                SpawnLocation =
                    ChildWorld.GetLocation();

                SpawnRotation =
                    ChildWorld.GetRotation();

                SpawnScale =
                    ChildWorld.GetScale3D();
            }
            else
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[CREATE][DIAG] Parent not available for world-spawn computation — guid=%s parent=%s local transform will be used as world spawn (will correct on next interpolation tick)"),
                    *Guid.ToString(EGuidFormats::Digits),
                    *ParentGuid.ToString(EGuidFormats::Digits));
            }
        }

        // =================================================
        // PRIMITIVE TYPE BYTE (CREATE-only, after parent GUID)
        // =================================================

        uint8 PrimitiveType = LSP_Cube;

        // V4+: read primitive type byte for all packets.
        // Blender always includes it for V4+ (CREATE, TRANSFORM, etc).
        if (Version >= LIVE_SYNC_VERSION_V4 &&
            Ptr < PacketEnd)
        {
            PrimitiveType = *Ptr;
            Ptr += 1;

            if (PacketType == 0x03)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[CREATE][DIAG] PARSED primitive_type=0x%02X guid=%s obj=%u/%u ver=%u"),
                    PrimitiveType,
                    *Guid.ToString(EGuidFormats::Digits),
                    i, ObjectCount, Version);
            }
        }
        else if (Version >= LIVE_SYNC_VERSION_V4 && PacketType == 0x03)
        {
            UE_LOG(LogLiveSync, Error,
                TEXT("[CREATE][DIAG] V4+ CREATE packet has no primitive type byte at end of object! guid=%s obj=%u/%u"),
                *Guid.ToString(EGuidFormats::Digits),
                i, ObjectCount);
        }

        // =================================================
        // APPLY
        // =================================================

        if (PacketType == 0x03)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[CREATE][DIAG] DISPATCH guid=%s loc=%s rot=(%.4f,%.4f,%.4f,%.4f) scale=%s prim=0x%02X parent=%s local=%d"),
                *Guid.ToString(EGuidFormats::Digits),
                *SpawnLocation.ToString(),
                SpawnRotation.W, SpawnRotation.X, SpawnRotation.Y, SpawnRotation.Z,
                *SpawnScale.ToString(),
                PrimitiveType,
                *ParentGuid.ToString(EGuidFormats::Digits),
                bIsLocalTransform ? 1 : 0);

            HandleCreateObject(
                Guid,
                SpawnLocation,
                SpawnRotation,
                SpawnScale,
                ParentGuid,
                PrimitiveType,
                bIsLocalTransform);

            // Phase 6H: track created GUIDs for ordering validation
            Phase6HCreatedThisTick.Add(Guid);
        }

        UpdateTargetTransform(

            Guid,

            OriginalLocation,

            OriginalRotation,

            OriginalScale,

            ParentGuid,

            bIsLocalTransform
        );

        if (ShouldLogVerbose())
        {
            UE_LOG(
                LogLiveSync,
                Log,
                TEXT("Processed: %s loc=%s"),
                *Guid.ToString(
                    EGuidFormats::Digits),
                *Location.ToString());
        }
    }
}


// =========================================================
// UPDATE TARGET TRANSFORM
// =========================================================

void UUELiveSyncSubsystem::
UpdateTargetTransform(

    const FGuid& Guid,

    const FVector& Location,

    const FQuat& Rotation,

    const FVector& Scale,

    const FGuid& ParentGuid,

    bool bIsLocalTransform)
{
    CHECK_GAME_THREAD();
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_UpdateTargetTransform);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("BEGIN TRACE: UpdateTargetTransform guid=%s"),
        *Guid.ToString(
            EGuidFormats::Digits));

    FSyncTransformState& State =

        TransformStates.
        FindOrAdd(
            Guid);

    double CurrentTime =
        FPlatformTime::
        Seconds();

    // Save parent-change before overwriting
    bool bParentChanged =
        ParentGuid !=
        State.ParentGuid;

    if (!State.bInitialized)
    {
        if (bIsLocalTransform &&
            ParentGuid.IsValid())
        {
            // Attached child: initialize local-space state
            State.CurrentLocalLocation =
                Location;

            State.CurrentLocalRotation =
                Rotation;

            State.CurrentLocalScale =
                Scale;

            State.LocalTargetLocation =
                Location;

            State.LocalTargetRotation =
                Rotation;

            State.LocalTargetScale =
                Scale;

            State.bHasLocalTarget =
                true;

            // NON-AUTHORITATIVE
            // World-space current state for attached actors
            // is informational only.
            // Attached interpolation authority remains
            // local-space.

            AActor* Parent =
                FindActorFast(ParentGuid);

            if (Parent)
            {
                FTransform LocalXForm(
                    Rotation,
                    Location,
                    Scale);

                FTransform ParentWorld =
                    Parent->
                    GetActorTransform();

                FTransform WorldXForm =
                    LocalXForm *
                    ParentWorld;

                State.CurrentLocation =
                    WorldXForm.
                    GetLocation();

                State.CurrentRotation =
                    WorldXForm.
                    GetRotation();

                State.CurrentScale =
                    WorldXForm.
                    GetScale3D();
            }
        }
        else
        {
            // Root actor: initialize world-space state
            State.CurrentLocation =
                Location;

            State.CurrentRotation =
                Rotation;

            State.CurrentScale =
                Scale;

            State.bHasLocalTarget =
                false;
        }

        // Target* mirrors current for initial state
        if (State.bHasLocalTarget)
        {
            State.TargetLocation =
                State.CurrentLocation;

            State.TargetRotation =
                State.CurrentRotation;

            State.TargetScale =
                State.CurrentScale;
        }
        else
        {
            State.TargetLocation =
                Location;

            State.TargetRotation =
                Rotation;

            State.TargetScale =
                Scale;
        }

        State.ParentGuid =
            ParentGuid;

        State.bHasParent =
            ParentGuid.IsValid();

        State.LastUpdateTime =
            CurrentTime;

        State.bInitialized =
            true;

        State.bPendingSceneGraphWrite =
            true;
    }

    // =====================================================
    // THRESHOLD CHANGE DETECTION
    // =====================================================
    // Compare against authoritative target:
    //   children → LocalTarget* (local space)
    //   roots    → Target* (world space)

    float LocThreshold =
        CVarLiveSyncThresholdLocation.
            GetValueOnGameThread();

    float RotThreshold =
        CVarLiveSyncThresholdRotation.
            GetValueOnGameThread();

    float SclThreshold =
        CVarLiveSyncThresholdScale.
            GetValueOnGameThread();

    float LocationDistance;

    float RotationDistance;

    float ScaleDistance;

    if (State.bHasLocalTarget)
    {
        LocationDistance =
            FVector::Dist(
                State.LocalTargetLocation,
                Location);

        RotationDistance =
            State.LocalTargetRotation.
            AngularDistance(
                Rotation);

        ScaleDistance =
            FVector::Dist(
                State.LocalTargetScale,
                Scale);
    }
    else
    {
        LocationDistance =
            FVector::Dist(
                State.TargetLocation,
                Location);

        RotationDistance =
            State.TargetRotation.
            AngularDistance(
                Rotation);

        ScaleDistance =
            FVector::Dist(
                State.TargetScale,
                Scale);
    }

    bool bLocationChanged =
        LocationDistance >=
        LocThreshold;

    bool bRotationChanged =
        RotationDistance >=
        RotThreshold;

    bool bScaleChanged =
        ScaleDistance >=
        SclThreshold;

    if (!bLocationChanged &&
        !bRotationChanged &&
        !bScaleChanged)
    {
        if (bParentChanged)
        {
            State.ParentGuid =
                ParentGuid;

            State.bHasParent =
                ParentGuid.IsValid();

            if (!State.bHasParent)
            {
                DetachFromParent(Guid);
            }
            else
            {
                AttachToParent(
                    Guid,
                    ParentGuid);
            }

            State.bPendingSceneGraphWrite =
                true;
        }

        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("END TRACE: UpdateTargetTransform guid=%s (unchanged)"),
            *Guid.ToString(
                EGuidFormats::Digits));

        return;
    }

    // =====================================================
    // VELOCITY (root prediction only)
    // =====================================================

    if (!State.bHasLocalTarget)
    {
        double DeltaTime =
            CurrentTime -
            State.LastUpdateTime;

        if (bLocationChanged &&
            DeltaTime >
            SMALL_NUMBER)
        {
            FVector DeltaLocation =
                Location -
                State.TargetLocation;

            FVector NewVelocity =
                DeltaLocation /
                DeltaTime;

            State.Velocity =
                FMath::VInterpTo(
                    State.Velocity,
                    NewVelocity,
                    DeltaTime,
                    8.0f);

            State.Velocity =
                State.Velocity.
                GetClampedToMaxSize(
                    5000.0f);
        }
    }

    // =====================================================
    // STORE AUTHORITATIVE TARGET
    // =====================================================

    if (State.bHasLocalTarget || (bIsLocalTransform && ParentGuid.IsValid()))
    {
        // Root→child transition: initialize local-space state
        if (!State.bHasLocalTarget)
        {
            State.CurrentLocalLocation = Location;
            State.CurrentLocalRotation = Rotation;
            State.CurrentLocalScale = Scale;
            State.bHasLocalTarget = true;
        }

        // Attached child: store local target (authoritative)

        State.LocalTargetLocation =
            Location;

        State.LocalTargetRotation =
            Rotation;

        State.LocalTargetScale =
            Scale;

        // NON-AUTHORITATIVE
        // Derived debug/fallback world-space cache only.
        // May become stale after parent movement.

        {
            AActor* Parent =
                FindActorFast(ParentGuid);

            if (Parent)
            {
                FTransform LocalXForm(
                    Rotation,
                    Location,
                    Scale);

                FTransform ParentWorld =
                    Parent->
                    GetActorTransform();

                FTransform WorldXForm =
                    LocalXForm *
                    ParentWorld;

                State.TargetLocation =
                    WorldXForm.
                    GetLocation();

                State.TargetRotation =
                    WorldXForm.
                    GetRotation();

                State.TargetScale =
                    WorldXForm.
                    GetScale3D();
            }
        }
    }
    else
    {
        // Root actor: store world-space target

        State.TargetLocation =
            Location;

        State.TargetRotation =
            Rotation;

        State.TargetScale =
            Scale;
    }

    State.ParentGuid =
        ParentGuid;

    State.bHasParent =
        ParentGuid.IsValid();

    State.LastUpdateTime =
        CurrentTime;

    State.bPendingSceneGraphWrite =
        true;

    // =====================================================
    // HANDLE PARENT CHANGE
    // =====================================================
    // Uses bParentChanged saved before State was modified.

    if (bParentChanged)
    {
        if (!State.bHasParent)
        {
            DetachFromParent(Guid);
        }
        else
        {
            AttachToParent(
                Guid,
                ParentGuid);
        }
    }
    // NOTE: unconditional AttachToParent on every tick
    // for bHasParent actors is REMOVED.
    // AttachToParent is only called on parent change.
    // Idempotency is maintained by AttachToParent's
    // internal guard against same-parent re-attach.

    // =====================================================
    // VERBOSE AUTHORITY-PATH LOGGING
    // =====================================================

    if (bEnableVerboseSyncLogs)
    {
        if (State.bHasLocalTarget &&
            State.bHasParent)
        {
            UE_LOG(
                LogLiveSync,
                Log,
                TEXT(
                    "Authority: child=%s"
                    " local target updated"),
                *Guid.ToString(
                    EGuidFormats::Digits));
        }
    }

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("END TRACE: UpdateTargetTransform guid=%s"),
        *Guid.ToString(
            EGuidFormats::Digits));
}


// =========================================================
// INTERPOLATION
// =========================================================

void UUELiveSyncSubsystem::
InterpolateTransforms(
    float DeltaTime)
{
    CHECK_GAME_THREAD();
        TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_InterpolateTransforms);

    static int InterpFreezeIter = 0;
    InterpFreezeIter++;

    UE_LOG(LogLiveSync, Log, TEXT("BEGIN InterpolateTransforms freezeIter=%d"), InterpFreezeIter);

    // Skip interpolation during snapshot build — all transforms
    // will be bulk-applied when EndSnapshot is received
    if (bInSnapshotBuild)
    {
        UE_LOG(LogLiveSync, Log, TEXT("END   InterpolateTransforms (snapshot build, skip)"));
        return;
    }

    // =====================================================
    // ISOLATION: Skip transform application if disabled
    // =====================================================

    if (CVarLiveSyncDisableTransformApply.GetValueOnGameThread())
    {
        UE_LOG(LogLiveSync, Log, TEXT("END   InterpolateTransforms (disabled by CVar)"));
        return;
    }

    const float PredictionTime =
        0.012f;

    int32 InterpMode =
        CVarLiveSyncInterpMode.
            GetValueOnGameThread();

    float SnapDist =
        CVarLiveSyncInterpSnap.
            GetValueOnGameThread();

    int MissingCount = 0;
    int ConvergedCount = 0;
    int SnapCount = 0;
    int InterpCount = 0;

    uint64 InterpLoopEntryCycles =
        FPlatformTime::Cycles64();

    int32 InterpIterationIndex = 0;

    for (auto& Pair :
        TransformStates)
    {
        InterpIterationIndex++;

        // =================================================
        // WATCHDOG: abort if InterpolateTransforms runs > 5s
        // =================================================

        if (InterpIterationIndex % 100 == 0)
        {
            double InterpElapsedMs =
                FPlatformTime::
                ToMilliseconds64(
                    FPlatformTime::Cycles64() -
                    InterpLoopEntryCycles);

            if (InterpElapsedMs > 5000.0)
            {
                UE_LOG(
                    LogLiveSync,
                    Error,
                    TEXT("FREEZE GUARD: InterpolateTransforms"
                         " exceeded 5s at iter=%d — aborting"),
                    InterpIterationIndex);

                ensureMsgf(false,
                    TEXT("STALL: InterpolateTransforms froze"
                         " for %.0fms at iter=%d"),
                    InterpElapsedMs,
                    InterpIterationIndex);

                return;
            }
        }

        const FGuid& Guid =
            Pair.Key;

        FSyncTransformState&
            State =
            Pair.Value;

        TWeakObjectPtr<AActor>*
            ActorPtr =

            ActorCache.Find(
                Guid);

        if (!ActorPtr ||
            !ActorPtr->IsValid())
        {
            MissingCount++;
            continue;
        }

        AActor* Actor =
            ActorPtr->Get();

        // =====================================================
        // STALE ACTOR VALIDATION
        // =====================================================

        if (!IsValid(Actor))
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("STALE ACTOR: guid=%s actor=%p IsValid=%d — skipping"),
                *Guid.ToString(EGuidFormats::Digits),
                (void*)Actor,
                IsValid(Actor) ? 1 : 0);
            MissingCount++;
            continue;
        }

        UWorld* ActorWorld = Actor->GetWorld();
        if (!ActorWorld || ActorWorld != GetWorld())
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("STALE WORLD: guid=%s actor=%p world=%p expected=%p — skipping"),
                *Guid.ToString(EGuidFormats::Digits),
                (void*)Actor,
                (void*)ActorWorld,
                (void*)GetWorld());
            MissingCount++;
            continue;
        }

        // =====================================================
        // PER-ACTOR TRACE
        // =====================================================

        UE_LOG(LogLiveSync, Log,
            TEXT("BEGIN transform apply guid=%s actor=%p iter=%d total=%d"),
            *Guid.ToString(EGuidFormats::Digits),
            (void*)Actor,
            InterpIterationIndex,
            TransformStates.Num());

        // ---------------------------------------------------
        // CONVERGENCE CHECK
        // ---------------------------------------------------

        bool bLocationConverged;
        bool bRotationConverged;
        bool bScaleConverged;

        if (State.bHasLocalTarget && State.bHasParent)
        {
            // Attached child: check local-space convergence
            bLocationConverged =
                FVector::Dist(
                    State.CurrentLocalLocation,
                    State.LocalTargetLocation)
                < KINDA_SMALL_NUMBER;

            bRotationConverged =
                State.CurrentLocalRotation.
                    Equals(
                        State.LocalTargetRotation,
                        0.01f);

            bScaleConverged =
                FVector::Dist(
                    State.CurrentLocalScale,
                    State.LocalTargetScale)
                < KINDA_SMALL_NUMBER;
        }
        else
        {
            // Root actor: check world-space convergence
            bLocationConverged =
                FVector::Dist(
                    State.CurrentLocation,
                    State.TargetLocation)
                < KINDA_SMALL_NUMBER;

            bRotationConverged =
                State.CurrentRotation.
                    Equals(
                        State.TargetRotation,
                        0.01f);

            bScaleConverged =
                FVector::Dist(
                    State.CurrentScale,
                    State.TargetScale)
                < KINDA_SMALL_NUMBER;
        }

        if (bLocationConverged &&
            bRotationConverged &&
            bScaleConverged)
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("END   transform apply guid=%s (converged)"),
                *Guid.ToString(EGuidFormats::Digits));
            ConvergedCount++;
            continue;
        }

        // ---------------------------------------------------
        // ATTACHED ACTOR PATH
        // ---------------------------------------------------
        // Attached actors never continuously drive world-space
        // transforms. UE attachment propagation owns child world
        // motion. Local-space interpolation updates internal
        // state only.

        if (State.bHasLocalTarget && State.bHasParent)
        {
            if (InterpMode == 0)
            {
                // Direct-set: snap local state to target
                State.CurrentLocalLocation =
                    State.LocalTargetLocation;

                State.CurrentLocalRotation =
                    State.LocalTargetRotation;

                State.CurrentLocalScale =
                    State.LocalTargetScale;
            }
            else if (FVector::Dist(
                State.CurrentLocalLocation,
                State.LocalTargetLocation) < SnapDist)
            {
                // Snap when close
                State.CurrentLocalLocation =
                    State.LocalTargetLocation;

                State.CurrentLocalRotation =
                    State.LocalTargetRotation;

                State.CurrentLocalScale =
                    State.LocalTargetScale;

                SnapCount++;
            }
            else
            {
                // Smooth interpolation in local space
                State.CurrentLocalLocation =
                    FMath::VInterpTo(
                        State.CurrentLocalLocation,
                        State.LocalTargetLocation,
                        DeltaTime,
                        State.AdaptiveInterpSpeed);

                // Patch 3: Normalize after Slerp to prevent
                // quaternion drift across long-running sessions.
                State.CurrentLocalRotation =
                    FQuat::Slerp(
                        State.CurrentLocalRotation,
                        State.LocalTargetRotation,
                        DeltaTime * 12.0f).
                    GetNormalized();

                // Assumes stable mostly-uniform hierarchical
                // scale behavior. Correct non-uniform
                // hierarchical scale propagation is deferred.
                State.CurrentLocalScale =
                    State.LocalTargetScale;

                InterpCount++;
            }

            // Scene graph write only when pending
            if (State.bPendingSceneGraphWrite)
            {
                AActor* Parent =
                    FindActorFast(
                        State.ParentGuid);

                if (Parent)
                {
                    FTransform LocalXForm(
                        State.CurrentLocalRotation,
                        State.CurrentLocalLocation,
                        State.CurrentLocalScale);

                    FTransform WorldXForm =
                        LocalXForm *
                        Parent->
                        GetActorTransform();

                    UE_LOG(LogLiveSync, Log,
                        TEXT("  BEGIN SetActorTransform guid=%s (attached child)"),
                        *Guid.ToString(EGuidFormats::Digits));

                    if (ValidateTransform(WorldXForm, Guid, TEXT("AttachedChild")))
                    {
                        UE_LOG(LogLiveSync, Log,
                            TEXT("  DO SetActorTransform guid=%s (attached child)"),
                            *Guid.ToString(EGuidFormats::Digits));
                        if (!CVarLiveSyncBypassSetActorTransform.GetValueOnGameThread())
                        {
                            Actor->SetActorTransform(WorldXForm);
                        }
                        else
                        {
                            UE_LOG(LogLiveSync, Log,
                                TEXT("  BYPASS SetActorTransform guid=%s (attached child)"),
                                *Guid.ToString(EGuidFormats::Digits));
                        }
                    }
                    else
                    {
                        UE_LOG(LogLiveSync, Error,
                            TEXT("  SKIP SetActorTransform guid=%s (invalid transform)"),
                            *Guid.ToString(EGuidFormats::Digits));
                    }

                    UE_LOG(LogLiveSync, Log,
                        TEXT("  END   SetActorTransform guid=%s (attached child)"),
                        *Guid.ToString(EGuidFormats::Digits));

                    // NON-AUTHORITATIVE
                    // Update debug world cache for diagnostics
                    State.CurrentLocation =
                        WorldXForm.GetLocation();

                    State.CurrentRotation =
                        WorldXForm.GetRotation();

                    State.CurrentScale =
                        WorldXForm.GetScale3D();

                    State.bPendingSceneGraphWrite =
                        false;

                    InterpCount++;
                }
                else
                {
                    UE_LOG(LogLiveSync, Warning,
                        TEXT("[HIERARCHY][PENDING] Cannot apply scene graph write for guid=%s — parent=%s not found in ActorCache, will retry next frame"),
                        *Guid.ToString(EGuidFormats::Digits),
                        *State.ParentGuid.ToString(EGuidFormats::Digits));
                }
            }

            // =================================================
            // DRIFT DIAGNOSTICS (verbose-only, thresholded)
            // =================================================

            if (bEnableVerboseSyncLogs)
            {
                AActor* Parent =
                    FindActorFast(
                        State.ParentGuid);

                if (Parent)
                {
                    FTransform LocalXForm(
                        State.CurrentLocalRotation,
                        State.CurrentLocalLocation,
                        State.CurrentLocalScale);

                    FTransform ExpectedWorld =
                        LocalXForm *
                        Parent->
                        GetActorTransform();

                    double Err =
                        FVector::Dist(
                            Actor->
                            GetActorLocation(),
                            ExpectedWorld.
                            GetLocation());

                    if (Err > 0.01)
                    {
                        UE_LOG(
                            LogLiveSync,
                            Verbose,
                            TEXT(
                                "Drift: child=%s"
                                " err=%.4f"),
                            *Guid.ToString(
                                EGuidFormats::
                                Digits),
                            Err);
                    }

                    if (Err >
                        HierarchyDiag.
                        MaxWorldErrorDistance)
                    {
                        HierarchyDiag.
                        MaxWorldErrorDistance =
                            Err;
                    }

                    HierarchyDiag.
                        WorldErrorDistance =
                            Err;
                }
            }

            continue;
        }

        // ---------------------------------------------------
        // ROOT ACTOR PATH
        // ---------------------------------------------------

        // =================================================
        // DIRECT-SET MODE (zero lag)
        // =================================================

        if (InterpMode == 0)
        {
            State.CurrentLocation =
                State.TargetLocation;

            State.CurrentRotation =
                State.TargetRotation;

            State.CurrentScale =
                State.TargetScale;

            UE_LOG(LogLiveSync, Log,
                TEXT("  BEGIN SetActorTransform guid=%s (root direct-set)"),
                *Guid.ToString(EGuidFormats::Digits));

            FTransform RootDirectXForm(
                State.CurrentRotation,
                State.CurrentLocation,
                State.CurrentScale);

            if (ValidateTransform(RootDirectXForm, Guid, TEXT("RootDirectSet")))
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("  DO SetActorTransform guid=%s (root direct-set)"),
                    *Guid.ToString(EGuidFormats::Digits));
                if (!CVarLiveSyncBypassSetActorTransform.GetValueOnGameThread())
                {
                    Actor->SetActorTransform(RootDirectXForm);
                }
                else
                {
                    UE_LOG(LogLiveSync, Log,
                        TEXT("  BYPASS SetActorTransform guid=%s (root direct-set)"),
                        *Guid.ToString(EGuidFormats::Digits));
                }
            }
            else
            {
                UE_LOG(LogLiveSync, Error,
                    TEXT("  SKIP SetActorTransform guid=%s (invalid transform)"),
                    *Guid.ToString(EGuidFormats::Digits));
            }

            UE_LOG(LogLiveSync, Log,
                TEXT("  END   SetActorTransform guid=%s (root direct-set)"),
                *Guid.ToString(EGuidFormats::Digits));

            InterpCount++;
            continue;
        }

        // =================================================
        // SNAP WHEN CLOSE
        // =================================================

        float DistToTarget =

            FVector::Dist(

                State.CurrentLocation,

                State.TargetLocation);

        if (DistToTarget < SnapDist)
        {
            State.CurrentLocation =
                State.TargetLocation;

            State.CurrentRotation =
                State.TargetRotation;

            State.CurrentScale =
                State.TargetScale;

            UE_LOG(LogLiveSync, Log,
                TEXT("  BEGIN SetActorTransform guid=%s (root snap)"),
                *Guid.ToString(EGuidFormats::Digits));

            FTransform RootSnapXForm(
                State.CurrentRotation,
                State.CurrentLocation,
                State.CurrentScale);

            if (ValidateTransform(RootSnapXForm, Guid, TEXT("RootSnap")))
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("  DO SetActorTransform guid=%s (root snap)"),
                    *Guid.ToString(EGuidFormats::Digits));
                if (!CVarLiveSyncBypassSetActorTransform.GetValueOnGameThread())
                {
                    Actor->SetActorTransform(RootSnapXForm);
                }
                else
                {
                    UE_LOG(LogLiveSync, Log,
                        TEXT("  BYPASS SetActorTransform guid=%s (root snap)"),
                        *Guid.ToString(EGuidFormats::Digits));
                }
            }
            else
            {
                UE_LOG(LogLiveSync, Error,
                    TEXT("  SKIP SetActorTransform guid=%s (invalid transform)"),
                    *Guid.ToString(EGuidFormats::Digits));
            }

            UE_LOG(LogLiveSync, Log,
                TEXT("  END   SetActorTransform guid=%s (root snap)"),
                *Guid.ToString(EGuidFormats::Digits));

            SnapCount++;
            continue;
        }

        // =================================================
        // SMOOTH INTERPOLATION (adaptive speed)
        // =================================================

        FVector PredictedLocation =

            State.TargetLocation +

            (State.Velocity *
             PredictionTime);

        float Distance =

            FVector::Dist(

                State.CurrentLocation,

                PredictedLocation);

        State.AdaptiveInterpSpeed =

            FMath::
            GetMappedRangeValueClamped(

                FVector2D(
                    0.0f,
                    100.0f),

                FVector2D(
                    16.0f,
                    40.0f),

                Distance);

        State.CurrentLocation =

            FMath::VInterpTo(

                State.CurrentLocation,

                PredictedLocation,

                DeltaTime,

                State.
                AdaptiveInterpSpeed);

        // Patch 3: Normalize after Slerp to prevent
        // quaternion drift across long-running sessions.
        State.CurrentRotation =

            FQuat::Slerp(

                State.CurrentRotation,

                State.TargetRotation,

                DeltaTime *
                12.0f).
            GetNormalized();

        State.CurrentScale =

            State.TargetScale;

        UE_LOG(LogLiveSync, Log,
            TEXT("  BEGIN SetActorTransform guid=%s (root smooth)"),
            *Guid.ToString(EGuidFormats::Digits));

        FTransform RootSmoothXForm(
            State.CurrentRotation,
            State.CurrentLocation,
            State.CurrentScale);

        if (ValidateTransform(RootSmoothXForm, Guid, TEXT("RootSmooth")))
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("  DO SetActorTransform guid=%s (root smooth)"),
                *Guid.ToString(EGuidFormats::Digits));
            if (!CVarLiveSyncBypassSetActorTransform.GetValueOnGameThread())
            {
                Actor->SetActorTransform(RootSmoothXForm);
            }
            else
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("  BYPASS SetActorTransform guid=%s (root smooth)"),
                    *Guid.ToString(EGuidFormats::Digits));
            }
        }
        else
        {
            UE_LOG(LogLiveSync, Error,
                TEXT("  SKIP SetActorTransform guid=%s (invalid transform)"),
                *Guid.ToString(EGuidFormats::Digits));
        }

        UE_LOG(LogLiveSync, Log,
            TEXT("  END   SetActorTransform guid=%s (root smooth)"),
            *Guid.ToString(EGuidFormats::Digits));

        UE_LOG(LogLiveSync, Log,
            TEXT("END   transform apply guid=%s"),
            *Guid.ToString(EGuidFormats::Digits));

        InterpCount++;
    }

    UE_LOG(LogLiveSync, Log, TEXT("END   InterpolateTransforms freezeIter=%d"), InterpFreezeIter);

    if (ShouldLogVerbose())
    {
        int Total = TransformStates.Num();

        UE_LOG(
            LogLiveSync,
            Log,
            TEXT(
                "Transform states: total=%d missing=%d converged=%d snap=%d interp=%d interpMode=%d"),
            Total,
            MissingCount,
            ConvergedCount,
            SnapCount,
            InterpCount,
            InterpMode
        );
    }
}


// =========================================================
// EVICT STALE TRANSFORM STATES (TTL)
// =========================================================

void UUELiveSyncSubsystem::
EvictStaleTransformStates()
{
    double StateTTL =
        CVarLiveSyncStateTTL.
            GetValueOnGameThread();

    double CurrentTime =
        FPlatformTime::Seconds();

    TArray<FGuid> StaleGuids;

    for (const auto& Pair :
        TransformStates)
    {
        if (CurrentTime -
            Pair.Value.LastUpdateTime
            > StateTTL)
        {
            StaleGuids.Add(
                Pair.Key);
        }
    }

    if (StaleGuids.Num() == 0)
    {
        return;
    }

    for (const FGuid& Guid :
        StaleGuids)
    {
        TransformStates.Remove(
            Guid);

        ActorCache.Remove(
            Guid);
    }

    if (ShouldLogVerbose())
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("Evicted %d stale transform states"),
            StaleGuids.Num());
    }
}


// =========================================================
// BUILD ACTOR CACHE
// =========================================================

void UUELiveSyncSubsystem::
BuildActorCache()
{
    CHECK_GAME_THREAD();
    UWorld* World =
        GetWorld();

    if (!World)
    {
        return;
    }

    int ScannedActors = 0;

    for (TActorIterator<AActor>
        It(World);
        It;
        ++It)
    {
        AActor* Actor =
            *It;

        if (!Actor)
        {
            continue;
        }

        ScannedActors++;
        TryCacheActor(Actor);
    }

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("BuildActorCache: scanned %d actors, found %d with GUID tags"),
        ScannedActors,
        ActorCache.Num());
}

void UUELiveSyncSubsystem::
TryCacheActor(
    AActor* Actor)
{
    if (!Actor)
    {
        return;
    }

    bool bFoundTag = false;

    for (const FName& Tag :
        Actor->Tags)
    {
        FString TagString =
            Tag.ToString();

        FString Prefix =
            TEXT("LiveSync_GUID=");

        if (!TagString.
            StartsWith(
                Prefix))
        {
            continue;
        }

        bFoundTag = true;

        FString GuidString =

            TagString.RightChop(
                Prefix.Len());

        FGuid Guid;

        if (!FGuid::ParseExact(

            GuidString,

            EGuidFormats::Digits,

            Guid))
        {
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("TryCacheActor: %s has bad GUID tag: %s"),
                *Actor->GetName(),
                *GuidString);

            bFoundTag = false;

            continue;
        }

        if (ActorCache.Contains(
            Guid))
        {
            return;
        }

        ActorCache.Add(
            Guid,
            Actor);

        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("Cached Actor %s | GUID=%s"),

            *Actor->GetName(),

            *Guid.ToString(EGuidFormats::Digits)
        );

        break;
    }
}

void UUELiveSyncSubsystem::
OnActorSpawned(
    AActor* Actor)
{
    TryCacheActor(Actor);
}

FGuid UUELiveSyncSubsystem::
FindGuidForActor(
    AActor* Actor) const
{
    if (!Actor)
    {
        return FGuid();
    }

    FString Prefix =
        TEXT("LiveSync_GUID=");

    for (const FName& Tag :
        Actor->Tags)
    {
        FString TagString =
            Tag.ToString();

        if (!TagString.
            StartsWith(
                Prefix))
        {
            continue;
        }

        FString GuidString =

            TagString.RightChop(
                Prefix.Len());

        FGuid Guid;

        if (FGuid::ParseExact(

            GuidString,

            EGuidFormats::Digits,

            Guid))
        {
            return Guid;
        }
    }

    return FGuid();
}


void UUELiveSyncSubsystem::
OnActorDestroyed(
    AActor* Actor)
{
    FGuid Guid =
        FindGuidForActor(Actor);

    if (Guid.IsValid())
    {
        TransformStates.Remove(
            Guid);

        // Phase 7A: Clean asset metadata on external actor destruction
        AssetMetadata.Remove(Guid);
        PendingAssetQueue.Remove(Guid);

        if (bEnableVerboseSyncLogs)
        {
            UE_LOG(
                LogLiveSync,
                Log,
                TEXT(
                    "OnActorDestroyed: removed TransformState"
                    " for %s | GUID=%s"),
                *Actor->GetName(),
                *Guid.ToString(
                    EGuidFormats::Digits));
        }
    }

    // Also clean ActorCache (both by pointer and by GUID)
    for (auto It =
        ActorCache.CreateIterator();
        It;
        ++It)
    {
        if (!It.Value().IsValid() ||
            It.Value().Get() == Actor)
        {
            It.RemoveCurrent();
        }
    }
}


// =========================================================
// FIND ACTOR
// =========================================================

AActor* UUELiveSyncSubsystem::
FindActorFast(
    const FGuid& Guid)
{
    TWeakObjectPtr<AActor>*
        Found =

        ActorCache.Find(
            Guid);

    if (!Found)
    {
        return nullptr;
    }

    return Found->Get();
}


// =========================================================
// HIERARCHY — ATTACH TO PARENT
// =========================================================

void UUELiveSyncSubsystem::
AttachToParent(
    const FGuid& Guid,
    const FGuid& ParentGuid)
{
    CHECK_GAME_THREAD();

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("BEGIN TRACE: AttachToParent child=%s parent=%s"),
        *Guid.ToString(
            EGuidFormats::Digits),
        *ParentGuid.ToString(
            EGuidFormats::Digits));

    if (!ParentGuid.IsValid())
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("END TRACE: AttachToParent child=%s (no parent)"),
            *Guid.ToString(
                EGuidFormats::Digits));
        return;
    }
    {
        return;
    }

    // =====================================================
    // Self-parent rejection
    // =====================================================

    if (Guid == ParentGuid)
    {
    UE_LOG(
        LogLiveSync,
        Warning,
        TEXT(
            "Self-parent rejection:"
            " child=parent=%s"),
        *Guid.ToString(
            EGuidFormats::Digits));
    return;
}

    // =====================================================
    // ISOLATION: Skip attachment if DisableAttachment is set
    // =====================================================

    if (CVarLiveSyncDisableAttachment.GetValueOnGameThread())
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("AttachToParent: DISABLED via CVar "
                 "for child=%s parent=%s"),
            *Guid.ToString(
                EGuidFormats::Digits),
            *ParentGuid.ToString(
                EGuidFormats::Digits));
        return;
    }

AActor* Child =
    FindActorFast(Guid);

    if (!Child)
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("END TRACE: AttachToParent child=%s (child not found)"),
            *Guid.ToString(
                EGuidFormats::Digits));
        return;
    }

    AActor* Parent =
        FindActorFast(
            ParentGuid);

    if (!Parent || bInSnapshotBuild)
    {
        if (bInSnapshotBuild)
        {
            // During snapshot build, defer all attachments
            FPendingAttachment NewEntry;
            NewEntry.Child = Guid;
            NewEntry.Parent = ParentGuid;
            NewEntry.RetryFrames = 0;
            NewEntry.CreatedTime =
                FPlatformTime::Seconds();

            PendingAttachments.Add(
                NewEntry);
        }
        else if (!Parent)
        {
            // Push to deferred retry queue
            FPendingAttachment NewEntry;
            NewEntry.Child = Guid;
            NewEntry.Parent = ParentGuid;
            NewEntry.RetryFrames = 0;
            NewEntry.CreatedTime =
                FPlatformTime::Seconds();

            PendingAttachments.Add(
                NewEntry);

            if (bEnableVerboseSyncLogs)
            {
                UE_LOG(
                    LogLiveSync,
                    Log,
                    TEXT(
                        "AttachToParent: child=%s"
                        " parent=%s not yet cached,"
                        " deferred"),
                    *Guid.ToString(
                        EGuidFormats::Digits),
                    *ParentGuid.ToString(
                        EGuidFormats::Digits));
            }
        }

        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("END TRACE: AttachToParent child=%s (deferred)"),
            *Guid.ToString(
                EGuidFormats::Digits));
        return;
    }

    // Guard: already attached to correct parent
    if (Child->GetAttachParentActor()
        == Parent)
    {
        // Attached while already attached to same parent = churn
        HierarchyDiag.AttachmentChurnCount++;
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("END TRACE: AttachToParent child=%s (already attached)"),
            *Guid.ToString(
                EGuidFormats::Digits));
        return;
    }

    // =====================================================
    // Max hierarchy depth walk with cycle detection
    // =====================================================

    int32 MaxDepth =
        CVarLiveSyncMaxDepth.
            GetValueOnGameThread();

    if (MaxDepth > 0)
    {
        int32 Depth = 0;
        AActor* Probe = Parent;

        while (Probe)
        {
            // Patch 4: Explicit cycle detection.
            // Avoid relying solely on depth overflow.
            if (Probe == Child)
            {
        UE_LOG(
            LogLiveSync,
            Warning,
            TEXT(
                "Hierarchy cycle detected:"
                " child=%s parent=%s"),
            *Guid.ToString(
                EGuidFormats::Digits),
            *ParentGuid.ToString(
                EGuidFormats::Digits));
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("END TRACE: AttachToParent child=%s (cycle detected)"),
            *Guid.ToString(
                EGuidFormats::Digits));
        return;
    }

            Depth++;

            if (Depth > MaxDepth)
            {
        UE_LOG(
            LogLiveSync,
            Warning,
            TEXT(
                "Exceeded max hierarchy depth"
                " %d: child=%s parent=%s"),
            MaxDepth,
            *Guid.ToString(
                EGuidFormats::Digits),
            *ParentGuid.ToString(
                EGuidFormats::Digits));
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("END TRACE: AttachToParent child=%s (depth exceeded)"),
            *Guid.ToString(
                EGuidFormats::Digits));
        return;
    }

            Probe =
                Probe->
                GetAttachParentActor();
        }
    }

    // =====================================================
    // STALE ACTOR VALIDATION BEFORE ATTACH
    // =====================================================

    if (!IsValid(Child) || !IsValid(Parent))
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("AttachToParent: stale actor child=%s valid=%d | parent=%s valid=%d — aborting"),
            *Guid.ToString(EGuidFormats::Digits),
            IsValid(Child) ? 1 : 0,
            *ParentGuid.ToString(EGuidFormats::Digits),
            IsValid(Parent) ? 1 : 0);
        return;
    }

    // =====================================================
    // OSCILLATING PARENT DETECTION
    // =====================================================

    {
        static TMap<FGuid, FGuid> LastAttachedParent;
        FGuid* PrevParent = LastAttachedParent.Find(Guid);
        if (PrevParent && *PrevParent != ParentGuid)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("AttachToParent: parent oscillating guid=%s was=%s now=%s"),
                *Guid.ToString(EGuidFormats::Digits),
                *PrevParent->ToString(EGuidFormats::Digits),
                *ParentGuid.ToString(EGuidFormats::Digits));
        }
        LastAttachedParent.Add(Guid, ParentGuid);
    }

    UE_LOG(LogLiveSync, Log,
        TEXT("  BEGIN AttachToActor child=%s parent=%s"),
        *Guid.ToString(EGuidFormats::Digits),
        *ParentGuid.ToString(EGuidFormats::Digits));

    Child->AttachToActor(
        Parent,
        FAttachmentTransformRules::
            KeepWorldTransform);

    UE_LOG(LogLiveSync, Log,
        TEXT("  END   AttachToActor child=%s parent=%s"),
        *Guid.ToString(EGuidFormats::Digits),
        *ParentGuid.ToString(EGuidFormats::Digits));

    // Verbose authority transition log
    if (bEnableVerboseSyncLogs)
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT(
                "Authority: child=%s"
                " entering local mode"
                " parent=%s"),
            *Guid.ToString(
                EGuidFormats::Digits),
            *ParentGuid.ToString(
                EGuidFormats::Digits));
    }

    HierarchyDiag.ReattachCount++;

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT(
            "Attached child=%s to parent=%s"),
        *Guid.ToString(
            EGuidFormats::Digits),
        *ParentGuid.ToString(
            EGuidFormats::Digits));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("END TRACE: AttachToParent child=%s parent=%s"),
        *Guid.ToString(
            EGuidFormats::Digits),
        *ParentGuid.ToString(
            EGuidFormats::Digits));
}


// =========================================================
// HIERARCHY — DETACH FROM PARENT
// =========================================================

void UUELiveSyncSubsystem::
DetachFromParent(
    const FGuid& Guid)
{
    CHECK_GAME_THREAD();
    AActor* Actor =
        FindActorFast(Guid);

    if (!Actor)
    {
        return;
    }

    // Log BEFORE state changes for diagnostic clarity
    const bool bWasAttached =
        Actor->GetAttachParentActor() != nullptr;

    FGuid OldParentGuid;
    bool bOldHasLocalTarget = false;
    if (FSyncTransformState* State =
        TransformStates.Find(Guid))
    {
        OldParentGuid = State->ParentGuid;
        bOldHasLocalTarget = State->bHasLocalTarget;
    }

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT(
            "[DETACH][DIAG] Child guid=%s"),
        *Guid.ToString(
            EGuidFormats::Digits));
    UE_LOG(
        LogLiveSync,
        Log,
        TEXT(
            "[DETACH][DIAG] Old parent=%s was_attached=%d"),
        *OldParentGuid.ToString(
            EGuidFormats::Digits),
        bWasAttached ? 1 : 0);

    // Detach from UE scene graph if still attached.
    // HandleHierarchy may have already detached the actor —
    // this check prevents redundant DetachFromActor while
    // ensuring state cleanup still runs below.
    if (bWasAttached)
    {
        Actor->DetachFromActor(
            FDetachmentTransformRules::
                KeepWorldTransform);

        UE_LOG(
            LogLiveSync,
            Log,
            TEXT(
                "[DETACH][DIAG] Attach state cleared"));
    }
    else
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT(
                "[DETACH][DIAG] Already detached"
                " (HandleHierarchy) — state cleanup only"));
    }

    // ----------------------------------------------------
    // STATE CLEANUP — runs even when actor was already
    // detached by HandleHierarchy. Without this, stale
    // bHasLocalTarget=true causes all subsequent world
    // transforms to be misrouted through the child branch.
    // ----------------------------------------------------

    if (FSyncTransformState* State =
        TransformStates.Find(Guid))
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT(
                "[DETACH][DIAG] Clearing local authority state"
                " (bHasLocalTarget=%d)"),
            bOldHasLocalTarget ? 1 : 0);

        State->bHasLocalTarget =
            false;

        // Re-seed authoritative world state from actor.
        // Prevents frozen transforms after detach.
        if (AActor* DetachedActor =
            FindActorFast(Guid))
        {
            State->CurrentLocation =
                DetachedActor->
                GetActorLocation();

            State->CurrentRotation =
                DetachedActor->
                GetActorQuat();

            State->CurrentScale =
                DetachedActor->
                GetActorScale3D();

            UE_LOG(
                LogLiveSync,
                Log,
                TEXT(
                    "[DETACH][DIAG] New world authority"
                    " final world transform=(%s)"),
                *DetachedActor->
                    GetActorLocation().
                    ToString());
        }

        State->bPendingSceneGraphWrite =
            true;

        UE_LOG(
            LogLiveSync,
            Log,
            TEXT(
                "[DETACH][DIAG] bHasLocalTarget=%d"
                " ParentGuid valid=%d"),
            State->bHasLocalTarget ? 1 : 0,
            State->ParentGuid.IsValid() ? 1 : 0);
    }

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT(
            "Detached actor=%s from parent"),
        *Guid.ToString(
            EGuidFormats::Digits));
}


// =========================================================
// HANDLE CREATE OBJECT
// =========================================================

void UUELiveSyncSubsystem::
HandleCreateObject(

    const FGuid& Guid,

    const FVector& Location,

    const FQuat& Rotation,

    const FVector& Scale,

    const FGuid& ParentGuid,

    uint8 PrimitiveType,

    bool bIsLocalTransform)
{
    CHECK_GAME_THREAD();
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_HandleCreateObject);
    UWorld* World = GetWorld();

    if (!World)
    {
        UE_LOG(LogLiveSync, Error,
            TEXT("[CREATE][DIAG] GetWorld() returned nullptr — aborting"));
        return;
    }

    // =====================================================
    // COMPREHENSIVE ENTRY DIAGNOSTICS
    // =====================================================

    {
        const FString WorldName = World->GetName();
        const FString LevelName = World->GetCurrentLevel() ? World->GetCurrentLevel()->GetName() : TEXT("None");
        const TCHAR* WorldTypeStr = TEXT("Unknown");
        switch (World->WorldType)
        {
            case EWorldType::None:        WorldTypeStr = TEXT("None");        break;
            case EWorldType::Game:        WorldTypeStr = TEXT("Game");        break;
            case EWorldType::Editor:      WorldTypeStr = TEXT("Editor");      break;
            case EWorldType::PIE:         WorldTypeStr = TEXT("PIE");         break;
            case EWorldType::EditorPreview: WorldTypeStr = TEXT("EditorPreview"); break;
            case EWorldType::GamePreview:   WorldTypeStr = TEXT("GamePreview");   break;
            case EWorldType::GameRPC:     WorldTypeStr = TEXT("GameRPC");     break;
            case EWorldType::Inactive:    WorldTypeStr = TEXT("Inactive");    break;
        }

        const int32 ActorCachePreCount = ActorCache.Num();

        UE_LOG(LogLiveSync, Warning,
            TEXT("[CREATE][DIAG] ENTRY guid=%s prim=0x%02X loc=%s rot=(%.4f,%.4f,%.4f,%.4f) scale=%s parent=%s local=%d ts=%.3f"),
            *Guid.ToString(EGuidFormats::Digits),
            PrimitiveType,
            *Location.ToString(),
            Rotation.W, Rotation.X, Rotation.Y, Rotation.Z,
            *Scale.ToString(),
            *ParentGuid.ToString(EGuidFormats::Digits),
            bIsLocalTransform ? 1 : 0,
            FPlatformTime::Seconds());

        UE_LOG(LogLiveSync, Warning,
            TEXT("[CREATE][DIAG] ENTRY world=%s type=%s level=%s ActorCache=%d"),
            *WorldName, WorldTypeStr, *LevelName, ActorCachePreCount);

        // Validate parsed payload integrity
        if (!Guid.IsValid())
        {
            UE_LOG(LogLiveSync, Error,
                TEXT("[CREATE][DIAG] INVALID GUID (all zero) — aborting"));
            return;
        }

        if (Scale.IsZero() || Scale.X <= 0.0f || Scale.Y <= 0.0f || Scale.Z <= 0.0f)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[CREATE][DIAG] SUSPICIOUS scale=%s — proceeding"),
                *Scale.ToString());
        }

        if (Location.SizeSquared() > 1e12f)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[CREATE][DIAG] SUSPICIOUS location magnitude=%f — proceeding"),
                Location.Size());
        }
    }

    // =====================================================
    // VALIDATE SPAWN TRANSFORM
    // =====================================================

    if (Location.ContainsNaN() || Rotation.ContainsNaN() || Scale.ContainsNaN())
    {
        UE_LOG(LogLiveSync, Error,
            TEXT("HandleCreateObject: NaN transform guid=%s loc=%s rot=(%.4f,%.4f,%.4f,%.4f) scale=%s — aborting"),
            *Guid.ToString(EGuidFormats::Digits),
            *Location.ToString(),
            Rotation.W, Rotation.X, Rotation.Y, Rotation.Z,
            *Scale.ToString());
        return;
    }

    // =====================================================
    // STALE WORLD VALIDATION
    // =====================================================

    if (World != GetWorld())
    {
        UE_LOG(LogLiveSync, Error,
            TEXT("HandleCreateObject: stale world guid=%s — aborting"),
            *Guid.ToString(EGuidFormats::Digits));
        return;
    }

    // =====================================================
    // (STAGE 4) REJECT: Tombstoned GUID — object was deleted
    // =====================================================

    if (IsTombstoned(Guid))
    {
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[CREATE][TOMBSTONE] GUID=%s — blocked by tombstone"),
            *Guid.ToString(EGuidFormats::Digits));
        Stats.DeleteTombstoneRejections.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    AActor* Existing =
        FindActorFast(Guid);

    if (Existing)
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("GUID match %s: found existing actor %s, skip spawn"),
            *Guid.ToString(
                EGuidFormats::Digits),
            *Existing->GetName());

        return;
    }

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("GUID match %s: NOT found in cache, spawning new actor"),
        *Guid.ToString(
            EGuidFormats::Digits));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("BEGIN TRACE: HandleCreateObject guid=%s"),
        *Guid.ToString(
            EGuidFormats::Digits));

    // =====================================================
    // ISOLATION: Skip spawn if DisableSpawning is set
    // =====================================================

    if (CVarLiveSyncDisableSpawning.GetValueOnGameThread())
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("HandleCreateObject: spawn DISABLED via CVar "
                 "for GUID=%s (location=%s)"),
            *Guid.ToString(
                EGuidFormats::Digits),
            *Location.ToString());
        return;
    }

    FActorSpawnParameters SpawnParams;

    SpawnParams.SpawnCollisionHandlingOverride =
        ESpawnActorCollisionHandlingMethod::
            AlwaysSpawn;

    // =====================================================
    // Step 1: Spawn actor at world position.
    // Location/Rotation/Scale are already world-space
    // (ProcessBinaryPacket computed world for children).
    // =====================================================

    uint64 SpawnBeginCycles =
        FPlatformTime::Cycles64();

    AActor* NewActor =

        World->SpawnActor<AActor>(

            AActor::StaticClass(),

            FTransform(
                Rotation,
                Location,
                Scale),

            SpawnParams);

    double SpawnMs =
        FPlatformTime::
        ToMilliseconds64(
            FPlatformTime::Cycles64() -
            SpawnBeginCycles);

    if (!NewActor)
    {
        const FString WorldName = World->GetName();
        const FString ActorClass = AActor::StaticClass()->GetName();

        UE_LOG(
            LogLiveSync,
            Error,
            TEXT("[CREATE][DIAG] SPAWN FAILED guid=%s class=%s world=%s spawnMs=%.1f"),
            *Guid.ToString(
                EGuidFormats::Digits),
            *ActorClass,
            *WorldName,
            SpawnMs);

        // Log possible reasons
        UWorld* InnerWorld = World;
        if (InnerWorld->WorldType == EWorldType::EditorPreview ||
            InnerWorld->WorldType == EWorldType::Inactive)
        {
            UE_LOG(LogLiveSync, Error,
                TEXT("[CREATE][DIAG] SPAWN FAILED REASON: world type is %d (EditorPreview/Inactive)"),
                (int32)InnerWorld->WorldType);
        }

        if (InnerWorld->GetCurrentLevel() == nullptr ||
            InnerWorld->GetCurrentLevel()->bIsVisible == false)
        {
            UE_LOG(LogLiveSync, Error,
                TEXT("[CREATE][DIAG] SPAWN FAILED REASON: level is null or not visible"));
        }

        return;
    }

    if (SpawnMs > 50.0)
    {
        UE_LOG(
            LogLiveSync,
            Warning,
            TEXT("[CREATE][DIAG] STALL: SpawnActor took %.1fms "
                 "for GUID=%s"),
            SpawnMs,
            *Guid.ToString(
                EGuidFormats::Digits));
    }

    // =====================================================
    // SPAWN SUCCESS DIAGNOSTICS
    // =====================================================

    {
        const FString ActorName = NewActor->GetName();
        const FString ActorClass = NewActor->GetClass()->GetName();
        const FString SpawnWorldName = NewActor->GetWorld() ? NewActor->GetWorld()->GetName() : TEXT("None");
        const FTransform SpawnXForm = NewActor->GetActorTransform();

        UE_LOG(LogLiveSync, Warning,
            TEXT("[CREATE][DIAG] SPAWN SUCCESS guid=%s name=%s class=%s world=%s spawnMs=%.1f"),
            *Guid.ToString(EGuidFormats::Digits),
            *ActorName,
            *ActorClass,
            *SpawnWorldName,
            SpawnMs);

        UE_LOG(LogLiveSync, Warning,
            TEXT("[CREATE][DIAG] SPAWN TRANSFORM loc=%s rot=(%.4f,%.4f,%.4f,%.4f) scale=%s"),
            *SpawnXForm.GetLocation().ToString(),
            (double)SpawnXForm.GetRotation().W,
            (double)SpawnXForm.GetRotation().X,
            (double)SpawnXForm.GetRotation().Y,
            (double)SpawnXForm.GetRotation().Z,
            *SpawnXForm.GetScale3D().ToString());

        // Verify actor is in a visible world
        if (NewActor->GetWorld() &&
            NewActor->GetWorld()->WorldType != EWorldType::Editor &&
            NewActor->GetWorld()->WorldType != EWorldType::Game &&
            NewActor->GetWorld()->WorldType != EWorldType::PIE)
        {
            const int32 WorldTypeVal = (int32)NewActor->GetWorld()->WorldType;

            UE_LOG(LogLiveSync, Error,
                TEXT("[CREATE][DIAG] ACTOR spawned into NON-VISIBLE world type=%d — will NOT appear in viewport!"),
                WorldTypeVal);
        }
    }

    // =====================================================
    // Tag and cache
    // =====================================================

    FString TagString =
        FString::Printf(
            TEXT("LiveSync_GUID=%s"),
            *Guid.ToString(
                EGuidFormats::Digits));

    NewActor->Tags.Add(
        FName(*TagString));

    ActorCache.Add(
        Guid,
        NewActor);

    // ── Persistent rename label restoration ──
    // If this GUID has an authoritative label from a previous rename,
    // restore it immediately to prevent the default-label window.
    // This ensures labels survive actor re-creation (e.g. after GUID
    // regeneration on the Blender side) and snapshot rebuild.
    {
        const FString* PersistentLabel = GRenamePersistentLabel.Find(Guid);
        if (PersistentLabel && !PersistentLabel->IsEmpty())
        {
#if WITH_EDITOR
            NewActor->SetActorLabel(*PersistentLabel);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[RENAME][DIAG] Restoring persistent label for guid=%s label=\"%s\""),
                *Guid.ToString(EGuidFormats::Digits),
                **PersistentLabel);
#endif
        }
    }

    // Verify registry integration
    {
        AActor* CacheCheck = FindActorFast(Guid);
        UE_LOG(LogLiveSync, Warning,
            TEXT("[CREATE][DIAG] REGISTRY guid=%s ActorCache check=%s"),
            *Guid.ToString(EGuidFormats::Digits),
            CacheCheck ? TEXT("FOUND") : TEXT("MISSING"));

        // Immediate post-spawn actor destruction check
        if (CacheCheck && CacheCheck->IsPendingKillPending())
        {
            UE_LOG(LogLiveSync, Error,
                TEXT("[CREATE][DIAG] ACTOR PENDING DESTROY IMMEDIATELY AFTER SPAWN guid=%s — cleanup race!"),
                *Guid.ToString(EGuidFormats::Digits));
        }
    }

    // =====================================================
    // Step 2: Attach to parent (initial attach).
    // Attach BEFORE initializing local state so attachment
    // is established before the first interpolation tick.
    // KeepWorldTransform preserves the initial world pose.
    // =====================================================

    AttachToParent(
        Guid,
        ParentGuid);

    // =====================================================
    // POST-ATTACH DIAGNOSTICS
    // =====================================================

    {
        AActor* PostAttachActor = FindActorFast(Guid);
        if (PostAttachActor)
        {
            AActor* CurrentParent = PostAttachActor->GetAttachParentActor();
            UE_LOG(LogLiveSync, Warning,
                TEXT("[CREATE][DIAG] ATTACH guid=%s parent=%s actualParent=%s"),
                *Guid.ToString(EGuidFormats::Digits),
                *ParentGuid.ToString(EGuidFormats::Digits),
                CurrentParent ? *CurrentParent->GetName() : TEXT("None"));
        }
    }

    // =====================================================
    // Validate primitive type
    // =====================================================

    if (PrimitiveType > LSP_Empty)
    {
        UE_LOG(
            LogLiveSync,
            Warning,
            TEXT("Unknown primitive type 0x%02X, defaulting to Cube"),
            PrimitiveType);

        PrimitiveType =
            LSP_Cube;
    }

    if (PrimitiveType == LSP_Empty)
    {
        // NOTE: State initialization is handled by the caller
        // (ProcessBinaryPacket or RecoverMissingActors) via
        // an explicit UpdateTargetTransform call with correct
        // (possibly local-space) transform values.
        // This avoids passing world-spawn values as local targets.

        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("END TRACE: HandleCreateObject guid=%s (empty, no mesh)"),
            *Guid.ToString(
                EGuidFormats::Digits));

        return;
    }

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("BEGIN TRACE: HandleCreateObject::RegisterComponent guid=%s"),
        *Guid.ToString(
            EGuidFormats::Digits));

    UStaticMeshComponent* MeshComp =
        NewObject<UStaticMeshComponent>(
            NewActor);

    if (!MeshComp)
    {
        UE_LOG(LogLiveSync, Error,
            TEXT("[CREATE][DIAG] NewObject<UStaticMeshComponent> FAILED guid=%s — aborting"),
            *Guid.ToString(EGuidFormats::Digits));
        return;
    }

    MeshComp->SetMobility(
        EComponentMobility::Movable);

    MeshComp->SetVisibility(
        true, true);

    UStaticMesh* PrimitiveMesh =
        GetPrimitiveMesh(PrimitiveType);

    if (PrimitiveMesh)
    {
        MeshComp->SetStaticMesh(
            PrimitiveMesh);

        UE_LOG(LogLiveSync, Warning,
            TEXT("[CREATE][DIAG] PRIMITIVE guid=%s type=0x%02X mesh=%s"),
            *Guid.ToString(EGuidFormats::Digits),
            PrimitiveType,
            *PrimitiveMesh->GetName());
    }
    else
    {
        UE_LOG(LogLiveSync, Error,
            TEXT("[CREATE][DIAG] PRIMITIVE RESOLVE FAILED guid=%s type=0x%02X — no mesh assigned, actor will be invisible!"),
            *Guid.ToString(EGuidFormats::Digits),
            PrimitiveType);
    }

    MeshComp->SetCollisionEnabled(
        ECollisionEnabled::NoCollision);

    NewActor->SetRootComponent(
        MeshComp);

    uint64 RegisterBeginCycles =
        FPlatformTime::Cycles64();

    MeshComp->RegisterComponent();

    double RegisterMs =
        FPlatformTime::
        ToMilliseconds64(
            FPlatformTime::Cycles64() -
            RegisterBeginCycles);

    if (RegisterMs > 50.0)
    {
        UE_LOG(
            LogLiveSync,
            Warning,
            TEXT("[CREATE][DIAG] STALL: RegisterComponent took %.1fms "
                 "for GUID=%s"),
            RegisterMs,
            *Guid.ToString(
                EGuidFormats::Digits));
    }

    UE_LOG(
        LogLiveSync,
        Warning,
        TEXT("[CREATE][DIAG] REGISTER COMPLETE guid=%s mesh=%s regMs=%.1f"),
        *Guid.ToString(EGuidFormats::Digits),
        PrimitiveMesh ? *PrimitiveMesh->GetName() : TEXT("NULL"),
        RegisterMs);

    // NOTE: State initialization is handled by the caller
    // (ProcessBinaryPacket or RecoverMissingActors) via
    // an explicit UpdateTargetTransform call with correct
    // (possibly local-space) transform values.
    // This avoids passing world-spawn values as local targets.

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("END TRACE: HandleCreateObject guid=%s"),
        *Guid.ToString(
            EGuidFormats::Digits));

    // ── Unified world replay recording (Phase 6G) ──
    if (!bInSnapshotBuild)
    {
        FWorldReplayEntry WorldEntry;
        WorldEntry.Domain = EWorldReplayDomain::Lifecycle;
        WorldEntry.PacketType = 0x03;
        WorldEntry.Guid = Guid;
        WorldEntry.Timestamp = FPlatformTime::Seconds();
        RecordWorldReplayEntry(WorldEntry);
    }
}


// =========================================================
// HANDLE DELETE OBJECT
// =========================================================

void UUELiveSyncSubsystem::
HandleDeleteObject(
    const FGuid& Guid)
{
    CHECK_GAME_THREAD();

    // ── UNEXPECTED DELETE DIAGNOSTICS ──
    // If we're deleting a GUID that was recently created, this could
    // explain actors disappearing immediately after spawn.
    {
        AActor* Found =
            FindActorFast(Guid);

        if (Found)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[DELETE][DIAG] Deleting EXISTING actor guid=%s name=%s bInSnapshot=%d"),
                *Guid.ToString(EGuidFormats::Digits),
                *Found->GetName(),
                bInSnapshotBuild ? 1 : 0);
        }
        else
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[DELETE][DIAG] Deleting MISSING guid=%s bInSnapshot=%d"),
                *Guid.ToString(EGuidFormats::Digits),
                bInSnapshotBuild ? 1 : 0);
        }
    }

    AActor* Actor =
        FindActorFast(Guid);

    bool bCacheHadEntry =
        ActorCache.Find(Guid) != nullptr;

    bool bTransformHadState =
        TransformStates.Find(Guid) != nullptr;

    // Remove from pending attachments if queued
    PendingAttachments.RemoveAll(
        [&Guid](
            const FPendingAttachment&
            Entry)
        {
            return Entry.Child == Guid ||
                   Entry.Parent == Guid;
        });

    // Remove from missing actor tracker
    MissingActorTracker.Remove(
        Guid);

    if (!bInSnapshotBuild)
    {
        if (Actor)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[DELETE][DIAG] DESTROYING actor guid=%s name=%s — will disappear from viewport!"),
                *Guid.ToString(EGuidFormats::Digits),
                *Actor->GetName());

            Actor->Destroy();
        }

        ActorCache.Remove(Guid);
    }

    TransformStates.Remove(
        Guid);

    if (bEnableVerboseSyncLogs)
    {
        FString ActorName =
            Actor
                ? Actor->GetName()
                : TEXT("nullptr");

        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("[Delete] GUID=%s Actor=%s Removed=%d StaleCache=%d"),
            *Guid.ToString(
                EGuidFormats::Digits),
            *ActorName,
            Actor ? 1 : 0,
            bCacheHadEntry ? 1 : 0);
    }

    // Remove asset metadata and pending resolution
    if (AssetMetadata.Remove(Guid))
    {
        PendingAssetQueue.Remove(Guid);
    }
}


// =========================================================
// HANDLE RENAME (Phase 6 — Semantic Event)
// =========================================================
// Applies a discrete rename event to a tracked actor.
//
// Rename is NOT a state-stream operation:
//   • Lifecycle-sensitive  — rejects Tombstoned/Unknown GUIDs
//   • Provenance-sensitive — every rename carries EChangeOrigin
//   • Callback-sensitive   — wrapped in FScopedRenameSuppression
//   • Replay-safe          — stale/duplicate rejection via sequence
//
// See Docs/Architecture/19-phase6-vertical-slice-rename.md §4
// =========================================================

void UUELiveSyncSubsystem::
HandleRename(
    const FGuid& Guid,
    const FString& OldName,
    const FString& NewName,
    uint32 SequenceNumber,
    double Timestamp,
    EChangeOrigin Origin)
{
    CHECK_GAME_THREAD();
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_HandleRename);

    // =====================================================
    // (STAGE 4) REJECT: Tombstoned GUID — object was deleted
    // =====================================================

    if (IsTombstoned(Guid))
    {
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[RENAME][TOMBSTONE] GUID=%s — blocked by tombstone"),
            *Guid.ToString(EGuidFormats::Digits));
        Stats.DeleteTombstoneRejections.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // REJECT: No tracked actor for this GUID
    // =====================================================

    AActor* Actor = FindActorFast(Guid);
    if (!Actor)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[RENAME] Rejected — no tracked actor for GUID=%s "
                 "(OldName=%s, NewName=%s, Seq=%u, Origin=%d)"),
            *Guid.ToString(EGuidFormats::Digits),
            *OldName, *NewName, SequenceNumber, (int32)Origin);
        Stats.RenameStaleRejections.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // REJECT: Stale or duplicate sequence number
    // =====================================================

    if (GRenameSequences.IsStaleOrDuplicate(Guid, SequenceNumber))
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[RENAME] Rejected — stale/duplicate sequence "
                 "GUID=%s Name=%s Seq=%u (last=%u)"),
            *Guid.ToString(EGuidFormats::Digits),
            *NewName, SequenceNumber,
            GRenameSequences.LastSequence.FindRef(Guid));
        Stats.RenameStaleRejections.fetch_add(1, std::memory_order_relaxed);

        if (Origin == EChangeOrigin::Replay)
        {
            Stats.RenameReplaySkipped.fetch_add(1, std::memory_order_relaxed);
        }

        return;
    }

    // =====================================================
    // APPLY RENAME with suppression scope
    // =====================================================

    {
        FScopedRenameSuppression Suppress(Guid);
        FScopedChangeOrigin OriginScope(Origin);

        // ── Diagnostics + persistent label registry ──
        {
            const FString PreLabel = Actor->GetActorLabel();
            UE_LOG(LogLiveSync, Warning,
                TEXT("[RENAME][DIAG] Applying rename guid=%s Origin=%s "
                     "OldName=\"%s\" NewName=\"%s\" Seq=%u PreLabel=\"%s\""),
                *Guid.ToString(EGuidFormats::Digits),
                Origin == EChangeOrigin::RemoteReplicated ? TEXT("REMOTE_REPLICATED") :
                Origin == EChangeOrigin::Replay ? TEXT("REPLAY") :
                Origin == EChangeOrigin::LocalUser ? TEXT("LOCAL_USER") :
                Origin == EChangeOrigin::Recovery ? TEXT("RECOVERY") :
                TEXT("UNSPECIFIED"),
                *OldName, *NewName, SequenceNumber,
                *PreLabel);
        }

        // Update persistent label registry BEFORE SetActorLabel
        // so HandleCreateObject / RestoreWorldState can restore it.
        GRenamePersistentLabel.Add(Guid, NewName);

        // SetActorLabel fires OnActorLabelChanged synchronously.
        // The callback handler checks GCurrentChangeOrigin and
        // FScopedRenameSuppression to prevent re-replication.
        Actor->SetActorLabel(NewName);

        GRenameSequences.Update(Guid, SequenceNumber);

        {
            const FString PostLabel = Actor->GetActorLabel();
            UE_LOG(LogLiveSync, Warning,
                TEXT("[RENAME][DIAG] Actor label changed guid=%s old=\"%s\" new=\"%s\""),
                *Guid.ToString(EGuidFormats::Digits),
                *OldName, *PostLabel);
        }

        if (Origin == EChangeOrigin::RemoteReplicated)
        {
            Stats.RenamesProcessed.fetch_add(1, std::memory_order_relaxed);
        }
        else if (Origin == EChangeOrigin::Replay)
        {
            Stats.RenameReplayApplied.fetch_add(1, std::memory_order_relaxed);
        }
    }

    // Suppression scope destructor re-enables replication

    // ── Unified world replay recording (Phase 6G) ──
    if (Origin == EChangeOrigin::RemoteReplicated)
    {
        FWorldReplayEntry WorldEntry;
        WorldEntry.Domain = EWorldReplayDomain::Rename;
        WorldEntry.PacketType = 0x0C;
        WorldEntry.Guid = Guid;
        WorldEntry.Sequence = SequenceNumber;
        WorldEntry.Timestamp = Timestamp;
        RecordWorldReplayEntry(WorldEntry);
    }
}


// =========================================================
// HANDLE VISIBILITY (Phase 6 — Semantic Event)
// =========================================================

void UUELiveSyncSubsystem::
HandleVisibility(
    const FGuid& Guid,
    bool bHidden,
    uint32 SequenceNumber,
    double Timestamp,
    EChangeOrigin Origin)
{
    CHECK_GAME_THREAD();
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_HandleVisibility);

    // =====================================================
    // (STAGE 4) REJECT: Tombstoned GUID — object was deleted
    // =====================================================

    if (IsTombstoned(Guid))
    {
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[VISIBILITY][TOMBSTONE] GUID=%s — blocked by tombstone"),
            *Guid.ToString(EGuidFormats::Digits));
        Stats.DeleteTombstoneRejections.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // REJECT: No tracked actor for this GUID
    // =====================================================

    AActor* Actor = FindActorFast(Guid);
    if (!Actor)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[VISIBILITY] Rejected — no tracked actor for GUID=%s "
                 "(bHidden=%d, Seq=%u, Origin=%d)"),
            *Guid.ToString(EGuidFormats::Digits),
            (int32)bHidden, SequenceNumber, (int32)Origin);
        Stats.VisibilityStaleRejections.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // REJECT: Stale or duplicate sequence number
    // =====================================================

    if (GVisibilitySequences.IsStaleOrDuplicate(Guid, SequenceNumber))
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[VISIBILITY] Rejected — stale/duplicate sequence "
                 "GUID=%s bHidden=%d Seq=%u (last=%u)"),
            *Guid.ToString(EGuidFormats::Digits),
            (int32)bHidden, SequenceNumber,
            GVisibilitySequences.LastSequence.FindRef(Guid));
        Stats.VisibilityStaleRejections.fetch_add(1, std::memory_order_relaxed);

        if (Origin == EChangeOrigin::Replay)
        {
            Stats.VisibilityReplaySkipped.fetch_add(1, std::memory_order_relaxed);
        }

        return;
    }

    // =====================================================
    // APPLY VISIBILITY with suppression scope
    // =====================================================

    {
        FScopedVisibilitySuppression Suppress(Guid);
        FScopedChangeOrigin OriginScope(Origin);

        UE_LOG(LogLiveSync, Log,
            TEXT("[VISIBILITY] Applying: GUID=%s Origin=%s "
                 "bHidden=%d Seq=%u"),
            *Guid.ToString(EGuidFormats::Digits),
            Origin == EChangeOrigin::RemoteReplicated ? TEXT("REMOTE_REPLICATED") :
            Origin == EChangeOrigin::Replay ? TEXT("REPLAY") :
            Origin == EChangeOrigin::LocalUser ? TEXT("LOCAL_USER") :
            Origin == EChangeOrigin::Recovery ? TEXT("RECOVERY") :
            TEXT("UNSPECIFIED"),
            (int32)bHidden, SequenceNumber);

        Actor->SetIsTemporarilyHiddenInEditor(bHidden);

        GVisibilitySequences.Update(Guid, SequenceNumber);

        if (Origin == EChangeOrigin::RemoteReplicated)
        {
            Stats.VisibilityProcessed.fetch_add(1, std::memory_order_relaxed);
        }
        else if (Origin == EChangeOrigin::Replay)
        {
            Stats.VisibilityReplayApplied.fetch_add(1, std::memory_order_relaxed);
        }

        UE_LOG(LogLiveSync, Log,
            TEXT("[VISIBILITY][DIAG] Applied: GUID=%s HiddenInGame=%d Actor=%s"),
            *Guid.ToString(EGuidFormats::Digits),
            (int32)Actor->IsTemporarilyHiddenInEditor(),
            *Actor->GetName());
    }
}


// =========================================================
// HANDLE HIERARCHY (Phase 6D — Semantic Event)
// =========================================================
// Stage 4 implementation: replay rejection layer only.
// Attachment application is deferred to Stage 6+.
// See Docs/Architecture/24-phase6D-hierarchy-scope-lock.md
// and 26-phase6D-hierarchy-implementation-plan.md
// =========================================================

void UUELiveSyncSubsystem::
HandleHierarchy(
    const FGuid& ChildGuid,
    const FGuid& ParentGuid,
    uint32 SequenceNumber,
    double Timestamp,
    EChangeOrigin Origin)
{
    CHECK_GAME_THREAD();
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_HandleHierarchy);

    // =====================================================
    // (STAGE 4) REJECT: Tombstoned GUID — object was deleted
    // =====================================================

    if (IsTombstoned(ChildGuid) || IsTombstoned(ParentGuid))
    {
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[HIERARCHY][TOMBSTONE] ChildGuid=%s ParentGuid=%s "
                 "— blocked by tombstone"),
            *ChildGuid.ToString(EGuidFormats::Digits),
            *ParentGuid.ToString(EGuidFormats::Digits));
        Stats.DeleteTombstoneRejections.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // REJECT: No tracked actor for child GUID
    // =====================================================

    AActor* ChildActor = FindActorFast(ChildGuid);
    if (!ChildActor)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[HIERARCHY] Rejected — no tracked actor for ChildGuid=%s "
                 "(ParentGuid=%s, Seq=%u, Origin=%d)"),
            *ChildGuid.ToString(EGuidFormats::Digits),
            *ParentGuid.ToString(EGuidFormats::Digits),
            SequenceNumber, (int32)Origin);
        Stats.HierarchyStaleRejections.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // REJECT: Stale or duplicate sequence number
    // =====================================================

    if (GHierarchySequences.IsStaleOrDuplicate(ChildGuid, SequenceNumber))
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[HIERARCHY] Rejected — stale/duplicate sequence "
                 "ChildGuid=%s ParentGuid=%s Seq=%u (last=%u)"),
            *ChildGuid.ToString(EGuidFormats::Digits),
            *ParentGuid.ToString(EGuidFormats::Digits),
            SequenceNumber,
            GHierarchySequences.LastSequence.FindRef(ChildGuid));
        Stats.HierarchyStaleRejections.fetch_add(1, std::memory_order_relaxed);

        if (Origin == EChangeOrigin::Replay)
        {
            Stats.HierarchyReplaySkipped.fetch_add(1, std::memory_order_relaxed);
        }

        return;
    }

    // =====================================================
    // STAGE 6-7: GRAPH MUTATION — Attach / Detach
    // =====================================================
    // First guarded graph mutation layer. Issues attachment
    // intent via AttachToActor/DetachFromActor using raw UE
    // APIs (NOT the frozen AttachToParent/DetachFromParent
    // wrappers which modify FSyncTransformState and have
    // deferred queues).
    //
    // All-zero ParentGuid = semantic detach-to-root.
    // Missing parent = DEFERRED to bounded retry queue
    //   (Stage 7: PendingHierarchyAttachments, max 2048,
    //    deterministic retry cadence, FINDING-001/002).
    // Already attached to correct parent = no-op.
    //
    // Cycle detection added in Stage 9.
    // =====================================================

    const bool bIsDetach = !ParentGuid.IsValid();

    if (bIsDetach)
    {
        // =================================================
        // DETACH PATH: all-zero ParentGuid
        // =================================================
        // Detach has zero dependencies — always applied
        // immediately. No deferral possible.
        // =================================================

        AActor* CurrentParent = ChildActor->GetAttachParentActor();
        if (CurrentParent != nullptr)
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[HIERARCHY][DETACH] BEGIN DetachFromActor "
                     "child=%s current_parent=%s"),
                *ChildGuid.ToString(EGuidFormats::Digits),
                *CurrentParent->GetName());

            ChildActor->DetachFromActor(
                FDetachmentTransformRules::KeepWorldTransform);

            UE_LOG(LogLiveSync, Log,
                TEXT("[HIERARCHY][DETACH] END   DetachFromActor "
                     "child=%s — now root"),
                *ChildGuid.ToString(EGuidFormats::Digits));
        }
        else
        {
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[HIERARCHY][DETACH] No-op: child=%s already root"),
                *ChildGuid.ToString(EGuidFormats::Digits));
        }

        // Update tracker — detach is a processed event
        GHierarchySequences.Update(ChildGuid, SequenceNumber);

        if (Origin == EChangeOrigin::RemoteReplicated)
        {
            Stats.HierarchyProcessed.fetch_add(1, std::memory_order_relaxed);
        }
        else if (Origin == EChangeOrigin::Replay)
        {
            Stats.HierarchyReplayApplied.fetch_add(1, std::memory_order_relaxed);
        }

        return;
    }

    // =====================================================
    // ATTACH PATH: non-zero ParentGuid
    // =====================================================

    AActor* ParentActor = FindActorFast(ParentGuid);
    if (!ParentActor)
    {
        // =================================================
        // DEFERRED — parent not found
        // =================================================
        // Push to bounded deferred retry queue. The resolver
        // (ResolveHierarchyAttachments) will retry each frame
        // with deterministic cadence and replay-safe checks.
        //
        // No hidden graph state — queue only stores unresolved
        // semantic intent. No FSyncTransformState modifications.
        // =================================================

        // FINDING-002 dedup: if this child already has a pending
        // entry, update it instead of adding a duplicate.
        FPendingHierarchyAttachment* Existing =
            FindPendingHierarchyAttachment(ChildGuid);

        if (Existing)
        {
            if (SequenceNumber > Existing->Sequence)
            {
                uint32 OldSeq = Existing->Sequence;
                Existing->Sequence = SequenceNumber;
                Existing->ParentGuid = ParentGuid;
                Existing->RetryCount = 0;
                Existing->CreatedTime = FPlatformTime::Seconds();
                Existing->Origin = Origin;
                Existing->State = EOrphanState::DEFERRED;  // Reset to deferred

                UE_LOG(LogLiveSync, Verbose,
                    TEXT("[HIERARCHY][ORPHAN] RETRYING — updated pending: "
                         "child=%s (seq %u → %u, state=DEFERRED)"),
                    *ChildGuid.ToString(EGuidFormats::Digits),
                    OldSeq, SequenceNumber);
            }
            else
            {
                UE_LOG(LogLiveSync, Verbose,
                    TEXT("[HIERARCHY][ORPHAN] STALE_REJECTED — deferred "
                         "update skipped: child=%s (incoming %u <= "
                         "existing %u)"),
                    *ChildGuid.ToString(EGuidFormats::Digits),
                    SequenceNumber, Existing->Sequence);
            }

            // Do NOT update sequence tracker — event was not applied.
            return;
        }

        // Queue overflow: FIFO eviction
        if (PendingHierarchyAttachments.Num() >= 2048)
        {
            FPendingHierarchyAttachment Evicted =
                PendingHierarchyAttachments[0];
            PendingHierarchyAttachments.RemoveAt(0);

            UE_LOG(LogLiveSync, Warning,
                TEXT("[HIERARCHY][ORPHAN] EVICTED — deferred queue overflow "
                     "— evicting child=%s (state=EVICTED)"),
                *Evicted.ChildGuid.ToString(EGuidFormats::Digits));

            Stats.HierarchyOrphans.fetch_add(1, std::memory_order_relaxed);
        }

        FPendingHierarchyAttachment NewEntry;
        NewEntry.ChildGuid = ChildGuid;
        NewEntry.ParentGuid = ParentGuid;
        NewEntry.Sequence = SequenceNumber;
        NewEntry.CreatedTime = FPlatformTime::Seconds();
        NewEntry.RetryCount = 0;
        NewEntry.Origin = Origin;
        NewEntry.State = EOrphanState::DEFERRED;

        PendingHierarchyAttachments.Add(NewEntry);

        UE_LOG(LogLiveSync, Log,
            TEXT("[HIERARCHY][ORPHAN] DEFERRED — enqueued: child=%s parent=%s "
                 "Seq=%u (queue size=%d)"),
            *ChildGuid.ToString(EGuidFormats::Digits),
            *ParentGuid.ToString(EGuidFormats::Digits),
            SequenceNumber,
            PendingHierarchyAttachments.Num());

        // Do NOT update sequence tracker — event was not applied yet.
        // The tracker is updated when the deferred entry resolves.
        return;
    }

    // =====================================================
    // STALE ACTOR VALIDATION BEFORE ATTACH
    // =====================================================

    if (!IsValid(ChildActor) || !IsValid(ParentActor))
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[HIERARCHY] Stale actor — child=%s valid=%d "
                 "parent=%s valid=%d — aborting attach"),
            *ChildGuid.ToString(EGuidFormats::Digits),
            IsValid(ChildActor) ? 1 : 0,
            *ParentGuid.ToString(EGuidFormats::Digits),
            IsValid(ParentActor) ? 1 : 0);

        Stats.HierarchyStaleRejections.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // STAGE 9: CYCLE DETECTION
    // =====================================================
    // Explicit semantic cycle detection before any graph
    // mutation. Uses the LIVE attachment graph only — no
    // shadow graph, no intent graph, no cached topology.
    //
    // Self-cycles, direct 2-cycles, and indirect N-cycles
    // are all detected by a bounded parent-chain walk.
    // Max depth = 256 to prevent infinite loops.
    //
    // On cycle: reject immediately, increment counter, log
    // with depth and path, NO deferral, NO auto-repair.
    // =====================================================

    if (WouldCreateHierarchyCycle(ChildGuid, ParentGuid))
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[HIERARCHY][CYCLE] Rejected: child=%s parent=%s "
                 "— cycle detected, no deferral"),
            *ChildGuid.ToString(EGuidFormats::Digits),
            *ParentGuid.ToString(EGuidFormats::Digits));

        Stats.HierarchyCycles.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // EXISTING-PARENT SHORT-CIRCUIT
    // =====================================================
    // Avoid redundant graph churn. If the child is already
    // attached to the requested parent, skip the mutation.
    // Still update the tracker to prevent stale re-sends.
    // =====================================================

    if (ChildActor->GetAttachParentActor() == ParentActor)
    {
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[HIERARCHY][ATTACH] No-op: child=%s already "
                 "attached to parent=%s — skipping"),
            *ChildGuid.ToString(EGuidFormats::Digits),
            *ParentGuid.ToString(EGuidFormats::Digits));

        GHierarchySequences.Update(ChildGuid, SequenceNumber);

        if (Origin == EChangeOrigin::RemoteReplicated)
        {
            Stats.HierarchyProcessed.fetch_add(1, std::memory_order_relaxed);
        }
        else if (Origin == EChangeOrigin::Replay)
        {
            Stats.HierarchyReplayApplied.fetch_add(1, std::memory_order_relaxed);
        }

        return;
    }

    // =====================================================
    // APPLY ATTACHMENT
    // =====================================================
    // Raw AttachToActor with KeepWorldTransform.
    // Does NOT go through the frozen AttachToParent wrapper
    // (which would add cycle detection, deferred queue,
    // FSyncTransformState modification, and oscillation
    // detection — all deferred to Stage 7+).
    // =====================================================

    UE_LOG(LogLiveSync, Log,
        TEXT("[HIERARCHY][ATTACH] BEGIN AttachToActor "
             "child=%s parent=%s"),
        *ChildGuid.ToString(EGuidFormats::Digits),
        *ParentGuid.ToString(EGuidFormats::Digits));

    ChildActor->AttachToActor(
        ParentActor,
        FAttachmentTransformRules::KeepWorldTransform);

    UE_LOG(LogLiveSync, Log,
        TEXT("[HIERARCHY][ATTACH] END   AttachToActor "
             "child=%s parent=%s"),
        *ChildGuid.ToString(EGuidFormats::Digits),
        *ParentGuid.ToString(EGuidFormats::Digits));

    // =====================================================
    // UPDATE TRACKER AND COUNTERS
    // =====================================================

    GHierarchySequences.Update(ChildGuid, SequenceNumber);

    if (Origin == EChangeOrigin::RemoteReplicated)
    {
        Stats.HierarchyProcessed.fetch_add(1, std::memory_order_relaxed);
    }
    else if (Origin == EChangeOrigin::Replay)
    {
        Stats.HierarchyReplayApplied.fetch_add(1, std::memory_order_relaxed);
    }
}


// =========================================================
// FIND PENDING HIERARCHY ATTACHMENT (by child GUID)
// =========================================================

UUELiveSyncSubsystem::FPendingHierarchyAttachment*
UUELiveSyncSubsystem::FindPendingHierarchyAttachment(
    const FGuid& ChildGuid)
{
    for (int32 i = 0; i < PendingHierarchyAttachments.Num(); i++)
    {
        if (PendingHierarchyAttachments[i].ChildGuid == ChildGuid)
        {
            return &PendingHierarchyAttachments[i];
        }
    }
    return nullptr;
}


// =========================================================
// RESOLVE HIERARCHY ATTACHMENTS (Phase 6D, Stage 7)
// =========================================================
// Deterministic deferred resolution for hierarchy events
// whose parent was not available at packet time.
//
// Called once per Tick after ResolvePendingAttachments
// (runtime resolver runs first, semantic runs second).
//
// Retry cadence: 10 fast (every frame) + 10 slow (every 5th
// frame) = max 20 retries. Hard timeout at 60 total frames.
//
// Each deferred entry is re-checked against the sequence
// tracker (FINDING-001) before application to prevent stale
// graph mutations.
// =========================================================

void UUELiveSyncSubsystem::
ResolveHierarchyAttachments()
{
    CHECK_GAME_THREAD();
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ResolveHierarchyAttachments);

    if (PendingHierarchyAttachments.Num() == 0)
    {
        return;
    }

    // Iterate forward, rebuilding remaining entries.
    // This avoids O(n) removal and preserves determinism.
    TArray<FPendingHierarchyAttachment> Remaining;
    Remaining.Reserve(PendingHierarchyAttachments.Num());

    for (FPendingHierarchyAttachment& Entry :
         PendingHierarchyAttachments)
    {
        // ---- STATE TRANSITION: DEFERRED → RETRYING ----
        Entry.State = EOrphanState::RETRYING;

        // ---- Hard timeout check (60 frames max) ----
        if (Entry.RetryCount >= 60)
        {
            Entry.State = EOrphanState::EVICTED;

            UE_LOG(LogLiveSync, Warning,
                TEXT("[HIERARCHY][ORPHAN] EVICTED — TIMEOUT: child=%s "
                     "parent=%s (state=EVICTED, retries=%d)"),
                *Entry.ChildGuid.ToString(EGuidFormats::Digits),
                *Entry.ParentGuid.ToString(EGuidFormats::Digits),
                Entry.RetryCount);

            Stats.HierarchyOrphans.fetch_add(1, std::memory_order_relaxed);
            continue; // Drop — do NOT re-queue
        }

        // ---- Retry cadence check ----
        // Fast phase: retries 0-9 (10 total), every frame
        // Slow phase: retries 10-19 (10 total), every 5th frame
        // After 20: retries 20-59, only timeout applies
        const bool bInFastPhase = Entry.RetryCount < 10;
        const bool bInSlowPhase = Entry.RetryCount >= 10 && Entry.RetryCount < 20;

        bool bShouldRetryThisFrame = false;
        if (bInFastPhase)
        {
            bShouldRetryThisFrame = true; // Every frame
        }
        else if (bInSlowPhase)
        {
            // Every 5th frame: retry on frames 0, 5, 10, 15, ...
            bShouldRetryThisFrame = (Entry.RetryCount % 5 == 0);
        }
        // After 20 retries: only timeout will evict

        if (!bShouldRetryThisFrame)
        {
            // Not a retry frame — re-queue and try later
            FPendingHierarchyAttachment UpdatedEntry = Entry;
            UpdatedEntry.RetryCount = Entry.RetryCount + 1;
            Remaining.Add(UpdatedEntry);
            continue;
        }

        // ---- FINDING-001: Re-validate sequence against tracker ----
        if (GHierarchySequences.IsStaleOrDuplicate(
                Entry.ChildGuid, Entry.Sequence))
        {
            Entry.State = EOrphanState::STALE_REJECTED;

            UE_LOG(LogLiveSync, Verbose,
                TEXT("[HIERARCHY][ORPHAN] STALE_REJECTED — deferred "
                     "resolution stale: child=%s (deferred seq=%u, "
                     "current last=%u)"),
                *Entry.ChildGuid.ToString(EGuidFormats::Digits),
                Entry.Sequence,
                GHierarchySequences.LastSequence.FindRef(Entry.ChildGuid));

            Stats.HierarchyStaleRejections.fetch_add(1, std::memory_order_relaxed);
            continue; // Drop — do NOT apply, do NOT re-queue
        }

        // ---- Check if parent is now available ----
        AActor* ParentActor = FindActorFast(Entry.ParentGuid);
        if (!ParentActor)
        {
            // Parent still not available — re-queue
            FPendingHierarchyAttachment UpdatedEntry = Entry;
            UpdatedEntry.RetryCount = Entry.RetryCount + 1;
            Remaining.Add(UpdatedEntry);

            if (bEnableVerboseSyncLogs &&
                Entry.RetryCount > 0 &&
                Entry.RetryCount % 10 == 0)
            {
                const TCHAR* PhaseStr =
                    bInFastPhase ? TEXT("fast") : TEXT("slow");
                UE_LOG(LogLiveSync, Verbose,
                    TEXT("[HIERARCHY][ORPHAN] RETRYING — child=%s "
                         "parent=%s (retry=%d, phase=%s)"),
                    *Entry.ChildGuid.ToString(EGuidFormats::Digits),
                    *Entry.ParentGuid.ToString(EGuidFormats::Digits),
                    Entry.RetryCount, PhaseStr);
            }
            continue;
        }

        // ---- Parent found — apply attachment ----
        AActor* ChildActor = FindActorFast(Entry.ChildGuid);
        if (!ChildActor)
        {
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[HIERARCHY][ORPHAN] EVICTED — child deleted while "
                     "deferred: child=%s — dropping"),
                *Entry.ChildGuid.ToString(EGuidFormats::Digits));
            continue;
        }

        // Stale actor safety check
        if (!IsValid(ChildActor) || !IsValid(ParentActor))
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[HIERARCHY][ORPHAN] EVICTED — stale actor during "
                     "resolution: child=%s parent=%s — dropping"),
                *Entry.ChildGuid.ToString(EGuidFormats::Digits),
                *Entry.ParentGuid.ToString(EGuidFormats::Digits));
            continue;
        }

        // ---- Stage 9: Cycle detection on deferred resolution ----
        if (WouldCreateHierarchyCycle(Entry.ChildGuid, Entry.ParentGuid))
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[HIERARCHY][CYCLE] Deferred resolution rejected: "
                     "child=%s parent=%s — cycle detected"),
                *Entry.ChildGuid.ToString(EGuidFormats::Digits),
                *Entry.ParentGuid.ToString(EGuidFormats::Digits));

            Stats.HierarchyCycles.fetch_add(1, std::memory_order_relaxed);
            continue; // Drop — do NOT apply, do NOT re-queue
        }

        // Existing-parent short-circuit (avoid redundant graph churn)
        if (ChildActor->GetAttachParentActor() == ParentActor)
        {
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[HIERARCHY][ORPHAN] RESOLVED — no-op: child=%s "
                     "already attached to parent=%s (retries=%d)"),
                *Entry.ChildGuid.ToString(EGuidFormats::Digits),
                *Entry.ParentGuid.ToString(EGuidFormats::Digits),
                Entry.RetryCount);
        }
        else
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[HIERARCHY][ORPHAN] RESOLVED — BEGIN AttachToActor "
                     "child=%s parent=%s (resolved after %d retries)"),
                *Entry.ChildGuid.ToString(EGuidFormats::Digits),
                *Entry.ParentGuid.ToString(EGuidFormats::Digits),
                Entry.RetryCount);

            ChildActor->AttachToActor(
                ParentActor,
                FAttachmentTransformRules::KeepWorldTransform);

            UE_LOG(LogLiveSync, Log,
                TEXT("[HIERARCHY][ORPHAN] RESOLVED — END   AttachToActor "
                     "child=%s parent=%s"),
                *Entry.ChildGuid.ToString(EGuidFormats::Digits),
                *Entry.ParentGuid.ToString(EGuidFormats::Digits));
        }

        // ---- Update sequence tracker ----
        GHierarchySequences.Update(Entry.ChildGuid, Entry.Sequence);

        // ---- Count resolution + update state ----
        Stats.HierarchyDeferredResolved.fetch_add(1, std::memory_order_relaxed);

        UE_LOG(LogLiveSync, Log,
            TEXT("[HIERARCHY][ORPHAN] RESOLVED: child=%s parent=%s "
                 "(after %d retries, seq=%u)"),
            *Entry.ChildGuid.ToString(EGuidFormats::Digits),
            *Entry.ParentGuid.ToString(EGuidFormats::Digits),
            Entry.RetryCount, Entry.Sequence);
    }

    PendingHierarchyAttachments = MoveTemp(Remaining);
}


// =========================================================
// STAGE 9: EXPLICIT CYCLE DETECTION
// =========================================================
// Bounded parent-chain cycle detection for the hierarchy
// semantic lane. Uses LIVE attachment graph only — no shadow
// graph, no intent graph, no cached topology.
//
// Detection types:
//   Self-cycle:     Child == Parent → immediate reject
//   Direct 2-cycle: Parent is attached to Child → reject
//   Indirect cycle: Walking parent chain from Parent upward
//                   reaches Child → reject
//
// Max walk depth = 256. If we hit the bound, reject as a
// safety measure (corrupted graph guard).
//
// On cycle: caller is responsible for incrementing the
// HierarchyCycles counter and rejecting the event.
// =========================================================

bool UUELiveSyncSubsystem::
WouldCreateHierarchyCycle(
    const FGuid& ChildGuid,
    const FGuid& ParentGuid)
{
    CHECK_GAME_THREAD();

    // ---- Self-cycle: A → A ----
    if (ChildGuid == ParentGuid)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[HIERARCHY][CYCLE] Self-cycle: child=parent=%s "
                 "(depth=0)"),
            *ChildGuid.ToString(EGuidFormats::Digits));
        return true;
    }

    // ---- Walk parent chain from ParentGuid upward ----
    // Uses live GetAttachParentActor() only.
    // Max depth = 256 hard bound.
    static constexpr int32 MAX_CYCLE_DEPTH = 256;

    AActor* CurrentActor = FindActorFast(ParentGuid);
    int32 Depth = 0;

    while (CurrentActor != nullptr && Depth < MAX_CYCLE_DEPTH)
    {
        FGuid CurrentGuid = FindGuidForActor(CurrentActor);
        if (CurrentGuid == ChildGuid)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[HIERARCHY][CYCLE] Chain cycle: child=%s "
                     "parent=%s (depth=%d)"),
                *ChildGuid.ToString(EGuidFormats::Digits),
                *ParentGuid.ToString(EGuidFormats::Digits),
                Depth + 1);
            return true;
        }

        CurrentActor = CurrentActor->GetAttachParentActor();
        Depth++;
    }

    if (Depth >= MAX_CYCLE_DEPTH)
    {
        // Safety bound — if we can't verify acyclicity in
        // 256 steps, reject to prevent infinite loops on
        // corrupted graphs.
        UE_LOG(LogLiveSync, Warning,
            TEXT("[HIERARCHY][CYCLE] Depth limit exceeded: "
                 "child=%s parent=%s (depth=%d) — rejecting"),
            *ChildGuid.ToString(EGuidFormats::Digits),
            *ParentGuid.ToString(EGuidFormats::Digits),
            Depth);
        return true;
    }

    return false; // No cycle detected — safe to attach
}


// =========================================================
// HANDLE DELETE (Phase 6E — Identity-Destruction Event)
// =========================================================
// Stage 5/6: LIVE DESTRUCTION ENABLED
//   — Sequence and tombstone checks (Stage 3)
//   — Actor destruction (Stage 5)
//   — Tombstone insertion (Stage 5)
//   — Child detach to root (Stage 6)
//   — ActorCache removal (Stage 5)
//   — Counters active (Stage 3)
//
// Stage 3 comment markers preserved for traceability.
// See Docs/Architecture/33-phase6E-lifecycle-implementation-plan.md §3.5
// =========================================================

void UUELiveSyncSubsystem::
HandleDelete(
    const FGuid& TargetGuid,
    uint32 SequenceNumber,
    double Timestamp,
    EChangeOrigin Origin)
{
    CHECK_GAME_THREAD();
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_HandleDelete);

    // =====================================================
    // (STAGE 3) REJECT: Stale or duplicate sequence number
    // =====================================================

    if (GDeleteSequences.IsStaleOrDuplicate(TargetGuid, SequenceNumber))
    {
        Stats.DeleteStaleRejections.fetch_add(1, std::memory_order_relaxed);

        if (Origin == EChangeOrigin::Replay)
        {
            Stats.DeleteReplaySkipped.fetch_add(1, std::memory_order_relaxed);
        }

        UE_LOG(LogLiveSync, Verbose,
            TEXT("[DELETE][STALE] Rejected — stale/duplicate sequence "
                 "GUID=%s Seq=%u (last=%u, Origin=%d)"),
            *TargetGuid.ToString(EGuidFormats::Digits),
            SequenceNumber,
            GDeleteSequences.LastSequence.FindRef(TargetGuid),
            (int32)Origin);
        return;
    }

    // =====================================================
    // (STAGE 3) CHECK: Tombstone — no-op if already deleted
    // =====================================================

    if (GDeleteTombstoneMap.Contains(TargetGuid))
    {
        Stats.DeleteTombstoneRejections.fetch_add(1, std::memory_order_relaxed);

        UE_LOG(LogLiveSync, Verbose,
            TEXT("[DELETE][TOMBSTONE] GUID=%s Seq=%u — already tombstoned, discarding"),
            *TargetGuid.ToString(EGuidFormats::Digits),
            SequenceNumber);
        return;
    }

    // =====================================================
    // (STAGE 3) CHECK: ActorCache
    // =====================================================

    AActor* TargetActor = FindActorFast(TargetGuid);
    if (!TargetActor)
    {
        Stats.DeleteMissingActor.fetch_add(1, std::memory_order_relaxed);

        UE_LOG(LogLiveSync, Verbose,
            TEXT("[DELETE][MISSING] GUID=%s Seq=%u — actor not in ActorCache"),
            *TargetGuid.ToString(EGuidFormats::Digits),
            SequenceNumber);
        return;
    }

    // =====================================================
    // (STAGE 5+6) DESTROY ACTOR + INSERT TOMBSTONE
    // =====================================================

    UE_LOG(LogLiveSync, Log,
        TEXT("[DELETE][%s] Destroying actor GUID=%s Name=%s "
             "Seq=%u"),
        Origin == EChangeOrigin::RemoteReplicated ? TEXT("APPLY") :
        Origin == EChangeOrigin::Replay ? TEXT("REPLAY") :
        TEXT("APPLY"),
        *TargetGuid.ToString(EGuidFormats::Digits),
        *TargetActor->GetName(),
        SequenceNumber);

    // Stage 6: Detach all children to root before destroying parent.
    // Uses raw UE API (GetAttachedActors). Does NOT update hierarchy
    // tracker or recursively destroy — children become free-floating
    // root actors. Logged for observability.
    {
        TArray<AActor*> AttachedChildren;
        TargetActor->GetAttachedActors(AttachedChildren);
        for (AActor* Child : AttachedChildren)
        {
            Child->DetachFromActor(
                FDetachmentTransformRules::KeepWorldTransform);

            UE_LOG(LogLiveSync, Log,
                TEXT("[DELETE][DETACH] Child detached to root: "
                     "ChildName=%s ParentGuid=%s"),
                *Child->GetName(),
                *TargetGuid.ToString(EGuidFormats::Digits));
        }
    }

    // Stage 5: Insert tombstone BEFORE destruction to prevent race
    // conditions where a CREATE arrives for the same GUID before
    // the actor is fully removed from the world.
    AddTombstone(TargetGuid, SequenceNumber);

    // Stage 5: Remove from ActorCache
    ActorCache.Remove(TargetGuid);

    // Stage 5: Clean asset metadata and pending resolution (Phase 7A)
    if (AssetMetadata.Remove(TargetGuid))
    {
        PendingAssetQueue.Remove(TargetGuid);
    }

    // Stage 5: Destroy the actor
    TargetActor->Destroy();

    // Stage 5: Update sequence tracker to prevent stale replay
    GDeleteSequences.Update(TargetGuid, SequenceNumber);

    // Stage 5: Apply counters
    if (Origin == EChangeOrigin::RemoteReplicated)
    {
        Stats.DeleteProcessed.fetch_add(1, std::memory_order_relaxed);

        // ── Unified world replay recording (Phase 6G) ──
        FWorldReplayEntry WorldEntry;
        WorldEntry.Domain = EWorldReplayDomain::Lifecycle;
        WorldEntry.PacketType = 0x0E;
        WorldEntry.Guid = TargetGuid;
        WorldEntry.Sequence = SequenceNumber;
        WorldEntry.Timestamp = Timestamp;
        RecordWorldReplayEntry(WorldEntry);
    }
    else if (Origin == EChangeOrigin::Replay)
    {
        Stats.DeleteReplayApplied.fetch_add(1, std::memory_order_relaxed);
    }
}


// =========================================================
// HANDLE COLLECTION (Phase 6F — Metadata-Only Log Handler)
// =========================================================
// HandleCollection — Stage 4: Apply collection membership
// mutations to GCollectionMembership registry.
//
// Rules:
//   - Metadata-only grouping layer (NO UE folder/group mapping)
//   - NO actor lookup, NO world mutation
//   - NO interaction with hierarchy, lifecycle, visibility, rename
//   - Sequence validation enforced before mutation
//   - FScopedCollectionSuppression applied during mutation
//
// Wire variants:
//   Membership ops (ADD/REMOVE/MOVE/CLEAR):
//     TargetGuid(16) + OpType(1) + OpFlags(1) + seq(4) + ts(8) +
//     CollectionGuid(16) = 46 bytes
//   Identity ops (CREATE/DELETE/RENAME/REPARENT):
//     TargetGuid(16) + OpType(1) + OpFlags(1) + seq(4) + ts(8) = 30 bytes
//     CollectionGuid == nullptr for identity ops.
//
// See Docs/Architecture/39-phase6F-vertical-slice-collection.md §4
// =========================================================

void UUELiveSyncSubsystem::
HandleCollection(
    const FGuid& TargetGuid,
    uint8 OpType,
    uint8 OpFlags,
    uint32 SequenceNumber,
    double Timestamp,
    const FGuid* CollectionGuid)
{
    CHECK_GAME_THREAD();
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_HandleCollectionPackets);

    // =====================================================
    // REJECT: Stale or duplicate sequence number
    // =====================================================

    if (GCollectionSequences.IsStaleOrDuplicate(TargetGuid, SequenceNumber))
    {
        if (GCollectionSequences.LastSequence.FindRef(TargetGuid) == SequenceNumber)
        {
            Stats.CollectionDuplicateRejected.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[COLLECTION][DUPLICATE] Rejected — duplicate sequence "
                     "GUID=%s OpType=0x%02X Seq=%u"),
                *TargetGuid.ToString(EGuidFormats::Digits),
                OpType, SequenceNumber);
        }
        else
        {
            Stats.CollectionStaleRejected.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[COLLECTION][STALE] Rejected — stale sequence "
                     "GUID=%s OpType=0x%02X Seq=%u (last=%u)"),
                *TargetGuid.ToString(EGuidFormats::Digits),
                OpType, SequenceNumber,
                GCollectionSequences.LastSequence.FindRef(TargetGuid));
        }
        return;
    }

    // =====================================================
    // REJECT: Identity ops without CollectionGuid
    // =====================================================

    const bool bIsMembershipOp = (OpType >= 0x01 && OpType <= 0x04);

    if (bIsMembershipOp && !CollectionGuid)
    {
        Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Warning,
            TEXT("[COLLECTION][MALFORMED] Membership op 0x%02X without "
                 "CollectionGuid — rejected (GUID=%s)"),
            OpType, *TargetGuid.ToString(EGuidFormats::Digits));
        return;
    }

    // =====================================================
    // APPLY: Membership state mutation
    // =====================================================

    FScopedCollectionSuppression SuppressScope(TargetGuid);

    // --- ADD: Add member to collection ---
    if (OpType == COLLECTION_OP_ADD)
    {
        TSet<FGuid>& Members = GCollectionMembership.FindOrAdd(*CollectionGuid);
        Members.Add(TargetGuid);

        Stats.CollectionAddsApplied.fetch_add(1, std::memory_order_relaxed);

        UE_LOG(LogLiveSync, Log,
            TEXT("[COLLECTION][ADD] %s → Collection=%s (total=%d seq=%u)"),
            *TargetGuid.ToString(EGuidFormats::Digits),
            *CollectionGuid->ToString(EGuidFormats::Digits),
            Members.Num(), SequenceNumber);
    }

    // --- REMOVE: Remove member from collection ---
    else if (OpType == COLLECTION_OP_REMOVE)
    {
        if (GCollectionMembership.Contains(*CollectionGuid))
        {
            TSet<FGuid>& Members = GCollectionMembership.FindChecked(*CollectionGuid);
            Members.Remove(TargetGuid);

            // Clean up empty collections
            if (Members.Num() == 0)
            {
                GCollectionMembership.Remove(*CollectionGuid);
            }
        }

        Stats.CollectionRemovesApplied.fetch_add(1, std::memory_order_relaxed);

        UE_LOG(LogLiveSync, Log,
            TEXT("[COLLECTION][REMOVE] %s ← Collection=%s seq=%u"),
            *TargetGuid.ToString(EGuidFormats::Digits),
            *CollectionGuid->ToString(EGuidFormats::Digits),
            SequenceNumber);
    }

    // --- MOVE: Remove from old collection, add to new ---
    else if (OpType == COLLECTION_OP_MOVE)
    {
        // Find old collection by scanning membership registry
        FGuid OldCollectionGuid;
        bool bFoundOld = false;

        for (const auto& Pair : GCollectionMembership)
        {
            if (Pair.Value.Contains(TargetGuid))
            {
                OldCollectionGuid = Pair.Key;
                bFoundOld = true;
                break;
            }
        }

        // Remove from old collection
        if (bFoundOld)
        {
            TSet<FGuid>& OldMembers = GCollectionMembership.FindChecked(OldCollectionGuid);
            OldMembers.Remove(TargetGuid);
            if (OldMembers.Num() == 0)
            {
                GCollectionMembership.Remove(OldCollectionGuid);
            }
        }

        // Add to new collection
        if (CollectionGuid && CollectionGuid->IsValid())
        {
            TSet<FGuid>& NewMembers = GCollectionMembership.FindOrAdd(*CollectionGuid);
            NewMembers.Add(TargetGuid);
        }

        Stats.CollectionMovesApplied.fetch_add(1, std::memory_order_relaxed);

        if (bFoundOld)
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[COLLECTION][MOVE] %s OldCollection=%s → NewCollection=%s seq=%u"),
                *TargetGuid.ToString(EGuidFormats::Digits),
                *OldCollectionGuid.ToString(EGuidFormats::Digits),
                CollectionGuid ? *CollectionGuid->ToString(EGuidFormats::Digits) : TEXT("ROOT"),
                SequenceNumber);
        }
        else
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[COLLECTION][MOVE] %s → NewCollection=%s (no prior collection) seq=%u"),
                *TargetGuid.ToString(EGuidFormats::Digits),
                CollectionGuid ? *CollectionGuid->ToString(EGuidFormats::Digits) : TEXT("ROOT"),
                SequenceNumber);
        }
    }

    // --- CLEAR: Remove all members from collection ---
    else if (OpType == COLLECTION_OP_CLEAR)
    {
        if (CollectionGuid && GCollectionMembership.Contains(*CollectionGuid))
        {
            TSet<FGuid>& Members = GCollectionMembership.FindChecked(*CollectionGuid);
            const int32 RemovedCount = Members.Num();
            Members.Empty();
            GCollectionMembership.Remove(*CollectionGuid);

            Stats.CollectionClearsApplied.fetch_add(1, std::memory_order_relaxed);

            UE_LOG(LogLiveSync, Log,
                TEXT("[COLLECTION][CLEAR] Collection=%s removed=%d seq=%u"),
                *CollectionGuid->ToString(EGuidFormats::Digits),
                RemovedCount, SequenceNumber);
        }
        else
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[COLLECTION][CLEAR] Collection=%s (empty or unknown) seq=%u"),
                CollectionGuid ? *CollectionGuid->ToString(EGuidFormats::Digits) : TEXT("NULL"),
                SequenceNumber);
        }
    }

    // --- CLASSIFY and LOG for identity ops (CREATE/DELETE/RENAME/REPARENT) ---
    else
    {
        const TCHAR* OpLabel = TEXT("UNKNOWN");

        switch (OpType)
        {
            case COLLECTION_OP_COLLECTION_CREATE:
                OpLabel = TEXT("COLLECTION_CREATE");
                if (CollectionGuid)
                {
                    GCollectionIdentities.FindOrAdd(*CollectionGuid) = FString();
                }
                break;
            case COLLECTION_OP_COLLECTION_DELETE:
                OpLabel = TEXT("COLLECTION_DELETE");
                if (CollectionGuid)
                {
                    GCollectionIdentities.Remove(*CollectionGuid);
                    GCollectionMembership.Remove(*CollectionGuid);
                }
                break;
            case COLLECTION_OP_COLLECTION_REPARENT:
                OpLabel = TEXT("COLLECTION_REPARENT");
                break;
            case COLLECTION_OP_RENAME_REF:
                OpLabel = TEXT("RENAME_REF");
                break;
        }

        UE_LOG(LogLiveSync, Log,
            TEXT("[COLLECTION][%s] GUID=%s Seq=%u Flags=0x%02X ts=%.3f"),
            OpLabel,
            *TargetGuid.ToString(EGuidFormats::Digits),
            SequenceNumber,
            OpFlags,
            Timestamp);
    }

    // =====================================================
    // UPDATE sequence tracker
    // =====================================================

    GCollectionSequences.Update(TargetGuid, SequenceNumber);

    UE_LOG(LogLiveSync, Log,
        TEXT("[COLLECTION][DIAG] Registry updated: TargetGuid=%s OpType=0x%02X "
             "CollectionMemberCount=%d TotalCollections=%d"),
        *TargetGuid.ToString(EGuidFormats::Digits),
        OpType,
        CollectionGuid ? GCollectionMembership.FindRef(*CollectionGuid).Num() : 0,
        GCollectionMembership.Num());
}


// =========================================================
// RECORD COLLECTION REPLAY PAYLOAD (Phase 6F Stage 5)
// =========================================================
// Appends a raw collection per-object payload (30 or 46 bytes)
// to the replay ring buffer. Bounded at 2048 entries with
// FIFO eviction on overflow. Stores sequence + checksum
// metadata for Stage 6 ordering validation + corruption
// detection.
// =========================================================

void UUELiveSyncSubsystem::
RecordCollectionReplayPayload(
    const uint8* Payload,
    int32 PayloadSize,
    uint32 SequenceNumber)
{
    if (!GCollectionReplayEnabled)
    {
        return;
    }

    TArray<uint8> Entry;
    Entry.Append(Payload, PayloadSize);

    if (GCollectionReplayBuffer.Num() >= COLLECTION_REPLAY_MAX)
    {
        Stats.CollectionReplayBufferOverflow.fetch_add(
            1, std::memory_order_relaxed);
        Stats.CollectionReplayPacketsDropped.fetch_add(
            1, std::memory_order_relaxed);
        GCollectionReplayBuffer.RemoveAt(0, 1, EAllowShrinking::No);
        GCollectionReplaySequences.RemoveAt(0, 1, EAllowShrinking::No);
        GCollectionReplayChecksums.RemoveAt(0, 1, EAllowShrinking::No);
    }

    // Track peak buffer usage
    if (GCollectionReplayBuffer.Num() + 1 > GCollectionReplayPeakUsage)
    {
        GCollectionReplayPeakUsage = GCollectionReplayBuffer.Num() + 1;
        Stats.CollectionReplayPeakBufferUsage.store(
            GCollectionReplayPeakUsage,
            std::memory_order_relaxed);
    }

    // Compute FNV-1a checksum for corruption detection (Stage 6)
    uint32 Check = CollectionReplayChecksum(Payload, PayloadSize);

    GCollectionReplayBuffer.Add(MoveTemp(Entry));
    GCollectionReplaySequences.Add(SequenceNumber);
    GCollectionReplayChecksums.Add(Check);
}


// =========================================================
// SET COLLECTION REPLAY ENABLED (Phase 6F Stage 5)
// =========================================================

void UUELiveSyncSubsystem::
SetCollectionReplayEnabled(bool bEnabled)
{
    GCollectionReplayEnabled = bEnabled;
}


// =========================================================
// REPLAY TIMELINE (Phase 6F Stage 7 — Observability)
// =========================================================

void UUELiveSyncSubsystem::
RecordReplayTimelineEvent(const FReplayTimelineEvent& Event)
{
    GCollectionReplayTimeline.Record(Event);
    Stats.CollectionReplayTimelineRecorded.fetch_add(
        1, std::memory_order_relaxed);
}

void UUELiveSyncSubsystem::
ClearReplayTimeline()
{
    GCollectionReplayTimeline.Clear();
    Stats.CollectionReplayTimelineRecorded.store(
        0, std::memory_order_relaxed);
}

const FReplayTimeline& UUELiveSyncSubsystem::
GetReplayTimeline() const
{
    return GCollectionReplayTimeline;
}


// =========================================================
// REPLAY TRACE SYSTEM (Phase 6F Stage 7 — Observability)
// =========================================================

void UUELiveSyncSubsystem::
EmitReplayTrace(
    EReplayTraceCategory Category,
    const FString& Message)
{
    if (!GCollectionReplayTraceConfig.bTracingEnabled)
        return;

    if (!EnumHasAnyFlags(
            GCollectionReplayTraceConfig.CategoryMask,
            Category))
        return;

    Stats.CollectionReplayTracesEmitted.fetch_add(
        1, std::memory_order_relaxed);

    UE_LOG(LogLiveSync, Log,
        TEXT("[REPLAY][TRACE] %s"), *Message);
}

bool UUELiveSyncSubsystem::
IsReplayTracingActive(EReplayTraceCategory Category) const
{
    if (!GCollectionReplayTraceConfig.bTracingEnabled)
        return false;
    return EnumHasAnyFlags(
        GCollectionReplayTraceConfig.CategoryMask,
        Category);
}

void UUELiveSyncSubsystem::
SetReplayTracingEnabled(
    bool bEnabled,
    EReplayTraceCategory CategoryMask)
{
    GCollectionReplayTraceConfig.bTracingEnabled = bEnabled;

    if (bEnabled)
    {
        GCollectionReplayTraceConfig.CategoryMask = CategoryMask;
        UE_LOG(LogLiveSync, Log,
            TEXT("[REPLAY][TRACE] Tracing enabled (mask=0x%02X)"),
            static_cast<uint8>(CategoryMask));
    }
    else
    {
        GCollectionReplayTraceConfig.CategoryMask =
            EReplayTraceCategory::None;
        UE_LOG(LogLiveSync, Log,
            TEXT("[REPLAY][TRACE] Tracing disabled"));
    }
}

#include "UELiveSyncSubsystem_Replay.inl"


// =========================================================
// HANDLE ASSET DEF (V5)
// =========================================================

void UUELiveSyncSubsystem::
HandleAssetDef(
    const FGuid& Guid,
    uint64 IdentityHigh,
    uint64 IdentityLow,
    uint8 PrimitiveFallback)
{
    CHECK_GAME_THREAD();
    FAssetIdentityRef Identity;
    Identity.High = IdentityHigh;
    Identity.Low  = IdentityLow;

    if (!Identity.IsValid())
    {
        Stats.AssetDefsSkipped.fetch_add(
            1,
            std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // (STAGE 4) REJECT: Tombstoned GUID — object was deleted
    // =====================================================

    if (IsTombstoned(Guid))
    {
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[ASSETDEF][TOMBSTONE] GUID=%s — blocked by tombstone"),
            *Guid.ToString(EGuidFormats::Digits));
        Stats.DeleteTombstoneRejections.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    FAssetMetadata& Meta =
        AssetMetadata.FindOrAdd(Guid);

    if (Meta.bResolved &&
        Meta.Identity == Identity)
    {
        return;
    }

    Meta.Identity = Identity;
    Meta.PrimitiveFallback =
        PrimitiveFallback;
    Meta.RetryCount = 0;
    Meta.NextRetryTime = 0.0;
    Meta.RetryInterval =
        ASSET_RETRY_INTERVAL_INITIAL;
    Meta.bResolved = false;
    Meta.bFallbackAssigned = false;

    PendingAssetQueue.Enqueue(Guid);

    if (bEnableVerboseSyncLogs)
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("[AssetDef] GUID=%s Identity=0x%llx%llx "
                 "Fallback=%d"),
            *Guid.ToString(
                EGuidFormats::Digits),
            Identity.High,
            Identity.Low,
            PrimitiveFallback);
    }
}


// =========================================================
// RESOLVE PENDING ASSETS
// =========================================================

void UUELiveSyncSubsystem::
ResolvePendingAssets()
{
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ResolvePendingAssets);
    double Now =
        FPlatformTime::Seconds();
    int32 ResolvedThisTick = 0;

    // Phase 7A Stage 2: Evict stale metadata entries that have
    // exceeded ASSET_STALE_TIMEOUT (60s) without resolution.
    // Prevents unbounded accumulation in AssetMetadata when
    // deleted GUIDs or orphaned entries are never cleaned.
    {
        TArray<FGuid> StaleKeys;
        for (const auto& Pair : AssetMetadata)
        {
            if (Pair.Value.HasTimedOut(Now))
            {
                StaleKeys.Add(Pair.Key);
            }
        }
        for (const FGuid& Key : StaleKeys)
        {
            AssetMetadata.Remove(Key);
            PendingAssetQueue.Remove(Key);
            Stats.StaleEvictions++;
        }
    }

    FGuid Guid;

    // Phase 5E fix: ResolvedThisTick is incremented at the TOP
    // of each iteration so the loop is ALWAYS bounded by
    // MAX_ASSET_RESOLUTIONS_PER_TICK (=8) regardless of which
    // code path is taken.  Without this, a tick where all
    // dequeued GUIDs hit the "re-enqueue (NextRetryTime not yet
    // reached)" path would loop indefinitely because
    // ResolvedThisTick was only incremented on successful
    // resolution — leading to a non-responsive editor hang.
    while (
        ResolvedThisTick <
            MAX_ASSET_RESOLUTIONS_PER_TICK &&
        PendingAssetQueue.Dequeue(Guid))
    {
        ResolvedThisTick++;

        FAssetMetadata* Meta =
            AssetMetadata.Find(Guid);

        if (!Meta)
        {
            continue;
        }

        if (Meta->bResolved)
        {
            continue;
        }

        if (Now < Meta->NextRetryTime)
        {
            PendingAssetQueue.Enqueue(Guid);
            continue;
        }

        FSoftObjectPath*
            CachedPath =
                AssetPathCache.Find(
                    Meta->Identity);

        if (!CachedPath ||
            CachedPath->IsNull())
        {
            Stats.AssetLookupsAttempted.fetch_add(
    1,
    std::memory_order_relaxed);

            Meta->RetryCount++;
            Meta->RetryInterval =
                FMath::Min(
                    Meta->RetryInterval * 2.0,
                    ASSET_RETRY_INTERVAL_MAX);
            Meta->NextRetryTime =
                Now + Meta->RetryInterval;

            if (Meta->RetryCount <
                MAX_ASSET_RETRY_ATTEMPTS)
            {
                PendingAssetQueue.Enqueue(Guid);
            }
            else
            {
                UE_LOG(
                    LogLiveSync,
                    Warning,
                    TEXT("[Asset] Resolution failed "
                         "for GUID=%s after %d retries "
                         "\u2014 using fallback primitive"),
                    *Guid.ToString(
                        EGuidFormats::Digits),
                    MAX_ASSET_RETRY_ATTEMPTS);

                Stats.AssetLookupsFailed.fetch_add(
                    1,
                    std::memory_order_relaxed);
                AssignFallbackPrimitive(
                    Guid,
                    Meta->PrimitiveFallback);
                Meta->bResolved = true;
                Meta->bFallbackAssigned = true;
            }

            continue;
        }

        AssignStaticMesh(Guid, *CachedPath);
        Meta->bResolved = true;
        Stats.AssetAssignmentsSucceeded.fetch_add(
            1,
            std::memory_order_relaxed);
    }
}


// =========================================================
// ASSIGN STATIC MESH
// =========================================================

void UUELiveSyncSubsystem::
AssignStaticMesh(
    const FGuid& Guid,
    const FSoftObjectPath& Path)
{
    AActor* Actor =
        FindActorFast(Guid);

    if (!Actor)
    {
        if (bEnableVerboseSyncLogs)
        {
            UE_LOG(
                LogLiveSync,
                Verbose,
                TEXT("[Asset] Assign deferred \u2014 "
                     "actor not yet created for GUID=%s"),
                *Guid.ToString(
                    EGuidFormats::Digits));
        }

        PendingAssetQueue.Enqueue(Guid);
        return;
    }

    UStaticMeshComponent* MeshComp =
        Actor->FindComponentByClass<
            UStaticMeshComponent>();

    if (!MeshComp)
    {
        return;
    }

    UStaticMesh* Mesh = Cast<UStaticMesh>(
        Path.TryLoad());

    if (!Mesh)
    {
        return;
    }

    MeshComp->SetStaticMesh(Mesh);

    Stats.AssetAssignmentsSucceeded++;
}


// =========================================================
// ASSIGN FALLBACK PRIMITIVE
// =========================================================

void UUELiveSyncSubsystem::
AssignFallbackPrimitive(
    const FGuid& Guid,
    uint8 PrimitiveType)
{
    AActor* Actor =
        FindActorFast(Guid);

    if (!Actor)
    {
        return;
    }

    UStaticMeshComponent* MeshComp =
        Actor->FindComponentByClass<
            UStaticMeshComponent>();

    if (!MeshComp)
    {
        MeshComp =
            NewObject<
                UStaticMeshComponent>(
                    Actor);

        if (Actor->GetRootComponent())
        {
            MeshComp->SetupAttachment(
                Actor->GetRootComponent());
        }
        else
        {
            Actor->SetRootComponent(
                MeshComp);
        }
    }

    UStaticMesh* Mesh =
        GetPrimitiveMesh(PrimitiveType);

    if (Mesh)
    {
        MeshComp->SetStaticMesh(Mesh);
        MeshComp->SetMobility(
            EComponentMobility::Movable);

        if (!MeshComp->IsRegistered())
        {
            MeshComp->RegisterComponent();
        }
    }
}


// =========================================================
// CACHE ASSET PATH
// =========================================================

void UUELiveSyncSubsystem::
CacheAssetPath(
    const FAssetIdentityRef& Identity,
    const FSoftObjectPath& Path)
{
    if (Identity.IsValid() &&
        !Path.IsNull())
    {
        AssetPathCache.Add(
            Identity,
            Path);
    }
}


// =========================================================
// HANDLE BEGIN SNAPSHOT
// =========================================================

void UUELiveSyncSubsystem::
HandleBeginSnapshot()
{
    bInSnapshotBuild = true;
    SnapshotStartTime =
        FPlatformTime::Seconds();

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("Snapshot build started — entering accumulation mode"));
}


// =========================================================
// ABORT SNAPSHOT
// =========================================================

// Aborts an in-progress snapshot build and clears pending attachment state.
void UUELiveSyncSubsystem::
AbortSnapshot()
{
    if (!bInSnapshotBuild)
    {
        return;
    }

    bInSnapshotBuild = false;
    SnapshotStartTime = 0.0;

    int32 PendingCount =
        PendingAttachments.Num();

    PendingAttachments.Empty();

    UE_LOG(
        LogLiveSync,
        Warning,
        TEXT("Snapshot aborted — flushed %d pending attachments"),
        PendingCount);
}


// =========================================================
// HANDLE END SNAPSHOT
// =========================================================

void UUELiveSyncSubsystem::
HandleEndSnapshot()
{
    bInSnapshotBuild = false;

    // Resolve all deferred hierarchy attachments
    ResolvePendingAttachments();

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("Snapshot build ended — flushed %d pending attachments"),
        PendingAttachments.Num());

    // Clear semantic hierarchy deferred queue (don't carry across sessions)
    PendingHierarchyAttachments.Empty();

    UE_LOG(LogLiveSync, Log,
        TEXT("[HIERARCHY] PendingHierarchyAttachments cleared (EndSnapshot)"));

    // Phase 6E: process deferred deletes, then clear queue
    // Ordering guarantee: deferred deletes processed BEFORE transient
    // replay state is cleared. Snapshot replay is authoritative after
    // EndSnapshot — stale replay cannot mutate runtime.
    if (DeferredDeleteQueue.Num() > 0)
    {
        TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessDeferredDeletes);

        UE_LOG(LogLiveSync, Log,
            TEXT("[DELETE] Processing %d deferred deletes after EndSnapshot"),
            DeferredDeleteQueue.Num());

        for (const FDeferredDelete& Del : DeferredDeleteQueue)
        {
            HandleDelete(Del.TargetGuid, Del.Sequence, Del.Timestamp, EChangeOrigin::Replay);
        }
        DeferredDeleteQueue.Empty();

        UE_LOG(LogLiveSync, Verbose,
            TEXT("[DELETE] Deferred queue cleared after processing"));
    }

    // =====================================================
    // COLLECTION REPLAY (Phase 6F Stage 5–7)
    // =====================================================
    // After snapshot is fully built, replay the recorded
    // collection packet stream to synchronize collection
    // membership and identity state.
    // =====================================================

    if (GCollectionReplayEnabled &&
        GCollectionReplayBuffer.Num() > 0)
    {
        const double ReplayStart = FPlatformTime::Seconds();
        const int32 PreReplayCount =
            GCollectionReplayBuffer.Num();

        ReplayCollectionStream();
        CheckReplayBufferHealth();

        const double ReplayDuration =
            (FPlatformTime::Seconds() - ReplayStart) * 1000.0;

        Stats.CollectionReplayReconnectRebuilds.fetch_add(
            1, std::memory_order_relaxed);
        Stats.CollectionReplayReconnectPacketsReplayed.fetch_add(
            PreReplayCount, std::memory_order_relaxed);

        // Check divergence after replay
        const uint64 CurrentHash = ComputeCollectionStateHash();
        if (GCollectionLastVerifiedHash != 0 &&
            CurrentHash != GCollectionLastVerifiedHash)
        {
            Stats.CollectionReplayReconnectDivergences.fetch_add(
                1, std::memory_order_relaxed);
        }

        UE_LOG(LogLiveSync, Log,
            TEXT("[COLLECTION] Replay: %d entries processed on reconnect (%.1f ms)"),
            PreReplayCount, ReplayDuration);
    }
    else if (GCollectionReplayEnabled)
    {
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[COLLECTION] Replay buffer empty — skipping replay on EndSnapshot"));
    }
}


// =========================================================
// RESOLVE PENDING ATTACHMENTS
// =========================================================

void UUELiveSyncSubsystem::
ResolvePendingAttachments()
{
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ResolvePendingAttachments);
    double Now =
        FPlatformTime::Seconds();

    TArray<FPendingAttachment>
        Remaining;

    static constexpr int32
        MaxRetries = 60;

    static constexpr int32
        FastWindow = 10;

    for (const FPendingAttachment&
        Entry : PendingAttachments)
    {
        int32 Retries =
            Entry.RetryFrames + 1;

        // Timeout: 60 retry attempts or 5s wall-clock
        if (Retries >= MaxRetries ||
            (Now - Entry.CreatedTime) > 5.0)
        {
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT(
                    "Deferred attach timeout: "
                    "child=%s parent=%s"),
                *Entry.Child.ToString(
                    EGuidFormats::Digits),
                *Entry.Parent.ToString(
                    EGuidFormats::Digits));

            continue;
        }

        // Determine if this frame is a retry frame
        bool bRetryFrame = (
            Retries <= FastWindow ||
            Retries % 5 == 0
        );

        FPendingAttachment Updated =
            Entry;

        Updated.RetryFrames = Retries;

        if (bRetryFrame)
        {
            AActor* Parent =
                FindActorFast(
                    Entry.Parent);

            if (Parent)
            {
                AActor* Child =
                    FindActorFast(
                        Entry.Child);

                if (Child)
                {
                    // =============================================
                    // ATTACHMENT-CYCLE PROTECTION
                    // =============================================

                    // 1. Self-parent check
                    if (Child == Parent)
                    {
                        UE_LOG(LogLiveSync, Error,
                            TEXT("ATTACH CYCLE: self-parent detected child=%s parent=%s — aborting"),
                            *Entry.Child.ToString(EGuidFormats::Digits),
                            *Entry.Parent.ToString(EGuidFormats::Digits));
                        continue;
                    }

                    // 2. Stale actor validation
                    if (!IsValid(Child) || !IsValid(Parent))
                    {
                        UE_LOG(LogLiveSync, Warning,
                            TEXT("ATTACH STALE: child=%s valid=%d | parent=%s valid=%d — aborting"),
                            *Entry.Child.ToString(EGuidFormats::Digits),
                            IsValid(Child) ? 1 : 0,
                            *Entry.Parent.ToString(EGuidFormats::Digits),
                            IsValid(Parent) ? 1 : 0);
                        continue;
                    }

                    // 3. Circular chain detection
                    // Walk up parent chain, verify child never appears as ancestor
                    {
                        AActor* Probe = Parent;
                        int32 Depth = 0;
                        bool bCircular = false;

                        while (Probe && Depth < 128)
                        {
                            if (Probe == Child)
                            {
                                UE_LOG(LogLiveSync, Error,
                                    TEXT("ATTACH CYCLE: circular chain child=%s parent=%s detected at depth=%d — aborting"),
                                    *Entry.Child.ToString(EGuidFormats::Digits),
                                    *Entry.Parent.ToString(EGuidFormats::Digits),
                                    Depth);
                                bCircular = true;
                                break;
                            }
                            Probe = Probe->GetAttachParentActor();
                            Depth++;
                        }

                        if (bCircular)
                        {
                            continue;
                        }

                        if (Depth >= 128)
                        {
                            UE_LOG(LogLiveSync, Error,
                                TEXT("ATTACH CYCLE: excessive depth (%d) child=%s — aborting"),
                                Depth, *Entry.Child.ToString(EGuidFormats::Digits));
                            continue;
                        }
                    }

                    // 4. Oscillating parent reassignment detection
                    // If same child had a different parent recently, log and track
                    {
                        static TMap<FGuid, FGuid> LastAssignedParent;
                        FGuid* PrevParent = LastAssignedParent.Find(Entry.Child);
                        if (PrevParent && *PrevParent != Entry.Parent)
                        {
                            // Check if this oscillates (old parent becomes child's new parent's child?)
                            UE_LOG(LogLiveSync, Warning,
                                TEXT("ATTACH OSCILLATION: child=%s parent reassign %s -> %s"),
                                *Entry.Child.ToString(EGuidFormats::Digits),
                                *PrevParent->ToString(EGuidFormats::Digits),
                                *Entry.Parent.ToString(EGuidFormats::Digits));
                        }
                        LastAssignedParent.Add(Entry.Child, Entry.Parent);
                    }

                    UE_LOG(LogLiveSync, Log,
                        TEXT("  BEGIN AttachToActor child=%s parent=%s"),
                        *Entry.Child.ToString(EGuidFormats::Digits),
                        *Entry.Parent.ToString(EGuidFormats::Digits));

                    Child->AttachToActor(
                        Parent,
                        FAttachmentTransformRules::
                            KeepWorldTransform);

                    UE_LOG(LogLiveSync, Log,
                        TEXT("  END   AttachToActor child=%s parent=%s"),
                        *Entry.Child.ToString(EGuidFormats::Digits),
                        *Entry.Parent.ToString(EGuidFormats::Digits));

                    // Patch 1: Force one world-space recompute
                    // on next interp tick. Child may have
                    // advanced local interpolation while waiting
                    // for parent resolution.
                    if (FSyncTransformState*
                        State =
                        TransformStates.Find(
                            Entry.Child))
                    {
                        State->
                            bPendingSceneGraphWrite =
                            true;
                    }

                    if (
                        bEnableVerboseSyncLogs
                    )
                    {
                        UE_LOG(
                            LogLiveSync,
                            Log,
                            TEXT(
                                "Authority: deferred"
                                " attach resolved"
                                " child=%s parent=%s"),
                            *Entry.Child.ToString(
                                EGuidFormats::Digits),
                            *Entry.Parent.ToString(
                                EGuidFormats::Digits));
                    }

                    // Resolved — don't requeue
                    continue;
                }
            }
        }

        // Not resolved yet — keep for next frame
        Remaining.Add(Updated);
    }

    PendingAttachments =
        MoveTemp(Remaining);
}


// =========================================================
// RECOVER MISSING ACTORS
// =========================================================

void UUELiveSyncSubsystem::
RecoverMissingActors()
{
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_RecoverMissingActors);
    double Now =
        FPlatformTime::Seconds();

    static constexpr int32
        MaxRecoveryAttempts = 3;

    TArray<FGuid> Resolved;

    for (auto& Pair :
        MissingActorTracker)
    {
        const FGuid& Guid =
            Pair.Key;

        FMissingActorState&
            State =
            Pair.Value;

        AActor* Existing =
            FindActorFast(Guid);

        if (Existing)
        {
            // Actor reappeared naturally
            Resolved.Add(Guid);
            continue;
        }

        FSyncTransformState*
            TransformState =
            TransformStates.Find(
                Guid);

        if (!TransformState)
        {
            // No transform state — nothing to recover
            Resolved.Add(Guid);
            continue;
        }

        State.MissingFrames++;

        if (State.MissingFrames >= 10 &&
            (Now - State.LastWarningTime) > 30.0)
        {
            State.LastWarningTime = Now;

            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("Missing actor GUID=%s — %d frames"),
                *Guid.ToString(
                    EGuidFormats::Digits),
                State.MissingFrames);
        }

        if (State.MissingFrames >= 30 &&
            !State.bRecoveryAttempted &&
            State.RecoveryAttempts < MaxRecoveryAttempts)
        {
            State.bRecoveryAttempted = true;
            State.RecoveryAttempts++;

            UE_LOG(
                LogLiveSync,
                Log,
                TEXT("Recovering missing actor GUID=%s (attempt %d/%d)"),
                *Guid.ToString(
                    EGuidFormats::Digits),
                State.RecoveryAttempts,
                MaxRecoveryAttempts);

            if (TransformState->bHasLocalTarget &&
                TransformState->ParentGuid.IsValid())
            {
                // Child recovery: pass local values directly.
                // HandleCreateObject computes world for spawn.
                HandleCreateObject(
                    Guid,
                    TransformState->
                        LocalTargetLocation,
                    TransformState->
                        LocalTargetRotation,
                    TransformState->
                        LocalTargetScale,
                    TransformState->ParentGuid,
                    LSP_Cube,
                    true);

                // Initialize state with stored local values
                UpdateTargetTransform(
                    Guid,
                    TransformState->
                        LocalTargetLocation,
                    TransformState->
                        LocalTargetRotation,
                    TransformState->
                        LocalTargetScale,
                    TransformState->ParentGuid,
                    true);
            }
            else
            {
                // Root recovery: pass world-space values.
                HandleCreateObject(
                    Guid,
                    TransformState->
                        TargetLocation,
                    TransformState->
                        TargetRotation,
                    TransformState->
                        TargetScale,
                    TransformState->ParentGuid,
                    LSP_Cube,
                    false);

                // Initialize state with stored world values
                UpdateTargetTransform(
                    Guid,
                    TransformState->
                        TargetLocation,
                    TransformState->
                        TargetRotation,
                    TransformState->
                        TargetScale,
                    TransformState->ParentGuid,
                    false);
            }
        }
        else if (State.MissingFrames >= 30 &&
                 State.RecoveryAttempts >= MaxRecoveryAttempts)
        {
            // Suppress repeated warning logs — only log once per frame
            if (State.MissingFrames == 30)
            {
                UE_LOG(
                    LogLiveSync,
                    Warning,
                    TEXT("Missing actor GUID=%s — max recovery attempts (%d) reached, giving up"),
                    *Guid.ToString(
                        EGuidFormats::Digits),
                    MaxRecoveryAttempts);
            }
        }

        if (State.MissingFrames > 60)
        {
            UE_LOG(
                LogLiveSync,
                Log,
                TEXT("Giving up recovery for GUID=%s — evicting"),
                *Guid.ToString(
                    EGuidFormats::Digits));

            TransformStates.Remove(Guid);

            Resolved.Add(Guid);
        }
    }

    for (const FGuid& Guid :
        Resolved)
    {
        MissingActorTracker.Remove(
            Guid);
    }
}


// =========================================================
// ROLLING METRICS (EMA-based, called every tick)
// =========================================================

void UUELiveSyncSubsystem::
TickMetrics(float DeltaTime)
{
    if (DeltaTime <= 0.0f)
        return;

    double Now =
        FPlatformTime::Seconds();

    double Elapsed =
        Now - LastRateSampleTime;

    // Compute instantaneous rate every ~1s
    if (LastRateSampleTime > 0.0 &&
        Elapsed >= 1.0)
    {
        int32 PacketsRecv =
            Stats.PacketsReceived.load(
                std::memory_order_relaxed);

        int64 BytesRecv =
            Stats.TotalBytesReceived.load(
                std::memory_order_relaxed);

        double InstantPktPerSec =
            (double)(PacketsRecv -
                LastRateSamplePackets) /
            Elapsed;

        double InstantBytesPerSec =
            (double)(BytesRecv -
                LastRateSampleBytes) /
            Elapsed;

        // EMA smoothing factor (≈ 4-second half-life)
        const double Alpha = 0.15;

        if (Stats.PacketsPerSecondEMA == 0.0)
        {
            Stats.PacketsPerSecondEMA =
                InstantPktPerSec;
        }
        else
        {
            Stats.PacketsPerSecondEMA =
                Alpha * InstantPktPerSec +
                (1.0 - Alpha) *
                    Stats.PacketsPerSecondEMA;
        }

        if (Stats.BytesPerSecondEMA == 0.0)
        {
            Stats.BytesPerSecondEMA =
                InstantBytesPerSec;
        }
        else
        {
            Stats.BytesPerSecondEMA =
                Alpha * InstantBytesPerSec +
                (1.0 - Alpha) *
                    Stats.BytesPerSecondEMA;
        }

        // Track peaks
        if (Stats.PacketsPerSecondEMA >
            Stats.PeakPacketsPerSecond)
        {
            Stats.PeakPacketsPerSecond =
                Stats.PacketsPerSecondEMA;
        }

        if (Stats.BytesPerSecondEMA >
            Stats.PeakBytesPerSecond)
        {
            Stats.PeakBytesPerSecond =
                Stats.BytesPerSecondEMA;
        }

        // Update process time EMA
        double ProcessMs =
            Stats.AvgProcessTimeMs;

        if (Stats.ProcessTimeMsEMA == 0.0)
        {
            Stats.ProcessTimeMsEMA = ProcessMs;
        }
        else
        {
            Stats.ProcessTimeMsEMA =
                Alpha * ProcessMs +
                (1.0 - Alpha) *
                    Stats.ProcessTimeMsEMA;
        }

        if (Stats.AvgProcessTimeMs >
            Stats.PeakProcessTimeMs)
        {
            Stats.PeakProcessTimeMs =
                Stats.AvgProcessTimeMs;
        }

        LastRateSampleTime = Now;
        LastRateSamplePackets = PacketsRecv;
        LastRateSampleBytes = BytesRecv;
    }
}


// =========================================================
// SAFETY MONITORS (flood detection, queue pressure)
// =========================================================

void UUELiveSyncSubsystem::
TickSafetyMonitors(float DeltaTime)
{
    double Now =
        FPlatformTime::Seconds();

    // --- Flood detection ---
    if (FloodWindowStart == 0.0)
    {
        FloodWindowStart = Now;
    }

    double WindowElapsed =
        Now - FloodWindowStart;

    if (WindowElapsed >=
        FloodDetectionWindow)
    {
        int32 PacketsInWindow =
            Stats.PacketsReceived.load(
                std::memory_order_relaxed) -
            FloodPacketCount;

        double RatePerSec =
            (double)PacketsInWindow /
            WindowElapsed;

        if (RatePerSec >
            FloodThresholdPacketsPerSec)
        {
            Stats.FloodWarnings++;

            Stats.LastFloodWarningTime =
                Now;

            if (ShouldLogVerbose())
            {
                UE_LOG(
                    LogLiveSync,
                    Warning,
                    TEXT("[Safety] Flood detected: "
                         "%.0f pkt/s (threshold %d)"),
                    RatePerSec,
                    (int32)
                        FloodThresholdPacketsPerSec);
            }
        }

        FloodPacketCount =
            Stats.PacketsReceived.load(
                std::memory_order_relaxed);

        FloodWindowStart = Now;
    }

    // --- Queue pressure ---
    if (Stats.QueueDepthCurrent >= 0)
    {
        QueuePressureAccumulator +=
            (double)Stats.QueueDepthCurrent *
            DeltaTime;

        double AvgDepth =
            QueuePressureAccumulator /
            (Now - FloodWindowStart +
             0.001);

        if (AvgDepth >
            QueuePressureThreshold)
        {
            Stats.QueuePressureWarnings++;

            Stats.LastQueuePressureTime =
                Now;

            if (ShouldLogVerbose())
            {
                UE_LOG(
                    LogLiveSync,
                    Warning,
                    TEXT("[Safety] Queue pressure: "
                         "avg depth %.0f/%d"),
                    AvgDepth,
                    (int32)
                        QueuePressureThreshold);
            }
        }
    }

    // --- Packet age watchdog ---
    // Warn if packets are sitting in the queue too long
    // (indicates Tick pipeline is not keeping up).
    {
        int32 QueueDepth =
            PacketQueue.Size();

        if (QueueDepth > 0)
        {
            double AgeEstimate =
                (double)QueueDepth *
                (Stats.AvgProcessTimeMs / 1000.0 + 0.016);

            if (AgeEstimate > PacketAgeWarnThreshold &&
                Now - LastPacketAgeWarnTime > 10.0)
            {
                LastPacketAgeWarnTime = Now;

                UE_LOG(
                    LogLiveSync,
                    Warning,
                    TEXT("[Safety] Packet age watchdog: "
                         "%d queued, estimated oldest age %.1fs "
                         "(threshold %.1fs)"),
                    QueueDepth,
                    AgeEstimate,
                    PacketAgeWarnThreshold);
            }

            if (AgeEstimate > PacketAgeHardLimit)
            {
                UE_LOG(
                    LogLiveSync,
                    Error,
                    TEXT("[Safety] Packet age HARD LIMIT: "
                         "%d queued, estimated oldest age %.1fs "
                         "(limit %.1fs) — flushing queue"),
                    QueueDepth,
                    AgeEstimate,
                    PacketAgeHardLimit);

                PacketQueue.Clear();

                Stats.PacketsDropped.fetch_add(
                    QueueDepth,
                    std::memory_order_relaxed);
            }
        }
    }

    // --- Queue depth spike warning ---
    {
        int32 QueueDepth =
            PacketQueue.Size();

        if (QueueDepth >=
            FLiveSyncQueue::MaxQueueSize * 0.9)
        {
            static double LastSpikeWarnTime = 0.0;

            if (Now - LastSpikeWarnTime > 5.0)
            {
                LastSpikeWarnTime = Now;

                UE_LOG(
                    LogLiveSync,
                    Warning,
                    TEXT("[Safety] Queue depth spike: %d/%d "
                         "(90%% capacity) — processing may be saturated"),
                    QueueDepth,
                    FLiveSyncQueue::MaxQueueSize);
            }
        }
    }
}


// =========================================================
// SET QUEUE DEPTH PEAK (called from LiveSyncQueue)
// =========================================================

void UUELiveSyncSubsystem::
SetQueueDepthPeak(int32 Depth)
{
    if (Depth >
        Stats.QueueDepthPeak)
    {
        Stats.QueueDepthPeak = Depth;
    }
}


#if WITH_EDITOR
// =========================================================
// DEBUG DRAW OVERLAY
// =========================================================

void UUELiveSyncSubsystem::
DrawDebugOverlay()
{
    UWorld* World =
        GetWorld();

    if (!World)
        return;

    // Build a compact one-line status string
    FString Status;

    bool bConnected =
        ConnectionSocket &&
        ConnectionSocket->
            GetConnectionState()
            == SCS_Connected;

    Status +=
        bConnected
            ? TEXT("Connected")
            : TEXT("Disconnected");

    Status +=
        FString::Printf(
            TEXT(" | Queue:%d/%d"),
            Stats.QueueDepthCurrent,
            Stats.QueueDepthPeak);

    Status +=
        FString::Printf(
            TEXT(" | Pkt/s:%.0f"),
            Stats.PacketsPerSecondEMA);

    Status +=
        FString::Printf(
            TEXT(" | Recv:%d Drp:%d"),
            Stats.PacketsReceived.load(
                std::memory_order_relaxed),
            Stats.PacketsDropped.load(
                std::memory_order_relaxed));

    int32 PendingAssets = 0;
    int32 Unused = 0;
    PendingAssetQueue.GetDiagnostics(
        PendingAssets, Unused);

    if (PendingAssets > 0)
    {
        Status +=
            FString::Printf(
                TEXT(" | Assets:%d"),
                PendingAssets);
    }

    GEngine->AddOnScreenDebugMessage(
        984531,                    // unique key
        0.0f,                      // duration (0=persistent)
        bConnected
            ? FColor::Green
            : FColor::Red,
        TEXT("LiveSync: ") + Status);
}


// =========================================================
// DIAGNOSTICS TEXT (polled by Slate widget)
// =========================================================

FText UUELiveSyncSubsystem::
GetDiagnosticsText()
{
    FString Report;

    bool bConnected =
        ConnectionSocket &&
        ConnectionSocket->
            GetConnectionState()
            == SCS_Connected;

    // Connection
    Report +=
        FString::Printf(
            TEXT("Connection: %s\n"),
            bConnected
                ? TEXT("Connected")
                : TEXT("Disconnected"));

    if (bConnected)
    {
        Report +=
            FString::Printf(
                TEXT("  Uptime: %s\n"),
                *GetUptimeText().ToString());
    }

    // Objects
    Report +=
        FString::Printf(
            TEXT("Objects Tracked: %d\n"),
            TransformStates.Num());

    // Queue
    Report +=
        FString::Printf(
            TEXT("Queue: %d current / %d peak\n"),
            Stats.QueueDepthCurrent,
            Stats.QueueDepthPeak);

    // Pipeline counters
    Report +=
        FString::Printf(
            TEXT("Packets: %d recv / %d proc / %d drop / %d malformed\n"),
            Stats.PacketsReceived.load(
                std::memory_order_relaxed),
            Stats.PacketsProcessed.load(
                std::memory_order_relaxed),
            Stats.PacketsDropped.load(
                std::memory_order_relaxed),
            Stats.MalformedPackets.load(
                std::memory_order_relaxed));

    Report +=
        FString::Printf(
            TEXT("Bytes: %lld recv\n"),
            Stats.TotalBytesReceived.load(
                std::memory_order_relaxed));

    // Performance (EMA)
    Report +=
        FString::Printf(
            TEXT("Performance (EMA):\n"));

    Report +=
        FString::Printf(
            TEXT("  %.0f pkt/s (peak %.0f)\n"),
            Stats.PacketsPerSecondEMA,
            Stats.PeakPacketsPerSecond);

    Report +=
        FString::Printf(
            TEXT("  %.0f B/s (peak %.0f)\n"),
            Stats.BytesPerSecondEMA,
            Stats.PeakBytesPerSecond);

    Report +=
        FString::Printf(
            TEXT("  %.2f ms/pkt (peak %.2f)\n"),
            Stats.ProcessTimeMsEMA,
            Stats.PeakProcessTimeMs);

    // Event history summaries
    Report +=
        FString::Printf(
            TEXT("Events: %d reconnects, %d overflow events\n"),
            Stats.ReconnectCount.load(
                std::memory_order_relaxed),
            OverflowHistory.Num());

    // Safety
    if (Stats.FloodWarnings > 0 ||
        Stats.QueuePressureWarnings > 0)
    {
        Report +=
            FString::Printf(
                TEXT("Safety: %d flood warnings, %d queue pressure\n"),
                Stats.FloodWarnings,
                Stats.QueuePressureWarnings);
    }

    // Asset resolution (Phase 5D)
    Report +=
        FString::Printf(
            TEXT("Collection (Phase 6F):\n"));

    Report +=
        FString::Printf(
            TEXT("  Pkts: %d recv\n"),
            Stats.CollectionPacketsReceived.load(
                std::memory_order_relaxed));

    Report +=
        FString::Printf(
            TEXT("  Reject: %d stale / %d dup\n"),
            Stats.CollectionStaleRejected.load(
                std::memory_order_relaxed),
            Stats.CollectionDuplicateRejected.load(
                std::memory_order_relaxed));

    Report +=
        FString::Printf(
            TEXT("  Applied: %d add / %d rem / %d move / %d clear\n"),
            Stats.CollectionAddsApplied.load(
                std::memory_order_relaxed),
            Stats.CollectionRemovesApplied.load(
                std::memory_order_relaxed),
            Stats.CollectionMovesApplied.load(
                std::memory_order_relaxed),
            Stats.CollectionClearsApplied.load(
                std::memory_order_relaxed));

    Report +=
        FString::Printf(
            TEXT("  Registry: %d collections / %d memberships\n"),
            GCollectionIdentities.Num(),
            GCollectionMembership.Num());

    Report +=
        FString::Printf(
            TEXT("  Replay: %d buf / %d proc / %d rej / %d hash-mismatch / %d rebuilds\n"),
            GCollectionReplayBuffer.Num(),
            Stats.CollectionReplayProcessed.load(
                std::memory_order_relaxed),
            Stats.CollectionReplayRejected.load(
                std::memory_order_relaxed),
            Stats.CollectionSnapshotHashMismatch.load(
                std::memory_order_relaxed),
            Stats.CollectionSnapshotRebuilds.load(
                std::memory_order_relaxed));

    Report +=
        FString::Printf(
            TEXT("  Order: %d gap / %d ooo / %d div / %d corr / %d rb\n"),
            Stats.CollectionReplaySequenceGap.load(
                std::memory_order_relaxed),
            Stats.CollectionReplayOutOfOrder.load(
                std::memory_order_relaxed),
            Stats.CollectionReplayDivergence.load(
                std::memory_order_relaxed),
            Stats.CollectionReplayCorruption.load(
                std::memory_order_relaxed),
            Stats.CollectionReplayRollbacks.load(
                std::memory_order_relaxed));

    Report +=
        FString::Printf(
            TEXT("  Observ: %d tlev / %d trace / %d overflow / %d pk-usage / %d latency\n"),
            Stats.CollectionReplayTimelineRecorded.load(
                std::memory_order_relaxed),
            Stats.CollectionReplayTracesEmitted.load(
                std::memory_order_relaxed),
            Stats.CollectionReplayBufferOverflow.load(
                std::memory_order_relaxed),
            Stats.CollectionReplayPeakBufferUsage.load(
                std::memory_order_relaxed),
            Stats.CollectionReplayLatencySamples.load(
                std::memory_order_relaxed));

    Report +=
        FString::Printf(
            TEXT("  Reconn: %d rbd / %d replayed / %d div / %d rb\n"),
            Stats.CollectionReplayReconnectRebuilds.load(
                std::memory_order_relaxed),
            Stats.CollectionReplayReconnectPacketsReplayed.load(
                std::memory_order_relaxed),
            Stats.CollectionReplayReconnectDivergences.load(
                std::memory_order_relaxed),
            Stats.CollectionReplayReconnectRollbacks.load(
                std::memory_order_relaxed));

    Report +=
        FString::Printf(
            TEXT("  Timing: avg=%.1fms rbd=%.1fms hsh=%.3fms\n"),
            GCollectionReplayWindowStats.AvgDurationMs(),
            GCollectionReplayWindowStats.AvgRebuildMs(),
            GCollectionReplayWindowStats.AvgHashVerifyMs());

    Report +=
        FString::Printf(
            TEXT("  Hash: last=0x%016llX cur=0x%016llX %s\n"),
            GCollectionLastVerifiedHash,
            ComputeCollectionStateHash(),
            (GCollectionLastVerifiedHash != 0 &&
             GCollectionLastVerifiedHash != ComputeCollectionStateHash())
                ? TEXT("DIVERGED") : TEXT("OK"));

    Report +=
        FString::Printf(
            TEXT("  WorldReplay: %d buf / %d rec / %d ver / %d div / %d rb\n"),
            GWorldReplayBuffer.Num(),
            Stats.WorldReplayEntriesRecorded.load(
                std::memory_order_relaxed),
            Stats.WorldReplayVerifications.load(
                std::memory_order_relaxed),
            Stats.WorldReplayDivergences.load(
                std::memory_order_relaxed),
            Stats.WorldReplayRollbacks.load(
                std::memory_order_relaxed));

    Report +=
        FString::Printf(
            TEXT("  WorldReplay: %d corr / %d dep / %d exports / %d rebuilds / %d rec-rbd\n"),
            Stats.WorldReplayCorruption.load(
                std::memory_order_relaxed),
            Stats.WorldReplayDependencyViolations.load(
                std::memory_order_relaxed),
            Stats.WorldReplaySnapshotExports.load(
                std::memory_order_relaxed),
            Stats.WorldReplaySnapshotRebuilds.load(
                std::memory_order_relaxed),
            Stats.WorldReplayReconnectRebuilds.load(
                std::memory_order_relaxed));

    int32 PendingAssets = 0;
    int32 UnresolvedAssets = 0;
    PendingAssetQueue.GetDiagnostics(
        PendingAssets,
        UnresolvedAssets);

    Stats.PendingAssetCount = PendingAssets;
    if (PendingAssets > Stats.PendingAssetPeak)
    {
        Stats.PendingAssetPeak = PendingAssets;
    }

    Report +=
        FString::Printf(
            TEXT("Asset:\n"));

    Report +=
        FString::Printf(
            TEXT("  Defs Received: %d (skipped %d)\n"),
            Stats.AssetDefsReceived.load(
                std::memory_order_relaxed),
            Stats.AssetDefsSkipped.load(
                std::memory_order_relaxed));

    Report +=
        FString::Printf(
            TEXT("  Assignments: %d ok / %d fail\n"),
            Stats.AssetAssignmentsSucceeded.load(
                std::memory_order_relaxed),
            Stats.AssetAssignmentsFailed.load(
                std::memory_order_relaxed));

    Report +=
        FString::Printf(
            TEXT("  Lookups: %d attempt / %d fail\n"),
            Stats.AssetLookupsAttempted.load(
                std::memory_order_relaxed),
            Stats.AssetLookupsFailed.load(
                std::memory_order_relaxed));

    Report +=
        FString::Printf(
            TEXT("  Pending: %d resolved (queue: %d)\n"),
            AssetMetadata.Num() - PendingAssets,
            PendingAssets);

    return FText::FromString(Report);
}
#endif


// =========================================================
// LOG RUNTIME METRICS
// =========================================================

void UUELiveSyncSubsystem::
LogRuntimeMetrics()
{
    int32 StateCount =
        TransformStates.Num();

    int32 CacheCount =
        ActorCache.Num();

    int32 QueueSize =
        PacketQueue.Size();

    int32 Connected =
        ConnectionSocket &&
        ConnectionSocket->
            GetConnectionState()
            == SCS_Connected
        ? 1 : 0;

    int32 PacketsRecv =
        Stats.PacketsReceived.load(
            std::memory_order_relaxed);

    int32 PacketsProc =
        Stats.PacketsProcessed.load(
            std::memory_order_relaxed);

    int32 PacketsDrop =
        Stats.PacketsDropped.load(
            std::memory_order_relaxed);

    int32 Malformed =
        Stats.MalformedPackets.load(
            std::memory_order_relaxed);

    int64 BytesRecv =
        Stats.TotalBytesReceived.load(
            std::memory_order_relaxed);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("[Metrics] States=%d Cache=%d Queue=%d "
             "Connected=%d Recv=%d Proc=%d Drop=%d "
             "Malformed=%d Bytes=%lld "
             "Pkt/s=%.0f(EMA) pk=%.0f "
             "B/s=%.0f(EMA) pk=%.0f "
             "Process=%.2fms(EMA) pk=%.2f "
             "FloodW=%d QPress=%d "
             "Reconn=%d Overflows=%d"),
        StateCount,
        CacheCount,
        QueueSize,
        Connected,
        PacketsRecv,
        PacketsProc,
        PacketsDrop,
        Malformed,
        BytesRecv,
        Stats.PacketsPerSecondEMA,
        Stats.PeakPacketsPerSecond,
        Stats.BytesPerSecondEMA,
        Stats.PeakBytesPerSecond,
        Stats.ProcessTimeMsEMA,
        Stats.PeakProcessTimeMs,
        Stats.FloodWarnings,
        Stats.QueuePressureWarnings,
        Stats.ReconnectCount.load(
            std::memory_order_relaxed),
        OverflowHistory.Num());

    int32 PendingAssets = 0;
    int32 Unused = 0;
    PendingAssetQueue.GetDiagnostics(
        PendingAssets, Unused);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("[Metrics-Asset] Defs=%d Skip=%d "
             "Asgn=%dok/%dfail Lkup=%datm/%dfail "
             "Pending=%d Stale=%d"),
        Stats.AssetDefsReceived.load(
            std::memory_order_relaxed),
        Stats.AssetDefsSkipped.load(
            std::memory_order_relaxed),
        Stats.AssetAssignmentsSucceeded.load(
            std::memory_order_relaxed),
        Stats.AssetAssignmentsFailed.load(
            std::memory_order_relaxed),
        Stats.AssetLookupsAttempted.load(
            std::memory_order_relaxed),
        Stats.AssetLookupsFailed.load(
            std::memory_order_relaxed),
        PendingAssets,
        Stats.StaleEvictions);
}


// =========================================================
// RUNTIME METRICS DASHBOARD (compact verbose diagnostics)
// =========================================================
// Logs a one-line LiveSync Stats summary every 30s when verbose is enabled.
// Designed for quick health assessment in the UE Output Log.
// =========================================================

void UUELiveSyncSubsystem::
LogRuntimeMetricsVerbose()
{
    int32 StateCount =
        TransformStates.Num();

    int32 QueueSize =
        PacketQueue.Size();

    int32 Connected =
        ConnectionSocket &&
        ConnectionSocket->
            GetConnectionState()
            == SCS_Connected
        ? 1 : 0;

    int32 PacketsRecv =
        Stats.PacketsReceived.load(
            std::memory_order_relaxed);

    int32 PacketsProc =
        Stats.PacketsProcessed.load(
            std::memory_order_relaxed);

    int32 PacketsDrop =
        Stats.PacketsDropped.load(
            std::memory_order_relaxed);

    int32 Malformed =
        Stats.MalformedPackets.load(
            std::memory_order_relaxed);

    int32 Reconnects =
        Stats.ReconnectCount.load(
            std::memory_order_relaxed);

    int32 PendingAssets = 0;
    int32 Unused = 0;
    PendingAssetQueue.GetDiagnostics(
        PendingAssets, Unused);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("=== LiveSync Stats Dashboard ==="));

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  Connection: %s | Objects: %d | Queue: %d/%d | Assets: %d"),
        Connected ? TEXT("Connected") : TEXT("Disconnected"),
        StateCount,
        QueueSize,
        FLiveSyncQueue::MaxQueueSize,
        PendingAssets);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  Packets: %d recv / %d proc / %d drop / %d malformed"),
        PacketsRecv,
        PacketsProc,
        PacketsDrop,
        Malformed);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  Rates: %.0f pkt/s (peak %.0f) | %.2f ms/pkt (peak %.2f) | %.0f B/s"),
        Stats.PacketsPerSecondEMA,
        Stats.PeakPacketsPerSecond,
        Stats.ProcessTimeMsEMA,
        Stats.PeakProcessTimeMs,
        Stats.BytesPerSecondEMA);

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("  Safety: FloodW=%d QPress=%d Reconn=%d Overflows=%d"),
        Stats.FloodWarnings,
        Stats.QueuePressureWarnings,
        Reconnects,
        OverflowHistory.Num());

    UE_LOG(
        LogLiveSync,
        Log,
        TEXT("=== End Dashboard ==="));
}


// =========================================================
// HIERARCHY SAFETY VALIDATION
// =========================================================
// Runs periodically to detect:
//   - Self-parenting (child == parent)
//   - Circular attachment chains (parent is descendant of child)
//   - Invalid parent GUID (parent Guid.IsValid() but no actor exists)
//   - Orphaned pending attachments (parent never appeared)
// =========================================================

void UUELiveSyncSubsystem::
ValidateHierarchy()
{
    for (const auto& Pair :
        TransformStates)
    {
        const FGuid& Guid =
            Pair.Key;

        const FSyncTransformState&
            State =
            Pair.Value;

        if (!State.bHasParent)
        {
            continue;
        }

        const FGuid& ParentGuid =
            State.ParentGuid;

        // =====================================================
        // SELF-PARENT CHECK
        // =====================================================

        if (Guid == ParentGuid)
        {
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("[HierarchySafety] Self-parent detected: "
                     "GUID=%s is its own parent — detaching"),
                *Guid.ToString(
                    EGuidFormats::Digits));

            DetachFromParent(Guid);
            continue;
        }

        // =====================================================
        // PARENT VALIDITY CHECK
        // =====================================================

        if (!ParentGuid.IsValid())
        {
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("[HierarchySafety] Invalid parent GUID: "
                     "GUID=%s has invalid parent — detaching"),
                *Guid.ToString(
                    EGuidFormats::Digits));

            DetachFromParent(Guid);
            continue;
        }

        // =====================================================
        // PARENT EXISTS IN CACHE CHECK
        // =====================================================

        AActor* ParentActor =
            FindActorFast(ParentGuid);

        if (!ParentActor)
        {
            // This is normal during deferred attachment —
            // only warn if pending for a long time
            continue;
        }

        AActor* ChildActor =
            FindActorFast(Guid);

        if (!ChildActor)
        {
            continue;
        }

        // =====================================================
        // CIRCULAR CHAIN DETECTION
        // =====================================================
        // Walk up the parent chain and verify we never
        // encounter the child as an ancestor.

        int32 Depth = 0;
        AActor* Probe =
            ParentActor;

        while (Probe && Depth < 128)
        {
            if (Probe == ChildActor)
            {
                FGuid ProbeGuid =
                    FindGuidForActor(Probe);

                UE_LOG(
                    LogLiveSync,
                    Error,
                    TEXT("[HierarchySafety] Circular chain detected: "
                         "GUID=%s -> parent=%s chain contains child"),
                    *Guid.ToString(
                        EGuidFormats::Digits),
                    ProbeGuid.IsValid()
                        ? *ProbeGuid.ToString(
                            EGuidFormats::Digits)
                        : TEXT("unknown"));

                UE_LOG(
                    LogLiveSync,
                    Log,
                    TEXT("[HierarchySafety] Aborting attachment "
                         "for GUID=%s to prevent recursion stall"),
                    *Guid.ToString(
                        EGuidFormats::Digits));

                DetachFromParent(Guid);
                break;
            }

            Probe =
                Probe->
                GetAttachParentActor();
            Depth++;
        }

        if (Depth >= 128)
        {
            UE_LOG(
                LogLiveSync,
                Error,
                TEXT("[HierarchySafety] Excessive hierarchy depth "
                     "(%d levels) for GUID=%s — detaching"),
                Depth,
                *Guid.ToString(
                    EGuidFormats::Digits));

            DetachFromParent(Guid);
        }
    }
}


// =========================================================
// WATCHDOG BACKOFF
// =========================================================

double UUELiveSyncSubsystem::
GetWatchdogBackoff() const
{
    int32 Index =
        FMath::Clamp(
            WatchdogRestartCount,
            0,
            4);

    return
        WatchdogBackoffDelays[Index];
}


#include "UELiveSyncSubsystem_Phase6H.inl"
#include "UELiveSyncSubsystem_Phase6I.inl"
#include "UELiveSyncSubsystem_Diagnostics.inl"

