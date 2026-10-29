"""The public entry point of Luna Core (pygame is only imported when a robot runs)."""
import os
from types import SimpleNamespace

from . import hardware as devices

DISPLAYS = ("auto", "window", "fullscreen", "headless")


class Robot:
    """One Luna robot: face, offline listening, speech and optional AI chat on one screen.

    display: "auto" (fullscreen on the Pi, a window on Windows), "window", "fullscreen" or
    "headless" (no screen needed: SDL's dummy video and audio drivers).
    voice, speech, ai: switch the microphone, spoken replies and online AI chat off.
    hardware: "real", "mock", a hardware.Hardware, or None for LUNA_HARDWARE (default real).
    """

    def __init__(self, *, display="auto", voice=True, speech=True, ai=True, hardware=None):
        if display not in DISPLAYS:
            raise ValueError("display must be one of: " + ", ".join(DISPLAYS))
        if not isinstance(hardware, devices.Hardware):
            hardware = devices.create(hardware, detect_audio=voice)
        self.display = display
        self.hardware = hardware
        self.options = SimpleNamespace(windowed=display in ("window", "headless"),
                                       fullscreen=display == "fullscreen",
                                       no_voice=not voice, no_speech=not speech, no_ai=not ai,
                                       hardware=hardware, pages=[])
        self.restart_requested = False

    def add_page(self, page):
        """Add an app page (a luna_core.ui.Page) with its own navigation button.

        Returns the page. The core pages are Chat, Home, Camera and Settings; names are unique.
        """
        names = {"Chat", "Home", "Camera", "Settings"} | {p.name for p in self.options.pages}
        if getattr(page, "name", None) in names:
            raise ValueError("there is already a page called %r" % page.name)
        self.options.pages.append(page)
        return page

    def run(self, screenshot=None):
        """Show the robot until it is closed. Returns True when it asked to restart
        (after a new robot name); `luna_core.cli.main` then starts it again."""
        if self.display == "headless":  # before pygame starts
            os.environ["SDL_VIDEODRIVER"] = "dummy"
            os.environ["SDL_AUDIODRIVER"] = "dummy"
        from .app import RobotHUD

        hud = RobotHUD(self.options)
        hud.run(screenshot=screenshot)
        self.restart_requested = hud.restart
        return hud.restart
