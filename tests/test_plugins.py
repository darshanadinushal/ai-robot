"""Plugins: the API, lifecycle, permissions, recovery and a real (headless) Luna with plugins."""
import json
import logging
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from luna_core import commands, i18n, languages
from luna_core.assistant_core.conversation import Conversation
from luna_core.assistant_core.tools import ToolCall, ToolResult
from luna_core.hardware.mock import MockMotors
from luna_core.plugins import PERMISSIONS, PermissionDenied, Plugin, Setting
from luna_core.plugins.api import version_matches
from luna_core.plugins.manager import PluginContext, PluginManager
from luna_core.plugins.store import PluginStore, UnsavedStore


def setUpModule():
    # The plugins here fail on purpose; their logged errors are expected.
    for name in ("luna.plugins", "luna.plugin"):
        logging.getLogger(name).setLevel(logging.CRITICAL + 1)


def tearDownModule():
    for name in ("luna.plugins", "luna.plugin"):
        logging.getLogger(name).setLevel(logging.NOTSET)


class FakeHost:
    def __init__(self):
        self.pages, self.spoken, self.faces, self.changes = [], [], [], []
        self.hardware = types.SimpleNamespace(motors=MockMotors(), camera="camera")
        self.facts = ["You like jazz."]

    def add_page(self, page):
        self.pages.append(page)

    def remove_page(self, page):
        self.pages.remove(page)

    def say(self, text):
        self.spoken.append(text)

    def face_actions(self, actions):
        self.faces.extend(actions)

    def memories(self):
        return self.facts

    def plugins_changed(self, words=False):
        self.changes.append(words)


class Lights(Plugin):
    id = "lights"
    name = "Lights"
    version = "1.2.0"
    permissions = ("speak", "ai")
    settings = (Setting("colour", "Colour", ("warm", "cold")),)

    def __init__(self):
        self.ran, self.changed, self.stopped = [], [], 0

    def setup(self, luna):
        self.luna = luna
        luna.add_command("on", self.ran.append, words={"en": ["lamps"], "sv": ["lampor"], "xx": ["x"]},
                         replies={"en": "Lamps on."})
        luna.add_tool("state", "Are the lamps on?", lambda arguments: "on")
        luna.add_personality("You can switch the lamps.")

    def teardown(self):
        self.stopped += 1

    def setting_changed(self, key, value):
        self.changed.append((key, value))


def manager(*plugins, store=None, **kwargs):
    host = FakeHost()
    plugins_manager = PluginManager(host, store if store is not None else UnsavedStore(), "1.0.0", **kwargs)
    for plugin in plugins:
        plugins_manager.add(plugin)
    plugins_manager.start_all()
    return plugins_manager, host


