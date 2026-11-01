"""An example Luna Core plugin with a background helper (see docs/PLUGINS.md).

"Hey Luna, plan three dinners from what's in my pantry": Luna's AI hands the job to the
plugin's "meals" helper, which looks in the pantry (the plugin's own tool, run by Luna after her
permission check) and reports back later. Needs background tasks (LUNA_TASKS=service) and the
user's OK for the "agents" permission in Settings > Plugins.
"""
from luna_core.plugins import Plugin, Setting

PANTRY = {
    "small": ["pasta", "tinned tomatoes", "onions", "eggs", "rice"],
    "full": ["pasta", "tinned tomatoes", "onions", "eggs", "rice", "lentils", "potatoes",
             "frozen peas", "cheese", "garlic"],
}


class PantryPlugin(Plugin):
    id = "pantry"
    name = "Pantry"
    version = "1.0.0"
    description = "A helper that plans meals from what is in your pantry."
    core = ">=1.1,<2"             # add_specialist is new in Luna Core 1.1
    permissions = ("ai", "agents")
    settings = (Setting("pantry", "Pantry", ("small", "full")),)

    def setup(self, luna):
        self.luna = luna
        luna.add_tool("items", "What is in the user's pantry now", self.items, risk="local")
        luna.add_specialist(
            "meals",
            description="Plans meals and dinners from what is in the user's pantry.",
            instructions=("You plan simple home meals. First look in the pantry with the "
                          "pantry_items tool, then suggest meals that use mostly those items "
                          "and name anything the user would need to buy."),
            tools=("items",),
            title="Meals",
            max_turns=4, max_seconds=60, max_usd=0.02)

    def items(self, arguments):
        return ", ".join(PANTRY[self.luna.setting("pantry")])
