"""Task service step 2: the general Yes/No confirmation (tasks/confirm.py) and its use for
memories the user did not ask for in their own words."""
import json
import tempfile
import unittest
from pathlib import Path

from luna_core import languages
from luna_core.assistant_core.conversation import (CONFIRM_DECLINED, CONFIRM_DONE, CONFIRM_TAP,
                                                   Conversation)
from luna_core.assistant_core.memory import CONFIRM_LABELS, MemoryStore
from luna_core.assistant_core.tools import (PermissionPolicy, Tool, ToolExecutor, ToolRegistry,
                                            ToolResult, _object)
from luna_core.tasks.confirm import Confirmation, Item, answer, call_key
from test_assistant_core import FakeAI, spoken
from test_tools import call

SAVE_TEA = call("remember_fact", fact="You like tea.", replaces=0)


class AnswerTests(unittest.TestCase):
    def test_whole_phrases_in_each_language(self):
        for text, language, expected in (("Yes please.", "en", "yes"), ("okay", "en", "yes"),
                                         ("No, don't", "en", "no"), ("Ja tack!", "sv", "yes"),
                                         ("nej", "sv", "no"), ("yes but call it green tea", "en", None),
                                         ("I like tea", "en", None), ("", "en", None)):
            with self.subTest(text):
                self.assertEqual(answer(text, language), expected)

    def test_touch_items_need_a_tap_and_silence_is_no(self):
        question = Confirmation([Item("lock:1", "Unlock the door", touch=True)], 10.0)
        self.assertEqual(question.by_voice("yes", "en"), "tap", "a spoken yes is not enough")
        self.assertEqual(question.by_voice("no", "en"), "no", "but no always works")
        self.assertFalse(question.expired(69.9))
        self.assertTrue(question.expired(70.0))

    def test_an_approval_covers_the_exact_arguments(self):
        self.assertEqual(call_key("remember_fact", {"fact": "a", "replaces": 0}),
                         call_key("remember_fact", {"replaces": 0, "fact": "a"}))
        self.assertNotEqual(call_key("remember_fact", {"fact": "a", "replaces": 0}),
                            call_key("remember_fact", {"fact": "b", "replaces": 0}))

    def test_every_pack_has_answers_and_screen_texts(self):
        for code, pack in languages.PACKS.items():
            with self.subTest(code):
                self.assertTrue(pack.answers.get("yes") and pack.answers.get("no"))
                if code != languages.DEFAULT:
                    texts = set(CONFIRM_LABELS) | {"Please confirm", "Yes", "No",
                                                   "Tap Yes to confirm.", "Say yes or no, or tap."}
                    self.assertEqual(texts - set(pack.screen), set())


def _sensitive(log):
    def unlock(arguments):
        log.append(arguments["door"])
        return [], ToolResult(True, "unlocked")
    return Tool("unlock_door", "Unlock a door.", _object({"door": {"type": "string"}}),
                "sensitive", unlock, confirm=lambda a: ("Unlock the door", a["door"]))


