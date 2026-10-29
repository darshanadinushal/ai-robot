"""Settings page model: sections and what each choice changes (no pygame).

The page in app.py shows one section at a time, chosen in the menu on the left. Every choice
is applied with `apply_choice` and saved straight away.
"""
from .i18n import LANGUAGES
from .naming import MAX_LETTERS, clean_name
from .preferences import COLOR_CHOICES, LAYOUT_CHOICES, MODES, WAVE_LAYOUTS
from .voices import VOICES, get_voice, voice_for, voices_for

SECTIONS = ("Display & Theme", "Voice", "Listening", "Memory", "About")


KEYBOARD_ROWS = ("QWERTYUIOPÅ", "ASDFGHJKLÖÄ", "ZXCVBNM")


class NameEditor:
    """Typing a new robot name on the on-screen keyboard (Settings > About > Robot name).

    `warning` is None, ("letters", None) for an invalid name or ("unknown", name) when the
    speech model does not know the word; Save again keeps it anyway.
    """

    def __init__(self, current):
        self.text, self.warning, self._confirmed = current, None, None

    def key(self, char):
        if len(self.text) < MAX_LETTERS and len(char) == 1 and char.isalpha():
            self.text += char.upper() if not self.text else char.lower()
            self.warning = None

    def back(self):
        self.text, self.warning = self.text[:-1], None

    def save(self, current, knows):
        """"restart" (a new name to save), "close" (unchanged) or None (see `warning`).

        `knows(word)` is True/False if the speech model knows the word, None if unsure.
        """
        name = clean_name(self.text)
        if name is None:
            self.warning = ("letters", None)
            return None
        if name == current:
            return "close"
        if knows(name.lower()) is False and self._confirmed != name:
            self._confirmed, self.warning = name, ("unknown", name)
            return None
        return "restart"


def voice_interrupt_wanted(prefs, echo_cancelling=False):
    """Interrupt by voice: the user's choice, or automatic (on with echo-cancelling audio)."""
    choice = prefs.get("voice_interrupt")
    return choice if isinstance(choice, bool) else bool(echo_cancelling)


def apply_choice(prefs, kind, value=None, echo_cancelling=False):
    """Apply one Settings choice to prefs. Returns the changed key, or None if nothing changed."""
    if kind == "mode" and value in MODES:
        key, new = "mode", value
    elif kind == "color" and value in COLOR_CHOICES:
        key, new = "color", value
    elif kind == "layout":
        key = "wave_layout" if prefs.get("mode") == "WAVE" else "layout"
        if value not in (WAVE_LAYOUTS if key == "wave_layout" else LAYOUT_CHOICES):
            return None
        new = value
    elif kind == "animations":
        key, new = "animations", not prefs.get("animations", True)
    elif kind == "online_listening":
        key, new = "online_listening", not prefs.get("online_listening", False)
    elif kind == "memory":
        key, new = "memory", not prefs.get("memory", True)
    elif kind == "voice_interrupt":
        key, new = "voice_interrupt", not voice_interrupt_wanted(prefs, echo_cancelling)
    elif kind == "language" and value in LANGUAGES:
        key, new = "language", value  # screen texts only, not the voice
    elif kind == "voice" and value in VOICES:
        key, new = "voice", value
    elif kind == "voice_language":
        # Keep the voice type if the new language has it, otherwise take its first voice.
        current = get_voice(prefs.get("voice"))
        found = voice_for(value, current.gender) or next(iter(voices_for(value)), None)
        if found is None:
            return None
        key, new = "voice", found.key
    elif kind == "gender":
        found = voice_for(get_voice(prefs.get("voice")).language, value)
        if found is None:
            return None
        key, new = "voice", found.key
    else:
        return None
    if prefs.get(key) == new:
        return None
    prefs[key] = new
    return key
