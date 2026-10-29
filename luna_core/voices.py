"""Catalogue of Luna's voices, loaded from voices.json (no audio code).

Natural voices are Piper models installed by robot-setup ("Natural voice" feature) in
~/.ai-robot-models/piper. robot-setup reads the same voices.json, so a voice added there is both
downloaded (checked against its SHA-256) and offered in Settings. Each voice also names a basic
eSpeak voice of the same language and gender, used when the natural voice is not available.
The voice's language must have a language pack (luna_core/languages/<code>.json).
"""
import json
from dataclasses import dataclass
from pathlib import Path

from . import languages
from .naming import personalise

CATALOGUE = Path(__file__).resolve().with_name("voices.json")


@dataclass(frozen=True)
class VoiceFile:
    name: str
    source: str  # path below download_base
    size: int
    sha256: str


@dataclass(frozen=True)
class Voice:
    key: str
    language: str  # language pack code, e.g. "en" or "sv"
    gender: str  # e.g. "female" or "male"
    label: str
    model: str  # Piper model name (<model>.onnx + <model>.onnx.json)
    espeak: str  # basic voice for espeak-ng
    license_note: str
    name: str = ""
    files: tuple = ()


def load(path=CATALOGUE):
    """(voices by key, default key, download base URL) from a catalogue file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    found = {}
    for item in data["voices"]:
        files = tuple(VoiceFile(f["name"], f["source"], int(f["size"]), f["sha256"])
                      for f in item.get("files", ()))
        voice = Voice(item["key"].upper(), item["language"], item["gender"], item["label"],
                      item["model"], item["espeak"], item.get("license_note", ""),
                      item.get("name", ""), files)
        found[voice.key] = voice
    default = data.get("default") or next(iter(found))
    if default not in found:
        raise ValueError("voices.json: default voice %s is not in the catalogue" % default)
    return found, default, data.get("download_base", "")


VOICES, DEFAULT_VOICE, DOWNLOAD_BASE = load()


def _in_order(values):
    return tuple(dict.fromkeys(values))


# Voice languages and genders, in catalogue order.
LANGUAGES = {code: languages.get(code).native_name
             for code in _in_order(v.language for v in VOICES.values())}
GENDERS = _in_order(v.gender for v in VOICES.values())
SAMPLES = {code: languages.get(code).sample for code in LANGUAGES}


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


def voices_for(language):
    """All voices of a language, in catalogue order."""
    return [voice for voice in VOICES.values() if voice.language == language]


def genders_for(language):
    return _in_order(voice.gender for voice in voices_for(language))


def voice_for(language, gender):
    """The first catalogue voice with this language and gender, or None."""
    for voice in voices_for(language):
        if voice.gender == gender:
            return voice
    return None


def sample_text(voice):
    """A short preview sentence in the voice's own language."""
    return personalise(languages.get(voice.language).sample
                       or languages.get(languages.DEFAULT).sample)
