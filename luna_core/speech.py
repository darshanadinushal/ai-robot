"""Luna's speech output: natural Piper voices with a basic eSpeak / Windows voice as fallback.

Pipeline (all in background threads, never on the touchscreen thread):

    speak_async(text) -> messages -> PREPARER: split into sentences, make a WAV per sentence
                                     with the active voice (at most READY_AHEAD sentences ahead)
                      -> ready    -> PLAYER: play each WAV, then delete it

The Piper voice is loaded once and kept in memory. stop() is immediate: it kills the sound that
is playing, empties both queues and makes every older message stale. set_voice() loads a voice
in the background and switches only after it loaded and spoke a test word; if that fails Luna
uses the basic voice of the same language and gender and voice_state() says why.
"""
import atexit
import itertools
import logging
import os
import queue
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

from . import languages
from . import voices as catalogue

log = logging.getLogger("luna.speech")

enabled = True

# Keep the microphone muted slightly after playback so Luna does not hear her own echo.
TAIL_SECONDS = 0.8
SPEECH_WPM = 150  # basic espeak-ng voice speed; 150 sounds clear on the Pi
MAX_SPOKEN_CHARS = 700
READY_AHEAD = 2  # sentences prepared while one plays (limits CPU and memory on the Pi)
MIN_SENTENCE_CHARS = 20  # shorter pieces are joined with the next sentence
MAX_SENTENCE_CHARS = 250
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class VoiceLoadError(Exception):
    """A natural voice could not be used; the message is shown to the user."""


