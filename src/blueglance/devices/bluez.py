"""BlueZ backend: connected devices, their names/types and ``Battery1`` levels.

UPower covers most batteries, but BlueZ is the source of truth for *which*
Bluetooth devices are connected, their user-visible alias and type, and lets us
spot connected devices that don't report a battery at all (e.g. headphones on
systems where BlueZ's experimental battery provider API is disabled).
"""

from __future__ import annotations

import logging
import re

from gi.repository import Gio

from ..models import ChargeState, DeviceKind, Report, clamp_level, normalize_address
from . import Provider
from .dbus_util import OBJECT_MANAGER_IFACE, SignalSubscriptions, call, get_system_bus

log = logging.getLogger(__name__)

BLUEZ = "org.bluez"
DEVICE_IFACE = "org.bluez.Device1"
BATTERY_IFACE = "org.bluez.Battery1"
ADAPTER_IFACE = "org.bluez.Adapter1"
BATTERY_PROVIDER_MANAGER_IFACE = "org.bluez.BatteryProviderManager1"

APPLE_VENDOR_ID = 0x004C

# Service UUIDs that mark a device as an audio device.
AUDIO_UUIDS = {
    "0000110b-0000-1000-8000-00805f9b34fb",  # A2DP sink
    "0000110d-0000-1000-8000-00805f9b34fb",  # A2DP
    "0000111e-0000-1000-8000-00805f9b34fb",  # Handsfree
    "00001108-0000-1000-8000-00805f9b34fb",  # Headset
    "00001131-0000-1000-8000-00805f9b34fb",  # Headset HS
    "0000184e-0000-1000-8000-00805f9b34fb",  # Audio Stream Control (LE audio)
}
HANDSFREE_UUIDS = {
    "0000111e-0000-1000-8000-00805f9b34fb",
    "00001108-0000-1000-8000-00805f9b34fb",
    "00001131-0000-1000-8000-00805f9b34fb",
}

ICON_KINDS = {
    "input-mouse": DeviceKind.MOUSE,
    "input-keyboard": DeviceKind.KEYBOARD,
    "input-gaming": DeviceKind.GAMEPAD,
    "input-tablet": DeviceKind.TABLET,
    "audio-headphones": DeviceKind.HEADPHONES,
    "audio-headset": DeviceKind.HEADSET,
    "audio-speakers": DeviceKind.SPEAKER,
    "audio-card": DeviceKind.SPEAKER,
    "phone": DeviceKind.PHONE,
}

# BLE GAP appearance values (Bluetooth Assigned Numbers, section 2.6).
APPEARANCE_KINDS = {
    0x00C0: DeviceKind.WATCH,
    0x00C1: DeviceKind.WATCH,
    0x00C2: DeviceKind.WATCH,
    0x0180: DeviceKind.REMOTE,
    0x03C1: DeviceKind.KEYBOARD,
    0x03C2: DeviceKind.MOUSE,
    0x03C3: DeviceKind.GAMEPAD,
    0x03C4: DeviceKind.GAMEPAD,
    0x03C5: DeviceKind.TABLET,
    0x03C7: DeviceKind.PEN,
    0x03C9: DeviceKind.TOUCHPAD,
    0x0841: DeviceKind.SPEAKER,
    0x0842: DeviceKind.SPEAKER,
    0x0843: DeviceKind.SPEAKER,
    0x0844: DeviceKind.SPEAKER,
    0x0845: DeviceKind.SPEAKER,
    0x0941: DeviceKind.EARBUDS,
    0x0942: DeviceKind.HEADSET,
    0x0943: DeviceKind.HEADPHONES,
    0x0944: DeviceKind.HEADPHONES,
}

_NAME_RULES = [
    (re.compile(r"airpods\s*max|beats\s*(studio\s*(pro|\d)|solo)", re.I), DeviceKind.HEADPHONES),
    (re.compile(r"airpods|buds|earbud|powerbeats|beats\s*(fit|flex|x\b)|\bwf-|\bearfun|\btws\b|in-?ear", re.I),
     DeviceKind.EARBUDS),
    (re.compile(r"dualsense|dualshock|controller|gamepad|joy-?con|8bitdo|xbox|\bpro con", re.I), DeviceKind.GAMEPAD),
    (re.compile(r"trackpad|touchpad", re.I), DeviceKind.TOUCHPAD),
    (re.compile(r"pencil|stylus|\bpen\b", re.I), DeviceKind.PEN),
    (re.compile(r"\bmouse\b|\bmx (master|anywhere)|\bmagic mouse|trackball", re.I), DeviceKind.MOUSE),
    (re.compile(r"keyboard|keychron|\bk\d{3}\b|\bmx keys", re.I), DeviceKind.KEYBOARD),
    (re.compile(r"\bwatch\b|\bband\b|fitbit|garmin", re.I), DeviceKind.WATCH),
    (re.compile(r"speaker|soundbar|\bflip \d|\bcharge \d|soundlink|boom", re.I), DeviceKind.SPEAKER),
]

