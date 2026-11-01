"""Background tasks, step 3: model, store, protocol, Announcer, TaskClient + FakeTaskService,
talk tools, voice controls and the conversation end to end (no SDK, no real service)."""
import dataclasses
import json
import os
import queue
import sys
import tempfile
import time
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from luna_core.assistant_core.context import TASK_RULES
from luna_core.assistant_core.conversation import Conversation
from luna_core.assistant_core.events import Announce, TaskStarted
from luna_core.assistant_core.events import Act
from luna_core.assistant_core.tools import (Tool, ToolCall, ToolExecutor, ToolRegistry, ToolResult,
                                            _object)
from luna_core.tasks import announce, protocol
from luna_core.tasks.announce import Announcement, Announcer
from luna_core.tasks.catalog import RESEARCH
from luna_core.tasks.client import FakeTaskService, TaskClient
from luna_core.tasks.process import ProcessTransport
from luna_core.tasks.model import (CANCELLED, DONE, FAILED, INTERRUPTED, QUEUED, RUNNING, WAITING,
                                   Task, new_id)
from luna_core.tasks.store import TaskStore
from test_assistant_core import FakeAI, spoken

NOON = datetime(2026, 3, 2, 12, 0)


class Clock:
    def __init__(self, now=NOON):
        self.now = now

    def __call__(self):
        return self.now


class FakeMeter:
    def __init__(self, usd=0.01):
        self.usd, self.calls = usd, []

    def add_tokens(self, path, model, input_tokens, cached=0, output=0):
        self.calls.append((path, model, input_tokens, cached, output))
        return self.usd


class FakeGuard:
    def __init__(self):
        self.ok = True

    def allows(self):
        return self.ok


def temp_store(test, clock=None):
    folder = tempfile.mkdtemp()
    store = TaskStore(Path(folder) / "tasks.db", clock=clock or Clock())
    test.addCleanup(store.close)
    return store


class StoreTests(unittest.TestCase):
    def test_lifecycle_and_illegal_moves(self):
        store = temp_store(self)
        task = store.add(Task(new_id(), "compare vacuums", "research"))
        self.assertEqual(store.get(task.id).state, QUEUED)
        self.assertIsNone(store.move(task.id, DONE), "queued cannot jump to done")
        self.assertEqual(store.move(task.id, RUNNING).state, RUNNING)
        done = store.move(task.id, DONE, summary="X is best.")
        self.assertEqual((done.state, done.summary, done.final), (DONE, "X is best.", True))
        self.assertIsNone(store.move(task.id, RUNNING), "final states are final")
        self.assertEqual([t.id for t in store.unannounced()], [task.id])
        store.update(task.id, announced=True)
        self.assertEqual(store.unannounced(), [])
        with self.assertRaises(ValueError):
            store.update(task.id, state=RUNNING)

    def test_recover_keeps_waiting_runs_and_purge_keeps_recent_history(self):
        clock = Clock()
        store = temp_store(self, clock)
        queued = store.add(Task(new_id(), "a", "research"))
        running = store.add(Task(new_id(), "b", "research"))
        store.move(running.id, RUNNING)
        waiting = store.add(Task(new_id(), "c", "research"))
        store.move(waiting.id, RUNNING)
        store.move(waiting.id, WAITING)
        lost = store.recover()
        self.assertEqual({t.id for t in lost}, {queued.id, running.id})
        self.assertEqual(store.get(running.id).error, "interrupted")
        self.assertEqual(store.get(waiting.id).state, WAITING)
        store.add_step(running.id, "tool", "web_search", "3 results")
        clock.now = NOON + timedelta(days=8)
        fresh = store.add(Task(new_id(), "d", "research"))
        store.move(fresh.id, CANCELLED)
        self.assertEqual(store.purge(7), 2, "old finished tasks only")
        self.assertIsNone(store.get(running.id))
        self.assertEqual(store.steps(running.id), [])
        self.assertIsNotNone(store.get(waiting.id), "an active task is never purged")
        self.assertIsNotNone(store.get(fresh.id))

    def test_an_unusable_file_keeps_tasks_in_memory(self):
        folder = tempfile.mkdtemp()
        with self.assertLogs("luna.tasks", "WARNING"):
            store = TaskStore(Path(folder), clock=Clock())  # a folder, not a file
        self.addCleanup(store.close)
        self.assertTrue(store.in_memory)
        task = store.add(Task(new_id(), "a", "research"))
        self.assertEqual(store.get(task.id).goal, "a")


