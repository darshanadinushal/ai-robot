"""Phase 4: streamed AI answers spoken sentence by sentence."""
import os
import queue
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

from robot_hud import ai
from robot_hud.ai import AIWorker, LunaBrain, StreamError
from robot_hud.preferences import read_preferences
from robot_hud.speech import SentenceStream
from test_settings import FakeSpeech


def feed_all(pieces):
    stream = SentenceStream()
    out = []
    for piece in pieces:
        out += stream.feed(piece)
    return out + stream.flush()


class SentenceStreamTests(unittest.TestCase):
    def test_sentences_once_and_in_order(self):
        text = ("Mars is the fourth planet from the Sun. It looks red because of rusty dust! "
                "Would you like to know more about its two moons?")
        pieces = [text[i:i + 3] for i in range(0, len(text), 3)]
        out = feed_all(pieces)
        self.assertEqual(out, ["Mars is the fourth planet from the Sun.",
                               "It looks red because of rusty dust!",
                               "Would you like to know more about its two moons?"])
        self.assertEqual(" ".join(out), text)

    def test_sentence_is_ready_before_the_answer_ends(self):
        stream = SentenceStream()
        self.assertEqual(stream.feed("The Moon is about 384,000 km away."), [])
        self.assertEqual(stream.feed(" It"), ["The Moon is about 384,000 km away."])

    def test_decimals_and_short_pieces(self):
        self.assertEqual(feed_all(["Pi is 3.", "14 roughly. ", "Yes. Ok. It is a constant."]),
                         ["Pi is 3.14 roughly. Yes.", "Ok. It is a constant."])

    def test_swedish_text(self):
        self.assertEqual(feed_all(["Hej! Jag heter Luna och jag ", "är en robot. Vad vill du veta?"]),
                         ["Hej! Jag heter Luna och jag är en robot.", "Vad vill du veta?"])

    def test_long_text_is_cut_and_limited(self):
        out = feed_all(["word " * 400])
        self.assertTrue(all(len(sentence) <= 250 for sentence in out))
        self.assertLessEqual(sum(len(sentence) + 1 for sentence in out), 701)

    def test_incomplete_last_sentence_is_flushed(self):
        stream = SentenceStream()
        self.assertEqual(stream.feed("This answer has no full stop"), [])
        self.assertEqual(stream.flush(), ["This answer has no full stop"])
        self.assertEqual(stream.flush(), [])


def event(kind, **values):
    return types.SimpleNamespace(type=kind, **values)


class FakeEvents:
    def __init__(self, events):
        self.events, self.closed = events, False

    def __iter__(self):
        return iter(self.events)

    def close(self):
        self.closed = True


class LunaBrainStreamTests(unittest.TestCase):
    def brain(self, events):
        brain = LunaBrain.__new__(LunaBrain)
        brain.model, brain.previous_id, brain.completed_id = "m", None, None
        self.calls, self.streams = [], []

        def create(**args):
            self.calls.append(args)
            self.streams.append(FakeEvents(list(events)))
            return self.streams[-1]
        brain.client = types.SimpleNamespace(responses=types.SimpleNamespace(create=create))
        return brain

    def test_yields_text_and_completes(self):
        brain = self.brain([event("response.created"),
                            event("response.output_text.delta", delta="Hej "),
                            event("response.output_text.delta", delta="där."),
                            event("response.completed", response=types.SimpleNamespace(id="r1"))])
        self.assertEqual(list(brain.stream("Hej", "sv")), ["Hej ", "där."])
        self.assertTrue(self.calls[0]["stream"])
        self.assertTrue(self.calls[0]["instructions"].endswith("Always answer in Swedish."))
        self.assertEqual(brain.completed_id, "r1")
        self.assertIsNone(brain.previous_id, "only remembered once spoken")
        self.assertTrue(self.streams[0].closed)
        brain.remember("r1")
        list(brain.stream("More?"))
        self.assertEqual(self.calls[1]["previous_response_id"], "r1")
        self.assertTrue(self.calls[1]["instructions"].endswith("Always answer in English."))

    def test_reasoning_effort_only_when_configured(self):
        brain = self.brain([event("response.completed", response=types.SimpleNamespace(id="r1"))])
        brain.reasoning = None
        list(brain.stream("q"))
        self.assertNotIn("reasoning", self.calls[0])
        brain.reasoning = ai.reasoning_effort({"LUNA_AI_REASONING": "Low"})
        list(brain.stream("q", "en", "Be brief."))
        self.assertEqual(self.calls[1]["reasoning"], {"effort": "low"})
        self.assertEqual(self.calls[1]["instructions"], "Be brief.")
        self.assertIsNone(ai.reasoning_effort({"LUNA_AI_REASONING": "turbo"}))

    def test_failed_or_unfinished_stream_raises(self):
        for events in ([event("response.output_text.delta", delta="Hi."), event("response.failed")],
                       [event("error")],
                       [event("response.output_text.delta", delta="Hi.")]):
            brain = self.brain(events)
            with self.assertRaises(StreamError):
                list(brain.stream("q"))
            self.assertIsNone(brain.completed_id)
            self.assertTrue(self.streams[0].closed)


