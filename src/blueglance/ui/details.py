"""Per-device details dialog."""

from __future__ import annotations

import time

from gi.repository import Adw, Gtk, Pango

from .. import icons
from ..models import Device, classify_level, format_relative_time
from .device_card import COMPONENT_NAMES, subtitle_for
from .ring import BatteryRing
from .widget_view import device_level_text

SOURCE_NAMES = {
    "upower": "UPower",
    "bluez": "BlueZ",
    "aap": "Apple accessory protocol",
    "demo": "Demo data",
}


class DeviceDetailsDialog(Adw.Dialog):
    __gtype_name__ = "BlueGlanceDeviceDetailsDialog"

    def __init__(self, app, device: Device):
        super().__init__(title=device.name, content_width=400)
        self.app = app
        self.device_id = device.id
        self.add_css_class("device-details")

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                       margin_start=18, margin_end=18, margin_bottom=24)

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, halign=Gtk.Align.CENTER)
        self.ring = BatteryRing(size=116, icon_size=46, thickness=0.085)
        self.pct = Gtk.Label(css_classes=["details-pct", "numeric"])
        self.name = Gtk.Label(css_classes=["title-2"], wrap=True, justify=Gtk.Justification.CENTER)
        self.subtitle = Gtk.Label(css_classes=["dim-label"], wrap=True, justify=Gtk.Justification.CENTER)
        for widget in (self.ring, self.pct, self.name, self.subtitle):
            hero.append(widget)
        page.append(hero)

        self.components = Gtk.Box(spacing=28, halign=Gtk.Align.CENTER, css_classes=["details-components"])
        page.append(self.components)

        info = Adw.PreferencesGroup()
        self.model_row = self._info_row(info, "Model")
        self.address_row = self._info_row(info, "Address")
        self.source_row = self._info_row(info, "Reported by")
        self.seen_row = self._info_row(info, "Last update")
        page.append(info)

        options = Adw.PreferencesGroup()
        self.visible_row = Adw.SwitchRow(title="Show on Widget and Tray",
                                         subtitle="Hidden devices still appear in this window")
        self.visible_row.set_active(not app.config.is_hidden(device.id))
        self.visible_row.connect("notify::active", self._on_visible_toggled)
        options.add(self.visible_row)
        page.append(options)

        self.forget_button = Gtk.Button(label="Forget Device", halign=Gtk.Align.CENTER,
                                        css_classes=["pill", "destructive-action"])
        self.forget_button.connect("clicked", self._on_forget)
        page.append(self.forget_button)

        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                      child=page)
        toolbar.set_content(scrolled)
        self.set_child(toolbar)

        self._handler = app.manager.connect("changed", self._on_changed)
        self.connect("closed", lambda *_: app.manager.disconnect(self._handler))
        self._render(device, animate=False)

    @staticmethod
    def _info_row(group: Adw.PreferencesGroup, title: str) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, css_classes=["property"])
        row.set_subtitle_selectable(True)
        group.add(row)
        return row

    def _on_changed(self, _manager) -> None:
        device = self.app.manager.find(self.device_id)
        if device is None:
            self.close()
            return
        self._render(device, animate=True)

    def _render(self, device: Device, animate: bool) -> None:
        threshold = self.app.config["low_threshold"]
        cls = classify_level(device.level, threshold)
        self.ring.update(device.level, cls, icon_name=icons.for_kind(device.kind),
                         charging=device.connected and device.charging, animate=animate)
        self.pct.set_label(device_level_text(device) if device.has_battery else "No battery data")
        for c in ("unknown", "critical", "low", "normal"):
            self.pct.remove_css_class(f"level-{c}")
        self.pct.add_css_class(f"level-{cls.value}")
        self.name.set_label(device.name)
        self.subtitle.set_label(subtitle_for(device, threshold))
        self.set_title(device.name)

        child = self.components.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.components.remove(child)
            child = nxt
        for comp in device.components:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            ring = BatteryRing(size=64, icon_size=26, thickness=0.1)
            ring.update(comp.level, classify_level(comp.level, threshold),
                        icon_name=icons.for_component(comp.key), charging=comp.charging, animate=False)
            box.append(ring)
            box.append(Gtk.Label(label="—" if comp.level is None else f"{comp.level}%",
                                 css_classes=["heading", "numeric"]))
            box.append(Gtk.Label(label=COMPONENT_NAMES.get(comp.key, comp.label),
                                 css_classes=["caption", "dim-label"], ellipsize=Pango.EllipsizeMode.END))
            self.components.append(box)
        self.components.set_visible(bool(device.components))

        self.model_row.set_subtitle(device.model or device.kind.label)
        self.address_row.set_subtitle(device.address or "—")
        self.address_row.set_visible(bool(device.address))
        self.source_row.set_subtitle(", ".join(SOURCE_NAMES.get(s, s) for s in device.sources) or "—")
        if device.connected:
            self.seen_row.set_subtitle(time.strftime("%H:%M", time.localtime(device.last_seen)))
        else:
            self.seen_row.set_subtitle(format_relative_time(device.last_seen))
        self.forget_button.set_visible(not device.connected)

    def _on_visible_toggled(self, row, _pspec) -> None:
        self.app.config.set_hidden(self.device_id, not row.get_active())

    def _on_forget(self, _button) -> None:
        self.app.manager.forget(self.device_id)
        self.close()
