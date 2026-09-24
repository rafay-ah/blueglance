"""Detailed AirPods / Beats battery (left, right and case) via Apple's AAP.

Apple accessories speak the "Apple Accessory Protocol" on a BR/EDR L2CAP
channel (PSM 0x1001) next to the normal audio profiles. After a handshake and
a notification request the earbuds push battery frames like::

    04 00 04 00 04 00  03  04 01 64 01 01  02 01 64 01 01  08 01 0C 02 01
    └── header ───┘ op  N   └ left 100 % ┘ └ right 100 % ┘ └ case 12 % ┘

This is a clean-room implementation based on public protocol notes (see the
README credits). No root is needed: connecting an L2CAP socket is allowed for
regular users. Everything runs on the GLib main loop with non-blocking sockets.
"""

from __future__ import annotations

import errno
import logging
import socket

from gi.repository import GLib

from ..models import ChargeState, Component, DeviceKind, Report, headline_level, normalize_address
from . import Provider

log = logging.getLogger(__name__)

AAP_PSM = 0x1001
AAP_SERVICE_UUID = "74ec2172-0bad-4d01-8f77-997b2be0722a"
APPLE_VENDOR_ID = 0x004C

HANDSHAKE = bytes.fromhex("00000400010002000000000000000000")
REQUEST_NOTIFICATIONS = bytes.fromhex("040004000f00ffffffff")
MESSAGE_HEADER = bytes.fromhex("04000400")
OPCODE_BATTERY = 0x0004

COMPONENT_SINGLE = 0x01
COMPONENT_RIGHT = 0x02
COMPONENT_LEFT = 0x04
COMPONENT_CASE = 0x08

STATUS_UNKNOWN = 0x00
STATUS_CHARGING = 0x01
STATUS_DISCHARGING = 0x02
STATUS_DISCONNECTED = 0x04
STATUS_OPTIMIZED_CHARGING = 0x05

# Apple product IDs (Modalias "bluetooth:v004Cp<PID>…") -> (name, kind)
MODELS = {
    0x2002: ("AirPods", DeviceKind.EARBUDS),
    0x200F: ("AirPods (2nd generation)", DeviceKind.EARBUDS),
    0x2013: ("AirPods (3rd generation)", DeviceKind.EARBUDS),
    0x2019: ("AirPods 4", DeviceKind.EARBUDS),
    0x201B: ("AirPods 4 with ANC", DeviceKind.EARBUDS),
    0x200E: ("AirPods Pro", DeviceKind.EARBUDS),
    0x2014: ("AirPods Pro 2", DeviceKind.EARBUDS),
    0x2024: ("AirPods Pro 2 (USB-C)", DeviceKind.EARBUDS),
    0x2027: ("AirPods Pro 3", DeviceKind.EARBUDS),
    0x200A: ("AirPods Max", DeviceKind.HEADPHONES),
    0x201F: ("AirPods Max (USB-C)", DeviceKind.HEADPHONES),
    0x2003: ("Powerbeats3", DeviceKind.EARBUDS),
    0x2005: ("BeatsX", DeviceKind.EARBUDS),
    0x2006: ("Beats Solo3", DeviceKind.HEADPHONES),
    0x2009: ("Beats Studio3", DeviceKind.HEADPHONES),
    0x200B: ("Powerbeats Pro", DeviceKind.EARBUDS),
    0x200C: ("Beats Solo Pro", DeviceKind.HEADPHONES),
    0x200D: ("Powerbeats4", DeviceKind.EARBUDS),
    0x2010: ("Beats Flex", DeviceKind.EARBUDS),
    0x2011: ("Beats Studio Buds", DeviceKind.EARBUDS),
    0x2012: ("Beats Fit Pro", DeviceKind.EARBUDS),
    0x2016: ("Beats Studio Buds+", DeviceKind.EARBUDS),
    0x2017: ("Beats Studio Pro", DeviceKind.HEADPHONES),
    0x201D: ("Powerbeats Pro 2", DeviceKind.EARBUDS),
    0x2025: ("Beats Solo 4", DeviceKind.HEADPHONES),
    0x2026: ("Beats Solo Buds", DeviceKind.EARBUDS),
}

