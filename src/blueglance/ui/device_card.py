"""A device card in the main window."""

from __future__ import annotations

from gi.repository import GObject, Gtk, Pango

from .. import icons
from ..models import Device, LevelClass, classify_level, format_relative_time
from .ring import BatteryRing
from .widget_view import device_level_text

COMPONENT_NAMES = {"left": "Left earbud", "right": "Right earbud", "case": "Charging case"}


def subtitle_for(device: Device, low_threshold: int) -> str:
    if not device.connected:
        return f"Last seen {format_relative_time(device.last_seen)}"
    if not device.has_battery:
        return f"{device.kind.label} · Doesn't report its battery"
    parts = [device.kind.label]
    cls = classify_level(device.level, low_threshold)
    if device.state.value == "full":
        parts.append("Fully charged")
    elif device.charging:
        parts.append("Charging")
    elif cls == LevelClass.CRITICAL:
        parts.append("Critically low — charge now")
    elif cls == LevelClass.LOW:
        parts.append("Low battery")
    if device.coarse:
        parts.append("Approximate")
    return " · ".join(parts)


class ComponentChip(Gtk.Box):
    def __init__(self):
        super().__init__(spacing=6, css_classes=["component-chip"])
        self.ring = BatteryRing(size=26, icon_size=13, thickness=0.12, show_badge=False)
        self.label = Gtk.Label(css_classes=["numeric", "component-label"])
        self.bolt = Gtk.Image(icon_name=icons.BOLT, pixel_size=11, css_classes=["component-bolt"])
        self.append(self.ring)
        self.append(self.label)
        self.append(self.bolt)

    def update(self, comp, low_threshold: int, animate: bool) -> None:
        cls = classify_level(comp.level, low_threshold)
        self.ring.update(comp.level, cls, icon_name=icons.for_component(comp.key), animate=animate)
        self.label.set_label("—" if comp.level is None else f"{comp.level}%")
        self.bolt.set_visible(comp.charging)
        self.set_tooltip_text(COMPONENT_NAMES.get(comp.key, comp.label))


class DeviceCard(Gtk.Button):
    __gtype_name__ = "BlueGlanceDeviceCard"
    __gsignals__ = {"show-details": (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    def __init__(self, device: Device, low_threshold: int):
        super().__init__(css_classes=["card", "device-card"])
        self.device_id = device.id
        self.connect("clicked", lambda *_: self.emit("show-details", self.device_id))

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        top = Gtk.Box(spacing=14)
        self.ring = BatteryRing(size=52, icon_size=22, thickness=0.09)
        top.append(self.ring)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER)
        self.name = Gtk.Label(xalign=0, css_classes=["device-name"], ellipsize=Pango.EllipsizeMode.END)
        self.subtitle = Gtk.Label(xalign=0, css_classes=["device-subtitle", "dim-label"],
                                  ellipsize=Pango.EllipsizeMode.END)
        text.append(self.name)
        text.append(self.subtitle)
        top.append(text)

        self.pct = Gtk.Label(css_classes=["device-pct", "numeric"], valign=Gtk.Align.CENTER)
        top.append(self.pct)
        outer.append(top)

        self.components = Gtk.Box(spacing=18, margin_start=66, css_classes=["component-row"])
        self._chips: dict[str, ComponentChip] = {}
        outer.append(self.components)
        self.set_child(outer)
        self.update(device, low_threshold, animate=False)

    def update(self, device: Device, low_threshold: int, animate: bool = True) -> None:
        cls = classify_level(device.level, low_threshold)
        self.ring.update(device.level, cls, icon_name=icons.for_kind(device.kind),
                         charging=device.connected and device.charging, animate=animate)
        self.name.set_label(device.name)
        self.subtitle.set_label(subtitle_for(device, low_threshold))
        self.pct.set_label(device_level_text(device) if device.has_battery else "")
        for c in LevelClass:
            self.pct.remove_css_class(f"level-{c.value}")
        self.pct.add_css_class(f"level-{cls.value}")
        if device.connected:
            self.remove_css_class("disconnected")
        else:
            self.add_css_class("disconnected")

        keys = [c.key for c in device.components]
        if list(self._chips) != keys:
            child = self.components.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                self.components.remove(child)
                child = nxt
            self._chips = {}
            for key in keys:
                chip = ComponentChip()
                self._chips[key] = chip
                self.components.append(chip)
        for comp in device.components:
            self._chips[comp.key].update(comp, low_threshold, animate)
        self.components.set_visible(bool(keys))
        tooltip = f"{device.name}: {device_level_text(device)}" if device.has_battery else device.name
        self.set_tooltip_text(tooltip)