class ProtocolTests(unittest.TestCase):
    def test_round_trip_and_checks(self):
        line = protocol.encode({"t": "cancel", "id": "x", "reason": "user"})
        self.assertTrue(line.endswith("\n"))
        self.assertEqual(protocol.decode(line, protocol.TO_SERVICE)["reason"], "user")
        message = protocol.decode('{"t":"usage","id":"x","model":"m","input":5,"cached":0,'
                                  '"output":2,"extra":1}')
        self.assertEqual(message["input"], 5, "unknown fields are ignored")
        bad = ['{"t":"usage","id":"x","model":"m","input":true,"cached":0,"output":2}',
               '{"t":"usage","id":"x","model":"m","input":-1,"cached":0,"output":2}',
               '{"t":"done","id":"x","summary":"s"}', '{"t":"hack","id":"x"}', '[1]',
               'not json', b'\xff\xfe']
        for text in bad:
            with self.assertRaises(protocol.ProtocolError, msg=text):
                protocol.decode(text)
        with self.assertRaises(protocol.ProtocolError):
            protocol.encode({"t": "check_result", "id": "x", "call": "c", "verdict": "maybe",
                             "reason": "", "summary": "", "touch": False})

    def test_line_limits(self):
        big = "x" * protocol.MAX_LINE
        with self.assertRaises(protocol.ProtocolError):
            protocol.decode(json.dumps({"t": "done", "id": "x", "summary": big, "details": ""}))
        paused = json.dumps({"t": "paused", "id": "x", "item": "i", "summary": "s",
                             "touch": False, "state": big})
        self.assertEqual(protocol.decode(paused)["t"], "paused", "a paused run may be larger")

    def test_versions(self):
        self.assertTrue(protocol.compatible("1.4"))
        self.assertFalse(protocol.compatible("2.0"))
        self.assertFalse(protocol.compatible("one"))


