"""Persistent memory (phase 5.2): short facts the user explicitly asked Luna to remember.

Stored in a small SQLite file in the home folder (`LUNA_MEMORY_FILE` overrides), never in the
repo. Nothing is saved automatically: the AI can only propose `remember_fact`, and the
PermissionPolicy runs it only when the user's own words asked for it (the pack's
`memory_words`). Private details (long numbers, passwords, ...) are refused here in code.
Memories are notes for answers; they never grant permission for any action. The user can see
and delete them in Settings > Memory.

"About you" (`profile`): the user's name, home town and birthday (day and month only, never
the year), saved the same way and always given to the AI.
"""
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .. import languages, naming
from .events import Log
from .tools import Tool, ToolResult, _object, words

MAX_FACTS = 50
MAX_CHARS = 120
RELEVANT = 5           # at most this many memories go into one question's instructions
ERRORS = (sqlite3.Error, OSError)
_DIGITS = re.compile(r"\d(?:[\s-]?\d){3,}")  # phone, card, PIN and other numbers
PROFILE_FIELDS = ("name", "home", "birthday")
_BIRTHDAY = re.compile(r"(\d{1,2})-(\d{1,2})")  # MM-DD


def birthday(value):
    """(month, day) of a saved "MM-DD" birthday, or None."""
    match = _BIRTHDAY.fullmatch(value or "")
    if not match:
        return None
    month, day = int(match.group(1)), int(match.group(2))
    try:
        date(2000, month, day)  # a leap year: 29 February is a real birthday
    except ValueError:
        return None
    return month, day


_MONTH_NAMES = {name: i + 1 for names in (
    ("january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"),
    ("januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september",
     "oktober", "november", "december"))
    for i, name in enumerate(names)}
_MONTH_NAMES.update({name[:3]: month for name, month in list(_MONTH_NAMES.items())})


def parse_birthday(text):
    """(month, day) from how the AI may write a birthday, the year always dropped:
    "10-28", "28-10" (day first when clear), "1990-10-28", "28/10", "October 28", "28 oktober",
    "28th of October". None when it is not a real day."""
    text = str(text).strip().lower()
    numbers = [int(n) for n in re.findall(r"\d+", text)]
    month = next((_MONTH_NAMES[word] for word in re.findall(r"[^\W\d_]+", text)
                  if word in _MONTH_NAMES), None)
    small = [n for n in numbers if n <= 31]  # a year is never the day or month
    if month is not None:
        pair = (month, small[0]) if len(small) == 1 else None
    elif len(small) == 2:
        first, second = small
        if len(numbers) == 3 and numbers[0] > 31:
            pair = (first, second)       # 1990-10-28
        elif len(numbers) == 3:
            pair = (second, first)       # 28-10-1990
        elif "/" in text or "." in text or first > 12:
            pair = (second, first)       # 28/10, 28.10, 28-10: day first
        else:
            pair = (first, second)       # 10-28: month first, as asked
    else:
        pair = None
    return birthday("%d-%d" % pair) if pair else None


def is_birthday(value, today):
    born = birthday(value)
    if born is None:
        return False
    if born == (2, 29) and today.month == 2 and today.day == 28:
        try:
            date(today.year, 2, 29)
        except ValueError:
            return True  # no 29th this year: celebrate on the 28th
    return born == (today.month, today.day)


