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

# Without the wake word, only short phrases count as commands (ignores background talk).
MAX_COMMAND_WORDS = 4

# Said in the current language when a voice switch is not possible (model or voice missing).
CANNOT_SWITCH = "Sorry, I cannot understand {language} yet."


def command_words(language="en"):
    """Spoken word -> (kind, value) for a language."""
    return {word: ACTIONS[action_id]
            for word, action_id in languages.get(language).commands.items()
            if action_id in ACTIONS}


KEYWORDS = command_words("en")


def vocabulary(language="en"):
    """Words the command-only recogniser listens for in `language`."""
    pack = languages.get(language)
    return sorted(set(pack.commands) | set(pack.filler_words) | set(pack.wake_words)
                  | switch_vocabulary(language))


def switch_vocabulary(language="en"):
    """Words of the "Luna, speak <language>" requests in `language`."""
    pack = languages.get(language)
    words = set(pack.switch_extra_words) | set(pack.wake_words) | set(pack.greetings)
    for phrases in pack.switch_phrases.values():
        for phrase in phrases:
            words.update(phrase.split())
    return words


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


def language_switch(text, language="en", conversation=False):
    """The language code if `text` explicitly asks Luna to speak another language, else None.

    `text` was heard in `language`. It must contain the wake word (or come right after an
    answer) and one of the pack's switch phrases, with only greetings, wake words and the
    pack's switch_extra_words around it: "hey luna can you speak swedish please" matches,
    "luna don't speak swedish" or "luna how do you say hello in swedish" do not.
    """
    pack = languages.get(language)
    words = _words(text)
    if not (conversation or any(w in pack.wake_words for w in words)):
        return None
    allowed = set(pack.wake_words) | set(pack.greetings) | set(pack.switch_extra_words)
    for target, phrases in pack.switch_phrases.items():
        if target not in languages.PACKS:
            continue
        for phrase in phrases:
            needed = phrase.split()
            for start in range(len(words) - len(needed) + 1):
                if words[start:start + len(needed)] == needed:
                    rest = words[:start] + words[start + len(needed):]
                    if all(w in allowed for w in rest):
                        return target
    return None


def route(text, conversation=False, language="en"):
    """Decide what to do with recognised speech.

    Returns ("command", actions), ("ai", question), ("wake", "") or ("ignore", "").
    `conversation` is True right after Luna answered, so follow-ups need no wake word.
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
    actions = parse_command(" ".join(words), language)
    if actions and len(words) <= MAX_COMMAND_WORDS:
        return ("command", actions)
    if woke or conversation:
        return ("ai", " ".join(words))
    return ("ignore", "")


def reply_for(actions, language="en"):
    """Short spoken confirmation for the first recognised action, or None."""
    if not actions:
        return None
    action_id = ACTION_IDS.get(tuple(actions[0]))
    return languages.get(language).reply(action_id) if action_id else None


def spoken(text, language="en"):
    """Luna's fixed spoken sentences in the listening/voice language."""
    return languages.get(language).say(text)