class AnnouncerTests(unittest.TestCase):
    def test_order_gap_and_busy(self):
        a = Announcer()
        a.user_active(0.0)
        a.add(Announcement("progress", "p", "still going"), 0.0, started=-60.0)
        a.add(Announcement("failure", "f", "failed"), 0.0)
        a.add(Announcement("result", "r", "found it"), 0.0)
        self.assertIsNone(a.next(1.0, NOON, busy=True))
        self.assertEqual(a.next(1.0, NOON).task_id, "r")
        self.assertIsNone(a.next(1.0 + announce.GAP - 1, NOON), "a gap between announcements")
        self.assertEqual(a.next(1.0 + announce.GAP, NOON).task_id, "f")
        self.assertEqual(a.next(2.0 + 2 * announce.GAP, NOON).task_id, "p")

    def test_progress_rules(self):
        a = Announcer()
        self.assertFalse(a.add(Announcement("progress", "x", "1"), 10.0, started=0.0), "too early")
        self.assertTrue(a.add(Announcement("progress", "x", "1"), 40.0, started=0.0))
        self.assertFalse(a.add(Announcement("progress", "x", "2"), 50.0, started=0.0), "once")
        a.add(Announcement("result", "x", "done"), 60.0)
        self.assertEqual([i.kind for i in a.pending()], ["result"], "a result drops progress")

    def test_quiet_hours_and_away(self):
        a = Announcer()
        a.add(Announcement("result", "r", "found it"), 0.0)
        self.assertIsNone(a.next(1.0, NOON), "nobody was around yet")
        a.user_active(1.0)
        late = NOON.replace(hour=23)
        self.assertIsNone(a.next(2.0, late), "quiet hours")
        self.assertTrue(announce.quiet_time(NOON.replace(hour=6)))
        self.assertFalse(announce.quiet_time(NOON.replace(hour=7)))
        self.assertIsNone(a.next(1.0 + announce.AWAY, NOON), "the user went away")
        a.user_active(700.0, invited=True)
        self.assertEqual(a.next(701.0, late).task_id, "r", "talking to Luna invites an answer")
        a.quiet = False
        a.add(Announcement("result", "s", "more"), 800.0)
        self.assertEqual(a.next(800.0, late).task_id, "s", "quiet hours switched off")


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = temp_store(self, self.clock)
        self.meter, self.guard = FakeMeter(), FakeGuard()
        self.service = FakeTaskService()
        self.client = TaskClient(self.service.connect, self.store, self.meter, self.guard)

    def start(self, goal="compare three robot vacuums", t=1.0):
        task, why = self.client.begin(goal, "research", "en", t=t)
        self.assertEqual(why, "")
        return task

    def test_start_run_and_finish(self):
        self.assertEqual(self.client.begin("x", "research"), (None, "unavailable"))
        self.client.poll(0.0)
        self.assertTrue(self.client.available)
        self.assertEqual(list(self.client.specialists), ["research"])
        self.assertEqual(self.client.begin("x" * 501, "research")[1], "goal")
        self.assertEqual(self.client.begin("x", "plumber")[1], "specialist")
        self.guard.ok = False
        self.assertEqual(self.client.begin("x", "research")[1], "budget")
        self.guard.ok = True
        task = self.start()
        sent = self.service.sent[-1]
        self.assertEqual((sent["t"], sent["goal"], sent["limits"]["max_usd"]),
                         ("start", task.goal, 0.05))
        self.client.poll(1.5)
        self.assertEqual(self.store.get(task.id).state, RUNNING)
        self.service.emit(t="step", id=task.id, kind="tool", name="web_search", summary="3 hits")
        self.service.finish(task.id, "X is best. " * 50, "long details")
        events = self.client.poll(2.0)
        self.assertEqual([e.kind for e in events], ["done"])
        done = self.store.get(task.id)
        self.assertEqual((done.state, len(done.summary), done.details), (DONE, 300, "long details"))
        self.assertEqual(self.store.steps(task.id)[0][1:], ("tool", "web_search", "3 hits"))

    def test_at_most_three_tasks(self):
        self.client.poll(0.0)
        for _ in range(3):
            self.start()
        self.assertEqual(self.client.begin("x", "research")[1], "busy")

    def test_money_is_counted_and_limited(self):
        self.client.poll(0.0)
        task = self.start()
        self.service.usage(task.id)
        self.client.poll(1.0)
        self.assertEqual(self.meter.calls, [("task", "gpt-4o-mini", 1000, 0, 100)])
        self.meter.usd = 0.05
        self.service.usage(task.id)
        events = self.client.poll(1.1)
        self.assertEqual([(e.kind, e.task.error) for e in events], [("failed", "cost")])
        self.assertEqual(self.service.sent[-1], {"t": "cancel", "id": task.id, "reason": "cost"})
        other = self.start(t=2.0)
        self.guard.ok = False
        self.service.usage(other.id)
        events = self.client.poll(2.1)
        self.assertEqual([(e.kind, e.task.state, e.task.error) for e in events],
                         [("cancelled", CANCELLED, "budget")])

    def test_crash_restart_and_giving_up(self):
        self.client.poll(0.0)
        self.start()
        self.service.crash()
        with self.assertLogs("luna.tasks", "INFO"):
            events = self.client.poll(1.0)
        self.assertEqual([(e.kind, e.task.state) for e in events], [("interrupted", INTERRUPTED)])
        self.assertFalse(self.client.available)
        self.client.poll(2.9)
        self.assertEqual(self.service.connects, 1, "waits 2 s before restarting")
        self.client.poll(3.0)
        self.assertEqual(self.service.connects, 2)
        self.assertTrue(self.client.available)
        self.service.crash()
        self.client.poll(4.0)
        self.client.poll(14.0)
        self.assertEqual(self.service.connects, 3, "then 10 s")
        self.service.crash()
        with self.assertLogs("luna.tasks", "WARNING"):
            self.client.poll(15.0)
        self.assertEqual(self.client.disabled, "crashes")
        self.client.poll(500.0)
        self.assertEqual(self.service.connects, 3, "off until Luna restarts")

    def test_no_ready_garbage_and_wrong_version(self):
        self.service.say_ready = False
        self.client.poll(0.0)
        with self.assertLogs("luna.tasks", "WARNING"):
            self.client.poll(21.0)
        self.assertIsNone(self.client.transport, "no ready in 20 s: restarted")
        self.service.say_ready = True
        self.client.poll(23.0)
        self.assertTrue(self.client.available)
        with self.assertLogs("luna.tasks", "WARNING"):
            self.service.garbage()
            self.client.poll(24.0)
        self.assertTrue(self.client.available, "one bad line is dropped")
        with self.assertLogs("luna.tasks", "WARNING"):
            for _ in range(5):
                self.service.garbage()
            self.client.poll(25.0)
        self.assertFalse(self.client.available, "repeated garbage restarts the service")
        old = FakeTaskService(version="2.0")
        client = TaskClient(old.connect, self.store)
        with self.assertLogs("luna.tasks", "WARNING"):
            client.poll(0.0)
        self.assertEqual((client.disabled, client.available), ("version", False))

    def test_a_helper_without_tools_cannot_use_any(self):
        self.client.poll(0.0)
        task = self.start()
        self.service.emit(t="check", id=task.id, call="c1", name="turn_on", args={})
        self.service.emit(t="tool_call", id=task.id, call="c2", name="turn_on", args={})
        self.client.poll(1.0)
        self.assertEqual(self.service.sent[-2]["verdict"], "refuse")
        self.assertFalse(self.service.sent[-1]["ok"])
        self.service.emit(t="paused", id=task.id, item="i", summary="s", touch=False, state="{}")
        events = self.client.poll(1.1)
        self.assertEqual([(e.kind, e.task.error) for e in events], [("failed", "paused")])

    def test_time_limit_cancel_and_switching_off(self):
        self.client.poll(0.0)
        slow = self.start(t=0.0)
        self.client.poll(0.5)  # accepted: its time starts
        events = self.client.poll(151.0)
        self.assertEqual([(e.kind, e.task.error) for e in events], [("failed", "time")])
        task = self.start(t=152.0)
        self.assertEqual(self.client.cancel().id, task.id)
        self.assertTrue(self.store.get(task.id).announced, "a cancelled task is not announced")
        self.assertIsNone(self.client.cancel())
        self.service.finish(task.id, "too late")
        self.assertEqual(self.client.poll(153.0), [], "results of stopped tasks are ignored")
        running = self.start(t=154.0)
        self.client.set_enabled(False)
        self.assertEqual((self.store.get(running.id).state, self.client.available),
                         (CANCELLED, False))
        self.assertEqual(self.store.get(slow.id).error, "time")

    def test_old_unreported_results_are_not_announced_after_a_restart(self):
        old = self.store.add(Task(new_id(), "old", "research"))
        self.store.move(old.id, FAILED)
        self.clock.now = NOON + timedelta(hours=13)
        recent = self.store.add(Task(new_id(), "recent", "research"))
        self.store.move(recent.id, RUNNING)
        client = TaskClient(FakeTaskService().connect, self.store)
        self.assertEqual([(t.id, t.state) for t in client.unannounced()],
                         [(recent.id, INTERRUPTED)])


TEXT = {"type": "string", "description": "text"}


