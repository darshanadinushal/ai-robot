"""Loading, starting, stopping and recovering plugins (pure, no pygame).

Lifecycle of one plugin:
  added (by the app, Robot.add_plugin) or discovered (installed package, entry point group
  "luna.plugins"; off until the user turns it on)
  -> checked: id, permissions, plugin API and core version ("incompatible" / "broken")
  -> waiting for the user to allow its sensitive permissions ("permission")
  -> setup(luna) -> "running"; an error there -> "failed", nothing it registered stays
  -> errors while running are caught; after MAX_ERRORS the plugin is turned off ("errors")
  -> turned off: everything it registered is removed, motors stop, teardown() runs.
A plugin can never crash Luna: every call into plugin code goes through PluginManager.call.

The host (app.RobotHUD) provides: add_page(page), remove_page(page), say(text),
face_actions(actions), memories() -> [text], hardware (camera, motors) and plugins_changed(words).
All of it runs on the UI thread.
"""
import logging
import re
from dataclasses import dataclass, field

from .. import commands, languages
from ..assistant_core.tools import RISKS, Tool, ToolResult, _object
from ..hardware.base import clamp_speed
from .api import PERMISSIONS, PLUGIN_API, PLUGIN_ID, SENSITIVE, PermissionDenied, Plugin, version_matches
from .store import PluginStore

log = logging.getLogger("luna.plugins")

MAX_ERRORS = 3          # errors while running before a plugin is turned off
MAX_START_FAILURES = 3  # failed starts in a row before it stays off
TOOL_RISKS = {"local": None, "info": None, "device": "devices"}  # risk -> permission needed
PAGE_METHODS = ("draw_icon", "draw", "touch", "key")
LOOKS = {"left": (-1.0, 0.0), "right": (1.0, 0.0), "up": (0.0, -1.0), "down": (0.0, 1.0),
         "center": (0.0, 0.0)}
STATUS_TEXT = {
    "running": "Running",
    "off": "Off",
    "permission": "Needs your permission",
    "failed": "Failed to start",
    "errors": "Turned off after errors",
    "incompatible": "Not made for this Luna version",
    "broken": "Could not be loaded",
}


@dataclass
class PluginRecord:
    plugin: object
    id: str
    name: str
    version: str = ""
    description: str = ""
    permissions: tuple = ()
    settings: tuple = ()
    source: str = "app"        # "app" (added by the app) or "installed" (entry point)
    status: str = "off"
    problem: str = ""          # why it is broken or incompatible (for the log)
    enabled: bool = False
    granted: set = field(default_factory=set)
    values: dict = field(default_factory=dict)
    errors: int = 0
    context: object = None


@dataclass(frozen=True)
class PluginView:
    """One plugin as Settings > Plugins shows it."""
    id: str
    name: str
    version: str
    description: str
    status: str
    enabled: bool
    permissions: tuple  # (name, sensitive, granted, description)
    settings: tuple     # (Setting, current value)


class GuardedMotors:
    """The robot's motors as a plugin sees them: speeds clamped, stopped when the plugin stops."""

    def __init__(self, motors, context):
        self._motors, self._context = motors, context

    def drive(self, left, right):
        self._context._need("motors")
        self._motors.drive(clamp_speed(left), clamp_speed(right))

    def stop(self):
        self._motors.stop()