class PluginLifecycleTests(unittest.TestCase):
    def test_an_app_plugin_adds_a_command_tool_and_personality_and_removes_them(self):
        lights = Lights()
        plugins, host = manager(lights)
        self.assertEqual(plugins.records["lights"].status, "running")
        self.assertEqual(host.changes, [True], "new words: the grammar is reloaded")
        actions = [("plugin", "plugin.lights.on")]
        self.assertEqual(commands.route("lamps"), ("command", actions))
        self.assertEqual(commands.parse_command("lampor", "sv"), actions)
        self.assertEqual(commands.reply_for(actions, "en"), "Lamps on.")
        self.assertIn("lamps", commands.vocabulary("en"))
        plugins.run_command("plugin.lights.on", 2.0)
        self.assertEqual(lights.ran, [2.0])
        self.assertEqual([t.name for t in plugins.tools()], ["lights_state"])
        self.assertEqual(plugins.instructions(), ("You can switch the lamps.",))

        plugins.set_enabled("lights", False)
        self.assertEqual((plugins.records["lights"].status, lights.stopped), ("off", 1))
        self.assertEqual(commands.route("lamps")[0], "ignore")
        self.assertNotIn("plugin.lights.on", commands.ACTIONS)
        self.assertNotIn("lamps", languages.get("en").commands)
        self.assertEqual((plugins.tools(), plugins.instructions()), ((), ()))
        plugins.set_enabled("lights", True)
        self.assertEqual(commands.route("lamps"), ("command", actions), "on again")
        plugins.stop_all()
        self.assertNotIn("lamps", languages.get("en").commands)

    def test_settings_are_remembered_and_reported(self):
        store = UnsavedStore()
        lights = Lights()
        plugins, _ = manager(lights, store=store)
        self.assertEqual(lights.luna.setting("colour"), "warm")
        plugins.set_setting("lights", "colour", "cold")
        plugins.set_setting("lights", "colour", "purple")  # not a choice
        self.assertEqual((lights.luna.setting("colour"), lights.changed), ("cold", [("colour", "cold")]))
        plugins.stop_all()
        again = Lights()
        plugins, _ = manager(again, store=store)
        self.assertEqual(again.luna.setting("colour"), "cold")
        plugins.stop_all()

    def test_a_failing_setup_leaves_nothing_behind_and_stays_off_after_three_tries(self):
        class Broken(Plugin):
            id = "broken"

            def setup(self, luna):
                luna.add_command("go", print, words={"en": ["zoom"]})
                raise RuntimeError("bad plugin")

        store = UnsavedStore()
        for attempt in range(3):
            plugins, _ = manager(Broken(), store=store)
            self.assertNotIn("zoom", languages.get("en").commands)
            self.assertNotIn("plugin.broken.go", commands.ACTIONS)
        self.assertEqual(plugins.records["broken"].status, "errors")
        self.assertFalse(store.get("broken")["enabled"])
        plugins, _ = manager(Broken(), store=store)
        self.assertEqual(plugins.records["broken"].status, "errors", "not tried again")

    def test_errors_while_running_turn_the_plugin_off(self):
        class Flaky(Plugin):
            id = "flaky"
            permissions = ("speak",)

            def setup(self, luna):
                luna.every(1, self.boom)

            def boom(self, t):
                raise ValueError("oops")

        store = UnsavedStore()
        plugins, host = manager(Flaky(), store=store)
        for t in (0, 1, 2, 3, 4, 5):
            plugins.tick(t)
        record = plugins.records["flaky"]
        self.assertEqual((record.status, record.enabled, record.errors), ("errors", False, 3))
        self.assertEqual(store.get("flaky")["off_reason"], "errors")
        plugins.set_enabled("flaky", True)
        self.assertEqual(plugins.records["flaky"].status, "running", "the user can try again")

    def test_systemexit_from_a_plugin_does_not_stop_luna(self):
        class Quitter(Plugin):
            id = "quitter"

            def setup(self, luna):
                raise SystemExit(1)

        plugins, _ = manager(Quitter())
        self.assertEqual(plugins.records["quitter"].status, "failed")


