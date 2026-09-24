"""Circular battery gauge, drawn with cairo and animated with libadwaita."""

from __future__ import annotations

import math

import cairo
from gi.repository import Adw, GObject, Graphene, Gtk

from .. import icons
from ..models import LevelClass

LEVEL_CLASSES = [f"level-{c.value}" for c in LevelClass]
TRACK_ALPHA = 0.16


def _css_color(widget: Gtk.Widget):
    if hasattr(widget, "get_color"):  # GTK >= 4.10
        return widget.get_color()
    return widget.get_style_context().get_color()  # pragma: no cover


class RingArea(Gtk.Widget):
    """Just the ring. Its CSS ``color`` is the fill; the track uses the parent's."""

    __gtype_name__ = "BlueGlanceRingArea"

    def __init__(self, thickness: float = 0.1):
        super().__init__()
        self.thickness = thickness
        self._fraction = 0.0
        self._has_value = False
        self._animation: Adw.TimedAnimation | None = None
        self.add_css_class("ring-area")

    def set_value(self, fraction: float | None, animate: bool = True) -> None:
        if fraction is None:
            self._has_value = False
            self._fraction = 0.0
            self.queue_draw()
            return
        fraction = max(0.0, min(1.0, fraction))
        start = self._fraction if self._has_value else 0.0
        self._has_value = True
        if self._animation is not None:
            self._animation.pause()
            self._animation = None
        if not animate or abs(fraction - start) < 0.001 or not self.get_mapped():
            self._fraction = fraction
            self.queue_draw()
            return
        target = Adw.CallbackAnimationTarget.new(self._on_animate)
        self._animation = Adw.TimedAnimation.new(self, start, fraction, 700, target)
        self._animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._animation.play()

    def _on_animate(self, value: float) -> None:
        self._fraction = value
        self.queue_draw()

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        size = min(width, height)
        if size <= 2:
            return
        fill = _css_color(self)
        parent = self.get_parent()
        track = _css_color(parent) if parent is not None else fill

        line = max(2.0, size * self.thickness)
        radius = (size - line) / 2.0
        cx, cy = width / 2.0, height / 2.0

        cr = snapshot.append_cairo(Graphene.Rect().init(0, 0, width, height))
        cr.set_line_width(line)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)

        cr.set_source_rgba(track.red, track.green, track.blue, track.alpha * TRACK_ALPHA)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()

        if self._has_value and self._fraction > 0.004:
            start = -math.pi / 2
            cr.set_source_rgba(fill.red, fill.green, fill.blue, fill.alpha)
            cr.arc(cx, cy, radius, start, start + 2 * math.pi * self._fraction)
            cr.stroke()


class BatteryRing(Gtk.Overlay):
    """A ring with a device icon (or text) in the middle and a charging badge."""

    __gtype_name__ = "BlueGlanceBatteryRing"

    def __init__(self, size: int = 56, icon_size: int | None = None, thickness: float = 0.1,
                 show_badge: bool = True):
        super().__init__()
        self.add_css_class("battery-ring")
        self.set_size_request(size, size)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self._size = size

        self.area = RingArea(thickness)
        self.area.set_size_request(size, size)
        self.set_child(self.area)

        self.icon = Gtk.Image(pixel_size=icon_size or max(12, int(size * 0.42)))
        self.icon.set_halign(Gtk.Align.CENTER)
        self.icon.set_valign(Gtk.Align.CENTER)
        self.icon.add_css_class("ring-icon")
        self.add_overlay(self.icon)

        self.label = Gtk.Label()
        self.label.add_css_class("ring-label")
        self.label.add_css_class("numeric")
        self.label.set_visible(False)
        self.add_overlay(self.label)

        badge_size = max(12, int(size * 0.34))
        self.badge = Gtk.Image(icon_name=icons.BOLT, pixel_size=max(8, int(badge_size * 0.62)))
        self.badge.add_css_class("charging-badge")
        self.badge.set_size_request(badge_size, badge_size)
        self.badge.set_halign(Gtk.Align.END)
        self.badge.set_valign(Gtk.Align.END)
        self.badge.set_visible(False)
        if show_badge:
            self.add_overlay(self.badge)

        self._level_class: str | None = None

    def update(self, level: int | None, level_class: LevelClass, *, icon_name: str | None = None,
               charging: bool = False, text: str | None = None, animate: bool = True) -> None:
        css = f"level-{level_class.value}"
        if css != self._level_class:
            for name in LEVEL_CLASSES:
                self.area.remove_css_class(name)
                self.label.remove_css_class(name)
            self.area.add_css_class(css)
            self.label.add_css_class(css)
            self._level_class = css
        self.area.set_value(None if level is None else level / 100.0, animate)

        if text is not None:
            self.label.set_label(text)
            self.label.set_visible(True)
            self.icon.set_visible(False)
        else:
            self.label.set_visible(False)
            self.icon.set_visible(bool(icon_name))
            if icon_name:
                self.icon.set_from_icon_name(icon_name)
        self.badge.set_visible(charging)


GObject.type_ensure(BatteryRing)
