#!/usr/bin/env python3
"""Run BlueGlance against mocked UPower and BlueZ services (no Bluetooth needed).

Starts a private system bus with python-dbusmock's upower and bluez5 templates,
populates a few devices, then launches the app in real (non-demo) mode:

    dbus-run-session -- python3 scripts/mock-system.py [-- blueglance args]

Needs python3-dbusmock. Everything runs on private buses; nothing touches the
real system.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import dbus
import dbusmock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def start_system_bus() -> subprocess.Popen:
    proc = subprocess.Popen(
        ["dbus-daemon", "--session", "--nofork", "--print-address"],
        stdout=subprocess.PIPE, text=True,
    )
    address = proc.stdout.readline().strip()
    os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = address
    return proc


def spawn(template: str) -> subprocess.Popen:
    proc = subprocess.Popen([sys.executable, "-m", "dbusmock", "--template", template],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    bus = dbus.bus.BusConnection(os.environ["DBUS_SYSTEM_BUS_ADDRESS"])
    name = {"upower": "org.freedesktop.UPower", "bluez5": "org.bluez"}[template]
    for _ in range(100):
        if bus.name_has_owner(name):
            break
        time.sleep(0.05)
    return proc


def v(kind, value):
    return kind(value, variant_level=1)


def populate():
    bus = dbus.bus.BusConnection(os.environ["DBUS_SYSTEM_BUS_ADDRESS"])

    upower = dbus.Interface(bus.get_object("org.freedesktop.UPower", "/org/freedesktop/UPower"),
                            dbusmock.MOCK_IFACE)

    def upower_device(path, native, model, serial, kind, percentage, state=2, level=1):
        upower.AddObject(path, "org.freedesktop.UPower.Device", {
            "NativePath": v(dbus.String, native), "Model": v(dbus.String, model), "Vendor": v(dbus.String, ""),
            "Serial": v(dbus.String, serial), "Type": v(dbus.UInt32, kind), "PowerSupply": v(dbus.Boolean, False),
            "IsPresent": v(dbus.Boolean, True), "State": v(dbus.UInt32, state),
            "Percentage": v(dbus.Double, percentage), "BatteryLevel": v(dbus.UInt32, level),
        }, [])

    upower_device("/org/freedesktop/UPower/devices/mouse_dev_E4_17_D8_00_11_22",
                  "/org/bluez/hci0/dev_E4_17_D8_00_11_22", "MX Master 3S", "E4:17:D8:00:11:22", 5, 64.0)
    upower_device("/org/freedesktop/UPower/devices/keyboard_hidpp_battery_0",
                  "hidpp_battery_0", "MX Keys", "4082-7e9f11aa", 6, 0.0, state=0, level=7)
    upower_device("/org/freedesktop/UPower/devices/gaming_input_ps_controller_battery_a0_ab_51_10_20_30",
                  "ps-controller-battery-a0:ab:51:10:20:30", "DualSense Wireless Controller", "", 12, 12.0)

    bluez = dbus.Interface(bus.get_object("org.bluez", "/"), "org.bluez.Mock")
    bluez.AddAdapter("hci0", "laptop")

    def bluez_device(address, alias, props, battery=None):
        path = bluez.AddDevice("hci0", address, alias)
        mock = dbus.Interface(bus.get_object("org.bluez", path), dbusmock.MOCK_IFACE)
        mock.UpdateProperties("org.bluez.Device1", props)
        if battery is not None:
            mock.AddProperties("org.bluez.Battery1", {"Percentage": v(dbus.Byte, battery)})
        bluez.ConnectDevice("hci0", address)

    bluez_device("E4:17:D8:00:11:22", "MX Master 3S", {"Icon": v(dbus.String, "input-mouse")}, battery=64)
    bluez_device("A0:AB:51:10:20:30", "DualSense Wireless Controller", {"Icon": v(dbus.String, "input-gaming")})
    bluez_device("AC:90:85:12:34:56", "Rafay's AirPods Pro", {
        "Icon": v(dbus.String, "audio-headphones"),
        "Modalias": v(dbus.String, "bluetooth:v004Cp2014d0E26"),
        "UUIDs": dbus.Array(["0000110b-0000-1000-8000-00805f9b34fb", "0000111e-0000-1000-8000-00805f9b34fb",
                             "74ec2172-0bad-4d01-8f77-997b2be0722a"], signature="s", variant_level=1),
    }, battery=80)
    bluez_device("00:1B:66:AA:BB:CC", "WH-1000XM5", {
        "Icon": v(dbus.String, "audio-headset"),
        "UUIDs": dbus.Array(["0000110b-0000-1000-8000-00805f9b34fb", "0000111e-0000-1000-8000-00805f9b34fb"],
                            signature="s", variant_level=1),
    })


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--":
        args = args[1:]
    system_bus = start_system_bus()
    mocks = [spawn("upower"), spawn("bluez5")]
    try:
        populate()
        env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"))
        return subprocess.call([sys.executable, "-m", "blueglance", *args], env=env)
    finally:
        for proc in mocks:
            proc.terminate()
        system_bus.terminate()


if __name__ == "__main__":
    sys.exit(main())
