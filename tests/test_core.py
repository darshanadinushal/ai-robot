"""No pygame required: test layout, blink, settings without a display."""
import tempfile
import unittest
from pathlib import Path

from luna_core.geometry import Viewport, blink_openness, clamp
from luna_core.preferences import COLOR_CHOICES, read_preferences, write_preferences


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
            write_preferences({'color':'PURPLE','mode':'FACE','layout':'CLASSIC','wave_layout':'MESH','animations':False,'memory':False,'robot_name':'max'},p)
            self.assertEqual(read_preferences(p),{'color':'PURPLE','mode':'FACE','layout':'CLASSIC','wave_layout':'MESH','animations':False,'voice':'EN_FEMALE','language':'en','online_listening':False,'memory':False,'voice_interrupt':None,'robot_name':'Max'})

    def test_invalid_values_are_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'prefs.json'
            p.write_text('{"color":"NOT_A_COLOR","animations":"no","robot_name":"R2-D2"}')
            self.assertEqual(read_preferences(p),{'color':'CYAN','mode':'FACE','layout':'CLASSIC','wave_layout':'MESH','animations':True,'voice':'EN_FEMALE','language':'en','online_listening':False,'memory':True,'voice_interrupt':None,'robot_name':'Luna'})
            self.assertIn('CYAN',COLOR_CHOICES)

    def test_voice_interrupt_is_automatic_until_chosen(self):
        from luna_core.settings import apply_choice, voice_interrupt_wanted
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'prefs.json'
            prefs=read_preferences(p)
            self.assertEqual((voice_interrupt_wanted(prefs,False),voice_interrupt_wanted(prefs,True)),(False,True))
            apply_choice(prefs,'voice_interrupt',None,echo_cancelling=True)  # was on by itself: off
            write_preferences(prefs,p)
            self.assertIs(read_preferences(p)['voice_interrupt'],False)
            self.assertFalse(voice_interrupt_wanted(read_preferences(p),True),'the choice is kept')


class AudioDeviceTests(unittest.TestCase):
    ARRAY_IN='alsa_input.usb-Seeed_Studio_reSpeaker_XVF3800_4-Mic_Array_101991441-00.analog-stereo'
    ARRAY_OUT='alsa_output.usb-Seeed_Studio_reSpeaker_XVF3800_4-Mic_Array_101991441-00.analog-stereo'
    SOUNDCORE='bluez_output.AC_12_2F_00_11_22.1'

    def choose(self,mic,speaker,env=None,inputs=()):
        from luna_core.audio_devices import choose
        return choose(mic,speaker,env or {},inputs)

    def test_array_as_mic_and_speaker_cancels_echo(self):
        audio=self.choose(self.ARRAY_IN,self.ARRAY_OUT)
        self.assertTrue(audio.echo_cancelling)
        self.assertIsNone(audio.mic_device,'already the default microphone')
        self.assertEqual(audio.mic,'Seeed Studio reSpeaker XVF3800 4-Mic Array')

    def test_speech_on_another_speaker_is_not_cancelled(self):
        self.assertFalse(self.choose(self.ARRAY_IN,self.SOUNDCORE).echo_cancelling,'Bluetooth music speaker')
        self.assertFalse(self.choose('Microphone (Realtek)','Speakers (Realtek)').echo_cancelling)

    def test_a_plugged_in_array_is_preferred_and_env_overrides(self):
        inputs=('Microphone (Realtek)','reSpeaker XVF3800 4-Mic Array: USB Audio (hw:2,0)')
        audio=self.choose('Microphone (Realtek)','reSpeaker XVF3800 4-Mic Array',inputs=inputs)
        self.assertEqual(audio.mic_device,inputs[1])
        self.assertTrue(audio.echo_cancelling)
        audio=self.choose('Microphone (Realtek)','Speakers',{'LUNA_MIC':'0','LUNA_ECHO_CANCELLING':'on'},inputs)
        self.assertEqual((audio.mic_device,audio.mic,audio.echo_cancelling),(0,'Microphone (Realtek)',True))
        self.assertFalse(self.choose(self.ARRAY_IN,self.ARRAY_OUT,{'LUNA_ECHO_CANCELLING':'off'}).echo_cancelling)
        audio=self.choose('Jabra Evolve','Jabra Evolve',{'LUNA_ECHO_CANCELLING_DEVICES':'jabra'})
        self.assertTrue(audio.echo_cancelling)

    def test_the_microphone_falls_back_to_the_default(self):
        from luna_core.hardware import SoundDeviceMicrophone
        opened=[]
        class FakeSoundDevice:
            def RawInputStream(self,device=None,**options):
                if device is not None:
                    raise OSError('Invalid number of channels')
                opened.append(options['channels'])
                return 'default stream'
        mic=SoundDeviceMicrophone(device='reSpeaker')
        self.assertEqual(mic._open(FakeSoundDevice(),None),'default stream')
        self.assertEqual((opened,mic.device),([1],None))


