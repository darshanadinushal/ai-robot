"""Which plugins are on, their granted permissions and settings (~/.luna/plugins.json).

Kept apart from the face settings so a broken plugin entry can never reset them.
LUNA_PLUGINS_FILE moves the file (tests).
"""
import json
import logging
import os
from pathlib import Path

log = logging.getLogger("luna.plugins")


def default_path():
    return Path(os.environ.get("LUNA_PLUGINS_FILE") or Path.home() / ".luna" / "plugins.json")


class PluginStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else default_path()
        self.data = self._read()

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            plugins = data.get("plugins") if isinstance(data, dict) else None
            return {key: value for key, value in (plugins or {}).items() if isinstance(value, dict)}
        except (OSError, ValueError, AttributeError):
            return {}

    def get(self, plugin_id):
        """The saved entry: enabled (None: never chosen), granted, settings, start_failures, off_reason."""
        entry = self.data.get(plugin_id, {})
        granted, settings = entry.get("granted"), entry.get("settings")
        enabled, failures, reason = entry.get("enabled"), entry.get("start_failures"), entry.get("off_reason")
        return {"enabled": enabled if isinstance(enabled, bool) else None,
                "granted": [p for p in granted if isinstance(p, str)] if isinstance(granted, list) else [],
                "settings": dict(settings) if isinstance(settings, dict) else {},
                "start_failures": failures if isinstance(failures, int) else 0,
                "off_reason": reason if isinstance(reason, str) else ""}

    def update(self, plugin_id, **values):
        entry = self.get(plugin_id)
        before = dict(entry)
        entry.update(values)
        if entry != before or plugin_id not in self.data:
            self.data[plugin_id] = entry
            self.save()

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"plugins": self.data}, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError as error:
            log.warning("Luna plugins: could not save %s (%s)", self.path, type(error).__name__)


class UnsavedStore(PluginStore):
    """A store that is never written to disk (tests)."""

    def __init__(self, data=None):
        self.path = None
        self.data = dict(data or {})

    def save(self):
        pass
