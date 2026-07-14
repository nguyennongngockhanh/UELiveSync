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

#if WITH_EDITOR
#include "Editor.h"
#include "LevelEditorViewport.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Components/SceneComponent.h"

#include "Engine/Texture2D.h"
#include "Misc/Paths.h"

#include "LevelSequence.h"
#include "MovieScene.h"
#include "Tracks/MovieSceneCameraCutTrack.h"
#include "Sections/MovieSceneCameraCutSection.h"
#include "Tracks/MovieScene3DTransformTrack.h"
#include "Sections/MovieScene3DTransformSection.h"
#include "Tracks/MovieSceneBoolTrack.h"
#include "Sections/MovieSceneBoolSection.h"

// Phase 10K.4: master material creation
#include "Materials/MaterialExpressionTextureSampleParameter2D.h"
#include "Materials/MaterialExpressionVectorParameter.h"
#include "Materials/MaterialExpressionScalarParameter.h"
#include "Materials/MaterialExpressionLinearInterpolate.h"
#include "Materials/MaterialExpressionTransform.h"
#include "Materials/MaterialExpressionConstant3Vector.h"
#include "UObject/SavePackage.h"

#include "AssetRegistry/IAssetRegistry.h"

#endif

#include "FBXImport/LiveSyncFBXImporter.h"

#include "EngineUtils.h"

#include "Common/TcpSocketBuilder.h"

#include "Sockets.h"

#include "SocketSubsystem.h"

#include "Interfaces/IPv4/IPv4Address.h"

#include "HAL/RunnableThread.h"

#include "Misc/Guid.h"

#include "LiveSyncRunnable.h"

#include "Components/StaticMeshComponent.h"
#include "Materials/MaterialInterface.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Materials/MaterialInstanceConstant.h"
#include "ProceduralMeshComponent.h"
#include "KismetProceduralMeshLibrary.h"

#include "AssetToolsModule.h"

#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"

#include <cmath>

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
        0,
        TEXT("Interpolation mode: 0=direct-set (zero lag, default), 1=smooth"),
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

static TAutoConsoleVariable<int32>
    CVarLiveSyncMaxMeshBuildsPerTick(
        TEXT("UE.LiveSync.MaxMeshBuildsPerTick"),
        10,
        TEXT("Max ProceduralMesh section builds per tick (spreads mesh rebuild work across frames, preventing game-thread stalls during snapshot replay)"),
        ECVF_Default
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

// Phase 7D Stage 4: apply active camera to editor viewport.
static TAutoConsoleVariable<int32>
    CVarLiveSyncActiveCameraApplyToViewport(
        TEXT("UE.LiveSync.ActiveCamera.ApplyToViewport"),
        0,
        TEXT("Apply active camera to editor viewport (1=on, 0=off). "
             "Default 0 — storage-only mode. When enabled, resolves the "
             "camera GUID through ActorCache and locks the viewport to "
             "the camera actor via SetActorLock on all level editor viewports."),
        ECVF_Default
    );

// =========================================================
// PHASE 7C STAGE 2C.10 — V1 DEBUG MATERIAL MODES
// =========================================================
// Opt-in diagnostic CVars for runtime shading/culling
// isolation on v1 ProceduralMesh builds. These do NOT
// change the packet format, do NOT modify the legacy V5
// path, and do NOT persist to assets.
// =========================================================

static TAutoConsoleVariable<int32>
    CVarLiveSyncV1DebugMaterialMode(
        TEXT("UE.LiveSync.V1DebugMaterialMode"),
        0,
        TEXT("V1 mesh debug material mode: 0=none (default), 1=unlit gray, 2=two-sided gray, 3=two-sided unlit gray."),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncV1DebugForceFaceNormals(
        TEXT("UE.LiveSync.V1DebugForceFaceNormals"),
        0,
        TEXT("V1 mesh debug: force per-triangle face normals instead of source v1 normals (1=on, 0=off)."),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncV1DebugDisableTangents(
        TEXT("UE.LiveSync.V1DebugDisableTangents"),
        0,
        TEXT("V1 mesh debug: pass empty tangent array to CreateMeshSection (1=on, 0=off)."),
        ECVF_Default
    );

static TAutoConsoleVariable<int32>
    CVarLiveSyncV1DisableTangents(
        TEXT("UE.LiveSync.V1DisableTangents"),
        0,
        TEXT("V1 mesh: skip tangent generation and pass empty tangent array (1=on, 0=off). "
             "Use when generated tangents cause shading artifacts (zero UVs → bad tangents)."),
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

// =============================================================
// STAGE 10A.2 — CameraComponent basis diagnostic helper
// Logs RootComponent / CameraComponent axes, quaternions, and
// deltaQuat = Root⁻¹ * Cam once per camera GUID.
// Called after spawn and after first SetActorTransform.
// =============================================================
static void DiagBasis_CameraOneShot(
    AActor* Actor,
    const FGuid& Guid)
{
    if (!Actor || !Guid.IsValid())
        return;

    ACameraActor* CamActor = Cast<ACameraActor>(Actor);
    if (!CamActor)
        return;

    // One-shot per GUID
    static TSet<FGuid> LoggedGuids;
    if (LoggedGuids.Contains(Guid))
        return;
    LoggedGuids.Add(Guid);

    USceneComponent* RootComp = CamActor->GetRootComponent();
    UCameraComponent* CamComp = CamActor->GetCameraComponent();
    if (!RootComp || !CamComp)
        return;

    const FVector RootFwd  = RootComp->GetForwardVector();
    const FVector RootRgt  = RootComp->GetRightVector();
    const FVector RootUp   = RootComp->GetUpVector();

    const FVector CamFwd   = CamComp->GetForwardVector();
    const FVector CamRgt   = CamComp->GetRightVector();
    const FVector CamUp    = CamComp->GetUpVector();

    const FQuat   RootQuat = RootComp->GetComponentQuat();
    const FQuat   CamQuat  = CamComp->GetComponentQuat();
    const FQuat   DeltaQuat = RootQuat.Inverse() * CamQuat;

    if (GEnableVerboseSyncLogs)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[CAMERA][DIAG_BASIS] guid=%s"
                 " rootQ=(%.4f,%.4f,%.4f,%.4f)"
                 " rootFwd=(%.4f,%.4f,%.4f)"
                 " rootRgt=(%.4f,%.4f,%.4f)"
                 " rootUp=(%.4f,%.4f,%.4f)"
                 " camQ=(%.4f,%.4f,%.4f,%.4f)"
                 " camFwd=(%.4f,%.4f,%.4f)"
                 " camRgt=(%.4f,%.4f,%.4f)"
                 " camUp=(%.4f,%.4f,%.4f)"
                 " deltaQ=(%.4f,%.4f,%.4f,%.4f)"),
            *Guid.ToString(EGuidFormats::Digits),
            RootQuat.X, RootQuat.Y, RootQuat.Z, RootQuat.W,
            RootFwd.X, RootFwd.Y, RootFwd.Z,
            RootRgt.X, RootRgt.Y, RootRgt.Z,
            RootUp.X,  RootUp.Y,  RootUp.Z,
            CamQuat.X, CamQuat.Y, CamQuat.Z, CamQuat.W,
            CamFwd.X,  CamFwd.Y,  CamFwd.Z,
            CamRgt.X,  CamRgt.Y,  CamRgt.Z,
            CamUp.X,   CamUp.Y,   CamUp.Z,
            DeltaQuat.X, DeltaQuat.Y, DeltaQuat.Z, DeltaQuat.W);
    }
}

// =========================================================
// STAGE 10B.1 — ASSET-BACKED LEVELSEQUENCE HELPER
// =========================================================
// Returns the ULevelSequence at
// /Game/UELiveSync/Sequences/LS_UELiveSync_Runtime,
// creating it if missing.  Uses real package (not
// GetTransientPackage) so the sequence survives across
// editor sessions and can be inspected via UE Python.
// =========================================================
#if WITH_EDITOR
static ULevelSequence* GetOrCreateLiveSyncLevelSequenceAsset()
{
    static const FString AssetPath
        = TEXT("/Game/UELiveSync/Sequences/LS_UELiveSync_Runtime");
    static const FName AssetObjName = FName(TEXT("LS_UELiveSync_Runtime"));
    static const FString AssetFullPath
        = AssetPath + TEXT(".") + AssetObjName.ToString();

    // Attempt to load existing asset via SoftObjectPath.
    // Use full path (package.object) so FSoftObjectPath resolves
    // directly to the LevelSequence, not the UPackage.
    static const FSoftObjectPath SeqSoftPath(*AssetFullPath);
    ULevelSequence* Existing = Cast<ULevelSequence>(SeqSoftPath.TryLoad());
    if (Existing)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[SEQ][ASSET_LOAD] Loaded existing sequence: %s"),
            *AssetPath);
        return Existing;
    }

    // Asset missing — create it
    const FString Dir = TEXT("/Game/UELiveSync/Sequences");
    IFileManager::Get().MakeDirectory(*Dir, true);

    UPackage* Package = CreatePackage(*AssetPath);
    if (!Package)
    {
        UE_LOG(LogLiveSync, Error,
            TEXT("[SEQ][ASSET_FAIL] Failed to create package for: %s"),
            *AssetPath);
        return nullptr;
    }

    // Use a fixed object name matching the package short name so
    // FSoftObjectPath and unreal.load_asset() can resolve cleanly.
    ULevelSequence* Seq = NewObject<ULevelSequence>(
        Package, AssetObjName, RF_Public | RF_Standalone | RF_MarkAsRootSet);
    if (!Seq)
    {
        UE_LOG(LogLiveSync, Error,
            TEXT("[SEQ][ASSET_FAIL] Failed to create ULevelSequence: %s"),
            *AssetPath);
        return nullptr;
    }

    Seq->Initialize();

    // Ensure package is dirty so it can be saved
    Package->MarkPackageDirty();

    // Save the asset to disk so it persists and is loadable
    // via unreal.load_asset() in UE Python.
    {
        FString FilePath = FPackageName::LongPackageNameToFilename(
            AssetPath, FPackageName::GetAssetPackageExtension());
        if (!FilePath.IsEmpty())
        {
            FSavePackageArgs SaveArgs;
            SaveArgs.TopLevelFlags = RF_Standalone;
            SaveArgs.SaveFlags = SAVE_NoError;
            UPackage::SavePackage(Package, nullptr, *FilePath, SaveArgs);
        }
    }

    UE_LOG(LogLiveSync, Log,
        TEXT("[SEQ][ASSET_CREATE] Created new sequence asset: %s"),
        *AssetPath);
    UE_LOG(LogLiveSync, Log,
        TEXT("[SEQ][ASSET_READY] Sequence asset ready: %s (package: %s)"),
        *AssetPath, *Package->GetName());

    return Seq;
}

// =========================================================
// STAGE 10C.1 — SAVE LIVE-SYNC LEVELSEQUENCE HELPER
// =========================================================
// Saves the asset-backed LevelSequence package to disk
// after runtime modifications (bindings, keyframes).
// Called from HandleKeyframe after successful keyframe apply.
// =========================================================
static void SaveLiveSyncLevelSequenceAsset(ULevelSequence* Seq)
{
    if (!Seq)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[SEQ][ASSET_SAVE_SKIP] Seq is null — nothing to save"));
        return;
    }

    UPackage* Package = Cast<UPackage>(Seq->GetOutermost());
    if (!Package)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[SEQ][ASSET_SAVE_FAIL] GetOutermost() returned null"));
        return;
    }

    // Mark dirty so the save picks up all changes
    Package->MarkPackageDirty();
    UE_LOG(LogLiveSync, Log,
        TEXT("[SEQ][ASSET_DIRTY] Package marked dirty: %s"),
        *Package->GetName());

    // Convert long package name to file path
    FString FilePath = FPackageName::LongPackageNameToFilename(
        Package->GetName(), FPackageName::GetAssetPackageExtension());
    if (FilePath.IsEmpty())
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[SEQ][ASSET_SAVE_FAIL] LongPackageNameToFilename failed for %s"),
            *Package->GetName());
        return;
    }

    FSavePackageArgs SaveArgs;
    SaveArgs.TopLevelFlags = RF_Standalone;
    SaveArgs.SaveFlags = SAVE_NoError;
    UPackage::SavePackage(Package, nullptr, *FilePath, SaveArgs);

    UE_LOG(LogLiveSync, Log,
        TEXT("[SEQ][ASSET_SAVE] Saved sequence: %s"),
        *Package->GetName());
}
#endif // WITH_EDITOR

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

    OnBeginFrameHandle =
        FCoreDelegates::OnBeginFrame.
        AddUObject(
            this,
            &UUELiveSyncSubsystem::OnEngineTick);

    LastTickRealTime =
        FPlatformTime::Seconds();

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
    // Remove OnBeginFrame first to prevent any re-entrant
    // Tick() call during teardown
    FCoreDelegates::OnBeginFrame.
        Remove(
            OnBeginFrameHandle);

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
// ON ENGINE TICK (OnBeginFrame wrapper)
// =========================================================

void UUELiveSyncSubsystem::OnEngineTick()
{
    double Now = FPlatformTime::Seconds();
    if (ShouldLogVerbose())
    {
        static double LastAlwaysOnLog = 0.0;
        if (Now - LastAlwaysOnLog >= 5.0)
        {
            LastAlwaysOnLog = Now;
            UE_LOG(
                LogLiveSync,
                Log,
                TEXT("[TICK][ALIVE] OnEngineTick executing "
                     "(vfc=%lld gfc=%lld realtime=%.3f)"),
                (long long)VerboseFrameCounter,
                (long long)GFrameCounter,
                Now);
        }
    }

    float DeltaTime = static_cast<float>(Now - LastTickRealTime);
    LastTickRealTime = Now;
    Tick(DeltaTime);
}


// =========================================================
// TICK
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

    if (bEnableVerboseSyncLogs && VerboseFrameCounter % 100 == 1)
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
    if (ShouldLogVerbose())
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("[TICK][HEARTBEAT] Tick is executing "
                 "(frame=%d)"),
            VerboseFrameCounter);
    }

    // =====================================================
    // Phase 10A.3: tick proof-of-life (every 10s)
    // =====================================================
    if (ShouldLogVerbose())
    {
        static double LastTickProofLogTime = 0.0;
        double NowTickProof = FPlatformTime::Seconds();
        if (NowTickProof - LastTickProofLogTime >= 10.0)
        {
            LastTickProofLogTime = NowTickProof;
            UE_LOG(
                LogLiveSync,
                Log,
                TEXT("[TICK] frame=%lld delta=%.4f queue=%d"),
                (long long)VerboseFrameCounter,
                DeltaTime,
                PacketQueue.Size());
        }
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
        if (ShouldLogVerbose())
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
                                 "from %s:%d conn=%d"),
                            *RemoteAddr->
                                ToString(false),
                            RemoteAddr->
                                GetPort(),
                            ConnectionGeneration + 1);
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

                    UE_LOG(LogLiveSync, Log,
                        TEXT("[TRANSPORT_ACCEPT_OK] generation=%d"),
                        ConnectionGeneration + 1);

                    StartNetworkThread();
                }
                else
                {
                    UE_LOG(LogLiveSync, Warning,
                        TEXT("[TRANSPORT_ACCEPT_FAIL] state=%d"),
                        static_cast<int32>(
                            NewSocket->
                                GetConnectionState()));

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
            TEXT("Stale connection, draining queue before cleanup"));

        ProcessQueuedPackets();

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
            TEXT("Detected thread exit, draining queue before cleanup"));

        ProcessQueuedPackets();

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
            TEXT("[HEARTBEAT_TIMEOUT] secondsSince=%.2f timeout=%.2f"),
            FPlatformTime::Seconds() - LastHeartbeatTime,
            HeartbeatTimeoutVal);

        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("Heartbeat timeout: draining queue before cleanup"));

        ProcessQueuedPackets();

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

    // Phase 10A.3: auto-reconnect when NetworkThread has exited
    // but ListenerSocket is still alive.  Blender must initiate
    // a fresh TCP connection; ListenerSocket will accept it.
    if (ListenerSocket &&
        !ConnectionSocket &&
        !NetworkRunnable)
    {
        if (ShouldLogVerbose())
        {
            // Throttle: log at most once every 2s to avoid flooding
            static double LastReconnectLog = 0.0;
            double NowLog = FPlatformTime::Seconds();
            if (NowLog - LastReconnectLog >= 2.0)
            {
                LastReconnectLog = NowLog;
                UE_LOG(
                    LogLiveSync,
                    Log,
                    TEXT("[RECONNECT] NetworkThread dead, "
                         "ListenerSocket waiting for peer"));
            }
        }

        StartNetworkThread();
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

        if (bEnableVerboseSyncLogs)
        {
            UE_LOG(LogLiveSync, Log, TEXT("BEGIN Pipeline: ProcessQueuedPackets"));
        }
        {
            TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessQueuedPackets);
            ProcessQueuedPackets();
        }
        if (bEnableVerboseSyncLogs)
        {
            UE_LOG(LogLiveSync, Log, TEXT("END   Pipeline: ProcessQueuedPackets"));
        }

        EvictStaleTransformStates();

        if (!CVarLiveSyncDisableInterpolation.GetValueOnGameThread())
        {
            if (bEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Log, TEXT("BEGIN Pipeline: InterpolateTransforms"));
            }
            {
                TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_InterpolateTransforms);
                InterpolateTransforms(DeltaTime);
            }
            if (bEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Log, TEXT("END   Pipeline: InterpolateTransforms"));
            }
        }
        else if (bEnableVerboseSyncLogs)
        {
            UE_LOG(LogLiveSync, Log, TEXT("SKIP  Pipeline: InterpolateTransforms (disabled by CVar)"));
        }

        if (!CVarLiveSyncDisableAttachmentResolution.GetValueOnGameThread())
        {
            if (bEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Log, TEXT("BEGIN Pipeline: ResolvePendingAttachments"));
            }
            {
                TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ResolvePendingAttachments);
                ResolvePendingAttachments();
            }
            if (bEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Log, TEXT("END   Pipeline: ResolvePendingAttachments"));
            }
        }
        else if (bEnableVerboseSyncLogs)
        {
            UE_LOG(LogLiveSync, Log, TEXT("SKIP  Pipeline: ResolvePendingAttachments (disabled by CVar)"));
        }

        // =================================================
        // SEMANTIC HIERARCHY DEFERRED RESOLUTION (Phase 6D)
        // Runs AFTER runtime ResolvePendingAttachments so the
        // runtime graph is settled before semantic attachements
        // are applied (FINDING-009).
        // =================================================

        if (bEnableVerboseSyncLogs)
        {
            UE_LOG(LogLiveSync, Log, TEXT("BEGIN Pipeline: ResolveHierarchyAttachments"));
        }
        {
            TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ResolveHierarchyAttachments);
            ResolveHierarchyAttachments();
        }
        if (bEnableVerboseSyncLogs)
        {
            UE_LOG(LogLiveSync, Log, TEXT("END   Pipeline: ResolveHierarchyAttachments"));
        }

        if (!CVarLiveSyncDisableRecovery.GetValueOnGameThread())
        {
            if (bEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Log, TEXT("BEGIN Pipeline: RecoverMissingActors"));
            }
            {
                TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_RecoverMissingActors);
                RecoverMissingActors();
            }
            if (bEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Log, TEXT("END   Pipeline: RecoverMissingActors"));
            }
        }
        else if (bEnableVerboseSyncLogs)
        {
            UE_LOG(LogLiveSync, Log, TEXT("SKIP  Pipeline: RecoverMissingActors (disabled by CVar)"));
        }

        if (!CVarLiveSyncDisableAssetResolution.GetValueOnGameThread())
        {
            if (bEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Log, TEXT("BEGIN Pipeline: ResolvePendingAssets"));
            }
            {
                TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ResolvePendingAssets);
                ResolvePendingAssets();
            }
            if (bEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Log, TEXT("END   Pipeline: ResolvePendingAssets"));
            }

            if (bEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Log, TEXT("BEGIN Pipeline: ResolvePendingMaterials"));
            }
            {
                TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ResolvePendingMaterials);
                ResolvePendingMaterials();
            }
            if (bEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Log, TEXT("END   Pipeline: ResolvePendingMaterials"));
            }

            if (bEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Log, TEXT("BEGIN Pipeline: ReconstructCompletedMeshes"));
            }
            {
                TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ReconstructCompletedMeshes);
                ReconstructCompletedMeshes();
            }
            if (bEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Log, TEXT("END   Pipeline: ReconstructCompletedMeshes"));
            }
        }
        else if (bEnableVerboseSyncLogs)
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
        if (ShouldLogVerbose())
        {
            UE_LOG(LogLiveSync, Log, TEXT("BEGIN Periodic: ValidateHierarchy"));
        }
        {
            TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ValidateHierarchy);
            ValidateHierarchy();
        }
        if (ShouldLogVerbose())
        {
            UE_LOG(LogLiveSync, Log, TEXT("END   Periodic: ValidateHierarchy"));
        }
    }

    // =====================================================
    // PHASE 6H — SEMANTIC CONSISTENCY DIAGNOSTICS
    // =====================================================

    if (bEnableVerboseSyncLogs)
    {
        UE_LOG(LogLiveSync, Log, TEXT("BEGIN Periodic: TickPhase6H"));
        {
            TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_TickPhase6H);
            TickPhase6H(DeltaTime);
        }
        UE_LOG(LogLiveSync, Log, TEXT("END   Periodic: TickPhase6H"));
    }

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

    if (bEnableVerboseSyncLogs)
    {
        UE_LOG(LogLiveSync, Log, TEXT("BEGIN TickMetrics"));
        TickMetrics(DeltaTime);
        UE_LOG(LogLiveSync, Log, TEXT("END   TickMetrics"));
    }

    // =====================================================
    // SAFETY MONITORS (flood detection, queue pressure)
    // =====================================================

    if (bEnableVerboseSyncLogs)
    {
        UE_LOG(LogLiveSync, Log, TEXT("BEGIN TickSafetyMonitors"));
        TickSafetyMonitors(DeltaTime);
        UE_LOG(LogLiveSync, Log, TEXT("END   TickSafetyMonitors"));
    }

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

    // =====================================================
    // Phase 10J.5D.5: Deferred FBX repair (post-import passes)
    // =====================================================

    ProcessDeferredRepairs();

    if (bEnableVerboseSyncLogs)
    {
        UE_LOG(LogLiveSync, Log, TEXT("END TRACE: Tick complete frame=%d"), VerboseFrameCounter);
    }
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
    // GUARD: no socket (Phase 10A.3: accept from ListenerSocket)
    // =====================================================

    if (!ConnectionSocket && !ListenerSocket)
    {
        bNetworkThreadStarting = false;
        UE_LOG(
            LogLiveSync,
            Warning,
            TEXT("StartNetworkThread: no socket"));

        return;
    }

    // Phase 10A.3: if no active ConnectionSocket, accept from
    // ListenerSocket so the thread has a socket to read from.
    if (!ConnectionSocket && ListenerSocket)
    {
        if (ShouldLogVerbose())
        {
            UE_LOG(
                LogLiveSync,
                Log,
                TEXT("StartNetworkThread: accepting from ListenerSocket"));
        }

        // Non-blocking accept — if no connection is pending, return
        // immediately. The caller (auto-reconnect path in Tick) will
        // retry on the next frame, keeping the game thread responsive.
        bool bPending = false;
        if (ListenerSocket->HasPendingConnection(bPending) && bPending)
        {
            ConnectionSocket = ListenerSocket->Accept(TEXT("LiveSyncConnection"));
            if (ConnectionSocket)
            {
                ConnectionSocket->SetNonBlocking(true);
            }
            else
            {
                UE_LOG(
                    LogLiveSync,
                    Warning,
                    TEXT("StartNetworkThread: accept failed"));
                bNetworkThreadStarting = false;
                return;
            }
        }
        else
        {
            UE_LOG(
                LogLiveSync,
                Verbose,
                TEXT("StartNetworkThread: no pending connection, deferring"));
            bNetworkThreadStarting = false;
            return;
        }
    }

    // =====================================================
    // GUARD: prevent double-start
    // =====================================================

    if (NetworkThread ||
        NetworkRunnable)
    {
        if (ShouldLogVerbose())
        {
            UE_LOG(
                LogLiveSync,
                Log,
                TEXT("StartNetworkThread: already running, "
                     "stopping old thread"));
        }

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

    ConnectionGeneration++;

    NetworkRunnable =
        new FLiveSyncRunnable(

            ConnectionSocket,

            &PacketQueue
        );

    NetworkRunnable->ConnectionGeneration =
        ConnectionGeneration;

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
    UE_LOG(LogLiveSync, Log,
        TEXT("[TRANSPORT_DISCONNECT] conn=%d"),
        ConnectionGeneration);

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

    // Phase 9: reset capability state on disconnect
    RemoteCapabilities = 0;
    bCapabilityResponseSent = false;

    // Phase 7E/10A: sequencer op state is per remote session. Reset on
    // disconnect so reconnects may restart SEQOP numbering from 1.
    bHasSequencerOpState = false;
    LastSequencerOpSequence = 0;
    bHasLiveSyncSequence = false;
    LiveSyncSequence = nullptr;
    LiveSyncGuidToSequencerBinding.Empty();
    PendingSequencerBindings.Empty();

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

    if (ShouldLogVerbose())
    {
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

        // [DIAG][PACKET_DISPATCH] log packet type and sequence
        if (PktSize >= 24)
        {
            uint64 _pktSeq = 0;
            FMemory::Memcpy(&_pktSeq, Pkt.RawData.GetData() + 8, sizeof(uint64));
            UE_LOG(LogLiveSync, Log,
                TEXT("[PACKET_DISPATCH] type=0x%02x seq=%llu size=%d"),
                PktType, _pktSeq, PktSize);
        }

        uint64 PktBeginCycles =
            FPlatformTime::Cycles64();

        if (ShouldLogVerbose())
        {
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
        }

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
        else if (ShouldLogVerbose())
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
            { 0x01, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1A, 0x1B };

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

    // =====================================================
    // CAPABILITY ANNOUNCE (Phase 9 — PT_CapabilityAnnounce 0x11)
    // =====================================================
    // Payload: FCapabilityAnnouncePayload (4 bytes fixed)
    //   CapabilityMask(4) uint32 LE — Blender's supported capabilities
    //
    // UE stores the mask in RemoteCapabilities for feature gating
    // and increments the diagnostic counter.
    // =====================================================

    if (PacketType == 0x11)
    {
        TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessCapabilityAnnounce);

        Stats.CapabilityAnnounceReceived.fetch_add(1, std::memory_order_relaxed);

        int32 ObjSize = static_cast<int32>(PacketEnd - Ptr);
        if (ObjSize < sizeof(FCapabilityAnnouncePayload))
        {
            Stats.CapabilityPacketsMalformed.fetch_add(1, std::memory_order_relaxed);
            Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[CAP] Malformed announce: size %d < %d"),
                ObjSize, sizeof(FCapabilityAnnouncePayload));
            Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
            return;
        }

        FCapabilityAnnouncePayload Payload;
        Payload.CapabilityMask = *reinterpret_cast<const uint32*>(Ptr);

        RemoteCapabilities = Payload.CapabilityMask;
        bCapabilityResponseSent = true;

        UE_LOG(LogLiveSync, Verbose,
            TEXT("[CAP] Announce received: mask=0x%08X"),
            Payload.CapabilityMask);

        Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // CAPABILITY RESPONSE (Phase 9 — PT_CapabilityResponse 0x12)
    // =====================================================
    // Payload: FCapabilityResponsePayload (4 bytes fixed)
    //   CapabilityMask(4) uint32 LE — UE's supported capabilities
    //
    // This packet is received when Blender echoes back a response
    // from a previous connection. Increment counter and discard.
    // =====================================================

    if (PacketType == 0x12)
    {
        TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessCapabilityResponse);

        Stats.CapabilityResponseReceived.fetch_add(1, std::memory_order_relaxed);

        int32 ObjSize = static_cast<int32>(PacketEnd - Ptr);
        if (ObjSize < sizeof(FCapabilityResponsePayload))
        {
            Stats.CapabilityPacketsMalformed.fetch_add(1, std::memory_order_relaxed);
            Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[CAP] Malformed response: size %d < %d"),
                ObjSize, sizeof(FCapabilityResponsePayload));
        }

        Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
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

        // Phase 6F: Collection/Group replication
        for (uint32 i = 0; i < ObjectCount; i++)
        {
            if (Ptr + 30 > PacketEnd)
            {
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[COLLECTION] Truncated packet at object %u/%u"),
                    i, ObjectCount);
                return;
            }

            // Determine payload size for this object
            uint8 ObjOpType = Ptr[16];
            int32 ObjSize = (ObjOpType >= 0x01 && ObjOpType <= 0x04)
                ? LIVE_SYNC_COLLECTION_MEMBERSHIP_SIZE
                : LIVE_SYNC_COLLECTION_BASE_SIZE;

            if (Ptr + ObjSize > PacketEnd)
            {
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[COLLECTION] Truncated packet (size=%d) at object %u/%u"),
                    ObjSize, i, ObjectCount);
                return;
            }

            FGuid Guid;
            FMemory::Memcpy(&Guid, Ptr, sizeof(FGuid));
            Ptr += sizeof(FGuid);

            uint8 OpType = *Ptr;
            Ptr += sizeof(uint8);

            uint8 OpFlags = *Ptr;
            Ptr += sizeof(uint8);

            uint32 SeqNum;
            FMemory::Memcpy(&SeqNum, Ptr, sizeof(uint32));
            Ptr += sizeof(uint32);

            double Timestamp;
            FMemory::Memcpy(&Timestamp, Ptr, sizeof(double));
            Ptr += sizeof(double);

            FGuid CollectionGuid;
            const bool bIsMembershipOp = (OpType >= 0x01 && OpType <= 0x04);
            if (bIsMembershipOp)
            {
                FMemory::Memcpy(&CollectionGuid, Ptr, sizeof(FGuid));
                Ptr += sizeof(FGuid);
            }

            HandleCollection(Guid, OpType, OpFlags, SeqNum, Timestamp,
                             bIsMembershipOp ? &CollectionGuid : nullptr);
        }

        Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // PLAYBACK STATE (Phase 7C — PT_PlaybackState 0x14)
    // =====================================================
    // Payload: FPlaybackStatePayload (14 bytes fixed)
    //   State(1) + bLoopEnabled(1) + Sequence(4) + Timestamp(8)
    //
    // Validation:
    //   - Payload size must be exactly 14 bytes
    //   - State must be 0 (PLAY), 1 (PAUSE), or 2 (STOP)
    //   - Sequence must be strictly greater than LastPlaybackSequence
    //     (first packet with any sequence is accepted)
    // =====================================================

    if (PacketType == 0x14)
    {
        TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessPlaybackState);

        Stats.PlaybackPacketsReceived.fetch_add(1, std::memory_order_relaxed);

        int32 ObjSize = static_cast<int32>(PacketEnd - Ptr);
        if (ObjSize < sizeof(FPlaybackStatePayload))
        {
            Stats.PlaybackPacketsMalformed.fetch_add(1, std::memory_order_relaxed);
            Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[PLAYBACK] Malformed packet: size %d < %d"),
                ObjSize, sizeof(FPlaybackStatePayload));
            Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
            return;
        }

        FPlaybackStatePayload Payload;
        Payload.State        = Ptr[0];
        Payload.bLoopEnabled = Ptr[1];
        Payload.Sequence     = *reinterpret_cast<const uint32*>(Ptr + 2);
        Payload.Timestamp    = *reinterpret_cast<const double*>(Ptr + 6);

        if (Payload.State > 2)
        {
            Stats.PlaybackPacketsMalformed.fetch_add(1, std::memory_order_relaxed);
            Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[PLAYBACK] Malformed packet: invalid state %d"),
                Payload.State);
            Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
            return;
        }

        if (bHasPlaybackState && Payload.Sequence <= LastPlaybackSequence)
        {
            Stats.PlaybackPacketsStale.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[PLAYBACK] Stale packet: seq %u <= %u"),
                Payload.Sequence, LastPlaybackSequence);
            Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
            return;
        }

        HandlePlaybackState(Payload);
        Stats.PlaybackPacketsApplied.fetch_add(1, std::memory_order_relaxed);
        Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // TIMELINE PACKET (Phase 7B)
    // =====================================================
    // Wire format (36 bytes fixed):
    //   FrameCurrent(4) + FrameStart(4) + FrameEnd(4) + FPSNum(4) + FPSDen(4)
    //   + Sequence(4) + Reserved(4) + Timestamp(8)
    //
    // Validation:
    //   - Payload size must be exactly 36 bytes
    //   - Sequence must be strictly greater than LastTimelineSequence
    //     (first packet with any sequence is accepted)
    //   - No editor/Sequencer/playback control — storage only
    // =====================================================

    if (PacketType == 0x13)
    {
        TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessTimeline);

        Stats.TimelinePacketsReceived.fetch_add(1, std::memory_order_relaxed);

        int32 ObjSize = static_cast<int32>(PacketEnd - Ptr);
        if (ObjSize < sizeof(FTimelinePayload))
        {
            Stats.TimelinePacketsMalformed.fetch_add(1, std::memory_order_relaxed);
            Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[TIMELINE] Malformed packet: size %d < %d"),
                ObjSize, sizeof(FTimelinePayload));
            Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
            return;
        }

        FTimelinePayload Payload;
        Payload.FrameCurrent = *reinterpret_cast<const int32*>(Ptr + 0);
        Payload.FrameStart   = *reinterpret_cast<const int32*>(Ptr + 4);
        Payload.FrameEnd     = *reinterpret_cast<const int32*>(Ptr + 8);
        Payload.FPSNum       = *reinterpret_cast<const int32*>(Ptr + 12);
        Payload.FPSDen       = *reinterpret_cast<const int32*>(Ptr + 16);
        Payload.Sequence     = *reinterpret_cast<const uint32*>(Ptr + 20);
        Payload.Reserved     = *reinterpret_cast<const int32*>(Ptr + 24);
        Payload.Timestamp    = *reinterpret_cast<const double*>(Ptr + 28);

        if (bHasTimelineState && Payload.Sequence <= LastTimelineSequence)
        {
            Stats.TimelinePacketsStale.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[TIMELINE] Stale packet: seq %u <= %u"),
                Payload.Sequence, LastTimelineSequence);
            Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
            return;
        }

        HandleTimeline(Payload);
        Stats.TimelinePacketsApplied.fetch_add(1, std::memory_order_relaxed);
        Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // TIMELINE STATE PACKET (Phase 7F Stage 1)
    // =====================================================
    // Wire format (20 bytes fixed):
    //   FrameStart(4) + FrameEnd(4) + FrameCurrent(4) + FPSNum(4) + FPSDen(4)
    //
    // Unlike PT_Timeline (0x13), this packet applies the frame range
    // directly to the LiveSync LevelSequence playback range.
    // =====================================================

    if (PacketType == 0x19)
    {
        TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessTimelineState);

        int32 ObjSize = static_cast<int32>(PacketEnd - Ptr);
        if (ObjSize < sizeof(FTimelineStatePayload))
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[TIMELINE][MALFORMED] size %d < %d"),
                ObjSize, sizeof(FTimelineStatePayload));
            Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
            Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
            return;
        }

        FTimelineStatePayload Payload;
        Payload.FrameStart   = *reinterpret_cast<const int32*>(Ptr + 0);
        Payload.FrameEnd     = *reinterpret_cast<const int32*>(Ptr + 4);
        Payload.FrameCurrent = *reinterpret_cast<const int32*>(Ptr + 8);
        Payload.FPSNum       = *reinterpret_cast<const int32*>(Ptr + 12);
        Payload.FPSDen       = *reinterpret_cast<const int32*>(Ptr + 16);

        UE_LOG(LogLiveSync, Log,
            TEXT("[TIMELINE][RECV] frame_start=%d frame_end=%d frame_current=%d fps=%d/%d"),
            Payload.FrameStart, Payload.FrameEnd, Payload.FrameCurrent,
            Payload.FPSNum, Payload.FPSDen);

        HandleTimelineState(Payload);
        Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // PLAYBACK TRANSPORT PACKET (Phase 7F Stage 2)
    // =====================================================
    // Wire format (6 bytes fixed):
    //   [0]    command       uint8  — 0=SetFrame, 1=Play, 2=Pause, 3=Stop
    //   [1-4]  frame_current int32  — current playhead position
    //   [5]    flags         uint8  — bit 0 = loop enabled (reserved)
    //
    // Validation:
    //   - Payload size must be exactly 6 bytes
    //   - Command must be in range [0, 3]
    //   - Malformed payload must log and skip safely

    if (PacketType == PT_PlaybackTransport)
    {
        TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessPlaybackTransport);

        int32 ObjSize = static_cast<int32>(PacketEnd - Ptr);
        if (ObjSize != 6)
        {
            Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[PLAYBACK][MALFORMED] Expected 6 bytes, got %d"),
                ObjSize);
            Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
            return;
        }

        FPlaybackTransportPayload Payload;
        Payload.Command      = *reinterpret_cast<const uint8*>(Ptr + 0);
        Payload.FrameCurrent = *reinterpret_cast<const int32*>(Ptr + 1);
        Payload.Flags        = *reinterpret_cast<const uint8*>(Ptr + 5);

        if (Payload.Command > 3)
        {
            Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[PLAYBACK][MALFORMED] Unknown command %d"),
                Payload.Command);
            Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
            return;
        }

        UE_LOG(LogLiveSync, Log,
            TEXT("[PLAYBACK][RECV] command=%d frame=%d flags=%d"),
            Payload.Command, Payload.FrameCurrent, Payload.Flags);

        HandlePlaybackTransport(Payload);
        Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // CAMERA DEFINITION PACKET (Phase 7G Stage 3)
    // =====================================================
    // Wire format (44 bytes fixed):
    //   CameraGUID(16) + FocalLengthMM(4) + SensorWidthMM(4) + SensorHeightMM(4)
    //   + ClipStart(4) + ClipEnd(4) + OrthoScale(4) + CameraFlags(1) + Reserved(3)
    //
    // Applies focal length, sensor size, clip planes, and orthographic settings
    // to the ACameraActor associated with the camera GUID.
    // =====================================================

    if (PacketType == 0x1B)
    {
        TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessCameraDef);

        Stats.CameraDefPacketsReceived.fetch_add(1, std::memory_order_relaxed);

        int32 ObjSize = static_cast<int32>(PacketEnd - Ptr);
        if (ObjSize < sizeof(FCameraDefPayload))
        {
            Stats.CameraDefPacketsMalformed.fetch_add(1, std::memory_order_relaxed);
            Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[CAMERA][MALFORMED] CameraDef payload size %d < %d"),
                ObjSize, sizeof(FCameraDefPayload));
            Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
            return;
        }

        FCameraDefPayload Payload;
        Payload.CameraGUID     = *reinterpret_cast<const FGuid*>(Ptr + 0);
        Payload.FocalLengthMM  = *reinterpret_cast<const float*>(Ptr + 16);
        Payload.SensorWidthMM  = *reinterpret_cast<const float*>(Ptr + 20);
        Payload.SensorHeightMM = *reinterpret_cast<const float*>(Ptr + 24);
        Payload.ClipStart      = *reinterpret_cast<const float*>(Ptr + 28);
        Payload.ClipEnd        = *reinterpret_cast<const float*>(Ptr + 32);
        Payload.OrthoScale     = *reinterpret_cast<const float*>(Ptr + 36);
        Payload.CameraFlags    = *reinterpret_cast<const uint8*>(Ptr + 40);

        HandleCameraDef(Payload);
        Stats.CameraDefPacketsApplied.fetch_add(1, std::memory_order_relaxed);
        Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // ACTIVE CAMERA PACKET (Phase 7D)
    // =====================================================
    // Wire format (28 bytes fixed):
    //   CameraGUID(16) + Sequence(4) + Timestamp(8)
    //
    // Validation:
    //   - Payload size must be exactly 28 bytes
    //   - Sequence must be strictly greater than LastActiveCameraSequence
    //     (first packet with any sequence is accepted)
    //   - All-zero GUID is a valid null-camera signal
    //   - No viewport SetViewTarget is called — storage only
    // =====================================================

    if (PacketType == 0x15)
    {
        TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessActiveCamera);

        Stats.ActiveCameraPacketsReceived.fetch_add(1, std::memory_order_relaxed);

        int32 ObjSize = static_cast<int32>(PacketEnd - Ptr);
        if (ObjSize < sizeof(FActiveCameraPayload))
        {
            Stats.ActiveCameraPacketsMalformed.fetch_add(1, std::memory_order_relaxed);
            Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[CAMERA] Malformed packet: size %d < %d"),
                ObjSize, sizeof(FActiveCameraPayload));
            Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
            return;
        }

        FActiveCameraPayload Payload;
        Payload.CameraGUID = *reinterpret_cast<const FGuid*>(Ptr + 0);
        Payload.Sequence   = *reinterpret_cast<const uint32*>(Ptr + 16);
        Payload.Timestamp  = *reinterpret_cast<const double*>(Ptr + 20);

        if (bHasEverReceivedActiveCamera && Payload.Sequence <= LastActiveCameraSequence)
        {
            Stats.ActiveCameraPacketsStale.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Log,
                TEXT("[CAMERA] Stale packet: seq %u <= %u"),
                Payload.Sequence, LastActiveCameraSequence);
            Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
            return;
        }

        HandleActiveCamera(Payload);
        Stats.ActiveCameraPacketsApplied.fetch_add(1, std::memory_order_relaxed);
        Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // SEQUENCER OP (Phase 7E — PT_SequencerOp 0x18)
    // =====================================================
    // Fixed-size common header (16 bytes) + optional opcode payload.
    //
    // Validation:
    //   - Total packet size >= sizeof(FSequencerOpHeader) (16 bytes)
    //   - Opcode must be within [SEQUENCER_OP_MIN_OPCODE, SEQUENCER_OP_MAX_OPCODE]
    //   - Remaining bytes must match expected payload size for opcode
    //   - Sequence must be strictly greater than LastSequencerOpSequence
    //     (first packet with any sequence is accepted)
    //
    // Applies CREATE_SEQUENCE, SET_FRAME_RANGE, and CLEAR_SEQUENCE
    // to the subsystem-owned transient ULevelSequence.
    // ADD_POSSESSABLE, REMOVE_POSSESSABLE, ADD_CAMERA_CUT deferred.
    // =====================================================

    if (PacketType == 0x18)
    {
        TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessSequencerOp);

        Stats.SequencerOpPacketsReceived.fetch_add(1, std::memory_order_relaxed);

        int32 ObjSize = static_cast<int32>(PacketEnd - Ptr);
        if (ObjSize < static_cast<int32>(sizeof(FSequencerOpHeader)))
        {
            Stats.SequencerOpPacketsMalformed.fetch_add(1, std::memory_order_relaxed);
            Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[SEQOP] Truncated header: size %d < %d"),
                ObjSize, sizeof(FSequencerOpHeader));
            Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
            return;
        }

        // Parse common header
        FSequencerOpHeader Header;
        FMemory::Memcpy(&Header, Ptr, sizeof(FSequencerOpHeader));

        // Validate opcode range
        if (Header.Opcode < SEQUENCER_OP_MIN_OPCODE ||
            Header.Opcode > SEQUENCER_OP_MAX_OPCODE)
        {
            Stats.SequencerOpPacketsMalformed.fetch_add(1, std::memory_order_relaxed);
            Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[SEQOP] Unknown opcode 0x%02X"),
                Header.Opcode);
            Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
            return;
        }

        // Validate payload size matches expected for opcode
        int32 HeaderSize = sizeof(FSequencerOpHeader);
        int32 PayloadBytes = ObjSize - HeaderSize;
        int32 ExpectedPayload = 0;

        switch (Header.Opcode)
        {
        case SEQUENCER_OP_CREATE_SEQUENCE:
            ExpectedPayload = SEQUENCER_OP_CREATE_SEQUENCE_PAYLOAD_SIZE;
            break;
        case SEQUENCER_OP_ADD_POSSESSABLE:
            ExpectedPayload = SEQUENCER_OP_ADD_POSSESSABLE_PAYLOAD_SIZE;
            break;
        case SEQUENCER_OP_REMOVE_POSSESSABLE:
            ExpectedPayload = SEQUENCER_OP_REMOVE_POSSESSABLE_PAYLOAD_SIZE;
            break;
        case SEQUENCER_OP_ADD_CAMERA_CUT:
            ExpectedPayload = SEQUENCER_OP_ADD_CAMERA_CUT_PAYLOAD_SIZE;
            break;
        case SEQUENCER_OP_CLEAR_SEQUENCE:
            ExpectedPayload = SEQUENCER_OP_CLEAR_SEQUENCE_PAYLOAD_SIZE;
            break;
        case SEQUENCER_OP_SET_FRAME_RANGE:
            ExpectedPayload = SEQUENCER_OP_SET_FRAME_RANGE_PAYLOAD_SIZE;
            break;
        }

        if (PayloadBytes < ExpectedPayload)
        {
            Stats.SequencerOpPacketsMalformed.fetch_add(1, std::memory_order_relaxed);
            Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[SEQOP] Truncated payload: opcode=0x%02X need=%d got=%d"),
                Header.Opcode, ExpectedPayload, PayloadBytes);
            Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
            return;
        }

        // Sequence monotonicity check
        if (bHasSequencerOpState && Header.Sequence <= LastSequencerOpSequence)
        {
            Stats.SequencerOpPacketsStale.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[SEQOP] Stale packet: seq %u <= %u"),
                Header.Sequence, LastSequencerOpSequence);
            Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
            return;
        }

        // Apply the sequencer op (create/update/clear sequence)
        HandleSequencerOp(Header, Ptr + HeaderSize, PayloadBytes);

        // Store accepted op state
        LastSequencerOpOpcode   = Header.Opcode;
        LastSequencerOpFlags    = Header.Flags;
        LastSequencerOpSequence = Header.Sequence;
        LastSequencerOpTimestamp = Header.Timestamp;
        bHasSequencerOpState    = true;

        Stats.SequencerOpPacketsApplied.fetch_add(1, std::memory_order_relaxed);
        Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // MATERIAL PACKET (Phase 7B Stage 1C)
    // =====================================================
    // Wire format per object:
    //   GUID(16) + SlotCount(1) + N × [SlotIndex(1) + MaterialLow(8) + MaterialHigh(8)]
    //
    // SlotCount > MAX_MATERIAL_SLOTS (8) is rejected with
    // MalformedPackets++ and a logged warning. Zero-slot packets
    // are accepted (clear existing material metadata).
    // Material assignment (SetMaterial) is NOT performed — see Stage 2.
    // =====================================================

    if (PacketType == 0x05)
    {
        TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessMaterialPackets);

        for (uint32 i = 0; i < ObjectCount; i++)
        {
            // Minimum: GUID(16) + SlotCount(1)
            if (Ptr + LIVE_SYNC_V5_MATERIAL_OBJECT_BASE_SIZE > PacketEnd)
            {
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[MATERIAL] Truncated packet at object %u/%u"),
                    i, ObjectCount);
                return;
            }

            FGuid Guid;
            FMemory::Memcpy(&Guid, Ptr, sizeof(FGuid));
            Ptr += sizeof(FGuid);

            uint8 SlotCount = *Ptr;
            Ptr += sizeof(uint8);

            if (SlotCount > MAX_MATERIAL_SLOTS)
            {
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[MATERIAL] SlotCount=%d exceeds MAX_MATERIAL_SLOTS=%d "
                         "for GUID=%s \u2014 rejecting"),
                    SlotCount, MAX_MATERIAL_SLOTS,
                    *Guid.ToString(EGuidFormats::Digits));
                return;
            }

            TArray<FMaterialSlotRef> Slots;
            Slots.Reserve(SlotCount);

            for (uint8 s = 0; s < SlotCount; s++)
            {
                if (Ptr + LIVE_SYNC_V5_MATERIAL_SLOT_SIZE > PacketEnd)
                {
                    Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                    UE_LOG(LogLiveSync, Warning,
                        TEXT("[MATERIAL] Truncated slot %u/%u for GUID=%s"),
                        s, SlotCount,
                        *Guid.ToString(EGuidFormats::Digits));
                    return;
                }

                FMaterialSlotRef Slot;
                Slot.SlotIndex = static_cast<int8>(*Ptr);
                Ptr += sizeof(uint8);

                uint64 Low, High;
                FMemory::Memcpy(&Low, Ptr, sizeof(uint64));
                Ptr += sizeof(uint64);
                FMemory::Memcpy(&High, Ptr, sizeof(uint64));
                Ptr += sizeof(uint64);

                Slot.Identity.High = High;
                Slot.Identity.Low = Low;

                Slots.Add(Slot);
            }

            if (GEnableVerboseSyncLogs)
            {
                // MATSTALL diagnostics: log material packet processing.
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MATSTALL][UE] mat_packet guid=%s slot_count=%d objects=%d"),
                    *Guid.ToString(EGuidFormats::Digits),
                    Slots.Num(), ObjectCount);

                // MATSTALL: quick ActorCache check for this GUID.
                AActor* _mat_actor = FindActorFast(Guid);
                if (_mat_actor)
                {
                    UE_LOG(LogLiveSync, Log,
                        TEXT("[MATSTALL][UE] mat_packet actor_cache_hit guid=%s actor=%s"),
                        *Guid.ToString(EGuidFormats::Digits),
                        *_mat_actor->GetName());
                }
                else
                {
                    UE_LOG(LogLiveSync, Warning,
                        TEXT("[MATSTALL][UE] mat_packet actor_cache_miss guid=%s"),
                        *Guid.ToString(EGuidFormats::Digits));
                }
            }

            HandleMaterialDef(Guid, Slots, ObjectCount);

            // Phase 10J.5H+5J: parse MATX extension block (optional properties)
            TArray<FMaterialSlotBasicProperties> BasicProps;
            if (Ptr + (int32)sizeof(uint32) <= PacketEnd)
            {
                uint32 MatxMagic = 0;
                FMemory::Memcpy(&MatxMagic, Ptr, sizeof(uint32));
                if (MatxMagic == MATX_MAGIC)
                {
                    // Phase 10J.5J: MATX receive trace
                    int32 RemainingBytes = PacketEnd - (Ptr + sizeof(uint32) + sizeof(uint8));
                    UE_LOG(LogLiveSync, Log,
                        TEXT("[MAT][RECV] guid=%s slotCount=%d hasMATX=1 remainingBytes=%d"),
                        *Guid.ToString(EGuidFormats::Digits),
                        ObjectCount, RemainingBytes);
                    Ptr += sizeof(uint32);
                    if (Ptr < PacketEnd)
                    {
                        uint8 ExVersion = *Ptr; Ptr++;
                        if (ExVersion == MATX_VERSION_CURRENT && Ptr < PacketEnd)
                        {
                            uint8 ExtSlotCount = *Ptr; Ptr++;
                            BasicProps.Reserve(ExtSlotCount);
                            for (uint8 es = 0; es < ExtSlotCount && es < MAX_MATERIAL_SLOTS; es++)
                            {
                                if (Ptr + MATX_PROP_SLOT_SIZE > PacketEnd)
                                {
                                    UE_LOG(LogLiveSync, Warning,
                                        TEXT("[MATERIAL] Truncated MATX slot %u/%u for GUID=%s"),
                                        es, ExtSlotCount,
                                        *Guid.ToString(EGuidFormats::Digits));
                                    break;
                                }

                                FMaterialSlotBasicProperties Prop;
                                Prop.bHasProperties = true;
                                int8 SlotIdx = static_cast<int8>(*Ptr); Ptr += sizeof(uint8);

                                float R, G, B, A;
                                FMemory::Memcpy(&R, Ptr, sizeof(float)); Ptr += sizeof(float);
                                FMemory::Memcpy(&G, Ptr, sizeof(float)); Ptr += sizeof(float);
                                FMemory::Memcpy(&B, Ptr, sizeof(float)); Ptr += sizeof(float);
                                FMemory::Memcpy(&A, Ptr, sizeof(float)); Ptr += sizeof(float);

                                Prop.BaseColor = FLinearColor(R, G, B);
                                Prop.Alpha = FMath::Clamp(A, 0.0f, 1.0f);

                                FMemory::Memcpy(&Prop.Roughness, Ptr, sizeof(float)); Ptr += sizeof(float);
                                FMemory::Memcpy(&Prop.Metallic, Ptr, sizeof(float)); Ptr += sizeof(float);
                                Prop.Roughness = FMath::Clamp(Prop.Roughness, 0.0f, 1.0f);
                                Prop.Metallic = FMath::Clamp(Prop.Metallic, 0.0f, 1.0f);

                                // Store at slot position
                                if (SlotIdx >= 0 && SlotIdx < MAX_MATERIAL_SLOTS)
                                {
                                    if (BasicProps.Num() <= SlotIdx)
                                        BasicProps.SetNum(SlotIdx + 1);
                                    BasicProps[SlotIdx] = Prop;

                                    // Phase 10J.5J: MATX parse trace
                                    UE_LOG(LogLiveSync, Log,
                                        TEXT("[MAT][PARSE] guid=%s slot=%d color=(%.3f,%.3f,%.3f,%.3f) "
                                             "roughness=%.3f metallic=%.3f alpha=%.3f"),
                                        *Guid.ToString(EGuidFormats::Digits), SlotIdx,
                                        Prop.BaseColor.R, Prop.BaseColor.G,
                                        Prop.BaseColor.B, Prop.BaseColor.A,
                                        Prop.Roughness, Prop.Metallic, Prop.Alpha);
                                }
                            }
                        }
                        else if (ExVersion != 0)
                        {
                            UE_LOG(LogLiveSync, Verbose,
                                TEXT("[MATERIAL] Unknown MATX version %u for GUID=%s \u2014 skipping"),
                                ExVersion, *Guid.ToString(EGuidFormats::Digits));
                        }
                    }
                }
            }

            // Phase 10K.1: parse MTEX extension block (optional texture map references)
            // Can appear after MATX or directly after identity block if MATX absent.
            TArray<FMaterialTextureMapRef> TexMaps;
            if (Ptr + (int32)sizeof(uint32) <= PacketEnd)
            {
                uint32 MtexMagic = 0;
                FMemory::Memcpy(&MtexMagic, Ptr, sizeof(uint32));
                if (MtexMagic == MTEX_MAGIC)
                {
                    int32 RemainingForMtex = PacketEnd - (Ptr + sizeof(uint32) + sizeof(uint8));
                    UE_LOG(LogLiveSync, Log,
                        TEXT("[MTEX][RECV] guid=%s hasMTEX=1 records=N remainingBytes=%d"),
                        *Guid.ToString(EGuidFormats::Digits), RemainingForMtex);
                    Ptr += sizeof(uint32);
                    if (Ptr < PacketEnd)
                    {
                        uint8 MtexVersion = *Ptr; Ptr++;
                        if (MtexVersion == MTEX_VERSION_CURRENT && Ptr < PacketEnd)
                        {
                            uint8 RecordCount = *Ptr; Ptr++;
                            TexMaps.Reserve(RecordCount);
                            for (uint8 ri = 0; ri < RecordCount; ri++)
                            {
                                if (Ptr + MTEX_RECORD_MIN_SIZE > PacketEnd)
                                {
                                    UE_LOG(LogLiveSync, Warning,
                                        TEXT("[MTEX][MALFORMED] guid=%s reason=truncated_record "
                                             "record=%u/%u remaining=%d"),
                                        *Guid.ToString(EGuidFormats::Digits),
                                        ri, RecordCount,
                                        (int32)(PacketEnd - Ptr));
                                    MtexMalformed++;
                                    break;
                                }

                                FMaterialTextureMapRef TexRef;
                                TexRef.SlotIndex = static_cast<int8>(*Ptr); Ptr += sizeof(uint8);
                                TexRef.Channel = *Ptr; Ptr += sizeof(uint8);
                                TexRef.Flags = *Ptr; Ptr += sizeof(uint8);

                                uint16 PathLen = 0;
                                FMemory::Memcpy(&PathLen, Ptr, sizeof(uint16)); Ptr += sizeof(uint16);

                                // Clamp PathLen to safe bounds
                                if (PathLen > MTEX_MAX_PATH_LEN)
                                {
                                    UE_LOG(LogLiveSync, Warning,
                                        TEXT("[MTEX][MALFORMED] guid=%s reason=path_len=%u exceeds max=%d "
                                             "record=%u/%u"),
                                        *Guid.ToString(EGuidFormats::Digits),
                                        PathLen, MTEX_MAX_PATH_LEN, ri, RecordCount);
                                    MtexMalformed++;
                                    PathLen = MTEX_MAX_PATH_LEN;
                                }

                                if (Ptr + PathLen > PacketEnd)
                                {
                                    UE_LOG(LogLiveSync, Warning,
                                        TEXT("[MTEX][MALFORMED] guid=%s reason=path_exceeds_packet "
                                             "record=%u/%u pathLen=%u remaining=%d"),
                                        *Guid.ToString(EGuidFormats::Digits),
                                        ri, RecordCount, PathLen,
                                        (int32)(PacketEnd - Ptr));
                                    MtexMalformed++;
                                    break;
                                }

                                if (PathLen > 0)
                                {
                                    FUtf8String PathStr;
                                    PathStr.Append(reinterpret_cast<const ANSICHAR*>(Ptr), PathLen);
                                    TexRef.Path = UTF8_TO_TCHAR(PathStr.GetCharArray().GetData());
                                    Ptr += PathLen;
                                }

                                if (Ptr >= PacketEnd)
                                {
                                    UE_LOG(LogLiveSync, Warning,
                                        TEXT("[MTEX][MALFORMED] guid=%s reason=missing_imagename_len "
                                             "record=%u/%u"),
                                        *Guid.ToString(EGuidFormats::Digits), ri, RecordCount);
                                    MtexMalformed++;
                                    break;
                                }

                                uint8 NameLen = *Ptr; Ptr++;

                                if (NameLen > MTEX_MAX_IMAGE_NAME_LEN)
                                {
                                    UE_LOG(LogLiveSync, Warning,
                                        TEXT("[MTEX][MALFORMED] guid=%s reason=imagename_len=%u exceeds max=%d "
                                             "record=%u/%u"),
                                        *Guid.ToString(EGuidFormats::Digits),
                                        NameLen, MTEX_MAX_IMAGE_NAME_LEN, ri, RecordCount);
                                    MtexMalformed++;
                                    NameLen = MTEX_MAX_IMAGE_NAME_LEN;
                                }

                                if (Ptr + NameLen > PacketEnd)
                                {
                                    UE_LOG(LogLiveSync, Warning,
                                        TEXT("[MTEX][MALFORMED] guid=%s reason=imagename_exceeds_packet "
                                             "record=%u/%u nameLen=%u remaining=%d"),
                                        *Guid.ToString(EGuidFormats::Digits),
                                        ri, RecordCount, NameLen,
                                        (int32)(PacketEnd - Ptr));
                                    MtexMalformed++;
                                    break;
                                }

                                if (NameLen > 0)
                                {
                                    FUtf8String NameStr;
                                    NameStr.Append(reinterpret_cast<const ANSICHAR*>(Ptr), NameLen);
                                    TexRef.ImageName = UTF8_TO_TCHAR(NameStr.GetCharArray().GetData());
                                    Ptr += NameLen;
                                }

                                TexMaps.Add(TexRef);

                                // Phase 7H.6 Task C: MATX texture receive log
                                const TCHAR* ChannelName = TEXT("Unknown");
                                switch (TexRef.Channel)
                                {
                                    case 1: ChannelName = TEXT("BaseColor"); break;
                                    case 2: ChannelName = TEXT("Roughness"); break;
                                    case 3: ChannelName = TEXT("Metallic"); break;
                                    case 4: ChannelName = TEXT("Alpha"); break;
                                    case 5: ChannelName = TEXT("Normal"); break;
                                }
                                UE_LOG(LogLiveSync, Log,
                                    TEXT("[MATERIAL][MATX_TEXTURE_RECV] guid=%s slot=%d channel=%s path=%s exists=%d"),
                                    *Guid.ToString(EGuidFormats::Digits),
                                    TexRef.SlotIndex, ChannelName,
                                    TexRef.Path.Len() > 0 ? *TexRef.Path : TEXT("(none)"),
                                    (TexRef.Path.Len() > 0 ? 1 : 0));

                                // Phase 10K.1: MTEX parse trace
                                UE_LOG(LogLiveSync, Log,
                                    TEXT("[MTEX][PARSE] guid=%s slot=%d channel=%s image=%s "
                                         "path=%s flags=%u"),
                                    *Guid.ToString(EGuidFormats::Digits),
                                    TexRef.SlotIndex, ChannelName,
                                    *TexRef.ImageName,
                                    TexRef.Path.Len() > 0 ? *TexRef.Path : TEXT("(none)"),
                                    TexRef.Flags);
                            }
                        }
                        else if (MtexVersion != 0)
                        {
                            UE_LOG(LogLiveSync, Verbose,
                                TEXT("[MTEX][SKIP] guid=%s version=%u unsupported"),
                                *Guid.ToString(EGuidFormats::Digits), MtexVersion);
                        }
                    }
                }
            }

            // Store MTEX in cache (Phase 10K.1: diagnostic only)
            if (TexMaps.Num() > 0)
            {
                MaterialTextureMapCache.Add(Guid, TexMaps);
                MtexBlocksParsed++;
                MtexRecordsParsed += TexMaps.Num();
            }

            // Phase 7H: log texture record count for all PT_Material packets
            UE_LOG(LogLiveSync, Log,
                TEXT("[MATERIAL][MATX_TEXTURE_RECV] guid=%s textureRecordCount=%d"),
                *Guid.ToString(EGuidFormats::Digits), TexMaps.Num());

#if WITH_EDITOR
            // Phase 10K.2: import textures from discovered MTEX records
            if (TexMaps.Num() > 0)
            {
                ImportTexturesFromMtexRecs(Guid, TexMaps);
            }
#endif

            // Apply material — persistent if Blender material identity present,
            // or MID fallback if slot is empty.
            for (const FMaterialSlotBasicProperties& BP : BasicProps)
            {
                if (BP.bHasProperties)
                {
                    ParseAndApplyGeneratedMaterial(Guid, BasicProps, Slots);
                    break;
                }
            }
        }

        Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // MESH CHUNK PACKET (Phase 7C Stage 1B)
    // =====================================================
    // Wire format per object:
    //   Header:  GUID(16) + VersionHash(64) + ChunkIndex(4) + ChunkCount(4) + Flags(1) = 89 bytes
    //   Payload: follows header (variable-length data blocks)
    //
    // Chunks are accumulated in PendingMeshReassembly by (GUID, VersionHash).
    // No mesh sections are built — deferred to Stage 1C.
    // =====================================================

    if (PacketType == 0x06)
    {
        TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessMeshChunkPackets);

        for (uint32 i = 0; i < ObjectCount; i++)
        {
            // Minimum: entire header must fit
            if (Ptr + LIVE_SYNC_V5_MESH_CHUNK_HEADER_SIZE > PacketEnd)
            {
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[MESH] Truncated chunk header at object %u/%u"),
                    i, ObjectCount);
                return;
            }

            FGuid Guid;
            FMemory::Memcpy(&Guid, Ptr, sizeof(FGuid));
            Ptr += sizeof(FGuid);

            // Version hash (64 bytes of ASCII hex)
            FString VersionHash;
            {
                ANSICHAR HashBuf[LIVE_SYNC_V5_MESH_VERSION_HASH_SIZE + 1] = {};
                FMemory::Memcpy(HashBuf, Ptr, LIVE_SYNC_V5_MESH_VERSION_HASH_SIZE);
                HashBuf[LIVE_SYNC_V5_MESH_VERSION_HASH_SIZE] = '\0';
                VersionHash = ANSI_TO_TCHAR(HashBuf);
                Ptr += LIVE_SYNC_V5_MESH_VERSION_HASH_SIZE;
            }

            if (VersionHash.Len() != LIVE_SYNC_V5_MESH_VERSION_HASH_SIZE)
            {
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[MESH] Invalid version hash length (%d) for GUID=%s"),
                    VersionHash.Len(),
                    *Guid.ToString(EGuidFormats::Digits));
                return;
            }

            if (!Guid.IsValid())
            {
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[MESH] Invalid GUID in chunk"));
                return;
            }

            uint32 ChunkIndex;
            uint32 ChunkCount;
            FMemory::Memcpy(&ChunkIndex, Ptr, sizeof(uint32));
            Ptr += sizeof(uint32);
            FMemory::Memcpy(&ChunkCount, Ptr, sizeof(uint32));
            Ptr += sizeof(uint32);

            uint8 Flags = *Ptr;
            Ptr += sizeof(uint8);

            // Validate chunk count
            if (ChunkCount == 0)
            {
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[MESH] ChunkCount=0 for GUID=%s"),
                    *Guid.ToString(EGuidFormats::Digits));
                return;
            }

            // Validate chunk index
            if (ChunkIndex >= ChunkCount)
            {
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[MESH] ChunkIndex=%d >= ChunkCount=%d for GUID=%s"),
                    ChunkIndex, ChunkCount,
                    *Guid.ToString(EGuidFormats::Digits));
                return;
            }

            // Compute actual per-object payload size from wire format.
            // V5: VertCount(4) + Verts(N*12) + TriCount(4) + Tris(N*12) + MatCount(4) + Mats(N*4)
            // V1: [SchemaVersion(4,chunk0)] VertexStride(4) VertexCount(4) Verts(N*S)
            //     IndexCount(4) Indices(N*4) MaterialSlotCount(4) MaterialSlots(N*4)
            const bool bHasFullAttr = (Flags & MESH_CHUNK_FLAG_FULL_ATTR) != 0;
            int32 ComputedPayloadSize = -1;
            {
                const uint8* P = Ptr;
                const uint8* End = PacketEnd;

                if (bHasFullAttr)
                {
                    // V1 wire format
                    if (ChunkIndex == 0 && P + 4 <= End) { P += 4; } // skip SchemaVersion
                    if (P + 4 <= End)
                    {
                        uint32 VS = 0; FMemory::Memcpy(&VS, P, sizeof(uint32)); P += 4;
                        if ((VS == 32 || VS == 48) && P + 4 <= End)
                        {
                            uint32 VC = 0; FMemory::Memcpy(&VC, P, sizeof(uint32)); P += 4;
                            int64 VB = static_cast<int64>(VC) * VS;
                            if (P + VB + 4 <= End)
                            {
                                P += VB;
                                uint32 IC = 0; FMemory::Memcpy(&IC, P, sizeof(uint32)); P += 4;
                                int64 IB = static_cast<int64>(IC) * 4;
                                if (P + IB + 4 <= End)
                                {
                                    P += IB;
                                    uint32 MC = 0; FMemory::Memcpy(&MC, P, sizeof(uint32)); P += 4;
                                    int64 MB = static_cast<int64>(MC) * 4;
                                    if (P + MB <= End) { P += MB; ComputedPayloadSize = static_cast<int32>(P - Ptr); }
                                }
                            }
                        }
                    }
                }
                else
                {
                    // V5 wire format
                    if (P + 4 <= End)
                    {
                        uint32 VC = 0; FMemory::Memcpy(&VC, P, sizeof(uint32)); P += 4;
                        int64 VB = static_cast<int64>(VC) * 12;
                        if (P + VB + 4 <= End)
                        {
                            P += VB;
                            uint32 TC = 0; FMemory::Memcpy(&TC, P, sizeof(uint32)); P += 4;
                            int64 TB = static_cast<int64>(TC) * 12;
                            if (P + TB + 4 <= End)
                            {
                                P += TB;
                                uint32 MC = 0; FMemory::Memcpy(&MC, P, sizeof(uint32)); P += 4;
                                int64 MB = static_cast<int64>(MC) * 4;
                                if (P + MB <= End) { P += MB; ComputedPayloadSize = static_cast<int32>(P - Ptr); }
                            }
                        }
                    }
                }
            }

            // Use computed size if valid, otherwise fall back to remaining bytes
            const int32 PayloadSize =
                (ComputedPayloadSize > 0 && Ptr + ComputedPayloadSize <= PacketEnd)
                ? ComputedPayloadSize
                : static_cast<int32>(PacketEnd - Ptr);

            UE_LOG(LogLiveSync, Log,
                TEXT("[MESH-PARSE] obj=%u/%u GUID=%s "
                     "OffsetBefore=%lld OffsetAfterHeader=%lld "
                     "PayloadSize=%d Computed=%d BytesAfterPayload=%lld"),
                i, ObjectCount,
                *Guid.ToString(EGuidFormats::Digits),
                OffsetBefore, OffsetAfterHeader,
                PayloadSize, ComputedPayloadSize,
                (int64)(PacketEnd - (Ptr + PayloadSize)));

            if (PayloadSize < 0)
            {
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[MESH] Negative payload size for GUID=%s"),
                    *Guid.ToString(EGuidFormats::Digits));
                return;
            }

            TArrayView<const uint8> PayloadView(
                Ptr, PayloadSize);

            // =====================================================
            // Phase 7C Stage 2C.2: FULL_ATTR v1 reassembly
            // =====================================================

            // Phase 10J.5E/K: Skip v1 chunk accumulation for FBX-authoritative AND FBX-pending GUIDs.
            if (bHasFullAttr && (FBXAuthoritativeGuids.Contains(Guid) || FBXPendingGuids.Contains(Guid)))
            {
                if (FBXPendingGuids.Contains(Guid))
                {
                    UE_LOG(LogLiveSync, Log,
                        TEXT("[MESH][AUTH] skip_pt_mesh_fbx_pending guid=%s"),
                        *Guid.ToString(EGuidFormats::Digits));
                }
                else
                {
                    UE_LOG(LogLiveSync, Log,
                        TEXT("[MESH][AUTH] skip_pt_mesh_fbx_authoritative guid=%s"),
                        *Guid.ToString(EGuidFormats::Digits));
                }
                Ptr += PayloadSize;
                continue;
            }

            if (bHasFullAttr)
            {
                FV1MeshParsedChunk ParsedChunk;
                if (ParseV1MeshPayload(Guid, ChunkIndex, ChunkCount, PayloadView, ParsedChunk))
                {
                    Stats.MeshSchemaV1PacketsParsed.fetch_add(1, std::memory_order_relaxed);

                    FV1MeshReassemblyKey Key;
                    Key.Guid = Guid;
                    Key.VersionHash = VersionHash;

                    FV1MeshReassemblyState& State =
                        PendingV1MeshReassembly.FindOrAdd(Key);

                    // First chunk for this key: initialize state
                    if (State.ChunkCount == 0)
                    {
                        State.ChunkCount   = ParsedChunk.ChunkCount;
                        State.VertexStride = ParsedChunk.VertexStride;
                    }
                    else
                    {
                        // Validate consistency with prior chunks
                        bool bMismatch = false;
                        if (State.ChunkCount != ParsedChunk.ChunkCount)
                        {
                            UE_LOG(LogLiveSync, Warning,
                                TEXT("[MESH][V1] ChunkCount mismatch for GUID=%s "
                                     "(existing=%u new=%u)"),
                                *Guid.ToString(EGuidFormats::Digits),
                                State.ChunkCount, ParsedChunk.ChunkCount);
                            bMismatch = true;
                        }
                        if (State.VertexStride != ParsedChunk.VertexStride)
                        {
                            UE_LOG(LogLiveSync, Warning,
                                TEXT("[MESH][V1] VertexStride mismatch for GUID=%s "
                                     "(existing=%u new=%u)"),
                                *Guid.ToString(EGuidFormats::Digits),
                                State.VertexStride, ParsedChunk.VertexStride);
                            bMismatch = true;
                        }

                        if (bMismatch)
                        {
                            Stats.MeshSchemaV1ReassemblyRejected.fetch_add(1, std::memory_order_relaxed);
                            Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                            Ptr += PayloadSize;
                            continue;
                        }
                    }

                    // Check for duplicate chunk index
                    if (State.HasChunk(ChunkIndex))
                    {
                        Stats.MeshSchemaV1DuplicateChunks.fetch_add(1, std::memory_order_relaxed);
                        UE_LOG(LogLiveSync, Verbose,
                            TEXT("[MESH][V1] Duplicate chunk %u/%u for GUID=%s"),
                            ChunkIndex, ChunkCount,
                            *Guid.ToString(EGuidFormats::Digits));
                        Ptr += PayloadSize;
                        continue;
                    }

                    // Store parsed chunk
                    State.Chunks.Add(ChunkIndex, MoveTemp(ParsedChunk));
                    State.ChunksReceived++;
                    Stats.MeshSchemaV1ChunksStored.fetch_add(1, std::memory_order_relaxed);

                    UE_LOG(LogLiveSync, Log,
                        TEXT("[MESH][V1] Stored chunk %u/%u for GUID=%s "
                             "(received=%u/%u)"),
                        ChunkIndex, ChunkCount,
                        *Guid.ToString(EGuidFormats::Digits),
                        State.ChunksReceived, State.ChunkCount);

                    // Check completion
                    if (State.IsComplete())
                    {
                        Stats.MeshSchemaV1MeshesCompleted.fetch_add(1, std::memory_order_relaxed);
                        UE_LOG(LogLiveSync, Log,
                            TEXT("[MESH][V1] Reassembly complete for GUID=%s "
                                 "(%u/%u chunks)"),
                            *Guid.ToString(EGuidFormats::Digits),
                            State.ChunksReceived, State.ChunkCount);
                    }
                }
                else
                {
                    Stats.MeshSchemaV1PacketsRejected.fetch_add(1, std::memory_order_relaxed);
                    Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                }

                Ptr += PayloadSize;
                continue;
            }

            // FULL_ATTR absent: legacy V5 path
            Stats.MeshSchemaV5Packets++;
            HandleMeshChunk(
                Guid,
                VersionHash,
                ChunkIndex,
                ChunkCount,
                Flags,
                PayloadView);

            Ptr += PayloadSize;
        }

        Stats.PacketsProcessed.fetch_add(
            1,
            std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // FBX IMPORT REQUEST (Phase 7C Stage 3A.1 — PT_FBXImportRequest 0x16)
    // =====================================================
    // Fixed 688-byte payload (Phase 10J.5F: added GeometryHash).
    // Backward compatible: old 680-byte payloads accepted (GeometryHash = 0).
    // UE validates path safety, imports FBX as StaticMesh under
    // /Game/UELiveSync/Imported, then spawns/updates a StaticMeshActor
    // tagged with the LiveSync GUID.
    // =====================================================

    if (PacketType == 0x16)
    {
        TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessFBXImport);

        Stats.FBXImportRequestsReceived.fetch_add(
            1, std::memory_order_relaxed);

        int32 ObjSize = static_cast<int32>(PacketEnd - Ptr);
        // Accept both old (680) and new (688) payload sizes
        constexpr int32 kFBXPayloadSizeMin = 680;
        if (ObjSize < kFBXPayloadSizeMin)
        {
            Stats.MalformedPackets.fetch_add(
                1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[FBX] Truncated payload: size %d < %d"),
                ObjSize, kFBXPayloadSizeMin);
            Stats.PacketsProcessed.fetch_add(
                1, std::memory_order_relaxed);
            return;
        }

        {
            // Phase 10J.5K: Extract GUID to mark FBX pending before import.
            FGuid FbxRequestGuid;
            FMemory::Memcpy(&FbxRequestGuid, Ptr, sizeof(FGuid));
            FBXPendingGuids.Add(FbxRequestGuid);
            UE_LOG(LogLiveSync, Log,
                TEXT("[FBX][AUTH] mark_pending guid=%s reason=fbx_request_received"),
                *FbxRequestGuid.ToString(EGuidFormats::Digits));

            FFBXImportContext Ctx;
            Ctx.World = GetWorld();
            Ctx.Stats = &Stats;
            Ctx.FindActor = [this](const FGuid& G) { return FindActorFast(G); };
            Ctx.OnActorCached = [this](const FGuid& G, AActor* A) { ActorCache.Add(G, A); };
            Ctx.OnMarkFbxAuthority = [this](const FGuid& G)
            {
                FBXAuthoritativeGuids.Add(G);
                FBXPendingGuids.Remove(G);
                UE_LOG(LogLiveSync, Log,
                    TEXT("[FBX][AUTH] guid=%s authority=fbx"),
                    *G.ToString(EGuidFormats::Digits));
            };
            Ctx.OnScheduleRepair = [this](const FGuid& G)
            {
                FDeferredFBXRepairEntry Entry;
                Entry.Guid = G;
                Entry.PassNumber = 1;
                Entry.ScheduleTime = FPlatformTime::Seconds();
                DeferredFBXRepairs.Add(Entry);

                FDeferredFBXRepairEntry Entry2;
                Entry2.Guid = G;
                Entry2.PassNumber = 2;
                Entry2.ScheduleTime = FPlatformTime::Seconds();
                DeferredFBXRepairs.Add(Entry2);
            };
            // Phase 10J.5L: After EnsureFBXMeshRenderable fallback, restore generated MIDs.
            // Phase 7H/7G.5: do NOT override imported FBX materials with MIDs.
            Ctx.OnRestoreGeneratedMaterials = [this](const FGuid& G, UStaticMeshComponent* SMC)
            {
                if (!SMC)
                    return;
                const FString GuidShort = G.ToString(EGuidFormats::Short);
                const int32 NumSlots = SMC->GetNumMaterials();

                // Phase 7H Task 4: Count cache entries before restore.
                int32 CacheHits = 0;
                for (int32 SlotIdx = 0; SlotIdx < NumSlots; ++SlotIdx)
                {
                    const FString Key = FString::Printf(TEXT("%s_%d"), *GuidShort, SlotIdx);
                    TObjectPtr<UMaterialInstanceDynamic>* Found = GeneratedMaterialCache.Find(Key);
                    if (Found && *Found) ++CacheHits;
                }

                UE_LOG(LogLiveSync, Log,
                    TEXT("[MATERIAL][GENERATED_MID_RESTORE_CHECK] guid=%s cachedSlots=%d meshSlots=%d"),
                    *G.ToString(EGuidFormats::Digits), CacheHits, NumSlots);

                // Guard: only restore if all mesh slots have cached MIDs.
                if (CacheHits != NumSlots)
                {
                    UE_LOG(LogLiveSync, Log,
                        TEXT("[MATERIAL][GENERATED_MID_RESTORE_SKIP] guid=%s cachedSlots=%d meshSlots=%d reason=slot_count_mismatch"),
                        *G.ToString(EGuidFormats::Digits), CacheHits, NumSlots);
                    return;
                }

                int32 RestoredCount = 0;
                for (int32 SlotIdx = 0; SlotIdx < NumSlots; ++SlotIdx)
                {
                    const FString Key = FString::Printf(TEXT("%s_%d"), *GuidShort, SlotIdx);
                    TObjectPtr<UMaterialInstanceDynamic>* Found = GeneratedMaterialCache.Find(Key);
                    UMaterialInterface* CurrentMat = SMC->GetMaterial(SlotIdx);
                    if (Found && *Found && CurrentMat != *Found)
                    {
                        SMC->SetMaterial(SlotIdx, *Found);
                        ++RestoredCount;
                        UE_LOG(LogLiveSync, Log,
                            TEXT("[MATERIAL][GENERATED_PARAM_MID_APPLY] guid=%s slot=%d restored=MID_UELiveSync_%s_%d"),
                            *G.ToString(EGuidFormats::Digits), SlotIdx,
                            *GuidShort, SlotIdx);
                    }
                }

                UE_LOG(LogLiveSync, Log,
                    TEXT("[MATERIAL][GENERATED_MID_RESTORE_OK] guid=%s slots=%d"),
                    *G.ToString(EGuidFormats::Digits), RestoredCount);

                if (RestoredCount > 0)
                {
                    UE_LOG(LogLiveSync, Log,
                        TEXT("[MAT][AUTH] guid=%s slot_count=%d authority=generated_mid"),
                        *G.ToString(EGuidFormats::Digits), RestoredCount);
                }
            };
            // Task 9B: wire sidecar texture import registration.
            Ctx.OnSidecarTextureImported = [this](
                const FGuid& G, const FString& SourceFilename, const TSoftObjectPtr<UTexture2D>& TexPtr)
            {
                if (!TexPtr.IsValid()) return;
                // Canonical key: lowercase basename without extension or hash suffix.
                // Must match the MTEX lookup key (TexRef.ImageName, dot-stripped, lowercased).
                FString BaseName = FPaths::GetBaseFilename(SourceFilename).ToLower();
                {
                    // Strip __{hash} suffix (sidecar naming convention)
                    int32 UnderscorePos = BaseName.Find(TEXT("__"));
                    if (UnderscorePos != INDEX_NONE)
                    {
                        BaseName = BaseName.Left(UnderscorePos);
                    }
                }
                {
                    // Strip any remaining dots (e.g. "marble.png" -> "marble")
                    int32 DotPos = INDEX_NONE;
                    if (BaseName.FindChar(TEXT('.'), DotPos))
                    {
                        BaseName = BaseName.Left(DotPos);
                    }
                }
                if (!ImportedSidecarTexturesByGuid.Contains(G))
                {
                    ImportedSidecarTexturesByGuid.Add(G, TMap<FString, TSoftObjectPtr<UTexture2D>>());
                }
                auto& SidecarMap = ImportedSidecarTexturesByGuid[G];
                if (!SidecarMap.Contains(BaseName))
                {
                    SidecarMap.Add(BaseName, TexPtr);
                }
            };
            FLiveSyncFBXImporter::HandleImport(Ptr, ObjSize, Ctx);
        }

        Stats.PacketsProcessed.fetch_add(
            1, std::memory_order_relaxed);
        return;
    }

    // =====================================================
    // KEYFRAME PACKET (Phase 7E Stage 7 — PT_Keyframe 0x17)
    // =====================================================
    // Variable-size payload: 14-byte header + N × 25-byte entries.
    //
    // Validation:
    //   - Total packet size >= header (14 bytes)
    //   - KeyCount must be in [1, KEYFRAME_MAX_KEYS (255)]
    //   - Total payload must match header + KeyCount * entry size
    //   - Entry ChannelIndex must be in [0, 255]
    //   - Sequence must be strictly greater than LastKeyframeSequence
    //     (first packet with any sequence is accepted)
    // =====================================================

    if (PacketType == 0x17)
    {
        TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessKeyframe);

        Stats.KeyframePacketsReceived.fetch_add(1, std::memory_order_relaxed);

        int32 ObjSize = static_cast<int32>(PacketEnd - Ptr);
        if (ObjSize < KEYFRAME_HEADER_SIZE)
        {
            Stats.KeyframePacketsMalformed.fetch_add(1, std::memory_order_relaxed);
            Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[KEYFRAME] Truncated header: size %d < %d"),
                ObjSize, KEYFRAME_HEADER_SIZE);
            Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
            return;
        }

        // Parse header
        FKeyframeHeader Header;
        FMemory::Memcpy(&Header, Ptr, sizeof(FKeyframeHeader));

        // Validate key count
        if (Header.KeyCount < KEYFRAME_MIN_KEYS ||
            Header.KeyCount > KEYFRAME_MAX_KEYS)
        {
            Stats.KeyframePacketsMalformed.fetch_add(1, std::memory_order_relaxed);
            Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[KEYFRAME] Invalid key count %d (range [%d,%d])"),
                Header.KeyCount, KEYFRAME_MIN_KEYS, KEYFRAME_MAX_KEYS);
            Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
            return;
        }

        // Validate total payload size matches expected
        int32 ExpectedSize = KEYFRAME_HEADER_SIZE +
            Header.KeyCount * KEYFRAME_ENTRY_SIZE;

        if (ObjSize < ExpectedSize)
        {
            Stats.KeyframePacketsMalformed.fetch_add(1, std::memory_order_relaxed);
            Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[KEYFRAME] Truncated payload: size %d < expected %d "
                     "(count=%d)"),
                ObjSize, ExpectedSize, Header.KeyCount);
            Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
            return;
        }

        // Validate each entry's channel index is in range
        const uint8* EntryPtr = Ptr + KEYFRAME_HEADER_SIZE;
        for (uint8 i = 0; i < Header.KeyCount; i++)
        {
            // ChannelIndex is the last byte of each 25-byte entry
            uint8 ChannelIndex = *(EntryPtr + KEYFRAME_ENTRY_SIZE - 1);
            if (ChannelIndex > KEYFRAME_MAX_CHANNEL)
            {
                Stats.KeyframePacketsMalformed.fetch_add(1, std::memory_order_relaxed);
                Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[KEYFRAME] Entry %d: channel %d out of range [0,%d]"),
                    i, ChannelIndex, KEYFRAME_MAX_CHANNEL);
                Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
                return;
            }
            EntryPtr += KEYFRAME_ENTRY_SIZE;
        }

        // Sequence monotonicity check
        if (bHasKeyframeState && Header.Sequence <= LastKeyframeSequence)
        {
            Stats.KeyframePacketsStale.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[KEYFRAME] Stale packet: seq %u <= %u"),
                Header.Sequence, LastKeyframeSequence);
            Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
            return;
        }

        // Apply — insert keyframes into transform tracks
        HandleKeyframe(Header, Ptr + KEYFRAME_HEADER_SIZE,
            ObjSize - KEYFRAME_HEADER_SIZE);

        Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
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
                if (GEnableVerboseSyncLogs)
                {
                    UE_LOG(LogLiveSync, Warning,
                        TEXT("[CREATE][DIAG] Parent not available for world-spawn computation — guid=%s parent=%s local transform will be used as world spawn (will correct on next interpolation tick)"),
                        *Guid.ToString(EGuidFormats::Digits),
                        *ParentGuid.ToString(EGuidFormats::Digits));
                }
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

            if (PacketType == 0x03 && GEnableVerboseSyncLogs)
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
                if (GEnableVerboseSyncLogs)
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
                }

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

    if (bEnableVerboseSyncLogs)
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("BEGIN TRACE: UpdateTargetTransform guid=%s loc=(%s) rot=(%s) scl=(%s) local=%d"),
            *Guid.ToString(EGuidFormats::Digits),
            *Location.ToString(),
            *Rotation.ToString(),
            *Scale.ToString(),
            bIsLocalTransform);
    }

    if (GEnableVerboseSyncLogs)
    {
        // MATSTALL: quick ActorCache hit/miss for transform.
        AActor* _matActorCacheActor = FindActorFast(Guid);
        if (_matActorCacheActor)
        {
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[MATSTALL][UE] transform actor_cache_hit guid=%s actor=%s"),
                *Guid.ToString(EGuidFormats::Digits),
                *_matActorCacheActor->GetName());
        }
        else
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[MATSTALL][UE] transform actor_cache_miss guid=%s"),
                *Guid.ToString(EGuidFormats::Digits));
        }
    }

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

        if (ShouldLogVerbose())
        {
            UE_LOG(
                LogLiveSync,
                Log,
                TEXT("END TRACE: UpdateTargetTransform guid=%s (unchanged)"),
                *Guid.ToString(
                    EGuidFormats::Digits));
        }

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

    if (ShouldLogVerbose())
    {
        UE_LOG(
            LogLiveSync,
            Log,
            TEXT("END TRACE: UpdateTargetTransform guid=%s"),
            *Guid.ToString(
                EGuidFormats::Digits));
    }
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

    if (bEnableVerboseSyncLogs)
    {
        UE_LOG(LogLiveSync, Log, TEXT("BEGIN InterpolateTransforms"));
    }

    // Skip interpolation during snapshot build — all transforms
    // will be bulk-applied when EndSnapshot is received
    if (bInSnapshotBuild)
    {
        if (bEnableVerboseSyncLogs)
        {
            UE_LOG(LogLiveSync, Log, TEXT("END   InterpolateTransforms (snapshot build, skip)"));
        }
        return;
    }

    // =====================================================
    // ISOLATION: Skip transform application if disabled
    // =====================================================

    if (CVarLiveSyncDisableTransformApply.GetValueOnGameThread())
    {
        if (bEnableVerboseSyncLogs)
        {
            UE_LOG(LogLiveSync, Log, TEXT("END   InterpolateTransforms (disabled by CVar)"));
        }
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

        if (ShouldLogVerbose())
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("BEGIN transform apply guid=%s actor=%p iter=%d total=%d"),
                *Guid.ToString(EGuidFormats::Digits),
                (void*)Actor,
                InterpIterationIndex,
                TransformStates.Num());
        }

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
            // Stage 7G.4: camera diagnostic marker for converged state
            if (bEnableVerboseSyncLogs)
            {
                if (Actor->IsA(ACameraActor::StaticClass()))
                {
                    UE_LOG(LogLiveSync, Log,
                        TEXT("[CAMERA][TRANSFORM_CONVERGED] guid=%s actor=%s"),
                        *Guid.ToString(EGuidFormats::Digits),
                        *Actor->GetName());
                }
                UE_LOG(LogLiveSync, Log,
                    TEXT("END   transform apply guid=%s (converged)"),
                    *Guid.ToString(EGuidFormats::Digits));
            }
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

                    if (ShouldLogVerbose())
                    {
                        UE_LOG(LogLiveSync, Log,
                            TEXT("  BEGIN SetActorTransform guid=%s (attached child)"),
                            *Guid.ToString(EGuidFormats::Digits));
                    }

                    if (ValidateTransform(WorldXForm, Guid, TEXT("AttachedChild")))
                    {
                        if (ShouldLogVerbose())
                        {
                            UE_LOG(LogLiveSync, Log,
                                TEXT("  DO SetActorTransform guid=%s (attached child)"),
                                *Guid.ToString(EGuidFormats::Digits));
                        }
                        if (!CVarLiveSyncBypassSetActorTransform.GetValueOnGameThread())
                        {
                            // Diagnostic: rotation pipeline before SetActorTransform
                            const FQuat TargetWorldRot = State.LocalTargetRotation * Parent->GetActorQuat();
                            const FQuat BeforeActorQuat = Actor->GetActorQuat();
                            const double DeltaAngleRad = TargetWorldRot.AngularDistance(WorldXForm.GetRotation());
                            if (bEnableVerboseSyncLogs)
                            {
                                UE_LOG(LogLiveSync, Log,
                                    TEXT("[SAT_DIAG][child] frame=%llu guid=%s"
                                         " netTargetQuat=(%.4f,%.4f,%.4f,%.4f)"
                                         " netTargetRot=(%.1f,%.1f,%.1f)"
                                         " actorQuatBefore=(%.4f,%.4f,%.4f,%.4f)"
                                         " appliedQuat=(%.4f,%.4f,%.4f,%.4f)"
                                         " deltaAngleDeg=%.2f"),
                                    GFrameCounter,
                                    *Guid.ToString(EGuidFormats::Digits),
                                    State.LocalTargetRotation.X, State.LocalTargetRotation.Y, State.LocalTargetRotation.Z, State.LocalTargetRotation.W,
                                    State.LocalTargetRotation.Rotator().Pitch, State.LocalTargetRotation.Rotator().Yaw, State.LocalTargetRotation.Rotator().Roll,
                                    BeforeActorQuat.X, BeforeActorQuat.Y, BeforeActorQuat.Z, BeforeActorQuat.W,
                                    WorldXForm.GetRotation().X, WorldXForm.GetRotation().Y, WorldXForm.GetRotation().Z, WorldXForm.GetRotation().W,
                                    FMath::RadiansToDegrees(DeltaAngleRad));
                            }

                            Actor->SetActorTransform(WorldXForm);
                            DiagBasis_CameraOneShot(Actor, Guid);
                            // Phase 10J.5D.5: TRANSFORM_WARN for FBX-authoritative actors
                            if (FBXAuthoritativeGuids.Contains(Guid))
                            {
                                const FVector PostScl = Actor->GetActorScale3D();
                                if (FMath::Abs(PostScl.X) > 100000.0f || FMath::Abs(PostScl.Y) > 100000.0f || FMath::Abs(PostScl.Z) > 100000.0f)
                                {
                                    UE_LOG(LogLiveSync, Warning,
                                        TEXT("[FBX][TRANSFORM_WARN] guid=%s reason=extreme_scale scl=(%s)"),
                                        *Guid.ToString(EGuidFormats::Digits),
                                        *PostScl.ToString());
                                }
                                const FVector PostLoc = Actor->GetActorLocation();
                                const float LocMag = PostLoc.Size();
                                if (LocMag > 1000000.0f)
                                {
                                    UE_LOG(LogLiveSync, Warning,
                                        TEXT("[FBX][TRANSFORM_WARN] guid=%s reason=large_location loc=(%s) magnitude=%.1f"),
                                        *Guid.ToString(EGuidFormats::Digits),
                                        *PostLoc.ToString(), LocMag);
                                }
                            }
                        }
                    else
                    {
                        if (ShouldLogVerbose())
                        {
                            UE_LOG(LogLiveSync, Log,
                                TEXT("  BYPASS SetActorTransform guid=%s (attached child)"),
                                *Guid.ToString(EGuidFormats::Digits));
                        }
                    }
                    }
                    else
                    {
                        UE_LOG(LogLiveSync, Error,
                            TEXT("  SKIP SetActorTransform guid=%s (invalid transform)"),
                            *Guid.ToString(EGuidFormats::Digits));
                    }

                    if (ShouldLogVerbose())
                    {
                        UE_LOG(LogLiveSync, Log,
                            TEXT("  END   SetActorTransform guid=%s (attached child)"),
                            *Guid.ToString(EGuidFormats::Digits));
                    }

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

            if (ShouldLogVerbose())
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("  BEGIN SetActorTransform guid=%s (root direct-set)"),
                    *Guid.ToString(EGuidFormats::Digits));
            }

            FTransform RootDirectXForm(
                State.CurrentRotation,
                State.CurrentLocation,
                State.CurrentScale);

            if (ValidateTransform(RootDirectXForm, Guid, TEXT("RootDirectSet")))
            {
                if (ShouldLogVerbose())
                {
                    UE_LOG(LogLiveSync, Log,
                        TEXT("  DO SetActorTransform guid=%s (root direct-set)"),
                        *Guid.ToString(EGuidFormats::Digits));
                }
                // MATSTALL: log the final applied transform.
                if (GEnableVerboseSyncLogs)
                {
                    UE_LOG(LogLiveSync, Log,
                        TEXT("[MATSTALL][UE] transform_applied guid=%s actor=%s loc=(%s) rot=(%s) scl=(%s) interp_mode=direct"),
                        *Guid.ToString(EGuidFormats::Digits),
                        *Actor->GetName(),
                        *RootDirectXForm.GetLocation().ToString(),
                        *RootDirectXForm.GetRotation().ToString(),
                        *RootDirectXForm.GetScale3D().ToString());
                }
                if (!CVarLiveSyncBypassSetActorTransform.GetValueOnGameThread())
                {
                    // Diagnostic: rotation pipeline before SetActorTransform
                    const FQuat BeforeActorQuat = Actor->GetActorQuat();
                    const double DeltaAngleRad = State.TargetRotation.AngularDistance(RootDirectXForm.GetRotation());
            if (bEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("[SAT_DIAG][direct] frame=%llu guid=%s"
                         " netTargetQuat=(%.4f,%.4f,%.4f,%.4f)"
                         " netTargetRot=(%.1f,%.1f,%.1f)"
                         " actorQuatBefore=(%.4f,%.4f,%.4f,%.4f)"
                         " appliedQuat=(%.4f,%.4f,%.4f,%.4f)"
                         " deltaAngleDeg=%.2f"),
                    GFrameCounter,
                    *Guid.ToString(EGuidFormats::Digits),
                    State.TargetRotation.X, State.TargetRotation.Y, State.TargetRotation.Z, State.TargetRotation.W,
                    State.TargetRotation.Rotator().Pitch, State.TargetRotation.Rotator().Yaw, State.TargetRotation.Rotator().Roll,
                    BeforeActorQuat.X, BeforeActorQuat.Y, BeforeActorQuat.Z, BeforeActorQuat.W,
                    RootDirectXForm.GetRotation().X, RootDirectXForm.GetRotation().Y, RootDirectXForm.GetRotation().Z, RootDirectXForm.GetRotation().W,
                    FMath::RadiansToDegrees(DeltaAngleRad));
            }

                    Actor->SetActorTransform(RootDirectXForm);
                    DiagBasis_CameraOneShot(Actor, Guid);
                    // Stage 7G.4: camera diagnostic marker
                    if (bEnableVerboseSyncLogs && Actor->IsA(ACameraActor::StaticClass()))
                    {
                        UE_LOG(LogLiveSync, Log,
                            TEXT("[CAMERA][TRANSFORM_APPLY] guid=%s actor=%s"),
                            *Guid.ToString(EGuidFormats::Digits),
                            *Actor->GetName());
                    }
                    // Phase 10J.5D.5: TRANSFORM_WARN for FBX-authoritative actors
                    if (FBXAuthoritativeGuids.Contains(Guid))
                    {
                        const FVector PostScl = Actor->GetActorScale3D();
                        if (FMath::Abs(PostScl.X) > 100000.0f || FMath::Abs(PostScl.Y) > 100000.0f || FMath::Abs(PostScl.Z) > 100000.0f)
                        {
                            UE_LOG(LogLiveSync, Warning,
                                TEXT("[FBX][TRANSFORM_WARN] guid=%s reason=extreme_scale scl=(%s)"),
                                *Guid.ToString(EGuidFormats::Digits),
                                *PostScl.ToString());
                        }
                        const FVector PostLoc = Actor->GetActorLocation();
                        const float LocMag = PostLoc.Size();
                        if (LocMag > 1000000.0f)
                        {
                            UE_LOG(LogLiveSync, Warning,
                                TEXT("[FBX][TRANSFORM_WARN] guid=%s reason=large_location loc=(%s) magnitude=%.1f"),
                                *Guid.ToString(EGuidFormats::Digits),
                                *PostLoc.ToString(), LocMag);
                        }
                    }
                }
                else
                {
                    if (ShouldLogVerbose())
                    {
                        UE_LOG(LogLiveSync, Log,
                            TEXT("  BYPASS SetActorTransform guid=%s (root direct-set)"),
                            *Guid.ToString(EGuidFormats::Digits));
                    }
                }
            }
            else
            {
                UE_LOG(LogLiveSync, Error,
                    TEXT("  SKIP SetActorTransform guid=%s (invalid transform)"),
                    *Guid.ToString(EGuidFormats::Digits));
            }

            if (ShouldLogVerbose())
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("  END   SetActorTransform guid=%s (root direct-set)"),
                    *Guid.ToString(EGuidFormats::Digits));
            }

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

            if (ShouldLogVerbose())
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("  BEGIN SetActorTransform guid=%s (root snap)"),
                    *Guid.ToString(EGuidFormats::Digits));
            }

            FTransform RootSnapXForm(
                State.CurrentRotation,
                State.CurrentLocation,
                State.CurrentScale);

            if (ValidateTransform(RootSnapXForm, Guid, TEXT("RootSnap")))
            {
                if (ShouldLogVerbose())
                {
                    UE_LOG(LogLiveSync, Log,
                        TEXT("  DO SetActorTransform guid=%s (root snap)"),
                        *Guid.ToString(EGuidFormats::Digits));
                }
                // MATSTALL: log the final applied transform.
                if (GEnableVerboseSyncLogs)
                {
                    UE_LOG(LogLiveSync, Log,
                        TEXT("[MATSTALL][UE] transform_applied guid=%s actor=%s loc=(%s) rot=(%s) scl=(%s) interp_mode=snap"),
                        *Guid.ToString(EGuidFormats::Digits),
                        *Actor->GetName(),
                        *RootSnapXForm.GetLocation().ToString(),
                        *RootSnapXForm.GetRotation().ToString(),
                        *RootSnapXForm.GetScale3D().ToString());
                }
                if (!CVarLiveSyncBypassSetActorTransform.GetValueOnGameThread())
                {
                    // Diagnostic: rotation pipeline before SetActorTransform
                    const FQuat BeforeActorQuat = Actor->GetActorQuat();
                    const double DeltaAngleRad = State.TargetRotation.AngularDistance(RootSnapXForm.GetRotation());
            if (bEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("[SAT_DIAG][snap] frame=%llu guid=%s"
                         " netTargetQuat=(%.4f,%.4f,%.4f,%.4f)"
                         " netTargetRot=(%.1f,%.1f,%.1f)"
                         " actorQuatBefore=(%.4f,%.4f,%.4f,%.4f)"
                         " appliedQuat=(%.4f,%.4f,%.4f,%.4f)"
                         " deltaAngleDeg=%.2f"),
                    GFrameCounter,
                    *Guid.ToString(EGuidFormats::Digits),
                    State.TargetRotation.X, State.TargetRotation.Y, State.TargetRotation.Z, State.TargetRotation.W,
                    State.TargetRotation.Rotator().Pitch, State.TargetRotation.Rotator().Yaw, State.TargetRotation.Rotator().Roll,
                    BeforeActorQuat.X, BeforeActorQuat.Y, BeforeActorQuat.Z, BeforeActorQuat.W,
                    RootSnapXForm.GetRotation().X, RootSnapXForm.GetRotation().Y, RootSnapXForm.GetRotation().Z, RootSnapXForm.GetRotation().W,
                    FMath::RadiansToDegrees(DeltaAngleRad));
            }

            Actor->SetActorTransform(RootSnapXForm);
                    DiagBasis_CameraOneShot(Actor, Guid);
                    // Phase 10J.5D.5: TRANSFORM_WARN for FBX-authoritative actors
                    if (FBXAuthoritativeGuids.Contains(Guid))
                    {
                        const FVector PostScl = Actor->GetActorScale3D();
                        if (FMath::Abs(PostScl.X) > 100000.0f || FMath::Abs(PostScl.Y) > 100000.0f || FMath::Abs(PostScl.Z) > 100000.0f)
                        {
                            UE_LOG(LogLiveSync, Warning,
                                TEXT("[FBX][TRANSFORM_WARN] guid=%s reason=extreme_scale scl=(%s)"),
                                *Guid.ToString(EGuidFormats::Digits),
                                *PostScl.ToString());
                        }
                        const FVector PostLoc = Actor->GetActorLocation();
                        const float LocMag = PostLoc.Size();
                        if (LocMag > 1000000.0f)
                        {
                            UE_LOG(LogLiveSync, Warning,
                                TEXT("[FBX][TRANSFORM_WARN] guid=%s reason=large_location loc=(%s) magnitude=%.1f"),
                                *Guid.ToString(EGuidFormats::Digits),
                                *PostLoc.ToString(), LocMag);
                        }
                    }
                }
                else
                {
                    if (ShouldLogVerbose())
                    {
                        UE_LOG(LogLiveSync, Log,
                            TEXT("  BYPASS SetActorTransform guid=%s (root snap)"),
                            *Guid.ToString(EGuidFormats::Digits));
                    }
                }
            }
            else
            {
                UE_LOG(LogLiveSync, Error,
                    TEXT("  SKIP SetActorTransform guid=%s (invalid transform)"),
                    *Guid.ToString(EGuidFormats::Digits));
            }

            if (ShouldLogVerbose())
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("  END   SetActorTransform guid=%s (root snap)"),
                    *Guid.ToString(EGuidFormats::Digits));
            }

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

        if (ShouldLogVerbose())
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("  BEGIN SetActorTransform guid=%s (root smooth)"),
                *Guid.ToString(EGuidFormats::Digits));
        }

        FTransform RootSmoothXForm(
            State.CurrentRotation,
            State.CurrentLocation,
            State.CurrentScale);

        if (ValidateTransform(RootSmoothXForm, Guid, TEXT("RootSmooth")))
        {
            if (ShouldLogVerbose())
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("  DO SetActorTransform guid=%s (root smooth)"),
                    *Guid.ToString(EGuidFormats::Digits));
            }
            // MATSTALL: log the final applied transform.
            if (GEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MATSTALL][UE] transform_applied guid=%s actor=%s loc=(%s) rot=(%s) scl=(%s) interp_mode=smooth"),
                    *Guid.ToString(EGuidFormats::Digits),
                    *Actor->GetName(),
                    *RootSmoothXForm.GetLocation().ToString(),
                    *RootSmoothXForm.GetRotation().ToString(),
                    *RootSmoothXForm.GetScale3D().ToString());
            }
            if (!CVarLiveSyncBypassSetActorTransform.GetValueOnGameThread())
            {
                // Diagnostic: rotation pipeline before SetActorTransform
                const FQuat BeforeActorQuat = Actor->GetActorQuat();
                const double DeltaAngleRad = State.TargetRotation.AngularDistance(RootSmoothXForm.GetRotation());
            if (bEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("[SAT_DIAG][smooth] frame=%llu guid=%s"
                         " netTargetQuat=(%.4f,%.4f,%.4f,%.4f)"
                         " netTargetRot=(%.1f,%.1f,%.1f)"
                         " actorQuatBefore=(%.4f,%.4f,%.4f,%.4f)"
                         " appliedQuat=(%.4f,%.4f,%.4f,%.4f)"
                         " deltaAngleDeg=%.2f"),
                    GFrameCounter,
                    *Guid.ToString(EGuidFormats::Digits),
                    State.TargetRotation.X, State.TargetRotation.Y, State.TargetRotation.Z, State.TargetRotation.W,
                    State.TargetRotation.Rotator().Pitch, State.TargetRotation.Rotator().Yaw, State.TargetRotation.Rotator().Roll,
                    BeforeActorQuat.X, BeforeActorQuat.Y, BeforeActorQuat.Z, BeforeActorQuat.W,
                    RootSmoothXForm.GetRotation().X, RootSmoothXForm.GetRotation().Y, RootSmoothXForm.GetRotation().Z, RootSmoothXForm.GetRotation().W,
                    FMath::RadiansToDegrees(DeltaAngleRad));
            }

            Actor->SetActorTransform(RootSmoothXForm);
                DiagBasis_CameraOneShot(Actor, Guid);
                // Phase 10J.5D.5: TRANSFORM_WARN for FBX-authoritative actors
                if (FBXAuthoritativeGuids.Contains(Guid))
                {
                    const FVector PostScl = Actor->GetActorScale3D();
                    if (FMath::Abs(PostScl.X) > 100000.0f || FMath::Abs(PostScl.Y) > 100000.0f || FMath::Abs(PostScl.Z) > 100000.0f)
                    {
                        UE_LOG(LogLiveSync, Warning,
                            TEXT("[FBX][TRANSFORM_WARN] guid=%s reason=extreme_scale scl=(%s)"),
                            *Guid.ToString(EGuidFormats::Digits),
                            *PostScl.ToString());
                    }
                    const FVector PostLoc = Actor->GetActorLocation();
                    const float LocMag = PostLoc.Size();
                    if (LocMag > 1000000.0f)
                    {
                        UE_LOG(LogLiveSync, Warning,
                            TEXT("[FBX][TRANSFORM_WARN] guid=%s reason=large_location loc=(%s) magnitude=%.1f"),
                            *Guid.ToString(EGuidFormats::Digits),
                            *PostLoc.ToString(), LocMag);
                    }
                }
            }
            else
            {
                if (ShouldLogVerbose())
                {
                    UE_LOG(LogLiveSync, Log,
                        TEXT("  BYPASS SetActorTransform guid=%s (root smooth)"),
                        *Guid.ToString(EGuidFormats::Digits));
                }
            }
        }
        else
        {
            UE_LOG(LogLiveSync, Error,
                TEXT("  SKIP SetActorTransform guid=%s (invalid transform)"),
                    *Guid.ToString(EGuidFormats::Digits));
        }

        if (ShouldLogVerbose())
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("  END   SetActorTransform guid=%s (root smooth)"),
                *Guid.ToString(EGuidFormats::Digits));

            UE_LOG(LogLiveSync, Log,
                TEXT("END   transform apply guid=%s"),
                *Guid.ToString(EGuidFormats::Digits));
        }

        InterpCount++;
    }

    if (bEnableVerboseSyncLogs)
    {
        UE_LOG(LogLiveSync, Log, TEXT("END   InterpolateTransforms"));
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

        // Phase 10J.5A: Clean material metadata to prevent stale entries
        MaterialMetadata.Remove(Guid);
        // Phase 10K.1: Clean texture map cache
        MaterialTextureMapCache.Remove(Guid);
        if (GEnableVerboseSyncLogs)
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[MATSTALL][UE] mat_cleanup actor_destroyed guid=%s actor=%s"),
                *Guid.ToString(EGuidFormats::Digits),
                *Actor->GetName());
        }

        // Phase 10J.5E: Remove FBX authority only if the destroyed actor
        // is the FBX-authoritative actor (not a stale procedural actor).
        // Check by comparing with ActorCache entry for this GUID.
        AActor* CachedActor = FindActorFast(Guid);
        if (CachedActor == Actor)
        {
            FBXAuthoritativeGuids.Remove(Guid);
            UE_LOG(LogLiveSync, Log,
                TEXT("[FBX][AUTH] cleanup actor_destroyed guid=%s actor=%s"),
                *Guid.ToString(EGuidFormats::Digits),
                *Actor->GetName());
        }

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
        if (ShouldLogVerbose())
        {
            UE_LOG(
                LogLiveSync,
                Log,
                TEXT("END TRACE: AttachToParent child=%s (depth exceeded)"),
                *Guid.ToString(
                    EGuidFormats::Digits));
        }
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

    if (ShouldLogVerbose())
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("  BEGIN AttachToActor child=%s parent=%s"),
            *Guid.ToString(EGuidFormats::Digits),
            *ParentGuid.ToString(EGuidFormats::Digits));
    }

    Child->AttachToActor(
        Parent,
        FAttachmentTransformRules::
            KeepWorldTransform);

    if (ShouldLogVerbose())
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("  END   AttachToActor child=%s parent=%s"),
            *Guid.ToString(EGuidFormats::Digits),
            *ParentGuid.ToString(EGuidFormats::Digits));
    }

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

    if (PrimitiveType == LSP_Camera)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[CAMERA][SAFE_LIFECYCLE_ENTER] HandleCreateObject guid=%s"),
            *Guid.ToString(EGuidFormats::Digits));
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

        if (GEnableVerboseSyncLogs)
        {
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
        }

        // Validate parsed payload integrity
        if (!Guid.IsValid())
        {
            UE_LOG(LogLiveSync, Error,
                TEXT("[CREATE][DIAG] INVALID GUID (all zero) — aborting"));
            return;
        }

        if (Scale.IsZero() || Scale.X <= 0.0f || Scale.Y <= 0.0f || Scale.Z <= 0.0f)
        {
            if (GEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[CREATE][DIAG] SUSPICIOUS scale=%s — proceeding"),
                    *Scale.ToString());
            }
        }

        if (Location.SizeSquared() > 1e12f)
        {
            if (GEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[CREATE][DIAG] SUSPICIOUS location magnitude=%f — proceeding"),
                    Location.Size());
            }
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
        if (AActor* ExistingActor = FindActorFast(Guid))
        {
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[CREATE][TOMBSTONE] GUID=%s prim=0x%02X — blocked by tombstone (actor still exists)"),
                *Guid.ToString(EGuidFormats::Digits),
                PrimitiveType);
            Stats.DeleteTombstoneRejections.fetch_add(1, std::memory_order_relaxed);
            return;
        }

        // Blender Undo preserves GUIDs.
        // If no live actor exists for the GUID, allow this Create to
        // recreate the object even though a tombstone still exists.
        //
        // This is a protocol heuristic.
        // If PT_CreateObject ever gains per-object ordering metadata,
        // replace this with sequence-based validation.
        RemoveTombstone(Guid);
        Stats.CreateTombstoneRestored.fetch_add(1, std::memory_order_relaxed);

        UE_LOG(LogLiveSync, Log,
            TEXT("[CREATE][TOMBSTONE_RESTORED] GUID=%s prim=0x%02X — tombstone cleared, spawning"),
            *Guid.ToString(EGuidFormats::Digits),
            PrimitiveType);
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

    AActor* NewActor = nullptr;

    // =====================================================
    // Camera spawn path (LSP_Camera = 0x05)
    // E2E.9: Use deferred spawn so frustum guard can be
    // applied before FinishSpawning. This ensures camera
    // components (especially UDrawFrustumComponent) are
    // suppressed before the actor becomes visible to the
    // SceneOutliner.
    // =====================================================

    if (PrimitiveType == LSP_Camera)
    {
        FActorSpawnParameters CamSpawnParams;
        CamSpawnParams.SpawnCollisionHandlingOverride =
            ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
        CamSpawnParams.bHideFromSceneOutliner = true;

        UE_LOG(LogLiveSync, Log,
            TEXT("[CAMERA][SAFE_SPAWN_BEGIN] Spawning ALiveSyncCameraActor (hide-outliner) guid=%s"),
            *Guid.ToString(EGuidFormats::Digits));

        FTransform CamSpawnXForm(
            Rotation,
            Location,
            Scale);

        ALiveSyncCameraActor* Cam =

            World->SpawnActor<ALiveSyncCameraActor>(

                ALiveSyncCameraActor::StaticClass(),

                CamSpawnXForm,

                CamSpawnParams);

        if (Cam)
        {
            // Apply frustum guard after spawn. Camera is hidden from
            // SceneOutliner, but frustum guard prevents the red-screen
            // UDrawFrustumComponent flash on the next render frame.
            ConfigureLiveSyncCameraActor(Cam);
            UE_LOG(LogLiveSync, Log,
                TEXT("[CAMERA][OUTLINER_GUARD] Applied frustum guard post-spawn guid=%s"),
                *Guid.ToString(EGuidFormats::Digits));

            UE_LOG(LogLiveSync, Log,
                TEXT("[CAMERA][E2E10_OUTLINER_HIDE] guid=%s"),
                *Guid.ToString(EGuidFormats::Digits));

            DiagBasis_CameraOneShot(Cam, Guid);

            NewActor = Cam;
        }
    }
    else
    {
        NewActor =

            World->SpawnActor<AActor>(

                AActor::StaticClass(),

                FTransform(
                    Rotation,
                    Location,
                    Scale),

                SpawnParams);
    }

    double SpawnMs =
        FPlatformTime::
        ToMilliseconds64(
            FPlatformTime::Cycles64() -
            SpawnBeginCycles);

    if (!NewActor)
    {
        const FString WorldName = World->GetName();
        const FString ActorClass = AActor::StaticClass()->GetName();

        if (GEnableVerboseSyncLogs)
        {
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
        }

        return;
    }

    if (SpawnMs > 50.0)
    {
        if (GEnableVerboseSyncLogs)
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
    }

    // =====================================================
    // SPAWN SUCCESS DIAGNOSTICS
    // =====================================================

    {
        const FString ActorName = NewActor->GetName();
        const FString ActorClass = NewActor->GetClass()->GetName();
        const FString SpawnWorldName = NewActor->GetWorld() ? NewActor->GetWorld()->GetName() : TEXT("None");
        const FTransform SpawnXForm = NewActor->GetActorTransform();

        if (GEnableVerboseSyncLogs)
        {
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
        }

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

    if (PrimitiveType == LSP_Camera)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[CAMERA][SAFE_CACHE_ADD] guid=%s"),
            *Guid.ToString(EGuidFormats::Digits));

        // Camera is spawned with bHideFromSceneOutliner=true — no
        // Need for deferred active processing (E2E.10 W3 approach).
        // The OUTLINER_HIDE marker confirms the actor is outliner-hidden.
    }

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
        if (GEnableVerboseSyncLogs)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[CREATE][DIAG] REGISTRY guid=%s ActorCache check=%s"),
                *Guid.ToString(EGuidFormats::Digits),
                CacheCheck ? TEXT("FOUND") : TEXT("MISSING"));
        }

        // Immediate post-spawn actor destruction check
        if (CacheCheck && CacheCheck->IsPendingKillPending())
        {
            if (GEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Error,
                    TEXT("[CREATE][DIAG] ACTOR PENDING DESTROY IMMEDIATELY AFTER SPAWN guid=%s — cleanup race!"),
                    *Guid.ToString(EGuidFormats::Digits));
            }
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
        if (PostAttachActor && GEnableVerboseSyncLogs)
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
    // Stage 7G.4: LSP_Camera (0x05) is now a valid type.
    // Unknown types > LSP_Camera still default to Cube.
    // =====================================================

    if (PrimitiveType > LSP_Camera)
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

    // =====================================================
    // Root component setup by primitive type
    // =====================================================

    UStaticMesh* ResolvedMesh = nullptr;

    if (PrimitiveType == LSP_Camera)
    {
        // Camera: ACameraActor already has UCameraComponent as root
        // with Movable mobility (set in constructor). Frustum guard
        // was applied right after SpawnActor (L8043).
        // All component setup is complete — no redundant calls needed.
        // Skipping redundant SetRootComponent / RegisterComponent
        // avoids the "Already registered" engine warning and prevents
        // any re-entrant notifications during end-of-frame processing.
        ALiveSyncCameraActor* CamActor = Cast<ALiveSyncCameraActor>(NewActor);
        if (CamActor && CamActor->GetCameraComponent())
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[CAMERA][SAFE_SPAWN_READY] ALiveSyncCameraActor ready guid=%s"),
                *Guid.ToString(EGuidFormats::Digits));

            UE_LOG(LogLiveSync, Log,
                TEXT("[CAMERA][CREATE] Spawned ALiveSyncCameraActor guid=%s"),
                *Guid.ToString(EGuidFormats::Digits));
        }
    }
    else
    {
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

        ResolvedMesh = GetPrimitiveMesh(PrimitiveType);

        if (ResolvedMesh)
        {
            MeshComp->SetStaticMesh(
                ResolvedMesh);

            if (GEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[CREATE][DIAG] PRIMITIVE guid=%s type=0x%02X mesh=%s"),
                    *Guid.ToString(EGuidFormats::Digits),
                    PrimitiveType,
                    *ResolvedMesh->GetName());
            }
        }
        else
        {
            if (GEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Error,
                    TEXT("[CREATE][DIAG] PRIMITIVE RESOLVE FAILED guid=%s type=0x%02X — no mesh assigned, actor will be invisible!"),
                    *Guid.ToString(EGuidFormats::Digits),
                    PrimitiveType);
            }
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

        if (RegisterMs > 50.0 && GEnableVerboseSyncLogs)
        {
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("[CREATE][DIAG] STALL: RegisterComponent took %.1fms "
                     "for guid=%s"),
                RegisterMs,
                *Guid.ToString(
                    EGuidFormats::Digits));
        }

        if (GEnableVerboseSyncLogs)
        {
            UE_LOG(
                LogLiveSync,
                Warning,
                TEXT("[CREATE][DIAG] REGISTER COMPLETE guid=%s mesh=%s regMs=%.1f"),
                *Guid.ToString(EGuidFormats::Digits),
                ResolvedMesh ? *ResolvedMesh->GetName() : TEXT("NULL"),
                RegisterMs);
        }
    }

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

    // Phase 10J.5E: Remove FBX authority on legacy delete
    FBXAuthoritativeGuids.Remove(Guid);
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
    // APPLY ATTACHMENT — E2E.3: Guarded by SafeAttachLiveSyncActor
    // =====================================================
    // Replaces raw AttachToActor (which was not going through
    // AttachToParent's cycle detection). Use SafeAttachLiveSyncActor
    // for actor-pointer-level validation.
    // =====================================================

    const bool bAttached = SafeAttachLiveSyncActor(
        ChildActor, ParentActor, ChildGuid, ParentGuid);

    if (!bAttached)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[HIERARCHY][ATTACH] SKIPPED — safety guard rejected "
                 "child=%s parent=%s"),
            *ChildGuid.ToString(EGuidFormats::Digits),
            *ParentGuid.ToString(EGuidFormats::Digits));
        // Keep world transform intact (no attach = world transform preserved).
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

    UE_LOG(LogLiveSync, Log,
        TEXT("[HIERARCHY][ATTACH] Attached child=%s parent=%s"),
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
// SEQUENCER OP (Phase 7E — PT_SequencerOp 0x18)
// =========================================================
// Applies CREATE_SEQUENCE, SET_FRAME_RANGE, and CLEAR_SEQUENCE
// to the subsystem-owned transient ULevelSequence.
//
// ADD_POSSESSABLE, REMOVE_POSSESSABLE, and ADD_CAMERA_CUT are
// not yet implemented (deferred to later stages).
//
// Ownership:
//   - The ULevelSequence is asset-backed (Stage 10B.1).
//   - Package: /Game/UELiveSync/Sequences/LS_UELiveSync_Runtime
//   - CLEAR_SEQUENCE clears only subsystem-owned state.
//   - Does NOT delete user assets or destroy actors.
// =========================================================

// =========================================================
// STAGE 10B.1 — GetOrCreateLiveSyncLevelSequence
// =========================================================
ULevelSequence*
UUELiveSyncSubsystem::GetOrCreateLiveSyncLevelSequence()
{
    if (LiveSyncSequence.IsValid())
    {
        return LiveSyncSequence.Get();
    }

#if WITH_EDITOR
    ULevelSequence* AssetSeq = GetOrCreateLiveSyncLevelSequenceAsset();
    if (!AssetSeq)
    {
        return nullptr;
    }

    LiveSyncSequence = AssetSeq;
    bHasLiveSyncSequence = true;
    return AssetSeq;
#else
    UE_LOG(LogLiveSync, Warning,
        TEXT("[SEQ][ASSET_FAIL] WITH_EDITOR is disabled — cannot create asset"));
    return nullptr;
#endif
}

void UUELiveSyncSubsystem::HandleSequencerOp(
    const FSequencerOpHeader& Header,
    const uint8* PayloadPtr,
    int32 PayloadSize)
{
    CHECK_GAME_THREAD();

    switch (Header.Opcode)
    {
    case SEQUENCER_OP_CREATE_SEQUENCE:
    {
        if (PayloadSize < SEQUENCER_OP_CREATE_SEQUENCE_PAYLOAD_SIZE)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[SEQOP] CREATE_SEQUENCE truncated payload"));
            return;
        }

        FSequencerOpCreateSequencePayload Payload;
        FMemory::Memcpy(&Payload, PayloadPtr, sizeof(Payload));

        // Get or create the asset-backed sequence (Stage 10B.1)
        ULevelSequence* Seq = GetOrCreateLiveSyncLevelSequence();
        if (!Seq)
        {
            UE_LOG(LogLiveSync, Error,
                TEXT("[SEQOP] CREATE_SEQUENCE: GetOrCreateLiveSyncLevelSequence() returned null"));
            return;
        }

        UMovieScene* MovieScene = Seq->GetMovieScene();
        if (!MovieScene)
        {
            UE_LOG(LogLiveSync, Error,
                TEXT("[SEQOP] CREATE_SEQUENCE: GetMovieScene() returned null"));
            return;
        }

        // Reset/clear previous runtime data before applying new data
        // Only clear tracks/possessables — do NOT delete user assets
        const int32 OldTrackCount = MovieScene->GetTracks().Num();
        const int32 OldPossessableCount = MovieScene->GetPossessableCount();

        // Remove all tracks (clear runtime state, keep asset)
        TArray<UMovieSceneTrack*> Tracks = MovieScene->GetTracks();
        for (UMovieSceneTrack* Track : Tracks)
        {
            if (Track)
            {
                MovieScene->RemoveTrack(*Track);
            }
        }

        LiveSyncGuidToSequencerBinding.Empty();
        PendingSequencerBindings.Empty();

        UE_LOG(LogLiveSync, Log,
            TEXT("[SEQ][RESET] Cleared %d tracks from sequence (possessable count: %d)"),
            OldTrackCount, OldPossessableCount);

        // Set frame range and display rate from payload
        MovieScene->SetPlaybackRange(
            FFrameNumber(Payload.FrameStart),
            Payload.FrameEnd - Payload.FrameStart + 1);

        MovieScene->SetDisplayRate(
            FFrameRate(Payload.FPSNum, Payload.FPSDen));

        // Store sequence and derived state
        LiveSyncSequence = Seq;
        bHasLiveSyncSequence = true;
        LiveSyncSequenceFrameStart = Payload.FrameStart;
        LiveSyncSequenceFrameEnd   = Payload.FrameEnd;
        LiveSyncSequenceFPSNum     = Payload.FPSNum;
        LiveSyncSequenceFPSDen     = Payload.FPSDen;

        UE_LOG(LogLiveSync, Log,
            TEXT("[SEQOP] CREATE_SEQUENCE: %d-%d %d/%d fps"),
            Payload.FrameStart, Payload.FrameEnd,
            Payload.FPSNum, Payload.FPSDen);
        break;
    }

    case SEQUENCER_OP_SET_FRAME_RANGE:
    {
        if (PayloadSize < SEQUENCER_OP_SET_FRAME_RANGE_PAYLOAD_SIZE)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[SEQOP] SET_FRAME_RANGE truncated payload"));
            return;
        }

        FSequencerOpSetFrameRangePayload Payload;
        FMemory::Memcpy(&Payload, PayloadPtr, sizeof(Payload));

        if (bHasLiveSyncSequence && LiveSyncSequence.IsValid())
        {
            UMovieScene* MovieScene = LiveSyncSequence->GetMovieScene();
            if (MovieScene)
            {
                MovieScene->SetPlaybackRange(
                    FFrameNumber(Payload.FrameStart),
                    Payload.FrameEnd - Payload.FrameStart + 1);

                MovieScene->SetDisplayRate(
                    FFrameRate(Payload.FPSNum, Payload.FPSDen));
            }
        }
        else
        {
            // No sequence exists — store pending desired range.
            // A subsequent CREATE_SEQUENCE will use these values.
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[SEQOP] SET_FRAME_RANGE without sequence: storing pending %d-%d %d/%d"),
                Payload.FrameStart, Payload.FrameEnd,
                Payload.FPSNum, Payload.FPSDen);
        }

        // Always update the stored range state (exists or pending)
        LiveSyncSequenceFrameStart = Payload.FrameStart;
        LiveSyncSequenceFrameEnd   = Payload.FrameEnd;
        LiveSyncSequenceFPSNum     = Payload.FPSNum;
        LiveSyncSequenceFPSDen     = Payload.FPSDen;

        UE_LOG(LogLiveSync, Log,
            TEXT("[SEQOP] SET_FRAME_RANGE: %d-%d %d/%d fps"),
            Payload.FrameStart, Payload.FrameEnd,
            Payload.FPSNum, Payload.FPSDen);
        break;
    }

    case SEQUENCER_OP_CLEAR_SEQUENCE:
    {
        // Clear subsystem-owned sequence state only.
        // Does NOT delete user assets or destroy actors.
        LiveSyncSequence = nullptr;
        bHasLiveSyncSequence = false;
        LiveSyncSequenceFrameStart = 0;
        LiveSyncSequenceFrameEnd   = 0;
        LiveSyncSequenceFPSNum     = 0;
        LiveSyncSequenceFPSDen     = 1;

        LiveSyncGuidToSequencerBinding.Empty();
        PendingSequencerBindings.Empty();

        UE_LOG(LogLiveSync, Log,
            TEXT("[SEQOP] CLEAR_SEQUENCE: subsystem state + bindings cleared"));
        break;
    }

    case SEQUENCER_OP_ADD_POSSESSABLE:
    {
        if (!bHasLiveSyncSequence || !LiveSyncSequence.IsValid())
        {
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[SEQOP] ADD_POSSESSABLE: no sequence — deferred"));
            break;
        }

        if (PayloadSize < SEQUENCER_OP_ADD_POSSESSABLE_PAYLOAD_SIZE)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[SEQOP] ADD_POSSESSABLE truncated payload"));
            return;
        }

        FSequencerOpAddPossessablePayload Payload;
        FMemory::Memcpy(&Payload, PayloadPtr, sizeof(Payload));

        // Idempotency: if already bound, increment duplicate counter and skip
        if (LiveSyncGuidToSequencerBinding.Contains(Payload.ObjectGuid))
        {
            Stats.SequencerPossessablesDuplicate.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[SEQOP] ADD_POSSESSABLE: duplicate guid %s — skipping"),
                *Payload.ObjectGuid.ToString());
            break;
        }

        // Resolve actor via ActorCache
        AActor* Actor = FindActorFast(Payload.ObjectGuid);
        if (!Actor)
        {
            Stats.SequencerPossessablesMissingActor.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[SEQOP] ADD_POSSESSABLE: actor not found for %s — deferred"),
                *Payload.ObjectGuid.ToString());

            FPendingSequencerBinding Pending;
            Pending.LiveSyncGuid = Payload.ObjectGuid;
            Pending.BindingType  = Payload.BindingType;
            Pending.Timestamp    = Header.Timestamp;
            PendingSequencerBindings.Add(Pending);
            break;
        }

        UMovieScene* MovieScene = LiveSyncSequence->GetMovieScene();
        if (!MovieScene)
        {
            UE_LOG(LogLiveSync, Error,
                TEXT("[SEQOP] ADD_POSSESSABLE: GetMovieScene() returned null"));
            break;
        }

        // Add possessable to MovieScene
        FGuid BindingGuid = MovieScene->AddPossessable(
            Actor->GetName(),
            Actor->GetClass());

        if (!BindingGuid.IsValid())
        {
            UE_LOG(LogLiveSync, Error,
                TEXT("[SEQOP] ADD_POSSESSABLE: AddPossessable failed for %s"),
                *Actor->GetName());
            break;
        }

        // Bind possessable to the actor
        LiveSyncSequence->BindPossessableObject(
            BindingGuid,
            *Actor,
            Actor->GetWorld());

        // Store mapping
        LiveSyncGuidToSequencerBinding.Add(Payload.ObjectGuid, BindingGuid);

        Stats.SequencerPossessablesAdded.fetch_add(1, std::memory_order_relaxed);

        UE_LOG(LogLiveSync, Log,
            TEXT("[SEQOP] ADD_POSSESSABLE: %s → guid=%s binding=%s type=%d"),
            *Actor->GetName(),
            *Payload.ObjectGuid.ToString(),
            *BindingGuid.ToString(),
            Payload.BindingType);
        break;
    }

    case SEQUENCER_OP_REMOVE_POSSESSABLE:
    {
        if (PayloadSize < SEQUENCER_OP_REMOVE_POSSESSABLE_PAYLOAD_SIZE)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[SEQOP] REMOVE_POSSESSABLE truncated payload"));
            return;
        }

        FSequencerOpRemovePossessablePayload Payload;
        FMemory::Memcpy(&Payload, PayloadPtr, sizeof(Payload));

        FGuid* FoundBinding = LiveSyncGuidToSequencerBinding.Find(Payload.ObjectGuid);
        if (!FoundBinding)
        {
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[SEQOP] REMOVE_POSSESSABLE: no binding found for %s — safe no-op"),
                *Payload.ObjectGuid.ToString());
            break;
        }

        // Remove possessable from MovieScene (does not destroy actor)
        if (bHasLiveSyncSequence && LiveSyncSequence.IsValid())
        {
            UMovieScene* MovieScene = LiveSyncSequence->GetMovieScene();
            if (MovieScene)
            {
                MovieScene->RemovePossessable(*FoundBinding);
            }
        }

        // Remove local mapping
        LiveSyncGuidToSequencerBinding.Remove(Payload.ObjectGuid);

        Stats.SequencerPossessablesRemoved.fetch_add(1, std::memory_order_relaxed);

        UE_LOG(LogLiveSync, Log,
            TEXT("[SEQOP] REMOVE_POSSESSABLE: guid=%s binding=%s removed"),
            *Payload.ObjectGuid.ToString(),
            *FoundBinding->ToString());
        break;
    }

    case SEQUENCER_OP_ADD_CAMERA_CUT:
    {
        if (!bHasLiveSyncSequence || !LiveSyncSequence.IsValid())
        {
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[SEQOP] ADD_CAMERA_CUT: no sequence — safe no-op"));
            break;
        }

        if (PayloadSize < SEQUENCER_OP_ADD_CAMERA_CUT_PAYLOAD_SIZE)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[SEQOP] ADD_CAMERA_CUT truncated payload"));
            return;
        }

        FSequencerOpAddCameraCutPayload Payload;
        FMemory::Memcpy(&Payload, PayloadPtr, sizeof(Payload));

        // Resolve camera binding from the possessable map
        FGuid* FoundBinding = LiveSyncGuidToSequencerBinding.Find(Payload.CameraGuid);
        if (!FoundBinding)
        {
            Stats.SequencerCameraCutsMissingBinding.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[SEQOP] ADD_CAMERA_CUT: no binding found for %s — safe no-op"),
                *Payload.CameraGuid.ToString());
            break;
        }

        // Validate frame range
        if (Payload.FrameEnd <= Payload.FrameStart)
        {
            Stats.SequencerCameraCutsMalformedRange.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[SEQOP] ADD_CAMERA_CUT: invalid range %d-%d for %s"),
                Payload.FrameStart, Payload.FrameEnd,
                *Payload.CameraGuid.ToString());
            break;
        }

        UMovieScene* MovieScene = LiveSyncSequence->GetMovieScene();
        if (!MovieScene)
        {
            UE_LOG(LogLiveSync, Error,
                TEXT("[SEQOP] ADD_CAMERA_CUT: GetMovieScene() returned null"));
            break;
        }

        // Get or create CameraCutTrack
        UMovieSceneCameraCutTrack* CameraCutTrack = Cast<UMovieSceneCameraCutTrack>(
            MovieScene->GetCameraCutTrack());
        if (!CameraCutTrack)
        {
            CameraCutTrack = Cast<UMovieSceneCameraCutTrack>(
                MovieScene->AddCameraCutTrack(UMovieSceneCameraCutTrack::StaticClass()));
        }

        if (!CameraCutTrack)
        {
            UE_LOG(LogLiveSync, Error,
                TEXT("[SEQOP] ADD_CAMERA_CUT: failed to create CameraCutTrack"));
            break;
        }

        // Create binding ID from the possessable binding GUID
        FMovieSceneObjectBindingID BindingID(
            (UE::MovieScene::FRelativeObjectBindingID(*FoundBinding)));

        // Add camera cut section
        UMovieSceneCameraCutSection* CutSection = CameraCutTrack->AddNewCameraCut(
            BindingID, FFrameNumber(Payload.FrameStart));

        if (!CutSection)
        {
            UE_LOG(LogLiveSync, Error,
                TEXT("[SEQOP] ADD_CAMERA_CUT: AddNewCameraCut failed"));
            break;
        }

        // Set the full range of the cut section
        CutSection->SetRange(
            TRange<FFrameNumber>(
                FFrameNumber(Payload.FrameStart),
                FFrameNumber(Payload.FrameEnd)));

        Stats.SequencerCameraCutsAdded.fetch_add(1, std::memory_order_relaxed);

        UE_LOG(LogLiveSync, Log,
            TEXT("[SEQOP] ADD_CAMERA_CUT: guid=%s binding=%s range=%d-%d"),
            *Payload.CameraGuid.ToString(),
            *FoundBinding->ToString(),
            Payload.FrameStart,
            Payload.FrameEnd);
        break;
    }

    default:
        // Should not reach here (validated before call)
        break;
    }
}


// =========================================================
// KEYFRAME REPLICATION — APPLY (Phase 7E Stage 9)
// =========================================================
// For each key entry in the validated packet:
// 1. Resolve LiveSync object GUID → MovieScene binding GUID
// 2. If missing binding → log + counter, skip (safe no-op)
// 3. Map wire channel index (0-8) to UE transform channel:
//    0=LocX, 1=LocY, 2=LocZ, 3=RotX, 4=RotY, 5=RotZ,
//    6=ScaleX, 7=ScaleY, 8=ScaleZ
//    Any channel > 8 → log + counter, skip
// 4. Find/create UMovieScene3DTransformTrack for binding
// 5. Find/create UMovieScene3DTransformSection in track
// 6. Insert key at frame with value via AddLinearKey
//
// Safety: only mutates subsystem-owned transient LevelSequence.
// Never destroys actors or mutates external sequences.
// =========================================================

void UUELiveSyncSubsystem::
HandleKeyframe(
    const FKeyframeHeader& Header,
    const uint8* PayloadPtr,
    int32 PayloadSize)
{
    CHECK_GAME_THREAD();

    // Store header state (always, even if no sequence)
    LastKeyframeSequence = Header.Sequence;
    LastKeyframeTimestamp = Header.Timestamp;
    bHasKeyframeState = true;

    // No active sequence — safe no-op (cannot mutate)
    if (!bHasLiveSyncSequence || !LiveSyncSequence.IsValid())
    {
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[KEYFRAME] No active sequence — skipping %d keys"),
            Header.KeyCount);
        return;
    }

    UMovieScene* MovieScene = LiveSyncSequence->GetMovieScene();
    if (!MovieScene)
    {
        UE_LOG(LogLiveSync, Error,
            TEXT("[KEYFRAME] GetMovieScene() returned null"));
        return;
    }

    int32 AppliedKeys       = 0;
    int32 MissingBinding    = 0;
    int32 UnsupportedChannel = 0;

    const uint8* EntryPtr = PayloadPtr;
    int32 Remaining = PayloadSize;

    for (uint8 i = 0; i < Header.KeyCount; i++)
    {
        if (Remaining < KEYFRAME_ENTRY_SIZE)
            break;

        const FKeyframeEntry* Entry =
            reinterpret_cast<const FKeyframeEntry*>(EntryPtr);

        // Step 1: Resolve LiveSync GUID → MovieScene binding
        FGuid* FoundBinding = LiveSyncGuidToSequencerBinding.Find(Entry->ObjectGUID);
        if (!FoundBinding)
        {
            const bool bIsVisibility = (Entry->ChannelIndex == 9 || Entry->ChannelIndex == 10);
            if (bIsVisibility)
            {
                Stats.KeyframeMissingBinding.fetch_add(1, std::memory_order_relaxed);
                MissingBinding++;
                UE_LOG(LogLiveSync, Verbose,
                    TEXT("[KEYFRAME][VISIBILITY] missing binding channel=%d guid=%s — skipping"),
                    Entry->ChannelIndex, *Entry->ObjectGUID.ToString());
            }
            else
            {
                Stats.KeyframeMissingBinding.fetch_add(1, std::memory_order_relaxed);
                MissingBinding++;
                UE_LOG(LogLiveSync, Verbose,
                    TEXT("[KEYFRAME] No binding for %s ch=%d — skipping"),
                    *Entry->ObjectGUID.ToString(), Entry->ChannelIndex);
            }
            EntryPtr += KEYFRAME_ENTRY_SIZE;
            Remaining -= KEYFRAME_ENTRY_SIZE;
            continue;
        }

        // Step 2: Channel dispatch — visibility (9/10) vs transform (0-8) vs unsupported
        if (Entry->ChannelIndex == 9 || Entry->ChannelIndex == 10)
        {
            // Stale sequence rejection
            if (!bHasLiveSyncSequence || !LiveSyncSequence.IsValid())
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[KEYFRAME][VISIBILITY] stale sequence rejected for %s (ch %d)"),
                    *Entry->ObjectGUID.ToString(), Entry->ChannelIndex);
                EntryPtr += KEYFRAME_ENTRY_SIZE;
                Remaining -= KEYFRAME_ENTRY_SIZE;
                continue;
            }

            // Visibility keyframe (channels 9=hide_viewport, 10=hide_render)
            // Apply to UMovieSceneBoolTrack for the object binding.

            // Find or create bool track
            UMovieSceneBoolTrack* BoolTrack =
                MovieScene->FindTrack<UMovieSceneBoolTrack>(*FoundBinding);
            if (!BoolTrack)
            {
                BoolTrack = MovieScene->AddTrack<UMovieSceneBoolTrack>(*FoundBinding);
                Stats.KeyframeVisibilityTrackCreated.fetch_add(1, std::memory_order_relaxed);
                UE_LOG(LogLiveSync, Log,
                    TEXT("[KEYFRAME][BOOL_TRACK_CREATE] guid=%s channel=%d"),
                    *Entry->ObjectGUID.ToString(), Entry->ChannelIndex);
            }

            // Find or create bool section
            UMovieSceneBoolSection* BoolSection = nullptr;
            const TArray<UMovieSceneSection*>& BoolSections = BoolTrack->GetAllSections();
            if (BoolSections.Num() > 0)
            {
                BoolSection = Cast<UMovieSceneBoolSection>(BoolSections[0]);
            }
            if (!BoolSection)
            {
                UMovieSceneSection* NewSection = BoolTrack->CreateNewSection();
                if (NewSection)
                {
                    BoolTrack->AddSection(*NewSection);
                    BoolSection = Cast<UMovieSceneBoolSection>(NewSection);
                    Stats.KeyframeVisibilitySectionCreated.fetch_add(1, std::memory_order_relaxed);
                    UE_LOG(LogLiveSync, Log,
                        TEXT("[KEYFRAME][BOOL_SECTION_CREATE] guid=%s channel=%d"),
                        *Entry->ObjectGUID.ToString(), Entry->ChannelIndex);
                }
            }

            if (BoolSection)
            {
                // Convert value: 0.0 → false, non-zero → true
                const bool bValue = (Entry->Value != 0.0f);
                BoolSection->GetChannel().AddKeys(
                    { FFrameNumber(Entry->Frame) },
                    { bValue });
                Stats.KeyframeVisibilityKeysApplied.fetch_add(1, std::memory_order_relaxed);
                AppliedKeys++;
                UE_LOG(LogLiveSync, Log,
                    TEXT("[KEYFRAME][BOOL_KEY] guid=%s channel=%d value=%d frame=%d"),
                    *Entry->ObjectGUID.ToString(), Entry->ChannelIndex,
                    bValue ? 1 : 0, Entry->Frame);
                UE_LOG(LogLiveSync, Log,
                    TEXT("[KEYFRAME][BOOL_APPLY] guid=%s channel=%d value=%d frame=%d"),
                    *Entry->ObjectGUID.ToString(), Entry->ChannelIndex,
                    bValue ? 1 : 0, Entry->Frame);
                UE_LOG(LogLiveSync, Log,
                    TEXT("[KEYFRAME][VISIBILITY] applied channel=%d guid=%s value=%d frame=%d"),
                    Entry->ChannelIndex, *Entry->ObjectGUID.ToString(),
                    bValue ? 1 : 0, Entry->Frame);
            }
            else
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[KEYFRAME] Failed to create bool section for %s (ch %d) — skipping"),
                    *Entry->ObjectGUID.ToString(), Entry->ChannelIndex);
            }

            EntryPtr += KEYFRAME_ENTRY_SIZE;
            Remaining -= KEYFRAME_ENTRY_SIZE;
            continue;
        }

        if (Entry->ChannelIndex > 10)
        {
            Stats.KeyframeVisibilityUnsupported.fetch_add(1, std::memory_order_relaxed);
            UnsupportedChannel++;
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[KEYFRAME][BOOL_UNSUPPORTED] channel=%d guid=%s — skipping"),
                Entry->ChannelIndex, *Entry->ObjectGUID.ToString());
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[KEYFRAME][VISIBILITY] unsupported channel=%d guid=%s — skipping"),
                Entry->ChannelIndex, *Entry->ObjectGUID.ToString());
            EntryPtr += KEYFRAME_ENTRY_SIZE;
            Remaining -= KEYFRAME_ENTRY_SIZE;
            continue;
        }

        // Step 3: Find or create 3D Transform track for this binding
        UMovieScene3DTransformTrack* TransformTrack =
            MovieScene->FindTrack<UMovieScene3DTransformTrack>(*FoundBinding);
        if (!TransformTrack)
        {
            TransformTrack = MovieScene->AddTrack<UMovieScene3DTransformTrack>(*FoundBinding);
            Stats.KeyframeTrackCreated.fetch_add(1, std::memory_order_relaxed);
        }

        // Step 4: Find or create section in track
        UMovieScene3DTransformSection* TransformSection = nullptr;
        if (TransformTrack->GetAllSections().Num() > 0)
        {
            TransformSection = Cast<UMovieScene3DTransformSection>(
                TransformTrack->GetAllSections()[0]);
        }
        if (!TransformSection)
        {
            UMovieSceneSection* NewSection = TransformTrack->CreateNewSection();
            if (NewSection)
            {
                TransformTrack->AddSection(*NewSection);
                TransformSection = Cast<UMovieScene3DTransformSection>(NewSection);
                Stats.KeyframeSectionCreated.fetch_add(1, std::memory_order_relaxed);
            }
        }
        if (!TransformSection)
        {
            UE_LOG(LogLiveSync, Error,
                TEXT("[KEYFRAME] Failed to create section for binding %s"),
                *FoundBinding->ToString());
            EntryPtr += KEYFRAME_ENTRY_SIZE;
            Remaining -= KEYFRAME_ENTRY_SIZE;
            continue;
        }

        // Step 5: Insert key on the appropriate transform channel
        FMovieSceneChannelProxy& ChannelProxy = TransformSection->GetChannelProxy();
        FMovieSceneDoubleChannel* Channel =
            ChannelProxy.GetChannel<FMovieSceneDoubleChannel>(Entry->ChannelIndex);
        if (Channel)
        {
            Channel->AddLinearKey(
                FFrameNumber(Entry->Frame),
                static_cast<double>(Entry->Value));
            AppliedKeys++;
        }
        else
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[KEYFRAME] GetChannel(%d) returned null for binding %s"),
                Entry->ChannelIndex, *FoundBinding->ToString());
        }

        EntryPtr += KEYFRAME_ENTRY_SIZE;
        Remaining -= KEYFRAME_ENTRY_SIZE;
    }

    // Update counters
    Stats.KeyframeKeysApplied.fetch_add(AppliedKeys, std::memory_order_relaxed);
    Stats.KeyframePacketsApplied.fetch_add(1, std::memory_order_relaxed);

    UE_LOG(LogLiveSync, Log,
        TEXT("[KEYFRAME] Applied seq=%u count=%d applied=%d miss=%d unsupp=%d"),
        Header.Sequence, Header.KeyCount,
        AppliedKeys, MissingBinding, UnsupportedChannel);

    // Persist the sequence after successful keyframe application (Stage 10C.1)
    if (AppliedKeys > 0 && LiveSyncSequence.IsValid())
    {
#if WITH_EDITOR
        SaveLiveSyncLevelSequenceAsset(LiveSyncSequence.Get());
#endif
    }
}


// =========================================================
// MATERIAL IDENTITY (Phase 7B Stage 1C)
// =========================================================
// Skeleton: parses and stores material slot metadata per GUID.
// Does NOT call SetMaterial() — that is deferred to Stage 2.
// Overwrites previous metadata for the same GUID on each packet.
// Zero-slot packets are stored (clears previous metadata).
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
            // E2E.3: Use guarded attach for deferred resolution too.
            const bool bAttached = SafeAttachLiveSyncActor(
                ChildActor, ParentActor, Entry.ChildGuid, Entry.ParentGuid);

            if (!bAttached)
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("[HIERARCHY][ORPHAN] RESOLVED — ATTACH SKIPPED: "
                         "child=%s parent=%s (safety guard rejected)"),
                    *Entry.ChildGuid.ToString(EGuidFormats::Digits),
                    *Entry.ParentGuid.ToString(EGuidFormats::Digits));
                // Keep world transform. Count as resolved (parent eventually
                // appears → next frame retry will succeed).
            }
            else
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("[HIERARCHY][ORPHAN] RESOLVED — attached: "
                         "child=%s parent=%s (after %d retries)"),
                    *Entry.ChildGuid.ToString(EGuidFormats::Digits),
                    *Entry.ParentGuid.ToString(EGuidFormats::Digits),
                    Entry.RetryCount);
            }
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
// UE5.7 COMPILE COMPATIBILITY HELPER
// =========================================================
// Replaces direct AActor::bPendingKill access (removed in UE5.7)
// with UE-safe public API.
// Returns true if Actor is unsafe for attachment:
//   - null pointer
//   - IsActorBeingDestroyed() called
//   - !IsValid (pending kill, unreachable, begin-destroyed, etc.)
// =========================================================

static bool IsLiveSyncActorInvalidForAttach(const AActor* Actor)
{
    return Actor == nullptr
        || Actor->IsActorBeingDestroyed()
        || !IsValid(Actor);
}


// =========================================================
// E2E.9: LIVE-SYNC CAMERA ACTOR EDITOR-SAFETY CHECK
// =========================================================
// Validates that an ACameraActor is in a safe state for
// editor-facing operations (SceneOutliner, Sequencer binding,
// viewport lock). Returns true if the camera is safe.
// The goal is to prevent the UE SceneOutliner from crashing
// when attempting to display a camera actor that is in a
// transitional state.
// =========================================================

static bool IsLiveSyncCameraSafeForEditorUse(const ACameraActor* Camera)
{
    if (Camera == nullptr)
    {
        return false;
    }
    if (!IsValid(Camera))
    {
        return false;
    }
    if (Camera->IsActorBeingDestroyed())
    {
        return false;
    }
    if (Camera->IsUnreachable())
    {
        return false;
    }
    if (Camera->GetWorld() == nullptr)
    {
        return false;
    }
    if (Camera->GetLevel() == nullptr)
    {
        return false;
    }
    if (Camera->GetOuter() == nullptr)
    {
        return false;
    }
    if (Camera->GetRootComponent() == nullptr)
    {
        return false;
    }
    if (Camera->GetCameraComponent() == nullptr)
    {
        return false;
    }
    return true;
}


// =========================================================
// E2E.3: ACTOR-POINTER-LEVEL ATTACHMENT CYCLE GUARD
// =========================================================
// Addresses FAIL_MANUAL_E2E_SCENE_OUTLINER_PARENT_RECURSION.
// WouldCreateHierarchyCycle operates on GUIDs and cannot
// validate actor pointer validity, null checks, or
// pending-kill state. This function operates on raw
// AActor* pointers for runtime safety.
// =========================================================

bool UUELiveSyncSubsystem::WouldCreateAttachmentCycle(
    AActor* Child,
    AActor* Parent)
{
    // ---- Null checks ----
    if (!Child)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[HIERARCHY][ATTACH_SKIP] null child"));
        return true;
    }

    if (!Parent)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[HIERARCHY][ATTACH_SKIP] null parent"));
        return true;
    }

    // ---- Actor invalidity check (replaces UE5.7-removed bPendingKill) ----
    if (IsLiveSyncActorInvalidForAttach(Child) ||
        IsLiveSyncActorInvalidForAttach(Parent))
    {
        const FString ChildName = Child ? Child->GetActorNameOrLabel() : TEXT("None");
        const FString ParentName = Parent ? Parent->GetActorNameOrLabel() : TEXT("None");
        const int32 bChildInvalid = IsLiveSyncActorInvalidForAttach(Child) ? 1 : 0;
        const int32 bParentInvalid = IsLiveSyncActorInvalidForAttach(Parent) ? 1 : 0;
        UE_LOG(LogLiveSync, Warning,
            TEXT("[HIERARCHY][ATTACH_SKIP] actor invalid for attach: "
                 "child=%s invalid=%d | parent=%s invalid=%d"),
            *ChildName,
            bChildInvalid,
            *ParentName,
            bParentInvalid);
        return true;
    }

    // ---- Self-parent check ----
    if (Child == Parent)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[HIERARCHY][ATTACH_SKIP_SELF] child=parent=%s"),
            *Child->GetActorNameOrLabel());
        return true;
    }

    // ---- Bounded parent-chain walk from Parent upward ----
    // Check: does Child appear anywhere in Parent's attach-parent chain?
    static constexpr int32 MAX_CYCLE_DEPTH = 256;
    AActor* Probe = Parent;
    int32 Depth = 0;

    while (Probe && Depth < MAX_CYCLE_DEPTH)
    {
        if (Probe == Child)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[HIERARCHY][CYCLE] Chain cycle via attach: "
                     "child=%s parent=%s (depth=%d)"),
                *Child->GetActorNameOrLabel(),
                *Parent->GetActorNameOrLabel(),
                Depth + 1);
            return true;
        }

        AActor* ParentActor = Probe->GetAttachParentActor();

        // Validate the next probe is safe for attach
        // (only check non-null probes to preserve original null-loop-exit behavior)
        if (ParentActor && IsLiveSyncActorInvalidForAttach(ParentActor))
        {
            const FString InvalidName = ParentActor->GetActorNameOrLabel();
            UE_LOG(LogLiveSync, Warning,
                TEXT("[HIERARCHY][ATTACH_SKIP] parent chain actor invalid: "
                     "depth=%d actor=%s"),
                Depth, *InvalidName);
            return true;
        }

        Probe = ParentActor;
        Depth++;
    }

    if (Depth >= MAX_CYCLE_DEPTH)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[HIERARCHY][CYCLE] Depth limit exceeded: "
                 "child=%s parent=%s (depth=%d) — rejecting"),
            *Child->GetActorNameOrLabel(),
            *Parent->GetActorNameOrLabel(),
            Depth);
        return true;
    }

    return false; // No cycle detected — safe to attach
}


// =========================================================
// E2E.3: SAFE ATTACH WRAPPER
// =========================================================
// Replaces direct AttachToActor calls in LiveSync paths.
// Logs: [HIERARCHY][ATTACH_GUARD] on entry,
//        [HIERARCHY][ATTACH_SKIP_CYCLE] / [ATTACH_SKIP_SELF] /
//        [ATTACH_SKIP_INVALID] on skip.
// Preserves world transform when skipping unsafe attach.
// =========================================================

bool UUELiveSyncSubsystem::SafeAttachLiveSyncActor(
    AActor* Child,
    AActor* Parent,
    const FGuid& ChildGuid,
    const FGuid& ParentGuid)
{
    // Always log guard entry
    UE_LOG(LogLiveSync, Log,
        TEXT("[HIERARCHY][ATTACH_GUARD] child=%s parent=%s"),
        ChildGuid.IsValid() ? *ChildGuid.ToString(EGuidFormats::Digits)
                             : TEXT("null"),
        ParentGuid.IsValid() ? *ParentGuid.ToString(EGuidFormats::Digits)
                             : TEXT("null"));

    // Run cycle detection
    if (WouldCreateAttachmentCycle(Child, Parent))
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[HIERARCHY][ATTACH_SKIP] Skipping attach for child=%s parent=%s"),
            ChildGuid.IsValid() ? *ChildGuid.ToString(EGuidFormats::Digits)
                                : TEXT("null"),
            ParentGuid.IsValid() ? *ParentGuid.ToString(EGuidFormats::Digits)
                                 : TEXT("null"));
        return false; // Skip attach
    }

    // Proceed with safe attach
    UE_LOG(LogLiveSync, Log,
        TEXT("[HIERARCHY][ATTACH_SAFE] Attached child=%s to parent=%s"),
        ChildGuid.IsValid() ? *ChildGuid.ToString(EGuidFormats::Digits)
                            : TEXT("null"),
        ParentGuid.IsValid() ? *ParentGuid.ToString(EGuidFormats::Digits)
                             : TEXT("null"));

    Child->AttachToActor(
        Parent,
        FAttachmentTransformRules::KeepWorldTransform);

    return true;
}


// =========================================================
// E2E.3: CAMERA-AWARE ATTACHMENT GUARD
// =========================================================
// Additional rules for camera actors:
//   - Never attach a LiveSync camera to itself.
//   - Never attach any actor to a parent whose chain includes
//     a LiveSync-spawned camera (prevents SceneOutliner recursion
//     when camera's frustum component corrupts the parent chain).
//   - Never attach a LiveSync camera to a parent whose chain
//     includes itself (circular camera attach).
// Does NOT disable camera Sequencer binding or camera cut.
// =========================================================

bool UUELiveSyncSubsystem::SafeAttachCameraOrToCamera(
    AActor* Child,
    AActor* Parent,
    const FGuid& ChildGuid,
    const FGuid& ParentGuid)
{
    // Run base cycle detection first
    if (WouldCreateAttachmentCycle(Child, Parent))
    {
        return false;
    }

    const bool bChildIsCamera = Child->IsA(ACameraActor::StaticClass());
    const bool bParentIsCamera = Parent->IsA(ACameraActor::StaticClass());

    // ---- Rule 1: Never attach a LiveSync camera to itself ----
    if (bChildIsCamera && Child == Parent)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[HIERARCHY][ATTACH_SKIP_SELF] camera child=parent=%s"),
            *Child->GetActorNameOrLabel());
        return false;
    }

    // ---- Rule 2: If parent is a LiveSync camera, check its chain ----
    // Reject attaching any actor to a parent whose chain includes
    // a LiveSync camera. This prevents the frustum proxy from
    // corrupting SceneOutliner's parent walk.
    if (bParentIsCamera)
    {
        // Walk parent chain of the camera parent.
        // If Child appears, reject.
        static constexpr int32 MAX_CYCLE_DEPTH = 256;
        AActor* Probe = Parent;
        int32 Depth = 0;

        while (Probe && Depth < MAX_CYCLE_DEPTH)
        {
            if (Probe == Child)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[HIERARCHY][CYCLE] Camera chain cycle: "
                         "child=%s camera-parent=%s (depth=%d)"),
                    *Child->GetActorNameOrLabel(),
                    *Parent->GetActorNameOrLabel(),
                    Depth + 1);
                return false;
            }
            Probe = Probe->GetAttachParentActor();
            Depth++;
        }

        if (Depth >= MAX_CYCLE_DEPTH)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[HIERARCHY][CYCLE] Camera parent chain depth exceeded: "
                     "child=%s camera-parent=%s (depth=%d)"),
                *Child->GetActorNameOrLabel(),
                *Parent->GetActorNameOrLabel(),
                Depth);
            return false;
        }
    }

    // ---- Rule 3: If child is a LiveSync camera, check parent chain ----
    // Reject attaching a LiveSync camera to a parent whose chain
    // includes the same camera (circular camera attach).
    if (bChildIsCamera)
    {
        static constexpr int32 MAX_CYCLE_DEPTH = 256;
        AActor* Probe = Parent;
        int32 Depth = 0;

        while (Probe && Depth < MAX_CYCLE_DEPTH)
        {
            if (Probe == Child)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[HIERARCHY][CYCLE] Camera attach cycle: "
                         "camera-child=%s parent-chain=%s (depth=%d)"),
                    *Child->GetActorNameOrLabel(),
                    *Probe->GetActorNameOrLabel(),
                    Depth + 1);
                return false;
            }
            Probe = Probe->GetAttachParentActor();
            Depth++;
        }

        if (Depth >= MAX_CYCLE_DEPTH)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[HIERARCHY][CYCLE] Camera child parent-chain depth exceeded: "
                     "camera-child=%s (depth=%d)"),
                *Child->GetActorNameOrLabel(),
                Depth);
            return false;
        }
    }

    return true; // Safe to attach
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

    // Phase 10J.5A: Clean material metadata to prevent stale entries
    MaterialMetadata.Remove(TargetGuid);
    // Phase 10K.1: Clean texture map cache
    MaterialTextureMapCache.Remove(TargetGuid);
    if (GEnableVerboseSyncLogs)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[MATSTALL][UE] mat_cleanup delete guid=%s"),
            *TargetGuid.ToString(EGuidFormats::Digits));
    }

    // Phase 10J.5E: Remove FBX authority on delete
    FBXAuthoritativeGuids.Remove(TargetGuid);
    UE_LOG(LogLiveSync, Log,
        TEXT("[FBX][AUTH] cleanup delete guid=%s"),
        *TargetGuid.ToString(EGuidFormats::Digits));

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
HandlePlaybackState(
    const FPlaybackStatePayload& Payload)
{
    CHECK_GAME_THREAD();

    LastPlaybackState = Payload.State;
    LastPlaybackSequence = Payload.Sequence;
    LastPlaybackTimestamp = Payload.Timestamp;
    bHasPlaybackState = true;

    UE_LOG(LogLiveSync, Verbose,
        TEXT("[PLAYBACK] Applied: state=%d loop=%d seq=%u ts=%.3f"),
        Payload.State, Payload.bLoopEnabled,
        Payload.Sequence, Payload.Timestamp);
}


// =========================================================
// SNAPSHOT BOUNDARY (Phase 7 — PT_BeginSnapshot / PT_EndSnapshot)
// =========================================================

void UUELiveSyncSubsystem::
HandleBeginSnapshot()
{
    CHECK_GAME_THREAD();

    bInSnapshotBuild = true;
    SnapshotStartTime = FPlatformTime::Seconds();

    UE_LOG(LogLiveSync, Verbose,
        TEXT("[SNAPSHOT] Begin: bInSnapshotBuild=1 ts=%.3f"),
        SnapshotStartTime);
}

void UUELiveSyncSubsystem::
HandleEndSnapshot()
{
    CHECK_GAME_THREAD();

    bInSnapshotBuild = false;

    // Replay collection stream
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

void UUELiveSyncSubsystem::
AbortSnapshot()
{
    CHECK_GAME_THREAD();

    bInSnapshotBuild = false;

    UE_LOG(LogLiveSync, Warning,
        TEXT("[SNAPSHOT] Aborted: bInSnapshotBuild=0"));
}


// =========================================================
// ACTIVE CAMERA (Phase 7D)
// =========================================================
// Storage-only: accepts valid FActiveCameraPayload, updates
// LastActiveCameraGUID, LastActiveCameraSequence, and
// LastActiveCameraTimestamp.
//
// When CVar UE.LiveSync.ActiveCamera.ApplyToViewport is enabled,
// additionally resolves the camera GUID through ActorCache and
// applies it as the editor viewport view target.
//
// Null GUID (all-zero) clears stored state without touching the
// viewport. Missing or non-camera GUIDs are logged and counted.
// =========================================================
// ULiveSyncCameraComponent::GetCameraView
// =========================================================
// Clean pass-through. The camera actor transform is driven by
// the shared transform pipeline via LiveSync transform packets.
// No viewport basis correction is applied here — incorrect
// viewport rotation will be investigated on a separate branch.
//
// The disabled R_y(90°) workaround and diagnostic logging have
// been removed. The old approach caused a feedback loop between
// the CameraComponent's GetCameraView and the transform sync.
// =========================================================

void
ULiveSyncCameraComponent::GetCameraView(
    float DeltaTime,
    FMinimalViewInfo& DesiredView)
{
    Super::GetCameraView(DeltaTime, DesiredView);
}


// =========================================================
// ALiveSyncCameraActor constructor
// =========================================================
// Uses SetDefaultSubobjectClass to replace UCameraComponent
// with ULiveSyncCameraComponent.
// =========================================================

ALiveSyncCameraActor::ALiveSyncCameraActor(const FObjectInitializer& ObjectInitializer)
    : Super(ObjectInitializer.SetDefaultSubobjectClass<ULiveSyncCameraComponent>(TEXT("CameraComponent")))
{
}


// =========================================================
// MANUAL E2E.1: Camera frustum guard
// =========================================================
// Suppress frustum/editor-visualization on LiveSync-spawned
// ACameraActor to avoid editor selection-parent crash during
// frustum render proxy creation.  UCameraComponent stays fully
// enabled and usable.
void UUELiveSyncSubsystem::
ConfigureLiveSyncCameraActor(ACameraActor* CameraActor)
{
    if (!CameraActor)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[CAMERA][FRUSTUM_GUARD_FAIL] null CameraActor"));
        return;
    }

    // Iterate all components looking for a frustum-draw component.
    // Use name-based detection to avoid adding Engine includes
    // that may vary across UE versions.
    TArray<UActorComponent*> Comps;
    CameraActor->GetComponents(Comps);

    bool bGuarded = false;
    for (UActorComponent* Comp : Comps)
    {
        if (!Comp)
            continue;
        const FString CompClass = Comp->GetClass()->GetName();
        // Match UDrawFrustumComponent or any component whose name
        // contains "Frustum" — covers editor frustum viz classes.
        if (CompClass.Contains(TEXT("Frustum")))
        {
            // Cast to USceneComponent before calling visibility APIs;
            // UActorComponent does not expose SetHiddenInGame / SetVisibility.
            if (USceneComponent* SceneComp = Cast<USceneComponent>(Comp))
            {
                SceneComp->SetHiddenInGame(true);
                SceneComp->SetVisibility(false, true);
                SceneComp->SetComponentTickEnabled(false);
                UE_LOG(LogLiveSync, Log,
                    TEXT("[CAMERA][FRUSTUM_GUARD] Suppressed frustum component %s on %s"),
                    *CompClass, *CameraActor->GetName());
                bGuarded = true;
            }
            else
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[CAMERA][FRUSTUM_GUARD_FAIL] Frustum-like component is not a USceneComponent class=%s actor=%s"),
                    *CompClass, *CameraActor->GetName());
            }
        }
    }

    if (!bGuarded)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[CAMERA][FRUSTUM_GUARD_SKIP] No frustum component found on %s"),
            *CameraActor->GetName());
    }
}

// =============================================================
// PHASE 7G STAGE 5: Ensure camera possessable binding and
// CameraCutTrack section in the persistent LevelSequence.
// =============================================================
void UUELiveSyncSubsystem::EnsureCameraSequencerBinding(
    ACameraActor* Camera,
    const FGuid& CameraGUID)
{
    CHECK_GAME_THREAD();

    if (!Camera || !CameraGUID.IsValid())
    {
        Stats.ActiveCameraCutSkipped.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    // Get or create the persistent LevelSequence
    ULevelSequence* Seq = GetOrCreateLiveSyncLevelSequence();
    if (!Seq)
    {
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[CAMERA][SEQ_BIND_SKIP] No LevelSequence for camera %s"),
            *CameraGUID.ToString());
        Stats.ActiveCameraCutSkipped.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    UMovieScene* MovieScene = Seq->GetMovieScene();
    if (!MovieScene)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[CAMERA][SEQ_BIND_SKIP] GetMovieScene() null for camera %s"),
            *CameraGUID.ToString());
        Stats.ActiveCameraCutSkipped.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    // Step 1: Ensure possessable binding exists
    FGuid* ExistingBinding = LiveSyncGuidToSequencerBinding.Find(CameraGUID);
    FGuid BindingGuid;
    bool bBindingCreated = false;

    if (ExistingBinding)
    {
        BindingGuid = *ExistingBinding;
        Stats.ActiveCameraBindingExists.fetch_add(1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[CAMERA][SEQ_BIND_SKIP] Camera %s already bound to %s"),
            *CameraGUID.ToString(), *BindingGuid.ToString());
    }
    else
    {
        // Add possessable to MovieScene
        BindingGuid = MovieScene->AddPossessable(
            Camera->GetName(),
            Camera->GetClass());

        if (!BindingGuid.IsValid())
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[CAMERA][SEQ_BIND] AddPossessable failed for camera %s"),
                *CameraGUID.ToString());
            Stats.ActiveCameraCutSkipped.fetch_add(1, std::memory_order_relaxed);
            return;
        }

        // Bind possessable to the camera actor
        Seq->BindPossessableObject(
            BindingGuid,
            *Camera,
            Camera->GetWorld());

        // Store mapping
        LiveSyncGuidToSequencerBinding.Add(CameraGUID, BindingGuid);

        bBindingCreated = true;
        Stats.ActiveCameraBindingCreated.fetch_add(1, std::memory_order_relaxed);

        UE_LOG(LogLiveSync, Log,
            TEXT("[CAMERA][SEQ_BIND] guid=%s camera=%s binding=%s"),
            *CameraGUID.ToString(),
            *Camera->GetName(),
            *BindingGuid.ToString());
    }

    // Step 2: Ensure CameraCutTrack and section
    UMovieSceneCameraCutTrack* CameraCutTrack = Cast<UMovieSceneCameraCutTrack>(
        MovieScene->GetCameraCutTrack());

    bool bTrackCreated = false;
    if (!CameraCutTrack)
    {
        CameraCutTrack = Cast<UMovieSceneCameraCutTrack>(
            MovieScene->AddCameraCutTrack(UMovieSceneCameraCutTrack::StaticClass()));
        bTrackCreated = true;
    }

    if (!CameraCutTrack)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[CAMERA][CUT_SKIP] Failed to get/create CameraCutTrack for camera %s"),
            *CameraGUID.ToString());
        Stats.ActiveCameraCutSkipped.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    if (bTrackCreated)
    {
        Stats.ActiveCameraCutTrackCreated.fetch_add(1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Log,
            TEXT("[CAMERA][CUT_TRACK] Created CameraCutTrack for camera %s binding=%s"),
            *CameraGUID.ToString(), *BindingGuid.ToString());
    }

    // Step 3: Create camera cut section targeting this binding
    FMovieSceneObjectBindingID BindingID(
        (UE::MovieScene::FRelativeObjectBindingID(BindingGuid)));

    // Use current start frame or 0 if range not set
    int32 StartFrame = FMath::Max(0, LiveSyncSequenceFrameStart);
    int32 EndFrame = FMath::Max(StartFrame + 1, LiveSyncSequenceFrameEnd);

    UMovieSceneCameraCutSection* CutSection = CameraCutTrack->AddNewCameraCut(
        BindingID, FFrameNumber(StartFrame));

    if (!CutSection)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[CAMERA][CUT_SKIP] AddNewCameraCut failed for camera %s"),
            *CameraGUID.ToString());
        Stats.ActiveCameraCutSkipped.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    CutSection->SetRange(
        TRange<FFrameNumber>(
            FFrameNumber(StartFrame),
            FFrameNumber(EndFrame)));

    Stats.ActiveCameraCutApplied.fetch_add(1, std::memory_order_relaxed);

    UE_LOG(LogLiveSync, Log,
        TEXT("[CAMERA][CUT_APPLY] guid=%s binding=%s range=%d-%d"),
        *CameraGUID.ToString(),
        *BindingGuid.ToString(),
        StartFrame,
        EndFrame);

    // Step 4: Save the sequence
#if WITH_EDITOR
    SaveLiveSyncLevelSequenceAsset(Seq);
    Stats.ActiveCameraSeqSaved.fetch_add(1, std::memory_order_relaxed);
    UE_LOG(LogLiveSync, Log,
        TEXT("[CAMERA][CUT_SAVE] Sequence saved for camera %s"),
        *CameraGUID.ToString());
#endif
}

// =========================================================
// E2E.10: Process deferred camera work one tick later.
// Defers Sequencer binding and viewport lock for
// newly-created cameras so the SceneOutliner tree can
// settle before active operations.
// =========================================================
void UUELiveSyncSubsystem::ProcessDeferredCameras()
{
    if (PendingActiveCameraData.Num() == 0)
    {
        return;
    }

    UE_LOG(LogLiveSync, Log,
        TEXT("[CAMERA][E2E10_DEFERRED_PROCESS] Processing %d deferred camera(s)"),
        PendingActiveCameraData.Num());

    // Process each deferred camera. If the camera is no longer
    // valid or safe, skip with log.
    TArray<FGuid> DeferredGUIDs;
    PendingActiveCameraData.GetKeys(DeferredGUIDs);

    for (const FGuid& GUID : DeferredGUIDs)
    {
        AActor* Found = FindActorFast(GUID);
        if (!Found)
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[CAMERA][E2E10_DEFERRED_SKIP] guid=%s — actor not found in cache"),
                *GUID.ToString());
            PendingActiveCameraData.Remove(GUID);
            continue;
        }

        ALiveSyncCameraActor* Camera = Cast<ALiveSyncCameraActor>(Found);
        if (!Camera)
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[CAMERA][E2E10_DEFERRED_SKIP] guid=%s — not a LiveSyncCameraActor"),
                *GUID.ToString());
            PendingActiveCameraData.Remove(GUID);
            continue;
        }

        if (!IsLiveSyncCameraSafeForEditorUse(Camera))
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[CAMERA][E2E10_DEFERRED_SKIP] guid=%s — camera not safe for editor use"),
                *GUID.ToString());
            PendingActiveCameraData.Remove(GUID);
            continue;
        }

        const FPendingCameraActivePayload* Payload =
            PendingActiveCameraData.Find(GUID);
        if (!Payload)
        {
            continue;
        }

        // Apply deferred Sequencer binding
        EnsureCameraSequencerBinding(Camera, GUID);
        UE_LOG(LogLiveSync, Log,
            TEXT("[CAMERA][E2E10_DEFERRED_SEQ] Applied Sequencer binding for guid=%s"),
            *GUID.ToString());

        // Apply deferred viewport lock
#if WITH_EDITOR
        if (GEditor)
        {
            int32 AppliedCount = 0;
            for (FLevelEditorViewportClient* LevelVC : GEditor->GetLevelViewportClients())
            {
                if (LevelVC)
                {
                    LevelVC->SetActorLock(Camera);
                    AppliedCount++;
                }
            }

            Stats.ActiveCameraPacketsAppliedToViewport.fetch_add(
                AppliedCount, std::memory_order_relaxed);

            UE_LOG(LogLiveSync, Log,
                TEXT("[CAMERA][E2E10_DEFERRED_LOCK] SetActorLock on %d viewport(s) for CameraActor=%s"),
                AppliedCount, *Camera->GetName());
        }
#endif

        PendingActiveCameraData.Remove(GUID);
    }
}

void UUELiveSyncSubsystem::
HandleActiveCamera(
    const FActiveCameraPayload& Payload)
{
    CHECK_GAME_THREAD();

    LastActiveCameraGUID = Payload.CameraGUID;
    LastActiveCameraSequence = Payload.Sequence;
    LastActiveCameraTimestamp = Payload.Timestamp;
    bHasActiveCamera = (Payload.CameraGUID != FGuid());
    bHasEverReceivedActiveCamera = true;

    UE_LOG(LogLiveSync, Log,
        TEXT("[CAMERA][ACTIVE_RECV] guid=%s seq=%u ts=%.3f hasCamera=%d"),
        *Payload.CameraGUID.ToString(),
        Payload.Sequence, Payload.Timestamp,
        bHasActiveCamera ? 1 : 0);

    // Phase 7G Stage 5: Sequencer binding is independent of viewport CVar.
    // Resolve camera actor first (find or auto-spawn), then ensure binding.
    ALiveSyncCameraActor* ResolvedCamera = nullptr;

    if (Payload.CameraGUID != FGuid())
    {
#if WITH_EDITOR
        AActor* Found = FindActorFast(Payload.CameraGUID);
        if (!Found)
        {
            // Auto-spawn ALiveSyncCameraActor for this camera GUID
            UWorld* World = GetWorld();
            if (World)
            {
                FTransform CamTransform(FTransform::Identity);
                CamTransform.SetLocation(FVector(0.0f, -200.0f, 100.0f));

                // E2E.10: Use non-deferred spawn with bHideFromSceneOutliner=true.
                // Prevents UE SceneOutliner EnsureParentForItem crash on camera spawn.
                FActorSpawnParameters AutoSpawnParams;
                AutoSpawnParams.SpawnCollisionHandlingOverride =
                    ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
                AutoSpawnParams.bHideFromSceneOutliner = true;

                ALiveSyncCameraActor* NewCamera = World->SpawnActor<ALiveSyncCameraActor>(
                    ALiveSyncCameraActor::StaticClass(), CamTransform,
                    AutoSpawnParams);
                if (NewCamera)
                {
                    // Apply frustum guard after spawn. Camera is hidden from
                    // SceneOutliner, so no EnsureParentForItem crash on this path.
                    ConfigureLiveSyncCameraActor(NewCamera);

                    UE_LOG(LogLiveSync, Log,
                        TEXT("[CAMERA][OUTLINER_GUARD] HandleActiveCamera post-spawn guid=%s"),
                        *Payload.CameraGUID.ToString());

                    UE_LOG(LogLiveSync, Log,
                        TEXT("[CAMERA][E2E10_OUTLINER_HIDE] auto-spawn guid=%s"),
                        *Payload.CameraGUID.ToString());

                    DiagBasis_CameraOneShot(NewCamera, Payload.CameraGUID);

                    UE_LOG(LogLiveSync, Log,
                        TEXT("[CAMERA][SAFE_SPAWN_READY] HandleActiveCamera auto-spawn guid=%s"),
                        *Payload.CameraGUID.ToString());

                    FString TagString = FString::Printf(
                        TEXT("LiveSync_GUID=%s"),
                        *Payload.CameraGUID.ToString(EGuidFormats::Digits));
                    NewCamera->Tags.Add(FName(*TagString));
                    ActorCache.Add(Payload.CameraGUID, NewCamera);

                    Stats.ActiveCameraPacketsSpawned.fetch_add(1, std::memory_order_relaxed);

                    UE_LOG(LogLiveSync, Log,
                        TEXT("[CAMERA][SPAWN] Spawned ALiveSyncCameraActor for GUID=%s"),
                        *Payload.CameraGUID.ToString());

                    Found = NewCamera;
                }
                else
                {
                    UE_LOG(LogLiveSync, Warning,
                        TEXT("[CAMERA][SPAWN_FAIL] Could not spawn ALiveSyncCameraActor for GUID=%s"),
                        *Payload.CameraGUID.ToString());
                }
            }
            else
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[CAMERA][SPAWN_FAIL] No world for camera GUID=%s"),
                    *Payload.CameraGUID.ToString());
            }
        }

        if (Found)
        {
            ResolvedCamera = Cast<ALiveSyncCameraActor>(Found);
            if (!ResolvedCamera)
            {
                Stats.ActiveCameraPacketsNotCamera.fetch_add(1, std::memory_order_relaxed);
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[CAMERA][SAFE_INVALID_SKIP] Actor %s (%s) is not a CameraActor"),
                    *Found->GetName(), *Found->GetClass()->GetName());
            }
        }
#endif
    }

    // E2E.10: Camera is spawned with bHideFromSceneOutliner=true, so the
    // SceneOutliner EnsureParentForItem crash is avoided. Process Sequencer
    // binding and viewport lock immediately (no deferral needed).
    // Clean up any stale pending data from previous E2E.10 attempts.
    if (ResolvedCamera)
    {
        PendingActiveCameraData.Remove(Payload.CameraGUID);
    }

    // Sequencer binding (always, independent of viewport CVar)
    // E2E.9: Gate with safety check — defer if camera is not yet safe.
    if (ResolvedCamera)
    {
        if (IsLiveSyncCameraSafeForEditorUse(ResolvedCamera))
        {
            EnsureCameraSequencerBinding(ResolvedCamera, Payload.CameraGUID);
        }
        else
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[CAMERA][SAFE_SEQ_DEFER] Deferring Sequencer binding for guid=%s — camera not safe"),
                *Payload.CameraGUID.ToString());
            Stats.ActiveCameraCutSkipped.fetch_add(1, std::memory_order_relaxed);
        }
    }

    // Viewport lock is gated by ApplyToViewport CVar
    if (!CVarLiveSyncActiveCameraApplyToViewport.GetValueOnGameThread())
    {
        return;
    }

    if (Payload.CameraGUID == FGuid())
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[CAMERA] Null GUID — viewport unchanged"));
        return;
    }

    if (!ResolvedCamera)
    {
        // Camera was not resolved (already logged above)
        return;
    }

#if WITH_EDITOR
    // Lock the camera actor on all level editor viewport clients (pilot mode)
    // E2E.9: Gate with safety check — defer if camera is not yet safe.
    if (GEditor && IsLiveSyncCameraSafeForEditorUse(ResolvedCamera))
    {
        int32 AppliedCount = 0;
        for (FLevelEditorViewportClient* LevelVC : GEditor->GetLevelViewportClients())
        {
            if (LevelVC)
            {
                LevelVC->SetActorLock(ResolvedCamera);
                AppliedCount++;
            }
        }

        Stats.ActiveCameraPacketsAppliedToViewport.fetch_add(
            AppliedCount, std::memory_order_relaxed);

        UE_LOG(LogLiveSync, Log,
            TEXT("[CAMERA][VIEW_TARGET] SetActorLock on %d viewport(s) for CameraActor=%s"),
            AppliedCount, *ResolvedCamera->GetName());
    }
    else if (GEditor && !IsLiveSyncCameraSafeForEditorUse(ResolvedCamera))
    {
        Stats.ActiveCameraPacketsViewTargetFailed.fetch_add(1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Log,
            TEXT("[CAMERA][SAFE_ACTIVE_DEFER] Deferring viewport lock for guid=%s — camera not safe"),
            *Payload.CameraGUID.ToString());
    }
    else
    {
        Stats.ActiveCameraPacketsViewTargetFailed.fetch_add(1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Log,
            TEXT("[CAMERA][VIEW_TARGET_FAIL] GEditor is null — cannot lock viewport to camera"));
    }
#else
    UE_LOG(LogLiveSync, Log,
        TEXT("[CAMERA] No editor — SetViewTarget not supported"));
#endif
    Stats.ActiveCameraPacketsApplied.fetch_add(1, std::memory_order_relaxed);
    Stats.PacketsProcessed.fetch_add(1, std::memory_order_relaxed);
    return;
}


// =========================================================
// FUTURE: UE-side camera actor spawn stability (Phase 7H hotfix)
// =========================================================
// ACameraActor spawn via PT_Create (0x03) in the editor currently
// freezes after CREATE + DEF + TRANSFORM sequence. Potential future
// alternatives:
//   1. Spawn deferred on editor tick (ProcessDeferredCameras model)
//   2. Spawn transient non-transactional actor
//   3. Avoid ACameraActor and use lightweight preview component
//   4. Use existing camera actor only (pre-spawned)
//   5. Create actor through editor subsystem safely
// For now, PT_Create is disabled in the manual camera operator.
// =========================================================

// =========================================================
// CAMERA DEFINITION (Phase 7G Stage 3)
// =========================================================
// Applies camera parameters from PT_CameraDef (0x1B) to the
// ACameraActor associated with the camera GUID.
//
// If the camera actor does not exist yet, stores the def
// for later application.  FOV is computed from focal length
// and sensor width: FOV = 2 * atan(sensor_width / (2 * focal_length))
// =========================================================

void UUELiveSyncSubsystem::
HandleCameraDef(
    const FCameraDefPayload& Payload)
{
    CHECK_GAME_THREAD();

    // Sequence validation — reject stale packets
    if (Payload.CameraGUID.IsValid() && Payload.CameraGUID != FGuid())
    {
        uint32 CurrentSeq = LastCameraDefSequence;
        // Each call increments; stale check is implicit via ordering.
        // Packets for different GUIDs are independent.
    }
    LastCameraDefSequence++;

    UE_LOG(LogLiveSync, Log,
        TEXT("[CAMERA][DEF_RECV] guid=%s focal=%.1f sensor=%.1fx%.1f clip=[%.1f,%.1f] ortho=%.1f flags=%d"),
        *Payload.CameraGUID.ToString(),
        Payload.FocalLengthMM,
        Payload.SensorWidthMM,
        Payload.SensorHeightMM,
        Payload.ClipStart,
        Payload.ClipEnd,
        Payload.OrthoScale,
        Payload.CameraFlags);

#if WITH_EDITOR
    AActor* Found = FindActorFast(Payload.CameraGUID);
    if (!Found)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[CAMERA][DEF_STORED_NO_ACTOR] guid=%s — storing def for later (manual safe: no spawn)"),
            *Payload.CameraGUID.ToString());
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[CAMERA][MANUAL_SAFE_NO_SPAWN] guid=%s — spawn disabled; "
                 "update existing camera actors only"),
            *Payload.CameraGUID.ToString());
        Stats.CameraDefPacketsStale.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    ALiveSyncCameraActor* Camera = Cast<ALiveSyncCameraActor>(Found);
    if (!Camera)
    {
        Stats.ActiveCameraPacketsNotCamera.fetch_add(1, std::memory_order_relaxed);
        UE_LOG(LogLiveSync, Warning,
            TEXT("[CAMERA][DEF] Actor %s is not a LiveSyncCameraActor"),
            *Found->GetName());
        Stats.CameraDefPacketsStale.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    UCameraComponent* CamComp = Camera->GetCameraComponent();
    if (!CamComp)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[CAMERA][DEF] CameraActor %s has no CameraComponent"),
            *Camera->GetName());
        Stats.CameraDefPacketsStale.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    const bool bIsOrtho = (Payload.CameraFlags & 0x01) != 0;

    if (bIsOrtho)
    {
        // Orthographic projection
        CamComp->SetProjectionMode(ECameraProjectionMode::Orthographic);
        CamComp->OrthoWidth = Payload.OrthoScale;
        CamComp->SetOrthoNearClipPlane(FMath::Max(Payload.ClipStart, 0.01f));
        CamComp->SetOrthoFarClipPlane(Payload.ClipEnd);
        UE_LOG(LogLiveSync, Log,
            TEXT("[CAMERA][DEF_APPLY] ortho: width=%.1f near=%.1f far=%.1f on %s"),
            CamComp->OrthoWidth,
            CamComp->OrthoNearClipPlane,
            CamComp->OrthoFarClipPlane,
            *Camera->GetName());
    }
    else
    {
        // Perspective projection — FOV from focal length and sensor width
        const float FocalMM = FMath::Max(Payload.FocalLengthMM, 1.0f);
        const float SensorW = FMath::Max(Payload.SensorWidthMM, 1.0f);
        const float FOVRad = 2.0f * FMath::Atan(SensorW / (2.0f * FocalMM));
        const float FOVDeg = FMath::RadiansToDegrees(FOVRad);

        CamComp->SetProjectionMode(ECameraProjectionMode::Perspective);
        CamComp->FieldOfView = FOVDeg;
        CamComp->AspectRatio = Payload.SensorWidthMM / FMath::Max(Payload.SensorHeightMM, 1.0f);
        CamComp->bConstrainAspectRatio = true;

        UE_LOG(LogLiveSync, Log,
            TEXT("[CAMERA][DEF_APPLY] persp: FOV=%.1f aspect=%.2f focal=%.1f sensor=%.1fx%.1f clip=[%.1f,%.1f] on %s"),
            FOVDeg,
            CamComp->AspectRatio,
            Payload.FocalLengthMM,
            Payload.SensorWidthMM,
            Payload.SensorHeightMM,
            Payload.ClipStart,
            Payload.ClipEnd,
            *Camera->GetName());

        // Clip planes: UCameraComponent does not expose direct ZNear/ZFar for
        // perspective mode.  Log the values for diagnostics; UE uses RHI defaults.
        UE_LOG(LogLiveSync, Log,
            TEXT("[CAMERA][DEF] clip planes (perspective): ZNear=%.1f ZFar=%.1f — UE RHI defaults apply"),
            Payload.ClipStart,
            Payload.ClipEnd);
    }
#else
    UE_LOG(LogLiveSync, Log,
        TEXT("[CAMERA][DEF] No editor — cannot apply camera parameters"));
    Stats.CameraDefPacketsStale.fetch_add(1, std::memory_order_relaxed);
    return;
#endif
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

                    // 4. E2E.3: Use SafeAttachLiveSyncActor for unified guard.
                    // Retains oscillation tracking (not in guard).
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

                    // Unified safety guard replaces inline AttachToActor.
                    const bool bAttached = SafeAttachLiveSyncActor(
                        Child, Parent, Entry.Child, Entry.Parent);

                    if (!bAttached)
                    {
                        UE_LOG(LogLiveSync, Log,
                            TEXT("  ATTACH SKIPPED: child=%s parent=%s"),
                            *Entry.Child.ToString(EGuidFormats::Digits),
                            *Entry.Parent.ToString(EGuidFormats::Digits));
                        continue;
                    }

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


// =========================================================
// HANDLE TIMELINE (Phase 7B)
// =========================================================

void UUELiveSyncSubsystem::
HandleTimeline(
    const FTimelinePayload& Payload)
{
}


// =========================================================
// HANDLE TIMELINE STATE (Phase 7F Stage 1)
// =========================================================

void UUELiveSyncSubsystem::
HandleTimelineState(
    const FTimelineStatePayload& Payload)
{
    // Store the latest state
    LastTimelineStatePayload = Payload;
    bHasTimelineStatePayload = true;

    // Apply to LevelSequence playback range if it exists
    ULevelSequence* Seq = LiveSyncSequence.Get();
    if (!Seq)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[TIMELINE][SKIP] No LiveSync LevelSequence to apply"));
        return;
    }

    UMovieScene* MovieScene = Seq->GetMovieScene();
    if (!MovieScene)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[TIMELINE][SKIP] No MovieScene in LevelSequence"));
        return;
    }

    // Apply frame range
    MovieScene->SetPlaybackRange(
        TRange<FFrameNumber>(
            FFrameNumber(Payload.FrameStart),
            FFrameNumber(Payload.FrameEnd)));

    // Apply display rate if FPS is valid
    if (Payload.FPSNum > 0 && Payload.FPSDen > 0)
    {
        FFrameRate DisplayRate(Payload.FPSNum, Payload.FPSDen);
        MovieScene->SetDisplayRate(DisplayRate);
    }

    // Store the applied values
    LiveSyncSequenceFrameStart = Payload.FrameStart;
    LiveSyncSequenceFrameEnd   = Payload.FrameEnd;
    LiveSyncSequenceFPSNum     = Payload.FPSNum;
    LiveSyncSequenceFPSDen     = Payload.FPSDen;

    UE_LOG(LogLiveSync, Log,
        TEXT("[TIMELINE][APPLY] range=[%d,%d] fps=%d/%d"),
        Payload.FrameStart, Payload.FrameEnd,
        Payload.FPSNum, Payload.FPSDen);
}


// =========================================================
// HANDLE PLAYBACK TRANSPORT (Phase 7F Stage 2)
// =========================================================

void UUELiveSyncSubsystem::
HandlePlaybackTransport(
    const FPlaybackTransportPayload& Payload)
{
    // Store the latest state
    LastPlaybackTransportPayload = Payload;
    bHasPlaybackTransportState = true;

    // For SetFrame/Scrub (command=0), attempt to apply the frame
    // if we have an active LevelSequence with a MovieScene.
    // For Play/Pause/Stop, store and log — Sequencer playback API
    // requires editor context and is deferred to Stage 2.1.
    //
    // Classification: PASS_TRANSPORT_STATE_ONLY for now.
    // UE Sequencer playback (ULevelSequencePlayer::Play/Pause/Stop)
    // is not safe to call from subsystem Tick in editor context
    // without an active MovieScene player instance.

    ULevelSequence* Seq = LiveSyncSequence.Get();
    if (!Seq)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[PLAYBACK][SKIP] No LiveSync LevelSequence"));
        return;
    }

    UMovieScene* MovieScene = Seq->GetMovieScene();
    if (!MovieScene)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[PLAYBACK][SKIP] No MovieScene in LevelSequence"));
        return;
    }

    // Apply SetFrame/Scrub to the playback range's current position
    // by clamping the frame to the playback range and setting the
    // MovieScene's playback position.
    if (Payload.Command == static_cast<uint8>(EPlaybackTransportCommand::SetFrame))
    {
        const int32 ClampedFrame = FMath::Clamp(
            Payload.FrameCurrent,
            LiveSyncSequenceFrameStart,
            LiveSyncSequenceFrameEnd);

        UE_LOG(LogLiveSync, Log,
            TEXT("[PLAYBACK][APPLY] SetFrame frame=%d (clamped=%d)"),
            Payload.FrameCurrent, ClampedFrame);

        // Store the current playhead frame for diagnostics
        LiveSyncSequenceFrameCurrent = ClampedFrame;
        return;
    }

    // Play/Pause/Stop — store and log only (PASS_TRANSPORT_STATE_ONLY)
    static const TCHAR* CommandNames[] =
        { TEXT("SetFrame"), TEXT("Play"), TEXT("Pause"), TEXT("Stop") };
    const TCHAR* CmdName = (Payload.Command <= 3)
        ? CommandNames[Payload.Command]
        : TEXT("Unknown");

    UE_LOG(LogLiveSync, Log,
        TEXT("[PLAYBACK][APPLY] command=%s frame=%d (PASS_TRANSPORT_STATE_ONLY)"),
        CmdName, Payload.FrameCurrent);

    // Store the current frame for diagnostics
    LiveSyncSequenceFrameCurrent = Payload.FrameCurrent;
}


// =========================================================
// CACHE MATERIAL PATH (Phase 7B Stage 1D)
// =========================================================

void UUELiveSyncSubsystem::
CacheMaterialPath(
    const FMaterialIdentityRef& Identity,
    const FSoftObjectPath& Path)
{
    if (!Identity.IsValid() ||
        Path.IsNull())
    {
        return;
    }

    FSoftObjectPath* Existing =
        MaterialPathCache.Find(Identity);

    if (Existing && *Existing != Path)
    {
        UE_LOG(
            LogLiveSync,
            Warning,
            TEXT("[MaterialRegistry] Identity collision: "
                 "0x%llx%llx was \"%s\" now \"%s\" \u2014 overwriting"),
            Identity.High,
            Identity.Low,
            *Existing->ToString(),
            *Path.ToString());
    }

    MaterialPathCache.Add(
        Identity,
        Path);
}


// =========================================================
// MAKE GENERATED MATERIAL KEY (Phase 10J.5H)
// =========================================================

FString UUELiveSyncSubsystem::
MakeGeneratedMaterialKey(
    const FGuid& Guid,
    int32 SlotIndex) const
{
    return FString::Printf(TEXT("%s_%d"),
        *Guid.ToString(EGuidFormats::Short),
        SlotIndex);
}


// =========================================================
// GET OR CREATE GENERATED MID (Phase 10J.5H)
// =========================================================

UMaterialInstanceDynamic* UUELiveSyncSubsystem::
GetOrCreateGeneratedMID(
    const FGuid& Guid,
    int32 SlotIndex,
    const FMaterialSlotBasicProperties& Props)
{
    const FString Key = MakeGeneratedMaterialKey(Guid, SlotIndex);
    TObjectPtr<UMaterialInstanceDynamic>* Existing = GeneratedMaterialCache.Find(Key);
    if (Existing && *Existing)
    {
        // Update existing MID params
        UMaterialInstanceDynamic* MID = *Existing;
        MID->SetVectorParameterValue(FName("Base Color"), Props.BaseColor);
        MID->SetVectorParameterValue(FName("BaseColor"), Props.BaseColor);
        MID->SetVectorParameterValue(FName("Color"), Props.BaseColor);
        MID->SetVectorParameterValue(FName("Diffuse Color"), Props.BaseColor);
        MID->SetVectorParameterValue(FName("DiffuseColor"), Props.BaseColor);
        MID->SetScalarParameterValue(FName("Roughness"), Props.Roughness);
        MID->SetScalarParameterValue(FName("Metallic"), Props.Metallic);
        MID->SetScalarParameterValue(FName("Alpha"), Props.Alpha);
        MID->SetScalarParameterValue(FName("Opacity"), Props.Alpha);
        return MID;
    }

    // Phase 10K.4: try to use LiveSync master material
    UMaterialInterface* BaseMat = GetOrCreateLiveSyncMasterMaterial();
    if (!BaseMat)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[MATERIAL] Failed to get master material for generated MID"));
        return nullptr;
    }

    UMaterialInstanceDynamic* MID = UMaterialInstanceDynamic::Create(BaseMat, this);
    if (!MID)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[MATERIAL] Failed to create generated MID for GUID=%s slot=%d"),
            *Guid.ToString(EGuidFormats::Digits), SlotIndex);
        return nullptr;
    }

    // Phase 10J.5L: name the MID clearly for UE material list visibility
    const FString GuidShort = Guid.ToString(EGuidFormats::Short);
    MID->Rename(*FString::Printf(TEXT("MID_UELiveSync_%s_%d"), *GuidShort, SlotIndex), this);

    // Phase 10K.4: log parent material
    FString ParentMatName = BaseMat ? BaseMat->GetPathName() : TEXT("null");
    UE_LOG(LogLiveSync, Log,
        TEXT("[MAT][GEN_PARENT] guid=%s slot=%d parent=%s"),
        *Guid.ToString(EGuidFormats::Digits), SlotIndex, *ParentMatName);

    MID->SetVectorParameterValue(FName("Base Color"), Props.BaseColor);
    MID->SetVectorParameterValue(FName("BaseColor"), Props.BaseColor);
    MID->SetVectorParameterValue(FName("Color"), Props.BaseColor);
    MID->SetVectorParameterValue(FName("Diffuse Color"), Props.BaseColor);
    MID->SetVectorParameterValue(FName("DiffuseColor"), Props.BaseColor);
    MID->SetScalarParameterValue(FName("Roughness"), Props.Roughness);
    MID->SetScalarParameterValue(FName("Metallic"), Props.Metallic);
    MID->SetScalarParameterValue(FName("Alpha"), Props.Alpha);
    MID->SetScalarParameterValue(FName("Opacity"), Props.Alpha);

    MID->SetFlags(RF_Transient);

    GeneratedMaterialCache.Add(Key, MID);

    UE_LOG(LogLiveSync, Log,
        TEXT("[MAT][GEN] create guid=%s slot=%d name=MID_UELiveSync_%s_%d "
             "color=(%.1f,%.1f,%.1f,%.1f) roughness=%.3f metallic=%.3f"),
        *Guid.ToString(EGuidFormats::Digits), SlotIndex,
        *GuidShort, SlotIndex,
        Props.BaseColor.R, Props.BaseColor.G, Props.BaseColor.B, Props.BaseColor.A,
        Props.Roughness, Props.Metallic);

    return MID;
}


// =========================================================
// COPY IMPORTED TEXTURES TO PARAM MID (Phase 7H hotfix)
// =========================================================
// Enumerates texture parameters on the imported FBX material
// and copies matching textures to the LiveSync master-material
// MID by channel convention (BaseColor, Roughness, Metallic,
// Normal, Alpha).

void UUELiveSyncSubsystem::
CopyImportedTexturesFromParent(
    UMaterialInstanceDynamic* LiveSyncMID,
    UMaterialInterface* ImportedParentMat,
    const FGuid& Guid,
    int32 SlotIndex)
{
    if (!LiveSyncMID || !ImportedParentMat)
    {
        return;
    }

    const FString GuidStr = Guid.ToString(EGuidFormats::Digits);

    struct FTextureChannelEntry
    {
        const TCHAR* LiveSyncParamName;
        const TCHAR* ToggleParamName;
        const TCHAR* ImportedNameHint;
    };
    static const FTextureChannelEntry ChannelsToCopy[] = {
        { TEXT("BaseColorTexture"),  TEXT("UseBaseColorTexture"),  TEXT("BaseColor") },
        { TEXT("BaseColorTexture"),  TEXT("UseBaseColorTexture"),  TEXT("Albedo") },
        { TEXT("BaseColorTexture"),  TEXT("UseBaseColorTexture"),  TEXT("Diffuse") },
        { TEXT("RoughnessTexture"),  TEXT("UseRoughnessTexture"),  TEXT("Roughness") },
        { TEXT("MetallicTexture"),   TEXT("UseMetallicTexture"),   TEXT("Metallic") },
        { TEXT("MetallicTexture"),   TEXT("UseMetallicTexture"),   TEXT("Metalness") },
        { TEXT("NormalTexture"),     TEXT("UseNormalTexture"),     TEXT("Normal") },
        { TEXT("NormalTexture"),     TEXT("UseNormalTexture"),     TEXT("Bump") },
        { TEXT("AlphaTexture"),      TEXT("UseAlphaTexture"),      TEXT("Alpha") },
        { TEXT("AlphaTexture"),      TEXT("UseAlphaTexture"),      TEXT("Opacity") },
    };

    TMap<FName, UTexture*> FoundTextures;
    TSet<FName> AlreadySet;

    // Try known texture parameter names via GetTextureParameterValue.
    for (const FTextureChannelEntry& Entry : ChannelsToCopy)
    {
        FName LiveSyncParam(Entry.LiveSyncParamName);
        if (AlreadySet.Contains(LiveSyncParam))
        {
            continue;
        }

        UTexture* Tex = nullptr;
        FName HintName(Entry.ImportedNameHint);
        ImportedParentMat->GetTextureParameterValue(HintName, Tex);
        if (!Tex)
        {
            ImportedParentMat->GetTextureParameterValue(LiveSyncParam, Tex);
        }
        if (Tex)
        {
            FoundTextures.Add(LiveSyncParam, Tex);
            AlreadySet.Add(LiveSyncParam);
        }
    }

    // Task 8B: Speculative folder-scanning heuristic removed.
    // Folder scanning may only help resolve an exact texture record.
    // It must never create a channel assignment.
    // Parameter-based discovery above is the sole import-source.
#if WITH_EDITOR
    if (FoundTextures.Num() == 0)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[MATERIAL][COPY_NO_IMPORT_PARAMS] guid=%s slot=%d reason=no_matched_import_params"),
            *GuidStr, SlotIndex);
    }
#endif

    // Apply found textures to LiveSync MID
    int32 CopiedCount = 0;
    for (const auto& Kvp : FoundTextures)
    {
        FName LiveSyncParam = Kvp.Key;
        UTexture* FoundTexture = Kvp.Value;
        LiveSyncMID->SetTextureParameterValue(LiveSyncParam, FoundTexture);
        CopiedCount++;

        // Set toggle scalar so the texture lerp uses the texture
        for (const FTextureChannelEntry& Entry : ChannelsToCopy)
        {
            if (LiveSyncParam == FName(Entry.LiveSyncParamName))
            {
                FName Toggle(Entry.ToggleParamName);
                LiveSyncMID->SetScalarParameterValue(Toggle, 1.0f);
                break;
            }
        }

        UE_LOG(LogLiveSync, Log,
            TEXT("[MATERIAL][IMPORTED_TEXTURE_TO_PARAM] guid=%s slot=%d "
                 "liveSyncParam=%s texture=%s"),
            *GuidStr, SlotIndex,
            *LiveSyncParam.ToString(),
            *FoundTexture->GetName());
    }

    if (CopiedCount == 0)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[MATERIAL][NO_IMPORTED_TEXTURE_FOUND] guid=%s slot=%d parent=%s reason=no_texture_asset_candidate"),
            *GuidStr, SlotIndex,
            *ImportedParentMat->GetPathName());
    }
    else
    {
        // Task E: readback log after texture binding to verify correctness
        UTexture* CheckTex = nullptr;
        LiveSyncMID->GetTextureParameterValue(TEXT("BaseColorTexture"), CheckTex);
        float CheckUseBaseColor = 0.0f;
        LiveSyncMID->GetScalarParameterValue(TEXT("UseBaseColorTexture"), CheckUseBaseColor);

        UE_LOG(LogLiveSync, Log,
            TEXT("[MATERIAL][TEXTURE_PARAM_READBACK] guid=%s slot=%d param=BaseColorTexture bound=%d texture=%s"),
            *GuidStr, SlotIndex,
            CheckTex ? 1 : 0,
            CheckTex ? *CheckTex->GetName() : TEXT("none"));

        UE_LOG(LogLiveSync, Log,
            TEXT("[MATERIAL][TEXTURE_TOGGLE_READBACK] guid=%s slot=%d param=UseBaseColorTexture value=%d"),
            *GuidStr, SlotIndex,
            CheckUseBaseColor > 0.5f ? 1 : 0);

        // Phase 7H.7: VALUE_PARAM_READBACK for scalar values
        float CheckBaseColorR = 0.0f, CheckBaseColorG = 0.0f, CheckBaseColorB = 0.0f, CheckAlpha = 0.0f;
        LiveSyncMID->GetScalarParameterValue(TEXT("BaseColorR"), CheckBaseColorR);
        LiveSyncMID->GetScalarParameterValue(TEXT("BaseColorG"), CheckBaseColorG);
        LiveSyncMID->GetScalarParameterValue(TEXT("BaseColorB"), CheckBaseColorB);
        LiveSyncMID->GetScalarParameterValue(TEXT("Alpha"), CheckAlpha);
        float CheckRoughness = 0.0f;
        LiveSyncMID->GetScalarParameterValue(TEXT("Roughness"), CheckRoughness);
        float CheckMetallic = 0.0f;
        LiveSyncMID->GetScalarParameterValue(TEXT("Metallic"), CheckMetallic);

        UE_LOG(LogLiveSync, Log,
            TEXT("[MATERIAL][VALUE_PARAM_READBACK] guid=%s slot=%d param=BaseColor value=(%.3f,%.3f,%.3f,%.3f)"),
            *GuidStr, SlotIndex, CheckBaseColorR, CheckBaseColorG, CheckBaseColorB, CheckAlpha);
        UE_LOG(LogLiveSync, Log,
            TEXT("[MATERIAL][VALUE_PARAM_READBACK] guid=%s slot=%d param=Roughness value=%.3f"),
            *GuidStr, SlotIndex, CheckRoughness);
        UE_LOG(LogLiveSync, Log,
            TEXT("[MATERIAL][VALUE_PARAM_READBACK] guid=%s slot=%d param=Metallic value=%.3f"),
            *GuidStr, SlotIndex, CheckMetallic);
    }

    MaterialImportedTextureCopied += CopiedCount;
}


// =========================================================
// PARSE AND APPLY GENERATED MATERIAL (Phase 10J.5H)
// =========================================================
// Task 8B: delegates to ApplyGeneratedMaterialFromResolvedState.
// Legacy path removed. One authoritative apply per slot.

bool UUELiveSyncSubsystem::
ParseAndApplyGeneratedMaterial(
    const FGuid& Guid,
    const TArray<FMaterialSlotBasicProperties>& BasicProps,
    const TArray<FMaterialSlotRef>& Slots)
{
    // Task 8B: Count effective slots from parsed MATX properties.
    int32 MatxPropertySlots = 0;
    for (const FMaterialSlotBasicProperties& BP : BasicProps)
    {
        if (BP.bHasProperties) MatxPropertySlots++;
    }

    // Task 8B: derive effectiveSlotCount from MATX parsed properties,
    // not from legacy ObjectCount (which may be 1 on early packets).
    int32 EffectiveSlotCount = MatxPropertySlots;
    if (EffectiveSlotCount == 0) EffectiveSlotCount = BasicProps.Num();

    // Task 9A: deferred replay — wait for mesh slots to be ready.
    AActor* Actor = FindActorFast(Guid);
    if (Actor)
    {
        UStaticMeshComponent* SMC = Actor->FindComponentByClass<UStaticMeshComponent>();
        if (SMC)
        {
            int32 MeshSlots = SMC->GetNumMaterials();
            if (MeshSlots < EffectiveSlotCount)
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MATERIAL][MATX_DEFER] guid=%s payloadSlots=%d meshSlots=%d reason=mesh_slot_count_not_ready"),
                    *Guid.ToString(EGuidFormats::Digits), EffectiveSlotCount, MeshSlots);

                // Phase 10J.5D: replay buffer already handles deferred re-application.
                // Log early state for diagnostic purposes.
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MATERIAL][MATX_DEFER_STATE] guid=%s payloadSlots=%d meshSlots=%d appliedSlots=%d"),
                    *Guid.ToString(EGuidFormats::Digits),
                    EffectiveSlotCount, MeshSlots, MeshSlots > 0 ? MeshSlots : 0);

                return false;
            }
        }
    }

    // Task 9A: apply persistent material or MID fallback per slot.
    // For each slot: if Blender material identity is present → persistent MIC.
    // If identity is absent (empty slot) → MID fallback.
    return ApplyMaterialSnapshotPerSlot(Guid, BasicProps, Slots, EffectiveSlotCount);
}


// =========================================================
// TASK 8B — EXACT TEXTURE RESOLUTION (basename/path match)
// =========================================================

UTexture2D* UUELiveSyncSubsystem::
ResolveTextureByExactName(
    const FString& ImageName,
    const FString& Path) const
{
    // 1. Check TextureImportCache by path (exact path match).
    if (Path.Len() > 0)
    {
        if (const TSoftObjectPtr<UTexture2D>* Cached = TextureImportCache.Find(Path))
        {
            if (Cached->IsValid()) return Cast<UTexture2D>(Cached->Get());
        }
    }

    // 2. Exact normalized basename match: "Wood.png" → "Wood".
    if (ImageName.Len() > 0)
    {
        FString BaseName = FPaths::GetBaseFilename(ImageName);
        for (const auto& Kvp : TextureImportCache)
        {
            // Check by full path basename
            if (Kvp.Key.Len() > 0)
            {
                FString CachedBaseName = FPaths::GetBaseFilename(Kvp.Key);
                if (CachedBaseName == BaseName)
                {
                    if (Kvp.Value.IsValid()) return Cast<UTexture2D>(Kvp.Value.Get());
                }
            }
            // Check by asset name
            if (Kvp.Value.IsValid())
            {
                UTexture2D* Loaded = Cast<UTexture2D>(Kvp.Value.Get());
                if (Loaded && Loaded->GetName() == BaseName)
                {
                    return Loaded;
                }
            }
        }
    }

    return nullptr;
}


// =========================================================
// TASK 9A — APPLY MATERIAL SNAPSHOT PER SLOT
// =========================================================
// Task 9A: Per-slot dispatch — persistent MIC or MID fallback.
// For each slot, checks material identity from Blender.
// If identity present → persistent MIC.
// If identity absent (empty slot) → MID fallback.

bool UUELiveSyncSubsystem::
ApplyMaterialSnapshotPerSlot(
    const FGuid& Guid,
    const TArray<FMaterialSlotBasicProperties>& BasicProps,
    const TArray<FMaterialSlotRef>& Slots,
    int32 EffectiveSlotCount)
{
    AActor* Actor = FindActorFast(Guid);
    if (!Actor)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[MATERIAL][MATX_APPLY] guid=%s reason=no_actor"),
            *Guid.ToString(EGuidFormats::Digits));
        return false;
    }

    UStaticMeshComponent* SMC = Actor->FindComponentByClass<UStaticMeshComponent>();
    if (!SMC)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[MATERIAL][MATX_APPLY] guid=%s reason=no_static_mesh_component"),
            *Guid.ToString(EGuidFormats::Digits));
        return false;
    }

    const FString GuidStr = Guid.ToString(EGuidFormats::Digits);

    // Per-slot dispatch.
    int32 PersistentApplied = 0;
    int32 MidApplied = 0;
    int32 TotalTexturesApplied = 0;
    int32 TotalTextureMisses = 0;

    // Resolve MTEX textures via per-GUID sidecar map.
    const TArray<FMaterialTextureMapRef>* TexMaps = MaterialTextureMapCache.Find(Guid);

    for (int32 SlotIdx = 0; SlotIdx < EffectiveSlotCount; SlotIdx++)
    {
        if (SlotIdx >= SMC->GetNumMaterials())
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[MATERIAL][MATX_APPLY] guid=%s slot=%d result=slot_out_of_range meshSlots=%d"),
                *GuidStr, SlotIdx, SMC->GetNumMaterials());
            continue;
        }

        // Get authoritative MATX properties for this slot.
        const FMaterialSlotBasicProperties& Props =
            SlotIdx < BasicProps.Num() ? BasicProps[SlotIdx] : FMaterialSlotBasicProperties();

        // Check if this slot has a Blender material identity.
        const FMaterialSlotRef* SlotRef = nullptr;
        if (SlotIdx < Slots.Num())
        {
            SlotRef = &Slots[SlotIdx];
        }

        // Task 9A: persistent material or MID fallback.
        UMaterialInterface* MatAsset = nullptr;
        UMaterialInstanceDynamic* MID = nullptr;

        if (SlotRef && SlotRef->Identity.IsValid())
        {
            // Persistent material authority.
            UE_LOG(LogLiveSync, Log,
                TEXT("[MAT][AUTH] guid=%s slot=%d authority=persistent_material"),
                *GuidStr, SlotIdx);

            // Get material name from the existing asset or use a default.
            FString MatName = TEXT("");
            UMaterialInstanceConstant* Existing = ResolvePersistentMaterialAsset(SlotRef->Identity);
            if (Existing)
            {
                MatName = Existing->GetName();
            }

            UMaterialInstanceConstant* PersistentMIC = GetOrCreatePersistentMaterialAsset(
                Guid, SlotIdx, SlotRef->Identity, MatName);

            if (PersistentMIC)
            {
                // Task 9B: apply sidecar textures for this specific slot.
                int32 SlotTexturesApplied = 0;
                int32 SlotTextureMisses = 0;

                if (TexMaps)
                {
                    // Filter TexMaps for this slot and apply sidecar textures.
                    TArray<FMaterialTextureMapRef> SlotTexMaps;
                    for (const FMaterialTextureMapRef& TexRef : *TexMaps)
                    {
                        if (TexRef.SlotIndex == SlotIdx && TexRef.IsValid())
                        {
                            SlotTexMaps.Add(TexRef);
                        }
                    }

                    ApplySidecarTexturesToPersistentMIC(Guid, SlotIdx, PersistentMIC, SlotTexMaps, SlotTexturesApplied, SlotTextureMisses);
                }

                // Apply scalar state (always needed for persistent MIC).
                PersistentMIC->SetScalarParameterValueEditorOnly(FName(TEXT("Roughness")), Props.Roughness);
                PersistentMIC->SetScalarParameterValueEditorOnly(FName(TEXT("Metallic")), Props.Metallic);
                PersistentMIC->SetScalarParameterValueEditorOnly(FName(TEXT("Alpha")), Props.Alpha);
                PersistentMIC->SetVectorParameterValueEditorOnly(FName(TEXT("BaseColor")), Props.BaseColor);
                PersistentMIC->PostEditChange();

                SMC->SetMaterial(SlotIdx, PersistentMIC);
                PersistentApplied++;
                TotalTexturesApplied += SlotTexturesApplied;
                TotalTextureMisses += SlotTextureMisses;

                UE_LOG(LogLiveSync, Log,
                    TEXT("[MATERIAL][PERSISTENT_SLOT_OK] guid=%s slot=%d asset=%s roughness=%.3f metallic=%.3f textures_applied=%d misses=%d"),
                    *GuidStr, SlotIdx, *PersistentMIC->GetPathName(),
                    Props.Roughness, Props.Metallic, SlotTexturesApplied, SlotTextureMisses);

                // Task 9B.4: read back final MIC state for all channels.
                {
                    static const TCHAR* const RBChannelNames[] = {
                        TEXT("BaseColor"), TEXT("Roughness"), TEXT("Metallic"),
                        TEXT("Alpha"), TEXT("Normal")
                    };
                    static const int32 RBChannelCount = 5;
                    for (int32 rbi = 0; rbi < RBChannelCount; ++rbi)
                    {
                        const FString RBChan = FString(RBChannelNames[rbi]);
                        const FString RBParam = RBChan + TEXT("Texture");
                        const FString RBToggle = TEXT("Use") + RBChan + TEXT("Texture");

                        UTexture* RBTex = nullptr;
                        float RBUse = 0.0f;
                        float RBScalar = 0.0f;
                        PersistentMIC->GetTextureParameterValue(FName(*RBParam), RBTex);
                        PersistentMIC->GetScalarParameterValue(FName(*RBToggle), RBUse);
                        PersistentMIC->GetScalarParameterValue(FName(*RBChan), RBScalar);

                        UE_LOG(LogLiveSync, Log,
                            TEXT("[MATERIAL][PERSISTENT_MIC_READBACK] guid=%s slot=%d channel=[%s] resolved=%s useTexture=%.1f scalar=%.3f"),
                            *GuidStr, SlotIdx, RBChannelNames[rbi],
                            RBTex ? *RBTex->GetPathName() : TEXT("null"),
                            RBUse, RBScalar);
                    }
                }
            }
        }
        else
        {
            // Empty slot — MID fallback.
            UE_LOG(LogLiveSync, Log,
                TEXT("[MAT][AUTH] guid=%s slot=%d authority=generated_mid (empty_slot)"),
                *GuidStr, SlotIdx);

            MID = GetOrCreateGeneratedMID(Guid, SlotIdx, Props);
            if (MID)
            {
                // Apply scalar params (no textures for empty slot).
                MID->SetVectorParameterValue(FName("BaseColor"), FLinearColor::White);
                MID->SetScalarParameterValue(FName("Roughness"), Props.Roughness);
                MID->SetScalarParameterValue(FName("Metallic"), Props.Metallic);
                MID->SetScalarParameterValue(FName("Alpha"), Props.Alpha);
                MID->SetScalarParameterValue(FName("UseBaseColorTexture"), 0.0f);
                MID->SetScalarParameterValue(FName("UseRoughnessTexture"), 0.0f);
                MID->SetScalarParameterValue(FName("UseMetallicTexture"), 0.0f);
                MID->SetScalarParameterValue(FName("UseNormalTexture"), 0.0f);
                MID->SetScalarParameterValue(FName("UseAlphaTexture"), 0.0f);
                SMC->SetMaterial(SlotIdx, MID);
                MidApplied++;
            }
        }
    }

    int32 TotalApplied = PersistentApplied + MidApplied;

    UE_LOG(LogLiveSync, Log,
        TEXT("[MATERIAL][MATX_FULL_SNAPSHOT_APPLY] guid=%s effectiveSlots=%d meshSlots=%d appliedSlots=%d persistent=%d mid_fallback=%d texturesApplied=%d textureMisses=%d"),
        *GuidStr, EffectiveSlotCount, SMC->GetNumMaterials(), TotalApplied, PersistentApplied, MidApplied, TotalTexturesApplied, TotalTextureMisses);

    if (PersistentApplied > 0)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[MAT][AUTH] guid=%s persistent_applied=%d authority=persistent_material_assets"),
            *GuidStr, PersistentApplied);
    }

    return TotalApplied > 0;
}


// =========================================================
// TASK 8B — APPLY GENERATED MATERIAL FROM RESOLVED STATE
// =========================================================
// One authoritative apply pass per slot. Builds resolved state
// from MATX + MTEX, applies once, and logs the final state.

bool UUELiveSyncSubsystem::
ApplyGeneratedMaterialFromResolvedState(
    const FGuid& Guid,
    const TArray<FMaterialSlotBasicProperties>& BasicProps,
    int32 EffectiveSlotCount)
{
    AActor* Actor = FindActorFast(Guid);
    if (!Actor)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[MATERIAL][MATX_APPLY] guid=%s reason=no_actor"),
            *Guid.ToString(EGuidFormats::Digits));
        return false;
    }

    UStaticMeshComponent* SMC = Actor->FindComponentByClass<UStaticMeshComponent>();
    if (!SMC)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[MATERIAL][MATX_APPLY] guid=%s reason=no_static_mesh_component"),
            *Guid.ToString(EGuidFormats::Digits));
        return false;
    }

    const FString GuidStr = Guid.ToString(EGuidFormats::Digits);

    // Count effective slots from parsed MATX properties.
    int32 MatxPropertySlots = 0;
    for (const FMaterialSlotBasicProperties& BP : BasicProps)
    {
        if (BP.bHasProperties) MatxPropertySlots++;
    }

    UE_LOG(LogLiveSync, Log,
        TEXT("[MATERIAL][MATX_FULL_SNAPSHOT_RECV] guid=%s legacySlotCount=%d matxPropertySlots=%d effectiveSlots=%d textureRecords=%d"),
        *GuidStr,
        SMC->GetNumMaterials(),
        MatxPropertySlots,
        EffectiveSlotCount,
        MtexRecordsParsed);

    // Build resolved state per slot.
    TArray<FResolvedMaterialSlotState> ResolvedSlots;
    ResolvedSlots.Init(FResolvedMaterialSlotState(), EffectiveSlotCount);

    for (int32 SlotIdx = 0; SlotIdx < EffectiveSlotCount; SlotIdx++)
    {
        ResolvedSlots[SlotIdx].SlotIndex = SlotIdx;

        // Find MATX properties for this slot.
        const FMaterialSlotBasicProperties* Props = nullptr;
        for (const FMaterialSlotBasicProperties& BP : BasicProps)
        {
            if (BP.bHasProperties)
            {
                if (!Props) Props = &BP;
                // If multiple slots, find by iterating BasicProps in order.
                // BasicProps is indexed by slot position.
            }
        }

        // Get the right properties — BasicProps is ordered by slot index.
        if (SlotIdx < BasicProps.Num() && BasicProps[SlotIdx].bHasProperties)
        {
            Props = &BasicProps[SlotIdx];
        }

        if (Props)
        {
            ResolvedSlots[SlotIdx].BaseColor = Props->BaseColor;
            ResolvedSlots[SlotIdx].Roughness = Props->Roughness;
            ResolvedSlots[SlotIdx].Metallic = Props->Metallic;
            ResolvedSlots[SlotIdx].Alpha = Props->Alpha;
        }
    }

    // Resolve textures from MTEX records.
    const TArray<FMaterialTextureMapRef>* TexMaps = MaterialTextureMapCache.Find(Guid);
    if (TexMaps)
    {
        for (const FMaterialTextureMapRef& TexRef : *TexMaps)
        {
            if (TexRef.SlotIndex < 0 || TexRef.SlotIndex >= EffectiveSlotCount) continue;
            if (!TexRef.IsValid()) continue;

            int32 SlotIdx = TexRef.SlotIndex;
            FResolvedMaterialSlotState& SlotState = ResolvedSlots[SlotIdx];

            // Resolve texture by exact match.
            UTexture2D* Resolved = ResolveTextureByExactName(TexRef.ImageName, TexRef.Path);

            FString ResultStr = Resolved ? TEXT("resolved") : TEXT("missing");
            FString AssetStr = Resolved ? Resolved->GetPathName() : TEXT("none");
            UE_LOG(LogLiveSync, Log,
                TEXT("[MATERIAL][MATX_EXACT_TEXTURE_RESOLVE] guid=%s slot=%d channel=%u image=%s asset=%s result=%s"),
                *GuidStr, SlotIdx, TexRef.Channel, *TexRef.ImageName, *AssetStr, *ResultStr);

            if (!Resolved) continue;

            // Assign to the correct channel.
            switch (static_cast<EMTEXChannel>(TexRef.Channel))
            {
            case EMTEXChannel::BaseColor:
                SlotState.BaseColorTexture = Resolved;
                SlotState.bUseBaseColorTexture = true;
                break;
            case EMTEXChannel::Roughness:
                SlotState.RoughnessTexture = Resolved;
                SlotState.bUseRoughnessTexture = true;
                break;
            case EMTEXChannel::Metallic:
                SlotState.MetallicTexture = Resolved;
                SlotState.bUseMetallicTexture = true;
                break;
            case EMTEXChannel::Normal:
                SlotState.NormalTexture = Resolved;
                SlotState.bUseNormalTexture = true;
                break;
            case EMTEXChannel::Alpha:
                SlotState.AlphaTexture = Resolved;
                SlotState.bUseAlphaTexture = true;
                break;
            default:
                break;
            }
        }
    }

    // Apply resolved state to generated MIDs — one authoritative pass.
    int32 AppliedCount = 0;
    for (int32 SlotIdx = 0; SlotIdx < EffectiveSlotCount; SlotIdx++)
    {
        if (SlotIdx >= SMC->GetNumMaterials())
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[MATERIAL][MATX_APPLY] guid=%s slot=%d result=slot_out_of_range meshSlots=%d"),
                *GuidStr, SlotIdx, SMC->GetNumMaterials());
            continue;
        }

        const FResolvedMaterialSlotState& SlotState = ResolvedSlots[SlotIdx];

        // Get authoritative MATX properties for this slot.
        const FMaterialSlotBasicProperties& Props =
            SlotIdx < BasicProps.Num() ? BasicProps[SlotIdx] : FMaterialSlotBasicProperties();

        // Get or create the MID.
        UMaterialInstanceDynamic* MID = GetOrCreateGeneratedMID(Guid, SlotIdx, Props);
        if (!MID)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[MATERIAL][MATX_APPLY] guid=%s slot=%d reason=no_generated_mid"),
                *GuidStr, SlotIdx);
            continue;
        }

        // Set scalar values from MATX.
        MID->SetVectorParameterValue(FName("BaseColor"), SlotState.BaseColor);
        MID->SetScalarParameterValue(FName("Roughness"), SlotState.Roughness);
        MID->SetScalarParameterValue(FName("Metallic"), SlotState.Metallic);
        MID->SetScalarParameterValue(FName("Alpha"), SlotState.Alpha);

        // Set texture parameters and toggles.
        MID->SetTextureParameterValue(FName("BaseColorTexture"), SlotState.BaseColorTexture);
        MID->SetScalarParameterValue(FName("UseBaseColorTexture"), SlotState.bUseBaseColorTexture ? 1.0f : 0.0f);
        MID->SetTextureParameterValue(FName("RoughnessTexture"), SlotState.RoughnessTexture);
        MID->SetScalarParameterValue(FName("UseRoughnessTexture"), SlotState.bUseRoughnessTexture ? 1.0f : 0.0f);
        MID->SetTextureParameterValue(FName("MetallicTexture"), SlotState.MetallicTexture);
        MID->SetScalarParameterValue(FName("UseMetallicTexture"), SlotState.bUseMetallicTexture ? 1.0f : 0.0f);
        MID->SetTextureParameterValue(FName("NormalTexture"), SlotState.NormalTexture);
        MID->SetScalarParameterValue(FName("UseNormalTexture"), SlotState.bUseNormalTexture ? 1.0f : 0.0f);
        MID->SetTextureParameterValue(FName("AlphaTexture"), SlotState.AlphaTexture);
        MID->SetScalarParameterValue(FName("UseAlphaTexture"), SlotState.bUseAlphaTexture ? 1.0f : 0.0f);

        // Log scalar values.
        UE_LOG(LogLiveSync, Log,
            TEXT("[MATERIAL][MATX_SLOT_VALUES] guid=%s slot=%d Roughness=%.3f Metallic=%.3f Alpha=%.3f"),
            *GuidStr, SlotIdx, SlotState.Roughness, SlotState.Metallic, SlotState.Alpha);

        // Log texture state.
        UE_LOG(LogLiveSync, Log,
            TEXT("[MATERIAL][MATX_SLOT_TEXTURES] guid=%s slot=%d BaseColor=%d Roughness=%d Metallic=%d Normal=%d Alpha=%d"),
            *GuidStr, SlotIdx,
            SlotState.bUseBaseColorTexture ? 1 : 0,
            SlotState.bUseRoughnessTexture ? 1 : 0,
            SlotState.bUseMetallicTexture ? 1 : 0,
            SlotState.bUseNormalTexture ? 1 : 0,
            SlotState.bUseAlphaTexture ? 1 : 0);

        // Log individual texture applies.
        if (SlotState.BaseColorTexture)
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[MATERIAL][MATX_SLOT_APPLY_OK] guid=%s slot=%d mid=%s BaseColor=%s"),
                *GuidStr, SlotIdx, *MID->GetName(), *SlotState.BaseColorTexture->GetPathName());
        }
        if (SlotState.RoughnessTexture)
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[MATERIAL][MATX_SLOT_APPLY_OK] guid=%s slot=%d mid=%s Roughness=%s"),
                *GuidStr, SlotIdx, *MID->GetName(), *SlotState.RoughnessTexture->GetPathName());
        }
        if (SlotState.NormalTexture)
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[MATERIAL][MATX_SLOT_APPLY_OK] guid=%s slot=%d mid=%s Normal=%s"),
                *GuidStr, SlotIdx, *MID->GetName(), *SlotState.NormalTexture->GetPathName());
        }
        if (SlotState.MetallicTexture)
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[MATERIAL][MATX_SLOT_APPLY_OK] guid=%s slot=%d mid=%s Metallic=%s"),
                *GuidStr, SlotIdx, *MID->GetName(), *SlotState.MetallicTexture->GetPathName());
        }
        if (SlotState.AlphaTexture)
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[MATERIAL][MATX_SLOT_APPLY_OK] guid=%s slot=%d mid=%s Alpha=%s"),
                *GuidStr, SlotIdx, *MID->GetName(), *SlotState.AlphaTexture->GetPathName());
        }

        // Scalar-only slots: log no textures.
        if (!SlotState.bUseBaseColorTexture && !SlotState.bUseRoughnessTexture &&
            !SlotState.bUseMetallicTexture && !SlotState.bUseNormalTexture && !SlotState.bUseAlphaTexture)
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[MATERIAL][MATX_SLOT_SCALAR_ONLY] guid=%s slot=%d mid=%s"),
                *GuidStr, SlotIdx, *MID->GetName());
        }

        SMC->SetMaterial(SlotIdx, MID);
        AppliedCount++;
    }

    UE_LOG(LogLiveSync, Log,
        TEXT("[MATERIAL][MATX_FULL_SNAPSHOT_APPLY] guid=%s payloadSlots=%d meshSlots=%d appliedSlots=%d"),
        *GuidStr, MatxPropertySlots, SMC->GetNumMaterials(), AppliedCount);

    if (AppliedCount > 0)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[MAT][AUTH] guid=%s slot_count=%d authority=generated_mid"),
            *GuidStr, EffectiveSlotCount);
        MaterialGeneratedApplied += AppliedCount;
        return true;
    }

    return false;
}


// =========================================================
// PHASE 7H TASK 9A — PERSISTENT MATERIAL AUTHORITY
// =========================================================

UMaterialInstanceConstant* UUELiveSyncSubsystem::
GetOrCreatePersistentMaterialAsset(
    const FGuid& ObjectGuid,
    int32 SlotIndex,
    const FMaterialIdentityRef& MaterialIdentity,
    const FString& MaterialName)
{
    // 1. Check if we already have a persistent path for this identity.
    UMaterialInstanceConstant* Existing = ResolvePersistentMaterialAsset(MaterialIdentity);
    if (Existing)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[MAT][PERSISTENT] guid=%s slot=%d action=reuse identity=0x%llx%llx path=%s"),
            *ObjectGuid.ToString(EGuidFormats::Digits), SlotIndex,
            MaterialIdentity.High, MaterialIdentity.Low,
            *Existing->GetPathName());
        return Existing;
    }

    // 2. Determine package path for the persistent MIC.
    const FString AssetBasePath = TEXT("/Game/UELiveSync/Imported/Materials");
    const FString IdentityHash = FString::Printf(TEXT("%016llx%016llx"), MaterialIdentity.High, MaterialIdentity.Low);
    const FString AssetName = FString::Printf(TEXT("MI_UELiveSync_%s_%d"), *IdentityHash, SlotIndex);
    const FString PackagePath = AssetBasePath / AssetName;

    // 3. Try to load existing asset.
    UMaterialInstanceConstant* MIC = Cast<UMaterialInstanceConstant>(StaticLoadObject(
        UMaterialInstanceConstant::StaticClass(),
        nullptr,
        *PackagePath));

    if (MIC)
    {
        // Reuse existing persistent MIC.
        RegisterPersistentMaterialPath(MaterialIdentity, FSoftObjectPath(PackagePath));
        UE_LOG(LogLiveSync, Log,
            TEXT("[MAT][PERSISTENT] guid=%s slot=%d action=reload identity=0x%llx%llx path=%s"),
            *ObjectGuid.ToString(EGuidFormats::Digits), SlotIndex,
            MaterialIdentity.High, MaterialIdentity.Low,
            *MIC->GetPathName());
        return MIC;
    }

    // 4. Get master material as parent.
    UMaterialInterface* MasterMat = GetOrCreateLiveSyncMasterMaterial();
    if (!MasterMat)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[MAT][PERSISTENT] guid=%s slot=%d reason=master_material_missing"),
            *ObjectGuid.ToString(EGuidFormats::Digits), SlotIndex);
        return nullptr;
    }

    // 5. Create package and MIC.
    UPackage* Package = CreatePackage(*PackagePath);
    if (!Package)
    {
        UE_LOG(LogLiveSync, Error,
            TEXT("[MAT][PERSISTENT] guid=%s slot=%d reason=create_package_failed"),
            *ObjectGuid.ToString(EGuidFormats::Digits), SlotIndex);
        return nullptr;
    }

    UMaterialInstanceConstant* NewMIC = NewObject<UMaterialInstanceConstant>(
        Package,
        FName(*AssetName),
        RF_Public | RF_Standalone | RF_MarkAsRootSet);

    if (!NewMIC)
    {
        UE_LOG(LogLiveSync, Error,
            TEXT("[MAT][PERSISTENT] guid=%s slot=%d reason=create_mic_failed"),
            *ObjectGuid.ToString(EGuidFormats::Digits), SlotIndex);
        return nullptr;
    }

    NewMIC->SetParentEditorOnly(MasterMat);

    // Save the new asset immediately.
    NewMIC->PostEditChange();
    NewMIC->MarkPackageDirty();

    FString FilePath = FPackageName::LongPackageNameToFilename(
        PackagePath, FPackageName::GetAssetPackageExtension());
    if (!FilePath.IsEmpty())
    {
        FSavePackageArgs SaveArgs;
        SaveArgs.TopLevelFlags = RF_Standalone;
        SaveArgs.SaveFlags = SAVE_NoError;
        UPackage::SavePackage(Package, NewMIC, *FilePath, SaveArgs);
    }

    UE_LOG(LogLiveSync, Log,
        TEXT("[MAT][PERSISTENT] guid=%s slot=%d action=create identity=0x%llx%llx name=%s path=%s"),
        *ObjectGuid.ToString(EGuidFormats::Digits), SlotIndex,
        MaterialIdentity.High, MaterialIdentity.Low,
        *AssetName, *PackagePath);

    // Register in path cache for future lookups.
    RegisterPersistentMaterialPath(MaterialIdentity, FSoftObjectPath(PackagePath));

    return NewMIC;
}


bool UUELiveSyncSubsystem::
ApplyFullMaterialSnapshotToPersistentAsset(
    UMaterialInstanceConstant* MaterialAsset,
    const FMaterialSlotBasicProperties& ScalarState,
    const TMap<uint8, UTexture2D*>& TextureState)
{
    if (!MaterialAsset)
    {
        return false;
    }

    // Apply scalar/color parameters from Blender.
    MaterialAsset->SetScalarParameterValueEditorOnly(FName(TEXT("Roughness")), ScalarState.Roughness);
    MaterialAsset->SetScalarParameterValueEditorOnly(FName(TEXT("Metallic")), ScalarState.Metallic);
    MaterialAsset->SetScalarParameterValueEditorOnly(FName(TEXT("Alpha")), ScalarState.Alpha);

    // Apply BaseColor vector parameter.
    MaterialAsset->SetVectorParameterValueEditorOnly(FName(TEXT("BaseColor")), ScalarState.BaseColor);

    // Apply texture-use toggles and texture parameters.
    // UseBaseColorTexture:
    {
        bool bHasTex = TextureState.Contains(static_cast<uint8>(EMTEXChannel::BaseColor)) && TextureState[static_cast<uint8>(EMTEXChannel::BaseColor)];
        MaterialAsset->SetScalarParameterValueEditorOnly(FName(TEXT("UseBaseColorTexture")), bHasTex ? 1.0f : 0.0f);
        if (bHasTex)
        {
            MaterialAsset->SetTextureParameterValueEditorOnly(FName(TEXT("BaseColorTexture")), TextureState[static_cast<uint8>(EMTEXChannel::BaseColor)]);
        }
        else
        {
            // Clear stale texture reference.
            MaterialAsset->SetTextureParameterValueEditorOnly(FName(TEXT("BaseColorTexture")), nullptr);
        }
    }

    // UseRoughnessTexture:
    {
        bool bHasTex = TextureState.Contains(static_cast<uint8>(EMTEXChannel::Roughness)) && TextureState[static_cast<uint8>(EMTEXChannel::Roughness)];
        MaterialAsset->SetScalarParameterValueEditorOnly(FName(TEXT("UseRoughnessTexture")), bHasTex ? 1.0f : 0.0f);
        if (bHasTex)
        {
            MaterialAsset->SetTextureParameterValueEditorOnly(FName(TEXT("RoughnessTexture")), TextureState[static_cast<uint8>(EMTEXChannel::Roughness)]);
        }
        else
        {
            MaterialAsset->SetTextureParameterValueEditorOnly(FName(TEXT("RoughnessTexture")), nullptr);
        }
    }

    // UseMetallicTexture:
    {
        bool bHasTex = TextureState.Contains(static_cast<uint8>(EMTEXChannel::Metallic)) && TextureState[static_cast<uint8>(EMTEXChannel::Metallic)];
        MaterialAsset->SetScalarParameterValueEditorOnly(FName(TEXT("UseMetallicTexture")), bHasTex ? 1.0f : 0.0f);
        if (bHasTex)
        {
            MaterialAsset->SetTextureParameterValueEditorOnly(FName(TEXT("MetallicTexture")), TextureState[static_cast<uint8>(EMTEXChannel::Metallic)]);
        }
        else
        {
            MaterialAsset->SetTextureParameterValueEditorOnly(FName(TEXT("MetallicTexture")), nullptr);
        }
    }

    // UseNormalTexture:
    {
        bool bHasTex = TextureState.Contains(static_cast<uint8>(EMTEXChannel::Normal)) && TextureState[static_cast<uint8>(EMTEXChannel::Normal)];
        MaterialAsset->SetScalarParameterValueEditorOnly(FName(TEXT("UseNormalTexture")), bHasTex ? 1.0f : 0.0f);
        if (bHasTex)
        {
            MaterialAsset->SetTextureParameterValueEditorOnly(FName(TEXT("NormalTexture")), TextureState[static_cast<uint8>(EMTEXChannel::Normal)]);
        }
        else
        {
            MaterialAsset->SetTextureParameterValueEditorOnly(FName(TEXT("NormalTexture")), nullptr);
        }
    }

    // UseAlphaTexture:
    {
        bool bHasTex = TextureState.Contains(static_cast<uint8>(EMTEXChannel::Alpha)) && TextureState[static_cast<uint8>(EMTEXChannel::Alpha)];
        MaterialAsset->SetScalarParameterValueEditorOnly(FName(TEXT("UseAlphaTexture")), bHasTex ? 1.0f : 0.0f);
        if (bHasTex)
        {
            MaterialAsset->SetTextureParameterValueEditorOnly(FName(TEXT("AlphaTexture")), TextureState[static_cast<uint8>(EMTEXChannel::Alpha)]);
        }
        else
        {
            MaterialAsset->SetTextureParameterValueEditorOnly(FName(TEXT("AlphaTexture")), nullptr);
        }
    }

    // Commit all changes at once.
    MaterialAsset->PostEditChange();
    MaterialAsset->MarkPackageDirty();

    UE_LOG(LogLiveSync, Log,
        TEXT("[MAT][PERSISTENT_APPLY] action=apply path=%s "
             "roughness=%.3f metallic=%.3f alpha=%.3f textures_applied=%d"),
        *MaterialAsset->GetPathName(),
        ScalarState.Roughness, ScalarState.Metallic, ScalarState.Alpha,
        TextureState.Num());

    return true;
}


void UUELiveSyncSubsystem::
RegisterPersistentMaterialPath(
    const FMaterialIdentityRef& Identity,
    const FSoftObjectPath& Path)
{
    CacheMaterialPath(Identity, Path);
    UE_LOG(LogLiveSync, Log,
        TEXT("[MAT][PERSISTENT_REG] identity=0x%llx%llx path=%s"),
        (uint64)Identity.High, (uint64)Identity.Low, *Path.ToString());
}


// =========================================================
// TASK 9B — APPLY SIDECAR TEXTURES TO PERSISTENT MIC
// =========================================================

bool UUELiveSyncSubsystem::
ApplySidecarTexturesToPersistentMIC(
    const FGuid& Guid,
    int32 SlotIdx,
    class UMaterialInstanceConstant* MIC,
    const TArray<FMaterialTextureMapRef>& TexMaps,
    int32& TexturesAppliedOut,
    int32& TextureMissesOut)
{
    TexturesAppliedOut = 0;
    TextureMissesOut = 0;

    if (!MIC) return false;

    const FString GuidStr = Guid.ToString(EGuidFormats::Digits);
    const TCHAR* const ChannelNameArr[] = {
        TEXT("BaseColor"), TEXT("Roughness"), TEXT("Metallic"),
        TEXT("Alpha"), TEXT("Normal")
    };
    const int32 ChannelNameArrCount = 5;

    // Look up per-GUID sidecar map.
    const TMap<FString, TSoftObjectPtr<UTexture2D>>* SidecarMap = ImportedSidecarTexturesByGuid.Find(Guid);

    // Task 9B.4: build set of channels present in this snapshot.
    TSet<uint8> PresentChannels;
    for (const FMaterialTextureMapRef& TexRef : TexMaps)
    {
        if (TexRef.IsValid())
        {
            PresentChannels.Add(static_cast<uint8>(TexRef.Channel));
        }
    }

    // Pre-clear pass: for each supported channel not in the snapshot,
    // clear stale texture override + disable toggle.
    // Key insight: GetTextureParameterValue/GetScalarParameterValue return
    // inherited values from parent, not just local overrides. We must check
    // the MIC's internal TextureParameterValues/ScalarParameterValues arrays
    // to detect explicit local overrides.
    for (int32 i = 0; i < ChannelNameArrCount; ++i)
    {
        const uint8 ChanNum = static_cast<uint8>(i) + 1;
        if (PresentChannels.Contains(ChanNum))
        {
            continue;
        }

        const FString ChannelName = FString(ChannelNameArr[i]);
        const FString ParamName = ChannelName + TEXT("Texture");
        const FString ToggleName = TEXT("Use") + ChannelName + TEXT("Texture");

        // Audit readback — resolved (inherited) values
        UTexture* AuditCurrentTex = nullptr;
        float AuditCurrentUse = 0.0f;
        MIC->GetTextureParameterValue(FName(*ParamName), AuditCurrentTex);
        MIC->GetScalarParameterValue(FName(*ToggleName), AuditCurrentUse);

        // Check for EXPLICIT local overrides in MIC's internal arrays
        bool bHasTextureOverride = false;
        bool bHasScalarOverride = false;
        UTexture* ExplicitOverrideTex = nullptr;
        float ExplicitOverrideUse = 0.0f;

        // TextureParameterValues: TArray<FTextureParameterValue>
        {
            const TArray<FTextureParameterValue>& TexParams = MIC->TextureParameterValues;
            for (const FTextureParameterValue& TexPV : TexParams)
            {
                if (TexPV.ParameterInfo.Name == FName(*ParamName))
                {
                    bHasTextureOverride = true;
                    ExplicitOverrideTex = TexPV.ParameterValue;
                    break;
                }
            }
        }

        // ScalarParameterValues: TArray<FScalarParameterValue>
        {
            const TArray<FScalarParameterValue>& ScalarParams = MIC->ScalarParameterValues;
            for (const FScalarParameterValue& ScalarPV : ScalarParams)
            {
                if (ScalarPV.ParameterInfo.Name == FName(*ToggleName))
                {
                    bHasScalarOverride = true;
                    ExplicitOverrideUse = ScalarPV.ParameterValue;
                    break;
                }
            }
        }

        // Only act if there is an explicit local override to clear
        if (bHasTextureOverride || bHasScalarOverride)
        {
            const FString PreviousTexPath = ExplicitOverrideTex ? ExplicitOverrideTex->GetPathName() : TEXT("null");

            // Remove override entries from the MIC's internal arrays
            if (bHasTextureOverride)
            {
                TArray<FTextureParameterValue>& TexParams = MIC->TextureParameterValues;
                for (int32 ti = TexParams.Num() - 1; ti >= 0; --ti)
                {
                    if (TexParams[ti].ParameterInfo.Name == FName(*ParamName))
                    {
                        TexParams.RemoveAt(ti);
                        break;
                    }
                }
            }

            if (bHasScalarOverride)
            {
                TArray<FScalarParameterValue>& ScalarParams = MIC->ScalarParameterValues;
                for (int32 si = ScalarParams.Num() - 1; si >= 0; --si)
                {
                    if (ScalarParams[si].ParameterInfo.Name == FName(*ToggleName))
                    {
                        ScalarParams.RemoveAt(si);
                        break;
                    }
                }
            }

            // Also nullify via API to ensure editor refresh
            MIC->SetTextureParameterValueEditorOnly(FName(*ParamName), nullptr);
            MIC->SetScalarParameterValueEditorOnly(FName(*ToggleName), 0.0f);

            UE_LOG(LogLiveSync, Log,
                TEXT("[MATERIAL][PERSISTENT_MIC_CHANNEL] guid=%s slot=%d channel=[%s] action=preclear_stale previousTexture=%s previousUse=%.1f hasTextureOverride=%d hasScalarOverride=%d"),
                *GuidStr, SlotIdx, ChannelNameArr[i], *PreviousTexPath, ExplicitOverrideUse,
                bHasTextureOverride ? 1 : 0, bHasScalarOverride ? 1 : 0);
        }
        else
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[MATERIAL][PERSISTENT_MIC_CHANNEL] guid=%s slot=%d channel=[%s] action=noop_absent_already_clear resolvedTex=%s resolvedUse=%.1f"),
                *GuidStr, SlotIdx, ChannelNameArr[i],
                AuditCurrentTex ? *AuditCurrentTex->GetPathName() : TEXT("null"),
                AuditCurrentUse);
        }
    }

    for (const FMaterialTextureMapRef& TexRef : TexMaps)
    {
        // Task 9B.1: The caller (ApplyMaterialSnapshotPerSlot) already pre-filters
        // TexMaps to the current slot. Do NOT check SlotIndex/EffectiveSlotCount here.
        if (!TexRef.IsValid()) continue;

        const uint8 Channel = static_cast<uint8>(TexRef.Channel);
        // Task 9B.1: array has ChannelNameArrCount entries (0..4) for channels 1..5.
        // The old condition `Channel <= ChannelNameArrCount - 1` was off-by-one,
        // causing Normal (channel 5) to map to BaseColor (index 0).
        const int32 ChannelIdx = (Channel > 0 && Channel <= ChannelNameArrCount)
            ? (static_cast<int32>(Channel) - 1) : 0;
        const TCHAR* ChannelNameTchar = ChannelNameArr[ChannelIdx];
        const FString ChannelName = FString(ChannelNameArr[ChannelIdx]);

        // Task 9B: canonicalize ImageName for sidecar lookup.
        FString ImageNameKey = TexRef.ImageName.ToLower();
        // Strip extension.
        int32 DotIdx = INDEX_NONE;
        ImageNameKey.FindLastChar(TEXT('.'), DotIdx);
        if (DotIdx != INDEX_NONE)
        {
            ImageNameKey = ImageNameKey.Left(DotIdx);
        }

        // Also check normalized basename from Path.
        FString PathBase = FPaths::GetBaseFilename(TexRef.Path).ToLower();
        int32 PathDotIdx = INDEX_NONE;
        PathBase.FindLastChar(TEXT('.'), PathDotIdx);
        if (PathDotIdx != INDEX_NONE)
        {
            PathBase = PathBase.Left(PathDotIdx);
        }

        // Resolve from sidecar map by canonical key.
        TSoftObjectPtr<UTexture2D> ResolvedPtr;
        if (SidecarMap && SidecarMap->Contains(ImageNameKey))
        {
            ResolvedPtr = (*SidecarMap)[ImageNameKey];
        }
        else if (SidecarMap && SidecarMap->Contains(PathBase))
        {
            ResolvedPtr = (*SidecarMap)[PathBase];
        }
        else
        {
            // FBX _ncl1_1 alias fallback: try stripping _ncl1_1 from sidecar keys.
            if (SidecarMap)
            {
                for (const TPair<FString, TSoftObjectPtr<UTexture2D>>& Entry : *SidecarMap)
                {
                    FString AliasCandidate = Entry.Key;
                    const FString AliasSuffix = TEXT("_ncl1_1");
                    if (AliasCandidate.EndsWith(AliasSuffix))
                    {
                        AliasCandidate = AliasCandidate.Left(AliasCandidate.Len() - AliasSuffix.Len());
                    }
                    if (AliasCandidate == ImageNameKey || AliasCandidate == PathBase)
                    {
                        ResolvedPtr = Entry.Value;
                        break;
                    }
                }
            }
        }

        UTexture2D* ResolvedTex = ResolvedPtr.IsValid() ? Cast<UTexture2D>(ResolvedPtr.Get()) : nullptr;

        if (ResolvedTex)
        {
            // Set the texture parameter on MIC.
            const FString ParamName = ChannelName + TEXT("Texture");
            MIC->SetTextureParameterValueEditorOnly(FName(*ParamName), ResolvedTex);

            // Enable corresponding Use*Texture toggle.
            FString ToggleName = TEXT("Use") + ChannelName + TEXT("Texture");
            MIC->SetScalarParameterValueEditorOnly(FName(*ToggleName), 1.0f);

            // Read back final state for this channel
            UTexture* RBTexAfter = nullptr;
            float RBUseAfter = 0.0f;
            MIC->GetTextureParameterValue(FName(*ParamName), RBTexAfter);
            MIC->GetScalarParameterValue(FName(*ToggleName), RBUseAfter);

            const FString AssetPath = ResolvedTex->GetPathName();
            UE_LOG(LogLiveSync, Log,
                TEXT("[MATERIAL][PERSISTENT_MIC_CHANNEL] guid=%s slot=%d channel=[%s] action=set_texture texture=%s resolvedTextureAfter=%s useAfter=%.1f"),
                *GuidStr, TexRef.SlotIndex, ChannelNameArr[ChannelIdx], *AssetPath,
                RBTexAfter ? *RBTexAfter->GetPathName() : TEXT("null"), RBUseAfter);

            TexturesAppliedOut++;
        }
        else
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[MATERIAL][PERSISTENT_MIC_CHANNEL] guid=%s slot=%d channel=[%s] action=texture_miss image=%s key=%s path=%s"),
                *GuidStr, TexRef.SlotIndex, ChannelNameArr[ChannelIdx], *TexRef.ImageName, *ImageNameKey, *TexRef.Path);

            // Clear stale texture reference + disable toggle.
            // Remove override entry from MIC's internal array, not just set null.
            const FString ClearParamName = ChannelName + TEXT("Texture");
            {
                TArray<FTextureParameterValue>& TexParams = MIC->TextureParameterValues;
                for (int32 ti = TexParams.Num() - 1; ti >= 0; --ti)
                {
                    if (TexParams[ti].ParameterInfo.Name == FName(*ClearParamName))
                    {
                        TexParams.RemoveAt(ti);
                        break;
                    }
                }
            }
            MIC->SetTextureParameterValueEditorOnly(FName(*ClearParamName), nullptr);

            FString ToggleName = TEXT("Use") + ChannelName + TEXT("Texture");
            {
                TArray<FScalarParameterValue>& ScalarParams = MIC->ScalarParameterValues;
                for (int32 si = ScalarParams.Num() - 1; si >= 0; --si)
                {
                    if (ScalarParams[si].ParameterInfo.Name == FName(*ToggleName))
                    {
                        ScalarParams.RemoveAt(si);
                        break;
                    }
                }
            }
            MIC->SetScalarParameterValueEditorOnly(FName(*ToggleName), 0.0f);

            // Read back final state
            UTexture* RBTexAfter = nullptr;
            float RBUseAfter = 0.0f;
            MIC->GetTextureParameterValue(FName(*ClearParamName), RBTexAfter);
            MIC->GetScalarParameterValue(FName(*ToggleName), RBUseAfter);

            UE_LOG(LogLiveSync, Log,
                TEXT("[MATERIAL][PERSISTENT_MIC_CHANNEL] guid=%s slot=%d channel=[%s] action=clear_unresolved image=%s key=%s resolvedTextureAfter=%s useAfter=%.1f"),
                *GuidStr, TexRef.SlotIndex, ChannelNameArr[ChannelIdx], *TexRef.ImageName, *ImageNameKey,
                RBTexAfter ? *RBTexAfter->GetPathName() : TEXT("null"), RBUseAfter);

            TextureMissesOut++;
        }
    }

    return true;
}


UMaterialInstanceConstant* UUELiveSyncSubsystem::
ResolvePersistentMaterialAsset(
    const FMaterialIdentityRef& MaterialIdentity) const
{
    if (!MaterialIdentity.IsValid())
    {
        return nullptr;
    }

    const FSoftObjectPath* Path = MaterialPathCache.Find(MaterialIdentity);
    if (!Path || Path->IsNull())
    {
        return nullptr;
    }

    UMaterialInstanceConstant* MIC = Cast<UMaterialInstanceConstant>(Path->TryLoad());
    return MIC;
}


// =========================================================
// PHASE 10K.2 — TEXTURE IMPORT FROM MTEX RECORDS
// =========================================================

void UUELiveSyncSubsystem::
ImportTexturesFromMtexRecs(
    const FGuid& Guid,
    const TArray<FMaterialTextureMapRef>& TexMaps)
{
#if WITH_EDITOR
    CHECK_GAME_THREAD();

    IAssetTools& AssetTools = FAssetToolsModule::GetModule().Get();
    const FString DestPath = TEXT("/Game/UELiveSync/Textures");
    const FString GuidStr = Guid.ToString(EGuidFormats::Digits);

    // Channel name lookup for Phase 7H.6 logs
    static const TArray<FString> ChannelNames = {
        TEXT("BaseColor"), TEXT("Roughness"), TEXT("Metallic"), TEXT("Alpha"), TEXT("Normal")
    };

    for (const FMaterialTextureMapRef& TexRef : TexMaps)
    {
        if (!TexRef.IsValid())
        {
            continue;
        }

        // Task 8B: packed flag describes original Blender storage.
        // Blender has already materialized packed images to sidecar PNGs.
        // Do not suppress resolution — rely on ImageName/path to resolve.
        if (TexRef.Flags & MTEX_FLAG_IMAGE_PACKED)
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[MTEX][TEX_INFO] guid=%s slot=%d channel=%u image=%s reason=packed_flag_not_blocking"),
                *GuidStr, TexRef.SlotIndex, TexRef.Channel, *TexRef.ImageName);
        }

        // Task 9B: accept Blender-relative paths (//Wood.png) — strip prefix.
        // Do not reject // paths; use ImageName as canonical key for sidecar map.
        FString EffectivePath = TexRef.Path;
        if (!EffectivePath.IsEmpty() && EffectivePath.StartsWith(TEXT("//")))
        {
            EffectivePath = EffectivePath.Mid(2);
            UE_LOG(LogLiveSync, Log,
                TEXT("[MTEX][PATH_NORMALIZE] guid=%s slot=%d channel=%u original=%s normalized=%s"),
                *GuidStr, TexRef.SlotIndex, TexRef.Channel,
                *TexRef.Path, *EffectivePath);
        }

        // Still reject if path is empty after normalization.
        if (EffectivePath.IsEmpty())
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[MTEX][TEX_SKIP] guid=%s slot=%d reason=empty_path_after_normalize "
                     "channel=%u image=%s"),
                *GuidStr, TexRef.SlotIndex, TexRef.Channel, *TexRef.ImageName);
            TextureImportSkipped++;
            continue;
        }

        // Task 9B.1: resolve texture from per-GUID sidecar map by canonical key.
        // Do NOT call FPaths::FileExists() on Blender-relative paths — they are
        // not valid local filesystem paths. Use the sidecar map as the sole authority.
        {
            FString SidecarKey = TexRef.ImageName;
            if (SidecarKey.IsEmpty())
            {
                SidecarKey = FPaths::GetBaseFilename(EffectivePath);
            }
            int32 DotPos = INDEX_NONE;
            if (SidecarKey.FindChar(TEXT('.'), DotPos))
            {
                SidecarKey = SidecarKey.Left(DotPos);
            }
            SidecarKey.ToLowerInline();

            const TMap<FString, TSoftObjectPtr<UTexture2D>>* PerGuidSidecar = ImportedSidecarTexturesByGuid.Find(Guid);
            if (PerGuidSidecar && PerGuidSidecar->Contains(SidecarKey))
            {
                TextureImportCache.Add(EffectivePath, (*PerGuidSidecar)[SidecarKey]);
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MTEX][SIDECAR_HIT] guid=%s slot=%d channel=%u key=%s path=%s"),
                    *GuidStr, TexRef.SlotIndex, TexRef.Channel, *SidecarKey, *EffectivePath);
                TextureCacheHit++;
                TextureResolveSkipped++;
                continue;
            }

            UE_LOG(LogLiveSync, Log,
                TEXT("[MTEX][SIDECAR_MISS] guid=%s slot=%d channel=%u key=%s path=%s"),
                *GuidStr, TexRef.SlotIndex, TexRef.Channel, *SidecarKey, *EffectivePath);
            TextureImportSkipped++;
            continue;
        }

        // Phase 10K.5: validate supported texture extension
        static const TArray<FString> SupportedExtensions = {
            TEXT(".png"), TEXT(".jpg"), TEXT(".jpeg"),
            TEXT(".tga"), TEXT(".bmp"),
        };
        const FString LowerPath = EffectivePath.ToLower();
        bool bSupportedExt = false;
        for (const FString& Ext : SupportedExtensions)
        {
            if (LowerPath.EndsWith(Ext))
            {
                bSupportedExt = true;
                break;
            }
        }
        if (!bSupportedExt)
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[MTEX][TEX_SKIP] guid=%s slot=%d reason=unsupported_extension "
                     "channel=%u path=%s"),
                *GuidStr, TexRef.SlotIndex, TexRef.Channel, *TexRef.Path);
            TextureImportSkipped++;
            continue;
        }

        // Check import cache (use normalized path).
        if (TextureImportCache.Contains(EffectivePath))
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[MTEX][TEX_CACHE_HIT] guid=%s slot=%d channel=%u path=%s"),
                *GuidStr, TexRef.SlotIndex, TexRef.Channel, *EffectivePath);
            TextureCacheHit++;
            TextureResolveSkipped++;
            continue;
        }

        // Import texture
        TextureImportRequested++;

        TArray<FString> Files = { EffectivePath };
        TArray<UObject*> ImportedAssets = AssetTools.ImportAssets(Files, DestPath);

        UTexture2D* Texture = nullptr;
        if (ImportedAssets.Num() > 0)
        {
            Texture = Cast<UTexture2D>(ImportedAssets[0]);
        }

        if (Texture)
        {
            // Phase 7H.6 Task D: import-from-MATX diagnostic logs
            const FString ImportSrcLog = TEXT("[MATERIAL][TEXTURE_IMPORT_FROM_MATX] guid=") + GuidStr +
                TEXT(" slot=") + FString::FromInt(TexRef.SlotIndex) +
                TEXT(" channel=") + ChannelNames[static_cast<int32>(TexRef.Channel) - 1] +
                TEXT(" src=") + EffectivePath;
            UE_LOG(LogLiveSync, Log, TEXT("%s"), *ImportSrcLog);

            // Set sRGB per channel policy:
            // BaseColor(1) → sRGB true; Roughness/Metallic/Alpha/Normal → sRGB false
            const bool bSRGB = (TexRef.Channel == static_cast<uint8>(EMTEXChannel::BaseColor));
            Texture->SRGB = bSRGB;

            // Task 9B.3: enforce TC_Normalmap for Normal channel textures.
            bool bSettingsChanged = false;
            if (TexRef.Channel == static_cast<uint8>(EMTEXChannel::Normal))
            {
                if (Texture->CompressionSettings != TextureCompressionSettings::TC_Normalmap)
                {
                    Texture->CompressionSettings = TextureCompressionSettings::TC_Normalmap;
                    bSettingsChanged = true;
                }
            }

            Texture->PostEditChange();

            UE_LOG(LogLiveSync, Log,
                TEXT("[NORMAL_TEXTURE_SETTINGS] guid=%s slot=%d channel=%u "
                     "asset=%s compression=TC_Normalmap srgb=%d changed=%d"),
                *GuidStr, TexRef.SlotIndex, TexRef.Channel,
                *Texture->GetPathName(), bSRGB ? 0 : 1, bSettingsChanged ? 1 : 0);

            TextureImportCache.Add(EffectivePath, Texture);

            const FString AssetPath = Texture->GetPathName();
            const FString ImportOkLog = TEXT("[MATERIAL][TEXTURE_IMPORT_FROM_MATX_OK] guid=") + GuidStr +
                TEXT(" slot=") + FString::FromInt(TexRef.SlotIndex) +
                TEXT(" channel=") + ChannelNames[static_cast<int32>(TexRef.Channel) - 1] +
                TEXT(" texture=") + AssetPath;
            UE_LOG(LogLiveSync, Log, TEXT("%s"), *ImportOkLog);

            UE_LOG(LogLiveSync, Log,
                TEXT("[MTEX][TEX_IMPORT] guid=%s slot=%d channel=%u "
                     "path=%s sRGB=%d"),
                *GuidStr, TexRef.SlotIndex, TexRef.Channel,
                *EffectivePath, bSRGB ? 1 : 0);

            // Phase 10K.5: cache size diagnostic
            const int32 CacheSize = TextureImportCache.Num();
            if (CacheSize > 50)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[MTEX][TEX_CACHE_WARN] size=%d exceeds_threshold=50 "
                         "consider_resync_with_cache_reset"),
                    CacheSize);
            }
        }
        else
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[MTEX][TEX_FAIL] guid=%s slot=%d channel=%u "
                     "reason=import_failed path=%s"),
                *GuidStr, TexRef.SlotIndex, TexRef.Channel, *EffectivePath);
            TextureImportFailed++;
            continue;
        }

        // Phase 10K.2: texture resolve (apply to material) is skipped
        TextureResolveSkipped++;
    }
#else
    // No-op in non-editor builds
    (void)Guid;
    (void)TexMaps;
#endif
}


// =========================================================
// PHASE 10K.3 — APPLY IMPORTED TEXTURES TO GENERATED MID
// =========================================================

bool UUELiveSyncSubsystem::
ApplyImportedTexturesToGeneratedMID(
    const FGuid& Guid,
    int32 SlotIndex,
    UMaterialInstanceDynamic* MID)
{
    CHECK_GAME_THREAD();

    if (!MID)
    {
        return false;
    }

    const FString GuidStr = Guid.ToString(EGuidFormats::Digits);
    TextureMaterialApplyRequests++;

    const TArray<FMaterialTextureMapRef>* TexMaps = MaterialTextureMapCache.Find(Guid);
    if (!TexMaps || TexMaps->Num() == 0)
    {
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[MAT][TEX_SKIP] guid=%s slot=%d reason=no_mtex_records"),
            *GuidStr, SlotIndex);
        TextureMaterialApplySkipped++;
        return false;
    }

    int32 AppliedCount = 0;

    for (const FMaterialTextureMapRef& TexRef : *TexMaps)
    {
        if (TexRef.SlotIndex != SlotIndex)
            continue;

        TSoftObjectPtr<UTexture2D>* CachedTexture = TextureImportCache.Find(TexRef.Path);
        if (!CachedTexture || !CachedTexture->IsValid())
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[MAT][TEX_SKIP] guid=%s slot=%d channel=%u reason=no_imported_texture"),
                *GuidStr, SlotIndex, TexRef.Channel);
            TextureMaterialApplySkipped++;
            continue;
        }

        UTexture2D* Texture = CachedTexture->Get();
        if (!Texture)
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[MAT][TEX_SKIP] guid=%s slot=%d channel=%u reason=texture_not_loaded"),
                *GuidStr, SlotIndex, TexRef.Channel);
            TextureMaterialApplySkipped++;
            continue;
        }

        FString ParamName;
        FString ChannelName;
        bool bValidChannel = true;

        switch (static_cast<EMTEXChannel>(TexRef.Channel))
        {
        case EMTEXChannel::BaseColor:
            ParamName = TEXT("BaseColorTexture");
            ChannelName = TEXT("BaseColor");
            break;
        case EMTEXChannel::Roughness:
            ParamName = TEXT("RoughnessTexture");
            ChannelName = TEXT("Roughness");
            break;
        case EMTEXChannel::Metallic:
            ParamName = TEXT("MetallicTexture");
            ChannelName = TEXT("Metallic");
            break;
        case EMTEXChannel::Alpha:
            ParamName = TEXT("AlphaTexture");
            ChannelName = TEXT("Alpha");
            break;
        case EMTEXChannel::Normal:
            ParamName = TEXT("NormalTexture");
            ChannelName = TEXT("Normal");
            break;
        default:
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[MAT][TEX_SKIP] guid=%s slot=%d channel=%u reason=unsupported_channel"),
                *GuidStr, SlotIndex, TexRef.Channel);
            bValidChannel = false;
            break;
        }

        if (!bValidChannel)
        {
            TextureMaterialApplySkipped++;
            continue;
        }

        MID->SetTextureParameterValue(*ParamName, Texture);
        AppliedCount++;

        UE_LOG(LogLiveSync, Log,
            TEXT("[MATERIAL][TEXTURE_PARAM_SET] guid=%s slot=%d param=%s texture=%s"),
            *GuidStr, SlotIndex, *ParamName, *TexRef.ImageName);

        // Phase 7H.6 Task E: set UseXTexture=1 for each bound texture
        const FString ToggleParamName = FString(TEXT("Use")) + FString(ChannelName) + TEXT("Texture");
        MID->SetScalarParameterValue(FName(*ToggleParamName), 1.0f);
        UE_LOG(LogLiveSync, Log,
            TEXT("[MATERIAL][TEXTURE_TOGGLE_SET] guid=%s slot=%d param=%s value=1"),
            *GuidStr, SlotIndex, *ToggleParamName);

        UE_LOG(LogLiveSync, Log,
            TEXT("[MAT][TEX_APPLY] guid=%s slot=%d channel=%s texture=%s param=%s"),
            *GuidStr, SlotIndex, *ChannelName,
            *TexRef.ImageName, *ParamName);

        // Phase 10K.5: log deferred channel status for Alpha/Normal
        if (static_cast<EMTEXChannel>(TexRef.Channel) == EMTEXChannel::Alpha)
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[MAT][TEX_WARN] guid=%s channel=Alpha "
                     "reason=visual_deferred_blending_not_enabled_in_master"),
                *GuidStr);
        }
        if (static_cast<EMTEXChannel>(TexRef.Channel) == EMTEXChannel::Normal)
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[MAT][TEX_APPLY_NORMAL] guid=%s channel=Normal "
                     "reason=master_normal_transform_wired_use_mic_readback"),
                *GuidStr);
        }
    }

    if (AppliedCount > 0)
    {
        // Log parent material for texture param visibility assessment
        FString ParentPath = TEXT("unknown");
        if (MID && MID->Parent)
        {
            ParentPath = MID->Parent->GetPathName();
            if (ParentPath.Contains(TEXT("BasicShapeMaterial")))
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MAT][TEX_WARN] guid=%s "
                         "reason=parent_material_may_not_use_texture_params "
                         "parent=%s"),
                    *GuidStr, *ParentPath);
            }
        }
        UE_LOG(LogLiveSync, Log,
            TEXT("[MAT][TEX_PARENT] guid=%s parent=%s"),
            *GuidStr, *ParentPath);

        // Phase 7H.6 Task C: hybrid apply summary log
        // Build texture list and value list for HYBRID_APPLY log
        TArray<FString> TexList;
        TArray<FString> ValList;
        // Check which texture params are bound
        UTexture* CheckTex = nullptr;
        float CheckUse = 0;
        MID->GetTextureParameterValue(FName(TEXT("BaseColorTexture")), CheckTex);
        MID->GetScalarParameterValue(FName(TEXT("UseBaseColorTexture")), CheckUse);
        if (CheckTex || CheckUse > 0)
        {
            TexList.Add(TEXT("BaseColor"));
        }
        MID->GetTextureParameterValue(FName(TEXT("RoughnessTexture")), CheckTex);
        MID->GetScalarParameterValue(FName(TEXT("UseRoughnessTexture")), CheckUse);
        if (CheckTex || CheckUse > 0)
        {
            TexList.Add(TEXT("Roughness"));
        }
        MID->GetTextureParameterValue(FName(TEXT("MetallicTexture")), CheckTex);
        MID->GetScalarParameterValue(FName(TEXT("UseMetallicTexture")), CheckUse);
        if (CheckTex || CheckUse > 0)
        {
            TexList.Add(TEXT("Metallic"));
        }
        MID->GetTextureParameterValue(FName(TEXT("NormalTexture")), CheckTex);
        MID->GetScalarParameterValue(FName(TEXT("UseNormalTexture")), CheckUse);
        if (CheckTex || CheckUse > 0)
        {
            TexList.Add(TEXT("Normal"));
        }
        MID->GetTextureParameterValue(FName(TEXT("AlphaTexture")), CheckTex);
        MID->GetScalarParameterValue(FName(TEXT("UseAlphaTexture")), CheckUse);
        if (CheckTex || CheckUse > 0)
        {
            TexList.Add(TEXT("Alpha"));
        }
        // Check scalar values
        FLinearColor BCVal;
        MID->GetVectorParameterValue(FName(TEXT("BaseColor")), BCVal);
        float RoughVal = 0, MetalVal = 0, AlphaVal = 0;
        MID->GetScalarParameterValue(FName(TEXT("Roughness")), RoughVal);
        MID->GetScalarParameterValue(FName(TEXT("Metallic")), MetalVal);
        MID->GetScalarParameterValue(FName(TEXT("Alpha")), AlphaVal);
        if (FMath::Abs(RoughVal) > 0.001)
        {
            ValList.Add(TEXT("Roughness"));
        }
        if (FMath::Abs(MetalVal) > 0.001)
        {
            ValList.Add(TEXT("Metallic"));
        }
        if (FMath::Abs(AlphaVal) > 0.001)
        {
            ValList.Add(TEXT("Alpha"));
        }
        FString TexStr = FString::Join(TexList, TEXT(","));
        FString ValStr = FString::Join(ValList, TEXT(","));
        UE_LOG(LogLiveSync, Log,
            TEXT("[MATERIAL][HYBRID_APPLY] guid=%s slot=%d textures=[%s] values=[%s]"),
            *GuidStr, SlotIndex, *TexStr, *ValStr);

        TextureMaterialApplySucceeded++;
        return true;
    }

    return false;
}


// =========================================================
// PHASE 10K.4 — GET OR CREATE LIVESYNC MASTER MATERIAL
// =========================================================

UMaterialInterface* UUELiveSyncSubsystem::
GetOrCreateLiveSyncMasterMaterial()
{
    static const FSoftObjectPath MasterMatPath(
        TEXT("/Game/UELiveSync/Materials/M_UELiveSync_Master.M_UELiveSync_Master"));

    UMaterialInterface* Existing = Cast<UMaterialInterface>(MasterMatPath.TryLoad());
    if (Existing)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[MAT][MASTER] action=load_existing "
                 "path=/Game/UELiveSync/Materials/M_UELiveSync_Master"));
        return Existing;
    }

    MasterMaterialCreationAttempted++;

#if WITH_EDITOR
    UMaterial* NewMat = CreateLiveSyncMasterMaterialAsset();
    if (NewMat)
    {
        MasterMaterialCreated++;
        UE_LOG(LogLiveSync, Log,
            TEXT("[MAT][MASTER] action=create "
                 "path=/Game/UELiveSync/Materials/M_UELiveSync_Master"));
        return NewMat;
    }
#endif

    MasterMaterialFallback++;
    UE_LOG(LogLiveSync, Warning,
        TEXT("[MAT][MASTER_WARN] reason=master_material_missing "
             "fallback=BasicShapeMaterial"));

    static const FSoftObjectPath BaseMatPath(
        TEXT("/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial"));
    return Cast<UMaterialInterface>(BaseMatPath.TryLoad());
}


#if WITH_EDITOR
// =========================================================
// PHASE 10K.4 — CREATE LIVESYNC MASTER MATERIAL ASSET
// =========================================================

static UMaterialExpression* CreateMasterExpression(
    UMaterial* Material,
    TSubclassOf<UMaterialExpression> Class,
    int32 PosX,
    int32 PosY,
    const TCHAR* NodeComment)
{
    UMaterialExpression* Expr = NewObject<UMaterialExpression>(Material, Class, NAME_None, RF_Transactional);
    if (Expr)
    {
        Expr->Material = Material;
        Expr->MaterialExpressionEditorX = PosX;
        Expr->MaterialExpressionEditorY = PosY;
        if (NodeComment)
        {
            Expr->Desc = NodeComment;
        }
        Material->GetExpressionCollection().AddExpression(Expr);
    }
    return Expr;
}

static UMaterialExpressionTextureSampleParameter2D* CreateMasterTexParam(
    UMaterial* Material,
    const FName& ParamName,
    EMaterialSamplerType SamplerType,
    int32 PosX,
    int32& InOutPosY)
{
    UMaterialExpressionTextureSampleParameter2D* Expr =
        Cast<UMaterialExpressionTextureSampleParameter2D>(
            CreateMasterExpression(Material,
                UMaterialExpressionTextureSampleParameter2D::StaticClass(),
                PosX, InOutPosY, nullptr));
    if (Expr)
    {
        Expr->ParameterName = ParamName;
        Expr->SamplerType = SamplerType;
    }
    InOutPosY += 220;
    return Expr;
}

static UMaterialExpressionVectorParameter* CreateMasterVecParam(
    UMaterial* Material,
    const FName& ParamName,
    const FLinearColor& DefaultValue,
    int32 PosX,
    int32& InOutPosY)
{
    UMaterialExpressionVectorParameter* Expr =
        Cast<UMaterialExpressionVectorParameter>(
            CreateMasterExpression(Material,
                UMaterialExpressionVectorParameter::StaticClass(),
                PosX, InOutPosY, nullptr));
    if (Expr)
    {
        Expr->ParameterName = ParamName;
        Expr->DefaultValue = DefaultValue;
    }
    InOutPosY += 220;
    return Expr;
}

static UMaterialExpressionScalarParameter* CreateMasterScalarParam(
    UMaterial* Material,
    const FName& ParamName,
    float DefaultValue,
    int32 PosX,
    int32& InOutPosY)
{
    UMaterialExpressionScalarParameter* Expr =
        Cast<UMaterialExpressionScalarParameter>(
            CreateMasterExpression(Material,
                UMaterialExpressionScalarParameter::StaticClass(),
                PosX, InOutPosY, nullptr));
    if (Expr)
    {
        Expr->ParameterName = ParamName;
        Expr->DefaultValue = DefaultValue;
    }
    InOutPosY += 220;
    return Expr;
}

static UMaterialExpressionLinearInterpolate* CreateMasterLerp(
    UMaterial* Material,
    int32 PosX,
    int32& InOutPosY)
{
    UMaterialExpressionLinearInterpolate* Expr =
        Cast<UMaterialExpressionLinearInterpolate>(
            CreateMasterExpression(Material,
                UMaterialExpressionLinearInterpolate::StaticClass(),
                PosX, InOutPosY, nullptr));
    InOutPosY += 220;
    return Expr;
}

UMaterial* UUELiveSyncSubsystem::CreateLiveSyncMasterMaterialAsset()
{
    const FString PackagePath = TEXT("/Game/UELiveSync/Materials/M_UELiveSync_Master");
    UPackage* Package = CreatePackage(*PackagePath);
    if (!Package)
    {
        UE_LOG(LogLiveSync, Warning, TEXT("[MAT][MASTER_WARN] reason=create_package_failed"));
        return nullptr;
    }

    UMaterial* Material = NewObject<UMaterial>(
        Package,
        FName(TEXT("M_UELiveSync_Master")),
        RF_Public | RF_Standalone | RF_MarkAsRootSet);
    if (!Material)
    {
        UE_LOG(LogLiveSync, Warning, TEXT("[MAT][MASTER_WARN] reason=create_material_failed"));
        return nullptr;
    }

    const int32 ParamX = -800;
    const int32 LerpX = -300;
    int32 Y = 0;

    // =====================================================
    // CHANNEL: BaseColor
    // =====================================================
    UMaterialExpressionVectorParameter* BaseColorParam = CreateMasterVecParam(
        Material, FName(TEXT("BaseColor")),
        FLinearColor(0.8f, 0.8f, 0.8f, 1.0f), ParamX, Y);

    UMaterialExpressionTextureSampleParameter2D* BaseColorTex = CreateMasterTexParam(
        Material, FName(TEXT("BaseColorTexture")), SAMPLERTYPE_Color, ParamX, Y);

    UMaterialExpressionScalarParameter* UseBaseColor = CreateMasterScalarParam(
        Material, FName(TEXT("UseBaseColorTexture")), 0.0f, ParamX, Y);

    UMaterialExpressionLinearInterpolate* BaseLerp = CreateMasterLerp(Material, LerpX, Y);
    if (BaseLerp && BaseColorParam && BaseColorTex && UseBaseColor)
    {
        BaseLerp->A.Expression = BaseColorParam;
        BaseLerp->A.OutputIndex = 0;
        BaseLerp->B.Expression = BaseColorTex;
        BaseLerp->B.OutputIndex = 0;
        BaseLerp->Alpha.Expression = UseBaseColor;
        BaseLerp->Alpha.OutputIndex = 0;

        {
            FExpressionInput* Input = Material->GetExpressionInputForProperty(EMaterialProperty::MP_BaseColor);
            if (Input)
            {
                Input->Expression = BaseLerp;
                Input->OutputIndex = 0;
            }
        }
    }

    // =====================================================
    // CHANNEL: Roughness
    // =====================================================
    UMaterialExpressionScalarParameter* RoughnessParam = CreateMasterScalarParam(
        Material, FName(TEXT("Roughness")), 0.5f, ParamX, Y);

    UMaterialExpressionTextureSampleParameter2D* RoughnessTex = CreateMasterTexParam(
        Material, FName(TEXT("RoughnessTexture")), SAMPLERTYPE_Masks, ParamX, Y);

    UMaterialExpressionScalarParameter* UseRoughness = CreateMasterScalarParam(
        Material, FName(TEXT("UseRoughnessTexture")), 0.0f, ParamX, Y);

    UMaterialExpressionLinearInterpolate* RoughLerp = CreateMasterLerp(Material, LerpX, Y);
    if (RoughLerp && RoughnessParam && RoughnessTex && UseRoughness)
    {
        RoughLerp->A.Expression = RoughnessParam;
        RoughLerp->A.OutputIndex = 0;
        RoughLerp->B.Expression = RoughnessTex;
        RoughLerp->B.OutputIndex = 0;
        RoughLerp->Alpha.Expression = UseRoughness;
        RoughLerp->Alpha.OutputIndex = 0;

        {
            FExpressionInput* Input = Material->GetExpressionInputForProperty(EMaterialProperty::MP_Roughness);
            if (Input)
            {
                Input->Expression = RoughLerp;
                Input->OutputIndex = 0;
            }
        }
    }

    // =====================================================
    // CHANNEL: Metallic
    // =====================================================
    UMaterialExpressionScalarParameter* MetallicParam = CreateMasterScalarParam(
        Material, FName(TEXT("Metallic")), 0.0f, ParamX, Y);

    UMaterialExpressionTextureSampleParameter2D* MetallicTex = CreateMasterTexParam(
        Material, FName(TEXT("MetallicTexture")), SAMPLERTYPE_Masks, ParamX, Y);

    UMaterialExpressionScalarParameter* UseMetallic = CreateMasterScalarParam(
        Material, FName(TEXT("UseMetallicTexture")), 0.0f, ParamX, Y);

    UMaterialExpressionLinearInterpolate* MetalLerp = CreateMasterLerp(Material, LerpX, Y);
    if (MetalLerp && MetallicParam && MetallicTex && UseMetallic)
    {
        MetalLerp->A.Expression = MetallicParam;
        MetalLerp->A.OutputIndex = 0;
        MetalLerp->B.Expression = MetallicTex;
        MetalLerp->B.OutputIndex = 0;
        MetalLerp->Alpha.Expression = UseMetallic;
        MetalLerp->Alpha.OutputIndex = 0;

        {
            FExpressionInput* Input = Material->GetExpressionInputForProperty(EMaterialProperty::MP_Metallic);
            if (Input)
            {
                Input->Expression = MetalLerp;
                Input->OutputIndex = 0;
            }
        }
    }

    // =====================================================
    // CHANNEL: Alpha (opaque default, parameter kept)
    // =====================================================
    CreateMasterScalarParam(Material, FName(TEXT("Alpha")), 1.0f, ParamX, Y);
    CreateMasterTexParam(Material, FName(TEXT("AlphaTexture")), SAMPLERTYPE_Masks, ParamX, Y);
    CreateMasterScalarParam(Material, FName(TEXT("UseAlphaTexture")), 0.0f, ParamX, Y);

    UE_LOG(LogLiveSync, Log,
        TEXT("[MAT][MASTER] deferred=Alpha reason=opaque_blend_mode "
             "param_alpha_kept_for_future_use"));

    // =====================================================
    // CHANNEL: Normal (tangent-space contract, bTangentSpaceNormal=true)
    // =====================================================
    UMaterialExpressionTextureSampleParameter2D* NormalTex = CreateMasterTexParam(
        Material, FName(TEXT("NormalTexture")), SAMPLERTYPE_Normal, ParamX, Y);

    UMaterialExpressionScalarParameter* UseNormal = CreateMasterScalarParam(
        Material, FName(TEXT("UseNormalTexture")), 0.0f, ParamX, Y);

    // Default encoded tangent-space normal (0.5,0.5,1) → decoded (0,0,1).
    // UE auto-decodes MP_Normal input from [0,1] to [-1,1] when bTangentSpaceNormal=true.
    UMaterialExpressionConstant3Vector* DefaultNormal = Cast<UMaterialExpressionConstant3Vector>(
        CreateMasterExpression(Material, UMaterialExpressionConstant3Vector::StaticClass(),
            ParamX, Y, TEXT("DefaultNormal")));
    if (DefaultNormal)
    {
        DefaultNormal->Constant = FLinearColor(0.0f, 0.0f, 1.0f, 0.0f);
    }

    // Lerp blends encoded default (0.5,0.5,1) with NormalTexture sample.
    // Output is in encoded tangent space → MP_Normal with bTangentSpaceNormal=true.
    // No explicit Transform: UE auto-decodes [0,1]→[-1,1] and transforms tangent→world.
    UMaterialExpressionLinearInterpolate* NormalLerp = CreateMasterLerp(Material, LerpX, Y);
    if (NormalLerp && DefaultNormal && NormalTex && UseNormal)
    {
        NormalLerp->A.Expression = DefaultNormal;
        NormalLerp->A.OutputIndex = 0;
        NormalLerp->B.Expression = NormalTex;
        NormalLerp->B.OutputIndex = 0;
        NormalLerp->Alpha.Expression = UseNormal;
        NormalLerp->Alpha.OutputIndex = 0;

        // Connect Lerp directly to MP_Normal — no explicit Transform.
        // bTangentSpaceNormal=true (default) expects encoded tangent-space input.
        FExpressionInput* Input = Material->GetExpressionInputForProperty(EMaterialProperty::MP_Normal);
        if (Input)
        {
            Input->Expression = NormalLerp;
            Input->OutputIndex = 0;
        }
    }

    // =====================================================
    // Material properties
    // =====================================================
    Material->BlendMode = EBlendMode::BLEND_Opaque;
    Material->SetShadingModel(EMaterialShadingModel::MSM_DefaultLit);
    Material->TwoSided = false;

    // Compile material
    Material->PostEditChange();
    Material->MarkPackageDirty();

    // Save the asset so it persists across restarts
    FString FilePath = FPackageName::LongPackageNameToFilename(
        PackagePath, FPackageName::GetAssetPackageExtension());
    if (!FilePath.IsEmpty())
    {
        FSavePackageArgs SaveArgs;
        SaveArgs.TopLevelFlags = RF_Standalone;
        SaveArgs.SaveFlags = SAVE_NoError;
        UPackage::SavePackage(Package, nullptr, *FilePath, SaveArgs);
    }

    UE_LOG(LogLiveSync, Log,
        TEXT("[MAT][MASTER] create_complete "
             "path=/Game/UELiveSync/Materials/M_UELiveSync_Master "
             "expressions=%d tex_params=BaseColorTexture,RoughnessTexture,MetallicTexture,NormalTexture"),
        Material->GetExpressions().Num());

    return Material;
}
#endif


// =========================================================
// HANDLE MATERIAL DEF (Phase 7B Stage 1C)
// =========================================================

void UUELiveSyncSubsystem::
HandleMaterialDef(
    const FGuid& Guid,
    const TArray<FMaterialSlotRef>& Slots,
    uint32 ObjectCount)
{
    CHECK_GAME_THREAD();

    if (!Guid.IsValid())
    {
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[MATERIAL] Skipping invalid GUID"));
        return;
    }

    MaterialMetadata.Add(
        Guid,
        Slots);

    MaterialDefsReceived++;
}


// =========================================================
// RESOLVE PENDING MATERIALS (Phase 7B Stage 1D)
// =========================================================

void UUELiveSyncSubsystem::
ResolvePendingMaterials()
{
    CHECK_GAME_THREAD();

    if (MaterialMetadata.Num() == 0)
    {
        return;
    }

    for (auto It = MaterialMetadata.CreateIterator(); It; ++It)
    {
        const FGuid& Guid = It.Key();
        const TArray<FMaterialSlotRef>& Slots = It.Value();

        AActor* Actor = FindActorFast(Guid);
        // MATSTALL: log ActorCache status for material resolve.
        if (!Actor)
        {
            if (GEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MATSTALL][UE] mat_resolve actor_missing guid=%s \u2014 removing stale metadata"),
                    *Guid.ToString(EGuidFormats::Digits));
            }
            // Phase 10J.5A: remove stale entry so it does not spam every tick.
            It.RemoveCurrent();
            continue;
        }

        UStaticMeshComponent* MeshComp =
            Actor->FindComponentByClass<
                UStaticMeshComponent>();

        if (!MeshComp)
        {
            if (bEnableVerboseSyncLogs)
            {
                UE_LOG(
                    LogLiveSync,
                    Verbose,
                    TEXT("[MaterialResolve] No mesh component for "
                         "GUID=%s \u2014 skipping"),
                    *Guid.ToString(
                        EGuidFormats::Digits));
            }
            continue;
        }

        bool bAnyValidUnresolved = false;

        for (const FMaterialSlotRef& Slot : Slots)
        {
            if (!Slot.IsValid())
            {
                continue;
            }

            FSoftObjectPath* Path =
                MaterialPathCache.Find(Slot.Identity);

            if (!Path || Path->IsNull())
            {
                bAnyValidUnresolved = true;
                continue;
            }

            UMaterialInterface* Mat =
                Cast<UMaterialInterface>(
                    Path->TryLoad());

            if (!Mat)
            {
                bAnyValidUnresolved = true;

                if (bEnableVerboseSyncLogs)
                {
                    UE_LOG(
                        LogLiveSync,
                        Verbose,
                        TEXT("[MaterialResolve] Failed to load "
                             "material for slot %d on GUID=%s"),
                        Slot.SlotIndex,
                        *Guid.ToString(
                            EGuidFormats::Digits));
                }
                continue;
            }

            MeshComp->SetMaterial(
                Slot.SlotIndex,
                Mat);

            MaterialAssignmentsSucceeded++;

            // MATSTALL diagnostics: log successful SetMaterial.
            if (GEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MATSTALL][UE] mat_resolve_set slot=%d guid=%s actor=%s comp=%s"),
                    Slot.SlotIndex,
                    *Guid.ToString(EGuidFormats::Digits),
                    *Actor->GetName(),
                    *MeshComp->GetName());
            }

            if (bEnableVerboseSyncLogs)
            {
                UE_LOG(
                    LogLiveSync,
                    Log,
                    TEXT("[MaterialResolve] Set slot %d on "
                         "GUID=%s \u2014 %s"),
                    Slot.SlotIndex,
                    *Guid.ToString(
                        EGuidFormats::Digits),
                    *Path->ToString());
            }
        }

        if (!bAnyValidUnresolved)
        {
            // MATSTALL: log when material metadata is fully resolved.
            if (GEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MATSTALL][UE] mat_resolve_complete guid=%s"),
                    *Guid.ToString(EGuidFormats::Digits));
            }
            It.RemoveCurrent();
        }
    }
}


// =========================================================
// PARSE V1 MESH PAYLOAD (Phase 7C Stage 2C)
// =========================================================
// Parser-only: validates FULL_ATTR v1 payload format without
// storing data or building mesh sections.
//
// Chunk 0 payload:
//   SchemaVersion(4) + VertexStride(4) + VertexCount(4) +
//   Vertex[VertexCount] + IndexCount(4) + Index[IndexCount]
//
// Chunk >0 payload (no SchemaVersion):
//   VertexStride(4) + VertexCount(4) + Vertex[VertexCount] +
//   IndexCount(4) + Index[IndexCount]
//
// Vertex stride 32: pos(float3) + normal(float3) + uv0(float2)
// Vertex stride 48: pos(float3) + normal(float3) + uv0(float2) + color0(float4)
// =========================================================
// =========================================================
// Safe float32 wire decode helpers for FULL_ATTR v1 mesh parse
// Wire vertex components are float32. UE5 FVector/FVector2D are
// double-based — read float32 individually, construct higher-
// precision types.
// =========================================================

static bool ReadU32_Safe(const uint8* Data, int32 DataLen, int32& Offset, uint32& OutVal)
{
    if (Offset + 4 > DataLen) return false;
    FMemory::Memcpy(&OutVal, Data + Offset, sizeof(uint32));
    Offset += 4;
    return true;
}

static bool ReadF32_Safe(const uint8* Data, int32 DataLen, int32& Offset, float& OutVal)
{
    if (Offset + 4 > DataLen) return false;
    FMemory::Memcpy(&OutVal, Data + Offset, sizeof(float));
    Offset += 4;
    return true;
}

static bool ReadVec3F32(const uint8* Data, int32 DataLen, int32& Offset, FVector& OutVec)
{
    float X, Y, Z;
    if (!ReadF32_Safe(Data, DataLen, Offset, X) ||
        !ReadF32_Safe(Data, DataLen, Offset, Y) ||
        !ReadF32_Safe(Data, DataLen, Offset, Z))
        return false;
    OutVec = FVector(X, Y, Z);
    return true;
}

static bool ReadVec2F32(const uint8* Data, int32 DataLen, int32& Offset, FVector2D& OutVec)
{
    float U, V;
    if (!ReadF32_Safe(Data, DataLen, Offset, U) ||
        !ReadF32_Safe(Data, DataLen, Offset, V))
        return false;
    OutVec = FVector2D(U, V);
    return true;
}

static bool ReadColor4F32(const uint8* Data, int32 DataLen, int32& Offset, FLinearColor& OutColor)
{
    float R, G, B, A;
    if (!ReadF32_Safe(Data, DataLen, Offset, R) ||
        !ReadF32_Safe(Data, DataLen, Offset, G) ||
        !ReadF32_Safe(Data, DataLen, Offset, B) ||
        !ReadF32_Safe(Data, DataLen, Offset, A))
        return false;
    OutColor = FLinearColor(R, G, B, A);
    return true;
}

static bool IsFiniteVec3(const FVector& V)
{
    return FMath::IsFinite(V.X) && FMath::IsFinite(V.Y) && FMath::IsFinite(V.Z);
}

static bool IsFiniteVec2(const FVector2D& V)
{
    return FMath::IsFinite(V.X) && FMath::IsFinite(V.Y);
}

// =========================================================

bool UUELiveSyncSubsystem::
ParseV1MeshPayload(
    const FGuid& Guid,
    uint32 ChunkIndex,
    uint32 ChunkCount,
    const TArrayView<const uint8>& Payload,
    FV1MeshParsedChunk& OutParsedChunk)
{
    CHECK_GAME_THREAD();

    const uint8* Data = Payload.GetData();
    int32 DataLen = Payload.Num();
    int32 Offset = 0;

    // Chunk 0: SchemaVersion (uint32)
    if (ChunkIndex == 0)
    {
        if (Offset + 4 > DataLen)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[MESH][V1] Chunk0 truncated: cannot read SchemaVersion for GUID=%s"),
                *Guid.ToString(EGuidFormats::Digits));
            Stats.MeshSchemaUnsupportedPackets.fetch_add(1, std::memory_order_relaxed);
            return false;
        }

        uint32 SchemaVersion = 0;
        FMemory::Memcpy(&SchemaVersion, Data + Offset, sizeof(uint32));
        Offset += 4;

        if (SchemaVersion != 1)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[MESH][V1] Unsupported SchemaVersion %u for GUID=%s"),
                SchemaVersion, *Guid.ToString(EGuidFormats::Digits));
            Stats.MeshSchemaUnsupportedPackets.fetch_add(1, std::memory_order_relaxed);
            return false;
        }
    }

    // Validate ChunkIndex < ChunkCount (defense-in-depth; already checked by caller)
    if (ChunkIndex >= ChunkCount)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[MESH][V1] ChunkIndex=%u >= ChunkCount=%u for GUID=%s"),
            ChunkIndex, ChunkCount,
            *Guid.ToString(EGuidFormats::Digits));
        return false;
    }

    // VertexStride (uint32) — always present in every chunk
    if (Offset + 4 > DataLen)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[MESH][V1] Truncated payload: cannot read VertexStride for GUID=%s chunk=%u"),
            *Guid.ToString(EGuidFormats::Digits), ChunkIndex);
        return false;
    }

    uint32 VertexStride = 0;
    FMemory::Memcpy(&VertexStride, Data + Offset, sizeof(uint32));
    Offset += 4;

    if (VertexStride != 32 && VertexStride != 48)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[MESH][V1] Unsupported VertexStride %u for GUID=%s chunk=%u"),
            VertexStride,
            *Guid.ToString(EGuidFormats::Digits), ChunkIndex);
        return false;
    }

    // VertexCount (uint32) + Vertex array
    if (Offset + 4 > DataLen)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[MESH][V1] Truncated payload: cannot read VertexCount for GUID=%s chunk=%u"),
            *Guid.ToString(EGuidFormats::Digits), ChunkIndex);
        return false;
    }

    uint32 VertexCount = 0;
    FMemory::Memcpy(&VertexCount, Data + Offset, sizeof(uint32));
    Offset += 4;

    uint32 VertexBytes = VertexCount * VertexStride;
    if (Offset + static_cast<int32>(VertexBytes) > DataLen)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[MESH][V1] Truncated vertex payload for GUID=%s chunk=%u: "
                 "need %u bytes, have %d"),
            *Guid.ToString(EGuidFormats::Digits), ChunkIndex,
            VertexBytes, DataLen - Offset);
        return false;
    }

    // Parse vertices using float32 wire decode
    // Wire vertex components are float32; UE5 FVector/FVector2D are double-based.
    const bool bHasColor0 = (VertexStride == 48);
    OutParsedChunk.Vertices.Reserve(VertexCount);
    for (uint32 i = 0; i < VertexCount; i++)
    {
        FV1MeshParsedVertex V;
        if (!ReadVec3F32(Data, DataLen, Offset, V.Position))
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[MESH][V1] Truncated position at vertex %u/%u for GUID=%s chunk=%u"),
                i, VertexCount,
                *Guid.ToString(EGuidFormats::Digits), ChunkIndex);
            return false;
        }
        if (!ReadVec3F32(Data, DataLen, Offset, V.Normal))
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[MESH][V1] Truncated normal at vertex %u/%u for GUID=%s chunk=%u"),
                i, VertexCount,
                *Guid.ToString(EGuidFormats::Digits), ChunkIndex);
            return false;
        }
        if (!ReadVec2F32(Data, DataLen, Offset, V.UV0))
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[MESH][V1] Truncated uv0 at vertex %u/%u for GUID=%s chunk=%u"),
                i, VertexCount,
                *Guid.ToString(EGuidFormats::Digits), ChunkIndex);
            return false;
        }
        if (bHasColor0)
        {
            if (!ReadColor4F32(Data, DataLen, Offset, V.Color0))
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[MESH][V1] Truncated color0 at vertex %u/%u for GUID=%s chunk=%u"),
                    i, VertexCount,
                    *Guid.ToString(EGuidFormats::Digits), ChunkIndex);
                return false;
            }
        }
        else
        {
            V.Color0 = FLinearColor(0, 0, 0, 0);
        }
        OutParsedChunk.Vertices.Add(V);
    }

    // IndexCount (uint32) + Indices[]
    if (Offset + 4 > DataLen)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[MESH][V1] Truncated payload: cannot read IndexCount for GUID=%s chunk=%u"),
            *Guid.ToString(EGuidFormats::Digits), ChunkIndex);
        return false;
    }

    uint32 IndexCount = 0;
    FMemory::Memcpy(&IndexCount, Data + Offset, sizeof(uint32));
    Offset += 4;

    uint32 IndexBytes = IndexCount * sizeof(uint32);
    if (Offset + static_cast<int32>(IndexBytes) > DataLen)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[MESH][V1] Truncated index payload for GUID=%s chunk=%u: "
                 "need %u bytes, have %d"),
            *Guid.ToString(EGuidFormats::Digits), ChunkIndex,
            IndexBytes, DataLen - Offset);
        return false;
    }

    // Validate and store indices
    OutParsedChunk.Indices.Reserve(IndexCount);
    for (uint32 i = 0; i < IndexCount; i++)
    {
        uint32 Index = 0;
        FMemory::Memcpy(&Index, Data + Offset, sizeof(uint32));
        Offset += sizeof(uint32);
        if (Index >= VertexCount)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[MESH][V1] Out-of-bounds index %u (>= VertexCount=%u) "
                     "for GUID=%s chunk=%u"),
                Index, VertexCount,
                *Guid.ToString(EGuidFormats::Digits), ChunkIndex);
            return false;
        }
        OutParsedChunk.Indices.Add(Index);
    }

    OutParsedChunk.ChunkIndex   = ChunkIndex;
    OutParsedChunk.ChunkCount   = ChunkCount;
    OutParsedChunk.VertexStride = VertexStride;
    OutParsedChunk.VertexCount  = VertexCount;
    OutParsedChunk.IndexCount   = IndexCount;

    return true;
}


// =========================================================
// HANDLE MESH CHUNK (Phase 7C Stage 1B)
// =========================================================

void UUELiveSyncSubsystem::
HandleMeshChunk(
    const FGuid& Guid,
    const FString& VersionHash,
    uint32 ChunkIndex,
    uint32 ChunkCount,
    uint8 Flags,
    const TArrayView<const uint8>& Payload)
{
    CHECK_GAME_THREAD();

    if (!Guid.IsValid())
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[MESH] HandleMeshChunk: invalid GUID"));
        Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    if (ChunkCount == 0 || ChunkIndex >= ChunkCount)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[MESH] HandleMeshChunk: invalid chunk index/count "
                 "(%u/%u) for GUID=%s"),
            ChunkIndex, ChunkCount,
            *Guid.ToString(EGuidFormats::Digits));
        Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    // Phase 10J.5E/K: Skip chunk accumulation for FBX-authoritative AND FBX-pending GUIDs.
    if (FBXAuthoritativeGuids.Contains(Guid) || FBXPendingGuids.Contains(Guid))
    {
        if (FBXPendingGuids.Contains(Guid))
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[MESH][AUTH] skip_pt_mesh_fbx_pending guid=%s"),
                *Guid.ToString(EGuidFormats::Digits));
        }
        else
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[MESH][AUTH] skip_pt_mesh_fbx_authoritative guid=%s"),
                *Guid.ToString(EGuidFormats::Digits));
        }
        Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    if (PendingMeshReassembly.Num() >= MAX_CONCURRENT_MESH_REASSEMBLIES &&
        !PendingMeshReassembly.Contains(Guid))
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[MESH] Too many pending reassemblies (%d) \u2014 "
                 "rejecting chunk for GUID=%s"),
            PendingMeshReassembly.Num(),
            *Guid.ToString(EGuidFormats::Digits));
        Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    FMeshReassemblyState& State =
        PendingMeshReassembly.FindOrAdd(Guid);

    if (State.ChunkCount == 0)
    {
        State.VersionHash = VersionHash;
        State.ChunkCount  = ChunkCount;
        State.Flags       = Flags;
        State.FirstChunkTime = FPlatformTime::Seconds();
    }
    else
    {
        if (State.VersionHash != VersionHash ||
            State.ChunkCount != ChunkCount)
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[MESH] Conflicting version hash or count for "
                     "GUID=%s (existing=%s/%u new=%s/%u)"),
                *Guid.ToString(EGuidFormats::Digits),
                *State.VersionHash, State.ChunkCount,
                *VersionHash, ChunkCount);
            PendingMeshReassembly.Remove(Guid);
            Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed);
            return;
        }
    }

    if (State.Chunks.Contains(ChunkIndex))
    {
        UE_LOG(LogLiveSync, Verbose,
            TEXT("[MESH] Duplicate chunk %u/%u for GUID=%s"),
            ChunkIndex, ChunkCount,
            *Guid.ToString(EGuidFormats::Digits));
        return;
    }

    TArray<uint8>& StoredPayload =
        State.Chunks.Add(ChunkIndex);
    StoredPayload.Append(
        Payload.GetData(),
        Payload.Num());

    State.ChunksReceived++;
    MeshChunksReceived++;

    if (State.IsComplete())
    {
        MeshReassembliesCompleted++;

        UE_LOG(LogLiveSync, Log,
            TEXT("[MESH] Reassembly complete for GUID=%s "
                 "(%u/%u chunks) \u2014 %d total chunks received this session"),
            *Guid.ToString(EGuidFormats::Digits),
            State.ChunksReceived,
            State.ChunkCount,
            MeshChunksReceived);
    }
}


// =========================================================
// RECONSTRUCT COMPLETED MESHES (Phase 7C Stage 1C)
// =========================================================

void UUELiveSyncSubsystem::
ReconstructCompletedMeshes()
{
    CHECK_GAME_THREAD();

    int32 DiagBuildCount = 0;
    int32 BuildsThisTick = 0;
    const int32 MaxBuildsPerTick =
        CVarLiveSyncMaxMeshBuildsPerTick.GetValueOnGameThread();
    TArray<FGuid> Reconstructed;

    for (auto& Pair : PendingMeshReassembly)
    {
        FMeshReassemblyState& State = Pair.Value;

        if (!State.IsComplete() || State.bReconstructed)
        {
            continue;
        }

        const FGuid& Guid = Pair.Key;

        // Phase 10J.5E: Skip reconstruction for FBX-authoritative GUIDs.
        // Clean up any stale pending data that may have accumulated before
        // FBX promotion.
        if (FBXAuthoritativeGuids.Contains(Guid))
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[MESH][AUTH] skip_pt_mesh_fbx_authoritative guid=%s"),
                *Guid.ToString(EGuidFormats::Digits));
            State.bReconstructed = true;
            Reconstructed.Add(Guid);
            continue;
        }

        // Per-tick build limit: time-slice mesh rebuilds across frames
        // to prevent game-thread stalls during snapshot replay.
        if (BuildsThisTick >= MaxBuildsPerTick)
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[MESH] Tick mesh build limit reached (%d/%d) for GUID=%s"
                     " \u2014 deferring remaining %d rebuilds"),
                BuildsThisTick, MaxBuildsPerTick,
                *Guid.ToString(EGuidFormats::Digits),
                PendingMeshReassembly.Num());
            break;
        }

        AActor* Actor = FindActorFast(Guid);
        if (!Actor)
        {
            continue;
        }

        UProceduralMeshComponent* ProcMesh =
            Actor->FindComponentByClass<
                UProceduralMeshComponent>();

        bool bMultiProcMeshNew = false;

        if (!ProcMesh)
        {
            bMultiProcMeshNew = true;

            ProcMesh =
                NewObject<UProceduralMeshComponent>(
                    Actor);

            if (Actor->GetRootComponent())
            {
                ProcMesh->SetupAttachment(
                    Actor->GetRootComponent());
            }
            else
            {
                Actor->SetRootComponent(
                    ProcMesh);
            }

            ProcMesh->RegisterComponent();
        }

        TArray<FVector>   Vertices;
        TArray<int32>     Triangles;
        TArray<int32>     MaterialIndices;

        int32 TotalVertices = 0;
        int32 TotalTriangles = 0;

        for (uint32 i = 0; i < State.ChunkCount; i++)
        {
            const TArray<uint8>* ChunkData =
                State.Chunks.Find(i);

            if (!ChunkData)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[MESH] Missing chunk %u/%u for GUID=%s "
                         "\u2014 skipping reconstruction"),
                    i, State.ChunkCount,
                    *Guid.ToString(EGuidFormats::Digits));
                return;
            }

            const uint8* Data = ChunkData->GetData();
            int32 DataLen = ChunkData->Num();

            int32 Offset = 0;

            if (Offset + 4 > DataLen) { return; }
            int32 VCount = *reinterpret_cast<const int32*>(Data + Offset);
            Offset += 4;
            TotalVertices += VCount;

            int32 VertexBytes = VCount * 12;
            if (Offset + VertexBytes > DataLen) { return; }
            Offset += VertexBytes;

            if (Offset + 4 > DataLen) { return; }
            int32 TCount = *reinterpret_cast<const int32*>(Data + Offset);
            Offset += 4;
            TotalTriangles += TCount;

            int32 TriBytes = TCount * 12;
            if (Offset + TriBytes > DataLen) { return; }
            Offset += TriBytes;

            if (Offset + 4 > DataLen) { return; }
            int32 MCount = *reinterpret_cast<const int32*>(Data + Offset);
            Offset += 4;

            int32 MatBytes = MCount * 4;
            if (Offset + MatBytes > DataLen) { return; }
            Offset += MatBytes;
        }

        if (TotalVertices == 0 || TotalTriangles == 0)
        {
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[MESH] Empty geometry for GUID=%s "
                     "\u2014 skipping reconstruction"),
                *Guid.ToString(EGuidFormats::Digits));
            State.bReconstructed = true;
            Reconstructed.Add(Guid);
            continue;
        }

        Vertices.Reserve(TotalVertices);
        Triangles.Reserve(TotalTriangles);
        MaterialIndices.Reserve(TotalTriangles);
        int32 VertexBase = 0;

        // The Y-flip axis conversion is a reflection (determinant = -1),
        // so it changes handedness. Reverse triangle winding to keep
        // outside-faces visible (fix inside-out / see-through artifact).
        const bool bWindingFlipped = true;

        for (uint32 i = 0; i < State.ChunkCount; i++)
        {
            const TArray<uint8>& ChunkData = *State.Chunks.Find(i);
            const uint8* Data = ChunkData.GetData();
            int32 Offset = 0;

            int32 VCount = *reinterpret_cast<const int32*>(Data + Offset);
            Offset += 4;

            for (int32 v = 0; v < VCount; v++)
            {
                const float* F = reinterpret_cast<const float*>(Data + Offset);
                // Convert Blender-local → UE-local (match Blender-side conversion matrix:
                //   Y → -Y flip). Blender sends raw mesh vertex coords in Blender
                //   coordinate space. Actor transform already carries the Y-flipped
                //   quaternion, so vertices must use the same convention.
                const float BlenderX = F[0];
                const float BlenderY = F[1];
                const float BlenderZ = F[2];
                FVector UEV(BlenderX * 100.0f, -BlenderY * 100.0f, BlenderZ * 100.0f);
                Vertices.Add(UEV);
                Offset += 12;
            }

            int32 TCount = *reinterpret_cast<const int32*>(Data + Offset);
            Offset += 4;

            for (int32 t = 0; t < TCount; t++)
            {
                const int32* Idx = reinterpret_cast<const int32*>(Data + Offset);
                // Original winding: A, B, C → flipped: A, C, B
                if (bWindingFlipped)
                {
                    Triangles.Add(Idx[0] + VertexBase);
                    Triangles.Add(Idx[2] + VertexBase);
                    Triangles.Add(Idx[1] + VertexBase);
                }
                else
                {
                    Triangles.Add(Idx[0] + VertexBase);
                    Triangles.Add(Idx[1] + VertexBase);
                    Triangles.Add(Idx[2] + VertexBase);
                }
                Offset += 12;
            }

            int32 MCount = *reinterpret_cast<const int32*>(Data + Offset);
            Offset += 4;

            for (int32 m = 0; m < MCount; m++)
            {
                const int32 MatIdx = *reinterpret_cast<const int32*>(Data + Offset);
                MaterialIndices.Add(MatIdx);
                Offset += 4;
            }

            VertexBase += VCount;
        }

        {
            FBox LocalBox(ForceInit);
            int32 NanCount = 0;
            int32 ZeroCount = 0;
            for (const FVector& V : Vertices)
            {
                LocalBox += V;
                if (FMath::IsNaN(V.X) || FMath::IsNaN(V.Y) || FMath::IsNaN(V.Z))
                    NanCount++;
                if (FMath::Abs(V.X) < KINDA_SMALL_NUMBER &&
                    FMath::Abs(V.Y) < KINDA_SMALL_NUMBER &&
                    FMath::Abs(V.Z) < KINDA_SMALL_NUMBER)
                    ZeroCount++;
            }
            int32 MinTriIdx = MAX_int32, MaxTriIdx = MIN_int32;
            int32 InvalidIdxCount = 0;
            for (int32 Idx : Triangles)
            {
                if (Idx < MinTriIdx) MinTriIdx = Idx;
                if (Idx > MaxTriIdx) MaxTriIdx = Idx;
                if (Idx < 0 || Idx >= TotalVertices)
                    InvalidIdxCount++;
            }
            if (GEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[MESH][DIAG] GUID=%s: verts=%d tris=%d "
                         "bbox=%s extent=%s "
                         "NaN=%d zero=%d triIdxRange=[%d,%d] invalidIdx=%d"),
                    *Guid.ToString(EGuidFormats::Digits),
                    TotalVertices, Triangles.Num() / 3,
                    *LocalBox.ToString(), *LocalBox.GetExtent().ToString(),
                    NanCount, ZeroCount,
                    MinTriIdx, MaxTriIdx, InvalidIdxCount);
                if (TotalVertices > 0)
                {
                    UE_LOG(LogLiveSync, Warning,
                        TEXT("[MESH][DIAG] GUID=%s first 3 verts: v0=(%g,%g,%g) v1=(%g,%g,%g) v2=(%g,%g,%g)"),
                        *Guid.ToString(EGuidFormats::Digits),
                        Vertices[0].X, Vertices[0].Y, Vertices[0].Z,
                        TotalVertices > 1 ? Vertices[1].X : 0,
                        TotalVertices > 1 ? Vertices[1].Y : 0,
                        TotalVertices > 1 ? Vertices[1].Z : 0,
                        TotalVertices > 2 ? Vertices[2].X : 0,
                        TotalVertices > 2 ? Vertices[2].Y : 0,
                        TotalVertices > 2 ? Vertices[2].Z : 0);
                    // Compact axis diagnostic: first Blender→UE conversion + winding + bounds
                    const FVector& FirstBlenderV = Vertices[0];
                    UE_LOG(LogLiveSync, Warning,
                        TEXT("[MESH][AXIS] firstBlenderV=(%g,%g,%g) firstUEV=(%g,%g,%g) windingFlipped=%d boundsExtent=%s"),
                        FirstBlenderV.X, -FirstBlenderV.Y, FirstBlenderV.Z,
                        FirstBlenderV.X, FirstBlenderV.Y, FirstBlenderV.Z,
                        bWindingFlipped ? 1 : 0,
                        *LocalBox.GetExtent().ToString());
                }
            }
        }

        int32 NumSections = 0;

        if (MaterialIndices.Num() == Triangles.Num() / 3)
        {
            TMap<int32, TArray<int32>> MaterialGroups;
            for (int32 t = 0; t < Triangles.Num() / 3; t++)
            {
                int32 MatIdx = (t < MaterialIndices.Num())
                    ? MaterialIndices[t]
                    : 0;
                MaterialGroups.FindOrAdd(MatIdx).Add(t);
            }

            for (auto& Group : MaterialGroups)
            {
                int32 SectionIndex = Group.Key;
                const TArray<int32>& TriIndices = Group.Value;

                TArray<FVector> SectionVerts;
                TArray<int32> SectionTris;
                TArray<FVector> SectionNormals;
                TArray<FVector2D> SectionUVs;
                TArray<FColor> SectionColors;
                TArray<FProcMeshTangent> SectionTangents;

                TMap<int32, int32> VMap;
                for (int32 triIdx : TriIndices)
                {
                    int32 BaseIdx = triIdx * 3;
                    for (int32 j = 0; j < 3; j++)
                    {
                        int32 OrigIdx = Triangles[BaseIdx + j];
                        int32* NewIdx = VMap.Find(OrigIdx);
                        if (!NewIdx)
                        {
                            NewIdx = &VMap.Add(OrigIdx, SectionVerts.Num());
                            SectionVerts.Add(Vertices[OrigIdx]);
                        }
                        SectionTris.Add(*NewIdx);
                    }
                }

                // Procedural UV: identity mapping since Blender doesn't send UVs.
                // This enables proper tangent basis computation in CalculateTangentsForMesh.
                SectionUVs.SetNum(SectionVerts.Num());
                for (int32 i = 0; i < SectionVerts.Num(); i++)
                {
                    SectionUVs[i] = FVector2D(0.0f, 0.0f);
                }

                // Calculate normals + tangents from vertices + triangles + UVs.
                UKismetProceduralMeshLibrary::CalculateTangentsForMesh(
                    SectionVerts,
                    SectionTris,
                    SectionUVs,
                    SectionNormals,
                    SectionTangents);

                {
                    const FProcMeshSection* ExistingSection = ProcMesh->GetProcMeshSection(SectionIndex);
                    const int32 NewVerts = SectionVerts.Num();
                    if (ExistingSection && ExistingSection->ProcVertexBuffer.Num() == NewVerts)
                    {
                        ProcMesh->UpdateMeshSection(SectionIndex, SectionVerts, SectionNormals,
                            SectionUVs, TArray<FVector2D>(), TArray<FVector2D>(), TArray<FVector2D>(),
                            SectionColors, SectionTangents);
                    }
                    else
                    {
                        ProcMesh->CreateMeshSection(
                            SectionIndex,
                            SectionVerts,
                            SectionTris,
                            SectionNormals,
                            SectionUVs,
                            SectionColors,
                            SectionTangents,
                            true);
                    }
                }

                NumSections++;

                UE_LOG(LogLiveSync, Warning,
                    TEXT("[MESH][NORMAL] section=%d normals=%d tangents=%d"),
                    SectionIndex, SectionNormals.Num(), SectionTangents.Num());
            }
        }
        else
        {
            // Single-section path: generate procedural UVs + calculate normals/tangents.
            TArray<FVector> SectionVerts = Vertices;
            TArray<int32> SectionTris = Triangles;
            TArray<FVector> SectionNormals;
            TArray<FVector2D> SectionUVs;
            TArray<FColor> SectionColors;
            TArray<FProcMeshTangent> SectionTangents;

            // Procedural UV: identity mapping.
            SectionUVs.SetNum(SectionVerts.Num());
            for (int32 i = 0; i < SectionVerts.Num(); i++)
            {
                SectionUVs[i] = FVector2D(0.0f, 0.0f);
            }

            // Calculate normals + tangents.
            UKismetProceduralMeshLibrary::CalculateTangentsForMesh(
                SectionVerts,
                SectionTris,
                SectionUVs,
                SectionNormals,
                SectionTangents);

            {
                const FProcMeshSection* ExistingSection = ProcMesh->GetProcMeshSection(0);
                const int32 NewVerts = SectionVerts.Num();
                if (ExistingSection && ExistingSection->ProcVertexBuffer.Num() == NewVerts)
                {
                    ProcMesh->UpdateMeshSection(0, SectionVerts, SectionNormals,
                        SectionUVs, TArray<FVector2D>(), TArray<FVector2D>(), TArray<FVector2D>(),
                        SectionColors, SectionTangents);
                }
                else
                {
                    ProcMesh->CreateMeshSection(
                        0,
                        SectionVerts,
                        SectionTris,
                        SectionNormals,
                        SectionUVs,
                        SectionColors,
                        SectionTangents,
                        true);
                }
            }

            NumSections = 1;

            UE_LOG(LogLiveSync, Warning,
                TEXT("[MESH][NORMAL] section=0 normals=%d tangents=%d"),
                SectionNormals.Num(), SectionTangents.Num());
        }

        {
            int32 FinalSectionCount = ProcMesh->GetNumSections();
            FBoxSphereBounds ProcBounds = ProcMesh->Bounds;
            FVector ProcExtent = ProcBounds.GetBox().GetExtent();
            // Compute pre-scale extent by unscaling Vertices
            FBox PreScaleBox(ForceInit);
            for (const FVector& V : Vertices) { PreScaleBox += V / 100.0f; }
            if (GEnableVerboseSyncLogs)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[MESH][SCALE] preScaleExtent=%s postScaleExtent=%s "
                         "sections=%d boundsOrigin=(%g,%g,%g) boundsExtent=%s "
                         "sphereRadius=%g"),
                    *PreScaleBox.GetExtent().ToString(), *ProcExtent.ToString(),
                    FinalSectionCount,
                    ProcBounds.Origin.X, ProcBounds.Origin.Y, ProcBounds.Origin.Z,
                    *ProcExtent.ToString(),
                    ProcBounds.SphereRadius);
                // STEP1: after CreateMeshSection
                FBoxSphereBounds ActorBS1; Actor->GetActorBounds(false, ActorBS1.Origin, ActorBS1.BoxExtent);
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[MESH][STEP1] reg=%d vis=%d hidden=%d root=%s attach=%s boundsExtent=%s actorExtent=%s"),
                    ProcMesh->IsRegistered(), ProcMesh->IsVisible(), ProcMesh->bHiddenInGame,
                    Actor->GetRootComponent() ? *Actor->GetRootComponent()->GetClass()->GetName() : TEXT("None"),
                    ProcMesh->GetAttachParent() ? *ProcMesh->GetAttachParent()->GetClass()->GetName() : TEXT("None"),
                    *ProcExtent.ToString(), *ActorBS1.BoxExtent.ToString());
            }
        }

        MeshSectionsBuilt += NumSections;

        UE_LOG(LogLiveSync, Log,
            TEXT("[MESH] Reconstructed GUID=%s: %d verts, %d tris, "
                 "%d sections, %d total sections built this session"),
            *Guid.ToString(EGuidFormats::Digits),
            TotalVertices,
            TotalTriangles,
            NumSections,
            MeshSectionsBuilt);

        bool bMultiRootChanged = (Actor->GetRootComponent() != ProcMesh);

        {
            UStaticMeshComponent* PlaceholderSMC =
                Actor->FindComponentByClass<UStaticMeshComponent>();
            if (PlaceholderSMC && PlaceholderSMC->IsVisible())
            {
                PlaceholderSMC->SetVisibility(false, false);
                PlaceholderSMC->SetHiddenInGame(true, false);
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MESH] Hidden placeholder SMC for GUID=%s"),
                    *Guid.ToString(EGuidFormats::Digits));
            }

            // (bMultiRootChanged already declared above)
            if (bMultiRootChanged)
            {
                ProcMesh->DetachFromComponent(
                    FDetachmentTransformRules::KeepWorldTransform);
                Actor->SetRootComponent(ProcMesh);
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MESH] Promoted ProcMesh to root for GUID=%s"),
                    *Guid.ToString(EGuidFormats::Digits));
            }
            // Explicitly restore ProcMesh visibility (placeholder SMC hide can propagate to children)
            ProcMesh->SetVisibility(true, true);
            ProcMesh->SetHiddenInGame(false, true);
            ProcMesh->UpdateBounds();
            if (GEnableVerboseSyncLogs)
            {
                // STEP2: after SetRootComponent(ProcMesh)
                {
                    FBoxSphereBounds ProcBS2 = ProcMesh->Bounds;
                    FBoxSphereBounds ActorBS2; Actor->GetActorBounds(false, ActorBS2.Origin, ActorBS2.BoxExtent);
                    UE_LOG(LogLiveSync, Warning,
                        TEXT("[MESH][STEP2] reg=%d vis=%d hidden=%d root=%s attach=%s boundsExtent=%s actorExtent=%s"),
                        ProcMesh->IsRegistered(), ProcMesh->IsVisible(), ProcMesh->bHiddenInGame,
                        Actor->GetRootComponent() ? *Actor->GetRootComponent()->GetClass()->GetName() : TEXT("None"),
                        ProcMesh->GetAttachParent() ? *ProcMesh->GetAttachParent()->GetClass()->GetName() : TEXT("None"),
                        *ProcBS2.GetBox().GetExtent().ToString(), *ActorBS2.BoxExtent.ToString());
                }

                // STEP3: after visibility restore (bounds already updated above)
                {
                    FBoxSphereBounds ProcBS3 = ProcMesh->Bounds;
                    FBoxSphereBounds ActorBS3; Actor->GetActorBounds(false, ActorBS3.Origin, ActorBS3.BoxExtent);
                    UE_LOG(LogLiveSync, Warning,
                        TEXT("[MESH][STEP3] reg=%d vis=%d hidden=%d root=%s attach=%s boundsExtent=%s actorExtent=%s"),
                        ProcMesh->IsRegistered(), ProcMesh->IsVisible(), ProcMesh->bHiddenInGame,
                        Actor->GetRootComponent() ? *Actor->GetRootComponent()->GetClass()->GetName() : TEXT("None"),
                        ProcMesh->GetAttachParent() ? *ProcMesh->GetAttachParent()->GetClass()->GetName() : TEXT("None"),
                        *ProcBS3.GetBox().GetExtent().ToString(), *ActorBS3.BoxExtent.ToString());
                }
            }
        }

        DiagBuildCount++;
        BuildsThisTick++;
        State.bReconstructed = true;
        Reconstructed.Add(Guid);
    }

    for (const FGuid& Guid : Reconstructed)
    {
        PendingMeshReassembly.Remove(Guid);
    }

    // Phase 7C Stage 2C.3: Build ProceduralMesh from completed v1 reassemblies
    BuildV1MeshFromReassembly();
}


// =========================================================
// BUILD V1 MESH FROM REASSEMBLY (Phase 7C Stage 2C.3)
// =========================================================
// Merges completed v1 reassembly chunks into final arrays,
// converts Blender→UE coordinate space (Y-flip + cm scale),
// flips winding (Blender CW → UE CCW), and builds one
// ProceduralMesh section per mesh.
//
// Missing actors: data is kept (not cleared) in case the
// actor appears in a later tick. Counter incremented.
// =========================================================

void UUELiveSyncSubsystem::
BuildV1MeshFromReassembly()
{
    CHECK_GAME_THREAD();

    int32 DiagBuildCount = 0;
    int32 BuildsThisTick = 0;
    const int32 MaxBuildsPerTick =
        CVarLiveSyncMaxMeshBuildsPerTick.GetValueOnGameThread();

    TArray<FV1MeshReassemblyKey> Reconstructed;

    for (auto& Pair : PendingV1MeshReassembly)
    {
        FV1MeshReassemblyState& State = Pair.Value;

        if (!State.IsComplete() || State.bReconstructed)
        {
            continue;
        }

        const FV1MeshReassemblyKey& Key = Pair.Key;
        const FGuid& Guid = Key.Guid;

        // Phase 10J.5E/K: Skip V1 reconstruction for FBX-authoritative AND
        // FBX-pending GUIDs.
        if (FBXAuthoritativeGuids.Contains(Guid) || FBXPendingGuids.Contains(Guid))
        {
            if (FBXPendingGuids.Contains(Guid))
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MESH][AUTH] skip_pt_mesh_fbx_pending guid=%s"),
                    *Guid.ToString(EGuidFormats::Digits));
            }
            else
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MESH][AUTH] skip_v1_pt_mesh_fbx_authoritative guid=%s"),
                    *Guid.ToString(EGuidFormats::Digits));
            }
            State.bReconstructed = true;
            Reconstructed.Add(Key);
            continue;
        }

        // Per-tick build limit: time-slice mesh rebuilds across frames
        // to prevent game-thread stalls during snapshot replay.
        if (BuildsThisTick >= MaxBuildsPerTick)
        {
            UE_LOG(LogLiveSync, Log,
                TEXT("[MESH][V1] Tick mesh build limit reached (%d/%d)"
                     " for GUID=%s vhash=%s \u2014 deferring remaining %d rebuilds"),
                BuildsThisTick, MaxBuildsPerTick,
                *Guid.ToString(EGuidFormats::Digits),
                *Key.VersionHash,
                PendingV1MeshReassembly.Num());
            break;
        }

        // Resolve actor from GUID
        AActor* Actor = FindActorFast(Guid);
        if (!Actor)
        {
            Stats.MeshSchemaV1MissingActor.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[MESH][V1] Missing actor for GUID=%s vhash=%s "
                     "\u2014 keeping data for late actor resolution"),
                *Guid.ToString(EGuidFormats::Digits),
                *Key.VersionHash);
            // Keep completed data; actor may appear later via BuildActorCache
            continue;
        }

        // Merge all chunks in order
        TArray<FVector> Positions;
        TArray<int32> Indices;
        TArray<FVector> Normals;
        TArray<FVector2D> UV0;
        TArray<FColor> Colors;
        bool bHasColor0 = false;
        bool bBuildValid = true;
        uint32 VertexBase = 0;

        for (uint32 i = 0; i < State.ChunkCount; i++)
        {
            const FV1MeshParsedChunk* Chunk = State.Chunks.Find(i);
            if (!Chunk)
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[MESH][V1] Missing chunk %u/%u for GUID=%s vhash=%s"),
                    i, State.ChunkCount,
                    *Guid.ToString(EGuidFormats::Digits),
                    *Key.VersionHash);
                bBuildValid = false;
                break;
            }

            bool bChunkHasColor0 = (Chunk->VertexStride == 48);
            bHasColor0 = bHasColor0 || bChunkHasColor0;

            // Convert and add vertices
            for (uint32 v = 0; v < Chunk->VertexCount; v++)
            {
                const FV1MeshParsedVertex& Vert = Chunk->Vertices[v];

                // Blender → UE: Y-flip + cm scale (same convention as V5)
                FVector UEPos(
                    Vert.Position.X * 100.0f,
                    -Vert.Position.Y * 100.0f,
                    Vert.Position.Z * 100.0f);
                Positions.Add(UEPos);

                // Normal: Y-flip only (no scale)
                FVector UENormal(
                    Vert.Normal.X,
                    -Vert.Normal.Y,
                    Vert.Normal.Z);
                Normals.Add(UENormal);

                // UV0: no conversion
                UV0.Add(Vert.UV0);

                // Color0: convert FLinearColor → FColor (linear, no sRGB)
                if (bChunkHasColor0)
                {
                    Colors.Add(Vert.Color0.ToFColor(false));
                }
            }

            // Add indices with VertexBase offset; flip winding (CW→CCW per Y-flip handedness)
            for (uint32 idx = 0; idx + 2 < Chunk->IndexCount; idx += 3)
            {
                Indices.Add(Chunk->Indices[idx] + VertexBase);
                Indices.Add(Chunk->Indices[idx + 2] + VertexBase);
                Indices.Add(Chunk->Indices[idx + 1] + VertexBase);
            }

            VertexBase += Chunk->VertexCount;
        }

        if (!bBuildValid)
        {
            Stats.MeshSchemaV1BuildRejected.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[MESH][V1] Build rejected for GUID=%s vhash=%s "
                     "\u2014 missing chunk during merge"),
                *Guid.ToString(EGuidFormats::Digits),
                *Key.VersionHash);
            State.bReconstructed = true;
            Reconstructed.Add(Key);
            continue;
        }

        if (Positions.Num() == 0 || Indices.Num() == 0)
        {
            Stats.MeshSchemaV1BuildRejected.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[MESH][V1] Empty geometry for GUID=%s vhash=%s "
                     "\u2014 skipping build"),
                *Guid.ToString(EGuidFormats::Digits),
                *Key.VersionHash);
            State.bReconstructed = true;
            Reconstructed.Add(Key);
            continue;
        }

        // Finite-float validation: reject if any position/normal/UV/color
        // contains NaN or Inf before handing data to CreateMeshSection.
        int32 NanPosCount = 0;
        int32 NanNormalCount = 0;
        int32 NanUVCount = 0;
        for (int32 vi = 0; vi < Positions.Num(); vi++)
        {
            if (!IsFiniteVec3(Positions[vi]))
                NanPosCount++;
            if (!IsFiniteVec3(Normals[vi]))
                NanNormalCount++;
            if (!IsFiniteVec2(UV0[vi]))
                NanUVCount++;
        }

        if (NanPosCount > 0 || NanNormalCount > 0 || NanUVCount > 0)
        {
            Stats.MeshSchemaV1BuildRejected.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[MESH][V1] Invalid float data for GUID=%s vhash=%s: "
                     "NaN/Inf positions=%d normals=%d uvs=%d \u2014 rejecting build"),
                *Guid.ToString(EGuidFormats::Digits),
                *Key.VersionHash,
                NanPosCount, NanNormalCount, NanUVCount);
            State.bReconstructed = true;
            Reconstructed.Add(Key);
            continue;
        }

        // Build-time diagnostics: bounds and first 3 sample vertices
        {
            FBox Bounds(ForceInit);
            for (const FVector& P : Positions)
                Bounds += P;
            UE_LOG(LogLiveSync, Log,
                TEXT("[MESH][V1] Bounds for GUID=%s vhash=%s: "
                     "Min=(%.6f, %.6f, %.6f) Max=(%.6f, %.6f, %.6f) "
                     "verts=%d tris=%d"),
                *Guid.ToString(EGuidFormats::Digits),
                *Key.VersionHash,
                Bounds.Min.X, Bounds.Min.Y, Bounds.Min.Z,
                Bounds.Max.X, Bounds.Max.Y, Bounds.Max.Z,
                Positions.Num(), Indices.Num() / 3);
            int32 Samples = FMath::Min(3, Positions.Num());
            for (int32 si = 0; si < Samples; si++)
            {
                UE_LOG(LogLiveSync, Verbose,
                    TEXT("[MESH][V1] Sample %d: pos=(%.6f, %.6f, %.6f) "
                         "normal=(%.6f, %.6f, %.6f) uv=(%.6f, %.6f)"),
                    si,
                    Positions[si].X, Positions[si].Y, Positions[si].Z,
                    Normals[si].X, Normals[si].Y, Normals[si].Z,
                    UV0[si].X, UV0[si].Y);
            }
        }

        // Triangle validation: each triangle must have 3 valid, in-bounds indices.
        // Skip degenerate triangles (3 identical indices), log count.
        int32 DegenerateCount = 0;
        TArray<int32> ValidIndices;
        ValidIndices.Reserve(Indices.Num());
        int32 NumVerts = Positions.Num();
        bool bTrianglesValid = true;
        for (int32 ti = 0; bTrianglesValid && ti + 2 < Indices.Num(); ti += 3)
        {
            int32 IA = Indices[ti];
            int32 IB = Indices[ti + 1];
            int32 IC = Indices[ti + 2];
            if (IA < 0 || IA >= NumVerts ||
                IB < 0 || IB >= NumVerts ||
                IC < 0 || IC >= NumVerts)
            {
                Stats.MeshSchemaV1BuildRejected.fetch_add(1, std::memory_order_relaxed);
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[MESH][V1] Out-of-range triangle indices "
                         "for GUID=%s vhash=%s \u2014 rejecting build"),
                    *Guid.ToString(EGuidFormats::Digits),
                    *Key.VersionHash);
                bTrianglesValid = false;
                break;
            }
            if (IA == IB && IB == IC)
            {
                DegenerateCount++;
                continue;
            }
            ValidIndices.Add(IA);
            ValidIndices.Add(IB);
            ValidIndices.Add(IC);
        }

        if (!bTrianglesValid)
        {
            State.bReconstructed = true;
            Reconstructed.Add(Key);
            continue;
        }

        if (DegenerateCount > 0)
        {
            UE_LOG(LogLiveSync, Verbose,
                TEXT("[MESH][V1] Skipped %d degenerate triangle(s) "
                     "for GUID=%s vhash=%s"),
                DegenerateCount,
                *Guid.ToString(EGuidFormats::Digits),
                *Key.VersionHash);
        }

        if (ValidIndices.Num() < 3)
        {
            Stats.MeshSchemaV1BuildRejected.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[MESH][V1] No valid triangles after degenerate filter "
                     "for GUID=%s vhash=%s \u2014 rejecting build"),
                *Guid.ToString(EGuidFormats::Digits),
                *Key.VersionHash);
            State.bReconstructed = true;
            Reconstructed.Add(Key);
            continue;
        }

        // Normal validation diagnostic: compare vertex normals with computed face normals
        {
            int32 TriangleCount = ValidIndices.Num() / 3;
            int32 NegativeDotCount = 0;
            int32 ZeroNormalCount = 0;
            double TotalDot = 0.0;
            int32 CheckedCount = 0;

            for (int32 ti = 0; ti < TriangleCount; ti++)
            {
                int32 IA = ValidIndices[ti * 3];
                int32 IB = ValidIndices[ti * 3 + 1];
                int32 IC = ValidIndices[ti * 3 + 2];

                FVector FaceNormal = FVector::CrossProduct(
                    Positions[IB] - Positions[IA],
                    Positions[IC] - Positions[IA]
                ).GetSafeNormal();

                for (int32 VI : {IA, IB, IC})
                {
                    const FVector& VN = Normals[VI];
                    if (!IsFiniteVec3(VN) || VN.IsNearlyZero())
                    {
                        ZeroNormalCount++;
                    }
                    else
                    {
                        float Dot = FVector::DotProduct(FaceNormal, VN);
                        TotalDot += Dot;
                        CheckedCount++;
                        if (Dot < 0.0f)
                            NegativeDotCount++;
                    }
                }
            }

            float AvgDot = (CheckedCount > 0) ? static_cast<float>(TotalDot / CheckedCount) : 0.0f;

            UE_LOG(LogLiveSync, Log,
                TEXT("[MESH][V1][NORMAL] GUID=%s vhash=%s: "
                     "tris=%d avgDot=%.4f negative=%d zero=%d checked=%d"),
                *Guid.ToString(EGuidFormats::Digits),
                *Key.VersionHash,
                TriangleCount, AvgDot, NegativeDotCount, ZeroNormalCount, CheckedCount);

            // If most dot products are negative, flip all normals
            if (CheckedCount > 0 && NegativeDotCount > CheckedCount / 2)
            {
                UE_LOG(LogLiveSync, Verbose,
                    TEXT("[MESH][V1][NORMAL] Flipping %d normals (negative=%d/%d) "
                         "for GUID=%s vhash=%s"),
                    Normals.Num(), NegativeDotCount, CheckedCount,
                    *Guid.ToString(EGuidFormats::Digits),
                    *Key.VersionHash);
                for (FVector& N : Normals)
                    N = -N;
            }

            // Replace zero/invalid normals with computed face normals
            if (ZeroNormalCount > 0)
            {
                int32 ReplacedCount = 0;
                for (int32 ti = 0; ti < TriangleCount; ti++)
                {
                    int32 IA = ValidIndices[ti * 3];
                    int32 IB = ValidIndices[ti * 3 + 1];
                    int32 IC = ValidIndices[ti * 3 + 2];

                    FVector FaceNormal = FVector::CrossProduct(
                        Positions[IB] - Positions[IA],
                        Positions[IC] - Positions[IA]
                    ).GetSafeNormal();

                    for (int32 VI : {IA, IB, IC})
                    {
                        if (!IsFiniteVec3(Normals[VI]) || Normals[VI].IsNearlyZero())
                        {
                            Normals[VI] = FaceNormal;
                            ReplacedCount++;
                        }
                    }
                }
                UE_LOG(LogLiveSync, Verbose,
                    TEXT("[MESH][V1][NORMAL] Replaced %d zero/invalid normals "
                         "for GUID=%s vhash=%s"),
                    ReplacedCount,
                    *Guid.ToString(EGuidFormats::Digits),
                    *Key.VersionHash);
            }
        }

        // Outward winding diagnostic: compute face normal vs outward direction.
        // For closed meshes, face normal should point away from mesh center.
        // If majority of triangles face inward, flip winding + normals.
        {
            FBox WindingBounds(ForceInit);
            for (const FVector& P : Positions)
                WindingBounds += P;
            FVector MeshCenter = WindingBounds.GetCenter();

            int32 WindingTriCount = ValidIndices.Num() / 3;
            int32 InwardCount = 0;
            int32 ZeroOutwardCount = 0;
            double TotalOutwardDot = 0.0;
            int32 CheckedOutward = 0;

            for (int32 ti = 0; ti < WindingTriCount; ti++)
            {
                int32 IA = ValidIndices[ti * 3];
                int32 IB = ValidIndices[ti * 3 + 1];
                int32 IC = ValidIndices[ti * 3 + 2];

                const FVector& A = Positions[IA];
                const FVector& B = Positions[IB];
                const FVector& C = Positions[IC];

                FVector FaceNormal = FVector::CrossProduct(B - A, C - A).GetSafeNormal();
                if (FaceNormal.IsNearlyZero())
                {
                    ZeroOutwardCount++;
                    continue;
                }

                FVector FaceCenter = (A + B + C) / 3.0f;
                FVector OutwardVec = (FaceCenter - MeshCenter).GetSafeNormal();
                if (OutwardVec.IsNearlyZero())
                {
                    ZeroOutwardCount++;
                    continue;
                }

                float OutwardDot = FVector::DotProduct(FaceNormal, OutwardVec);
                TotalOutwardDot += OutwardDot;
                CheckedOutward++;
                if (OutwardDot < 0.0f)
                    InwardCount++;

                if (ti < 3)
                {
                    UE_LOG(LogLiveSync, Verbose,
                        TEXT("[MESH][V1][WINDING] Tri %d: outwardDot=%.4f "
                             "faceNormal=(%.4f,%.4f,%.4f) faceCenter=(%.4f,%.4f,%.4f)"),
                        ti, OutwardDot,
                        FaceNormal.X, FaceNormal.Y, FaceNormal.Z,
                        FaceCenter.X, FaceCenter.Y, FaceCenter.Z);
                }
            }

            float AvgOutwardDot = (CheckedOutward > 0)
                ? static_cast<float>(TotalOutwardDot / CheckedOutward)
                : 0.0f;

            UE_LOG(LogLiveSync, Log,
                TEXT("[MESH][V1][WINDING] GUID=%s vhash=%s: "
                     "tris=%d avgOutwardDot=%.4f inward=%d/%d zeroOutward=%d"),
                *Guid.ToString(EGuidFormats::Digits),
                *Key.VersionHash,
                WindingTriCount, AvgOutwardDot,
                InwardCount, CheckedOutward, ZeroOutwardCount);

            // Auto-fix: if majority of triangles face inward, flip all windings
            // and negate normals to produce consistent outward-facing mesh.
            if (CheckedOutward > 0 && InwardCount > CheckedOutward / 2)
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MESH][V1][WINDING] Flipping %d triangles outward "
                         "for GUID=%s vhash=%s (inward=%d/%d)"),
                    WindingTriCount,
                    *Guid.ToString(EGuidFormats::Digits),
                    *Key.VersionHash,
                    InwardCount, CheckedOutward);

                // Flip winding: swap B and C in each triangle
                for (int32 ti = 0; ti < WindingTriCount; ti++)
                {
                    int32 Base = ti * 3;
                    Swap(ValidIndices[Base + 1], ValidIndices[Base + 2]);
                }

                // Negate normals to stay consistent with flipped winding
                for (FVector& N : Normals)
                    N = -N;

                // Recompute outward diagnostics after fix
                {
                    double FixTotalOutwardDot = 0.0;
                    int32 FixChecked = 0;
                    int32 FixInward = 0;
                    for (int32 ti = 0; ti < WindingTriCount; ti++)
                    {
                        int32 IA = ValidIndices[ti * 3];
                        int32 IB = ValidIndices[ti * 3 + 1];
                        int32 IC = ValidIndices[ti * 3 + 2];

                        FVector FaceNormal = FVector::CrossProduct(
                            Positions[IB] - Positions[IA],
                            Positions[IC] - Positions[IA]).GetSafeNormal();
                        if (FaceNormal.IsNearlyZero()) continue;

                        FVector FaceCenter = (Positions[IA] + Positions[IB] + Positions[IC]) / 3.0f;
                        FVector OutwardVec = (FaceCenter - MeshCenter).GetSafeNormal();
                        if (OutwardVec.IsNearlyZero()) continue;

                        float Dot = FVector::DotProduct(FaceNormal, OutwardVec);
                        FixTotalOutwardDot += Dot;
                        FixChecked++;
                        if (Dot < 0.0f) FixInward++;
                    }
                    float FixAvg = (FixChecked > 0)
                        ? static_cast<float>(FixTotalOutwardDot / FixChecked)
                        : 0.0f;
                    UE_LOG(LogLiveSync, Log,
                        TEXT("[MESH][V1][WINDING] After fix for GUID=%s vhash=%s: "
                             "avgOutwardDot=%.4f inward=%d/%d"),
                        *Guid.ToString(EGuidFormats::Digits),
                        *Key.VersionHash,
                        FixAvg, FixInward, FixChecked);
                }

                // Log hint about one-sided materials
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MESH][V1][WINDING] Winding corrected for GUID=%s vhash=%s. "
                         "If mesh still appears inside-out, check material TwoSided setting."),
                    *Guid.ToString(EGuidFormats::Digits),
                    *Key.VersionHash);
            }
        }

        // Find or create ProceduralMeshComponent
        UProceduralMeshComponent* ProcMesh =
            Actor->FindComponentByClass<UProceduralMeshComponent>();

        bool bSingleProcMeshNew = false;

        if (!ProcMesh)
        {
            bSingleProcMeshNew = true;

            ProcMesh = NewObject<UProceduralMeshComponent>(Actor);

            if (Actor->GetRootComponent())
            {
                ProcMesh->SetupAttachment(
                    Actor->GetRootComponent());
            }
            else
            {
                Actor->SetRootComponent(ProcMesh);
            }

            ProcMesh->RegisterComponent();
        }

        // For stride 32 (no color0), pass empty color array
        TArray<FColor> SectionColors;
        if (bHasColor0)
        {
            SectionColors = MoveTemp(Colors);
        }

        // === STAGE 2C.9: Validate section array sizes ===

        // T8: If Normals.Num() != Vertices.Num(), reject build.
        if (Normals.Num() != Positions.Num())
        {
            Stats.MeshSchemaV1BuildRejected.fetch_add(1, std::memory_order_relaxed);
            UE_LOG(LogLiveSync, Warning,
                TEXT("[MESH][V1] Rejecting build: normals count (%d) != vertices count (%d) "
                     "for GUID=%s vhash=%s"),
                Normals.Num(), Positions.Num(),
                *Guid.ToString(EGuidFormats::Digits),
                *Key.VersionHash);
            State.bReconstructed = true;
            Reconstructed.Add(Key);
            continue;
        }

        // Save source v1 normals after validation/fix — must not be overwritten
        // by CalculateTangentsForMesh (which computes face normals).
        TArray<FVector> PreservedNormals = Normals;

        // === STAGE 2C.10: V1 DEBUG — FORCE FACE NORMALS ===
        // If CVar UE.LiveSync.V1DebugForceFaceNormals == 1, replace
        // all vertex normals with computed per-triangle face normals.
        // This is diagnostic only: it does not change packet format
        // or the legacy V5 path.
        int32 DebugFaceNormalsMode = 0;
        {
            int32 CV = CVarLiveSyncV1DebugForceFaceNormals.GetValueOnAnyThread();
            DebugFaceNormalsMode = (CV != 0) ? 1 : 0;
            if (DebugFaceNormalsMode)
            {
                int32 TriangleCount = ValidIndices.Num() / 3;
                TArray<FVector> FaceNormals;
                FaceNormals.Reserve(TriangleCount);
                for (int32 ti = 0; ti < TriangleCount; ti++)
                {
                    int32 IA = ValidIndices[ti * 3];
                    int32 IB = ValidIndices[ti * 3 + 1];
                    int32 IC = ValidIndices[ti * 3 + 2];
                    FVector FN = FVector::CrossProduct(
                        Positions[IB] - Positions[IA],
                        Positions[IC] - Positions[IA]
                    ).GetSafeNormal();
                    FaceNormals.Add(FN);
                }
                // Assign face normal to each vertex of each triangle
                // (last writer wins — sufficient for per-triangle isolation)
                PreservedNormals.Empty();
                PreservedNormals.SetNum(Positions.Num(), EAllowShrinking::No);
                for (int32 ti = 0; ti < TriangleCount; ti++)
                {
                    int32 IA = ValidIndices[ti * 3];
                    int32 IB = ValidIndices[ti * 3 + 1];
                    int32 IC = ValidIndices[ti * 3 + 2];
                    PreservedNormals[IA] = FaceNormals[ti];
                    PreservedNormals[IB] = FaceNormals[ti];
                    PreservedNormals[IC] = FaceNormals[ti];
                }
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MESH][V1][DEBUG_FACE_NORMALS] enabled normals=%d tris=%d"),
                    PreservedNormals.Num(), TriangleCount);
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MESH][V1][DEBUG_NORMALS] mode=face"));
            }
            else
            {
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MESH][V1][DEBUG_FACE_NORMALS] disabled normals=%d"),
                    PreservedNormals.Num());
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MESH][V1][DEBUG_NORMALS] mode=source"));
            }
        }

        // Generate tangents from geometry. Discard the recomputed normals
        // from CalculateTangentsForMesh — we preserve source v1 normals.
        TArray<FVector> TangentNormals;
        TArray<FProcMeshTangent> FinalTangents;
        UKismetProceduralMeshLibrary::CalculateTangentsForMesh(
            Positions,
            ValidIndices,
            UV0,
            TangentNormals,
            FinalTangents);

        // Orthogonalize each tangent against preserved normal (Gram-Schmidt).
        // Preserve original handedness by adjusting bFlipTangentY so that
        // cross(N, OrthoT) * sign aligns with the original bitangent direction.
        int32 DegenerateTangentCount = 0;
        float MaxNormalDelta = 0.0f;
        {
            int32 NormalCount = FMath::Min(PreservedNormals.Num(), TangentNormals.Num());
            for (int32 ni = 0; ni < NormalCount; ni++)
            {
                float Delta = (PreservedNormals[ni] - TangentNormals[ni]).Size();
                if (Delta > MaxNormalDelta)
                    MaxNormalDelta = Delta;
            }
        }

        for (int32 ti = 0; ti < FinalTangents.Num() && ti < PreservedNormals.Num(); ti++)
        {
            FVector T = FinalTangents[ti].TangentX;
            bool bOrigFlip = FinalTangents[ti].bFlipTangentY;
            const FVector& N = PreservedNormals[ti];
            const FVector& NOrig = TangentNormals[ti];

            // Gram-Schmidt: project out normal component
            FVector OrthoT = T - FVector::DotProduct(T, N) * N;
            if (OrthoT.IsNearlyZero())
            {
                // Degenerate UV tangent — fallback to arbitrary orthogonal vector
                OrthoT = FVector::CrossProduct(N, FVector(1.0f, 0.0f, 0.0f));
                if (OrthoT.IsNearlyZero())
                    OrthoT = FVector::CrossProduct(N, FVector(0.0f, 1.0f, 0.0f));
                DegenerateTangentCount++;
            }
            OrthoT.Normalize();

            // Preserve original bitangent direction: compute sign so that
            // cross(OrthoT, N) * sign aligns with original cross(T, NOrig) * sign.
            FVector OrigBT = FVector::CrossProduct(T, NOrig).GetSafeNormal();
            if (bOrigFlip) OrigBT = -OrigBT;
            FVector NewBT = FVector::CrossProduct(OrthoT, N).GetSafeNormal();
            bool bFlip = (FVector::DotProduct(OrigBT, NewBT) < 0.0f);

            FinalTangents[ti] = FProcMeshTangent(OrthoT, bFlip);
        }

        // Count bad orthogonal tangents: |dot(N,T)| > 0.1 means tangent is not
        // properly orthogonalized to the preserved normal.
        int32 BadOrthogonalCount = 0;
        {
            for (int32 ti = 0; ti < FinalTangents.Num() && ti < PreservedNormals.Num(); ti++)
            {
                float Dot = FVector::DotProduct(FinalTangents[ti].TangentX, PreservedNormals[ti]);
                if (FMath::Abs(Dot) > 0.1f)
                    BadOrthogonalCount++;
            }
        }

        // Unconditional tangent diagnostic (Log level)
        UE_LOG(LogLiveSync, Log,
            TEXT("[MESH][V1][TANGENT] GUID=%s vhash=%s: "
                 "tangents=%d normalPreservedDeltaMax=%.6f degenTangent=%d badOrthogonal=%d"),
            *Guid.ToString(EGuidFormats::Digits),
            *Key.VersionHash,
            FinalTangents.Num(), MaxNormalDelta, DegenerateTangentCount, BadOrthogonalCount);

        // Log first 3 preserved normals and tangents (Log level)
        {
            int32 Samples = FMath::Min(3, PreservedNormals.Num());
            for (int32 si = 0; si < Samples; si++)
            {
                FString TangentStr = (si < FinalTangents.Num())
                    ? FString::Printf(TEXT("(%.6f, %.6f, %.6f)"),
                        FinalTangents[si].TangentX.X,
                        FinalTangents[si].TangentX.Y,
                        FinalTangents[si].TangentX.Z)
                    : TEXT("(N/A)");
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MESH][V1][TANGENT] Sample %d: normal=(%.6f, %.6f, %.6f) tangent=%s"),
                    si,
                    PreservedNormals[si].X,
                    PreservedNormals[si].Y,
                    PreservedNormals[si].Z,
                    *TangentStr);
            }
        }

        // === T7/T2: Tangent count == vertices count ===
        // If Tangents.Num() != Vertices.Num(), generate fallback tangent for every vertex.
        if (FinalTangents.Num() != Positions.Num())
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[MESH][V1][TANGENT] FinalTangents.Num() (%d) != Vertices.Num() (%d) "
                     "— generating fallback tangents for GUID=%s vhash=%s"),
                FinalTangents.Num(), Positions.Num(),
                *Guid.ToString(EGuidFormats::Digits),
                *Key.VersionHash);
            // Generate fallback tangents for all vertices
            TArray<FProcMeshTangent> FallbackTangents;
            FallbackTangents.SetNum(Positions.Num());
            for (int32 vi = 0; vi < Positions.Num(); vi++)
            {
                const FVector& N = PreservedNormals[vi];
                FVector Fallback = FVector::CrossProduct(N, FVector(1.0f, 0.0f, 0.0f));
                if (Fallback.IsNearlyZero())
                    Fallback = FVector::CrossProduct(N, FVector(0.0f, 1.0f, 0.0f));
                Fallback.Normalize();
                FallbackTangents[vi] = FProcMeshTangent(Fallback, false);
            }
            FinalTangents = MoveTemp(FallbackTangents);
            DegenerateTangentCount = Positions.Num();
        }

        // === T9: UV0 count == vertices count ===
        // If UV0.Num() != Vertices.Num(), fill zero UVs and log.
        if (UV0.Num() != Positions.Num())
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[MESH][V1][SECTION_ARRAYS] UV0.Num() (%d) != Vertices.Num() (%d) "
                     "— filling zero UVs for GUID=%s vhash=%s"),
                UV0.Num(), Positions.Num(),
                *Guid.ToString(EGuidFormats::Digits),
                *Key.VersionHash);
            TArray<FVector2D> ZeroUVs;
            ZeroUVs.SetNum(Positions.Num());
            for (int32 ui = 0; ui < ZeroUVs.Num(); ui++)
                ZeroUVs[ui] = FVector2D(0.0f, 0.0f);
            UV0 = MoveTemp(ZeroUVs);
        }

        // === T9: Unconditional section arrays diagnostic ===
        {
            int32 VertCount = Positions.Num();
            int32 IndexCount = ValidIndices.Num();
            int32 NormalCount = PreservedNormals.Num();
            int32 UV0Count = UV0.Num();
            int32 TangentCount = FinalTangents.Num();
            int32 ComputedTangentsCount = TangentCount;
            int32 PassedTangentsCount = ComputedTangentsCount;
            {
                int32 CV = CVarLiveSyncV1DebugDisableTangents.GetValueOnAnyThread();
                if (CV != 0)
                    PassedTangentsCount = 0;
            }
            {
                int32 NCV = CVarLiveSyncV1DisableTangents.GetValueOnAnyThread();
                if (NCV != 0)
                    PassedTangentsCount = 0;
            }
            int32 ColorCount = SectionColors.Num();
            int32 FiniteNormals = 0;
            int32 FiniteTangents = 0;
            for (int32 vi = 0; vi < NormalCount; vi++)
            {
                if (IsFiniteVec3(PreservedNormals[vi]))
                    FiniteNormals++;
            }
            for (int32 ti = 0; ti < TangentCount; ti++)
            {
                if (IsFiniteVec3(FinalTangents[ti].TangentX))
                    FiniteTangents++;
            }
            UE_LOG(LogLiveSync, Log,
                TEXT("[MESH][V1][SECTION_ARRAYS] GUID=%s vhash=%s: "
                     "verts=%d indices=%d normals=%d uv0=%d "
                     "computedTangents=%d passedTangents=%d colors=%d "
                     "finiteNormals=%d finiteTangents=%d badTangents=%d"),
                *Guid.ToString(EGuidFormats::Digits),
                *Key.VersionHash,
                VertCount, IndexCount, NormalCount, UV0Count,
                ComputedTangentsCount, PassedTangentsCount,
                ColorCount, FiniteNormals, FiniteTangents, BadOrthogonalCount);

            // Log first 3 samples of each array
            int32 Samples = FMath::Min(3, VertCount);
            for (int32 si = 0; si < Samples; si++)
            {
                FString VertStr = FString::Printf(TEXT("(%.4f, %.4f, %.4f)"),
                    Positions[si].X, Positions[si].Y, Positions[si].Z);
                FString NormStr = FString::Printf(TEXT("(%.6f, %.6f, %.6f)"),
                    PreservedNormals[si].X, PreservedNormals[si].Y, PreservedNormals[si].Z);
                FString TangStr = (si < FinalTangents.Num())
                    ? FString::Printf(TEXT("(%.6f, %.6f, %.6f)"),
                        FinalTangents[si].TangentX.X,
                        FinalTangents[si].TangentX.Y,
                        FinalTangents[si].TangentX.Z)
                    : TEXT("(N/A)");
                FString UVStr = (si < UV0.Num())
                    ? FString::Printf(TEXT("(%.4f, %.4f)"), UV0[si].X, UV0[si].Y)
                    : TEXT("(N/A)");
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MESH][V1][SECTION_ARRAYS] Sample %d: vert=%s normal=%s tangent=%s uv=%s"),
                    si, *VertStr, *NormStr, *TangStr, *UVStr);
            }
        }

        // === STAGE 2C.11: V1 DEBUG — DISABLE TANGENTS ===
        // If CVar UE.LiveSync.V1DebugDisableTangents == 1, pass an empty
        // tangent array to CreateMeshSection for diagnostic isolation.
        // Computed tangents are always generated — only the passed array varies.
        // === STAGE 2C.12: V1 NON-DEBUG DISABLE TANGENTS ===
        // If CVar UE.LiveSync.V1DisableTangents == 1, same effect as the debug CVar
        // but intended for production use when generated tangents cause shading artifacts.
        // Debug CVar takes priority when both are set.
        TArray<FProcMeshTangent> DebugTangents;
        {
            int32 DebugCV = CVarLiveSyncV1DebugDisableTangents.GetValueOnAnyThread();
            int32 NonDebugCV = CVarLiveSyncV1DisableTangents.GetValueOnAnyThread();
            if (DebugCV != 0)
            {
                DebugTangents.Empty();
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MESH][V1][DEBUG_TANGENTS] disabled (debug) computed=%d passed=%d"),
                    FinalTangents.Num(), DebugTangents.Num());
            }
            else if (NonDebugCV != 0)
            {
                DebugTangents.Empty();
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MESH][V1][DEBUG_TANGENTS] disabled (non-debug) computed=%d passed=%d"),
                    FinalTangents.Num(), DebugTangents.Num());
            }
            else
            {
                DebugTangents = FinalTangents;
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MESH][V1][DEBUG_TANGENTS] enabled computed=%d passed=%d"),
                    FinalTangents.Num(), DebugTangents.Num());
            }
        }

        // === T12: Collision disabled for v1 ===
        // Stage 2C.5: collision disabled to avoid Chaos NaN checks.
        // Stage 10A.2B: reuse existing component — use UpdateMeshSection
        // when vertex count matches to avoid MarkRenderStateDirty and
        // the deferred scene-proxy rebuild stall in SendAllEndOfFrameUpdatesInternal.
        {
            const FProcMeshSection* ExistingSection = ProcMesh->GetProcMeshSection(0);
            const int32 NewVerts = Positions.Num();
            if (ExistingSection && ExistingSection->ProcVertexBuffer.Num() == NewVerts)
            {
                ProcMesh->UpdateMeshSection(0, Positions, PreservedNormals,
                    UV0, TArray<FVector2D>(), TArray<FVector2D>(), TArray<FVector2D>(),
                    SectionColors, DebugTangents);
                ProcMesh->UpdateBounds();
            }
            else
            {
                ProcMesh->CreateMeshSection(
                    0,
                    Positions,
                    ValidIndices,
                    PreservedNormals,
                    UV0,
                    SectionColors,
                    DebugTangents,
                    false);
            }
        }

        // === STAGE 2C.10: V1 DEBUG — ASSIGN DEBUG MATERIAL ===
        // If CVar UE.LiveSync.V1DebugMaterialMode != 0, create or
        // select a transient debug material and assign it to section 0.
        // Diagnostic only — does NOT change packet format, does NOT
        // modify the legacy V5 path, and does NOT persist to assets.
        {
            int32 DebugMode = CVarLiveSyncV1DebugMaterialMode.GetValueOnAnyThread();
            FString MatName = TEXT("None");
            int32 Assigned = 0;

            if (DebugMode != 0)
            {
                // Create a transient debug material
                static UMaterial* DebugMatUnlit = nullptr;
                static UMaterial* DebugMatTwoSided = nullptr;
                static UMaterial* DebugMatTwoSidedUnlit = nullptr;
                static bool bDebugMaterialsCreated = false;

                if (!bDebugMaterialsCreated)
                {
                    // Create transient unlit gray material
                    {
                        DebugMatUnlit = NewObject<UMaterial>(
                            GetTransientPackage(),
                            NAME_None,
                            EObjectFlags::RF_Transient | EObjectFlags::RF_Public);
                        DebugMatUnlit->SetShadingModel(EMaterialShadingModel::MSM_Unlit);
                        DebugMatUnlit->MaterialDomain = MD_Surface;
                        DebugMatUnlit->PostEditChange();
                    }

                    // Create transient two-sided gray material (default-lit, two-sided)
                    {
                        DebugMatTwoSided = NewObject<UMaterial>(
                            GetTransientPackage(),
                            NAME_None,
                            EObjectFlags::RF_Transient | EObjectFlags::RF_Public);
                        DebugMatTwoSided->SetShadingModel(EMaterialShadingModel::MSM_DefaultLit);
                        DebugMatTwoSided->TwoSided = 1;
                        DebugMatTwoSided->PostEditChange();
                    }

                    // Create transient two-sided unlit gray material
                    {
                        DebugMatTwoSidedUnlit = NewObject<UMaterial>(
                            GetTransientPackage(),
                            NAME_None,
                            EObjectFlags::RF_Transient | EObjectFlags::RF_Public);
                        DebugMatTwoSidedUnlit->SetShadingModel(EMaterialShadingModel::MSM_Unlit);
                        DebugMatTwoSidedUnlit->TwoSided = 1;
                        DebugMatTwoSidedUnlit->PostEditChange();
                    }

                    bDebugMaterialsCreated = true;
                }

                UMaterialInterface* TargetMat = nullptr;

                switch (DebugMode)
                {
                case 1:
                    TargetMat = DebugMatUnlit;
                    MatName = TEXT("UnlitGray");
                    break;
                case 2:
                    TargetMat = DebugMatTwoSided;
                    MatName = TEXT("TwoSidedGray");
                    break;
                case 3:
                    TargetMat = DebugMatTwoSidedUnlit;
                    MatName = TEXT("TwoSidedUnlitGray");
                    break;
                default:
                    MatName = TEXT("None");
                    break;
                }

                if (TargetMat)
                {
                    ProcMesh->SetMaterial(0, TargetMat);
                    Assigned = 1;
                }
            }

            UE_LOG(LogLiveSync, Log,
                TEXT("[MESH][V1][DEBUG_MATERIAL] mode=%d material=%s assigned=%d"),
                DebugMode, *MatName, Assigned);
        }

        // === Post-CreateMeshSection diagnostics ===
        {
            // Section local bounds
            FBox SectionBounds(ForceInit);
            bool bBoundsComputed = false;
            for (int32 vi = 0; vi < Positions.Num(); vi++)
            {
                if (!bBoundsComputed)
                {
                    SectionBounds.Min = Positions[vi];
                    SectionBounds.Max = Positions[vi];
                    bBoundsComputed = true;
                }
                else
                {
                    SectionBounds += Positions[vi];
                }
            }
            UE_LOG(LogLiveSync, Log,
                TEXT("[MESH][V1][AFTER_BUILD] GUID=%s vhash=%s: "
                     "sectionBounds Min=(%.4f, %.4f, %.4f) Max=(%.4f, %.4f, %.4f) "
                     "numSections=%d"),
                *Guid.ToString(EGuidFormats::Digits),
                *Key.VersionHash,
                SectionBounds.Min.X, SectionBounds.Min.Y, SectionBounds.Min.Z,
                SectionBounds.Max.X, SectionBounds.Max.Y, SectionBounds.Max.Z,
                ProcMesh->GetNumSections());

            // Material slot/name on section 0 (read-only diagnostic)
            if (ProcMesh->GetNumSections() > 0 && ProcMesh->GetMaterial(0))
            {
                UMaterialInterface* Mat = ProcMesh->GetMaterial(0);
                UE_LOG(LogLiveSync, Log,
                    TEXT("[MESH][V1][AFTER_BUILD] GUID=%s vhash=%s: "
                         "section0 material=%s"),
                    *Guid.ToString(EGuidFormats::Digits),
                    *Key.VersionHash,
                    *Mat->GetName());
            }
        }

        Stats.MeshSchemaV1SectionsBuilt.fetch_add(1, std::memory_order_relaxed);

        DiagBuildCount++;

        UE_LOG(LogLiveSync, Log,
            TEXT("[MESH][V1] Built section for GUID=%s vhash=%s: "
                 "%d verts, %d tris, stride=%d, hasColor=%d"),
            *Guid.ToString(EGuidFormats::Digits),
            *Key.VersionHash,
            Positions.Num(),
            ValidIndices.Num() / 3,
            State.VertexStride,
            bHasColor0 ? 1 : 0);

        // Mark completed and remove from pending
        BuildsThisTick++;
        State.bReconstructed = true;
        Reconstructed.Add(Key);
    }

    for (const FV1MeshReassemblyKey& Key : Reconstructed)
    {
        PendingV1MeshReassembly.Remove(Key);
    }
}


// =========================================================
// Phase 10J.5D.5: Deferred FBX visibility repair
// =========================================================
// Scheduled by OnScheduleRepair callback after FBX import.
// Processes entries in tick pipeline with TWeakObjectPtr
// safety guards.
// =========================================================

void UUELiveSyncSubsystem::
ProcessDeferredRepairs()
{
    const double Now = FPlatformTime::Seconds();
    const double ScheduleDelay = 0.1; // ~2 ticks at 60fps

    TArray<FGuid> Completed;

    for (const FDeferredFBXRepairEntry& Entry : DeferredFBXRepairs)
    {
        if (Entry.PassNumber == 1)
        {
            // Next-tick pass: always execute.
        }
        else if (Entry.PassNumber == 2)
        {
            // Delayed pass: wait for ScheduleDelay.
            if (Now - Entry.ScheduleTime < ScheduleDelay)
                continue;
        }

        // Use TWeakObjectPtr for actor/component/mesh safety.
        // Resolving from ActorCache returns weak pointer that survives
        // brief destruction races between scheduling and execution.
        TWeakObjectPtr<AActor> WeakActor = FindActorFast(Entry.Guid);
        AActor* Actor = WeakActor.Get();
        if (!Actor)
        {
            Completed.Add(Entry.Guid);
            continue;
        }

        AStaticMeshActor* SMA = Cast<AStaticMeshActor>(Actor);
        if (!SMA)
        {
            Completed.Add(Entry.Guid);
            continue;
        }

        TWeakObjectPtr<UStaticMeshComponent> WeakSMC(SMA->GetStaticMeshComponent());
        UStaticMeshComponent* SMC = WeakSMC.Get();
        if (!SMC)
        {
            Completed.Add(Entry.Guid);
            continue;
        }

        TWeakObjectPtr<UStaticMesh> WeakMesh(SMC->GetStaticMesh());
        UStaticMesh* Mesh = WeakMesh.Get();
        if (!Mesh)
        {
            Completed.Add(Entry.Guid);
            continue;
        }

        // Full repair (same as EnsureFBXMeshRenderable + RefreshFBXStaticMeshComponent)
        SMC->SetVisibility(true, true);
        SMC->SetHiddenInGame(false, true);
        Actor->SetActorHiddenInGame(false);
        SMC->UpdateBounds();
        SMC->MarkRenderStateDirty();

        // Also run EnsureFBXMeshRenderable for material + visibility safety
        FLiveSyncFBXImporter::EnsureFBXMeshRenderable(SMC, Mesh, SMA, Entry.Guid);

        UE_LOG(LogLiveSync, Log,
            TEXT("[FBX][DEFERRED_REPAIR] guid=%s pass=%d reason=post_import%s"),
            *Entry.Guid.ToString(EGuidFormats::Digits),
            Entry.PassNumber,
            Entry.PassNumber == 2 ? TEXT("_delay") : TEXT(""));

        Completed.Add(Entry.Guid);
    }

    // Remove completed entries
    for (const FGuid& G : Completed)
    {
        DeferredFBXRepairs.RemoveAll([&](const FDeferredFBXRepairEntry& E) {
            return E.Guid == G;
        });
    }
}


void UUELiveSyncSubsystem::
RepairAllFBXActors()
{
    UE_LOG(LogLiveSync, Log,
        TEXT("[FBX][MANUAL_REPAIR] starting repair for %d FBX-authoritative GUIDs"),
        FBXAuthoritativeGuids.Num());

    int32 RepairedCount = 0;
    for (const FGuid& Guid : FBXAuthoritativeGuids)
    {
        TWeakObjectPtr<AActor> WeakActor = FindActorFast(Guid);
        AActor* Actor = WeakActor.Get();
        if (!Actor)
            continue;

        AStaticMeshActor* SMA = Cast<AStaticMeshActor>(Actor);
        if (!SMA)
            continue;

        TWeakObjectPtr<UStaticMeshComponent> WeakSMC(SMA->GetStaticMeshComponent());
        UStaticMeshComponent* SMC = WeakSMC.Get();
        if (!SMC)
            continue;

        TWeakObjectPtr<UStaticMesh> WeakMesh(SMC->GetStaticMesh());
        UStaticMesh* Mesh = WeakMesh.Get();
        if (!Mesh)
            continue;

        FLiveSyncFBXImporter::EnsureFBXMeshRenderable(SMC, Mesh, SMA, Guid);
        ++RepairedCount;

        UE_LOG(LogLiveSync, Log,
            TEXT("[FBX][MANUAL_REPAIR] repaired guid=%s actor=%s"),
            *Guid.ToString(EGuidFormats::Digits),
            *Actor->GetName());
    }

    UE_LOG(LogLiveSync, Log,
        TEXT("[FBX][MANUAL_REPAIR] repair complete: %d actors repaired"), RepairedCount);
}


// =========================================================
// Phase 10J.5D.5: Console command — UE.LiveSync.RepairFBX
// =========================================================

static FAutoConsoleCommandWithWorld GRepairFBXCommand(
    TEXT("UE.LiveSync.RepairFBX"),
    TEXT("Manually repair visibility for all FBX-authoritative actors"),
    FConsoleCommandWithWorldDelegate::CreateLambda([](UWorld* World)
    {
        if (!World)
            return;
        UUELiveSyncSubsystem* Subsystem =
            World->GetSubsystem<UUELiveSyncSubsystem>();
        if (Subsystem)
        {
            Subsystem->RepairAllFBXActors();
        }
    })
);


#include "UELiveSyncSubsystem_Replay.inl"
#include "UELiveSyncSubsystem_Phase6H.inl"
#include "UELiveSyncSubsystem_Phase6I.inl"
#include "UELiveSyncSubsystem_Diagnostics.inl"

