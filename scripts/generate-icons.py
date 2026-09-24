#!/usr/bin/env python3
"""Generate BlueGlance's symbolic icon set.

Symbolic icons must be plain filled <path>s (GTK and GNOME Shell recolour only
the fill of rect/circle/path elements), so every icon is modelled with shapely
geometry — strokes are buffered into outlines — and exported as one evenodd path.

    pip install shapely
    python3 scripts/generate-icons.py
"""

from __future__ import annotations

import math
import os

from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIRS = [
    os.path.join(ROOT, "src", "blueglance", "icons", "hicolor", "scalable", "actions"),
    os.path.join(ROOT, "gnome-extension", "blueglance@rafay-ah.github.io", "icons"),
]
APP_SYMBOLIC_DIR = os.path.join(ROOT, "data", "icons", "hicolor", "symbolic", "apps")

# ------------------------------------------------------------------ helpers


def circle(cx, cy, r):
    return Point(cx, cy).buffer(r, quad_segs=32)


def rrect(x, y, w, h, r=0.0):
    if r <= 0:
        return box(x, y, x + w, y + h)
    r = min(r, w / 2, h / 2)
    return box(x + r, y + r, x + w - r, y + h - r).buffer(r, quad_segs=24)


def outline(shape, width):
    return shape.difference(shape.buffer(-width, quad_segs=24))


def line(points, width, cap="round", join="round"):
    return LineString(points).buffer(width / 2, cap_style=cap, join_style=join, quad_segs=24)


def arc_points(cx, cy, r, start_deg, end_deg, steps=96):
    pts = []
    for i in range(steps + 1):
        a = math.radians(start_deg + (end_deg - start_deg) * i / steps)
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def mirror(shape):
    return affinity.scale(shape, xfact=-1, yfact=1, origin=(8, 8))


def union(*shapes):
    return unary_union(list(shapes))


# ------------------------------------------------------------------ icons


def bolt():
    return Polygon([(9.6, 0.6), (2.6, 9.3), (7.2, 9.3), (6.3, 15.4), (13.4, 6.6), (8.8, 6.6)])


def earbud_left():
    head = circle(9.0, 4.9, 3.7)
    stem = rrect(5.3, 4.9, 2.9, 10.4, 1.45)
    mesh = circle(9.7, 4.6, 1.15)
    return union(head, stem).difference(mesh)


def earbud_right():
    return mirror(earbud_left())


def earbuds():
    left = union(circle(5.1, 5.0, 2.75), rrect(2.4, 4.8, 2.2, 10.0, 1.1)).difference(circle(5.6, 4.8, 0.85))
    return union(left, mirror(left))


def case():
    body = rrect(1.2, 3.2, 13.6, 10.6, 3.4)
    seam = box(0, 6.6, 16, 7.5)
    led = circle(8, 10.4, 0.95)
    return body.difference(seam).difference(led)


def headphones():
    band = line(arc_points(8, 8.8, 6.1, 180, 360), 1.7)
    cups = union(rrect(1.0, 8.4, 3.9, 6.4, 1.5), rrect(11.1, 8.4, 3.9, 6.4, 1.5))
    return union(band, cups)


def headset():
    band = line(arc_points(8, 8.4, 5.9, 180, 360), 1.6)
    cups = union(rrect(1.2, 7.8, 3.6, 5.6, 1.4), rrect(11.2, 7.8, 3.6, 5.6, 1.4))
    boom = line([(3.0, 13.2), (4.0, 14.8), (8.2, 14.8)], 1.3)
    mic = rrect(8.0, 13.7, 3.0, 2.2, 1.1)
    return union(band, cups, boom, mic)


def mouse():
    shell = outline(rrect(3.4, 0.8, 9.2, 14.4, 4.6), 1.5)
    wheel = rrect(7.2, 3.6, 1.6, 3.4, 0.8)
    divider = box(7.45, 2.0, 8.55, 3.8)
    return union(shell, wheel, divider)


