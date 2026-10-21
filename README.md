# AI Robot — Futuristic HUD Eyes (Design #1)

A **runnable, touch-friendly futuristic robot face** for a Raspberry Pi 5 + official
7-inch Raspberry Pi Touch Display 2 in landscape orientation (1280 × 720).
Develop it first on a Windows laptop with VS Code, then push to GitHub and run on the Pi.

**Version:** 1.0 — face and dashboard only. AI voice, camera video, motors and home
control are future integrations, **not** working hardware features in this release.

## Included

- Two animated glowing circular cyber/HUD eyes, moving iris and automatic blinking
- Small glowing smile and scrolling HUD details
- **Chat**, **Home**, **Camera**, **Settings** touchscreen navigation
- Chat page with **simulated** voice waveform (no audio capture)
- Camera page with a clearly labelled **placeholder**, not a camera feed
- Working face-theme settings (cyan, purple, green, amber), animation toggle
- Settings automatically saved in `~/.ai-robot-hud.json`
- True fullscreen at the Pi's current display resolution, with correct touchscreen scaling
- Resizable Windows preview for development
- Local clock; status symbols for wireless/audio/battery are **visual placeholders**, not live device readings
- No paid API, keys, internet access, or additional hardware necessary to launch the face

## 1 — Run on Windows (coding laptop)

**Easiest:** Extract the ZIP and double-click `SETUP_WINDOWS.bat` once, then
double-click `RUN_WINDOWS.bat` to open the face.

**Or use PowerShell manually:**

Install [Python](https://www.python.org/downloads/windows/) (including the **py** launcher)
then open PowerShell in the project directory:

```powershell
py -3 --version
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py --windowed
```

You can also run `powershell -ExecutionPolicy Bypass -File scripts/setup-windows.ps1`
for setup, then `powershell -ExecutionPolicy Bypass -File scripts/run-windows.ps1`
to launch. No need to activate the virtual environment.

VS Code extensions: **Python** and optionally **Remote - SSH**.

## 2 — Publish to your GitHub repository

Create an **empty** GitHub repository named `ai-robot` (do not initialize with README if
starting from this ZIP). Extract this ZIP, open a terminal **inside the extracted folder**:

```powershell
git init
git add .
git commit -m "Initial AI Robot HUD face"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/ai-robot.git
git push -u origin main
```

Replace `YOUR_USERNAME`. GitHub will require sign-in or a credential manager; use your
usual secure Git authentication, **not** a password or personal access token in code.

**Already have a repository?** Clone it to a separate folder with `git clone`, copy the
project files into that folder (do not copy an `.git` directory), then use `git add .`,
`git commit -m "Add HUD face"`, and `git push`. Do not force-push over existing work.

## 3 — Install and run on Raspberry Pi 5

On the Raspberry Pi **desktop Terminal** (not Windows PowerShell):

```bash
sudo apt update
sudo apt install -y python3-pygame git
python3 -c "import pygame; print(pygame.version.ver)"
```

Clone your repository, replacing the username:

```bash
cd ~
git clone https://github.com/YOUR_USERNAME/ai-robot.git
cd ai-robot
python3 main.py --fullscreen
```

**Private repo?** Configure GitHub SSH or HTTPS authentication first.
If `~/ai-robot` already exists and contains work, do not overwrite it. Clone into an
unused directory instead, such as `ai-robot-github`:

```bash
git clone https://github.com/YOUR_USERNAME/ai-robot.git ~/ai-robot-github
cd ~/ai-robot-github
python3 main.py --fullscreen
```

You can also use `bash scripts/install-pi.sh` to install requirements or
`bash scripts/run-pi.sh` to start the UI from the Pi's desktop session.

**Important:** Start from a terminal **on the Raspberry Pi desktop** so pygame has
access to the current graphical display. A bare SSH session may not be able to open
the Pi's screen without separate display-session configuration.

## Controls

| Input | Effect |
| --- | --- |
| Touch anywhere on face | Move pupils toward your finger + blink |
| Bottom **Home** | Return to animated face |
| Bottom **Chat** | Simulated voice waveform page |
| Bottom **Camera** | Camera placeholder page |
| Bottom **Settings** | Choose colour and enable/disable idle animations |
| `1` / `2` / `3` / `4` | Chat / Home / Camera / Settings |
| `Space` | Blink |
| `F11` | Toggle fullscreen / windowed mode |
| `Esc`, `Q`, or top-right `X` | Exit |

## Later updates from GitHub

```bash
cd ~/ai-robot   # or the directory you cloned into
git pull
python3 main.py --fullscreen
```

Stop the running app before pulling and relaunch it after updating.

## Development and tests

Run pure-Python unit tests (works even without pygame):

```bash
python3 -m unittest discover -s tests -v
```

Generate a PNG screenshot on a computer with pygame installed:

```bash
python3 main.py --windowed --screenshot preview.png
```

For CI without a graphical display, use `SDL_VIDEODRIVER=dummy` and
`SDL_AUDIODRIVER=dummy`. GitHub Actions does this automatically.

## File structure

```text
ai-robot/
├── SETUP_WINDOWS.bat / RUN_WINDOWS.bat # Double-click launchers
├── main.py                         # App launcher
├── robot_hud/
│   ├── __init__.py
│   ├── app.py                      # Eyes, HUD rendering, touch and page UI
│   ├── geometry.py                 # Viewport scaling and blink maths
│   └── preferences.py              # Theme storage
├── scripts/
│   ├── install-pi.sh
│   ├── run-pi.sh
│   ├── setup-windows.ps1
│   └── run-windows.ps1
├── tests/
├── .github/workflows/tests.yml
├── requirements.txt
├── .gitignore
└── README.md
```

## Common problems

- **`ModuleNotFoundError: pygame`:** On the Pi, run `sudo apt install -y python3-pygame`
  and use the **system** `python3`; on Windows install from `requirements.txt` into `.venv`.
- **`No available video device`:** Run within the Pi graphical desktop (or Windows
  GUI), not inside a plain text-only SSH terminal.
- **Screen rotated:** Set the Touch Display 2 to landscape through Raspberry Pi OS
  display settings before starting this program.
- **`mv: cannot stat ...`:** The downloaded Python file is not in that path; using Git
  clone instead of moving individual Downloads files avoids this issue.
- **Touch offset:** The app maps the actual display area to the logical 1280×720
  coordinates and ignores letterbox bars; verify OS touch calibration if still wrong.

No credentials should be committed to this repository. For future AI services, use a
local `.env` that stays in `.gitignore`, or a secure secrets manager.