COMPONENT_ORDER = {COMPONENT_LEFT: 0, COMPONENT_RIGHT: 1, COMPONENT_CASE: 2}
COMPONENT_NAMES = {COMPONENT_LEFT: ("left", "Left"), COMPONENT_RIGHT: ("right", "Right"),
                   COMPONENT_CASE: ("case", "Case")}

RETRY_DELAYS = (3, 10, 30, 60, 120)
SEND_RETRIES = 12
SEND_RETRY_MS = 150
RESPONSE_TIMEOUT_MS = 1500
BATTERY_TIMEOUT_MS = 4000


def parse_battery(frame: bytes) -> dict[int, tuple[int | None, int]] | None:
    """Decode an AAP battery frame into ``{component: (level, status)}``.

    Returns ``None`` if ``frame`` isn't a (well-formed) battery frame. Levels
    outside 0..100 (0x7F after closing the case, 0xFF) become ``None``.
    """
    if len(frame) < 7 or frame[:4] != MESSAGE_HEADER:
        return None
    if int.from_bytes(frame[4:6], "little") != OPCODE_BATTERY:
        return None
    count = frame[6]
    if count == 0 or count > 4 or len(frame) < 7 + 5 * count:
        return None
    result = {}
    for index in range(count):
        component, _kind, level, status, _spacer = frame[7 + 5 * index: 12 + 5 * index]
        result[component] = (level if 0 <= level <= 100 else None, status)
    return result


def components_from_battery(battery: dict[int, tuple[int | None, int]]):
    """-> (headline level, charging, components tuple) for a decoded frame."""
    if COMPONENT_SINGLE in battery:
        level, status = battery[COMPONENT_SINGLE]
        return level, status in (STATUS_CHARGING, STATUS_OPTIMIZED_CHARGING), ()
    comps = []
    for component in sorted(battery, key=lambda c: COMPONENT_ORDER.get(c, 9)):
        level, status = battery[component]
        if component not in COMPONENT_NAMES or status == STATUS_DISCONNECTED or level is None:
            continue
        key, label = COMPONENT_NAMES[component]
        comps.append(Component(key, label, level, status in (STATUS_CHARGING, STATUS_OPTIMIZED_CHARGING)))
    comps = tuple(comps)
    charging = any(c.charging for c in comps if c.key != "case")
    return headline_level(comps), charging, comps


def is_apple_audio(props: dict) -> tuple[bool, int | None]:
    """Does this BlueZ ``Device1`` look like AirPods/Beats? Returns (match, product id)."""
    uuids = {str(u).lower() for u in props.get("UUIDs", [])}
    product = None
    modalias = str(props.get("Modalias") or "")
    if modalias.lower().startswith("bluetooth:v004cp") and len(modalias) >= 20:
        try:
            product = int(modalias[16:20], 16)
        except ValueError:
            product = None
    return (AAP_SERVICE_UUID in uuids or product in MODELS), product


