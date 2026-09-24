"""Tray icon protocol test against a fake StatusNotifierWatcher on a private bus.

Run under ``dbus-run-session`` (CI does); skipped when no session bus exists.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DBUS_SESSION_BUS_ADDRESS"), reason="needs a session bus")

WATCHER_XML = """
<node><interface name="org.kde.StatusNotifierWatcher">
  <method name="RegisterStatusNotifierItem"><arg type="s" direction="in"/></method>
</interface></node>
"""


def run_until(loop_context, predicate, timeout=3.0):
    import time

    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        loop_context.iteration(False)
        time.sleep(0.005)
    return predicate()


class FakeApp:
    def __init__(self):
        from blueglance.config import Config
        from blueglance.devices.demo import DemoProvider
        from blueglance.devices.manager import DeviceManager

        self.config = Config(persist=False)
        self.manager = DeviceManager([DemoProvider(animate=False)], remember=False)
        self.manager.start()
        self.services = {}
        self.activated = 0
        self.shown = []

    def activate(self):
        self.activated += 1

    def show_device(self, device_id):
        self.shown.append(device_id)

    def show_preferences(self):
        pass

    def quit(self):
        pass


def test_tray_registers_and_serves_menu():
    from gi.repository import Gio, GLib

    from blueglance.services.tray import TrayController

    ctx = GLib.MainContext.default()
    address = os.environ["DBUS_SESSION_BUS_ADDRESS"]
    watcher_bus = Gio.DBusConnection.new_for_address_sync(
        address,
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None, None)
    registered = []

    def on_call(conn, sender, path, iface, method, params, invocation):
        registered.append((sender, params.unpack()[0]))
        invocation.return_value(None)

    node = Gio.DBusNodeInfo.new_for_xml(WATCHER_XML)
    watcher_bus.register_object("/StatusNotifierWatcher", node.interfaces[0], on_call, None, None)
    owned = []
    Gio.bus_own_name_on_connection(watcher_bus, "org.kde.StatusNotifierWatcher", Gio.BusNameOwnerFlags.NONE,
                                   lambda *a: owned.append(True), None)
    assert run_until(ctx, lambda: owned)

    app = FakeApp()
    run_until(ctx, lambda: app.manager.devices, 1.0)
    tray = TrayController(app)
    tray.start()
    assert run_until(ctx, lambda: registered)
    sender, service = registered[-1]
    assert service.startswith("org.kde.StatusNotifierItem-") or service == "/StatusNotifierItem"
    bus_name = service if not service.startswith("/") else sender

    def call(path, iface, method, args, reply=None):
        # The item lives in this very process, so never block the main loop.
        box = []
        watcher_bus.call(bus_name, path, iface, method, args, GLib.VariantType(reply) if reply else None,
                         Gio.DBusCallFlags.NONE, 3000, None,
                         lambda conn, res: box.append(conn.call_finish(res)))
        assert run_until(ctx, lambda: box)
        return box[0].unpack()

    # Check the exact wire format: each child must be a "v" holding "(ia{sv}av)".
    raw = []
    watcher_bus.call(bus_name, "/MenuBar", "com.canonical.dbusmenu", "GetLayout",
                     GLib.Variant("(iias)", (0, -1, [])), GLib.VariantType("(u(ia{sv}av))"),
                     Gio.DBusCallFlags.NONE, 3000, None, lambda conn, res: raw.append(conn.call_finish(res)))
    assert run_until(ctx, lambda: raw)
    first_child = raw[0].get_child_value(1).get_child_value(2).get_child_value(0)
    assert first_child.get_variant().get_type_string() == "(ia{sv}av)"

    revision, layout = call("/MenuBar", "com.canonical.dbusmenu", "GetLayout",
                            GLib.Variant("(iias)", (0, -1, [])), "(u(ia{sv}av))")
    root_id, root_props, children = layout
    assert root_id == 0 and root_props["children-display"] == "submenu"
    labels = {child[0]: child[1].get("label") for child in children}
    texts = [t for t in labels.values() if t]
    assert any(t.startswith("AirPods Pro") and "L 92%" in t for t in texts)
    assert any(t.startswith("MX Master 3S") and "74%" in t for t in texts)
    toggle_id = next(i for i, t in labels.items() if t == "Show Desktop Widget")
    assert next(c[1] for c in children if c[0] == toggle_id)["toggle-state"] == 1

    tooltip = call("/StatusNotifierItem", "org.freedesktop.DBus.Properties", "Get",
                   GLib.Variant("(ss)", ("org.kde.StatusNotifierItem", "ToolTip")), "(v)")[0]
    assert "Keychron K3: 16%" in tooltip[3]

    call("/MenuBar", "com.canonical.dbusmenu", "Event",
         GLib.Variant("(isvu)", (toggle_id, "clicked", GLib.Variant("i", 0), 0)))
    assert run_until(ctx, lambda: app.config["widget_enabled"] is False)

    device_item = next(i for i, t in labels.items() if t and t.startswith("MX Master 3S"))
    call("/MenuBar", "com.canonical.dbusmenu", "Event",
         GLib.Variant("(isvu)", (device_item, "clicked", GLib.Variant("s", ""), 0)))
    assert run_until(ctx, lambda: app.shown == ["demo:mouse"])

    call("/StatusNotifierItem", "org.kde.StatusNotifierItem", "Activate", GLib.Variant("(ii)", (0, 0)))
    assert run_until(ctx, lambda: app.activated == 1)

    # Menu reflects the new widget state after a refresh.
    new_revision, layout = call("/MenuBar", "com.canonical.dbusmenu", "GetLayout",
                                GLib.Variant("(iias)", (0, -1, [])), "(u(ia{sv}av))")
    assert new_revision > revision
    toggle = next(c for c in layout[2] if c[1].get("label") == "Show Desktop Widget")
    assert toggle[1]["toggle-state"] == 0

    # Hiding the icon goes Passive first, so hosts that track us by unique
    # name (Flatpak registers by path) drop it right away.
    statuses = []
    watcher_bus.signal_subscribe(None, "org.kde.StatusNotifierItem", "NewStatus", "/StatusNotifierItem", None,
                                 Gio.DBusSignalFlags.NONE, lambda *a: statuses.append(a[5].unpack()[0]))
    app.config["tray_icon"] = False
    assert tray.item is None
    assert run_until(ctx, lambda: statuses == ["Passive"])
    tray.stop()
    app.manager.stop()
