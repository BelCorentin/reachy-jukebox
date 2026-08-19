"""Hand-gesture recognition (MediaPipe) + debouncing.

The recognizer wraps MediaPipe's stock GestureRecognizer task (pretrained
classes: Thumb_Up, Thumb_Down, Victory, ILoveYou, Open_Palm, Closed_Fist,
Pointing_Up). "Pointing_Down" is NOT a stock class — it's derived from the
hand landmarks: index finger extended downward, other fingers curled.

The debouncer is pure logic, unit-tested separately: a gesture fires after
N consecutive frames agree, with a cooldown and a require-release rule —
except gestures marked *repeatable* (volume), which re-fire while held.
"""

from __future__ import annotations

from pathlib import Path

MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "gesture_recognizer.task"
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/"
    "gesture_recognizer/float16/latest/gesture_recognizer.task"
)

# MediaPipe hand-landmark indices
WRIST = 0
INDEX_MCP, INDEX_PIP, INDEX_TIP = 5, 6, 8
MIDDLE_MCP, MIDDLE_TIP = 9, 12
RING_MCP, RING_TIP = 13, 16
PINKY_MCP, PINKY_TIP = 17, 20


def detect_pointing_down(landmarks) -> bool:
    """Index finger extended downward, other fingers curled.

    landmarks: sequence of 21 points with .x/.y in normalized image coords
    (y grows DOWNWARD in image space).
    """
    if not landmarks or len(landmarks) < 21:
        return False
    lm = landmarks

    def dy(a: int, b: int) -> float:
        return lm[a].y - lm[b].y

    def dist(a: int, b: int) -> float:
        return ((lm[a].x - lm[b].x) ** 2 + (lm[a].y - lm[b].y) ** 2) ** 0.5

    index_down = dy(INDEX_TIP, INDEX_MCP) > 0.10 and dy(INDEX_TIP, INDEX_PIP) > 0.03
    index_straight = abs(lm[INDEX_TIP].x - lm[INDEX_MCP].x) < 0.6 * dist(INDEX_TIP, INDEX_MCP)
    others_curled = all(
        dist(tip, WRIST) < 0.8 * dist(INDEX_TIP, WRIST)
        for tip in (MIDDLE_TIP, RING_TIP, PINKY_TIP)
    )
    return index_down and index_straight and others_curled


class GestureDebouncer:
    """Turn a noisy per-frame gesture stream into discrete actions."""

    def __init__(self, need_frames: int = 3, cooldown_s: float = 3.0,
                 repeatable: frozenset[str] = frozenset(), repeat_s: float = 0.8):
        self.need_frames = need_frames
        self.cooldown_s = cooldown_s
        self.repeatable = repeatable
        self.repeat_s = repeat_s
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

        repeat = gesture in self.repeatable
        wait = self.repeat_s if repeat else self.cooldown_s
        if now - self._last_fire_t < wait:
            return None
        if not repeat and gesture == self._last_fired and not self._released:
            return None  # still holding the same sign since it fired

        self._last_fired = gesture
        self._last_fire_t = now
        self._released = False
        return gesture


class Recognizer:
    """MediaPipe GestureRecognizer + the landmark-based Pointing_Down."""

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

        landmarks = result.hand_landmarks[0] if result.hand_landmarks else None

        name = None
        if result.gestures and result.gestures[0]:
            top = result.gestures[0][0]
            if top.score >= self.min_score and top.category_name not in ("", "None"):
                name = top.category_name

        # The stock model has no Pointing_Down; derive it from landmarks when
        # the classifier found nothing (it labels a down-point as None).
        if name is None and landmarks is not None and detect_pointing_down(landmarks):
            name = "Pointing_Down"
        return name