def bridge_tools(ran):
    def run(name):
        return lambda args: ([Act((name,))], ran.append((name, args)) or ToolResult(True, "ok"))
    return [Tool("face", "Change the face", _object({"mood": TEXT}), "local", run("face")),
            Tool("note", "Save a note", _object({"text": TEXT}), "memory", run("note"),
                 asked_with="remember", confirm=lambda a: ("Save a note", a["text"])),
            Tool("unlock", "Unlock the door", _object({}), "sensitive", run("unlock"),
                 confirm=lambda a: ("Unlock the door", "")),
            Tool("drive", "Drive", _object({"to": TEXT}), "motion", run("drive"))]


def bridge_client(test, store, service=None, lazy=False, **options):
    """A client whose research helper may ask for face, note, unlock and drive."""
    test.ran = []
    test.executor = ToolExecutor(ToolRegistry(bridge_tools(test.ran)))
    spec = dataclasses.replace(RESEARCH, tools=("face", "note", "unlock", "drive"))
    service = service or FakeTaskService(specialists=[{"name": "research", "tools": ["face"]}])
    client = TaskClient(service.connect, store, FakeMeter(), FakeGuard(),
                        catalog={"research": spec}, lazy=lazy, **options)
    client.executor = lambda: test.executor
    return client, service


class BridgeTests(unittest.TestCase):
    """A task asks to use Luna's tools: Luna's code decides every call (step 4)."""

    def setUp(self):
        self.clock = Clock()
        self.store = temp_store(self, self.clock)
        self.client, self.service = bridge_client(self, self.store)
        self.client.poll(0.0)
        self.task, _ = self.client.begin("note that we need milk", "research", "en", t=1.0)
        self.client.poll(1.1)

    def check(self, call, name, args, t=2.0):
        self.service.check(self.task.id, call, name, args)
        events = self.client.poll(t)
        return self.service.replies("check_result")[-1], events

    def call(self, call, name, args, t=2.5):
        self.service.tool_call(self.task.id, call, name, args)
        events = self.client.poll(t)
        return self.service.replies("tool_result")[-1], events

    def test_luna_offers_only_her_catalogue_tools_never_motion(self):
        start = self.service.replies("start")[0]
        self.assertEqual([tool["name"] for tool in start["context"]["tools"]],
                         ["face", "note", "unlock"])
        self.assertEqual(start["limits"]["max_turns"], RESEARCH.limits.max_turns)
        verdict, _ = self.check("c1", "drive", {"to": "kitchen"})
        self.assertEqual(verdict["verdict"], "refuse")
        result, _ = self.call("c1", "drive", {"to": "kitchen"})
        self.assertFalse(result["ok"])
        self.assertEqual(self.ran, [], "motion never runs through a task")
        verdict, _ = self.check("c2", "shell", {})
        self.assertEqual(verdict["verdict"], "refuse", "a tool Luna does not have")

    def test_an_allowed_call_runs_once_with_the_checked_arguments(self):
        verdict, _ = self.check("c1", "face", {"mood": "happy"})
        self.assertEqual(verdict["verdict"], "allow")
        result, _ = self.call("c1", "face", {"mood": "sad"})
        self.assertFalse(result["ok"], "other arguments than the checked ones")
        self.check("c2", "face", {"mood": "happy"})
        result, events = self.call("c2", "face", {"mood": "happy"})
        self.assertTrue(result["ok"])
        self.assertEqual(self.ran, [("face", {"mood": "happy"})])
        self.assertEqual([(e.kind, e.effects) for e in events], [("effects", (Act(("face",)),))])
        result, _ = self.call("c2", "face", {"mood": "happy"}, t=3.0)
        self.assertFalse(result["ok"], "a check allows one call")
        result, _ = self.call("c3", "face", {"mood": "happy"}, t=3.5)
        self.assertFalse(result["ok"], "never checked")
        verdict, _ = self.check("c4", "face", {"mood": 3, "extra": 1}, t=4.0)
        self.assertEqual(verdict["verdict"], "refuse")
        self.assertEqual(len(self.ran), 1)

    def test_a_failing_tool_does_not_stop_luna(self):
        broken = Tool("face", "x", _object({"mood": TEXT}), "local", lambda a: 1 / 0)
        self.executor = ToolExecutor(ToolRegistry([broken]))
        self.check("c1", "face", {"mood": "happy"})
        with self.assertLogs("luna.tasks", "WARNING"):
            result, _ = self.call("c1", "face", {"mood": "happy"})
        self.assertEqual((result["ok"], result["output"]), (False, "the tool failed"))

    def test_asking_the_user_pauses_the_task_until_they_answer(self):
        verdict, _ = self.check("c1", "note", {"text": "milk"})
        self.assertEqual((verdict["verdict"], verdict["summary"], verdict["touch"]),
                         ("confirm", "milk", False))
        self.service.pause(self.task.id, "c1", state="RUN-STATE")
        events = self.client.poll(3.0)
        self.assertEqual([e.kind for e in events], ["approval"])
        approval = events[0].approval
        self.assertEqual((approval.label, approval.detail, approval.touch),
                         ("Save a note", "milk", False))
        self.assertEqual(self.store.get(self.task.id).state, WAITING)
        self.assertEqual(self.client.waiting(), [approval])
        self.assertTrue(self.client.answer(self.task.id, "c1", True, "voice"))
        resume = self.service.replies("resume")[-1]
        self.assertEqual((resume["state"], resume["item"], resume["approved"],
                          resume["specialist"]), ("RUN-STATE", "c1", True, "research"))
        self.assertEqual(self.store.get(self.task.id).state, RUNNING)
        self.assertFalse(self.client.answer(self.task.id, "c1", True, "voice"), "answered once")
        result, _ = self.call("c1", "note", {"text": "milk"}, t=4.0)
        self.assertTrue(result["ok"])
        result, _ = self.call("c1", "note", {"text": "milk"}, t=4.5)
        self.assertFalse(result["ok"], "a yes covers one call")
        self.assertEqual(self.ran, [("note", {"text": "milk"})])
        steps = [step[1:] for step in self.store.steps(self.task.id)]
        self.assertIn(("approval", "note", "yes (voice)"), steps)

    def test_no_means_no(self):
        self.check("c1", "note", {"text": "milk"})
        self.service.pause(self.task.id, "c1")
        self.client.poll(3.0)
        self.client.answer(self.task.id, "c1", False, "voice")
        self.assertFalse(self.service.replies("resume")[-1]["approved"])
        result, _ = self.call("c1", "note", {"text": "milk"}, t=4.0)
        self.assertFalse(result["ok"])
        self.assertEqual(self.ran, [])

    def test_sensitive_tools_need_a_tap(self):
        verdict, _ = self.check("c1", "unlock", {})
        self.assertEqual((verdict["verdict"], verdict["touch"]), ("confirm", True))

    def test_a_question_survives_a_restart_and_expires(self):
        self.check("c1", "note", {"text": "milk"})
        self.service.pause(self.task.id, "c1")
        self.client.poll(3.0)
        client, _ = bridge_client(self, self.store)
        self.assertEqual(self.store.get(self.task.id).state, WAITING, "kept by recover()")
        self.assertEqual([a.call for a in client.waiting()], ["c1"])
        client.poll(10.0)
        self.assertEqual(self.store.get(self.task.id).state, WAITING)
        self.clock.now += timedelta(hours=13)
        events = client.poll(100.0)
        self.assertEqual([(e.kind, e.task.error) for e in events], [("failed", "no answer")])
        self.assertEqual(client.waiting(), [])