def keyboard():
    frame = outline(rrect(0.4, 2.8, 15.2, 10.4, 2.2), 1.5)
    keys = []
    row1_y, row2_y, key_h = 5.75, 8.75, 1.5
    for x in (2.8, 5.05, 7.3, 9.55, 11.8):
        keys.append(rrect(x, row1_y, 1.45, key_h, 0.35))
    keys.append(rrect(2.8, row2_y, 1.45, key_h, 0.35))
    keys.append(rrect(5.05, row2_y, 5.9, key_h, 0.35))
    keys.append(rrect(11.8, row2_y, 1.45, key_h, 0.35))
    return union(frame, *keys)


def gamepad():
    body = union(rrect(0.8, 3.6, 14.4, 7.4, 3.7), circle(3.7, 10.4, 2.7), circle(12.3, 10.4, 2.7))
    dpad = union(rrect(2.6, 6.75, 3.8, 1.3, 0.3), rrect(3.85, 5.5, 1.3, 3.8, 0.3))
    buttons = union(*(circle(x, y, 0.72) for x, y in ((11.3, 5.9), (12.9, 7.4), (11.3, 8.9), (9.7, 7.4))))
    return body.difference(dpad).difference(buttons)


def speaker():
    frame = outline(rrect(3.2, 0.6, 9.6, 14.8, 2.4), 1.5)
    woofer = circle(8, 10.1, 2.45).difference(circle(8, 10.1, 0.8))
    tweeter = circle(8, 4.9, 1.15)
    return union(frame, woofer, tweeter)


def phone():
    frame = rrect(3.6, 0.5, 8.8, 15, 2.3)
    screen = rrect(5.1, 2.4, 5.8, 10.3, 0.5)
    home = rrect(6.9, 13.6, 2.2, 0.8, 0.4)
    return frame.difference(screen).difference(home)


def tablet():
    frame = rrect(1.2, 1.0, 13.6, 14.0, 2.2)
    screen = rrect(2.7, 2.5, 10.6, 10.3, 0.5)
    home = rrect(6.9, 13.3, 2.2, 0.8, 0.4)
    return frame.difference(screen).difference(home)


def pen():
    body = rrect(6.6, 0.4, 2.8, 10.6, 0.9)
    tip = Polygon([(6.6, 10.6), (9.4, 10.6), (8.0, 14.9)])
    band = box(6.0, 2.9, 10.0, 3.7)
    shape = union(body, tip).difference(band)
    return affinity.rotate(shape, 45, origin=(8, 8))


def watch():
    straps = union(rrect(5.4, 0.3, 5.2, 3.3, 0.8), rrect(5.4, 12.4, 5.2, 3.3, 0.8))
    body = rrect(3.3, 2.9, 9.4, 10.2, 2.7)
    face = rrect(4.8, 4.4, 6.4, 7.2, 1.5)
    crown = rrect(12.2, 6.4, 1.5, 2.6, 0.5)
    return union(straps.difference(body), body.difference(face), crown)


def touchpad():
    frame = outline(rrect(0.8, 2.2, 14.4, 11.6, 2.4), 1.5)
    bar = box(2.0, 9.3, 14.0, 10.5)
    split = box(7.4, 10.0, 8.6, 12.6)
    return union(frame, bar, split)


def remote():
    frame = outline(rrect(4.9, 0.4, 6.2, 15.2, 3.1), 1.5)
    buttons = union(circle(8, 4.5, 1.05), circle(8, 8.3, 0.7), circle(8, 10.8, 0.7))
    return union(frame, buttons)


def laptop():
    screen = outline(rrect(2.4, 1.8, 11.2, 8.8, 1.5), 1.5)
    base = rrect(0.4, 11.4, 15.2, 2.4, 1.2).difference(rrect(6.3, 11.0, 3.4, 1.1, 0.5))
    return union(screen, base)


def rune(width=1.55):
    pts = [(4.1, 4.7), (11.6, 11.1), (8.0, 14.5), (8.0, 1.5), (11.6, 4.9), (4.1, 11.3)]
    return line(pts, width, cap="round", join="round")


def bluetooth():
    return rune()


def bluetooth_disabled():
    slash_band = line([(1.6, 1.6), (14.4, 14.4)], 3.4)
    slash = line([(1.8, 1.8), (14.2, 14.2)], 1.5)
    return union(rune().difference(slash_band), slash)


