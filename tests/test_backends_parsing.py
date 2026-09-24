from blueglance.devices.bluez import (
    BATTERY_IFACE,
    DEVICE_IFACE,
    guess_kind,
    kind_from_class,
    parse_modalias,
    report_from_objects,
)
from blueglance.devices.upower import report_from_properties
from blueglance.models import ChargeState, DeviceKind


def upower_props(**overrides):
    props = {
        "NativePath": "/org/bluez/hci0/dev_E4_17_D8_00_11_22",
        "Vendor": "",
        "Model": "MX Master 3S",
        "Serial": "e4:17:d8:00:11:22",
        "Type": 5,
        "PowerSupply": False,
        "IsPresent": True,
        "State": 2,
        "Percentage": 81.0,
        "BatteryLevel": 1,
    }
    props.update(overrides)
    return props


def test_upower_bluetooth_mouse():
    report = report_from_properties("/org/freedesktop/UPower/devices/mouse_dev_E4", upower_props())
    assert report.key == "E4:17:D8:00:11:22"
    assert report.kind == DeviceKind.MOUSE
    assert report.level == 81
    assert report.state == ChargeState.DISCHARGING
    assert report.name == "MX Master 3S"


def test_upower_hid_battery_uses_native_path_address():
    report = report_from_properties(
        "/org/freedesktop/UPower/devices/keyboard_hid",
        upower_props(NativePath="hid-dc:2c:26:aa:bb:cc-battery", Serial="", Type=6, Model="K380"),
    )
    assert report.key == "DC:2C:26:AA:BB:CC"
    assert report.kind == DeviceKind.KEYBOARD


def test_upower_receiver_device_without_mac_keys_on_serial():
    report = report_from_properties(
        "/p", upower_props(NativePath="hidpp_battery_3", Serial="4082-1a2b3c4d", Model="MX Keys")
    )
    assert report.key == "upower:4082-1a2b3c4d"
    assert report.address is None


def test_upower_coarse_level():
    report = report_from_properties("/p", upower_props(Percentage=0.0, BatteryLevel=7, State=0))
    assert report.coarse
    assert report.level == 70


def test_upower_unreported_hid_battery_has_no_level():
    report = report_from_properties("/p", upower_props(Percentage=0.0, State=0))
    assert report.level is None


def test_upower_skips_line_power_and_system_battery_by_default():
    assert report_from_properties("/p", upower_props(Type=1)) is None
    system = upower_props(Type=2, PowerSupply=True, NativePath="BAT0", Serial="", Model="5B10W13930")
    assert report_from_properties("/p", system) is None
    report = report_from_properties("/p", system, include_system=True)
    assert report.kind == DeviceKind.COMPUTER
    assert report.name == "This computer"


def bluez_objects(**dev_overrides):
    dev = {
        "Address": "AC:90:85:12:34:56",
        "Alias": "Rafay's AirPods Pro",
        "Name": "AirPods Pro",
        "Icon": "audio-headphones",
        "Class": 0x240418,
        "Connected": True,
        "Paired": True,
        "UUIDs": ["0000110b-0000-1000-8000-00805f9b34fb", "0000111e-0000-1000-8000-00805f9b34fb"],
        "Modalias": "bluetooth:v004Cp2014d0E26",
    }
    dev.update(dev_overrides)
    return {DEVICE_IFACE: dev}


def test_bluez_airpods_without_battery():
    report = report_from_objects("/org/bluez/hci0/dev_AC_90_85_12_34_56", bluez_objects())
    assert report.key == "AC:90:85:12:34:56"
    assert report.kind == DeviceKind.EARBUDS
    assert report.level is None
    assert report.hints["apple"] and report.hints["audio"] and report.hints["handsfree"]
    assert report.hints["product_id"] == 0x2014


def test_bluez_battery1_level():
    objects = bluez_objects()
    objects[BATTERY_IFACE] = {"Percentage": 70, "Source": "HFP 1.7"}
    report = report_from_objects("/org/bluez/hci0/dev_AC_90_85_12_34_56", objects)
    assert report.level == 70
    assert report.hints["battery_source"] == "HFP 1.7"


def test_bluez_ignores_disconnected_devices_without_battery():
    assert report_from_objects("/x", bluez_objects(Connected=False)) is None


def test_modalias_and_class_parsing():
    assert parse_modalias("bluetooth:v004Cp2014d0E26") == ("bluetooth", 0x004C, 0x2014)
    assert parse_modalias("usb:v046DpB023d0012") == ("usb", 0x046D, 0xB023)
    assert parse_modalias("garbage") is None
    assert kind_from_class(0x002580) == DeviceKind.MOUSE  # peripheral, pointing
    assert kind_from_class(0x002540) == DeviceKind.KEYBOARD
    assert kind_from_class(0x002508) == DeviceKind.GAMEPAD
    assert kind_from_class(0x240404) == DeviceKind.HEADSET
    assert kind_from_class(0x240418) == DeviceKind.HEADPHONES
    assert kind_from_class(0x240414) == DeviceKind.SPEAKER


def test_guess_kind_prefers_name_for_audio_subtypes():
    assert guess_kind({"Alias": "AirPods Max", "Icon": "audio-headphones"}) == DeviceKind.HEADPHONES
    assert guess_kind({"Alias": "Galaxy Buds2 Pro", "Icon": "audio-headset"}) == DeviceKind.EARBUDS
    assert guess_kind({"Alias": "WH-1000XM5", "Icon": "audio-headset"}) == DeviceKind.HEADSET
    assert guess_kind({"Alias": "Xbox Wireless Controller", "Icon": "input-gaming"}) == DeviceKind.GAMEPAD
    # A name must not turn a mouse into earbuds just because it contains "buds".
    assert guess_kind({"Alias": "Logitech Pebble", "Icon": "input-mouse"}) == DeviceKind.MOUSE
    assert guess_kind({"Alias": "Keychron K3", "Appearance": 0x03C1}) == DeviceKind.KEYBOARD
    assert guess_kind({"Alias": "Thing"}) == DeviceKind.OTHER
