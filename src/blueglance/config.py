"""User preferences, stored as JSON in ``$XDG_CONFIG_HOME/blueglance/config.json``.

A plain JSON file (instead of GSettings) keeps BlueGlance runnable straight
from a git checkout, a .deb, a Flatpak or ``~/.local`` without installing and
compiling schemas.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path

from gi.repository import GLib, GObject

log = logging.getLogger(__name__)

WIDGET_SIZES = ("small", "medium", "large")
WIDGET_THEMES = ("auto", "light", "dark")
WIDGET_ANCHORS = ("top-left", "top-right", "bottom-left", "bottom-right")

DEFAULTS: dict = {
    # General
    "autostart": True,
    "run_in_background": True,
    "onboarded": False,
    # Tray / top bar
    "tray_icon": True,
    "tray_label": False,
    # Desktop widget
    "widget_enabled": True,
    "widget_size": "medium",
    "widget_theme": "auto",
    "widget_position": None,  # {"x": int, "y": int} for the X11 window
    "widget_anchor": "top-right",  # layer-shell compositors
    "widget_margin_x": 48,
    "widget_margin_y": 64,
    "shell_widget_position": None,  # opaque, owned by the GNOME Shell extension
    # Notifications
    "notify_low": True,
    "low_threshold": 20,
    "notify_full": False,
    # Devices
    "show_disconnected": True,
    "show_no_battery": True,
    "show_system_battery": False,
    "hidden_devices": [],
    "airpods_enhanced": True,
}


def config_dir() -> Path:
    return Path(GLib.get_user_config_dir()) / "blueglance"


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(GLib.get_home_dir(), ".local", "state")
    return Path(base) / "blueglance"


def _validate(key: str, value):
    default = DEFAULTS[key]
    if key == "widget_size" and value not in WIDGET_SIZES:
        return default
    if key == "widget_theme" and value not in WIDGET_THEMES:
        return default
    if key == "widget_anchor" and value not in WIDGET_ANCHORS:
        return default
    if key == "low_threshold":
        try:
            return max(5, min(50, int(value)))
        except (TypeError, ValueError):
            return default
    if key in ("widget_margin_x", "widget_margin_y"):
        try:
            return max(0, min(4000, int(value)))
        except (TypeError, ValueError):
            return default
    if key == "hidden_devices":
        return [str(v) for v in value] if isinstance(value, list) else []
    if isinstance(default, bool):
        return bool(value)
    return value


class Config(GObject.Object):
    """Tiny observable key/value store.

    ``changed`` is emitted with the key name after every effective change.
    Writes are debounced and atomic (write to temp file + rename).
    """

    __gtype_name__ = "BlueGlanceConfig"
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    def __init__(self, path: Path | None = None, *, persist: bool = True):
        super().__init__()
        self.path = path or config_dir() / "config.json"
        self._persist = persist
        self._values = copy.deepcopy(DEFAULTS)
        self._save_source = 0
        self.first_run = True
        self._load()

    # -- public API ---------------------------------------------------------
    def get(self, key: str):
        return copy.deepcopy(self._values[key])

    def __getitem__(self, key: str):
        return self.get(key)

    def set(self, key: str, value) -> None:
        if key not in DEFAULTS:
            raise KeyError(key)
        value = _validate(key, value)
        if self._values.get(key) == value:
            return
        self._values[key] = copy.deepcopy(value)
        self._schedule_save()
        self.emit("changed", key)

    def __setitem__(self, key: str, value) -> None:
        self.set(key, value)

    def reset(self, key: str) -> None:
        self.set(key, copy.deepcopy(DEFAULTS[key]))

    def is_hidden(self, device_id: str) -> bool:
        return device_id in self._values["hidden_devices"]

    def set_hidden(self, device_id: str, hidden: bool) -> None:
        hidden_ids = [d for d in self._values["hidden_devices"] if d != device_id]
        if hidden:
            hidden_ids.append(device_id)
        self.set("hidden_devices", hidden_ids)

    def flush(self) -> None:
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = 0
        self._save()

    # -- persistence --------------------------------------------------------
    def _load(self) -> None:
        if not self._persist:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.warning("Ignoring unreadable config %s: %s", self.path, exc)
            return
        self.first_run = False
        if not isinstance(data, dict):
            return
        for key, value in data.items():
            if key in DEFAULTS:
                self._values[key] = _validate(key, value)

    def _schedule_save(self) -> None:
        if not self._persist or self._save_source:
            return
        self._save_source = GLib.timeout_add(250, self._on_save_timeout)

    def _on_save_timeout(self) -> bool:
        self._save_source = 0
        self._save()
        return GLib.SOURCE_REMOVE

    def _save(self) -> None:
        if not self._persist:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._values, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.path)
            self.first_run = False
        except OSError as exc:
            log.warning("Could not save config to %s: %s", self.path, exc)
