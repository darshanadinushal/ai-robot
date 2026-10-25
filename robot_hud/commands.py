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

# Swedish command words (used when Luna listens in Swedish).
SWEDISH_KEYWORDS = {
    "glad": ("expression", "happy"), "lycklig": ("expression", "happy"),
    "ledsen": ("expression", "sad"), "gråt": ("expression", "sad"), "gråta": ("expression", "sad"),
    "arg": ("expression", "angry"), "sur": ("expression", "angry"),
    "trött": ("expression", "sleepy"), "sömnig": ("expression", "sleepy"),
    "sova": ("expression", "sleepy"),
    "förvånad": ("expression", "surprised"), "oj": ("expression", "surprised"),
    "neutral": ("expression", "neutral"), "normal": ("expression", "neutral"),
    "vänster": ("look", (-1.0, 0.0)), "höger": ("look", (1.0, 0.0)),
    "upp": ("look", (0.0, -1.0)), "ner": ("look", (0.0, 1.0)), "ned": ("look", (0.0, 1.0)),
    "mitten": ("look", (0.0, 0.0)), "framåt": ("look", (0.0, 0.0)),
    "blinka": ("blink", None),
    "lyssna": ("wave", "listening"), "lyssnar": ("wave", "listening"),
    "prata": ("wave", "speaking"), "tala": ("wave", "speaking"),
    "tyst": ("wave", "quiet"),
    "ansikte": ("mode", "FACE"), "ansiktet": ("mode", "FACE"),
    "våg": ("mode", "WAVE"), "vågen": ("mode", "WAVE"),
    "cyan": ("color", "CYAN"), "lila": ("color", "PURPLE"),
    "grön": ("color", "GREEN"), "grönt": ("color", "GREEN"),
    "bärnsten": ("color", "AMBER"), "orange": ("color", "AMBER"),
}
COMMAND_WORDS = {"en": KEYWORDS, "sv": SWEDISH_KEYWORDS}

# Extra words the recogniser may hear without triggering anything.
FILLER_WORDS = ("look", "luna", "mode", "be", "go", "turn")
SWEDISH_FILLER_WORDS = ("titta", "läge", "bli", "gå", "vänd", "hej", "färg", "åt")

VOCABULARY = sorted(set(KEYWORDS) | set(FILLER_WORDS))


def vocabulary(language="en"):
    """Words the command-only recogniser listens for in `language`."""
    if language == "sv":
        return sorted(set(SWEDISH_KEYWORDS) | set(SWEDISH_FILLER_WORDS) | set(SWEDISH_WAKE_WORDS))
    return VOCABULARY


def parse_command(text, language="en"):
    """Return a list of (kind, value) actions found in the recognised text."""
    keywords = COMMAND_WORDS.get(language, KEYWORDS)
    actions = []
    for word in str(text).lower().replace(",", " ").split():
        action = keywords.get(word)
        if action is not None:
            actions.append(action)
    return actions


# How the offline recogniser may spell "Luna". The Swedish model has no word "luna";
# in tests with Piper's Swedish voices it heard these words instead.
WAKE_WORDS = ("luna", "lunar", "loona", "looner")
SWEDISH_WAKE_WORDS = ("lugn", "lugna", "lund", "lunch", "låna", "luna",
                      "helene", "helena")  # "hej luna" heard as one word
# Includes common speech-recognition mishearings of "hey".
GREETINGS = ("hey", "hi", "hello", "ok", "okay", "he", "hay", "a", "eh", "oh",
             "hej", "hallå", "okej", "tja")
# Without the wake word, only short phrases count as commands (ignores background talk).
MAX_COMMAND_WORDS = 4


def _words(text):
    cleaned = "".join(c if c.isalnum() or c == "'" else " " for c in str(text).lower())
    return cleaned.split()


def route(text, conversation=False, language="en"):
    """Decide what to do with recognised speech.

    Returns ("command", actions), ("ai", question), ("wake", "") or ("ignore", "").
    `conversation` is True right after Luna answered, so follow-ups need no wake word.
    """
    words = _words(text)
    wake_words = SWEDISH_WAKE_WORDS if language == "sv" else WAKE_WORDS
    woke = any(w in wake_words for w in words)
    if woke:
        words = [w for w in words if w not in wake_words]
        while words and words[0] in GREETINGS:
            words = words[1:]
    if not words:
        return ("wake", "") if woke else ("ignore", "")
    actions = parse_command(" ".join(words), language)
    if actions and len(words) <= MAX_COMMAND_WORDS:
        return ("command", actions)
    if woke or conversation:
        return ("ai", " ".join(words))
    return ("ignore", "")


_LOOK_NAMES = {(-1.0, 0.0): "left", (1.0, 0.0): "right", (0.0, -1.0): "up",
               (0.0, 1.0): "down", (0.0, 0.0): "ahead"}


_SWEDISH_MOODS = {"happy": "glad", "sad": "ledsen", "angry": "arg", "sleepy": "trött",
                  "surprised": "förvånad"}
_SWEDISH_LOOKS = {"left": "åt vänster", "right": "åt höger", "up": "upp", "down": "ner",
                  "ahead": "framåt", "around": "runt"}
_SWEDISH_COLORS = {"CYAN": "cyan", "PURPLE": "lila", "GREEN": "grön", "AMBER": "bärnsten"}


def reply_for(actions, language="en"):
    """Short spoken confirmation for the first recognised action, or None."""
    if not actions:
        return None
    kind, value = actions[0]
    if language == "sv":
        return _swedish_reply(kind, value)
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


def _swedish_reply(kind, value):
    if kind == "expression":
        return ("Okej, jag är %s." % _SWEDISH_MOODS[value] if value != "neutral"
                else "Okej, tillbaka till normalt.")
    if kind == "look":
        return "Tittar %s." % _SWEDISH_LOOKS[_LOOK_NAMES.get(value, "around")]
    if kind == "blink":
        return "Klart."
    if kind == "wave":
        return {"listening": "Jag lyssnar.", "speaking": "Jag pratar.",
                "quiet": "Okej, tyst."}[value]
    if kind == "mode":
        return "Byter till %s." % ("ansiktsläge" if value == "FACE" else "vågläge")
    if kind == "color":
        return "Färgen är nu %s." % _SWEDISH_COLORS[value]
    return None


# Luna's other spoken sentences, in Swedish.
SWEDISH_SPEECH = {
    "Yes?": "Ja?",
    "My AI brain is not set up yet.": "Min AI-hjärna är inte installerad än.",
    "I am not sure what to say.": "Jag vet inte riktigt vad jag ska säga.",
    "My AI brain is not installed yet.": "Min AI-hjärna är inte installerad än.",
    "My AI key is not working. Please check it.": "Min AI-nyckel fungerar inte. Kontrollera den.",
    "I cannot reach the internet right now.": "Jag når inte internet just nu.",
    "My AI model is not available. Please check the model name.":
        "Min AI-modell är inte tillgänglig. Kontrollera modellnamnet.",
    "My AI brain is busy or out of credit. Please try again later.":
        "Min AI-hjärna är upptagen eller saknar kredit. Försök igen senare.",
    "Sorry, my AI brain had a problem.": "Förlåt, min AI-hjärna fick ett problem.",
}


def spoken(text, language="en"):
    """Luna's fixed spoken sentences in the listening/voice language."""
    return SWEDISH_SPEECH.get(text, text) if language == "sv" else text
