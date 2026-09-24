"""UPower backend.

UPower already aggregates most peripheral batteries on Linux:

* Bluetooth devices exposing ``org.bluez.Battery1`` (GATT battery service,
  and headsets whose battery PipeWire forwards to BlueZ),
* kernel HID batteries (``/sys/class/power_supply/hid-*-battery``) such as
  Bluetooth mice/keyboards, DualSense/DualShock controllers,
* Logitech Unifying/Bolt receivers (``hidpp_battery_*``) and other wireless
  dongles.
"""

from __future__ import annotations

import logging

from gi.repository import Gio, GLib

from ..models import ChargeState, DeviceKind, Report, clamp_level, normalize_address
from . import Provider
from .dbus_util import SignalSubscriptions, call, get_all_properties, get_system_bus

log = logging.getLogger(__name__)

UPOWER = "org.freedesktop.UPower"
UPOWER_PATH = "/org/freedesktop/UPower"
DEVICE_IFACE = "org.freedesktop.UPower.Device"

# UpDeviceKind (up-types.h)
KIND_LINE_POWER = 1
KIND_BATTERY = 2
KIND_UPS = 3
KIND_MAP = {
    2: DeviceKind.COMPUTER,
    5: DeviceKind.MOUSE,
    6: DeviceKind.KEYBOARD,
    7: DeviceKind.PHONE,  # PDA
    8: DeviceKind.PHONE,
    10: DeviceKind.TABLET,
    12: DeviceKind.GAMEPAD,
    13: DeviceKind.PEN,
    14: DeviceKind.TOUCHPAD,
    17: DeviceKind.HEADSET,
    18: DeviceKind.SPEAKER,
    19: DeviceKind.HEADPHONES,
    21: DeviceKind.HEADPHONES,  # "other audio"
    22: DeviceKind.REMOTE,
    26: DeviceKind.WATCH,
}

# UpDeviceState
STATE_MAP = {
    1: ChargeState.CHARGING,
    2: ChargeState.DISCHARGING,
    3: ChargeState.DISCHARGING,  # empty
    4: ChargeState.FULL,
    5: ChargeState.NOT_CHARGING,  # pending charge
    6: ChargeState.DISCHARGING,  # pending discharge
}

# UpDeviceLevel -> representative percentage for devices that only report a coarse level.
LEVEL_NONE = 1
COARSE_LEVELS = {3: 10, 4: 5, 6: 55, 7: 70, 8: 100}


def report_from_properties(path: str, props: dict, include_system: bool = False) -> Report | None:
    """Turn an ``org.freedesktop.UPower.Device`` property dict into a report."""
    kind_id = int(props.get("Type", 0))
    if kind_id == KIND_LINE_POWER:
        return None
    power_supply = bool(props.get("PowerSupply", False))
    if power_supply or kind_id in (KIND_BATTERY, KIND_UPS):
        if not include_system or kind_id != KIND_BATTERY:
            return None
    if not props.get("IsPresent", True):
        return None

    native_path = str(props.get("NativePath", ""))
    serial = str(props.get("Serial", "") or "")
    address = normalize_address(serial) or normalize_address(native_path)
    if address:
        key = address
    elif serial:
        key = f"upower:{serial}"
    else:
        key = f"upower:{native_path or path}"

    state = STATE_MAP.get(int(props.get("State", 0)), ChargeState.UNKNOWN)
    percentage = props.get("Percentage")
    battery_level = int(props.get("BatteryLevel", LEVEL_NONE) or 0)
    coarse = battery_level in COARSE_LEVELS
    if coarse:
        level = clamp_level(percentage) or COARSE_LEVELS[battery_level]
    else:
        level = clamp_level(percentage)
        # HID batteries show up as 0 % "unknown" until the device reports once.
        if level == 0 and state == ChargeState.UNKNOWN:
            level = None

    kind = KIND_MAP.get(kind_id, DeviceKind.OTHER)
    model = str(props.get("Model", "") or "").strip()
    vendor = str(props.get("Vendor", "") or "").strip()
    if kind == DeviceKind.COMPUTER:
        name = "This computer"
    else:
        name = model or vendor or None

    return Report(
        source="upower",
        key=key,
        name=name,
        kind=kind,
        level=level,
        state=state,
        connected=True,
        address=address,
        vendor=vendor or None,
        coarse=coarse,
        priority=20,
        hints={"upower_path": path},
    )


