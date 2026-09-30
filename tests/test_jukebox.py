#!/usr/bin/env python3
"""Standalone jukebox tests (no pytest — run with .venv/bin/python tests/test_jukebox.py).

Covers: mapping, debouncer (incl. repeatable volume signs), Pointing_Down
landmark detector, band analysis on synthetic signals, decorrelated dance
pose math, stream gain, and play/stop/volume dispatch against a stubbed
robot. No camera, no MediaPipe model, no robot.
"""

import json
import math
import sys
import tempfile
import time
import wave as wave_mod
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from jukebox.analysis import analyze  # noqa: E402
from jukebox.gestures import GestureDebouncer, detect_pointing_down  # noqa: E402
from jukebox.songs import (  # noqa: E402
    SPECIAL, STOP, STREAM_RATE, find_audio, load_mapping, missing_songs, save_binding,
)
from jukebox import setup_songs  # noqa: E402
from jukebox.main import GAIN_STEP, Player, act  # noqa: E402

PASSED = 0


def ok(cond: bool, label: str) -> None:
    global PASSED
    assert cond, f"FAIL: {label}"
    PASSED += 1
    print(f"  ok: {label}")


# ── mapping ─────────────────────────────────────────────────────────────────

print("mapping")
tmp = Path(tempfile.mkdtemp())
mapping = load_mapping(tmp / "absent.json")
ok(mapping["Closed_Fist"] == STOP, "no songs.json: Closed_Fist still maps to STOP")
ok(mapping["Pointing_Up"] == "VOLUME_UP" and mapping["Pointing_Down"] == "VOLUME_DOWN",
   "no songs.json: volume signs still mapped")
ok(not any(v not in SPECIAL for v in mapping.values()), "no songs.json: no songs bound")

bad = tmp / "bad.json"
bad.write_text(json.dumps({"Jazz_Hands": "x.mp3"}))
try:
    load_mapping(bad)
    ok(False, "unknown gesture rejected")
except ValueError:
    ok(True, "unknown gesture rejected")

example = load_mapping(REPO / "songs.example.json")
ok(set(missing_songs(example)) == {"Thumb_Up", "Thumb_Down", "Victory", "ILoveYou", "Open_Palm"},
   "example file loads (comment key skipped) and its placeholder songs are reported missing")

print("binding")
lib = tmp / "music"
(lib / "Tango").mkdir(parents=True)
for name in ("Tango/Libertango.mp3", "Waltz.flac", "notes.txt"):
    (lib / name).write_bytes(b"x")
ok([p.name for p in find_audio(lib)] == ["Libertango.mp3", "Waltz.flac"], "find_audio: audio only, recursive")

songs = tmp / "songs.json"
save_binding("Victory", str(lib / "Tango/Libertango.mp3"), songs)
m = load_mapping(songs)
ok(m["Victory"].endswith("Libertango.mp3") and m["Closed_Fist"] == STOP, "bind a song, controls kept")
ok(not missing_songs(m), "bound file is not missing")
try:
    save_binding("Victory", str(lib / "nope.mp3"), songs)
    ok(False, "binding a missing file is refused")
except FileNotFoundError:
    ok(True, "binding a missing file is refused")
save_binding("Victory", None, songs)
ok("Victory" not in load_mapping(songs), "binding removed")

answers = iter([str(lib), "waltz", "1", "", "", "", "tango", "1"])
code = setup_songs.run(ask=lambda _prompt: next(answers), songs_file=songs)
m = load_mapping(songs)
ok(code == 0 and m["Thumb_Up"].endswith("Waltz.flac") and m["Open_Palm"].endswith("Libertango.mp3")
   and "Thumb_Down" not in m, "interactive setup: search, pick by number, Enter skips")

# ── debouncer ───────────────────────────────────────────────────────────────

print("debouncer")
d = GestureDebouncer(need_frames=3, cooldown_s=3.0)
ok(d.feed("Thumb_Up", 0.0) is None, "1 frame not enough")
ok(d.feed("Thumb_Up", 0.1) is None, "2 frames not enough")
ok(d.feed("Thumb_Up", 0.2) == "Thumb_Up", "3 consecutive frames fire")
ok(d.feed("Thumb_Up", 5.0) is None, "held sign does not re-fire even after cooldown")
ok(d.feed(None, 5.1) is None, "release resets")
fired = [d.feed("Thumb_Up", 5.2 + i * 0.1) for i in range(3)]
ok("Thumb_Up" in fired, "release + re-sign fires again")

