"""Battery data sources and the manager that merges them."""

from gi.repository import GObject

from ..models import Report


class Provider(GObject.Object):
    """Base class for a battery data source.

    Subclasses keep ``self._reports`` up to date and call :meth:`_changed`.
    All work happens on the GLib main loop (async D-Bus / IO watches), so no
    locking is required anywhere.
    """

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    source = "base"

    def __init__(self):
        super().__init__()
        self._reports: dict[str, Report] = {}

    @property
    def reports(self) -> list[Report]:
        return list(self._reports.values())

    def start(self) -> None:  # pragma: no cover - interface
        pass

    def stop(self) -> None:  # pragma: no cover - interface
        pass

    def _changed(self) -> None:
        self.emit("changed")