class StreamBrain:
    """A fake brain whose answer arrives in pieces; can fail or wait part-way."""

    def __init__(self, pieces=(), fail_after=None, gate=None):
        self.pieces, self.fail_after, self.gate = list(pieces), fail_after, gate
        self.previous_id, self.completed_id = None, None
        self.languages, self.closed = [], 0

    def stream(self, question, language="en", instructions=None, result=None):
        self.languages.append(language)
        self.instructions = instructions
        self.completed_id = None
        try:
            for index, piece in enumerate(self.pieces):
                if self.gate is not None and index == 1:
                    self.gate.wait(5)
                if index == self.fail_after:
                    raise ConnectionError("dropped")
                yield piece
            self.completed_id = "resp-" + question
            if result is not None:
                result["id"] = self.completed_id
        finally:
            self.closed += 1

    def remember(self, response_id):
        self.previous_id = response_id

    def forget(self):
        self.previous_id = None


def collect(worker, until=("done", "error", "lost"), timeout=5):
    events = []
    while True:
        message = worker.answers.get(timeout=timeout)
        events.append(message)
        if message[0] in until:
            return events


def wait_idle(worker):
    for _ in range(200):
        if not worker.busy:
            return
        threading.Event().wait(0.01)


class StreamingWorkerTests(unittest.TestCase):
    def test_sentences_in_order_and_text_without_duplicates(self):
        pieces = ["Mars is the fourth ", "planet. It has two ", "small moons. Bye", " for now."]
        brain = StreamBrain(pieces)
        worker = AIWorker(brain_factory=lambda: brain)
        request = worker.ask("mars")
        events = collect(worker)
        self.assertTrue(all(rid == request for _, rid, _ in events))
        self.assertEqual("".join(v for k, _, v in events if k == "text"), "".join(pieces))
        self.assertEqual([v for k, _, v in events if k == "sentence"],
                         ["Mars is the fourth planet.", "It has two small moons.", "Bye for now."])
        self.assertEqual(events[-1], ("done", request, None))
        # The first sentence is sent before the rest of the answer has arrived.
        kinds = [k for k, _, _ in events]
        self.assertLess(kinds.index("sentence"), len(kinds) - 3)

    def test_decision_a_history_only_after_complete_answer_is_spoken(self):
        brain = StreamBrain(["One answer here."])
        worker = AIWorker(brain_factory=lambda: brain)
        request = worker.ask("q1")
        collect(worker)
        self.assertIsNone(brain.previous_id, "not before it was spoken")
        worker.confirm(request)
        self.assertEqual(brain.previous_id, "resp-q1")
        # Interrupted while still speaking: the answer is forgotten, the checkpoint stays.
        wait_idle(worker)
        request = worker.ask("q2")
        collect(worker)
        worker.cancel(request)
        worker.confirm(request)
        self.assertEqual(brain.previous_id, "resp-q1")

    def test_decision_a_failed_answer_is_not_remembered(self):
        brain = StreamBrain(["Here is half of an answer. ", "never"], fail_after=1)
        worker = AIWorker(brain_factory=lambda: brain)
        request = worker.ask("q")
        self.assertEqual(collect(worker)[-1], ("lost", request, None))
        worker.confirm(request)
        self.assertIsNone(brain.previous_id)

    def test_cancel_stops_the_stream_silently(self):
        gate = threading.Event()
        brain = StreamBrain(["First sentence is here. ", "Second sentence. ", "Third."], gate=gate)
        worker = AIWorker(brain_factory=lambda: brain)
        request = worker.ask("q")
        events = collect(worker, until=("sentence",))
        self.assertEqual(events[-1], ("sentence", request, "First sentence is here."))
        worker.cancel(request)
        self.assertFalse(worker.busy, "a new question can be asked straight away")
        gate.set()
        wait_idle(worker)
        threading.Event().wait(0.1)
        self.assertTrue(worker.answers.empty(), "nothing more, and no error, after a cancel")
        self.assertEqual(brain.closed, 1, "the stream was closed")
        worker.confirm(request)
        self.assertIsNone(brain.previous_id)
        request = worker.ask("next")
        self.assertEqual(collect(worker)[-1], ("done", request, None))

    def test_decision_b_error_before_and_after_speaking(self):
        brain = StreamBrain(["never"], fail_after=0)
        worker = AIWorker(brain_factory=lambda: brain)
        request = worker.ask("q")
        self.assertEqual(collect(worker), [("error", request, "Sorry, my AI brain had a problem.")])
        wait_idle(worker)
        brain.pieces, brain.fail_after = ["Hello there, my good friend. ", "Then"], 1
        request = worker.ask("q")
        kinds = [k for k, _, _ in collect(worker)]
        self.assertEqual(kinds, ["text", "sentence", "lost"])

    def test_text_without_full_sentence_before_failure_is_an_error(self):
        brain = StreamBrain(["Hello the", "re"], fail_after=1)
        worker = AIWorker(brain_factory=lambda: brain)
        worker.ask("q")
        self.assertEqual([k for k, _, _ in collect(worker)], ["text", "error"])

    def test_empty_answer(self):
        brain = StreamBrain([])
        worker = AIWorker(brain_factory=lambda: brain)
        request = worker.ask("q")
        self.assertEqual(collect(worker), [("say", request, "I am not sure what to say."),
                                           ("done", request, None)])

    def test_swedish_language_reaches_the_brain(self):
        brain = StreamBrain(["Hej! Jag är Luna."])
        worker = AIWorker(brain_factory=lambda: brain)
        worker.ask("vem är du", "sv")
        collect(worker)
        self.assertEqual(brain.languages, ["sv"])

    def test_busy_while_answering(self):
        gate = threading.Event()
        brain = StreamBrain(["A first sentence here. ", "More."], gate=gate)
        worker = AIWorker(brain_factory=lambda: brain)
        self.assertTrue(worker.ask("q"))
        self.assertFalse(worker.ask("again"))
        gate.set()
        collect(worker)


