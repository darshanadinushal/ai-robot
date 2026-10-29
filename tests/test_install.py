"""A clean installation, as a user or an app gets it (slow: opt in with LUNA_SLOW_TESTS=1).

Builds the luna-core wheel, installs it with the example plugin into a new virtual
environment (needs the internet for pygame), and checks the command, the imports, the
data files, plugin discovery and a headless screenshot, all outside the source folder.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("LUNA_SLOW_TESTS") == "1", "set LUNA_SLOW_TESTS=1 to run")
class CleanInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = Path(tempfile.mkdtemp(prefix="luna-install-"))
        wheels = cls.folder / "wheels"
        cls.run_ok([sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w", str(wheels), str(ROOT)])
        env_dir = cls.folder / "env"
        venv.EnvBuilder(with_pip=True).create(env_dir)
        scripts = env_dir / ("Scripts" if os.name == "nt" else "bin")
        cls.python = scripts / ("python.exe" if os.name == "nt" else "python")
        cls.luna = scripts / ("luna.exe" if os.name == "nt" else "luna")
        wheel = next(wheels.glob("luna_core-*.whl"))
        cls.run_ok([cls.python, "-m", "pip", "install", "-q", str(wheel)])
        cls.run_ok([cls.python, "-m", "pip", "install", "-q", "--no-deps", str(ROOT / "examples" / "hello_plugin")])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, ignore_errors=True)

    @classmethod
    def run_ok(cls, command, **kwargs):
        env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy", LUNA_LOG_FILE="off",
                   LUNA_PLUGINS_FILE=str(cls.folder / "plugins.json"), PYTHONPATH="")
        result = subprocess.run([str(c) for c in command], cwd=cls.folder, env=env, capture_output=True,
                                text=True, timeout=900, **kwargs)
        if result.returncode:
            raise AssertionError("%s failed:\n%s\n%s" % (command[0], result.stdout[-2000:], result.stderr[-2000:]))
        return result.stdout

    def test_the_luna_command(self):
        self.assertEqual(self.run_ok([self.luna, "--version"]).strip(), "Luna Core 1.0.0")

    def test_imports_data_and_plugins_come_from_the_installed_package(self):
        code = """
import importlib.metadata as metadata, pathlib, luna_core, robot_hud
from luna_core import Robot, languages
from luna_core.plugins import Plugin
assert "site-packages" in luna_core.__file__, luna_core.__file__
assert robot_hud.Robot is Robot
assert {"en", "sv"} <= set(languages.PACKS)
assert (pathlib.Path(luna_core.__file__).parent / "voices.json").is_file()
assert [p.name for p in metadata.entry_points(group="luna.plugins")] == ["hello"]
print("ok")
"""
        self.assertEqual(self.run_ok([self.python, "-c", code]).strip(), "ok")

    def test_headless_screenshot_with_the_installed_plugin(self):
        picture = self.folder / "luna.png"
        self.run_ok([self.luna, "--headless", "--screenshot", picture])
        self.assertGreater(picture.stat().st_size, 10_000)
        self.run_ok([self.luna, "--headless", "--no-plugins", "--screenshot", picture])


if __name__ == "__main__":
    unittest.main()
