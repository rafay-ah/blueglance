"""System tray icon via StatusNotifierItem + com.canonical.dbusmenu, in pure Gio.

GTK 4 has no tray API and libappindicator is GTK 3 only, so BlueGlance speaks
the D-Bus protocols directly. Works with KDE Plasma, the Ubuntu AppIndicator
GNOME extension, XFCE, Cinnamon, Budgie, waybar, etc. On GNOME with the
BlueGlance Shell extension running, the extension's own top bar indicator is
used instead.
"""

from __future__ import annotations

import logging
import os

from gi.repository import Gio, GLib

from .. import APP_NAME, icons
from ..icons import ICON_DIR
from ..session import info as session_info
from ..ui.widget_view import component_summary_text, device_level_text

log = logging.getLogger(__name__)

WATCHER_BUS = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"

SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="XAyatanaLabel" type="s" access="read"/>
    <property name="XAyatanaLabelGuide" type="s" access="read"/>
    <property name="XAyatanaOrderingIndex" type="u" access="read"/>
    <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="SecondaryActivate">
      <arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/>
    </method>
    <method name="Scroll">
      <arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/>
    </method>
    <method name="XAyatanaSecondaryActivate"><arg name="timestamp" type="u" direction="in"/></method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewOverlayIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewMenu"/>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
    <signal name="XAyatanaNewLabel"><arg name="label" type="s"/><arg name="guide" type="s"/></signal>
  </interface>
</node>
"""

DBUSMENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="ai" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/>
      <arg type="a(ias)" name="removedProps"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg type="i" name="id"/>
      <arg type="u" name="timestamp"/>
    </signal>
  </interface>
</node>
"""


class MenuItem:
    def __init__(self, item_id: int, props: dict, callback=None, children=None):
        self.id = item_id
        self.props = props
        self.callback = callback
        self.children = children or []

    def variant_props(self, names=None) -> dict:
        props = {}
        for key, value in self.props.items():
            if names and key not in names:
                continue
            if isinstance(value, GLib.Variant):
                props[key] = value
            elif isinstance(value, bool):
                props[key] = GLib.Variant("b", value)
            elif isinstance(value, int):
                props[key] = GLib.Variant("i", value)
            else:
                props[key] = GLib.Variant("s", str(value))
        return props


class DBusMenu:
    """A tiny dbusmenu server: a root item with a flat list of children."""

    def __init__(self, bus: Gio.DBusConnection):
        self.bus = bus
        self.revision = 1
        self.items: dict[int, MenuItem] = {}
        self.root = MenuItem(0, {"children-display": "submenu"})
        self.items[0] = self.root
        node = Gio.DBusNodeInfo.new_for_xml(DBUSMENU_XML)
        self._reg = bus.register_object(MENU_PATH, node.interfaces[0], self._on_call, self._on_get, None)

    def unregister(self) -> None:
        if self._reg:
            self.bus.unregister_object(self._reg)
            self._reg = 0

    def set_items(self, children: list[MenuItem]) -> None:
        self.root.children = children
        self.items = {0: self.root}
        for child in children:
            self.items[child.id] = child
        self.revision += 1
        if self._reg:
            try:
                self.bus.emit_signal(None, MENU_PATH, "com.canonical.dbusmenu", "LayoutUpdated",
                                     GLib.Variant("(ui)", (self.revision, 0)))
            except GLib.Error as error:
                log.debug("LayoutUpdated failed: %s", error.message)

    def _layout(self, item: MenuItem, depth: int, names) -> GLib.Variant:
        children = []
        if depth != 0:
            for child in item.children:
                children.append(GLib.Variant("v", self._layout(child, depth - 1, names)))
        return GLib.Variant("(ia{sv}av)", (item.id, item.variant_props(names), children))

    def _on_get(self, _conn, _sender, _path, _iface, name):
        return {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", [str(ICON_DIR)]),
        }.get(name)

    def _on_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        args = params.unpack()
        if method == "GetLayout":
            parent_id, depth, names = args
            item = self.items.get(parent_id, self.root)
            layout = self._layout(item, depth, set(names))
            invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("u", self.revision), layout))
        elif method == "GetGroupProperties":
            ids, names = args
            result = [(i, self.items[i].variant_props(set(names))) for i in (ids or self.items) if i in self.items]
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (result,)))
        elif method == "GetProperty":
            item_id, name = args
            item = self.items.get(item_id)
            value = item.variant_props().get(name) if item else None
            if value is None:
                invocation.return_dbus_error("com.canonical.dbusmenu.Error", "No such property")
            else:
                invocation.return_value(GLib.Variant("(v)", (value,)))
        elif method == "Event":
            item_id, event_id, _data, _ts = args
            self._dispatch(item_id, event_id)
            invocation.return_value(None)
        elif method == "EventGroup":
            errors = []
            for item_id, event_id, _data, _ts in args[0]:
                if item_id not in self.items:
                    errors.append(item_id)
                self._dispatch(item_id, event_id)
            invocation.return_value(GLib.Variant("(ai)", (errors,)))
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    def _dispatch(self, item_id: int, event_id: str) -> None:
        item = self.items.get(item_id)
        if item is not None and event_id == "clicked" and item.callback is not None:
            GLib.idle_add(lambda: (item.callback(), GLib.SOURCE_REMOVE)[1])


