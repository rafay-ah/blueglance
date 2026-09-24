from blueglance.devices.manager import merge_reports
from blueglance.models import (
    ChargeState,
    Component,
    Device,
    DeviceKind,
    LevelClass,
    Report,
    classify_level,
    format_relative_time,
    headline_level,
    normalize_address,
)


def test_normalize_address_variants():
    assert normalize_address("aa:bb:cc:dd:ee:ff") == "AA:BB:CC:DD:EE:FF"
    assert normalize_address("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_0F") == "AA:BB:CC:DD:EE:0F"
    assert normalize_address("hid-a1:b2:c3:d4:e5:f6-battery") == "A1:B2:C3:D4:E5:F6"
    assert normalize_address("hidpp_battery_0") is None
    assert normalize_address("") is None
    assert normalize_address(None) is None


def test_classify_level():
    assert classify_level(None, 20) == LevelClass.UNKNOWN
    assert classify_level(5, 20) == LevelClass.CRITICAL
    assert classify_level(10, 20) == LevelClass.CRITICAL
    assert classify_level(19, 20) == LevelClass.LOW
    assert classify_level(20, 20) == LevelClass.LOW
    assert classify_level(21, 20) == LevelClass.NORMAL
    # A threshold below the critical level caps "critical" at the threshold.
    assert classify_level(7, 5) == LevelClass.NORMAL


def test_headline_level_ignores_case():
    comps = (Component("left", "Left", 80), Component("right", "Right", 60), Component("case", "Case", 5))
    assert headline_level(comps) == 60
    assert headline_level((Component("case", "Case", 40),)) == 40
    assert headline_level(()) is None


def test_merge_prefers_bluez_name_and_highest_priority_level():
    reports = [
        Report(source="upower", key="AA:BB:CC:DD:EE:FF", name="MX Master 3S (UPower)", kind=DeviceKind.MOUSE,
               level=70, state=ChargeState.DISCHARGING, connected=True, priority=20),
        Report(source="bluez", key="AA:BB:CC:DD:EE:FF", name="My Mouse", kind=DeviceKind.MOUSE,
               level=68, connected=True, priority=10),
    ]
    [device] = merge_reports(reports, now=1000)
    assert device.name == "My Mouse"
    assert device.level == 70
    assert device.state == ChargeState.DISCHARGING
    assert device.sources == ("bluez", "upower")


def test_merge_aap_components_win_and_kind_is_refined():
    comps = (Component("left", "Left", 90), Component("right", "Right", 85), Component("case", "Case", 40, True))
    reports = [
        Report(source="bluez", key="11:22:33:44:55:66", name="AirPods Pro", kind=DeviceKind.HEADPHONES,
               level=80, connected=True, priority=10),
        Report(source="aap", key="11:22:33:44:55:66", kind=DeviceKind.EARBUDS, level=85,
               components=comps, priority=30, model="AirPods Pro 2"),
    ]
    [device] = merge_reports(reports)
    assert device.kind == DeviceKind.EARBUDS
    assert device.level == 85
    assert device.component("case").charging
    assert not device.charging  # only the case charges
    assert device.model == "AirPods Pro 2"


def test_merge_drops_idle_paired_devices_without_battery():
    reports = [
        Report(source="bluez", key="00:11:22:33:44:55", name="Old speaker", connected=False),
        Report(source="bluez", key="00:11:22:33:44:66", name="Speaker", connected=True),
    ]
    devices = merge_reports(reports)
    assert [d.name for d in devices] == ["Speaker"]
    assert not devices[0].has_battery


def test_bluez_disconnect_overrides_upower_presence():
    reports = [
        Report(source="upower", key="K", name="Kbd", level=50, connected=True, priority=20),
        Report(source="bluez", key="K", name="Kbd", connected=False, priority=10),
    ]
    [device] = merge_reports(reports)
    assert not device.connected


def test_relative_time():
    assert format_relative_time(1000, now=1030) == "just now"
    assert format_relative_time(1000, now=1000 + 5 * 60) == "5 min ago"
    assert format_relative_time(1000, now=1000 + 3 * 3600) == "3 h ago"
    assert format_relative_time(1000, now=1000 + 30 * 3600) == "yesterday"
    assert format_relative_time(1000, now=1000 + 80 * 3600) == "3 days ago"


def test_device_round_trips_through_history():
    device = Device(id="AC:90:85:12:34:56", name="AirPods Pro", kind=DeviceKind.EARBUDS, level=40,
                    connected=False, address="AC:90:85:12:34:56",
                    components=(Component("left", "Left", 40), Component("case", "Case", 90, charging=True)),
                    last_seen=1234)
    restored = Device.from_dict(device.to_dict())
    assert restored.address == device.address
    assert restored.components == device.components
    assert (restored.name, restored.kind, restored.level) == ("AirPods Pro", DeviceKind.EARBUDS, 40)
    assert restored.last_seen == 1234
