"""The desktop widget as a GTK window (for every desktop except GNOME, where the
companion Shell extension draws it).

Modes
-----
``layer``    Wayland compositors with wlr-layer-shell (KDE Plasma, Sway, Hyprland,
             COSMIC, niri, labwc, Wayfire…): a real bottom-layer surface.
``x11``      Any X11 window manager: kept below other windows, on all workspaces,
             hidden from the taskbar and pager, position remembered.
``floating`` Fallback (e.g. GNOME without the extension): a plain borderless window.
"""

from __future__ import annotations

import logging
import math

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gtk

from ..session import info as session_info
from ..session import layer_shell_loaded
from .widget_view import WidgetView

log = logging.getLogger(__name__)

SHADOW_MARGIN = 22
DRAG_THRESHOLD = 6
DEFAULT_OFFSET = (48, 64)  # from the top-right corner of the primary monitor

_layer_shell = None


def layer_shell_module():
    """The Gtk4LayerShell GI module, or None if unusable in this session."""
    global _layer_shell
    if _layer_shell is None:
        _layer_shell = False
        if layer_shell_loaded():
            try:
                import gi

                gi.require_version("Gtk4LayerShell", "1.0")
                from gi.repository import Gtk4LayerShell

                if Gtk4LayerShell.is_supported():
                    _layer_shell = Gtk4LayerShell
            except (ImportError, ValueError) as exc:
                log.debug("gtk4-layer-shell unavailable: %s", exc)
    return _layer_shell or None


def widget_menu() -> Gio.Menu:
    menu = Gio.Menu()
    main = Gio.Menu()
    main.append("Open BlueGlance", "app.show-window")
    menu.append_section(None, main)
    sizes = Gio.Menu()
    for label, size in (("Small", "small"), ("Medium", "medium"), ("Large", "large")):
        item = Gio.MenuItem.new(label, None)
        item.set_action_and_target_value("app.widget-size", GLib.Variant.new_string(size))
        sizes.append_item(item)
    menu.append_section("Size", sizes)
    rest = Gio.Menu()
    rest.append("Preferences…", "app.preferences")
    rest.append("Hide Widget", "app.hide-widget")
    menu.append_section(None, rest)
    return menu


