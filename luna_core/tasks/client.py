"""TaskClient: Luna's side of the task service (pure: no pygame, no SDK; runs on the UI thread).

The client owns the tasks (TaskStore) and talks to the service through a transport: an object
with `send(line)`, `receive()` (the complete lines that arrived, never blocking), `alive` and
`close()`. `connect()` makes one (tasks/process.py: the child process; FakeTaskService in
tests and the demo). Every check is code: tasks on, budget, goal length, known specialist, at
most MAX_ACTIVE tasks; the money is counted here (usage messages), over a task's limit or the
daily budget it is stopped.

`lazy`: the service is started only when a task needs it and stopped after IDLE_STOP seconds
without work, so it costs no memory or CPU while Luna just talks. Until it runs, the AI is
offered the specialists of `catalog` (tasks/catalog.py).

Actions (the bridge): a specialist never acts itself. Before one of Luna's tools runs, the
service sends `check`; Luna validates the arguments and asks the same PermissionPolicy as the
talk path (`executor()` gives the conversation's current ToolExecutor). Only tools named in
Luna's own catalogue for that specialist exist for it. `allow` lets exactly that call run
(`tool_call`, here on the UI thread); `confirm` makes the run pause: the paused run is stored
(state `waiting_approval`) and an `approval` TaskEvent asks the user (Yes/No). The answer
resumes it; a yes covers that one call (tool + argument hash) only. `motion` is never allowed.

A lost service interrupts its running tasks and is restarted after 2, 10 and 60 s; after 3
failures in 10 minutes (or a service with another protocol version) tasks stay off until Luna
restarts. A task waiting for approval needs no service and survives both.
"""
import logging
import time
from dataclasses import dataclass
from datetime import datetime

from .confirm import call_key
from .model import (CANCELLED, DETAILS_CHARS, DONE, FAILED, GOAL_CHARS, INTERRUPTED, QUEUED,
                    RUNNING, SUMMARY_CHARS, WAITING, Limits, Specialist, Task, clean, new_id,
                    speakable)
from .protocol import (FROM_SERVICE, PROTOCOL_VERSION, TO_SERVICE, ProtocolError,
                       compatible, decode, encode)
from .store import HISTORY_DAYS

log = logging.getLogger("luna.tasks")

MAX_ACTIVE = 3
READY_WAIT = 20.0
IDLE_STOP = 300.0     # a lazy service with nothing to do is stopped after this long
BACKOFF = (2.0, 10.0, 60.0)
CRASH_WINDOW = 600.0
CRASHES = 3
GARBAGE = 5
GRACE = 30.0          # after a task's max_seconds Luna stops it herself
REPORT_HOURS = 12     # older unreported results are not announced after a restart
LIMIT_CAP = Limits()  # the settings' cap on every specialist's limits
OUTPUT_CHARS = 8000   # a tool's answer sent back to the agent
ANSWER_HOURS = 12     # a question nobody answered within this long means no
EXPIRY_CHECK = 60.0   # seconds between checks for such questions


@dataclass(frozen=True)
class Approval:
    """A question for the user about one call of a task (what the Yes/No box shows)."""
    task_id: str
    call: str
    key: str           # call_key(tool, arguments)
    label: str
    detail: str = ""
    touch: bool = False


@dataclass(frozen=True)
class TaskEvent:
    """Something the conversation should know: started, done, failed, cancelled,
    interrupted, progress (`text`), approval (`approval`) or effects (a tool the task was
    allowed to use changed something: `effects` for the app)."""
    kind: str
    task: Task
    text: str = ""
    approval: Approval = None
    effects: tuple = ()


