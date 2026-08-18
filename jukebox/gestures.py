"""Hand-gesture recognition (MediaPipe) + debouncing.

The recognizer wraps MediaPipe's stock GestureRecognizer task (pretrained
classes: Thumb_Up, Thumb_Down, Victory, ILoveYou, Open_Palm, Closed_Fist,
Pointing_Up). The debouncer is pure logic, unit-tested separately: a gesture
fires only after N consecutive frames agree, with a cooldown between actions
and a require-release rule so holding a thumb up doesn't restart the song.
"""

from __future__ import annotations

from pathlib import Path

MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "gesture_recognizer.task"
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/"
    "gesture_recognizer/float16/latest/gesture_recognizer.task"
)


class GestureDebouncer:
    """Turn a noisy per-frame gesture stream into discrete actions."""

    def __init__(self, need_frames: int = 3, cooldown_s: float = 3.0):
        self.need_frames = need_frames
        self.cooldown_s = cooldown_s
        self._candidate: str | None = None
        self._count = 0
        self._last_fired: str | None = None
        self._last_fire_t = float("-inf")
        self._released = True

    def feed(self, gesture: str | None, now: float) -> str | None:
        """Feed one frame's gesture (or None); return an action name or None."""
        if gesture is None or gesture == "None":
            self._candidate = None
            self._count = 0
            self._released = True
            return None

        if gesture == self._candidate:
            self._count += 1
        else:
            self._candidate = gesture
            self._count = 1

        if self._count < self.need_frames:
            return None
        if now - self._last_fire_t < self.cooldown_s:
            return None
        if gesture == self._last_fired and not self._released:
            return None  # still holding the same sign since it fired

        self._last_fired = gesture
        self._last_fire_t = now
        self._released = False
        return gesture


class Recognizer:
    """Thin wrapper around MediaPipe's GestureRecognizer (image mode)."""

    def __init__(self, model_path: Path = MODEL_PATH, min_score: float = 0.5):
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        if not model_path.is_file():
            raise FileNotFoundError(
                f"gesture model missing: {model_path}\nDownload it with: ./run.sh --setup"
            )
        self._mp = mp
        self.min_score = min_score
        options = vision.GestureRecognizerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            num_hands=1,
        )
        self._recognizer = vision.GestureRecognizer.create_from_options(options)

    def recognize_bgr(self, frame_bgr) -> str | None:
        """Return the top gesture name for a BGR numpy frame, or None."""
        import cv2

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._recognizer.recognize(image)
        if not result.gestures or not result.gestures[0]:
            return None
        top = result.gestures[0][0]
        if top.score < self.min_score or top.category_name in ("", "None"):
            return None
        return top.category_name
