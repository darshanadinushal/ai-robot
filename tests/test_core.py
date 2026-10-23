"""No pygame required: test layout, blink, settings without a display."""
import tempfile
import unittest
from pathlib import Path

from robot_hud.geometry import Viewport, blink_openness, clamp
from robot_hud.preferences import COLOR_CHOICES, read_preferences, write_preferences


class GeometryTests(unittest.TestCase):
    def test_landscape_native(self):
        v = Viewport(1280,720)
        self.assertEqual(v.rectangle, (0,0,1280,720))
        self.assertEqual(v.to_logical(100,200), (100,200))

    def test_letterbox_click(self):
        v = Viewport(1000,1000)
        left, top, width, height = v.rectangle
        self.assertEqual(width,1000)
        self.assertIsNone(v.to_logical(400,0))
        x,y=v.to_logical(left+width/2,top+height/2)
        self.assertAlmostEqual(x,640)
        self.assertAlmostEqual(y,360)

    def test_blink_cycle(self):
        self.assertEqual(blink_openness(0,10),1)
        self.assertAlmostEqual(blink_openness(10.14,10),.08)
        self.assertAlmostEqual(blink_openness(10.28,10),1,places=5)

    def test_clamp(self):
        self.assertEqual(clamp(-3, -1,1),-1)
        self.assertEqual(clamp(0.5,-1,1),0.5)
        self.assertEqual(clamp(3,-1,1),1)


class PreferenceTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'preferences.json'
            self.assertEqual(read_preferences(p)['color'],'CYAN')
            write_preferences({'color':'PURPLE','mode':'FACE','layout':'CLASSIC','wave_layout':'MESH','animations':False},p)
            self.assertEqual(read_preferences(p),{'color':'PURPLE','mode':'FACE','layout':'CLASSIC','wave_layout':'MESH','animations':False})

    def test_invalid_values_are_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'prefs.json'
            p.write_text('{"color":"NOT_A_COLOR","animations":"no"}')
            self.assertEqual(read_preferences(p),{'color':'CYAN','mode':'FACE','layout':'CLASSIC','wave_layout':'MESH','animations':True})
            self.assertIn('CYAN',COLOR_CHOICES)


class CommandTests(unittest.TestCase):
    def test_parse(self):
        from robot_hud.commands import parse_command
        self.assertEqual(parse_command("be happy"), [("expression", "happy")])
        self.assertEqual(parse_command("look left"), [("look", (-1.0, 0.0))])
        self.assertEqual(parse_command("wave mode"), [("mode", "WAVE")])
        self.assertEqual(parse_command("hello"), [])
        from robot_hud.commands import reply_for
        self.assertEqual(reply_for(parse_command("look left")), "Looking left.")
        self.assertIsNone(reply_for([]))


if __name__=='__main__':
    unittest.main()
