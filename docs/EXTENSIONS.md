# Luna Core: building independent extensions

Luna's existing `robot_hud/assistant_core` remains the common conversation and
AI-tool foundation. It does not import pygame. `robot_hud/app.py` owns the
face, microphone, speech, screen and Raspberry Pi-specific adapters.

This first extension API adds independently installable **AI tools**. It does
not yet provide a plugin-controlled touchscreen page, autonomous motion or a
home automation driver. The existing Luna behavior stays the default.

## Install and enable a demo

From a checkout of `ai-robot`, using the same Python environment that runs
Luna:

```bash
python -m pip install -e ./examples/luna-education
```

Enable its entry point in the environment:

```bash
export LUNA_EXTENSIONS=education
python main.py --windowed
```

On the Raspberry Pi, install with `.venv/bin/python -m pip ...` and add a
line `LUNA_EXTENSIONS=education` to `~/.luna.env` instead of exporting.
Restart Luna after changing this setting. This works even if AI is disabled,
but AI tools require an API connection to actually answer conversationally.

With AI enabled, try: "Hey Luna, give me a multiplication practice question."
The demo returns an arithmetic question to the AI. It stores no student data.

Without `LUNA_EXTENSIONS`, no extension code loads. For multiple extensions:

```text
LUNA_EXTENSIONS=education,home
```

The exact names must match installed distribution entry-point names. Missing
or broken extensions are reported by name and exception type and skipped
rather than preventing Luna from starting.

## Create a separate Luna Home or Education repository

Publish a normal Python package in its own GitHub repository, and declare its
entry point:

```toml
[project.entry-points."luna.extensions"]
home = "luna_home:extension"
```

The factory returns a `LunaExtension`:

```python
from robot_hud.assistant_core.extensions import LunaExtension
from robot_hud.assistant_core.tools import Tool, ToolResult

def home_status(_arguments):
    # Replace this with a verified read from your connected home service.
    return [], ToolResult(False, "Home service is not connected")

def extension():
    return LunaExtension(
        name="home",
        tools=(Tool(
            name="home_status",
            description="Read live home sensor status if connected.",
            parameters={"type": "object", "properties": {},
                        "required": [], "additionalProperties": False},
            risk="info",
            run=home_status,
        ),),
        abilities=(),  # Only advertise working capabilities.
        connected=(),  # e.g. ("smart-home control",) AFTER it truly works.
    )
```

Install the extension into Luna's existing virtual environment (for example
`python -m pip install -e ../luna-home`). Do **not** copy Luna's face,
conversation, memory or voice code into the extension repository. For
production, use tested package versions instead of moving editable installs.

## Contract and safety

- `LunaExtension(name, tools=(), abilities=(), connected=(), api_version=1)` supplies
  strictly defined `Tool` objects, short verified capability text and names
  from `context.NOT_CONNECTED` that the extension has genuinely connected.
- `tools`, `abilities` and `connected` must be tuples. The factory's
  `name` must match its entry-point name.
- Tool names must be unique across extensions and cannot replace built-in
  face or memory actions.
- Every extension tool uses the **existing** `ToolExecutor`, JSON schema
  validation, per-turn limit and `PermissionPolicy`. Defaults permit only
  `local` and `memory`. `info` and `device` are denied until Luna gets an
  explicit user-permission policy. `motion` and `sensitive` are always
  refused by this path.
- A tool's exception produces a failed result, not an app crash. Do not log
  or return secrets in any tool result.
- Extensions run as trusted Python **inside Luna's process**, not a sandbox.
  Install and enable only packages you trust. Their factories should not
  connect to devices or do lengthy blocking work at startup.
- When AI tools are off, extension tools and advertised capabilities are
  not offered to the model. Other built-in voice commands are unchanged.
- User memory remains core-owned; toggling memory on/off reconstructs the
  registry without dropping installed extensions.

## Next milestones

Extract stable service interfaces for audio, memory, skills and hardware;
introduce optional extension settings/pages; then add per-device permissions,
capability health checks and extension lifecycle (start/stop/reconnect).
Home Assistant device integration and Education progress storage belong in
their **own** repositories, with their own tests and release versions.

Before deploying to the robot, run:

```bash
python -m unittest discover -s tests -v
```

Verify voice interaction, settings, memory and startup on the Raspberry Pi.
