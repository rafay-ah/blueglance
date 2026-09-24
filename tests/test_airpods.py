from blueglance.devices.airpods import (
    STATUS_CHARGING,
    STATUS_DISCHARGING,
    STATUS_DISCONNECTED,
    components_from_battery,
    is_apple_audio,
    parse_battery,
)


def frame(hexstr):
    return bytes.fromhex(hexstr.replace(" ", ""))


def test_parse_pro_capture_all_charging():
    battery = parse_battery(frame("04 00 04 00 04 00 03 04 01 64 01 01 02 01 64 01 01 08 01 0C 02 01"))
    assert battery == {0x04: (100, STATUS_CHARGING), 0x02: (100, STATUS_CHARGING), 0x08: (12, STATUS_DISCHARGING)}
    level, charging, comps = components_from_battery(battery)
    assert level == 100 and charging
    assert [(c.key, c.level, c.charging) for c in comps] == [
        ("left", 100, True), ("right", 100, True), ("case", 12, False)]


def test_parse_right_first_order_and_optimized_charging():
    battery = parse_battery(frame("04 00 04 00 04 00 03 02 01 64 02 01 04 01 63 01 01 08 01 11 02 01"))
    level, charging, comps = components_from_battery(battery)
    assert [(c.key, c.level) for c in comps] == [("left", 99), ("right", 100), ("case", 17)]
    assert level == 99 and charging  # left bud charging

    pro3 = parse_battery(frame("04 00 04 00 04 00 03 04 01 50 05 01 02 01 4F 05 01 08 01 30 01 01"))
    level, charging, comps = components_from_battery(pro3)
    assert level == 79 and charging
    assert comps[2].charging


def test_disconnected_and_invalid_levels_are_dropped():
    battery = parse_battery(frame("04 00 04 00 04 00 03 04 01 3C 02 01 02 01 40 02 01 08 01 00 04 01"))
    assert battery[0x08] == (0, STATUS_DISCONNECTED)
    _level, _charging, comps = components_from_battery(battery)
    assert [c.key for c in comps] == ["left", "right"]
    closed_case = parse_battery(frame("04 00 04 00 04 00 01 08 01 7F 02 01"))
    assert closed_case[0x08] == (None, STATUS_DISCHARGING)


def test_single_component_headphones():
    battery = parse_battery(frame("04 00 04 00 04 00 01 01 01 2A 02 01"))
    level, charging, comps = components_from_battery(battery)
    assert level == 42 and not charging and comps == ()


def test_rejects_other_frames():
    assert parse_battery(frame("01 00 04 00 00 00 01 00 02 00 05 00 49 4E 05 00 A5 4F")) is None
    assert parse_battery(frame("04 00 04 00 06 00 00 01")) is None  # ear detection
    assert parse_battery(frame("04 00 04 00 04 00 03 04 01 64")) is None  # truncated
    assert parse_battery(b"") is None


def test_is_apple_audio():
    assert is_apple_audio({"Modalias": "bluetooth:v004Cp2014d0E26"}) == (True, 0x2014)
    assert is_apple_audio({"UUIDs": ["74ec2172-0bad-4d01-8f77-997b2be0722a"]}) == (True, None)
    # An Apple keyboard is not an AAP audio accessory.
    assert is_apple_audio({"Modalias": "bluetooth:v004Cp0267d0001"}) == (False, 0x0267)
    assert is_apple_audio({"Modalias": "bluetooth:v046DpB023d0012"}) == (False, None)


def test_connection_state_machine_over_a_socketpair():
    """Drive AapConnection against a fake accessory on a local SEQPACKET pair."""
    import socket

    from gi.repository import GLib

    from blueglance.devices import airpods

    ours, accessory = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    ours.setblocking(False)
    received = []
    closed = []
    loop = GLib.MainLoop()

    class FakeConnection(airpods.AapConnection):
        def _create_socket(self):
            return ours, 0

    def on_battery(_conn, battery):
        received.append(battery)
        loop.quit()

    conn = FakeConnection("AA:BB:CC:DD:EE:FF", on_battery, lambda *a: closed.append(a))
    conn.open()

    def accessory_io(_fd, _cond):
        data = accessory.recv(1024)
        if data == airpods.HANDSHAKE:
            accessory.send(bytes.fromhex("01000400000001000200050049 4E 0500A54F".replace(" ", "")))
        elif data == airpods.REQUEST_NOTIFICATIONS:
            accessory.send(bytes.fromhex("040004000400020401550201020157020 1".replace(" ", "")))
        return GLib.SOURCE_CONTINUE

    GLib.io_add_watch(accessory.fileno(), GLib.PRIORITY_DEFAULT, GLib.IOCondition.IN, accessory_io)
    GLib.timeout_add(3000, loop.quit)
    loop.run()
    conn.close()
    accessory.close()
    assert received == [{0x04: (0x55, 0x02), 0x02: (0x57, 0x02)}]
    assert closed == []


def test_gives_up_after_retries_and_ignores_bluez_churn(monkeypatch):
    """A refusing accessory is retried with backoff, then left alone until it reconnects."""
    import socket
    import time

    from gi.repository import GLib, GObject

    from blueglance.devices import airpods
    from blueglance.models import Report

    monkeypatch.setattr(airpods, "RETRY_DELAYS", (0, 0, 0))
    attempts = []

    class RefusedConnection(airpods.AapConnection):
        def _create_socket(self):
            attempts.append(self.address)
            ours, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            peer.close()  # hang up like an accessory that refuses AAP
            ours.setblocking(False)
            return ours, 0

    monkeypatch.setattr(airpods, "AapConnection", RefusedConnection)
    address = "AC:90:85:12:34:56"
    connected = [{"Address": address, "Modalias": "bluetooth:v004Cp2014d0E26", "UUIDs": []}]

    class FakeBlueZ(GObject.Object):
        __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

        def connected_device_properties(self):
            return connected

    def spin(seconds, until=lambda: False):
        ctx = GLib.MainContext.default()
        end = time.monotonic() + seconds
        while time.monotonic() < end and not until():
            while ctx.iteration(False):
                pass
            time.sleep(0.005)

    bluez = FakeBlueZ()
    provider = airpods.AirPodsProvider(bluez)
    provider.supported = True  # the fake sockets don't need Bluetooth support in Python
    provider._reports[address] = Report(source="aap", key=address, level=50)  # stale data from a past session
    provider.start()
    try:
        spin(10, until=lambda: address in provider._given_up)
        assert len(attempts) == 4  # first try + one per retry delay
        assert provider.reports == []  # stale levels are dropped, not shown forever

        for _ in range(5):  # e.g. RSSI updates while Bluetooth settings scans
            bluez.emit("changed")
            spin(0.05)
        assert len(attempts) == 4

        connected.clear()  # the buds disconnect and come back: try again
        bluez.emit("changed")
        spin(0.05)
        connected.append({"Address": address, "Modalias": "bluetooth:v004Cp2014d0E26", "UUIDs": []})
        bluez.emit("changed")
        spin(0.2)
        assert len(attempts) == 5
    finally:
        provider.stop()
