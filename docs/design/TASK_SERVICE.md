# Task service: hybrid multi-agent foundation (design)

Status: **proposed for review** (phase 1 of [ROADMAP.md](../ROADMAP.md); decision:
[ADR-0002](../adr/0002-hybrid-task-service.md)). Nothing here is implemented yet.

## 1. Goals and non-goals
Goals
- Luna can hand work that needs several steps (research, comparisons, plans, later home
  routines and maintenance) to specialist agents and keep talking meanwhile.
- Luna stays the only voice and face: she starts tasks, asks for approvals and reports results.
- Every action is checked in code by the same permission policy as today; a hard daily budget.
- Works on the Raspberry Pi 5 without slowing the face or the conversation.
- Testable without the network, the real API or the Agents SDK (fakes).

Non-goals (this phase)
- Replacing `LunaBrain` / the talk path, realtime speech, motion, vision, Home Assistant
  (phase 2), running generated code (phase 7), local models (later; the design allows them).

## 2. Hybrid in four ways
| Hybrid of | Here |
|---|---|
| Deterministic + AI | Policy, budget, approvals, announcements, lifecycle are code; models only propose |
| Talk + task path | Quick answers stay in `LunaBrain`; multi-step work runs in the task service |
| Brains + hands | Agents run in the service; actions run in Luna through `ToolExecutor` |
| Cloud + local | OpenAI models now; the SDK model interface allows local models later |

Orchestration is hybrid too: open-ended work uses a supervisor with specialists as tools
(`Agent.as_tool`); known routines (later, e.g. a morning briefing) are fixed workflows in code.

## 3. Overview
```
Luna process                                         Task service (child process)
──────────────────────────────────────────────       ─────────────────────────────────────
Conversation ── talk tools: start_task,               asyncio loop
   │            task_status, cancel_task              Supervisor ─as_tool─▶ specialists
   ▼                                                  RunHooks: usage per model call
TaskClient ◀──── JSON lines (stdin/stdout) ────────▶  bridge: remote tools (check/call)
   │                                                  local trace processor (no OpenAI upload)
   ├─ TaskStore  (~/.luna/tasks.db, only writer)      stateless: paused runs go to Luna
   ├─ CostGuard + UsageMeter (talk + tasks)
   ├─ Announcer (when to speak)
   └─ ToolExecutor + PermissionPolicy (all actions)
```
Luna owns all state; the service is stateless and can be restarted at any time.

## 4. Code layout
```
luna_core/tasks/            pure: no SDK, no pygame; always installed
  model.py       Task, TaskState, Specialist, Limits
  store.py       TaskStore (SQLite, 0600; LUNA_TASKS_FILE for tests)
  budget.py      UsageMeter, CostGuard, prices.json loader
  prices.json    USD per 1M tokens per model (input, cached, output)
  announce.py    Announcer
  confirm.py     Confirmation (general yes/no, touch-only)            [step 2]
  protocol.py    encode/decode/validate messages, PROTOCOL_VERSION
  client.py      TaskClient (process, restart, timeouts); FakeTaskService for tests
luna_core/agents/           optional extra [agents] (openai-agents pinned <next minor)
  __main__.py    `python -m luna_core.agents`: stdin/stdout loop
  service.py     task runner, concurrency, cancel
  specialists.py builds SDK agents from Specialist specs; supervisor
  bridge.py      remote function tools (check + call through Luna)
  hooks.py       usage and limits (RunHooks)
  tracing.py     local trace processor -> `step` messages
  models.py      role -> model name (LUNA_AGENT_MODEL_<ROLE>)
```
Only `luna_core/agents/` imports the SDK. Effects added to `assistant_core/events.py`:
`TaskStarted(task, goal)`, `Announce(text, task)`, `AskApproval(confirmation)`.

