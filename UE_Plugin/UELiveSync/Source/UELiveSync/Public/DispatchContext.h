#pragma once

// =========================================================
// DispatchContext.h — External consumers for Dispatch
// =========================================================
// Passed by value (const ref) to every Dispatch call.
// Bridge owns nothing. Holds no state. Pure function pipeline.
// =========================================================

struct IGameplaySink;

struct DispatchContext
{
    IGameplaySink* Gameplay = nullptr;
};
