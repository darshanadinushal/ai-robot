"""Phase 5.0A: the assistant core works and is testable without pygame or audio devices."""
import queue
import subprocess
import sys
import threading
import unittest
from datetime import datetime
from pathlib import Path

from luna_core.ai import AIWorker
from luna_core.assistant_core.context import ConversationSettings, Profile, settings_from_env
from luna_core.assistant_core.conversation import Conversation
from luna_core.assistant_core.events import Accepted, Act, Log, Mood, Speak, StopSpeech
from luna_core.assistant_core.session import FOLLOWUP_SECONDS, QUESTION_FOLLOWUP_SECONDS
from test_streaming import StreamBrain

ROOT = Path(__file__).resolve().parents[1]


class FakeAI:
    def __init__(self):
        self.answers = queue.Queue()
        self.asked, self.cancelled, self.confirmed, self.forgot = [], [], [], 0
        self.busy = False
        self.next_id = 0
        self.results = []

    def ask(self, question, language="en", instructions=None, tools=None):
        if self.busy:
            return False
        self.asked.append((question, language))
        self.instructions, self.tools = instructions, tools
        self.next_id += 1
        return self.next_id

    def cancel(self, request):
        self.cancelled.append(request)

    def confirm(self, request):
        self.confirmed.append(request)

    def forget(self):
        self.forgot += 1

    def tool_results(self, request, outputs):
        self.results.append((request, outputs))

    def send(self, kind, value=None, request=None):
        self.answers.put((kind, request or self.next_id, value))


