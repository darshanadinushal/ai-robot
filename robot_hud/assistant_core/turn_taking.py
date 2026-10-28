"""Phase 5.3: interrupting Luna by voice while she talks (setting "Interrupt by voice").

While Luna talks only two things count: a stop phrase (the packs' `controls.stop`, with or
without "Luna") and the wake word ("Hey Luna, ..."). Words Luna is saying herself are ignored
(echo guard), so her own voice in the microphone cannot stop her. Tapping the screen always
works too. Never use this as a safety stop for motion: that needs its own hardware stop.
"""
from .. import languages
from ..commands import _words


def interruption(text, language, saying=""):
    """"stop", "wake" or None for what the microphone heard while Luna says `saying`."""
    words = _words(text)
    if not words:
        return None
    echo = set(_words(saying))
    if all(word in echo for word in words):
        return None  # only her own words: the speaker's echo
    pack = languages.get(language)
    rest = [word for word in words if word not in pack.wake_words]
    if " ".join(rest) in pack.controls.get("stop", ()):
        return "stop"
    if len(rest) < len(words) and not any(word in echo for word in pack.wake_words
                                          if word in words):
        return "wake"
    return None
