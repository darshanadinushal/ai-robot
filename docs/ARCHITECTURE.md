# Luna 1.0 — Architecture

Luna is a touchscreen robot "face" written in Python with **pygame**. It targets a
Raspberry Pi 5 with the 7" Touch Display 2 (1280×720, landscape) and is developed on Windows.
It shows animated eyes or a voice-wave, reacts to **offline voice commands** (Vosk), and talks
back with **offline text-to-speech**. With an OpenAI key it also holds **"Hey Luna" conversations**
through an online AI "brain"; everything else keeps working offline.

No secrets live in this repository. The only secret is `OPENAI_API_KEY`, read from the
environment or from `~/.luna.env` outside the repo (see section 5a).

## 1. Repository map

```text
main.py                     Entry point: calls robot_hud.app.main()
robot_hud/
  app.py                    UI, rendering, input, main loop (class RobotHUD)
  commands.py               Pure: spoken words -> actions; actions -> spoken reply (from the packs)
  languages/                Language packs: en.json, sv.json (+ loader in __init__.py)
  voice.py                  Mic capture + Vosk recognition in a background thread
  speech.py                 Offline TTS: sentence pipeline, Piper natural voice, basic fallback
  voices.py                 Loads voices.json (the voice catalogue) and model paths
  voices.json               Voice catalogue: Piper model, eSpeak voice, files with SHA-256
  settings.py               Settings page model: sections and applying choices
  i18n.py                   tr(): screen text in the app language (from the packs)
  preferences.py            User settings stored as JSON in the home folder
  geometry.py               Pure maths: viewport scaling, blink curve, clamp
  ai.py                     Online AI: env loading, LunaBrain (OpenAI), AIWorker thread
scripts/
  install-pi.sh             Raspberry Pi OS setup (apt, venv, Vosk, model)
  run-pi.sh                 Start fullscreen on the Pi (uses .venv if present)
  download-voice-model.py   Downloads the small Vosk models (en, sv, ...: from the packs)
  setup-windows.ps1, run-windows.ps1   (+ SETUP_WINDOWS.bat / RUN_WINDOWS.bat)
  test_ai.py                Typed chat with LunaBrain (needs OPENAI_API_KEY)
tests/
  test_core.py              Unit tests: geometry, preferences, commands, routing, speech text, AIWorker
  test_streaming.py         Phase 4: sentence splitter, AI stream, cancel, history, errors, live answer text
  test_language_switch.py   Phase 4.1: "Luna, speak Swedish": matcher, switch ears (fake Vosk), app flow
  test_speech.py            Speech engine tests with fake voices/player (no audio)
  test_settings.py          Settings choices and taps on a headless screen
  test_swedish.py           Swedish listening, commands, replies and AI language
  test_languages.py         Every language pack and voice is complete; partial packs fall back
  test_visual_smoke.py      Launches the app headless and saves a screenshot
docs/ARCHITECTURE.md        This file
.github/workflows/tests.yml GitHub Actions: runs the tests headless
AGENTS.md                   Rules and quick facts for AI coding assistants
```

## 2. Runtime overview

```text
 microphone ──> VoiceListener (thread) ──texts queue──> RobotHUD.handle_speech()
                 │  level, partial                         │ commands.route()
                 v                                         ├─ command ─> apply_actions() ─┐
 touch / mouse / keyboard ──> RobotHUD.events()            ├─ wake ────> "Yes?" ──────────┤
                                      │                    └─ ai ──────> ask_ai()          │
                                      v                                  │ AIWorker (thread)
                              RobotHUD.frame(t)                          │ OpenAI Responses API
                                      │            answers queue <───────┘                 │
                                      v                    │                               v
                    canvas 1280x720 ──scaled──>    poll_voice() ──> speech.speak_async() (queue worker)
```

Main loop (`RobotHUD.run`, 30 FPS): `events(t)` → `frame(t)` → `present()`.
`frame()` first calls `poll_voice(t)`, which drains recognised text (→ `handle_speech`) and
AI answers (→ show + speak), and opens the follow-up window when speech ends.

