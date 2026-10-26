# Luna 1.0 — Futuristic HUD Robot Face

**Luna** is a touch-friendly, voice-controlled robot face for a Raspberry Pi 5 with the official
7-inch Raspberry Pi Touch Display 2 in landscape orientation (1280 × 720). Develop it on a
Windows laptop, push it to GitHub, then run it on the Pi.

Luna shows animated eyes or a voice wave, obeys **offline voice commands**, answers out loud
with **offline text-to-speech**, and — with an OpenAI API key — holds **"Hey Luna" conversations**
with an online AI. Camera video, motors and home control are future work.

## Features

- Two animated glowing HUD eyes with moving iris, automatic blinking and a small smile
- Moods: happy, sad, angry, sleepy, surprised, plus a "thinking" look while the AI works
- **Face mode** (classic, compact, minimal layouts) and **Wave mode** (mic with mesh, bars or
  circle voice-wave) — the wave reacts to your microphone volume
- **Offline voice commands** with Vosk: expressions, looking around, blinking, colours, modes
- **English or Swedish**: Luna listens, speaks and answers in the language of the selected
  voice (Swedish commands such as "titta åt vänster", "var glad", "blinka"). More languages and
  voices are added as data, without code changes (see "Adding a language" in
  `docs/ARCHITECTURE.md`)
- **Spoken replies** (Windows built-in voice / `espeak-ng` on the Pi); the mic is muted while
  Luna talks so she does not hear herself
- **AI conversation** (optional): "Hey Luna, …" questions, spoken + on-screen answers,
  follow-up questions, tap to stop. Answers are streamed: Luna starts speaking after the first
  sentence while the rest is still being generated, and the text appears as it arrives
- **Live transcript:** what you say appears in a "YOU:" bubble; Luna's reply appears at the bottom
- **Chat**, **Home**, **Camera**, **Settings** touchscreen navigation
- Settings with a section menu: Display & Theme (mode, colour, layout), Language
  (screen text in English / Svenska), Voice (voice language, female / male, **Play sample**,
  loading status), Animations,
  About — saved automatically in `~/.ai-robot-hud.json`
- Fullscreen on the Pi with correct touch scaling; resizable window on Windows
- Chat page waveform is **simulated**; Camera page is a **placeholder**; Wi-Fi/audio/battery
  icons are decorative

Without a microphone, voice model or API key, Luna still starts and shows her face.

## 1 — Run on Windows (coding laptop)

**Easiest:** double-click `SETUP_WINDOWS.bat` once, then `RUN_WINDOWS.bat`.

**Or with PowerShell** (Python 3.11+ installed), in the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\download-voice-model.py en sv   # once: English + Swedish listening
.\.venv\Scripts\python.exe main.py --windowed
```

If `python` is not found, use the full path, e.g.
`& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m venv .venv`.
No need to activate the virtual environment. VS Code extensions: **Python** and
optionally **Remote - SSH**.

## 2 — Publish to your GitHub repository

```powershell
git add .
git commit -m "Describe your change"
git push
```

First time only (new empty repository):

```powershell
git init
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/ai-robot.git
git push -u origin main
```

Sign in with the browser / Git Credential Manager. Never put passwords, tokens or API keys
in code. If push says `403 Permission denied to <other-account>`, Git is using a different
saved GitHub account: remove the `git:https://github.com` entry in Windows Credential Manager
and push again.

## 3 — Install and run on Raspberry Pi 5

On the Raspberry Pi **desktop Terminal**:

```bash
cd ~
git clone https://github.com/YOUR_USERNAME/ai-robot.git
cd ai-robot
bash scripts/install-pi.sh     # apt packages, .venv, Vosk, openai, voice model
bash scripts/run-pi.sh         # starts Luna fullscreen
```

`install-pi.sh` installs `python3-pygame`, `libportaudio2`, `espeak-ng` and git with apt,
creates `.venv` (with access to the system pygame), installs `vosk sounddevice openai` and
downloads the voice model to `~/.ai-robot-models`.

If a folder with that name already exists, clone into another one
(`git clone ... ~/ai-robot-github`). **Start from a terminal on the Pi desktop** so pygame
can open the screen; over SSH use `DISPLAY=:0 bash scripts/run-pi.sh`.

## Voice commands (offline)

No internet or key needed. Use `--no-voice` to disable the microphone.

| Say | Reaction |
| --- | --- |
| happy / sad / angry / sleepy / surprised / neutral | Face expression for 10 s (switches to Face mode) |
| left / right / up / down / center | Eyes look in that direction |
| blink | Blink |
| speak / listen / quiet | Wave amplitude: strong / medium / flat (switches to Wave mode) |
| face / wave | Switch mode |
| cyan / purple / green / amber | Change theme colour |