def spoken(effects):
    return [e.text for e in effects if isinstance(e, Speak)]


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.ai = FakeAI()
        self.chat = Conversation(self.ai)

    def test_core_does_not_import_pygame(self):
        code = ("import sys, luna_core.assistant_core.conversation; "
                "sys.exit('pygame' in sys.modules)")
        self.assertEqual(subprocess.run([sys.executable, "-c", code], cwd=ROOT).returncode, 0)

    def test_routes_commands_wake_ignore_and_ai(self):
        self.assertEqual(self.chat.hear("luna look left", 1.0, "en", "en"),
                         [Act((("look", (-1.0, 0.0)),))])
        self.assertEqual(spoken(self.chat.hear("hej luna", 2.0, "sv", "sv")), ["Ja?"])
        self.assertEqual(self.chat.hear("we should buy milk later today", 3.0, "en", "en"), [])
        self.assertEqual(self.chat.heard_kind, "ignore")
        self.assertEqual(self.chat.hear("hey luna why is the sky blue", 4.0, "en", "en"),
                         [Accepted("why is the sky blue")])
        self.assertEqual(self.ai.asked, [("why is the sky blue", "en")])
        self.assertTrue(self.chat.thinking and self.chat.streaming)

    def test_without_ai(self):
        chat = Conversation()
        self.assertEqual(spoken(chat.hear("hey luna why is the sky blue", 1.0, "en", "en")),
                         ["My AI brain is not set up yet."])
        self.assertFalse(chat.in_followup(1.0))

    def test_busy_ai_ignores_a_second_question(self):
        self.ai.busy = True
        self.assertEqual(self.chat.ask("again", 1.0, "en"), [])
        self.assertIsNone(self.chat.turn)

    def test_answer_spoken_in_order_then_remembered_and_follow_up(self):
        self.chat.ask("mars", 1.0, "sv")
        for kind, value in (("text", "Mars är röd. "), ("sentence", "Mars är röd."),
                            ("text", "Den har två månar."), ("sentence", "Den har två månar."),
                            ("done", None)):
            self.ai.send(kind, value)
        effects = self.chat.poll(1.5, speaking=True, talking=True)
        self.assertEqual(spoken(effects), ["Mars är röd.", "Den har två månar."])
        self.assertEqual([e for e in effects if isinstance(e, Log)][0].message,
                         "Luna: first spoken reply 0.5 s after the question")
        self.assertEqual(self.chat.answer, "Mars är röd. Den har två månar.")
        self.assertEqual(self.ai.confirmed, [], "not remembered while speaking")
        self.assertFalse(any(isinstance(e, Log) for e in self.chat.poll(1.6, True, True)),
                         "logged once")
        self.chat.poll(3.0, speaking=False)
        self.assertEqual(self.ai.confirmed, [1])
        self.assertTrue(self.chat.in_followup(3.0 + FOLLOWUP_SECONDS - 0.1))
        self.assertFalse(self.chat.in_followup(3.0 + FOLLOWUP_SECONDS))
        self.chat.poll(4.0, speaking=False)
        self.assertEqual(self.ai.confirmed, [1], "confirmed once")

    def test_lost_connection_after_speaking(self):
        self.chat.ask("q", 1.0, "sv")
        self.ai.send("sentence", "Första delen.")
        self.ai.send("lost")
        effects = self.chat.poll(1.2, speaking=False)
        self.assertEqual(spoken(effects), ["Första delen.", "Förlåt, jag tappade anslutningen."])
        self.assertIn(Mood("sad", 4.0), effects)
        self.assertFalse(self.chat.streaming)
        self.chat.poll(2.0, speaking=False)
        self.assertEqual(self.ai.confirmed, [], "a failed answer is never remembered")

    def test_interrupt_is_silent_and_late_messages_never_reach_the_next_question(self):
        self.chat.ask("first", 1.0, "en")
        self.ai.send("sentence", "Once upon a time.")
        self.chat.poll(1.1, speaking=True)
        self.assertEqual(self.chat.interrupt(), [StopSpeech()])
        self.assertEqual(self.ai.cancelled, [1])
        self.assertFalse(self.chat.streaming or self.chat.thinking)
        self.chat.ask("second", 2.0, "en")
        self.ai.send("sentence", "There was a robot.", request=1)
        self.ai.send("lost", request=1)
        self.ai.send("sentence", "The second answer.", request=2)
        self.ai.send("done", request=2)
        self.assertEqual(spoken(self.chat.poll(2.1, speaking=False)), ["The second answer."])
        self.chat.poll(2.2, speaking=False)
        self.assertEqual(self.ai.confirmed, [2])

    def test_new_session_rejects_old_answers_and_forgets_history(self):
        self.chat.ask("old", 1.0, "en")
        old_turn = self.chat.turn
        self.assertEqual(self.chat.new_session(), [StopSpeech()])
        self.assertEqual(self.ai.forgot, 1)
        self.chat.turn = old_turn  # even an answer with a reused request id is refused
        self.ai.send("sentence", "Old answer.", request=1)
        self.assertEqual(self.chat.poll(2.0, speaking=False), [])

    def answer(self, text, t, language="en", chat=None):
        """Ask, receive a whole answer and finish speaking it (follow-up opens at t + 0.1)."""
        chat = chat or self.chat
        chat.ask("question", t, language)
        for kind, value in (("text", text), ("sentence", text), ("done", None)):
            self.ai.send(kind, value)
        chat.poll(t + 0.1, speaking=False)

    def test_instructions_carry_date_time_profile_and_language(self):
        chat = Conversation(self.ai, ConversationSettings(Profile("Sam", "the kitchen")),
                            clock=lambda: datetime(2026, 10, 26, 23, 17))
        chat.ask("what time is it", 1.0, "sv")
        text = self.ai.instructions
        for part in ("Monday 26 October 2026, 23:17", "called Sam", "located in: the kitchen",
                     "Not connected yet: a camera", "ask one short question"):
            self.assertIn(part, text)
        self.assertTrue(text.endswith("Always answer in Swedish."))
        self.chat.ask("hi", 1.0, "en")
        self.assertNotIn("called", self.ai.instructions, "nothing that was not configured")

    def test_settings_from_env(self):
        self.assertEqual(settings_from_env({}), ConversationSettings())
        settings = settings_from_env({"LUNA_USER_NAME": "  Sam\n Lee ", "LUNA_MAX_TURNS": "5",
                                      "LUNA_IDLE_RESET_SECONDS": "0", "LUNA_LOCATION": "Home"})
        self.assertEqual(settings, ConversationSettings(Profile("Sam Lee", "Home"), 5, 0.0))
        broken = settings_from_env({"LUNA_MAX_TURNS": "lots", "LUNA_IDLE_RESET_SECONDS": "-1"})
        self.assertEqual((broken.max_turns, broken.idle_seconds), (20, 300.0))

    def test_answer_that_asks_back_gets_a_longer_follow_up(self):
        self.answer("Would you like to hear more?", 1.0)
        self.assertTrue(self.chat.in_followup(1.1 + QUESTION_FOLLOWUP_SECONDS - 0.1))
        self.answer("Mars is red.", 30.0)
        self.assertFalse(self.chat.in_followup(30.1 + FOLLOWUP_SECONDS))

    def test_never_mind_closes_the_follow_up_without_forgetting(self):
        self.answer("Would you like to hear more?", 1.0)
        effects = self.chat.hear("never mind", 3.0, "en", "en")
        self.assertEqual(spoken(effects), ["Okay."])
        self.assertEqual(self.chat.heard_kind, "control")
        self.assertFalse(self.chat.in_followup(3.1))
        self.assertEqual((self.ai.forgot, self.ai.asked[1:]), (0, []))
        self.assertEqual(self.chat.hear("never mind", 5.0, "en", "en"), [], "background talk")

    def test_new_conversation_forgets_and_listens_again(self):
        self.answer("Mars is red.", 1.0, "sv")
        effects = self.chat.hear("luna starta ett nytt samtal", 3.0, "sv", "sv")
        self.assertEqual(spoken(effects), ["Okej, vi börjar ett nytt samtal."])
        self.assertEqual((self.ai.forgot, self.chat.session.turns), (1, 0))
        self.chat.poll(4.0, speaking=False)
        self.assertTrue(self.chat.in_followup(4.1), "the next question needs no wake word")

    def test_idle_reset_only_after_real_quiet(self):
        self.answer("Mars is red.", 1.0)
        start = 1.1 + FOLLOWUP_SECONDS
        self.chat.poll(start + 299, speaking=False)
        self.chat.poll(start + 400, speaking=True)
        self.chat.poll(start + 701, speaking=False, listening=True)
        self.assertEqual(self.ai.forgot, 0, "never while speaking or listening")
        effects = self.chat.poll(start + 1002, speaking=False)
        self.assertEqual(self.ai.forgot, 1)
        self.assertEqual([e.message for e in effects],
                         ["Luna: new conversation after 301 s without talking"])
        self.chat.poll(start + 2000, speaking=False)
        self.assertEqual(self.ai.forgot, 1, "once")

    def test_turn_limit_starts_a_fresh_history(self):
        chat = Conversation(self.ai, ConversationSettings(max_turns=2))
        self.answer("One.", 1.0, chat=chat)
        self.answer("Two.", 2.0, chat=chat)
        self.assertEqual((self.ai.forgot, chat.session.turns), (0, 2))
        effects = chat.ask("third", 3.0, "en")
        self.assertEqual(self.ai.forgot, 1)
        self.assertEqual(effects[0], Log("Luna: new conversation after 2 answers"))
        self.assertEqual(chat.turn.session, chat.session.id)

    def test_stalled_request_with_the_real_worker(self):
        gate = threading.Event()
        brain = StreamBrain(["Stalled first sentence. ", "Never spoken."], gate=gate)
        worker = AIWorker(brain_factory=lambda: brain)
        chat = Conversation(worker)
        chat.ask("first", 1.0, "en")
        said = []
        for _ in range(200):
            said += spoken(chat.poll(1.1, speaking=True))
            if said:
                break
            threading.Event().wait(0.01)
        self.assertEqual(said, ["Stalled first sentence."])
        chat.interrupt()  # the stream is still stuck waiting for the network
        brain.pieces = ["Fresh answer."]
        self.assertEqual(chat.ask("second", 2.0, "en"), [Accepted("second")])
        said = []
        for _ in range(300):  # answered while the first one is still stuck
            said += spoken(chat.poll(2.1, speaking=False))
            if not chat.streaming:
                break
            threading.Event().wait(0.01)
        self.assertEqual(said, ["Fresh answer."])
        chat.poll(2.5, speaking=False)
        self.assertEqual(brain.previous_id, "resp-second")
        gate.set()  # the stuck answer finally ends: nothing of it is spoken or remembered
        for _ in range(100):
            if brain.closed == 2:
                break
            threading.Event().wait(0.01)
        self.assertEqual(brain.closed, 2)
        self.assertEqual(spoken(chat.poll(3.0, speaking=False)), [])
        self.assertEqual(brain.previous_id, "resp-second")


if __name__ == "__main__":
    unittest.main()
