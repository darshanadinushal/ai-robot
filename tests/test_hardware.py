"""Luna Core without physical hardware: mock and absent devices, and the app wired to them."""
import os
import tempfile
import time
import types
import unittest
import wave
from pathlib import Path
from unittest import mock

from luna_core import hardware
from luna_core.preferences import read_preferences
from luna_core.speech import SpeechEngine


def wait_until(condition, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


class WavSynth:
    """Writes 0.05 s of silence as a real WAV file."""

    def __init__(self, voice):
        self.voice = voice

    def synthesize(self, text, wav):
        with wave.open(str(wav), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\0\0" * 800)


class CreateTests(unittest.TestCase):
    def test_mock_hardware_needs_no_devices(self):
        hw = hardware.create("mock")
        self.assertEqual(hw.mode, "mock")
        self.assertIsInstance(hw.microphone, hardware.MockMicrophone)
        self.assertIsInstance(hw.speaker, hardware.MockSpeaker)
        self.assertTrue(hw.camera.available and hw.motors.available)
        self.assertEqual((hw.audio.mic, hw.audio.speaker), ("Mock microphone", "Mock speaker"))

    def test_real_hardware_without_camera_or_motors(self):
        hw = hardware.create("real", detect_audio=False)
        self.assertIsInstance(hw.microphone, hardware.SoundDeviceMicrophone)
        self.assertIsInstance(hw.speaker, hardware.SystemSpeaker)
        self.assertFalse(hw.camera.available or hw.motors.available)
        self.assertIsNone(hw.camera.capture())
        with self.assertRaises(hardware.HardwareUnavailable):
            hw.motors.drive(0.5, 0.5)
        hw.close()  # stopping absent motors is always safe

    def test_choosing_by_environment(self):
        self.assertEqual(hardware.mode_from_env({}), "real")
        self.assertEqual(hardware.mode_from_env({"LUNA_HARDWARE": " Mock "}), "mock")
        with self.assertLogs("luna.hardware", "WARNING"):
            self.assertEqual(hardware.mode_from_env({"LUNA_HARDWARE": "robot"}), "real")
        with mock.patch.dict(os.environ, {"LUNA_HARDWARE": "mock"}):
            self.assertEqual(hardware.create().mode, "mock")
        with self.assertRaises(ValueError):
            hardware.create("tv")


class MockDeviceTests(unittest.TestCase):
    def test_microphone_delivers_fed_audio_then_silence(self):
        mic = hardware.MockMicrophone(realtime=False)
        mic.feed(b"\x01\x02" * 150)  # 1.5 blocks of 100 samples
        blocks = []
        with mic.open(lambda data, status: blocks.append(data), 16000, 100):
            self.assertTrue(wait_until(lambda: len(blocks) >= 3))
            self.assertTrue(mic.is_open)
        self.assertFalse(mic.is_open)
        self.assertEqual(blocks[0], b"\x01\x02" * 100)
        self.assertEqual(blocks[1], b"\x01\x02" * 50 + b"\0" * 100)
        self.assertEqual(blocks[2], b"\0" * 200)

    def test_motors_clamp_and_stop_when_closed(self):
        hw = hardware.create("mock")
        hw.motors.drive(2.0, -0.25)
        self.assertEqual(hw.motors.speeds, (1.0, -0.25))
        hw.close()
        self.assertEqual((hw.motors.speeds, hw.motors.commands[-1]), ((0.0, 0.0), ("stop",)))
        frame = hw.camera.capture()
        self.assertEqual(len(frame.pixels), frame.width * frame.height * 3)

    def test_speech_plays_through_the_chosen_speaker(self):
        engine = SpeechEngine(make_natural=WavSynth, make_basic=WavSynth)
        first, second = hardware.MockSpeaker(), hardware.MockSpeaker()
        engine.set_player(lambda: first)
        engine.speak_async("Hello, this is the first sentence.")
        self.assertTrue(wait_until(lambda: first.played and not engine.is_talking()))
        engine.set_player(lambda: second)  # also while the speech threads run
        engine.speak_async("And this is the second sentence.")
        self.assertTrue(wait_until(lambda: second.played and not engine.is_talking()))
        self.assertEqual((len(first.played), len(second.played)), (1, 1))
        self.assertAlmostEqual(second.played[0][1], 0.05, places=2)


class AppWithMockHardwareTests(unittest.TestCase):
    def test_app_listens_and_speaks_through_the_given_hardware(self):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        from luna_core import app
        from test_settings import FakeSpeech
        folder = tempfile.mkdtemp()
        speech = FakeSpeech()
        patches = [mock.patch.object(app, "speech", speech),
                   mock.patch.object(app, "read_preferences",
                                     lambda: read_preferences(Path(folder) / "prefs.json")),
                   mock.patch.object(app, "write_preferences", lambda prefs: None),
                   mock.patch.dict(os.environ, {"LUNA_MEMORY_FILE": str(Path(folder) / "m.db")})]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        hw = hardware.create("mock")
        args = types.SimpleNamespace(fullscreen=False, windowed=True, no_voice=False,
                                     no_speech=False, no_ai=True, hardware=hw)
        hud = app.RobotHUD(args)
        self.addCleanup(app.pygame.quit)
        self.addCleanup(hud.voice.stop)
        self.assertIs(hud.voice.microphone, hw.microphone)
        self.assertIs(speech.speaker, hw.speaker)
        self.assertEqual(hud.audio.mic, "Mock microphone")
        # With Vosk and a model the mock microphone is opened; without them Luna still runs.
        self.assertTrue(wait_until(lambda: hw.microphone.is_open or hud.voice.status not in
                                   ("starting", "loading"), timeout=15))
        hud.frame(1.0)


if __name__ == "__main__":
    unittest.main()
