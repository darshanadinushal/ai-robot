"""Command line start of Luna (main.py, `python -m luna_core` and the `luna` command)."""
import argparse
import os
import sys

from . import __version__
from .logs import setup_logging
from .robot import Robot


def restart_command(argv=None):
    """The command that starts Luna again with the same arguments."""
    argv = list(sys.argv if argv is None else argv)
    script = os.path.basename(argv[0]) if argv else ""
    if script.endswith(".py") and script != "__main__.py":  # started as `python main.py`
        return [sys.executable] + argv
    return [sys.executable, "-m", "luna_core"] + argv[1:]  # `luna` or `python -m luna_core`


def restart_app():
    """Start Luna again (after a new robot name)."""
    command = restart_command()
    if os.name == "nt":  # execv mangles arguments on Windows: start a new process instead
        import subprocess
        subprocess.Popen(command)
        sys.exit(0)
    os.execv(sys.executable, command)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Luna robot (Raspberry Pi + Windows)")
    parser.add_argument("--version", action="version", version="Luna Core " + __version__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--windowed", action="store_true", help="Use a resizable desktop window")
    mode.add_argument("--fullscreen", action="store_true", help="Use current display fullscreen")
    parser.add_argument("--no-voice", action="store_true", help="Disable microphone commands")
    parser.add_argument("--no-speech", action="store_true", help="Disable spoken replies")
    parser.add_argument("--no-ai", action="store_true", help="Disable online AI conversation")
    mode.add_argument("--headless", action="store_true", help="Run without a screen")
    parser.add_argument("--mock-hardware", action="store_true",
                        help="Use mock microphone, speaker, camera and motors (LUNA_HARDWARE=mock)")
    parser.add_argument("--screenshot", metavar="FILE", help="Save first frame as PNG and exit")
    args = parser.parse_args(argv)
    setup_logging()
    if args.screenshot:
        # One static frame: don't start the microphone, speech or AI threads.
        args.no_voice = args.no_speech = args.no_ai = True
    display = ("window" if args.windowed else "fullscreen" if args.fullscreen
               else "headless" if args.headless else "auto")
    robot = Robot(display=display, voice=not args.no_voice, speech=not args.no_speech,
                  ai=not args.no_ai, hardware="mock" if args.mock_hardware else None)
    if robot.run(screenshot=args.screenshot):
        restart_app()
