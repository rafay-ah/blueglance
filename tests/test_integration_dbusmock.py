"""UPower and BlueZ backends against python-dbusmock system services."""

import os
import subprocess
import time

import pytest

dbusmock = pytest.importorskip("dbusmock")
dbus = pytest.importorskip("dbus")

from gi.repository import Gio, GLib  # noqa: E402

from blueglance.devices.bluez import BlueZProvider  # noqa: E402
from blueglance.devices.upower import UPowerProvider  # noqa: E402
from blueglance.models import DeviceKind  # noqa: E402

MOUSE_PATH = "/org/freedesktop/UPower/devices/mouse_dev_E4_17_D8_00_11_22"


def wait_for(predicate, timeout=5.0):
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while ctx.iteration(False):
            pass
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def gio_system_bus():
    return Gio.DBusConnection.new_for_address_sync(
        os.environ["DBUS_SYSTEM_BUS_ADDRESS"],
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None, None)


class TestUPower(dbusmock.DBusTestCase):
    @classmethod
    def setUpClass(cls):
        cls.start_system_bus()
        cls.dbus_con = cls.get_dbus(system_bus=True)

    def setUp(self):
        self.p_mock, self.obj = self.spawn_server_template("upower", {}, stdout=subprocess.DEVNULL)
        self.mock = dbus.Interface(self.obj, dbusmock.MOCK_IFACE)

    def tearDown(self):
        self.p_mock.terminate()
        self.p_mock.wait()

    def add_mouse(self, percentage=81.0):
        self.mock.AddObject(MOUSE_PATH, "org.freedesktop.UPower.Device", {
            "NativePath": dbus.String("/org/bluez/hci0/dev_E4_17_D8_00_11_22", variant_level=1),
            "Model": dbus.String("MX Master 3S", variant_level=1),
            "Vendor": dbus.String("", variant_level=1),
            "Serial": dbus.String("E4:17:D8:00:11:22", variant_level=1),
            "Type": dbus.UInt32(5, variant_level=1),
            "PowerSupply": dbus.Boolean(False, variant_level=1),
            "IsPresent": dbus.Boolean(True, variant_level=1),
            "State": dbus.UInt32(0, variant_level=1),
            "Percentage": dbus.Double(percentage, variant_level=1),
            "BatteryLevel": dbus.UInt32(1, variant_level=1),
        }, [])

    def test_enumerate_update_remove(self):
        self.add_mouse()
        provider = UPowerProvider(gio_system_bus())
        provider.start()
        try:
            assert wait_for(lambda: provider.reports)
            [report] = provider.reports
            assert report.key == "E4:17:D8:00:11:22"
            assert report.kind == DeviceKind.MOUSE
            assert report.level == 81

            self.mock.SetDeviceProperties(MOUSE_PATH, {"Percentage": dbus.Double(42.0, variant_level=1)})
            assert wait_for(lambda: provider.reports and provider.reports[0].level == 42)

            self.mock.RemoveDevice(MOUSE_PATH)
            assert wait_for(lambda: not provider.reports)

            self.add_mouse(55.0)
            self.mock.EmitSignal("org.freedesktop.UPower", "DeviceAdded", "o", [MOUSE_PATH])
            assert wait_for(lambda: provider.reports and provider.reports[0].level == 55)
        finally:
            provider.stop()

    def test_system_battery_is_opt_in(self):
        path = self.mock.AddDischargingBattery("mock_BAT", "Mock Battery", 30.0, 1200)
        provider = UPowerProvider(gio_system_bus())
        provider.start()
        try:
            wait_for(lambda: False, timeout=0.5)
            assert provider.reports == []
            provider.include_system = True
            assert wait_for(lambda: provider.reports)
            assert provider.reports[0].kind == DeviceKind.COMPUTER
            assert provider.reports[0].level == 30
            assert path
        finally:
            provider.stop()


class TestBlueZ(dbusmock.DBusTestCase):
    @classmethod
    def setUpClass(cls):
        cls.start_system_bus()
        cls.dbus_con = cls.get_dbus(system_bus=True)

    def setUp(self):
        self.p_mock, self.obj = self.spawn_server_template("bluez5", {}, stdout=subprocess.DEVNULL)
        self.mock = dbus.Interface(self.obj, "org.bluez.Mock")

    def tearDown(self):
        self.p_mock.terminate()
        self.p_mock.wait()

    def test_connected_device_with_battery(self):
        self.mock.AddAdapter("hci0", "my-computer")
        path = self.mock.AddDevice("hci0", "AC:90:85:12:34:56", "AirPods Pro")
        device = self.dbus_con.get_object("org.bluez", path)
        device_mock = dbus.Interface(device, dbusmock.MOCK_IFACE)
        device_mock.UpdateProperties("org.bluez.Device1", {
            "Icon": dbus.String("audio-headphones", variant_level=1),
            "Modalias": dbus.String("bluetooth:v004Cp2014d0E26", variant_level=1),
            "UUIDs": dbus.Array(["0000111e-0000-1000-8000-00805f9b34fb"], signature="s", variant_level=1),
        })
        device_mock.AddProperties("org.bluez.Battery1", {"Percentage": dbus.Byte(70, variant_level=1)})
        self.mock.ConnectDevice("hci0", "AC:90:85:12:34:56")

        provider = BlueZProvider(gio_system_bus())
        provider.start()
        try:
            assert wait_for(lambda: provider.reports)
            [report] = provider.reports
            assert report.name == "AirPods Pro"
            assert report.connected is True
            assert report.level == 70
            assert report.kind == DeviceKind.EARBUDS
            assert report.hints["apple"] and report.hints["handsfree"]
            assert provider.adapters == ["/org/bluez/hci0"]
            assert provider.powered is True
            # The mock adapter has no BatteryProviderManager1 (like BlueZ < 5.71
            # without Experimental = true).
            assert provider.battery_provider_supported is False
            [props] = provider.connected_device_properties()
            assert props["Address"] == "AC:90:85:12:34:56"

            device_mock.UpdateProperties("org.bluez.Battery1", {"Percentage": dbus.Byte(64, variant_level=1)})
            assert wait_for(lambda: provider.reports and provider.reports[0].level == 64)

            self.mock.DisconnectDevice("hci0", "AC:90:85:12:34:56")
            assert wait_for(lambda: provider.reports and provider.reports[0].connected is False)
        finally:
            provider.stop()