Luna answers with a short spoken confirmation ("Looking left.", "Okay, I feel happy.").
Use `--no-speech` to silence her.

### Natural voice (optional, offline)
With the robot-setup **Natural voice** feature (`--with piper`), Luna speaks with a Piper voice:

| Key | Voice |
| --- | --- |
| `EN_FEMALE` (default) | English female (Lessac) |
| `EN_MALE` | English male (Ryan) |
| `SV_FEMALE` | Svenska kvinna (Alma) |
| `SV_MALE` | Svenska man (NST) |

The voice files live in `~/.ai-robot-models/piper/`, outside Git. The caption shows
`VOICE: NATURAL`, `LOADING` or `BASIC`; Settings shows the reason when the basic voice is used.
Without Piper Luna simply keeps the basic voice. The choice is saved as `voice` in the
preferences file — choose it in **Settings → Voice** and tap **Play sample**. To try
another voice for one run without saving it, set `LUNA_VOICE=SV_MALE`.
To force a sound player (e.g. a headset), set `LUNA_AUDIO_PLAYER`, for example
`LUNA_AUDIO_PLAYER="aplay -D plughw:2,0"`. These voices are for the personal prototype only;
see the licence notes in [`docs/VOICE_DESIGN.md`](docs/VOICE_DESIGN.md).

## Talk to Luna (online AI)

1. Create an API key at https://platform.openai.com/api-keys
   (API billing is separate from a ChatGPT subscription).
2. Give Luna the key — it is read from the environment or from `~/.luna.env`, **never** from
   the repository:

   **Windows** (only for the current PowerShell window — enter the key, then start Luna from the
   same window):

   ```powershell
   $env:OPENAI_API_KEY = Read-Host "OpenAI key" -MaskInput
   .\.venv\Scripts\python.exe main.py --windowed
   ```

   **Raspberry Pi** (saved once, readable only by you):

   ```bash
   read -rsp "OpenAI API key: " KEY; echo
   printf 'OPENAI_API_KEY=%s\n' "$KEY" > ~/.luna.env; chmod 600 ~/.luna.env; unset KEY
   bash scripts/run-pi.sh
   ```

3. Check the top-left status says **`AI: ON`**.

Then talk:

| You say | Luna does |
| --- | --- |
| "Hey Luna, tell me about Mars." | Looks up while thinking, then speaks and shows the answer |
| "Luna" | Says "Yes?" and waits for your question |
| A follow-up within ~8 s, e.g. "How far is it?" | Answers without needing "Luna" again (15 s if she asked you something) |
| "Never mind" / "No thanks" (or "Luna, cancel") | Stops waiting for a follow-up |
| "Luna, start a new conversation" | Forgets the conversation so far |
| "look left", "happy", … | Still handled offline as commands |
| Tap the screen or press `S` | Stops her talking |

- Only the recognised **text** is sent to OpenAI, never microphone audio. You pay only for AI answers.
- Mishearings such as "he luna", "lunar" or "loona" are accepted as the wake word.
- **In Swedish** (Swedish voice selected) say "Hej Luna, …"; Luna answers in Swedish. The Swedish
  model has no word "Luna", so the words it hears instead (lugn, lugna, lund, lunch, helene…)
  count as the wake word. If the Swedish model is missing, Luna keeps listening in English and
  Settings → Voice → Listening says so.
- With AI on, the microphone understands free speech, so single command words are recognised
  slightly less reliably. Use `--no-ai` for command-only mode.
- Choose another model with `LUNA_AI_MODEL=<model>` (environment variable or a line in `~/.luna.env`).
- Optional lines in `~/.luna.env`: `LUNA_USER_NAME=Sam` (what Luna calls you),
  `LUNA_LOCATION=Living room`, `LUNA_AI_REASONING=low` (only if your model supports it; compare
  with `python scripts/test_ai.py --bench`), `LUNA_IDLE_RESET_SECONDS=300` (a new conversation
  after this much quiet), `LUNA_MAX_TURNS=20`.
- Luna knows today's date and time, and says so when something (camera, motors, smart home,
  music) is not connected yet instead of pretending.
- Try typed chat without the face: `python scripts/test_ai.py`.

## Controls

| Input | Effect |
| --- | --- |
| Touch anywhere on face | Move pupils toward your finger + blink (or stop Luna talking) |
| Bottom **Home** | Return to the face / wave |
| Bottom **Chat** | Simulated voice waveform page |
| Bottom **Camera** | Camera placeholder page |
| Bottom **Settings** | Mode, colour, layout, screen language, voice, preview, animations (menu on the left; Up / Down keys change section) |
| `1` / `2` / `3` / `4` | Chat / Home / Camera / Settings |
| `Space` | Blink |
| `S` | Stop speaking |
| `F11` | Toggle fullscreen / windowed mode |
| `Esc`, `Q`, or top-right `X` | Exit |

