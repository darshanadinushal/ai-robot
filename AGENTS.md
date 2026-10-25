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

## Where things go
- Spoken word -> behaviour: `robot_hud/commands.py` (`KEYWORDS`, `route`, `reply_for`) +
  `RobotHUD.handle_speech` / `apply_actions`.
- AI conversation: `robot_hud/ai.py` (`LunaBrain`, `AIWorker`) + `RobotHUD.ask_ai` / `poll_voice`.
- Drawing and pages: `robot_hud/app.py`. Settings stored by `robot_hud/preferences.py`.
- Pure logic needs tests in `tests/test_core.py`; keep pure modules free of pygame imports.

## Rules and gotchas
- **Never commit secrets.** API keys come only from the `OPENAI_API_KEY` environment variable or
  `~/.luna.env` (outside the repo, loaded by `ai.load_env_file`, OPENAI_*/LUNA_* keys only).
  Never log keys or exception messages from the AI client (log the class name only).
  Do not put keys, tokens, personal emails or home paths in code, docs or tests.
  User settings live in `~/.ai-robot-hud.json`, outside the repo; voice models in `~/.ai-robot-models`.
- Draw on the 1280×720 logical canvas only; use the shared `*_RECTS` constants for both drawing and hit tests.
- Worker threads (voice, speech, AI) must not call pygame; communicate via queue/float/status string.
- Voice and AI dependencies are optional; the app must still start without them
  (guarded imports, `--no-voice`, `--no-speech`, `--no-ai`).
- Voice recognition is grammar-restricted to `commands.VOCABULARY` when AI is off, and free-form
  when AI is on; new commands must be added to `KEYWORDS` either way.
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
follow-ups and tap-to-stop, Windows + Pi scripts, tests.
Planned: AI-model setting, autostart, camera/motors/smart home.
