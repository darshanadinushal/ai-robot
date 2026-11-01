"""TaskStore: Luna's tasks, their audit steps and approvals in SQLite (pure: no pygame, no SDK).

Same file as the usage totals (tasks.budget): ~/.luna/tasks.db, or LUNA_TASKS_FILE; created
owner-only. Only Luna writes it, on the UI thread. Finished tasks older than the history
setting (30 days by default) are deleted with their steps and approvals. If the file cannot be
used, tasks are kept in memory for this run (logged once).
"""
import dataclasses
import logging
import os
import sqlite3
from datetime import timedelta, datetime
from pathlib import Path

from .budget import default_path
from .model import ACTIVE, FINAL, INTERRUPTED, QUEUED, RUNNING, Task, can_move, clean

log = logging.getLogger("luna.tasks")

HISTORY_DAYS = 30
HISTORY_CHOICES = (7, 30, 90)
ERRORS = (sqlite3.Error, OSError)
FIELDS = tuple(f.name for f in dataclasses.fields(Task))
# PRAGMA user_version of tasks.db. Changes so far only add columns (see _schema); raise this for
# a change that needs a migration. A higher number is a newer Luna's file (after a rollback).
SCHEMA_VERSION = 1
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, created TEXT NOT NULL, "
    "updated TEXT NOT NULL, goal TEXT NOT NULL, specialist TEXT NOT NULL, language TEXT NOT NULL, "
    "state TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', details TEXT NOT NULL DEFAULT '', "
    "error TEXT NOT NULL DEFAULT '', cost_usd REAL NOT NULL DEFAULT 0, "
    "tainted INTEGER NOT NULL DEFAULT 0, run_state TEXT NOT NULL DEFAULT '', "
    "announced INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS steps (task_id TEXT NOT NULL, at TEXT NOT NULL, "
    "kind TEXT NOT NULL, name TEXT NOT NULL, summary TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS approvals (task_id TEXT NOT NULL, item TEXT NOT NULL, "
    "call_id TEXT NOT NULL, tool TEXT NOT NULL, args_hash TEXT NOT NULL, risk TEXT NOT NULL, "
    "touch INTEGER NOT NULL, asked TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '', "
    "answered TEXT NOT NULL DEFAULT '', by TEXT NOT NULL DEFAULT '', "
    "label TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '')",
    "CREATE INDEX IF NOT EXISTS steps_task ON steps (task_id)",
)