def _specialists(items, known=None):
    """The specialists a `ready` message lists. With `known` (Luna's catalogue) only those, with
    Luna's tools and limits: a service cannot give itself more."""
    found = {}
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        name = clean(item["name"], 40)
        if not name:
            continue
        if known is not None:
            if name in known:
                found[name] = known[name]
            continue
        limits = item.get("limits") if isinstance(item.get("limits"), dict) else {}
        try:
            spec_limits = Limits(int(limits.get("max_turns", 8)),
                                 float(limits.get("max_seconds", 120.0)),
                                 float(limits.get("max_usd", 0.05))).within(LIMIT_CAP)
        except (TypeError, ValueError):
            spec_limits = Limits()
        tools = item.get("tools") if isinstance(item.get("tools"), list) else []
        found[name] = Specialist(name, clean(item.get("description"), 300),
                                 clean(item.get("title") or name, 40), spec_limits,
                                 tools=tuple(clean(tool, 80) for tool in tools
                                             if isinstance(tool, str)),
                                 reads_untrusted=True)  # Luna cannot vouch for it
    return found


class TaskClient:
    def __init__(self, connect, store, meter=None, guard=None, enabled=True,
                 history_days=HISTORY_DAYS, max_active=MAX_ACTIVE, catalog=None, lazy=False,
                 idle_stop=IDLE_STOP):
        self.connect = connect
        self.store = store
        self.meter = meter
        self.guard = guard
        self.enabled = enabled
        self.history_days = history_days
        self.max_active = max_active
        self.catalog = dict(catalog) if catalog is not None else None
        self.lazy = lazy
        self.idle_stop = idle_stop
        self.executor = None     # function() -> the conversation's ToolExecutor (or None)
        self.transport = None
        self.ready = False
        self.disabled = ""       # why tasks are off until restart ("crashes", "version")
        self.specialists = dict(self.catalog) if lazy and self.catalog else {}
        self.crashes = []
        self.retry_at = 0.0
        self.connected_at = None
        self.worked_at = 0.0     # last time the service had something to do
        self.garbage = 0
        self.outbox = []         # start/resume messages waiting for the service to be ready
        self.started = {}        # task id -> t it started running (this run)
        self.limits = {}         # task id -> Limits
        self.permitted = {}      # (task id, call) -> call_key Luna allowed in `check`
        self.events = []
        self.t = 0.0
        self.expiry_at = 0.0
        store.recover()
        store.purge(history_days)
        old = (store.clock().timestamp() - REPORT_HOURS * 3600)
        for task in store.unannounced():
            if _stamp(task.updated) < old:
                store.update(task.id, announced=True)

    # --- state ---------------------------------------------------------------------------
    @property
    def available(self):
        if not (self.enabled and not self.disabled and self.specialists):
            return False
        return self.lazy or bool(self.ready and self.transport and self.transport.alive)

    @property
    def running(self):
        """The service process is up."""
        return self.transport is not None

    def active(self):
        return self.store.active()

    def recent(self, limit=20):
        return self.store.recent(limit)

    def unannounced(self):
        return self.store.unannounced()

    def mark_announced(self, task_id):
        self.store.update(task_id, announced=True)

    def waiting(self):
        """The open questions of tasks waiting for approval (asked again after a restart)."""
        found = []
        for task in self.store.active():
            row = self.store.pending_approval(task.id) if task.state == WAITING else None
            if row is not None:
                found.append(_approval(row))
        return found

    def set_enabled(self, on):
        """Settings: tasks on or off (off stops the running ones and the service)."""
        if on == self.enabled:
            return
        self.enabled = on
        if not on:
            for task in self.store.active():
                self._stop(task, CANCELLED, "off", announced=True)
            self.outbox = []
            self._send({"t": "shutdown"})
            self._close()

    def set_history(self, days):
        self.history_days = days
        self.store.purge(days)

    # --- requests from the conversation -------------------------------------------------
    def begin(self, goal, specialist, language="en", t=None):
        """Start a task: (task, "") or (None, reason): unavailable, budget, goal, specialist,
        busy."""
        t = self.t if t is None else t
        text = clean(goal, GOAL_CHARS + 1)
        if not self.available:
            return None, "unavailable"
        if self.guard is not None and not self.guard.allows():
            return None, "budget"
        if not text or len(text) > GOAL_CHARS:
            return None, "goal"
        spec = self.specialists.get(specialist)
        if spec is None:
            return None, "specialist"
        if len(self.store.active()) >= self.max_active:
            return None, "busy"
        task = self.store.add(Task(new_id(), text, spec.name, clean(language, 8) or "en",
                                   tainted=spec.reads_untrusted))
        limits = spec.limits.within(LIMIT_CAP)
        message = {"t": "start", "id": task.id, "goal": task.goal, "specialist": spec.name,
                   "language": task.language, "context": self._context(spec),
                   "limits": limits.as_dict()}
        if not self._deliver(message):
            self.store.move(task.id, FAILED, error="unavailable", announced=True)
            self._lost(t)
            return None, "unavailable"
        self.started[task.id] = t
        self.limits[task.id] = limits
        self.worked_at = t
        log.info("Luna tasks: started %s (%s)", task.id, spec.name)
        return task, ""

    def cancel(self, which="latest"):
        """Stop the newest active task (or the one with id `which`); the task or None."""
        active = self.store.active()
        if which != "latest":
            active = [task for task in active if task.id == which]
        if not active:
            return None
        task = active[-1]
        self.outbox = [m for m in self.outbox if m["id"] != task.id]
        if self.ready:
            self._send({"t": "cancel", "id": task.id, "reason": "user"})
        return self._stop(task, CANCELLED, "user", announced=True)

    def answer(self, task_id, call, approved, by):
        """The user answered a task's question (`by`: voice, tap, timeout): resume the run.
        True if the task was waiting for it."""
        task = self.store.get(task_id)
        row = self.store.pending_approval(task_id, call) if task is not None else None
        if task is None or task.state != WAITING or row is None:
            return False
        self.store.answer_approval(task_id, call, "yes" if approved else "no", by)
        self.store.add_step(task_id, "approval", row["tool"], ("yes" if approved else "no")
                            + " (%s)" % by)
        spec = self.specialists.get(task.specialist) or (self.catalog or {}).get(task.specialist)
        if spec is None or not self.enabled or self.disabled:
            self._stop(task, FAILED, "unavailable", event="failed")
            return True
        limits = spec.limits.within(LIMIT_CAP)
        message = {"t": "resume", "id": task_id, "state": task.run_state, "item": call,
                   "approved": bool(approved), "specialist": spec.name,
                   "language": task.language, "context": self._context(spec),
                   "limits": limits.as_dict()}
        self.store.move(task_id, RUNNING, run_state="")
        if not self._deliver(message):
            self._stop(self.store.get(task_id), INTERRUPTED, "interrupted", event="interrupted")
            return True
        self.started[task_id] = self.t
        self.limits[task_id] = limits
        self.worked_at = self.t
        log.info("Luna tasks: %s resumed (user said %s)", task_id, "yes" if approved else "no")
        return True

    # --- the service ---------------------------------------------------------------------
    def poll(self, t):
        """Run the connection and handle what the service sent; returns TaskEvents."""
        self.t = t
        if self.enabled and not self.disabled:
            wanted = not self.lazy or self.outbox or self.started
            if self.transport is None and wanted and t >= self.retry_at:
                self._connect(t)
            elif self.transport is not None and not self.transport.alive:
                self._lost(t)
            if self.transport is not None:
                self._read(t)
            if (self.transport is not None and not self.ready
                    and t - self.connected_at > READY_WAIT):
                log.warning("Luna tasks: the service did not start")
                self._lost(t)
            if (self.lazy and self.transport is not None and self.ready and not self.outbox
                    and not self.started and t - self.worked_at > self.idle_stop):
                log.info("Luna tasks: service stopped (nothing to do)")
                self._send({"t": "shutdown"})
                self._close()
            self._timeouts(t)
        if t >= self.expiry_at:
            self.expiry_at = t + EXPIRY_CHECK
            self._expire_questions()
        events, self.events = self.events, []
        return events

    def close(self):
        self._send({"t": "shutdown"})
        self._close()
        self.store.close()

    def _context(self, spec):
        """What the service may know besides the goal: the schemas of Luna's tools this
        specialist may ask to use (nothing from the conversation)."""
        executor = self.executor() if self.executor is not None else None
        tools = []
        for name in spec.tools:
            tool = executor.registry.get(name) if executor is not None else None
            if tool is not None and tool.risk != "motion":
                tools.append({"name": tool.name, "description": tool.description,
                              "parameters": tool.parameters})
        return {"tools": tools}

    def _deliver(self, message):
        """Send a start/resume now, or (lazy) once the service is ready."""
        if self.ready and self.transport is not None:
            return self._send(message)
        if self.lazy:
            self.outbox.append(message)
            return True
        return False

    def _connect(self, t):
        try:
            self.transport = self.connect()
        except (OSError, ValueError) as error:
            log.warning("Luna tasks: could not start the service (%s)", type(error).__name__)
            self.transport = None
            self._crashed(t)
            return
        self.connected_at = t
        self.worked_at = t
        self.ready = False
        self.garbage = 0

    def _close(self):
        if self.transport is not None:
            try:
                self.transport.close()
            except OSError:
                pass
        self.transport = None
        self.ready = False
        self.permitted = {}

    def _lost(self, t):
        """The service stopped: interrupt what it was doing and plan a restart."""
        self._close()
        self.outbox = []
        self._interrupt_all()
        self._crashed(t)

    def _interrupt_all(self):
        for task in self.store.active():
            if task.state != WAITING:
                self._stop(task, INTERRUPTED, "interrupted", event="interrupted")

    def _crashed(self, t):
        self.crashes = [c for c in self.crashes if t - c < CRASH_WINDOW] + [t]
        if len(self.crashes) >= CRASHES:
            self.disabled = "crashes"
            self.outbox = []
            self._interrupt_all()
            log.warning("Luna tasks: the service keeps failing; tasks are off until restart")
        else:
            self.retry_at = t + BACKOFF[len(self.crashes) - 1]

    def _send(self, message):
        if self.transport is None:
            return False
        try:
            self.transport.send(encode(message, TO_SERVICE))
            return True
        except (OSError, ProtocolError) as error:
            log.warning("Luna tasks: could not send %s (%s)", message.get("t"),
                        type(error).__name__)
            return False

    def _read(self, t):
        try:
            lines = self.transport.receive()
        except OSError as error:
            log.warning("Luna tasks: the service connection failed (%s)", type(error).__name__)
            self._lost(t)
            return
        for line in lines:
            try:
                message = decode(line, FROM_SERVICE)
            except ProtocolError as error:
                self.garbage += 1
                log.warning("Luna tasks: dropped a message (%s)", error)
                if self.garbage >= GARBAGE:
                    self._lost(t)
                    return
                continue
            self._handle(message, t)
            if self.transport is None:
                return

    def _handle(self, message, t):
        kind = message["t"]
        if kind == "ready":
            if not compatible(message["version"]):
                log.warning("Luna tasks: service protocol %s does not match %s",
                            clean(message["version"], 20), PROTOCOL_VERSION)
                self.disabled = "version"
                self._close()
                self.outbox = []
                self._interrupt_all()
                return
            self.specialists = _specialists(message["specialists"], self.catalog)
            self.ready = True
            outbox, self.outbox = self.outbox, []
            for queued in outbox:
                self._send(queued)
            return
        self.worked_at = t
        task = self.store.get(message["id"])
        if task is None or not task.active:
            if kind in ("check", "tool_call"):  # never leave the agent waiting
                self._refuse(message, "the task was stopped")
            return
        if kind == "accepted":
            self.store.move(task.id, RUNNING)
            if task.id in self.started:
                self.started[task.id] = t  # its time starts now (it may have waited its turn)
        elif kind == "step":
            self.store.add_step(task.id, message["kind"], message["name"], message["summary"])
            if message["kind"] == "web":  # a hosted web search: billed per call, untrusted text
                task = self.store.update(task.id, tainted=True)
                self._charge(task, self.meter.add_calls("task", "web_search", 1)
                             if self.meter is not None else 0.0)
        elif kind == "usage":
            self._usage(task, message)
        elif kind == "check":
            self._check(task, message)
        elif kind == "tool_call":
            self._tool_call(task, message)
        elif kind == "paused":
            self._paused(task, message)
        elif kind == "progress":
            text = clean(message["text"], SUMMARY_CHARS)
            if text:
                self.events.append(TaskEvent("progress", task, text))
        elif kind == "done":
            moved = self.store.move(task.id, DONE, summary=speakable(message["summary"]),
                                    details=str(message["details"])[:DETAILS_CHARS])
            self._ended(moved, "done")
        elif kind == "failed":
            self._stop(task, FAILED, clean(message["error"], 40) or "error", event="failed")

    # --- the bridge: Luna's tools for a task ----------------------------------------------
    def _tool(self, task, name):
        """The tool `name` if this task's specialist may use it (else None)."""
        spec = (self.catalog or self.specialists).get(task.specialist)
        executor = self.executor() if self.executor is not None else None
        if spec is None or executor is None or name not in spec.tools:
            return None, None
        return executor.registry.get(name), executor

    def _arguments(self, tool, args):
        from ..assistant_core.tools import validate  # pure; imported late (no cycle)
        return validate(tool.parameters, args)

    def _refuse(self, message, reason):
        if message["t"] == "check":
            self._send({"t": "check_result", "id": message["id"], "call": message["call"],
                        "verdict": "refuse", "reason": reason, "summary": "", "touch": False})
        else:
            self._send({"t": "tool_result", "id": message["id"], "call": message["call"],
                        "ok": False, "output": "not allowed: " + reason})

    def _check(self, task, message):
        name, call = clean(message["name"], 80), message["call"]
        tool, executor = self._tool(task, name)
        if tool is None:
            self.store.add_step(task.id, "check", name, "refused: unknown tool")
            return self._refuse(message, "this helper cannot use that tool")
        try:
            arguments = self._arguments(tool, message["args"])
        except ValueError as error:
            self.store.add_step(task.id, "check", name, "refused: invalid arguments")
            return self._refuse(message, "invalid arguments: %s" % error)
        key = call_key(tool.name, arguments)
        if self.store.use_approval(task.id, key):
            decision_text, verdict = "allowed (the user said yes)", "allow"
        else:
            decision = executor.policy.decide(tool, arguments)
            if decision.allowed and task.tainted and tool.risk != "local":
                # Taint rule: after untrusted text (web pages...) only the user may say yes.
                from ..assistant_core.tools import Decision  # pure; imported late (no cycle)
                decision = Decision(False, "", "touch" if tool.risk == "sensitive" else "voice")
                decision_text, verdict = "asks the user (untrusted content read)", "confirm"
            elif decision.allowed:
                decision_text, verdict = "allowed", "allow"
            elif decision.confirm:
                decision_text, verdict = "asks the user", "confirm"
            else:
                self.store.add_step(task.id, "check", name, "refused")
                return self._refuse(message, decision.reason)
        summary, touch = "", False
        if verdict == "allow":
            self.permitted[(task.id, call)] = key
        else:
            label, detail = tool.confirm(arguments) if tool.confirm else (tool.name, "")
            touch = decision.confirm == "touch"
            self.store.ask_approval(task.id, call, tool.name, key, tool.risk, touch, label, detail)
            summary = clean(detail or label, 200)
        self.store.add_step(task.id, "check", name, decision_text)
        self._send({"t": "check_result", "id": task.id, "call": call, "verdict": verdict,
                    "reason": "", "summary": summary, "touch": touch})

    def _tool_call(self, task, message):
        name, call = clean(message["name"], 80), message["call"]
        tool, _ = self._tool(task, name)
        try:
            arguments = self._arguments(tool, message["args"]) if tool is not None else None
        except ValueError:
            arguments = None
        key = call_key(tool.name, arguments) if arguments is not None else None
        allowed = key is not None and (self.permitted.pop((task.id, call), None) == key
                                       or self.store.use_approval(task.id, key))
        if not allowed:
            self.store.add_step(task.id, "tool", name, "refused: not checked or approved")
            return self._refuse(message, "this call was not allowed")
        try:
            effects, result = tool.run(arguments)
        except Exception as error:  # a plugin's tool must not stop Luna
            log.warning("Luna tasks: tool %s failed (%s)", tool.name, type(error).__name__)
            effects, ok, output = [], False, "the tool failed"
        else:
            ok, output = result.ok, result.output()
        self.store.add_step(task.id, "tool", tool.name, "ok" if ok else "failed")
        if effects:
            self.events.append(TaskEvent("effects", task, effects=tuple(effects)))
        self._send({"t": "tool_result", "id": task.id, "call": call, "ok": ok,
                    "output": output[:OUTPUT_CHARS]})

    def _paused(self, task, message):
        row = self.store.pending_approval(task.id, message["item"])
        if row is None or task.state != RUNNING:
            self._send({"t": "cancel", "id": task.id, "reason": "nothing to approve"})
            return self._stop(task, FAILED, "paused", event="failed")
        moved = self.store.move(task.id, WAITING, run_state=message["state"])
        self._forget(task.id)
        log.info("Luna tasks: %s waits for the user's answer", task.id)
        self.events.append(TaskEvent("approval", moved, approval=_approval(row)))

    def _usage(self, task, message):
        usd = 0.0
        if self.meter is not None:
            usd = self.meter.add_tokens("task", clean(message["model"], 80), message["input"],
                                        message["cached"], message["output"])
        self._charge(task, usd)

    def _charge(self, task, usd):
        """Add `usd` to the task; stop it when the day's budget or its own limit is used up."""
        task = self.store.update(task.id, cost_usd=task.cost_usd + usd)
        limits = self.limits.get(task.id, LIMIT_CAP)
        if self.guard is not None and not self.guard.allows():
            self._send({"t": "cancel", "id": task.id, "reason": "budget"})
            self._stop(task, CANCELLED, "budget", event="cancelled")
        elif task.cost_usd > limits.max_usd:
            self._send({"t": "cancel", "id": task.id, "reason": "cost"})
            self._stop(task, FAILED, "cost", event="failed")

    def _expire_questions(self):
        """A task that waited ANSWER_HOURS for the user's OK is given up (nobody said yes)."""
        old = self.store.clock().timestamp() - ANSWER_HOURS * 3600
        for task in self.store.active():
            if task.state == WAITING and _stamp(task.updated) < old:
                row = self.store.pending_approval(task.id)
                if row is not None:
                    self.store.answer_approval(task.id, row["call_id"], "no", "expired")
                self._stop(task, FAILED, "no answer", event="failed")

    def _timeouts(self, t):
        for task_id, started in list(self.started.items()):
            limits = self.limits.get(task_id, LIMIT_CAP)
            if t - started > limits.max_seconds + GRACE:
                task = self.store.get(task_id)
                if task is not None and task.state in (QUEUED, RUNNING):
                    self.outbox = [m for m in self.outbox if m["id"] != task_id]
                    self._send({"t": "cancel", "id": task_id, "reason": "time"})
                    self._stop(task, FAILED, "time", event="failed")
                else:
                    self._forget(task_id)

    def _stop(self, task, state, error, event=None, announced=False):
        moved = self.store.move(task.id, state, error=error, announced=announced)
        if event:
            self._ended(moved, event)
        else:
            self._forget(task.id)
        return moved

    def _ended(self, task, kind):
        if task is None:
            return
        self._forget(task.id)
        self.events.append(TaskEvent(kind, task))
        log.info("Luna tasks: %s %s (%s)", task.id, task.state, task.error or "ok")

    def _forget(self, task_id):
        self.started.pop(task_id, None)
        self.limits.pop(task_id, None)
        for key in [k for k in self.permitted if k[0] == task_id]:
            del self.permitted[key]


