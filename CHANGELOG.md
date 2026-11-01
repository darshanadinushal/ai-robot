# Changelog

Luna Core versions. Releases are git tags `vX.Y.Z`
(major: apps must change; minor: new features; patch: fixes).

## 1.1.0 (unreleased)
- **Robot Setup integration fixes**:
  - `~/.luna.env` is read when Luna starts, so `LUNA_TASKS=service` written there (as the README
    and Robot Setup do) turns background tasks on under systemd too. Other `LUNA_*` settings in
    the file (such as `LUNA_VOICE`, `LUNA_AI_PRICES` and `LUNA_LOG_FILE`) now also apply from
    the start. The environment still wins.
  - `rollback-pi.sh` back to v1.0.1 with background tasks on no longer stops part-way: an extra
    the older version does not have (`agents`) is skipped.
  - `upgrade-pi.sh` backs up `~/.luna/tasks.db` (task history and AI usage) too, and
    `rollback-pi.sh --with-data` restores it to its own place.
- Backups of the SQLite databases (`tasks.db`, memories) use SQLite's backup API and an
  integrity check, so a backup taken while Luna writes (AI usage after each reply) holds no
  half-written transaction; other files are still copied with `cp -p`.
- `rollback-pi.sh --with-data` stops `luna.service` before restoring (Luna keeps her
  databases open), removes old `-journal`/`-wal` files and starts her again afterwards.
  - `tasks.db` records its schema version (`PRAGMA user_version`); a newer Luna's file is
    logged after a rollback and still used.
  - CI tests ARM Linux and the Agents SDK on Python 3.13 (Raspberry Pi OS Trixie).
- **Plugin helpers** (task service step 7): plugins can add background helpers with
  `PluginContext.add_specialist(...)` and the new sensitive permission `agents` (off until you
  allow it, since helpers use your AI budget). A helper uses only its plugin's own tools, checked
  by Luna's permission rules; limits are capped at 8 turns, 2 min and $0.05. Luna's own helpers
  cannot be replaced; when a plugin stops, its tasks fail ("plugin off"). Task protocol 1.1
  (`start`/`resume` may carry the helper's definition). Example: `examples/pantry_plugin`.
- **Research helper searches the web** (task service step 5): OpenAI's web search (about
  $0.01 per search, counted in the task's cost and the daily budget; off with
  `LUNA_AI_WEB_SEARCH=0`). Each search shows as a task step. Safety: a task that read web pages
  is "tainted" and needs your OK for every tool beyond Luna's local ones; its result reaches the
  talk AI as data, not instructions. Spoken results have no links, markdown or URLs and end at a
  whole sentence. A helper asking for a tool it does not have no longer fails the task.
- **Task helper on the Pi** (step 6): `install-pi.sh` / `upgrade-pi.sh` install the `[agents]`
  extra when `LUNA_TASKS=service` is set; the helper uses 2 malloc arenas on Linux;
  `scripts/measure_tasks.py` measures helper start, memory, task time and UI frame lateness.
- **Background tasks, real helper** (task service step 4): with `LUNA_TASKS=service` and the new
  `[agents]` extra (`openai-agents`), tasks run in a separate helper process
  (`python -m luna_core.agents`, started only when needed, stopped after 5 idle minutes, lower
  CPU priority; about 100 MB while running). A research specialist (8 turns, 2 min, $0.05 per
  task) with usage counted in the daily budget. Helpers can only use Luna's tools through Luna:
  every call is checked by her permission rules; tools that need your OK pause the task and
  Luna asks "My task needs your OK: ..." (Yes/No box; unanswered for 12 h means no). Motion
  tools are never available to helpers. Model per helper: `LUNA_AGENT_MODEL_<ROLE>`. Default
  Luna (no `LUNA_TASKS`) is unchanged and never loads the SDK.
- **Background tasks, core** (task service step 3, `luna_core/tasks/`): task model, SQLite
  store (`tasks`, `steps`, `approvals` in `~/.luna/tasks.db`), service protocol, `TaskClient`
  (limits, budget, crash recovery) and `Announcer` (when Luna tells you a result; quiet hours).
  The AI can start, check and cancel tasks; "Luna, what are you working on?" / "Luna, cancel
  the task" (Swedish too). New Tasks page and Settings > AI: Background tasks, Quiet hours,
  Task history (7/30/90 days). No real helper yet: try it with `LUNA_TASKS=demo`
  (a fake research helper). New preferences `tasks`, `quiet_hours`, `task_history_days`;
  new pack controls `task_status`, `cancel_task`.
- **Yes/No confirmation** (`luna_core/tasks/confirm.py`): when Luna asks "Shall I remember
  that?", a Yes/No box appears. Say "yes"/"no" (pack `answers`, English and Swedish) or tap;
  Yes saves exactly what is shown, without another AI call. No answer in 60 s, or talking about
  something else, means no. `sensitive` tools (none yet) need a tap on Yes; `motion` is never
  asked. New tool field `Tool.confirm`, pack field `answers`.
- **Daily AI budget** (Settings > AI, default $0.30): Luna counts the estimated cost of answers,
  web searches and online listening (`luna_core/tasks/budget.py`, list prices in
  `tasks/prices.json`, override with `LUNA_AI_PRICES`). At 80 % she mentions it once; when it is
  used up she says so and keeps doing offline commands until midnight. Daily totals (numbers
  only) are kept in `~/.luna/tasks.db` (`LUNA_TASKS_FILE`). New preference `ai_daily_budget`.
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
