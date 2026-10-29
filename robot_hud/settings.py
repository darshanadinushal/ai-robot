"""Settings page model: sections and what each choice changes (no pygame).

The page in app.py shows one section at a time, chosen in the menu on the left. Every choice
is applied with `apply_choice` and saved straight away.
"""
from .i18n import LANGUAGES
from .preferences import COLOR_CHOICES, LAYOUT_CHOICES, MODES, WAVE_LAYOUTS
from .voices import VOICES, get_voice, voice_for, voices_for

SECTIONS = ("Display & Theme", "Language", "Voice", "Listening", "Memory", "About")


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