class PolicyTests(unittest.TestCase):
    def test_which_refusals_may_be_confirmed(self):
        policy = PermissionPolicy()
        sensitive = _sensitive([])
        self.assertEqual(policy.decide(sensitive, {"door": "front"}).confirm, "touch")
        self.assertTrue(policy.decide(sensitive, {"door": "front"}, confirmed=True).allowed)
        motion = Tool("drive", "Drive.", _object(), "motion", None)
        self.assertEqual(policy.decide(motion, {}, confirmed=True).allowed, False)
        self.assertEqual(policy.decide(motion, {}).confirm, "", "movement is never asked about")
        device = Tool("lamp", "Lamp.", _object(), "device", None, asked_with="remember")
        self.assertEqual(policy.decide(device, {}).confirm, "",
                         "a risk the settings do not allow is not offered either")


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(Path(tempfile.mkdtemp()) / "memory.db")
        self.ai = FakeAI()
        self.chat = Conversation(self.ai, memory=self.store)

    def luna_asks(self, tool_call=SAVE_TEA, t=1.0):
        """The user mentions something; the AI's save is refused and it asks instead."""
        self.chat.ask("I really like tea", t, "en")
        self.ai.send("tools", [tool_call])
        self.chat.poll(t + 0.1, speaking=False)
        self.chat.poll(t + 0.15, speaking=False)  # sends the tool results back
        refused = json.loads(self.ai.results[-1][1][0][1])
        self.assertFalse(refused["ok"])
        self.ai.send("text", "Should I remember that you like tea?")
        self.ai.send("done")
        self.chat.poll(t + 0.2, speaking=True)
        self.assertIsNone(self.chat.confirmation, "not before Luna has finished asking")
        self.chat.poll(t + 2.0, speaking=False)
        return self.chat.confirmation

    def test_yes_saves_exactly_what_was_shown_without_an_ai_call(self):
        question = self.luna_asks()
        self.assertEqual([(i.label, i.detail, i.touch) for i in question.items],
                         [("Remember", "You like tea.", False)])
        asked = len(self.ai.asked)
        effects = self.chat.hear("yes please", 3.0, "en", "en", conversation=True)
        self.assertEqual(spoken(effects), [CONFIRM_DONE])
        self.assertEqual([f.text for f in self.store.facts()], ["You like tea."])
        self.assertEqual(len(self.ai.asked), asked, "the yes is handled in code")
        self.assertIsNone(self.chat.confirmation)
        self.chat.ask("what else do you know", 4.0, "en")
        self.assertIn("They said yes, and it is done", self.ai.instructions)
        self.assertNotIn("They said yes", self.ai.instructions.fixed, "the cached part is unchanged")

    def test_no_saves_nothing_and_a_later_yes_grants_nothing(self):
        self.luna_asks()
        self.assertEqual(spoken(self.chat.hear("no", 3.0, "en", "en", conversation=True)),
                         [CONFIRM_DECLINED])
        self.chat.hear("Luna yes", 4.0, "en", "en")
        self.assertEqual(self.ai.asked[-1][0], "yes", "a yes with nothing open goes to the AI")
        self.assertIn("They said no, so it was not done", self.ai.instructions)
        self.ai.send("tools", [call("remember_fact", "c9", fact="You like tea.", replaces=0)])
        self.chat.poll(4.1, speaking=False)
        self.assertEqual(self.store.facts(), [], "the old refusal was not turned into a grant")

    def test_tap_answers_and_silence_means_no(self):
        self.luna_asks()
        self.assertEqual(spoken(self.chat.answer_touch(True, 3.0)), [CONFIRM_DONE])
        self.assertEqual(len(self.store.facts()), 1)
        self.store.forget_all()
        self.luna_asks(t=10.0)
        self.chat.poll(71.9, speaking=False)
        self.assertIsNotNone(self.chat.confirmation)
        self.chat.poll(72.1, speaking=False)
        self.assertIsNone(self.chat.confirmation)
        self.assertEqual(self.store.facts(), [])

    def test_talking_about_something_else_closes_it(self):
        self.luna_asks()
        self.chat.hear("what time is it", 3.0, "en", "en", conversation=True)
        self.assertIsNone(self.chat.confirmation)
        self.assertEqual(self.ai.asked[-1][0], "what time is it")
        self.assertIn("said something else instead", self.ai.instructions)
        self.assertEqual(self.store.facts(), [])

    def test_background_speech_is_not_an_answer(self):
        self.luna_asks()
        self.chat.session.close_followup()
        self.chat.hear("yes", 3.0, "en", "en", conversation=False)
        self.assertIsNotNone(self.chat.confirmation, "a yes not said to Luna counts for nothing")
        self.assertEqual(self.store.facts(), [])

    def test_swedish(self):
        self.luna_asks()
        self.assertTrue(self.chat.is_answer("ja tack", "sv"))
        self.chat.hear("ja tack", 3.0, "sv", "sv", conversation=True)
        self.assertEqual(len(self.store.facts()), 1)

    def test_sensitive_actions_need_a_tap(self):
        unlocked = []
        tools = ToolExecutor(ToolRegistry([_sensitive(unlocked)]))
        chat = Conversation(self.ai, tools=tools)
        chat.ask("unlock the front door", 1.0, "en")
        self.ai.send("tools", [call("unlock_door", door="front")])
        chat.poll(1.1, speaking=False)
        self.ai.send("done")
        chat.poll(1.2, speaking=False)
        chat.poll(1.3, speaking=False)
        self.assertTrue(chat.confirmation.touch)
        self.assertEqual(spoken(chat.hear("yes", 2.0, "en", "en", conversation=True)), [CONFIRM_TAP])
        self.assertEqual(unlocked, [])
        chat.answer_touch(True, 3.0)
        self.assertEqual(unlocked, ["front"])


if __name__ == "__main__":
    unittest.main()