def pin():
    head = rrect(4.4, 0.6, 7.2, 2.4, 1.2)
    body = Polygon([(5.9, 2.6), (10.1, 2.6), (11.0, 8.2), (5.0, 8.2)])
    plate = rrect(2.9, 8.0, 10.2, 2.0, 1.0)
    needle = Polygon([(7.35, 9.8), (8.65, 9.8), (8.35, 15.4), (7.65, 15.4)])
    return union(head, body, plate, needle)


def app_symbolic():
    gauge = line(arc_points(8, 8, 6.55, -90, 180), 1.8)
    inner = affinity.scale(rune(1.6), xfact=0.56, yfact=0.56, origin=(8, 8))
    return union(gauge, inner)


ICONS = {
    "blueglance-bolt-symbolic": bolt,
    "blueglance-earbud-left-symbolic": earbud_left,
    "blueglance-earbud-right-symbolic": earbud_right,
    "blueglance-earbuds-symbolic": earbuds,
    "blueglance-case-symbolic": case,
    "blueglance-headphones-symbolic": headphones,
    "blueglance-headset-symbolic": headset,
    "blueglance-mouse-symbolic": mouse,
    "blueglance-keyboard-symbolic": keyboard,
    "blueglance-gamepad-symbolic": gamepad,
    "blueglance-speaker-symbolic": speaker,
    "blueglance-phone-symbolic": phone,
    "blueglance-tablet-symbolic": tablet,
    "blueglance-pen-symbolic": pen,
    "blueglance-watch-symbolic": watch,
    "blueglance-touchpad-symbolic": touchpad,
    "blueglance-remote-symbolic": remote,
    "blueglance-laptop-symbolic": laptop,
    "blueglance-bluetooth-symbolic": bluetooth,
    "blueglance-bluetooth-disabled-symbolic": bluetooth_disabled,
    "blueglance-pin-symbolic": pin,
}

# ------------------------------------------------------------------ export


def ring_d(coords) -> str:
    pts = list(coords)[:-1]
    head = f"M{pts[0][0]:.2f} {pts[0][1]:.2f}"
    rest = "".join(f"L{x:.2f} {y:.2f}" for x, y in pts[1:])
    return f"{head}{rest}Z"


def to_path(geometry) -> str:
    geometry = geometry.simplify(0.01, preserve_topology=True)
    polys = list(geometry.geoms) if isinstance(geometry, MultiPolygon) else [geometry]
    parts = []
    for poly in polys:
        if poly.is_empty:
            continue
        parts.append(ring_d(poly.exterior.coords))
        parts.extend(ring_d(hole.coords) for hole in poly.interiors)
    return "".join(parts)


def svg(geometry) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
        f'<path fill="#2e3436" fill-rule="evenodd" d="{to_path(geometry)}"/></svg>\n'
    )


# Only the app uses these; extensions.gnome.org rejects unused files.
APP_ONLY = {"blueglance-bluetooth-disabled-symbolic", "blueglance-pin-symbolic"}


def main() -> None:
    for out_dir in OUT_DIRS:
        os.makedirs(out_dir, exist_ok=True)
        for name, factory in ICONS.items():
            if name in APP_ONLY and out_dir != OUT_DIRS[0]:
                continue
            with open(os.path.join(out_dir, f"{name}.svg"), "w", encoding="utf-8") as fh:
                fh.write(svg(factory()))
    # The app's symbolic icon: installed into hicolor, and bundled next to the
    # UI icons so the tray icon also resolves when running from a checkout.
    bundled = os.path.join(ROOT, "src", "blueglance", "icons", "hicolor", "scalable", "apps")
    for directory in (APP_SYMBOLIC_DIR, bundled):
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "io.github.rafay_ah.BlueGlance-symbolic.svg"), "w",
                  encoding="utf-8") as fh:
            fh.write(svg(app_symbolic()))
    ext_dir = OUT_DIRS[1]
    with open(os.path.join(ext_dir, "blueglance-app-symbolic.svg"), "w", encoding="utf-8") as fh:
        fh.write(svg(app_symbolic()))
    print(f"wrote {len(ICONS)} icons")


if __name__ == "__main__":
    main()
