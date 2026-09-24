"""The BlueGlance application: wires config, devices, UI and background services."""

from __future__ import annotations

import logging
import os
import shutil
import signal
import sys

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import APP_ID, APP_NAME, ISSUE_URL, WEBSITE, __version__
from .config import Config, state_dir
from .devices.manager import DeviceManager
from .icons import ICON_DIR

log = logging.getLogger(__name__)

STYLESHEET = os.path.join(os.path.dirname(__file__), "ui", "style.css")


class BlueGlanceApplication(Adw.Application):
    __gtype_name__ = "BlueGlanceApplication"

    def __init__(self, demo: bool = False):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.demo = demo or os.environ.get("BLUEGLANCE_DEMO") == "1"
        self.config: Config | None = None
        self.manager: DeviceManager | None = None
        self.window = None
        self.services: dict[str, object] = {}
        self._held = False
        self._started = False

        self.add_main_option("background", ord("b"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Start without opening the window (used at login)", None)
        self.add_main_option("demo", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Show sample devices instead of real ones", None)
        self.add_main_option("quit", ord("q"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Quit the running instance", None)
        self.add_main_option("version", ord("v"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Print the version and exit", None)
        self.add_main_option("debug", ord("d"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Verbose logging", None)

    # -- GApplication vfuncs -----------------------------------------------
    def do_handle_local_options(self, options: GLib.VariantDict) -> int:
        # Don't call options.end() here: the same dict is forwarded to the
        # primary instance's command-line handler.
        if options.contains("version"):
            print(f"{APP_NAME} {__version__}")
            return 0
        logging.basicConfig(
            level=logging.DEBUG if options.contains("debug") else logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        if options.contains("demo"):
            self.demo = True
        return -1

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        GLib.set_application_name(APP_NAME)
        Gtk.Window.set_default_icon_name(APP_ID)

        demo_config = self.demo and os.environ.get("BLUEGLANCE_DEMO_CONFIG") != "persist"
        self.config = Config(persist=not demo_config)
        self.manager = DeviceManager(
            self._create_providers(),
            history_path=state_dir() / "devices.json",
            remember=not self.demo,
        )
        self._setup_style()
        self._setup_actions()
        self.config.connect("changed", self._on_config_changed)
        self.manager.start()
        self._apply_background_hold()
        # Logging out sends SIGTERM: shut down cleanly so settings and the
        # remembered devices get saved.
        for signum in (signal.SIGTERM, signal.SIGHUP):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, self._on_unix_signal)

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        options = command_line.get_options_dict()
        if options.contains("quit"):
            self.quit()
            return 0
        background = options.contains("background")
        first = not self._started
        if first:
            self._started = True
            self._start_services()
        if background:
            if first:
                self._first_run_checks(background=True)
            return 0
        self.activate()
        return 0

    def do_activate(self) -> None:
        if not self._started:
            self._started = True
            self._start_services()
        if self.window is None:
            from .ui.window import MainWindow

            self.window = MainWindow(self)
            self.window.connect("close-request", self._on_window_close_request)
            self._apply_window_theme()
        self.window.present()
        self._first_run_checks(background=False)

    def do_shutdown(self) -> None:
        for name, service in list(self.services.items()):
            stop = getattr(service, "stop", None)
            if stop is not None:
                try:
                    stop()
                except Exception:
                    log.exception("Error stopping %s", name)
        if self.manager is not None:
            self.manager.stop()
        if self.config is not None:
            self.config.flush()
        Adw.Application.do_shutdown(self)

    def do_dbus_register(self, connection: Gio.DBusConnection, object_path: str) -> bool:
        from .services.dbus_api import DBusApi

        # Exported before the bus name is acquired, so clients never see a
        # half-initialised service. The API itself becomes usable after startup.
        self.services["dbus"] = DBusApi(self, connection, object_path)
        return Adw.Application.do_dbus_register(self, connection, object_path)

    def do_dbus_unregister(self, connection: Gio.DBusConnection, object_path: str) -> None:
        api = self.services.pop("dbus", None)
        if api is not None:
            api.unexport()
        Adw.Application.do_dbus_unregister(self, connection, object_path)

    def _on_unix_signal(self) -> bool:
        self.quit()
        return GLib.SOURCE_REMOVE

    # -- setup --------------------------------------------------------------
    def _create_providers(self):
        if self.demo:
            from .devices.demo import DemoProvider

            return [DemoProvider(animate=os.environ.get("BLUEGLANCE_DEMO_STATIC") != "1")]

        from .devices.bluez import BlueZProvider
        from .devices.upower import UPowerProvider

        upower = UPowerProvider()
        upower.include_system = bool(self.config["show_system_battery"])
        providers = [upower, BlueZProvider()]
        try:
            from .devices.airpods import AirPodsProvider

            airpods = AirPodsProvider(providers[1])
            airpods.enabled = bool(self.config["airpods_enhanced"])
            providers.append(airpods)
        except ImportError:  # pragma: no cover - optional
            log.debug("AirPods support unavailable", exc_info=True)
        return providers

    def _setup_style(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        Gtk.IconTheme.get_for_display(display).add_search_path(str(ICON_DIR))
        provider = Gtk.CssProvider()
        provider.load_from_path(STYLESHEET)
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        style = Adw.StyleManager.get_default()
        style.connect("notify::dark", lambda *_: self._apply_window_theme())

    def _apply_window_theme(self) -> None:
        if self.window is None:
            return
        dark = Adw.StyleManager.get_default().get_dark()
        self.window.remove_css_class("light" if dark else "dark")
        self.window.add_css_class("dark" if dark else "light")

    def _setup_actions(self) -> None:
        def add(name, callback, accels=None):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_: callback())
            self.add_action(action)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)

        add("preferences", self.show_preferences, ["<Primary>comma"])
        add("about", self.show_about)
        add("quit", self.quit, ["<Primary>q"])
        add("show-window", self.activate)
        add("bluetooth-settings", self.open_bluetooth_settings)
        add("headset-battery-help", self.show_headset_battery_help)

        widget_action = Gio.SimpleAction.new_stateful(
            "widget-enabled", None, GLib.Variant.new_boolean(bool(self.config["widget_enabled"]))
        )
        widget_action.connect("change-state", self._on_widget_action)
        self.add_action(widget_action)
        self.set_accels_for_action("app.widget-enabled", ["<Primary>d"])
        self.set_accels_for_action("window.close", ["<Primary>w"])

    def _start_services(self) -> None:
        from .services.autostart import Autostart
        from .services.notifier import Notifier

        self.services["autostart"] = Autostart(self)
        self.services["notifier"] = Notifier(self)
        try:
            from .services.shell import ShellIntegration

            self.services["shell"] = ShellIntegration(self)
        except Exception:
            log.exception("GNOME Shell integration failed to start")
        try:
            from .ui.widget_window import WidgetController

            self.services["widget"] = WidgetController(self)
        except Exception:
            log.exception("Desktop widget failed to start")
        try:
            from .services.tray import TrayController

            self.services["tray"] = TrayController(self)
        except Exception:
            log.exception("Tray icon failed to start")
        for service in self.services.values():
            start = getattr(service, "start", None)
            if start is not None:
                try:
                    start()
                except Exception:
                    log.exception("Failed to start %s", type(service).__name__)

    def _first_run_checks(self, background: bool) -> None:
        if self.config["onboarded"] or self.demo:
            return
        self.config["onboarded"] = True
        autostart = self.services.get("autostart")
        if autostart is not None and self.config["autostart"]:
            autostart.sync()
        if not background and self.window is not None:
            toast = Adw.Toast(title="BlueGlance will now start automatically when you log in",
                              button_label="Undo", timeout=8)
            toast.connect("button-clicked", lambda *_: self.config.set("autostart", False))
            self.window.toasts.add_toast(toast)

    # -- reactions ----------------------------------------------------------
    def _apply_background_hold(self) -> None:
        want = bool(self.config["run_in_background"])
        if want and not self._held:
            self.hold()
            self._held = True
        elif not want and self._held:
            self.release()
            self._held = False

    def _on_window_close_request(self, window) -> bool:
        self.window = None
        if not self.config["run_in_background"]:
            GLib.idle_add(self.quit)
        return False

    def _on_widget_action(self, action, value) -> None:
        self.config["widget_enabled"] = value.get_boolean()

    def _on_config_changed(self, _config, key: str) -> None:
        if key == "run_in_background":
            self._apply_background_hold()
        elif key == "widget_enabled":
            action = self.lookup_action("widget-enabled")
            action.set_state(GLib.Variant.new_boolean(bool(self.config[key])))
        elif key == "show_system_battery":
            upower = self.manager.get_provider("upower")
            if upower is not None:
                upower.include_system = bool(self.config[key])
        elif key == "airpods_enhanced":
            airpods = self.manager.get_provider("aap")
            if airpods is not None:
                airpods.enabled = bool(self.config[key])

    # -- actions ------------------------------------------------------------
    def show_preferences(self) -> None:
        from .ui.preferences import PreferencesDialog

        self.activate()
        PreferencesDialog(self).present(self.window)

    def show_about(self) -> None:
        self.activate()
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            developer_name="Abdul Rafay",
            version=__version__,
            website=WEBSITE,
            issue_url=ISSUE_URL,
            license_type=Gtk.License.GPL_3_0,
            comments="Battery levels of your Bluetooth mice, keyboards, headphones and controllers "
                     "— at a glance, right on your desktop.",
            developers=["Abdul Rafay https://github.com/rafay-ah"],
            copyright="© 2026 Abdul Rafay and contributors",
        )
        about.present(self.window)

    def show_device(self, device_id: str) -> None:
        from .ui.details import DeviceDetailsDialog

        self.activate()
        device = self.manager.find(device_id)
        if device is not None and self.window is not None:
            DeviceDetailsDialog(self, device).present(self.window)

    def show_headset_battery_help(self) -> None:
        from .ui.troubleshoot import HeadsetBatteryDialog

        self.activate()
        HeadsetBatteryDialog(self).present(self.window)

    def open_bluetooth_settings(self) -> None:
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        candidates = []
        if "kde" in desktop:
            candidates.append(["systemsettings", "kcm_bluetooth"])
        if "gnome" in desktop or "unity" in desktop or "ubuntu" in desktop:
            candidates.append(["gnome-control-center", "bluetooth"])
        if "cinnamon" in desktop:
            candidates.append(["cinnamon-settings", "bluetooth"])
        if "budgie" in desktop:
            candidates.append(["budgie-control-center", "bluetooth"])
        if "cosmic" in desktop:
            candidates.append(["cosmic-settings", "bluetooth"])
        candidates += [
            ["gnome-control-center", "bluetooth"],
            ["blueman-manager"],
            ["blueberry"],
            ["overskride"],
            ["systemsettings", "kcm_bluetooth"],
        ]
        for argv in candidates:
            if shutil.which(argv[0]):
                try:
                    Gio.Subprocess.new(argv, Gio.SubprocessFlags.NONE)
                    return
                except GLib.Error as error:
                    log.warning("Could not launch %s: %s", argv[0], error.message)
        if self.window is not None:
            self.window.toast("No Bluetooth settings app found")


def run(argv=None) -> int:
    app = BlueGlanceApplication()
    return app.run(argv if argv is not None else sys.argv)