def clean_for_speech(text, limit=MAX_SPOKEN_CHARS):
    """Strip markdown that sounds odd when read aloud and keep answers short."""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", str(text))
    text = re.sub(r"(?m)^\s*(?:[-*+•]|\d+[.)])\s+", "", text)
    text = re.sub(r"[*_#`>|~]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        cut = text[:limit]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        text = cut[:end + 1] if end > limit // 3 else cut.rsplit(" ", 1)[0] + "."
    return text


def split_sentences(text, min_chars=MIN_SENTENCE_CHARS, max_chars=MAX_SENTENCE_CHARS):
    """Sentences to speak one by one. Very short pieces are joined, very long ones are cut."""
    pieces = []
    for part in re.split(r"(?<=[.!?…])\s+", text.strip()):
        part = part.strip()
        while len(part) > max_chars:
            cut = part.rfind(", ", 0, max_chars) + 1
            if cut < max_chars // 3:
                cut = part.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            pieces.append(part[:cut].strip())
            part = part[cut:].strip()
        if part:
            pieces.append(part)
    sentences = []
    for piece in pieces:
        if sentences and len(sentences[-1]) < min_chars:
            sentences[-1] += " " + piece
        else:
            sentences.append(piece)
    return sentences


_SENTENCE_END = re.compile(r"[.!?…]+[\"')\]]*\s+")


class SentenceStream:
    """Turns text that arrives in pieces (a streamed AI answer) into whole sentences.

    feed() returns the sentences completed by a piece, flush() the rest at the end. Every
    character is returned at most once and in order. A sentence only ends at . ! ? followed by
    a space, so "3.5" is never cut; short pieces are joined like split_sentences does. After
    `limit` characters the rest is dropped (same limit as one spoken message).
    """

    def __init__(self, min_chars=MIN_SENTENCE_CHARS, max_chars=MAX_SENTENCE_CHARS,
                 limit=MAX_SPOKEN_CHARS):
        self.min_chars, self.max_chars, self.limit = min_chars, max_chars, limit
        self._buffer = ""
        self._used = 0

    @property
    def full(self):
        return self._used >= self.limit

    def feed(self, piece):
        self._buffer += str(piece or "")
        out = []
        while not self.full:
            sentence = self._next()
            if sentence is None:
                break
            self._emit(sentence, out)
        return out

    def flush(self):
        out = []
        rest, self._buffer = self._buffer.strip(), ""
        if rest and not self.full:
            for sentence in split_sentences(rest, self.min_chars, self.max_chars):
                self._emit(sentence, out)
        return out

    def _next(self):
        for match in _SENTENCE_END.finditer(self._buffer):
            if len(self._buffer[:match.end()].strip()) >= self.min_chars:
                sentence, self._buffer = self._buffer[:match.end()], self._buffer[match.end():]
                return sentence.strip()
        if len(self._buffer) > self.max_chars:
            cut = self._buffer.rfind(", ", 0, self.max_chars) + 1
            if cut < self.max_chars // 3:
                cut = self._buffer.rfind(" ", 0, self.max_chars)
            if cut <= 0:
                cut = self.max_chars
            sentence, self._buffer = self._buffer[:cut], self._buffer[cut:]
            return sentence.strip()
        return None

    def _emit(self, sentence, out):
        if not sentence or self.full:
            return
        room = self.limit - self._used
        if len(sentence) > room:
            sentence = clean_for_speech(sentence, room) if room > 20 else ""
            self._used = self.limit
        else:
            self._used += len(sentence) + 1
        if sentence:
            out.append(sentence)


# ---------------------------------------------------------------- voices (text -> WAV file)

class PiperSynth:
    """A natural voice, loaded once with Piper's Python API and kept in memory."""

    natural = True

    def __init__(self, voice, folder=None):
        if not catalogue.is_installed(voice, folder):
            raise VoiceLoadError("natural voice not installed (run robot-setup)")
        try:
            from piper import PiperVoice
        except ImportError:
            raise VoiceLoadError("Piper is not installed (run robot-setup)") from None
        try:
            self._piper = PiperVoice.load(str(catalogue.model_path(voice, folder)))
        except Exception as error:
            raise VoiceLoadError("could not load %s (%s)" % (voice.model, type(error).__name__))
        self.voice = voice

    def synthesize(self, text, wav):
        with wave.open(str(wav), "wb") as handle:
            self._piper.synthesize_wav(text, handle)


WINDOWS_TTS = (
    "[Console]::InputEncoding = [Text.Encoding]::UTF8;"
    "Add-Type -AssemblyName System.Speech;"
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
    "try { $s.SelectVoiceByHints([Enum]::Parse([System.Speech.Synthesis.VoiceGender], $env:LUNA_GENDER),"
    " [System.Speech.Synthesis.VoiceAge]::Adult, 0,"
    " [Globalization.CultureInfo]::new($env:LUNA_CULTURE)) } catch {};"
    "$s.SetOutputToWaveFile($env:LUNA_WAV);"
    "$s.Speak([Console]::In.ReadToEnd());"
    "$s.Dispose()"
)


class BasicSynth:
    """The always-available voice: espeak-ng on Linux, the built-in voice on Windows."""

    natural = False

    def __init__(self, voice):
        self.voice = voice

    def synthesize(self, text, wav):
        env = None
        if os.name == "nt":
            cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", WINDOWS_TTS]
            env = {**os.environ, "LUNA_WAV": str(wav), "LUNA_GENDER": self.voice.gender.title(),
                   "LUNA_CULTURE": languages.get(self.voice.language).windows_culture}
        else:
            # Text goes through stdin so it can never be parsed as a command-line option.
            cmd = ["espeak-ng", "-v", self.voice.espeak, "-s", str(SPEECH_WPM),
                   "-w", str(wav), "--stdin"]
        try:
            proc = subprocess.run(cmd, input=text, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", env=env, timeout=60,
                                  creationflags=NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise OSError("%s failed: %s" % (cmd[0], type(error).__name__)) from None
        if proc.returncode or not Path(wav).is_file():
            raise OSError("%s failed: %s" % (cmd[0], (proc.stderr or "").strip()[:120]))


# ---------------------------------------------------------------- players (WAV file -> speaker)

class CommandPlayer:
    """Plays a WAV with pw-play / paplay / aplay (or LUNA_AUDIO_PLAYER). stop() kills it."""

    def __init__(self, command=None):
        custom = os.environ.get("LUNA_AUDIO_PLAYER", "")
        if command is None and custom.strip():
            command = shlex.split(custom)
        if command is None:
            found = next((p for p in ("pw-play", "paplay", "aplay") if shutil.which(p)), None)
            command = [found] if found else None
        self.command = command
        self._proc = None

    def start(self, wav):
        if not self.command:
            raise OSError("no audio player found (pw-play, paplay or aplay)")
        self._proc = subprocess.Popen(self.command + [str(wav)], stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def wait(self):
        proc = self._proc
        if proc is not None:
            _, err = proc.communicate()
            if proc.returncode > 0:
                log.error("Luna audio error: %s", (err or b"").decode(errors="replace").strip()[:160])

    def stop(self):
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()


class WindowsPlayer:
    """Plays a WAV with winsound in the background; stop() silences it at once."""

    def __init__(self):
        import winsound
        self._winsound = winsound
        self._done = threading.Event()
        self._seconds = 0.0

    def start(self, wav):
        with wave.open(str(wav), "rb") as handle:
            self._seconds = handle.getnframes() / float(handle.getframerate() or 1)
        self._done.clear()
        flags = self._winsound.SND_FILENAME | self._winsound.SND_ASYNC | self._winsound.SND_NODEFAULT
        self._winsound.PlaySound(str(wav), flags)

    def wait(self):
        self._done.wait(self._seconds + 0.15)

    def stop(self):
        self._winsound.PlaySound(None, 0)
        self._done.set()


def default_player():
    return WindowsPlayer() if os.name == "nt" else CommandPlayer()


# ---------------------------------------------------------------- engine

class SpeechEngine:
    def __init__(self, make_natural=PiperSynth, make_basic=BasicSynth, make_player=default_player,
                 voice_key=catalogue.DEFAULT_VOICE):
        self._make_natural = make_natural
        self._make_basic = make_basic
        self._make_player = make_player
        self._lock = threading.RLock()
        self._generation = 0
        self._messages = queue.Queue()
        self._ready = queue.Queue(maxsize=READY_AHEAD)
        self._open = set()  # messages queued or playing (cleared by stop)
        self._ids = itertools.count(1)
        self._files = itertools.count(1)
        self._quiet_until = 0.0
        self._player = None
        self._threads = []
        self._folder = None
        self._voice = catalogue.get_voice(voice_key)
        self._synth = None
        self._load_request = 0
        self.state = "basic"  # "basic", "loading" or "natural"
        self.status = "Basic voice"

    # ---- public API
    def is_talking(self):
        """True only while messages are queued or playing (no echo tail)."""
        return bool(self._open)

    def is_speaking(self):
        return self.is_talking() or time.monotonic() < self._quiet_until

    @property
    def voice(self):
        return self._voice

    def speak_async(self, message):
        text = clean_for_speech(message)
        if not text:
            return
        self._start_threads()
        with self._lock:
            message_id = next(self._ids)
            self._open.add(message_id)
            self._messages.put((self._generation, message_id, text))

    def stop(self):
        """Silence Luna now: stop the sound, cancel queued sentences and messages."""
        with self._lock:
            self._generation += 1
            self._open.clear()
            self._quiet_until = time.monotonic() + TAIL_SECONDS
            if self._player is not None:
                try:
                    self._player.stop()
                except OSError:
                    pass
        for pending in (self._messages, self._ready):
            while True:
                try:
                    item = pending.get_nowait()
                except queue.Empty:
                    break
                if pending is self._ready and item[2] is not None:
                    _remove(item[2])

    def set_voice(self, key):
        """Load a voice in the background; switch only when it works. Returns the thread."""
        voice = catalogue.get_voice(key)
        with self._lock:
            self._load_request += 1
            request = self._load_request
            self.state, self.status = "loading", "Loading %s..." % voice.label
        thread = threading.Thread(target=self._load, args=(voice, request), daemon=True,
                                  name="luna-voice-load")
        thread.start()
        return thread

    # ---- internals
    def _load(self, voice, request):
        synth, problem = None, None
        try:
            synth = self._make_natural(voice)
            warm = self._temp_wav()
            try:
                synth.synthesize(languages.get(voice.language).warm_up, warm)  # proves it works
            finally:
                _remove(warm)
        except VoiceLoadError as error:
            problem = str(error)
        except Exception as error:  # a broken voice must never stop Luna
            problem = "%s failed (%s)" % (voice.model, type(error).__name__)
        with self._lock:
            if request != self._load_request:
                return  # a newer choice was made meanwhile
            self._voice = voice
            if problem is None:
                self._synth = synth
                self.state, self.status = "natural", "Natural voice: " + voice.label
            else:
                self._synth = self._make_basic(voice)
                self.state, self.status = "basic", "Basic voice: " + problem
        log.info("Luna voice: %s", self.status)

    def _active_synth(self):
        with self._lock:
            if self._synth is None:
                self._synth = self._make_basic(self._voice)
            return self._synth

    def _natural_failed(self, synth, error):
        with self._lock:
            if self._synth is synth:
                self._synth = self._make_basic(synth.voice)
                self.state = "basic"
                self.status = "Basic voice: natural voice failed (%s)" % type(error).__name__
                log.info("Luna voice: %s", self.status)
            return self._synth

    def _stale(self, generation):
        return generation != self._generation

    def _temp_wav(self):
        with self._lock:
            if self._folder is None:
                self._folder = tempfile.mkdtemp(prefix="luna-tts-")
                atexit.register(shutil.rmtree, self._folder, True)
            return Path(self._folder) / ("s%d.wav" % next(self._files))

    def _start_threads(self):
        with self._lock:
            if self._threads:
                return
            self._player = self._make_player() if self._player is None else self._player
            for target, name in ((self._prepare_loop, "luna-speech-prepare"),
                                 (self._play_loop, "luna-speech-play")):
                thread = threading.Thread(target=target, daemon=True, name=name)
                thread.start()
                self._threads.append(thread)

    def _offer(self, item):
        """Put item on the ready queue unless its message was cancelled meanwhile."""
        while not self._stale(item[0]):
            try:
                self._ready.put(item, timeout=0.1)
                return True
            except queue.Full:
                pass
        return False

    def _make_wav(self, sentence):
        synth = self._active_synth()
        for _ in range(2):
            wav = self._temp_wav()
            try:
                synth.synthesize(sentence, wav)
                return wav
            except Exception as error:
                _remove(wav)
                if not getattr(synth, "natural", False):
                    log.error("Luna speech error: %s", error)
                    return None
                synth = self._natural_failed(synth, error)  # retry once with the basic voice
        return None

    def _prepare_loop(self):
        while True:
            generation, message_id, text = self._messages.get()
            for sentence in split_sentences(text):
                if self._stale(generation):
                    break
                wav = self._make_wav(sentence)
                if wav is not None and not self._offer((generation, message_id, wav)):
                    _remove(wav)
                    break
            self._offer((generation, message_id, None))  # end of this message

    def _play_loop(self):
        while True:
            generation, message_id, wav = self._ready.get()
            if wav is None:
                with self._lock:
                    self._open.discard(message_id)
                    self._quiet_until = time.monotonic() + TAIL_SECONDS
                continue
            try:
                with self._lock:  # stop() cannot slip between this check and the start
                    if self._stale(generation):
                        continue
                    self._player.start(wav)
                self._player.wait()
            except Exception as error:  # a playback problem must not end the player thread
                log.error("Luna speech error: %s", error)
            finally:
                with self._lock:
                    self._quiet_until = max(self._quiet_until, time.monotonic() + TAIL_SECONDS)
                _remove(wav)


def _remove(path):
    try:
        Path(path).unlink()
    except OSError:
        pass


# ---------------------------------------------------------------- module API used by the app

_engine = SpeechEngine()


def speak_async(message):
    """Queue a message; Luna speaks queued messages in order."""
    if enabled and str(message).strip():
        _engine.speak_async(message)


def stop():
    """Cancel queued speech and interrupt the current sentence immediately."""
    _engine.stop()


def is_talking():
    return _engine.is_talking()


def is_speaking():
    """Talking, or within TAIL_SECONDS after it (the microphone stays muted)."""
    return _engine.is_speaking()


def set_voice(key):
    """Choose the voice by catalogue key (e.g. "SV_FEMALE"); loads in the background."""
    if enabled:
        return _engine.set_voice(key)
    return None


def voice_state():
    """("natural" | "loading" | "basic", status text for the screen)."""
    return _engine.state, _engine.status
