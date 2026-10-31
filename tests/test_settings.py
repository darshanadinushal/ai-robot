import os
import re
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from luna_core import i18n
from luna_core.preferences import read_preferences
from luna_core.settings import SECTIONS, apply_choice
from luna_core.voices import sample_text, get_voice, voice_for


class NameEditorTests(unittest.TestCase):
    def test_typing_checking_and_confirming(self):
        from luna_core.settings import NameEditor
        editor = NameEditor("Luna")
        for _ in range(4):
            editor.back()
        for char in "nOVa1":
            editor.key(char)
        self.assertEqual(editor.text, "Nova")
        self.assertEqual(editor.save("Luna", lambda word: True), "restart")
        self.assertEqual(editor.save("Nova", lambda word: True), "close", "unchanged")
        editor.text = "Zyra"
        self.assertIsNone(editor.save("Luna", lambda word: False))
        self.assertEqual(editor.warning, ("unknown", "Zyra"))
        self.assertEqual(editor.save("Luna", lambda word: False), "restart", "Save again keeps it")
        editor.text = "Z"
        self.assertIsNone(editor.save("Luna", lambda word: None))
        self.assertEqual(editor.warning, ("letters", None))


class ChoiceTests(unittest.TestCase):
    def setUp(self):
        self.prefs = read_preferences(Path(tempfile.gettempdir()) / "no-such-luna-prefs.json")

    def test_display_choices(self):
        self.assertEqual(apply_choice(self.prefs, "color", "AMBER"), "color")
        self.assertIsNone(apply_choice(self.prefs, "color", "AMBER"), "no change, no save")
        self.assertIsNone(apply_choice(self.prefs, "color", "PINK"))
        self.assertEqual(apply_choice(self.prefs, "layout", "MINIMAL"), "layout")
        self.assertEqual(apply_choice(self.prefs, "mode", "WAVE"), "mode")
        self.assertEqual(apply_choice(self.prefs, "layout", "BARS"), "wave_layout")
        self.assertIsNone(apply_choice(self.prefs, "layout", "MINIMAL"), "not a wave layout")
        self.assertEqual(apply_choice(self.prefs, "animations"), "animations")
        self.assertFalse(self.prefs["animations"])
        self.assertEqual((self.prefs["color"], self.prefs["layout"], self.prefs["wave_layout"]),
                         ("AMBER", "MINIMAL", "BARS"))

    def test_app_language_does_not_change_the_voice(self):
        self.assertEqual(apply_choice(self.prefs, "language", "sv"), "language")
        self.assertEqual((self.prefs["language"], self.prefs["voice"]), ("sv", "EN_FEMALE"))
        self.assertIsNone(apply_choice(self.prefs, "language", "de"))

    def test_voice_language_keeps_gender_and_gender_keeps_language(self):
        self.assertEqual(self.prefs["voice"], "EN_FEMALE")
        self.assertEqual(apply_choice(self.prefs, "gender", "male"), "voice")
        self.assertEqual(self.prefs["voice"], "EN_MALE")
        self.assertEqual(apply_choice(self.prefs, "voice_language", "sv"), "voice")
        self.assertEqual(self.prefs["voice"], "SV_MALE")
        self.assertEqual(apply_choice(self.prefs, "gender", "female"), "voice")
        self.assertEqual(self.prefs["voice"], "SV_FEMALE")
        self.assertIsNone(apply_choice(self.prefs, "voice_language", "sv"))
        self.assertIsNone(apply_choice(self.prefs, "voice_language", "de"))
        self.assertEqual(self.prefs["language"], "en")
        self.assertIsNone(apply_choice(self.prefs, "nonsense", 1))

    def test_every_language_has_both_genders_and_a_sample(self):
        for language in ("en", "sv"):
            for gender in ("female", "male"):
                self.assertIsNotNone(voice_for(language, gender))
        self.assertIn("Hej", sample_text(get_voice("SV_MALE")))
        self.assertIn("Hello", sample_text(get_voice("EN_FEMALE")))


class FakeSpeech:
    """Stands in for luna_core.speech in the touch tests."""

    def __init__(self):
        self.enabled = True
        self.loaded, self.spoken, self.stops = [], [], 0
        self.state = "natural"

    def set_voice(self, key):
        self.loaded.append(key)

    def use_speaker(self, speaker):
        self.speaker = speaker

    def voice_state(self):
        return self.state, "status"

    def speak_async(self, text):
        self.spoken.append(text)

    def stop(self):
        self.stops += 1

    def is_talking(self):
        return bool(self.spoken)

    def is_speaking(self):
        return False


