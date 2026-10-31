"""Optional online listening: OpenAI speech-to-text for questions to Luna. OFF by default.

Vosk stays Luna's listener: it hears the wake word and the commands and decides when an
utterance has ended. Only when the user turned "Online listening" on in Settings (and the AI
is set up) is an utterance that Vosk routed to the AI -- so it began with the wake word or
came in the follow-up window -- sent once more, as audio, for a more accurate transcript.
Privacy rules:

- AudioRing keeps recent microphone audio in memory only (never in files): at most
  RING_SECONDS, cut back to PREROLL_SECONDS while nobody is speaking, and cleared while Luna
  speaks. The VoiceListener only fills it while online listening is on.
- Commands, the wake word alone and background talk never leave the robot.
- One upload per question, in its own thread with a timeout; the audio is dropped after it.
  On any error, timeout or cancel the offline Vosk text is used (fallback).

Model: LUNA_TRANSCRIBE_MODEL (default gpt-transcribe); LUNA_TRANSCRIBE_TIMEOUT (seconds).
Each upload's audio minutes are reported to the usage meter (`listen`); the app sends nothing
once the daily AI budget is used up.
"""
import io
import logging
import os
import threading
import wave
from collections import deque
from dataclasses import dataclass

from . import languages
from .naming import personalise

log = logging.getLogger("luna.listening")

DEFAULT_MODEL = "gpt-transcribe"
SAMPLE_RATE = 16000
PREROLL_SECONDS = 2.0  # audio kept from before Vosk showed the first words
RING_SECONDS = 50.0    # longer than the longest utterance (listening.MAX_UTTERANCE_SECONDS)
MIN_SECONDS = 0.3      # less audio than this is not uploaded


def _env_seconds(name, default):
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        return default
    return value if value > 0 else default


TIMEOUT_SECONDS = _env_seconds("LUNA_TRANSCRIBE_TIMEOUT", 8.0)
_NOT_ALLOWED = str.maketrans("", "", "<>\r\n")  # the API rejects these in hints


def seconds(pcm):
    return len(pcm) / 2 / SAMPLE_RATE


class AudioRing:
    """Recent microphone blocks (16 kHz mono int16) with the time each was heard.

    Filled by the listening thread, read by the UI thread; memory only.
    """

    def __init__(self, limit_seconds=RING_SECONDS):
        self.limit = int(limit_seconds * SAMPLE_RATE * 2)
        self._blocks = deque()  # (monotonic time, bytes)
        self._size = 0
        self._lock = threading.Lock()

    def add(self, block, stamp):
        block = bytes(block)
        with self._lock:
            self._blocks.append((stamp, block))
            self._size += len(block)
            while self._size > self.limit and self._blocks:
                self._size -= len(self._blocks.popleft()[1])

    def since(self, start):
        """The audio heard at or after `start` (monotonic seconds)."""
        with self._lock:
            return b"".join(block for stamp, block in self._blocks if stamp >= start)

    def trim(self, before):
        """Forget audio heard before `before`."""
        with self._lock:
            while self._blocks and self._blocks[0][0] < before:
                self._size -= len(self._blocks.popleft()[1])

    def clear(self):
        with self._lock:
            self._blocks.clear()
            self._size = 0

    @property
    def seconds(self):
        return self._size / 2 / SAMPLE_RATE


def _hint(text):
    return " ".join(personalise(str(text)).translate(_NOT_ALLOWED).split())


def request_options(model, language):
    """Arguments for audio.transcriptions.create: model, language and the pack's hints."""
    pack = languages.get(language)
    options = {"model": model}
    prompt = _hint(pack.transcription_prompt)
    if prompt:
        options["prompt"] = prompt
    if model.startswith("gpt-transcribe"):
        # gpt-transcribe: `languages` replaces `language`; keywords are literal terms.
        extra = {"languages": [language]}
        keywords = [k for k in (_hint(k) for k in pack.transcription_keywords) if k]
        if keywords:
            extra["keywords"] = keywords
        options["extra_body"] = extra
    else:
        options["language"] = language  # older models
    return options


