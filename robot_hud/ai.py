"""Online AI brain for Luna (OpenAI Responses API).

The API key is read from the OPENAI_API_KEY environment variable, or from the optional
file ~/.luna.env (outside the repository). Keys are never logged or written anywhere.
"""
import os
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

from .assistant_core.context import BASE_INSTRUCTIONS, instructions
from .speech import SentenceStream

DEFAULT_MODEL = "gpt-6-luna"
INSTRUCTIONS = BASE_INSTRUCTIONS  # built by assistant_core.context
LOST_CONNECTION = "Sorry, I lost my connection."
ENV_FILE = Path.home() / ".luna.env"
_ALLOWED_PREFIXES = ("OPENAI_", "LUNA_")
# Values of the Responses API "reasoning.effort"; not every model accepts every value.
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")


def load_env_file(path=ENV_FILE):
    """Load KEY=VALUE lines (OPENAI_* / LUNA_* only) without overriding the environment."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key.startswith(_ALLOWED_PREFIXES) and value and key not in os.environ:
            os.environ[key] = value


def ai_configured():
    load_env_file()
    return bool(os.environ.get("OPENAI_API_KEY"))


def friendly_error(error):
    """Short spoken explanation of a failure; never includes secrets."""
    name = type(error).__name__
    if isinstance(error, ImportError):
        return "My AI brain is not installed yet."
    if name in ("AuthenticationError", "PermissionDeniedError"):
        return "My AI key is not working. Please check it."
    if name in ("APIConnectionError", "APITimeoutError"):
        return "I cannot reach the internet right now."
    if name == "NotFoundError":
        return "My AI model is not available. Please check the model name."
    if name == "RateLimitError":
        return "My AI brain is busy or out of credit. Please try again later."
    return "Sorry, my AI brain had a problem."


def reasoning_effort(env=None):
    """LUNA_AI_REASONING if it is a known effort; empty = the model's default (not sent)."""
    value = (os.environ if env is None else env).get("LUNA_AI_REASONING", "").strip().lower()
    if value and value not in REASONING_EFFORTS:
        print("Luna AI: ignoring unknown LUNA_AI_REASONING (use one of %s)"
              % ", ".join(REASONING_EFFORTS), flush=True)
        return None
    return value or None


class StreamError(Exception):
    """The AI stream failed or ended without a complete answer."""


class LunaBrain:
    """Talks to the OpenAI Responses API.

    Conversation history is the id of the last *completed* answer (`previous_id`). A streamed
    answer is only added with `remember()` once Luna has spoken it all, so an interrupted or
    failed answer is never part of the history.
    `reasoning` (LUNA_AI_REASONING) is sent only when configured; a model that does not support
    it fails with BadRequestError, which is logged, not hidden.
    """

    def __init__(self, model=None):
        from openai import OpenAI  # imported lazily: the package is optional

        load_env_file()
        self.model = model or os.environ.get("LUNA_AI_MODEL", DEFAULT_MODEL)
        self.client = OpenAI(timeout=30.0, max_retries=1)
        self.reasoning = reasoning_effort()
        self.previous_id = None
        self.completed_id = None

    def _args(self, question, language, instructions_text=None):
        # Luna answers in the language she listens and speaks in (the voice language).
        args = {"model": self.model, "input": question,
                "instructions": instructions_text or instructions(language, datetime.now())}
        if self.previous_id:
            args["previous_response_id"] = self.previous_id
        if getattr(self, "reasoning", None):
            args["reasoning"] = {"effort": self.reasoning}
        return args

    def ask(self, question, language="en", instructions_text=None):
        """The whole answer at once (used by scripts/test_ai.py); remembered straight away."""
        response = self.client.responses.create(**self._args(question, language, instructions_text))
        self.previous_id = response.id
        return response.output_text.strip()

    def stream(self, question, language="en", instructions_text=None, result=None):
        """Yield the answer text in pieces as it is generated.

        Afterwards `completed_id` (and `result["id"]` if a dict is given: safe when two streams
        overlap) is the answer's id, or StreamError is raised if the answer did not complete.
        Closing the generator early closes the connection.
        """
        self.completed_id = None
        events = self.client.responses.create(**self._args(question, language, instructions_text),
                                              stream=True)
        try:
            for event in events:
                kind = getattr(event, "type", "")
                if kind == "response.output_text.delta":
                    yield event.delta
                elif kind == "response.completed":
                    self.completed_id = event.response.id
                    if result is not None:
                        result["id"] = event.response.id
                elif kind in ("response.failed", "response.incomplete", "error"):
                    raise StreamError(kind)
        finally:
            close = getattr(events, "close", None)
            if close:
                close()
        if self.completed_id is None:
            raise StreamError("incomplete")

    def remember(self, response_id):
        """Make a completed answer the conversation history checkpoint."""
        self.previous_id = response_id

    def forget(self):
        self.previous_id = None


