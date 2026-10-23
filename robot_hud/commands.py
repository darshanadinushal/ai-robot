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
