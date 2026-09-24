"""Integration with the BlueGlance GNOME Shell extension.

On GNOME (Wayland) regular apps can't pin a window to the desktop layer, so
the desktop widget and the top bar indicator are drawn by a small companion
Shell extension. While it runs it owns the bus name :data:`EXTENSION_BUS_NAME`;
the app then hides its own widget window and tray icon.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from gi.repository import Gio, GLib, GObject

from .. import EXTENSION_UUID
from ..session import info as session_info

log = logging.getLogger(__name__)

EXTENSION_BUS_NAME = "io.github.rafay_ah.BlueGlance.ShellExtension"
# The small org.gnome.Shell.Extensions service forwards to GNOME Shell and is
# reachable from Flatpak too.
SHELL_BUS = "org.gnome.Shell.Extensions"
SHELL_PATH = "/org/gnome/Shell/Extensions"
EXTENSIONS_IFACE = "org.gnome.Shell.Extensions"
INACTIVE_GRACE_MS = 2500

STATE_ACTIVE = 1
STATE_INACTIVE = 2
STATE_ERROR = 3
STATE_OUT_OF_DATE = 4
STATE_INITIALIZED = 6


def bundled_extension_dir() -> Path | None:
    """Where this installation keeps a copy of the extension (for 'Install')."""
    candidates = []
    if os.environ.get("BLUEGLANCE_EXTENSION_DIR"):
        candidates.append(Path(os.environ["BLUEGLANCE_EXTENSION_DIR"]))
    here = Path(__file__).resolve()
    candidates.append(here.parents[3] / "gnome-extension" / EXTENSION_UUID)  # git checkout
    candidates.append(here.parents[2] / "gnome-extension" / EXTENSION_UUID)  # pkgdatadir
    for base in GLib.get_system_data_dirs():
        candidates.append(Path(base) / "gnome-shell" / "extensions" / EXTENSION_UUID)
    for path in candidates:
        if (path / "metadata.json").is_file():
            return path
    return None


def user_extension_dir() -> Path:
    if session_info().flatpak:
        # The sandbox's XDG_DATA_HOME is private; GNOME Shell reads the host's.
        base = os.environ.get("HOST_XDG_DATA_HOME") or os.path.join(GLib.get_home_dir(), ".local", "share")
        return Path(base) / "gnome-shell" / "extensions" / EXTENSION_UUID
    return Path(GLib.get_user_data_dir()) / "gnome-shell" / "extensions" / EXTENSION_UUID


def installed_on_disk() -> bool:
    if (user_extension_dir() / "metadata.json").is_file():
        return True
    if session_info().flatpak:
        return False  # the sandbox can't see the host's system-wide extensions
    return any(
        (Path(base) / "gnome-shell" / "extensions" / EXTENSION_UUID / "metadata.json").is_file()
        for base in GLib.get_system_data_dirs()
    )


class ShellIntegration(GObject.Object):
    __gtype_name__ = "BlueGlanceShellIntegration"
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.relevant = session_info().gnome
        self.active = False
        self.info: dict = {}
        self.user_extensions_enabled = True
        self._bus: Gio.DBusConnection | None = None
        self._watch_ids: list[int] = []
        self._signal_id = 0
        self._inactive_source = 0

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as error:
            log.warning("No session bus: %s", error.message)
            return
        self._watch_ids.append(Gio.bus_watch_name_on_connection(
            self._bus, EXTENSION_BUS_NAME, Gio.BusNameWatcherFlags.NONE,
            lambda *_: self._set_active(True), lambda *_: self._set_active(False)))
        if not self.relevant:
            return
        self._watch_ids.append(Gio.bus_watch_name_on_connection(
            self._bus, "org.gnome.Shell", Gio.BusNameWatcherFlags.NONE,
            lambda *_: self.refresh(), lambda *_: self._set_info({})))
        self._signal_id = self._bus.signal_subscribe(
            SHELL_BUS, EXTENSIONS_IFACE, "ExtensionStateChanged", SHELL_PATH, None,
            Gio.DBusSignalFlags.NONE, self._on_state_changed)

    def stop(self) -> None:
        if self._inactive_source:
            GLib.source_remove(self._inactive_source)
            self._inactive_source = 0
        for watch_id in self._watch_ids:
            Gio.bus_unwatch_name(watch_id)
        self._watch_ids.clear()
        if self._bus is not None and self._signal_id:
            self._bus.signal_unsubscribe(self._signal_id)
            self._signal_id = 0

    # -- state --------------------------------------------------------------
    def _set_active(self, active: bool) -> None:
        if self._inactive_source:
            GLib.source_remove(self._inactive_source)
            self._inactive_source = 0
        if active:
            self._apply_active(True)
        elif self.active:
            # Don't flash the fallback widget while the Shell reloads the extension.
            self._inactive_source = GLib.timeout_add(INACTIVE_GRACE_MS, self._on_inactive_timeout)

    def _on_inactive_timeout(self) -> bool:
        self._inactive_source = 0
        self._apply_active(False)
        return GLib.SOURCE_REMOVE

    def _apply_active(self, active: bool) -> None:
        if active != self.active:
            log.info("GNOME Shell extension %s", "connected" if active else "not running")
            self.active = active
            self.emit("changed")

    def _set_info(self, info: dict) -> None:
        self.info = info
        self._maybe_auto_enable()
        self.emit("changed")

    def _maybe_auto_enable(self) -> None:
        """Enable the extension once, the first time GNOME Shell knows about it.

        The desktop widget is on by default; if the user later disables the
        extension themselves, we respect that.
        """
        config = self.app.config
        if config is None or config["shell_extension_autoenabled"] or not self.info:
            return
        if not config["widget_enabled"] and not config["tray_icon"]:
            return
        config["shell_extension_autoenabled"] = True
        state = int(self.info.get("state", 0) or 0)
        if state in (STATE_INACTIVE, STATE_INITIALIZED):
            log.info("Enabling the BlueGlance GNOME Shell extension")
            self.enable()

    def _on_state_changed(self, _conn, _sender, _path, _iface, _signal, params) -> None:
        uuid, info = params.unpack()
        if uuid == EXTENSION_UUID:
            self._set_info(info)

    def refresh(self) -> None:
        if self._bus is None:
            return

        def on_info(bus, result):
            try:
                info = bus.call_finish(result).unpack()[0]
            except GLib.Error as error:
                log.debug("GetExtensionInfo failed: %s", error.message)
                info = {}
            self._set_info(info)

        self._bus.call(SHELL_BUS, SHELL_PATH, EXTENSIONS_IFACE, "GetExtensionInfo",
                       GLib.Variant("(s)", (EXTENSION_UUID,)), GLib.VariantType("(a{sv})"),
                       Gio.DBusCallFlags.NONE, 5000, None, on_info)

        def on_prop(bus, result):
            try:
                value = bus.call_finish(result).unpack()[0]
                self.user_extensions_enabled = bool(value)
            except GLib.Error:
                self.user_extensions_enabled = True
            self.emit("changed")

        self._bus.call(SHELL_BUS, SHELL_PATH, "org.freedesktop.DBus.Properties", "Get",
                       GLib.Variant("(ss)", (EXTENSIONS_IFACE, "UserExtensionsEnabled")),
                       GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 5000, None, on_prop)

    def describe(self) -> tuple[str, str, str | None]:
        """(status, human readable explanation, button label or None)."""
        if self.active:
            return "active", "The widget is pinned to your desktop and lives in the top bar", None
        state = int(self.info.get("state", 0) or 0) if self.info else 0
        if self.info and not self.user_extensions_enabled:
            return "blocked", "Extensions are switched off in GNOME", "Turn On"
        if state == STATE_ERROR:
            error = self.info.get("error") or "unknown error"
            return "error", f"The extension failed to load: {error}", None
        if state == STATE_OUT_OF_DATE:
            return "outdated", "The extension doesn't support this GNOME version yet", None
        if self.info:
            return "disabled", "Enable it to pin the widget to your desktop", "Enable"
        if installed_on_disk():
            return "pending", "Log out and back in once to finish setting up the widget", None
        if bundled_extension_dir() is not None:
            return "missing", "Install the companion extension to pin the widget to your desktop", "Install"
        return "missing", "The companion extension isn't installed", None

    def perform_action(self, parent=None) -> None:
        status, _text, _button = self.describe()
        if status == "disabled":
            self.enable()
        elif status == "blocked":
            self._bus.call(SHELL_BUS, SHELL_PATH, "org.freedesktop.DBus.Properties", "Set",
                           GLib.Variant("(ssv)", (EXTENSIONS_IFACE, "UserExtensionsEnabled",
                                                  GLib.Variant("b", True))),
                           None, Gio.DBusCallFlags.NONE, 5000, None, lambda *_: self.refresh())
        elif status == "missing":
            self.install_user_copy()

    def enable(self) -> None:
        if self._bus is None:
            return

        def done(bus, result):
            try:
                bus.call_finish(result)
            except GLib.Error as error:
                log.warning("EnableExtension failed: %s", error.message)
            self.refresh()

        self._bus.call(SHELL_BUS, SHELL_PATH, EXTENSIONS_IFACE, "EnableExtension",
                       GLib.Variant("(s)", (EXTENSION_UUID,)), GLib.VariantType("(b)"),
                       Gio.DBusCallFlags.NONE, 5000, None, done)

    def install_user_copy(self) -> bool:
        source = bundled_extension_dir()
        if source is None:
            return False
        target = user_extension_dir()
        try:
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target)
        except OSError as exc:
            log.warning("Could not install the Shell extension: %s", exc)
            return False
        # GNOME only discovers new extensions at login, but queue it as enabled
        # so it starts right away next time.
        self._add_to_enabled_list()
        self.emit("changed")
        return True

    def _add_to_enabled_list(self) -> None:
        source = Gio.SettingsSchemaSource.get_default()
        if source is None or source.lookup("org.gnome.shell", True) is None:
            return
        settings = Gio.Settings.new("org.gnome.shell")
        enabled = list(settings.get_strv("enabled-extensions"))
        if EXTENSION_UUID not in enabled:
            enabled.append(EXTENSION_UUID)
            settings.set_strv("enabled-extensions", enabled)
        disabled = [u for u in settings.get_strv("disabled-extensions") if u != EXTENSION_UUID]
        settings.set_strv("disabled-extensions", disabled)
        Gio.Settings.sync()
