"""Reachy jukebox: hand signs pick songs, played on the robot speaker.

Loop: camera frame -> MediaPipe gesture -> debounce -> play/stop on the
daemon speaker (session-independent play_sound), with head wobbling so the
robot grooves to the music.
"""

from __future__ import annotations

import argparse
import logging
import time

from jukebox.capture import make_source
from jukebox.gestures import GestureDebouncer, Recognizer
from jukebox.songs import STOP, load_mapping, wav_for

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("jukebox")


class Player:
    """Play/stop songs on the robot speaker (None robot = log only, dev mode)."""

    def __init__(self, robot=None, wobble: bool = True):
        self.robot = robot
        self.now_playing: str | None = None
        if robot is not None and wobble:
            try:
                robot.enable_wobbling()
            except Exception as e:
                logger.warning("wobbling unavailable: %s", e)

    def play(self, source_path: str) -> None:
        wav = wav_for(source_path)
        logger.info("PLAY %s", source_path)
        if self.robot is None:
            return
        self.robot.media.stop_playing()
        self.robot.media.play_sound(str(wav))
        self.now_playing = source_path

    def stop(self) -> None:
        logger.info("STOP")
        if self.robot is not None:
            self.robot.media.stop_playing()
        self.now_playing = None


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
    ap.add_argument("--no-wobble", action="store_true")
    ap.add_argument("--play", metavar="GESTURE", help="play one gesture's song and exit (no camera)")
    args = ap.parse_args()

    mapping = load_mapping()
    logger.info("mapping: %s", {g: t.rsplit('/', 1)[-1] if t != STOP else t for g, t in mapping.items()})

    if args.play:
        src = make_source("robot") if args.source == "robot" else None
        player = Player(src.robot if src else None, wobble=not args.no_wobble)
        act(player, mapping, args.play)
        if src:
            input("Playing — press Enter to stop and exit.\n")
            player.stop()
            src.close()
        return

    source = make_source(args.source)
    robot = getattr(source, "robot", None)
    player = Player(robot, wobble=not args.no_wobble)
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