class FakeAI:
    def __init__(self):
        self.answers = queue.Queue()
        self.asked, self.cancelled, self.confirmed = [], [], []

    def ask(self, question, language="en", instructions=None):
        self.asked.append((question, language))
        return len(self.asked)

    def cancel(self, request):
        self.cancelled.append(request)

    def confirm(self, request):
        self.confirmed.append(request)

    def send(self, kind, value=None, request=None):
        self.answers.put((kind, request or len(self.asked), value))


class TalkingSpeech(FakeSpeech):
    def __init__(self):
        super().__init__()
        self.talking = False

    def is_talking(self):
        return self.talking

    def is_speaking(self):
        return self.talking


class StreamingAppTests(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        from robot_hud import app
        self.app = app
        self.speech = TalkingSpeech()
        folder = tempfile.mkdtemp()
        for patch in (mock.patch.object(app, "speech", self.speech),
                      mock.patch.object(app, "read_preferences",
                                        lambda: read_preferences(Path(folder) / "p.json")),
                      mock.patch.object(app, "write_preferences", lambda prefs: None),
                      mock.patch.dict(os.environ, {"LUNA_VOICE": ""})):
            patch.start()
            self.addCleanup(patch.stop)
        self.hud = app.RobotHUD(types.SimpleNamespace(
            fullscreen=False, windowed=True, no_voice=True, no_speech=False, no_ai=True))
        self.addCleanup(app.pygame.quit)
        self.ai = self.hud.ai = FakeAI()

    def test_decision_c_text_shows_progressively_and_sentences_are_spoken(self):
        self.hud.ask_ai("tell me about mars", 1.0)
        self.assertTrue(self.hud.conversation.thinking)
        self.assertTrue(self.hud.mic_muted(), "deaf while the answer streams")
        self.ai.send("text", "Mars is red")
        self.hud.poll_voice(1.1)
        self.assertFalse(self.hud.conversation.thinking)
        self.assertEqual(self.hud.conversation.answer, "Mars is red")
        self.hud.frame(1.1)
        self.ai.send("text", ". It has")
        self.ai.send("sentence", "Mars is red.")
        self.hud.poll_voice(1.2)
        self.assertEqual(self.hud.conversation.answer, "Mars is red. It has")
        self.assertEqual(self.speech.spoken, ["Mars is red."])
        self.ai.send("text", " two moons.")
        self.ai.send("sentence", "It has two moons.")
        self.ai.send("done")
        self.speech.talking = True
        self.hud.poll_voice(1.3)
        self.assertEqual(self.hud.conversation.answer, "Mars is red. It has two moons.")
        self.assertEqual(self.speech.spoken, ["Mars is red.", "It has two moons."])
        self.assertEqual(self.ai.confirmed, [], "not remembered while still talking")
        self.speech.talking = False
        self.hud.poll_voice(1.4)
        self.assertEqual(self.ai.confirmed, [1])
        self.assertGreater(self.hud.conversation.session.followup_until, 1.4, "follow-up window opens")
        self.assertFalse(self.hud.mic_muted())
        self.hud.frame(1.5)

    def test_tap_interrupts_without_error_or_history(self):
        self.hud.ask_ai("tell me a story", 1.0)
        self.ai.send("text", "Once upon a time. ")
        self.ai.send("sentence", "Once upon a time.")
        self.hud.poll_voice(1.1)
        self.speech.talking = True
        self.hud.handle_touch(640, 300, 1.2)
        self.assertEqual(self.ai.cancelled, [1])
        self.assertGreater(self.speech.stops, 0)
        self.assertFalse(self.hud.conversation.streaming)
        # Late messages from the interrupted answer are ignored.
        self.speech.talking = False
        self.ai.send("sentence", "There was a robot.", request=1)
        self.ai.send("lost", request=1)
        self.hud.poll_voice(1.3)
        self.assertEqual(self.speech.spoken, ["Once upon a time."])
        self.assertEqual(self.ai.confirmed, [])
        self.assertEqual(self.hud.conversation.session.followup_until, 0.0)

    def test_decision_b_apology_after_speaking(self):
        for language, apology in (("en", "Sorry, I lost my connection."),
                                  ("sv", "Förlåt, jag tappade anslutningen.")):
            self.speech.spoken.clear()
            self.hud.settings_choice("voice_language", language)
            self.hud.ask_ai("question", 1.0)
            self.ai.send("text", "First part. ")
            self.ai.send("sentence", "First part.")
            self.ai.send("lost")
            self.hud.poll_voice(1.1)
            self.assertEqual(self.speech.spoken, ["First part.", apology])
            self.assertEqual(self.hud.conversation.answer, "First part. " + apology)
            self.assertEqual(self.hud.mood, "sad")
            self.assertFalse(self.hud.conversation.followup_pending)
            self.assertNotIn(len(self.ai.asked), self.ai.confirmed)

    def test_error_before_speaking_uses_the_existing_message(self):
        self.hud.settings_choice("voice_language", "sv")
        self.hud.ask_ai("fråga", 1.0)
        self.ai.send("error", "I cannot reach the internet right now.")
        self.hud.poll_voice(1.1)
        self.assertEqual(self.speech.spoken, ["Jag når inte internet just nu."])
        self.assertEqual(self.ai.asked, [("fråga", "sv")])
        self.assertFalse(self.hud.conversation.followup_pending)


if __name__ == "__main__":
    unittest.main()
