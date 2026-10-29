"""Swedish listening, commands and answers (follows the voice language)."""
import os
import queue
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from luna_core import languages, voice
from luna_core.commands import parse_command, reply_for, route, spoken, vocabulary
from luna_core.preferences import read_preferences
from test_settings import FakeSpeech


class SwedishCommandTests(unittest.TestCase):
    def test_commands(self):
        self.assertEqual(parse_command("titta åt vänster", "sv"), [("look", (-1.0, 0.0))])
        self.assertEqual(parse_command("byt till grön färg", "sv"), [("color", "GREEN")])
        self.assertEqual(parse_command("var glad", "sv"), [("expression", "happy")])
        self.assertEqual(parse_command("left", "sv"), [], "English words only in English")
        self.assertEqual(parse_command("vänster"), [])

    def test_wake_words_heard_by_the_swedish_model(self):
        # What the Swedish model heard when Piper said "Luna" / "Hej Luna ...".
        self.assertEqual(route("lugna", language="sv")[0], "wake")
        self.assertEqual(route("hej lund", language="sv")[0], "wake")
        self.assertEqual(route("hej lund vad är huvudstäderna i sverige", language="sv"),
                         ("ai", "vad är huvudstäderna i sverige"))
        self.assertEqual(route("lunch var glad", language="sv"),
                         ("command", [("expression", "happy")]))
        self.assertEqual(route("blinka", language="sv"), ("command", [("blink", None)]))
        self.assertEqual(route("helene titta åt höger", language="sv"),
                         ("command", [("look", (1.0, 0.0))]))
        self.assertEqual(route("vi åt mat och sedan gick vi hem", language="sv")[0], "ignore")
        self.assertEqual(route("lund")[0], "ignore", "not a wake word in English")

    def test_replies_and_fixed_sentences(self):
        self.assertEqual(reply_for([("look", (-1.0, 0.0))], "sv"), "Tittar åt vänster.")
        self.assertEqual(reply_for([("expression", "happy")], "sv"), "Okej, jag är glad.")
        self.assertEqual(reply_for([("color", "AMBER")], "sv"), "Färgen är nu bärnsten.")
        for kind, value in set(parse_command(" ".join(vocabulary("sv")), "sv")):
            self.assertTrue(reply_for([(kind, value)], "sv"), (kind, value))
        self.assertEqual(spoken("Yes?", "sv"), "Ja?")
        self.assertEqual(spoken("Yes?"), "Yes?")
        self.assertEqual(spoken("Mars is red.", "sv"), "Mars is red.")

    def test_every_ai_error_has_swedish(self):
        from luna_core import ai
        errors = [ImportError()] + [type(name, (Exception,), {})() for name in (
            "AuthenticationError", "APIConnectionError", "NotFoundError", "RateLimitError",
            "Other")]
        for error in errors:
            self.assertIn(ai.friendly_error(error), languages.get("sv").speech)


class SwedishAITests(unittest.TestCase):
    def test_brain_is_told_the_answer_language(self):
        from luna_core import ai
        brain = ai.LunaBrain.__new__(ai.LunaBrain)
        calls = []
        brain.model, brain.previous_id = "m", None
        brain.client = types.SimpleNamespace(responses=types.SimpleNamespace(
            create=lambda **args: calls.append(args) or types.SimpleNamespace(
                id="r1", output_text=" Hej! ")))
        self.assertEqual(brain.ask("Hej Luna", "sv"), "Hej!")
        self.assertTrue(calls[0]["instructions"].endswith("Always answer in Swedish."))
        brain.ask("Hello")
        self.assertTrue(calls[1]["instructions"].endswith("Always answer in English."))

    def test_worker_passes_the_language(self):
        from luna_core.ai import AIWorker
        asked = []

        class Brain:
            def ask(self, question, language="en", instructions=None):
                asked.append(language)
                return "Svar."

        worker = AIWorker(brain_factory=Brain)
        request = worker.ask("fråga", "sv")
        self.assertEqual(worker.answers.get(timeout=5), ("text", request, "Svar."))
        self.assertEqual(worker.answers.get(timeout=5), ("sentence", request, "Svar."))
        self.assertEqual(asked, ["sv"])


