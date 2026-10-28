"""Phase 5.3: interrupting Luna by voice while she talks."""
import unittest

from robot_hud.assistant_core.conversation import Conversation
from robot_hud.assistant_core.turn_taking import interruption
from robot_hud.commands import route, vocabulary
from test_assistant_core import FakeAI
from test_listening import ListeningAppTests


class InterruptionTests(unittest.TestCase):
    def test_stop_and_wake_word(self):
        for text, language, kind in (("stop", "en", "stop"), ("luna stop", "en", "stop"),
                                     ("okay stop", "en", "stop"), ("be quiet", "en", "stop"),
                                     ("luna sluta", "sv", "stop"), ("var tyst", "sv", "stop"),
                                     ("hey luna", "en", "wake"), ("luna what time is it", "en", "wake"),
                                     ("hej luna", "sv", "wake"), ("what a nice day", "en", None),
                                     ("luna quiet", "en", "wake"), ("", "en", None)):
            with self.subTest(text):
                self.assertEqual(interruption(text, language, "The moon is far away."), kind)

    def test_her_own_words_are_echo(self):
        saying = "Please stop at the red light. My name is Luna."
        self.assertIsNone(interruption("please stop", "en", saying))
        self.assertIsNone(interruption("stop", "en", saying))
        self.assertIsNone(interruption("my name is luna", "en", saying))
        self.assertEqual(interruption("luna stop", "en", "Please stop at the light."), "stop",
                         "'luna' is a new word: not echo")

    def test_stop_is_a_control_and_in_the_offline_vocabulary(self):
        self.assertEqual(route("luna stop", language="en"), ("control", "stop"))
        self.assertEqual(route("sluta", conversation=True, language="sv"), ("control", "stop"))
        self.assertEqual(route("stop", language="en"), ("ignore", ""), "background talk")
        self.assertEqual(route("luna quiet", language="en")[0], "command", "wave command kept")
        self.assertIn("stop", vocabulary("en"))
        self.assertIn("sluta", vocabulary("sv"))


class ConversationBargeInTests(unittest.TestCase):
    def test_stop_drops_the_answer_without_a_word(self):
        ai = FakeAI()
        chat = Conversation(ai)
        chat.ask("tell me a story", 1.0, "en")
        ai.send("text", "Once upon a time there was a fox.")
        chat.poll(1.1, speaking=True)
        self.assertEqual(chat.barge_in("the fox", "en"), (None, []), "her own words")
        kind, effects = chat.barge_in("luna stop", "en")
        self.assertEqual(kind, "stop")
        self.assertEqual(ai.cancelled, [1])
        self.assertFalse(chat.streaming)
        chat.poll(1.2, speaking=False)
        self.assertEqual(ai.confirmed, [], "an interrupted answer is not remembered")
        effects = chat.hear("luna stop", 2.0, "en", "en")
        self.assertFalse([e for e in effects if type(e).__name__ == "Speak"], "stop says nothing")


class BargeInAppTests(unittest.TestCase):
    """The app part, with the listening tests' fakes."""
    setUp = ListeningAppTests.setUp
    say = ListeningAppTests.say

    def talk(self, t, answer="Once upon a time there was a fox."):
        self.hud.ask_ai("tell me a story", t)
        self.ai.send("text", answer)
        self.ai.send("sentence", answer)
        self.speech.talking = True
        self.hud.poll_voice(t)

    def test_off_luna_cannot_hear_while_talking(self):
        self.talk(1.0)
        self.say(1.5, final="luna stop")
        self.assertEqual((self.speech.stops, self.ai.cancelled), (0, []))

    def test_on_stop_by_voice(self):
        self.hud.settings_choice("voice_interrupt", None)
        self.talk(1.0)
        self.say(1.3, final="what a nice day")
        self.say(1.4, partial="the fox")
        self.assertEqual(self.speech.stops, 0, "other talk and her own echo do not count")
        self.say(1.5, final="luna stop")
        self.assertEqual((self.speech.stops, self.ai.cancelled), (1, [1]))
        self.speech.talking = False
        self.say(3.0)
        self.assertEqual(len(self.ai.asked), 1, "nothing else is asked")

    def test_on_a_new_question_cuts_in(self):
        self.hud.settings_choice("voice_interrupt", None)
        self.talk(1.0)
        self.say(1.5, partial="hey luna")
        self.assertEqual(self.ai.cancelled, [1], "quiet as soon as 'Luna' is heard")
        self.speech.talking = False
        self.say(1.8, partial="hey luna what time")
        self.say(2.2, final="hey luna what time is it")
        self.say(3.5)
        self.assertEqual(self.ai.asked[-1], ("what time is it", "en"))


if __name__ == "__main__":
    unittest.main()
