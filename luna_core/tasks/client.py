"""TaskClient: Luna's side of the task service (pure: no pygame, no SDK; runs on the UI thread).

The client owns the tasks (TaskStore) and talks to the service through a transport: an object
with `send(line)`, `receive()` (the complete lines that arrived, never blocking), `alive` and
`close()`. `connect()` makes one (a child process from step 4; FakeTaskService here). Every
check is code: tasks on, budget, goal length, known specialist, at most MAX_ACTIVE tasks; the
money is counted here (usage messages), over a task's limit or the daily budget it is stopped.

A lost service interrupts its running tasks and is restarted after 2, 10 and 60 s; after 3
failures in 10 minutes (or a service with another protocol version) tasks stay off until Luna
restarts. Approval questions and action tools come in step 4: for now `check` is refused,
`tool_call` fails and a `paused` task fails.
"""
import logging
import time
from dataclasses import dataclass
from datetime import datetime

from .model import (CANCELLED, DETAILS_CHARS, DONE, FAILED, GOAL_CHARS, INTERRUPTED, RUNNING,
                    SUMMARY_CHARS, WAITING, Limits, Specialist, Task, clean, new_id)
from .protocol import (FROM_SERVICE, PROTOCOL_VERSION, TO_SERVICE, ProtocolError, compatible,
                       decode, encode)
from .store import HISTORY_DAYS

log = logging.getLogger("luna.tasks")

MAX_ACTIVE = 3
READY_WAIT = 20.0
BACKOFF = (2.0, 10.0, 60.0)
CRASH_WINDOW = 600.0
CRASHES = 3
GARBAGE = 5
GRACE = 30.0          # after a task's max_seconds Luna stops it herself
REPORT_HOURS = 12     # older unreported results are not announced after a restart
LIMIT_CAP = Limits()  # the settings' cap on every specialist's limits


@dataclass(frozen=True)
class TaskEvent:
    """Something the conversation should know: started, done, failed, cancelled,
    interrupted or progress (`text`)."""
    kind: str
    task: Task
    text: str = ""


def _specialists(items):
    found = {}
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        name = clean(item["name"], 40)
        limits = item.get("limits") if isinstance(item.get("limits"), dict) else {}
        try:
            spec_limits = Limits(int(limits.get("max_turns", 8)),
                                 float(limits.get("max_seconds", 120.0)),
                                 float(limits.get("max_usd", 0.05))).within(LIMIT_CAP)
        except (TypeError, ValueError):
            spec_limits = Limits()
        if name:
            found[name] = Specialist(name, clean(item.get("description"), 300),
                                     clean(item.get("title") or name, 40), spec_limits)
    return found


