"""Map spoken words to robot reactions (pure Python, no audio dependencies).

The words, wake words, greetings and replies of each language come from its language pack
(luna_core/languages/<code>.json). Packs map words to the action IDs in ACTIONS below; new
languages need no code changes, new actions do.
"""
from . import languages, naming

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
CORE_ACTIONS = frozenset(ACTIONS)


def add_action(action_id, action):
    """A new action ID (plugins: ("plugin", id)); pack words can then map to it."""
    action = tuple(action)
    if action_id in ACTIONS or action in ACTION_IDS:
        raise ValueError("action already exists: " + action_id)
    ACTIONS[action_id] = action
    ACTION_IDS[action] = action_id


def remove_action(action_id):
    """Remove an added action (Luna's own actions stay)."""
    if action_id in CORE_ACTIONS:
        raise ValueError("Luna's own actions cannot be removed")
    action = ACTIONS.pop(action_id, None)
    if action is not None:
        ACTION_IDS.pop(action, None)

# Conversation controls (phrases in the packs' "controls"): stop the follow-up, start afresh,
# or stop Luna talking ("stop" also works while she talks, see assistant_core/turn_taking.py);
# ask about or stop background tasks (answered in code, see assistant_core/conversation.py).
CONTROLS = ("cancel", "new_conversation", "stop", "task_status", "cancel_task")

# Without the wake word, only short phrases count as commands (ignores background talk).
MAX_COMMAND_WORDS = 4


def command_words(language="en"):
    """Spoken word -> (kind, value) for a language."""
    return {word: ACTIONS[action_id]
            for word, action_id in languages.get(language).commands.items()
            if action_id in ACTIONS}


KEYWORDS = command_words("en")


def wake_words(language="en"):
    """How the recogniser writes the robot's name: the pack's list for "Luna", else the name."""
    if naming.is_default():
        return languages.get(language).wake_words
    return (naming.name.lower(),)


def vocabulary(language="en"):
    """Words the command-only recogniser listens for in `language`."""
    pack = languages.get(language)
    stop = {word for phrase in pack.controls.get("stop", ()) for word in phrase.lower().split()}
    return sorted(set(pack.commands) | set(pack.filler_words) | set(wake_words(language)) | stop)


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


def _sound(word):
    """A rough spelling of how a word sounds: "loona", "loner", "lunar", "lou"+"na" -> "luna"."""
    word = word.lower()
    if word.endswith("'s"):
        word = word[:-2]
    for ending in ("er", "ar", "ah", "uh", "or"):
        if word.endswith(ending) and len(word) > 3:
            word = word[:-len(ending)] + "a"
            break
    for spelling, sound in (("oo", "u"), ("ou", "u"), ("ew", "u"), ("o", "u")):
        word = word.replace(spelling, sound)
    return "".join(c for i, c in enumerate(word) if i == 0 or c != word[i - 1])


def _distance(a, b):
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        previous, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            previous, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, previous + (ca != cb))
    return row[-1]


def sounds_like_name(word):
    """True for words the recogniser may write instead of the name ("Luna": lena, loona, luma,
    lou na...).

    The word must start with the same sound, end like the name (on a vowel or not) and differ by
    at most one sound, so "lung", "lunch" or "tuna" do not count. Names shorter than 4 sounds
    ("Max") only count exactly: too many words are one sound away.
    """
    target, sound = _sound(naming.name), _sound(word)
    return (len(target) >= 4 and abs(len(sound) - len(target)) <= 1 and sound[0] == target[0]
            and (sound[-1] in "aeiouy") == (target[-1] in "aeiouy")
            and _distance(sound, target) <= 1)


def find_wake(words, language="en"):
    """Indexes of the words that are the wake word ("Luna" or how the recogniser heard it).

    The pack's `wake_words` count anywhere. Sound-alikes ("Hey Lena", "Loona, look left",
    "what time is it, Lou na") count only where a name is said: first (after a greeting) or last.
    """
    pack, names = languages.get(language), wake_words(language)
    found = {i for i, word in enumerate(words) if word in names}
    if found or not words:
        return found
    first = 0
    while first < len(words) - 1 and words[first] in pack.greetings:
        first += 1
    for i in (first, len(words) - 1):
        if sounds_like_name(words[i]):
            return {i}
    for i in (first, len(words) - 2):
        if 0 <= i < len(words) - 1 and sounds_like_name(words[i] + words[i + 1]):
            return {i, i + 1}
    return set()


def route(text, conversation=False, language="en"):
    """Decide what to do with recognised speech.

    Returns ("command", actions), ("control", name), ("ai", question), ("wake", "") or
    ("ignore", ""). `conversation` is True right after Luna answered, so follow-ups need no wake
    word. A control ("never mind", "new conversation") is the whole utterance, said with the
    wake word or in a conversation; background talk never cancels anything.
    """
    pack = languages.get(language)
    words = _words(text)
    wake = find_wake(words, language)
    woke = bool(wake)
    if woke:
        words = [w for i, w in enumerate(words) if i not in wake]
        while words and words[0] in pack.greetings:
            words = words[1:]
    if all(w in pack.noise_words for w in words):
        words = []  # "the", "huh": noise, not a question (and not worth an AI call)
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
        known = set(pack.commands) | set(pack.filler_words) | set(pack.greetings)
        wake = find_wake(words, language)
        if all(w in known for i, w in enumerate(words) if i not in wake):
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
