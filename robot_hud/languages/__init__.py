"""Language packs: one JSON file per language in this folder (en.json, sv.json, ...).

To add a language, copy en.json to <code>.json and translate it; no code changes are needed.
English is the reference pack. A pack may leave fields out, so a new language can start with
only its names and screen texts: missing screen texts, replies and sentences are English, and
without a listening_model Luna listens (and understands commands) in English.

Fields:
  code, name (in English), native_name   required
  screen          English screen text -> translated text (missing texts stay English)
  listening_model offline Vosk model for listening (downloaded by robot-setup);
                  listening_model_mb is its download size, shown by robot-setup
  wake_words      how the recogniser hears "Luna";  greetings: "hey", "hej", ...
  commands        spoken word -> action ID (see commands.ACTIONS); filler_words are ignored
  controls        "cancel" / "new_conversation" -> whole phrases ("never mind", "nytt samtal");
                  like commands, not taken from English
  continuation_words  words that rarely end a question ("and", "the", "och", "att"): when
                  speech stops on one, Luna waits longer for the rest (not taken from English)
  continue_seconds  pause after an apparently complete question before Luna answers; slower
                  listening models need more (they show resumed speech later); 0 = default
  replies         action ID -> Luna's spoken confirmation
  speech          Luna's fixed English sentences -> translated sentence
  ai_instruction  added to the AI instructions so answers come in this language
  sample          voice preview sentence;  warm_up: short word used to test a natural voice
  windows_culture culture of the built-in Windows voice (local testing), e.g. "sv-SE"
  transcription_prompt, transcription_keywords  context and literal words ("Luna", colour
                  names) for online listening; not taken from English
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

FOLDER = Path(__file__).resolve().parent
DEFAULT = "en"


@dataclass(frozen=True)
class LanguagePack:
    code: str
    name: str
    native_name: str
    screen: dict
    listening_model: str
    wake_words: tuple
    greetings: tuple
    commands: dict
    filler_words: tuple
    replies: dict
    speech: dict
    ai_instruction: str
    sample: str
    warm_up: str
    windows_culture: str
    continuation_words: tuple = ()
    continue_seconds: float = 0.0
    controls: dict = field(default_factory=dict)
    transcription_prompt: str = ""
    transcription_keywords: tuple = ()

    def reply(self, action_id):
        return self.replies.get(action_id)

    def say(self, text):
        """One of Luna's fixed English sentences in this language."""
        return self.speech.get(text, text)


def _read(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("code", "name", "native_name"):
        if not isinstance(data.get(key), str) or not data[key]:
            raise ValueError("%s: '%s' is required" % (path.name, key))
    if data["code"] != path.stem:
        raise ValueError("%s: code must be '%s'" % (path.name, path.stem))
    return data


def _build(data, english):
    """A pack from JSON data; fields it leaves out come from English."""
    def field(key, default):
        value = data.get(key)
        if value in (None, "", [], {}):
            return english.get(key, default) if english else default
        return value
    replies = dict(english.get("replies", {})) if english else {}
    replies.update(data.get("replies") or {})
    return LanguagePack(
        code=data["code"], name=data["name"], native_name=data["native_name"],
        screen=dict(data.get("screen") or {}),
        listening_model=str(data.get("listening_model") or ""),
        wake_words=tuple(w.lower() for w in data.get("wake_words") or ()),
        greetings=tuple(w.lower() for w in data.get("greetings") or ()),
        commands={w.lower(): a for w, a in (data.get("commands") or {}).items()},
        filler_words=tuple(w.lower() for w in data.get("filler_words") or ()),
        replies=replies, speech=dict(data.get("speech") or {}),
        ai_instruction=data.get("ai_instruction") or "Always answer in %s." % data["name"],
        sample=field("sample", ""), warm_up=field("warm_up", "Hello."),
        windows_culture=str(data.get("windows_culture") or ""),
        continuation_words=tuple(w.lower() for w in data.get("continuation_words") or ()),
        continue_seconds=float(data.get("continue_seconds") or 0.0),
        controls={name: tuple(" ".join(p.lower().split()) for p in phrases)
                  for name, phrases in (data.get("controls") or {}).items()},
        transcription_prompt=str(data.get("transcription_prompt") or ""),
        transcription_keywords=tuple(str(k) for k in data.get("transcription_keywords") or ()))


def load(folder=FOLDER):
    """All packs in `folder`, English first, then sorted by code."""
    raw = {}
    for path in sorted(Path(folder).glob("*.json")):
        data = _read(path)
        raw[data["code"]] = data
    if DEFAULT not in raw:
        raise ValueError("The English language pack (%s.json) is missing" % DEFAULT)
    english = raw[DEFAULT]
    order = [DEFAULT] + sorted(code for code in raw if code != DEFAULT)
    return {code: _build(raw[code], None if code == DEFAULT else english) for code in order}


PACKS = load()


def get(code):
    """The pack for `code`; unknown codes give English."""
    return PACKS.get(code, PACKS[DEFAULT])


def codes():
    return tuple(PACKS)
