"""Persist only user-selected visual preferences in the home directory."""
import json
from pathlib import Path

COLOR_CHOICES = {
    "CYAN": (34, 216, 255),
    "PURPLE": (184, 113, 255),
    "GREEN": (67, 235, 179),
    "AMBER": (255, 185, 67),
}


# name -> (eye offset from centre, eye y, eye scale, smile y or None)
LAYOUT_CHOICES = {
    "CLASSIC": (224, 296, 1.0, 474),
    "COMPACT": (150, 300, 0.8, 440),
    "MINIMAL": (200, 310, 0.9, None),
}
MODES = ("FACE", "WAVE")
WAVE_LAYOUTS = ("MESH", "BARS", "CIRCLE")


def default_path():
    return Path.home() / ".ai-robot-hud.json"


def read_preferences(path=None):
    result = {"color": "CYAN", "mode": "FACE", "layout": "CLASSIC",
              "wave_layout": "MESH", "animations": True}
    try:
        data = json.loads(Path(path or default_path()).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            if data.get("color") in COLOR_CHOICES:
                result["color"] = data["color"]
            if data.get("mode") in MODES:
                result["mode"] = data["mode"]
            if data.get("wave_layout") in WAVE_LAYOUTS:
                result["wave_layout"] = data["wave_layout"]
            if data.get("layout") in LAYOUT_CHOICES:
                result["layout"] = data["layout"]
            if isinstance(data.get("animations"), bool):
                result["animations"] = data["animations"]
    except (OSError, ValueError, TypeError):
        pass
    return result


def write_preferences(prefs, path=None):
    path = Path(path or default_path())
    content = {"color": prefs["color"], "mode": prefs.get("mode", "FACE"),
               "layout": prefs.get("layout", "CLASSIC"),
               "wave_layout": prefs.get("wave_layout", "MESH"), "animations": bool(prefs["animations"])}
    path.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
