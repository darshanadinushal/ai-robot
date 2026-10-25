"""Catalogue of Luna's voices (pure data, no audio code).

Natural voices are Piper models installed by robot-setup ("Natural voice" feature) in
~/.ai-robot-models/piper. Each voice also names a basic eSpeak voice of the same language and
gender, used when the natural voice is not available. To replace a voice, change this table.
"""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Voice:
    key: str
    language: str  # "en" or "sv"
    gender: str  # "female" or "male"
    label: str
    model: str  # Piper model name (<model>.onnx + <model>.onnx.json)
    espeak: str  # basic voice for espeak-ng
    license_note: str


VOICES = {
    "EN_FEMALE": Voice("EN_FEMALE", "en", "female", "English female (Lessac)",
                       "en_US-lessac-medium", "en-us+f3",
                       "prototype only: non-commercial training data"),
    "EN_MALE": Voice("EN_MALE", "en", "male", "English male (Ryan)",
                     "en_US-ryan-high", "en-us+m3",
                     "prototype only: CC BY-NC-SA 4.0 training data"),
    "SV_FEMALE": Voice("SV_FEMALE", "sv", "female", "Svenska kvinna (Alma)",
                       "sv_SE-alma-medium", "sv+f3", "CC BY 4.0: credit required"),
    "SV_MALE": Voice("SV_MALE", "sv", "male", "Svenska man (NST)",
                     "sv_SE-nst-medium", "sv+m3", "CC0 training data"),
}
DEFAULT_VOICE = "EN_FEMALE"
LANGUAGES = {"en": "English", "sv": "Svenska"}
GENDERS = ("female", "male")
SAMPLES = {
    "en": "Hello, I am Luna. This is how my voice sounds.",
    "sv": "Hej, jag heter Luna. Så här låter min röst.",
}


def models_dir():
    return Path.home() / ".ai-robot-models" / "piper"


def get_voice(key):
    """The voice for key; unknown keys fall back to the default voice."""
    return VOICES.get(str(key or "").upper(), VOICES[DEFAULT_VOICE])


def model_path(voice, folder=None):
    return Path(folder or models_dir()) / (voice.model + ".onnx")


def is_installed(voice, folder=None):
    model = model_path(voice, folder)
    return model.is_file() and model.with_name(model.name + ".json").is_file()


def voice_for(language, gender):
    """The catalogue voice with this language and gender, or None."""
    for voice in VOICES.values():
        if voice.language == language and voice.gender == gender:
            return voice
    return None


def sample_text(voice):
    """A short preview sentence in the voice's own language."""
    return SAMPLES.get(voice.language, SAMPLES["en"])
