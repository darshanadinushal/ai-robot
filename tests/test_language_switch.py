"""Phase 4.1: "Luna, can you speak Swedish?" switches the voice language by voice."""
import json
import os
import queue
import struct
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from robot_hud import commands, languages, voice
from robot_hud.commands import CANNOT_SWITCH, language_switch, vocabulary
from robot_hud.preferences import read_preferences, write_preferences
from test_settings import FakeSpeech
from test_streaming import FakeAI


class MatcherTests(unittest.TestCase):
    def test_english_requests(self):
        for text, target in (("luna can you speak english", "en"),
                             ("luna can you speak swedish", "sv"),
                             ("luna switch to english", "en"),
                             ("hey luna switch to swedish please", "sv"),
                             ("okay luna could you talk in swedish now", "sv"),
                             ("lunar speak english", "en")):
            self.assertEqual(language_switch(text, "en"), target, text)

    def test_swedish_requests(self):
        for text, target in (("hej luna kan du prata engelska", "en"),
                             ("hej luna kan du prata svenska", "sv"),
                             ("hej lund byt till engelska", "en"),
                             ("hej lugn byt till svenska", "sv"),
                             ("luna kan du tala på engelska tack", "en")):
            self.assertEqual(language_switch(text, "sv"), target, text)

    def test_no_false_positives(self):
        for text, language in (("can you speak swedish", "en"),           # no wake word
                               ("luna don't speak swedish", "en"),
                               ("luna how do you say hello in swedish", "en"),
                               ("luna what does speak english mean", "en"),
                               ("luna speak", "en"),
                               ("luna swedish meatballs", "en"),
                               ("luna is english hard to speak", "en"),
                               ("hej luna vad betyder prata engelska", "sv"),
                               ("min kompis kan prata engelska", "sv"),
                               ("luna speak english", "sv")):              # wrong language
            self.assertIsNone(language_switch(text, language), text)

    def test_follow_up_needs_no_wake_word(self):
        self.assertEqual(language_switch("can you speak swedish", "en", conversation=True), "sv")

    def test_switch_words_are_in_the_command_vocabulary(self):
        self.assertTrue({"english", "swedish", "switch"} <= set(vocabulary("en")))
        self.assertTrue({"engelska", "svenska", "byt"} <= set(vocabulary("sv")))

    def test_every_pack_can_switch_to_every_language(self):
        for code, pack in languages.PACKS.items():
            with self.subTest(language=code):
                self.assertEqual(set(pack.switch_phrases), set(languages.PACKS))
                self.assertTrue(pack.switch_confirmation)
                for target, phrases in pack.switch_phrases.items():
                    for phrase in phrases:
                        self.assertEqual(language_switch("luna " + phrase, code), target)
        self.assertEqual(languages.get("en").switch_confirmation,
                         "Of course! I'll speak English now.")
        self.assertEqual(languages.get("sv").switch_confirmation,
                         "Självklart! Nu pratar jag svenska.")
        self.assertIn(CANNOT_SWITCH, languages.get("sv").speech)


# ------------------------------------------------------------------ listener (fake Vosk)

LOUD = struct.pack("<4000h", *([3000, -3000] * 2000))
SILENT = bytes(8000)


class ScriptedRecognizer:
    """A fake KaldiRecognizer: `finals` and `partials` map the block number to text."""

    def __init__(self, finals=None, partials=None, rest=""):
        self.finals, self.partials, self.rest = finals or {}, partials or {}, rest
        self.blocks = self.resets = self.finished = 0
        self._last = ""

    def AcceptWaveform(self, data):
        self.blocks += 1
        if self.blocks in self.finals:
            self._last = self.finals[self.blocks]
            return True
        return False

    def Result(self):
        return json.dumps({"text": self._last})

    def PartialResult(self):
        return json.dumps({"partial": self.partials.get(self.blocks, "")})

    def FinalResult(self):
        self.finished += 1
        text, self.rest = self.rest, ""
        return json.dumps({"text": text})

    def Reset(self):
        self.resets += 1


