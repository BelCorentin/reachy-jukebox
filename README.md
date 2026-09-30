# reachy-jukebox

Show Reachy Mini a hand sign and it plays one of **your** songs on its speaker,
and dances to it: the body follows the bass, the head follows the melody.
✊ stops the music, ☝️ / 👇 turn it up or down.

## How it works

Camera frame → MediaPipe's stock `GestureRecognizer` (pretrained, no training)
→ debounce (a few consecutive frames + cooldown + release before re-firing) →
the song is streamed to the robot speaker while a dance thread follows it.

Songs can be any format ffmpeg reads. They stay where they are on your disk
and are converted once into `cache/`.

## Setup (once)

Needs [`uv`](https://docs.astral.sh/uv/) and `ffmpeg`.

```bash
./run.sh --setup
```

This installs the dependencies, downloads the gesture model (~8 MB), then asks
you which song each sign should play. For each sign, type part of a song name,
pick it from the list, or press Enter to skip:

```
Music folder [~/Music]:
Found 412 songs in /home/you/Music.

👍 Thumb_Up (now: nothing) — part of a song name, Enter = keep, '-' = remove: tango
   1. Libertango.mp3
   2. Tango de Roxanne.flac
  number (Enter = search again): 1
  ✓ 👍 → Libertango.mp3
```

Change your songs any time:

```bash
./run.sh --setup-songs [FOLDER]          # the same walk-through again
./run.sh --bind Victory ~/Music/song.mp3 # one sign
./run.sh --songs                         # what each sign plays
```

Your choices are saved in `songs.json`, which stays out of git. See
`songs.example.json` for the format.

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

## The signs

| Sign | Gesture name | Does |
|---|---|---|
| 👍 | `Thumb_Up` | your song |
| 👎 | `Thumb_Down` | your song |
| ✌️ | `Victory` | your song |
| 🤟 | `ILoveYou` | your song |
| ✋ | `Open_Palm` | your song |
| ✊ | `Closed_Fist` | **stop** |
| ☝️ | `Pointing_Up` | **volume up** (hold to repeat) |
| 👇 | `Pointing_Down` | **volume down** (hold to repeat) |

My own set, for the record: Tamacun (Rodrigo y Gabriela), Beethoven's 7th
Allegretto, Piazzolla's Libertango, Asaf Avidan's *Love it or Leave it*, and
Hisaishi's *Path of the Wind*. Libertango shows the two-layer dance best.

## Robot notes (shared with reachy-memoire)

`run.sh` handles the known daemon quirks: it falls back to the last known IP
when mDNS is slow, does the media-acquire preflight, and enables the motors
(they boot disabled, and the dance needs them). Don't run at the same time
as the conversation app — its mic will hear the music and the model will
start reviewing your taste.

## Tests

```bash
.venv/bin/python tests/test_jukebox.py   # 51 checks, no robot/camera needed
```
