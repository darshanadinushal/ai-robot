"""Adapters for the real microphone and speaker (device libraries load only when used)."""
import logging

from .base import Microphone, Speaker

log = logging.getLogger("luna.hardware")


class SoundDeviceMicrophone(Microphone):
    """The microphone through sounddevice. device: an index or name (audio_devices); None is
    the system default, which is also used when the chosen device cannot be opened."""

    def __init__(self, device=None, name=""):
        self.device = device
        self.name = name or "default"

    def open(self, callback, samplerate, blocksize):
        import sounddevice  # ImportError: the voice packages are missing

        def on_block(data, frames, time_info, status):
            callback(data, status)

        return self._open(sounddevice, on_block, samplerate, blocksize)

    def _open(self, sounddevice, callback, samplerate=16000, blocksize=1600):
        options = dict(samplerate=samplerate, blocksize=blocksize, dtype="int16", channels=1,
                       callback=callback)
        if self.device is not None:
            try:
                return sounddevice.RawInputStream(device=self.device, **options)
            except Exception as exc:
                log.warning("Luna voice: microphone %r failed (%s); using the default",
                            self.device, type(exc).__name__)
                self.device = None
        return sounddevice.RawInputStream(**options)


class SystemSpeaker(Speaker):
    """The system speaker: winsound on Windows, pw-play / paplay / aplay elsewhere
    (LUNA_AUDIO_PLAYER overrides). The player is made on the first sentence."""
    name = "system"

    def __init__(self):
        self._player = None

    def _get(self):
        if self._player is None:
            from ..speech import default_player
            self._player = default_player()
        return self._player

    def start(self, wav):
        self._get().start(wav)

    def wait(self):
        self._get().wait()

    def stop(self):
        if self._player is not None:
            self._player.stop()