class LazyServiceTests(unittest.TestCase):
    """LUNA_TASKS=service: the helper process runs only while there is work."""

    def test_started_when_needed_and_stopped_when_idle(self):
        store = temp_store(self, Clock())
        client, service = bridge_client(self, store, lazy=True, idle_stop=300.0)
        client.poll(0.0)
        self.assertEqual((service.connects, client.available, client.running), (0, True, False))
        self.assertEqual(sorted(client.specialists), ["research"], "from Luna's catalogue")
        task, _ = client.begin("compare vacuums", "research", "en", t=1.0)
        self.assertEqual(service.sent, [], "queued until the service is ready")
        client.poll(1.0)
        self.assertEqual((service.connects, [m["t"] for m in service.sent]), (1, ["start"]))
        client.poll(100.0)
        self.assertEqual(store.get(task.id).state, RUNNING)
        self.assertEqual(client.started[task.id], 100.0, "its time starts when it runs")
        service.finish(task.id, "Done.")
        client.poll(101.0)
        client.poll(400.0)
        self.assertTrue(client.running)
        client.poll(402.0)
        self.assertEqual((client.running, service.sent[-1]["t"]), (False, "shutdown"))
        self.assertEqual(client.crashes, [], "stopping on purpose is not a crash")
        client.begin("again", "research", "en", t=500.0)
        client.poll(500.0)
        self.assertEqual(service.connects, 2)

    def test_a_cancelled_queued_task_is_never_sent(self):
        store = temp_store(self, Clock())
        client, service = bridge_client(self, store, lazy=True)
        client.begin("compare vacuums", "research", "en", t=1.0)
        client.cancel()
        client.poll(1.0)
        self.assertEqual(service.connects, 0)


CHILD = r"""
import sys
out = sys.stdout.buffer
out.write(b'{"t":"ready"}\n'); out.flush()
for line in sys.stdin.buffer:
    if line.strip() == b"crash":
        sys.exit(3)
    out.write(b"echo " + line); out.flush()
"""


class ProcessTransportTests(unittest.TestCase):
    def wait_for(self, transport, count, seconds=10.0):
        lines, end = [], time.monotonic() + seconds
        while len(lines) < count and time.monotonic() < end:
            lines += transport.receive()
            time.sleep(0.01)
        return lines

    def test_lines_both_ways_and_a_clean_stop(self):
        transport = ProcessTransport((sys.executable, "-c", CHILD))
        self.assertEqual(self.wait_for(transport, 1), [b'{"t":"ready"}\n'])
        transport.send("hello\n")
        transport.send("big " + "x" * 300000 + "\n")
        lines = self.wait_for(transport, 2)
        self.assertEqual((lines[0], len(lines[1])), (b"echo hello\n", 300010))
        self.assertTrue(transport.alive)
        transport.close()
        transport.process.wait(10)
        self.assertEqual(transport.process.returncode, 0, "closing stdin stops it")
        self.wait_for(transport, 0, 0.2)
        self.assertFalse(transport.alive)

    def test_a_crash_is_noticed(self):
        transport = ProcessTransport((sys.executable, "-c", CHILD))
        self.wait_for(transport, 1)
        transport.send("crash\n")
        transport.process.wait(10)
        end = time.monotonic() + 5
        while transport.alive and time.monotonic() < end:
            transport.receive()
            time.sleep(0.01)
        self.assertFalse(transport.alive)
        with self.assertRaises(OSError):
            transport.send("hello\n")
        transport.close()

    def test_a_missing_program_cannot_connect(self):
        with self.assertRaises(OSError):
            ProcessTransport(("no-such-program-for-luna",))


class ConversationTaskTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = temp_store(self, self.clock)
        self.ai = FakeAI()
        self.chat = Conversation(self.ai, clock=self.clock)
        self.service = FakeTaskService()
        self.client = TaskClient(self.service.connect, self.store, FakeMeter(), FakeGuard())
        self.announcer = Announcer()
        self.chat.set_tasks(self.client, self.announcer)

    def poll(self, t, speaking=False):
        return self.chat.poll(t, speaking)

    def delegate(self, t=1.0):
        self.poll(t - 0.5)
        self.chat.hear("luna compare three robot vacuums for me", t, "en", "en")
        call = ToolCall("c1", "start_task", json.dumps({"goal": "Compare three robot vacuums",
                                                        "specialist": "research"}))
        self.ai.send("tools", [call])
        effects = self.poll(t + 0.1)
        self.poll(t + 0.2)
        self.ai.send("sentence", "I'll look into it and tell you.")
        self.ai.send("done")
        self.poll(t + 0.3)
        self.poll(t + 0.4)
        return effects

    def test_delegate_announce_and_follow_up(self):
        self.poll(0.0)
        self.chat.hear("luna what is two plus two", 0.1, "en", "en")
        names = [tool.get("name") for tool in self.ai.tools]
        self.assertIn("start_task", names)
        self.assertIn(TASK_RULES, str(self.ai.instructions))
        self.chat.interrupt()
        effects = self.delegate(t=1.0)
        started = [e for e in effects if isinstance(e, TaskStarted)]
        self.assertEqual(len(started), 1)
        output = json.loads(self.ai.results[-1][1][0][1])
        self.assertTrue(output["ok"])
        task_id = started[0].task_id
        self.service.finish(task_id, "The Roborock S8 is the best value.")
        self.assertEqual([e for e in self.poll(2.0) if isinstance(e, Announce)], [],
                         "not during the follow-up window")
        announced = [e for e in self.poll(60.0) if isinstance(e, Announce)]
        self.assertEqual([e.text for e in announced],
                         ["Here is what I found. The Roborock S8 is the best value."])
        self.assertTrue(self.store.get(task_id).announced)
        self.assertTrue(self.chat.followup_pending, "the user may ask about it straight away")
        self.chat.hear("why that one", 61.0, "en", "en", conversation=True)
        self.assertIn("The Roborock S8 is the best value.", self.ai.instructions.notes)
        self.assertIn("not instructions", self.ai.instructions.notes)

    def test_nothing_is_said_while_luna_is_busy(self):
        self.delegate(t=1.0)
        task_id = self.service.started()[0]
        self.service.finish(task_id, "Done.")
        self.assertEqual([e for e in self.poll(60.0, speaking=True) if isinstance(e, Announce)],
                         [])
        self.assertEqual(len([e for e in self.poll(61.0) if isinstance(e, Announce)]), 1)

    def test_voice_controls(self):
        self.poll(0.0)
        say = lambda text, t: spoken(self.chat.hear(text, t, "en", "en"))
        self.assertEqual(say("luna what are you working on", 1.0),
                         ["I'm not working on any task right now."])
        self.assertEqual(say("luna cancel the task", 2.0), ["There is no task to stop."])
        task, _ = self.client.begin("Compare robot vacuums", "research", "en", t=3.0)
        self.assertEqual(say("luna what are you working on", 4.0),
                         ["I'm working on this: Compare robot vacuums"])
        self.client.begin("Find a recipe", "research", "en", t=4.5)
        self.assertEqual(say("luna what are you working on", 5.0),
                         ["I'm working on 2 tasks. The latest: Find a recipe"])
        self.assertEqual(say("luna cancel the task", 6.0), ["Okay, I stopped the task."])
        self.assertTrue(self.store.get(task.id).active, "the newest one was stopped")
        self.service.finish(self.service.started()[1], "Pancakes.")
        self.assertEqual([e for e in self.poll(60.0) if isinstance(e, Announce)], [])
        chat = Conversation(FakeAI())
        self.assertEqual(spoken(chat.hear("luna cancel the task", 1.0, "en", "en")),
                         ["I can't do tasks on this robot yet."])
        self.assertNotIn("start_task", chat.tools.registry.names())

    def test_restart_recovery_quiet_hours_and_swedish(self):
        task = self.store.add(Task(new_id(), "Jämför robotdammsugare", "research", "sv"))
        self.store.move(task.id, RUNNING)
        self.clock.now = NOON.replace(hour=23)
        chat = Conversation(FakeAI(), clock=self.clock)
        client = TaskClient(FakeTaskService().connect, self.store)
        chat.set_tasks(client, Announcer())
        chat.touched(0.5)
        self.assertEqual([e for e in chat.poll(1.0, False) if isinstance(e, Announce)], [],
                         "quiet hours")
        chat.hear("luna vad är klockan", 2.0, "sv", "sv")
        chat.interrupt()
        said = [e.text for e in chat.poll(3.0, False) if isinstance(e, Announce)]
        self.assertEqual(said, ["Jag blev avbruten när jag jobbade med det här: "
                                "Jämför robotdammsugare. Fråga mig gärna igen."])
        self.assertEqual(client.unannounced(), [])


