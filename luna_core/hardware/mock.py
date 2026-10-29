"""Devices for running Luna without hardware (tests, CI, a laptop without a microphone)."""
import queue
import threading
import wave

from .base import Camera, Frame, Microphone, Motors, Speaker, clamp_speed


class MockMicrophone(Microphone):
    """Delivers fed PCM (16-bit mono) at real-time pace, silence otherwise."""
    name = "Mock microphone"

    def __init__(self, realtime=True):
        self.realtime = realtime
        self._fed = queue.Queue()
        self.blocks_sent = 0
        self.is_open = False

    def feed(self, pcm):
        """Queue audio for the listener, as if it was spoken into the microphone."""
        self._fed.put(bytes(pcm))

    def open(self, callback, samplerate, blocksize):
        return _MockStream(self, callback, samplerate, blocksize)


class _MockStream:
    def __init__(self, mic, callback, samplerate, blocksize):
        self._mic, self._callback = mic, callback
        self._bytes = blocksize * 2
        self._seconds = blocksize / float(samplerate)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="mock-microphone", daemon=True)

    def __enter__(self):
        self._mic.is_open = True
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(2)
        self._mic.is_open = False
        return False

    def _run(self):
        pending = b""
        while not self._stop.is_set():
            while len(pending) < self._bytes:
                try:
                    pending += self._mic._fed.get_nowait()
                except queue.Empty:
                    break
            block, pending = pending[:self._bytes], pending[self._bytes:]
            self._callback(block.ljust(self._bytes, b"\0"), None)
            self._mic.blocks_sent += 1
            if self._mic.realtime:
                self._stop.wait(self._seconds)


class MockSpeaker(Speaker):
    """Records every WAV it is asked to play; wait() lasts as long as the audio (realtime)."""
    name = "Mock speaker"

    def __init__(self, realtime=True):
        self.realtime = realtime
        self.played = []  # (file name, seconds)
        self._done = threading.Event()
        self._seconds = 0.0

    def start(self, wav):
        with wave.open(str(wav), "rb") as handle:
            self._seconds = handle.getnframes() / float(handle.getframerate() or 1)
        self.played.append((str(wav), self._seconds))
        self._done.clear()

    def wait(self):
        if self.realtime:
            self._done.wait(self._seconds)
        self._done.set()

    def stop(self):
        self._done.set()


class MockCamera(Camera):
    """A grey test picture."""
    available = True

    def __init__(self, width=320, height=240):
        self.width, self.height = width, height
        self.captures = 0

    def capture(self):
        self.captures += 1
        return Frame(self.width, self.height, bytes([128]) * (self.width * self.height * 3))


class MockMotors(Motors):
    """Records every command; `speeds` is what the motors would be doing now."""
    available = True

    def __init__(self):
        self.commands = []
        self.speeds = (0.0, 0.0)

    def drive(self, left, right):
        self.speeds = (clamp_speed(left), clamp_speed(right))
        self.commands.append(("drive",) + self.speeds)

    def stop(self):
        self.speeds = (0.0, 0.0)
        self.commands.append(("stop",))
