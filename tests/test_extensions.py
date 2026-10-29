"""Tests for Luna's explicitly enabled, reusable extension interface."""
import json
import unittest

from robot_hud.assistant_core.context import ConversationSettings
from robot_hud.assistant_core.conversation import Conversation
from robot_hud.assistant_core.extensions import LunaExtension, load_extensions
from robot_hud.assistant_core.tools import Tool, ToolCall, ToolExecutor, ToolRegistry, ToolResult


EMPTY = {"type": "object", "properties": {}, "required": [],
         "additionalProperties": False}


def quiz_tool(name="start_quiz"):
    return Tool(name, "Start a practice question.", EMPTY, "local",
                lambda _: ([], ToolResult(True, "Quiz is ready")))


class Entry:
    def __init__(self, name, factory):
        self.name, self.factory = name, factory
        self.loads = 0

    def load(self):
        self.loads += 1
        return self.factory


class FakeAI:
    def ask(self, question, language, instructions, tools=None):
        self.instructions, self.schemas = instructions, tools
        return 1


class ExtensionTests(unittest.TestCase):
    def test_disabled_extensions_do_not_import_any_code(self):
        entry = Entry("education", lambda: LunaExtension("education"))
        self.assertEqual(load_extensions("", [entry]), ((), ()))
        self.assertEqual(entry.loads, 0)

    def test_only_requested_extension_loads(self):
        education = Entry("education", lambda: LunaExtension(
            "education", (quiz_tool(),), ("offer arithmetic quizzes",)))
        home = Entry("home", lambda: LunaExtension("home"))
        extensions, errors = load_extensions("education,education", [education, home])
        self.assertEqual(errors, ())
        self.assertEqual([item.name for item in extensions], ["education"])
        self.assertEqual(education.loads, 1)
        self.assertEqual(home.loads, 0)

    def test_missing_broken_and_duplicate_tools_fail_closed(self):
        good = Entry("education", lambda: LunaExtension("education", (quiz_tool(),)))
        collision = Entry("home", lambda: LunaExtension("home", (quiz_tool(),)))
        broken = Entry("broken", lambda: 1 / 0)
        extensions, errors = load_extensions("education,home,missing,broken",
                                             [good, collision, broken])
        self.assertEqual([item.name for item in extensions], ["education"])
        self.assertEqual(dict(errors), {"home": "ValueError", "missing": "not installed",
                                        "broken": "ZeroDivisionError"})

    def test_core_tool_names_cannot_be_overridden(self):
        for name in ("blink", "remember_fact"):
            with self.subTest(name=name):
                entry = Entry("bad", lambda: LunaExtension("bad", (quiz_tool(name),)))
                self.assertEqual(load_extensions("bad", [entry])[0], ())

    def test_unsupported_api_version_is_rejected(self):
        entry = Entry("future", lambda: LunaExtension("future", api_version=2))
        self.assertEqual(load_extensions("future", [entry])[1],
                         (("future", "ValueError"),))

    def test_duplicate_entry_point_names_are_rejected(self):
        entries = [Entry("education", lambda: LunaExtension("education")) for _ in range(2)]
        self.assertEqual(load_extensions("education", entries)[1],
                         (("education", "duplicate entry point"),))
        self.assertEqual([entry.loads for entry in entries], [0, 0])

    def test_extension_is_available_before_and_after_memory_change(self):
        extension = LunaExtension("education", (quiz_tool(),),
                                  ("offer arithmetic quizzes",))
        chat = Conversation(extensions=(extension,))
        self.assertIn("start_quiz", chat.tools.registry.names())
        chat.set_memory(object())
        self.assertIn("start_quiz", chat.tools.registry.names())
        self.assertIn("remember_fact", chat.tools.registry.names())
        chat.set_memory(None)
        self.assertIn("start_quiz", chat.tools.registry.names())
        self.assertNotIn("remember_fact", chat.tools.registry.names())

    def test_ai_gets_only_enabled_extension_capabilities(self):
        extension = LunaExtension("education", (quiz_tool(),),
                                  ("offer arithmetic quizzes",))
        ai = FakeAI()
        chat = Conversation(ai, ConversationSettings(web_search=False),
                            extensions=(extension,))
        chat.ask("teach me maths", 1, "en")
        self.assertIn("offer arithmetic quizzes", ai.instructions)
        self.assertIn("start_quiz", [item["name"] for item in ai.schemas])
        self.assertIn("smart-home control", ai.instructions)

        off = Conversation(ai, ConversationSettings(tools=False, web_search=False),
                           extensions=(extension,))
        off.ask("teach me maths", 2, "en")
        self.assertNotIn("offer arithmetic quizzes", ai.instructions)
        self.assertIsNone(ai.schemas)

    def test_verified_connection_removes_only_its_not_connected_warning(self):
        extension = LunaExtension("home", (quiz_tool("home_status"),),
                                  ("read connected home sensor values",),
                                  ("smart-home control",))
        ai = FakeAI()
        chat = Conversation(ai, ConversationSettings(web_search=False),
                            extensions=(extension,))
        chat.ask("home status", 1, "en")
        self.assertNotIn("Not connected yet: smart-home control", ai.instructions)
        self.assertNotIn("smart-home control", ai.instructions)
        self.assertIn("motors or wheels", ai.instructions)

    def test_extension_obeys_same_permission_policy(self):
        tool = Tool("lights", "Toggle a light", EMPTY, "device",
                    lambda _: ([], ToolResult(True, "done")))
        extension = LunaExtension("home", (tool,))
        chat = Conversation(extensions=(extension,))
        effects, result = chat.tools.execute(ToolCall("1", "lights", "{}"))
        self.assertEqual(effects, [])
        self.assertFalse(result.ok)
        self.assertIn("not allowed", result.message)

    def test_broken_tool_cannot_crash_core(self):
        def broken(_):
            raise RuntimeError("internal details must not be spoken")
        tool = Tool("broken", "Broken tool", EMPTY, "local", broken)
        executor = ToolExecutor(ToolRegistry((tool,)))
        effects, result = executor.execute(ToolCall("1", "broken", json.dumps({})))
        self.assertEqual(effects, [])
        self.assertEqual(result.message, "tool failed (RuntimeError)")
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
