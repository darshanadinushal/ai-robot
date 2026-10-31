# Changelog

Luna Core versions. Releases are git tags `vX.Y.Z`
(major: apps must change; minor: new features; patch: fixes).

## Unreleased
- Docs: `docs/ROADMAP.md`, decision records in `docs/adr/` (Home Assistant on a separate
  device; hybrid multi-agent task service) and the task service design
  `docs/design/TASK_SERVICE.md` (proposed, not implemented).

## 1.0.1 (2026-10-31)
- Startup works with [robot-setup](https://github.com/darshanadinushal/robot-setup): there is
  one `luna.service`. `scripts/install-service.sh` marks its files and refuses (also with
  `--remove`) when Robot Setup manages the service; Robot Setup replaces and later restores them.
- `install-service.sh`: quoted `ExecStart` (folders with spaces) and no `Nice=-2`, which a
  systemd user service may not set.
- Raspberry Pi: faster start and less memory. Pygame starts only the display and fonts (not the
  unused mixer, which opened the sound device, or joysticks); `run-pi.sh` sets `MALLOC_ARENA_MAX=2`.
- `scripts/measure_startup.py`: start-up time, peak memory and idle CPU/memory (docs/BASELINE.md).

## 1.0.0 (2026-10-30)
- Luna Core is an installable package, `luna-core` (`pyproject.toml`), with optional extras
  `[voice]`, `[speech]`, `[ai]` and `[all]`, and a `luna` command (also `python -m luna_core`).
- The package is renamed from `robot_hud` to `luna_core`. The public entry point is
  `from luna_core import Robot`. Old `robot_hud` imports still work (deprecated, removed in 2.0).
- Logging goes to the console and `~/.luna/logs/luna.log` (rotated, `LUNA_LOG_FILE`), with API
  keys masked; crashes are logged.
- `luna --version` and `luna_core.__version__`.
- Hardware layer `luna_core.hardware`: microphone, speaker, camera and motor interfaces with
  real and mock adapters. `--mock-hardware` / `LUNA_HARDWARE=mock` / `Robot(hardware=...)`, and
  `--headless` / `Robot(display="headless")` run Luna with no devices or screen.
- `app.py` is split: drawing, pages and touch input are in `luna_core.ui`, the face state in
  `luna_core.face_state`. Apps add pages with `Robot.add_page(page)` (a `luna_core.ui.Page`);
  the navigation bar makes room for them and keys `1`-`9` choose pages. Old `app.X` names still work.
- Plugins (`luna_core.plugins`, docs/PLUGINS.md): apps add commands, pages, AI tools,
  personality, language text and settings with `Robot.add_plugin(plugin)` or as installed
  packages (entry points `luna.plugins`, off until the user turns them on). Settings > Plugins
  turns plugins on and off, allows sensitive permissions (microphone, memory, network, camera,
  motors, devices: off by default) and changes plugin settings (`~/.luna/plugins.json`).
  Plugin errors are caught; after 3 a plugin is turned off and everything it added is removed.
  Version checks (`core = ">=1.0,<2"`, plugin API 1) and `luna --no-plugins` safe mode.
- Tests: plugin unit, lifecycle, permission, compatibility and integration tests, an example
  plugin (`examples/hello_plugin`), an opt-in clean-install test (`LUNA_SLOW_TESTS=1`), and CI on
  Linux, Windows and ARM Linux with Python 3.11-3.13.
- Raspberry Pi 5 performance: drawing reuses its surfaces and caches colours (wave mode
  21.9 → 6.5 ms per frame, face 5.5 → 4.3 ms, pixels unchanged); 30 FPS while busy, 15 FPS when
  idle or on still pages (`LUNA_FPS`, `LUNA_IDLE_FPS`); no scaling work on a 1280×720 screen;
  the AI client is prepared in the background at start; faster microphone level.
- AI cost: the fixed instructions and tools are sent unchanged every question so OpenAI caches
  them (cached input is 10x cheaper); time and memories go in a small per-question note.
  Noise utterances ("um", "the": new pack field `noise_words`) never reach the AI. Optional
  `LUNA_AI_MAX_OUTPUT_TOKENS`; token use of each answer is logged. Default model `gpt-6-luna`.
- Deployment: `scripts/install-service.sh` (systemd user service started with the desktop,
  restarts after a crash), `scripts/upgrade-pi.sh <version>` (backs up settings, memories and
  plugin choices, checks that Luna starts, goes back automatically if not) and
  `scripts/rollback-pi.sh [--with-data]`. `install-pi.sh` installs Luna from `pyproject.toml`.
  Release steps in `docs/RELEASING.md`; upgrade/rollback test in `tests/test_install.py`.
- Includes everything before the package: face, offline voice commands, Piper speech, AI chat
  with web search and memory, wake-word sound-alikes, configurable robot name, Settings.
