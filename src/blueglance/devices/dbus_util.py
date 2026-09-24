"""Small helpers around Gio's D-Bus API (async calls, signal subscriptions)."""

from __future__ import annotations

import logging
from collections.abc import Callable

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
OBJECT_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"


def call(
    bus: Gio.DBusConnection,
    name: str,
    path: str,
    interface: str,
    method: str,
    args: GLib.Variant | None,
    reply_type: str | None,
    callback: Callable[[object | None, GLib.Error | None], None],
    cancellable: Gio.Cancellable | None = None,
    timeout_ms: int = 10_000,
) -> None:
    """Async method call; ``callback(result, error)`` gets unpacked Python values."""

    def on_done(connection, result):
        try:
            reply = connection.call_finish(result)
        except GLib.Error as error:
            if not error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                callback(None, error)
            return
        callback(reply.unpack() if reply is not None else None, None)

    bus.call(
        name,
        path,
        interface,
        method,
        args,
        GLib.VariantType(reply_type) if reply_type else None,
        Gio.DBusCallFlags.NONE,
        timeout_ms,
        cancellable,
        on_done,
    )


def get_all_properties(bus, name, path, interface, callback, cancellable=None) -> None:
    def done(result, error):
        callback(result[0] if result else None, error)

    call(bus, name, path, PROPERTIES_IFACE, "GetAll", GLib.Variant("(s)", (interface,)), "(a{sv})", done, cancellable)


class SignalSubscriptions:
    """Keeps track of ``signal_subscribe`` ids so they can all be dropped at once."""

    def __init__(self, bus: Gio.DBusConnection):
        self.bus = bus
        self._ids: list[int] = []

    def add(
        self,
        sender: str | None,
        interface: str | None,
        member: str | None,
        callback: Callable[[str, str, tuple], None],
        path: str | None = None,
        arg0: str | None = None,
    ) -> None:
        def on_signal(_conn, _sender, object_path, _iface, signal_name, parameters):
            try:
                callback(object_path, signal_name, parameters.unpack())
            except Exception:  # never let a handler kill the main loop dispatch
                log.exception("Error handling D-Bus signal %s", signal_name)

        self._ids.append(
            self.bus.signal_subscribe(
                sender, interface, member, path, arg0, Gio.DBusSignalFlags.NONE, on_signal
            )
        )

    def clear(self) -> None:
        for sub_id in self._ids:
            self.bus.signal_unsubscribe(sub_id)
        self._ids.clear()


def get_system_bus() -> Gio.DBusConnection | None:
    try:
        return Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except GLib.Error as error:
        log.warning("System D-Bus unavailable: %s", error.message)
        return None
