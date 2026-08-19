"""Beat-synced dancing: a thread driving set_target from the song analysis.

Style scales with tempo, amplitude follows the live energy envelope:

- fast (>= 115 BPM): body-yaw sway on half notes, head bob dipping on every
  beat, antennas flicking on the beat
- slow (< 85 BPM): gentle head roll + slow body sway on whole notes
- mid: linear blend of the two

All angles stay well inside the safety clamps (head pitch/roll ±40°,
head<->body yaw diff 65°). The pose math is pure (`dance_pose`) so tests can
sweep it without a robot.
"""

from __future__ import annotations

import logging
import math
import threading
import time

import numpy as np

logger = logging.getLogger("jukebox.dance")

TICK_HZ = 20.0


def _style_blend(bpm: float) -> float:
    """0.0 = slow style, 1.0 = fast style."""
    return min(max((bpm - 85.0) / 30.0, 0.0), 1.0)


def energy_at(analysis: dict, t: float) -> float:
    env = analysis["energy"]
    if not env:
        return 0.5
    i = int(t * analysis["energy_hz"])
    return float(env[min(i, len(env) - 1)])


def dance_pose(analysis: dict, t: float) -> dict:
    """Pose targets at song time t: head angles (deg), z (m), antennas, body_yaw (rad)."""
    bpm = analysis["bpm"]
    beat = 60.0 / bpm
    phase = (t / beat) % 1.0        # 0..1 within a beat
    bar_phase = (t / (4 * beat)) % 1.0
    fast = _style_blend(bpm)
    amp = 0.3 + 0.7 * energy_at(analysis, t)

    # Fast: sway ±18° on half notes, bob dips 12 mm each beat, pitch nod 8°.
    sway_fast = 18.0 * math.sin(2 * math.pi * t / (2 * beat))
    bob_fast = -0.012 * (math.sin(math.pi * phase) ** 2)
    pitch_fast = 8.0 * math.sin(2 * math.pi * phase)
    roll_fast = 0.0
    ant_fast = 0.45 * math.sin(2 * math.pi * phase) + 0.15

    # Slow: roll ±10° + sway ±10° over a whole bar, slow deep bob, no nod.
    sway_slow = 10.0 * math.sin(2 * math.pi * bar_phase)
    bob_slow = -0.008 * (math.sin(math.pi * bar_phase) ** 2)
    pitch_slow = 3.0 * math.sin(2 * math.pi * bar_phase)
    roll_slow = 10.0 * math.sin(2 * math.pi * t / (2 * beat))
    ant_slow = 0.25 * math.sin(2 * math.pi * bar_phase) + 0.1

    mix = lambda a, b: (1 - fast) * a + fast * b  # noqa: E731
    body_yaw_deg = amp * mix(sway_slow, sway_fast)
    return {
        "roll": amp * mix(roll_slow, roll_fast),
        "pitch": amp * mix(pitch_slow, pitch_fast),
        "yaw": 0.35 * body_yaw_deg,  # head leads the sway a little
        "z": amp * mix(bob_slow, bob_fast),
        "antennas": (
            amp * mix(ant_slow, ant_fast),
            -amp * mix(ant_slow, ant_fast),
        ),
        "body_yaw": math.radians(body_yaw_deg),
    }


class Dancer(threading.Thread):
    """Drive the robot to the beat until stopped."""

    def __init__(self, robot, analysis: dict, start_time: float):
        super().__init__(name="jukebox-dancer", daemon=True)
        self.robot = robot
        self.analysis = analysis
        self.start_time = start_time
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
        create_head_pose = self._head_pose
        logger.info("dancing at %.0f BPM", self.analysis["bpm"])
        period = 1.0 / TICK_HZ
        while not self._stop.wait(period):
            t = time.monotonic() - self.start_time
            if t < 0:
                continue
            if t > self.analysis["duration_s"]:
                logger.info("song over, dance done")
                break
            p = dance_pose(self.analysis, t)
            try:
                self.robot.set_target(
                    head=create_head_pose(
                        0, 0, p["z"], p["roll"], p["pitch"], p["yaw"], degrees=True
                    ),
                    antennas=np.array(p["antennas"]),
                    body_yaw=p["body_yaw"],
                )
            except Exception as e:
                logger.warning("set_target failed: %s", e)
                time.sleep(0.5)
