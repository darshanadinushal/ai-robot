"""Persist only user-selected visual preferences in the home directory."""
import json
from pathlib import Path

COLOR_CHOICES = {
    "CYAN": (34, 216, 255),
    "PURPLE": (184, 113, 255),
    "GREEN": (67, 235, 179),
    "AMBER": (255, 185, 67),
}


def default_path():
    return Path.home() / ".ai-robot-hud.json"


def read_preferences(path=None):
    result = {"color": "CYAN", "animations": True}
    try:
        data = json.loads(Path(path or default_path()).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            if data.get("color") in COLOR_CHOICES:
                result["color"] = data["color"]
            if isinstance(data.get("animations"), bool):
                result["animations"] = data["animations"]
    except (OSError, ValueError, TypeError):
        pass
    return result


def write_preferences(prefs, path=None):
    path = Path(path or default_path())
    content = {"color": prefs["color"], "animations": bool(prefs["animations"])}
    path.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