dr = GestureDebouncer(need_frames=2, cooldown_s=3.0, repeatable=frozenset({"Pointing_Up"}), repeat_s=0.5)
dr.feed("Pointing_Up", 0.0)
ok(dr.feed("Pointing_Up", 0.1) == "Pointing_Up", "repeatable fires first time")
ok(dr.feed("Pointing_Up", 0.3) is None, "repeatable respects repeat interval")
ok(dr.feed("Pointing_Up", 0.7) == "Pointing_Up", "HELD repeatable re-fires (no release needed)")
ok(dr.feed("Pointing_Up", 1.3) == "Pointing_Up", "keeps stepping while held")

# ── Pointing_Down landmark detector ─────────────────────────────────────────

print("pointing down")


class P:
    def __init__(self, x, y):
        self.x, self.y = x, y


def hand(index_tip_y, curl=0.1):
    """21 landmarks: wrist at (0.5,0.5); index at x=0.5; others near wrist."""
    lm = [P(0.5, 0.5) for _ in range(21)]
    lm[5] = P(0.5, 0.55)              # index mcp
    lm[6] = P(0.5, (0.55 + index_tip_y) / 2)  # index pip
    lm[8] = P(0.5, index_tip_y)       # index tip
    for tip, mcp in ((12, 9), (16, 13), (20, 17)):
        lm[mcp] = P(0.55, 0.5)
        lm[tip] = P(0.55, 0.5 + curl)  # curled: close to wrist
    return lm


ok(detect_pointing_down(hand(index_tip_y=0.85)), "index down + curled others detected")
ok(not detect_pointing_down(hand(index_tip_y=0.2)), "index UP not detected as down")
open_hand = hand(index_tip_y=0.85)
for tip in (12, 16, 20):
    open_hand[tip] = P(0.55, 0.95)  # other fingers also extended down
ok(not detect_pointing_down(open_hand), "open hand not detected")
ok(not detect_pointing_down(None), "no landmarks -> False")

# ── analysis (synthetic signals) ────────────────────────────────────────────

print("analysis")


