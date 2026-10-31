"""App pages (ui.pages) and the face state (face_state) split out of app.py."""
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from luna_core import Robot
from luna_core.face_state import FaceState
from luna_core.preferences import read_preferences
from test_settings import FakeSpeech


class FaceStateTests(unittest.TestCase):
    def test_actions_change_the_face_and_report_preference_changes(self):
        face = FaceState()
        changes = face.apply([("expression", "happy"), ("look", (-1.0, 0.0)), ("color", "PURPLE")], 5.0)
        self.assertEqual(changes, {"mode": "FACE", "color": "PURPLE"})
        self.assertEqual((face.mood, face.mood_until, face.look_held), ("happy", 15.0, True))
        self.assertEqual(face.apply([("wave", "speaking")], 5.0), {"mode": "WAVE"})
        face.expire(20.0)
        self.assertEqual((face.mood, face.wave_state), ("neutral", "listening"))

    def test_a_held_look_lasts_while_luna_is_busy(self):
        face = FaceState()
        face.apply([("look", (-1.0, 0.0))], 0.0)
        face.move_eyes(10.0, thinking=False, busy=True, animations=False)
        self.assertEqual((face.target_x, face.look_held), (-1.0, True))
        face.move_eyes(13.0, thinking=False, busy=False, animations=False)
        self.assertEqual((face.target_x, face.look_held), (0, False))


class AppPageTests(unittest.TestCase):
    """An app page on a real (headless) RobotHUD."""

    def setUp(self):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        from luna_core import app
        from luna_core.ui import Page
        from luna_core.ui.theme import nav_rects
        self.app, self.nav_rects = app, nav_rects
        folder = tempfile.mkdtemp()
        for patch in (mock.patch.object(app, "speech", FakeSpeech()),
                      mock.patch.object(app, "read_preferences",
                                        lambda: read_preferences(Path(folder) / "prefs.json")),
                      mock.patch.object(app, "write_preferences", lambda prefs: None),
                      mock.patch.dict(os.environ, {"LUNA_MEMORY_FILE": str(Path(folder) / "m.db"),
                                    "LUNA_TASKS_FILE": str(Path(folder) / "t.db")})):
            patch.start()
            self.addCleanup(patch.stop)

        class Lights(Page):
            name = "Lights"

            def __init__(self):
                self.drawn, self.taps = 0, []

            def draw(self, hud, surface, t):
                self.drawn += 1

            def touch(self, hud, x, y, t):
                self.taps.append((x, y))

        self.page = Lights()
        robot = Robot(display="headless", voice=False, speech=False, ai=False, hardware="mock")
        robot.add_page(self.page)
        with self.assertRaises(ValueError):
            robot.add_page(types.SimpleNamespace(name="Home"))
        self.hud = app.RobotHUD(robot.options)
        self.addCleanup(app.pygame.quit)

    def test_luna_pages_keep_their_buttons_and_the_app_page_gets_one(self):
        self.assertEqual([p.name for p in self.hud.pages], ["Chat", "Home", "Camera", "Settings", "Lights"])
        self.assertEqual([tuple(r) for r in self.nav_rects(4)],
                         [(216 + i * 216, 597, 199, 93) for i in range(4)])
        rects = self.nav_rects(5)
        self.assertTrue(all(a.right < b.left for a, b in zip(rects, rects[1:])))
        self.assertLessEqual(rects[-1].right, 1280)

    def test_the_app_page_is_shown_and_gets_taps(self):
        self.hud.handle_touch(*self.nav_rects(5)[4].center, 1.0)
        self.assertEqual(self.hud.page, "Lights")
        self.hud.frame(1.0)
        self.hud.handle_touch(640, 300, 1.0)
        self.assertEqual((self.page.drawn, self.page.taps), (1, [(640, 300)]))

    def test_a_yes_no_question_takes_taps_on_its_box_only(self):
        from luna_core.assistant_core.tools import FACE_TOOLS
        from luna_core.tasks.confirm import Confirmation, Item
        from luna_core.ui.theme import CONFIRM_NO_RECT, CONFIRM_YES_RECT
        self.hud.page = "Lights"
        chat = self.hud.conversation
        chat.confirmation = Confirmation([Item("blink:1", "Remember", "You like tea.")], 1.0,
                                         payload=[(FACE_TOOLS[-1], {})])
        self.hud.frame(1.0)  # draws the question over the page
        self.hud.handle_touch(640, 200, 1.0)
        self.assertEqual((self.page.taps, chat.confirmation is not None), ([], True))
        self.hud.handle_touch(*CONFIRM_NO_RECT.center, 1.0)
        self.assertIsNone(chat.confirmation)
        chat.confirmation = Confirmation([Item("blink:1", "Remember")], 1.0, payload=[(FACE_TOOLS[-1], {})])
        self.hud.handle_touch(*CONFIRM_YES_RECT.center, 1.0)
        self.assertIsNone(chat.confirmation)
        self.hud.handle_touch(640, 200, 1.0)
        self.assertEqual(self.page.taps, [(640, 200)], "taps reach the page again")

    def test_number_keys_choose_pages(self):
        event = types.SimpleNamespace(type=self.app.pygame.KEYDOWN, key=self.app.pygame.K_5, unicode="5")
        with mock.patch.object(self.app.pygame.event, "get", return_value=[event]):
            self.hud.events(1.0)
        self.assertEqual(self.hud.page, "Lights")

    def test_only_pages_can_be_added(self):
        with self.assertRaises(TypeError):
            self.hud.add_page(object())


if __name__ == "__main__":
    unittest.main()
