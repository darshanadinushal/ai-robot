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
            write_preferences({'color':'PURPLE','mode':'FACE','layout':'CLASSIC','wave_layout':'MESH','animations':False,'memory':False},p)
            self.assertEqual(read_preferences(p),{'color':'PURPLE','mode':'FACE','layout':'CLASSIC','wave_layout':'MESH','animations':False,'voice':'EN_FEMALE','language':'en','online_listening':False,'memory':False})

    def test_invalid_values_are_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'prefs.json'
            p.write_text('{"color":"NOT_A_COLOR","animations":"no"}')
            self.assertEqual(read_preferences(p),{'color':'CYAN','mode':'FACE','layout':'CLASSIC','wave_layout':'MESH','animations':True,'voice':'EN_FEMALE','language':'en','online_listening':False,'memory':True})
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



class RouteTests(unittest.TestCase):
    def test_routes(self):
        from robot_hud.commands import route
        self.assertEqual(route("hey luna tell me about mars"), ("ai", "tell me about mars"))
        self.assertEqual(route("look left")[0], "command")
        self.assertEqual(route("luna")[0], "wake")
        self.assertEqual(route("he luna")[0], "wake")
        self.assertEqual(route("he luna what is mars"), ("ai", "what is mars"))
        self.assertEqual(route("so we went to the shop and bought some bread")[0], "ignore")
        self.assertEqual(route("why is the sky blue", conversation=True), ("ai", "why is the sky blue"))
        self.assertEqual(route("")[0], "ignore")

    def test_clean_for_speech(self):
        from robot_hud.speech import clean_for_speech
        self.assertEqual(clean_for_speech("**Hi** `there`\n- one"), "Hi there one")
        self.assertLessEqual(len(clean_for_speech("word " * 500)), 701)


class AIWorkerTests(unittest.TestCase):
    def _get(self, worker):
        import queue
        return worker.answers.get(timeout=5)

    def test_answer_and_error(self):
        from robot_hud.ai import AIWorker

        class Brain:
            def ask(self, q, language="en", instructions=None):
                if q == "boom":
                    raise RuntimeError("secret detail")
                return "Mars is red."
            def forget(self):
                pass

        w = AIWorker(brain_factory=Brain)
        request = w.ask("mars?")
        self.assertTrue(request)
        self.assertEqual(self._get(w), ("text", request, "Mars is red."))
        self.assertEqual(self._get(w), ("sentence", request, "Mars is red."))
        self.assertEqual(self._get(w), ("done", request, None))
        import time
        for _ in range(50):
            if not w.busy:
                break
            time.sleep(0.01)
        self.assertTrue(w.ask("boom"))
        kind, _, text = self._get(w)
        self.assertEqual(kind, "error")
        self.assertNotIn("secret", text)

if __name__=='__main__':
    unittest.main()
