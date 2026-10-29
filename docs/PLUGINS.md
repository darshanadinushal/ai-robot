# Luna Core plugins

A plugin adds commands, pages, AI tools, personality, language text and settings to Luna
without editing Luna Core. Apps (luna-home, luna-education, ...) are mostly plugins plus a
small start script. Plugin API version: `luna_core.plugins.PLUGIN_API` = 1.

A full example is in [`examples/hello_plugin/`](../examples/hello_plugin/luna_hello/__init__.py).

## A plugin

```python
from luna_core import Robot
from luna_core.plugins import Plugin, Setting


class Lights(Plugin):
    id = "lights"                 # unique: lower case, digits, - and _
    name = "Lights"               # shown in Settings > Plugins
    version = "1.0.0"
    description = "Say 'lamps' to switch the lamps on."
    core = ">=1.0,<2"             # Luna Core versions it works with
    permissions = ("speak", "ai", "devices")
    settings = (Setting("colour", "Colour", ("warm", "cold")),)

    def setup(self, luna):        # luna is a PluginContext
        luna.add_command("on", self.on, words={"en": ["lamps"], "sv": ["lampor"]},
                         replies={"en": "Lamps on.", "sv": "Lamporna är tända."})
        luna.add_tool("switch", "Switch the lamps", self.switch,
                      parameters={"state": ["on", "off"]}, risk="device")
        luna.add_personality("You can switch the lamps in the living room.")

    def on(self, t):
        ...

    def switch(self, arguments):
        return "the lamps are " + arguments["state"]

    def teardown(self):           # stop your own threads or files; Luna removes the rest
        ...


robot = Robot()
robot.add_plugin(Lights())
robot.run()
```

## What `luna` (PluginContext) offers

| Method | Needs | What it does |
|---|---|---|
| `add_command(name, run, words, replies=None)` | `speak` for replies | One-word spoken commands; `run(t)` is called |
| `add_page(page)` | – | A `luna_core.ui.Page` with its own navigation button |
| `add_tool(name, description, run, parameters, risk)` | `ai` (+ `devices` for risk `"device"`) | An AI tool; `run(arguments)` returns a short text |
| `add_personality(text)` | `ai` | A sentence added to what the AI is told |
| `add_language_text(code, screen=, speech=)` | – | Translations for `tr()` and spoken texts |
| `on_heard(callback)` | `microphone` | `callback(text)` for each sentence Luna hears |
| `every(seconds, callback)` | – | `callback(t)` regularly on the UI thread |
| `say(text)` | `speak` | Luna says it |
| `set_mood(mood)`, `look(direction)` | `face` | Change Luna's face |
| `memories()` | `memory` | What Luna remembers about the user |
| `camera()`, `motors()` | `camera`, `motors` | Devices; motor speeds are clamped and stop with the plugin |
| `setting(key)` | – | The user's choice for one of your `settings` |

Everything runs on Luna's UI thread: keep callbacks quick, and never call `luna` from your own
threads. Command words must be single words Luna does not already use. With the AI off, Luna
listens only for known words, so new words are added to the speech grammar when a plugin starts.

## Permissions

| Permission | Sensitive | Meaning |
|---|---|---|
| `speak`, `face`, `ai` | no | Talk, change the face, add AI tools and personality |
| `microphone` | yes | Hear what the user says |
| `memory` | yes | Read what Luna remembers |
| `network` | yes | Use the internet (declared and shown to the user) |
| `camera`, `motors`, `devices` | yes | Use the camera, move the robot, control home devices |

- A plugin must declare every permission it uses; anything else raises `PermissionDenied`.
- Sensitive permissions are **off until the user allows them** in Settings > Plugins; until then
  the plugin waits ("Needs your permission"). Taking a permission back stops the plugin.
- AI tools: `"local"`/`"info"` tools are allowed; `"device"` tools need `devices`. Movement
  (`"motion"`) and `"sensitive"` tools are never run by the AI.
- Plugins are Python code inside Luna, so permissions are **not a sandbox**: they control Luna's
  API and what the user is told, not what arbitrary Python can do. Install only plugins you trust.

## Lifecycle and recovery

```text
added / discovered -> checked (id, permissions, API, core version)
  -> waiting for permission -> setup(luna) -> running -> stopped (teardown)
```

- **App plugins** (`Robot(plugins=[...])`, `robot.add_plugin`) are on by default.
- **Installed plugins** (packages with an entry point in the group `luna.plugins`, see the
  example's `pyproject.toml`) are listed but **off** until the user turns them on.
- The user turns plugins on and off, allows permissions and changes settings in
  Settings > Plugins; this is saved in `~/.luna/plugins.json` (`LUNA_PLUGINS_FILE`).
- Every call into plugin code is guarded: an error is logged (with the traceback, keys masked)
  and counted. After 3 errors the plugin is turned off ("Turned off after errors"); its pages,
  commands, tools and texts are removed and Luna keeps running. A plugin that fails to start 3
  times in a row stays off until the user turns it on again.
- A plugin made for another Luna Core version or plugin API is shown as "Not made for this Luna
  version" and not started. A package that cannot be imported is shown as "Could not be loaded".
- Safe mode: `luna --no-plugins` (or `Robot(safe_mode=True)`) starts Luna with no plugins.

## Compatibility promise
Within Luna Core 1.x the names of `Plugin`, `Setting`, the `PluginContext` methods and the
permissions stay; new ones may be added in minor versions. `tests/test_plugins.py` checks this
list. A change that breaks plugins raises `PLUGIN_API` and the major version.
