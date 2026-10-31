"""The task service protocol: one JSON object per line, field `t` = message type (pure).

Luna and the task service (a child process from step 4; FakeTaskService in tests) exchange
these messages over stdin/stdout. `encode` and `decode` check every message: required fields
and their types, line length (64 KB; 2 MB for `paused`). Unknown fields are ignored; an
unknown type is a ProtocolError (the client logs and drops it). A service with another major
PROTOCOL_VERSION is not used. Never in a message: API keys, API error messages (class names
only), raw audio.
"""
import json

PROTOCOL_VERSION = "1.0"
MAX_LINE = 64 * 1024
MAX_PAUSED = 2 * 1024 * 1024
VERDICTS = ("allow", "refuse", "confirm")

# Luna -> service
TO_SERVICE = {
    "start": {"id": str, "goal": str, "specialist": str, "language": str, "context": dict,
              "limits": dict},
    "resume": {"id": str, "state": str, "item": str, "approved": bool},
    "cancel": {"id": str, "reason": str},
    "check_result": {"id": str, "call": str, "verdict": str, "reason": str, "summary": str,
                     "touch": bool},
    "tool_result": {"id": str, "call": str, "ok": bool, "output": str},
    "shutdown": {},
}
# Service -> Luna
FROM_SERVICE = {
    "ready": {"version": str, "specialists": list},
    "accepted": {"id": str},
    "step": {"id": str, "kind": str, "name": str, "summary": str},
    "usage": {"id": str, "model": str, "input": int, "cached": int, "output": int},
    "check": {"id": str, "call": str, "name": str, "args": dict},
    "tool_call": {"id": str, "call": str, "name": str, "args": dict},
    "paused": {"id": str, "item": str, "summary": str, "touch": bool, "state": str},
    "progress": {"id": str, "text": str},
    "done": {"id": str, "summary": str, "details": str},
    "failed": {"id": str, "error": str},
}


class ProtocolError(ValueError):
    """A message that breaks the protocol (the text never contains the message itself)."""


def _check(message, table):
    if not isinstance(message, dict) or not isinstance(message.get("t"), str):
        raise ProtocolError("not a message")
    kind = message["t"]
    if kind not in table:
        raise ProtocolError("unknown message type")
    for name, kind_of in table[kind].items():
        value = message.get(name)
        if kind_of is int:
            ok = isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else:
            ok = isinstance(value, kind_of)
        if not ok:
            raise ProtocolError("%s: field %s missing or not %s" % (kind, name, kind_of.__name__))
    if kind == "check_result" and message["verdict"] not in VERDICTS:
        raise ProtocolError("check_result: unknown verdict")
    return message


def _limit(kind):
    return MAX_PAUSED if kind == "paused" else MAX_LINE


def encode(message, table=TO_SERVICE):
    """One checked message as a line of text (with the newline)."""
    _check(message, table)
    line = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    if len(line.encode("utf-8")) > _limit(message["t"]):
        raise ProtocolError("%s: message too long" % message["t"])
    return line + "\n"


def decode(line, table=FROM_SERVICE):
    """One line (str or bytes) as a checked message dict; ProtocolError if it is not one."""
    if isinstance(line, bytes):
        if len(line) > MAX_PAUSED + 1:
            raise ProtocolError("line too long")
        try:
            line = line.decode("utf-8")
        except UnicodeDecodeError:
            raise ProtocolError("not UTF-8") from None
    if len(line) > MAX_PAUSED + 1:
        raise ProtocolError("line too long")
    try:
        message = json.loads(line)
    except ValueError:
        raise ProtocolError("not JSON") from None
    _check(message, table)
    if len(line.strip().encode("utf-8")) > _limit(message["t"]):
        raise ProtocolError("%s: message too long" % message["t"])
    return message


def compatible(version):
    """A service speaking `version` can be used (same major version)."""
    try:
        return int(str(version).split(".")[0]) == int(PROTOCOL_VERSION.split(".")[0])
    except ValueError:
        return False
