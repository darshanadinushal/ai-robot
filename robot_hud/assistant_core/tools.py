"""Structured AI tools: a registry of allowed tools, a permission policy and an executor.

The AI only *proposes* a tool call (name + JSON arguments). Application code validates the
arguments against the tool's schema, asks the PermissionPolicy, and only then turns the call
into effects for the app. Nothing the model writes is ever run as code.

Risk classes (future skills reuse the same interfaces):
  local      Luna's own face and screen: harmless, always allowed
  memory     Luna's notes about the user (phase 5.2); saving or deleting needs the user's own
             words ("remember", "forget"), checked in code, never the AI's judgement
  info       reading information (sensors, states): planned
  device     controlling smart-home devices: planned, needs user permission settings
  motion     driving the robot: only through a MotionManager with a hardware stop; never here
  sensitive  locks, cameras, purchases, deleting things: extra confirmation; never automatic
"""
import json
import re
from dataclasses import dataclass, field

from .. import languages
from ..commands import EXPRESSIONS
from .events import Act

RISKS = ("local", "memory", "info", "device", "motion", "sensitive")
MAX_CALLS_PER_TURN = 6


@dataclass(frozen=True)
class ToolCall:
    """One call proposed by the AI. `arguments` is the raw JSON text from the model."""
    call_id: str
    name: str
    arguments: str = "{}"


@dataclass(frozen=True)
class ToolResult:
    """What happened, sent back to the AI (never shown raw to the user)."""
    ok: bool
    message: str
    data: dict = field(default_factory=dict)

    def output(self):
        return json.dumps({"ok": self.ok, "message": self.message, **self.data})


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict           # JSON schema of the arguments (objects of enums/strings)
    risk: str
    run: object                # function(arguments) -> (effects, ToolResult); no pygame
    asked_with: str = ""       # memory_words kind the user's own words must contain

    def schema(self):
        """The tool in the Responses API function-calling format."""
        return {"type": "function", "name": self.name, "description": self.description,
                "parameters": self.parameters, "strict": True}


def _object(properties=None):
    properties = properties or {}
    return {"type": "object", "properties": properties, "required": sorted(properties),
            "additionalProperties": False}


def _enum(values, description):
    return {"type": "string", "enum": list(values), "description": description}


def words(text):
    """Lower-case words of `text`, space-separated with a space at each end (phrase search)."""
    return " %s " % " ".join(re.findall(r"\w+", str(text).lower()))


def said(text, language, kind):
    """The user's words contain one of the pack's `memory_words[kind]` phrases."""
    heard = words(text)
    return any(" %s " % phrase in heard
               for phrase in languages.get(language).memory_words.get(kind, ()))


def validate(schema, arguments):
    """Arguments (a dict) that match the schema exactly, or ValueError. Supports the small
    subset Luna's tools use: objects with required strings, enums and integers, no extra keys."""
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    properties = schema.get("properties", {})
    extra = set(arguments) - set(properties)
    if extra:
        raise ValueError("unknown argument: " + ", ".join(sorted(extra)))
    for name in schema.get("required", ()):
        if name not in arguments:
            raise ValueError("missing argument: " + name)
    for name, value in arguments.items():
        rule = properties[name]
        if rule.get("type") == "string" and not isinstance(value, str):
            raise ValueError(name + " must be text")
        if rule.get("type") == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValueError(name + " must be a whole number")
        if "enum" in rule and value not in rule["enum"]:
            raise ValueError("%s must be one of: %s" % (name, ", ".join(rule["enum"])))
    return arguments


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""


NOT_ASKED = ("the user did not ask for this in their own words; ask them briefly whether "
             "you should, and call it again only if they say yes")


class PermissionPolicy:
    """Decides, in code, whether a validated call may run. The AI's confidence never counts.

    `local` and `memory` tools are allowed; a tool with `asked_with` also needs the user's own
    words for it in this turn. Later phases add user-configured autonomy levels for
    `info`/`device`; `motion` and `sensitive` are never allowed through this path.
    """

    def __init__(self, allowed=("local", "memory")):
        self.allowed = frozenset(allowed) - {"motion", "sensitive"}

    def decide(self, tool, arguments, heard="", language="en", granted=()):
        """`granted`: asked_with kinds the user agreed to with "yes" (see ToolExecutor)."""
        kinds = (tool.asked_with,) if isinstance(tool.asked_with, str) else tool.asked_with
        if (tool.asked_with and not set(kinds) & set(granted)
                and not any(said(heard, language, kind) for kind in kinds)):
            return Decision(False, NOT_ASKED)
        if tool.risk == "motion":
            return Decision(False, "movement is only possible through the motion system")
        if tool.risk == "sensitive":
            return Decision(False, "this needs the user's confirmation")
        if tool.risk not in self.allowed:
            return Decision(False, "not allowed by the user's settings")
        return Decision(True)