class AIWorker:
    """Streams one answer at a time, sentence by sentence, each in its own background thread.

    `ask()` returns a request id (or False while busy). A cancelled request frees the worker at
    once, even if its network read is stuck: the next question starts in a new thread and the
    stuck one ends silently later. The UI polls `answers` for
    (kind, request_id, value):
      ("text", id, piece)        more answer text to show (in order, never repeated)
      ("sentence", id, text)     a complete sentence to speak now
      ("say", id, sentence)      one of Luna's fixed English sentences (translate, then speak)
      ("done", id, None)         the answer completed; call confirm(id) once it was spoken
      ("lost", id, None)         the stream failed after sentences were sent: apologise
      ("error", id, sentence)    it failed before anything was said: a fixed error sentence
    `cancel(id)` stops a request: no more messages for it, and it is not added to the history.
    """

    def __init__(self, brain_factory=LunaBrain):
        self._factory = brain_factory
        self._brain = None
        self._brain_lock = threading.Lock()
        self.answers = queue.Queue()
        self._lock = threading.Lock()
        self._next_id = 0
        self._active = None  # the request being answered
        self._cancelled = set()
        self._pending = None  # (request id, response id) waiting for confirm()

    @property
    def busy(self):
        with self._lock:
            return self._active is not None

    def ask(self, question, language="en", instructions_text=None):
        with self._lock:
            if self._active is not None:
                return False
            self._next_id += 1
            request = self._active = self._next_id
        threading.Thread(target=self._run, args=(request, question, language, instructions_text),
                         daemon=True).start()
        return request

    def cancel(self, request):
        """Stop a request now (the user interrupted Luna). Silent: no error is reported."""
        with self._lock:
            self._cancelled.add(request)
            if self._pending and self._pending[0] == request:
                self._pending = None
            if self._active == request:
                self._active = None

    def confirm(self, request):
        """The answer was spoken completely: keep it in the conversation history."""
        with self._lock:
            pending, self._pending = self._pending, None
            if pending and pending[0] == request and request not in self._cancelled \
                    and self._brain is not None and hasattr(self._brain, "remember"):
                self._brain.remember(pending[1])

    def forget(self):
        if self._brain is not None:
            self._brain.forget()

    def _stopped(self, request):
        with self._lock:
            return request in self._cancelled

    def _send(self, request, kind, value=None):
        with self._lock:
            if request not in self._cancelled:
                self.answers.put((kind, request, value))
                return True
        return False

    def _get_brain(self):
        with self._brain_lock:
            if self._brain is None:
                self._brain = self._factory()
            return self._brain

    def _run(self, request, question, language, instructions_text):
        spoke = []  # one entry per sentence sent, so errors know if Luna already talked
        brain = None
        try:
            brain = self._get_brain()
            self._handle(brain, request, question, language, instructions_text, spoke)
        except Exception as error:  # network, key, model or package problems
            if not self._stopped(request):
                name = type(error).__name__
                print("Luna AI error:", name, flush=True)
                if name == "BadRequestError" and getattr(brain, "reasoning", None):
                    print("Luna AI: the model may not support LUNA_AI_REASONING=%s"
                          % brain.reasoning, flush=True)
                if spoke:
                    self._send(request, "lost")
                else:
                    self._send(request, "error", friendly_error(error))
        finally:
            with self._lock:
                if self._active == request:
                    self._active = None

    def _handle(self, brain, request, question, language, instructions_text, spoke):
        started = time.monotonic()
        result = {}
        if hasattr(brain, "stream"):
            pieces = brain.stream(question, language, instructions_text, result)
        else:  # a brain that only answers in one piece (already remembered by ask())
            pieces = [brain.ask(question, language, instructions_text)]
        sentences = SentenceStream()
        try:
            for piece in pieces:
                if self._stopped(request):
                    return
                if piece and self._send(request, "text", piece):
                    self._speak(request, sentences.feed(piece), spoke, started)
        finally:
            close = getattr(pieces, "close", None)
            if close:
                close()  # stops the download when Luna was interrupted
        if self._stopped(request):
            return
        self._speak(request, sentences.flush(), spoke, started)
        if not spoke:
            self._send(request, "say", "I am not sure what to say.")
        completed = result.get("id")
        with self._lock:
            if request not in self._cancelled:
                self._pending = (request, completed) if completed else None
        self._send(request, "done")

    def _speak(self, request, sentences, spoke, started):
        for sentence in sentences:
            if self._send(request, "sentence", sentence):
                if not spoke:
                    print("Luna AI: first sentence after %.1f s" % (time.monotonic() - started),
                          flush=True)
                spoke.append(sentence)
