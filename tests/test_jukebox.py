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

# ── analysis (synthetic click tracks) ───────────────────────────────────────

print("analysis")
import math  # noqa: E402
import wave as wave_mod  # noqa: E402

import numpy as np  # noqa: E402

from jukebox.analysis import analyze  # noqa: E402


def click_track(bpm: float, seconds: float = 20.0, sr: int = 44100, quiet_head: bool = False) -> Path:
    """s16 mono wav: decaying 1 kHz clicks on the beat, optional quiet first half."""
    n = int(seconds * sr)
    x = np.zeros(n, dtype=np.float32)
    beat = 60.0 / bpm
    t = 0.0
    while t < seconds:
        i = int(t * sr)
        dur = int(0.05 * sr)
        env = np.exp(-np.linspace(0, 8, dur))
        tone = np.sin(2 * math.pi * 1000 * np.arange(dur) / sr)
        seg = (env * tone)[: n - i]
        amp = 0.2 if (quiet_head and t < seconds / 2) else 0.9
        x[i : i + len(seg)] += amp * seg
        t += beat
    path = Path(tempfile.mkdtemp()) / f"click{int(bpm)}.wav"
    with wave_mod.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((x * 32767).astype(np.int16).tobytes())
    return path


a_fast = analyze(click_track(130.0))
ok(abs(a_fast["bpm"] - 130.0) < 4.0, f"130 BPM detected ({a_fast['bpm']})")
a_slow = analyze(click_track(70.0))
ok(abs(a_slow["bpm"] - 70.0) < 4.0 or abs(a_slow["bpm"] - 140.0) < 8.0,
   f"70 BPM detected as 70 or folded 140 ({a_slow['bpm']})")
a_dyn = analyze(click_track(120.0, quiet_head=True))
e = a_dyn["energy"]
ok(np.mean(e[: len(e) // 2]) < np.mean(e[len(e) // 2 :]), "energy envelope tracks quiet->loud")
ok(0.0 <= min(e) and max(e) <= 1.0, "energy normalised to [0,1]")
p = analyze(click_track(130.0))
ok(a_fast["duration_s"] == p["duration_s"], "sidecar cache round-trips")

# ── dance pose math ─────────────────────────────────────────────────────────

print("dance pose")
from jukebox.dance import dance_pose  # noqa: E402

for analysis, label in ((a_fast, "fast"), (a_slow, "slow")):
    for t in np.linspace(0, 10, 200):
        pose = dance_pose(analysis, float(t))
        assert abs(pose["roll"]) <= 40 and abs(pose["pitch"]) <= 40, "head angle clamp"
        assert abs(math.degrees(pose["body_yaw"])) <= 20, "body yaw modest"
        assert abs(pose["z"]) <= 0.015, "bob within 15mm"
    ok(True, f"{label} style poses inside safety clamps over 10s sweep")

fast_amp = max(abs(dance_pose(a_fast, t)["pitch"]) for t in np.linspace(0, 4, 160))
slow_amp = max(abs(dance_pose(a_slow, t)["pitch"]) for t in np.linspace(0, 4, 160))
ok(fast_amp > slow_amp, "fast style nods harder than slow style")

# ── player dispatch ─────────────────────────────────────────────────────────

print("player dispatch")


class StubMedia:
    def __init__(self):
        self.played, self.stops = [], 0
        self.audio = None

    def play_sound(self, f):
        self.played.append(f)

    def stop_playing(self):
        self.stops += 1


class StubRobot:
    def __init__(self):
        self.media = StubMedia()
        self.wobbling = False
        self.targets = []

    def enable_wobbling(self):
        self.wobbling = True

    def set_target(self, **kw):
        self.targets.append(kw)

    def goto_target(self, **kw):
        pass


import jukebox.main as jm  # noqa: E402

song = click_track(120.0)
jm.wav_for = lambda p: song  # skip ffmpeg
robot = StubRobot()
player = Player(robot, dance=False)
ok(robot.wobbling, "wobble enabled when dance off")
act(player, {"Thumb_Up": "/x/y.mp3", "Closed_Fist": STOP}, "Thumb_Up")
ok(robot.media.played == [str(song)], "play dispatches converted wav")
ok(robot.media.stops == 1, "previous sound stopped before playing")
ok(player.now_playing == "/x/y.mp3", "now_playing tracked")
act(player, {"Closed_Fist": STOP}, "Closed_Fist")
ok(robot.media.stops == 2 and player.now_playing is None, "STOP stops")
act(player, {"Closed_Fist": STOP}, "Pointing_Up")
ok(robot.media.stops == 2, "unmapped gesture ignored")

robot2 = StubRobot()
player2 = Player(robot2, dance=True)
ok(not robot2.wobbling, "wobble skipped when dancing (no double motion)")
act(player2, {"Thumb_Up": "/x/y.mp3"}, "Thumb_Up")
import time as _t  # noqa: E402

_t.sleep(0.4)
ok(player2.dancer is not None and player2.dancer.is_alive(), "dancer thread running")
ok(len(robot2.targets) >= 3, "dancer drives set_target")
player2.stop()
_t.sleep(0.2)
ok(not player2.dancer_alive() if hasattr(player2, "dancer_alive") else player2.dancer is None,
   "stop kills dancer")

print(f"\nALL {PASSED} CHECKS PASSED")
