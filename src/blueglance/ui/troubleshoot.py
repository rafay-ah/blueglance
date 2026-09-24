"""Help for headphones that don't report their battery.

Headsets report battery over the hands-free profile; PipeWire forwards it to
BlueZ through ``org.bluez.BatteryProviderManager1``. BlueZ 5.56–5.70 only
exposes that API with ``Experimental = true`` in /etc/bluetooth/main.conf
(5.71+ always does). This dialog offers to flip that switch via pkexec.
"""

from __future__ import annotations

import logging
import os
import shutil

from gi.repository import Adw, Gio, GLib, Gtk

from ..session import info as session_info

log = logging.getLogger(__name__)

HELPER_NAME = "blueglance-enable-bluez-battery"
MANUAL_COMMAND = (
    "sudo sed -i -E 's/^[#[:space:]]*Experimental[[:space:]]*=.*/Experimental = true/' "
    "/etc/bluetooth/main.conf && sudo systemctl restart bluetooth"
)


def find_helper() -> str | None:
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "..", "..", "..", "data", HELPER_NAME),  # git checkout
        "/usr/libexec/blueglance/" + HELPER_NAME,
        "/usr/lib/blueglance/" + HELPER_NAME,
        os.path.expanduser("~/.local/libexec/blueglance/" + HELPER_NAME),
    ]
    for path in candidates:
        path = os.path.abspath(path)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


class HeadsetBatteryDialog(Adw.Dialog):
    __gtype_name__ = "BlueGlanceHeadsetBatteryDialog"

    def __init__(self, app):
        super().__init__(title="Headphone Battery", content_width=440)
        self.app = app
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                      margin_start=24, margin_end=24, margin_bottom=24)
        status = Adw.StatusPage(icon_name="blueglance-headphones-symbolic",
                                title="Turn On Headphone Battery Reporting")
        status.add_css_class("compact")
        status.set_description(
            "Most headphones send their battery level over the hands-free Bluetooth profile. "
            "PipeWire reads it, but the installed BlueZ version only accepts it when its "
            "experimental features are switched on."
        )
        box.append(status)

        self.enable_button = Gtk.Button(label="Turn On", halign=Gtk.Align.CENTER,
                                        css_classes=["pill", "suggested-action"])
        self.enable_button.connect("clicked", self._on_enable)
        box.append(self.enable_button)

        manual = Adw.PreferencesGroup(title="Or do it yourself",
                                      description="Run this in a terminal, then reconnect your headphones:")
        command = Gtk.Label(label=MANUAL_COMMAND, selectable=True, wrap=True, xalign=0,
                            css_classes=["monospace", "card"], margin_top=6)
        command.set_margin_start(2)
        manual.add(command)
        copy_row = Gtk.Button(label="Copy Command", halign=Gtk.Align.START, css_classes=["flat"])
        copy_row.connect("clicked", self._on_copy)
        manual.add(copy_row)
        box.append(manual)

        tip = Gtk.Label(
            label="Tip: the battery is only sent while the headset (HFP) profile is connected, "
                  "not in music-only (A2DP) mode on some devices.",
            wrap=True, xalign=0, css_classes=["dim-label", "caption"])
        box.append(tip)

        self.toasts = Adw.ToastOverlay(child=box)
        toolbar.set_content(self.toasts)
        self.set_child(toolbar)

        helper = find_helper()
        if session_info().flatpak or not helper or not shutil.which("pkexec"):
            self.enable_button.set_visible(False)
        self._helper = helper

    def _on_copy(self, _button) -> None:
        self.get_clipboard().set(MANUAL_COMMAND)
        self.toasts.add_toast(Adw.Toast(title="Copied"))

    def _on_enable(self, button) -> None:
        button.set_sensitive(False)
        try:
            proc = Gio.Subprocess.new(["pkexec", self._helper], Gio.SubprocessFlags.NONE)
        except GLib.Error as error:
            self.toasts.add_toast(Adw.Toast(title=f"Couldn't start: {error.message}"))
            button.set_sensitive(True)
            return

        def done(proc, result):
            button.set_sensitive(True)
            try:
                proc.wait_check_finish(result)
            except GLib.Error as error:
                log.warning("Enabling BlueZ experimental features failed: %s", error.message)
                self.toasts.add_toast(Adw.Toast(title="Nothing was changed"))
                return
            self.toasts.add_toast(Adw.Toast(title="Done! Reconnect your headphones", timeout=6))

        proc.wait_check_async(None, done)
