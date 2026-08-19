"""Stream music over the WebRTC audio channel with live software gain.

Why not the daemon's play_sound: its playbin is single-slot — ANY daemon
sound (including the volume endpoint's test chirp) kills the current song,
and hardware volume can't change without that chirp. Streaming through
``media.push_audio_sample`` (the same path the conversation app speaks
through) gives us a per-chunk gain multiplier instead: volume changes are
instant, silent, and purely client-side.

Chunks are pushed ~LEAD_S ahead of real time, so a gain change is audible
within that lead. The playback clock (``song_time()``) is sample-exact,
which is what keeps the dance on the beat.
"""

from __future__ import annotations

import logging
import threading
import time
import wave
from pathlib import Path

import numpy as np

from jukebox.songs import STREAM_RATE

logger = logging.getLogger("jukebox.stream")

CHUNK_S = 0.10
LEAD_S = 0.40
OUTPUT_LATENCY_S = 0.60  # WebRTC + jitter buffer + speaker; tune with --latency


def load_samples(wav_path: Path | str) -> np.ndarray:
    """Load a cache WAV (mono s16 @ STREAM_RATE) as float32 in [-1, 1]."""
    with wave.open(str(wav_path), "rb") as w:
        assert w.getframerate() == STREAM_RATE and w.getsampwidth() == 2
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if w.getnchannels() > 1:
            data = data.reshape(-1, w.getnchannels()).mean(axis=1)
    return data.astype(np.float32) / 32768.0


class StreamPlayer(threading.Thread):
    """Push one song's samples to the robot, paced, with live gain."""

    def __init__(self, robot, samples: np.ndarray, gain: float = 1.0,
                 latency_s: float = OUTPUT_LATENCY_S):
        super().__init__(name="jukebox-stream", daemon=True)
        self.robot = robot
        self.samples = samples
        self.gain = float(gain)
        self.latency_s = latency_s
        self._stop = threading.Event()
        self._t0: float | None = None
        self.done = False

    # -- control ------------------------------------------------------------

    def set_gain(self, gain: float) -> float:
        self.gain = float(min(max(gain, 0.0), 1.5))
        return self.gain

    def song_time(self) -> float:
        """Seconds of the song currently coming out of the speaker."""
        if self._t0 is None:
            return 0.0
        return time.monotonic() - self._t0 - self.latency_s

    def stop(self) -> None:
        self._stop.set()
        # Drop the ~LEAD_S of audio already queued so stop is instant.
        audio = getattr(self.robot.media, "audio", None)
        if audio is not None and hasattr(audio, "clear_player"):
            try:
                audio.clear_player()
            except Exception:
                pass

    # -- loop ---------------------------------------------------------------

    def run(self) -> None:
        chunk_n = int(CHUNK_S * STREAM_RATE)
        pos = 0
        self._t0 = time.monotonic()
        pushed_s = 0.0
        while not self._stop.is_set() and pos < len(self.samples):
            elapsed = time.monotonic() - self._t0
            if pushed_s - elapsed < LEAD_S:
                chunk = self.samples[pos : pos + chunk_n]
                pos += len(chunk)
                pushed_s += len(chunk) / STREAM_RATE
                try:
                    self.robot.media.push_audio_sample(
                        np.clip(chunk * self.gain, -1.0, 1.0)
                    )
                except Exception as e:
                    logger.warning("push_audio_sample failed: %s", e)
                    time.sleep(0.5)
            else:
                time.sleep(CHUNK_S / 2)
        # Let the tail drain unless we were stopped.
        if not self._stop.is_set():
            time.sleep(LEAD_S + self.latency_s)
        self.done = True
