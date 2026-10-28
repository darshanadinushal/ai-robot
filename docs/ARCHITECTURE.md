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
  app.py                    UI, rendering, input, main loop (class RobotHUD): the adapter
  assistant_core/           Pure (no pygame): the conversation logic (section 2a)
    conversation.py         Conversation: routes utterances, AI answer lifecycle, follow-ups
    session.py              Session (id, follow-up window) and Turn (one question)
    context.py              Instructions (personality, abilities, date/time, profile) + settings
    events.py               Effects the core returns: Speak, Act, StopSpeech, Accepted, Mood, Log
    tools.py                AI tools: ToolRegistry, PermissionPolicy, ToolExecutor, face tools
    memory.py               Phase 5.2: MemoryStore (SQLite, ~/.luna-memory.db) + memory tools
    expression.py           MoodTag (strips an answer's "[mood]" tag), face_motion (listen/talk)
  commands.py               Pure: spoken words -> actions; actions -> spoken reply (from the packs)
  languages/                Language packs: en.json, sv.json (+ loader in __init__.py)
  voice.py                  Mic capture + Vosk recognition in a background thread
  listening.py              Pure: UtteranceAssembler joins Vosk's pieces into one question
  transcription.py          Phase 5.1 optional online listening (gpt-transcribe, OFF by default)
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
  measure_listening.py      Real Vosk + Piper timing of the smart listening (laptop or Pi);
                            --cases speaks the baseline sentences through Vosk
tests/
  test_core.py              Unit tests: geometry, preferences, commands, routing, speech text, AIWorker
  test_assistant_core.py    Phase 5.0A: Conversation without pygame, stale/stalled answers, sessions
  test_tools.py             Phase 5.0C: tool validation, permissions, no duplicates, tool rounds
  test_memory.py            Phase 5.2: memory store, "remember"/"forget" permission, instructions
  test_expression.py        Phase 5.0D: mood tags, answer expression lifecycle, "One moment."
  test_conversation_cases.py  Baseline: 34 English/Swedish sentences (conversation_cases.json)
  test_streaming.py         Phase 4: sentence splitter, AI stream, cancel, history, errors, live answer text
  test_listening.py         Phase 4.2/5.1: joining pauses, endpoints, mic buffer, online listening
  test_speech.py            Speech engine tests with fake voices/player (no audio)
  test_settings.py          Settings choices and taps on a headless screen
  test_swedish.py           Swedish listening, commands, replies and AI language
  test_languages.py         Every language pack and voice is complete; partial packs fall back
  test_visual_smoke.py      Launches the app headless and saves a screenshot
docs/ARCHITECTURE.md        This file
docs/BASELINE.md            Phase 5.0 baseline measurements (simulated and real Pi)
.github/workflows/tests.yml GitHub Actions: runs the tests headless
AGENTS.md                   Rules and quick facts for AI coding assistants
```

## 2. Runtime overview

```text
 microphone ──> VoiceListener (thread) ──texts──> UtteranceAssembler ──> RobotHUD.handle_speech()
                 │  level, partial      (one question)                   │
                 v                                                       v
 touch / mouse / keyboard ──> RobotHUD.events()        assistant core: Conversation.hear()
                                      │                  │ commands.route()
                                      v                  ├─ command ─> Act ─────────────┐
                              RobotHUD.frame(t)          ├─ wake ────> Speak "Yes?" ────┤
                                      │                  └─ ai ──────> AIWorker (thread)│
                                      v                     answers queue │ OpenAI      │
                    canvas 1280x720 ──scaled──>    Conversation.poll() <──┘             v
                                                   effects ──> RobotHUD.run_effects() ──> speech / face
```

Main loop (`RobotHUD.run`, 30 FPS): `events(t)` → `frame(t)` → `present()`.
`frame()` first calls `poll_voice(t)`: it assembles recognised text (→ `handle_speech` →
`Conversation.hear`) and calls `Conversation.poll`, which handles AI answers and opens the
follow-up window when speech ends. The core returns effects; `run_effects` carries them out.

## 2a. Assistant core (`robot_hud/assistant_core/`, phase 5.0)
The conversation logic lives in a pure package so it can grow (context, tools, memory, smart
home, driving) without growing `app.py`, and is tested without pygame
(`tests/test_assistant_core.py` checks that it never imports pygame).
- **Inputs:** `hear(text, t, heard_language, talk_language, conversation)` for one complete
  utterance; `poll(t, speaking, talking, listening)` every frame; `interrupt()`; `new_session()`.
- **Outputs:** a list of effects (`events.py`): `Speak(text)`, `Act(actions)`, `StopSpeech()`,
  `Accepted(question)` (calm face while thinking), `Mood(mood, seconds)`, `Log(message)`.
  `RobotHUD.run_effects` applies them on the UI thread; the core never calls speech or pygame.
- **State for drawing:** `thinking`, `heard`, `heard_kind`, `answer`, `answer_until`,
  `streaming` (mic stays muted), `in_followup(t)`.
- **Turns and sessions:** each question is a `Turn(session, request, ...)`. AI messages are used
  only if both the request id and the session id match the current turn, so cancelled, stalled
  or old answers never reach a new question. `new_session()` stops the answer, renews the session
  and forgets the AI history.
- **Context (phase 5.0B, `context.py`):** every question gets fresh instructions: the global
  personality (warm, brief, more detail when asked, one short clarifying question when unclear,
  never invent readings or actions), what Luna `ABILITIES` really has and what is
  `NOT_CONNECTED` yet (camera, motors, smart home, music, timers, search, memory), the local
  date and time, the optional `LUNA_USER_NAME` / `LUNA_LOCATION`, and the pack's
  `ai_instruction` (language and its style). Only list an ability once it really works.
  Follow-ups and references ("tell me more", "the second one") use the AI's conversation history.
- **Conversation lifecycle (5.0B):** the follow-up window is 8 s, or 15 s when Luna's answer
  ended with a question. A session ends (fresh AI history, log line `Luna: new conversation ...`):
  after `LUNA_IDLE_RESET_SECONDS` (300; 0 = never) without talking, listening, answering or
  speaking; before the question after `LUNA_MAX_TURNS` (20) remembered answers (the
  `previous_response_id` chain cannot be trimmed, so it starts fresh); or when asked. This is
  session history only, separate from the saved facts of Persistent memory (below).
- **Conversation controls (5.0B):** the packs' `controls` phrases, as the whole utterance with
  "Luna" or during a follow-up: `cancel` ("never mind", "no thanks", "strunt samma") closes the
  follow-up and says "Okay."; `new_conversation` ("start a new conversation", "nytt samtal")
  forgets the history and listens for a new question. `route` returns `("control", name)`.
  The microphone is off while Luna speaks, so they do not interrupt her; tap the screen for that.
- **Tools (phase 5.0C, `tools.py`):** natural requests ("you look tired, cheer up", "make your
  eyes purple") reach the AI, which may *propose* function calls (Responses API, `strict`
  schemas). Exact offline commands keep their fast path. Flow:
  1. `AIWorker` streams a response with `tools`; a `function_call` item marks the round as a
     tool round (text beside it is not spoken or shown) and the calls are sent as
     `("tools", id, [ToolCall])`.
  2. `Conversation` (UI thread) checks the turn is current, then `ToolExecutor.execute`:
     parse JSON, validate against the schema (known enum values only, no extra keys),
     `PermissionPolicy.decide`, run once. Effects are `Act(..., quiet=True)` (no offline reply).
  3. On the **next** poll, after `run_effects` applied them, the `ToolResult`s go back with
     `AIWorker.tool_results`; the worker continues with `function_call_output` items and
     `previous_response_id` = the tool round. At most `MAX_TOOL_ROUNDS` (3); the last round has
     `tool_choice: "none"`, so it must answer in words. The final response id enters the history.
  - Safety: the same call id, or the same tool + arguments, runs once per question; at most
    `MAX_CALLS_PER_TURN` (6) actions; cancelled or old turns never run tools; the worker waits
    at most 10 s for results; tool calls are logged as `Luna tool: name -> ok/reason`.
  - Risk classes: `local` (own face, allowed), `info`, `device` (planned, off), `motion` and
    `sensitive` (always refused here; driving will need a MotionManager, ESP32 watchdog and a
    hardware emergency stop). New skills register a `Tool` with a risk; the AI never decides
    permission.
  - `LUNA_AI_TOOLS=off` sends no tools (for a model without function calling; a
    `BadRequestError` logs that hint). Instructions then describe only the offline commands.
- **Persistent memory (phase 5.2, `memory.py`):** facts the user *asked* Luna to keep
  ("remember that I like jazz", "kom ihåg att ..."), never whole conversations.
  - `MemoryStore`: SQLite file `LUNA_MEMORY_FILE` or `~/.luna-memory.db` (outside the repo,
    created on the first save, mode 600, `secure_delete`). At most `MAX_FACTS` (50) facts of
    `MAX_CHARS` (120); duplicates are not saved twice; `replaces` corrects a fact. Facts that look
    private (4+ digit runs, the pack's `memory_words.private` words such as "password",
    "personnummer") are refused.
  - Tools (risk `memory`): `remember_fact(fact, replaces)`, `recall_memories()`,
    `forget_memory(id)`. `Tool.asked_with` makes `PermissionPolicy` refuse saving/forgetting
    unless the user's **own words** this turn contain a pack `memory_words.remember` /
    `forget` phrase (checked in code, whole words). Recall is always allowed. "Forget
    everything" exists only in Settings (two taps), never by voice.
  - Each question: `MemoryStore.relevant` picks up to `RELEVANT` (5) facts sharing a word stem
    with it (minus the pack's `memory_words.ignore` words) and `context.instructions` lists them
    as notes, "never permission"; with more facts saved the AI may call `recall_memories`.
    A memory never grants a device or motion permission. Read errors are logged by class name
    and the question is answered without memories.
  - Settings → Memory: on/off (preference `memory`, default on; off = not used and nothing
    saved, nothing deleted), the facts newest first (4 per page) with a delete button each, and
    "Forget everything". Needs the AI with tools for voice; offline only the Settings page works.
- **Expressions (phase 5.0D, `expression.py`):** the instructions ask the AI to start each
  answer with one tag (`[happy]`, `[sad]`, `[surprised]`, `[sleepy]`, `[neutral]`); no extra AI
  request. `AIWorker` runs a `MoodTag` per round *before* `SentenceStream`: it holds back at
  most a few leading characters, sends `("mood", id, mood)` and removes the tag, so it is never
  shown or spoken and sentence order/timing are unchanged. `Conversation.expression` is that
  mood (not `neutral`; ignored if a `set_face_mood` tool ran this turn) while Luna answers and
  `LINGER_SECONDS` (1.5 s) after; it clears on interrupt, error and new session. The app draws
  it *above* `self.mood`, so the user's expression, colour and mode are never changed.
  - Thinking face as soon as a question is accepted (as before). `face_motion`: eyes open a
    little wider with the mic level while the user speaks; a soft bob while Luna talks.
  - Slow answers: after `progress_seconds` (6 s, `LUNA_PROGRESS_SECONDS`, 0 = off) with
    nothing spoken, Luna says "One moment." once per question and the caption reads
    "STILL THINKING...". Fast answers get no filler. `LUNA_AI_EXPRESSIONS=off` drops the tag rule.
- **Diagnostics:** `Luna: first spoken reply X s after the question` is logged once per answer
  (time from the routed utterance to the first audible sentence).
- `RobotHUD` (the adapter) keeps the microphone (`listen`), the face (`apply_actions`), touch and
  drawing. `hud.ai` is a property that sets `conversation.ai`.

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
  the footer in Wave mode) with the question so far (`UtteranceAssembler.display()`: joined
  pieces + live partial, the newest words when it is long; pulsing border) or the last heard
  phrase; ignored phrases are dimmed with a "say Hey Luna first" note. `draw_footer` shows
  "LUNA IS THINKING..." or "LUNA: answer" (2 lines, while talking and 20 s after), else a hint.
  `draw_caption` (top left) shows THINKING / follow-up / mic status plus `AI: ON|OFF`.

### Settings page
A section menu on the left (Display & Theme, Language, Voice, Animations, Listening, Memory, About) works like
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
3. Vosk's final results go to `VoiceListener.texts` (a `queue.Queue`); `level` is the mic volume
   and `partial` the in-progress text. Vosk ends a result after a short pause (about 0.5-1 s),
   so one question can arrive in several pieces: see "Smart listening" below.
4. `RobotHUD.listen` joins the pieces into one utterance and then
   `commands.route(text, conversation)` decides, once per utterance:
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
answer is still streaming (so she stays deaf in the gaps between streamed sentences). The
unfinished utterance and queued pieces are dropped too (`forget_utterance`).

### Smart listening (phase 4.2)
One Vosk recogniser, no extra voice detector or model. `listening.UtteranceAssembler` (pure,
times passed in, used from the UI loop by `RobotHUD.listen`) keeps the final results as
confirmed text and the live partial as "still talking":
- While a partial is showing, nothing is sent. When it stops, `commands.utterance_end` (words
  only; Vosk has no punctuation) picks the wait after the last new words:
  `command` (the whole utterance is an exact short command, e.g. "Luna, look left") → now;
  `complete` → the pack's `continue_seconds` (en 0.6 s, sv 0.9 s: the Swedish model shows
  resumed speech about 0.7 s late), + 0.04 s per word above 8 (at most +0.6 s) and + 0.3 s once
  the user has paused inside this utterance; `wake` (only "Luna") and `unfinished` (ends on a
  pack `continuation_words` word: and, the, of, och, att, som...) → 1.8 s. So "Luna" alone
  says "Yes?" only if no question follows.
- Exactly one utterance is routed; the follow-up window is checked at the time the user
  **started** speaking (`Utterance.conversation`).
- Limits: 45 s or 1,000 characters (`LUNA_MAX_UTTERANCE_SECONDS`, `LUNA_MAX_UTTERANCE_CHARS`);
  then the text so far (cut at a word) is sent and the rest of that speech is skipped.
- Microphone queue: at most 10 s (`LUNA_MIC_BUFFER_SECONDS`, 0.25 s blocks). While muted or
  loading a model, queued audio is discarded. If blocks are dropped while listening (the Pi
  cannot keep up) the recogniser is reset, `overruns` goes up and the app drops the unfinished
  utterance (`invalidate`) and skips the rest of that speech: never an answer to missing words.
- Cleared when Luna talks, the voice language changes, she is interrupted or the mic stops.
- Tuning: `LUNA_CONTINUE_SECONDS` (all languages), `LUNA_LONG_PAUSE_SECONDS` (1.8).
- Diagnostics (stdout / journal), one line per utterance: `Luna heard: N chars in P part(s),
  S s of speech, handled X s after the last words (ending); recogniser C s CPU; dropped audio
  blocks D`, plus a line when microphone audio is lost.

Measured on the laptop (`scripts/measure_listening.py`: Piper speech into the real Vosk models;
the Pi numbers still need to be measured there):

| Case | Before (1st piece sent) | Now (sent after speech end) |
| --- | --- | --- |
| Command "Luna, look left" (en / sv) | 1.1 s / 0.7 s | same, immediately |
| Short question (en / sv) | 0.6-0.7 s | 1.2-1.4 s / 1.6-1.7 s |
| Question with 2 pauses (0.9 s, 1.2 s) | 3 separate pieces | 1 question, about 2 s |
| 18 s question with 4 pauses | 5 separate pieces | 1 question, about 2.6 s |
| "Hey Luna." 1.5 s pause, question | "Yes?" + separate question | 1 question |

A pause after a complete-sounding phrase is joined up to about 1.2 s (en) / 1.0 s (sv);
longer pauses or pauses after a word ending with a continuation word up to about 1.8 s. The
recogniser used about 0.1x real time (en) and 0.02x (sv) of one laptop core.
Simulated tests (`tests/test_listening.py`) are separate from these measurements.

### Online listening (Phase 5.1, optional, OFF by default)
Vosk always does the wake word, commands and controls offline. With Settings → Listening →
Online listening on (preference `online_listening`; also needs the AI and the microphone), a
**question for the AI** is transcribed online with `gpt-transcribe` for better accuracy:
1. `VoiceListener.capture` is an `AudioRing` (`transcription.py`): the last mic blocks, in
   memory only, stamped with `time.monotonic()`. `RobotHUD.listen` trims it to a 2 s pre-roll
   while nobody talks, clears it when online listening is off, and the listener clears it while
   muted or after lost audio.
2. When an utterance is finished, it is routed on its **Vosk** text first. Only `'ai'` (wake word
   or open follow-up window) is uploaded; commands, controls, "Hey Luna" alone and background
   speech never leave the robot. The audio since the utterance start (minus the pre-roll) is
   taken out of the ring, which is then cleared.
3. `OnlineListening.start` runs `CloudTranscriber` on a thread (WAV in memory, `prompt` and
   `extra_body` `languages` + `keywords` from the pack's `transcription_prompt` /
   `transcription_keywords`; `language` only for older models). Meanwhile the mic is muted and
   Luna shows her thinking face.
4. `RobotHUD.listen` polls it; `route_heard` sends the online text to `handle_speech` (as a
   conversation turn, Vosk already authorised it). On an error, empty text, a timeout
   (`LUNA_TRANSCRIBE_TIMEOUT`, 8 s) or audio under 0.3 s the Vosk text is used instead. A tap
   cancels it and a late result is ignored. Logs give only the reason and lengths, never the
   text or the exception message.
Model `LUNA_TRANSCRIBE_MODEL` (default `gpt-transcribe`). Each question costs API usage.

### Adding a voice command
1. New word for an existing action: add it to `commands` in each pack (`languages/<code>.json`).
2. New action: add its ID to `commands.ACTIONS`, a word and a reply for it in **every** pack
   (`test_languages` checks this), and handle a new `kind` in `RobotHUD.apply_actions`.
3. Add a case to `CommandTests` in `tests/test_core.py`.

### Adding a language (no code changes)
1. Copy `robot_hud/languages/en.json` to `<code>.json` (e.g. `de.json`) and translate it:
   `name`, `native_name`, `screen` (English text → translation), `commands`, `controls`,
   `wake_words`, `greetings`, `replies`, `speech`, `memory_words` (`remember`, `forget`,
   `private`, `ignore` phrases for Persistent memory), `ai_instruction` (language and speaking
   style), `transcription_prompt` and `transcription_keywords` (online listening hints),
   `sample`, `warm_up`, `windows_culture`.
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
| `online_listening` | boolean — questions transcribed online (Phase 5.1) | `false` |
| `memory` | boolean — use and save remembered facts (Phase 5.2) | `true` |

`read_preferences` validates every field and silently falls back to defaults on any error;
unknown keys are ignored. Adding a setting means: default in `read_preferences`, validation,
`write_preferences`, a choice in `settings.apply_choice`, a control in `settings_page`, and updating the tests.

## 5a. AI conversation (`ai.py`)
- `ai_configured()` loads `~/.luna.env` (only `OPENAI_*` / `LUNA_*` keys, never overriding
  the real environment) and returns whether `OPENAI_API_KEY` is set. The app creates an
  `AIWorker` only if it is configured and `--no-ai` is not given.
- `LunaBrain` lazily imports `openai`, uses the Responses API with `previous_response_id`
  for conversation memory, timeout 30 s, 1 retry. Model: `LUNA_AI_MODEL` or `DEFAULT_MODEL`
  (unverified default). The instructions are built by the assistant core
  (`context.instructions`, section 2a) and passed with each question: 1–3 short plain sentences,
  up to 5 when asked for detail (well under the 700-character spoken limit of `SentenceStream`).
- **Reasoning effort:** `LUNA_AI_REASONING` (`none`, `minimal`, `low`, `medium`, `high`,
  `xhigh`) is sent as `reasoning.effort` only when set. Not every model supports every value; the
  API then fails with `BadRequestError`, which is logged with a hint, never silently changed.
  Compare speeds with `python scripts/test_ai.py --bench --efforts default,low`.
- **Streaming (phase 4):** `LunaBrain.stream()` calls the Responses API with `stream=True` and
  yields text pieces (`response.output_text.delta`). It sets `completed_id` on
  `response.completed`; `response.failed` / `incomplete` / `error`, or an end without
  completion, raise `StreamError`. The stream is closed when the generator stops.
  `ask()` (whole answer) is kept for `scripts/test_ai.py`.
- `AIWorker` handles one question at a time, each in its own daemon thread. `ask()` returns a
  request id (False if busy); a cancelled request frees it at once, even when its network read is
  stuck, and the stuck thread later ends silently. The completed response id is returned per
  call (`stream(..., result)`), so an overlapping stuck stream can never enter the history. `speech.SentenceStream` turns the pieces into sentences (a sentence ends at
  `.!?…` + space, so "3.5" is never cut; same 20/250/700-character rules as `split_sentences`).
  It puts `(kind, request id, value)` on `answers`:
  `text` (show it, appended once in order), `sentence` (speak now), `say` (fixed sentence, e.g.
  empty answer), `done`, `error` (failed before anything was spoken: friendly sentence, as
  before) and `lost` (failed after Luna started speaking: she says "Sorry, I lost my connection.",
  translated by the pack). The time to the first sentence is logged.
- **History:** `previous_id` is the last *completed and fully spoken* answer. After `done`,
  `Conversation.poll` calls `AIWorker.confirm(id)` once speech has finished, which calls
  `LunaBrain.remember()`. Interrupted, failed or incomplete answers are never remembered.
- **Interrupt:** tapping Home (or `S`, or Preview Voice) calls `RobotHUD.interrupt_answer()` →
  `Conversation.interrupt()`: `speech.stop()` plus `AIWorker.cancel(id)`, which stops the stream
  silently (no error message), drops it from the history and frees the worker. Late messages for
  an old request are ignored by `Conversation.poll`.
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
`hud.conversation.thinking`, `answer`, `answer_until`.
Baseline sentences: `tests/conversation_cases.json` (route expected today, and the phase meant
to change a case); `scripts/measure_listening.py --cases` speaks them through the real models.

## 9. Design decisions worth knowing
- **Offline first:** recognition, commands, expressions and speech work with no internet.
  Only the AI conversation needs the network, and only recognised *text* is sent.
- **Optional heavy dependencies:** voice and AI imports are lazy/guarded so the face always starts.
- **Pure modules** (`assistant_core/`, `commands.py`, `geometry.py`, `preferences.py`) have no pygame import and
  are the easiest place to add tested logic.
- **One window, one canvas:** simple to scale for any screen; no widgets library.
- Threads never touch pygame; they communicate through a queue, a float (`level`) and a status string.

## 10. Planned work (not implemented)
1. **Settings tab to pick the AI model** (saved as a preference; today use `LUNA_AI_MODEL`).
2. **Autostart on Pi boot** (desktop autostart entry + auto-login).
3. Real camera, motors, smart-home control (UI placeholders exist).
4. Natural turn-taking (Phase 5.3), smart home and driving, each as its own phase,
   reusing the tool registry and permission policy.

## 11. Known gaps
- Wi-Fi, audio and battery icons are decorative, not live readings.
- The small Vosk model may mishear "Luna"; variants are accepted in `commands.route`.
- Free-form recognition (AI on) is less accurate for one-word commands than grammar mode.
- The microphone is muted while Luna speaks, so you cannot interrupt her by voice (tap instead).
