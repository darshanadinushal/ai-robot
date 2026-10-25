# Luna 1.0 — Architecture

Luna is a touchscreen robot "face" written in Python with **pygame**. It targets a
Raspberry Pi 5 with the 7" Touch Display 2 (1280×720, landscape) and is developed on Windows.
It shows animated eyes or a voice-wave, reacts to **offline voice commands** (Vosk), and talks
back with **offline text-to-speech**. An online AI "brain" is planned but not wired into the app yet.

No secrets live in this repository. The only planned secret is `OPENAI_API_KEY`, read from an
environment variable (see "Planned work").

## 1. Repository map

```text
main.py                     Entry point: calls robot_hud.app.main()
robot_hud/
  app.py                    UI, rendering, input, main loop (class RobotHUD)
  commands.py               Pure: spoken words -> actions; actions -> spoken reply
  voice.py                  Mic capture + Vosk recognition in a background thread
  speech.py                 Offline TTS in background threads (Windows / Linux)
  preferences.py            User settings stored as JSON in the home folder
  geometry.py               Pure maths: viewport scaling, blink curve, clamp
  ai.py                     Online AI client (LunaBrain). NOT used by app.py yet
scripts/
  install-pi.sh             Raspberry Pi OS setup (apt, venv, Vosk, model)
  run-pi.sh                 Start fullscreen on the Pi (uses .venv if present)
  download-voice-model.py   Downloads the small Vosk English model
  setup-windows.ps1, run-windows.ps1   (+ SETUP_WINDOWS.bat / RUN_WINDOWS.bat)
  test_ai.py                Typed chat with LunaBrain (needs OPENAI_API_KEY)
tests/
  test_core.py              Unit tests: geometry, preferences, command parsing
  test_visual_smoke.py      Launches the app headless and saves a screenshot
docs/ARCHITECTURE.md        This file
.github/workflows/tests.yml GitHub Actions: runs the tests headless
AGENTS.md                   Rules and quick facts for AI coding assistants
```

## 2. Runtime overview

```text
 microphone ──> VoiceListener (thread) ──texts queue──┐
                 │  level (0..1)                       │
                 └──────────────┐                      v
 touch / mouse / keyboard ──> RobotHUD.events() ──> RobotHUD.handle_touch()
                                      │              RobotHUD.apply_commands()
                                      v                │   │
                              RobotHUD.frame(t)  <─────┘   └─> speech.speak_async(reply)
                                      │                            (thread: espeak-ng / SAPI)
                                      v
                    canvas 1280x720 ──scaled──> window / fullscreen
```

Main loop (`RobotHUD.run`, 30 FPS): `events(t)` → `frame(t)` → `present()`.
`frame()` first calls `poll_voice(t)`, which drains recognised text and calls `apply_commands`.

### Rendering model
- Everything is drawn on a fixed **1280×720 logical canvas** (`CANVAS_WIDTH/HEIGHT` in
  `geometry.py`). `Viewport` letterboxes/scales it to the real screen and maps touch
  coordinates back (`Viewport.to_logical`). Never use raw screen coordinates for UI logic.
- Layout rectangles are module constants in `app.py` (`NAV_RECTS`, `TAB_RECTS`, `COLOR_RECTS`,
  `LAYOUT_RECTS`, `ANIM_RECT`, `CHAT_RECT`, `QUIT_RECT`). Drawing and hit-testing share them,
  so change a rect in one place.
- Colours: theme accent comes from `COLOR_CHOICES` (`preferences.py`); neutral palette
  constants (`BACKGROUND`, `TEXT`, `MUTED`) are at the top of `app.py`.

### Pages (bottom nav, keys `1`–`4`)
`Chat` (simulated waveform demo), `Home` (the robot), `Camera` (placeholder only),
`Settings`. Chat/Camera are placeholders: no audio recording, no camera feed.

### Home page: two modes (`prefs['mode']`)
- **FACE**: two `draw_eye` calls + `smile`. Face layouts live in `LAYOUT_CHOICES`
  (`preferences.py`) as `(eye offset, eye y, eye scale, smile y | None)`.
  Mood (`self.mood`) changes lid cuts and smile curve: `neutral, happy, sad, angry,
  sleepy, surprised`. Gaze follows touch or a slow idle drift.
- **WAVE**: microphone icon + one of `WAVE_LAYOUTS` (`MESH`, `BARS`, `CIRCLE`), drawn by
  `draw_wave`, `draw_wave_bars`, `draw_wave_circle`. Amplitude `self.wave_amp` is smoothed
  toward `max(state base, live mic level)`; state is `listening` / `speaking` / `quiet`.

### Settings page
Two tabs (FACE MODE / WAVE MODE) choose which mode Home shows. Both tabs share colour and
animation toggle; the layout row shows face layouts or wave layouts depending on the tab.
Every change calls `RobotHUD.save()`.

## 3. Voice pipeline

1. `voice.VoiceListener` runs in a daemon thread. It needs the optional packages
   `vosk` and `sounddevice` and the model folder `~/.ai-robot-models/vosk-model-small-en-us-0.15`
   (fetched by `scripts/download-voice-model.py`). If anything is missing it sets
   `status` (shown on screen) and the app keeps working without voice.
