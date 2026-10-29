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
- Includes everything before the package: face, offline voice commands, Piper speech, AI chat
  with web search and memory, wake-word sound-alikes, configurable robot name, Settings.
