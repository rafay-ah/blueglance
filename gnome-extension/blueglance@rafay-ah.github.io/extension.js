// BlueGlance for GNOME Shell: a pinned desktop widget and a top bar menu with
// the battery levels of your Bluetooth devices. All device logic lives in the
// BlueGlance app; this extension only renders the state it publishes on D-Bus.

import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {BlueGlanceClient} from './client.js';
import {BlueGlanceIndicator} from './indicator.js';
import {DesktopWidget} from './widget.js';

export default class BlueGlanceExtension extends Extension {
    enable() {
        this._state = null;
        this._widget = null;
        this._indicator = null;
        this._handlers = [];
        this._client = new BlueGlanceClient(state => {
            this._state = state;
            this._sync();
        });

        const settings = St.Settings.get();
        this._handlers.push([settings, settings.connect('notify::color-scheme', () => this._sync())]);
        this._handlers.push([Main.sessionMode, Main.sessionMode.connect('updated', () => this._sync())]);
        if (Main.layoutManager._startingUp) {
            this._handlers.push([Main.layoutManager,
                Main.layoutManager.connect('startup-complete', () => this._sync())]);
        }
    }

    disable() {
        for (const [obj, id] of this._handlers)
            obj.disconnect(id);
        this._handlers = [];
        this._client?.destroy();
        this._client = null;
        this._destroyWidget();
        this._destroyIndicator();
        this._state = null;
    }

    _widgetTheme(state) {
        const theme = state.widget?.theme ?? 'auto';
        if (theme === 'light' || theme === 'dark')
            return theme;
        return St.Settings.get().color_scheme === St.SystemColorScheme.PREFER_DARK ? 'dark' : 'light';
    }

    _menuTheme() {
        return Main.getStyleVariant?.() === 'light' ? 'light' : 'dark';
    }

    _sync() {
        const state = this._state;
        const usable = state && state.ready && !Main.sessionMode.isLocked && !Main.layoutManager._startingUp;

        if (usable && state.widget?.enabled) {
            this._widget ??= new DesktopWidget(this.path, this._client);
            this._widget.setState(state, this._widgetTheme(state));
        } else {
            this._destroyWidget();
        }

        if (usable && state.indicator?.enabled !== false) {
            if (!this._indicator) {
                this._indicator = new BlueGlanceIndicator(this.path, this._client);
                Main.panel.addToStatusArea(this.uuid, this._indicator);
            }
            this._indicator.setState(state, this._menuTheme());
        } else {
            this._destroyIndicator();
        }
    }

    _destroyWidget() {
        this._widget?.destroy();
        this._widget = null;
    }

    _destroyIndicator() {
        this._indicator?.destroy();
        this._indicator = null;
    }
}
