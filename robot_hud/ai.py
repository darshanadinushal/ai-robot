"""Online AI brain for Luna (OpenAI Responses API).

The API key is read from the OPENAI_API_KEY environment variable, or from the optional
file ~/.luna.env (outside the repository). Keys are never logged or written anywhere.
"""
import os
import queue
import threading
import time
from pathlib import Path

from .assistant_core.context import BASE_INSTRUCTIONS, instructions
from .speech import SentenceStream

DEFAULT_MODEL = "gpt-6-luna"
INSTRUCTIONS = BASE_INSTRUCTIONS  # built by assistant_core.context
LOST_CONNECTION = "Sorry, I lost my connection."
ENV_FILE = Path.home() / ".luna.env"
_ALLOWED_PREFIXES = ("OPENAI_", "LUNA_")


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


class StreamError(Exception):
    """The AI stream failed or ended without a complete answer."""


class LunaBrain:
    """Talks to the OpenAI Responses API.

    Conversation history is the id of the last *completed* answer (`previous_id`). A streamed
    answer is only added with `remember()` once Luna has spoken it all, so an interrupted or
    failed answer is never part of the history.
    """

    def __init__(self, model=None):
        from openai import OpenAI  # imported lazily: the package is optional

        load_env_file()
        self.model = model or os.environ.get("LUNA_AI_MODEL", DEFAULT_MODEL)
        self.client = OpenAI(timeout=30.0, max_retries=1)
        self.previous_id = None
        self.completed_id = None

    def _args(self, question, language):
        # Luna answers in the language she listens and speaks in (the voice language).
        args = {"model": self.model, "instructions": instructions(language), "input": question}
        if self.previous_id:
            args["previous_response_id"] = self.previous_id
        return args

    def ask(self, question, language="en"):
        """The whole answer at once (used by scripts/test_ai.py); remembered straight away."""
        response = self.client.responses.create(**self._args(question, language))
        self.previous_id = response.id
        return response.output_text.strip()

    def stream(self, question, language="en"):
        """Yield the answer text in pieces as it is generated.

        Afterwards `completed_id` is the answer's id, or StreamError is raised if the answer did
        not complete. Closing the generator early closes the connection.
        """
        self.completed_id = None
        events = self.client.responses.create(**self._args(question, language), stream=True)
        try:
            for event in events:
                kind = getattr(event, "type", "")
                if kind == "response.output_text.delta":
                    yield event.delta
                elif kind == "response.completed":
                    self.completed_id = event.response.id
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
    """Streams one answer at a time in a background thread, sentence by sentence.

    `ask()` returns a request id (or False while busy). The UI polls `answers` for
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
        self._questions = queue.Queue()
        self.answers = queue.Queue()
        self._busy = threading.Event()
        self._lock = threading.Lock()
        self._next_id = 0
        self._cancelled = set()
        self._pending = None  # (request id, response id) waiting for confirm()
        threading.Thread(target=self._run, daemon=True).start()

    @property
    def busy(self):
        return self._busy.is_set()

    def ask(self, question, language="en"):
        if self.busy:
            return False
        self._busy.set()
        with self._lock:
            self._next_id += 1
            request = self._next_id
        self._questions.put((request, question, language))
        return request

    def cancel(self, request):
        """Stop a request now (the user interrupted Luna). Silent: no error is reported."""
        with self._lock:
            self._cancelled.add(request)
            if self._pending and self._pending[0] == request:
                self._pending = None
            if request == self._next_id:
                self._busy.clear()

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

    def _run(self):
        while True:
            request, question, language = self._questions.get()
            spoke = []  # one entry per sentence sent, so errors know if Luna already talked
            try:
                if self._brain is None:
                    self._brain = self._factory()
                self._handle(request, question, language, spoke)
            except Exception as error:  # network, key, model or package problems
                if not self._stopped(request):
                    print("Luna AI error:", type(error).__name__, flush=True)
                    if spoke:
                        self._send(request, "lost")
                    else:
                        self._send(request, "error", friendly_error(error))
            finally:
                with self._lock:
                    if request == self._next_id:
                        self._busy.clear()

    def _handle(self, request, question, language, spoke):
        started = time.monotonic()
        if hasattr(self._brain, "stream"):
            pieces = self._brain.stream(question, language)
        else:  # a brain that only answers in one piece (already remembered by ask())
            pieces = [self._brain.ask(question, language)]
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
        completed = getattr(self._brain, "completed_id", None) if hasattr(self._brain, "stream") \
            else None
        with self._lock:
            self._pending = (request, completed) if completed else None
        self._send(request, "done")

    def _speak(self, request, sentences, spoke, started):
        for sentence in sentences:
            if self._send(request, "sentence", sentence):
                if not spoke:
                    print("Luna AI: first sentence after %.1f s" % (time.monotonic() - started),
                          flush=True)
                spoke.append(sentence)
