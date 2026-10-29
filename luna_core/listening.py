"""Smart listening: join Vosk's pieces of one spoken question (pure Python, no audio).

Vosk ends a result after a short pause (about 0.5-1 s), so "What is... the biggest planet?"
arrives as two final results. The UtteranceAssembler collects final results (confirmed
text) and watches the live partial result (the user is still talking), and only hands over
one complete utterance when the user has really finished:

- an exact short command ("Luna, look left") is handed over at once;
- an apparently complete question after continue_seconds(language) without new words
  (per language pack: the Swedish model shows resumed speech about 0.7 s late), a little
  longer for long questions and for people who already paused in this one (see _wait);
- the wake word alone, or speech ending on "and", "the", "och", "att"... after
  LONG_PAUSE_SECONDS, so the rest of the question can follow.

Times are the app's clock (seconds), passed in, so everything is testable without audio.
"""
import os
from dataclasses import dataclass

from . import languages


def _seconds(name, default):
    try:
        return max(0.0, float(os.environ.get(name, default)))
    except ValueError:
        return float(default)


CONTINUE_SECONDS = 0.6  # when a language pack sets none
LONG_PAUSE_SECONDS = _seconds("LUNA_LONG_PAUSE_SECONDS", 1.8)
MAX_UTTERANCE_SECONDS = _seconds("LUNA_MAX_UTTERANCE_SECONDS", 45)
MAX_UTTERANCE_CHARS = int(_seconds("LUNA_MAX_UTTERANCE_CHARS", 1000))



def continue_seconds(language):
    """Pause after an apparently complete question; LUNA_CONTINUE_SECONDS overrides all."""
    if os.environ.get("LUNA_CONTINUE_SECONDS"):
        return _seconds("LUNA_CONTINUE_SECONDS", CONTINUE_SECONDS)
    return languages.get(language).continue_seconds or CONTINUE_SECONDS


@dataclass(frozen=True)
class Utterance:
    text: str
    conversation: bool  # the follow-up window was open when the user started speaking
    ending: str         # "command", "complete", "wake", "unfinished" or "limit"
    parts: int          # number of Vosk final results joined
    started: float      # first words seen
    last_words: float   # last new words seen
    finished: float     # handed over


def _cut(text, limit):
    if len(text) <= limit:
        return text
    cut = text[:limit]
    return cut[:cut.rfind(" ")] if " " in cut else cut


class UtteranceAssembler:
    """Builds one utterance from Vosk results. Not thread-safe: use it from the UI loop.

    `classify(text, conversation)` returns one of the commands.utterance_end kinds.
    """

    def __init__(self, classify, continue_seconds=None, long_pause_seconds=None,
                 max_seconds=None, max_chars=None):
        self.classify = classify
        self.continue_seconds = CONTINUE_SECONDS if continue_seconds is None else continue_seconds
        self.long_pause = LONG_PAUSE_SECONDS if long_pause_seconds is None else long_pause_seconds
        self.max_seconds = MAX_UTTERANCE_SECONDS if max_seconds is None else max_seconds
        self.max_chars = MAX_UTTERANCE_CHARS if max_chars is None else max_chars
        self.skipping = False  # ignore the rest of speech that was cut off or invalidated
        self._skip_since = 0.0
        self.clear()

    def clear(self):
        """Forget the unfinished utterance (Luna talks, language change, interruption...)."""
        self.parts = []
        self.partial = ""
        self.started = None
        self.last_words = 0.0
        self.conversation = False

    def invalidate(self, t):
        """Audio was lost: drop the unfinished utterance and the rest of this speech."""
        self.clear()
        self.skipping, self._skip_since = True, t

    @property
    def pending(self):
        return self.started is not None

    def display(self):
        """The question so far, for the touchscreen."""
        return " ".join(self.parts + ([self.partial] if self.partial else []))

    def _activity(self, t, conversation):
        if self.started is None:
            self.started, self.conversation = t, bool(conversation)
        self.last_words = t

    def add_final(self, text, t, conversation=False):
        text = " ".join(str(text).split())
        self.partial = ""
        if self.skipping:
            self.skipping = False  # this result ends the speech being skipped
            return
        if not text:
            return
        self._activity(t, conversation)
        self.parts.append(text)

    def add_partial(self, text, t, conversation=False):
        text = " ".join(str(text).split())
        if self.skipping:
            if text:
                self._skip_since = t
            return
        if self.parts and text == self.parts[-1]:
            return  # an old copy of the piece that just became final
        if text and text != self.partial:
            self._activity(t, conversation)
        self.partial = text

    def poll(self, t):
        """The finished Utterance, or None while the user may still be talking."""
        if self.skipping:
            if t - self._skip_since >= self.long_pause:
                self.skipping = False
            return None
        if self.started is None:
            return None
        if (t - self.started >= self.max_seconds or len(self.display()) > self.max_chars):
            text = _cut(self.display(), self.max_chars)
            self.skipping, self._skip_since = True, t
            return self._finish(text, "limit", t)
        if self.partial:
            return None  # still talking
        if not self.parts:
            if t - self.last_words >= self.long_pause:
                self.clear()  # the partial words were dropped by Vosk ([unk], noise)
            return None
        text = " ".join(self.parts)
        ending = self.classify(text, self.conversation)
        if t - self.last_words >= self._wait(ending, text) - 1e-6:
            return self._finish(text, ending, t)
        return None

    def _wait(self, ending, text):
        if ending == "command":
            return 0.0
        if ending != "complete":
            return self.long_pause
        # Short questions are answered quickly; people pause to think inside long ones.
        extra = min(0.6, 0.04 * max(0, len(text.split()) - 8))
        if len(self.parts) > 1:
            extra += 0.3
        return min(self.long_pause, self.continue_seconds + extra)

    def _finish(self, text, ending, t):
        done = Utterance(text=text, conversation=self.conversation, ending=ending,
                         parts=len(self.parts) + (1 if self.partial and ending == "limit" else 0),
                         started=self.started, last_words=self.last_words, finished=t)
        self.clear()
        return done
