"""Phase 5.0C: structured AI tools (registry, validation, permission policy, executor, rounds)."""
import json
import threading
import types
import unittest

from robot_hud import ai
from robot_hud.ai import AIWorker
from robot_hud.assistant_core.context import ConversationSettings
from robot_hud.assistant_core.conversation import Conversation
from robot_hud.assistant_core.events import Act, Log, Speak
from robot_hud.assistant_core.tools import (MAX_CALLS_PER_TURN, PermissionPolicy, Tool, ToolCall,
                                            ToolExecutor, ToolRegistry, ToolResult, face_registry)
from test_assistant_core import FakeAI
import test_streaming
from test_streaming import event


def call(name, call_id="c1", **arguments):
    return ToolCall(call_id, name, json.dumps(arguments))


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tools = ToolExecutor(face_registry())

    def test_schemas_are_strict_and_closed(self):
        schemas = face_registry().schemas()
        self.assertEqual([s["name"] for s in schemas], ["set_face_mood", "set_face_color",
                         "set_face_mode", "set_gaze_direction", "blink"])
        for schema in schemas:
            self.assertTrue(schema["strict"])
            self.assertFalse(schema["parameters"]["additionalProperties"])
            self.assertEqual(schema["parameters"]["required"],
                             sorted(schema["parameters"]["properties"]))

    def test_valid_calls_become_quiet_face_actions(self):
        cases = ((call("set_face_color", color="purple"), ("color", "PURPLE")),
                 (call("set_face_mood", "c2", mood="happy"), ("expression", "happy")),
                 (call("set_gaze_direction", "c3", direction="left"), ("look", (-1.0, 0.0))),
                 (call("set_face_mode", "c4", mode="wave"), ("mode", "WAVE")),
                 (call("blink", "c5"), ("blink", None)))
        for tool_call, action in cases:
            effects, result = self.tools.execute(tool_call)
            self.assertEqual(effects, [Act((action,), quiet=True)])
            self.assertTrue(result.ok)
            self.assertTrue(json.loads(result.output())["ok"])

    def test_invalid_calls_do_nothing(self):
        for tool_call in (call("set_face_color", color="pink"),
                          call("set_face_mood", mood="happy", extra="x"),
                          call("set_face_mood"),
                          ToolCall("c1", "set_face_mood", "{not json"),
                          ToolCall("c1", "set_face_mood", "[1]"),
                          call("drive_forward", metres="2"),
                          ToolCall("c1", "set_face_mood", json.dumps({"mood": 3}))):
            with self.subTest(tool_call):
                effects, result = ToolExecutor(face_registry()).execute(tool_call)
                self.assertEqual((effects, result.ok), ([], False))

    def test_a_call_never_runs_twice(self):
        first = self.tools.execute(call("blink", "c1"))
        self.assertEqual(self.tools.execute(call("blink", "c1")), ([], first[1]))
        self.assertEqual(self.tools.execute(call("blink", "c2")), ([], first[1]), "same action")
        self.tools.new_turn()
        self.assertTrue(self.tools.execute(call("blink", "c1"))[0], "a new turn may blink again")

    def test_actions_per_turn_are_limited(self):
        moods = ("happy", "sad", "angry", "sleepy", "surprised", "neutral")
        directions = ("left", "right")
        ran = [self.tools.execute(call("set_face_mood", str(i), mood=m))[0]
               for i, m in enumerate(moods)]
        ran += [self.tools.execute(call("set_gaze_direction", d, direction=d))[0]
                for d in directions]
        self.assertEqual(sum(1 for effects in ran if effects), MAX_CALLS_PER_TURN)

    def test_policy_never_allows_motion_or_sensitive_tools(self):
        def tool(name, risk):
            return Tool(name, "", {"type": "object", "properties": {}, "required": [],
                                   "additionalProperties": False}, risk,
                        lambda a: ([Act((("blink", None),))], ToolResult(True, "ran")))
        registry = ToolRegistry([tool("drive", "motion"), tool("unlock", "sensitive"),
                                 tool("lights", "device"), tool("temperature", "info")])
        everything = ToolExecutor(registry, PermissionPolicy(("local", "info", "device",
                                                               "motion", "sensitive")))
        default = ToolExecutor(registry)
        for name in ("drive", "unlock"):
            effects, result = everything.execute(call(name, name))
            self.assertEqual(effects, [], name)
            self.assertIn("not allowed", result.message)
        self.assertEqual(default.execute(call("lights", "l"))[0], [], "not allowed by default")
        self.assertTrue(everything.execute(call("temperature", "t"))[0])
        with self.assertRaises(ValueError):
            registry.register(tool("drive", "motion"))