class ConversationApprovalTests(unittest.TestCase):
    """A task's question: asked when Luna is free, answered by voice, tap or not at all."""

    def setUp(self):
        self.clock = Clock()
        self.store = temp_store(self, self.clock)
        self.chat = Conversation(FakeAI(), clock=self.clock)
        self.ran = []
        self.chat.set_plugins(tools=bridge_tools(self.ran))
        spec = dataclasses.replace(RESEARCH, tools=("face", "note", "unlock"))
        self.service = FakeTaskService()
        self.client = TaskClient(self.service.connect, self.store, FakeMeter(), FakeGuard(),
                                 catalog={"research": spec})
        self.chat.set_tasks(self.client, Announcer())
        self.chat.touched(0.0)
        self.chat.poll(0.0, False)

    def ask(self, tool="note", args=None, language="en", t=1.0):
        args = {"text": "milk"} if args is None else args
        task, _ = self.client.begin("Plan the shopping", "research", language, t=t)
        self.service.check(task.id, "c1", tool, args)
        self.service.pause(task.id, "c1")
        effects = self.chat.poll(t + 0.5, False)
        return task, effects

    def test_yes_by_voice_resumes_the_task_and_its_tool_changes_the_face(self):
        task, effects = self.ask()
        self.assertEqual([e.text for e in effects if isinstance(e, Announce)],
                         ["My task needs your OK: Plan the shopping. Should I go ahead?"])
        self.assertIsNotNone(self.chat.confirmation)
        self.assertEqual(self.chat.confirmation.summary(), "Save a note: milk")
        self.chat.poll(2.0, False)
        self.assertTrue(self.chat.in_followup(2.1), "the answer needs no wake word")
        self.assertEqual(spoken(self.chat.hear("yes", 2.5, "en", "en")), ["Okay."])
        self.assertIsNone(self.chat.confirmation)
        self.assertTrue(self.service.replies("resume")[-1]["approved"])
        self.service.tool_call(task.id, "c1", "note", {"text": "milk"})
        effects = self.chat.poll(3.0, False)
        self.assertIn(Act(("note",)), effects)
        self.assertEqual(self.ran, [("note", {"text": "milk"})])

    def test_no_answer_or_moving_on_means_no(self):
        task, _ = self.ask()
        self.chat.poll(2.0, False)
        self.chat.poll(62.0, False)
        self.assertIsNone(self.chat.confirmation)
        resume = self.service.replies("resume")[-1]
        self.assertFalse(resume["approved"])
        self.assertIn(("approval", "note", "no (timeout)"),
                      [step[1:] for step in self.store.steps(task.id)])
        self.service.fail(task.id, "turns")
        other, _ = self.ask(t=100.0)
        self.chat.poll(101.0, False)
        self.chat.hear("luna what time is it", 102.0, "en", "en")
        self.assertFalse(self.service.replies("resume")[-1]["approved"])

    def test_a_spoken_yes_does_not_approve_a_sensitive_tool(self):
        task, _ = self.ask("unlock", {})
        self.chat.poll(2.0, False)
        self.assertEqual(spoken(self.chat.hear("yes", 2.5, "en", "en")),
                         ["Please tap Yes on the screen."])
        self.assertEqual(self.service.replies("resume"), [])
        self.chat.answer_touch(True, 3.0)
        self.assertTrue(self.service.replies("resume")[-1]["approved"])

    def test_cancelling_the_task_closes_its_question_and_swedish(self):
        task, effects = self.ask(language="sv")
        self.assertEqual([e.text for e in effects if isinstance(e, Announce)],
                         ["Min uppgift behöver ditt godkännande: Plan the shopping. "
                          "Ska jag fortsätta?"])
        self.chat.poll(2.0, False)
        self.chat.hear("luna avbryt uppgiften", 2.5, "sv", "sv")
        self.assertIsNone(self.chat.confirmation)
        self.assertEqual(self.store.get(task.id).state, CANCELLED)
        self.assertEqual(self.service.replies("resume"), [])

    def test_a_question_from_before_a_restart_is_asked_again(self):
        task, _ = self.ask()
        self.chat.poll(2.0, False)
        chat = Conversation(FakeAI(), clock=self.clock)
        client = TaskClient(FakeTaskService().connect, self.store, catalog=self.client.catalog)
        chat.set_tasks(client, Announcer())
        chat.touched(5.0)
        said = [e.text for e in chat.poll(5.0, False) if isinstance(e, Announce)]
        self.assertEqual(said, ["My task needs your OK: Plan the shopping. Should I go ahead?"])
        self.assertEqual(chat.confirmation.payload.task_id, task.id)