class PluginContext:
    """`luna` in Plugin.setup: the only way a plugin reaches Luna. Call it on the UI thread
    (from setup and from the callbacks Luna calls); worker threads must not use it."""

    def __init__(self, manager, record):
        self._manager, self._record = manager, record
        self.id = record.id
        self.log = logging.getLogger("luna.plugin." + record.id)
        self._actions = {}
        self._pages = []
        self._tools = []
        self._instructions = []
        self._heard = []
        self._timers = []
        self._motors = None
        self.words_added = False
        self.closed = False

    def _need(self, permission):
        if self.closed:
            raise PermissionDenied("the plugin has stopped")
        if permission not in self._record.permissions:
            raise PermissionDenied("%s did not declare the %r permission" % (self.id, permission))
        if permission in SENSITIVE and permission not in self._record.granted:
            raise PermissionDenied("the user has not allowed %r for %s" % (permission, self.id))

    def _open(self):
        if self.closed:
            raise PermissionDenied("the plugin has stopped")

    # --- what the plugin adds ---------------------------------------------------------------
    def add_command(self, name, run, words, replies=None):
        """Spoken words that run `run(t)`: words = {"en": ["lights"], "sv": ["lampor"]}.

        Each word is one word that is not already a command. `replies`: {"en": "Lights on."},
        spoken by Luna (needs "speak"). The speech grammar is reloaded with the new words.
        """
        self._open()
        if not re.match(r"^[a-z0-9_]{1,30}$", name):
            raise ValueError("command name: lower-case letters, digits and _")
        if replies:
            self._need("speak")
        action_id = "plugin.%s.%s" % (self.id, name)
        tables = {}
        for code, spoken in words.items():
            pack = languages.get(code)
            if code not in languages.PACKS:
                self.log.warning("no %r language in Luna: words %s ignored", code, list(spoken))
                continue
            table = {}
            for word in spoken:
                word = str(word).lower().strip()
                if not re.match(r"^\w+$", word):
                    raise ValueError("%r: a command is one word" % word)
                taken = (word in pack.commands or word in pack.wake_words or word in pack.greetings
                         or word in pack.filler_words)
                if taken:
                    raise ValueError("%r is already a word Luna uses in %s" % (word, code))
                table[word] = action_id
            tables[code] = table
        commands.add_action(action_id, ("plugin", action_id))
        self._actions[action_id] = run
        for code, table in tables.items():
            languages.extend(self.id, code, commands=table,
                             replies={action_id: replies[code]} if replies and code in replies else {})
            self.words_added = self.words_added or bool(table)
        return action_id

    def add_page(self, page):
        """A page (luna_core.ui.Page) with its own navigation button."""
        self._open()
        page.plugin = self.id
        for method in PAGE_METHODS:  # an error in the page's code is caught and counted
            setattr(page, method, self._manager.guarded(self.id, getattr(page, method), "page " + method))
        self._manager.host.add_page(page)
        self._pages.append(page)
        return page

    def add_tool(self, name, description, run, parameters=None, risk="info"):
        """An AI tool: `run(arguments)` returns a short text (or a ToolResult) for the AI.

        `parameters`: {"room": "which room"} (text), {"room": ["kitchen", "hall"]} (a choice)
        or a full JSON schema. `risk`: "local", "info" or "device" (needs "devices").
        """
        self._need("ai")
        if risk not in TOOL_RISKS or risk not in RISKS:
            raise ValueError("risk must be one of: " + ", ".join(TOOL_RISKS))
        if TOOL_RISKS[risk]:
            self._need(TOOL_RISKS[risk])
        if not re.match(r"^[a-z0-9_]{1,40}$", name):
            raise ValueError("tool name: lower-case letters, digits and _")
        schema = parameters if isinstance(parameters, dict) and parameters.get("type") == "object" else _object(
            {key: ({"type": "string", "enum": list(rule), "description": key} if isinstance(rule, (list, tuple))
                   else {"type": "string", "description": str(rule)})
             for key, rule in (parameters or {}).items()})
        manager, record = self._manager, self._record

        def run_tool(arguments):
            ok, value = manager.call(record, run, arguments, where="tool " + name)
            if not ok:
                return [], ToolResult(False, "the tool failed")
            if isinstance(value, ToolResult):
                return [], value
            return [], ToolResult(True, str(value if value is not None else "done")[:500])

        tool = Tool("%s_%s" % (self.id.replace("-", "_"), name), str(description)[:300], schema, risk, run_tool)
        self._tools.append(tool)
        return tool.name

    def add_personality(self, text):
        """A sentence added to what the AI is told with every question (max 500 characters)."""
        self._need("ai")
        self._instructions.append(str(text).strip()[:500])

    def add_language_text(self, code, screen=None, speech=None):
        """Screen texts (English -> translated, for tr) and spoken sentences for a language."""
        self._open()
        return languages.extend(self.id, code, screen=dict(screen or {}), speech=dict(speech or {}))

    def on_heard(self, callback):
        """`callback(text)` for every complete sentence Luna hears (needs "microphone")."""
        self._need("microphone")
        self._heard.append(callback)

    def every(self, seconds, callback):
        """`callback(t)` every `seconds` (at least 0.1) on the UI thread; keep it quick."""
        self._open()
        self._timers.append([max(.1, float(seconds)), None, callback])

    # --- what the plugin can do -------------------------------------------------------------
    def say(self, text):
        self._need("speak")
        self._manager.host.say(str(text))

    def set_mood(self, mood):
        self._need("face")
        if mood not in commands.EXPRESSIONS:
            raise ValueError("mood must be one of: " + ", ".join(commands.EXPRESSIONS))
        self._manager.host.face_actions([("expression", mood)])

    def look(self, direction):
        self._need("face")
        self._manager.host.face_actions([("look", LOOKS[direction])])

    def memories(self):
        """What Luna remembers about the user (texts); empty when memory is off."""
        self._need("memory")
        return list(self._manager.host.memories())

    def camera(self):
        self._need("camera")
        return self._manager.host.hardware.camera

    def motors(self):
        self._need("motors")
        if self._motors is None:
            self._motors = GuardedMotors(self._manager.host.hardware.motors, self)
        return self._motors

    def setting(self, key):
        self._open()
        return self._record.values[key]

    # --- removing everything ----------------------------------------------------------------
    def close(self):
        """Undo every registration (the plugin stopped)."""
        if self.closed:
            return
        self.closed = True
        if self._motors is not None:
            try:
                self._motors.stop()
            except Exception as error:  # the motors themselves failed: still stop the plugin
                log.warning("Luna plugin %s: motors did not stop (%s)", self.id, type(error).__name__)
        for action_id in self._actions:
            commands.remove_action(action_id)
        languages.retract(self.id)
        for page in self._pages:
            self._manager.host.remove_page(page)
        self._actions, self._pages, self._tools, self._instructions = {}, [], [], []
        self._heard, self._timers = [], []