class DesktopWidgetWindow(Gtk.Window):
    __gtype_name__ = "BlueGlanceDesktopWidgetWindow"

    def __init__(self, controller, mode: str):
        super().__init__(title="BlueGlance Widget", decorated=False, resizable=False)
        self.controller = controller
        self.app = controller.app
        self.config = controller.app.config
        self.mode = mode
        self._x11 = None
        self._x11_watch = 0
        self._save_source = 0
        self._dragging = False
        self._drag_moved = False
        self._layer_margins = [0, 0]

        self.add_css_class("bg-widget-window")
        self.insert_action_group("app", self.app)

        self.view = WidgetView(self.config["widget_size"])
        for side in ("top", "bottom", "start", "end"):
            getattr(self.view, f"set_margin_{side}")(SHADOW_MARGIN)
        self.set_child(self.view)

        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.view.add_controller(drag)

        secondary = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        secondary.connect("pressed", self._on_secondary_click)
        self.view.add_controller(secondary)

        self.menu = Gtk.PopoverMenu.new_from_model(widget_menu())
        self.menu.set_has_arrow(False)
        self.menu.set_parent(self.view)

        if mode == "layer":
            self._setup_layer_shell()
        self.connect("realize", self._on_realize)
        self.connect("map", self._on_map)
        self.connect("destroy", self._on_destroy)

    # -- content ------------------------------------------------------------
    def refresh(self, devices, threshold: int, message: str | None) -> None:
        self.view.set_size(self.config["widget_size"])
        theme = self.config["widget_theme"]
        dark = Adw.StyleManager.get_default().get_dark() if theme == "auto" else theme == "dark"
        self.view.set_theme(dark)
        self.view.set_devices(devices, threshold, message)

    # -- platform setup -----------------------------------------------------
    def _setup_layer_shell(self) -> None:
        shell = layer_shell_module()
        shell.init_for_window(self)
        shell.set_namespace(self, "blueglance-widget")
        shell.set_layer(self, shell.Layer.BOTTOM)
        shell.set_keyboard_mode(self, shell.KeyboardMode.NONE)
        shell.set_exclusive_zone(self, 0)
        self.apply_layer_position()

    def apply_layer_position(self) -> None:
        shell = layer_shell_module()
        anchor = self.config["widget_anchor"]
        vertical, horizontal = anchor.split("-")
        self._layer_margins = [self.config["widget_margin_x"], self.config["widget_margin_y"]]
        edges = {
            "top": shell.Edge.TOP, "bottom": shell.Edge.BOTTOM,
            "left": shell.Edge.LEFT, "right": shell.Edge.RIGHT,
        }
        for name, edge in edges.items():
            shell.set_anchor(self, edge, name in (vertical, horizontal))
            shell.set_margin(self, edge, 0)
        shell.set_margin(self, edges[horizontal], max(0, self._layer_margins[0] - SHADOW_MARGIN))
        shell.set_margin(self, edges[vertical], max(0, self._layer_margins[1] - SHADOW_MARGIN))

    def _on_realize(self, _window) -> None:
        if self.mode != "x11":
            return
        try:
            import gi

            gi.require_version("GdkX11", "4.0")
            from gi.repository import GdkX11

            from ..session.x11 import X11Window

            surface = self.get_surface()
            self._x11 = X11Window(GdkX11.X11Surface.get_xid(surface))
            self._x11.set_initial_state()
            GdkX11.X11Surface.set_skip_taskbar_hint(surface, True)
            GdkX11.X11Surface.set_skip_pager_hint(surface, True)
            GdkX11.X11Surface.set_user_time(surface, 0)  # don't steal focus when shown
        except Exception:
            log.exception("Could not pin the widget on X11; using a floating window")
            self._x11 = None

    def _on_map(self, _window) -> None:
        if self._x11 is None:
            return
        self._x11.pin()
        self._restore_x11_position()
        self._x11.watch_configure()
        self._x11_watch = GLib.io_add_watch(self._x11.connection_fd(), GLib.PRIORITY_DEFAULT,
                                            GLib.IOCondition.IN, self._on_x11_events)
        # Some window managers only honour the state once the window is managed.
        GLib.timeout_add(400, lambda: (self._x11 and self._x11.pin(), GLib.SOURCE_REMOVE)[1])

    def _restore_x11_position(self) -> None:
        scale = self.get_surface().get_scale_factor() if self.get_surface() else 1
        position = self.config["widget_position"]
        monitors = [m.get_geometry() for m in Gdk.Display.get_default().get_monitors()]
        if isinstance(position, dict) and "x" in position and "y" in position:
            x, y = int(position["x"]), int(position["y"])
            if any(g.x * scale <= x + 40 < (g.x + g.width) * scale and
                   g.y * scale <= y + 40 < (g.y + g.height) * scale for g in monitors):
                self._x11.move(x, y)
                return
        if monitors:
            geom = monitors[0]
            width = self.get_width() or self.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
            x = (geom.x + geom.width - width - DEFAULT_OFFSET[0] + SHADOW_MARGIN) * scale
            y = (geom.y + DEFAULT_OFFSET[1] - SHADOW_MARGIN) * scale
            self._x11.move(int(x), int(y))

    def _on_x11_events(self, _fd, _condition) -> bool:
        if self._x11 is None:
            self._x11_watch = 0
            return GLib.SOURCE_REMOVE
        if self._x11.drain_configure_events() and not self._save_source:
            self._save_source = GLib.timeout_add(500, self._save_x11_position)
        return GLib.SOURCE_CONTINUE

    def _save_x11_position(self) -> bool:
        self._save_source = 0
        if self._x11 is not None:
            position = self._x11.position()
            if position is not None:
                self.config["widget_position"] = {"x": position[0], "y": position[1]}
        return GLib.SOURCE_REMOVE

    def _on_destroy(self, _window) -> None:
        if self._x11_watch:
            GLib.source_remove(self._x11_watch)
            self._x11_watch = 0
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = 0
        if self._x11 is not None:
            self._x11.close()
            self._x11 = None
        self.menu.unparent()

    # -- input region -------------------------------------------------------
    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        Gtk.Window.do_size_allocate(self, width, height, baseline)
        surface = self.get_surface()
        if surface is None:
            return
        try:
            import cairo

            rect = cairo.RectangleInt(SHADOW_MARGIN, SHADOW_MARGIN,
                                      max(1, width - 2 * SHADOW_MARGIN), max(1, height - 2 * SHADOW_MARGIN))
            surface.set_input_region(cairo.Region(rect))
        except (ImportError, TypeError) as exc:  # pragma: no cover - old pycairo
            log.debug("Cannot set input region: %s", exc)

    # -- interaction --------------------------------------------------------
    def _on_drag_begin(self, gesture, _x, _y) -> None:
        self._dragging = True
        self._drag_moved = False

    def _on_drag_update(self, gesture, offset_x: float, offset_y: float) -> None:
        if not self._dragging:
            return
        if not self._drag_moved and math.hypot(offset_x, offset_y) < DRAG_THRESHOLD:
            return
        if self.mode == "layer":
            self._drag_moved = True
            self._move_layer_surface(offset_x, offset_y)
            return
        if not self._drag_moved:
            self._drag_moved = True
            ok, start_x, start_y = gesture.get_start_point()
            toplevel = self.get_surface()
            if not ok or toplevel is None or not hasattr(toplevel, "begin_move"):
                return
            ok, point = self.view.compute_point(self, Graphene.Point().init(start_x, start_y))
            wx, wy = (point.x, point.y) if ok else (start_x + SHADOW_MARGIN, start_y + SHADOW_MARGIN)
            tx, ty = self.get_surface_transform()
            toplevel.begin_move(gesture.get_device(), Gdk.BUTTON_PRIMARY, wx + tx, wy + ty,
                                gesture.get_current_event_time())
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self._dragging = False

    def _move_layer_surface(self, dx: float, dy: float) -> None:
        # The surface moves under the pointer, so every update's offset is the
        # extra distance still to travel.
        vertical, horizontal = self.config["widget_anchor"].split("-")
        mx, my = self._layer_margins
        mx += -dx if horizontal == "right" else dx
        my += -dy if vertical == "bottom" else dy
        self._layer_margins = [max(SHADOW_MARGIN, int(mx)), max(SHADOW_MARGIN, int(my))]
        shell = layer_shell_module()
        edges = {"top": shell.Edge.TOP, "bottom": shell.Edge.BOTTOM,
                 "left": shell.Edge.LEFT, "right": shell.Edge.RIGHT}
        shell.set_margin(self, edges[horizontal], self._layer_margins[0] - SHADOW_MARGIN)
        shell.set_margin(self, edges[vertical], self._layer_margins[1] - SHADOW_MARGIN)

    def _on_drag_end(self, _gesture, _offset_x, _offset_y) -> None:
        was_drag = self._drag_moved
        self._dragging = False
        self._drag_moved = False
        if self.mode == "layer" and was_drag:
            self.config["widget_margin_x"] = self._layer_margins[0]
            self.config["widget_margin_y"] = self._layer_margins[1]
            return
        if not was_drag:
            self.app.activate()

    def _on_secondary_click(self, gesture, _n_press, x, y) -> None:
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        self.menu.set_pointing_to(rect)
        self.menu.popup()
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)