class PluginPermissionTests(unittest.TestCase):
    def test_sensitive_permissions_wait_for_the_user(self):
        class Driver(Plugin):
            id = "driver"
            permissions = ("motors", "memory")

            def setup(self, luna):
                self.motors = luna.motors()
                self.facts = luna.memories()

        driver = Driver()
        store = UnsavedStore()
        plugins, host = manager(driver, store=store)
        self.assertEqual(plugins.records["driver"].status, "permission")
        plugins.grant("driver", "motors", True)
        self.assertEqual(plugins.records["driver"].status, "permission", "memory still missing")
        plugins.grant("driver", "memory", True)
        self.assertEqual(plugins.records["driver"].status, "running")
        self.assertEqual(driver.facts, ["You like jazz."])
        driver.motors.drive(5, -5)
        self.assertEqual(host.hardware.motors.speeds, (1.0, -1.0), "speeds are clamped")
        plugins.grant("driver", "motors", False)
        self.assertEqual(plugins.records["driver"].status, "permission")
        self.assertEqual(host.hardware.motors.commands[-1], ("stop",), "motors stop with the plugin")
        with self.assertRaises(PermissionDenied):
            driver.motors.drive(.5, .5)
        self.assertEqual(store.get("driver")["granted"], ["memory"])

    def test_undeclared_permissions_are_denied(self):
        class Sneaky(Plugin):
            id = "sneaky"

            def setup(self, luna):
                luna.say("hello")

        plugins, host = manager(Sneaky())
        self.assertEqual((plugins.records["sneaky"].status, host.spoken), ("failed", []))

    def test_device_tools_need_the_devices_permission(self):
        class Home(Plugin):
            id = "home"
            permissions = ("ai",)

            def setup(self, luna):
                luna.add_tool("unlock", "Unlock the door", print, risk="device")

        plugins, _ = manager(Home())
        self.assertEqual(plugins.records["home"].status, "failed")

    def test_unknown_permission_is_reported_not_started(self):
        class Odd(Plugin):
            id = "odd"
            permissions = ("everything",)

        plugins, _ = manager(Odd())
        self.assertEqual(plugins.records["odd"].status, "broken")