class AapConnection:
    """One non-blocking AAP session with a connected accessory."""

    def __init__(self, address: str, on_battery, on_closed):
        self.address = address
        self._on_battery = on_battery
        self._on_closed = on_closed
        self._sock: socket.socket | None = None
        self._watch = 0
        self._timers: list[int] = []
        self._send_attempts = 0
        self._handshake_acked = False
        self._got_battery = False
        self.closed = False

    # -- lifecycle ----------------------------------------------------------
    def _create_socket(self) -> tuple[socket.socket, int]:
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
        sock.setblocking(False)
        return sock, sock.connect_ex((self.address, AAP_PSM))

    def open(self) -> None:
        try:
            self._sock, code = self._create_socket()
        except OSError as exc:
            self._fail(f"socket error: {exc}")
            return
        if code not in (0, errno.EINPROGRESS, errno.EAGAIN):
            self._fail(f"connect: {errno.errorcode.get(code, code)}")
            return
        self._add_watch(GLib.IOCondition.OUT | GLib.IOCondition.ERR | GLib.IOCondition.HUP, self._on_connect_ready)

    def close(self, reason: str = "closed") -> None:
        if self.closed:
            return
        self.closed = True
        if self._watch:
            GLib.source_remove(self._watch)
            self._watch = 0
        for timer in self._timers:
            GLib.source_remove(timer)
        self._timers.clear()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        log.debug("AAP %s: %s", self.address, reason)

    # -- internals ----------------------------------------------------------
    def _fail(self, reason: str) -> None:
        got_data = self._got_battery
        self.close(reason)
        self._on_closed(self, reason, got_data)

    def _add_watch(self, condition, callback) -> None:
        if self._watch:
            GLib.source_remove(self._watch)
        self._watch = GLib.io_add_watch(self._sock.fileno(), GLib.PRIORITY_DEFAULT, condition, callback)

    def _timeout(self, ms: int, callback) -> None:
        def run():
            self._timers.remove(timer)
            callback()
            return GLib.SOURCE_REMOVE

        timer = GLib.timeout_add(ms, run)
        self._timers.append(timer)

    def _on_connect_ready(self, _fd, condition) -> bool:
        self._watch = 0
        error = self._sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) if self._sock else errno.EBADF
        if error or condition & (GLib.IOCondition.ERR | GLib.IOCondition.HUP):
            self._fail(f"connect failed: {errno.errorcode.get(error, error)}")
            return GLib.SOURCE_REMOVE
        self._add_watch(GLib.IOCondition.IN | GLib.IOCondition.ERR | GLib.IOCondition.HUP, self._on_readable)
        self._send_with_retry(HANDSHAKE, self._after_handshake_sent)
        return GLib.SOURCE_REMOVE

    def _send_with_retry(self, payload: bytes, then=None) -> None:
        """BlueZ may report ENOTCONN for a moment right after connecting."""
        if self.closed:
            return
        try:
            self._sock.send(payload)
        except (BlockingIOError, InterruptedError):
            self._retry_send(payload, then)
            return
        except OSError as exc:
            if exc.errno in (errno.ENOTCONN, errno.EAGAIN) and self._send_attempts < SEND_RETRIES:
                self._retry_send(payload, then)
                return
            self._fail(f"send failed: {exc}")
            return
        self._send_attempts = 0
        if then is not None:
            then()

    def _retry_send(self, payload: bytes, then) -> None:
        self._send_attempts += 1
        self._timeout(SEND_RETRY_MS, lambda: self._send_with_retry(payload, then))

    def _after_handshake_sent(self) -> None:
        # Ask for notifications once the accessory acknowledged the handshake,
        # or after a short grace period if the acknowledgement never shows up.
        self._timeout(RESPONSE_TIMEOUT_MS, self._request_notifications)

    def _request_notifications(self) -> None:
        if self.closed:
            return
        self._send_with_retry(REQUEST_NOTIFICATIONS)
        self._timeout(BATTERY_TIMEOUT_MS, self._maybe_repeat_request)

    def _maybe_repeat_request(self) -> None:
        if not self.closed and not self._got_battery:
            self._send_with_retry(REQUEST_NOTIFICATIONS)

    def _on_readable(self, _fd, condition) -> bool:
        if condition & (GLib.IOCondition.ERR | GLib.IOCondition.HUP):
            self._watch = 0
            self._fail("connection closed by accessory")
            return GLib.SOURCE_REMOVE
        try:
            frame = self._sock.recv(2048)
        except (BlockingIOError, InterruptedError):
            return GLib.SOURCE_CONTINUE
        except OSError as exc:
            self._watch = 0
            self._fail(f"recv failed: {exc}")
            return GLib.SOURCE_REMOVE
        if not frame:
            self._watch = 0
            self._fail("connection closed")
            return GLib.SOURCE_REMOVE
        self._handle_frame(frame)
        return GLib.SOURCE_CONTINUE

    def _handle_frame(self, frame: bytes) -> None:
        if frame[:4] == bytes.fromhex("01000400") and not self._handshake_acked:
            self._handshake_acked = True
            # Skip the grace period: request notifications right away.
            for timer in self._timers:
                GLib.source_remove(timer)
            self._timers.clear()
            self._request_notifications()
            return
        battery = parse_battery(frame)
        if battery is not None:
            self._got_battery = True
            self._on_battery(self, battery)


