"""Merges reports from all providers into one list of devices."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import replace
from pathlib import Path

from gi.repository import GLib, GObject

from ..models import ChargeState, Device, DeviceKind, Report

log = logging.getLogger(__name__)

# Which source's opinion wins, per attribute (first match wins).
NAME_ORDER = ("demo", "bluez", "upower", "aap")
KIND_ORDER = ("demo", "aap", "bluez", "upower")

HISTORY_MAX_AGE = 14 * 24 * 3600
HISTORY_MAX_ITEMS = 40


def _ordered(group: list[Report], order: tuple[str, ...]) -> list[Report]:
    rank = {source: index for index, source in enumerate(order)}
    return sorted(group, key=lambda r: rank.get(r.source, len(order)))


def merge_group(key: str, group: list[Report], now: float | None = None) -> Device:
    """Merge every report about one physical device into a :class:`Device`."""
    now = time.time() if now is None else now
    by_priority = sorted(group, key=lambda r: r.priority, reverse=True)

    name = next((r.name for r in _ordered(group, NAME_ORDER) if r.name), None) or "Unknown device"

    kind = DeviceKind.OTHER
    for report in _ordered(group, KIND_ORDER):
        if report.kind not in (None, DeviceKind.OTHER):
            kind = report.kind
            break

    level_report = next((r for r in by_priority if r.level is not None), None)
    level = level_report.level if level_report else None
    coarse = level_report.coarse if level_report else False

    state = ChargeState.UNKNOWN
    if level_report and level_report.state != ChargeState.UNKNOWN:
        state = level_report.state
    else:
        state = next((r.state for r in by_priority if r.state != ChargeState.UNKNOWN), ChargeState.UNKNOWN)

    components = next((r.components for r in by_priority if r.components), ())

    bluez = next((r for r in group if r.source == "bluez" and r.connected is not None), None)
    if bluez is not None:
        connected = bool(bluez.connected)
    else:
        flags = [r.connected for r in group if r.connected is not None]
        connected = any(flags) if flags else True

    hints: dict = {}
    for report in group:
        hints.update(report.hints)

    return Device(
        id=key,
        name=name,
        kind=kind,
        level=level,
        state=state,
        connected=connected,
        address=next((r.address for r in group if r.address), None),
        components=components,
        model=next((r.model for r in by_priority if r.model), None),
        vendor=next((r.vendor for r in by_priority if r.vendor), None),
        coarse=coarse,
        sources=tuple(sorted({r.source for r in group})),
        hints=hints,
        last_seen=now,
    )


def merge_reports(reports: list[Report], now: float | None = None) -> list[Device]:
    groups: dict[str, list[Report]] = {}
    for report in reports:
        groups.setdefault(report.key, []).append(report)
    devices = [merge_group(key, group, now) for key, group in groups.items()]
    # A paired-but-idle Bluetooth device without any battery data is just noise.
    return [d for d in devices if d.connected or d.has_battery]


class DeviceManager(GObject.Object):
    """Owns the providers and exposes the merged, sorted device list.

    ``changed`` is emitted (at most every ~80 ms) whenever anything visible
    about the device list changes.
    """

    __gtype_name__ = "BlueGlanceDeviceManager"
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, providers, *, history_path: Path | None = None, remember: bool = True):
        super().__init__()
        self.providers = list(providers)
        self._devices: list[Device] = []
        self._fingerprint = None
        self._history: dict[str, Device] = {}
        self._history_path = history_path
        self._remember = remember
        self._update_source = 0
        self._save_source = 0
        self._handlers = []
        if remember and history_path:
            self._load_history()

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        for provider in self.providers:
            self._handlers.append((provider, provider.connect("changed", self._on_provider_changed)))
            try:
                provider.start()
            except Exception:  # a broken backend must never take the app down
                log.exception("Failed to start %s provider", provider.source)
        self._recompute()

    def stop(self) -> None:
        for provider, handler in self._handlers:
            provider.disconnect(handler)
        self._handlers.clear()
        for provider in self.providers:
            try:
                provider.stop()
            except Exception:
                log.exception("Failed to stop %s provider", provider.source)
        if self._update_source:
            GLib.source_remove(self._update_source)
            self._update_source = 0
        self._flush_history()

    def get_provider(self, source: str):
        return next((p for p in self.providers if p.source == source), None)

    # -- data ---------------------------------------------------------------
    @property
    def devices(self) -> list[Device]:
        """Every device worth showing: connected ones and remembered ones."""
        return list(self._devices)

    def connected_with_battery(self) -> list[Device]:
        return [d for d in self._devices if d.connected and d.has_battery]

    def find(self, device_id: str) -> Device | None:
        return next((d for d in self._devices if d.id == device_id), None)

    def forget(self, device_id: str) -> None:
        if self._history.pop(device_id, None) is not None:
            self._schedule_history_save()
            self._recompute()

    # -- internals ----------------------------------------------------------
    def _on_provider_changed(self, _provider) -> None:
        if not self._update_source:
            self._update_source = GLib.timeout_add(80, self._on_update_timeout)

    def _on_update_timeout(self) -> bool:
        self._update_source = 0
        self._recompute()
        return GLib.SOURCE_REMOVE

    def _recompute(self) -> None:
        now = time.time()
        reports = [r for p in self.providers for r in p.reports]
        live = merge_reports(reports, now)
        live_ids = {d.id for d in live if d.connected}

        if self._remember:
            history_changed = False
            for device in live:
                if device.connected and device.has_battery:
                    self._history[device.id] = device
                    history_changed = True
            if history_changed:
                self._schedule_history_save()

        devices = [d for d in live if d.connected or d.id not in self._history]
        for device_id, known in self._history.items():
            if device_id in live_ids or now - known.last_seen > HISTORY_MAX_AGE:
                continue
            devices.append(replace(known, connected=False, state=ChargeState.UNKNOWN))

        devices.sort(key=lambda d: d.sort_key)
        fingerprint = [self._fingerprint_of(d) for d in devices]
        self._devices = devices
        if fingerprint != self._fingerprint:
            self._fingerprint = fingerprint
            self.emit("changed")

    @staticmethod
    def _fingerprint_of(device: Device):
        data = device.to_dict()
        if device.connected:
            data.pop("lastSeen")
        data["hints"] = sorted(device.hints.items())
        return json.dumps(data, sort_keys=True, default=str)

    # -- history persistence -------------------------------------------------
    def _load_history(self) -> None:
        try:
            raw = json.loads(self._history_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.warning("Ignoring unreadable device history: %s", exc)
            return
        now = time.time()
        for item in raw.get("devices", []) if isinstance(raw, dict) else []:
            try:
                device = Device.from_dict(item)
            except (KeyError, TypeError, ValueError):
                continue
            if device.has_battery and now - device.last_seen <= HISTORY_MAX_AGE:
                self._history[device.id] = replace(device, connected=False)

    def _schedule_history_save(self) -> None:
        if self._history_path and not self._save_source:
            self._save_source = GLib.timeout_add_seconds(5, self._on_save_timeout)

    def _on_save_timeout(self) -> bool:
        self._save_source = 0
        self._flush_history()
        return GLib.SOURCE_REMOVE

    def _flush_history(self) -> None:
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = 0
        if not (self._remember and self._history_path):
            return
        items = sorted(self._history.values(), key=lambda d: d.last_seen, reverse=True)
        payload = {"version": 1, "devices": [d.to_dict() for d in items[:HISTORY_MAX_ITEMS]]}
        try:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._history_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
            os.replace(tmp, self._history_path)
        except OSError as exc:
            log.warning("Could not save device history: %s", exc)
