"""Tasks: background work Luna hands to specialist agents (pure data: no SDK, no pygame).

Lifecycle (docs/design/TASK_SERVICE.md section 5):

    queued -> running -> done
                 |  ^  +-> failed       (error class, budget, time, no network)
                 v  |  +-> cancelled    (user, budget)
          waiting_approval +-> interrupted (service or Luna stopped while running)

Luna owns every task; the task service only works on the ones it is given.
"""
import uuid
from dataclasses import dataclass, field

QUEUED = "queued"
RUNNING = "running"
WAITING = "waiting_approval"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"
INTERRUPTED = "interrupted"
STATES = (QUEUED, RUNNING, WAITING, DONE, FAILED, CANCELLED, INTERRUPTED)
ACTIVE = (QUEUED, RUNNING, WAITING)
FINAL = (DONE, FAILED, CANCELLED, INTERRUPTED)
MOVES = {
    QUEUED: {RUNNING, FAILED, CANCELLED, INTERRUPTED},
    RUNNING: {WAITING, DONE, FAILED, CANCELLED, INTERRUPTED},
    WAITING: {RUNNING, FAILED, CANCELLED},  # a paused run survives a restart (stored)
}

GOAL_CHARS = 500      # what the AI may ask a specialist to do
SUMMARY_CHARS = 300   # the spoken result
DETAILS_CHARS = 4000  # shown on the Tasks page, never spoken
NOTE_CHARS = 600      # a result given to the AI with later questions


def can_move(old, new):
    return new in MOVES.get(old, ())


def clean(text, limit):
    """One line of at most `limit` characters (texts from the service or the AI)."""
    return " ".join(str(text or "").split())[:limit]


def new_id():
    return uuid.uuid4().hex[:12]


@dataclass(frozen=True)
class Limits:
    """What one task may use; the service enforces turns and time, Luna the money."""
    max_turns: int = 8
    max_seconds: float = 120.0
    max_usd: float = 0.05

    def within(self, cap):
        """These limits, but never above `cap` (the settings)."""
        return Limits(min(self.max_turns, cap.max_turns), min(self.max_seconds, cap.max_seconds),
                      min(self.max_usd, cap.max_usd))

    def as_dict(self):
        return {"max_turns": self.max_turns, "max_seconds": self.max_seconds,
                "max_usd": self.max_usd}


@dataclass(frozen=True)
class Specialist:
    """A kind of helper agent (data). `description` tells the AI when to pick it.

    The task service builds the agent from the rest: `instructions`, `model_role` (picks the
    model: LUNA_AGENT_MODEL_<ROLE>), `tools` (names of Luna's tools it may ask to use, always
    through Luna's permission check) and `hosted` (OpenAI-hosted tools such as web search).
    """
    name: str
    description: str
    title: str = ""
    limits: Limits = field(default_factory=Limits)
    instructions: str = ""
    model_role: str = ""
    tools: tuple = ()
    hosted: tuple = ()


@dataclass(frozen=True)
class Task:
    id: str
    goal: str
    specialist: str
    language: str = "en"
    state: str = QUEUED
    created: str = ""   # local time, ISO (seconds)
    updated: str = ""
    summary: str = ""
    details: str = ""
    error: str = ""     # an error class or budget / time / turns / offline / interrupted
    cost_usd: float = 0.0
    tainted: bool = False
    run_state: str = ""  # a paused run (step 4)
    announced: bool = False

    @property
    def active(self):
        return self.state in ACTIVE

    @property
    def final(self):
        return self.state in FINAL
