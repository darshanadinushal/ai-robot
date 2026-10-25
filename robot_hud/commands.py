"""Map spoken words to robot reactions (pure Python, no audio dependencies)."""

EXPRESSIONS = ("neutral", "happy", "sad", "angry", "sleepy", "surprised")

KEYWORDS = {
    "happy": ("expression", "happy"), "smile": ("expression", "happy"),
    "glad": ("expression", "happy"),
    "sad": ("expression", "sad"), "cry": ("expression", "sad"),
    "angry": ("expression", "angry"), "mad": ("expression", "angry"),
    "sleepy": ("expression", "sleepy"), "sleep": ("expression", "sleepy"),
    "tired": ("expression", "sleepy"),
    "surprised": ("expression", "surprised"), "surprise": ("expression", "surprised"),
    "wow": ("expression", "surprised"),
    "neutral": ("expression", "neutral"), "normal": ("expression", "neutral"),
    "relax": ("expression", "neutral"),
    "left": ("look", (-1.0, 0.0)), "right": ("look", (1.0, 0.0)),
    "up": ("look", (0.0, -1.0)), "down": ("look", (0.0, 1.0)),
    "center": ("look", (0.0, 0.0)), "centre": ("look", (0.0, 0.0)),
    "blink": ("blink", None),
    "listen": ("wave", "listening"), "listening": ("wave", "listening"),
    "speak": ("wave", "speaking"), "speaking": ("wave", "speaking"),
    "talk": ("wave", "speaking"),
    "quiet": ("wave", "quiet"), "silence": ("wave", "quiet"), "mute": ("wave", "quiet"),
    "face": ("mode", "FACE"), "wave": ("mode", "WAVE"),
    "cyan": ("color", "CYAN"), "purple": ("color", "PURPLE"),
    "green": ("color", "GREEN"), "amber": ("color", "AMBER"),
}

# Extra words the recogniser may hear without triggering anything.
FILLER_WORDS = ("look", "luna", "mode", "be", "go", "turn")

VOCABULARY = sorted(set(KEYWORDS) | set(FILLER_WORDS))


def parse_command(text):
    """Return a list of (kind, value) actions found in the recognised text."""
    actions = []
    for word in str(text).lower().replace(",", " ").split():
        action = KEYWORDS.get(word)
        if action is not None:
            actions.append(action)
    return actions


# How the offline recogniser may spell "Luna".
WAKE_WORDS = ("luna", "lunar", "loona", "looner")
# Includes common speech-recognition mishearings of "hey".
GREETINGS = ("hey", "hi", "hello", "ok", "okay", "he", "hay", "a", "eh", "oh")
# Without the wake word, only short phrases count as commands (ignores background talk).
MAX_COMMAND_WORDS = 4


def _words(text):
    cleaned = "".join(c if c.isalnum() or c == "'" else " " for c in str(text).lower())
    return cleaned.split()


def route(text, conversation=False):
    """Decide what to do with recognised speech.

    Returns ("command", actions), ("ai", question), ("wake", "") or ("ignore", "").
    `conversation` is True right after Luna answered, so follow-ups need no wake word.
    """
    words = _words(text)
    woke = any(w in WAKE_WORDS for w in words)
    if woke:
        words = [w for w in words if w not in WAKE_WORDS]
        while words and words[0] in GREETINGS:
            words = words[1:]
    if not words:
        return ("wake", "") if woke else ("ignore", "")
    actions = parse_command(" ".join(words))
    if actions and len(words) <= MAX_COMMAND_WORDS:
        return ("command", actions)
    if woke or conversation:
        return ("ai", " ".join(words))
    return ("ignore", "")


_LOOK_NAMES = {(-1.0, 0.0): "left", (1.0, 0.0): "right", (0.0, -1.0): "up",
               (0.0, 1.0): "down", (0.0, 0.0): "ahead"}


def reply_for(actions):
    """Short spoken confirmation for the first recognised action, or None."""
    if not actions:
        return None
    kind, value = actions[0]
    if kind == "expression":
        return "Okay, I feel %s." % value if value != "neutral" else "Okay, back to normal."
    if kind == "look":
        return "Looking %s." % _LOOK_NAMES.get(value, "around")
    if kind == "blink":
        return "Done."
    if kind == "wave":
        return {"listening": "I am listening.", "speaking": "I am speaking.",
                "quiet": "Okay, quiet."}[value]
    if kind == "mode":
        return "Switching to %s mode." % value.lower()
    if kind == "color":
        return "Colour changed to %s." % value.lower()
    return None