def wav_file(pcm):
    data = io.BytesIO()
    with wave.open(data, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(pcm)
    data.name = "speech.wav"
    data.seek(0)
    return data


class CloudTranscriber:
    def __init__(self, client=None, model=None, timeout=None, meter=None):
        self._client = client
        self.meter = meter  # tasks.budget.UsageMeter or None
        self.model = model or os.environ.get("LUNA_TRANSCRIBE_MODEL", "").strip() or DEFAULT_MODEL
        self.timeout = TIMEOUT_SECONDS if timeout is None else timeout

    def transcribe(self, pcm, language):
        """The transcript (may be ""); raises on any problem, so the caller falls back."""
        wav = wav_file(pcm)
        try:
            client = self._client
            if client is None:
                from openai import OpenAI  # optional package
                client = self._client = OpenAI(timeout=self.timeout, max_retries=0)
            result = client.audio.transcriptions.create(file=wav,
                                                        **request_options(self.model, language))
        finally:
            wav.close()
        if self.meter is not None:
            try:
                self.meter.add_audio("listen", self.model, seconds(pcm))
            except Exception as error:  # never lose the transcript over the meter
                log.warning("Luna listening: usage not counted (%s)", type(error).__name__)
        return " ".join(str(getattr(result, "text", "") or "").split())


class _Job:
    """One upload in its own thread; the UI thread only reads `done`, `text` and `error`."""

    def __init__(self, transcriber, pcm, language):
        self.done = threading.Event()
        self.text, self.error = "", ""
        threading.Thread(target=self._run, args=(transcriber, pcm, language),
                         daemon=True).start()

    def _run(self, transcriber, pcm, language):
        try:
            self.text = transcriber.transcribe(pcm, language)
        except Exception as error:  # offline, timeout, bad key, unknown model...
            self.error = type(error).__name__  # never the message (it may contain details)
        finally:
            self.done.set()


@dataclass(frozen=True)
class Heard:
    """What to route for an utterance: the online text, or Vosk's text as the fallback."""
    utterance: object  # listening.Utterance (Vosk's text and timing)
    text: str
    online: bool
    note: str          # for the log (never contains the text)


class OnlineListening:
    """UI-thread side: one transcription at a time. Not thread-safe except `busy`.

    start() returns a Heard at once when nothing is uploaded; otherwise poll() returns it when
    the transcript arrives, fails or times out. cancel() forgets the question (a late result
    is ignored; the thread ends on its own).
    """

    def __init__(self, transcriber=None, timeout=None, meter=None):
        self.transcriber = transcriber or CloudTranscriber(meter=meter)
        self.timeout = self.transcriber.timeout if timeout is None else timeout
        self._job = None
        self._waiting = None  # (utterance, started at, audio seconds)

    @property
    def busy(self):
        return self._waiting is not None

    def start(self, utterance, audio, language, t):
        self.cancel()
        length = seconds(audio)
        if length < MIN_SECONDS:
            return Heard(utterance, utterance.text, False, "no audio to send")
        self._job = _Job(self.transcriber, audio, language)
        self._waiting = (utterance, t, length)
        return None

    def poll(self, t):
        if self._waiting is None:
            return None
        utterance, started, length = self._waiting
        job = self._job
        if job.done.is_set():
            self.cancel()
            if job.error:
                return Heard(utterance, utterance.text, False, "failed (%s)" % job.error)
            if not job.text:
                return Heard(utterance, utterance.text, False, "no words found")
            return Heard(utterance, job.text, True, "%.1f s of audio, text after %.1f s"
                         % (length, t - started))
        if t - started >= self.timeout + 1.0:
            self.cancel()
            return Heard(utterance, utterance.text, False, "no answer in %.0f s" % self.timeout)
        return None

    def cancel(self):
        self._job = None
        self._waiting = None
