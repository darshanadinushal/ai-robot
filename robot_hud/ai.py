"""Online AI brain for Luna (OpenAI Responses API).

The API key is read from the OPENAI_API_KEY environment variable, or from the optional
file ~/.luna.env (outside the repository). Keys are never logged or written anywhere.
"""
import os
import queue
import threading
from pathlib import Path

DEFAULT_MODEL = "gpt-6-luna"
INSTRUCTIONS = (
    "You are Luna, a friendly AI robot with a touchscreen face. "
    "Your reply is spoken aloud, so answer in one to three short sentences of plain text, "
    "with no lists, markdown, emojis or code."
)
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


class LunaBrain:
    def __init__(self, model=None):
        from openai import OpenAI  # imported lazily: the package is optional

        load_env_file()
        self.model = model or os.environ.get("LUNA_AI_MODEL", DEFAULT_MODEL)
        self.client = OpenAI(timeout=30.0, max_retries=1)
        self.previous_id = None

    def ask(self, question):
        args = {"model": self.model, "instructions": INSTRUCTIONS, "input": question}
        if self.previous_id:
            args["previous_response_id"] = self.previous_id
        response = self.client.responses.create(**args)
        self.previous_id = response.id
        return response.output_text.strip()

    def forget(self):
        self.previous_id = None


class AIWorker:
    """Answers one question at a time in a background thread.

    The UI calls `ask()` and polls `answers` for ("answer", text) or ("error", text).
    """

    def __init__(self, brain_factory=LunaBrain):
        self._factory = brain_factory
        self._brain = None
        self._questions = queue.Queue()
        self.answers = queue.Queue()
        self._busy = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    @property
    def busy(self):
        return self._busy.is_set()

    def ask(self, question):
        if self.busy:
            return False
        self._busy.set()
        self._questions.put(question)
        return True

    def forget(self):
        if self._brain is not None:
            self._brain.forget()

    def _run(self):
        while True:
            question = self._questions.get()
            try:
                if self._brain is None:
                    self._brain = self._factory()
                answer = self._brain.ask(question)
                self.answers.put(("answer", answer or "I am not sure what to say."))
            except Exception as error:  # network, key, model or package problems
                print("Luna AI error:", type(error).__name__, flush=True)
                self.answers.put(("error", friendly_error(error)))
            finally:
                self._busy.clear()
