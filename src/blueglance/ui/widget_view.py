"""The desktop widget's content: rings or rows of batteries, macOS style.

Used by the floating/pinned widget window and by the preview in Preferences.
"""

from __future__ import annotations

from gi.repository import Gtk, Pango

from .. import icons
from ..models import Device, LevelClass, classify_level
from .ring import BatteryRing

SLOTS = {"small": 4, "medium": 4, "large": 6}
# Minimum sizes; the large widget grows with the number of devices.
CARD_SIZES = {"small": (170, 170), "medium": (360, 170), "large": (360, 170)}
SHORT_COMPONENT_LABELS = {"left": "L", "right": "R", "case": "Case"}


def device_level_text(device: Device) -> str:
    if device.level is None:
        return "—"
    return f"{device.level}%"


def component_summary_text(device: Device) -> str:
    """``L 92% · R 88% · Case 61% (charging)``."""
    parts = []
    for comp in device.components:
        label = SHORT_COMPONENT_LABELS.get(comp.key, comp.label)
        level = "—" if comp.level is None else f"{comp.level}%"
        parts.append(f"{label} {level}{' (charging)' if comp.charging else ''}")
    return " · ".join(parts)


def device_status_text(device: Device, low_threshold: int) -> str:
    """Short status line used under a device name in list layouts."""
    if device.components:
        return component_summary_text(device)
    if device.state.value == "full":
        return "Fully charged"
    if device.charging:
        return "Charging"
    cls = classify_level(device.level, low_threshold)
    if cls == LevelClass.CRITICAL:
        return "Critically low"
    if cls == LevelClass.LOW:
        return "Low battery"
    return device.kind.label


class ComponentSummary(Gtk.Box):
    """Inline ``[L] 92%  [R] 88%  [case] 61% ⚡`` with icons, for list rows."""

    def __init__(self):
        super().__init__(spacing=10, css_classes=["component-summary"])
        self._keys: list[str] = []
        self._parts: dict[str, tuple] = {}

    def update(self, device: Device) -> None:
        keys = [c.key for c in device.components]
        if keys != self._keys:
            child = self.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                self.remove(child)
                child = nxt
            self._parts = {}
            for key in keys:
                box = Gtk.Box(spacing=3)
                icon = Gtk.Image(icon_name=icons.for_component(key), pixel_size=12)
                label = Gtk.Label(css_classes=["numeric"])
                bolt = Gtk.Image(icon_name=icons.BOLT, pixel_size=10, css_classes=["component-bolt"])
                box.append(icon)
                box.append(label)
                box.append(bolt)
                self.append(box)
                self._parts[key] = (label, bolt)
            self._keys = keys
        for comp in device.components:
            label, bolt = self._parts[comp.key]
            label.set_label("—" if comp.level is None else f"{comp.level}%")
            bolt.set_visible(comp.charging)


