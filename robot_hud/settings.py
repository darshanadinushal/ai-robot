"""Settings page model: sections and what each choice changes (no pygame).

The page in app.py shows one section at a time, chosen in the menu on the left. Every choice
is applied with `apply_choice` and saved straight away.
"""
from .i18n import LANGUAGES
from .preferences import COLOR_CHOICES, LAYOUT_CHOICES, MODES, WAVE_LAYOUTS
from .voices import get_voice, voice_for

SECTIONS = ("Display & Theme", "Language", "Voice", "Animations", "About")


def apply_choice(prefs, kind, value=None):
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
    elif kind == "language" and value in LANGUAGES:
        key, new = "language", value  # screen texts only, not the voice
    elif kind in ("voice_language", "gender"):
        current = get_voice(prefs.get("voice"))
        found = voice_for(value if kind == "voice_language" else current.language,
                          value if kind == "gender" else current.gender)
        if found is None:
            return None
        key, new = "voice", found.key
    else:
        return None
    if prefs.get(key) == new:
        return None
    prefs[key] = new
    return key
