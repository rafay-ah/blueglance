from blueglance.models import ChargeState, Device, DeviceKind
from blueglance.services.notifier import BatteryAlerts


def dev(level, state=ChargeState.DISCHARGING, connected=True, id="m"):
    return Device(id=id, name="Mouse", kind=DeviceKind.MOUSE, level=level, state=state, connected=connected)


def kinds(events):
    return [k for k, _ in events]


def test_low_then_critical_then_recovered_by_charging():
    alerts = BatteryAlerts()
    assert kinds(alerts.evaluate([dev(50)], 20, True, False)) == []
    assert kinds(alerts.evaluate([dev(19)], 20, True, False)) == ["low"]
    assert kinds(alerts.evaluate([dev(18)], 20, True, False)) == []  # no repeat
    assert kinds(alerts.evaluate([dev(9)], 20, True, False)) == ["critical"]
    assert kinds(alerts.evaluate([dev(9, ChargeState.CHARGING)], 20, True, False)) == ["recovered"]
    assert kinds(alerts.evaluate([dev(15)], 20, True, False)) == ["low"]


def test_hysteresis_before_rearming():
    alerts = BatteryAlerts()
    alerts.evaluate([dev(20)], 20, True, False)
    assert kinds(alerts.evaluate([dev(22)], 20, True, False)) == []  # still within hysteresis
    assert kinds(alerts.evaluate([dev(20)], 20, True, False)) == []
    assert kinds(alerts.evaluate([dev(26)], 20, True, False)) == ["recovered"]
    assert kinds(alerts.evaluate([dev(20)], 20, True, False)) == ["low"]


def test_low_alerts_can_be_disabled():
    alerts = BatteryAlerts()
    assert kinds(alerts.evaluate([dev(5)], 20, False, False)) == []


def test_full_only_after_charging_was_seen():
    alerts = BatteryAlerts()
    # A device that simply connects at 100 % is reported "fully charged" by UPower.
    assert kinds(alerts.evaluate([dev(100, ChargeState.FULL)], 20, True, True)) == []
    alerts = BatteryAlerts()
    alerts.evaluate([dev(97, ChargeState.CHARGING)], 20, True, True)
    assert kinds(alerts.evaluate([dev(100, ChargeState.FULL)], 20, True, True)) == ["full"]
    assert kinds(alerts.evaluate([dev(100, ChargeState.FULL)], 20, True, True)) == []


def test_disconnected_devices_are_ignored_and_forgotten():
    alerts = BatteryAlerts()
    alerts.evaluate([dev(10)], 20, True, False)
    assert kinds(alerts.evaluate([dev(10, connected=False)], 20, True, False)) == []
    assert "m" not in alerts.stage
