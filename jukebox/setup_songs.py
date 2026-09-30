"""Interactive song setup: pick a song from your music folder for each hand sign.

Run with ``./run.sh --setup-songs [FOLDER]`` (default folder: ~/Music).
For each sign you type part of a song name, pick one of the matches, and it is
written to songs.json. Enter keeps the current song; ``-`` removes it.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from jukebox.songs import (
    SIGN_EMOJI,
    SONG_GESTURES,
    SONGS_FILE,
    find_audio,
    load_mapping,
    save_binding,
)

MAX_MATCHES = 15


def _pick(gesture: str, library: list[Path], current: str | None, ask: Callable[[str], str]) -> Path | None | str:
    """Ask for one sign; returns a file, None to remove, or "keep"."""
    label = f"{SIGN_EMOJI[gesture]} {gesture}"
    now = Path(current).name if current else "nothing"
    while True:
        query = ask(f"\n{label} (now: {now}) — part of a song name, Enter = keep, '-' = remove: ").strip()
        if not query:
            return "keep"
        if query == "-":
            return None
        matches = [p for p in library if query.lower() in p.name.lower()]
        if not matches:
            print(f"  no song matches {query!r}, try another word")
            continue
        for i, p in enumerate(matches[:MAX_MATCHES], 1):
            print(f"  {i:2d}. {p.name}")
        if len(matches) > MAX_MATCHES:
            print(f"  … and {len(matches) - MAX_MATCHES} more, type a longer name to narrow it down")
        choice = ask("  number (Enter = search again): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= min(len(matches), MAX_MATCHES):
            return matches[int(choice) - 1]


def run(folder: str | None = None, ask: Callable[[str], str] = input, songs_file: Path = SONGS_FILE) -> int:
    """Walk through every song sign and save the choices; returns a process exit code."""
    if shutil.which("ffmpeg") is None:
        print("⚠ ffmpeg is not installed — the jukebox needs it to read your songs "
              "(Debian/Ubuntu: sudo apt install ffmpeg · macOS: brew install ffmpeg).")
    root = Path(folder or ask("Music folder [~/Music]: ").strip() or "~/Music").expanduser()
    if not root.is_dir():
        print(f"✗ {root} is not a folder")
        return 1
    library = find_audio(root)
    if not library:
        print(f"✗ no audio files found under {root}")
        return 1
    print(f"Found {len(library)} songs in {root}.")

    mapping = load_mapping(songs_file)
    for gesture in SONG_GESTURES:
        picked = _pick(gesture, library, mapping.get(gesture), ask)
        if picked == "keep":
            continue
        save_binding(gesture, None if picked is None else str(picked), songs_file)
        print(f"  ✓ {SIGN_EMOJI[gesture]} → {picked.name if isinstance(picked, Path) else 'nothing'}")

    print(f"\nSaved to {songs_file.name}. ✊ stops the music, ☝️ / 👇 change the volume.")
    print("Start the jukebox with ./run.sh (or ./run.sh --source webcam to try it with your laptop camera).")
    return 0
