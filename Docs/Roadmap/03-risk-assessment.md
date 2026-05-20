# Risk Assessment

## Risk Matrix

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| GUID desync after protocol change | Medium | High | Keep V2 parser for fallback; test with known GUID values |
| Blender background thread race | Medium | High | threading.Lock around connect/close; single-threaded sender |
| Socket leak on thread exit | Low | Medium | Socket timeout on Recv; shutdown(SHUT_RDWR) before closing |
| ActorCache miss during rebuild | High | Medium | Incremental approach eliminates rebuild window entirely |
| Interpolation change causes visual regression | Medium | Medium | A/B test; make interpolation mode configurable |
| Level transition crash | Low | High | Clean up World callbacks on Deinitialize |
| Memory leak from TransformStates | High | Medium | TTL eviction: remove states not updated in 60s |
| Network buffer overflow on Blender | Low | Medium | Bounded queue with drop; log warning when dropping |

## Mitigation Plan

### GUID Desync
- Before protocol change: log both hex and binary GUID for verification
- Keep V2 parser operational during migration
- Unit test: serialize → deserialize → compare to original

### Thread Safety
- Blender: single background thread, queue-based, lock-protected connect/close
- UE: Continue current pattern (thread stops before socket destroy)
- Add socket recv timeout (5s) to prevent thread hang

### Rollback Plan
- Each phase is git-committed separately
- Protocol changes are opt-in (version field)
- Non-protocol changes (logging, queue size) can be reverted individually
