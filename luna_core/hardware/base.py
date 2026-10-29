"""The device interfaces every hardware adapter implements."""
from dataclasses import dataclass, field


class HardwareUnavailable(RuntimeError):
    """The robot has no such device (for example motors on the current Luna)."""


class Microphone:
    """16-bit mono PCM in blocks. `open` returns a context manager; while it is open,
    `callback(data, status)` is called from an audio thread for every block (status may be
    None or have `input_overflow`). It must never block."""
    name = ""

    def open(self, callback, samplerate, blocksize):
        raise NotImplementedError


class Speaker:
    """Plays one WAV file at a time: start(wav), wait() until it ends, stop() at once."""
    name = ""

    def start(self, wav):
        raise NotImplementedError

    def wait(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError


@dataclass(frozen=True)
class Frame:
    """One camera picture: RGB bytes, width * height * 3 long."""
    width: int
    height: int
    pixels: bytes = field(repr=False)


class Camera:
    available = False

    def capture(self):
        """The latest Frame, or None if there is no picture."""
        return None

    def close(self):
        pass


class Motors:
    """Two drive motors. Speeds are -1.0 (full reverse) to 1.0 (full forward)."""
    available = False

    def drive(self, left, right):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def close(self):
        self.stop()


class NoCamera(Camera):
    """No camera fitted: never a picture."""


class NoMotors(Motors):
    """No motors fitted: driving is an error, stopping is always safe."""

    def drive(self, left, right):
        raise HardwareUnavailable("this robot has no motors")

    def stop(self):
        pass


def clamp_speed(value):
    return max(-1.0, min(1.0, float(value)))


@dataclass
class Hardware:
    """The devices one robot uses. `audio` holds the names shown in Settings."""
    mode: str
    microphone: Microphone
    speaker: Speaker
    camera: Camera
    motors: Motors
    audio: object = None

    def close(self):
        """Stop moving and release the devices (safe to call twice)."""
        for device in (self.motors, self.camera):
            try:
                device.close()
            except Exception:
                pass