class StatusNotifierItem:
    def __init__(self, bus: Gio.DBusConnection, on_activate):
        self.bus = bus
        self.on_activate = on_activate
        self.icon_name = icons.APP_SYMBOLIC
        self.tooltip_title = APP_NAME
        self.tooltip_body = ""
        self.label = ""
        self.status = "Active"
        self.menu = DBusMenu(bus)
        node = Gio.DBusNodeInfo.new_for_xml(SNI_XML)
        self._reg = bus.register_object(ITEM_PATH, node.interfaces[0], self._on_call, self._on_get, None)
        self._bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self._own_id = 0
        self._watch_id = 0
        self._name_owned = False
        if not session_info().flatpak:
            self._own_id = Gio.bus_own_name_on_connection(
                bus, self._bus_name, Gio.BusNameOwnerFlags.NONE, self._on_name_acquired, self._on_name_lost)
        self._watch_id = Gio.bus_watch_name_on_connection(
            bus, WATCHER_BUS, Gio.BusNameWatcherFlags.NONE, self._on_watcher_appeared, None)

    def destroy(self) -> None:
        if self._watch_id:
            Gio.bus_unwatch_name(self._watch_id)
            self._watch_id = 0
        if self._own_id:
            Gio.bus_unown_name(self._own_id)
            self._own_id = 0
        if self._reg:
            self.bus.unregister_object(self._reg)
            self._reg = 0
        self.menu.unregister()

    # -- registration -------------------------------------------------------
    def _on_name_acquired(self, *_args) -> None:
        self._name_owned = True
        self._register()

    def _on_name_lost(self, *_args) -> None:
        self._name_owned = False

    def _on_watcher_appeared(self, *_args) -> None:
        self._register()

    def _register(self) -> None:
        service = self._bus_name if self._name_owned else ITEM_PATH

        def done(bus, result):
            try:
                bus.call_finish(result)
                log.debug("Registered tray icon as %s", service)
            except GLib.Error as error:
                log.debug("No StatusNotifierWatcher: %s", error.message)

        self.bus.call(WATCHER_BUS, WATCHER_PATH, WATCHER_BUS, "RegisterStatusNotifierItem",
                      GLib.Variant("(s)", (service,)), None, Gio.DBusCallFlags.NO_AUTO_START, 5000, None, done)

    # -- updates ------------------------------------------------------------
    def _emit(self, signal: str, params: GLib.Variant | None = None) -> None:
        if not self._reg:
            return
        try:
            self.bus.emit_signal(None, ITEM_PATH, "org.kde.StatusNotifierItem", signal, params)
        except GLib.Error as error:
            log.debug("%s failed: %s", signal, error.message)

    def update(self, *, tooltip: str, label: str, attention: bool) -> None:
        if tooltip != self.tooltip_body:
            self.tooltip_body = tooltip
            self._emit("NewToolTip")
        if label != self.label:
            self.label = label
            self._emit("XAyatanaNewLabel", GLib.Variant("(ss)", (label, "100%" if label else "")))
        status = "NeedsAttention" if attention else "Active"
        if status != self.status:
            self.status = status
            self._emit("NewStatus", GLib.Variant("(s)", (status,)))

    def _on_get(self, _conn, _sender, _path, _iface, name):
        values = {
            "Category": GLib.Variant("s", "Hardware"),
            "Id": GLib.Variant("s", "blueglance"),
            "Title": GLib.Variant("s", APP_NAME),
            "Status": GLib.Variant("s", self.status),
            "WindowId": GLib.Variant("i", 0),
            "IconName": GLib.Variant("s", self.icon_name),
            "IconThemePath": GLib.Variant("s", str(ICON_DIR)),
            "IconPixmap": GLib.Variant("a(iiay)", []),
            "OverlayIconName": GLib.Variant("s", ""),
            "OverlayIconPixmap": GLib.Variant("a(iiay)", []),
            "AttentionIconName": GLib.Variant("s", self.icon_name),
            "AttentionIconPixmap": GLib.Variant("a(iiay)", []),
            "AttentionMovieName": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", (self.icon_name, [], self.tooltip_title, self.tooltip_body)),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", MENU_PATH),
            "XAyatanaLabel": GLib.Variant("s", self.label),
            "XAyatanaLabelGuide": GLib.Variant("s", "100%" if self.label else ""),
            "XAyatanaOrderingIndex": GLib.Variant("u", 0),
        }
        return values.get(name)

    def _on_call(self, _conn, _sender, _path, _iface, method, _params, invocation):
        if method in ("Activate", "SecondaryActivate", "XAyatanaSecondaryActivate"):
            GLib.idle_add(lambda: (self.on_activate(), GLib.SOURCE_REMOVE)[1])
        invocation.return_value(None)


