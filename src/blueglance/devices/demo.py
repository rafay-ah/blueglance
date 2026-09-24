"""Sample devices for screenshots, development and ``blueglance --demo``."""

from __future__ import annotations

import random
from dataclasses import replace

from gi.repository import GLib

from ..models import ChargeState, Component, DeviceKind, Report, headline_level
from . import Provider


def _airpods(left: int, right: int, case: int, case_charging: bool) -> tuple[Component, ...]:
    return (
        Component("left", "Left", left),
        Component("right", "Right", right),
        Component("case", "Case", case, charging=case_charging),
    )


class DemoProvider(Provider):
    source = "demo"

    def __init__(self, animate: bool = True):
        super().__init__()
        self._animate = animate
        self._timer = 0
        self._tick = 0
        buds = _airpods(92, 88, 61, True)
        samples = [
            Report(
                source="demo", key="demo:airpods", name="AirPods Pro", kind=DeviceKind.EARBUDS,
                level=headline_level(buds), state=ChargeState.DISCHARGING, connected=True,
                components=buds, model="AirPods Pro 2", priority=50,
            ),
            Report(
                source="demo", key="demo:mouse", name="MX Master 3S", kind=DeviceKind.MOUSE,
                level=74, state=ChargeState.DISCHARGING, connected=True, priority=50,
            ),
            Report(
                source="demo", key="demo:keyboard", name="Keychron K3", kind=DeviceKind.KEYBOARD,
                level=16, state=ChargeState.DISCHARGING, connected=True, priority=50,
            ),
            Report(
                source="demo", key="demo:gamepad", name="DualSense", kind=DeviceKind.GAMEPAD,
                level=43, state=ChargeState.CHARGING, connected=True, priority=50,
            ),
            Report(
                source="demo", key="demo:headphones", name="WH-1000XM5", kind=DeviceKind.HEADPHONES,
                level=58, state=ChargeState.DISCHARGING, connected=False, priority=50,
            ),
            Report(
                source="demo", key="demo:speaker", name="Flip 6", kind=DeviceKind.SPEAKER,
                level=None, connected=True, priority=50, hints={"audio": True},
            ),
        ]
        self._reports = {r.key: r for r in samples}

    def start(self) -> None:
        self._changed()
        if self._animate and not self._timer:
            self._timer = GLib.timeout_add_seconds(4, self._on_tick)

    def stop(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _on_tick(self) -> bool:
        """Drift levels a little so the UI has something to animate."""
        self._tick += 1
        gamepad = self._reports["demo:gamepad"]
        level = gamepad.level + 1 if gamepad.level < 100 else 100
        state = ChargeState.FULL if level >= 100 else ChargeState.CHARGING
        self._reports["demo:gamepad"] = replace(gamepad, level=level, state=state)
        if self._tick % 3 == 0:
            mouse = self._reports["demo:mouse"]
            self._reports["demo:mouse"] = replace(mouse, level=max(1, mouse.level - random.choice((0, 1))))
        self._changed()
        return GLib.SOURCE_CONTINUE

