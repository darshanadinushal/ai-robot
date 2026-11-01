"""AI usage meter and daily cost guard (pure: no pygame, no network).

Every path that calls a paid API reports what it used here: the talk path (`talk`: answers,
including web searches) and online listening (`listen`: audio minutes); the task service
(`task`) will join it. The cost is an estimate from the provider's list prices in
`prices.json` (override with LUNA_AI_PRICES=<file>); an unknown model is priced like the most
expensive known one, so the budget errs on the safe side.

`CostGuard` checks the day's total before Luna asks the AI. The day is the local calendar
day. A question that starts below the budget is answered in full, so the day can end slightly
over it (by at most one answer).

Daily totals (numbers only: no questions, answers or keys) are kept in SQLite: ~/.luna/tasks.db,
or LUNA_TASKS_FILE. The file is created (owner-only) with the first record; rows older than
KEEP_DAYS are deleted. Thread-safe: the AI and transcription threads record, the UI thread reads.
"""
import json
import logging
import os
import sqlite3
import threading
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger("luna.budget")

PRICES_FILE = Path(__file__).with_name("prices.json")
DEFAULT_DAILY_USD = 0.30
BUDGET_CHOICES = (0.10, 0.30, 0.50, 1.00, 2.00)
WARN_AT = 0.8      # Luna mentions once a day that most of the budget is used
KEEP_DAYS = 400
PATHS = ("talk", "listen", "task")
ERRORS = (sqlite3.Error, OSError)


def default_path(env=None):
    env = os.environ if env is None else env
    return Path(env.get("LUNA_TASKS_FILE") or Path.home() / ".luna" / "tasks.db")


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