class TaskClient:
    def __init__(self, connect, store, meter=None, guard=None, enabled=True,
                 history_days=HISTORY_DAYS, max_active=MAX_ACTIVE):
        self.connect = connect
        self.store = store
        self.meter = meter
        self.guard = guard
        self.enabled = enabled
        self.history_days = history_days
        self.max_active = max_active
        self.transport = None
        self.ready = False
        self.disabled = ""       # why tasks are off until restart ("crashes", "version")
        self.specialists = {}
        self.crashes = []
        self.retry_at = 0.0
        self.connected_at = None
        self.garbage = 0
        self.started = {}        # task id -> t it was started (this run)
        self.limits = {}         # task id -> Limits
        self.events = []
        self.t = 0.0
        store.recover()
        store.purge(history_days)
        old = (store.clock().timestamp() - REPORT_HOURS * 3600)
        for task in store.unannounced():
            if _stamp(task.updated) < old:
                store.update(task.id, announced=True)

    # --- state ---------------------------------------------------------------------------
    @property
    def available(self):
        return bool(self.enabled and not self.disabled and self.ready and self.transport
                    and self.transport.alive and self.specialists)

    def active(self):
        return self.store.active()

    def recent(self, limit=20):
        return self.store.recent(limit)

    def unannounced(self):
        return self.store.unannounced()

    def mark_announced(self, task_id):
        self.store.update(task_id, announced=True)

    def set_enabled(self, on):
        """Settings: tasks on or off (off stops the running ones and the service)."""
        if on == self.enabled:
            return
        self.enabled = on
        if not on:
            for task in self.store.active():
                self._stop(task, CANCELLED, "off", announced=True)
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
        task = self.store.add(Task(new_id(), text, spec.name, clean(language, 8) or "en"))
        limits = spec.limits.within(LIMIT_CAP)
        try:
            self.transport.send(encode({"t": "start", "id": task.id, "goal": task.goal,
                                        "specialist": spec.name, "language": task.language,
                                        "context": {}, "limits": limits.as_dict()}))
        except (OSError, ProtocolError) as error:
            log.warning("Luna tasks: could not start a task (%s)", type(error).__name__)
            self.store.move(task.id, FAILED, error="unavailable", announced=True)
            self._lost(t)
            return None, "unavailable"
        self.started[task.id] = t
        self.limits[task.id] = limits
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
        self._send({"t": "cancel", "id": task.id, "reason": "user"})
        return self._stop(task, CANCELLED, "user", announced=True)

    # --- the service ---------------------------------------------------------------------
    def poll(self, t):
        """Run the connection and handle what the service sent; returns TaskEvents."""
        self.t = t
        if self.enabled and not self.disabled:
            if self.transport is None and t >= self.retry_at:
                self._connect(t)
            elif self.transport is not None and not self.transport.alive:
                self._lost(t)
            if self.transport is not None:
                self._read(t)
            if (self.transport is not None and not self.ready
                    and t - self.connected_at > READY_WAIT):
                log.warning("Luna tasks: the service did not start")
                self._lost(t)
            self._timeouts(t)
        events, self.events = self.events, []
        return events

    def close(self):
        self._send({"t": "shutdown"})
        self._close()
        self.store.close()

    def _connect(self, t):
        try:
            self.transport = self.connect()
        except (OSError, ValueError) as error:
            log.warning("Luna tasks: could not start the service (%s)", type(error).__name__)
            self.transport = None
            self._crashed(t)
            return
        self.connected_at = t
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

    def _lost(self, t):
        """The service stopped: interrupt what it was doing and plan a restart."""
        self._close()
        for task in self.store.active():
            if task.state != WAITING:
                self._stop(task, INTERRUPTED, "interrupted", event="interrupted")
        self._crashed(t)

    def _crashed(self, t):
        self.crashes = [c for c in self.crashes if t - c < CRASH_WINDOW] + [t]
        if len(self.crashes) >= CRASHES:
            self.disabled = "crashes"
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
                return
            self.specialists = _specialists(message["specialists"])
            self.ready = True
            return
        task = self.store.get(message["id"])
        if task is None or not task.active:
            return  # a task that was stopped meanwhile
        if kind == "accepted":
            self.store.move(task.id, RUNNING)
        elif kind == "step":
            self.store.add_step(task.id, message["kind"], message["name"], message["summary"])
        elif kind == "usage":
            self._usage(task, message)
        elif kind == "check":
            self.store.add_step(task.id, "check", message["name"], "refused")
            self._send({"t": "check_result", "id": task.id, "call": message["call"],
                        "verdict": "refuse", "reason": "not available yet", "summary": "",
                        "touch": False})
        elif kind == "tool_call":
            self._send({"t": "tool_result", "id": task.id, "call": message["call"],
                        "ok": False, "output": "not available yet"})
        elif kind == "paused":
            self._send({"t": "cancel", "id": task.id, "reason": "approvals not available"})
            self._stop(task, FAILED, "paused", event="failed")
        elif kind == "progress":
            text = clean(message["text"], SUMMARY_CHARS)
            if text:
                self.events.append(TaskEvent("progress", task, text))
        elif kind == "done":
            moved = self.store.move(task.id, DONE, summary=clean(message["summary"], SUMMARY_CHARS),
                                    details=str(message["details"])[:DETAILS_CHARS])
            self._ended(moved, "done")
        elif kind == "failed":
            self._stop(task, FAILED, clean(message["error"], 40) or "error", event="failed")

    def _usage(self, task, message):
        usd = 0.0
        if self.meter is not None:
            usd = self.meter.add_tokens("task", clean(message["model"], 80), message["input"],
                                        message["cached"], message["output"])
        task = self.store.update(task.id, cost_usd=task.cost_usd + usd)
        limits = self.limits.get(task.id, LIMIT_CAP)
        if self.guard is not None and not self.guard.allows():
            self._send({"t": "cancel", "id": task.id, "reason": "budget"})
            self._stop(task, CANCELLED, "budget", event="cancelled")
        elif task.cost_usd > limits.max_usd:
            self._send({"t": "cancel", "id": task.id, "reason": "cost"})
            self._stop(task, FAILED, "cost", event="failed")

    def _timeouts(self, t):
        for task_id, started in list(self.started.items()):
            limits = self.limits.get(task_id, LIMIT_CAP)
            if t - started > limits.max_seconds + GRACE:
                task = self.store.get(task_id)
                if task is not None and task.active and task.state != WAITING:
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

    def crash(self):
        self.alive = False

    def garbage(self, line="not json"):
        self.outbox.append(line)


def demo_client(store, meter=None, guard=None, **options):
    """A TaskClient with the fake service finishing every task after 8 s (LUNA_TASKS=demo)."""
    service = FakeTaskService(auto=8.0)
    return TaskClient(service.connect, store, meter, guard, **options)