class AirPodsProvider(Provider):
    source = "aap"

    def __init__(self, bluez_provider):
        super().__init__()
        self.bluez = bluez_provider
        self._enabled = True
        self._running = False
        self._handler = 0
        self._connections: dict[str, AapConnection] = {}
        self._targets: dict[str, int | None] = {}  # address -> product id
        self._failures: dict[str, int] = {}
        self._retry_sources: dict[str, int] = {}
        self.supported = hasattr(socket, "AF_BLUETOOTH") and hasattr(socket, "BTPROTO_L2CAP")

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        if value == self._enabled:
            return
        self._enabled = value
        if not value:
            self._teardown_all()
            self._reports.clear()
            self._changed()
        else:
            self._sync()

    def start(self) -> None:
        if not self.supported:
            log.info("Python was built without Bluetooth sockets; detailed AirPods battery disabled")
            return
        self._running = True
        self._handler = self.bluez.connect("changed", lambda *_: self._sync())
        self._sync()

    def stop(self) -> None:
        self._running = False
        if self._handler:
            self.bluez.disconnect(self._handler)
            self._handler = 0
        self._teardown_all()

    def _teardown_all(self) -> None:
        for conn in list(self._connections.values()):
            conn.close("stopped")
        self._connections.clear()
        for source in self._retry_sources.values():
            GLib.source_remove(source)
        self._retry_sources.clear()

    # -- which devices to talk to --------------------------------------------
    def _sync(self) -> None:
        if not (self._running and self._enabled):
            return
        targets = {}
        for props in self.bluez.connected_device_properties():
            match, product = is_apple_audio(props)
            address = normalize_address(str(props.get("Address", "")))
            if match and address:
                targets[address] = product
        self._targets = targets

        for address in list(self._connections):
            if address not in targets:
                self._connections.pop(address).close("device disconnected")
        for address in list(self._retry_sources):
            if address not in targets:
                GLib.source_remove(self._retry_sources.pop(address))
        stale = [key for key in self._reports if key not in targets]
        for key in stale:
            del self._reports[key]
        for address in list(self._failures):
            if address not in targets:
                del self._failures[address]
        if stale:
            self._changed()

        for address in targets:
            if address not in self._connections and address not in self._retry_sources:
                self._connect(address)

    def _connect(self, address: str) -> None:
        log.debug("AAP: connecting to %s", address)
        conn = AapConnection(address, self._on_battery, self._on_closed)
        self._connections[address] = conn
        conn.open()

    def _on_closed(self, conn: AapConnection, reason: str, got_data: bool) -> None:
        if self._connections.get(conn.address) is conn:
            del self._connections[conn.address]
        if conn.address not in self._targets or not self._running or not self._enabled:
            return
        failures = 0 if got_data else self._failures.get(conn.address, 0) + 1
        self._failures[conn.address] = failures
        if failures > len(RETRY_DELAYS):
            log.info("AAP: giving up on %s (%s)", conn.address, reason)
            return
        delay = RETRY_DELAYS[max(0, failures - 1)]
        log.debug("AAP: %s (%s); retrying in %ss", conn.address, reason, delay)

        def retry():
            self._retry_sources.pop(conn.address, None)
            if conn.address in self._targets and conn.address not in self._connections:
                self._connect(conn.address)
            return GLib.SOURCE_REMOVE

        self._retry_sources[conn.address] = GLib.timeout_add_seconds(delay, retry)

    def _on_battery(self, conn: AapConnection, battery) -> None:
        product = self._targets.get(conn.address)
        model, kind = MODELS.get(product, (None, None))
        level, charging, comps = components_from_battery(battery)
        previous = self._reports.get(conn.address)
        if previous is not None and not comps and level is None:
            return
        self._failures[conn.address] = 0
        self._reports[conn.address] = Report(
            source="aap",
            key=conn.address,
            kind=kind,
            level=level,
            state=ChargeState.CHARGING if charging else ChargeState.DISCHARGING,
            connected=True,
            address=conn.address,
            components=comps,
            model=model,
            vendor="Apple",
            priority=30,
            hints={"aap": True},
        )
        self._changed()
