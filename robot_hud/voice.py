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
from .commands import language_switch, switch_vocabulary, vocabulary

# Offline recognition model for each listening language (follows the voice language), from the
# language packs. Languages without one are heard with the English model.
MODELS = {code: pack.listening_model for code, pack in languages.PACKS.items()
          if pack.listening_model}
MODEL_NAME = MODELS[languages.DEFAULT]
SAMPLE_RATE = 16000

# Cross-language switching ("Luna, speak English" while Luna listens in Swedish): a small
# recogniser per other installed language that only knows its switch phrases.
# LUNA_SWITCH_EARS limits how many (each keeps one more Vosk model in memory); 0 turns it off.
SWITCH_EARS = max(0, int(os.environ.get("LUNA_SWITCH_EARS", "1") or 0))
VOICE_LEVEL = 0.04     # microphone level that counts as someone speaking
SWITCH_HANGOVER = 1.0  # seconds the switch ears keep listening after the speech stops
HOLD_SECONDS = 1.5     # longest a heard sentence waits for the switch ears to decide


def model_path(language="en"):
    return Path.home() / ".ai-robot-models" / MODELS.get(language, MODEL_NAME)


def listening_installed(language):
    """Can Luna listen in `language`? (Packs without a listening model use English.)"""
    if not languages.get(language).listening_model:
        return True
    return language in MODELS and model_path(language).is_dir()


class SwitchEar:
    """A recogniser that only listens for "Luna, speak <language>" in `language`.

    It gets a grammar of the switch words; models that support one then answer [unk] for
    other speech, which is rejected. Models without runtime grammars (the Swedish one) still
    work: language_switch only accepts the exact request structure.
    """

    def __init__(self, language, recognizer):
        self.language, self.recognizer = language, recognizer
        self.active = False     # inside an utterance
        self.heard_wake = False

    def accept(self, data):
        """Feed audio; returns (target language, text) when a switch request was heard."""
        self.active = True
        if self.recognizer.AcceptWaveform(data):
            return self._result(self.recognizer.Result())
        partial = json.loads(self.recognizer.PartialResult()).get("partial", "").split()
        self.heard_wake = any(w in languages.get(self.language).wake_words for w in partial)
        return None

    def finish(self):
        return self._result(self.recognizer.FinalResult())

    def reset(self):
        self.recognizer.Reset()
        self.active = self.heard_wake = False

    def _result(self, raw):
        self.active = self.heard_wake = False
        text = json.loads(raw).get("text", "")
        words = text.split()
        if not words or "[unk]" in words:  # other speech: never guess a switch
            return None
        target = language_switch(text, self.language)
        return (target, text) if target else None


