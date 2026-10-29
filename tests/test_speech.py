import tempfile
import threading
import time
import unittest
from pathlib import Path

from luna_core import speech, voices
from luna_core.preferences import read_preferences, write_preferences
from luna_core.speech import SpeechEngine, VoiceLoadError, split_sentences


def wait_until(condition, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


class FakeSynth:
    def __init__(self, voice, natural, log, delay=0.0, fail=False):
        self.voice, self.natural, self.log = voice, natural, log
        self.delay, self.fail = delay, fail

    def synthesize(self, text, wav):
        time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("broken")
        self.log.append(("made", self.voice.key, self.natural, text, time.monotonic()))
        Path(wav).write_text(("natural|" if self.natural else "basic|") + text, encoding="utf-8")


class FakePlayer:
    def __init__(self, seconds=0.05):
        self.seconds = seconds
        self.played = []  # (text, start, end)
        self.files = []
        self.stops = 0
        self._stopped = threading.Event()
        self._text = None

    def start(self, wav):
        self.files.append(Path(wav))
        self._text = Path(wav).read_text(encoding="utf-8")
        self._stopped.clear()
        self._start = time.monotonic()

    def wait(self):
        self._stopped.wait(self.seconds)
        self.played.append((self._text, self._start, time.monotonic()))

    def stop(self):
        self.stops += 1
        self._stopped.set()


class Harness:
    """A SpeechEngine with fake voices and player."""

    def __init__(self, seconds=0.05, natural_delay=0.0, natural_fail=None, load_error=None,
                 load_gate=None):
        self.log = []
        self.player = FakePlayer(seconds)
        self.natural_fail = natural_fail or set()

        def make_natural(voice):
            if load_gate is not None:
                load_gate.wait(5)
            if load_error:
                raise VoiceLoadError(load_error)
            synth = FakeSynth(voice, True, self.log, natural_delay)
            synth.fail = voice.key in self.natural_fail
            return synth

        self.engine = SpeechEngine(make_natural=make_natural,
                                   make_basic=lambda v: FakeSynth(v, False, self.log),
                                   make_player=lambda: self.player)

    def texts(self):
        return [p[0] for p in self.player.played]

    def idle(self):
        return wait_until(lambda: not self.engine.is_talking())


class SentenceTests(unittest.TestCase):
    def test_split_joins_short_pieces_and_cuts_long_ones(self):
        self.assertEqual(split_sentences("Hello, my name is Luna. How can I help you today?"),
                         ["Hello, my name is Luna.", "How can I help you today?"])
        self.assertEqual(split_sentences("Hej. Jag heter Luna och jag pratar svenska."),
                         ["Hej. Jag heter Luna och jag pratar svenska."])
        long = "word " * 120
        parts = split_sentences(long)
        self.assertTrue(len(parts) > 1 and all(len(p) <= 250 for p in parts))
        self.assertEqual(" ".join(parts).split(), long.split())


class EngineTests(unittest.TestCase):
    def test_speaks_sentences_in_order_then_stops_talking(self):
        h = Harness()
        h.engine.speak_async("First sentence is here. Second sentence is here.")
        h.engine.speak_async("Another message to say.")
        self.assertTrue(h.engine.is_talking())
        self.assertTrue(h.idle())
        self.assertEqual(h.texts(), ["basic|First sentence is here.",
                                     "basic|Second sentence is here.",
                                     "basic|Another message to say."])
        self.assertTrue(h.engine.is_speaking(), "echo tail keeps the microphone muted")
        self.assertFalse(any(f.exists() for f in h.player.files), "WAV files are deleted")

    def test_next_sentence_is_prepared_while_one_plays(self):
        h = Harness(seconds=0.3)
        h.engine.speak_async("Sentence number one is long. Sentence number two is long.")
        self.assertTrue(h.idle())
        made_second = [e[4] for e in h.log if e[3].startswith("Sentence number two")][0]
        first_play = h.player.played[0]
        self.assertLess(made_second, first_play[2], "second sentence was ready before the first ended")
        gap = h.player.played[1][1] - first_play[2]
        self.assertLess(gap, 0.1)

    def test_stop_is_immediate_and_cancels_everything(self):
        h = Harness(seconds=5.0)
        h.engine.speak_async("This is the first long sentence. This is the second one. And a third.")
        h.engine.speak_async("A whole other message.")
        self.assertTrue(wait_until(lambda: h.player.files))
        started = time.monotonic()
        h.engine.stop()
        self.assertFalse(h.engine.is_talking())
        self.assertTrue(wait_until(lambda: h.player.played))
        self.assertLess(time.monotonic() - started, 0.5, "the playing sentence was cut off")
        time.sleep(0.3)
        self.assertEqual(h.texts(), ["basic|This is the first long sentence."])
        self.assertGreaterEqual(h.player.stops, 1)
        h.player.seconds = 0.01
        h.engine.speak_async("Speaking again after stop.")
        self.assertTrue(h.idle())
        self.assertEqual(h.texts()[-1], "basic|Speaking again after stop.")

    def test_natural_voice_is_used_after_it_loads(self):
        h = Harness()
        h.engine.set_voice("SV_MALE").join(2)
        self.assertEqual(h.engine.state, "natural")
        self.assertIn("Svenska man", h.engine.status)
        h.engine.speak_async("Hej, jag heter Luna.")
        self.assertTrue(h.idle())
        self.assertEqual(h.texts(), ["natural|Hej, jag heter Luna."])
        self.assertEqual(h.log[0][3], "Hej.", "the voice is warmed up while loading")

    def test_failed_load_keeps_basic_voice_with_a_reason(self):
        h = Harness(load_error="natural voice not installed (run robot-setup)")
        h.engine.set_voice("EN_MALE").join(2)
        self.assertEqual(h.engine.state, "basic")
        self.assertEqual(h.engine.status, "Basic voice: natural voice not installed (run robot-setup)")
        self.assertEqual(h.engine.voice.key, "EN_MALE", "basic voice of the chosen gender/language")
        h.engine.speak_async("Still talking.")
        self.assertTrue(h.idle())
        self.assertEqual(h.texts(), ["basic|Still talking."])

    def test_slow_load_does_not_block_speaking_and_switches_after(self):
        gate = threading.Event()
        h = Harness(load_gate=gate)
        loader = h.engine.set_voice("EN_FEMALE")
        self.assertEqual(h.engine.state, "loading")
        h.engine.speak_async("Talking while loading.")
        self.assertTrue(h.idle())
        self.assertEqual(h.texts(), ["basic|Talking while loading."])
        gate.set()
        loader.join(2)
        h.engine.speak_async("Now natural.")
        self.assertTrue(h.idle())
        self.assertEqual(h.texts()[-1], "natural|Now natural.")

    def test_newest_voice_choice_wins(self):
        gate = threading.Event()
        h = Harness(load_gate=gate)
        first = h.engine.set_voice("EN_MALE")
        second = h.engine.set_voice("SV_FEMALE")
        gate.set()
        first.join(2)
        second.join(2)
        self.assertEqual(h.engine.voice.key, "SV_FEMALE")

    def test_natural_failure_while_speaking_falls_back(self):
        h = Harness(natural_fail={"EN_FEMALE"})
        h.engine._synth = FakeSynth(voices.get_voice("EN_FEMALE"), True, h.log, fail=True)
        h.engine.speak_async("Please say this anyway.")
        self.assertTrue(h.idle())
        self.assertEqual(h.texts(), ["basic|Please say this anyway."])
        self.assertEqual(h.engine.state, "basic")
        self.assertIn("natural voice failed", h.engine.status)

    def test_disabled_module_does_not_speak_or_load(self):
        old = speech.enabled
        try:
            speech.enabled = False
            self.assertIsNone(speech.set_voice("EN_MALE"))
            speech.speak_async("hello")
            self.assertFalse(speech.is_talking())
        finally:
            speech.enabled = old


class VoiceCatalogueTests(unittest.TestCase):
    def test_catalogue_matches_installed_files(self):
        self.assertEqual({k: v.model for k, v in voices.VOICES.items()},
                         {"EN_FEMALE": "en_US-lessac-medium", "EN_MALE": "en_US-ryan-high",
                          "SV_FEMALE": "sv_SE-alma-medium", "SV_MALE": "sv_SE-nst-medium"})
        self.assertEqual(voices.get_voice("nonsense").key, voices.DEFAULT_VOICE)
        self.assertEqual(voices.get_voice("sv_male").key, "SV_MALE")
        self.assertEqual(voices.models_dir().parts[-2:], (".ai-robot-models", "piper"))
        with tempfile.TemporaryDirectory() as folder:
            voice = voices.VOICES["SV_FEMALE"]
            self.assertFalse(voices.is_installed(voice, folder))
            Path(folder, "sv_SE-alma-medium.onnx").write_bytes(b"x")
            self.assertFalse(voices.is_installed(voice, folder))
            Path(folder, "sv_SE-alma-medium.onnx.json").write_text("{}")
            self.assertTrue(voices.is_installed(voice, folder))

    def test_missing_files_give_a_clear_load_error(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(VoiceLoadError, "not installed"):
                speech.PiperSynth(voices.VOICES["EN_MALE"], folder)

    def test_voice_preference_is_saved_and_validated(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "prefs.json"
            self.assertEqual(read_preferences(path)["voice"], "EN_FEMALE")
            prefs = read_preferences(path)
            prefs["voice"] = "SV_MALE"
            write_preferences(prefs, path)
            self.assertEqual(read_preferences(path)["voice"], "SV_MALE")
            path.write_text('{"voice": "KLINGON"}')
            self.assertEqual(read_preferences(path)["voice"], "EN_FEMALE")


if __name__ == "__main__":
    unittest.main()
