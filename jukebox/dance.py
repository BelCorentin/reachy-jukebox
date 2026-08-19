"""Two-band decorrelated dancing: body follows the bass, head the melody.

v3, after live feedback:
- phases are anchored to the song's actual beat grid (analysis
  ``beat_offset_s``), not to t=0, so the sway lands ON the music
- everything moved one metric level down — body sways over TWO BARS, head
  nods on half notes, roll over a bar — groovy instead of twitchy
- ``calm()`` lets the main loop damp the dance (~15% amplitude, smoothly)
  while a hand is in frame, so the camera steadies enough to read signs

Band mapping unchanged: BODY ← low band (40–250 Hz, tango circle),
HEAD ← high band (1–4 kHz, nods + transient accents).
"""

from __future__ import annotations

import logging
import math
import threading
import time

import numpy as np

logger = logging.getLogger("jukebox.dance")

TICK_HZ = 20.0
CALM_FACTOR = 0.15
CALM_RAMP_S = 0.4


def _env_at(analysis: dict, key: str, t: float) -> float:
    env = analysis.get(key) or []
    if not env:
        return 0.5
    i = int(t * analysis["env_hz"])
    return float(env[min(max(i, 0), len(env) - 1)])


def _transient(analysis: dict, key: str, t: float) -> float:
    dt = 1.0 / analysis["env_hz"]
    return min(max((_env_at(analysis, key, t) - _env_at(analysis, key, t - dt)) * 4.0, 0.0), 1.0)


def dance_pose(analysis: dict, t: float) -> dict:
    """Pose targets at song time t (angles deg, x/y/z m, body_yaw rad)."""
    bpm = analysis["bpm"]
    beat = 60.0 / bpm
    tb = t - analysis.get("beat_offset_s", 0.0)  # beat-grid time
    fast = min(max((bpm - 85.0) / 30.0, 0.0), 1.0)

    half_phase = (tb / (2 * beat)) % 1.0        # half note
    bar_phase = (tb / (4 * beat)) % 1.0         # bar
    twobar_phase = (tb / (8 * beat)) % 1.0      # two bars

    low = _env_at(analysis, "low", t)
    high = _env_at(analysis, "high", t)
    hit = _transient(analysis, "high", t)

    # BODY — bass: slow sweep over two bars, circle over a bar, half-note pulse.
    body_amp = 0.25 + 0.75 * low
    body_yaw_deg = body_amp * (14.0 + 4.0 * fast) * math.sin(2 * math.pi * twobar_phase)
    x = body_amp * 0.006 * math.sin(2 * math.pi * bar_phase)
    y = body_amp * 0.006 * math.cos(2 * math.pi * bar_phase)
    z = -body_amp * (0.006 + 0.004 * fast) * (math.sin(math.pi * half_phase) ** 2)

    # HEAD — melody: nod on half notes (bar when slow), roll over the bar,
    # transient accents on top so runs still read.
    head_amp = 0.15 + 0.85 * high
    nod_phase = half_phase if fast > 0.5 else bar_phase
    pitch = head_amp * (5.0 + 3.0 * fast) * math.sin(2 * math.pi * nod_phase) + 5.0 * hit
    roll = head_amp * 6.0 * math.sin(2 * math.pi * bar_phase)
    head_yaw = 0.3 * body_yaw_deg

    ant = 0.15 + 0.3 * high * math.sin(2 * math.pi * half_phase) + 0.3 * hit
    return {
        "x": x, "y": y, "z": z,
        "roll": roll, "pitch": pitch, "yaw": head_yaw,
        "antennas": (ant, -ant),
        "body_yaw": math.radians(body_yaw_deg),
    }


class Dancer(threading.Thread):
    """Drive the robot to the beat until stopped; clock comes from the player."""

    def __init__(self, robot, analysis: dict, clock):
        """clock: callable returning current song time in seconds."""
        super().__init__(name="jukebox-dancer", daemon=True)
        self.robot = robot
        self.analysis = analysis
        self.clock = clock
        self._stop = threading.Event()
        self._calm_until = 0.0
        self._damp = 1.0
        # Import here (not in run) so the SDK load cost lands before the
        # playback clock starts, keeping the first ticks on the beat.
        from reachy_mini.utils import create_head_pose

        self._head_pose = create_head_pose

    def calm(self, seconds: float = 2.0) -> None:
        """Damp the dance so the camera steadies (a hand is being shown)."""
        self._calm_until = time.monotonic() + seconds

    def stop(self, recenter: bool = True) -> None:
        self._stop.set()
        if recenter:
            try:
                self.robot.goto_target(
                    head=self._head_pose(0, 0, 0, 0, 0, 0, degrees=True),
                    antennas=[0.0, 0.0], body_yaw=0.0, duration=0.8,
                )
            except Exception:
                pass

    def _damping(self) -> float:
        """Smoothly approach CALM_FACTOR while calmed, 1.0 otherwise."""
        target = CALM_FACTOR if time.monotonic() < self._calm_until else 1.0
        step = 1.0 / (TICK_HZ * CALM_RAMP_S)
        if self._damp < target:
            self._damp = min(self._damp + step, target)
        else:
            self._damp = max(self._damp - step, target)
        return self._damp

    def run(self) -> None:
        logger.info(
            "dancing at %.0f BPM, beat offset %.2fs (body<-low, head<-high)",
            self.analysis["bpm"], self.analysis.get("beat_offset_s", 0.0),
        )
        period = 1.0 / TICK_HZ
        while not self._stop.wait(period):
            t = self.clock()
            if t < 0:
                continue
            if t > self.analysis["duration_s"]:
                logger.info("song over, dance done")
                break
            p = dance_pose(self.analysis, t)
            k = self._damping()
            try:
                self.robot.set_target(
                    head=self._head_pose(
                        k * p["x"], k * p["y"], k * p["z"],
                        k * p["roll"], k * p["pitch"], k * p["yaw"],
                        degrees=True,
                    ),
                    antennas=np.array((k * p["antennas"][0], k * p["antennas"][1])),
                    body_yaw=k * p["body_yaw"],
                )
            except Exception as e:
                logger.warning("set_target failed: %s", e)
                time.sleep(0.5)