class SettingsTouchTests(unittest.TestCase):
    """Taps on the real Settings page, drawn on a headless screen."""

    def setUp(self):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        from luna_core import app
        self.app = app
        self.speech = FakeSpeech()
        folder = tempfile.mkdtemp()
        self.saved = []
        patches = [mock.patch.object(app, "speech", self.speech),
                   mock.patch.object(app, "read_preferences",
                                     lambda: read_preferences(Path(folder) / "prefs.json")),
                   mock.patch.object(app, "write_preferences",
                                     lambda prefs: self.saved.append(dict(prefs))),
                   mock.patch.dict(os.environ, {"LUNA_MEMORY_FILE": str(Path(folder) / "m.db"),
                                    "LUNA_TASKS_FILE": str(Path(folder) / "t.db")})]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        args = types.SimpleNamespace(fullscreen=False, windowed=True, no_voice=True,
                                     no_speech=False, no_ai=True)
        self.hud = app.RobotHUD(args)
        self.addCleanup(app.pygame.quit)
        self.hud.page = "Settings"

    def draw(self):
        self.hud.frame(1.0)

    def tap(self, action):
        self.draw()
        rect = [r for r, a in self.hud.settings_hits if a == action][0]
        self.hud.handle_touch(*rect.center, 1.0)

    def test_choose_swedish_male_and_preview(self):
        self.assertEqual(self.speech.loaded, ["EN_FEMALE"])
        self.tap(("section", 1))
        self.tap(("voice_language", "sv"))
        self.tap(("voice", "SV_MALE"))
        self.assertEqual(self.speech.loaded, ["EN_FEMALE", "SV_FEMALE", "SV_MALE"])
        self.assertEqual(self.saved[-1]["voice"], "SV_MALE", "saved automatically")
        self.speech.state = "loading"
        self.tap(("preview", None))
        self.draw()
        self.assertEqual(self.speech.spoken, [], "waits until the voice has loaded")
        self.assertEqual(self.hud.preview, "waiting")
        self.speech.state = "natural"
        self.draw()
        self.assertEqual(self.speech.spoken, [sample_text(get_voice("SV_MALE"))])
        self.assertEqual(self.hud.preview, "playing")

    def test_display_choices_still_work(self):
        self.tap(("color", "PURPLE"))
        self.tap(("mode", "WAVE"))
        self.tap(("layout", "CIRCLE"))
        self.tap(("animations", None))
        last = self.saved[-1]
        self.assertEqual((last["color"], last["mode"], last["wave_layout"], last["animations"]),
                         ("PURPLE", "WAVE", "CIRCLE", False))

    def test_only_the_selected_section_is_shown(self):
        def actions():
            self.draw()
            return {a[0] for _, a in self.hud.settings_hits} - {"section"}
        self.assertEqual(actions(), {"mode", "color", "layout", "language", "animations"})
        expected = {1: {"voice_language", "voice", "preview"},
                    2: {"online_listening", "voice_interrupt"}, 3: {"memory"},
                    SECTIONS.index("AI"): {"ai_daily_budget"},
                    SECTIONS.index("Plugins"): set(), SECTIONS.index("About"): {"name_edit"}}
        for index, kinds in expected.items():
            self.tap(("section", index))
            self.assertEqual(actions(), kinds, SECTIONS[index])


    def test_memory_section_lists_and_deletes(self):
        memory = self.hud.memory
        for text in ("You like jazz.", "Your cat is Misse.", "You live in Lund.", "You love tea.",
                     "You wake at seven."):
            memory.remember(text)
        self.tap(("section", 3))
        self.draw()
        forget = sorted(a[1] for _, a in self.hud.settings_hits if a[0] == "forget_memory")
        self.assertEqual(forget, [2, 3, 4, 5], "newest first, 4 per page")
        self.tap(("memory_page", 1))
        self.tap(("forget_memory", 1))
        self.assertEqual([f.id for f in memory.facts()], [2, 3, 4, 5])
        self.tap(("forget_all", None))
        self.assertEqual(len(memory.facts()), 4, "one tap is not enough")
        self.tap(("forget_all", None))
        self.assertEqual(memory.facts(), [])
        memory.set_profile("name", "Darshana")
        memory.set_profile("birthday", "05-12")
        self.draw()
        rows = [a for _, a in self.hud.settings_hits if a[0] in ("forget_profile", "forget_memory")]
        self.assertEqual(rows, [("forget_profile", "name"), ("forget_profile", "birthday")])
        self.assertIn("Your birthday: 12/5", [text for text, _ in self.hud.memory_view()[0]])
        self.tap(("forget_profile", "name"))
        self.assertEqual(memory.profile(), {"birthday": "05-12"})
        self.tap(("memory", None))
        self.assertEqual((self.saved[-1]["memory"], self.hud.conversation.memory), (False, None))
        self.tap(("memory", None))
        self.assertIs(self.hud.conversation.memory, memory)

    def test_daily_ai_budget(self):
        self.tap(("section", SECTIONS.index("AI")))
        self.tap(("ai_daily_budget", 1.0))
        self.assertEqual(self.saved[-1]["ai_daily_budget"], 1.0)
        self.assertEqual(self.hud.budget.daily_usd, 1.0)
        self.assertIs(self.hud.conversation.budget, self.hud.budget)

    def test_new_robot_name_on_the_keyboard_restarts(self):
        self.tap(("section", SECTIONS.index("About")))
        self.tap(("name_edit", None))
        for _ in range(4):
            self.tap(("name_back", None))
        for letter in "NOVA":
            self.tap(("name_key", letter))
        self.assertEqual(self.hud.name_editor.text, "Nova")
        self.tap(("name_save", None))
        self.assertEqual(self.saved[-1]["robot_name"], "Nova")
        self.assertTrue(self.hud.restart and not self.hud.running)

    def test_switching_app_language_to_swedish(self):
        self.tap(("language", "sv"))
        self.assertEqual(i18n.language, "sv")
        self.assertEqual(self.saved[-1]["language"], "sv")
        self.assertEqual(self.speech.loaded, ["EN_FEMALE"], "the voice is not changed")
        self.assertEqual(i18n.tr("Settings"), "Inställningar")
        for page in ("Home", "Chat", "Camera", "Settings"):
            self.hud.page = page
            self.draw()
        self.tap(("language", "en"))
        self.assertEqual(i18n.tr("Settings"), "Settings")


class TranslationTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("en")

    def test_every_screen_text_is_translated(self):
        core = Path(__file__).parents[1] / "luna_core"
        files = [core / "app.py"] + sorted((core / "ui").glob("*.py"))
        source = "\n".join(f.read_text(encoding="utf-8") for f in files)
        texts = set(re.findall(r"tr\('((?:[^'\\]|\\.)*)'", source))
        texts |= set(re.findall(r"(?:'|\()'([^']+)' if ", source))  # tr('A' if x else 'B')
        texts |= set(re.findall(r" else '([^']+)'\)", source))
        from luna_core import languages
        names = set(SECTIONS) | {pack.name for pack in languages.PACKS.values()}
        names |= {"Listening", "Chat", "Home", "Camera", "Settings", "FACE MODE", "WAVE MODE",
                                 "FEMALE", "MALE", "Wave layout", "Face layout", "Online listening",
                                 "Hear long questions more accurately",
                                 "What Luna remembers about you", "Apps that add to Luna",
                                 "Daily limit for paid AI use", "Daily budget", "Used today"}
        from luna_core.plugins import PERMISSIONS
        from luna_core.plugins.manager import STATUS_TEXT
        names |= {text for _, text in PERMISSIONS.values()} | set(STATUS_TEXT.values())
        from luna_core.preferences import COLOR_CHOICES, LAYOUT_CHOICES, WAVE_LAYOUTS
        from luna_core.voices import VOICES
        names |= set(COLOR_CHOICES) | set(LAYOUT_CHOICES) | set(WAVE_LAYOUTS)
        names |= {v.license_note for v in VOICES.values()}
        names |= {"LISTENING...", "SPEAKING...", "QUIET...", "THINKING...", "NATURAL",
                  "LOADING", "BASIC", "starting", "listening", "voice packages missing",
                  "voice model missing"}
        for code in languages.codes()[1:]:
            screen = i18n.translations(code)
            missing = sorted(t for t in texts | names if t not in screen and "{" not in t
                             and t not in ("Status", "AI: "))
            self.assertEqual(missing, [], "%s.json screen texts" % code)
        self.assertGreater(len(texts), 20, "the source scan found the tr() calls")

    def test_tr_and_set_language(self):
        i18n.set_language("sv")
        self.assertEqual(i18n.tr("Home"), "Hem")
        self.assertEqual(i18n.tr("Natural voice: {label}", label="Alma"), "Naturlig röst: Alma")
        self.assertEqual(i18n.tr("not translated"), "not translated")
        i18n.set_language("xx")
        self.assertEqual(i18n.language, "en")
        self.assertEqual(i18n.tr("Home"), "Home")


if __name__ == "__main__":
    unittest.main()
