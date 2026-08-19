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
./run.sh                        # robot camera + robot speaker
./run.sh --gain 0.7             # initial software volume (0-1.5)
./run.sh --volume 70            # set HARDWARE speaker volume (0-100) at startup
./run.sh --source webcam        # laptop webcam, log-only playback (dev mode)
./run.sh --play Victory         # skip gestures, just play that song on the robot
./run.sh --no-dance             # music without choreography
```

Hold a sign steady ~1 s within ~2 m of the camera, decent light.

**Volume during playback**: ☝️ `Pointing_Up` = louder, point DOWN
(index finger down, other fingers curled — landmark-derived, not a stock
MediaPipe class) = quieter. Hold the sign to keep stepping (repeats ~every
0.8 s). This adjusts a software gain on the audio stream, so it's instant
and never interrupts the song. (The hardware `--volume` endpoint can't be
used mid-song: the daemon plays a test chirp that kills the current sound.)

## Streaming, not file playback

Music is streamed over the WebRTC audio channel (`push_audio_sample`, the
same path the conversation app speaks through) at 16 kHz, ~0.4 s ahead of
real time. That's what makes live gain possible, starts playback instantly
(no multi-MB upload first), and gives the dance a sample-exact clock.

## Dancing — body follows the bass, head follows the melody

Each song is analysed once (pure numpy): BPM by onset autocorrelation, plus
three 20 Hz envelopes of the waveform — broadband energy, **low band
40–250 Hz** (rhythm section) and **high band 1–4 kHz** (melody, voice,
bandoneon) — cached as a `.dance.json` sidecar. A 20 Hz thread replays them
against the stream clock:

- **BODY ← low band**: tango-like circle — body yaw sweeping over the bar,
  head base tracing a small x/y circle, z pulse on each beat. Moves only
  when the rhythm section actually plays.
- **HEAD ← high band**: pitch nods on the beat + roll wiggle at double time,
  scaled by the melody envelope and accented on its transients — accordion
  runs visibly ride on top of the body motion, decorrelated from it.
- Tempo sets the base pace: ≥115 BPM sways on half notes, <85 BPM on whole
  bars (Libertango gets both layers doing different things — that's the
  point).

## Bindings (`songs.json`)

| Sign | Gesture name | Song |
|---|---|---|
| 👍 | `Thumb_Up` | Rodrigo y Gabriela — Tamacun |
| 👎 | `Thumb_Down` | Beethoven — Symphony No. 7, Allegretto |
| ✌️ | `Victory` | Astor Piazzolla — Libertango |
| 🤟 | `ILoveYou` | Asaf Avidan — Love it or Leave it |
| ✋ | `Open_Palm` | Joe Hisaishi — Path of the Wind |
| ✊ | `Closed_Fist` | **stop** |
| ☝️ | `Pointing_Up` | **volume up** (hold to repeat) |
| 👇 | `Pointing_Down` | **volume down** (hold to repeat) |

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
