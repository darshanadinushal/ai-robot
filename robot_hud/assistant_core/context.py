"""What Luna is told with every question."""
from .. import languages

BASE_INSTRUCTIONS = (
    "You are Luna, a friendly AI robot with a touchscreen face. "
    "Your reply is spoken aloud, so answer in one to three short sentences of plain text, "
    "with no lists, markdown, emojis or code."
)


def instructions(language="en"):
    """Instructions for one question; Luna answers in the voice language."""
    return BASE_INSTRUCTIONS + " " + languages.get(language).ai_instruction
