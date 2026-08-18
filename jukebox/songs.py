"""Gesture→song mapping and WAV conversion cache.

songs.json maps MediaPipe gesture names to audio files (any format ffmpeg
reads). Files are converted once to the speaker format the daemon likes
(mono 44.1 kHz s16 WAV — same recipe as reachy-memoire's hub/speech.py)
and cached in cache/ keyed by source path + mtime, so editing the mapping
or replacing a file just works.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO / "cache"
STOP = "STOP"

KNOWN_GESTURES = (
    "Thumb_Up",
    "Thumb_Down",
    "Victory",
    "ILoveYou",
    "Open_Palm",
    "Closed_Fist",
    "Pointing_Up",
)


def load_mapping(path: Path | None = None) -> dict[str, str]:
    """Load songs.json; values are audio paths (~ expanded) or "STOP"."""
    path = path or REPO / "songs.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    mapping: dict[str, str] = {}
    for gesture, target in raw.items():
        if gesture not in KNOWN_GESTURES:
            raise ValueError(f"unknown gesture {gesture!r} (known: {', '.join(KNOWN_GESTURES)})")
        mapping[gesture] = target if target == STOP else str(Path(target).expanduser())
    return mapping


def wav_for(source: str) -> Path:
    """Return the cached speaker-ready WAV for *source*, converting if needed."""
    src = Path(source).expanduser()
    if not src.is_file():
        raise FileNotFoundError(f"song not found: {src}")
    key = hashlib.sha256(f"{src}|{src.stat().st_mtime_ns}".encode()).hexdigest()[:24]
    out = CACHE_DIR / f"{key}.wav"
    if out.exists():
        return out
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-ac", "1", "-ar", "44100", "-sample_fmt", "s16", str(tmp)],
        check=True, timeout=300,
    )
    tmp.rename(out)
    return out
