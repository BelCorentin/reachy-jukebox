"""Reachy jukebox: hand signs pick songs, played on the robot speaker.

Loop: camera frame -> MediaPipe gesture -> debounce -> play/stop on the
daemon speaker (session-independent play_sound). While a song plays, a
Dancer thread drives beat-synced moves whose style follows the track's BPM
and whose amplitude follows the live energy envelope (see jukebox/dance.py).
"""

from __future__ import annotations

import argparse
import logging
import time

from jukebox.analysis import analyze
from jukebox.capture import make_source
from jukebox.dance import Dancer
from jukebox.gestures import GestureDebouncer, Recognizer
from jukebox.songs import STOP, load_mapping, wav_for
from jukebox import volume as vol

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("jukebox")


class Player:
    """Play/stop songs + manage the dancer (None robot = log only, dev mode)."""

    def __init__(self, robot=None, dance: bool = True, wobble: bool = True):
        self.robot = robot
        self.dance = dance
        self.now_playing: str | None = None
        self.dancer: Dancer | None = None
        # Dance replaces wobbling (both would fight over the head).
        if robot is not None and wobble and not dance:
            try:
                robot.enable_wobbling()
            except Exception as e:
                logger.warning("wobbling unavailable: %s", e)

    def play(self, source_path: str) -> None:
        wav = wav_for(source_path)
        analysis = analyze(wav)
        logger.info("PLAY %s (%.0f BPM)", source_path, analysis["bpm"])
        if self.robot is None:
            return
        self._stop_dancer()
        self.robot.media.stop_playing()

        # Pre-upload so the playback clock starts at the actual play request,
        # not upload start — keeps the dance on the beat.
        remote = str(wav)
        audio = getattr(self.robot.media, "audio", None)
        if audio is not None and hasattr(audio, "upload_sound"):
            try:
                remote = audio.upload_sound(str(wav))
            except Exception as e:
                logger.warning("pre-upload failed, playing directly: %s", e)
        self.robot.media.play_sound(remote)
        start = time.monotonic()
        self.now_playing = source_path

        if self.dance:
            self.dancer = Dancer(self.robot, analysis, start)
            self.dancer.start()

    def stop(self) -> None:
        logger.info("STOP")
        self._stop_dancer()
        if self.robot is not None:
            self.robot.media.stop_playing()
        self.now_playing = None

    def _stop_dancer(self) -> None:
        if self.dancer is not None:
            self.dancer.stop()
            self.dancer = None


def act(player: Player, mapping: dict[str, str], gesture: str) -> None:
    target = mapping.get(gesture)
    if target is None:
        return
    if target == STOP:
        player.stop()
    else:
        player.play(target)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default="robot", help="robot | webcam[:N] | dir:<path>")
    ap.add_argument("--interval", type=float, default=0.25, help="seconds between frames")
    ap.add_argument("--cooldown", type=float, default=3.0, help="min seconds between actions")
    ap.add_argument("--need-frames", type=int, default=3, help="consecutive frames to confirm a sign")
    ap.add_argument("--volume", type=int, metavar="0-100", help="set speaker volume at startup")
    ap.add_argument("--volume-only", action="store_true", help="set volume and exit (with --volume)")
    ap.add_argument("--no-dance", action="store_true", help="disable dancing (falls back to wobble)")
    ap.add_argument("--play", metavar="GESTURE", help="play one gesture's song and exit (no camera)")
    args = ap.parse_args()

    if args.volume is not None:
        applied = vol.set_volume(args.volume)
        logger.info("speaker volume -> %d", applied)
        if args.volume_only:
            return

    mapping = load_mapping()
    logger.info("mapping: %s", {g: t.rsplit('/', 1)[-1] if t != STOP else t for g, t in mapping.items()})

    if args.play:
        src = make_source("robot") if args.source == "robot" else None
        player = Player(src.robot if src else None, dance=not args.no_dance)
        act(player, mapping, args.play)
        if src:
            input("Playing — press Enter to stop and exit.\n")
            player.stop()
            src.close()
        return

    source = make_source(args.source)
    robot = getattr(source, "robot", None)
    player = Player(robot, dance=not args.no_dance)
    recognizer = Recognizer()
    debouncer = GestureDebouncer(need_frames=args.need_frames, cooldown_s=args.cooldown)

    logger.info("watching for hand signs (%s)…  Ctrl-C to quit", args.source)
    try:
        while True:
            frame = source.frame()
            if frame is None:
                if args.source.startswith("dir:"):
                    break
                time.sleep(args.interval)
                continue
            gesture = recognizer.recognize_bgr(frame)
            action = debouncer.feed(gesture, time.monotonic())
            if action:
                act(player, mapping, action)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        player.stop()
        source.close()


if __name__ == "__main__":
    main()