class DemoModeAppTests(unittest.TestCase):
    """LUNA_TASKS=demo: the Tasks page and the task settings on the real app."""

    def setUp(self):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        from luna_core import app
        from luna_core.preferences import read_preferences
        from test_settings import FakeSpeech
        self.app = app
        folder = tempfile.mkdtemp()
        self.saved = []
        patches = [mock.patch.object(app, "speech", FakeSpeech()),
                   mock.patch.object(app, "read_preferences",
                                     lambda: read_preferences(Path(folder) / "prefs.json")),
                   mock.patch.object(app, "write_preferences",
                                     lambda prefs: self.saved.append(dict(prefs))),
                   mock.patch.dict(os.environ, {"LUNA_MEMORY_FILE": str(Path(folder) / "m.db"),
                                                "LUNA_TASKS_FILE": str(Path(folder) / "t.db"),
                                                "LUNA_TASKS": "demo"})]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        args = types.SimpleNamespace(fullscreen=False, windowed=True, no_voice=True,
                                     no_speech=False, no_ai=True)
        self.hud = app.RobotHUD(args)
        self.addCleanup(self.hud.tasks.close)
        self.addCleanup(app.pygame.quit)

    def test_tasks_page_and_settings(self):
        from luna_core import i18n
        from luna_core.settings import SECTIONS
        self.addCleanup(i18n.set_language, "en")
        self.assertEqual([p.name for p in self.hud.pages][4], "Tasks")
        tasks_page = self.hud.pages[4]
        self.hud.remove_page(tasks_page)
        self.assertIn(tasks_page, self.hud.pages, "a core page")
        self.hud.frame(0.0)
        self.assertTrue(self.hud.tasks.available)
        task, _ = self.hud.tasks.begin("Compare robot vacuums", "research", "en", t=0.5)
        for language in ("en", "sv"):
            i18n.set_language(language)
            self.hud.page = "Tasks"
            self.hud.frame(1.0 if language == "en" else 3.0)
        stop = self.app.pygame.Rect(930, 158, 150, 46)
        self.hud.handle_touch(*stop.center, 4.0)
        self.assertEqual(self.hud.tasks.store.get(task.id).state, CANCELLED)
        self.hud.page = "Settings"
        self.hud.settings_section = SECTIONS.index("AI")
        for action in (("quiet_hours", None), ("task_history_days", 90), ("tasks", None)):
            self.hud.frame(5.0)
            rect = [r for r, a in self.hud.settings_hits if a == action][0]
            self.hud.handle_touch(*rect.center, 5.0)
        self.assertEqual((self.saved[-1]["quiet_hours"], self.saved[-1]["task_history_days"],
                          self.saved[-1]["tasks"]), (False, 90, False))
        self.assertFalse(self.hud.conversation.announcer.quiet)
        self.assertEqual(self.hud.tasks.history_days, 90)
        self.assertFalse(self.hud.tasks.enabled)


if __name__ == "__main__":
    unittest.main()


class ServiceModeAppTests(unittest.TestCase):
    """LUNA_TASKS=service on the real app: opt-in, and no helper process until a task needs it."""

    def make(self, ai_on):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        from luna_core import app
        from luna_core.preferences import read_preferences
        from test_settings import FakeSpeech
        folder = tempfile.mkdtemp()
        patches = [mock.patch.object(app, "speech", FakeSpeech()),
                   mock.patch.object(app, "read_preferences",
                                     lambda: read_preferences(Path(folder) / "prefs.json")),
                   mock.patch.object(app, "write_preferences", lambda prefs: None),
                   mock.patch.object(app, "ai_configured", lambda: ai_on),
                   mock.patch.object(app, "AIWorker",
                                     lambda **kw: mock.MagicMock(answers=queue.Queue())),
                   mock.patch.object(app.task_agents, "available", lambda: True),
                   mock.patch.dict(os.environ, {"LUNA_MEMORY_FILE": str(Path(folder) / "m.db"),
                                                "LUNA_TASKS_FILE": str(Path(folder) / "t.db"),
                                                "LUNA_TASKS": "service"})]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        args = types.SimpleNamespace(fullscreen=False, windowed=True, no_voice=True,
                                     no_speech=False, no_ai=False)
        hud = app.RobotHUD(args)
        self.addCleanup(app.pygame.quit)
        return hud

    def test_off_without_the_ai(self):
        hud = self.make(ai_on=False)
        self.assertIsNone(hud.tasks)
        self.assertNotIn("Tasks", [page.name for page in hud.pages])

    def test_on_but_no_process_until_a_task_needs_it(self):
        hud = self.make(ai_on=True)
        self.addCleanup(hud.tasks.close)
        connects = []
        hud.tasks.connect = lambda: connects.append(1) or FakeTaskService().connect()
        for frame in range(5):
            hud.frame(frame * 0.1)
        self.assertEqual((hud.tasks.lazy, hud.tasks.running, connects), (True, False, []))
        self.assertTrue(hud.tasks.available)
        self.assertIn("Tasks", [page.name for page in hud.pages])
        self.assertIn("start_task", hud.conversation.tools.registry.names())
        self.assertIs(hud.tasks.executor(), hud.conversation.tools)

