"""What a plugin author uses: the Plugin base class, Setting, permissions and versions.

A plugin adds commands, pages, AI tools, personality, language text and settings to Luna
without editing the core. Everything goes through the PluginContext (`luna`) given to
`setup`; Luna removes it all again when the plugin is turned off.

Permissions decide what the context gives a plugin. Plugins are Python code running inside
Luna, so permissions are not a sandbox: they control the core API (and what the user is told),
not what arbitrary Python can do. Install only plugins you trust.
"""
import re
from dataclasses import dataclass

PLUGIN_API = 1  # changes only when existing plugins must change (with a major core version)

# name -> (sensitive, description shown in Settings). Sensitive permissions are off until the
# user turns them on in Settings > Plugins; a plugin does not start without its sensitive ones.
PERMISSIONS = {
    "speak": (False, "Talk with Luna's voice"),
    "face": (False, "Change Luna's face"),
    "ai": (False, "Add AI tools and personality"),
    "microphone": (True, "Hear what you say to Luna"),
    "memory": (True, "Read what Luna remembers about you"),
    "network": (True, "Use the internet"),
    "camera": (True, "Use the camera"),
    "motors": (True, "Move the robot"),
    "devices": (True, "Control devices in your home"),
}
SENSITIVE = frozenset(name for name, (sensitive, _) in PERMISSIONS.items() if sensitive)
PLUGIN_ID = re.compile(r"^[a-z][a-z0-9_-]{1,30}$")


class PermissionDenied(Exception):
    """A plugin used something it did not declare, or the user has not allowed."""


@dataclass(frozen=True)
class Setting:
    """A choice the user makes in Settings > Plugins; read it with `luna.setting(key)`."""
    key: str
    label: str
    choices: tuple
    default: str = None

    def __post_init__(self):
        if not self.choices or len(self.choices) > 4:
            raise ValueError("a setting needs 1 to 4 choices")
        if self.default is not None and self.default not in self.choices:
            raise ValueError("the default must be one of the choices")

    @property
    def initial(self):
        return self.default if self.default is not None else self.choices[0]


class Plugin:
    """Subclass this. Luna calls `setup(luna)` when the plugin starts, `teardown()` when it stops.

    id           unique, lower case: letters, digits, - and _
    name         shown in Settings (the id when empty)
    core         Luna Core versions it works with, e.g. ">=1.0,<2"
    permissions  names from PERMISSIONS it uses
    settings     Setting objects the user can change
    """

    id = ""
    name = ""
    version = "0.0.0"
    description = ""
    core = ">=1.0,<2"
    api = PLUGIN_API
    permissions = ()
    settings = ()

    def setup(self, luna):
        """Register commands, pages, tools... through `luna` (a PluginContext)."""

    def teardown(self):
        """Stop what the plugin started itself (threads, files). Registrations are undone by Luna."""

    def setting_changed(self, key, value):
        """The user changed one of the plugin's settings."""


def _version(text):
    parts = []
    for part in str(text).strip().split("."):
        digits = re.match(r"\d+", part)
        parts.append(int(digits.group()) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def version_matches(version, spec):
    """True when `version` ("1.2.0") meets every condition of `spec` (">=1.0,<2"; "" = any)."""
    have = _version(version)
    for condition in filter(None, (c.strip() for c in str(spec or "").split(","))):
        match = re.match(r"^(>=|<=|==|!=|>|<)\s*([\d.]+)$", condition)
        if not match:
            raise ValueError("unknown version condition: %r" % condition)
        op, want = match.group(1), _version(match.group(2))
        ok = {">=": have >= want, "<=": have <= want, "==": have == want, "!=": have != want,
              ">": have > want, "<": have < want}[op]
        if not ok:
            return False
    return True
