"""Offline song analysis: BPM + energy envelope, cached as a JSON sidecar.

Runs once per converted WAV (mono 44.1 kHz s16, produced by songs.wav_for).
Pure numpy — no librosa:

- onset envelope: positive first-difference of frame RMS at 50 Hz
- tempo: autocorrelation of the onset envelope, peak in the 60–190 BPM range,
  with octave folding (prefer the 90–180 window when a half/double is stronger)
- energy: RMS at 10 Hz, normalised by its 95th percentile → [0, 1]

The dance thread replays this against the playback clock, so motion amplitude
follows the actual waveform (quiet intro = small moves, chorus = big).
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np

FRAME_HZ = 50.0
ENERGY_HZ = 10.0
BPM_MIN, BPM_MAX = 60.0, 190.0


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        assert w.getsampwidth() == 2, "expected s16 wav (songs.wav_for output)"
        sr = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if w.getnchannels() > 1:
            data = data.reshape(-1, w.getnchannels()).mean(axis=1)
    return data.astype(np.float32) / 32768.0, sr


def _frame_rms(x: np.ndarray, sr: int, rate_hz: float) -> np.ndarray:
    hop = int(sr / rate_hz)
    n = len(x) // hop
    frames = x[: n * hop].reshape(n, hop)
    return np.sqrt((frames**2).mean(axis=1))


def _estimate_bpm(onset: np.ndarray, rate_hz: float) -> float:
    onset = onset - onset.mean()
    ac = np.correlate(onset, onset, mode="full")[len(onset) - 1 :]
    lag_min = int(rate_hz * 60.0 / BPM_MAX)
    lag_max = min(int(rate_hz * 60.0 / BPM_MIN), len(ac) - 1)
    if lag_max <= lag_min:
        return 100.0
    lags = np.arange(lag_min, lag_max + 1)
    window = ac[lag_min : lag_max + 1]
    best = lags[int(np.argmax(window))]
    bpm = 60.0 * rate_hz / best
    # Octave folding: dances read better in 90–180; fold halves/doubles in.
    while bpm < 90.0 and bpm * 2 <= BPM_MAX:
        bpm *= 2
    while bpm > 180.0:
        bpm /= 2
    return float(bpm)


def analyze(wav_path: Path | str) -> dict:
    """Return {bpm, duration_s, energy_hz, energy: [...]} for a WAV, cached."""
    wav_path = Path(wav_path)
    sidecar = wav_path.with_suffix(".dance.json")
    if sidecar.exists() and sidecar.stat().st_mtime >= wav_path.stat().st_mtime:
        return json.loads(sidecar.read_text())

    x, sr = _read_wav(wav_path)
    rms = _frame_rms(x, sr, FRAME_HZ)
    onset = np.maximum(np.diff(rms, prepend=rms[:1]), 0.0)
    bpm = _estimate_bpm(onset, FRAME_HZ)

    energy = _frame_rms(x, sr, ENERGY_HZ)
    scale = float(np.percentile(energy, 95)) or 1.0
    energy = np.clip(energy / scale, 0.0, 1.0)

    result = {
        "bpm": round(bpm, 1),
        "duration_s": round(len(x) / sr, 2),
        "energy_hz": ENERGY_HZ,
        "energy": [round(float(e), 3) for e in energy],
    }
    sidecar.write_text(json.dumps(result))
    return result