class FakeModel:
    loaded = []

    def __init__(self, path):
        FakeModel.loaded.append(Path(path).name)


class FakeRecognizer:
    def __init__(self, model, rate, grammar=None):
        self.grammar = grammar


class ListenerModelTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        patch = mock.patch.object(voice, "model_path",
                                  lambda language="en": self.home / voice.MODELS[language])
        patch.start()
        self.addCleanup(patch.stop)
        FakeModel.loaded = []

    def install(self, language):
        (self.home / voice.MODELS[language]).mkdir()

    def test_switches_model_with_the_language(self):
        self.install("en")
        self.install("sv")
        listener = voice.VoiceListener(language="sv")
        recognizer = listener._load(FakeModel, FakeRecognizer)
        self.assertEqual(listener.active_language, "sv")
        self.assertIn("vänster", recognizer.grammar)
        listener.set_language("en")
        listener._load(FakeModel, FakeRecognizer)
        self.assertEqual(FakeModel.loaded, [voice.MODELS["sv"], voice.MODELS["en"]])
        listener.set_language("en")
        listener._load(FakeModel, FakeRecognizer)
        self.assertEqual(len(FakeModel.loaded), 2, "same model is not loaded again")

    def test_keeps_english_when_the_swedish_model_is_missing(self):
        self.install("en")
        listener = voice.VoiceListener(language="sv")
        self.assertIsNotNone(listener._load(FakeModel, FakeRecognizer))
        self.assertEqual((listener.language, listener.active_language), ("sv", "en"))
        self.assertEqual(listener._loaded_for, "sv", "no endless reloading")

    def test_no_model_at_all(self):
        listener = voice.VoiceListener(language="sv")
        self.assertIsNone(listener._load(FakeModel, FakeRecognizer))
        self.assertEqual(listener.status, "voice model missing")


class FakeListener:
    def __init__(self):
        self.language, self.active_language, self.status = "en", "en", "listening"
        self.partial, self.level = "", 0.0
        self.texts = queue.Queue()

    def set_language(self, language):
        self.language = self.active_language = language

    def stop(self):
        pass


class SwedishAppTests(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        from luna_core import app
        self.app = app
        self.speech = FakeSpeech()
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
        self.hud.voice = FakeListener()

    def test_swedish_voice_switches_listening_and_replies(self):
        self.hud.settings_choice("voice_language", "sv")
        self.assertEqual(self.hud.voice.language, "sv")
        self.assertEqual(self.hud.talk_language, "sv")
        self.hud.handle_speech("titta åt vänster", 1.0)
        self.assertEqual(self.speech.spoken[-1], "Tittar åt vänster.")
        self.assertEqual((self.hud.target_x, self.hud.target_y), (-1.0, 0.0))
        self.hud.handle_speech("hej lund", 2.0)
        self.assertEqual(self.speech.spoken[-1], "Ja?")
        self.hud.handle_speech("hej lund vad är klockan", 3.0)
        self.assertEqual(self.speech.spoken[-1], "Min AI-hjärna är inte installerad än.")
        self.hud.settings_choice("voice_language", "en")
        self.hud.handle_speech("look left", 4.0)
        self.assertEqual(self.speech.spoken[-1], "Looking left.")

    def test_listening_status_line(self):
        status = self.app.listening_status
        listener = FakeListener()
        self.assertEqual(status(listener, "en"), ("natural", "Listening in English"))
        self.assertEqual(status(listener, "sv")[0], "basic", "Swedish model missing")
        self.assertIn("not installed", status(listener, "sv")[1])
        listener.status = "loading"
        self.assertEqual(status(listener, "sv")[0], "loading")
        self.assertEqual(status(None, "en"), ("basic", "Microphone off"))
        self.hud.page, self.hud.settings_section = "Settings", 2
        self.hud.frame(1.0)


if __name__ == "__main__":
    unittest.main()
