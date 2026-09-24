"""Icon names. BlueGlance ships its own symbolic icon set (``src/blueglance/icons``)
so the widget looks identical on every icon theme."""

from __future__ import annotations

from pathlib import Path

from .models import DeviceKind

ICON_DIR = Path(__file__).resolve().parent / "icons"

KIND_ICONS = {
    DeviceKind.EARBUDS: "blueglance-earbuds-symbolic",
    DeviceKind.HEADPHONES: "blueglance-headphones-symbolic",
    DeviceKind.HEADSET: "blueglance-headset-symbolic",
    DeviceKind.SPEAKER: "blueglance-speaker-symbolic",
    DeviceKind.MOUSE: "blueglance-mouse-symbolic",
    DeviceKind.KEYBOARD: "blueglance-keyboard-symbolic",
    DeviceKind.TOUCHPAD: "blueglance-touchpad-symbolic",
    DeviceKind.GAMEPAD: "blueglance-gamepad-symbolic",
    DeviceKind.PEN: "blueglance-pen-symbolic",
    DeviceKind.TABLET: "blueglance-tablet-symbolic",
    DeviceKind.PHONE: "blueglance-phone-symbolic",
    DeviceKind.WATCH: "blueglance-watch-symbolic",
    DeviceKind.REMOTE: "blueglance-remote-symbolic",
    DeviceKind.COMPUTER: "blueglance-laptop-symbolic",
    DeviceKind.OTHER: "blueglance-bluetooth-symbolic",
}

COMPONENT_ICONS = {
    "left": "blueglance-earbud-left-symbolic",
    "right": "blueglance-earbud-right-symbolic",
    "case": "blueglance-case-symbolic",
}

BOLT = "blueglance-bolt-symbolic"
PIN = "blueglance-pin-symbolic"
BLUETOOTH = "blueglance-bluetooth-symbolic"
BLUETOOTH_OFF = "blueglance-bluetooth-disabled-symbolic"
APP_SYMBOLIC = "io.github.rafay_ah.BlueGlance-symbolic"


def for_kind(kind: DeviceKind) -> str:
    return KIND_ICONS.get(kind, BLUETOOTH)


def for_component(key: str, fallback_kind: DeviceKind = DeviceKind.EARBUDS) -> str:
    return COMPONENT_ICONS.get(key) or for_kind(fallback_kind)
