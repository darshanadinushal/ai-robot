"""Persistent memory (phase 5.2): short facts the user explicitly asked Luna to remember.

Stored in a small SQLite file in the home folder (`LUNA_MEMORY_FILE` overrides), never in the
repo. Nothing is saved automatically: the AI can only propose `remember_fact`, and the
PermissionPolicy runs it only when the user's own words asked for it (the pack's
`memory_words`). Private details (long numbers, passwords, ...) are refused here in code.
Memories are notes for answers; they never grant permission for any action. The user can see
and delete them in Settings > Memory.
"""
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .. import languages
from .events import Log
from .tools import Tool, ToolResult, _object, words

MAX_FACTS = 50
MAX_CHARS = 120
RELEVANT = 5           # at most this many memories go into one question's instructions
ERRORS = (sqlite3.Error, OSError)
_DIGITS = re.compile(r"\d(?:[\s-]?\d){3,}")  # phone, card, PIN and other numbers


@dataclass(frozen=True)
class Fact:
    id: int
    text: str
    saved: str  # ISO date


@dataclass(frozen=True)
class Recall:
    """The memories that matter for one question, and how many there are in total."""
    relevant: tuple
    total: int


def default_path(env=None):
    env = os.environ if env is None else env
    return Path(env.get("LUNA_MEMORY_FILE") or Path.home() / ".luna-memory.db")


def private(text):
    """Looks like a detail Luna must never keep (any language's `memory_words.private`)."""
    if _DIGITS.search(text):
        return True
    said = words(text)
    return any(" %s " % phrase in said for pack in languages.PACKS.values()
               for phrase in pack.memory_words.get("private", ()))


def _keys(text, ignore):
    return {word[:5] for word in re.findall(r"\w+", text.lower())
            if len(word) >= 4 and word not in ignore}


class MemoryStore:
    """The facts, cached in memory; every change is written to the SQLite file at once.

    Used from the UI thread only. The file is created (readable by the owner only) when the
    first fact is saved; deleted facts are overwritten (`secure_delete`).
    """

    def __init__(self, path=None):
        self.path = Path(path) if path else default_path()
        self._facts = None

    def _connect(self):
        new = not self.path.exists()
        db = sqlite3.connect(str(self.path))
        db.execute("PRAGMA secure_delete = ON")
        db.execute("CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                   "text TEXT NOT NULL, saved TEXT NOT NULL)")
        if new:
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        return db

    def facts(self):
        """All facts, oldest first."""
        if self._facts is None:
            if not self.path.exists():
                return []
            with closing(self._connect()) as db:
                rows = db.execute("SELECT id, text, saved FROM facts ORDER BY id").fetchall()
            self._facts = [Fact(*row) for row in rows]
        return list(self._facts)

    def remember(self, text, replaces=0, today=None):
        """(Fact, status) when saved or already known, else (None, why not)."""
        text = " ".join(str(text).split())
        if len(text) < 3:
            return None, "nothing to remember"
        if len(text) > MAX_CHARS:
            return None, "too long; keep it to one short fact"
        if private(text):
            return None, "private details such as numbers, codes or passwords are never saved"
        facts = self.facts()
        old = next((fact for fact in facts if fact.id == replaces), None) if replaces else None
        if replaces and old is None:
            return None, "no memory with that id"
        same = next((fact for fact in facts if fact.text.casefold().rstrip(".!")
                     == text.casefold().rstrip(".!")), None)
        if same is not None and old is None:
            return same, "already remembered"
        if old is None and len(facts) >= MAX_FACTS:
            return None, "memory is full; the user can delete memories in Settings"
        saved = (today or date.today()).isoformat()
        with closing(self._connect()) as db, db:
            if old is not None:
                db.execute("UPDATE facts SET text = ?, saved = ? WHERE id = ?",
                           (text, saved, old.id))
                fact_id = old.id
            else:
                fact_id = db.execute("INSERT INTO facts (text, saved) VALUES (?, ?)",
                                     (text, saved)).lastrowid
        self._facts = None
        return Fact(fact_id, text, saved), "updated" if old is not None else "saved"

    def forget(self, fact_id):
        if not any(fact.id == fact_id for fact in self.facts()):
            return False
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
        self._facts = None
        return True

    def forget_all(self):
        count = len(self.facts())
        if count:
            with closing(self._connect()) as db, db:
                db.execute("DELETE FROM facts")
        self._facts = None
        return count

    def relevant(self, question, language, limit=RELEVANT):
        """Memories sharing words with the question (best first); unrelated ones are left out."""
        facts = self.facts()
        ignore = set(languages.get(language).memory_words.get("ignore", ()))
        wanted = _keys(question, ignore)
        scored = sorted(((len(wanted & _keys(fact.text, ignore)), fact) for fact in facts),
                        key=lambda item: (-item[0], -item[1].id))
        return Recall(tuple(fact for score, fact in scored if score)[:limit], len(facts))


def memory_tools(store):
    """remember_fact / forget_memory need the user's own words (see PermissionPolicy)."""

    def remember(arguments):
        try:
            fact, status = store.remember(arguments["fact"], arguments["replaces"])
        except ERRORS as error:
            return [], ToolResult(False, "memory could not be saved (%s)" % type(error).__name__)
        if fact is None:
            return [], ToolResult(False, status)
        changed = [] if status == "already remembered" else [
            Log("Luna memory: %s memory %d" % (status, fact.id))]
        return (changed,
                ToolResult(True, status, {"id": fact.id, "fact": fact.text}))

    def recall(arguments):
        try:
            facts = store.facts()
        except ERRORS as error:
            return [], ToolResult(False, "memory could not be read (%s)" % type(error).__name__)
        return [], ToolResult(True, "%d memories" % len(facts),
                              {"memories": [{"id": fact.id, "fact": fact.text} for fact in facts]})

    def forget(arguments):
        try:
            done = store.forget(arguments["id"])
        except ERRORS as error:
            return [], ToolResult(False, "memory could not be deleted (%s)" % type(error).__name__)
        if not done:
            return [], ToolResult(False, "no memory with that id")
        return [Log("Luna memory: forgot memory %d" % arguments["id"])], ToolResult(True, "forgotten")

    return (
        Tool("remember_fact",
             "Save one short fact about the user. Only when the user explicitly asked you to "
             "remember it.",
             _object({"fact": {"type": "string",
                               "description": "one short fact in the user's language, said to "
                                              "the user, e.g. 'You like jazz.'"},
                      "replaces": {"type": "integer",
                                   "description": "id of the memory this corrects, or 0"}}),
             "memory", remember, asked_with="remember"),
        Tool("recall_memories", "List everything the user asked you to remember, with ids.",
             _object(), "memory", recall),
        Tool("forget_memory",
             "Delete one memory by its id. Only when the user asked you to forget it.",
             _object({"id": {"type": "integer", "description": "the memory's id"}}),
             "memory", forget, asked_with="forget"),
    )
