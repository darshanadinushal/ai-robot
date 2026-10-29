"""Luna's devices behind small interfaces, so the core runs with or without hardware.

    hw = hardware.create("mock")       # or "real"; None reads LUNA_HARDWARE (default real)
    hw.microphone, hw.speaker, hw.camera, hw.motors, hw.audio

"real" uses the system microphone (sounddevice) and speaker (pw-play/paplay/aplay, winsound
on Windows); Luna has no camera or motors yet, so those are absent (available=False).
"mock" needs no devices at all: a microphone you can feed audio, a speaker that records what
it plays, a test-pattern camera and motors that record commands. Apps may pass their own
`Hardware` with other adapters. Pure Python: no pygame, and device libraries load lazily.
"""
import logging
import os

from ..audio_devices import AudioSetup
from .base import (Camera, Frame, Hardware, HardwareUnavailable, Microphone, Motors, NoCamera,
                   NoMotors, Speaker)
from .mock import MockCamera, MockMicrophone, MockMotors, MockSpeaker
from .real import SoundDeviceMicrophone, SystemSpeaker

MODES = ("real", "mock")
log = logging.getLogger("luna.hardware")


def mode_from_env(env=None):
    """LUNA_HARDWARE: "real" (default) or "mock"."""
    value = (os.environ if env is None else env).get("LUNA_HARDWARE", "").strip().lower()
    if value in ("", "auto"):
        return "real"
    if value not in MODES:
        log.warning("Luna hardware: unknown LUNA_HARDWARE=%r, using real hardware", value)
        return "real"
    return value


def create(mode=None, detect_audio=True):
    """The robot's devices. detect_audio=False skips finding the real microphone/speaker names."""
    mode = mode_from_env() if mode is None else mode
    if mode == "mock":
        return Hardware("mock", MockMicrophone(), MockSpeaker(), MockCamera(), MockMotors(),
                        AudioSetup("Mock microphone", "Mock speaker", None, False))
    if mode != "real":
        raise ValueError("hardware must be one of: " + ", ".join(MODES))
    if detect_audio:
        from .. import audio_devices
        audio = audio_devices.detect()
    else:
        audio = AudioSetup()
    return Hardware("real", SoundDeviceMicrophone(audio.mic_device, audio.mic), SystemSpeaker(),
                    NoCamera(), NoMotors(), audio)


__all__ = ["Camera", "Frame", "Hardware", "HardwareUnavailable", "Microphone", "Motors",
           "NoCamera", "NoMotors", "Speaker", "MockCamera", "MockMicrophone", "MockMotors",
           "MockSpeaker", "SoundDeviceMicrophone", "SystemSpeaker", "MODES", "create",
           "mode_from_env"]