class Prices:
    """List prices in USD: per 1M tokens, per tool call and per audio minute."""

    def __init__(self, models=None, tools=None, audio_minute=None):
        self.models = dict(models or {})
        self.tools = dict(tools or {})
        self.audio_minute = dict(audio_minute or {})
        self._warned = set()

    @classmethod
    def load(cls, path=PRICES_FILE, override=None):
        """The shipped table, with the entries of `override` (LUNA_AI_PRICES) on top."""
        tables = {"models": {}, "tools": {}, "audio_minute": {}}
        override = os.environ.get("LUNA_AI_PRICES", "") if override is None else override
        for source in (path, override):
            if not source:
                continue
            try:
                data = json.loads(Path(source).read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                log.warning("Luna budget: price file not read (%s)", type(error).__name__)
                continue
            for table in tables:
                for name, value in (data.get(table) or {}).items() if isinstance(data, dict) else ():
                    if table == "models" and isinstance(value, dict):
                        rates = {k: _number(value.get(k)) for k in ("input", "cached", "output")}
                        if rates["input"] is not None and rates["output"] is not None:
                            if rates["cached"] is None:
                                rates["cached"] = rates["input"]
                            tables[table][str(name)] = rates
                    elif table != "models" and _number(value) is not None:
                        tables[table][str(name)] = _number(value)
        return cls(**tables)

    def _find(self, table, name, kind):
        """The entry for `name` (also a dated snapshot such as name-2026-10-01), else the dearest."""
        if name in table:
            return table[name]
        for known in sorted(table, key=len, reverse=True):
            if name.startswith(known + "-"):
                return table[known]
        if (kind, name) not in self._warned:
            self._warned.add((kind, name))
            log.warning("Luna budget: no price for %s %r; counting it at the highest known price",
                        kind, name)
        return None

    def tokens(self, model, input_tokens, cached, output):
        rates = self._find(self.models, model, "model")
        if rates is None:
            rates = {k: max((r[k] for r in self.models.values()), default=0.0)
                     for k in ("input", "cached", "output")}
        cached = min(cached, input_tokens)
        return ((input_tokens - cached) * rates["input"] + cached * rates["cached"]
                + output * rates["output"]) / 1_000_000

    def calls(self, tool, count):
        price = self._find(self.tools, tool, "tool")
        if price is None:
            price = max(self.tools.values(), default=0.0)
        return count * price

    def audio(self, model, seconds):
        price = self._find(self.audio_minute, model, "audio model")
        if price is None:
            price = max(self.audio_minute.values(), default=0.0)
        return seconds / 60.0 * price


class UsageMeter:
    """Counts AI use per day, path and model; `spent()` is today's estimated cost in USD."""

    def __init__(self, path=None, prices=None, clock=datetime.now):
        self.path = Path(path) if path else default_path()
        self.prices = prices or Prices.load()
        self.clock = clock
        self._lock = threading.Lock()
        self._totals = {}       # day (ISO) -> USD, read from the file once per day
        self._broken = False    # the file failed: count in memory only (logged once)
        self._cleaned = False   # rows older than KEEP_DAYS deleted this run

    def today(self):
        return self.clock().date().isoformat()

    def add_tokens(self, path, model, input_tokens, cached=0, output=0):
        usd = self.prices.tokens(model, int(input_tokens), int(cached), int(output))
        return self._add(path, model, usd, input_tokens=int(input_tokens), cached=int(cached),
                         output=int(output))

    def add_calls(self, path, tool, count=1):
        return self._add(path, tool, self.prices.calls(tool, count), calls=int(count)) if count else 0.0

    def add_audio(self, path, model, seconds):
        return self._add(path, model, self.prices.audio(model, seconds), seconds=float(seconds))

    def spent(self, day=None):
        day = day or self.today()
        with self._lock:
            return self._total(day)

    def _total(self, day):
        if day not in self._totals:
            self._totals[day] = self._read(day)
        return self._totals[day]

    def _add(self, path, item, usd, input_tokens=0, cached=0, output=0, calls=0, seconds=0.0):
        day = self.today()
        with self._lock:
            self._totals[day] = self._total(day) + usd
            self._write(day, path if path in PATHS else "talk", str(item)[:80],
                        (input_tokens, cached, output, calls, seconds, usd))
        return usd

    def _connect(self):
        new = not self.path.exists()
        if new:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.path), timeout=2.0)
        try:
            db.execute("CREATE TABLE IF NOT EXISTS usage (day TEXT NOT NULL, path TEXT NOT NULL, "
                       "model TEXT NOT NULL, input INTEGER NOT NULL DEFAULT 0, "
                       "cached INTEGER NOT NULL DEFAULT 0, output INTEGER NOT NULL DEFAULT 0, "
                       "calls INTEGER NOT NULL DEFAULT 0, seconds REAL NOT NULL DEFAULT 0, "
                       "usd REAL NOT NULL DEFAULT 0, PRIMARY KEY (day, path, model))")
            if new:
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    pass
            if not self._cleaned:
                self._cleaned = True
                old = (date.fromisoformat(self.today()) - timedelta(days=KEEP_DAYS)).isoformat()
                with db:
                    db.execute("DELETE FROM usage WHERE day < ?", (old,))
        except BaseException:
            db.close()
            raise
        return db

    def _read(self, day):
        if self._broken or not self.path.exists():
            return 0.0
        try:
            with closing(self._connect()) as db:
                row = db.execute("SELECT COALESCE(SUM(usd), 0) FROM usage WHERE day = ?",
                                 (day,)).fetchone()
            return float(row[0])
        except ERRORS as error:
            self._fail(error)
            return 0.0

    def _write(self, day, path, item, numbers):
        if self._broken:
            return
        try:
            with closing(self._connect()) as db, db:
                db.execute("INSERT INTO usage (day, path, model, input, cached, output, calls, "
                           "seconds, usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                           "ON CONFLICT (day, path, model) DO UPDATE SET "
                           "input = input + excluded.input, cached = cached + excluded.cached, "
                           "output = output + excluded.output, calls = calls + excluded.calls, "
                           "seconds = seconds + excluded.seconds, usd = usd + excluded.usd",
                           (day, path, item) + tuple(numbers))
        except ERRORS as error:
            self._fail(error)

    def _fail(self, error):
        self._broken = True
        log.warning("Luna budget: usage file not available (%s); counting in memory only",
                    type(error).__name__)


class CostGuard:
    """The daily AI budget (USD), for the talk path, online listening and (later) tasks."""

    def __init__(self, meter, daily_usd=DEFAULT_DAILY_USD):
        self.meter = meter
        self.daily_usd = daily_usd
        self._warned_day = None

    def allows(self):
        """True while today's estimated cost is below the budget."""
        return self.meter.spent() < self.daily_usd

    def warning_due(self):
        """True once a day, when most (WARN_AT) of the budget is used but some is left."""
        day = self.meter.today()
        spent = self.meter.spent(day)
        if self._warned_day != day and WARN_AT * self.daily_usd <= spent < self.daily_usd:
            self._warned_day = day
            return True
        return False

    def status(self):
        """(spent, budget) in USD for the screen."""
        return self.meter.spent(), self.daily_usd
