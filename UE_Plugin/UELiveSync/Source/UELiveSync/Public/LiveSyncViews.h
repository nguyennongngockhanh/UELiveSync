#pragma once

// =========================================================
// LiveSyncViews.h — Protocol View structs (data contracts)
// =========================================================
// Immutable data transfer objects for all message types.
// No UE dependencies. No serializer dependencies.
// Only primitive types: uint8_t, uint16_t, uint32_t, uint64_t,
// float, std::string, std::vector, std::array<uint8_t, 16>.
//
// These structs are the ONLY interface between protocol layer
// (bridge) and gameplay layer (UE). Gameplay includes only
// this header — never the bridge header.
// =========================================================

#include <cstdint>
#include <string>
#include <vector>
#include <array>

namespace LiveSyncBridge
{

// =========================================================
// Camera
// =========================================================

struct CameraCreateView
{
    std::array<uint8_t, 16> CameraId;
    std::string Name;
    bool HasParentId;
    std::array<uint8_t, 16> ParentId;
    struct { float X, Y, Z, Rx, Ry, Rz, Rw, Sx, Sy, Sz; } Transform;
    float FocalLength;
    float SensorWidth;
    float SensorHeight;
    float ClipStart;
    float ClipEnd;
    float OrthoScale;
    uint8_t CameraFlags;
    uint32_t SequenceNumber;
    double Timestamp;
};

struct CameraUpdateView
{
    std::array<uint8_t, 16> CameraId;
    bool HasTransform;
    struct { float X, Y, Z, Rx, Ry, Rz, Rw, Sx, Sy, Sz; } Transform;
    bool HasFocalLength;
    float FocalLength;
    bool HasSensorWidth;
    float SensorWidth;
    bool HasSensorHeight;
    float SensorHeight;
    bool HasClipStart;
    float ClipStart;
    bool HasClipEnd;
    float ClipEnd;
    bool HasOrthoScale;
    float OrthoScale;
    bool HasCameraFlags;
    uint8_t CameraFlags;
    uint32_t SequenceNumber;
    double Timestamp;
};

struct CameraSetActiveView
{
    std::array<uint8_t, 16> CameraId;
};

// =========================================================
// FBX Import (MIG-005: semantic FBX_IMPORT_REQUEST)
// =========================================================
// Represents the semantic FBX_IMPORT_REQUEST (0x60) message.
// fbx_path/object_name are variable-length utf8 strings
// instead of fixed-size arrays; payload timestamp is informational
// (UE resolves freshness from the FBX file mtime).

struct FbxImportRequestView
{
    std::array<uint8_t, 16> PersistentId;
    uint32_t Version;
    std::string FbxPath;
    std::string ObjectName;
    uint32_t VertCount;
    uint32_t TriCount;
    uint32_t MatSlotCount;
    uint64_t GeometryHash;
    uint32_t SequenceNumber;
    double Timestamp;
};

// =========================================================
// Object
// =========================================================

struct ObjectCreateView
{
    std::array<uint8_t, 16> PersistentId;
    std::string Name;
    bool HasParentId;
    std::array<uint8_t, 16> ParentId;
    uint8_t PrimitiveType;
    std::vector<float> Transform;
    uint32_t SequenceNumber;
    double Timestamp;
};

struct ObjectUpdateView
{
    std::array<uint8_t, 16> PersistentId;
    bool HasTransform;
    std::vector<float> Transform;
    bool HasName;
    std::string Name;
    bool HasVisibility;
    uint8_t Visibility;
    uint32_t SequenceNumber;
    double Timestamp;
};

struct ObjectDeleteView
{
    std::array<uint8_t, 16> PersistentId;
    uint32 SequenceNumber;
    double Timestamp;
};

struct ObjectRenameView
{
    std::array<uint8_t, 16> PersistentId;
    std::string NewName;
};

struct ObjectVisibilityView
{
    std::array<uint8_t, 16> PersistentId;
    uint8_t Visible;
};

struct ObjectReparentView
{
    std::array<uint8_t, 16> PersistentId;
    std::array<uint8_t, 16> NewParentId;
};

// =========================================================
// Handshake
// =========================================================

struct HelloView
{
    uint8_t ProtocolVersionMajor;
    uint8_t ProtocolVersionMinor;
    uint64_t Capabilities;
};

struct HelloAckView
{
    uint8_t ProtocolVersionMajor;
    uint8_t ProtocolVersionMinor;
    uint64_t AcceptedCapabilities;
    uint32_t MaxChunkSize;
    uint64_t SessionId;
};

struct HeartbeatView
{
    uint32_t SequenceId;
    bool HasSessionId;
    uint64_t SessionId;
};

struct HeartbeatAckView
{
    uint32_t SequenceId;
    bool HasSessionId;
    uint64_t SessionId;
};

// =========================================================
// Material
// =========================================================

struct MaterialCreateView
{
    std::array<uint8_t, 16> MaterialId;
    std::string Name;
    std::vector<float> BaseColor;
    float Metallic;
    float Roughness;
    std::vector<float> Emission;
    bool HasTexturePath;
    std::string TexturePath;
    uint32_t SequenceNumber;
    double Timestamp;
};

struct MaterialUpdateView
{
    std::array<uint8_t, 16> MaterialId;
    std::vector<float> BaseColor;
    float Metallic;
    float Roughness;
    std::vector<float> Emission;
    bool HasTexturePath;
    std::string TexturePath;
    uint32_t SequenceNumber;
    double Timestamp;
};

struct MaterialAssignView
{
    std::array<uint8_t, 16> PersistentId;
    std::array<uint8_t, 16> MaterialId;
    uint8_t SlotIndex;
    uint32_t SequenceNumber;
    double Timestamp;
};

// =========================================================
// Mesh
// =========================================================

struct MeshStartView
{
    std::array<uint8_t, 16> PersistentId;
    uint16_t TotalChunks;
    uint8_t FormatFlags;
};

struct MeshChunkView
{
    std::array<uint8_t, 16> PersistentId;
    uint16_t ChunkIndex;
    uint16_t VertexOffset;
    uint32_t VertexCount;
    uint32_t IndexCount;
    std::vector<uint8_t> Data;
};

struct MeshEndView
{
    std::array<uint8_t, 16> PersistentId;
    uint32_t Checksum;
};

struct MeshDataView
{
    std::array<uint8_t, 16> PersistentId;
    uint32_t VertexCount;
    uint32_t IndexCount;
    uint8_t FormatFlags;
    std::vector<float> Vertices;
    std::vector<float> Normals;
    std::vector<float> Uvs;
    std::vector<uint32_t> Indices;
};

struct MeshDeltaView
{
    std::array<uint8_t, 16> PersistentId;
    uint32_t VertexCount;
    uint8_t FormatFlags;
    std::vector<float> Vertices;
    std::vector<float> Normals;
    std::vector<float> Uvs;
};

} // namespace LiveSyncBridge
