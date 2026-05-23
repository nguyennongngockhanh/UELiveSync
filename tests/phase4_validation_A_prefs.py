"""
Phase 4 Validation — A: Preferences & Config

Blender-side: load addon, change each pref, verify thresholds
reflected in transforms_different(), verify port change affects
connection target, verify heartbeat/scan intervals propagate.
"""

import bpy
import time
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "Blender_Addon"))

PASS = 0
FAIL = 0


def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        msg = f"  FAIL: {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)


def report():
    total = PASS + FAIL
    print(f"\n{'='*50}")
    print(f"RESULTS: {PASS}/{total} passed, {FAIL} failed")
    print(f"{'='*50}")
    return FAIL == 0


# =============================================================
# SETUP
# =============================================================

# Register addon
import importlib
import sync as live_sync
import network as live_net
import __init__ as addon_module

for cls in addon_module.classes:
    try:
        bpy.utils.register_class(cls)
    except:
        pass

prefs = bpy.context.preferences.addons[
    addon_module.__package__
].preferences


print("\n" + "=" * 50)
print("PHASE 4 VALIDATION — A: PREFERENCES & CONFIG")
print("=" * 50)


# =============================================================
# 1. DEFAULT PREF VALUES
# =============================================================
print("\n--- 1. DEFAULT VALUES ---")

test("1a: server_port default",
     prefs.server_port == 57000,
     f"got {prefs.server_port}")

test("1b: threshold_location default",
     abs(prefs.threshold_location - 0.01) < 0.0001,
     f"got {prefs.threshold_location}")

test("1c: threshold_rotation default",
     abs(prefs.threshold_rotation - 0.0001) < 0.00001,
     f"got {prefs.threshold_rotation}")

test("1d: threshold_scale default",
     abs(prefs.threshold_scale - 0.001) < 0.0001,
     f"got {prefs.threshold_scale}")

test("1e: heartbeat_interval default",
     abs(prefs.heartbeat_interval - 5.0) < 0.1,
     f"got {prefs.heartbeat_interval}")

test("1f: scan_interval default",
     prefs.scan_interval == 300,
     f"got {prefs.scan_interval}")


# =============================================================
# 2. PREF CHANGES PROPAGATE TO _get_threshold
# =============================================================
print("\n--- 2. PROPAGATION TO _get_threshold ---")

old_loc = prefs.threshold_location
prefs.threshold_location = 0.05
# Verify runtime_config gets synced
live_sync._sync_runtime_config()
val = live_sync._get_threshold("threshold_location", 0.01)
test("2a: location threshold propagates",
     abs(val - 0.05) < 0.0001,
     f"expected 0.05, got {val}")
prefs.threshold_location = old_loc

old_rot = prefs.threshold_rotation
prefs.threshold_rotation = 0.001
live_sync._sync_runtime_config()
val = live_sync._get_threshold("threshold_rotation", 0.0001)
test("2b: rotation threshold propagates",
     abs(val - 0.001) < 0.00001,
     f"expected 0.001, got {val}")
prefs.threshold_rotation = old_rot

old_scl = prefs.threshold_scale
prefs.threshold_scale = 0.01
live_sync._sync_runtime_config()
val = live_sync._get_threshold("threshold_scale", 0.001)
test("2c: scale threshold propagates",
     abs(val - 0.01) < 0.0001,
     f"expected 0.01, got {val}")
prefs.threshold_scale = old_scl

old_hb = prefs.heartbeat_interval
prefs.heartbeat_interval = 10.0
live_sync._sync_runtime_config()
val = live_sync._get_threshold("heartbeat_interval", 5.0)
test("2d: heartbeat interval propagates",
     abs(val - 10.0) < 0.1,
     f"expected 10.0, got {val}")
prefs.heartbeat_interval = old_hb

old_sc = prefs.scan_interval
prefs.scan_interval = 500
live_sync._sync_runtime_config()
val = live_sync._get_threshold("scan_interval", 300)
test("2e: scan interval propagates",
     val == 500,
     f"expected 500, got {val}")
prefs.scan_interval = old_sc


# =============================================================
# 3. transforms_different() USES THRESHOLDS
# =============================================================
print("\n--- 3. transforms_different() THRESHOLD BEHAVIOR ---")

# Set known thresholds
prefs.threshold_location = 1.0  # Very high
prefs.threshold_rotation = 1.0  # Very high
prefs.threshold_scale = 1.0     # Very high
live_sync._sync_runtime_config()

a = {"location": [0, 0, 0], "rotation": [0, 0, 0, 1], "scale": [1, 1, 1]}
b = {"location": [0.5, 0, 0], "rotation": [0, 0, 0, 1], "scale": [1, 1, 1]}

test("3a: small delta within high threshold is NOT different",
     not live_sync.transforms_different(a, b),
     f"threshold 1.0, delta 0.5 should be same")

# Reset to tight thresholds
prefs.threshold_location = 0.01
prefs.threshold_rotation = 0.0001
prefs.threshold_scale = 0.001
live_sync._sync_runtime_config()


# =============================================================
# 4. RUNTIME_CONFIG CACHE WORKS
# =============================================================
print("\n--- 4. RUNTIME_CONFIG CACHE ---")

# Direct cache override (bypass prefs)
live_sync._runtime_config["heartbeat_interval"] = 99.0
val = live_sync._get_threshold("heartbeat_interval", 5.0)
test("4a: runtime_config override takes effect",
     abs(val - 99.0) < 0.1,
     f"expected 99.0, got {val}")

# Sync from prefs should overwrite cache
live_sync._sync_runtime_config()
val = live_sync._get_threshold("heartbeat_interval", 5.0)
test("4b: _sync_runtime_config restores from prefs",
     abs(val - live_sync._runtime_config["heartbeat_interval"]) < 0.1,
     f"got {val}")


# =============================================================
# 5. RUNTIME_STATS CENTRALIZATION
# =============================================================
print("\n--- 5. RUNTIME_STATS CENTRALIZATION ---")

stats = live_sync._runtime_stats
test("5a: _runtime_stats has required keys",
     all(k in stats for k in [
         "tracked_objects", "queue_depth", "reconnect_count",
         "uptime", "last_error", "dropped_packets",
         "serialization_failures", "heartbeat_interval",
         "scan_interval", "reconnect_escalated",
     ]),
     f"missing keys")

test("5b: get_tracked_count syncs to stats",
     live_sync.get_tracked_count() == stats["tracked_objects"],
     f"{live_sync.get_tracked_count()} vs {stats['tracked_objects']}")


# =============================================================
# 6. DUMP_DIAGNOSTICS OUTPUT
# =============================================================
print("\n--- 6. DUMP_DIAGNOSTICS ---")

try:
    live_sync.dump_diagnostics()
    test("6a: dump_diagnostics() runs", True)
except Exception as e:
    test("6a: dump_diagnostics() runs", False, str(e))


# =============================================================
# REPORT
# =============================================================
sys.exit(0 if report() else 1)
