"""Reachy jukebox: hand signs pick songs, streamed to the robot speaker.

Loop: camera frame -> MediaPipe gesture -> debounce -> play/stop/volume.
Music is streamed over the WebRTC audio channel (jukebox/stream.py) so the
volume signs (Pointing_Up / Pointing_Down) change a software gain instantly,
mid-song. While a song plays, a Dancer thread follows the track's band
envelopes: body moves with the bass, head with the melody (jukebox/dance.py).
"""

from __future__ import annotations

import argparse
import logging
import time

from jukebox.analysis import analyze
from jukebox.capture import make_source
from jukebox.dance import Dancer
from jukebox.gestures import GestureDebouncer, Recognizer
from jukebox.songs import STOP, VOLUME_DOWN, VOLUME_UP, load_mapping, wav_for
from jukebox.stream import StreamPlayer, load_samples
from jukebox import volume as vol

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("jukebox")

GAIN_STEP = 0.15
REPEATABLE = frozenset({"Pointing_Up", "Pointing_Down"})


class Player:
    """Play/stop/volume + manage the dancer (None robot = log only, dev mode)."""

    def __init__(self, robot=None, dance: bool = True, gain: float = 1.0,
                 latency_s: float | None = None):
        self.robot = robot
        self.dance = dance
        self.gain = gain
        self.latency_s = latency_s
        self.now_playing: str | None = None
        self.stream: StreamPlayer | None = None
        self.dancer: Dancer | None = None

    def play(self, source_path: str) -> None:
        wav = wav_for(source_path)
        analysis = analyze(wav)
        logger.info("PLAY %s (%.0f BPM, gain %.2f)", source_path, analysis["bpm"], self.gain)
        if self.robot is None:
            return
        self._teardown()
        kwargs = {} if self.latency_s is None else {"latency_s": self.latency_s}
        self.stream = StreamPlayer(self.robot, load_samples(wav), gain=self.gain, **kwargs)
        self.stream.start()
        self.now_playing = source_path
        if self.dance:
            self.dancer = Dancer(self.robot, analysis, clock=self.stream.song_time)
            self.dancer.start()

    def stop(self) -> None:
        logger.info("STOP")
        self._teardown()
        self.now_playing = None

    def volume_step(self, delta: float) -> float:
        self.gain = min(max(self.gain + delta, 0.0), 1.5)
        if self.stream is not None:
            self.stream.set_gain(self.gain)
        logger.info("gain -> %.2f", self.gain)
        return self.gain

    def _teardown(self) -> None:
        if self.dancer is not None:
            self.dancer.stop()
            self.dancer = None
        if self.stream is not None:
            self.stream.stop()
            self.stream = None


def act(player: Player, mapping: dict[str, str], gesture: str) -> None:
    target = mapping.get(gesture)
    if target is None:
        return
    if target == STOP:
        player.stop()
    elif target == VOLUME_UP:
        player.volume_step(+GAIN_STEP)
    elif target == VOLUME_DOWN:
        player.volume_step(-GAIN_STEP)
    else:
        player.play(target)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default="robot", help="robot | webcam[:N] | dir:<path>")
    ap.add_argument("--interval", type=float, default=0.25, help="seconds between frames")
    ap.add_argument("--cooldown", type=float, default=3.0, help="min seconds between actions")
    ap.add_argument("--need-frames", type=int, default=3, help="consecutive frames to confirm a sign")
    ap.add_argument("--gain", type=float, default=1.0, help="initial software gain (0-1.5)")
    ap.add_argument("--volume", type=int, metavar="0-100", help="set SPEAKER (hardware) volume at startup")
    ap.add_argument("--volume-only", action="store_true", help="set hardware volume and exit (with --volume)")
    ap.add_argument("--latency", type=float, default=None, help="audio output latency for dance sync (s)")
    ap.add_argument("--no-dance", action="store_true", help="disable dancing")
    ap.add_argument("--play", metavar="GESTURE", help="play one gesture's song and exit (no camera)")
    args = ap.parse_args()

    if args.volume is not None:
        applied = vol.set_volume(args.volume)
        logger.info("hardware speaker volume -> %d", applied)
        if args.volume_only:
            return

    mapping = load_mapping()
    logger.info("mapping: %s", {g: t.rsplit('/', 1)[-1] for g, t in mapping.items()})

    if args.play:
        src = make_source("robot") if args.source == "robot" else None
        player = Player(src.robot if src else None, dance=not args.no_dance,
                        gain=args.gain, latency_s=args.latency)
        act(player, mapping, args.play)
        if src:
            input("Playing — press Enter to stop and exit.\n")
            player.stop()
            src.close()
        return

    source = make_source(args.source)
    robot = getattr(source, "robot", None)
    player = Player(robot, dance=not args.no_dance, gain=args.gain, latency_s=args.latency)
    recognizer = Recognizer()
    debouncer = GestureDebouncer(
        need_frames=args.need_frames, cooldown_s=args.cooldown, repeatable=REPEATABLE
    )

    logger.info("watching for hand signs (%s)…  Ctrl-C to quit", args.source)
    try:
        while True:
            frame = source.frame()
            if frame is None:
                if args.source.startswith("dir:"):
                    break
                time.sleep(args.interval)
                continue
            gesture, hand_visible = recognizer.recognize_bgr(frame)
            if hand_visible and player.dancer is not None:
                # Calm the dance so the camera steadies and the sign can be read.
                player.dancer.calm(2.0)
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
