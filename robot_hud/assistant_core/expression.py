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


# Web search answers carry source links: "([bbc.com](https://...))", "[SVT](https://...)", bare URLs.
_CITATION = re.compile(
    r"\s*\((?:\s*\[[^\]]*\]\([^)\s]*\)\s*[,;]?)+\s*\)"   # ([a](u)) or ([a](u), [b](v))
    r"|\[([^\]]*)\]\([^)\s]*\)"                         # [text](url) -> text
    r"|\s*\(?\s*https?://[^\s)]*\)?"                     # a bare web address
    r"|\u3010[^\u3011]*\u3011")                           # 【1†source】 marks
_URL_START = re.compile(r"(?:^|[\s(])(h|ht|htt|https?|https?:/{0,2}[^\s()]*)$")
_MAX_HELD_CHARS = 400
_CLOSING = {")": "(", "]": "[", "\u3011": "\u3010"}


def strip_citations(text):
    """Text without links, web addresses and citation marks (never spoken or shown)."""
    return _CITATION.sub(lambda m: m.group(1) or "", text)


def _unfinished(text, before=""):
    """Where a possible link or bracket that is not closed yet starts (len(text): none)."""
    stack, bracket = [], None
    for i, ch in enumerate(text):
        if ch in "([\u3010":
            start = bracket[0] if ch == "(" and bracket and bracket[1] == i - 1 else i
            stack.append((ch, start))
        elif ch in _CLOSING and stack and stack[-1][0] == _CLOSING[ch]:
            start = stack.pop()[1]
            if ch == "]":
                bracket = (start, i)
    words = (before + text).rstrip()[-1:]  # `before`: the text already released
    starts = [start for _, start in stack]
    if words and words not in ".!?:;,\u2026":
        starts.append(len(text.rstrip()))  # a space after a word may precede a link
    if bracket and bracket[1] == len(text) - 1:
        starts.append(bracket[0])  # "[name]" may still get its "(url)"
    match = _URL_START.search(text)
    if match:
        starts.append(match.start(1))
    return len(text[:min(starts)].rstrip()) if starts else len(text)  # with spaces before


class CitationFilter:
    """Removes source links from streamed answer text.

    feed(piece) returns the text that is safe to use now; a bracket or web address that may
    still become a link is held until it closes (at most _MAX_HELD_CHARS).
    """

    def __init__(self):
        self._held, self._last = "", ""

    def feed(self, piece):
        held = self._held + str(piece or "")
        cut = len(held) if len(held) > _MAX_HELD_CHARS else _unfinished(held, self._last)
        self._held = held[cut:]
        self._last = (self._last + held[:cut])[-1:]
        return strip_citations(held[:cut])

    def flush(self):
        held, self._held, self._last = self._held, "", ""
        return strip_citations(held)


class AnswerText:
    """Streamed answer text: mood tag read (MoodTag), then source links removed."""

    def __init__(self):
        self._tag, self._links = MoodTag(), CitationFilter()

    def feed(self, piece):
        mood, text = self._tag.feed(piece)
        return mood, self._links.feed(text) if text else ""

    def flush(self):
        return self._links.feed(self._tag.flush()) + self._links.flush()


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
