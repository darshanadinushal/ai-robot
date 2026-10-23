"""Offline voice commands using Vosk and sounddevice (both optional)."""
import json
import math
import queue
import struct
import threading
from pathlib import Path

from .commands import VOCABULARY

MODEL_NAME = "vosk-model-small-en-us-0.15"
SAMPLE_RATE = 16000


def model_path():
    return Path.home() / ".ai-robot-models" / MODEL_NAME


class VoiceListener:
    """Runs recognition in a thread; poll `texts` and `level` from the UI loop."""

    def __init__(self, muted=None):
        self._muted = muted or (lambda: False)
        self.texts = queue.Queue()
        self.level = 0.0
        self.status = "starting"
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        try:
            import sounddevice
            from vosk import KaldiRecognizer, Model, SetLogLevel
        except ImportError:
            self.status = "voice packages missing"
            return
        path = model_path()
        if not path.is_dir():
            self.status = "voice model missing"
            return
        try:
            SetLogLevel(-1)
            recognizer = KaldiRecognizer(
                Model(str(path)), SAMPLE_RATE, json.dumps(VOCABULARY + ["[unk]"]))
            audio = queue.Queue()

            def callback(data, frames, time_info, status):
                audio.put(bytes(data))

            with sounddevice.RawInputStream(
                    samplerate=SAMPLE_RATE, blocksize=4000, dtype="int16",
                    channels=1, callback=callback):
                self.status = "listening"
                while not self._stop.is_set():
                    try:
                        data = audio.get(timeout=.5)
                    except queue.Empty:
                        continue
                    if self._muted():
                        recognizer.Reset()
                        self.level = 0.0
                        continue
                    samples = struct.unpack("<%dh" % (len(data) // 2), data)
                    rms = math.sqrt(sum(s * s for s in samples) / max(len(samples), 1))
                    self.level = min(1.0, rms / 6000.0)
                    if recognizer.AcceptWaveform(data):
                        text = json.loads(recognizer.Result()).get("text", "")
                        if text and text != "[unk]":
                            self.texts.put(text)
        except Exception as exc:  # no microphone, busy device, etc.
            self.status = "microphone error: " + str(exc)[:60]