_MODALIAS_RE = re.compile(r"^(bluetooth|usb):v([0-9A-Fa-f]{4})p([0-9A-Fa-f]{4})")


def parse_modalias(modalias: str | None) -> tuple[str, int, int] | None:
    """``bluetooth:v004Cp2014d0E26`` -> ("bluetooth", 0x004C, 0x2014)."""
    if not modalias:
        return None
    match = _MODALIAS_RE.match(modalias)
    if not match:
        return None
    return match.group(1), int(match.group(2), 16), int(match.group(3), 16)


def kind_from_class(cod: int | None) -> DeviceKind | None:
    if not cod:
        return None
    major = (cod >> 8) & 0x1F
    minor = (cod >> 2) & 0x3F
    if major == 0x02:
        return DeviceKind.PHONE
    if major == 0x04:
        if minor in (0x01, 0x02):
            return DeviceKind.HEADSET
        if minor == 0x06:
            return DeviceKind.HEADPHONES
        if minor in (0x05, 0x07, 0x08, 0x0A):
            return DeviceKind.SPEAKER
        return None
    if major == 0x05:
        sub = minor & 0x0F
        if sub in (0x01, 0x02):
            return DeviceKind.GAMEPAD
        if sub == 0x03:
            return DeviceKind.REMOTE
        if sub == 0x05:
            return DeviceKind.TABLET
        if sub == 0x07:
            return DeviceKind.PEN
        kbd_mouse = (minor >> 4) & 0x03
        if kbd_mouse in (0x01, 0x03):
            return DeviceKind.KEYBOARD
        if kbd_mouse == 0x02:
            return DeviceKind.MOUSE
        return None
    if major == 0x07:
        return DeviceKind.WATCH
    return None


def kind_from_name(name: str | None) -> DeviceKind | None:
    if not name:
        return None
    for pattern, kind in _NAME_RULES:
        if pattern.search(name):
            return kind
    return None


def guess_kind(props: dict) -> DeviceKind:
    """Best-effort device type from BlueZ ``Device1`` properties."""
    name = props.get("Alias") or props.get("Name")
    from_name = kind_from_name(name)
    icon_kind = ICON_KINDS.get(str(props.get("Icon") or ""))
    appearance_kind = APPEARANCE_KINDS.get(int(props.get("Appearance") or 0))
    class_kind = kind_from_class(int(props.get("Class") or 0))
    base = appearance_kind or icon_kind or class_kind

    # Names are the only way to tell earbuds from over-ear headphones, and they
    # also rescue devices that advertise a generic type.
    if from_name and (base is None or base.is_audio == from_name.is_audio or base == DeviceKind.OTHER):
        return from_name
    return base or from_name or DeviceKind.OTHER


def report_from_objects(path: str, interfaces: dict) -> Report | None:
    dev = interfaces.get(DEVICE_IFACE)
    if not dev:
        return None
    battery = interfaces.get(BATTERY_IFACE)
    connected = bool(dev.get("Connected", False))
    if not connected and battery is None:
        return None

    address = normalize_address(str(dev.get("Address", ""))) or normalize_address(path)
    if not address:
        return None
    uuids = {str(u).lower() for u in dev.get("UUIDs", [])}
    modalias = parse_modalias(dev.get("Modalias"))
    hints = {
        "bluez_path": path,
        "audio": bool(uuids & AUDIO_UUIDS),
        "handsfree": bool(uuids & HANDSFREE_UUIDS),
        "apple": bool(modalias and modalias[1] == APPLE_VENDOR_ID),
    }
    if modalias:
        hints["product_id"] = modalias[2]
    if battery is not None and battery.get("Source"):
        hints["battery_source"] = str(battery["Source"])

    return Report(
        source="bluez",
        key=address,
        name=str(dev.get("Alias") or dev.get("Name") or "") or None,
        kind=guess_kind(dev),
        level=clamp_level(battery.get("Percentage")) if battery is not None else None,
        state=ChargeState.UNKNOWN,
        connected=connected,
        address=address,
        priority=10,
        hints=hints,
    )


