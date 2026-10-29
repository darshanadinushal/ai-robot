"""Plugins: apps add commands, pages, AI tools, personality, language text and settings.

    from luna_core import Robot
    from luna_core.plugins import Plugin, Setting

    class Lights(Plugin):
        id = "lights"
        name = "Lights"
        version = "1.0.0"
        permissions = ("speak",)

        def setup(self, luna):
            luna.add_command("on", self.on, words={"en": ["lights"]}, replies={"en": "Lights on."})

        def on(self, t):
            ...

    robot = Robot()
    robot.add_plugin(Lights())
    robot.run()

Installed packages can also offer plugins through the entry point group "luna.plugins";
those start off until the user turns them on in Settings > Plugins.
See docs/PLUGINS.md.
"""
from .api import PERMISSIONS, PLUGIN_API, PermissionDenied, Plugin, Setting

__all__ = ["Plugin", "Setting", "PermissionDenied", "PERMISSIONS", "PLUGIN_API"]
