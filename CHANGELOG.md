# Changelog

Luna Core versions. Releases are git tags `vX.Y.Z`
(major: apps must change; minor: new features; patch: fixes).

## 1.0.0 (in progress)
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
- Includes everything before the package: face, offline voice commands, Piper speech, AI chat
  with web search and memory, wake-word sound-alikes, configurable robot name, Settings.