Command-line options: `--windowed`, `--fullscreen`, `--no-voice`, `--no-speech`, `--no-ai`,
`--screenshot FILE`.

## Later updates from GitHub

Stop Luna, then on the Pi:

```bash
cd ~/ai-robot   # or the folder you cloned into
git pull
.venv/bin/pip install vosk sounddevice openai   # only if requirements changed
bash scripts/run-pi.sh
```

## Development and tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe main.py --windowed --no-voice --no-speech --no-ai --screenshot preview.png
```

Tests never call the real AI. For CI without a display, set `SDL_VIDEODRIVER=dummy` and
`SDL_AUDIODRIVER=dummy`; GitHub Actions (`.github/workflows/tests.yml`) does this automatically.

**For developers and AI assistants:** read [AGENTS.md](AGENTS.md) and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## File structure

```text
ai-robot/
├── main.py                     # App launcher
├── SETUP_WINDOWS.bat / RUN_WINDOWS.bat
├── robot_hud/
│   ├── app.py                  # UI, drawing, touch, voice/AI wiring (RobotHUD)
│   ├── assistant_core/         # Conversation logic without the screen (routing, answers, follow-ups)
│   ├── commands.py             # Words -> actions, wake-word routing, spoken replies
│   ├── languages/              # Language packs (en.json, sv.json): add a language here
│   ├── voice.py                # Microphone + Vosk recognition (background thread)
│   ├── listening.py            # Joins pauses into one complete question
│   ├── transcription.py        # Optional cloud transcription (off, not used yet)
│   ├── speech.py               # Text-to-speech: sentence pipeline, Piper + basic voice
│   ├── voices.json             # Voice catalogue (also used by robot-setup): add voices here
│   ├── voices.py               # Loads the voice catalogue
│   ├── settings.py             # Settings sections and choices
│   ├── ai.py                   # OpenAI conversation (background thread)
│   ├── preferences.py          # Settings saved to ~/.ai-robot-hud.json
│   └── geometry.py             # Screen scaling and blink maths
├── scripts/
│   ├── install-pi.sh / run-pi.sh
│   ├── setup-windows.ps1 / run-windows.ps1
│   ├── download-voice-model.py
│   ├── measure_listening.py    # Listening timing with the real models (--cases: test sentences)
│   └── test_ai.py              # Typed chat test for the AI
├── tests/                      # Unit tests + headless visual smoke test
├── docs/ARCHITECTURE.md        # How the code works
├── AGENTS.md                   # Rules for AI coding assistants
├── .github/workflows/tests.yml
└── requirements.txt
```

## Common problems

| Problem | Fix |
| --- | --- |
| `ModuleNotFoundError: pygame` | Pi: `sudo apt install -y python3-pygame`. Windows: install `requirements.txt` into `.venv`. |
| `No available video device` | Run from the Pi desktop (or `DISPLAY=:0` over SSH), not a plain SSH session. |
| Top left says `MIC: VOICE MODEL MISSING` | Run `scripts/download-voice-model.py en sv` with the venv Python. |
| Top left says `MIC: VOICE PACKAGES MISSING` | Install `requirements.txt` (Windows) or run `install-pi.sh` (Pi). |
| Mic hears nothing | Check the default input device (Windows Sound settings / Pi audio menu). |
| No spoken replies on the Pi | `sudo apt install -y espeak-ng`; check the speaker/headset output. |
| `VOICE: BASIC` instead of natural | Read the reason in Settings; install with robot-setup `--with piper`, then `verify`. |
| Natural voice silent on a headset | Check `wpctl status` (default sink), try `pw-play file.wav`, or set `LUNA_AUDIO_PLAYER`. |
| `AI: OFF` | The key is not set in this window / `~/.luna.env`, or `--no-ai` was used. |
| "My AI key is not working" | Check or recreate the API key. |
| "My AI model is not available" | Set `LUNA_AI_MODEL` to a model your account can use. |
| "Busy or out of credit" | Add API credit / check limits on platform.openai.com. |
| "I cannot reach the internet" | Check Wi-Fi; offline commands still work. |
| Screen rotated / touch offset | Set landscape in Raspberry Pi OS display settings; check touch calibration. |

## Security

No credentials are stored in this repository. Keep API keys in environment variables or
`~/.luna.env` (outside the project, `chmod 600` on the Pi). `.env` files are also gitignored.
Never commit keys, tokens or personal data.
