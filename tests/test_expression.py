"""Phase 5.0D: answer expressions (mood tag), progress feedback and face motion."""
import unittest

from robot_hud.ai import AIWorker
from robot_hud.assistant_core.context import ConversationSettings, instructions, settings_from_env
from robot_hud.assistant_core.conversation import Conversation
from robot_hud.assistant_core.expression import LINGER_SECONDS, MoodTag, face_motion
from robot_hud.assistant_core.tools import ToolCall
from test_assistant_core import FakeAI, spoken
from test_streaming import StreamBrain, collect


def run_tag(pieces):
    tag, moods, out = MoodTag(), [], []
    for piece in pieces:
        mood, text = tag.feed(piece)
        moods.append(mood)
        out.append(text)
    out.append(tag.flush())
    return [m for m in moods if m], "".join(out), out


class MoodTagTests(unittest.TestCase):
    def test_tag_split_across_pieces_is_removed(self):
        moods, text, _ = run_tag(["[ha", "ppy] Hello", " there."])
        self.assertEqual((moods, text), (["happy"], "Hello there."))

    def test_no_tag_is_released_immediately(self):
        _, text, out = run_tag(["Hello", " there."])
        self.assertEqual((text, out[0]), ("Hello there.", "Hello"))

    def test_only_a_leading_tag_counts(self):
        moods, text, _ = run_tag(["Hi [sad] there."])
        self.assertEqual((moods, text), ([], "Hi [sad] there."))

    def test_unknown_tags_and_brackets(self):
        self.assertEqual(run_tag(["[smile] Hi."])[:2], ([], "Hi."))
        self.assertEqual(run_tag(["[1] First step."])[:2], ([], "[1] First step."))
        self.assertEqual(run_tag(["[hap"])[:2], ([], "[hap"), "held text comes back at the end")
        self.assertEqual(run_tag(["[NEUTRAL]", "Ok."])[:2], (["neutral"], "Ok."))


class WorkerMoodTests(unittest.TestCase):
    def test_tag_never_shown_or_spoken(self):
        brain = StreamBrain(["[surp", "rised] Wow, that is a big planet. It ", "has two moons."])
        worker = AIWorker(brain_factory=lambda: brain)
        request = worker.ask("q")
        events = collect(worker)
        self.assertEqual(events[0], ("mood", request, "surprised"))
        self.assertEqual("".join(v for k, _, v in events if k == "text"),
                         "Wow, that is a big planet. It has two moons.")
        self.assertEqual([v for k, _, v in events if k == "sentence"],
                         ["Wow, that is a big planet.", "It has two moons."])


class ConversationExpressionTests(unittest.TestCase):
    def setUp(self):
        self.ai = FakeAI()
        self.chat = Conversation(self.ai)

    def answer(self, *messages, t=1.5, speaking=True):
        for kind, value in messages:
            self.ai.send(kind, value)
        return self.chat.poll(t, speaking=speaking, talking=speaking)

    def test_expression_while_answering_then_linger_and_clear(self):
        self.chat.ask("joke", 1.0, "en")
        self.answer(("mood", "happy"), ("text", "Ha."), ("sentence", "Ha."), ("done", None))
        self.assertEqual(self.chat.expression, "happy")
        self.chat.poll(5.0, speaking=False)
        self.assertEqual(self.chat.expression, "happy", "lingers after speaking")
        self.chat.poll(5.0 + LINGER_SECONDS, speaking=False)
        self.assertIsNone(self.chat.expression)

    def test_neutral_disabled_tool_mood_and_interrupt(self):
        self.chat.ask("q", 1.0, "en")
        self.answer(("mood", "neutral"))
        self.assertIsNone(self.chat.expression)
        self.answer(("mood", "sad"))
        self.chat.interrupt()
        self.assertIsNone(self.chat.expression, "cleared when the user stops Luna")

        self.chat.ask("cheer up", 2.0, "en")
        self.answer(("mood", "sad"),
                    ("tools", [ToolCall("c1", "set_face_mood", '{"mood": "happy"}')]))
        self.assertIsNone(self.chat.expression, "the tool's mood wins")
        self.answer(("mood", "sad"))
        self.assertIsNone(self.chat.expression)

        chat = Conversation(self.ai, ConversationSettings(expressions=False))
        chat.ask("q", 1.0, "en")
        self.ai.send("mood", "happy")
        chat.poll(1.5, speaking=False)
        self.assertIsNone(chat.expression)

    def test_error_clears_expression(self):
        self.chat.ask("q", 1.0, "en")
        self.answer(("mood", "happy"), ("error", "Sorry, I could not answer that."))
        self.assertIsNone(self.chat.expression)

    def test_slow_answer_says_one_moment_once(self):
        self.chat.ask("hard", 1.0, "sv")
        self.assertEqual(spoken(self.chat.poll(6.9, speaking=False)), [])
        self.assertEqual(spoken(self.chat.poll(7.0, speaking=False)), ["Ett ögonblick."])
        self.assertTrue(self.chat.slow)
        self.assertEqual(spoken(self.chat.poll(20.0, speaking=False)), [], "once per question")
        self.chat.ask("again", 30.0, "en")
        self.assertFalse(self.chat.slow)

    def test_fast_answer_and_disabled_progress_stay_silent(self):
        self.chat.ask("q", 1.0, "en")
        self.answer(("text", "Hi."), ("sentence", "Hi."), t=2.0)
        self.assertEqual(spoken(self.chat.poll(10.0, speaking=False)), [])
        chat = Conversation(self.ai, ConversationSettings(progress_seconds=0))
        chat.ask("q", 1.0, "en")
        self.assertEqual(spoken(chat.poll(60.0, speaking=False)), [])


class SettingsAndMotionTests(unittest.TestCase):
    def test_settings_and_instruction_rule(self):
        settings = settings_from_env({"LUNA_AI_EXPRESSIONS": "off", "LUNA_PROGRESS_SECONDS": "3"})
        self.assertEqual((settings.expressions, settings.progress_seconds), (False, 3.0))
        self.assertIn("[happy]", instructions("en", expressions=True))
        self.assertNotIn("[happy]", instructions("en"))

    def test_face_motion(self):
        self.assertEqual(face_motion(1.0, False, False), (1.0, 0.0))
        self.assertGreater(face_motion(1.0, True, False, level=1.0)[0],
                           face_motion(1.0, True, False, level=0.0)[0])
        self.assertNotEqual(face_motion(0.5, False, True)[1], 0.0)


if __name__ == "__main__":
    unittest.main()
