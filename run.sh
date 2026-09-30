#!/usr/bin/env bash
# Reachy jukebox launcher.
# Usage: ./run.sh --setup                 install + pick your songs (once)
#        ./run.sh --setup-songs | --songs | --bind SIGN FILE
#        ./run.sh [--source webcam] [...]  run the jukebox
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ROOT/.venv/bin/python"

if [[ "${1:-}" == "--setup" ]]; then
  uv venv -q "$ROOT/.venv" 2>/dev/null || true
  uv pip install -q -p "$PY" --prerelease=allow reachy-mini mediapipe
  mkdir -p "$ROOT/models"
  if [[ ! -f "$ROOT/models/gesture_recognizer.task" ]]; then
    curl -fSL -o "$ROOT/models/gesture_recognizer.task" \
      "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/latest/gesture_recognizer.task"
  fi
  echo "setup done"
  if [[ ! -f "$ROOT/songs.json" && -t 0 ]]; then
    echo; echo "Now pick the songs Reachy plays for each hand sign:"
    cd "$ROOT" && exec "$PY" -m jukebox.main --setup-songs
  fi
  exit 0
fi

# Song setup commands need neither the robot nor the camera.
case "${1:-}" in
  --songs|--bind|--setup-songs|--help|-h) cd "$ROOT" && exec "$PY" -m jukebox.main "$@" ;;
esac

# Resolve the robot (mDNS goes cold after restarts; fall back to last-known IP).
ROBOT_HOST="${REACHY_HOST:-}"
if [[ -z "$ROBOT_HOST" ]]; then
  ROBOT_HOST="$(getent hosts reachy-mini.local 2>/dev/null | awk '{print $1; exit}' || true)"
fi
if [[ -z "$ROBOT_HOST" && -f "$ROOT/.last_robot_ip" ]]; then
  CAND="$(cat "$ROOT/.last_robot_ip")"
  curl -s -m3 "http://$CAND:8000/api/daemon/status" >/dev/null 2>&1 && ROBOT_HOST="$CAND"
fi

if [[ " $* " != *" --source webcam"* && " $* " != *" dir:"* ]]; then
  if [[ -z "$ROBOT_HOST" ]]; then
    echo "Cannot resolve reachy-mini.local (set REACHY_HOST=<ip>, or use --source webcam)" >&2
    exit 1
  fi
  echo "$ROBOT_HOST" > "$ROOT/.last_robot_ip"
  export REACHY_HOST="$ROBOT_HOST"
  # Daemon quirks (see reachy-memoire Field Log): signalling host + media
  # acquire preflight, and motors boot disabled (wobbling needs them).
  export REACHY_SIGNALLING_HOST="${REACHY_SIGNALLING_HOST:-$ROBOT_HOST}"
  curl -s -m5 -X POST "http://$ROBOT_HOST:8000/api/media/acquire" >/dev/null || true
  curl -s -m5 -X POST "http://$ROBOT_HOST:8000/api/motors/set_mode/enabled" >/dev/null || true
  echo "Robot: $ROBOT_HOST"
fi

cd "$ROOT"
exec "$PY" -m jukebox.main "$@"
