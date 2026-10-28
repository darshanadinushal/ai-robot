"""Map spoken words to robot reactions (pure Python, no audio dependencies).

The words, wake words, greetings and replies of each language come from its language pack
(robot_hud/languages/<code>.json). Packs map words to the action IDs in ACTIONS below; new
languages need no code changes, new actions do.
"""
from . import languages

EXPRESSIONS = ("neutral", "happy", "sad", "angry", "sleepy", "surprised")

# Action ID -> (kind, value) used by the app.
ACTIONS = {"expression." + mood: ("expression", mood) for mood in EXPRESSIONS}
ACTIONS.update({
    "look.left": ("look", (-1.0, 0.0)), "look.right": ("look", (1.0, 0.0)),
    "look.up": ("look", (0.0, -1.0)), "look.down": ("look", (0.0, 1.0)),
    "look.center": ("look", (0.0, 0.0)),
    "blink": ("blink", None),
    "wave.listening": ("wave", "listening"), "wave.speaking": ("wave", "speaking"),
    "wave.quiet": ("wave", "quiet"),
    "mode.FACE": ("mode", "FACE"), "mode.WAVE": ("mode", "WAVE"),
    "color.CYAN": ("color", "CYAN"), "color.PURPLE": ("color", "PURPLE"),
    "color.GREEN": ("color", "GREEN"), "color.AMBER": ("color", "AMBER"),
})
ACTION_IDS = {action: action_id for action_id, action in ACTIONS.items()}

# Conversation controls (phrases in the packs' "controls"): stop the follow-up, start afresh,
# or stop Luna talking ("stop" also works while she talks, see assistant_core/turn_taking.py).
CONTROLS = ("cancel", "new_conversation", "stop")

# Without the wake word, only short phrases count as commands (ignores background talk).
MAX_COMMAND_WORDS = 4


def command_words(language="en"):
    """Spoken word -> (kind, value) for a language."""
    return {word: ACTIONS[action_id]
            for word, action_id in languages.get(language).commands.items()
            if action_id in ACTIONS}


KEYWORDS = command_words("en")


def vocabulary(language="en"):
    """Words the command-only recogniser listens for in `language`."""
    pack = languages.get(language)
    stop = {word for phrase in pack.controls.get("stop", ()) for word in phrase.lower().split()}
    return sorted(set(pack.commands) | set(pack.filler_words) | set(pack.wake_words) | stop)


VOCABULARY = vocabulary("en")


def parse_command(text, language="en"):
    """Return a list of (kind, value) actions found in the recognised text."""
    keywords = command_words(language)
    actions = []
    for word in str(text).lower().replace(",", " ").split():
        action = keywords.get(word)
        if action is not None:
            actions.append(action)
    return actions


def _words(text):
    cleaned = "".join(c if c.isalnum() or c == "'" else " " for c in str(text).lower())
    return cleaned.split()


def route(text, conversation=False, language="en"):
    """Decide what to do with recognised speech.

    Returns ("command", actions), ("control", name), ("ai", question), ("wake", "") or
    ("ignore", ""). `conversation` is True right after Luna answered, so follow-ups need no wake
    word. A control ("never mind", "new conversation") is the whole utterance, said with the
    wake word or in a conversation; background talk never cancels anything.
    """
    pack = languages.get(language)
    words = _words(text)
    woke = any(w in pack.wake_words for w in words)
    if woke:
        words = [w for w in words if w not in pack.wake_words]
        while words and words[0] in pack.greetings:
            words = words[1:]
    if not words:
        return ("wake", "") if woke else ("ignore", "")
    if woke or conversation:
        phrase = " ".join(words)
        for name, phrases in pack.controls.items():
            if name in CONTROLS and phrase in phrases:
                return ("control", name)
    actions = parse_command(" ".join(words), language)
    if actions and len(words) <= MAX_COMMAND_WORDS:
        return ("command", actions)
    if woke or conversation:
        return ("ai", " ".join(words))
    return ("ignore", "")


def utterance_end(text, language="en", conversation=False):
    """How long Luna should wait for more speech after `text` (words only, no punctuation).

    "command"    an exact short command ("Luna, look left"): act now
    "wake"       only the wake word: the question may follow after a pause
    "unfinished" ends on a word like "and", "the", "och", "att": wait longer
    "complete"   anything else (an apparently complete question): a short wait
    """
    pack = languages.get(language)
    words = _words(text)
    if not words:
        return "complete"
    if words[-1] in pack.continuation_words:
        return "unfinished"
    kind, _ = route(text, conversation, language)
    if kind == "wake":
        return "wake"
    if kind == "control":
        return "command"
    if kind == "command":
        known = set(pack.commands) | set(pack.filler_words) | set(pack.wake_words) | set(pack.greetings)
        if all(w in known for w in words):
            return "command"
    return "complete"


def reply_for(actions, language="en"):
    """Short spoken confirmation for the first recognised action, or None."""
    if not actions:
        return None
    action_id = ACTION_IDS.get(tuple(actions[0]))
    return languages.get(language).reply(action_id) if action_id else None


def spoken(text, language="en"):
    """Luna's fixed spoken sentences in the listening/voice language."""
    return languages.get(language).say(text)
