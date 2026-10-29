"""Audio devices: the microphone Luna hears with, the speaker she talks through, and whether that
pair cancels her own voice (echo), e.g. a ReSpeaker XVF3800 array with the speaker on its amplifier.

Echo cancellation needs Luna's speech to play *through* the array (it uses what it plays as the
reference), so both the microphone and the speech speaker must be the array. A music speaker on
Bluetooth/AUX is not cancelled by it.

Environment (all optional):
  LUNA_MIC                       microphone: a sounddevice index or part of its name
  LUNA_ECHO_CANCELLING=on|off    override the detection (e.g. "on" for a headset with echo cancelling)
  LUNA_ECHO_CANCELLING_DEVICES   extra name parts, comma separated, that count as echo cancelling
"""
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

ECHO_CANCELLING_HINTS = ("xvf3800", "respeaker")


@dataclass(frozen=True)
class AudioSetup:
    mic: str = ""                 # names for Settings ("" = system default, unknown)
    speaker: str = ""
    mic_device: object = None     # given to sounddevice; None = the system default microphone
    echo_cancelling: bool = False


def hints(env):
    extra = [h.strip().lower() for h in env.get("LUNA_ECHO_CANCELLING_DEVICES", "").split(",")]
    return ECHO_CANCELLING_HINTS + tuple(h for h in extra if h)


def is_echo_cancelling(name, known=ECHO_CANCELLING_HINTS):
    name = (name or "").lower()
    return any(h in name for h in known)


def readable(name):
    """'alsa_input.usb-Seeed_reSpeaker_XVF3800-00.analog-stereo' -> 'Seeed reSpeaker XVF3800'."""
    name = (name or "").strip()
    for prefix in ("alsa_input.", "alsa_output.", "bluez_input.", "bluez_output."):
        if name.startswith(prefix):
            name = name[len(prefix):].split(".")[0]
            name = name[4:] if name.startswith("usb-") else name
            name = re.sub(r"[-_][0-9A-Fa-f]{6,}(-\d+)?$|-\d+$", "", name).replace("_", " ")
    return name[:60]


def choose(mic_name="", speaker_name="", env=None, input_names=()):
    """Pick the microphone and decide on echo cancellation from the device names.

    The system default microphone is kept unless LUNA_MIC names one, or the default is not an
    echo-cancelling array but one is plugged in (then that array is used).
    """
    env = os.environ if env is None else env
    known = hints(env)
    mic_device = None
    wanted = env.get("LUNA_MIC", "").strip()
    if wanted:
        mic_device = int(wanted) if wanted.isdigit() else wanted
        if not wanted.isdigit():
            mic_name = next((n for n in input_names if wanted.lower() in n.lower()), wanted)
        elif int(wanted) < len(input_names):
            mic_name = input_names[int(wanted)]
    elif not is_echo_cancelling(mic_name, known):
        found = next((n for n in input_names if is_echo_cancelling(n, known)), None)
        if found:
            mic_device = mic_name = found
    override = env.get("LUNA_ECHO_CANCELLING", "").strip().lower()
    if override in ("on", "1", "true", "yes"):
        echo = True
    elif override in ("off", "0", "false", "no"):
        echo = False
    else:
        echo = is_echo_cancelling(mic_name, known) and is_echo_cancelling(speaker_name, known)
    return AudioSetup(readable(mic_name), readable(speaker_name), mic_device, echo)


def _pactl(*args):
    if not shutil.which("pactl"):
        return ""
    try:
        return subprocess.run(["pactl", *args], capture_output=True, text=True,
                              timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def detect(env=None):
    """Read the current device names (PipeWire/PulseAudio defaults, else sounddevice)."""
    mic = speaker = ""
    inputs = []
    if os.name != "nt":
        mic, speaker = _pactl("get-default-source"), _pactl("get-default-sink")
    try:
        import sounddevice
        devices = sounddevice.query_devices()
        inputs = [d["name"] for d in devices if d.get("max_input_channels", 0) > 0]
        if not mic:
            mic = sounddevice.query_devices(kind="input")["name"]
        if not speaker:
            speaker = sounddevice.query_devices(kind="output")["name"]
    except Exception:  # sounddevice missing, no audio devices
        pass
    if mic in ("default", "pipewire", "pulse"):
        mic = ""
    if speaker in ("default", "pipewire", "pulse"):
        speaker = ""
    return choose(mic, speaker, env, inputs)