def _profile_value(field, value):
    """(clean value, None) or (None, why not)."""
    value = " ".join(str(value).split())
    if field == "birthday":
        born = parse_birthday(value)
        if born is None:
            return None, "birthday not understood; write it like 'October 28' (no year)"
        return "%02d-%02d" % born, None
    limit = 40 if field == "name" else 60
    if not value or len(value) > limit:
        return None, "%s must be 1 to %d characters" % (field, limit)
    if re.search(r"\d", value) or private(value):
        return None, ("no numbers: only a name" if field == "name" else
                      "only a town or city, never a street address")
    return value, None


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
        self._profile = None

    def _connect(self):
        new = not self.path.exists()
        db = sqlite3.connect(str(self.path))
        db.execute("PRAGMA secure_delete = ON")
        db.execute("CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                   "text TEXT NOT NULL, saved TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS profile (field TEXT PRIMARY KEY, "
                   "value TEXT NOT NULL)")
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

    def profile(self):
        """{field: value} for the saved "About you" fields (name, home, birthday "MM-DD")."""
        if self._profile is None:
            if not self.path.exists():
                return {}
            with closing(self._connect()) as db:
                rows = db.execute("SELECT field, value FROM profile").fetchall()
            self._profile = {field: value for field, value in rows if field in PROFILE_FIELDS}
        return dict(self._profile)

    def set_profile(self, field, value):
        """(value, status) when saved, else (None, why not)."""
        if field not in PROFILE_FIELDS:
            return None, "unknown field"
        value, problem = _profile_value(field, value)
        if problem:
            return None, problem
        if self.profile().get(field) == value:
            return value, "already remembered"
        with closing(self._connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO profile (field, value) VALUES (?, ?)",
                       (field, value))
        self._profile = None
        return value, "saved"

    def forget_profile(self, field):
        if field not in self.profile():
            return False
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM profile WHERE field = ?", (field,))
        self._profile = None
        return True

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
        """Delete every fact and the "About you" fields; returns how many there were."""
        count = len(self.facts()) + len(self.profile())
        if count:
            with closing(self._connect()) as db, db:
                db.execute("DELETE FROM facts")
                db.execute("DELETE FROM profile")
        self._facts = self._profile = None
        return count

    def relevant(self, question, language, limit=RELEVANT):
        """Memories sharing words with the question (best first); unrelated ones are left out."""
        facts = self.facts()
        ignore = set(languages.get(language).memory_words.get("ignore", ())) | {naming.name.lower()}
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
            facts, about = store.facts(), store.profile()
        except ERRORS as error:
            return [], ToolResult(False, "memory could not be read (%s)" % type(error).__name__)
        return [], ToolResult(True, "%d memories" % len(facts),
                              {"about_user": about,
                               "memories": [{"id": fact.id, "fact": fact.text} for fact in facts]})

    def forget(arguments):
        try:
            done = store.forget(arguments["id"])
        except ERRORS as error:
            return [], ToolResult(False, "memory could not be deleted (%s)" % type(error).__name__)
        if not done:
            return [], ToolResult(False, "no memory with that id")
        return [Log("Luna memory: forgot memory %d" % arguments["id"])], ToolResult(True, "forgotten")

    def set_about(arguments):
        try:
            value, status = store.set_profile(arguments["field"], arguments["value"])
        except ERRORS as error:
            return [], ToolResult(False, "memory could not be saved (%s)" % type(error).__name__)
        if value is None:
            return [], ToolResult(False, status)
        changed = [] if status == "already remembered" else [
            Log("Luna memory: saved %s" % arguments["field"])]
        return changed, ToolResult(True, status, {arguments["field"]: value})

    def forget_about(arguments):
        try:
            done = store.forget_profile(arguments["field"])
        except ERRORS as error:
            return [], ToolResult(False, "memory could not be deleted (%s)" % type(error).__name__)
        if not done:
            return [], ToolResult(False, "nothing saved for that")
        return [Log("Luna memory: forgot %s" % arguments["field"])], ToolResult(True, "forgotten")

    field = {"type": "string", "enum": list(PROFILE_FIELDS)}

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
        Tool("set_about_user",
             "Save the user's name, home town or birthday when the user tells you it (\"my "
             "name is\", \"I live in\", \"my birthday is\") or asks you to remember it.",
             _object({"field": field,
                      "value": {"type": "string",
                                "description": "name: what to call the user; home: town or "
                                               "city only; birthday: English month name and "
                                               "day, e.g. 'October 28', never the year"}}),
             "memory", set_about, asked_with=("remember", "about")),
        Tool("forget_about_user",
             "Delete the user's saved name, home town or birthday. Only when asked to forget it.",
             _object({"field": field}), "memory", forget_about, asked_with="forget"),
    )
