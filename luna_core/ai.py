"""Online AI brain for Luna (OpenAI Responses API).

The API key is read from the OPENAI_API_KEY environment variable, or from the optional
file ~/.luna.env (outside the repository). Keys are never logged or written anywhere.
"""
import logging
import os
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

from .assistant_core.context import BASE_INSTRUCTIONS, instructions
from .assistant_core.tools import ToolCall
from .assistant_core.expression import AnswerText, strip_citations
from .speech import SentenceStream

log = logging.getLogger("luna.ai")

DEFAULT_MODEL = "gpt-6-luna"
INSTRUCTIONS = BASE_INSTRUCTIONS  # built by assistant_core.context
LOST_CONNECTION = "Sorry, I lost my connection."
ENV_FILE = Path.home() / ".luna.env"
_ALLOWED_PREFIXES = ("OPENAI_", "LUNA_")
MAX_TOOL_ROUNDS = 3      # rounds of tool calls per question; the last answer must be text
TOOL_WAIT_SECONDS = 10.0  # how long the worker waits for the app to run the tools
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


def max_output_tokens(env=None):
    """LUNA_AI_MAX_OUTPUT_TOKENS: an optional cap on each answer (reasoning included); None = no cap."""
    value = (os.environ if env is None else env).get("LUNA_AI_MAX_OUTPUT_TOKENS", "").strip()
    try:
        number = int(value) if value else 0
    except ValueError:
        number = 0
    if value and number < 16:  # the API minimum
        log.warning("Luna AI: ignoring LUNA_AI_MAX_OUTPUT_TOKENS (use a number of 16 or more)")
        return None
    return number or None


