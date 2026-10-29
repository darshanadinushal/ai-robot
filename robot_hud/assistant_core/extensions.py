"""Optional, explicitly enabled Luna extensions.

Extensions are installed Python distributions with an entry point in the
"luna.extensions" group. Only names listed in LUNA_EXTENSIONS are imported.
Entry points execute trusted Python code in Luna's process: they are NOT sandboxed.
"""
from dataclasses import dataclass
from importlib.metadata import entry_points

from .tools import FACE_TOOLS, RISKS, Tool

ENTRY_POINT_GROUP = "luna.extensions"
# Core names are reserved even if memory is switched off at the moment.
CORE_TOOL_NAMES = frozenset(tool.name for tool in FACE_TOOLS) | {
    "remember_fact", "recall_memories", "forget_memory", "set_about_user",
    "forget_about_user",
}


@dataclass(frozen=True)
class LunaExtension:
    """An optional set of tools plus verified abilities for AI instructions.

    The factory should check dependencies before advertising an ability.
    'connected' contains exact entries from context.NOT_CONNECTED that are now
    genuinely available, e.g. "smart-home control".
    """
    name: str
    tools: tuple = ()
    abilities: tuple = ()
    connected: tuple = ()


def _valid(extension, name, taken):
    if not isinstance(extension, LunaExtension) or extension.name != name:
        raise ValueError("invalid extension descriptor")
    if not isinstance(extension.tools, tuple) or not all(
            isinstance(tool, Tool) and tool.risk in RISKS for tool in extension.tools):
        raise ValueError("invalid tools")
    if not all(isinstance(part, tuple) and all(
            isinstance(value, str) and bool(value.strip()) for value in part)
            for part in (extension.abilities, extension.connected)):
        raise ValueError("invalid capabilities")
    names = [tool.name for tool in extension.tools]
    if len(set(names)) != len(names) or set(names) & taken:
        raise ValueError("duplicate or reserved tool name")
    return names


def load_extensions(enabled="", available=None):
    """Return (extensions, errors) for explicitly selected entry-point names.

    Errors are pairs (name, short reason), safe for startup logs. An unavailable
    or broken extension cannot prevent Luna Core from starting. No entry points
    are imported unless their names were explicitly selected.
    """
    requested = tuple(dict.fromkeys(
        name.strip() for name in str(enabled or "").split(",") if name.strip()))
    if not requested:
        return (), ()
    if available is None:
        discovered = entry_points()
        available = (discovered.select(group=ENTRY_POINT_GROUP)
                     if hasattr(discovered, "select")
                     else discovered.get(ENTRY_POINT_GROUP, ()))
    points = {}
    duplicates = set()
    for point in available:
        if point.name in points:
            duplicates.add(point.name)
        points[point.name] = point

    loaded, errors = [], []
    taken = set(CORE_TOOL_NAMES)
    for name in requested:
        if name in duplicates:
            errors.append((name, "duplicate entry point"))
            continue
        point = points.get(name)
        if point is None:
            errors.append((name, "not installed"))
            continue
        try:
            factory = point.load()
            extension = factory()
            names = _valid(extension, name, taken)
        except Exception as error:
            errors.append((name, type(error).__name__))
            continue
        loaded.append(extension)
        taken.update(names)
    return tuple(loaded), tuple(errors)