class PluginManager:
    def __init__(self, host, store=None, core_version=None, max_errors=MAX_ERRORS):
        if core_version is None:
            from .. import __version__ as core_version
        self.host = host
        self.store = store if store is not None else PluginStore()
        self.core_version = core_version
        self.max_errors = max_errors
        self.records = {}

    # --- adding -----------------------------------------------------------------------------
    def add(self, plugin, source="app"):
        """Add a plugin (a Plugin instance or class). The app's plugins start on by default."""
        if isinstance(plugin, type) and issubclass(plugin, Plugin):
            plugin = plugin()
        plugin_id = getattr(plugin, "id", "")
        if not isinstance(plugin, Plugin):
            raise TypeError("a plugin must be a luna_core.plugins.Plugin, not %s" % type(plugin).__name__)
        if not PLUGIN_ID.match(str(plugin_id)):
            raise ValueError("plugin id %r: lower case letters, digits, - and _ (2-31 characters)" % plugin_id)
        if plugin_id in self.records:
            raise ValueError("there is already a plugin called %r" % plugin_id)
        saved = self.store.get(plugin_id)
        record = PluginRecord(plugin, plugin_id, str(plugin.name or plugin_id), str(plugin.version),
                              str(plugin.description), tuple(plugin.permissions), tuple(plugin.settings), source)
        record.granted = set(saved["granted"]) & SENSITIVE
        default_on = source == "app"
        record.enabled = saved["enabled"] if saved["enabled"] is not None else default_on
        record.problem = self._problem(plugin)
        if record.problem:
            record.status = "broken" if not record.problem.startswith("needs") else "incompatible"
            log.warning("Luna plugin %s: %s", plugin_id, record.problem)
        else:
            record.values = {s.key: saved["settings"].get(s.key) if saved["settings"].get(s.key) in s.choices
                             else s.initial for s in record.settings}
            record.status = saved["off_reason"] if saved["off_reason"] in ("errors",) and not record.enabled else "off"
        self.records[plugin_id] = record
        return record

    def _problem(self, plugin):
        unknown = sorted(set(plugin.permissions) - set(PERMISSIONS))
        if unknown:
            return "unknown permission: " + ", ".join(unknown)
        keys = [s.key for s in plugin.settings]
        if len(set(keys)) != len(keys):
            return "two settings with the same key"
        if getattr(plugin, "api", PLUGIN_API) != PLUGIN_API:
            return "needs plugin API %s (Luna has %d)" % (plugin.api, PLUGIN_API)
        try:
            if not version_matches(self.core_version, plugin.core):
                return "needs Luna Core %s (this is %s)" % (plugin.core, self.core_version)
        except ValueError as error:
            return str(error)
        return ""

    def discover(self, entry_points=None):
        """Add installed plugins (entry point group "luna.plugins"); they start off."""
        if entry_points is None:
            from importlib.metadata import entry_points as installed
            entry_points = installed(group="luna.plugins")
        for point in entry_points:
            if point.name in self.records:
                continue
            try:
                self.add(point.load(), source="installed")
            except Exception as error:  # a broken package must not stop Luna
                log.warning("Luna plugin %s could not be loaded (%s: %s)", point.name,
                            type(error).__name__, error)
                name = re.sub(r"[^a-z0-9_-]", "-", point.name.lower())[:31] or "plugin"
                if name not in self.records:
                    self.records[name] = PluginRecord(None, name, point.name, source="installed",
                                                      status="broken", problem=type(error).__name__)

    # --- starting and stopping --------------------------------------------------------------
    def start_all(self):
        for record in list(self.records.values()):
            if record.enabled:
                self._start(record)
        self._changed(any(r.context and r.context.words_added for r in self.records.values()))

    def stop_all(self):
        for record in self.records.values():
            if record.context is not None:
                self._stop(record, notify=False)

    def _start(self, record):
        if record.status in ("broken", "incompatible") or record.context is not None:
            return False
        missing = sorted(set(record.permissions) & SENSITIVE - record.granted)
        if missing:
            record.status = "permission"
            log.info("Luna plugin %s waits for permission: %s", record.id, ", ".join(missing))
            return False
        context = PluginContext(self, record)
        record.context, record.errors = context, 0
        try:
            record.plugin.setup(context)
        except (Exception, SystemExit) as error:
            log.error("Luna plugin %s failed to start (%s)", record.id, type(error).__name__, exc_info=True)
            context.close()
            record.context = None
            failures = self.store.get(record.id)["start_failures"] + 1
            if failures >= MAX_START_FAILURES:
                record.enabled, record.status = False, "errors"
                self.store.update(record.id, enabled=False, start_failures=failures, off_reason="errors")
            else:
                record.status = "failed"
                self.store.update(record.id, start_failures=failures)
            return False
        record.status = "running"
        self.store.update(record.id, start_failures=0)
        log.info("Luna plugin %s %s started", record.id, record.version)
        return True

    def _stop(self, record, notify=True):
        context = record.context
        if context is None:
            return
        record.context = None
        context.close()
        try:
            record.plugin.teardown()
        except (Exception, SystemExit) as error:
            log.error("Luna plugin %s: teardown failed (%s)", record.id, type(error).__name__, exc_info=True)
        if notify:
            self._changed(False)

    def _changed(self, words):
        self.host.plugins_changed(words)

    # --- the user's choices (Settings > Plugins) --------------------------------------------
    def set_enabled(self, plugin_id, on):
        record = self.records[plugin_id]
        if record.status in ("broken", "incompatible"):
            return
        record.enabled = bool(on)
        if on:
            self.store.update(plugin_id, enabled=True, start_failures=0, off_reason="")
            record.status = "off"
            if self._start(record):
                self._changed(record.context.words_added)
        else:
            self.store.update(plugin_id, enabled=False, off_reason="")
            self._stop(record)
            record.status = "off"

    def grant(self, plugin_id, permission, on):
        """Allow or take back a sensitive permission; the plugin starts or stops to match."""
        record = self.records[plugin_id]
        if permission not in SENSITIVE or permission not in record.permissions:
            return
        if on:
            record.granted.add(permission)
        else:
            record.granted.discard(permission)
        self.store.update(plugin_id, granted=sorted(record.granted))
        if not on and record.context is not None:
            self._stop(record)
            record.status = "permission"
        elif on and record.enabled and record.context is None and record.status == "permission":
            if self._start(record):
                self._changed(record.context.words_added)

    def set_setting(self, plugin_id, key, value):
        record = self.records[plugin_id]
        setting = next((s for s in record.settings if s.key == key), None)
        if setting is None or value not in setting.choices or record.values.get(key) == value:
            return
        record.values[key] = value
        self.store.update(plugin_id, settings=dict(record.values))
        if record.context is not None:
            self.call(record, record.plugin.setting_changed, key, value, where="setting_changed")

    # --- calling plugin code ----------------------------------------------------------------
    def call(self, record, function, *args, where=""):
        """(True, result), or (False, None) when the plugin's code failed (logged and counted)."""
        try:
            return True, function(*args)
        except (Exception, SystemExit) as error:
            self.failed(record, error, where)
            return False, None

    def failed(self, record, error, where=""):
        if isinstance(record, str):
            record = self.records[record]
        record.errors += 1
        log.error("Luna plugin %s: %s failed (%s), error %d of %d", record.id, where or "code",
                  type(error).__name__, record.errors, self.max_errors, exc_info=error)
        if record.errors >= self.max_errors and record.context is not None:
            log.warning("Luna plugin %s turned off after %d errors", record.id, record.errors)
            self._stop(record)
            record.enabled, record.status = False, "errors"
            self.store.update(record.id, enabled=False, off_reason="errors")

    def run_command(self, action_id, t):
        for record in self.running():
            run = record.context._actions.get(action_id)
            if run is not None:
                self.call(record, run, t, where="command")
                return True
        return False

    def heard(self, text):
        for record in self.running():
            for callback in list(record.context._heard):
                if record.context is None:
                    break
                self.call(record, callback, text, where="on_heard")

    def tick(self, t):
        for record in self.running():
            for timer in list(record.context._timers):
                if record.context is None:
                    break
                seconds, due, callback = timer
                if due is None:
                    timer[1] = t + seconds
                elif t >= due:
                    timer[1] = t + seconds
                    self.call(record, callback, t, where="timer")

    def guarded(self, plugin_id, function, where):
        """`function` wrapped by guard (for a plugin's page methods)."""
        def run(*args):
            return self.guard(plugin_id, function, *args, where=where)
        return run

    def guard(self, plugin_id, function, *args, where=""):
        """Run a plugin's page code (draw, touch, key) without letting it crash Luna."""
        record = self.records.get(plugin_id)
        if record is None or record.context is None:
            return None
        return self.call(record, function, *args, where=where)[1]

    # --- what the host reads ----------------------------------------------------------------
    def running(self):
        return [r for r in self.records.values() if r.context is not None]

    def tools(self):
        return tuple(tool for r in self.running() for tool in r.context._tools)

    def tool_risks(self):
        return tuple(sorted({tool.risk for tool in self.tools()}))

    def instructions(self):
        return tuple(text for r in self.running() for text in r.context._instructions)

    def view(self):
        return [PluginView(r.id, r.name, r.version, r.description, r.status, r.enabled,
                           tuple((p, p in SENSITIVE, p not in SENSITIVE or p in r.granted, PERMISSIONS[p][1])
                                 for p in r.permissions if p in PERMISSIONS),
                           tuple((s, r.values.get(s.key, s.initial)) for s in r.settings))
                for r in self.records.values()]
