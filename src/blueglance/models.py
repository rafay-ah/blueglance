"""Plain data types shared by the backends, the UI and the D-Bus API.

Nothing in here imports GTK, so it can be unit tested without a display.
"""

from __future__ import annotations

import enum
import re
import time
from dataclasses import dataclass, field

CRITICAL_LEVEL = 10

_MAC_RE = re.compile(r"(?<![0-9a-f])([0-9a-f]{2}[:_-]){5}[0-9a-f]{2}(?![0-9a-f])", re.IGNORECASE)


class DeviceKind(str, enum.Enum):
    EARBUDS = "earbuds"
    HEADPHONES = "headphones"
    HEADSET = "headset"
    SPEAKER = "speaker"
    MOUSE = "mouse"
    KEYBOARD = "keyboard"
    TOUCHPAD = "touchpad"
    GAMEPAD = "gamepad"
    PEN = "pen"
    TABLET = "tablet"
    PHONE = "phone"
    WATCH = "watch"
    REMOTE = "remote"
    COMPUTER = "computer"
    OTHER = "other"

    @property
    def label(self) -> str:
        return _KIND_LABELS[self]

    @property
    def is_audio(self) -> bool:
        return self in (DeviceKind.EARBUDS, DeviceKind.HEADPHONES, DeviceKind.HEADSET, DeviceKind.SPEAKER)


_KIND_LABELS = {
    DeviceKind.EARBUDS: "Earbuds",
    DeviceKind.HEADPHONES: "Headphones",
    DeviceKind.HEADSET: "Headset",
    DeviceKind.SPEAKER: "Speaker",
    DeviceKind.MOUSE: "Mouse",
    DeviceKind.KEYBOARD: "Keyboard",
    DeviceKind.TOUCHPAD: "Touchpad",
    DeviceKind.GAMEPAD: "Controller",
    DeviceKind.PEN: "Pen",
    DeviceKind.TABLET: "Tablet",
    DeviceKind.PHONE: "Phone",
    DeviceKind.WATCH: "Wearable",
    DeviceKind.REMOTE: "Remote",
    DeviceKind.COMPUTER: "This computer",
    DeviceKind.OTHER: "Device",
}

# Stable display order: this computer first, then audio, pointing devices, …
KIND_ORDER = {
    kind: index
    for index, kind in enumerate(
        (
            DeviceKind.COMPUTER,
            DeviceKind.EARBUDS,
            DeviceKind.HEADPHONES,
            DeviceKind.HEADSET,
            DeviceKind.MOUSE,
            DeviceKind.KEYBOARD,
            DeviceKind.TOUCHPAD,
            DeviceKind.GAMEPAD,
            DeviceKind.PEN,
            DeviceKind.TABLET,
            DeviceKind.SPEAKER,
            DeviceKind.PHONE,
            DeviceKind.WATCH,
            DeviceKind.REMOTE,
            DeviceKind.OTHER,
        )
    )
}


class ChargeState(str, enum.Enum):
    UNKNOWN = "unknown"
    CHARGING = "charging"
    DISCHARGING = "discharging"
    FULL = "full"
    NOT_CHARGING = "not-charging"


class LevelClass(str, enum.Enum):
    UNKNOWN = "unknown"
    CRITICAL = "critical"
    LOW = "low"
    NORMAL = "normal"


def classify_level(level: int | None, low_threshold: int) -> LevelClass:
    if level is None:
        return LevelClass.UNKNOWN
    if level <= min(CRITICAL_LEVEL, low_threshold):
        return LevelClass.CRITICAL
    if level <= low_threshold:
        return LevelClass.LOW
    return LevelClass.NORMAL


def normalize_address(value: str | None) -> str | None:
    """Extract a Bluetooth/MAC address from free text and normalise it.

    Accepts ``aa:bb:cc:dd:ee:ff``, ``AA_BB_…`` (BlueZ object paths),
    ``hid-aa:bb:…-battery`` (kernel power supplies) and similar.
    """
    if not value:
        return None
    match = _MAC_RE.search(value)
    if not match:
        return None
    return re.sub(r"[_-]", ":", match.group(0)).upper()


