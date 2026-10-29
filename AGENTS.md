# AGENTS.md — guide for AI coding assistants

Luna 1.0: pygame robot face + offline voice commands + offline speech + optional online AI chat, for Raspberry Pi 5
(7" touchscreen, 1280×720) and Windows development. **Read `docs/ARCHITECTURE.md` first.**

## Commands
```powershell
# Windows
.\.venv\Scripts\python.exe -m unittest discover -s tests -v     # all tests (headless-safe)
.\.venv\Scripts\python.exe main.py --windowed                    # run
.\.venv\Scripts\python.exe main.py --windowed --no-voice --no-speech --screenshot out.png
```
```bash
# Raspberry Pi (from the desktop session)
bash scripts/install-pi.sh && bash scripts/run-pi.sh
```
Luna Core is the package `luna-core` (`pyproject.toml`; `pip install -e ".[voice,speech,ai]"`);
the public API is `from luna_core import Robot`. Record changes in `CHANGELOG.md`.

## Where things go
- Languages are data: `luna_core/languages/<code>.json` (screen texts, command words, wake
  words, replies, AI instruction...). Voices are data: `luna_core/voices.json`. robot-setup reads
  copies in `robot_hud/` (old package name, kept for compatibility): copy changed packs and
  `voices.json` there too (`tests/test_packaging.py` checks). Never add `if language == "sv"` code; add pack fields instead. Guide:
  "Adding a language" in `docs/ARCHITECTURE.md`; completeness checked by `tests/test_languages.py`.
- Spoken word -> behaviour: `luna_core/commands.py` (`ACTIONS`, `route`, `reply_for`; everything
  takes the voice language) + `RobotHUD.handle_speech` / `apply_actions`.
- Listening: Vosk final results are pieces; `listening.UtteranceAssembler` (via `RobotHUD.listen`)
  joins them into one utterance (`commands.utterance_end` picks the wait). Route only complete
  utterances; never call `handle_speech` per Vosk result. Tests: `tests/test_listening.py`.
- Online listening (`transcription.py`, setting `online_listening`, off by default): only
  utterances that Vosk routes to `'ai'` may be uploaded; audio stays in memory; always fall back
  to the Vosk text. Never upload commands, background speech or anything before the wake word.
- Audio devices: `audio_devices.py` (pure `choose`, impure `detect`). Echo cancelling only when
  mic *and* speech speaker are the array (ReSpeaker XVF3800; speech on its 4 Ω speaker; music
  speaker separate). Tests in `tests/test_core.py` (`AudioDeviceTests`).
- Voice barge-in (`assistant_core/turn_taking.py`, setting `voice_interrupt`, `null` = automatic:
  on with echo-cancelling audio):
  while Luna talks, heard text goes only to `Conversation.barge_in` (stop phrase or wake word;
  her own words are echo). A spoken "stop" is never a motion safety stop. Tests: `tests/test_turn_taking.py`.
- Conversation logic: `luna_core/assistant_core/` (pure, no pygame): `Conversation.hear` / `poll` /
  `interrupt` return effects (`events.py`) that `RobotHUD.run_effects` carries out. New
  conversation behaviour goes here, not in `app.py`; tests in `tests/test_assistant_core.py`.
  Baseline sentences: `tests/conversation_cases.json` (update a case's `route` when a phase changes it).
  AI context is built in `assistant_core/context.py`; list an ability there (and remove it from
  `NOT_CONNECTED`) only once it really works. Conversation controls are pack `controls` phrases.
- AI tools: `luna_core/assistant_core/tools.py`. A new skill is a `Tool` (strict JSON schema,
  risk class, pure `run` returning effects + `ToolResult`) in a `ToolRegistry`; validation and
  `PermissionPolicy` are code, never the model. `motion`/`sensitive` are never allowed here.
  Tests: `tests/test_tools.py` (fake brains, never the real API).
- Persistent memory: `assistant_core/memory.py` (`MemoryStore`, SQLite `~/.luna-memory.db` or
  `LUNA_MEMORY_FILE`; memory tools). Save only facts the user asked to keep: saving/forgetting is
  gated in code by the user's own `memory_words` phrases (`Tool.asked_with`); never save whole
  conversations or private data (About you: name, town, birthday MM-DD without year only), and never treat a memory as a permission. Tests: `tests/test_memory.py`.
- Answer expressions: `assistant_core/expression.py` (`MoodTag` strips the AI's leading "[mood]"
  tag in `AIWorker` before sentences; `Conversation.expression` is drawn above the user's mood,
  never saved). Tests: `tests/test_expression.py`.
- AI conversation: `luna_core/ai.py` (`LunaBrain.stream`, `AIWorker`); instructions from
  `assistant_core/context.py`. Answers stream sentence by sentence (`speech.SentenceStream`);
  only completed, fully spoken answers enter the history (`AIWorker.confirm`, via `Conversation.poll`).
- Drawing and pages: `luna_core/app.py`. Settings logic in `luna_core/settings.py` (tested in
  `tests/test_settings.py`), stored by `luna_core/preferences.py`.
- Screen text is translated: wrap new on-screen text in `tr()` and add it to `screen` in every
  non-English pack (the tests check this).
- Pure logic needs tests in `tests/test_core.py`; keep pure modules free of pygame imports.

## Rules and gotchas
- **Never commit secrets.** API keys come only from the `OPENAI_API_KEY` environment variable or
  `~/.luna.env` (outside the repo, loaded by `ai.load_env_file`, OPENAI_*/LUNA_* keys only).
  Never log keys or exception messages from the AI client (log the class name only).
  Log with `logging.getLogger("luna.<area>")`, not `print` (`logs.py` masks keys; tests set
  `LUNA_LOG_FILE` when they start the app).
  Do not put keys, tokens, personal emails or home paths in code, docs or tests.
  User settings live in `~/.ai-robot-hud.json`, memories in `~/.luna-memory.db`, outside the repo;
  voice models in `~/.ai-robot-models`. Tests must point `LUNA_MEMORY_FILE` at a temp folder.
- Draw on the 1280×720 logical canvas only; use the shared `*_RECTS` constants for both drawing and hit tests.
- Devices go through `luna_core/hardware/` (interfaces + real + mock adapters); never open
  sounddevice, players, cameras or motors directly elsewhere. Every new device needs a mock.
  Run without hardware: `main.py --headless --mock-hardware`.
- Worker threads (voice, speech, AI) must not call pygame; communicate via queue/float/status string.
- Speech: `speech.py` (pipeline, Piper/basic voices, immediate `stop()`), catalogue in `voices.json`;
  tests in `tests/test_speech.py` use fake voices/players. Design: `docs/VOICE_DESIGN.md`.
- Voice and AI dependencies are optional; the app must still start without them
  (guarded imports, `--no-voice`, `--no-speech`, `--no-ai`).
- Voice recognition is grammar-restricted to `commands.vocabulary(language)` when AI is off, and
  free-form when AI is on; new command words go in the packs' `commands` either way.
- The robot's name is configurable (`naming.py`): write fixed texts with "Luna" (they pass
  through `personalise`) and use `commands.wake_words`, never a hard-coded "luna" check.
- Changing a setting means updating `read_preferences`, `write_preferences`, the Settings UI,
  `handle_touch`, and the preference tests (they compare the whole dict).
- Raspberry Pi uses apt `python3-pygame`; do not "fix" this by pinning pygame there.
- Shell scripts must keep LF line endings.
- Chat waveform and Camera page are intentional placeholders, not real features.
- The default AI model name in `ai.py` is unverified; users override it with `LUNA_AI_MODEL`.
  Tests must use a fake `brain_factory` and never call the real API.
- Keep user-facing text short; the UI is a small touchscreen.

## Status snapshot
Working: face + wave modes, 3 face layouts, 3 wave layouts, 4 colours, mood expressions,
offline voice commands, spoken replies with mic muting, "Hey Luna" AI conversation with
follow-ups and tap-to-stop, date/time-aware answers, web search (news, live scores), configurable robot name, optional online listening, AI face tools, persistent memory (explicit facts, About you), interrupt by voice (headset), answer expressions, "never mind" / "new conversation", idle reset, Windows + Pi scripts, tests.
Planned: AI-model setting, autostart, camera/motors/smart home.
