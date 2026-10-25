"""Optional cloud transcription (OpenAI) for long questions. OFF by default.

This is an interface for a later phase: nothing in Luna calls it yet, and the offline Vosk
recogniser stays the only listener. Privacy rules built in:

- Off unless LUNA_CLOUD_TRANSCRIPTION=on is set explicitly (plus an OpenAI API key).
- Audio is only uploaded for an utterance authorised by the offline wake word ("Luna"),
  confirmed locally by Vosk. Background audio before the wake word is never uploaded.
- PreRoll keeps only the last PREROLL_SECONDS in memory, so the start of an authorised
  question ("Luna, what...") is not lost; it is cleared when it is not used.
- Audio stays in memory (no files), is limited to MAX_SECONDS and is discarded after the
  upload, on cancellation and on any error.
- Every failure returns None, so the caller keeps the offline Vosk text (fallback).

Model: LUNA_TRANSCRIBE_MODEL (default below); check OpenAI's current speech-to-text model
names before enabling.
"""
import io
import os
import threading
import wave
from collections import deque

DEFAULT_MODEL = "gpt-4o-mini-transcribe"
SAMPLE_RATE = 16000
PREROLL_SECONDS = 3.0
MAX_SECONDS = 45.0
TIMEOUT_SECONDS = 15.0
_ON = ("1", "on", "true", "yes")


def cloud_requested():
    """True only when the user explicitly turned cloud transcription on."""
    return os.environ.get("LUNA_CLOUD_TRANSCRIPTION", "").strip().lower() in _ON


def _seconds(pcm):
    return len(pcm) / 2 / SAMPLE_RATE


class PreRoll:
    """The last few seconds of microphone audio, in memory only (16 kHz mono int16)."""

    def __init__(self, seconds=PREROLL_SECONDS):
        self.limit = int(seconds * SAMPLE_RATE * 2)
        self._blocks = deque()
        self._size = 0

    def add(self, block):
        block = bytes(block)
        self._blocks.append(block)
        self._size += len(block)
        while self._size > self.limit and self._blocks:
            self._size -= len(self._blocks.popleft())

    def take(self):
        """The buffered audio, then forget it."""
        data = b"".join(self._blocks)
        self.clear()
        return data

    def clear(self):
        self._blocks.clear()
        self._size = 0


class CloudUtterance:
    """Audio of one wake-word-authorised utterance, kept in memory until transcribed."""

    def __init__(self, transcriber, preroll_audio=b""):
        self._transcriber = transcriber
        self._audio = bytearray(preroll_audio[-int(MAX_SECONDS * SAMPLE_RATE * 2):])
        self.cancelled = threading.Event()
        self.too_long = False

    def add(self, block):
        if self.cancelled.is_set() or self.too_long:
            return
        if _seconds(self._audio) + _seconds(block) > MAX_SECONDS:
            self.too_long = True
            return
        self._audio.extend(block)

    def cancel(self):
        self.cancelled.set()
        self._audio = bytearray()

    def finish(self, language):
        """Transcribed text, or None (use the offline Vosk text). Always drops the audio."""
        audio, self._audio = bytes(self._audio), bytearray()
        if self.cancelled.is_set() or not audio:
            return None
        return self._transcriber.transcribe(audio, language, self.cancelled)


class CloudTranscriber:
    def __init__(self, client=None, model=None, timeout=TIMEOUT_SECONDS):
        self._client = client
        self.model = model or os.environ.get("LUNA_TRANSCRIBE_MODEL", DEFAULT_MODEL)
        self.timeout = timeout

    @property
    def enabled(self):
        return cloud_requested() and (self._client is not None
                                      or bool(os.environ.get("OPENAI_API_KEY")))

    def begin(self, preroll, wake_word_confirmed):
        """Start collecting an utterance; None unless enabled and authorised by the wake word."""
        if not (self.enabled and wake_word_confirmed):
            preroll.clear()
            return None
        return CloudUtterance(self, preroll.take())

    def transcribe(self, pcm, language, cancelled=None):
        if not self.enabled or not pcm or (cancelled and cancelled.is_set()):
            return None
        wav = io.BytesIO()
        with wave.open(wav, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(SAMPLE_RATE)
            out.writeframes(pcm)
        wav.name = "speech.wav"
        wav.seek(0)
        try:
            client = self._client
            if client is None:
                from openai import OpenAI  # optional package
                client = self._client = OpenAI(timeout=self.timeout, max_retries=0)
            result = client.audio.transcriptions.create(model=self.model, file=wav,
                                                        language=language)
        except Exception as error:  # offline, timeout, bad key...: keep the Vosk text
            print("Luna cloud transcription failed (%s); using offline text"
                  % type(error).__name__, flush=True)
            return None
        finally:
            wav.close()
        if cancelled and cancelled.is_set():
            return None
        text = " ".join(str(getattr(result, "text", "") or "").split())
        return text or None