### Rendering model
- Everything is drawn on a fixed **1280×720 logical canvas** (`CANVAS_WIDTH/HEIGHT` in
  `geometry.py`). `Viewport` letterboxes/scales it to the real screen and maps touch
  coordinates back (`Viewport.to_logical`). Never use raw screen coordinates for UI logic.
- Layout rectangles are module constants in `app.py` (`NAV_RECTS`, `CHAT_RECT`, `QUIT_RECT`,
  and for Settings `SIDEBAR_RECT`, `SECTION_RECTS`, `CONTENT_RECT`, `CONTENT_CLIP`). Drawing and
  hit-testing share them, so change a rect in one place.
- Colours: theme accent comes from `COLOR_CHOICES` (`preferences.py`); neutral palette
  constants (`BACKGROUND`, `TEXT`, `MUTED`) are at the top of `app.py`.

### Pages (bottom nav, keys `1`–`4`)
`Chat` (simulated waveform demo), `Home` (the robot), `Camera` (placeholder only),
`Settings`. Chat/Camera are placeholders: no audio recording, no camera feed.

### Home page: two modes (`prefs['mode']`)
- **FACE**: two `draw_eye` calls + `smile`. Face layouts live in `LAYOUT_CHOICES`
  (`preferences.py`) as `(eye offset, eye y, eye scale, smile y | None)`.
  Mood (`self.mood`) changes lid cuts and smile curve: `neutral, happy, sad, angry,
  sleepy, surprised`, plus `thinking` (pulsing dots, gaze up-right) while the AI works.
  `smile(..., talking=True)` animates an open mouth while speech plays. Gaze follows touch
  or a slow idle drift.
- **WAVE**: microphone icon + one of `WAVE_LAYOUTS` (`MESH`, `BARS`, `CIRCLE`), drawn by
  `draw_wave`, `draw_wave_bars`, `draw_wave_circle`. Amplitude `self.wave_amp` is smoothed
  toward `max(state base, live mic level)`; state is `listening` / `speaking` / `quiet`, and is
  overridden by `thinking` (AI busy) or `speaking` (speech playing).
- **Transcript / footer / caption:** `draw_you` draws a "YOU:" bubble (top in Face mode, above
  the footer in Wave mode) with live `voice.partial` text (pulsing border) or the last heard
  phrase; ignored phrases are dimmed with a "say Hey Luna first" note. `draw_footer` shows
  "LUNA IS THINKING..." or "LUNA: answer" (2 lines, while talking and 20 s after), else a hint.
  `draw_caption` (top left) shows THINKING / follow-up / mic status plus `AI: ON|OFF`.

### Settings page
A section menu on the left (Display & Theme, Language, Voice, Animations, About) works like
tabs: the panel on the right shows **only the selected section** (`self.settings_section`;
Up/Down keys change it). The section list and what each tap changes live in `settings.py`
(no pygame, unit-tested).
- `settings_page(...)` draws the selected section and **returns the tap targets**
  `[(rect, action)]`; `handle_touch` uses the ones from the last frame, so only visible
  controls can be tapped.
- Display & Theme: FACE / WAVE mode (which one Home shows), colour, and face or wave layouts.
- Language (ENGLISH / SVENSKA) changes only the screen text on every page and saves
  `language`. Each on-screen text goes through `i18n.tr()`, which looks it up in the app
  language's pack (`screen`); a test fails if a `tr('...')` text in `app.py` is missing from a
  pack. The buttons come from the packs and wrap onto more rows after four.
  Listening, spoken replies and AI answers follow the voice language instead.