class WidgetView(Gtk.Box):
    """Renders ``devices`` in one of three sizes. Keeps ring widgets alive between
    updates so level changes animate smoothly."""

    __gtype_name__ = "BlueGlanceWidgetView"

    def __init__(self, size: str = "medium"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("bg-widget")
        self._size = None
        self._signature = None
        self._slots: dict[str, tuple] = {}
        self._devices: list[Device] = []
        self._low_threshold = 20
        self._message = None
        self.set_size(size)

    # -- public -------------------------------------------------------------
    @property
    def size(self) -> str:
        return self._size

    def set_size(self, size: str) -> None:
        if size not in SLOTS:
            size = "medium"
        if size == self._size:
            return
        if self._size:
            self.remove_css_class(self._size)
        self._size = size
        self.add_css_class(size)
        width, height = CARD_SIZES[size]
        self.set_size_request(width, height)
        self._signature = None
        self._render(animate=False)

    def set_theme(self, dark: bool) -> None:
        self.remove_css_class("dark" if not dark else "light")
        self.add_css_class("dark" if dark else "light")

    def set_devices(self, devices: list[Device], low_threshold: int = 20, message: str | None = None) -> None:
        self._devices = list(devices)
        self._low_threshold = low_threshold
        self._message = message
        self._render(animate=True)

    # -- rendering ----------------------------------------------------------
    def _render(self, animate: bool) -> None:
        shown = self._devices[: SLOTS[self._size]]
        signature = (self._size, tuple(d.id for d in shown), self._message if not shown else None)
        if signature != self._signature:
            self._rebuild(shown)
            self._signature = signature
            animate = True
        for device in shown:
            slot = self._slots.get(device.id)
            if slot:
                self._update_slot(slot, device, animate)

    def _clear(self) -> None:
        child = self.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.remove(child)
            child = nxt
        self._slots.clear()

    def _rebuild(self, shown: list[Device]) -> None:
        self._clear()
        builder = {"small": self._build_small, "medium": self._build_medium, "large": self._build_large}
        builder[self._size](shown)

    def _build_small(self, shown: list[Device]) -> None:
        if len(shown) == 1:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, vexpand=True, valign=Gtk.Align.CENTER)
            ring = BatteryRing(size=84, icon_size=34, thickness=0.095)
            pct = Gtk.Label(css_classes=["pct", "pct-large", "numeric"])
            name = Gtk.Label(css_classes=["device-name", "caption"], ellipsize=Pango.EllipsizeMode.END,
                             max_width_chars=16)
            box.append(ring)
            box.append(pct)
            box.append(name)
            self.append(box)
            self._slots[shown[0].id] = ("ring+pct+name", ring, pct, name)
            return
        grid = Gtk.Grid(row_spacing=14, column_spacing=14, halign=Gtk.Align.CENTER,
                        valign=Gtk.Align.CENTER, vexpand=True, row_homogeneous=True, column_homogeneous=True)
        for index in range(4):
            ring = BatteryRing(size=58, icon_size=24, thickness=0.1)
            grid.attach(ring, index % 2, index // 2, 1, 1)
            if index < len(shown):
                self._slots[shown[index].id] = ("ring", ring)
            else:
                ring.update(None, LevelClass.UNKNOWN, icon_name=None, animate=False)
                ring.add_css_class("placeholder")
        self.append(grid)

    def _build_medium(self, shown: list[Device]) -> None:
        row = Gtk.Box(spacing=0, homogeneous=True, vexpand=True, valign=Gtk.Align.CENTER)
        for index in range(4):
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.CENTER)
            ring = BatteryRing(size=62, icon_size=26, thickness=0.1)
            pct = Gtk.Label(css_classes=["pct", "numeric"])
            column.append(ring)
            column.append(pct)
            row.append(column)
            if index < len(shown):
                self._slots[shown[index].id] = ("ring+pct", ring, pct)
            else:
                ring.update(None, LevelClass.UNKNOWN, icon_name=None, animate=False)
                ring.add_css_class("placeholder")
                pct.set_label(" ")
        self.append(row)

    def _build_large(self, shown: list[Device]) -> None:
        header = Gtk.Box(spacing=8, css_classes=["widget-header"])
        header.append(Gtk.Image(icon_name=icons.BLUETOOTH, pixel_size=14, css_classes=["header-icon"]))
        header.append(Gtk.Label(label="Batteries", xalign=0, hexpand=True, css_classes=["header-title"]))
        self.append(header)

        if not shown:
            empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, vexpand=True, valign=Gtk.Align.CENTER)
            empty.append(Gtk.Image(icon_name=icons.BLUETOOTH, pixel_size=40, css_classes=["empty-icon"]))
            empty.append(Gtk.Label(label=self._message or "No devices connected", css_classes=["empty-label"],
                                   wrap=True, justify=Gtk.Justification.CENTER))
            self.append(empty)
            return

        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, vexpand=True, valign=Gtk.Align.START)
        for device in shown:
            row = Gtk.Box(spacing=12, css_classes=["widget-row"])
            ring = BatteryRing(size=40, icon_size=18, thickness=0.1)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True, valign=Gtk.Align.CENTER)
            name = Gtk.Label(xalign=0, css_classes=["device-name"], ellipsize=Pango.EllipsizeMode.END)
            status = Gtk.Label(xalign=0, css_classes=["device-status", "caption"], ellipsize=Pango.EllipsizeMode.END)
            summary = ComponentSummary()
            summary.add_css_class("device-status")
            text.append(name)
            text.append(status)
            text.append(summary)
            pct = Gtk.Label(css_classes=["pct", "numeric"], valign=Gtk.Align.CENTER)
            row.append(ring)
            row.append(text)
            row.append(pct)
            rows.append(row)
            self._slots[device.id] = ("row", ring, pct, name, status, summary)
        self.append(rows)

    def _update_slot(self, slot: tuple, device: Device, animate: bool) -> None:
        kind = slot[0]
        ring: BatteryRing = slot[1]
        cls = classify_level(device.level, self._low_threshold)
        ring.update(device.level, cls, icon_name=icons.for_kind(device.kind), charging=device.charging,
                    animate=animate)
        ring.set_tooltip_text(f"{device.name} — {device_level_text(device)}")
        if kind in ("ring+pct", "ring+pct+name", "row"):
            pct: Gtk.Label = slot[2]
            pct.set_label(device_level_text(device))
            for c in LevelClass:
                pct.remove_css_class(f"level-{c.value}")
            pct.add_css_class(f"level-{cls.value}")
        if kind == "ring+pct+name":
            slot[3].set_label(device.name)
        if kind == "row":
            slot[3].set_label(device.name)
            slot[4].set_label(device_status_text(device, self._low_threshold))
            slot[4].set_visible(not device.components)
            slot[5].set_visible(bool(device.components))
            if device.components:
                slot[5].update(device)