class ConversationToolTests(unittest.TestCase):
    def setUp(self):
        self.ai = FakeAI()
        self.chat = Conversation(self.ai)

    def test_tools_are_offered_with_their_rules(self):
        self.chat.ask("you look tired, cheer up", 1.0, "en")
        self.assertEqual(len(self.ai.tools), 5)
        self.assertIn("When you call a tool, write no other text", self.ai.instructions)
        chat = Conversation(self.ai, ConversationSettings(tools=False))
        chat.ask("hello", 1.0, "en")
        self.assertIsNone(self.ai.tools)
        self.assertNotIn("call a tool", self.ai.instructions)

    def test_results_are_sent_after_the_app_applied_the_actions(self):
        self.chat.ask("make your eyes purple", 1.0, "en")
        self.ai.send("tools", [call("set_face_color", color="purple")])
        effects = self.chat.poll(1.2, speaking=False)
        self.assertIn(Act((("color", "PURPLE"),), quiet=True), effects)
        self.assertIn(Log("Luna tool: set_face_color -> ok"), effects)
        self.assertEqual(self.ai.results, [], "the app applies the effects first")
        self.assertTrue(self.chat.thinking and self.chat.streaming)
        self.chat.poll(1.3, speaking=False)
        (request, outputs), = self.ai.results
        self.assertEqual((request, outputs[0][0]), (1, "c1"))
        self.assertTrue(json.loads(outputs[0][1])["ok"])

    def test_cancelled_or_old_tool_calls_never_run(self):
        self.chat.ask("cheer up", 1.0, "en")
        self.ai.send("tools", [call("set_face_mood", mood="happy")])
        self.chat.interrupt()
        self.assertEqual(self.chat.poll(1.2, speaking=False), [])
        self.chat.ask("again", 2.0, "en")
        self.ai.send("tools", [call("blink")])
        self.chat.poll(2.1, speaking=False)
        self.chat.interrupt()  # stopped before the result was sent
        self.chat.poll(2.2, speaking=False)
        self.assertEqual(self.ai.results, [])


class ToolBrain:
    """Fake brain: each round is a list of text pieces and ToolCalls."""

    def __init__(self, rounds, gate=None):
        self.rounds, self.gate = rounds, gate
        self.requests, self.previous_id = [], None

    def stream(self, given, language="en", instructions=None, result=None, **options):
        self.requests.append((given, options))
        number = len(self.requests)
        for piece in self.rounds[min(number, len(self.rounds)) - 1]:
            if isinstance(piece, ToolCall):
                result["calling"] = True
                result.setdefault("calls", []).append(piece)
            else:
                yield piece
        result["id"] = "resp-%d" % number

    def remember(self, response_id):
        self.previous_id = response_id

    def forget(self):
        self.previous_id = None


def run(chat, seconds=5.0):
    """Poll like the app does until the answer is over; returns (spoken, acts)."""
    spoken, acts = [], []
    for _ in range(int(seconds / 0.01)):
        for effect in chat.poll(1.0, speaking=False):
            if isinstance(effect, Speak):
                spoken.append(effect.text)
            elif isinstance(effect, Act):
                acts.append(effect)
        if not chat.streaming:
            break
        threading.Event().wait(0.01)
    chat.poll(2.0, speaking=False)
    return spoken, acts