class TrayController:
    def __init__(self, app):
        self.app = app
        self.config = app.config
        self.item: StatusNotifierItem | None = None
        self._handlers = []
        self._bus: Gio.DBusConnection | None = None

    def start(self) -> None:
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as error:
            log.warning("No session bus for the tray icon: %s", error.message)
            return
        self._handlers.append((self.config, self.config.connect("changed", self._on_config_changed)))
        self._handlers.append((self.app.manager, self.app.manager.connect("changed", lambda *_: self.refresh())))
        shell = self.app.services.get("shell")
        if shell is not None:
            self._handlers.append((shell, shell.connect("changed", lambda *_: self.sync())))
        self.sync()

    def stop(self) -> None:
        for obj, handler in self._handlers:
            obj.disconnect(handler)
        self._handlers.clear()
        if self.item is not None:
            self.item.destroy()
            self.item = None

    def _on_config_changed(self, _config, key: str) -> None:
        if key == "tray_icon":
            self.sync()
        elif key in ("tray_label", "widget_enabled", "low_threshold", "hidden_devices"):
            self.refresh()

    def sync(self) -> None:
        shell = self.app.services.get("shell")
        want = bool(self.config["tray_icon"]) and not (shell is not None and shell.active)
        if want and self.item is None and self._bus is not None:
            self.item = StatusNotifierItem(self._bus, self.app.activate)
            self.refresh()
        elif not want and self.item is not None:
            self.item.destroy()
            self.item = None

    def refresh(self) -> None:
        if self.item is None:
            return
        config = self.config
        devices = [d for d in self.app.manager.connected_with_battery() if not config.is_hidden(d.id)]
        threshold = config["low_threshold"]

        items: list[MenuItem] = []
        next_id = 1
        for device in devices:
            text = f"{device.name}  —  {device_level_text(device)}"
            if device.components:
                text = f"{device.name}  —  {component_summary_text(device)}"
            if device.charging:
                text += "  (charging)"
            items.append(MenuItem(next_id, {"label": text.replace("_", "__"),
                                            "icon-name": icons.for_kind(device.kind)},
                                  callback=lambda device_id=device.id: self.app.show_device(device_id)))
            next_id += 1
        if not devices:
            items.append(MenuItem(next_id, {"label": "No devices connected", "enabled": False}))
            next_id += 1
        items.append(MenuItem(next_id, {"type": "separator"}))
        next_id += 1
        items.append(MenuItem(next_id, {"label": f"Open {APP_NAME}"}, callback=self.app.activate))
        next_id += 1
        items.append(MenuItem(next_id, {"label": "Show Desktop Widget", "toggle-type": "checkmark",
                                        "toggle-state": 1 if config["widget_enabled"] else 0},
                              callback=lambda: config.set("widget_enabled", not config["widget_enabled"])))
        next_id += 1
        items.append(MenuItem(next_id, {"label": "Preferences…"}, callback=self.app.show_preferences))
        next_id += 1
        items.append(MenuItem(next_id, {"type": "separator"}))
        next_id += 1
        items.append(MenuItem(next_id, {"label": f"Quit {APP_NAME}"}, callback=self.app.quit))
        self.item.menu.set_items(items)

        lines = [f"{d.name}: {device_level_text(d)}{' (charging)' if d.charging else ''}" for d in devices]
        tooltip = "\n".join(lines) if lines else "No devices connected"
        lowest = min((d for d in devices if d.level is not None), key=lambda d: d.level, default=None)
        label = f"{lowest.level}%" if (lowest is not None and config["tray_label"]) else ""
        attention = lowest is not None and lowest.level <= min(10, threshold) and not lowest.charging
        self.item.update(tooltip=tooltip, label=label, attention=attention)

