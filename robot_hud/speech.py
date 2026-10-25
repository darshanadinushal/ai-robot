"""Non-blocking offline speech output for Luna (Windows, Raspberry Pi OS, Linux).

Messages are queued and spoken one after another by a single worker thread.
`stop()` cancels queued messages and interrupts the current one.
"""
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

enabled = True

# Keep the microphone muted slightly after playback so Luna does not hear her own echo.
TAIL_SECONDS = 0.8
MAX_SPOKEN_CHARS = 700

_queue = queue.Queue()
_busy = threading.Event()
_proc_lock = threading.Lock()
_current = None
_generation = 0
_quiet_until = 0.0
_worker = None


def is_speaking():
    return _busy.is_set() or time.monotonic() < _quiet_until


def is_talking():
    """True only while messages are queued or playing (no echo tail)."""
    return _busy.is_set()


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


def _run(cmd, generation, text=None):
    """Run one audio command; returns quietly if stop() interrupted it."""
    global _current
    with _proc_lock:
        if generation != _generation:
            return
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE if text is not None else subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _current = proc
    _, err = proc.communicate(text)
    with _proc_lock:
        _current = None
    if proc.returncode and generation == _generation:
        raise OSError("%s failed: %s" % (cmd[0], (err or "").strip()[:120]))


def _say_windows(message, generation):
    script = ("Add-Type -AssemblyName System.Speech;"
              "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
              "$s.Speak([Console]::In.ReadToEnd())")
    _run(["powershell", "-NoProfile", "-Command", script], generation, message)


def _say_linux(message, generation):
    player = next((p for p in ("pw-play", "paplay", "aplay") if shutil.which(p)), None)
    if player is None:
        raise OSError("no audio player found (pw-play, paplay or aplay)")
    with tempfile.TemporaryDirectory(prefix="luna-tts-") as folder:
        wav = Path(folder) / "reply.wav"
        # Text goes through stdin so it can never be parsed as a command-line option.
        _run(["espeak-ng", "-w", str(wav), "--stdin"], generation, message)
        if wav.exists():
            _run([player, str(wav)], generation)


def speak(message, generation=None):
    """Speak now and block until finished (or until stop() is called)."""
    global _quiet_until
    message = clean_for_speech(message)
    if generation is None:
        generation = _generation
    if not enabled or not message or generation != _generation:
        return
    try:
        if os.name == "nt":
            _say_windows(message, generation)
        else:
            _say_linux(message, generation)
    except OSError as error:
        print("Luna speech error:", error, flush=True)
    finally:
        _quiet_until = time.monotonic() + TAIL_SECONDS


def _work():
    while True:
        generation, message = _queue.get()
        try:
            speak(message, generation)
        finally:
            if _queue.empty():
                _busy.clear()


def speak_async(message):
    """Queue a message; Luna speaks queued messages in order."""
    global _worker
    if not enabled or not str(message).strip():
        return
    _busy.set()
    _queue.put((_generation, message))
    if _worker is None:
        _worker = threading.Thread(target=_work, daemon=True)
        _worker.start()


def stop():
    """Cancel queued speech and interrupt the current message."""
    global _generation
    with _proc_lock:
        _generation += 1  # queued messages from older generations are skipped
        if _current is not None and _current.poll() is None:
            _current.kill()