class BlueZProvider(Provider):
    source = "bluez"

    def __init__(self, bus: Gio.DBusConnection | None = None):
        super().__init__()
        self._bus = bus
        self._objects: dict[str, dict[str, dict]] = {}
        self._signals: SignalSubscriptions | None = None
        self._watch_id = 0
        self._cancellable = Gio.Cancellable()
        self.available = False

    # -- public diagnostics -------------------------------------------------
    @property
    def adapters(self) -> list[str]:
        return sorted(p for p, ifaces in self._objects.items() if ADAPTER_IFACE in ifaces)

    @property
    def powered(self) -> bool:
        return any(self._objects[p][ADAPTER_IFACE].get("Powered", False) for p in self.adapters)

    @property
    def battery_provider_supported(self) -> bool | None:
        """Whether BlueZ accepts battery levels forwarded by PipeWire (headsets).

        ``None`` when unknown (no adapter). On older BlueZ versions the
        interface only exists when bluetoothd runs with experimental features.
        """
        adapters = self.adapters
        if not adapters:
            return None
        return any(BATTERY_PROVIDER_MANAGER_IFACE in self._objects[p] for p in adapters)

    def device_properties(self, address: str) -> dict | None:
        for path, ifaces in self._objects.items():
            dev = ifaces.get(DEVICE_IFACE)
            if dev and normalize_address(str(dev.get("Address", ""))) == address:
                return dict(dev, _path=path)
        return None

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        self._bus = self._bus or get_system_bus()
        if self._bus is None:
            return
        self._signals = SignalSubscriptions(self._bus)
        self._signals.add(BLUEZ, OBJECT_MANAGER_IFACE, "InterfacesAdded", self._on_interfaces_added)
        self._signals.add(BLUEZ, OBJECT_MANAGER_IFACE, "InterfacesRemoved", self._on_interfaces_removed)
        self._signals.add(BLUEZ, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                          self._on_properties_changed)
        self._watch_id = Gio.bus_watch_name_on_connection(
            self._bus, BLUEZ, Gio.BusNameWatcherFlags.NONE, self._on_appeared, self._on_vanished
        )

    def stop(self) -> None:
        self._cancellable.cancel()
        self._cancellable = Gio.Cancellable()
        if self._watch_id:
            Gio.bus_unwatch_name(self._watch_id)
            self._watch_id = 0
        if self._signals:
            self._signals.clear()
        self._objects.clear()
        self._reports.clear()

    # -- D-Bus plumbing -----------------------------------------------------
    def _on_appeared(self, _conn, _name, _owner) -> None:
        self.available = True
        call(self._bus, BLUEZ, "/", OBJECT_MANAGER_IFACE, "GetManagedObjects", None,
             "(a{oa{sa{sv}}})", self._on_managed_objects, self._cancellable)

    def _on_vanished(self, _conn, _name) -> None:
        self.available = False
        self._objects.clear()
        self._rebuild()

    def _on_managed_objects(self, result, error) -> None:
        if error:
            log.warning("BlueZ GetManagedObjects failed: %s", error.message)
            return
        self._objects = {path: dict(ifaces) for path, ifaces in result[0].items()}
        self._rebuild()

    def _on_interfaces_added(self, _path, _signal, args) -> None:
        path, interfaces = args
        self._objects.setdefault(path, {}).update(interfaces)
        self._rebuild()

    def _on_interfaces_removed(self, _path, _signal, args) -> None:
        path, interfaces = args
        ifaces = self._objects.get(path)
        if ifaces is None:
            return
        for iface in interfaces:
            ifaces.pop(iface, None)
        if not ifaces:
            self._objects.pop(path, None)
        self._rebuild()

    def _on_properties_changed(self, path, _signal, args) -> None:
        iface, changed, invalidated = args
        ifaces = self._objects.get(path)
        if ifaces is None or iface not in ifaces:
            return
        props = ifaces[iface]
        props.update(changed)
        for name in invalidated:
            props.pop(name, None)
        if iface in (DEVICE_IFACE, BATTERY_IFACE, ADAPTER_IFACE):
            self._rebuild()

    def _rebuild(self) -> None:
        reports = {}
        for path, interfaces in self._objects.items():
            report = report_from_objects(path, interfaces)
            if report is not None:
                reports[report.key] = report
        self._reports = reports
        self._changed()


def adapter_of(path: str) -> str:
    """``/org/bluez/hci0/dev_AA_BB…`` -> ``/org/bluez/hci0``."""
    return path.rsplit("/", 1)[0] if "/dev_" in path else path

