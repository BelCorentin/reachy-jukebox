# reachy-jukebox

Show Reachy Mini a hand sign, it plays a song on its speaker — and grooves
(head wobbling follows the audio). Thumbs up = upbeat, thumbs down = Beethoven.

## How it works

Camera frame → MediaPipe stock `GestureRecognizer` (pretrained, no training)
→ debounce (N consecutive frames + cooldown + release-before-refire) →
`media.play_sound()` on the robot speaker via the daemon REST API
(session-independent — no conversation app involved). `Closed_Fist` stops.

Songs are any format ffmpeg reads; they're converted once to mono 44.1 kHz
s16 WAV and cached in `cache/` (keyed by path+mtime).

## Setup (once)

```bash
./run.sh --setup   # venv + reachy-mini (prerelease) + mediapipe + gesture model (~8 MB)
```

Needs `ffmpeg` on PATH.

## Use

```bash
./run.sh                    # robot camera + robot speaker
./run.sh --source webcam    # laptop webcam, log-only playback (dev mode)
./run.sh --play Thumb_Up    # skip gestures, just play that song on the robot
```

Hold a sign steady ~1 s within ~2 m of the camera, decent light.

## Bindings (`songs.json`)

| Sign | Gesture name | Song |
|---|---|---|
| 👍 | `Thumb_Up` | Rodrigo y Gabriela — Tamacun |
| 👎 | `Thumb_Down` | Beethoven — Symphony No. 7, Allegretto |
| ✌️ | `Victory` | Astor Piazzolla — Libertango |
| 🤟 | `ILoveYou` | Asaf Avidan — Love it or Leave it |
| ✋ | `Open_Palm` | Joe Hisaishi — Path of the Wind |
| ✊ | `Closed_Fist` | **stop** |

Edit `songs.json` to rebind (values = audio path or `"STOP"`). Available
gesture names: `Thumb_Up, Thumb_Down, Victory, ILoveYou, Open_Palm,
Closed_Fist, Pointing_Up`. Audio files stay where they are (`~/Music/...`);
nothing is copied into the repo.

## Robot notes (shared with reachy-memoire)

`run.sh` handles the known daemon quirks: mDNS-cold fallback to the last
known IP, `REACHY_SIGNALLING_HOST`, media-acquire preflight, and enabling
motors (they boot disabled; wobbling needs them). Don't run at the same time
as the conversation app — its mic will hear the music and the model will
start reviewing your taste.

## Tests

```bash
.venv/bin/python tests/test_jukebox.py   # 19 checks, no robot/camera needed
```