class SwitchEarListenerTests(unittest.TestCase):
    def listener(self, main, ear, language="sv", muted=False):
        listener = voice.VoiceListener(language=language, muted=lambda: muted, free_form=True)
        listener._recognizer = main
        listener._ears = [voice.SwitchEar("en" if language == "sv" else "sv", ear)]
        return listener

    def feed(self, listener, blocks, start=0.0):
        for index, data in enumerate(blocks):
            listener._hear(data, start + index * 0.25)

    def drained(self, q):
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        return items

    def test_english_request_while_listening_in_swedish(self):
        main = ScriptedRecognizer(finals={3: "lugn kan ju spika england"})
        ear = ScriptedRecognizer(finals={4: "luna can you speak english"},
                                 partials={1: "luna", 2: "luna can", 3: "luna can you"})
        listener = self.listener(main, ear)
        self.feed(listener, [LOUD] * 4)
        self.assertEqual(self.drained(listener.switches),
                         [("en", "en", "luna can you speak english")])
        self.assertEqual(self.drained(listener.texts), [], "the Swedish mishearing is dropped")

    def test_swedish_request_while_listening_in_english(self):
        main = ScriptedRecognizer(finals={4: "hey loony beat ill spending"})
        ear = ScriptedRecognizer(finals={2: "hej luna byt till svenska"}, partials={1: "hej luna"})
        listener = self.listener(main, ear, language="en")
        self.feed(listener, [LOUD] * 4)
        self.assertEqual(self.drained(listener.switches),
                         [("sv", "sv", "hej luna byt till svenska")])
        self.assertEqual(self.drained(listener.texts), [], "main result of that sentence dropped")

    def test_other_speech_is_passed_on_unchanged(self):
        main = ScriptedRecognizer(finals={3: "hej lund vad är klockan"})
        ear = ScriptedRecognizer(finals={4: "luna [unk] [unk]"}, partials={1: "luna", 2: "luna"})
        listener = self.listener(main, ear)
        self.feed(listener, [LOUD] * 4)
        self.assertEqual(self.drained(listener.switches), [])
        self.assertEqual(self.drained(listener.texts), ["hej lund vad är klockan"])

    def test_restricted_guess_without_wake_word_is_ignored(self):
        main = ScriptedRecognizer(finals={2: "jag vill ha kaffe"})
        ear = ScriptedRecognizer(finals={2: "can you speak english"})
        listener = self.listener(main, ear)
        self.feed(listener, [LOUD] * 2)
        self.assertEqual(self.drained(listener.switches), [])
        self.assertEqual(self.drained(listener.texts), ["jag vill ha kaffe"])

    def test_ears_rest_in_silence_and_finish_after_speech(self):
        main = ScriptedRecognizer()
        ear = ScriptedRecognizer(rest="luna speak english")
        listener = self.listener(main, ear)
        self.feed(listener, [SILENT] * 8)
        self.assertEqual(ear.blocks, 0, "no extra recognition work in silence")
        self.feed(listener, [LOUD] * 2 + [SILENT] * 8, start=10.0)
        self.assertEqual(ear.blocks, 2 + 3, "speech plus about one second")
        self.assertEqual(ear.finished, 1)
        self.assertEqual(self.drained(listener.switches), [("en", "en", "luna speak english")])
        self.assertEqual(main.blocks, 18, "the main recogniser hears everything")

    def test_nothing_is_heard_while_luna_talks(self):
        main = ScriptedRecognizer(finals={1: "luna speak english"})
        ear = ScriptedRecognizer(finals={1: "luna speak english"})
        listener = self.listener(main, ear, muted=True)
        self.feed(listener, [LOUD] * 3)
        self.assertEqual((main.blocks, ear.blocks), (0, 0))
        self.assertEqual(self.drained(listener.switches) + self.drained(listener.texts), [])

    def test_held_sentence_is_released_if_the_ear_never_decides(self):
        main = ScriptedRecognizer(finals={1: "lugn hej"})
        ear = ScriptedRecognizer(partials={n: "luna" for n in range(1, 20)})
        listener = self.listener(main, ear)
        self.feed(listener, [LOUD] * 3)
        self.assertEqual(self.drained(listener.texts), [])
        self.feed(listener, [LOUD] * 5, start=1.0)
        self.assertEqual(self.drained(listener.texts), ["lugn hej"])


class FakeModel:
    loaded = []

    def __init__(self, path):
        FakeModel.loaded.append(Path(path).name)


class FakeRecognizer:
    def __init__(self, model, rate, grammar=None):
        self.grammar = grammar


class EarLoadingTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        patch = mock.patch.object(voice, "model_path",
                                  lambda language="en": self.home / voice.MODELS[language])
        patch.start()
        self.addCleanup(patch.stop)
        FakeModel.loaded = []

    def install(self, *codes):
        for code in codes:
            (self.home / voice.MODELS[code]).mkdir()

    def test_one_ear_for_the_other_language_shares_its_model(self):
        self.install("en", "sv")
        listener = voice.VoiceListener(language="sv", free_form=True)
        listener._load(FakeModel, FakeRecognizer)
        ears = listener._make_ears(FakeModel, FakeRecognizer)
        self.assertEqual([ear.language for ear in ears], ["en"])
        grammar = json.loads(ears[0].recognizer.grammar)
        self.assertTrue({"luna", "speak", "english", "switch", "[unk]"} <= set(grammar))
        self.assertNotIn("happy", grammar, "only switch words: small and cheap")
        listener.set_language("en")
        listener._load(FakeModel, FakeRecognizer)
        self.assertEqual(FakeModel.loaded, [voice.MODELS["sv"], voice.MODELS["en"]],
                         "switching reuses the ear's model")

    def test_no_ear_without_the_model_or_when_turned_off(self):
        self.install("sv")
        listener = voice.VoiceListener(language="sv")
        listener._load(FakeModel, FakeRecognizer)
        self.assertEqual(listener._make_ears(FakeModel, FakeRecognizer), [])
        self.install("en")
        with mock.patch.object(voice, "SWITCH_EARS", 0):
            self.assertEqual(listener._make_ears(FakeModel, FakeRecognizer), [])
        self.assertEqual(list(listener._models), ["sv"], "unused models are freed")

    def test_large_models_get_no_ear(self):
        self.install("en", "sv")
        (self.home / voice.MODELS["sv"] / "HCLG.fst").write_bytes(bytes(2 * 2**20))
        english = voice.VoiceListener(language="en")
        english._load(FakeModel, FakeRecognizer)
        with mock.patch.object(voice, "SWITCH_EAR_MB", 1):
            self.assertEqual(english.ear_languages(), [], "no Swedish ear: too much RAM")
            swedish = voice.VoiceListener(language="sv")
            swedish._load(FakeModel, FakeRecognizer)
            self.assertEqual(swedish.ear_languages(), ["en"], "the small English model is fine")

    def test_listening_installed(self):
        self.assertFalse(voice.listening_installed("sv"))
        self.install("sv")
        self.assertTrue(voice.listening_installed("sv"))


# ------------------------------------------------------------------ app

class SwitchListener:
    def __init__(self):
        self.language = self.active_language = "en"
        self.status, self.partial, self.level = "listening", "", 0.0
        self.texts, self.switches = queue.Queue(), queue.Queue()

    def set_language(self, language):
        self.language = self.active_language = language

    def stop(self):
        pass


class LoadingSpeech(FakeSpeech):
    def set_voice(self, key):
        super().set_voice(key)
        self.state = "loading"

    def is_talking(self):
        return False


