"""Offline speech recognition using Vosk and sounddevice (both optional)."""
import json
import math
import os
import queue
import struct
import threading
import time
from pathlib import Path

from . import languages
from .commands import vocabulary

# Offline recognition model for each listening language (follows the voice language), from the
# language packs. Languages without one are heard with the English model.
MODELS = {code: pack.listening_model for code, pack in languages.PACKS.items()
          if pack.listening_model}
MODEL_NAME = MODELS[languages.DEFAULT]
SAMPLE_RATE = 16000
BLOCK_SAMPLES = 4000  # 0.25 s of audio per microphone block
# At most this much unprocessed microphone audio is kept; more means the Pi cannot keep up.
try:
    BUFFER_SECONDS = max(1.0, float(os.environ.get("LUNA_MIC_BUFFER_SECONDS", "10")))
except ValueError:
    BUFFER_SECONDS = 10.0


def model_path(language="en"):
    return Path.home() / ".ai-robot-models" / MODELS.get(language, MODEL_NAME)


class VoiceListener:
    """Runs recognition in a thread; poll `texts`, `partial` and `level` from the UI loop.

    free_form=True recognises any sentence (needed for AI questions);
    False restricts recognition to the command vocabulary, which is more accurate.
    `language` picks the model; `set_language` switches it inside the listening thread.
    If that model is missing Luna keeps listening in English (`active_language`).

    `texts` gets Vosk's final results (pieces of a question; the app joins them with
    listening.UtteranceAssembler). Microphone audio waits in a bounded queue (BUFFER_SECONDS).
    Audio is thrown away while muted or loading; if audio is lost while listening, the
    recogniser is reset and `overruns` goes up, so the app drops the unfinished question.
    Diagnostics: `dropped_blocks`, `overruns`, `decode_seconds` (recogniser CPU time).
    `capture`: an AudioRing set by the app only while online listening is on; heard blocks are
    added with their time, and it is cleared while muted (Luna never records herself).
    """

    def __init__(self, muted=None, free_form=False, language="en"):
        self._muted = muted or (lambda: False)
        self.free_form = free_form
        self.language = language if language in MODELS else languages.DEFAULT
        self.active_language = None
        self._loaded_for = None
        self._model = None
        self.texts = queue.Queue()
        self.audio = queue.Queue(maxsize=max(1, int(BUFFER_SECONDS * SAMPLE_RATE / BLOCK_SAMPLES)))
        self._overflow = threading.Event()
        self.dropped_blocks = 0
        self.overruns = 0
        self.decode_seconds = 0.0
        self.partial = ""
        self.level = 0.0
        self.status = "starting"
        self.capture = None
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
            def callback(data, frames, time_info, status):
                self._on_audio(data, status)

            with sounddevice.RawInputStream(
                    samplerate=SAMPLE_RATE, blocksize=BLOCK_SAMPLES, dtype="int16",
                    channels=1, callback=callback):
                self.status = "listening"
                while not self._stop.is_set():
                    try:
                        data = self.audio.get(timeout=.5)
                    except queue.Empty:
                        continue
                    if self.language != self._loaded_for:
                        recognizer = self._load(Model, KaldiRecognizer) or recognizer
                        self.status = "listening"
                        self.partial = ""
                        self._drain()  # skip speech heard while loading
                        self._overflow.clear()
                        continue
                    self._step(recognizer, data)
        except Exception as exc:  # no microphone, busy device, etc.
            self.status = "microphone error: " + str(exc)[:60]
        finally:
            self.partial = ""

    def _on_audio(self, data, status=None):
        """Microphone callback (audio thread): queue a block, never block or grow forever."""
        if status and getattr(status, "input_overflow", False):
            self._overflow.set()
        try:
            self.audio.put_nowait(bytes(data))
        except queue.Full:
            self.dropped_blocks += 1
            self._overflow.set()

    def _drain(self):
        while True:
            try:
                self.audio.get_nowait()
            except queue.Empty:
                return

    def _step(self, recognizer, data):
        """Recognise one block of audio (listening thread)."""
        muted = self._muted()
        if self._overflow.is_set() or muted:
            lost = self._overflow.is_set()
            self._overflow.clear()
            recognizer.Reset()
            capture = self.capture
            if capture is not None:
                capture.clear()
            self._drain()  # stale: heard while Luna talked, or behind after lost audio
            self.partial = ""
            self.level = 0.0
            if lost and not muted:
                self.overruns += 1
                print("Luna listening: microphone audio lost (%d blocks dropped so far), "
                      "the unfinished question is dropped" % self.dropped_blocks, flush=True)
            return
        samples = struct.unpack("<%dh" % (len(data) // 2), data)
        rms = math.sqrt(sum(s * s for s in samples) / max(len(samples), 1))
        self.level = min(1.0, rms / 6000.0)
        capture = self.capture
        if capture is not None:
            capture.add(data, time.monotonic())
        started = time.perf_counter()
        if recognizer.AcceptWaveform(data):
            self.partial = ""
            text = json.loads(recognizer.Result()).get("text", "")
            text = text.replace("[unk]", "").strip()
            if text:
                self.texts.put(text)
        else:
            partial = json.loads(recognizer.PartialResult()).get("partial", "")
            self.partial = partial.replace("[unk]", "").strip()
        self.decode_seconds += time.perf_counter() - started

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
