"""Every language pack and voice is complete, so new languages and voices need no code changes."""
import json
import re
import tempfile
import unittest
from pathlib import Path

from robot_hud import ai, commands, languages, voices
from robot_hud.preferences import read_preferences
from robot_hud.settings import apply_choice

FIXED_SENTENCES = {"Yes?", "My AI brain is not set up yet.", "I am not sure what to say.",
                   ai.LOST_CONNECTION, "Okay.", "Okay, let's start a new conversation.",
                   "One moment."}


def ai_errors():
    errors = [ImportError()] + [type(name, (Exception,), {})() for name in (
        "AuthenticationError", "APIConnectionError", "NotFoundError", "RateLimitError", "Other")]
    return {ai.friendly_error(error) for error in errors}


class LanguagePackTests(unittest.TestCase):
    def test_english_is_first_and_codes_match_files(self):
        self.assertEqual(languages.codes()[0], "en")
        for code, pack in languages.PACKS.items():
            self.assertEqual(pack.code, code)
            self.assertTrue(pack.name and pack.native_name, code)

    def test_every_pack_is_complete(self):
        for code, pack in languages.PACKS.items():
            with self.subTest(language=code):
                self.assertTrue(pack.listening_model, "listening_model")
                self.assertTrue(pack.wake_words, "wake_words")
                self.assertTrue(pack.greetings, "greetings")
                self.assertTrue(pack.sample and pack.warm_up and pack.ai_instruction)
                self.assertTrue(pack.windows_culture, "windows_culture")
                unknown = set(pack.commands.values()) - set(commands.ACTIONS)
                self.assertEqual(unknown, set(), "commands use unknown action IDs")
                self.assertEqual(set(pack.commands.values()), set(commands.ACTIONS),
                                 "a spoken word for every action")
                raw = json.loads((languages.FOLDER / (code + ".json")).read_text("utf-8"))
                self.assertEqual(set(raw.get("replies", {})), set(commands.ACTIONS),
                                 "a reply for every action")
                self.assertEqual(set(pack.controls), set(commands.CONTROLS), "controls")
                for kind in ("remember", "forget", "private", "ignore"):
                    self.assertTrue(pack.memory_words.get(kind), "memory_words: " + kind)
                for name, phrases in pack.controls.items():
                    self.assertTrue(phrases, name)
                    for phrase in phrases:
                        self.assertEqual(commands.route("luna " + phrase, language=code),
                                         ("control", name), phrase)
                if code != languages.DEFAULT:
                    missing = (FIXED_SENTENCES | ai_errors()) - set(pack.speech)
                    self.assertEqual(missing, set(), "speech: Luna's fixed sentences")

    def test_wake_words_are_not_command_words(self):
        for code, pack in languages.PACKS.items():
            self.assertEqual(set(pack.wake_words) & set(pack.commands), set(), code)
            self.assertEqual(commands.route("%s" % pack.wake_words[0], language=code)[0], "wake")

    def test_unknown_language_is_english(self):
        self.assertIs(languages.get("xx"), languages.get("en"))
        self.assertEqual(commands.spoken("Yes?", "xx"), "Yes?")


class PartialPackTests(unittest.TestCase):
    def write(self, folder, code, data):
        Path(folder, code + ".json").write_text(json.dumps(data), encoding="utf-8")

    def test_a_new_language_can_start_with_only_names(self):
        with tempfile.TemporaryDirectory() as folder:
            english = (languages.FOLDER / "en.json").read_text(encoding="utf-8")
            Path(folder, "en.json").write_text(english, encoding="utf-8")
            self.write(folder, "de", {"code": "de", "name": "German", "native_name": "Deutsch",
                                      "screen": {"Home": "Start"}})
            packs = languages.load(folder)
        self.assertEqual(list(packs), ["en", "de"])
        german = packs["de"]
        self.assertEqual(german.screen["Home"], "Start")
        self.assertEqual(german.listening_model, "", "listens in English instead")
        self.assertEqual(german.commands, {})
        self.assertEqual(german.reply("blink"), "Done.", "English reply")
        self.assertEqual(german.say("Yes?"), "Yes?")
        self.assertEqual(german.ai_instruction, "Always answer in German.")
        self.assertEqual(german.sample, packs["en"].sample)

    def test_broken_packs_are_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            self.write(folder, "de", {"code": "de", "name": "German", "native_name": "Deutsch"})
            with self.assertRaisesRegex(ValueError, "English"):
                languages.load(folder)
            self.write(folder, "en", {"code": "xx", "name": "English", "native_name": "English"})
            with self.assertRaisesRegex(ValueError, "code must be 'en'"):
                languages.load(folder)


class VoiceCatalogueTests(unittest.TestCase):
    def test_every_voice_is_complete(self):
        self.assertIn(voices.DEFAULT_VOICE, voices.VOICES)
        self.assertTrue(voices.DOWNLOAD_BASE.startswith("https://"))
        models = [v.model for v in voices.VOICES.values()]
        self.assertEqual(len(models), len(set(models)))
        for voice in voices.VOICES.values():
            with self.subTest(voice=voice.key):
                self.assertIn(voice.language, languages.PACKS, "needs a language pack")
                self.assertTrue(voice.label and voice.espeak and voice.gender and voice.name)
                names = {f.name for f in voice.files}
                self.assertEqual(names, {voice.model + ".onnx", voice.model + ".onnx.json"})
                for file in voice.files:
                    self.assertRegex(file.sha256, r"^[0-9a-f]{64}$")
                    self.assertGreater(file.size, 0)
                    self.assertTrue(file.source.endswith("/" + file.name))

    def test_voices_for_a_language(self):
        for code in voices.LANGUAGES:
            self.assertTrue(voices.voices_for(code))
            self.assertEqual(voices.genders_for(code), ("female", "male"))
        self.assertEqual(voices.voices_for("xx"), [])


class VoiceChoiceTests(unittest.TestCase):
    def setUp(self):
        self.prefs = read_preferences(Path(tempfile.gettempdir()) / "no-such-luna-prefs.json")

    def test_choose_a_voice_by_key(self):
        self.assertEqual(apply_choice(self.prefs, "voice", "SV_FEMALE"), "voice")
        self.assertEqual(self.prefs["voice"], "SV_FEMALE")
        self.assertIsNone(apply_choice(self.prefs, "voice", "NOPE"))

    def test_language_without_the_current_type_takes_its_first_voice(self):
        catalogue = dict(voices.VOICES)
        extra = voices.Voice("XX_ROBOT", "sv", "robot", "Robot", "robot", "sv", "", "Robo")
        try:
            voices.VOICES.clear()
            voices.VOICES.update({"EN_FEMALE": catalogue["EN_FEMALE"], "XX_ROBOT": extra})
            self.assertEqual(apply_choice(self.prefs, "voice_language", "sv"), "voice")
            self.assertEqual(self.prefs["voice"], "XX_ROBOT")
        finally:
            voices.VOICES.clear()
            voices.VOICES.update(catalogue)


if __name__ == "__main__":
    unittest.main()
