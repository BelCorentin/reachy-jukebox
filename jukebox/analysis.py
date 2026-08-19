"""Offline song analysis: BPM + band envelopes, cached as a JSON sidecar.

Runs once per converted WAV (mono s16 @ STREAM_RATE). Pure numpy:

- onset envelope: positive first-difference of frame RMS at 50 Hz
- tempo: autocorrelation of the onset envelope, peak in 60–190 BPM,
  octave-folded into the 90–180 window
- envelopes at 20 Hz, each normalised by its 95th percentile → [0, 1]:
  - ``energy``: broadband RMS (overall loudness)
  - ``low``:  40–250 Hz band (bass / rhythm section — drives the BODY)
  - ``high``: 1–4 kHz band (melody / accordion / voice — drives the HEAD)

The dance thread replays these against the sample-exact playback clock, so
the two body parts follow different components of the actual waveform
(Libertango: body does the tango pulse, head rides the bandoneon runs).
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np

VERSION = 3
FRAME_HZ = 50.0
ENV_HZ = 20.0
BPM_MIN, BPM_MAX = 60.0, 190.0
LOW_BAND = (40.0, 250.0)
HIGH_BAND = (1000.0, 4000.0)


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        assert w.getsampwidth() == 2, "expected s16 wav (songs.wav_for output)"
        sr = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if w.getnchannels() > 1:
            data = data.reshape(-1, w.getnchannels()).mean(axis=1)
    return data.astype(np.float32) / 32768.0, sr


def _frames(x: np.ndarray, sr: int, rate_hz: float) -> np.ndarray:
    hop = int(sr / rate_hz)
    n = len(x) // hop
    return x[: n * hop].reshape(n, hop)


def _estimate_bpm(onset: np.ndarray, rate_hz: float) -> float:
    onset = onset - onset.mean()
    ac = np.correlate(onset, onset, mode="full")[len(onset) - 1 :]
    lag_min = int(rate_hz * 60.0 / BPM_MAX)
    lag_max = min(int(rate_hz * 60.0 / BPM_MIN), len(ac) - 1)
    if lag_max <= lag_min:
        return 100.0
    lags = np.arange(lag_min, lag_max + 1)
    best = lags[int(np.argmax(ac[lag_min : lag_max + 1]))]
    bpm = 60.0 * rate_hz / best
    # Fold into a danceable 70-140 window: a 176 "BPM" waltz subdivision
    # should read as a graceful 88, not a headbang.
    while bpm < 70.0:
        bpm *= 2
    while bpm >= 140.0:
        bpm /= 2
    return float(bpm)


def _beat_offset(onset: np.ndarray, rate_hz: float, bpm: float) -> float:
    """Phase of the beat grid (seconds): where within a period the onsets live.

    Sums onset energy over a grid of candidate phases; the best phase aligns
    the dance's beat 0 with the song's actual beats instead of t=0.
    """
    period = 60.0 / bpm
    lag = period * rate_hz  # onset frames per beat
    n = len(onset)
    if n < 2 * lag:
        return 0.0
    phases = np.arange(0, int(round(lag)))
    scores = [onset[(np.arange(int(n // lag)) * lag + p).astype(int) % n].sum() for p in phases]
    return float(phases[int(np.argmax(scores))] / rate_hz)


def _norm(env: np.ndarray) -> list[float]:
    scale = float(np.percentile(env, 95)) or 1.0
    return [round(float(e), 3) for e in np.clip(env / scale, 0.0, 1.0)]


def _band_envelope(frames: np.ndarray, sr: int, band: tuple[float, float]) -> np.ndarray:
    """Per-frame amplitude in a frequency band, via rfft on each frame."""
    spec = np.abs(np.fft.rfft(frames * np.hanning(frames.shape[1]), axis=1))
    freqs = np.fft.rfftfreq(frames.shape[1], d=1.0 / sr)
    mask = (freqs >= band[0]) & (freqs < band[1])
    return np.sqrt((spec[:, mask] ** 2).mean(axis=1))


def analyze(wav_path: Path | str) -> dict:
    """Return {bpm, duration_s, env_hz, energy, low, high} for a WAV, cached."""
    wav_path = Path(wav_path)
    sidecar = wav_path.with_suffix(".dance.json")
    if sidecar.exists() and sidecar.stat().st_mtime >= wav_path.stat().st_mtime:
        cached = json.loads(sidecar.read_text())
        if cached.get("version") == VERSION:
            return cached

    x, sr = _read_wav(wav_path)

    rms50 = np.sqrt((_frames(x, sr, FRAME_HZ) ** 2).mean(axis=1))
    onset = np.maximum(np.diff(rms50, prepend=rms50[:1]), 0.0)
    bpm = _estimate_bpm(onset, FRAME_HZ)

    frames20 = _frames(x, sr, ENV_HZ)
    result = {
        "version": VERSION,
        "bpm": round(bpm, 1),
        "beat_offset_s": round(_beat_offset(onset, FRAME_HZ, bpm), 3),
        "duration_s": round(len(x) / sr, 2),
        "env_hz": ENV_HZ,
        "energy": _norm(np.sqrt((frames20**2).mean(axis=1))),
        "low": _norm(_band_envelope(frames20, sr, LOW_BAND)),
        "high": _norm(_band_envelope(frames20, sr, HIGH_BAND)),
    }
    sidecar.write_text(json.dumps(result))
    return result
