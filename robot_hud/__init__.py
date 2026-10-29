"""Old name of Luna Core, kept so existing code keeps working (to be removed in Luna Core 2.0).

`import robot_hud.app` gives the very same module object as `luna_core.app` (so patches in
old tests still reach the real code). voices.json and languages/ in this folder are copies
for robot-setup, which reads them from here; tests/test_packaging.py keeps them identical.
"""
import importlib
import importlib.abc
import importlib.util
import sys
import warnings

from luna_core import Robot, __version__

warnings.warn("robot_hud is now luna_core; import luna_core instead", DeprecationWarning,
              stacklevel=2)


class _Alias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Imports robot_hud.<name> as luna_core.<name>."""

    def find_spec(self, name, path=None, target=None):
        if name.startswith(__name__ + "."):
            return importlib.util.spec_from_loader(name, self)
        return None

    def create_module(self, spec):
        return importlib.import_module("luna_core" + spec.name[len(__name__):])

    def exec_module(self, module):
        pass


if not any(type(finder).__module__ == __name__ for finder in sys.meta_path):
    sys.meta_path.insert(0, _Alias())
