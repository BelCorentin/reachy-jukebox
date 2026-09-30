"""Gesture→song mapping and WAV conversion cache.

songs.json (your own, not in git — see songs.example.json) maps hand signs to
audio files (any format ffmpeg reads). Only songs need to be listed: the stop
and volume signs have built-in defaults. Bind songs with
``./run.sh --setup-songs`` or ``./run.sh --bind Thumb_Up ~/Music/song.mp3``. Files are converted once to the speaker format the daemon likes
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
VOLUME_UP = "VOLUME_UP"
VOLUME_DOWN = "VOLUME_DOWN"
SPECIAL = (STOP, VOLUME_UP, VOLUME_DOWN)

# Music is streamed over the WebRTC audio channel (16 kHz — see stream.py),
# so the cache WAVs are rendered at that rate directly.
STREAM_RATE = 16000

KNOWN_GESTURES = (
    "Thumb_Up",
    "Thumb_Down",
    "Victory",
    "ILoveYou",
    "Open_Palm",
    "Closed_Fist",
    "Pointing_Up",
    "Pointing_Down",  # not a stock MediaPipe class; detected from landmarks
)


SONGS_FILE = REPO / "songs.json"
SONG_GESTURES = ("Thumb_Up", "Thumb_Down", "Victory", "ILoveYou", "Open_Palm")
SIGN_EMOJI = {
    "Thumb_Up": "👍", "Thumb_Down": "👎", "Victory": "✌️", "ILoveYou": "🤟", "Open_Palm": "✋",
    "Closed_Fist": "✊", "Pointing_Up": "☝️", "Pointing_Down": "👇",
}
CONTROL_DEFAULTS = {"Closed_Fist": STOP, "Pointing_Up": VOLUME_UP, "Pointing_Down": VOLUME_DOWN}
AUDIO_SUFFIXES = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".wma"}

SETUP_HINT = (
    "Bind your songs with one of:\n"
    "  ./run.sh --setup-songs                      # interactive: pick from your music folder\n"
    "  ./run.sh --bind Thumb_Up ~/Music/song.mp3   # one sign at a time\n"
    "  ./run.sh --songs                            # show what is bound\n"
    f"Signs for songs: {', '.join(SIGN_EMOJI[g] + ' ' + g for g in SONG_GESTURES)}"
)


def load_mapping(path: Path | None = None) -> dict[str, str]:
    """Load songs.json over the control defaults; values are audio paths (~ expanded) or a control."""
    path = path or SONGS_FILE
    mapping = dict(CONTROL_DEFAULTS)
    if not path.is_file():
        return mapping
    raw = json.loads(path.read_text(encoding="utf-8"))
    for gesture, target in raw.items():
        if gesture.startswith("_"):  # comment keys, as in songs.example.json
            continue
        if gesture not in KNOWN_GESTURES:
            raise ValueError(f"unknown gesture {gesture!r} in {path.name} (known: {', '.join(KNOWN_GESTURES)})")
        mapping[gesture] = target if target in SPECIAL else str(Path(target).expanduser())
    return mapping


def song_bindings(mapping: dict[str, str]) -> dict[str, str]:
    """The gestures bound to an audio file (not to a control)."""
    return {g: t for g, t in mapping.items() if t not in SPECIAL}


def missing_songs(mapping: dict[str, str]) -> dict[str, str]:
    """Bound songs whose file no longer exists."""
    return {g: t for g, t in song_bindings(mapping).items() if not Path(t).is_file()}


def save_binding(gesture: str, audio: str | None, path: Path | None = None) -> Path:
    """Bind *gesture* to *audio* in songs.json (None removes it); returns the file written."""
    path = path or SONGS_FILE
    if gesture not in KNOWN_GESTURES:
        raise ValueError(f"unknown sign {gesture!r} (songs: {', '.join(SONG_GESTURES)})")
    raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if audio is None:
        raw.pop(gesture, None)
    else:
        src = Path(audio).expanduser().resolve()
        if not src.is_file():
            raise FileNotFoundError(f"no such file: {src}")
        home = Path.home()
        raw[gesture] = f"~/{src.relative_to(home)}" if src.is_relative_to(home) else str(src)
    path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def find_audio(folder: Path) -> list[Path]:
    """All audio files under *folder*, sorted by name."""
    return sorted(
        (p for p in folder.expanduser().rglob("*") if p.suffix.lower() in AUDIO_SUFFIXES and p.is_file()),
        key=lambda p: p.name.lower(),
    )


def wav_for(source: str) -> Path:
    """Return the cached speaker-ready WAV for *source*, converting if needed."""
    src = Path(source).expanduser()
    if not src.is_file():
        raise FileNotFoundError(f"song not found: {src}")
    key = hashlib.sha256(f"{src}|{src.stat().st_mtime_ns}|{STREAM_RATE}".encode()).hexdigest()[:24]
    out = CACHE_DIR / f"{key}.wav"
    if out.exists():
        return out
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-ac", "1", "-ar", str(STREAM_RATE), "-sample_fmt", "s16", str(tmp)],
        check=True, timeout=300,
    )
    tmp.rename(out)
    return out