def usage_numbers(usage):
    """(input, cached input, output) tokens of one response, or None if unknown."""
    try:
        details = getattr(usage, "input_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) or 0
        return int(usage.input_tokens), int(cached), int(usage.output_tokens)
    except (AttributeError, TypeError, ValueError):
        return None


def usage_text(usage):
    """Token counts of one response for the log (numbers only), or '' if unknown."""
    numbers = usage_numbers(usage)
    return "%d in (%d cached), %d out" % numbers if numbers else ""


def web_searches(response):
    """How many hosted web searches a completed response made (each is billed per call)."""
    return sum(getattr(item, "type", "") == "web_search_call"
               for item in getattr(response, "output", None) or ())


def reasoning_effort(env=None):
    """LUNA_AI_REASONING if it is a known effort; empty = the model's default (not sent)."""
    value = (os.environ if env is None else env).get("LUNA_AI_REASONING", "").strip().lower()
    if value and value not in REASONING_EFFORTS:
        log.warning("Luna AI: ignoring unknown LUNA_AI_REASONING (use one of %s)",
                    ", ".join(REASONING_EFFORTS))
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
    Instructions given as context.TurnInstructions are sent in two parts: the fixed part as
    `instructions` (the same for the whole conversation, so the provider's prompt cache can
    reuse it) and the per-question notes as a developer message before the question.
    """

    def __init__(self, model=None):
        from openai import OpenAI  # imported lazily: the package is optional

        load_env_file()
        self.model = model or os.environ.get("LUNA_AI_MODEL", DEFAULT_MODEL)
        self.client = OpenAI(timeout=30.0, max_retries=1)
        self.reasoning = reasoning_effort()
        self.max_output_tokens = max_output_tokens()
        self.previous_id = None
        self.completed_id = None

    def _args(self, question, language, instructions_text=None, tools=None, tool_choice=None,
              previous=None):
        # Luna answers in the language she listens and speaks in (the voice language).
        text = instructions_text or instructions(language, datetime.now())
        notes = getattr(text, "notes", "")
        if notes and isinstance(question, str):
            question = [{"role": "developer",
                         "content": "Notes for this question (they replace earlier notes): " + notes},
                        {"role": "user", "content": question}]
        args = {"model": self.model, "input": question,
                "instructions": getattr(text, "fixed", "") or str(text)}
        previous = previous or self.previous_id
        if previous:
            args["previous_response_id"] = previous
        if getattr(self, "reasoning", None):
            args["reasoning"] = {"effort": self.reasoning}
        if getattr(self, "max_output_tokens", None):
            args["max_output_tokens"] = self.max_output_tokens
        if tools:
            args["tools"] = tools
            args["tool_choice"] = tool_choice or "auto"
        return args

    def ask(self, question, language="en", instructions_text=None):
        """The whole answer at once (used by scripts/test_ai.py); remembered straight away."""
        response = self.client.responses.create(**self._args(question, language, instructions_text))
        self.previous_id = response.id
        return strip_citations(response.output_text).strip()

    def stream(self, question, language="en", instructions_text=None, result=None, tools=None,
               tool_choice=None, previous=None):
        """Yield the answer text in pieces as it is generated.

        Afterwards `completed_id` (and `result["id"]` if a dict is given: safe when two streams
        overlap) is the answer's id, or StreamError is raised if the answer did not complete.
        Once the response completed, `result["usage"]` is (input, cached, output) tokens and
        `result["searches"]` the number of web searches, if any (for the usage meter).
        With `tools`, function calls are collected in `result["calls"]` (ToolCall) and
        `result["calling"]` is set as soon as one starts. `question` may be a list of input items
        (tool outputs), continuing from response `previous`.
        Closing the generator early closes the connection.
        """
        self.completed_id = None
        result = {} if result is None else result
        events = self.client.responses.create(
            **self._args(question, language, instructions_text, tools, tool_choice, previous),
            stream=True)
        try:
            for event in events:
                kind = getattr(event, "type", "")
                item = getattr(event, "item", None)
                is_call = getattr(item, "type", "") == "function_call"
                if kind == "response.output_text.delta":
                    yield event.delta
                elif kind == "response.output_item.added" and is_call:
                    result["calling"] = True
                elif kind == "response.output_item.done" and is_call:
                    result["calling"] = True
                    result.setdefault("calls", []).append(
                        ToolCall(item.call_id, item.name, item.arguments or "{}"))
                elif kind == "response.completed":
                    self.completed_id = event.response.id
                    result["id"] = event.response.id
                    numbers = usage_numbers(getattr(event.response, "usage", None))
                    if numbers:
                        result["usage"] = numbers
                        log.info("Luna AI: tokens %d in (%d cached), %d out" % numbers)
                    searches = web_searches(event.response)
                    if searches:
                        result["searches"] = searches
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
      ("mood", id, mood)         the answer's leading "[mood]" tag (removed from the text)
      ("text", id, piece)        more answer text to show (in order, never repeated)
      ("sentence", id, text)     a complete sentence to speak now
      ("say", id, sentence)      one of Luna's fixed English sentences (translate, then speak)
      ("tools", id, [ToolCall])  run these (validated by the app), then call tool_results()
      ("done", id, None)         the answer completed; call confirm(id) once it was spoken
      ("lost", id, None)         the stream failed after sentences were sent: apologise
      ("error", id, sentence)    it failed before anything was said: a fixed error sentence
    `cancel(id)` stops a request: no more messages for it, and it is not added to the history.
    `meter` (tasks.budget.UsageMeter, optional) counts the tokens and web searches of every
    completed response, also of cancelled requests (they are billed too).
    Text in a response that calls tools is not spoken: the answer after the tool results is.
    """

    def __init__(self, brain_factory=LunaBrain, meter=None):
        self._factory = brain_factory
        self.meter = meter
        self._brain = None
        self._brain_lock = threading.Lock()
        self.answers = queue.Queue()
        self._lock = threading.Lock()
        self._next_id = 0
        self._active = None  # the request being answered
        self._cancelled = set()
        self._pending = None  # (request id, response id) waiting for confirm()
        self._tool_waits = {}  # request id -> (threading.Event, outputs list)

    @property
    def busy(self):
        with self._lock:
            return self._active is not None

    def ask(self, question, language="en", instructions_text=None, tools=None):
        with self._lock:
            if self._active is not None:
                return False
            self._next_id += 1
            request = self._active = self._next_id
        threading.Thread(target=self._run,
                         args=(request, question, language, instructions_text, tools),
                         daemon=True).start()
        return request

    def warm_up(self):
        """Create the AI client in the background now (importing it takes a moment on a Pi), so
        the first question is answered sooner. No request is sent; errors show on the first ask."""
        def load():
            try:
                self._get_brain()
            except Exception as error:  # reported when the user asks
                log.info("Luna AI: not ready yet (%s)", type(error).__name__)
        threading.Thread(target=load, name="luna-ai-warm-up", daemon=True).start()

    def tool_results(self, request, outputs):
        """The app ran the tools of a "tools" message: [(call_id, output text)]."""
        with self._lock:
            waiting = self._tool_waits.get(request)
            if waiting:
                waiting[1].extend(outputs)
                waiting[0].set()

    def cancel(self, request):
        """Stop a request now (the user interrupted Luna). Silent: no error is reported."""
        with self._lock:
            self._cancelled.add(request)
            if self._pending and self._pending[0] == request:
                self._pending = None
            if self._active == request:
                self._active = None
            waiting = self._tool_waits.get(request)
            if waiting:
                waiting[0].set()

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

    def _run(self, request, question, language, instructions_text, tools):
        spoke = []  # one entry per sentence sent, so errors know if Luna already talked
        brain = None
        try:
            brain = self._get_brain()
            self._handle(brain, request, question, language, instructions_text, tools, spoke)
        except Exception as error:  # network, key, model or package problems
            if not self._stopped(request):
                name = type(error).__name__
                log.error("Luna AI error: %s", name)
                if name == "BadRequestError" and getattr(brain, "reasoning", None):
                    log.warning("Luna AI: the model may not support LUNA_AI_REASONING=%s",
                                brain.reasoning)
                if name == "BadRequestError" and tools:
                    log.warning("Luna AI: the model may not support tools; LUNA_AI_TOOLS=off or "
                                "LUNA_AI_WEB_SEARCH=off turns them off")
                if spoke:
                    self._send(request, "lost")
                else:
                    self._send(request, "error", friendly_error(error))
        finally:
            with self._lock:
                if self._active == request:
                    self._active = None

    def _handle(self, brain, request, question, language, instructions_text, tools, spoke):
        started = time.monotonic()
        if not hasattr(brain, "stream"):  # a brain that answers in one piece (no tools)
            self._finish(request, [brain.ask(question, language, instructions_text)], {},
                         SentenceStream(), spoke, started, AnswerText())
            return
        given, previous = question, None
        for round_ in range(MAX_TOOL_ROUNDS + 1):
            result = {}
            options = {}
            if tools:
                options = {"tools": tools,
                           "tool_choice": "none" if round_ == MAX_TOOL_ROUNDS else "auto"}
            if previous:
                options["previous"] = previous
            sentences, tag = SentenceStream(), AnswerText()
            pieces = brain.stream(given, language, instructions_text, result, **options)
            try:
                read = self._read(request, pieces, result, sentences, spoke, started, tag)
            finally:
                self._count(brain, result)
            if not read:
                return
            if not result.get("calls"):
                self._finish(request, (), result, sentences, spoke, started, tag)
                return
            outputs = self._wait_for_tools(request, result["calls"])
            if outputs is None:
                return  # cancelled
            given = [{"type": "function_call_output", "call_id": call_id, "output": output}
                     for call_id, output in outputs]
            previous = result.get("id")

    def _read(self, request, pieces, result, sentences, spoke, started, tag):
        """Stream one response; False if the request was cancelled meanwhile."""
        try:
            for piece in pieces:
                if self._stopped(request):
                    return False
                if result.get("calling"):
                    continue  # text next to a tool call is not spoken
                self._piece(request, piece, sentences, spoke, started, tag)
        finally:
            close = getattr(pieces, "close", None)
            if close:
                close()  # stops the download when Luna was interrupted
        return not self._stopped(request)

    def _count(self, brain, result):
        """Report one response's usage to the meter (never fails the answer)."""
        if self.meter is None or not (result.get("usage") or result.get("searches")):
            return
        try:
            model = getattr(brain, "model", "") or "unknown"
            usd = self.meter.add_tokens("talk", model, *(result.get("usage") or (0, 0, 0)))
            usd += self.meter.add_calls("talk", "web_search", result.get("searches") or 0)
            log.info("Luna AI: about $%.4f; today $%.3f", usd, self.meter.spent())
        except Exception as error:  # the meter must never break a conversation
            log.warning("Luna AI: usage not counted (%s)", type(error).__name__)

    def _wait_for_tools(self, request, calls):
        event, outputs = threading.Event(), []
        with self._lock:
            self._tool_waits[request] = (event, outputs)
        try:
            if not self._send(request, "tools", list(calls)):
                return None
            if not event.wait(TOOL_WAIT_SECONDS):
                raise TimeoutError("tools")
            return None if self._stopped(request) else outputs
        finally:
            with self._lock:
                self._tool_waits.pop(request, None)

    def _piece(self, request, piece, sentences, spoke, started, tag):
        mood, text = tag.feed(piece)
        if mood:
            self._send(request, "mood", mood)
        if text and self._send(request, "text", text):
            self._speak(request, sentences.feed(text), spoke, started)

    def _finish(self, request, pieces, result, sentences, spoke, started, tag):
        for piece in pieces:
            self._piece(request, piece, sentences, spoke, started, tag)
        if self._stopped(request):
            return
        held = tag.flush()
        if held and self._send(request, "text", held):
            self._speak(request, sentences.feed(held), spoke, started)
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
                    log.info("Luna AI: first sentence after %.1f s", time.monotonic() - started)
                spoke.append(sentence)
