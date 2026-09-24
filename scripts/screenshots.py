#!/usr/bin/env python3
"""Render README screenshots from demo data.

Run headless with:  xvfb-run -a -s "-screen 0 1920x1200x24" python3 scripts/screenshots.py [outdir]
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("BLUEGLANCE_DEMO", "1")
os.environ.setdefault("BLUEGLANCE_DEMO_STATIC", "1")
os.environ.setdefault("GTK_A11Y", "none")

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, GLib, Graphene, Gtk

from blueglance.application import BlueGlanceApplication
from blueglance.ui.widget_view import WidgetView

OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "screenshots"))
SCALE = float(os.environ.get("SHOT_SCALE", "2"))


def render(widget: Gtk.Widget, name: str) -> None:
    width, height = widget.get_width(), widget.get_height()
    snapshot = Gtk.Snapshot()
    snapshot.scale(SCALE, SCALE)
    Gtk.WidgetPaintable.new(widget).snapshot(snapshot, width, height)
    node = snapshot.to_node()
    renderer = widget.get_native().get_renderer()
    texture = renderer.render_texture(node, Graphene.Rect().init(0, 0, width * SCALE, height * SCALE))
    path = os.path.join(OUT, name)
    texture.save_to_png(path)
    print("wrote", path, f"{int(width * SCALE)}x{int(height * SCALE)}")


class Shooter:
    def __init__(self, app: BlueGlanceApplication):
        self.app = app
        self.steps = []

    def run_steps(self, steps):
        self.steps = list(steps)
        GLib.timeout_add(1200, self._next)

    def _next(self):
        if not self.steps:
            self.app.quit()
            return GLib.SOURCE_REMOVE
        step, delay = self.steps.pop(0)
        step()
        GLib.timeout_add(delay, self._next)
        return GLib.SOURCE_REMOVE


def widget_gallery(app, dark: bool) -> Gtk.Window:
    window = Gtk.Window(title="gallery", decorated=False)
    window.add_css_class("dark" if dark else "light")
    wallpaper = Gtk.Box(spacing=28, css_classes=["shot-wallpaper", "dark" if dark else "light"])
    wallpaper.set_margin_top(0)
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=28, valign=Gtk.Align.START)
    devices = [d for d in app.manager.connected_with_battery()]
    for size in ("small", "medium"):
        view = WidgetView(size)
        view.set_halign(Gtk.Align.START)
        view.set_theme(dark)
        view.set_devices(devices, app.config["low_threshold"])
        column.append(view)
    row = Gtk.Box(spacing=28)
    row.append(column)
    large = WidgetView("large")
    large.set_theme(dark)
    large.set_devices(devices, app.config["low_threshold"])
    large.set_valign(Gtk.Align.START)
    row.append(large)
    wallpaper.append(row)
    window.set_child(wallpaper)
    window.present()
    return window


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    app = BlueGlanceApplication(demo=True)
    shooter = Shooter(app)
    css = Gtk.CssProvider()
    css.load_from_data(
        b"""
        .shot-wallpaper { padding: 44px; }
        .shot-wallpaper.dark { background-image: linear-gradient(135deg, #1d2b53 0%, #3b2a63 45%, #6a2c5a 100%); }
        .shot-wallpaper.light { background-image: linear-gradient(135deg, #a8c0ff 0%, #c7b8f5 50%, #f5c1d9 100%); }
        """,
        -1,
    )
    state = {}

    def on_activate(*_args):
        if state.get("started"):
            return
        state["started"] = True
        from gi.repository import Gdk

        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, 900)
        style = Adw.StyleManager.get_default()

        def set_scheme(dark):
            def step():
                style.set_color_scheme(Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)
            return step

        def shot_main(name):
            return lambda: render(app.window, name)

        def open_prefs():
            from blueglance.ui.preferences import PreferencesDialog

            state["prefs"] = PreferencesDialog(app)
            state["prefs"].present(app.window)

        def close_prefs():
            state["prefs"].close()

        def open_details():
            from blueglance.ui.details import DeviceDetailsDialog

            device = app.manager.find("demo:airpods")
            state["details"] = DeviceDetailsDialog(app, device)
            state["details"].present(app.window)

        def close_details():
            state["details"].close()

        def gallery(dark):
            def step():
                state["gallery"] = widget_gallery(app, dark)
            return step

        def shot_gallery(name):
            def step():
                render(state["gallery"], name)
                state["gallery"].destroy()
            return step

        app.window.set_default_size(500, 720)
        shooter.run_steps(
            [
                (set_scheme(True), 900),
                (shot_main("main-dark.png"), 300),
                (set_scheme(False), 900),
                (shot_main("main-light.png"), 300),
                (set_scheme(True), 300),
                (open_details, 1300),
                (shot_main("details-dark.png"), 300),
                (close_details, 600),
                (open_prefs, 1300),
                (shot_main("preferences-dark.png"), 300),
                (close_prefs, 600),
                (gallery(True), 1500),
                (shot_gallery("widgets-dark.png"), 300),
                (gallery(False), 1500),
                (shot_gallery("widgets-light.png"), 300),
            ]
        )

    app.connect_after("activate", on_activate)
    return app.run(["blueglance", "--demo"])


if __name__ == "__main__":
    sys.exit(main())
