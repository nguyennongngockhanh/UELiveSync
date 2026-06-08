#!/usr/bin/env python3
"""
Phase 7E Stage 10A.2 — Visibility BoolTrack Apply Tests.

UE-side simulation of visibility keyframe apply for channels 9-10.

T1  hide_viewport channel 9 key applied
T2  hide_render channel 10 key applied
T3  mixed transform + visibility packet preserves channels 0-8
T4  missing binding safe and counted
T5  unsupported channel >10 safe and counted
T6  stale sequence rejected before apply
T7  counters increment correctly
T8  track created only when missing
T9  section created only when missing
T10 no packet format change (validated by code review)
T11 no new packet type (validated by code review)
T12 transform tests still pass (run stage 9 test separately)
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'Blender_Addon'))

PASS = 0
FAIL = 0
RESULTS = []

def banner(title):
    width = 60
    print()
    print("=" * width)
    print(f"  {title}")
    print("=" * width)

def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        msg = f"  FAIL: {name}"
        if detail:
            msg += f" \u2014 {detail}"
        print(msg)
    RESULTS.append((name, condition))

def section(title):
    print()
    print(f"--- {title} ---")


# =========================================================
# Mocks
# =========================================================

class MockBoolTrack:
    """Simulates a UMovieSceneBoolTrack with sections."""

    def __init__(self, binding_guid):
        self.binding_guid = binding_guid
        self.sections = []

    @property
    def has_section(self):
        return len(self.sections) > 0

    def add_section(self):
        section = MockBoolSection()
        self.sections.append(section)
        return section


class MockBoolSection:
    """Simulates a UMovieSceneBoolSection with one bool channel."""

    def __init__(self):
        self.keys = []  # list of (frame, bool_value)

    def add_key(self, frame, value):
        self.keys.append((frame, value))


class MockTransformTrack:
    """Same as stage 9 — simulates 3D transform track."""

    def __init__(self, binding_guid):
        self.binding_guid = binding_guid
        self.sections = []

    @property
    def has_section(self):
        return len(self.sections) > 0

    def add_section(self):
        section = MockTransformSection()
        self.sections.append(section)
        return section


class MockTransformSection:
    """Same as stage 9 — 9 double channels."""

    def __init__(self):
        self.channels = [[] for _ in range(9)]

    def add_linear_key(self, channel_index, frame, value):
        if 0 <= channel_index < 9:
            self.channels[channel_index].append((frame, value))


class MockVisibilityKeyframeState:
    """Simulates HandleKeyframe with visibility ch 9-10 support."""

    def __init__(self):
        self.bHasSequence = False
        self.binding_map = {}           # guid_hex → binding_str
        self.transform_tracks = {}      # binding_str → MockTransformTrack
        self.bool_tracks = {}           # binding_str → MockBoolTrack
        self.bHasKeyframeState = False
        self.LastKeyframeSequence = 0

        self.counters = {
            'KFPacketsApplied': 0,
            'KFKeysApplied': 0,
            'KFMissingBinding': 0,
            'KFUnsupportedChannel': 0,
            'KFTrackCreated': 0,
            'KFSectionCreated': 0,
            'KFVisibilityKeysApplied': 0,
            'KFVisibilityTrackCreated': 0,
            'KFVisibilitySectionCreated': 0,
            'KFVisibilityUnsupported': 0,
        }

        self.logs = []  # captures log messages for test verification

    def create_sequence(self):
        self.bHasSequence = True

    def clear_sequence(self):
        self.bHasSequence = False
        self.binding_map.clear()
        self.transform_tracks.clear()
        self.bool_tracks.clear()

    def add_possessable(self, guid_hex):
        if not self.bHasSequence:
            return
        binding_guid = f"BIND_{guid_hex[-8:]}"
        self.binding_map[guid_hex] = binding_guid

    def handle_keyframe(self, header, entries):
        """Simulates C++ HandleKeyframe with visibility ch 9-10 dispatch."""
        self.bHasKeyframeState = True
        self.LastKeyframeSequence = header['sequence']

        if not self.bHasSequence:
            self.logs.append("[KEYFRAME] No active sequence")
            return

        applied = 0
        missing = 0
        unsupp = 0
        tracks_created = 0
        sections_created = 0
        vis_applied = 0
        vis_track_created = 0
        vis_section_created = 0
        vis_unsupp = 0

        for entry in entries:
            guid_hex = entry['guid_hex']
            channel = entry['channel']
            frame = entry['frame']
            value = entry['value']

            # Step 1: Resolve binding
            if guid_hex not in self.binding_map:
                if channel in (9, 10):
                    missing += 1
                    self.logs.append(
                        f"[KEYFRAME][VISIBILITY] missing binding channel={channel} "
                        f"guid={guid_hex}")
                else:
                    missing += 1
                    self.logs.append(
                        f"[KEYFRAME] No binding for {guid_hex} ch={channel}")
                continue

            binding_guid = self.binding_map[guid_hex]

            # Step 2: Channel dispatch
            if channel == 9 or channel == 10:
                # Stale sequence check
                if not self.bHasSequence:
                    self.logs.append(
                        f"[KEYFRAME][VISIBILITY] stale sequence rejected "
                        f"for {guid_hex} (ch {channel})")
                    continue

                # Find or create bool track
                if binding_guid not in self.bool_tracks:
                    self.bool_tracks[binding_guid] = MockBoolTrack(binding_guid)
                    vis_track_created += 1

                bool_track = self.bool_tracks[binding_guid]

                # Find or create bool section
                if not bool_track.has_section:
                    bool_track.add_section()
                    vis_section_created += 1

                bool_section = bool_track.sections[0]

                # Add key
                bool_value = value != 0.0
                bool_section.add_key(frame, bool_value)
                vis_applied += 1
                self.logs.append(
                    f"[KEYFRAME][VISIBILITY] applied channel={channel} "
                    f"guid={guid_hex} value={1 if bool_value else 0} frame={frame}")
                continue

            if channel > 10:
                vis_unsupp += 1
                unsupp += 1
                self.logs.append(
                    f"[KEYFRAME][VISIBILITY] unsupported channel={channel} "
                    f"guid={guid_hex}")
                continue

            # Transform channels 0-8
            if binding_guid not in self.transform_tracks:
                self.transform_tracks[binding_guid] = MockTransformTrack(binding_guid)
                tracks_created += 1

            track = self.transform_tracks[binding_guid]

            if not track.has_section:
                track.add_section()
                sections_created += 1

            section = track.sections[0]
            section.add_linear_key(channel, frame, value)
            applied += 1

        # Update counters
        self.counters['KFPacketsApplied'] += 1
        self.counters['KFKeysApplied'] += applied
        self.counters['KFMissingBinding'] += missing
        self.counters['KFUnsupportedChannel'] += unsupp
        self.counters['KFTrackCreated'] += tracks_created
        self.counters['KFSectionCreated'] += sections_created
        self.counters['KFVisibilityKeysApplied'] += vis_applied
        self.counters['KFVisibilityTrackCreated'] += vis_track_created
        self.counters['KFVisibilitySectionCreated'] += vis_section_created
        self.counters['KFVisibilityUnsupported'] += vis_unsupp


def make_entry(guid_hex, frame, value, channel):
    return {'guid_hex': guid_hex, 'frame': frame, 'value': value, 'channel': channel}


def make_header(sequence, timestamp, key_count):
    return {'sequence': sequence, 'timestamp': timestamp, 'key_count': key_count}


# =========================================================
# Helpers
# =========================================================

def make_guid(val):
    """Create a deterministic 32-char hex GUID from an int."""
    h = f"{val:032x}"
    return h


# =========================================================
# T1: hide_viewport channel 9 key applied
# =========================================================

banner("T1 — hide_viewport channel 9 key applied")

state = MockVisibilityKeyframeState()
state.create_sequence()

guid = make_guid(1)
state.add_possessable(guid)

entries = [make_entry(guid, 10, 1.0, 9)]
state.handle_keyframe(make_header(1, time.time(), len(entries)), entries)

binding = state.binding_map[guid]
track = state.bool_tracks.get(binding)

test("T1.1: bool track created",
     track is not None)
test("T1.2: bool section created",
     track is not None and track.has_section)
test("T1.3: 1 visibility key applied",
     state.counters['KFVisibilityKeysApplied'] == 1,
     f"got {state.counters['KFVisibilityKeysApplied']}")
test("T1.4: channel 9 key value=True (1.0 → true)",
     track is not None and track.sections[0].keys[-1][1] == True)
test("T1.5: channel 9 key frame=10",
     track is not None and track.sections[0].keys[-1][0] == 10)
test("T1.6: 1 visibility track created",
     state.counters['KFVisibilityTrackCreated'] == 1,
     f"got {state.counters['KFVisibilityTrackCreated']}")
test("T1.7: 1 visibility section created",
     state.counters['KFVisibilitySectionCreated'] == 1,
     f"got {state.counters['KFVisibilitySectionCreated']}")

# =========================================================
# T2: hide_render channel 10 key applied
# =========================================================

banner("T2 — hide_render channel 10 key applied")

state2 = MockVisibilityKeyframeState()
state2.create_sequence()
state2.add_possessable(guid)

entries2 = [make_entry(guid, 25, 1.0, 10)]
state2.handle_keyframe(make_header(1, time.time(), len(entries2)), entries2)

binding2 = state2.binding_map[guid]
track2 = state2.bool_tracks.get(binding2)

test("T2.1: bool track created for ch 10",
     track2 is not None)
test("T2.2: channel 10 key applied",
     state2.counters['KFVisibilityKeysApplied'] == 1,
     f"got {state2.counters['KFVisibilityKeysApplied']}")
test("T2.3: channel 10 key value=True",
     track2 is not None and track2.sections[0].keys[-1][1] == True)
test("T2.4: channel 10 key frame=25",
     track2 is not None and track2.sections[0].keys[-1][0] == 25)

# =========================================================
# T3: mixed transform + visibility packet
# =========================================================

banner("T3 — mixed transform + visibility packet")

state3 = MockVisibilityKeyframeState()
state3.create_sequence()
state3.add_possessable(guid)

entries3 = [
    make_entry(guid, 10, 100.0, 0),   # LocX
    make_entry(guid, 10, 200.0, 1),   # LocY
    make_entry(guid, 10, 300.0, 2),   # LocZ
    make_entry(guid, 20, 1.0, 9),     # hide_viewport=true
    make_entry(guid, 30, 45.0, 3),    # RotX
    make_entry(guid, 30, 1.0, 10),    # hide_render=true
]
state3.handle_keyframe(make_header(1, time.time(), len(entries3)), entries3)

binding3 = state3.binding_map[guid]
t3_track = state3.transform_tracks.get(binding3)
t3_bool = state3.bool_tracks.get(binding3)

test("T3.1: transform track created",
     t3_track is not None)
test("T3.2: bool track created",
     t3_bool is not None)
test("T3.3: 4 transform keys applied",
     state3.counters['KFKeysApplied'] == 4,
     f"got {state3.counters['KFKeysApplied']}")
test("T3.4: 2 visibility keys applied",
     state3.counters['KFVisibilityKeysApplied'] == 2,
     f"got {state3.counters['KFVisibilityKeysApplied']}")
test("T3.5: LocX channel preserved",
     t3_track is not None and len(t3_track.sections[0].channels[0]) == 1)
test("T3.6: LocX value=100.0 preserved",
     t3_track is not None and t3_track.sections[0].channels[0][0][1] == 100.0)
test("T3.7: RotX channel preserved",
     t3_track is not None and len(t3_track.sections[0].channels[3]) == 1)
test("T3.8: RotX value=45.0 preserved",
     t3_track is not None and t3_track.sections[0].channels[3][0][1] == 45.0)
test("T3.9: visibility ch 9 has key",
     t3_bool is not None and any(k[0] == 20 for k in t3_bool.sections[0].keys))
test("T3.10: visibility ch 10 has key",
     t3_bool is not None and any(k[0] == 30 for k in t3_bool.sections[0].keys))

# =========================================================
# T4: missing binding safe and counted
# =========================================================

banner("T4 — missing binding safe and counted")

state4 = MockVisibilityKeyframeState()
state4.create_sequence()
# Do NOT add possessable — binding will be missing

missing_guid = make_guid(99)
entries4 = [
    make_entry(missing_guid, 10, 1.0, 9),   # vis ch 9, missing
    make_entry(missing_guid, 20, 0.0, 10),  # vis ch 10, missing
    make_entry(missing_guid, 30, 100.0, 0), # transform, missing
]
state4.handle_keyframe(make_header(1, time.time(), len(entries4)), entries4)

test("T4.1: missing binding counted (transform)",
     state4.counters['KFMissingBinding'] >= 1,
     f"got {state4.counters['KFMissingBinding']}")
test("T4.2: visibility unsupported NOT incremented for missing binding",
     state4.counters['KFVisibilityUnsupported'] == 0,
     f"got {state4.counters['KFVisibilityUnsupported']}")
test("T4.3: no crash — transform tracks empty",
     len(state4.transform_tracks) == 0)
test("T4.4: no crash — bool tracks empty",
     len(state4.bool_tracks) == 0)
test("T4.5: no visibility keys applied",
     state4.counters['KFVisibilityKeysApplied'] == 0)
test("T4.6: missing binding log emitted for vis channels",
     any("VISIBILITY" in l and "missing binding" in l for l in state4.logs),
     f"logs: {[l for l in state4.logs if 'missing' in l]}")

# =========================================================
# T5: unsupported channel >10 safe and counted
# =========================================================

banner("T5 — unsupported channel >10 safe and counted")

state5 = MockVisibilityKeyframeState()
state5.create_sequence()
state5.add_possessable(guid)

entries5 = [
    make_entry(guid, 10, 1.0, 9),    # valid vis
    make_entry(guid, 20, 1.0, 11),   # unsupported
    make_entry(guid, 30, 1.0, 42),   # unsupported
    make_entry(guid, 40, 100.0, 0),  # valid transform
]
state5.handle_keyframe(make_header(1, time.time(), len(entries5)), entries5)

test("T5.1: unsupported channel counted",
     state5.counters['KFUnsupportedChannel'] == 2,
     f"got {state5.counters['KFUnsupportedChannel']}")
test("T5.2: visibility unsupported counted",
     state5.counters['KFVisibilityUnsupported'] == 2,
     f"got {state5.counters['KFVisibilityUnsupported']}")
test("T5.3: valid visibility keys still applied",
     state5.counters['KFVisibilityKeysApplied'] == 1,
     f"got {state5.counters['KFVisibilityKeysApplied']}")
test("T5.4: valid transform keys still applied",
     state5.counters['KFKeysApplied'] == 1,
     f"got {state5.counters['KFKeysApplied']}")
test("T5.5: no crash — tracks intact",
     len(state5.bool_tracks) == 1 and len(state5.transform_tracks) == 1)
test("T5.6: unsupported log emitted",
     any("unsupported" in l for l in state5.logs),
     f"logs: {[l for l in state5.logs if 'unsupported' in l]}")

# =========================================================
# T6: stale sequence rejected before apply
# =========================================================

banner("T6 — stale sequence rejected before apply")

state6 = MockVisibilityKeyframeState()
state6.create_sequence()
state6.add_possessable(guid)

entries6 = [make_entry(guid, 10, 1.0, 9)]

# First packet applies
state6.handle_keyframe(make_header(1, time.time(), len(entries6)), entries6)
first_vis = state6.counters['KFVisibilityKeysApplied']

# Now clear sequence (simulate stale/destroyed sequence)
state6.clear_sequence()

# Second packet should be rejected (no active sequence)
state6.handle_keyframe(make_header(2, time.time(), len(entries6)), entries6)

test("T6.1: first key applied",
     first_vis == 1,
     f"got {first_vis}")
test("T6.2: no keys applied to stale sequence",
     state6.counters['KFVisibilityKeysApplied'] == 1,
     f"got {state6.counters['KFVisibilityKeysApplied']}")
test("T6.3: stale log emitted",
     any("No active sequence" in l for l in state6.logs),
     f"logs: {state6.logs}")

# =========================================================
# T7: counters increment correctly
# =========================================================

banner("T7 — counters increment correctly")

state7 = MockVisibilityKeyframeState()
state7.create_sequence()
state7.add_possessable(guid)

entries7 = [
    make_entry(guid, 10, 1.0, 9),    # visibility
    make_entry(guid, 20, 1.0, 10),   # visibility
    make_entry(guid, 30, 100.0, 0),  # transform
    make_entry(guid, 40, 200.0, 1),  # transform
]
state7.handle_keyframe(make_header(1, time.time(), len(entries7)), entries7)

test("T7.1: KFVisibilityKeysApplied == 2",
     state7.counters['KFVisibilityKeysApplied'] == 2,
     f"got {state7.counters['KFVisibilityKeysApplied']}")
test("T7.2: KFVisibilityTrackCreated == 1",
     state7.counters['KFVisibilityTrackCreated'] == 1,
     f"got {state7.counters['KFVisibilityTrackCreated']}")
test("T7.3: KFVisibilitySectionCreated == 1",
     state7.counters['KFVisibilitySectionCreated'] == 1,
     f"got {state7.counters['KFVisibilitySectionCreated']}")
test("T7.4: KFVisibilityUnsupported == 0",
     state7.counters['KFVisibilityUnsupported'] == 0,
     f"got {state7.counters['KFVisibilityUnsupported']}")
test("T7.5: KFKeysApplied == 2 (transform)",
     state7.counters['KFKeysApplied'] == 2,
     f"got {state7.counters['KFKeysApplied']}")
test("T7.6: KFPacketsApplied == 1",
     state7.counters['KFPacketsApplied'] == 1,
     f"got {state7.counters['KFPacketsApplied']}")

# =========================================================
# T8: track created only when missing
# =========================================================

banner("T8 — track created only when missing")

state8 = MockVisibilityKeyframeState()
state8.create_sequence()
state8.add_possessable(guid)

# First packet: ch 9 → creates bool track
entries8a = [make_entry(guid, 10, 1.0, 9)]
state8.handle_keyframe(make_header(1, time.time(), len(entries8a)), entries8a)

# Second packet: ch 9 → reuses existing bool track
entries8b = [make_entry(guid, 20, 0.0, 9)]
state8.handle_keyframe(make_header(2, time.time(), len(entries8b)), entries8b)

test("T8.1: track created on first key",
     state8.counters['KFVisibilityTrackCreated'] == 1,
     f"got {state8.counters['KFVisibilityTrackCreated']}")
test("T8.2: track not recreated",
     state8.counters['KFVisibilityTrackCreated'] == 1,
     f"recreated: {state8.counters['KFVisibilityTrackCreated']}")
test("T8.3: section not recreated",
     state8.counters['KFVisibilitySectionCreated'] == 1,
     f"recreated: {state8.counters['KFVisibilitySectionCreated']}")
test("T8.4: both keys applied",
     state8.counters['KFVisibilityKeysApplied'] == 2,
     f"got {state8.counters['KFVisibilityKeysApplied']}")

# =========================================================
# T9: section created only when missing
# =========================================================

banner("T9 — section created only when missing")

state9 = MockVisibilityKeyframeState()
state9.create_sequence()
state9.add_possessable(guid)

# First ch 10 → creates track + section
entries9a = [make_entry(guid, 10, 1.0, 10)]
state9.handle_keyframe(make_header(1, time.time(), len(entries9a)), entries9a)

# Second ch 10 → reuses section
entries9b = [make_entry(guid, 20, 0.0, 10)]
state9.handle_keyframe(make_header(2, time.time(), len(entries9b)), entries9b)

test("T9.1: section created once",
     state9.counters['KFVisibilitySectionCreated'] == 1,
     f"got {state9.counters['KFVisibilitySectionCreated']}")
test("T9.2: track created once",
     state9.counters['KFVisibilityTrackCreated'] == 1,
     f"got {state9.counters['KFVisibilityTrackCreated']}")
test("T9.3: both keys in same section",
     len(state9.bool_tracks[state9.binding_map[guid]].sections[0].keys) == 2,
     f"got keys: {state9.bool_tracks[state9.binding_map[guid]].sections[0].keys}")

# =========================================================
# Summary
# =========================================================

banner("Summary")
total = PASS + FAIL
print(f"  {PASS}/{total} passed, {FAIL} failed")
if FAIL > 0:
    print("  FAILED TESTS:")
    for name, ok in RESULTS:
        if not ok:
            print(f"    - {name}")

sys.exit(0 if FAIL == 0 else 1)
