"""Desktop notifications for low / critical / fully charged batteries."""

from __future__ import annotations

import logging

from gi.repository import Gio

from .. import icons
from ..models import ChargeState, Device, LevelClass, classify_level

log = logging.getLogger(__name__)

HYSTERESIS = 5  # re-arm "low" only after the level climbs this far above the threshold
FULL_REARM_BELOW = 90


class BatteryAlerts:
    """Pure decision logic, separated from GIO for unit testing.

    ``evaluate`` returns a list of ``(kind, device)`` events where kind is one of
    ``"low"``, ``"critical"``, ``"full"`` or ``"recovered"``.
    """

    def __init__(self):
        self.stage: dict[str, str] = {}
        self.full_sent: set[str] = set()
        # UPower reports any device at 100 % as "fully charged", so only announce
        # "full" for devices we actually watched charging.
        self.seen_charging: set[str] = set()

    def evaluate(self, devices: list[Device], threshold: int, notify_low: bool, notify_full: bool):
        events = []
        seen = set()
        for device in devices:
            if not device.connected or device.level is None:
                continue
            seen.add(device.id)
            stage = self.stage.get(device.id, "normal")
            level = device.level
            cls = classify_level(level, threshold)
            charging = device.charging or device.state == ChargeState.FULL

            if charging:
                if stage in ("low", "critical"):
                    events.append(("recovered", device))
                stage = "normal"
            elif cls == LevelClass.CRITICAL and stage != "critical":
                if notify_low:
                    events.append(("critical", device))
                stage = "critical"
            elif cls == LevelClass.LOW and stage == "normal":
                if notify_low:
                    events.append(("low", device))
                stage = "low"
            elif cls == LevelClass.NORMAL and level >= threshold + HYSTERESIS and stage != "normal":
                events.append(("recovered", device))
                stage = "normal"
            self.stage[device.id] = stage

            if device.charging:
                self.seen_charging.add(device.id)
            full = device.state == ChargeState.FULL or (device.charging and level >= 100)
            if full and device.id in self.seen_charging and device.id not in self.full_sent:
                if notify_full:
                    events.append(("full", device))
                self.full_sent.add(device.id)
                self.seen_charging.discard(device.id)
            elif not full and level < FULL_REARM_BELOW:
                self.full_sent.discard(device.id)

        for gone in set(self.stage) - seen:
            del self.stage[gone]
            self.seen_charging.discard(gone)
        return events


class Notifier:
    def __init__(self, app):
        self.app = app
        self.alerts = BatteryAlerts()
        self._handlers = []

    def start(self) -> None:
        self._handlers = [
            (self.app.manager, self.app.manager.connect("changed", self._evaluate)),
            (self.app.config, self.app.config.connect("changed", self._on_config_changed)),
        ]
        self._evaluate()

    def stop(self) -> None:
        for obj, handler in self._handlers:
            obj.disconnect(handler)
        self._handlers.clear()

    def _on_config_changed(self, _config, key: str) -> None:
        if key in ("low_threshold", "notify_low", "notify_full"):
            self._evaluate()

    def _evaluate(self, *_args) -> None:
        config = self.app.config
        devices = [d for d in self.app.manager.devices if not config.is_hidden(d.id)]
        events = self.alerts.evaluate(devices, config["low_threshold"], config["notify_low"], config["notify_full"])
        for kind, device in events:
            if kind == "recovered":
                self.app.withdraw_notification(f"battery-{device.id}")
            else:
                self._send(kind, device)

    def _send(self, kind: str, device: Device) -> None:
        if kind == "critical":
            title = f"{device.name} is almost out of battery"
            body = f"{device.level}% left — charge it now."
            priority = Gio.NotificationPriority.HIGH
        elif kind == "low":
            title = f"{device.name} battery is low"
            body = f"{device.level}% left. Charge it soon."
            priority = Gio.NotificationPriority.NORMAL
        else:
            title = f"{device.name} is fully charged"
            body = "You can unplug it now."
            priority = Gio.NotificationPriority.LOW
        notification = Gio.Notification.new(title)
        notification.set_body(body)
        notification.set_priority(priority)
        notification.set_icon(Gio.ThemedIcon.new(icons.for_kind(device.kind)))
        notification.set_default_action("app.show-window")
        notification_id = f"battery-{device.id}" if kind != "full" else f"full-{device.id}"
        log.info("Notify: %s", title)
        self.app.send_notification(notification_id, notification)
