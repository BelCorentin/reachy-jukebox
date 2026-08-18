"""Frame sources: robot camera, laptop webcam, or a directory of images."""

from __future__ import annotations

import os
from pathlib import Path


class RobotSource:
    """Frames from the Reachy Mini camera (BGR numpy via the SDK)."""

    def __init__(self):
        from reachy_mini import ReachyMini

        kwargs = {}
        host = os.getenv("REACHY_HOST")
        if host:
            kwargs["host"] = host
        self.robot = ReachyMini(**kwargs)

    def frame(self):
        return self.robot.media.get_frame()

    def close(self) -> None:
        try:
            self.robot.__exit__(None, None, None)
        except Exception:
            pass


class WebcamSource:
    """Frames from the laptop webcam (dev mode, no robot needed)."""

    def __init__(self, index: int = 0):
        import cv2

        self._cv2 = cv2
        self.cap = cv2.VideoCapture(index)
        if not self.cap.isOpened():
            raise RuntimeError(f"cannot open webcam {index}")

    def frame(self):
        ok, frame = self.cap.read()
        return frame if ok else None

    def close(self) -> None:
        self.cap.release()


class DirSource:
    """Frames from a directory of images, in name order (tests/dev)."""

    def __init__(self, path: str):
        import cv2

        self._cv2 = cv2
        self.files = sorted(
            p for p in Path(path).expanduser().iterdir()
            if p.suffix.lower() in (".jpg", ".jpeg", ".png")
        )
        self._i = 0

    def frame(self):
        if self._i >= len(self.files):
            return None
        frame = self._cv2.imread(str(self.files[self._i]))
        self._i += 1
        return frame

    def close(self) -> None:
        pass


def make_source(spec: str):
    if spec == "robot":
        return RobotSource()
    if spec == "webcam":
        return WebcamSource()
    if spec.startswith("webcam:"):
        return WebcamSource(int(spec.split(":", 1)[1]))
    if spec.startswith("dir:"):
        return DirSource(spec.split(":", 1)[1])
    raise ValueError(f"unknown source {spec!r} (robot | webcam[:N] | dir:<path>)")
