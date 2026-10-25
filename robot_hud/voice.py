"""Offline speech recognition using Vosk and sounddevice (both optional)."""
import json
import math
import queue
import struct
import threading
from pathlib import Path

from . import languages
from .commands import vocabulary

# Offline recognition model for each listening language (follows the voice language), from the
# language packs. Languages without one are heard with the English model.
MODELS = {code: pack.listening_model for code, pack in languages.PACKS.items()
          if pack.listening_model}
MODEL_NAME = MODELS[languages.DEFAULT]
SAMPLE_RATE = 16000


def model_path(language="en"):
    return Path.home() / ".ai-robot-models" / MODELS.get(language, MODEL_NAME)


class VoiceListener:
    """Runs recognition in a thread; poll `texts`, `partial` and `level` from the UI loop.

    free_form=True recognises any sentence (needed for AI questions);
    False restricts recognition to the command vocabulary, which is more accurate.
    `language` picks the model; `set_language` switches it inside the listening thread.
    If that model is missing Luna keeps listening in English (`active_language`).
    """

    def __init__(self, muted=None, free_form=False, language="en"):
        self._muted = muted or (lambda: False)
        self.free_form = free_form
        self.language = language if language in MODELS else languages.DEFAULT
        self.active_language = None
        self._loaded_for = None
        self._model = None
        self.texts = queue.Queue()
        self.partial = ""
        self.level = 0.0
        self.status = "starting"
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def set_language(self, language):
        self.language = language if language in MODELS else languages.DEFAULT

    def start(self):
        self._thread.start()

    def stop(self, timeout=3.0):
        """Stop and wait so native audio code is not running at interpreter exit."""
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout)

    def _run(self):
        try:
            import sounddevice
            from vosk import KaldiRecognizer, Model, SetLogLevel
        except ImportError:
            self.status = "voice packages missing"
            return
        try:
            SetLogLevel(-1)
            recognizer = self._load(Model, KaldiRecognizer)
            if recognizer is None or self._stop.is_set():
                return
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
                    if self.language != self._loaded_for:
                        recognizer = self._load(Model, KaldiRecognizer) or recognizer
                        self.status = "listening"
                        while not audio.empty():  # skip speech heard while loading
                            audio.get_nowait()
                        continue
                    if self._muted():
                        recognizer.Reset()
                        self.level = 0.0
                        self.partial = ""
                        continue
                    samples = struct.unpack("<%dh" % (len(data) // 2), data)
                    rms = math.sqrt(sum(s * s for s in samples) / max(len(samples), 1))
                    self.level = min(1.0, rms / 6000.0)
                    if recognizer.AcceptWaveform(data):
                        self.partial = ""
                        text = json.loads(recognizer.Result()).get("text", "")
                        text = text.replace("[unk]", "").strip()
                        if text:
                            self.texts.put(text)
                    else:
                        partial = json.loads(recognizer.PartialResult()).get("partial", "")
                        self.partial = partial.replace("[unk]", "").strip()
        except Exception as exc:  # no microphone, busy device, etc.
            self.status = "microphone error: " + str(exc)[:60]

    def _load(self, Model, KaldiRecognizer):
        """Load the model for `language` (English if it is missing); None if none exists."""
        wanted = self.language
        used = wanted if model_path(wanted).is_dir() else "en"
        if not model_path(used).is_dir():
            self.status = "voice model missing"
            return None
        if used != self.active_language:
            self.status = "loading"
            self._model = Model(str(model_path(used)))
        self._loaded_for, self.active_language = wanted, used
        if self.free_form:
            return KaldiRecognizer(self._model, SAMPLE_RATE)
        return KaldiRecognizer(self._model, SAMPLE_RATE,
                               json.dumps(vocabulary(used) + ["[unk]"], ensure_ascii=False))
