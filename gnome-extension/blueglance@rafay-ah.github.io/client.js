// D-Bus client for the BlueGlance app, which owns all the battery logic.
//
// The app exports io.github.rafay_ah.BlueGlance1 with a JSON "state"
// document. While this extension is enabled it owns PRESENCE_NAME so the app
// knows it doesn't need its own widget window and tray icon.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

const BUS_NAME = 'io.github.rafay_ah.BlueGlance';
const OBJECT_PATH = '/io/github/rafay_ah/BlueGlance';
const IFACE = 'io.github.rafay_ah.BlueGlance1';
const PRESENCE_NAME = 'io.github.rafay_ah.BlueGlance.ShellExtension';
export const DESKTOP_ID = 'io.github.rafay_ah.BlueGlance.desktop';

function sanitizeLevel(value) {
    return Number.isFinite(value) ? Math.max(0, Math.min(100, Math.round(value))) : null;
}

function sanitizeDevice(device) {
    return {
        ...device,
        name: String(device.name ?? ''),
        kind: String(device.kind ?? 'other'),
        kindLabel: String(device.kindLabel ?? ''),
        level: sanitizeLevel(device.level),
        charging: Boolean(device.charging),
        components: (Array.isArray(device.components) ? device.components : [])
            .filter(c => c && typeof c === 'object')
            .map(c => ({
                key: String(c.key ?? ''),
                label: String(c.label ?? ''),
                level: sanitizeLevel(c.level),
                charging: Boolean(c.charging),
            })),
    };
}

// Never trust the shape of the JSON: a missing field must not leave the
// widget or the menu half-built.
export function sanitizeState(state) {
    if (!state || typeof state !== 'object')
        return null;
    const devices = Array.isArray(state.devices) ? state.devices : [];
    return {
        ...state,
        devices: devices.filter(d => d && typeof d === 'object' && typeof d.id === 'string').map(sanitizeDevice),
        lowThreshold: Number.isFinite(state.lowThreshold) ? state.lowThreshold : 20,
        widget: state.widget && typeof state.widget === 'object' ? state.widget : {},
        indicator: state.indicator && typeof state.indicator === 'object' ? state.indicator : {},
        bluetooth: state.bluetooth && typeof state.bluetooth === 'object' ? state.bluetooth : {},
    };
}

export class BlueGlanceClient {
    constructor(onState) {
        this._onState = onState;
        this._cancellable = new Gio.Cancellable();
        this._bus = Gio.DBus.session;
        this._signalId = this._bus.signal_subscribe(
            null, IFACE, 'StateChanged', OBJECT_PATH, null, Gio.DBusSignalFlags.NONE,
            (_conn, sender, _path, _iface, _signal, params) => {
                if (sender === this._owner)
                    this._handleState(params.deepUnpack()[0]);
            });
        this._owner = null;
        this._watchId = Gio.bus_watch_name_on_connection(
            this._bus, BUS_NAME, Gio.BusNameWatcherFlags.NONE,
            (_conn, _name, owner) => this._appeared(owner),
            () => this._vanished());
        this._ownId = Gio.bus_own_name_on_connection(
            this._bus, PRESENCE_NAME, Gio.BusNameOwnerFlags.NONE, null, null);
    }

    get running() {
        return this._owner !== null;
    }

    _appeared(owner) {
        this._owner = owner;
        this._call('GetState', null, reply => this._handleState(reply.deepUnpack()[0]));
    }

    _vanished() {
        this._owner = null;
        this._onState(null);
    }

    _handleState(json) {
        let state;
        try {
            state = JSON.parse(json);
        } catch (e) {
            console.warn(`BlueGlance: invalid state from app: ${e.message}`);
            return;
        }
        this._onState(sanitizeState(state));
    }

    _call(method, params, callback = null) {
        if (!this._owner)
            return;
        this._bus.call(BUS_NAME, OBJECT_PATH, IFACE, method, params, null,
            Gio.DBusCallFlags.NO_AUTO_START, 5000, this._cancellable,
            (conn, result) => {
                try {
                    const reply = conn.call_finish(result);
                    callback?.(reply);
                } catch (e) {
                    if (!e.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                        console.warn(`BlueGlance: ${method} failed: ${e.message}`);
                }
            });
    }

    showWindow() {
        if (this._owner)
            this._call('ShowWindow', null);
        else
            this.launchApp();
    }

    showPreferences() {
        this._call('ShowPreferences', null);
    }

    showDevice(id) {
        this._call('ShowDevice', new GLib.Variant('(s)', [id]));
    }

    setWidgetEnabled(enabled) {
        this._call('SetWidgetEnabled', new GLib.Variant('(b)', [enabled]));
    }

    setWidgetSize(size) {
        this._call('SetWidgetSize', new GLib.Variant('(s)', [size]));
    }

    setWidgetPosition(position) {
        this._call('SetShellWidgetPosition', new GLib.Variant('(s)', [JSON.stringify(position)]));
    }

    launchApp() {
        const info = Gio.DesktopAppInfo.new(DESKTOP_ID);
        try {
            info?.launch([], global.create_app_launch_context(0, -1));
        } catch (e) {
            console.warn(`BlueGlance: cannot launch the app: ${e.message}`);
        }
    }

    destroy() {
        this._cancellable.cancel();
        if (this._signalId) {
            this._bus.signal_unsubscribe(this._signalId);
            this._signalId = 0;
        }
        if (this._watchId) {
            Gio.bus_unwatch_name(this._watchId);
            this._watchId = 0;
        }
        if (this._ownId) {
            Gio.bus_unown_name(this._ownId);
            this._ownId = 0;
        }
        this._owner = null;
    }
}
