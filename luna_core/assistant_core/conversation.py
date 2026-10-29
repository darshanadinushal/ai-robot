"""Conversation coordinator: complete utterances and AI messages in, effects out.

Runs on the UI thread (the app calls it every frame); the AI itself runs in AIWorker's thread.
Only answers for the current Turn of the current Session are used, so cancelled, stalled or
old answers can never reach a new question.
"""
import queue
from datetime import datetime

from ..ai import LOST_CONNECTION
from ..commands import route, spoken
from .context import WEB_SEARCH, ConversationSettings, instructions
from .events import Accepted, Act, Log, Mood, Speak, StopSpeech
from .expression import LINGER_SECONDS
from .memory import ERRORS as MEMORY_ERRORS, is_birthday, memory_tools
from .session import FOLLOWUP_SECONDS, QUESTION_FOLLOWUP_SECONDS, Session, Turn
from .tools import FACE_TOOLS, PermissionPolicy, ToolExecutor, ToolRegistry
from .turn_taking import interruption

HEARD_SECONDS = 8.0    # how long the screen shows what Luna heard
ANSWER_SECONDS = 20.0  # how long it shows her answer after the last piece arrived


class Conversation:
    """Routes speech to offline commands or the AI and follows one answer to its end.

    `ai` is an AIWorker (or a fake with ask/cancel/confirm/forget and an `answers` queue).
    History: an answer is confirmed (kept by the AI) only after Luna has spoken all of it.
    A session ends after `settings.idle_seconds` of real quiet, after `settings.max_turns`
    answers, or when the user asks for a new conversation; the AI history starts fresh then.
    `clock` gives the local wall-clock time for the instructions.
    Tools: the AI proposes calls, `tools` (a ToolExecutor) validates, permits and runs them
    here on the UI thread; their results go back to the AI on the next poll, after the app
    has applied the effects.
    Expressions: `expression` is the mood of the current answer (its "[mood]" tag), shown while
    Luna answers and LINGER_SECONDS after; the user's own face settings are never changed.
    A mood set by a tool this turn wins. `slow`: no answer after `settings.progress_seconds`,
    so Luna said "One moment." (once per question).
    Memory: `memory` is a MemoryStore (None: off). Its relevant facts go into the instructions
    and its tools join the face tools (see set_memory).
    """

    def __init__(self, ai=None, settings=None, clock=datetime.now, tools=None, memory=None):
        self.ai = ai
        self.settings = settings or ConversationSettings()
        self.clock = clock
        self._own_tools = tools is None
        self.tools = tools or ToolExecutor(ToolRegistry(FACE_TOOLS))
        self.plugin_tools, self.plugin_risks, self.plugin_instructions = (), (), ()
        self.memory = None
        self.set_memory(memory)
        self._tool_outputs = None  # (request, [(call_id, output)]) to send on the next poll
        self.session = Session()
        self.turn = None               # the question being answered or spoken
        self.streaming = False         # its answer is still arriving
        self.followup_pending = False  # open the follow-up window once Luna is silent
        self.thinking = False
        self.heard = ""
        self.heard_until = 0.0
        self.heard_kind = ""
        self.question = ""
        self.answer = ""
        self.answer_until = 0.0
        self._confirmed = False
        self._audible = False
        self._asked_back = False  # Luna's answer ended with a question to the user
        self.expression = None    # the answer's mood, drawn above the user's mood
        self._expression_until = None
        self._tool_mood = False   # a tool changed the face this turn: ignore the answer's tag
        self.slow = False
        self._birthday_wished = None  # the date Luna last wished happy birthday

    def set_memory(self, memory):
        """Switch memory on (a MemoryStore) or off (None); saved facts are kept either way."""
        self.memory = memory
        if self._own_tools:
            extra = memory_tools(memory) if memory is not None else ()
            self.tools = ToolExecutor(ToolRegistry(FACE_TOOLS + tuple(extra) + tuple(self.plugin_tools)),
                                      PermissionPolicy(("local", "memory") + tuple(self.plugin_risks)))

    def set_plugins(self, tools=(), risks=(), instructions=()):
        """The running plugins' AI tools (with the risk classes they were allowed) and personality."""
        self.plugin_tools, self.plugin_risks = tuple(tools), tuple(risks)
        self.plugin_instructions = tuple(instructions)
        self.set_memory(self.memory)

    def in_followup(self, t):
        return self.ai is not None and self.session.in_followup(t)

    def muted(self, speaking):
        """Keep the microphone off while Luna speaks and between streamed sentences."""
        return speaking or self.streaming

    def hear(self, text, t, heard_language, talk_language, conversation=None):
        """One complete utterance. `conversation`: the follow-up window was open when it began."""
        self.heard = text
        self.heard_until = t + HEARD_SECONDS
        if conversation is None:
            conversation = self.in_followup(t)
        kind, value = route(text, conversation=conversation, language=heard_language)
        self.heard_kind = kind
        if kind != "ignore":
            self.session.touch(t)
        if kind == "control":
            return self.control(value, talk_language)
        if kind == "command":
            return [Act(tuple(value))]
        if kind == "wake":
            self.followup_pending = True
            return [Speak(spoken("Yes?", talk_language))]
        if kind == "ai":
            return self.ask(value, t, talk_language)
        return []

    def control(self, name, language):
        """"Never mind" / "cancel": stop listening for a follow-up. "New conversation": forget.
        "Stop": silence Luna without a word."""
        if name == "new_conversation":
            effects = self.new_session()
            self.followup_pending = True  # listen for the first question straight away
            return effects + [Log("Luna: new conversation (asked by the user)"),
                              Speak(spoken("Okay, let's start a new conversation.", language))]
        effects = self.interrupt()
        self.session.close_followup()
        if name == "stop":
            return effects + [Log("Luna: stopped by voice")]
        return effects + [Speak(spoken("Okay.", language))]

    def barge_in(self, text, language):
        """Heard while Luna talks (voice interruption on): ("stop" | "wake" | None, effects).

        Either one silences her and drops the unfinished answer; after "wake" the question that
        follows is heard as usual.
        """
        kind = interruption(text, language, self.answer)
        if kind is None:
            return None, []
        return kind, self.interrupt() + [Log("Luna: interrupted by voice (%s)" % kind)]

    def ask(self, question, t, language):
        if self.ai is None:
            return [Speak(spoken("My AI brain is not set up yet.", language))]
        effects = []
        if not self.streaming and self.session.turns >= self.settings.max_turns:
            effects = self._fresh("after %d answers" % self.session.turns)
        use_tools = self.settings.tools
        web = self.settings.web_search
        memories, about = None, {}
        if self.memory is not None:
            try:
                memories = self.memory.relevant(question, language)
                about = self.memory.profile()
            except MEMORY_ERRORS as error:
                memories = None
                effects.append(Log("Luna memory: could not be read (%s)" % type(error).__name__))
        now = self.clock()
        wish = (is_birthday(about.get("birthday"), now.date())
                and self._birthday_wished != now.date())
        request = self.ai.ask(
            question, language,
            instructions(language, now, self.settings.profile, use_tools,
                         self.settings.expressions, memories, about, wish, web,
                         self.plugin_instructions),
            tools=((self.tools.registry.schemas() if use_tools else [])
                   + ([WEB_SEARCH] if web else [])) or None)
        if not request:
            return []  # still answering the previous question
        if wish:
            self._birthday_wished = now.date()
        self.tools.new_turn(question, language)
        self._tool_outputs = None
        self.session.touch(t)
        self.turn = Turn(self.session.id, request, question, language, t)
        self._confirmed = self._audible = self._asked_back = False
        self._tool_mood = self.slow = False
        self._clear_expression()
        self.streaming = True
        self.followup_pending = False
        self.thinking = True
        self.heard_until = t + 3600
        self.question = question
        self.answer = ""
        self.session.close_followup()
        return effects + [Accepted(question)]

    def poll(self, t, speaking, talking=False, listening=False):
        """Handle the AI's messages; once the answer was spoken, keep it and open the follow-up.

        `speaking`: Luna talks or just stopped (speech.is_speaking); `talking`: audio is playing;
        `listening`: the user is in the middle of an utterance.
        """
        effects = []
        if self._tool_outputs is not None:  # applied by the app since the last poll
            request, outputs = self._tool_outputs
            self._tool_outputs = None
            if self._current(request):
                self.ai.tool_results(request, outputs)
        if self.ai is not None:
            while True:
                try:
                    kind, request, value = self.ai.answers.get_nowait()
                except queue.Empty:
                    break
                if self._current(request):
                    effects += self._answer(kind, value, t)
        turn = self.turn
        if turn is not None and talking and not self._audible:
            self._audible = True
            effects.append(Log("Luna: first spoken reply %.1f s after the question"
                               % (t - turn.asked_at)))
        if (turn is not None and self.thinking and not self.slow and not self._audible
                and not speaking and self.settings.progress_seconds > 0
                and t - turn.asked_at >= self.settings.progress_seconds):
            self.slow = True
            effects.append(Speak(spoken("One moment.", turn.language)))
        self._expression_lifecycle(t, speaking)
        if speaking or listening:
            self.session.touch(t)
        if self.followup_pending and not speaking:
            self.followup_pending = False
            if self.ai is not None and turn is not None and not self._confirmed:
                self._confirmed = True
                self.ai.confirm(turn.request)
                self.session.turns += 1
            self.session.open_followup(
                t, QUESTION_FOLLOWUP_SECONDS if self._asked_back else FOLLOWUP_SECONDS)
            self._asked_back = False
        elif self._idle(t, speaking, listening):
            effects += self._fresh("after %.0f s without talking" % self.session.idle_for(t))
        return effects

    def interrupt(self):
        """The user stopped Luna: silence her and drop the unfinished answer (not remembered)."""
        self.followup_pending = False
        if self.turn is not None or self.streaming:
            if self.ai is not None and self.turn is not None:
                self.ai.cancel(self.turn.request)
            self.turn = None
            self._tool_outputs = None
            self.streaming = False
            self.thinking = False
        self._clear_expression()
        return [StopSpeech()]

    def new_session(self):
        """Start a fresh conversation: stop the current answer and forget the history."""
        effects = self.interrupt()
        self.session.renew()
        if self.ai is not None:
            self.ai.forget()
        return effects

    def _run_tools(self, calls):
        effects, outputs = [], []
        for call in calls:
            done, result = self.tools.execute(call)
            effects += done
            outputs.append((call.call_id, result.output()))
            effects.append(Log("Luna tool: %s -> %s" % (call.name, "ok" if result.ok
                                                       else result.message)))
            if call.name == "set_face_mood" and result.ok:
                self._tool_mood = True
                self._clear_expression()
        self._tool_outputs = (self.turn.request, outputs)
        return effects

    def _idle(self, t, speaking, listening):
        """Nothing is going on, and there is a conversation that has been quiet long enough."""
        seconds = self.settings.idle_seconds
        busy = speaking or listening or self.streaming or self.followup_pending
        has_history = self.session.turns or self.turn is not None
        return (seconds > 0 and has_history and not busy
                and self.session.idle_for(t) >= seconds)

    def _expression_lifecycle(self, t, speaking):
        if self.expression is None:
            return
        if self.streaming or speaking:
            self._expression_until = None
        elif self._expression_until is None:
            self._expression_until = t + LINGER_SECONDS
        elif t >= self._expression_until:
            self._clear_expression()

    def _clear_expression(self):
        self.expression = None
        self._expression_until = None

    def _fresh(self, reason):
        """Start a new session quietly (nothing is playing or being answered)."""
        self.turn = None
        self._clear_expression()
        self.session.renew()
        if self.ai is not None:
            self.ai.forget()
        return [Log("Luna: new conversation " + reason)]

    def _current(self, request):
        turn = self.turn
        return turn is not None and request == turn.request and turn.session == self.session.id

    def _answer(self, kind, value, t):
        """One AIWorker message for the current turn (see AIWorker for the kinds)."""
        language = self.turn.language
        if kind == "mood":
            if self.settings.expressions and not self._tool_mood and value != "neutral":
                self.expression = value
                self._expression_until = None
            return []
        self.thinking = False
        self.session.touch(t)
        self.answer_until = t + ANSWER_SECONDS
        self.heard_until = t + ANSWER_SECONDS
        if kind == "text":
            self.answer += value  # each piece arrives once, in order
        elif kind == "sentence":
            return [Speak(value)]
        elif kind == "tools":
            self.thinking = True  # the spoken answer comes after the tools
            self.answer = ""      # text beside a tool call is not shown or spoken
            return self._run_tools(value)
        elif kind == "say":
            text = spoken(value, language)
            self.answer = self.answer or text
            return [Speak(text)]
        elif kind == "done":
            self.streaming = False
            self.followup_pending = True
            self._asked_back = self.answer.rstrip().endswith(("?", "\uff1f"))
        elif kind in ("error", "lost"):
            self.streaming = False
            text = spoken(value if kind == "error" else LOST_CONNECTION, language)
            self.answer = text if kind == "error" else (self.answer.rstrip() + " " + text).strip()
            self._clear_expression()
            return [Mood("sad", 4.0), Speak(text)]
        return []
