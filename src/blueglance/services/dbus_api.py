"""Session D-Bus API, consumed by the BlueGlance GNOME Shell extension.

Bus name  : io.github.rafay_ah.BlueGlance (owned by GApplication)
Object    : /io/github/rafay_ah/BlueGlance
Interface : io.github.rafay_ah.BlueGlance1

``GetState`` / ``StateChanged`` carry one JSON document so the extension
(GJS) only has to ``JSON.parse`` it.
"""

from __future__ import annotations

import json
import logging

from gi.repository import Gio, GLib

from .. import __version__

log = logging.getLogger(__name__)

INTERFACE = "io.github.rafay_ah.BlueGlance1"
API_VERSION = 1

INTROSPECTION_XML = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="GetState">
      <arg type="s" name="state" direction="out"/>
    </method>
    <method name="ShowWindow"/>
    <method name="ShowPreferences"/>
    <method name="ShowDevice">
      <arg type="s" name="device_id" direction="in"/>
    </method>
    <method name="SetWidgetEnabled">
      <arg type="b" name="enabled" direction="in"/>
    </method>
    <method name="SetWidgetSize">
      <arg type="s" name="size" direction="in"/>
    </method>
    <method name="SetShellWidgetPosition">
      <arg type="s" name="position" direction="in"/>
    </method>
    <signal name="StateChanged">
      <arg type="s" name="state"/>
    </signal>
    <property name="Version" type="s" access="read"/>
    <property name="ApiVersion" type="u" access="read"/>
  </interface>
</node>
"""


def build_state(app) -> dict:
    config = app.config
    manager = app.manager
    if config is None or manager is None:
        return {"apiVersion": API_VERSION, "ready": False, "devices": []}
    devices = [d.to_dict() for d in manager.connected_with_battery() if not config.is_hidden(d.id)]
    bluez = manager.get_provider("bluez")
    return {
        "apiVersion": API_VERSION,
        "ready": True,
        "version": __version__,
        "devices": devices,
        "lowThreshold": config["low_threshold"],
        "bluetooth": {
            "available": bool(bluez and bluez.available and bluez.adapters),
            "powered": bool(bluez and bluez.powered),
        } if bluez is not None else {"available": True, "powered": True},
        "widget": {
            "enabled": config["widget_enabled"],
            "size": config["widget_size"],
            "theme": config["widget_theme"],
            "position": config["shell_widget_position"],
        },
        "indicator": {
            "enabled": config["tray_icon"],
            "showPercentage": config["tray_label"],
        },
    }


class DBusApi:
    def __init__(self, app, connection: Gio.DBusConnection, object_path: str):
        self.app = app
        self.connection = connection
        self.object_path = object_path
        self._handlers = []
        self._emit_source = 0
        self._last_payload = None
        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        self._registration = connection.register_object(
            object_path, node.interfaces[0], self._on_method_call, self._on_get_property, None
        )

    def start(self) -> None:
        self._handlers = [
            (self.app.manager, self.app.manager.connect("changed", self._schedule_emit)),
            (self.app.config, self.app.config.connect("changed", self._schedule_emit)),
        ]
        self._schedule_emit()

    def stop(self) -> None:
        for obj, handler in self._handlers:
            obj.disconnect(handler)
        self._handlers.clear()
        if self._emit_source:
            GLib.source_remove(self._emit_source)
            self._emit_source = 0

    def unexport(self) -> None:
        self.stop()
        if self._registration:
            self.connection.unregister_object(self._registration)
            self._registration = 0

    # -- signal -------------------------------------------------------------
    def _schedule_emit(self, *_args) -> None:
        if not self._emit_source:
            self._emit_source = GLib.timeout_add(60, self._emit)

    def _emit(self) -> bool:
        self._emit_source = 0
        payload = json.dumps(build_state(self.app), sort_keys=True)
        if payload != self._last_payload:
            self._last_payload = payload
            try:
                self.connection.emit_signal(None, self.object_path, INTERFACE, "StateChanged",
                                            GLib.Variant("(s)", (payload,)))
            except GLib.Error as error:
                log.debug("Could not emit StateChanged: %s", error.message)
        return GLib.SOURCE_REMOVE

    # -- vtable -------------------------------------------------------------
    def _on_get_property(self, _conn, _sender, _path, _iface, name):
        if name == "Version":
            return GLib.Variant("s", __version__)
        if name == "ApiVersion":
            return GLib.Variant("u", API_VERSION)
        return None

    def _on_method_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        app = self.app
        try:
            if method == "GetState":
                invocation.return_value(GLib.Variant("(s)", (json.dumps(build_state(app)),)))
                return
            if app.config is None:
                invocation.return_dbus_error(f"{INTERFACE}.Error.NotReady", "BlueGlance is starting up")
                return
            if method == "ShowWindow":
                app.activate()
            elif method == "ShowPreferences":
                app.show_preferences()
            elif method == "ShowDevice":
                app.show_device(params.unpack()[0])
            elif method == "SetWidgetEnabled":
                app.config["widget_enabled"] = bool(params.unpack()[0])
            elif method == "SetWidgetSize":
                app.config["widget_size"] = params.unpack()[0]
            elif method == "SetShellWidgetPosition":
                position = json.loads(params.unpack()[0])
                app.config["shell_widget_position"] = position if isinstance(position, dict) else None
            else:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
                return
            invocation.return_value(None)
        except (ValueError, TypeError) as exc:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.InvalidArgs", str(exc))