2. Recognition is **grammar-restricted** to `commands.VOCABULARY` (+ `[unk]`) for accuracy.
   Consequence: it can only recognise our command words — free-form questions need a
   different recogniser setup (see Planned work).
3. Finished phrases go to `VoiceListener.texts` (a `queue.Queue`); `level` is the mic volume.
4. `commands.parse_command(text)` maps each word through `KEYWORDS` to
   `(kind, value)` actions: `expression`, `look`, `blink`, `wave`, `mode`, `color`.
5. `RobotHUD.apply_commands` applies the actions, then `speech.speak_async(reply_for(actions))`.
   Expressions revert to neutral after 10 s; `speaking` wave state lasts 6 s.

**Echo protection:** `VoiceListener(muted=speech.is_speaking)` drops audio and resets the
recogniser while Luna is talking and for `speech.TAIL_SECONDS` (0.8 s) afterwards.

### Adding a voice command
1. Add the word(s) to `KEYWORDS` in `commands.py` (vocabulary updates automatically).
2. Handle a new `kind` in `RobotHUD.apply_commands` and add a spoken line in `reply_for`.
3. Add a case to `CommandTests` in `tests/test_core.py`.

## 4. Speech output (`speech.py`)
- `speak(message)` is blocking; `speak_async(message)` starts a daemon thread.
- A non-blocking lock means a new request is **dropped** while one is playing.
- Windows: PowerShell + `System.Speech` (text passed through stdin, never through the command
  line). Linux/Pi: `espeak-ng` writes a temp WAV, then `pw-play`, `paplay` or `aplay`.
- `speech.enabled = False` (CLI `--no-speech`) disables all speech.

## 5. Preferences (`preferences.py`)
File: `~/.ai-robot-hud.json` (outside the repo; its name is gitignored).

| Key | Values | Default |
| --- | --- | --- |
| `color` | `CYAN, PURPLE, GREEN, AMBER` | `CYAN` |
| `mode` | `FACE, WAVE` | `FACE` |
| `layout` | `CLASSIC, COMPACT, MINIMAL` | `CLASSIC` |
| `wave_layout` | `MESH, BARS, CIRCLE` | `MESH` |
| `animations` | boolean | `true` |

`read_preferences` validates every field and silently falls back to defaults on any error;
unknown keys are ignored. Adding a setting means: default in `read_preferences`, validation,
`write_preferences`, UI in `settings_page` + `handle_touch`, and updating the tests.

## 6. Command-line options (`main.py`)
`--windowed` (resizable window; default on Windows), `--fullscreen` (default elsewhere),
`--no-voice`, `--no-speech`, `--screenshot FILE` (save first frame and exit).
Keyboard: `1-4` pages, `Space` blink, `F11` fullscreen, `Esc`/`Q` quit.

## 7. Platform notes
- **Windows dev:** `.venv` + `requirements.txt` (pygame, vosk, sounddevice).
- **Raspberry Pi OS:** pygame comes from apt (`python3-pygame`); the venv is created with
  `--system-site-packages` so it can still import it. Needs `libportaudio2`, `espeak-ng`,
  and a PipeWire/Pulse/ALSA player. Run from the desktop session (or `DISPLAY=:0` over SSH).
- Headless tests/CI: `SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy`.

## 8. Testing
`python -m unittest discover -s tests -v`. `test_core.py` is pure Python (no display).
`test_visual_smoke.py` renders one frame headless. Visual changes are best checked by
rendering to PNG (`--screenshot`) or by building `RobotHUD` with a dummy driver and saving
`canvas` — pass a namespace with `fullscreen, windowed, no_voice, no_speech`.

## 9. Design decisions worth knowing
- **Offline first:** recognition, commands, expressions and speech work with no internet.
  Only the future AI conversation needs the network, and only recognised *text* would be sent.
- **Optional heavy dependencies:** voice and AI imports are lazy/guarded so the face always starts.
- **Pure modules** (`commands.py`, `geometry.py`, `preferences.py`) have no pygame import and
  are the easiest place to add tested logic.
- **One window, one canvas:** simple to scale for any screen; no widgets library.
- Threads never touch pygame; they communicate through a queue, a float (`level`) and a status string.

## 10. Planned work (not implemented)
1. **AI conversation:** `robot_hud/ai.py` (`LunaBrain`, OpenAI Responses API with
   `previous_response_id` history) exists and is exercised only by `scripts/test_ai.py`.
   Needs: unrestricted/wake-word recognition for open questions, a call from the app, and
   showing/speaking the answer. Key via `OPENAI_API_KEY` environment variable only.
2. **Settings tab to pick the AI model** (saved as a preference).
3. **Autostart on Pi boot** (desktop autostart entry + auto-login).
4. Real camera, motors, smart-home control (UI placeholders exist).

## 11. Known gaps
- Wi-Fi, audio and battery icons are decorative, not live readings.
- Speech requests arriving during playback are dropped rather than queued.
