"""Non-blocking offline speech output for Luna (Windows, Raspberry Pi OS, Linux)."""
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_quiet_until = 0.0
enabled = True

# Keep the microphone muted slightly after playback so Luna does not hear her own echo.
TAIL_SECONDS = 0.8


def is_speaking():
    return _lock.locked() or time.monotonic() < _quiet_until


def _run_windows(message):
    script = ("Add-Type -AssemblyName System.Speech;"
              "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
              "$s.Speak([Console]::In.ReadToEnd())")
    subprocess.run(["powershell", "-NoProfile", "-Command", script],
                   input=message, text=True, check=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _run_linux(message):
    player = next((p for p in ("pw-play", "paplay", "aplay") if shutil.which(p)), None)
    if player is None:
        raise OSError("no audio player found (pw-play, paplay or aplay)")
    with tempfile.TemporaryDirectory(prefix="luna-tts-") as folder:
        wav = Path(folder) / "reply.wav"
        subprocess.run(["espeak-ng", "-w", str(wav), message], check=True)
        subprocess.run([player, str(wav)], check=True)


def speak(message):
    global _quiet_until
    if not enabled or not _lock.acquire(blocking=False):
        return
    try:
        if os.name == "nt":
            _run_windows(message)
        else:
            _run_linux(message)
    except (OSError, subprocess.CalledProcessError) as error:
        print("Luna speech error:", error, flush=True)
    finally:
        _quiet_until = time.monotonic() + TAIL_SECONDS
        _lock.release()


def speak_async(message):
    threading.Thread(target=speak, args=(message,), daemon=True).start()
