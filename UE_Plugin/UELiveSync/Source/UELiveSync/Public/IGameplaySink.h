#pragma once

// =========================================================
// IGameplaySink.h — Gameplay interface for protocol messages
// =================================================//
// Virtual interface that gameplay layer implements to receive
// decoded protocol messages as View objects.
//
// Only includes LiveSyncViews.h — never the bridge header.
// Bridge never includes this header.
// =========================================================

#include "LiveSyncViews.h"

struct IGameplaySink
{
    virtual ~IGameplaySink() = default;

    virtual void OnObjectCreate(const LiveSyncBridge::ObjectCreateView&) {}
    virtual void OnObjectUpdate(const LiveSyncBridge::ObjectUpdateView&) {}
    virtual void OnObjectDelete(const LiveSyncBridge::ObjectDeleteView&) {}
    virtual void OnObjectRename(const LiveSyncBridge::ObjectRenameView&) {}
    virtual void OnObjectVisibility(const LiveSyncBridge::ObjectVisibilityView&) {}
    virtual void OnObjectReparent(const LiveSyncBridge::ObjectReparentView&) {}

    virtual void OnMaterialCreate(const LiveSyncBridge::MaterialCreateView&) {}
    virtual void OnMaterialUpdate(const LiveSyncBridge::MaterialUpdateView&) {}
    virtual void OnMaterialAssign(const LiveSyncBridge::MaterialAssignView&) {}

    virtual void OnMeshStart(const LiveSyncBridge::MeshStartView&) {}
    virtual void OnMeshChunk(const LiveSyncBridge::MeshChunkView&) {}
    virtual void OnMeshEnd(const LiveSyncBridge::MeshEndView&) {}
    virtual void OnMeshData(const LiveSyncBridge::MeshDataView&) {}
    virtual void OnMeshDelta(const LiveSyncBridge::MeshDeltaView&) {}

    virtual void OnCameraCreate(const LiveSyncBridge::CameraCreateView&) = 0;
    virtual void OnCameraUpdate(const LiveSyncBridge::CameraUpdateView&) = 0;
    virtual void OnCameraSetActive(const LiveSyncBridge::CameraSetActiveView&) = 0;
};
