"""Conversation coordinator: complete utterances and AI messages in, effects out.

Runs on the UI thread (the app calls it every frame); the AI itself runs in AIWorker's thread.
Only answers for the current Turn of the current Session are used, so cancelled, stalled or
old answers can never reach a new question.
"""
import queue

from ..ai import LOST_CONNECTION
from ..commands import route, spoken
from .events import Accepted, Act, Log, Mood, Speak, StopSpeech
from .session import Session, Turn

HEARD_SECONDS = 8.0    # how long the screen shows what Luna heard
ANSWER_SECONDS = 20.0  # how long it shows her answer after the last piece arrived


class Conversation:
    """Routes speech to offline commands or the AI and follows one answer to its end.

    `ai` is an AIWorker (or a fake with ask/cancel/confirm/forget and an `answers` queue).
    History: an answer is confirmed (kept by the AI) only after Luna has spoken all of it.
    """

    def __init__(self, ai=None):
        self.ai = ai
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
        if kind == "command":
            return [Act(tuple(value))]
        if kind == "wake":
            self.followup_pending = True
            return [Speak(spoken("Yes?", talk_language))]
        if kind == "ai":
            return self.ask(value, t, talk_language)
        return []

    def ask(self, question, t, language):
        if self.ai is None:
            return [Speak(spoken("My AI brain is not set up yet.", language))]
        request = self.ai.ask(question, language)
        if not request:
            return []  # still answering the previous question
        self.turn = Turn(self.session.id, request, question, language, t)
        self._confirmed = self._audible = False
        self.streaming = True
        self.followup_pending = False
        self.thinking = True
        self.heard_until = t + 3600
        self.question = question
        self.answer = ""
        self.session.close_followup()
        return [Accepted(question)]

    def poll(self, t, speaking, talking=False):
        """Handle the AI's messages; once the answer was spoken, keep it and open the follow-up.

        `speaking`: Luna talks or just stopped (speech.is_speaking); `talking`: audio is playing.
        """
        effects = []
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
        if self.followup_pending and not speaking:
            self.followup_pending = False
            if self.ai is not None and turn is not None and not self._confirmed:
                self._confirmed = True
                self.ai.confirm(turn.request)
            self.session.open_followup(t)
        return effects

    def interrupt(self):
        """The user stopped Luna: silence her and drop the unfinished answer (not remembered)."""
        self.followup_pending = False
        if self.turn is not None or self.streaming:
            if self.ai is not None and self.turn is not None:
                self.ai.cancel(self.turn.request)
            self.turn = None
            self.streaming = False
            self.thinking = False
        return [StopSpeech()]

    def new_session(self):
        """Start a fresh conversation: stop the current answer and forget the history."""
        effects = self.interrupt()
        self.session.renew()
        if self.ai is not None:
            self.ai.forget()
        return effects

    def _current(self, request):
        turn = self.turn
        return turn is not None and request == turn.request and turn.session == self.session.id

    def _answer(self, kind, value, t):
        """One AIWorker message for the current turn (see AIWorker for the kinds)."""
        language = self.turn.language
        self.thinking = False
        self.answer_until = t + ANSWER_SECONDS
        self.heard_until = t + ANSWER_SECONDS
        if kind == "text":
            self.answer += value  # each piece arrives once, in order
        elif kind == "sentence":
            return [Speak(value)]
        elif kind == "say":
            text = spoken(value, language)
            self.answer = self.answer or text
            return [Speak(text)]
        elif kind == "done":
            self.streaming = False
            self.followup_pending = True
        elif kind in ("error", "lost"):
            self.streaming = False
            text = spoken(value if kind == "error" else LOST_CONNECTION, language)
            self.answer = text if kind == "error" else (self.answer.rstrip() + " " + text).strip()
            return [Mood("sad", 4.0), Speak(text)]
        return []