class VoiceListener:
    """Runs recognition in a thread; poll `texts`, `partial` and `level` from the UI loop.

    free_form=True recognises any sentence (needed for AI questions);
    False restricts recognition to the command vocabulary, which is more accurate.
    `language` picks the model; `set_language` switches it inside the listening thread.
    If that model is missing Luna keeps listening in English (`active_language`).
    Switch requests heard by a SwitchEar in another language go to `switches` as
    (target language, heard language, text); the main recogniser's version of that same
    sentence is then dropped.
    """

    def __init__(self, muted=None, free_form=False, language="en"):
        self._muted = muted or (lambda: False)
        self.free_form = free_form
        self.language = language if language in MODELS else languages.DEFAULT
        self.active_language = None
        self._loaded_for = None
        self._model = None
        self._models = {}  # language -> loaded Vosk model (shared by the main recogniser and ears)
        self._models_lock = threading.Lock()
        self._recognizer = None
        self._ears = []
        self._voice_until = 0.0
        self._held = None          # (text, deadline): waits for the ears to decide
        self._suppress_until = 0.0  # drop the main result of a sentence an ear understood
        self.texts = queue.Queue()
        self.switches = queue.Queue()
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
            self._recognizer = self._load(Model, KaldiRecognizer)
            if self._recognizer is None or self._stop.is_set():
                return
            self._start_ears(Model, KaldiRecognizer)
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
                        self._release()
                        self._ears = []
                        self._recognizer = self._load(Model, KaldiRecognizer) or self._recognizer
                        self._start_ears(Model, KaldiRecognizer)
                        self.status = "listening"
                        while not audio.empty():  # skip speech heard while loading
                            audio.get_nowait()
                        continue
                    self._hear(data, time.monotonic())
        except Exception as exc:  # no microphone, busy device, etc.
            self.status = "microphone error: " + str(exc)[:60]

    def _hear(self, data, now):
        """Recognise one block of audio with the main recogniser and the switch ears."""
        recognizer, ears = self._recognizer, list(self._ears)
        if self._muted():
            recognizer.Reset()
            for ear in ears:
                ear.reset()
            self._release()
            self.level = 0.0
            self.partial = ""
            return
        samples = struct.unpack("<%dh" % (len(data) // 2), data)
        rms = math.sqrt(sum(s * s for s in samples) / max(len(samples), 1))
        self.level = min(1.0, rms / 6000.0)
        # The ears only run around speech, so they cost almost nothing in silence.
        if self.level >= VOICE_LEVEL:
            self._voice_until = now + SWITCH_HANGOVER
        for ear in ears:
            if now < self._voice_until:
                found = ear.accept(data)
            elif ear.active:
                found = ear.finish()
            else:
                continue
            if found:
                self.switches.put((found[0], ear.language, found[1]))
                self._held = None
                self._suppress_until = now + HOLD_SECONDS
                for other in ears:
                    if other is not ear:
                        other.reset()
                break
        if recognizer.AcceptWaveform(data):
            self.partial = ""
            text = json.loads(recognizer.Result()).get("text", "")
            text = text.replace("[unk]", "").strip()
            if text:
                if now < self._suppress_until:
                    self._suppress_until = 0.0  # an ear already took this sentence
                elif any(ear.active and ear.heard_wake for ear in ears):
                    self._release()
                    self._held = (text, now + HOLD_SECONDS)
                else:
                    self.texts.put(text)
        else:
            partial = json.loads(recognizer.PartialResult()).get("partial", "")
            self.partial = partial.replace("[unk]", "").strip()
        if self._held and (now >= self._held[1] or
                           not any(ear.active and ear.heard_wake for ear in ears)):
            self._release()

    def _release(self):
        if self._held:
            self.texts.put(self._held[0])
            self._held = None

    def _get_model(self, language, Model):
        with self._models_lock:
            if language not in self._models:
                self._models[language] = Model(str(model_path(language)))
            return self._models[language]

    def _load(self, Model, KaldiRecognizer):
        """Load the model for `language` (English if it is missing); None if none exists."""
        wanted = self.language
        used = wanted if model_path(wanted).is_dir() else "en"
        if not model_path(used).is_dir():
            self.status = "voice model missing"
            return None
        if used != self.active_language:
            self.status = "loading"
            self._model = self._get_model(used, Model)
        self._loaded_for, self.active_language = wanted, used
        if self.free_form:
            return KaldiRecognizer(self._model, SAMPLE_RATE)
        return KaldiRecognizer(self._model, SAMPLE_RATE,
                               json.dumps(vocabulary(used) + ["[unk]"], ensure_ascii=False))

    def ear_languages(self):
        """Other installed languages that have switch phrases (at most SWITCH_EARS)."""
        found = [code for code in MODELS
                 if code != self.active_language and model_path(code).is_dir()
                 and languages.get(code).switch_phrases]
        return found[:SWITCH_EARS]

    def _start_ears(self, Model, KaldiRecognizer):
        """Load the switch ears in the background, so listening starts straight away."""
        active = self.active_language

        def load():
            ears = self._make_ears(Model, KaldiRecognizer)
            if active == self.active_language and self._loaded_for == self.language:
                self._ears = ears
        threading.Thread(target=load, daemon=True, name="luna-switch-ears").start()

    def _make_ears(self, Model, KaldiRecognizer):
        ears = []
        for code in self.ear_languages():
            try:
                grammar = sorted(switch_vocabulary(code)) + ["[unk]"]
                recognizer = KaldiRecognizer(self._get_model(code, Model), SAMPLE_RATE,
                                             json.dumps(grammar, ensure_ascii=False))
                ears.append(SwitchEar(code, recognizer))
            except Exception as exc:  # a broken model must not stop listening
                print("Luna listening: no switch ear for %s (%s)" % (code, type(exc).__name__),
                      flush=True)
        keep = {self.active_language} | {ear.language for ear in ears}
        with self._models_lock:  # free models nobody uses any more
            for code in list(self._models):
                if code not in keep:
                    del self._models[code]
        return ears