## 5. Task lifecycle
```
queued ─▶ running ─▶ done
             │  ▲  ├─▶ failed       (error class, budget, time, no network)
             ▼  │  ├─▶ cancelled    (user, budget)
      waiting_approval  └─▶ interrupted (service or Luna stopped while running)
```
- Luna starts with: `running` → `interrupted` (announced once: "I was interrupted while ...;
  ask me again"); `waiting_approval` stays waiting (its paused run is stored).
- At most `max_concurrent` running tasks (1 on the Pi, setting); others wait `queued`.
- History kept **30 days** (setting): tasks, steps and approvals are deleted after that;
  daily usage totals (numbers only) are kept for a year.

## 6. Protocol
- One JSON object per line, UTF-8, field `t` = message type. Max line 64 KB, except `paused`
  (max 2 MB). Unknown fields are ignored; unknown types are logged and dropped.
- The service sends `ready` with its protocol version; a different major version means Luna
  stops it and says tasks are unavailable ("update Luna").
- Never in messages: API keys, API error messages (class names only), raw audio.

Luna → service
| t | fields | meaning |
|---|---|---|
| `start` | id, goal, specialist, language, context, limits | run a task |
| `resume` | id, state, item, approved | continue a paused run with the user's answer |
| `cancel` | id, reason | stop now (user, budget, shutdown) |
| `check_result` | id, call, verdict (`allow`/`refuse`/`confirm`), reason, summary, touch | answer to `check` |
| `tool_result` | id, call, ok, output | answer to `tool_call` |
| `shutdown` | — | finish quickly and exit |

Service → Luna
| t | fields | meaning |
|---|---|---|
| `ready` | version, specialists | started |
| `accepted` | id | task is running |
| `step` | id, kind, name, summary | audit entry (model call, tool, web search, specialist) |
| `usage` | id, model, input, cached, output | after every model call |
| `check` | id, call, name, args | may this bridged tool run? (before the SDK's approval step) |
| `tool_call` | id, call, name, args | run this tool in Luna (after allow/approval) |
| `paused` | id, item, summary, touch, state | waiting for approval; `state` = `RunState.to_string()` |
| `progress` | id, text | rare, short ("Comparing three sources") |
| `done` | id, summary, details | finished; summary is speakable (≤ 300 chars) |
| `failed` | id, error | error class or `budget` / `time` / `turns` / `offline` |

Timeouts: `check`/`tool_call` answers within 10 s (else the call fails for the agent).
No `ready` within 20 s or no answer to a ping for 30 s: the service is restarted.

## 7. Store schema (`~/.luna/tasks.db`, mode 0600)
```
tasks(id, created, updated, goal, specialist, language, state, summary, details, error,
      cost_usd, tainted, run_state, announced)
steps(task_id, at, kind, name, summary)                       -- audit, no secrets
approvals(task_id, item, call_id, tool, args_hash, risk, touch, asked, answer, answered, by)
usage(day, path, model, input, cached, output, usd)            -- path: talk | task
```

## 8. Delegation from the talk path
- Talk tools (registered only when tasks are available and on):
  `start_task(goal: string ≤ 500, specialist: enum)`, `task_status()`,
  `cancel_task(which: "latest" | id)`. The enum and descriptions come from the specialists.
- Fixed-instruction rule (keeps the prompt cache): *"For requests that need several web
  searches, comparisons or steps, call start_task and say in one sentence that you will report
  back. Answer quick questions yourself."* Delegation is **automatic** (decided by the model),
  limited by the budget and `max_concurrent`.
- Code before `start`: budget check, length limit, specialist exists, tasks on. Refusals return
  a `ToolResult` the model turns into a short sentence.
- Context sent: goal, language, and only what the specialist's permissions allow (user name or
  town from the profile; memories only with `memory`). Never the whole conversation.
- Offline voice controls (pack `controls`): "cancel the task" / "avbryt uppgiften", "what are
  you working on" / "vad jobbar du med". They work without the network.

## 9. Announcements (`Announcer`, pure)
Inputs each frame: time, speaking, listening, streaming, follow-up open, last user activity,
quiet hours, pending events. At most one announcement per free moment:
1. Never while Luna speaks, the user speaks, an answer streams or a follow-up window is open;
   at least 20 s between announcements.
2. Order: approval questions, then results, then failures; `progress` is spoken at most once
   per task and only after 30 s.
3. **Quiet hours 22:00-07:00** (setting): nothing is spoken; a badge and the Tasks page show it.
   Spoken later when the user next talks to Luna ("I have one result for you").
4. User away (no voice or touch for 10 min): held until the next wake word.
5. Barge-in or "stop" silences an announcement; it does not cancel the task.
6. After a result is spoken, its summary (≤ 600 chars) goes into `TurnInstructions.notes` for
   15 minutes, so "which one is cheapest?" works in the talk path with the prompt cache intact.

## 10. Permissions, approvals and prompt injection
- **Bridge:** specialists' action tools are proxies. `check` → Luna validates the arguments
  against the tool schema and asks `PermissionPolicy`:
  - `allow` → the SDK runs the proxy → `tool_call` → `ToolExecutor` in Luna (plugins guarded);
  - `refuse` → the agent gets the reason;
  - `confirm` → the SDK's `needs_approval` pauses the run → `paused` → Luna asks the user.
- **Confirmation (step 2):** spoken question plus Yes/No on screen; no answer in 60 s = no.
  `sensitive` tools need a **touch** (voice alone could be a TV or a visitor). `motion` is never
  allowed through this path. An approval covers that one call (tool + argument hash) only.
- **Taint rule:** once a task has read untrusted content (web search, web pages, later
  messages or emails), every later non-`local` action in that task needs confirmation. Research
  specialists have no action tools at all; their output is passed on as data.
- Secrets live only in the environment of the process that needs them; the audit log keeps
  tool names and short argument summaries.
- Agents SDK tracing to OpenAI is disabled (`set_tracing_disabled(True)`); a local trace
  processor sends `step` messages. `LUNA_AGENT_TRACING=openai` enables upload for debugging.

## 11. Budget (`UsageMeter`, `CostGuard`)
- One daily budget for the talk and task paths: **USD 0.30** by default
  (`LUNA_AI_DAILY_BUDGET`, Settings). The day starts at local midnight.
- Cost = tokens × `prices.json` (input, cached input, output per 1M tokens). An unknown model
  is priced at the most expensive known model (logged once). Hosted tool fees (web search) are
  added per call from the same file. Prices change: the file is data, updated with releases.
- Checks: before a talk question or task starts (refused if the day is spent); after every
  `usage` message (the task is cancelled when it passes its own `max_usd` or the day's budget).
- Per-task limits (specialist defaults, capped by settings): `max_turns` 8, `max_seconds` 120,
  `max_usd` 0.05; the service enforces turns and time itself too.
- Spent: "I've reached today's AI budget." Offline commands, reminders and the face still work.
  At 80 % Luna mentions it once.

## 12. Specialists
A `Specialist` is data: `name`, `title` (screen, translated), `description` (for the tool enum),
`instructions`, `model_role`, `tools` (names in Luna's registry), `hosted` (e.g. `web_search`),
`permissions`, `limits`, `reads_untrusted`. The supervisor gets the specialists as tools and
returns one speakable summary plus details.

First specialist: **research** (hosted web search, `reads_untrusted`, no action tools,
`gpt-6-luna` by default). Later: home (`luna-home` plugin, phase 2), planner (phase 3),
maintenance (read-only health, phase 7), code runner (sandbox, phase 7).
Plugins add specialists with `PluginContext.add_specialist(...)` (step 7, new public API,
`agents` permission; their tools stay in Luna's process).

Models per role: `LUNA_AGENT_MODEL_SUPERVISOR`, `LUNA_AGENT_MODEL_RESEARCH`, ... default
`LUNA_AI_MODEL`.

## 13. Development steps
Each step: one reviewed change, tests, green CI, docs and CHANGELOG. Release branches are
tagged only after approval.

| # | Step | Delivers | Done when |
|---|---|---|---|
| 0 | Design and baseline | This document, ROADMAP, ADRs; Pi numbers in BASELINE.md | Design approved; Pi measured |
| 1 | Usage meter and cost guard | `tasks/budget.py`, `prices.json`, `usage` table; talk path counted and refused over budget; Settings: daily budget | Tests: over-budget refusal, day rollover, unknown model price |
| 2 | General confirmation | `tasks/confirm.py`; pack `controls.yes/no`; Yes/No overlay; touch-only; timeout = no; memory grants moved onto it | Memory tests unchanged and green; new confirmation tests |
| 3 | Task core without the SDK | model, store, protocol, Announcer, `TaskClient` + `FakeTaskService`; talk tools; voice controls; Tasks page; Settings (tasks on/off, quiet hours, history days) | End-to-end with the fake service: delegate, announce, follow-up, cancel, restart recovery, quiet hours |
| 4 | Task service | `[agents]` extra; `luna_core/agents/`; bridge; hooks; local tracing; paused runs stored | Fake-model tests: approval, refusal, cancel, budget cut, turn/time limits, service crash |
| 5 | Research specialist | Specialist, taint rule, summaries | Prompt-injection test; manual run on the laptop with a real key |
| 6 | Pi hardening, release 1.1.0 | RAM/CPU/latency on the Pi; docs (README, ARCHITECTURE, AGENTS) | Talk latency unchanged on the Pi |
| 7 | Plugin specialists, release 1.2.0 | `add_specialist`, `agents` permission, docs/PLUGINS.md, example | Plugin API compatibility tests |

## 14. Test plan
- Pure tests (`tests/test_tasks.py`): lifecycle transitions, store retention, protocol
  validation and limits, Announcer timing rules, CostGuard maths and rollover, taint rule.
- Client tests with `FakeTaskService` (no subprocess): every message path and timeout.
- Process tests: a scripted fake service as a real child process (crash, slow start, garbage
  output, version mismatch).
- Service tests (`tests/test_agents.py`, skipped without the SDK): fake SDK `Model`s; never the
  real API. One CI job installs `[agents]`.
- Conversation cases: delegation sentences in `tests/conversation_cases.json`.
- Security: a research result that says "turn off all lights" never causes a device call; a
  `sensitive` tool cannot be approved by voice; `motion` is always refused.
- Pi: `scripts/measure_startup.py` and talk latency with and without the service running.

## 15. Failure handling
| Failure | Behaviour |
|---|---|
| `[agents]` not installed / AI not configured / tasks off | No talk tools; "I can't do tasks on this robot yet." |
| Service crashes | Running tasks `interrupted` and announced; restart after 2, 10, 60 s; after 3 failures in 10 min tasks are off until restart |
| No network / model error | Task `failed` (error class), one friendly sentence |
| Budget reached | Running task cancelled, partial work not announced as a result |
| Approval not answered | Treated as no after 60 s; the agent is told and finishes or fails |
| Luna restarts | Waiting approvals asked again; running tasks `interrupted` |
| Protocol garbage | Line dropped and logged; repeated garbage restarts the service |

## 16. Later
Local models (AI HAT+ 2, Ollama-compatible endpoints) per role; event-triggered tasks (Home
Assistant events, schedules); fixed workflows (morning briefing); moving the transport to a
local bus (MQTT) when motion and vision processes arrive; a Robot Setup "Agents" feature.
