"""The robot's name (Settings > About > Robot name; default "Luna"). Pure Python.

The name is set once at start-up (the app restarts after it changes). Fixed texts are written
with "Luna" and `personalise` puts the chosen name in: screen texts (via i18n.tr), the AI's
personality, the voice sample and the transcription hint. The wake word is the name itself
(commands.find_wake); only "Luna" also has the packs' misheard variants.
"""
import re

DEFAULT_NAME = "Luna"
MIN_LETTERS, MAX_LETTERS = 2, 12

name = DEFAULT_NAME


def clean_name(text):
    """A valid robot name (one word of 2-12 letters, capitalised), or None."""
    text = str(text or "").strip()
    if not (MIN_LETTERS <= len(text) <= MAX_LETTERS) or not text.isalpha():
        return None
    return text[0].upper() + text[1:].lower()


def set_name(new_name):
    global name
    name = clean_name(new_name) or DEFAULT_NAME


def is_default():
    return name == DEFAULT_NAME


def personalise(text):
    """`text` with "Luna" / "LUNA" replaced by the robot's name."""
    if is_default() or not text:
        return text
    text = re.sub(r"\bLUNA\b", name.upper(), text)
    return re.sub(r"\bLuna\b", name, text)