class CommandTests(unittest.TestCase):
    def test_parse(self):
        from luna_core.commands import parse_command
        self.assertEqual(parse_command("be happy"), [("expression", "happy")])
        self.assertEqual(parse_command("look left"), [("look", (-1.0, 0.0))])
        self.assertEqual(parse_command("wave mode"), [("mode", "WAVE")])
        self.assertEqual(parse_command("hello"), [])
        from luna_core.commands import reply_for
        self.assertEqual(reply_for(parse_command("look left")), "Looking left.")
        self.assertIsNone(reply_for([]))



class RouteTests(unittest.TestCase):
    def test_routes(self):
        from luna_core.commands import route
        self.assertEqual(route("hey luna tell me about mars"), ("ai", "tell me about mars"))
        self.assertEqual(route("look left")[0], "command")
        self.assertEqual(route("luna")[0], "wake")
        self.assertEqual(route("he luna")[0], "wake")
        self.assertEqual(route("he luna what is mars"), ("ai", "what is mars"))
        self.assertEqual(route("so we went to the shop and bought some bread")[0], "ignore")
        self.assertEqual(route("why is the sky blue", conversation=True), ("ai", "why is the sky blue"))
        self.assertEqual(route("")[0], "ignore")

    def test_names_that_sound_like_luna(self):
        from luna_core.commands import route
        for text, expected in (("hey lena what time is it", ("ai", "what time is it")),
                               ("lou na why is the sky blue", ("ai", "why is the sky blue")),
                               ("what time is it loner", ("ai", "what time is it")),
                               ("hey luma", ("wake", "")),
                               ("loona look left", ("command", [("look", (-1.0, 0.0))]))):
            with self.subTest(text):
                self.assertEqual(route(text), expected)
        self.assertEqual(route("hej lona vad är klockan", language="sv"), ("ai", "vad är klockan"))
        for text in ("the lung is an organ", "lunch is ready now", "tuna salad please",
                     "we met lena today and talked", "hello"):
            with self.subTest(text):
                self.assertEqual(route(text)[0], "ignore", "only near the start or end, like a name")

    def test_a_new_robot_name(self):
        from luna_core import i18n, naming
        from luna_core.assistant_core.context import instructions
        from luna_core.commands import route, vocabulary
        self.assertEqual([naming.clean_name(n) for n in ('nOVA', 'Åsa', 'R2', 'x', 'Two words')],
                         ['Nova', 'Åsa', None, None, None])
        naming.set_name('Nova')
        try:
            self.assertEqual(route('hey nova what is mars'), ('ai', 'what is mars'))
            self.assertEqual(route('hey nava what is mars'), ('ai', 'what is mars'), 'sounds alike')
            self.assertEqual(route('hey luna what is mars')[0], 'ignore', 'the old name is gone')
            self.assertIn('nova', vocabulary('sv'))
            self.assertNotIn('lugn', vocabulary('sv'))
            self.assertTrue(instructions().startswith('You are Nova,'))
            self.assertEqual(i18n.tr('SAY "HEY LUNA" AND ASK ANYTHING  •  TAP TO STOP TALKING'),
                             'SAY "HEY NOVA" AND ASK ANYTHING  •  TAP TO STOP TALKING')
            naming.set_name('Max')
            self.assertEqual(route('hey max look left')[0], 'command')
            self.assertEqual(route('hey mix look left')[0], 'command', 'short name: exact only')
            self.assertEqual(route('mix look left please now')[0], 'ignore')
        finally:
            naming.set_name('Luna')

    def test_clean_for_speech(self):
        from luna_core.speech import clean_for_speech
        self.assertEqual(clean_for_speech("**Hi** `there`\n- one"), "Hi there one")
        self.assertLessEqual(len(clean_for_speech("word " * 500)), 701)


class AIWorkerTests(unittest.TestCase):
    def _get(self, worker):
        import queue
        return worker.answers.get(timeout=5)

    def test_answer_and_error(self):
        from luna_core.ai import AIWorker

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