class WorkerToolTests(unittest.TestCase):
    def test_tool_round_then_spoken_answer(self):
        brain = ToolBrain([["Sure", call("set_face_color", color="purple"), " thing."],
                           ["My eyes are purple now."]])
        chat = Conversation(AIWorker(brain_factory=lambda: brain))
        chat.ask("make your eyes purple", 1.0, "en")
        spoken, acts = run(chat)
        self.assertEqual(acts, [Act((("color", "PURPLE"),), quiet=True)])
        self.assertEqual(spoken, ["My eyes are purple now."], "text beside the call is not said")
        self.assertEqual(chat.answer, "My eyes are purple now.")
        (first, options0), (second, options1) = brain.requests
        self.assertEqual(options0["tool_choice"], "auto")
        self.assertNotIn("previous", options0)
        self.assertEqual(options1["previous"], "resp-1")
        self.assertEqual(second[0]["type"], "function_call_output")
        self.assertTrue(json.loads(second[0]["output"])["ok"])
        self.assertEqual(brain.previous_id, "resp-2", "the whole exchange is remembered")

    def test_rounds_are_limited_and_the_last_one_must_answer(self):
        brain = ToolBrain([[call("blink", "a")], [call("blink", "b")], [call("blink", "c")],
                           ["Done blinking."]])
        chat = Conversation(AIWorker(brain_factory=lambda: brain))
        chat.ask("blink a lot", 1.0, "en")
        spoken, acts = run(chat)
        self.assertEqual(len(brain.requests), ai.MAX_TOOL_ROUNDS + 1)
        self.assertEqual(brain.requests[-1][1]["tool_choice"], "none")
        self.assertEqual(len(acts), 1, "the same blink runs once per question")
        self.assertEqual(spoken, ["Done blinking."])

    def test_cancel_while_waiting_for_tools(self):
        brain = ToolBrain([[call("blink")], ["Never said."]])
        worker = AIWorker(brain_factory=lambda: brain)
        request = worker.ask("blink", "en", tools=[{}])
        kind, got, calls = worker.answers.get(timeout=5)
        self.assertEqual((kind, got), ("tools", request))
        worker.cancel(request)
        worker.tool_results(request, [("c1", "{}")])
        for _ in range(100):
            if not worker.busy and len(brain.requests) == 1:
                break
            threading.Event().wait(0.01)
        threading.Event().wait(0.05)
        self.assertEqual(len(brain.requests), 1, "no second round after cancel")
        self.assertTrue(worker.answers.empty())


class LunaBrainToolTests(unittest.TestCase):
    brain = test_streaming.LunaBrainStreamTests.brain

    def test_function_calls_are_collected(self):
        item = types.SimpleNamespace(type="function_call", call_id="c1", name="blink",
                                     arguments="{}")
        brain = self.brain([event("response.output_item.added", item=item),
                            event("response.output_item.done", item=item),
                            event("response.completed", response=types.SimpleNamespace(id="r1"))])
        result = {}
        tools = face_registry().schemas()
        self.assertEqual(list(brain.stream("blink", "en", "Be brief.", result, tools=tools)), [])
        self.assertEqual(result, {"calling": True, "calls": [ToolCall("c1", "blink", "{}")],
                                  "id": "r1"})
        self.assertEqual((self.calls[0]["tools"], self.calls[0]["tool_choice"]), (tools, "auto"))
        outputs = [{"type": "function_call_output", "call_id": "c1", "output": "{}"}]
        list(brain.stream(outputs, "en", "Be brief.", {}, tools=tools, tool_choice="none",
                          previous="r1"))
        self.assertEqual((self.calls[1]["input"], self.calls[1]["previous_response_id"],
                          self.calls[1]["tool_choice"]), (outputs, "r1", "none"))
        list(brain.stream("hi"))
        self.assertNotIn("tools", self.calls[2])

if __name__ == "__main__":
    unittest.main()