class VoiceSwitchAppTests(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        from robot_hud import app
        self.app = app
        self.speech = LoadingSpeech()
        self.prefs_file = Path(tempfile.mkdtemp()) / "prefs.json"
        self.installed = {"en", "sv"}
        for patch in (mock.patch.object(app, "speech", self.speech),
                      mock.patch.object(app, "read_preferences",
                                        lambda: read_preferences(self.prefs_file)),
                      mock.patch.object(app, "write_preferences",
                                        lambda prefs: write_preferences(prefs, self.prefs_file)),
                      mock.patch.object(app, "listening_installed",
                                        lambda code: code in self.installed),
                      mock.patch.dict(os.environ, {"LUNA_VOICE": ""})):
            patch.start()
            self.addCleanup(patch.stop)
        self.hud = app.RobotHUD(types.SimpleNamespace(
            fullscreen=False, windowed=True, no_voice=True, no_speech=False, no_ai=True))
        self.addCleanup(app.pygame.quit)
        self.hud.voice = SwitchListener()
        self.ai = self.hud.ai = FakeAI()
        self.speech.state = "natural"  # the startup voice has loaded

    def loaded(self):
        self.speech.state = "natural"
        self.hud.frame(5.0)

    def test_english_to_swedish_keeps_type_and_screen_language(self):
        self.hud.settings_choice("gender", "male")
        self.speech.spoken.clear()
        self.hud.handle_speech("luna can you speak swedish", 1.0)
        self.assertEqual(self.hud.prefs["voice"], "SV_MALE")
        self.assertEqual(self.hud.prefs["language"], "en", "screen language unchanged")
        self.assertEqual((self.hud.talk_language, self.hud.voice.language), ("sv", "sv"))
        self.assertEqual(self.speech.loaded[-1], "SV_MALE")
        self.assertEqual(read_preferences(self.prefs_file)["voice"], "SV_MALE", "saved")
        self.assertEqual(self.ai.asked, [], "not sent to the AI")
        self.assertEqual(self.speech.spoken, [], "waits for the Swedish voice")
        self.hud.frame(1.1)
        self.assertEqual(self.speech.spoken, [])
        self.assertTrue(self.hud.mic_muted(), "deaf until the confirmation was said")
        self.loaded()
        self.assertEqual(self.speech.spoken, ["Självklart! Nu pratar jag svenska."])
        self.hud.frame(5.1)
        self.assertEqual(len(self.speech.spoken), 1, "said once")
        self.assertFalse(self.hud.mic_muted())
        self.hud.handle_speech("hej luna vad är klockan", 6.0)
        self.assertEqual(self.ai.asked[-1], ("vad är klockan", "sv"), "AI answers in Swedish")

    def test_swedish_to_english(self):
        self.hud.settings_choice("voice_language", "sv")
        self.loaded()
        self.speech.spoken.clear()
        self.hud.handle_speech("hej lund byt till engelska", 6.0)
        self.assertEqual(self.hud.prefs["voice"], "EN_FEMALE")
        self.assertEqual(self.hud.voice.language, "en")
        self.loaded()
        self.assertEqual(self.speech.spoken, ["Of course! I'll speak English now."])

    def test_request_heard_by_the_other_language_ear(self):
        self.hud.settings_choice("voice_language", "sv")
        self.loaded()
        self.speech.spoken.clear()
        self.hud.voice.switches.put(("en", "en", "luna can you speak english"))
        self.hud.poll_voice(6.0)
        self.assertEqual(self.hud.talk_language, "en")
        self.assertEqual(self.hud.heard, "luna can you speak english")
        self.loaded()
        self.assertEqual(self.speech.spoken, ["Of course! I'll speak English now."])

    def test_missing_model_keeps_the_language(self):
        self.installed = {"en"}
        self.hud.handle_speech("luna speak swedish", 1.0)
        self.assertEqual((self.hud.talk_language, self.hud.voice.language), ("en", "en"))
        self.assertEqual(self.speech.spoken, ["Sorry, I cannot understand Swedish yet."])
        self.assertFalse(os.path.exists(self.prefs_file) and
                         read_preferences(self.prefs_file)["voice"] != "EN_FEMALE")

    def test_missing_model_message_in_swedish(self):
        self.hud.settings_choice("voice_language", "sv")
        self.loaded()
        self.speech.spoken.clear()
        self.installed = {"sv"}
        self.hud.handle_speech("hej luna prata engelska", 6.0)
        self.assertEqual(self.hud.talk_language, "sv")
        self.assertEqual(self.speech.spoken, ["Förlåt, jag kan inte förstå engelska än."])

    def test_same_language_just_confirms(self):
        self.hud.handle_speech("luna speak english", 1.0)
        self.assertEqual(self.speech.loaded, ["EN_FEMALE"], "voice not reloaded")
        self.hud.frame(1.1)
        self.assertEqual(self.speech.spoken, ["Of course! I'll speak English now."])

    def test_interrupts_a_streaming_answer_silently(self):
        self.hud.ask_ai("tell me a long story", 1.0)
        self.ai.send("text", "Once upon a time. ")
        self.ai.send("sentence", "Once upon a time.")
        self.hud.poll_voice(1.1)
        stops = self.speech.stops
        self.hud.voice.switches.put(("sv", "sv", "hej luna prata svenska"))
        self.ai.send("sentence", "There was a robot.")
        self.ai.send("lost")
        self.hud.poll_voice(1.2)
        self.assertEqual(self.ai.cancelled, [1])
        self.assertGreater(self.speech.stops, stops)
        self.assertFalse(self.hud.answer_streaming)
        self.assertEqual(self.ai.confirmed, [], "interrupted answer not remembered")
        self.loaded()
        self.assertEqual(self.speech.spoken, ["Once upon a time.",
                                              "Självklart! Nu pratar jag svenska."])

    def test_ordinary_sentences_still_reach_commands_and_ai(self):
        self.hud.handle_speech("luna how do you say hello in swedish", 1.0)
        self.assertEqual(self.ai.asked, [("how do you say hello in swedish", "en")])
        self.assertEqual(self.hud.talk_language, "en")
        self.hud.handle_speech("speak", 2.0)
        self.assertEqual(self.hud.wave_state, "speaking", "the speak command still works")

    def test_tap_cancels_a_pending_confirmation(self):
        self.hud.handle_speech("luna speak swedish", 1.0)
        self.hud.handle_touch(640, 300, 1.1)
        self.loaded()
        self.assertEqual(self.speech.spoken, [])
        self.assertEqual(self.hud.talk_language, "sv", "the switch itself stays")


if __name__ == "__main__":
    unittest.main()
