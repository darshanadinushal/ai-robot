"""An example Luna Core plugin (see docs/PLUGINS.md).

Say "greet" (or "hälsa" in Swedish): Luna smiles and greets you. A "Hello" page counts the
greetings, the AI can ask how many there were, and Settings > Plugins chooses the greeting.
"""
from luna_core.plugins import Plugin, Setting
from luna_core.ui import Page

GREETINGS = {"short": "Hi!", "long": "Hello there, nice to see you!"}


class HelloPage(Page):
    name = "Hello"

    def __init__(self, plugin):
        self.plugin_object = plugin

    def draw(self, hud, surface, t):
        from luna_core.ui.draw import draw_text
        from luna_core.ui.theme import TEXT

        text = "Greetings: %d" % self.plugin_object.count
        draw_text(surface, text, hud.fonts["title"], TEXT, center=(640, 320))

    def touch(self, hud, x, y, t):
        self.plugin_object.greet(t)


class HelloPlugin(Plugin):
    id = "hello"
    name = "Hello"
    version = "1.0.0"
    description = "Say \"greet\" and Luna greets you."
    core = ">=1.0,<2"
    permissions = ("speak", "face", "ai")
    settings = (Setting("greeting", "Greeting", ("short", "long")),)

    def setup(self, luna):
        self.luna, self.count = luna, 0
        luna.add_command("greet", self.greet, words={"en": ["greet"], "sv": ["hälsa"]})
        luna.add_page(HelloPage(self))
        luna.add_language_text("sv", screen={"Hello": "Hej", "Greeting": "Hälsning"})
        luna.add_tool("greetings", "How many times Luna has greeted the user today",
                      lambda arguments: str(self.count), risk="local")
        luna.add_personality("When the user greets you, you are glad to see them.")

    def greet(self, t):
        self.count += 1
        self.luna.set_mood("happy")
        self.luna.say(GREETINGS[self.luna.setting("greeting")])
