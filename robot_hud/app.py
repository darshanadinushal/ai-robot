"""robot-setup recognises Luna's folder by this file. The app is luna_core/app.py; with the
robot_hud package imported, `robot_hud.app` already is that module, so this file is not run."""
from luna_core.app import RobotHUD, main  # noqa: F401
