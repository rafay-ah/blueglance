"""The main BlueGlance window."""

from __future__ import annotations

import os

from gi.repository import Adw, Gio, GLib, Gtk

from .. import APP_NAME, icons
from ..models import Device
from .device_card import DeviceCard


class Section(Gtk.Box):
    def __init__(self, title: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.title = Gtk.Label(label=title, xalign=0, css_classes=["section-title"])
        self.append(self.title)
        self.cards = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.append(self.cards)


class MainWindow(Adw.ApplicationWindow):
    __gtype_name__ = "BlueGlanceMainWindow"

    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME)
        self.app = app
        self.set_default_size(500, 680)
        self.set_size_request(360, 360)
        self.add_css_class("blueglance-main")
        self._cards: dict[str, DeviceCard] = {}

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.title_widget = Adw.WindowTitle(title=APP_NAME)
        header.set_title_widget(self.title_widget)

        self.pin_button = Gtk.ToggleButton(icon_name=icons.PIN, tooltip_text="Show widget on desktop")
        self.pin_button.set_action_name("app.widget-enabled")
        header.pack_start(self.pin_button)

        menu = Gio.Menu()
        section = Gio.Menu()
        section.append("_Preferences", "app.preferences")
        section.append("_Keyboard Shortcuts", "win.show-help-overlay")
        section.append(f"_About {APP_NAME}", "app.about")
        menu.append_section(None, section)
        quit_section = Gio.Menu()
        quit_section.append("_Quit", "app.quit")
        menu.append_section(None, quit_section)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Main Menu", primary=True))
        toolbar.add_top_bar(header)

        self.banner = Adw.Banner()
        self.banner.connect("button-clicked", self._on_banner_clicked)
        toolbar.add_top_bar(self.banner)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)

        # Device list
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22,
                          margin_top=12, margin_bottom=24, margin_start=16, margin_end=16)
        self.connected_section = Section("Connected")
        self.nobattery_section = Section("No battery information")
        self.recent_section = Section("Recently connected")
        for sec in (self.connected_section, self.nobattery_section, self.recent_section):
            content.append(sec)
        clamp = Adw.Clamp(maximum_size=560, tightening_threshold=420, child=content)
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp, vexpand=True)
        self.stack.add_named(scrolled, "list")

        # Empty / off / no adapter
        self.status_page = Adw.StatusPage(icon_name=icons.BLUETOOTH)
        settings_button = Gtk.Button(label="Open Bluetooth Settings", halign=Gtk.Align.CENTER,
                                     css_classes=["pill", "suggested-action"])
        settings_button.set_action_name("app.bluetooth-settings")
        self.status_page.set_child(settings_button)
        self.stack.add_named(self.status_page, "status")

        self.toasts = Adw.ToastOverlay(child=self.stack)
        toolbar.set_content(self.toasts)
        self.set_content(toolbar)

        self._install_shortcuts_overlay()
        self._manager_handler = app.manager.connect("changed", lambda *_: self.refresh())
        self._config_handler = app.config.connect("changed", self._on_config_changed)
        self._shell = app.services.get("shell")
        self._shell_handler = self._shell.connect("changed", lambda *_: self.refresh(animate=False)) \
            if self._shell is not None else 0
        self._banner_action = None
        self.connect("close-request", self._on_close_request)
        # Don't start with a card focused: the scrolled view would jump to it
        # and hide the section heading above.
        self.connect("map", lambda *_: GLib.idle_add(lambda: self.set_focus(None)))
        self.refresh(animate=False)

    # -- helpers ------------------------------------------------------------
    def toast(self, message: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=message, timeout=4))

    def _install_shortcuts_overlay(self) -> None:
        xml = """
        <interface>
          <object class="GtkShortcutsWindow" id="help_overlay">
            <property name="modal">true</property>
            <child><object class="GtkShortcutsSection"><property name="section-name">shortcuts</property>
              <child><object class="GtkShortcutsGroup"><property name="title">General</property>
                <child><object class="GtkShortcutsShortcut">
                  <property name="title">Preferences</property>
                  <property name="accelerator">&lt;Primary&gt;comma</property>
                </object></child>
                <child><object class="GtkShortcutsShortcut">
                  <property name="title">Toggle desktop widget</property>
                  <property name="accelerator">&lt;Primary&gt;d</property>
                </object></child>
                <child><object class="GtkShortcutsShortcut">
                  <property name="title">Close window</property><property name="accelerator">&lt;Primary&gt;w</property>
                </object></child>
                <child><object class="GtkShortcutsShortcut">
                  <property name="title">Quit</property><property name="accelerator">&lt;Primary&gt;q</property>
                </object></child>
              </object></child>
            </object></child>
          </object>
        </interface>"""
        builder = Gtk.Builder.new_from_string(xml, -1)
        self.set_help_overlay(builder.get_object("help_overlay"))

    def _on_close_request(self, _window) -> bool:
        # The window is destroyed on close; the application itself keeps running in
        # the background (widget, tray, notifications) when that's enabled.
        self.app.manager.disconnect(self._manager_handler)
        self.app.config.disconnect(self._config_handler)
        if self._shell_handler:
            self._shell.disconnect(self._shell_handler)
        return False

    def _on_config_changed(self, _config, key: str) -> None:
        if key in ("low_threshold", "show_disconnected", "show_no_battery", "hidden_devices", "widget_enabled"):
            self.refresh(animate=False)

    # -- rendering ----------------------------------------------------------
    def refresh(self, animate: bool = True) -> None:
        config = self.app.config
        threshold = config["low_threshold"]
        devices = self.app.manager.devices

        connected = [d for d in devices if d.connected and d.has_battery]
        nobattery = [d for d in devices if d.connected and not d.has_battery] if config["show_no_battery"] else []
        recent = [d for d in devices if not d.connected] if config["show_disconnected"] else []

        self._fill(self.connected_section, connected, threshold, animate)
        self._fill(self.nobattery_section, nobattery, threshold, animate)
        self._fill(self.recent_section, recent, threshold, animate)

        count = len(connected) + len(nobattery)
        subtitle = f"{count} device{'s' if count != 1 else ''} connected" if count else ""
        if self.app.demo and os.environ.get("BLUEGLANCE_SCREENSHOT") != "1":
            subtitle = "Demo mode · sample devices"
        self.title_widget.set_subtitle(subtitle)

        if connected or nobattery or recent:
            self.stack.set_visible_child_name("list")
        else:
            self._show_status()
        self._update_banner(nobattery)

    def _fill(self, section: Section, devices: list[Device], threshold: int, animate: bool) -> None:
        section.set_visible(bool(devices))
        wanted = [d.id for d in devices]
        current = []
        child = section.cards.get_first_child()
        while child is not None:
            current.append(child.device_id)
            child = child.get_next_sibling()

        if current != wanted:
            child = section.cards.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                section.cards.remove(child)
                child = nxt
            for device in devices:
                card = self._cards.get(device.id)
                if card is None or card.get_parent() is not None:
                    card = DeviceCard(device, threshold)
                    card.connect("show-details", self._on_show_details)
                    self._cards[device.id] = card
                section.cards.append(card)
        for device in devices:
            self._cards[device.id].update(device, threshold, animate)

        live = {d.id for d in self.app.manager.devices}
        for stale in [k for k in self._cards if k not in live]:
            del self._cards[stale]

    def _show_status(self) -> None:
        bluez = self.app.manager.get_provider("bluez")
        page = self.status_page
        if bluez is not None and bluez.available and not bluez.adapters:
            page.set_icon_name(icons.BLUETOOTH_OFF)
            page.set_title("No Bluetooth Adapter")
            page.set_description("BlueGlance couldn't find a Bluetooth adapter on this computer.")
        elif bluez is not None and bluez.adapters and not bluez.powered:
            page.set_icon_name(icons.BLUETOOTH_OFF)
            page.set_title("Bluetooth Is Off")
            page.set_description("Turn Bluetooth on to see the battery level of your devices.")
        else:
            page.set_icon_name(icons.BLUETOOTH)
            page.set_title("No Devices Connected")
            page.set_description(
                "Connect a Bluetooth mouse, keyboard, headphones or game controller and its "
                "battery level shows up here — and on your desktop widget."
            )
        self.stack.set_visible_child_name("status")

    def _update_banner(self, nobattery: list[Device]) -> None:
        bluez = self.app.manager.get_provider("bluez")
        needs_fix = (
            bluez is not None
            and bluez.battery_provider_supported is False
            and any(d.hints.get("handsfree") for d in nobattery)
        )
        title, button, action = None, None, None
        # Keep banner titles short: long ones wrap and overlap the list below.
        if needs_fix:
            title = "Headphone battery reporting is off"
            button, action = "Fix…", "headset"
        elif self._shell is not None and self._shell.relevant and self.app.config["widget_enabled"]:
            status, _text, shell_button = self._shell.describe()
            if status != "active":
                title = {
                    "pending": "Log out and in to pin the widget",
                    "disabled": "Pin the widget to your desktop",
                    "missing": "Pin the widget to your desktop",
                    "blocked": "GNOME extensions are switched off",
                }.get(status, "The widget extension couldn't load")
                button, action = shell_button, "shell" if shell_button else None
        self._banner_action = action
        if title:
            self.banner.set_title(title)
            self.banner.set_button_label(button)
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)

    def _on_banner_clicked(self, _banner) -> None:
        if self._banner_action == "headset":
            self.app.activate_action("headset-battery-help", None)
        elif self._banner_action == "shell" and self._shell is not None:
            self._shell.perform_action(self)
            status, _text, _button = self._shell.describe()
            if status == "pending":
                self.toast("Installed — log out and back in to finish")

    def _on_show_details(self, _card, device_id: str) -> None:
        from .details import DeviceDetailsDialog

        device = self.app.manager.find(device_id)
        if device is not None:
            DeviceDetailsDialog(self.app, device).present(self)