class UPowerProvider(Provider):
    source = "upower"

    def __init__(self, bus: Gio.DBusConnection | None = None):
        super().__init__()
        self._bus = bus
        self._props: dict[str, dict] = {}
        self._signals: SignalSubscriptions | None = None
        self._watch_id = 0
        self._cancellable = Gio.Cancellable()
        self._include_system = False
        self.available = False

    # -- configuration ------------------------------------------------------
    @property
    def include_system(self) -> bool:
        return self._include_system

    @include_system.setter
    def include_system(self, value: bool) -> None:
        if value != self._include_system:
            self._include_system = value
            self._rebuild()

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        self._bus = self._bus or get_system_bus()
        if self._bus is None:
            return
        self._signals = SignalSubscriptions(self._bus)
        self._signals.add(UPOWER, UPOWER, "DeviceAdded", self._on_device_added, path=UPOWER_PATH)
        self._signals.add(UPOWER, UPOWER, "DeviceRemoved", self._on_device_removed, path=UPOWER_PATH)
        self._signals.add(UPOWER, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                          self._on_properties_changed, arg0=DEVICE_IFACE)
        self._watch_id = Gio.bus_watch_name_on_connection(
            self._bus, UPOWER, Gio.BusNameWatcherFlags.AUTO_START, self._on_appeared, self._on_vanished
        )

    def stop(self) -> None:
        self._cancellable.cancel()
        self._cancellable = Gio.Cancellable()
        if self._watch_id:
            Gio.bus_unwatch_name(self._watch_id)
            self._watch_id = 0
        if self._signals:
            self._signals.clear()
        self._props.clear()
        self._reports.clear()

    # -- D-Bus plumbing -----------------------------------------------------
    def _on_appeared(self, _conn, _name, _owner) -> None:
        self.available = True
        call(self._bus, UPOWER, UPOWER_PATH, UPOWER, "EnumerateDevices", None, "(ao)",
             self._on_enumerated, self._cancellable)

    def _on_vanished(self, _conn, _name) -> None:
        self.available = False
        self._props.clear()
        self._rebuild()

    def _on_enumerated(self, result, error) -> None:
        if error:
            log.warning("UPower EnumerateDevices failed: %s", error.message)
            return
        for path in result[0]:
            self._fetch(path)

    def _fetch(self, path: str) -> None:
        def done(props, error):
            if error:
                log.debug("UPower GetAll(%s) failed: %s", path, error.message)
                return
            self._props[path] = props
            self._rebuild()

        get_all_properties(self._bus, UPOWER, path, DEVICE_IFACE, done, self._cancellable)

    def _on_device_added(self, _path, _signal, args) -> None:
        self._fetch(args[0])

    def _on_device_removed(self, _path, _signal, args) -> None:
        if self._props.pop(args[0], None) is not None:
            self._rebuild()

    def _on_properties_changed(self, path, _signal, args) -> None:
        _iface, changed, invalidated = args
        props = self._props.get(path)
        if props is None:
            self._fetch(path)
            return
        props.update(changed)
        if invalidated:
            self._fetch(path)
        self._rebuild()

    def _rebuild(self) -> None:
        reports = {}
        for path, props in self._props.items():
            report = report_from_properties(path, props, self._include_system)
            if report is None:
                continue
            existing = reports.get(report.key)
            # The same device can appear twice (e.g. BlueZ + kernel HID battery);
            # keep the one that actually has a level, preferring the freshest.
            if existing is None or (existing.level is None and report.level is not None):
                reports[report.key] = report
        self._reports = reports
        self._changed()
