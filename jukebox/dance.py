"""Two-band decorrelated dancing: body follows the bass, head the melody.

The analysis sidecar carries three 20 Hz envelopes of the actual waveform:
``low`` (40–250 Hz — rhythm section), ``high`` (1–4 kHz — melody/voice/
bandoneon) and broadband ``energy``. The dance replays them against the
stream player's sample-exact clock:

- **BODY (low band)** — a slow tango-like circle: body yaw sweeps over a
  bar while the head's x/y base traces a small circle, plus a z pulse on
  each beat. All of it scaled by the low envelope, so the body only really
  moves when the rhythm section plays.
- **HEAD (high band)** — pitch nods on the beat, roll wiggle at double
  time, scaled by the high envelope *and* its positive derivative
  (transients), so melody runs visibly ride on top of the body motion.

Tempo still sets the base pace (fast songs sway on half notes, slow ones
on whole bars). The pose math is pure (`dance_pose`) for robot-free tests.
"""

from __future__ import annotations

import logging
import math
import threading
import time

import numpy as np

logger = logging.getLogger("jukebox.dance")

TICK_HZ = 20.0


def _env_at(analysis: dict, key: str, t: float) -> float:
    env = analysis.get(key) or []
    if not env:
        return 0.5
    i = int(t * analysis["env_hz"])
    return float(env[min(max(i, 0), len(env) - 1)])


def _transient(analysis: dict, key: str, t: float) -> float:
    """Positive short-time derivative of an envelope, in [0, 1]."""
    dt = 1.0 / analysis["env_hz"]
    return min(max((_env_at(analysis, key, t) - _env_at(analysis, key, t - dt)) * 4.0, 0.0), 1.0)


def dance_pose(analysis: dict, t: float) -> dict:
    """Pose targets at song time t (angles deg, x/y/z m, body_yaw rad)."""
    bpm = analysis["bpm"]
    beat = 60.0 / bpm
    fast = min(max((bpm - 85.0) / 30.0, 0.0), 1.0)  # 0 slow .. 1 fast
    sway_period = (2.0 + 2.0 * (1.0 - fast)) * beat  # half notes fast, bar slow
    beat_phase = (t / beat) % 1.0
    circle_phase = 2 * math.pi * t / (4 * beat)

    low = _env_at(analysis, "low", t)
    high = _env_at(analysis, "high", t)
    hit = _transient(analysis, "high", t)

    # BODY — bass: yaw sweep + circular x/y base + z pulse on the beat.
    body_amp = 0.25 + 0.75 * low
    body_yaw_deg = body_amp * (12.0 + 6.0 * fast) * math.sin(2 * math.pi * t / sway_period)
    x = body_amp * 0.006 * math.sin(circle_phase)
    y = body_amp * 0.006 * math.cos(circle_phase)
    z = -body_amp * (0.006 + 0.004 * fast) * (math.sin(math.pi * beat_phase) ** 2)

    # HEAD — melody: nod + roll ride on top, plus transient accents.
    head_amp = 0.15 + 0.85 * high
    pitch = head_amp * (6.0 + 3.0 * fast) * math.sin(2 * math.pi * beat_phase) + 6.0 * hit
    roll = head_amp * 7.0 * math.sin(2 * math.pi * t / (beat / 2 if fast > 0.5 else beat))
    head_yaw = 0.3 * body_yaw_deg

    ant = 0.15 + 0.35 * high * math.sin(2 * math.pi * beat_phase) + 0.3 * hit
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
        # Import here (not in run) so the SDK load cost lands before the
        # playback clock starts, keeping the first ticks on the beat.
        from reachy_mini.utils import create_head_pose

        self._head_pose = create_head_pose

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

    def run(self) -> None:
        logger.info(
            "dancing at %.0f BPM (body<-low band, head<-high band)", self.analysis["bpm"]
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
            try:
                self.robot.set_target(
                    head=self._head_pose(
                        p["x"], p["y"], p["z"], p["roll"], p["pitch"], p["yaw"],
                        degrees=True,
                    ),
                    antennas=np.array(p["antennas"]),
                    body_yaw=p["body_yaw"],
                )
            except Exception as e:
                logger.warning("set_target failed: %s", e)
                time.sleep(0.5)
