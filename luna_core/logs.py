"""Luna's log: the console plus a file, so a Pi without a screen still has a record.

The file is ~/.luna/logs/luna.log (rotated, 3 x 1 MB); LUNA_LOG_FILE picks another file
or "off". Anything that looks like an API key is masked before it is written anywhere.
"""
import logging
import logging.handlers
import os
import re
import sys
import threading
from pathlib import Path

DEFAULT_LOG_FILE = Path.home() / ".luna" / "logs" / "luna.log"
FILE_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_KEY = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}")

log = logging.getLogger("luna")


def redact(text):
    """Mask API keys: sk-... patterns and the configured OPENAI_API_KEY value."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if len(key) >= 8:
        text = text.replace(key, "***")
    return _KEY.sub("sk-***", text)


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))


def log_file():
    """The log file from LUNA_LOG_FILE (None when it is "off")."""
    value = os.environ.get("LUNA_LOG_FILE", "").strip()
    if value.lower() in ("off", "none", "0", "false"):
        return None
    return Path(value).expanduser() if value else DEFAULT_LOG_FILE


def setup_logging(path="env", console=sys.stderr):
    """Send Luna's log to the console and the log file (once per process)."""
    if getattr(log, "luna_ready", False):
        return log
    log.setLevel(logging.INFO)
    log.propagate = False
    if console is not None:
        handler = logging.StreamHandler(console)
        handler.setFormatter(RedactingFormatter("%(message)s"))
        log.addHandler(handler)
    path = log_file() if path == "env" else path
    if path is not None:
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3,
                                                           encoding="utf-8")
        except OSError as error:
            log.warning("Luna log: cannot write %s (%s)", path, type(error).__name__)
        else:
            handler.setFormatter(RedactingFormatter(FILE_FORMAT))
            log.addHandler(handler)
    _log_crashes()
    log.luna_ready = True
    return log


def _log_crashes():
    """Crashes (also in worker threads) go to the log file too."""
    original = sys.excepthook

    def crashed(kind, value, trace):
        if not issubclass(kind, KeyboardInterrupt):
            log.critical("Luna crashed", exc_info=(kind, value, trace))
        original(kind, value, trace)

    def thread_crashed(info):
        log.error("Luna thread %s crashed",
                  getattr(info.thread, "name", "?"),
                  exc_info=(info.exc_type, info.exc_value, info.exc_traceback))

    sys.excepthook = crashed
    threading.excepthook = thread_crashed
