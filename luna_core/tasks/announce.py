"""Announcer: decides when Luna may tell the user about her tasks (pure, no clock of its own).

Luna never talks over anything: nothing is said while she is busy (speaking, listening,
thinking, in a follow-up or a confirmation). Between two announcements there are at least GAP
seconds. She speaks on her own only when the user was around recently (heard or touched within
AWAY seconds) and it is not quiet hours (22:00-07:00, a setting); when the user talked to her
within INVITED seconds she may always answer. Otherwise announcements wait (the Tasks page
shows them). Order: approval questions, results, failures, then progress; at most one progress
note per task, only for tasks running longer than PROGRESS_AFTER seconds, and it is dropped
when the task ends.
"""
from dataclasses import dataclass

GAP = 20.0
AWAY = 600.0
INVITED = 300.0
PROGRESS_AFTER = 30.0
QUIET_HOURS = (22, 7)
KINDS = ("approval", "result", "failure", "progress")


@dataclass(frozen=True)
class Announcement:
    kind: str      # one of KINDS
    task_id: str
    text: str      # what Luna says
    note: str = ""  # given to the AI with the follow-up questions (results only)


def quiet_time(now, hours=QUIET_HOURS):
    """`now` (a datetime) is inside the quiet hours (which may wrap midnight)."""
    start, end = hours
    if start <= end:
        return start <= now.hour < end
    return now.hour >= start or now.hour < end


class Announcer:
    def __init__(self, quiet=True):
        self.quiet = quiet
        self.items = []
        self.progressed = set()
        self.last_said = None
        self.last_seen = None
        self.last_invited = None

    def add(self, item, t, started=None):
        """Queue an announcement; False if it was dropped (progress rules)."""
        if item.kind == "progress":
            if (item.task_id in self.progressed
                    or (started is not None and t - started < PROGRESS_AFTER)):
                return False
            self.progressed.add(item.task_id)
        else:
            self.items = [old for old in self.items
                          if not (old.task_id == item.task_id and old.kind == "progress")]
        self.items.append(item)
        return True

    def drop(self, task_id):
        """Forget everything about a task (it was cancelled or reported another way)."""
        self.items = [item for item in self.items if item.task_id != task_id]
        self.progressed.discard(task_id)

    def user_active(self, t, invited=False):
        """The user is here (touch, or speech; `invited` when they talked to Luna)."""
        self.last_seen = t
        if invited:
            self.last_invited = t

    def pending(self):
        return list(self.items)

    def may_speak(self, t, now):
        if self.last_invited is not None and t - self.last_invited < INVITED:
            return True
        if self.quiet and quiet_time(now):
            return False
        return self.last_seen is not None and t - self.last_seen < AWAY

    def next(self, t, now, busy=False):
        """The announcement to say now (removed from the queue), or None."""
        if busy or not self.items:
            return None
        if self.last_said is not None and t - self.last_said < GAP:
            return None
        if not self.may_speak(t, now):
            return None
        item = min(self.items, key=lambda a: KINDS.index(a.kind))
        self.items.remove(item)
        self.last_said = t
        return item
