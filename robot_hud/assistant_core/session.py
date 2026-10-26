"""Conversation sessions and turns."""
from dataclasses import dataclass

FOLLOWUP_SECONDS = 8.0  # after an answer, Luna listens this long without "Luna" first


@dataclass(frozen=True)
class Turn:
    """One question sent to the AI. `request` is AIWorker's id; `session` the Session's id."""
    session: int
    request: int
    question: str
    language: str
    asked_at: float


class Session:
    """One conversation: an id (a new session never accepts old answers) and the follow-up window."""

    def __init__(self):
        self.id = 1
        self.followup_until = 0.0

    def renew(self):
        self.id += 1
        self.followup_until = 0.0

    def open_followup(self, t):
        self.followup_until = t + FOLLOWUP_SECONDS

    def close_followup(self):
        self.followup_until = 0.0

    def in_followup(self, t):
        return t < self.followup_until