def clamp_level(value: float | int | None) -> int | None:
    if value is None:
        return None
    try:
        level = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, level))


@dataclass(frozen=True)
class Component:
    """One battery inside a multi-battery device (e.g. AirPods left/right/case)."""

    key: str  # "left" | "right" | "case" | …
    label: str
    level: int | None
    charging: bool = False

    def to_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "level": self.level, "charging": self.charging}


@dataclass
class Report:
    """What a single backend knows about a device.

    The :class:`~blueglance.devices.manager.DeviceManager` merges reports from
    all backends that share the same ``key`` into one :class:`Device`.
    """

    source: str
    key: str
    name: str | None = None
    kind: DeviceKind | None = None
    level: int | None = None
    state: ChargeState = ChargeState.UNKNOWN
    connected: bool | None = None
    address: str | None = None
    components: tuple[Component, ...] = ()
    model: str | None = None
    vendor: str | None = None
    coarse: bool = False
    # Higher wins when several sources provide a level for the same device.
    priority: int = 0
    # Free-form hints (e.g. {"audio": True, "apple": True}) used for diagnostics.
    hints: dict = field(default_factory=dict)


@dataclass
class Device:
    id: str
    name: str
    kind: DeviceKind = DeviceKind.OTHER
    level: int | None = None
    state: ChargeState = ChargeState.UNKNOWN
    connected: bool = True
    address: str | None = None
    components: tuple[Component, ...] = ()
    model: str | None = None
    vendor: str | None = None
    coarse: bool = False
    sources: tuple[str, ...] = ()
    hints: dict = field(default_factory=dict)
    last_seen: float = field(default_factory=time.time)

    @property
    def charging(self) -> bool:
        return self.state == ChargeState.CHARGING or any(
            c.charging for c in self.components if c.key != "case"
        )

    @property
    def has_battery(self) -> bool:
        return self.level is not None or any(c.level is not None for c in self.components)

    @property
    def sort_key(self) -> tuple:
        return (not self.connected, KIND_ORDER.get(self.kind, 99), self.name.casefold(), self.id)

    def component(self, key: str) -> Component | None:
        for comp in self.components:
            if comp.key == key:
                return comp
        return None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind.value,
            "kindLabel": self.kind.label,
            "level": self.level,
            "state": self.state.value,
            "charging": self.charging,
            "connected": self.connected,
            "address": self.address,
            "coarse": self.coarse,
            "model": self.model,
            "components": [c.to_dict() for c in self.components],
            "lastSeen": int(self.last_seen),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Device:
        return cls(
            id=str(data["id"]),
            name=str(data.get("name") or "Device"),
            kind=_enum_or(DeviceKind, data.get("kind"), DeviceKind.OTHER),
            level=clamp_level(data.get("level")),
            state=_enum_or(ChargeState, data.get("state"), ChargeState.UNKNOWN),
            connected=bool(data.get("connected", False)),
            address=data.get("address") or None,
            coarse=bool(data.get("coarse", False)),
            model=data.get("model"),
            components=tuple(
                Component(
                    key=str(c.get("key")),
                    label=str(c.get("label")),
                    level=clamp_level(c.get("level")),
                    charging=bool(c.get("charging", False)),
                )
                for c in data.get("components") or ()
            ),
            last_seen=float(data.get("lastSeen") or 0),
        )


def _enum_or(enum_type, value, default):
    try:
        return enum_type(value)
    except ValueError:
        return default


def headline_level(components: tuple[Component, ...] | list[Component]) -> int | None:
    """The single number shown for a multi-battery device: its weakest earbud.

    The charging case is ignored unless it is the only thing reporting.
    """
    buds = [c.level for c in components if c.key != "case" and c.level is not None]
    if buds:
        return min(buds)
    rest = [c.level for c in components if c.level is not None]
    return min(rest) if rest else None


def format_relative_time(timestamp: float, now: float | None = None) -> str:
    now = time.time() if now is None else now
    delta = max(0, int(now - timestamp))
    if delta < 60:
        return "just now"
    minutes = delta // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} h ago"
    days = hours // 24
    return "yesterday" if days == 1 else f"{days} days ago"