class WidgetController(GObject.Object):
    """Shows/hides the GTK widget window and keeps it up to date."""

    __gtype_name__ = "BlueGlanceWidgetController"

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.config = app.config
        self.window: DesktopWidgetWindow | None = None
        self._handlers = []
        session = session_info()
        if session.x11:
            self.mode = "x11"
        elif session.wayland and layer_shell_module() is not None:
            self.mode = "layer"
        else:
            self.mode = "floating"
        log.info("Desktop widget mode: %s", self.mode)

    @property
    def uses_layer_shell(self) -> bool:
        return self.mode == "layer"

    def start(self) -> None:
        self._add_actions()
        self._handlers.append((self.config, self.config.connect("changed", self._on_config_changed)))
        self._handlers.append((self.app.manager, self.app.manager.connect("changed", lambda *_: self._refresh())))
        shell = self.app.services.get("shell")
        if shell is not None:
            self._handlers.append((shell, shell.connect("changed", lambda *_: self.sync())))
        style = Adw.StyleManager.get_default()
        self._handlers.append((style, style.connect("notify::dark", lambda *_: self._refresh())))
        self.sync()

    def stop(self) -> None:
        for obj, handler in self._handlers:
            obj.disconnect(handler)
        self._handlers.clear()
        if self.window is not None:
            self.window.destroy()
            self.window = None

    def _add_actions(self) -> None:
        size_action = Gio.SimpleAction.new_stateful(
            "widget-size", GLib.VariantType.new("s"), GLib.Variant.new_string(self.config["widget_size"]))
        size_action.connect("change-state", lambda a, v: self.config.set("widget_size", v.get_string()))
        self.app.add_action(size_action)
        hide_action = Gio.SimpleAction.new("hide-widget", None)
        hide_action.connect("activate", lambda *_: self.config.set("widget_enabled", False))
        self.app.add_action(hide_action)

    def _on_config_changed(self, _config, key: str) -> None:
        if key == "widget_enabled":
            self.sync()
        elif key == "widget_size":
            self.app.lookup_action("widget-size").set_state(GLib.Variant.new_string(self.config[key]))
            self._refresh()
        elif key in ("widget_theme", "low_threshold", "hidden_devices"):
            self._refresh()
        elif key in ("widget_anchor",) and self.window is not None and self.mode == "layer":
            self.window.apply_layer_position()
        elif key in ("widget_position", "widget_margin_x", "widget_margin_y") and self.window is not None:
            if self.config[key] is None or key != "widget_position":
                self._recreate_if_reset(key)

    def _recreate_if_reset(self, key: str) -> None:
        # "Reset position" in Preferences resets these keys; re-place the window.
        if self.mode == "layer":
            self.window.apply_layer_position()
        elif self.mode == "x11" and self.config["widget_position"] is None:
            self.window.destroy()
            self.window = None
            self.sync()

    def sync(self) -> None:
        shell = self.app.services.get("shell")
        want = bool(self.config["widget_enabled"]) and not (shell is not None and shell.active)
        if want and self.window is None:
            self.window = DesktopWidgetWindow(self, self.mode)
            self.window.connect("close-request", self._on_window_close_request)
            self._refresh()
            self.window.present()
        elif not want and self.window is not None:
            self.window.destroy()
            self.window = None

    def _on_window_close_request(self, _window) -> bool:
        # Alt+F4 on the widget hides it for good (until re-enabled).
        self.config["widget_enabled"] = False
        return True

    def _refresh(self) -> None:
        if self.window is None:
            return
        devices = [d for d in self.app.manager.connected_with_battery() if not self.config.is_hidden(d.id)]
        message = None
        bluez = self.app.manager.get_provider("bluez")
        if not devices and bluez is not None and bluez.adapters and not bluez.powered:
            message = "Bluetooth is off"
        self.window.refresh(devices, self.config["low_threshold"], message)
