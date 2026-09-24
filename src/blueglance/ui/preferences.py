"""Preferences dialog."""

from __future__ import annotations

from gi.repository import Adw, GLib, Gtk

from ..config import WIDGET_ANCHORS, WIDGET_SIZES, WIDGET_THEMES
from ..session import info as session_info
from .widget_view import WidgetView

SIZE_LABELS = ["Small", "Medium", "Large"]
THEME_LABELS = ["Follow System", "Light", "Dark"]
ANCHOR_LABELS = ["Top Left", "Top Right", "Bottom Left", "Bottom Right"]


class PreferencesDialog(Adw.PreferencesDialog):
    __gtype_name__ = "BlueGlancePreferencesDialog"

    def __init__(self, app):
        super().__init__(title="Preferences", search_enabled=False)
        self.app = app
        self.config = app.config
        self._bindings: list = []
        self._handlers = []

        page = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")
        self.add(page)

        # ---- Live preview -------------------------------------------------
        preview_group = Adw.PreferencesGroup()
        preview_box = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                              css_classes=["widget-preview"])
        self.preview = WidgetView(self.config["widget_size"])
        self.preview.set_halign(Gtk.Align.CENTER)
        self.preview.set_valign(Gtk.Align.CENTER)
        preview_box.append(self.preview)
        preview_group.add(preview_box)
        page.add(preview_group)

        # ---- Desktop widget ---------------------------------------------
        widget_group = Adw.PreferencesGroup(title="Desktop Widget")
        widget_group.add(self._switch("widget_enabled", "Show Widget on Desktop",
                                      "A glanceable battery widget pinned to your desktop"))
        widget_group.add(self._combo("widget_size", "Size", WIDGET_SIZES, SIZE_LABELS))
        widget_group.add(self._combo("widget_theme", "Appearance", WIDGET_THEMES, THEME_LABELS))

        widget_controller = app.services.get("widget")
        if widget_controller is not None and getattr(widget_controller, "uses_layer_shell", False):
            widget_group.add(self._combo("widget_anchor", "Corner", WIDGET_ANCHORS, ANCHOR_LABELS))

        self.shell_row = Adw.ActionRow(title="GNOME Shell Integration")
        self.shell_button = Gtk.Button(valign=Gtk.Align.CENTER)
        self.shell_button.connect("clicked", self._on_shell_button)
        self.shell_row.add_suffix(self.shell_button)
        widget_group.add(self.shell_row)

        reset = Adw.ButtonRow(title="Reset Widget Position") if hasattr(Adw, "ButtonRow") else None
        if reset is not None:
            reset.connect("activated", lambda *_: self._reset_position())
            widget_group.add(reset)
        else:
            reset_row = Adw.ActionRow(title="Widget Position", subtitle="Drag the widget to move it")
            reset_button = Gtk.Button(label="Reset", valign=Gtk.Align.CENTER)
            reset_button.connect("clicked", lambda *_: self._reset_position())
            reset_row.add_suffix(reset_button)
            widget_group.add(reset_row)
        page.add(widget_group)

        # ---- Notifications -----------------------------------------------
        notify_group = Adw.PreferencesGroup(title="Alerts")
        notify_group.add(self._switch("notify_low", "Low Battery Alerts",
                                      "Get a notification before a device runs out"))
        threshold = Adw.SpinRow.new_with_range(5, 50, 5)
        threshold.set_title("Alert Level")
        threshold.set_subtitle("Warn when a battery drops to this percentage")
        threshold.set_value(self.config["low_threshold"])
        threshold.connect("notify::value", lambda row, _p: self.config.set("low_threshold", int(row.get_value())))
        notify_group.add(threshold)
        notify_group.add(self._switch("notify_full", "Fully Charged Alerts",
                                      "Know when it's time to unplug"))
        page.add(notify_group)

        # ---- Tray ---------------------------------------------------------
        gnome = session_info().gnome
        tray_group = Adw.PreferencesGroup(title="Top Bar" if gnome else "System Tray")
        tray_group.add(self._switch("tray_icon", "Show Icon", "Quick access to your devices' batteries"))
        tray_group.add(self._switch("tray_label", "Show Lowest Percentage",
                                    "Display the weakest battery next to the icon"))
        page.add(tray_group)

        # ---- Devices ------------------------------------------------------
        devices_group = Adw.PreferencesGroup(title="Devices")
        devices_group.add(self._switch("airpods_enhanced", "Detailed AirPods Battery",
                                       "Left, right and case levels for AirPods and Beats"))
        devices_group.add(self._switch("show_disconnected", "Show Recently Connected",
                                       "Keep the last known level of disconnected devices"))
        devices_group.add(self._switch("show_no_battery", "Show Devices Without Battery Info"))
        devices_group.add(self._switch("show_system_battery", "Include This Computer's Battery"))
        self.visibility_row = Adw.ExpanderRow(title="Shown on Widget and Tray")
        devices_group.add(self.visibility_row)
        self._visibility_rows: list = []
        page.add(devices_group)

        # ---- Startup ------------------------------------------------------
        startup_group = Adw.PreferencesGroup(title="Startup")
        startup_group.add(self._switch("autostart", "Launch at Login",
                                       "Start BlueGlance in the background when you log in"))
        startup_group.add(self._switch("run_in_background", "Run in Background",
                                       "Keep the widget, icon and alerts running after closing the window"))
        page.add(startup_group)

        self._handlers.append((app.manager, app.manager.connect("changed", lambda *_: self._refresh_devices())))
        self._handlers.append((self.config, self.config.connect("changed", self._on_config_changed)))
        shell = app.services.get("shell")
        if shell is not None:
            self._handlers.append((shell, shell.connect("changed", lambda *_: self._refresh_shell_row())))
        self.connect("closed", self._on_closed)
        self._refresh_devices()
        self._refresh_shell_row()
        self._apply_preview_theme()

    # -- row factories ----------------------------------------------------
    def _switch(self, key: str, title: str, subtitle: str | None = None) -> Adw.SwitchRow:
        row = Adw.SwitchRow(title=title)
        if subtitle:
            row.set_subtitle(subtitle)
        row.set_active(bool(self.config[key]))
        row.connect("notify::active", lambda r, _p: self.config.set(key, r.get_active()))
        self._bindings.append((key, row))
        return row

    def _combo(self, key: str, title: str, values: tuple, labels: list[str]) -> Adw.ComboRow:
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new(labels))
        current = self.config[key]
        row.set_selected(values.index(current) if current in values else 0)
        row.connect("notify::selected", lambda r, _p: self.config.set(key, values[r.get_selected()]))
        self._bindings.append((key, row))
        return row

    # -- reactions --------------------------------------------------------
    def _on_closed(self, *_args) -> None:
        for obj, handler in self._handlers:
            obj.disconnect(handler)
        self._handlers.clear()

    def _on_config_changed(self, _config, key: str) -> None:
        for bound_key, row in self._bindings:
            if bound_key != key:
                continue
            value = self.config[key]
            if isinstance(row, Adw.SwitchRow) and row.get_active() != bool(value):
                row.set_active(bool(value))
        if key == "widget_size":
            self.preview.set_size(self.config[key])
            self._refresh_devices()
        elif key == "widget_theme":
            self._apply_preview_theme()
        elif key in ("hidden_devices", "low_threshold"):
            self._refresh_devices()

    def _apply_preview_theme(self) -> None:
        theme = self.config["widget_theme"]
        dark = Adw.StyleManager.get_default().get_dark() if theme == "auto" else theme == "dark"
        self.preview.set_theme(dark)

    def _refresh_devices(self) -> None:
        manager = self.app.manager
        visible = [d for d in manager.connected_with_battery() if not self.config.is_hidden(d.id)]
        self.preview.set_devices(visible, self.config["low_threshold"])

        for row in self._visibility_rows:
            self.visibility_row.remove(row)
        self._visibility_rows = []
        known = [d for d in manager.devices if d.has_battery]
        for device in known:
            row = Adw.SwitchRow(title=GLib.markup_escape_text(device.name), subtitle=device.kind.label)
            row.set_active(not self.config.is_hidden(device.id))
            row.connect("notify::active",
                        lambda r, _p, device_id=device.id: self.config.set_hidden(device_id, not r.get_active()))
            self.visibility_row.add_row(row)
            self._visibility_rows.append(row)
        self.visibility_row.set_subtitle(
            f"{sum(1 for d in known if not self.config.is_hidden(d.id))} of {len(known)} devices"
            if known else "No devices yet"
        )
        self.visibility_row.set_enable_expansion(bool(known))

    def _reset_position(self) -> None:
        self.config.reset("widget_position")
        self.config.reset("shell_widget_position")
        self.config.reset("widget_anchor")
        self.config.reset("widget_margin_x")
        self.config.reset("widget_margin_y")
        self.add_toast(Adw.Toast(title="Widget moved back to its default spot"))

    # -- GNOME Shell integration -------------------------------------------
    def _refresh_shell_row(self) -> None:
        shell = self.app.services.get("shell")
        if shell is None or not shell.relevant:
            self.shell_row.set_visible(False)
            return
        self.shell_row.set_visible(True)
        status, subtitle, button = shell.describe()
        self.shell_row.set_subtitle(subtitle)
        self.shell_button.set_visible(button is not None)
        if button:
            self.shell_button.set_label(button)
        self.shell_row.set_title("GNOME Shell Integration" + (" ✓" if status == "active" else ""))

    def _on_shell_button(self, _button) -> None:
        shell = self.app.services.get("shell")
        if shell is not None:
            shell.perform_action(self)

