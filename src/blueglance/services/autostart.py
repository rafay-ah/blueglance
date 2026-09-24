"""Launch at login: an XDG autostart entry, or the Background portal in Flatpak."""

from __future__ import annotations

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


def autostart_file() -> Path:
    return Path(GLib.get_user_config_dir()) / "autostart" / f"{APP_ID}.desktop"


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


def render_entry(argv: list[str]) -> str:
    return "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            f"Name={APP_NAME}",
            "Comment=Battery levels of your Bluetooth devices, at a glance",
            f"Icon={APP_ID}",
            f"Exec={desktop_exec(argv + ['--background'])}",
            "Terminal=false",
            "X-GNOME-Autostart-enabled=true",
            "X-GNOME-Autostart-Delay=3",
            "X-KDE-autostart-after=panel",
            "",
        ]
    )


class Autostart:
    def __init__(self, app):
        self.app = app
        self.config = app.config
        self._handler = 0

    def start(self) -> None:
        self._handler = self.config.connect("changed", self._on_config_changed)
        if not self.config["onboarded"] or session_info().flatpak:
            return
        # The autostart file is the source of truth once set up: respect it if the
        # user removed/added it with another tool (e.g. GNOME Tweaks).
        exists = autostart_file().exists()
        if exists != self.config["autostart"]:
            self.config["autostart"] = exists
        elif exists:
            self.sync()  # refresh Exec= in case BlueGlance moved

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
        path = autostart_file()
        try:
            if enabled:
                path.parent.mkdir(parents=True, exist_ok=True)
                content = render_entry(launch_command())
                if not path.exists() or path.read_text(encoding="utf-8") != content:
                    path.write_text(content, encoding="utf-8")
            elif path.exists():
                path.unlink()
        except OSError as exc:
            log.warning("Could not update autostart entry %s: %s", path, exc)

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

