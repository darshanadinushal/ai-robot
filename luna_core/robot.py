"""The public entry point of Luna Core (pygame is only imported when a robot runs)."""
from types import SimpleNamespace

DISPLAYS = ("auto", "window", "fullscreen")


class Robot:
    """One Luna robot: face, offline listening, speech and optional AI chat on one screen.

    display: "auto" (fullscreen on the Pi, a window on Windows), "window" or "fullscreen".
    voice, speech, ai: switch the microphone, spoken replies and online AI chat off.
    """

    def __init__(self, *, display="auto", voice=True, speech=True, ai=True):
        if display not in DISPLAYS:
            raise ValueError("display must be one of: " + ", ".join(DISPLAYS))
        self.options = SimpleNamespace(windowed=display == "window",
                                       fullscreen=display == "fullscreen",
                                       no_voice=not voice, no_speech=not speech, no_ai=not ai)
        self.restart_requested = False

    def run(self, screenshot=None):
        """Show the robot until it is closed. Returns True when it asked to restart
        (after a new robot name); `luna_core.cli.main` then starts it again."""
        from .app import RobotHUD

        hud = RobotHUD(self.options)
        hud.run(screenshot=screenshot)
        self.restart_requested = hud.restart
        return hud.restart
