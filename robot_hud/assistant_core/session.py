"""Conversation sessions and turns."""
from dataclasses import dataclass

FOLLOWUP_SECONDS = 8.0           # after an answer, Luna listens this long without "Luna" first
QUESTION_FOLLOWUP_SECONDS = 15.0  # ...and this long when her answer asked the user something


@dataclass(frozen=True)
class Turn:
    """One question sent to the AI. `request` is AIWorker's id; `session` the Session's id."""
    session: int
    request: int
    question: str
    language: str
    asked_at: float


class Session:
    """One conversation: an id (a new session never accepts old answers), the follow-up window,
    the number of answers kept in its history and the time of the last activity."""

    def __init__(self):
        self.id = 1
        self.followup_until = 0.0
        self.turns = 0
        self.last_activity = 0.0

    def renew(self):
        self.id += 1
        self.followup_until = 0.0
        self.turns = 0

    def touch(self, t):
        self.last_activity = max(self.last_activity, t)

    def open_followup(self, t, seconds=FOLLOWUP_SECONDS):
        self.followup_until = t + seconds
        self.touch(t)

    def close_followup(self):
        self.followup_until = 0.0

    def in_followup(self, t):
        return t < self.followup_until

    def idle_for(self, t):
        return t - max(self.last_activity, self.followup_until)
