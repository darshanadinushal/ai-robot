"""Phase 5.0A: the assistant core works and is testable without pygame or audio devices."""
import queue
import subprocess
import sys
import threading
import unittest
from pathlib import Path

from robot_hud.ai import AIWorker
from robot_hud.assistant_core.conversation import Conversation
from robot_hud.assistant_core.events import Accepted, Act, Log, Mood, Speak, StopSpeech
from robot_hud.assistant_core.session import FOLLOWUP_SECONDS
from test_streaming import StreamBrain

ROOT = Path(__file__).resolve().parents[1]


class FakeAI:
    def __init__(self):
        self.answers = queue.Queue()
        self.asked, self.cancelled, self.confirmed, self.forgot = [], [], [], 0
        self.busy = False
        self.next_id = 0

    def ask(self, question, language="en"):
        if self.busy:
            return False
        self.asked.append((question, language))
        self.next_id += 1
        return self.next_id

    def cancel(self, request):
        self.cancelled.append(request)

    def confirm(self, request):
        self.confirmed.append(request)

    def forget(self):
        self.forgot += 1

    def send(self, kind, value=None, request=None):
        self.answers.put((kind, request or self.next_id, value))


def spoken(effects):
    return [e.text for e in effects if isinstance(e, Speak)]


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.ai = FakeAI()
        self.chat = Conversation(self.ai)

    def test_core_does_not_import_pygame(self):
        code = ("import sys, robot_hud.assistant_core.conversation; "
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
        gate.set()
        said = []
        for _ in range(300):
            said += spoken(chat.poll(2.1, speaking=False))
            if not chat.streaming:
                break
            threading.Event().wait(0.01)
        self.assertEqual(said, ["Fresh answer."], "nothing from the stalled answer")
        chat.poll(2.5, speaking=False)
        self.assertEqual(brain.previous_id, "resp-second")


if __name__ == "__main__":
    unittest.main()
