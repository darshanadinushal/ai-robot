"""Raspberry Pi start-up cost and the one-startup rule shared with Robot Setup."""
import os
import shutil
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from luna_core import hardware
from luna_core.preferences import read_preferences

ROOT = Path(__file__).resolve().parents[1]


class StartupCostTests(unittest.TestCase):
    def test_app_starts_only_display_and_font(self):
        from luna_core import app
        from test_settings import FakeSpeech
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        patches = [mock.patch.object(app, "speech", FakeSpeech()),
                   mock.patch.object(app, "read_preferences",
                                     lambda: read_preferences(Path(folder) / "prefs.json")),
                   mock.patch.object(app, "write_preferences", lambda prefs: None),
                   mock.patch.dict(os.environ, {"LUNA_MEMORY_FILE": str(Path(folder) / "m.db")})]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        app.pygame.quit()
        args = types.SimpleNamespace(fullscreen=False, windowed=True, no_voice=True, no_speech=True,
                                     no_ai=True, hardware=hardware.create("mock"))
        hud = app.RobotHUD(args)
        self.addCleanup(app.pygame.quit)
        self.assertTrue(app.pygame.display.get_init())
        self.assertTrue(app.pygame.font.get_init())
        self.assertFalse(app.pygame.mixer.get_init())
        self.assertFalse(app.pygame.joystick.get_init())
        hud.frame(1.0)  # draws, events and the clock work without pygame.init()


class FontTests(unittest.TestCase):
    def setUp(self):
        import pygame
        pygame.font.init()
        self.pygame = pygame

    def test_falls_back_to_the_font_search_without_dejavu_files(self):
        from luna_core.ui import theme
        with mock.patch.dict(theme.DEJAVU_FILES, {False: "/no/such.ttf", True: "/no/such-bold.ttf"}):
            font = theme.load_font(20, bold=True)
        self.assertGreater(font.size("Luna")[0], 0)

    @unittest.skipUnless(os.path.isfile("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
                         and os.path.isfile("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
                         "needs the DejaVu fonts (Raspberry Pi OS, Debian, Ubuntu)")
    def test_direct_fonts_draw_exactly_like_sysfont(self):
        from luna_core.ui import theme
        text = "Hej! Luna lyssnar — 12:34 ÅÄÖ"
        for size, bold in ((17, False), (25, False), (27, True), (46, True)):
            direct = theme.load_font(size, bold=bold).render(text, True, (255, 255, 255))
            searched = self.pygame.font.SysFont("dejavusans", size, bold=bold).render(
                text, True, (255, 255, 255))
            self.assertEqual(direct.get_size(), searched.get_size(), (size, bold))
            to_bytes = getattr(self.pygame.image, "tobytes", None) or self.pygame.image.tostring
            self.assertEqual(to_bytes(direct, "RGBA"), to_bytes(searched, "RGBA"), (size, bold))


@unittest.skipIf(os.name == "nt" or not shutil.which("bash"), "needs Linux bash")
class InstallServiceTests(unittest.TestCase):
    """scripts/install-service.sh with a fake systemctl and a temporary home."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="luna-service-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        bin_dir = self.home / "bin"
        bin_dir.mkdir()
        self.calls = self.home / "systemctl.log"
        fake = bin_dir / "systemctl"
        fake.write_text('#!/bin/sh\necho "$@" >> "%s"\n' % self.calls)
        fake.chmod(0o755)
        self.env = dict(os.environ, HOME=str(self.home), PATH="%s:%s" % (bin_dir, os.environ["PATH"]))
        self.env.pop("XDG_CONFIG_HOME", None)
        self.unit = self.home / ".config" / "systemd" / "user" / "luna.service"
        self.desktop = self.home / ".config" / "autostart" / "luna.desktop"
        self.script = ROOT / "scripts" / "install-service.sh"

    def run_script(self, *args):
        return subprocess.run(["bash", str(self.script), *args], env=self.env, capture_output=True,
                              text=True, timeout=60)

    def test_install_and_remove(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        unit = self.unit.read_text()
        self.assertTrue(unit.startswith("# Created by Luna Core scripts/install-service.sh\n"))
        self.assertIn('ExecStart="%s/scripts/run-pi.sh"' % ROOT, unit)
        # A user service may not raise its own priority (Nice=-2 needs RLIMIT_NICE).
        self.assertNotIn("Nice=", unit)
        self.assertIn("Restart=on-failure", unit)
        self.assertIn("# Created by Luna Core", self.desktop.read_text())
        self.assertIn("--user restart luna.service", self.calls.read_text())

        self.assertEqual(self.run_script("--remove").returncode, 0)
        self.assertFalse(self.unit.exists())
        self.assertFalse(self.desktop.exists())

    def check_refused(self, *args):
        before = self.unit.read_text() if self.unit.exists() else None
        result = self.run_script(*args)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Robot Setup manages Luna", result.stderr)
        self.assertEqual(self.unit.read_text() if self.unit.exists() else None, before)
        self.assertFalse(self.desktop.exists())
        self.assertFalse(self.calls.exists())

    def test_leaves_a_robot_setup_service_alone(self):
        self.unit.parent.mkdir(parents=True)
        self.unit.write_text("# Managed by Robot Setup. Repair replaces this file.\n[Unit]\n")
        self.check_refused()
        self.check_refused("--remove")

    def test_leaves_robot_setup_startup_alone(self):
        entry = self.home / ".config" / "autostart" / "luna-robot-setup.desktop"
        entry.parent.mkdir(parents=True)
        entry.write_text("[Desktop Entry]\n")
        self.check_refused()
        self.assertTrue(entry.exists())


class RunPiTests(unittest.TestCase):
    def test_limits_malloc_arenas_but_respects_the_user(self):
        text = (ROOT / "scripts" / "run-pi.sh").read_text()
        self.assertIn('export MALLOC_ARENA_MAX="${MALLOC_ARENA_MAX:-2}"', text)
        self.assertLess(text.index("MALLOC_ARENA_MAX"), text.index("exec "))


if __name__ == "__main__":
    unittest.main()