class TaskStore:
    def __init__(self, path=None, clock=datetime.now):
        self.path = Path(path) if path else default_path()
        self.clock = clock
        self.in_memory = False
        self._db = self._open()

    def _now(self):
        return self.clock().isoformat(timespec="seconds")

    def _open(self):
        try:
            new = not self.path.exists()
            if new:
                self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            db = sqlite3.connect(str(self.path), timeout=2.0)
            if new:
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    pass
            try:
                self._schema(db)
            except BaseException:
                db.close()
                raise
            return db
        except ERRORS as error:
            return self._memory(error)

    def _memory(self, error):
        log.warning("Luna tasks: task file not available (%s); keeping tasks in memory",
                    type(error).__name__)
        self.in_memory = True
        db = sqlite3.connect(":memory:")
        self._schema(db)
        return db

    @staticmethod
    def _schema(db):
        with db:
            for statement in SCHEMA:
                db.execute(statement)
            have = {row[1] for row in db.execute("PRAGMA table_info(approvals)")}
            for column in ("label", "detail"):  # files made before step 4
                if column not in have:
                    db.execute("ALTER TABLE approvals ADD COLUMN %s TEXT NOT NULL DEFAULT ''"
                               % column)
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                log.warning("Luna tasks: the task file is from a newer Luna (schema %d); "
                            "using the parts this version knows", version)
            elif version < SCHEMA_VERSION:
                db.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)

    def _run(self, sql, args=()):
        """Run a statement (in a transaction); on a file error, continue in memory."""
        try:
            with self._db:
                return self._db.execute(sql, args).fetchall()
        except sqlite3.OperationalError as error:
            if self.in_memory:
                raise
            self._db.close()
            self._db = self._memory(error)
            with self._db:
                return self._db.execute(sql, args).fetchall()

    @staticmethod
    def _task(row):
        values = dict(zip(FIELDS, row))
        values["tainted"] = bool(values["tainted"])
        values["announced"] = bool(values["announced"])
        return Task(**values)

    def _select(self, where="", args=(), order="created", limit=None):
        sql = "SELECT %s FROM tasks %s ORDER BY %s, rowid" % (", ".join(FIELDS), where, order)
        if limit is not None:
            sql += " LIMIT %d" % int(limit)
        return [self._task(row) for row in self._run(sql, args)]

    # --- tasks ---------------------------------------------------------------------------
    def add(self, task):
        now = self._now()
        task = dataclasses.replace(task, created=now, updated=now)
        self._run("INSERT INTO tasks (%s) VALUES (%s)"
                  % (", ".join(FIELDS), ", ".join("?" * len(FIELDS))), dataclasses.astuple(task))
        return task

    def get(self, task_id):
        found = self._select("WHERE id = ?", (task_id,))
        return found[0] if found else None

    def update(self, task_id, **fields):
        """Change fields other than the state; returns the task (None if unknown)."""
        if "state" in fields or set(fields) - set(FIELDS):
            raise ValueError("unknown or state field")
        if fields:
            fields["updated"] = self._now()
            self._run("UPDATE tasks SET %s WHERE id = ?" % ", ".join(k + " = ?" for k in fields),
                      tuple(fields.values()) + (task_id,))
        return self.get(task_id)

    def move(self, task_id, state, **fields):
        """Move a task to `state` (with other fields); None if unknown or not allowed now."""
        task = self.get(task_id)
        if task is None or not can_move(task.state, state):
            if task is not None:
                log.info("Luna tasks: %s cannot go from %s to %s", task_id, task.state, state)
            return None
        fields.update(state=state, updated=self._now())
        self._run("UPDATE tasks SET %s WHERE id = ? AND state = ?"
                  % ", ".join(k + " = ?" for k in fields),
                  tuple(fields.values()) + (task_id, task.state))
        return self.get(task_id)

    def recent(self, limit=20):
        """The newest tasks first."""
        return self._select(order="created DESC", limit=limit)

    def active(self):
        """Queued, running and waiting tasks, oldest first."""
        return self._select("WHERE state IN (%s)" % ", ".join("?" * len(ACTIVE)), ACTIVE)

    def unannounced(self):
        """Finished tasks Luna has not told the user about yet, oldest first."""
        return self._select("WHERE announced = 0 AND state IN (%s)" % ", ".join("?" * len(FINAL)),
                            FINAL)

    # --- audit ---------------------------------------------------------------------------
    def add_step(self, task_id, kind, name, summary=""):
        self._run("INSERT INTO steps (task_id, at, kind, name, summary) VALUES (?, ?, ?, ?, ?)",
                  (task_id, self._now(), clean(kind, 20), clean(name, 80), clean(summary, 200)))

    def steps(self, task_id):
        return [tuple(row) for row in self._run(
            "SELECT at, kind, name, summary FROM steps WHERE task_id = ? ORDER BY rowid", (task_id,))]

    # --- approvals (one row per question; `item` is the call_key of the exact call) -------
    def ask_approval(self, task_id, call_id, tool, item, risk, touch, label, detail=""):
        self._run("INSERT INTO approvals (task_id, item, call_id, tool, args_hash, risk, touch, "
                  "asked, label, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (task_id, item, clean(call_id, 80), clean(tool, 80), item.rsplit(":", 1)[-1],
                   clean(risk, 20), int(bool(touch)), self._now(), clean(label, 80),
                   clean(detail, 200)))

    def pending_approval(self, task_id, call_id=None):
        """The newest unanswered question of a task (for `call_id` only, if given), as a dict."""
        sql = ("SELECT task_id, call_id, tool, item, risk, touch, label, detail FROM approvals "
               "WHERE task_id = ? AND answer = ''")
        args = (task_id,)
        if call_id is not None:
            sql += " AND call_id = ?"
            args += (call_id,)
        rows = self._run(sql + " ORDER BY rowid DESC LIMIT 1", args)
        if not rows:
            return None
        keys = ("task_id", "call_id", "tool", "item", "risk", "touch", "label", "detail")
        found = dict(zip(keys, rows[0]))
        found["touch"] = bool(found["touch"])
        return found

    def answer_approval(self, task_id, call_id, answer, by):
        """`answer`: "yes" or "no"; `by`: "voice", "tap" or "timeout"."""
        self._run("UPDATE approvals SET answer = ?, answered = ?, by = ? "
                  "WHERE task_id = ? AND call_id = ? AND answer = ''",
                  (answer, self._now(), by, task_id, call_id))

    def use_approval(self, task_id, item):
        """True once for each "yes" to exactly this call (tool + arguments); it is then used."""
        rows = self._run("SELECT rowid FROM approvals WHERE task_id = ? AND item = ? "
                         "AND answer = 'yes' ORDER BY rowid LIMIT 1", (task_id, item))
        if not rows:
            return False
        self._run("UPDATE approvals SET answer = 'used' WHERE rowid = ?", (rows[0][0],))
        return True

    # --- start-up ------------------------------------------------------------------------
    def recover(self):
        """Luna (re)started: queued and running tasks were interrupted (a paused run waiting
        for approval is kept). Returns the interrupted tasks."""
        lost = self._select("WHERE state IN (?, ?)", (QUEUED, RUNNING))
        return [moved for moved in (self.move(task.id, INTERRUPTED, error="interrupted")
                                    for task in lost) if moved is not None]

    def purge(self, days=HISTORY_DAYS):
        """Delete finished tasks (with steps and approvals) not changed for `days` days."""
        old = (self.clock() - timedelta(days=days)).isoformat(timespec="seconds")
        marks = ", ".join("?" * len(FINAL))
        ids = [row[0] for row in self._run(
            "SELECT id FROM tasks WHERE updated < ? AND state IN (%s)" % marks, (old,) + FINAL)]
        for task_id in ids:
            for table, column in (("steps", "task_id"), ("approvals", "task_id"), ("tasks", "id")):
                self._run("DELETE FROM %s WHERE %s = ?" % (table, column), (task_id,))
        return len(ids)

    def close(self):
        try:
            self._db.close()
        except ERRORS:
            pass
