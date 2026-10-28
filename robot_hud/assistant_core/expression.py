"""Luna's expressions during a conversation (pure: no pygame).

The AI starts an answer with a mood tag such as "[happy]" (asked for in the instructions); MoodTag
removes it from the streamed text before anything is shown or spoken, so choosing an expression
costs no extra AI request and never delays speech. face_motion gives the small listening and
talking animations.
"""
import math
import re

ANSWER_MOODS = ("happy", "sad", "surprised", "sleepy", "neutral")
LINGER_SECONDS = 1.5  # the answer's expression stays this long after Luna stops speaking
_TAG = re.compile(r"\s*\[([A-Za-z]{1,12})\]\s*")
_MAX_TAG_CHARS = 20


class MoodTag:
    """Strips one leading "[mood]" tag from streamed text.

    feed(piece) returns (mood or None, text to use now). Text that cannot start with a tag is
    released at once; a possible tag is held back for at most a few characters. Unknown word
    tags ("[smile]") are removed but give no mood.
    """

    def __init__(self):
        self._buffer = ""
        self._done = False

    def feed(self, piece):
        piece = str(piece or "")
        if self._done:
            return None, piece
        self._buffer += piece
        start = self._buffer.lstrip()
        if not start:
            return None, ""
        if not start.startswith("["):
            return self._release(None, self._buffer)
        match = _TAG.match(self._buffer)
        if match:
            word = match.group(1).lower()
            rest = self._buffer[match.end():]
            return self._release(word if word in ANSWER_MOODS else None, rest)
        if "]" in start or len(start) > _MAX_TAG_CHARS:
            return self._release(None, self._buffer)  # not a tag, e.g. "[1] ..."
        return None, ""

    def flush(self):
        """Text still held back when the answer ended."""
        if self._done:
            return ""
        return self._release(None, self._buffer)[1]

    def _release(self, mood, text):
        self._done, self._buffer = True, ""
        return mood, text


def face_motion(t, listening, talking, level=0.0):
    """(eye scale factor, vertical offset in px) for the face.

    Listening: eyes open a little wider, following the microphone level (attentive).
    Talking: a soft, slow bob. Otherwise still.
    """
    if listening:
        return 1.03 + 0.03 * min(1.0, max(0.0, float(level)) * 1.6), 0.0
    if talking:
        return 1.0, 3.0 * math.sin(t * 2.4)
    return 1.0, 0.0