def synth_wav(seconds=16.0, sr=STREAM_RATE, bpm=None, low_hz=None, high_hz=None,
              split=False, offset=0.0):
    """Click track and/or tones; split=True puts low in 1st half, high in 2nd."""
    n = int(seconds * sr)
    t = np.arange(n) / sr
    x = np.zeros(n, dtype=np.float32)
    if bpm:
        beat = 60.0 / bpm
        for bt in np.arange(offset, seconds, beat):
            i = int(bt * sr)
            dur = int(0.05 * sr)
            env = np.exp(-np.linspace(0, 8, dur))
            x[i : i + dur] += (env * np.sin(2 * np.pi * 1000 * np.arange(dur) / sr))[: n - i] * 0.8
    if low_hz:
        seg = x[: n // 2] if split else x
        seg += 0.5 * np.sin(2 * np.pi * low_hz * t[: len(seg)]).astype(np.float32)
    if high_hz:
        seg = slice(n // 2, n) if split else slice(0, n)
        x[seg] += 0.5 * np.sin(2 * np.pi * high_hz * t[: n - (n // 2 if split else 0)]).astype(np.float32)
    path = Path(tempfile.mkdtemp()) / "s.wav"
    with wave_mod.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    return path


a_fast = analyze(synth_wav(bpm=130))
ok(abs(a_fast["bpm"] - 130.0) < 5.0, f"130 BPM detected ({a_fast['bpm']})")
a_slow = analyze(synth_wav(bpm=70))
ok(abs(a_slow["bpm"] - 70.0) < 4.0 or abs(a_slow["bpm"] - 140.0) < 8.0,
   f"70 BPM detected as 70 or folded 140 ({a_slow['bpm']})")

a_band = analyze(synth_wav(low_hz=100, high_hz=2000, split=True))
half = len(a_band["low"]) // 2
ok(np.mean(a_band["low"][:half]) > np.mean(a_band["low"][half:]),
   "low envelope tracks the 100 Hz half")
ok(np.mean(a_band["high"][half:]) > np.mean(a_band["high"][:half]),
   "high envelope tracks the 2 kHz half")
for k in ("energy", "low", "high"):
    ok(0.0 <= min(a_band[k]) and max(a_band[k]) <= 1.0, f"{k} normalised to [0,1]")
ok(analyze(synth_wav(bpm=130))["version"] == 3, "sidecar version 3")

a_off = analyze(synth_wav(bpm=120, offset=0.25))
period = 60.0 / 120.0
err = min(abs(a_off["beat_offset_s"] - 0.25), abs(a_off["beat_offset_s"] - 0.25 + period),
          abs(a_off["beat_offset_s"] - 0.25 - period))
ok(err < 0.06, f"beat offset recovered ({a_off['beat_offset_s']}s vs 0.25s)")
a_zero = analyze(synth_wav(bpm=120, offset=0.0))
err0 = min(abs(a_zero["beat_offset_s"]), abs(a_zero["beat_offset_s"] - period))
ok(err0 < 0.06, f"zero offset stays ~zero ({a_zero['beat_offset_s']}s)")

# ── dance pose math ─────────────────────────────────────────────────────────

print("dance pose")
from jukebox.dance import dance_pose  # noqa: E402

for analysis, label in ((a_fast, "fast"), (a_slow, "slow")):
    for t in np.linspace(0, 10, 200):
        p = dance_pose(analysis, float(t))
        assert abs(p["roll"]) <= 40 and abs(p["pitch"]) <= 40, "head angle clamp"
        assert abs(math.degrees(p["body_yaw"])) <= 20, "body yaw modest"
        assert abs(p["z"]) <= 0.015 and abs(p["x"]) <= 0.01 and abs(p["y"]) <= 0.01, "translations small"
    ok(True, f"{label} style poses inside safety clamps over 10s sweep")

# decorrelation: body follows low band, head follows high band
quiet = dict(a_band, low=[0.0] * len(a_band["low"]), high=[1.0] * len(a_band["high"]))
loud = dict(a_band, low=[1.0] * len(a_band["low"]), high=[1.0] * len(a_band["high"]))
ts = np.linspace(0, 8, 160)
body_quiet = max(abs(dance_pose(quiet, float(t))["body_yaw"]) for t in ts)
body_loud = max(abs(dance_pose(loud, float(t))["body_yaw"]) for t in ts)
ok(body_loud > 2 * body_quiet, "body amplitude driven by LOW band")
nohigh = dict(a_band, low=[1.0] * len(a_band["low"]), high=[0.0] * len(a_band["high"]))
head_quiet = max(abs(dance_pose(nohigh, float(t))["roll"]) for t in ts)
head_loud = max(abs(dance_pose(loud, float(t))["roll"]) for t in ts)
ok(head_loud > 2 * head_quiet, "head amplitude driven by HIGH band")
ok(body_quiet >= 0 and max(abs(dance_pose(nohigh, float(t))["body_yaw"]) for t in ts) > 2 * body_quiet,
   "low band moves body even with head band silent (decorrelated)")

# ── stream + player dispatch ────────────────────────────────────────────────

print("stream + dispatch")


class StubMedia:
    def __init__(self):
        self.chunks, self.cleared = [], 0
        self.audio = self

    def push_audio_sample(self, data):
        self.chunks.append(data)

    def clear_player(self):
        self.cleared += 1


class StubRobot:
    def __init__(self):
        self.media = StubMedia()
        self.targets = []

    def set_target(self, **kw):
        self.targets.append(kw)

    def goto_target(self, **kw):
        pass


import jukebox.main as jm  # noqa: E402

song = synth_wav(seconds=4.0, bpm=120)
jm.wav_for = lambda p: song
robot = StubRobot()
player = Player(robot, dance=True, gain=1.0)
act(player, mapping | {"Thumb_Up": str(song)}, "Thumb_Up")
time.sleep(0.6)
ok(len(robot.media.chunks) >= 3, "stream pushes audio chunks")
ok(player.dancer is not None and player.dancer.is_alive(), "dancer running on stream clock")
ok(len(robot.targets) >= 3, "dancer drives set_target")

act(player, mapping, "Pointing_Up")
ok(abs(player.gain - (1.0 + GAIN_STEP)) < 1e-9, "volume sign raises gain")
ok(abs(player.stream.gain - player.gain) < 1e-9, "live stream gain updated mid-song")
for _ in range(10):
    act(player, mapping, "Pointing_Up")
ok(player.gain <= 1.5, "gain capped at 1.5")
for _ in range(20):
    act(player, mapping, "Pointing_Down")
ok(player.gain >= 0.0, "gain floored at 0")

# calm: hand in frame damps the dance so the camera steadies
dancer = player.dancer
n0 = len(robot.targets)
time.sleep(0.2)
full_moves = robot.targets[n0:]
dancer.calm(1.0)
time.sleep(0.6)
n1 = len(robot.targets)
time.sleep(0.2)
calm_moves = robot.targets[n1:]
ok(dancer._damp < 0.3, "calm damps motion toward 15%")
ok(len(calm_moves) > 0, "dancer keeps ticking while calm")

act(player, mapping, "Closed_Fist")
time.sleep(0.3)
ok(player.stream is None and player.dancer is None, "STOP tears down stream + dancer")
ok(robot.media.cleared >= 1, "stop flushes queued audio (instant silence)")

print(f"\nALL {PASSED} CHECKS PASSED")
