"""Effects returned by the assistant core; the app carries them out on the UI thread."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Speak:
    """Say this text (already in the voice language), after anything already queued."""
    text: str


@dataclass(frozen=True)
class Act:
    """Apply robot actions: (kind, value) pairs from commands.route or a tool.
    `quiet`: no spoken confirmation (a tool's result is reported by the AI's answer)."""
    actions: tuple
    quiet: bool = False


@dataclass(frozen=True)
class StopSpeech:
    """Silence Luna now and drop queued sentences."""


@dataclass(frozen=True)
class Accepted:
    """A question was sent to the AI: show a calm, listening face while she thinks."""
    question: str


@dataclass(frozen=True)
class Mood:
    """Show a temporary facial expression."""
    mood: str
    seconds: float


@dataclass(frozen=True)
class Log:
    """A diagnostic line for the log (never contains keys or API error messages)."""
    message: str