def _approval(row):
    return Approval(row["task_id"], row["call_id"], row["item"], row["label"] or row["tool"],
                    row["detail"], row["touch"])


def _stamp(text):
    try:
        return datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError):
        return 0.0


# --- a service without a process (tests, and the demo mode LUNA_TASKS=demo) ------------------
DEMO_SPECIALISTS = [{"name": "research", "title": "Research",
                     "description": "Looks things up and compares several sources; for questions "
                                    "that need more than one quick search."}]
DEMO_SUMMARY = "This was only a demo: real helpers come in a later update."


class FakeTaskService:
    """An in-process task service. `connect` is the TaskClient's connect function; tests drive
    it with finish / fail / progress / usage / crash / garbage. With `auto` (seconds), every
    task finishes by itself with DEMO_SUMMARY (demo mode)."""

    def __init__(self, specialists=None, version=PROTOCOL_VERSION, auto=None,
                 clock=time.monotonic):
        self.specialists = DEMO_SPECIALISTS if specialists is None else specialists
        self.version = version
        self.auto = auto
        self.clock = clock
        self.alive = False
        self.say_ready = True
        self.fail_connect = False
        self.connects = 0
        self.sent = []
        self.outbox = []
        self.due = {}

    def connect(self):
        if self.fail_connect:
            raise OSError("cannot start")
        self.connects += 1
        self.alive = True
        self.outbox = []
        self.due = {}
        if self.say_ready:
            self.emit(t="ready", version=self.version, specialists=self.specialists)
        return self

    # transport
    def send(self, line):
        if not self.alive:
            raise OSError("closed")
        message = decode(line, TO_SERVICE)
        self.sent.append(message)
        if message["t"] == "start":
            self.emit(t="accepted", id=message["id"])
            if self.auto is not None:
                self.due[message["id"]] = self.clock() + self.auto
        elif message["t"] == "cancel":
            self.due.pop(message["id"], None)

    def receive(self):
        now = self.clock()
        for task_id, at in list(self.due.items()):
            if now >= at:
                del self.due[task_id]
                self.finish(task_id, DEMO_SUMMARY)
        lines, self.outbox = self.outbox, []
        return lines

    def close(self):
        self.alive = False

    # test controls
    def emit(self, **message):
        self.outbox.append(encode(message, FROM_SERVICE))

    def started(self):
        return [m["id"] for m in self.sent if m["t"] == "start"]

    def finish(self, task_id, summary, details=""):
        self.emit(t="done", id=task_id, summary=summary, details=details)

    def fail(self, task_id, error="RuntimeError"):
        self.emit(t="failed", id=task_id, error=error)

    def progress(self, task_id, text):
        self.emit(t="progress", id=task_id, text=text)

    def usage(self, task_id, model="gpt-4o-mini", input_tokens=1000, output=100, cached=0):
        self.emit(t="usage", id=task_id, model=model, input=input_tokens, cached=cached,
                  output=output)

    def check(self, task_id, call, name, args):
        self.emit(t="check", id=task_id, call=call, name=name, args=args)

    def tool_call(self, task_id, call, name, args):
        self.emit(t="tool_call", id=task_id, call=call, name=name, args=args)

    def pause(self, task_id, item, state="paused-run"):
        self.emit(t="paused", id=task_id, item=item, summary="", touch=False, state=state)

    def replies(self, kind):
        """What Luna sent of one kind (check_result, tool_result, resume, cancel...)."""
        return [m for m in self.sent if m["t"] == kind]

    def crash(self):
        self.alive = False

    def garbage(self, line="not json"):
        self.outbox.append(line)


def demo_client(store, meter=None, guard=None, **options):
    """A TaskClient with the fake service finishing every task after 8 s (LUNA_TASKS=demo)."""
    service = FakeTaskService(auto=8.0)
    return TaskClient(service.connect, store, meter, guard, **options)

