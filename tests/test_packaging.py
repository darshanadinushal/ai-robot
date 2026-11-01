"""Luna Core as a package: the public entry point, the old robot_hud name, logging, restart."""
import filecmp
import logging
import os
import shutil
import subprocess
import tempfile
import sys
import tomllib
import unittest
import warnings
from pathlib import Path
from unittest import mock

import luna_core
from luna_core import cli, logs

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name != "nt" and shutil.which("bash"), "needs bash (Linux, the Pi)")
class DeployScriptTests(unittest.TestCase):
    def test_the_agents_extra_is_installed_only_with_background_tasks_on(self):
        home = tempfile.mkdtemp()
        script = ('source scripts/deploy-common.sh; for case in none file env; do '
                  'if [ $case = file ]; then printf "X=1\\nexport LUNA_TASKS=\\"service\\"\\n" '
                  '> "$HOME/.luna.env"; fi; '
                  'if [ $case = env ]; then rm "$HOME/.luna.env"; export LUNA_TASKS=service; fi; '
                  'wants_agents && echo "$case yes" || echo "$case no"; done')
        env = {k: v for k, v in os.environ.items() if k != "LUNA_TASKS"}
        result = subprocess.run(["bash", "-c", script], cwd=ROOT, capture_output=True, text=True,
                                env=dict(env, HOME=home), timeout=30)
        self.assertEqual(result.stdout.split("\n")[:3], ["none no", "file yes", "env yes"],
                         result.stderr)


    def test_an_extra_the_version_does_not_have_is_skipped(self):
        # A rollback to v1.0.1 (no [agents] extra) with background tasks on.
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        (folder / "scripts").mkdir()
        shutil.copy(ROOT / "scripts" / "deploy-common.sh", folder / "scripts")
        (folder / "pyproject.toml").write_text(
            '[project.optional-dependencies]\nvoice = ["vosk>=0.3.45"]\nai = ["openai>=1.40"]\n')
        bin_dir = folder / ".venv" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "pip").write_text('#!/bin/sh\necho "pip $*" >> "$LOG"\n')
        (bin_dir / "pip").chmod(0o755)
        (bin_dir / "python").symlink_to(sys.executable)
        script = "source scripts/deploy-common.sh; install_packages && echo installed"
        env = dict(os.environ, HOME=str(folder), LUNA_TASKS="service", LOG=str(folder / "log"))
        env.pop("LUNA_PIP", None)
        result = subprocess.run(["bash", "-c", script], cwd=folder, capture_output=True, text=True,
                                env=env, timeout=60)
        self.assertIn("installed", result.stdout, result.stderr)
        log = (folder / "log").read_text()
        self.assertIn("vosk>=0.3.45", log)
        self.assertIn("openai>=1.40", log)
        self.assertNotIn("agents", log)


class PackageTests(unittest.TestCase):
    def test_version_matches_pyproject(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(project["tool"]["setuptools"]["dynamic"]["version"]["attr"],
                         "luna_core.__version__")
        self.assertEqual(luna_core.__version__, "1.1.0")

    def test_importing_the_core_does_not_start_pygame(self):
        code = ("import sys, luna_core, luna_core.plugins.manager; "
                "print('pygame' in sys.modules, luna_core.Robot.__name__)")
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.stdout.split(), ["False", "Robot"], result.stderr)

    def test_robot_display_choices(self):
        robot = luna_core.Robot(display="window", voice=False, ai=False)
        self.assertEqual((robot.options.windowed, robot.options.fullscreen, robot.options.no_voice,
                          robot.options.no_speech, robot.options.no_ai), (True, False, True, False, True))
        with self.assertRaises(ValueError):
            luna_core.Robot(display="tv")


class OldNameTests(unittest.TestCase):
    def test_robot_hud_imports_give_the_same_modules(self):
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            import robot_hud
            from robot_hud import commands
            from robot_hud.assistant_core import memory
            import robot_hud.app
        import luna_core.app
        import luna_core.assistant_core.memory
        self.assertIs(commands, luna_core.commands)
        self.assertIs(memory, luna_core.assistant_core.memory)
        self.assertIs(robot_hud.app, luna_core.app)
        self.assertIs(robot_hud.Robot, luna_core.Robot)

    def test_robot_setup_copies_match_the_core(self):
        old, core = ROOT / "robot_hud", ROOT / "luna_core"
        self.assertTrue((old / "app.py").is_file())  # robot-setup finds Luna's folder by it
        packs = sorted(p.name for p in (core / "languages").glob("*.json"))
        self.assertEqual(sorted(p.name for p in (old / "languages").glob("*.json")), packs)
        for name in ["voices.json"] + ["languages/" + p for p in packs]:
            self.assertTrue(filecmp.cmp(core / name, old / name, shallow=False),
                            "copy luna_core/%s to robot_hud/%s (robot-setup reads it)" % (name, name))


class LogTests(unittest.TestCase):
    def test_keys_never_reach_the_log(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "abc123secretvalue"}):
            text = logs.redact("key abc123secretvalue and sk-proj-AbCdEf1234567890_x end")
        self.assertEqual(text, "key *** and sk-*** end")
        record = logging.LogRecord("luna", logging.ERROR, __file__, 1, "bad key %s", ("sk-1234567890abcd",), None)
        self.assertEqual(logs.RedactingFormatter("%(message)s").format(record), "bad key sk-***")
        try:  # a plugin's error is logged with its traceback: that is masked too
            raise ValueError("token sk-1234567890abcd")
        except ValueError:
            record = logging.LogRecord("luna.plugins", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
        text = logs.RedactingFormatter("%(message)s").format(record)
        self.assertIn("ValueError: token sk-***", text)
        self.assertNotIn("1234567890abcd", text)

    def test_log_file_setting(self):
        with mock.patch.dict(os.environ, {"LUNA_LOG_FILE": "off"}):
            self.assertIsNone(logs.log_file())
        with mock.patch.dict(os.environ, {"LUNA_LOG_FILE": ""}):
            self.assertEqual(logs.log_file(), logs.DEFAULT_LOG_FILE)


class RestartTests(unittest.TestCase):
    def test_restart_uses_the_way_luna_was_started(self):
        python = sys.executable
        self.assertEqual(cli.restart_command(["main.py", "--windowed"]), [python, "main.py", "--windowed"])
        self.assertEqual(cli.restart_command(["/x/luna_core/__main__.py", "--no-ai"]),
                         [python, "-m", "luna_core", "--no-ai"])
        self.assertEqual(cli.restart_command(["/usr/bin/luna"]), [python, "-m", "luna_core"])


if __name__ == "__main__":
    unittest.main()