- Voice: Voice language (one button per language in `voices.json`) and Voice type (one button
  per voice of that language, `voices.voices_for`; FEMALE / MALE, plus the voice name when a
  language has two of a type) pick a catalogue voice, call `speech.set_voice` and save `voice`.
  The voice's language is also `RobotHUD.talk_language`: the listener switches model
  (`VoiceListener.set_language`), and replies and AI answers use that language. Commands are
  understood in the language actually heard (`RobotHUD.heard_language`, English when that
  language's model is missing). A "Listening" row shows the model in use (`listening_status`).
- **Play sample** stops current speech and waits until the voice has loaded
  (`preview` = `waiting` → `playing`), then speaks `voices.sample_text` in that language.
- Status shows `speech.voice_state()` with a dot: green natural, amber loading, red basic.
- Every change goes through `settings.apply_choice` and calls `RobotHUD.save()` only when
  something changed. The sci-fi frame is not drawn behind Settings.

## 3. Voice pipeline

1. `voice.VoiceListener` runs in a daemon thread. It needs the optional packages
   `vosk` and `sounddevice` and a model folder in `~/.ai-robot-models` per language
   (`voice.MODELS`, from each pack's `listening_model`, fetched by
   `scripts/download-voice-model.py en sv`). `set_language` switches the model inside the
   listening thread (status `loading`), so the screen never waits. If a language's model is
   missing it keeps English (`active_language`). If nothing is installed it sets `status` (shown on
   screen) and the app keeps working without voice.
2. Recognition mode: **grammar-restricted** to `commands.vocabulary(language)` (+ `[unk]`) when
   AI is off (most accurate for commands), **free-form** (`VoiceListener(free_form=True)`) when AI
   is on. The Swedish model has a fixed graph, so it always recognises free speech.
3. Finished phrases go to `VoiceListener.texts` (a `queue.Queue`); `level` is the mic volume and
   `partial` the in-progress text.
4. **Language switch first** (phase 4.1): `commands.language_switch(text, language,
   conversation)` returns a target language for "Luna, can you speak Swedish?", "Hej Luna, byt
   till engelska" etc. (see *Voice language switching* below). Only otherwise:
   `commands.route(text, conversation)` decides:
   - `("command", actions)`: the phrase has actions and at most 4 words ("look left", "happy").
   - `("wake", "")`: only the wake word ("luna"; misheard variants lunar/loona/looner accepted).
     Each pack lists its `wake_words`: the Swedish model has no "luna", so sv.json lists what it
     hears instead (lugn, lugna, lund, lunch, helene…; found by feeding it Piper's Swedish voices).
   - `("ai", question)`: wake word plus more words (leading hey/hi/hello/ok are stripped), or any
     longer phrase during the 8 s follow-up window (`conversation=True`).
   - `("ignore", "")`: background talk without the wake word.
5. `commands.parse_command(text, language)` maps each word through the pack's `commands`
   (word → action ID such as `look.left`, see `commands.ACTIONS`) to `(kind, value)` actions:
   `expression`, `look`, `blink`, `wave`, `mode`, `color`. `RobotHUD.apply_actions` applies them
   and speaks `reply_for(actions, language)` (the pack's `replies`). Other fixed sentences ("Yes?",
   AI errors) go through `commands.spoken` (the pack's `speech`); the AI gets the pack's
   `ai_instruction` ("Always answer in Swedish."). Expressions revert after 10 s; `speaking` wave
   state lasts 6 s.

**Echo protection:** `VoiceListener(muted=RobotHUD.mic_muted)` drops audio and resets the
recogniser while Luna is talking, for `speech.TAIL_SECONDS` (0.8 s) afterwards, and while an AI
answer is still streaming (so she stays deaf in the gaps between streamed sentences).

### Voice language switching (phase 4.1)
- **Phrases** live in each pack: `switch_phrases` (target code → phrases *in that pack's
  language*), `switch_extra_words` and `switch_confirmation` (said in the new language).
  `language_switch` needs the wake word (or the follow-up window) and a phrase surrounded only
  by wake words, greetings and extra words, so "Luna, how do you say hello in Swedish?" or
  "Luna, don't speak Swedish" go on to the AI / are ignored. It runs before commands and AI
  ("speak" is also a command word). The Swedish model often hears "byt" as "bit"/"by", so
  sv.json lists those spellings too.
- **Cross-language:** while Luna listens in Swedish, the Swedish model cannot understand
  "Luna, can you speak English?". `VoiceListener` therefore runs a `SwitchEar` for each other
  installed language (`LUNA_SWITCH_EARS`, default 1; `0` turns it off): a recogniser on the same
  audio whose grammar holds only the switch words (`commands.switch_vocabulary`) plus `[unk]`.
  Results with `[unk]` are rejected. The Swedish model has no runtime grammar, so its ear
  hears freely, and only `language_switch` filters it.
- **Cost on the Pi:** the ears use the main recogniser's model cache (switching language
  reuses the loaded model, unused models are freed), so both listening models stay in memory.
  They load in a background thread and only get audio while someone speaks (level >=
  `VOICE_LEVEL`, plus 1 s), then `FinalResult()`. Measured on the laptop: about 0.1-0.2 s of CPU
  per 5 s of speech, against about 0.8 s for the English free-form main recogniser.
- **One answer per sentence:** if an ear is hearing the wake word when the main recogniser
  finishes a sentence, that sentence is held (at most `HOLD_SECONDS`, 1.5 s). If the ear
  recognises a switch, the held or later main result of the same sentence is dropped and
  `(target, heard language, text)` goes to `VoiceListener.switches`.
- **App:** `RobotHUD.voice_switch(target)` stops speech and any AI stream silently
  (`interrupt_answer`), then calls `settings_choice('voice_language', target)`, the same path
  as the Settings button. That keeps the voice type, saves the preference, loads the voice and
  switches the microphone. It does not change the screen language. The pack's
  `switch_confirmation` is spoken once `speech.voice_state()` is no longer `loading`; the mic
  stays muted until then. If the target has no voice, or its listening model is missing
  (`voice.listening_installed`), Luna keeps her language and says
  `commands.CANNOT_SWITCH` ("Sorry, I cannot understand Swedish yet."). Asking for the current
  language just confirms. A tap on Home cancels a pending confirmation.

### Adding a voice command
1. New word for an existing action: add it to `commands` in each pack (`languages/<code>.json`).
2. New action: add its ID to `commands.ACTIONS`, a word and a reply for it in **every** pack
   (`test_languages` checks this), and handle a new `kind` in `RobotHUD.apply_actions`.
3. Add a case to `CommandTests` in `tests/test_core.py`.

### Adding a language (no code changes)
1. Copy `robot_hud/languages/en.json` to `<code>.json` (e.g. `de.json`) and translate it:
   `name`, `native_name`, `screen` (English text → translation), `commands`, `wake_words`,
   `greetings`, `replies`, `speech`, `ai_instruction`, `sample`, `warm_up`, `windows_culture`,
   `switch_phrases` (for every language), `switch_extra_words`, `switch_confirmation`.
   Also add phrases for the new language to `switch_phrases` in **every** other pack
   ("speak german", "byt till tyska"); `test_language_switch` checks this.
2. Set `listening_model` (and `listening_model_mb`) to a small Vosk model from
   alphacephei.com/vosk/models. Find the wake words by testing what that model hears for "Luna".
3. Add at least one voice of the language to `voices.json` (below), then run the tests.
A pack may start partial: missing screen texts, replies and sentences are English, and without
a `listening_model` Luna listens in English. The tests list what is still missing.

### Adding or replacing a voice (no code changes)
Add an entry to `robot_hud/voices.json`: `key` (e.g. `DE_FEMALE`), `language` (a pack code),
`gender`, `name`, `label`, Piper `model`, a basic `espeak` voice, licence notes, and the two
files (`<model>.onnx`, `<model>.onnx.json`) with their path below `download_base`, size and
SHA-256. robot-setup reads the same file, so Install/Repair downloads and checks the new voice.

## 4. Speech output (`speech.py`)
Design: `docs/VOICE_DESIGN.md`. Voices are listed in `voices.json` (loaded by `voices.py`).
- `speak_async(message)` cleans the text (`clean_for_speech`: no markdown, max 700 chars) and
  splits it into sentences (`split_sentences`: short pieces joined, long ones cut at 250 chars).
- Two daemon threads: the **preparer** turns sentences into temp WAV files and stays at most
  two sentences ahead; the **player** plays them in order. So the next sentence is ready while
  one plays. Nothing runs on the pygame thread.
- **Natural voice:** `PiperSynth` loads a Piper model once (Python API) and keeps it in memory.
  **Basic voice:** `BasicSynth` = `espeak-ng` (Pi, 150 WPM) or Windows `System.Speech`, same
  language and gender. Text goes through stdin, never through the command line.
- `set_voice(key)` loads the voice in a background thread (newest request wins) and warms it
  up; Luna keeps talking with the old voice meanwhile and switches only after success. On
  failure she stays on the basic voice. `voice_state()` → (`natural`/`loading`/`basic`,
  status text); the caption shows it and Settings shows the reason. If Piper fails while
  speaking, that sentence is retried with the basic voice.
- `stop()` is immediate: bumps a generation counter, kills the playing sound, drops prepared
  and pending sentences (tap Home or press `S`). Playback starts under the same lock, so a
  stale sentence never starts after a stop.
- Players: `pw-play`, `paplay`, `aplay` (first found) or `LUNA_AUDIO_PLAYER`; Windows uses `winsound`.
- `is_talking()` = a message is still being prepared or played; `is_speaking()` also covers the
  0.8 s tail and is used for mic muting. `speech.enabled = False` (`--no-speech`) disables all speech.

## 5. Preferences (`preferences.py`)
File: `~/.ai-robot-hud.json` (outside the repo; its name is gitignored).

| Key | Values | Default |
| --- | --- | --- |
| `color` | `CYAN, PURPLE, GREEN, AMBER` | `CYAN` |
| `mode` | `FACE, WAVE` | `FACE` |
| `layout` | `CLASSIC, COMPACT, MINIMAL` | `CLASSIC` |
| `wave_layout` | `MESH, BARS, CIRCLE` | `MESH` |
| `animations` | boolean | `true` |
| `voice` | a key in `voices.json`: `EN_FEMALE, EN_MALE, SV_FEMALE, SV_MALE` | `EN_FEMALE` |
| `language` | a language pack code: `en, sv` — screen text (`i18n.LANGUAGES`) | `en` |

`read_preferences` validates every field and silently falls back to defaults on any error;
unknown keys are ignored. Adding a setting means: default in `read_preferences`, validation,
`write_preferences`, a choice in `settings.apply_choice`, a control in `settings_page`, and updating the tests.

## 5a. AI conversation (`ai.py`)
- `ai_configured()` loads `~/.luna.env` (only `OPENAI_*` / `LUNA_*` keys, never overriding
  the real environment) and returns whether `OPENAI_API_KEY` is set. The app creates an
  `AIWorker` only if it is configured and `--no-ai` is not given.
- `LunaBrain` lazily imports `openai`, uses the Responses API with `previous_response_id`
  for conversation memory, timeout 30 s, 1 retry. Model: `LUNA_AI_MODEL` or `DEFAULT_MODEL`
  (unverified default). `INSTRUCTIONS` ask for 1–3 short plain sentences, because answers are spoken.
- **Streaming (phase 4):** `LunaBrain.stream()` calls the Responses API with `stream=True` and
  yields text pieces (`response.output_text.delta`). It sets `completed_id` on
  `response.completed`; `response.failed` / `incomplete` / `error`, or an end without
  completion, raise `StreamError`. The stream is closed when the generator stops.
  `ask()` (whole answer) is kept for `scripts/test_ai.py`.
- `AIWorker` handles one question at a time in a daemon thread. `ask()` returns a request id
  (False if busy). `speech.SentenceStream` turns the pieces into sentences (a sentence ends at
  `.!?…` + space, so "3.5" is never cut; same 20/250/700-character rules as `split_sentences`).
  It puts `(kind, request id, value)` on `answers`:
  `text` (show it, appended once in order), `sentence` (speak now), `say` (fixed sentence, e.g.
  empty answer), `done`, `error` (failed before anything was spoken: friendly sentence, as
  before) and `lost` (failed after Luna started speaking: she says "Sorry, I lost my connection.",
  translated by the pack). The time to the first sentence is logged.
- **History:** `previous_id` is the last *completed and fully spoken* answer. After `done`,
  `RobotHUD` calls `AIWorker.confirm(id)` once speech has finished, which calls
  `LunaBrain.remember()`. Interrupted, failed or incomplete answers are never remembered.
- **Interrupt:** tapping Home (or `S`, or Preview Voice) calls `RobotHUD.interrupt_answer()`:
  `speech.stop()` plus `AIWorker.cancel(id)`, which stops the stream silently (no error
  message), drops it from the history and frees the worker. Late messages for an old request id
  are ignored by `poll_voice`.
- Errors are logged by class name only; `friendly_error` turns them into a short spoken sentence (no secrets).
- Privacy: only recognised text is sent, never audio.

## 6. Command-line options (`main.py`)
`--windowed` (resizable window; default on Windows), `--fullscreen` (default elsewhere),
`--no-voice`, `--no-speech`, `--no-ai`, `--screenshot FILE` (save first frame and exit; implies
no voice/speech/AI so no native audio thread is alive at exit — on the Pi that caused a segfault).
`VoiceListener.stop()` joins the thread (3 s timeout) for the same reason.
Keyboard: `1-4` pages, `Space` blink, `S` stop speaking, `F11` fullscreen, `Esc`/`Q` quit.

## 7. Platform notes
- **Windows dev:** `.venv` + `requirements.txt` (pygame, vosk, sounddevice, openai).
- **Raspberry Pi OS:** pygame comes from apt (`python3-pygame`); the venv is created with
  `--system-site-packages` so it can still import it. Needs `libportaudio2`, `espeak-ng`,
  and a PipeWire/Pulse/ALSA player. Run from the desktop session (or `DISPLAY=:0` over SSH).
- Headless tests/CI: `SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy`.

## 8. Testing
`python -m unittest discover -s tests -v`. `test_core.py` is pure Python (no display).
`test_visual_smoke.py` renders one frame headless. Visual changes are best checked by
rendering to PNG (`--screenshot`) or by building `RobotHUD` with a dummy driver and saving
`canvas` — pass a namespace with `fullscreen, windowed, no_voice, no_speech, no_ai`.
To preview AI states, assign a fake object with an `answers` queue to `hud.ai` and set
`thinking`, `question`, `answer`, `answer_until`.

## 9. Design decisions worth knowing
- **Offline first:** recognition, commands, expressions and speech work with no internet.
  Only the AI conversation needs the network, and only recognised *text* is sent.
- **Optional heavy dependencies:** voice and AI imports are lazy/guarded so the face always starts.
- **Pure modules** (`commands.py`, `geometry.py`, `preferences.py`) have no pygame import and
  are the easiest place to add tested logic.
- **One window, one canvas:** simple to scale for any screen; no widgets library.
- Threads never touch pygame; they communicate through a queue, a float (`level`) and a status string.

## 10. Planned work (not implemented)
1. **Settings tab to pick the AI model** (saved as a preference; today use `LUNA_AI_MODEL`).
2. **Autostart on Pi boot** (desktop autostart entry + auto-login).
3. Real camera, motors, smart-home control (UI placeholders exist).

## 11. Known gaps
- Wi-Fi, audio and battery icons are decorative, not live readings.
- The small Vosk model may mishear "Luna"; variants are accepted in `commands.route`.
- Free-form recognition (AI on) is less accurate for one-word commands than grammar mode.
- The microphone is muted while Luna speaks, so you cannot interrupt her by voice (tap instead).
