# Luna roadmap

Where Luna is going after Luna Core 1.0: from a talking robot face to a modular, supervised,
mobile assistant. Luna stays the single voice and face; specialist agents, Home Assistant,
the scheduler, motion and vision work behind her.

Decisions are recorded in [docs/adr/](adr/). A phase starts only after its design is approved,
and a release is tagged only after the owner approves it ([RELEASING.md](RELEASING.md)).

## Principles
- **Three paths.** *Fast*: offline commands, wake word and "stop" (no network, no cost).
  *Talk*: one cheap model call with tools, streamed sentence by sentence (today's `LunaBrain`).
  *Task*: background specialist agents for work that needs many steps
  ([design](design/TASK_SERVICE.md)). The task path never blocks or interrupts the talk path.
- **Safety and permissions are code, never the model.** Physical E-stop → ESP32 watchdog →
  Pi safety gate → `PermissionPolicy` → AI. The AI asks for bounded goals, never raw motor speeds.
- **Offline first, privacy by default.** Recognition, speech, commands and reminders work
  without the internet; cloud use is opt-in, budgeted and sends text, not audio.
- **Core stays generic.** Interfaces (base, camera, tasks, scheduler, health, budget) live in
  Luna Core; integrations (Home Assistant, calendars, music services) are plugins in app repos
  such as `luna-home`.
- **Home Assistant runs on its own device**, not on the robot ([ADR-0001](adr/0001-home-assistant-separate-device.md)).
- **Measure on the Pi** before and after each phase ([BASELINE.md](BASELINE.md)).

## Phases
Each phase ends with a demo, green CI and an approved, tagged minor release.

| Phase | Contents | Gate | Status |
|---|---|---|---|
| 0. Baseline and design | Pi measurements in `BASELINE.md`; this roadmap; ADRs; task-service design | Design approved; Pi numbers filled in | In progress |
| 1. Multi-agent foundation | Usage meter and cost guard, general confirmation, task core, task service, research specialist, plugin specialists ([steps](design/TASK_SERVICE.md#13-development-steps)) | Talk latency unchanged on the Pi; budget enforced | Planned |
| 2. Home Assistant | `luna-home` plugin: MCP Server for actions, WebSocket for live state; exposed entities only; confirmation for sensitive devices; announcements; Robot Setup module for the token | "Turn on the kitchen light" < 2 s; locks need a touch confirmation | Planned |
| 3. Daily life | Offline reminders and timers (SQLite scheduler); calendars through Home Assistant; music on the second speaker (Music Assistant) with ducking; opt-in conversation summaries | Reminders survive a reboot and work offline | Planned |
| 4. Mobile base | Mecanum chassis, power and E-stop; ESP32 firmware (PID on encoders, 300 ms watchdog, bumpers, cliff sensors, battery); `Base.move(vx, vy, wz)` interface with mock and simulator; touch joystick; bounded voice moves | Watchdog, E-stop and cliff tests pass on the bench and floor | Hardware later |
| 5. Vision | Camera Module 3 Wide, pan-tilt, face following, person and object detection on demand, "what do you see" (cloud, on request), camera indicator | 2–5 fps vision without slowing the conversation | Hardware later |
| 6. Navigation | LiDAR, reactive avoidance, "come here" (voice direction + person detection), mapping evaluation (custom vs. ROS 2 slam_toolbox in Docker), named places | 30 min supervised driving without a collision | Hardware later |
| 7. Autonomy | Planner and maintenance specialists, health monitor, sandboxed code runner, self-improvement through pull requests that the owner merges | Every action logged; every code change approved by a human | Planned |

## Things that stay true in every phase
- `motion` and `sensitive` actions are never run on the AI's word alone.
- New devices go through `luna_core/hardware/` with a mock; new features have tests that never
  call the real API.
- Luna must still start without optional extras (`[voice]`, `[speech]`, `[ai]`, later `[agents]`).
- No secrets, personal data or home paths in the repository.