class ToolRegistry:
    def __init__(self, tools=()):
        self._tools = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool):
        if tool.risk not in RISKS:
            raise ValueError("unknown risk: " + tool.risk)
        if tool.name in self._tools:
            raise ValueError("tool registered twice: " + tool.name)
        self._tools[tool.name] = tool

    def get(self, name):
        return self._tools.get(name)

    def names(self):
        return tuple(self._tools)

    def schemas(self):
        return [tool.schema() for tool in self._tools.values()]


class ToolExecutor:
    """Runs the AI's calls for one turn: validate, check permission, run once.

    The same call id, or the same tool with the same arguments, never runs twice in a turn (the
    earlier result is returned), and at most MAX_CALLS_PER_TURN run.
    """

    def __init__(self, registry, policy=None):
        self.registry = registry
        self.policy = policy or PermissionPolicy()
        self.new_turn()

    def new_turn(self, heard="", language="en"):
        """A new question: `heard` is what the user said (for `asked_with` tools).

        If the last turn refused an `asked_with` tool because the user had not asked, and the
        user now answers "yes" (the pack's `memory_words.yes`), that kind is granted for this
        turn only. A yes at any other time grants nothing.
        """
        refused = getattr(self, "_refused", set())
        self._granted = refused if said(heard, language, "yes") else set()
        self._refused = set()
        self._heard, self._language = heard, language
        self._by_id = {}
        self._by_call = {}
        self._ran = 0

    def execute(self, call):
        """(effects, ToolResult) for one call; effects are empty unless it was allowed."""
        if call.call_id in self._by_id:
            return [], self._by_id[call.call_id]
        effects, result = self._execute(call)
        self._by_id[call.call_id] = result
        return effects, result

    def _execute(self, call):
        tool = self.registry.get(call.name)
        if tool is None:
            return [], ToolResult(False, "unknown tool")
        try:
            arguments = validate(tool.parameters, json.loads(call.arguments or "{}"))
        except (ValueError, TypeError) as error:  # json.JSONDecodeError is a ValueError
            return [], ToolResult(False, "invalid arguments: %s" % error)
        key = (tool.name, json.dumps(arguments, sort_keys=True))
        if key in self._by_call:
            return [], self._by_call[key]
        decision = self.policy.decide(tool, arguments, self._heard, self._language,
                                      self._granted)
        if decision.reason == NOT_ASKED:
            kinds = (tool.asked_with,) if isinstance(tool.asked_with, str) else tool.asked_with
            self._refused.update(kinds)
        if not decision.allowed:
            return [], ToolResult(False, "not allowed: " + decision.reason)
        if self._ran >= MAX_CALLS_PER_TURN:
            return [], ToolResult(False, "too many actions in one answer")
        self._ran += 1
        effects, result = tool.run(arguments)
        self._by_call[key] = result
        return effects, result


# --- Luna's face (phase 5.0C) -------------------------------------------------------------
COLORS = ("cyan", "purple", "green", "amber")
DIRECTIONS = {"left": (-1.0, 0.0), "right": (1.0, 0.0), "up": (0.0, -1.0), "down": (0.0, 1.0),
              "center": (0.0, 0.0)}
MODES = {"face": "FACE", "wave": "WAVE"}


def _face(action, message):
    return [Act((action,), quiet=True)], ToolResult(True, message)


FACE_TOOLS = (
    Tool("set_face_mood", "Show an expression on Luna's face for a few seconds.",
         _object({"mood": _enum(EXPRESSIONS, "the expression")}), "local",
         lambda a: _face(("expression", a["mood"]), "face shows " + a["mood"])),
    Tool("set_face_color", "Change the colour of Luna's face and eyes (saved as her theme).",
         _object({"color": _enum(COLORS, "one of the supported colours")}), "local",
         lambda a: _face(("color", a["color"].upper()), "colour is now " + a["color"])),
    Tool("set_face_mode", "Show Luna's face, or the sound-wave display instead.",
         _object({"mode": _enum(MODES, "face or wave")}), "local",
         lambda a: _face(("mode", MODES[a["mode"]]), "showing the " + a["mode"])),
    Tool("set_gaze_direction", "Make Luna's eyes look in a direction.",
         _object({"direction": _enum(DIRECTIONS, "where to look")}), "local",
         lambda a: _face(("look", DIRECTIONS[a["direction"]]), "looking " + a["direction"])),
    Tool("blink", "Make Luna blink once.", _object(), "local",
         lambda a: _face(("blink", None), "blinked")),
)


def face_registry():
    return ToolRegistry(FACE_TOOLS)
