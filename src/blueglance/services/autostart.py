"""Launch at login: an XDG autostart entry, or the Background portal in Flatpak.

Distribution packages install a system-wide entry (/etc/xdg/autostart) so
BlueGlance starts from the very first login. A per-user entry with the same
name overrides it: turning autostart off writes one with ``Hidden=true``.
"""

from __future__ import annotations

import configparser
import logging
import os
import shutil
import sys
from pathlib import Path

from gi.repository import Gio, GLib

from .. import APP_ID, APP_NAME
from ..session import info as session_info

log = logging.getLogger(__name__)

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
BACKGROUND_IFACE = "org.freedesktop.portal.Background"
FILE_NAME = f"{APP_ID}.desktop"


def user_entry() -> Path:
    return Path(GLib.get_user_config_dir()) / "autostart" / FILE_NAME


def system_entry() -> Path | None:
    for base in GLib.get_system_config_dirs():
        path = Path(base) / "autostart" / FILE_NAME
        if path.is_file():
            return path
    return None


def entry_enabled(path: Path) -> bool:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, configparser.Error):
        return False
    section = parser["Desktop Entry"] if parser.has_section("Desktop Entry") else {}
    hidden = str(section.get("Hidden", "false")).strip().lower() == "true"
    disabled = str(section.get("X-GNOME-Autostart-enabled", "true")).strip().lower() == "false"
    return not hidden and not disabled


def is_enabled() -> bool:
    user = user_entry()
    if user.exists():
        return entry_enabled(user)
    system = system_entry()
    return system is not None and entry_enabled(system)


def launch_command() -> list[str]:
    """The command that starts this very installation of BlueGlance."""
    argv0 = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    if os.path.basename(argv0) == "blueglance" and os.access(argv0, os.X_OK):
        return [argv0]
    installed = shutil.which("blueglance")
    if installed:
        return [installed]
    src_dir = str(Path(__file__).resolve().parents[2])
    return ["env", f"PYTHONPATH={src_dir}", sys.executable, "-m", "blueglance"]


def desktop_exec(argv: list[str]) -> str:
    def quote(arg: str) -> str:
        if arg and all(c.isalnum() or c in "/._-=:+," for c in arg):
            return arg
        escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
        return f'"{escaped}"'

    return " ".join(quote(a) for a in argv)


def render_entry(argv: list[str], hidden: bool = False) -> str:
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={APP_NAME}",
        "Comment=Battery levels of your Bluetooth devices, at a glance",
        f"Icon={APP_ID}",
        f"Exec={desktop_exec(argv + ['--background'])}",
        "Terminal=false",
        "NoDisplay=true",
        f"X-GNOME-Autostart-enabled={'false' if hidden else 'true'}",
        "X-GNOME-Autostart-Delay=3",
        "X-KDE-autostart-after=panel",
    ]
    if hidden:
        lines.append("Hidden=true")
    return "\n".join(lines) + "\n"


def set_enabled(enabled: bool) -> None:
    user = user_entry()
    system = system_entry()
    if enabled:
        if system is not None and entry_enabled(system):
            if user.exists():
                user.unlink()  # drop our override, the system entry takes over
            return
        content = render_entry(launch_command())
    else:
        if system is None:
            if user.exists():
                user.unlink()
            return
        content = render_entry(launch_command(), hidden=True)
    user.parent.mkdir(parents=True, exist_ok=True)
    if not user.exists() or user.read_text(encoding="utf-8") != content:
        user.write_text(content, encoding="utf-8")


class Autostart:
    def __init__(self, app):
        self.app = app
        self.config = app.config
        self._handler = 0

    def start(self) -> None:
        if not session_info().flatpak and (self.config["onboarded"] or system_entry() is not None):
            # The files are the source of truth once set up: respect changes made
            # with other tools (GNOME Tweaks, KDE System Settings, …).
            enabled = is_enabled()
            if enabled != self.config["autostart"]:
                self.config["autostart"] = enabled
            elif enabled and user_entry().exists():
                self.sync()  # refresh Exec= in case BlueGlance moved
        self._handler = self.config.connect("changed", self._on_config_changed)

    def stop(self) -> None:
        if self._handler:
            self.config.disconnect(self._handler)
            self._handler = 0

    def _on_config_changed(self, _config, key: str) -> None:
        if key == "autostart":
            self.sync()

    def sync(self) -> None:
        enabled = bool(self.config["autostart"])
        if session_info().flatpak:
            self._request_background(enabled)
            return
        try:
            set_enabled(enabled)
        except OSError as exc:
            log.warning("Could not update the autostart entry: %s", exc)

    def _request_background(self, enabled: bool) -> None:
        token = f"blueglance{GLib.random_int_range(0, 1_000_000)}"
        options = {
            "handle_token": GLib.Variant("s", token),
            "reason": GLib.Variant("s", "Show battery levels on your desktop and warn when they run low"),
            "autostart": GLib.Variant("b", enabled),
            "commandline": GLib.Variant("as", ["blueglance", "--background"]),
            "dbus-activatable": GLib.Variant("b", False),
        }
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call(PORTAL_BUS, PORTAL_PATH, BACKGROUND_IFACE, "RequestBackground",
                     GLib.Variant("(sa{sv})", ("", options)), GLib.VariantType("(o)"),
                     Gio.DBusCallFlags.NONE, -1, None, self._on_portal_reply)
        except GLib.Error as error:
            log.warning("Background portal unavailable: %s", error.message)

    @staticmethod
    def _on_portal_reply(bus, result) -> None:
        try:
            bus.call_finish(result)
        except GLib.Error as error:
            log.warning("Background portal request failed: %s", error.message)
