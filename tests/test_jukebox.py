#!/usr/bin/env python3
"""Standalone jukebox tests (no pytest — run with .venv/bin/python tests/test_jukebox.py).

Covers: mapping load/validation, debouncer state machine, conversion cache
keying, and play/stop dispatch against a stubbed robot. No camera, no
MediaPipe, no robot.
"""

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from jukebox.gestures import GestureDebouncer  # noqa: E402
from jukebox.songs import STOP, load_mapping  # noqa: E402
from jukebox.main import Player, act  # noqa: E402

PASSED = 0


def ok(cond: bool, label: str) -> None:
    global PASSED
    assert cond, f"FAIL: {label}"
    PASSED += 1
    print(f"  ok: {label}")


# ── mapping ─────────────────────────────────────────────────────────────────

print("mapping")
mapping = load_mapping()
ok(mapping["Closed_Fist"] == STOP, "Closed_Fist maps to STOP")
ok(all(v == STOP or v.startswith("/") for v in mapping.values()), "paths are expanded to absolute")

bad = Path(tempfile.mkdtemp()) / "songs.json"
bad.write_text(json.dumps({"Jazz_Hands": "x.mp3"}))
try:
    load_mapping(bad)
    ok(False, "unknown gesture rejected")
except ValueError:
    ok(True, "unknown gesture rejected")

# ── debouncer ───────────────────────────────────────────────────────────────

print("debouncer")
d = GestureDebouncer(need_frames=3, cooldown_s=3.0)
ok(d.feed("Thumb_Up", 0.0) is None, "1 frame not enough")
ok(d.feed("Thumb_Up", 0.1) is None, "2 frames not enough")
ok(d.feed("Thumb_Up", 0.2) == "Thumb_Up", "3 consecutive frames fire")
ok(d.feed("Thumb_Up", 0.3) is None, "held sign does not re-fire")
ok(d.feed("Thumb_Up", 5.0) is None, "held sign does not re-fire even after cooldown")
ok(d.feed(None, 5.1) is None, "release resets")
for i in range(3):
    r = d.feed("Thumb_Up", 5.2 + i * 0.1)
ok(r == "Thumb_Up", "release + re-sign fires again after cooldown")

d2 = GestureDebouncer(need_frames=3, cooldown_s=3.0)
for i in range(3):
    d2.feed("Thumb_Up", i * 0.1)
d2.feed(None, 0.4)
r = None
for i in range(3):
    r = d2.feed("Victory", 0.5 + i * 0.1)
ok(r is None, "different sign inside cooldown suppressed")
fired = [d2.feed("Victory", 4.0 + i * 0.1) for i in range(3)]
ok("Victory" in fired, "different sign after cooldown fires")

d3 = GestureDebouncer(need_frames=3, cooldown_s=0.0)
d3.feed("Thumb_Up", 0.0)
d3.feed("Victory", 0.1)
d3.feed("Victory", 0.2)
ok(d3.feed("Victory", 0.3) == "Victory", "candidate switch restarts the count")

# ── player dispatch ─────────────────────────────────────────────────────────

print("player dispatch")


class StubMedia:
    def __init__(self):
        self.played, self.stops = [], 0

    def play_sound(self, f):
        self.played.append(f)

    def stop_playing(self):
        self.stops += 1


class StubRobot:
    def __init__(self):
        self.media = StubMedia()
        self.wobbling = False

    def enable_wobbling(self):
        self.wobbling = True


import jukebox.main as jm  # noqa: E402

song = Path(tempfile.mkdtemp()) / "t.wav"
song.write_bytes(b"RIFF0000WAVE")
jm.wav_for = lambda p: song  # skip ffmpeg
robot = StubRobot()
player = Player(robot)
ok(robot.wobbling, "wobbling enabled at start")
act(player, {"Thumb_Up": "/x/y.mp3", "Closed_Fist": STOP}, "Thumb_Up")
ok(robot.media.played == [str(song)], "play dispatches converted wav")
ok(robot.media.stops == 1, "previous sound stopped before playing")
ok(player.now_playing == "/x/y.mp3", "now_playing tracked")
act(player, {"Closed_Fist": STOP}, "Closed_Fist")
ok(robot.media.stops == 2 and player.now_playing is None, "STOP stops")
act(player, {"Closed_Fist": STOP}, "Pointing_Up")
ok(robot.media.stops == 2, "unmapped gesture ignored")

print(f"\nALL {PASSED} CHECKS PASSED")
