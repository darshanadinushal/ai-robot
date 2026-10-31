"""General yes/no confirmation (task service step 2). Pure: no pygame, no network.

Luna asks before doing something the user did not clearly ask for (saving a memory from a
passing remark today; later a task's action). The question is spoken and shown with Yes/No
buttons. An approval covers exactly the items asked about: each is a tool name plus a hash of
its arguments, so the AI cannot swap in a different action after the user agreed.

Rules (checked in code, never by the model):
- No answer within TIMEOUT_SECONDS means no.
- A `touch` item (risk `sensitive`) needs a tap on Yes: a spoken yes could come from a TV or a
  visitor. Voice can still say no.
- Voice answers are whole phrases from the pack's `answers` ("yes", "ja tack"), said to Luna
  (wake word or follow-up window). Anything else said to Luna closes the question as a no.
"""
import hashlib
import json
from dataclasses import dataclass

from .. import languages

TIMEOUT_SECONDS = 60.0
MAX_ITEMS = 3


def call_key(name, arguments):
    """What an approval covers: the tool and its exact arguments."""
    text = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
    return "%s:%s" % (name, hashlib.sha256(text.encode("utf-8")).hexdigest()[:16])


@dataclass(frozen=True)
class Item:
    key: str           # call_key(tool, arguments)
    label: str         # English screen text, translated when drawn ("Remember")
    detail: str = ""   # what exactly, in the user's language ("You like tea.")
    touch: bool = False


def answer(text, language="en"):
    """"yes", "no" or None (not an answer) for an utterance said to Luna."""
    said = languages.phrase(text)
    pack = languages.get(language)
    for kind in ("yes", "no"):
        if said in pack.answers.get(kind, ()):
            return kind
    return None


class Confirmation:
    """One open question. `payload` is what the owner runs on yes (e.g. the tool calls)."""

    def __init__(self, items, opened, language="en", payload=None, timeout=TIMEOUT_SECONDS):
        if not items:
            raise ValueError("nothing to confirm")
        self.items = tuple(items)[:MAX_ITEMS]
        self.opened = opened
        self.language = language
        self.payload = payload
        self.deadline = opened + timeout

    @property
    def touch(self):
        """Only a tap on Yes approves."""
        return any(item.touch for item in self.items)

    @property
    def keys(self):
        return tuple(item.key for item in self.items)

    def expired(self, t):
        return t >= self.deadline

    def by_voice(self, text, language):
        """("yes" | "no" | "tap" | None): "tap" is a spoken yes that needs a touch instead."""
        said = answer(text, language)
        if said == "yes" and self.touch:
            return "tap"
        return said

    def summary(self):
        """Short text for logs and the AI (no arguments beyond the shown details)."""
        return "; ".join(("%s: %s" % (item.label, item.detail)) if item.detail else item.label
                         for item in self.items)
