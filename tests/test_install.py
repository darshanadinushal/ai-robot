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
        self.assertEqual(self.run_ok([self.luna, "--version"]).strip(), "Luna Core 1.0.1")

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


@unittest.skipUnless(os.environ.get("LUNA_SLOW_TESTS") == "1", "set LUNA_SLOW_TESTS=1 to run")
@unittest.skipIf(os.name == "nt" or not shutil.which("bash") or not shutil.which("git"), "needs Linux bash and git")
class UpgradeRollbackTests(unittest.TestCase):
    """scripts/upgrade-pi.sh and rollback-pi.sh on a copy of this repository."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="luna-deploy-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.repo = self.home / "ai-robot"
        self.env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        self.env.update(HOME=str(self.home), LUNA_PYTHON=sys.executable, LUNA_PIP="skip",
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
        self.env.pop("LUNA_MEMORY_FILE", None)
        self.env.pop("LUNA_TASKS_FILE", None)
        self.env.pop("LUNA_PLUGINS_FILE", None)
        self.run_in(["git", "clone", "--quiet", "--no-local", str(ROOT), str(self.repo)], cwd=self.home)
        self.run_in(["git", "remote", "remove", "origin"])
        for script in (ROOT / "scripts").glob("*.sh"):  # the scripts as they are now
            shutil.copy(script, self.repo / "scripts" / script.name)
        self.commit("good")
        (self.repo / "NOTE.txt").write_text("a harmless change\n")
        self.commit("good2")
        init = self.repo / "luna_core" / "__init__.py"
        init.write_text(init.read_text() + "\nraise RuntimeError('broken release')\n")
        self.commit("bad")
        self.run_in(["git", "checkout", "--quiet", "good"])
        (self.home / ".ai-robot-hud.json").write_text('{"mode": "face"}')

    def run_in(self, command, cwd=None, check=True):
        result = subprocess.run(command, cwd=cwd or self.repo, env=self.env, capture_output=True,
                                text=True, timeout=300)
        if check and result.returncode:
            raise AssertionError("%s failed:\n%s\n%s" % (command, result.stdout, result.stderr))
        return result

    def commit(self, tag):
        self.run_in(["git", "add", "-A"])
        self.run_in(["git", "commit", "--quiet", "--allow-empty", "-m", tag])
        self.run_in(["git", "tag", tag])

    def head(self, ref="HEAD"):
        return self.run_in(["git", "rev-parse", ref]).stdout.strip()

    def test_upgrade_failed_upgrade_and_rollback(self):
        self.run_in([shutil.which("bash"), "scripts/upgrade-pi.sh", "good2"])
        self.assertEqual(self.head(), self.head("good2"))
        self.assertTrue((self.home / ".luna" / "last-backup").is_file())
        backup, = (self.home / ".luna" / "backups").iterdir()
        self.assertEqual((backup / ".ai-robot-hud.json").read_text(), '{"mode": "face"}')

        failed = self.run_in([shutil.which("bash"), "scripts/upgrade-pi.sh", "bad"], check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("did not start", failed.stdout)
        self.assertEqual(self.head(), self.head("good2"))

        (self.home / ".ai-robot-hud.json").write_text('{"mode": "wave"}')
        self.run_in([shutil.which("bash"), "scripts/rollback-pi.sh", "--with-data"])
        self.assertEqual(self.head(), self.head("good"))
        self.assertEqual((self.home / ".ai-robot-hud.json").read_text(), '{"mode": "face"}')
        self.run_in([shutil.which("bash"), "scripts/rollback-pi.sh"])  # undo the rollback
        self.assertEqual(self.head(), self.head("good2"))


if __name__ == "__main__":
    unittest.main()
