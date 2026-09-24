// Top bar indicator with a battery overview menu.

import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import St from 'gi://St';

import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {BatteryRing, iconFor, kindIconName, levelClass} from './ring.js';
import {componentSummary, levelText, statusText, vbox} from './widget.js';

const DeviceItem = GObject.registerClass(
class DeviceItem extends PopupMenu.PopupBaseMenuItem {
    _init(extensionPath) {
        super._init({style_class: 'blueglance-menu-device'});
        this._path = extensionPath;
        this._ring = new BatteryRing(extensionPath, {size: 34, iconSize: 15, thickness: 0.11});
        this.add_child(this._ring);

        const text = vbox({x_expand: true, y_align: Clutter.ActorAlign.CENTER, style_class: 'blueglance-menu-text'});
        this._name = new St.Label({style_class: 'blueglance-menu-name'});
        this._status = new St.Label();
        this._summary = new St.Bin({x_align: Clutter.ActorAlign.START});
        text.add_child(this._name);
        text.add_child(this._status);
        text.add_child(this._summary);
        this.add_child(text);

        this._pct = new St.Label({y_align: Clutter.ActorAlign.CENTER});
        this.add_child(this._pct);
    }

    update(device, threshold, theme) {
        const cls = levelClass(device.level, threshold);
        this._ring.update({level: device.level, cls, theme, iconName: kindIconName(device.kind),
            charging: Boolean(device.charging), animate: false});
        this._name.text = device.name;
        const hasComponents = device.components.length > 0;
        this._status.text = hasComponents ? '' : statusText(device, threshold);
        this._status.style_class = `blueglance-menu-status ${theme}`;
        this._status.visible = !hasComponents;
        this._summary.child = hasComponents
            ? componentSummary(this._path, device, `blueglance-menu-status blueglance-components ${theme}`) : null;
        this._summary.visible = hasComponents;
        this._pct.text = levelText(device);
        this._pct.style_class = `blueglance-menu-pct level-${cls} ${theme}`;
    }
});

export const BlueGlanceIndicator = GObject.registerClass(
class BlueGlanceIndicator extends PanelMenu.Button {
    _init(extensionPath, client) {
        super._init(0.5, 'BlueGlance', false);
        this._path = extensionPath;
        this._client = client;

        const box = new St.BoxLayout({style_class: 'panel-status-indicators-box'});
        this._icon = new St.Icon({
            gicon: iconFor(extensionPath, 'blueglance-app-symbolic'),
            style_class: 'system-status-icon',
        });
        this._label = new St.Label({
            style_class: 'blueglance-panel-label',
            y_align: Clutter.ActorAlign.CENTER,
            visible: false,
        });
        box.add_child(this._icon);
        box.add_child(this._label);
        this.add_child(box);

        const title = new PopupMenu.PopupMenuItem('Batteries', {reactive: false, style_class: 'blueglance-menu-title'});
        this.menu.addMenuItem(title);
        this._devices = new PopupMenu.PopupMenuSection();
        this._items = new Map();
        this._layout = null;
        this.menu.addMenuItem(this._devices);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._widgetSwitch = new PopupMenu.PopupSwitchMenuItem('Desktop Widget', false);
        this._syncing = false;
        this._widgetSwitch.connect('toggled', () => {
            // Since GNOME 47 setToggleState() emits "toggled" too (and 47.0
            // passes a bogus value), so only forward what the user did.
            if (!this._syncing)
                this._client.setWidgetEnabled(this._widgetSwitch.state);
        });
        this.menu.addMenuItem(this._widgetSwitch);
        this.menu.addAction('Open BlueGlance', () => this._client.showWindow());
        this.menu.addAction('Preferences', () => this._client.showPreferences());
        // A device list that changed while the menu was open is rebuilt on close.
        this.menu.connect('open-state-changed', (_menu, open) => {
            if (!open && this._state)
                this.setState(this._state, this._theme);
        });
    }

    setState(state, theme) {
        this._state = state;
        this._theme = theme;
        const devices = state.devices;
        const threshold = state.lowThreshold;
        const off = state.bluetooth?.available && !state.bluetooth.powered;
        const empty = devices.length ? null : (off ? 'Bluetooth is off' : 'No devices connected');
        const layout = JSON.stringify([devices.map(d => d.id), empty]);
        // Don't pull items out from under the pointer or keyboard focus.
        if (layout !== this._layout && !this.menu.isOpen) {
            this._layout = layout;
            this._devices.removeAll();
            this._items.clear();
            for (const device of devices) {
                const item = new DeviceItem(this._path);
                const id = device.id;
                item.connect('activate', () => this._client.showDevice(id));
                this._items.set(id, item);
                this._devices.addMenuItem(item);
            }
            if (empty)
                this._devices.addMenuItem(new PopupMenu.PopupMenuItem(empty, {reactive: false}));
        }
        for (const device of devices)
            this._items.get(device.id)?.update(device, threshold, theme);

        this._syncing = true;
        try {
            this._widgetSwitch.setToggleState(Boolean(state.widget?.enabled));
        } finally {
            this._syncing = false;
        }

        const withLevel = devices.filter(d => d.level !== null && d.level !== undefined);
        const lowest = withLevel.reduce((a, b) => (a === null || b.level < a.level ? b : a), null);
        const showLabel = Boolean(state.indicator?.showPercentage) && lowest !== null;
        this._label.visible = showLabel;
        this._label.text = showLabel ? `${lowest.level}%` : '';
        const warn = lowest !== null && !lowest.charging && levelClass(lowest.level, threshold) !== 'normal';
        if (warn)
            this._icon.add_style_class_name('blueglance-warning');
        else
            this._icon.remove_style_class_name('blueglance-warning');
    }
});