class PluginCompatibilityTests(unittest.TestCase):
    def test_versions(self):
        self.assertTrue(version_matches("1.0.0", ">=1.0,<2"))
        self.assertTrue(version_matches("1.4", ""))
        self.assertFalse(version_matches("2.0.0", ">=1.0,<2"))
        self.assertFalse(version_matches("1.0.0", ">1.0"))
        with self.assertRaises(ValueError):
            version_matches("1.0", "~=1.0")

    def test_plugins_for_another_core_or_api_do_not_start(self):
        class Future(Plugin):
            id = "future"
            core = ">=2.0"

        class NewApi(Plugin):
            id = "newapi"
            api = 2

        plugins, _ = manager(Future(), NewApi())
        self.assertEqual({r.status for r in plugins.records.values()}, {"incompatible"})
        plugins.set_enabled("future", True)
        self.assertEqual(plugins.records["future"].status, "incompatible")

    def test_installed_plugins_start_off_and_broken_packages_are_listed(self):
        class Installed(Plugin):
            id = "installed"

        def fail():
            raise ImportError("missing dependency")

        points = [types.SimpleNamespace(name="installed", load=lambda: Installed),
                  types.SimpleNamespace(name="Bad Package", load=fail)]
        plugins = PluginManager(FakeHost(), UnsavedStore(), "1.0.0")
        plugins.discover(points)
        plugins.start_all()
        self.assertEqual(plugins.records["installed"].status, "off")
        self.assertEqual(plugins.records["bad-package"].status, "broken")
        plugins.set_enabled("installed", True)
        plugins.set_enabled("bad-package", True)
        self.assertEqual(plugins.records["installed"].status, "running")
        self.assertEqual(plugins.records["bad-package"].status, "broken")

    def test_bad_ids_and_taken_words_are_refused(self):
        plugins = PluginManager(FakeHost(), UnsavedStore(), "1.0.0")
        with self.assertRaises(ValueError):
            plugins.add(type("Bad", (Plugin,), {"id": "Bad Id"})())
        with self.assertRaises(TypeError):
            plugins.add(object())

        class Taker(Plugin):
            id = "taker"

            def setup(self, luna):
                luna.add_command("x", print, words={"en": ["happy"]})

        plugins, _ = manager(Taker())
        self.assertEqual(plugins.records["taker"].status, "failed")
        self.assertEqual(commands.route("happy"), ("command", [("expression", "happy")]))

    def test_the_example_plugin_works_with_this_core(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples" / "hello_plugin"))
        self.addCleanup(sys.path.pop, 0)
        import luna_hello

        plugins, host = manager(luna_hello.HelloPlugin())
        self.addCleanup(plugins.stop_all)
        self.assertEqual(plugins.records["hello"].status, "running")
        self.assertEqual(commands.route("greet"), ("command", [("plugin", "plugin.hello.greet")]))
        plugins.set_setting("hello", "greeting", "long")
        plugins.run_command("plugin.hello.greet", 1.0)
        self.assertEqual((host.spoken, host.faces), (["Hello there, nice to see you!"], [("expression", "happy")]))
        self.assertEqual([p.name for p in host.pages], ["Hello"])

    def test_the_public_plugin_api_stays_the_same(self):
        """Apps rely on these names (plugin API 1): only add to them in a minor version."""
        public = {name for name in vars(PluginContext) if not name.startswith("_")}
        self.assertEqual(public, {"add_command", "add_page", "add_tool", "add_personality",
                                  "add_language_text", "on_heard", "every", "say", "set_mood", "look",
                                  "memories", "camera", "motors", "setting", "close"})
        self.assertEqual({n for n in vars(Plugin) if not n.startswith("_")},
                         {"id", "name", "version", "description", "core", "api", "permissions",
                          "settings", "setup", "teardown", "setting_changed"})
        self.assertEqual(set(PERMISSIONS), {"speak", "face", "ai", "microphone", "memory", "network",
                                            "camera", "motors", "devices"})


class PluginLanguageTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("en")

    def test_plugin_texts_are_translated_and_core_texts_win(self):
        class Words(Plugin):
            id = "words"

            def setup(self, luna):
                luna.add_language_text("sv", screen={"Lamps": "Lampor", "Home": "Hus"})

        plugins, _ = manager(Words())
        i18n.set_language("sv")
        self.assertEqual((i18n.tr("Lamps"), i18n.tr("Home")), ("Lampor", "Hem"))
        plugins.stop_all()
        self.assertEqual(i18n.tr("Lamps"), "Lamps")


class PluginStoreTests(unittest.TestCase):
    def test_saved_atomically_and_a_broken_file_is_ignored(self):
        path = Path(tempfile.mkdtemp()) / "plugins.json"
        store = PluginStore(path)
        store.update("lights", enabled=True, granted=["memory"])
        self.assertEqual(PluginStore(path).get("lights")["granted"], ["memory"])
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual(PluginStore(path).get("lights")["enabled"], None)
        path.write_text(json.dumps({"plugins": {"x": {"enabled": "yes", "granted": "all"}}}), encoding="utf-8")
        self.assertEqual(PluginStore(path).get("x"), {"enabled": None, "granted": [], "settings": {},
                                                     "start_failures": 0, "off_reason": ""})


class ConversationToolTests(unittest.TestCase):
    def test_plugin_tools_and_personality_reach_the_ai(self):
        plugins, _ = manager(Lights())
        conversation = Conversation()
        conversation.set_plugins(plugins.tools(), plugins.tool_risks(), plugins.instructions())
        self.assertIn("lights_state", conversation.tools.registry.names())
        conversation.tools.new_turn("are the lamps on")
        effects, result = conversation.tools.execute(ToolCall("1", "lights_state"))
        self.assertEqual(result, ToolResult(True, "on"))
        conversation.set_memory(None)
        self.assertIn("lights_state", conversation.tools.registry.names(), "kept when memory changes")
        plugins.stop_all()

    def test_a_failing_tool_answers_the_ai_and_counts_as_an_error(self):
        class Bad(Plugin):
            id = "bad"
            permissions = ("ai",)

            def setup(self, luna):
                luna.add_tool("fail", "Fails", lambda arguments: 1 / 0, risk="local")

        plugins, _ = manager(Bad())
        effects, result = plugins.tools()[0].run({})
        self.assertEqual((effects, result.ok, plugins.records["bad"].errors), ([], False, 1))
        plugins.stop_all()


class RobotWithPluginsTests(unittest.TestCase):
    """A real Luna (headless) with plugins: commands, pages, Settings, crash recovery."""

    def setUp(self):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        from luna_core import app
        from luna_core.preferences import read_preferences
        from luna_core.ui import Page
        from test_settings import FakeSpeech

        self.app, self.speech = app, FakeSpeech()
        folder = tempfile.mkdtemp()
        for patch in (mock.patch.object(app, "speech", self.speech),
                      mock.patch.object(app, "read_preferences", lambda: read_preferences(Path(folder) / "p.json")),
                      mock.patch.object(app, "write_preferences", lambda prefs: None),
                      mock.patch.dict(os.environ, {"LUNA_MEMORY_FILE": str(Path(folder) / "m.db"),
                                    "LUNA_TASKS_FILE": str(Path(folder) / "t.db")})):
            patch.start()
            self.addCleanup(patch.stop)

        class Crashy(Page):
            name = "Crashy"

            def draw(self, hud, surface, t):
                raise RuntimeError("cannot draw")

        class Pages(Plugin):
            id = "pages"
            name = "Page plugin"
            permissions = ("speak", "microphone")

            def setup(self, luna):
                self.heard = []
                luna.add_page(Crashy())
                luna.add_command("hello", lambda t: luna.say("Hello!"), words={"en": ["howdy"]})
                luna.on_heard(self.heard.append)

        self.plugin = Pages()
        self.store = UnsavedStore({"pages": {"granted": ["microphone"]}})
        args = types.SimpleNamespace(fullscreen=False, windowed=True, no_voice=True, no_speech=False,
                                     no_ai=True, plugins=[self.plugin], plugin_store=self.store)
        self.hud = app.RobotHUD(args)
        self.addCleanup(self.hud.plugins.stop_all)
        self.addCleanup(app.pygame.quit)

    def test_spoken_command_and_on_heard(self):
        self.hud.handle_speech("howdy", 1.0)
        self.assertEqual(self.speech.spoken, ["Hello!"])
        self.assertEqual(self.plugin.heard, ["howdy"])

    def test_a_crashing_page_is_removed_and_luna_keeps_running(self):
        self.assertEqual([p.name for p in self.hud.pages][-1], "Crashy")
        self.hud.page = "Crashy"
        for t in (1.0, 2.0, 3.0):
            self.hud.frame(t)
        self.assertEqual(self.hud.plugins.records["pages"].status, "errors")
        self.assertEqual((self.hud.page, len(self.hud.pages)), ("Home", 4))
        self.hud.frame(4.0)

    def test_settings_turns_plugins_off_and_on_and_shows_permissions(self):
        from luna_core.settings import SECTIONS
        self.hud.page = "Settings"

        def tap(action):
            self.hud.frame(1.0)
            rect = [r for r, a in self.hud.settings_hits if a == action][0]
            self.hud.handle_touch(*rect.center, 1.0)

        tap(("section", SECTIONS.index("Plugins")))
        tap(("plugin_toggle", "pages"))
        self.assertEqual(self.hud.plugins.records["pages"].status, "off")
        self.assertNotIn("Crashy", [p.name for p in self.hud.pages])
        tap(("plugin_open", "pages"))
        tap(("plugin_toggle", "pages"))
        self.assertEqual(self.hud.plugins.records["pages"].status, "running")
        tap(("plugin_grant", ("pages", "microphone")))
        self.assertEqual(self.hud.plugins.records["pages"].status, "permission")
        self.assertEqual(self.store.get("pages")["granted"], [])
        tap(("plugin_back", None))
        self.hud.frame(1.0)
        self.assertIn(("plugin_open", "pages"), [a for _, a in self.hud.settings_hits])

    def test_safe_mode_starts_no_plugins(self):
        args = types.SimpleNamespace(fullscreen=False, windowed=True, no_voice=True, no_speech=False,
                                     no_ai=True, plugins=[Lights()], plugin_store=UnsavedStore(),
                                     no_plugins=True)
        self.hud.plugins.stop_all()
        hud = self.app.RobotHUD(args)
        self.assertEqual((hud.plugins.records, commands.route("lamps")[0]), ({}, "ignore"))


if __name__ == "__main__":
    unittest.main()
