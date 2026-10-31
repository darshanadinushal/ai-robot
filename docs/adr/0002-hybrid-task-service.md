# ADR-0002: Hybrid multi-agent task service beside the talk path

- Status: Accepted (2026-10-31)
- Design: [docs/design/TASK_SERVICE.md](../design/TASK_SERVICE.md)

## Context
Luna should coordinate specialist agents (research, planning, home, maintenance...) while she
stays the single voice and face. Today's conversation (`LunaBrain`, `Conversation`) is tuned
for speed: streaming sentences, a cached prompt, barge-in, one question at a time. Multi-agent
runs take 10-60 s and several model calls, so they must not replace or block it.

## Decision
1. **Two paths.** The talk path stays as it is. Work that needs many steps goes to a background
   **task service** (OpenAI Agents SDK, supervisor with specialists as tools).
2. **Separate process.** The service is a child process of Luna (JSON lines over stdin/stdout,
   versioned protocol), installed with the optional `[agents]` extra.
3. **Brains in the service, hands in Luna.** Every action a specialist wants is sent back to
   Luna and runs through her `ToolExecutor` and `PermissionPolicy`. `motion` is never allowed;
   `sensitive` needs a touch confirmation.
4. **Delegation is automatic**: the talk model calls `start_task` when a request needs several
   searches or steps, within the budget.
5. **Hard daily AI budget**, enforced in code for the talk and task paths together; default
   **USD 0.30 per day**, changeable in Settings.
6. **Quiet hours** 22:00-07:00 by default: results are shown on screen, not spoken.
7. **Task history kept 30 days**, visible and deletable.
8. Agents SDK tracing to OpenAI is off; a local trace processor writes the audit log.
9. Specialists start small: research first; others (home, planner, maintenance, code runner)
   in later phases and from plugins.

## Consequences
- Conversation latency and the prompt cache are unaffected; a crash in the service never
  stops the face or voice.
- About 60-150 MB more RAM on the Pi (to be measured); more moving parts (process, protocol,
  database), kept testable with a fake service and fake models.
- Costs per task are several model calls; the budget and per-task limits cap them.
- The Agents SDK is pre-1.0: it is pinned, and only `luna_core/agents/` imports it.
- AGENTS.md rules stay: policy in code, no secrets in logs, tests never call the real API.
