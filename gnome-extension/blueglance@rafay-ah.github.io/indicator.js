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
    _init(extensionPath, device, threshold, theme) {
        super._init({style_class: 'blueglance-menu-device'});
        const cls = levelClass(device.level, threshold);
        const ring = new BatteryRing(extensionPath, {size: 34, iconSize: 15, thickness: 0.11});
        ring.update({level: device.level, cls, theme, iconName: kindIconName(device.kind),
            charging: Boolean(device.charging), animate: false});
        this.add_child(ring);

        const text = vbox({x_expand: true, y_align: Clutter.ActorAlign.CENTER, style_class: 'blueglance-menu-text'});
        text.add_child(new St.Label({text: device.name, style_class: 'blueglance-menu-name'}));
        if ((device.components ?? []).length) {
            text.add_child(componentSummary(extensionPath, device,
                `blueglance-menu-status blueglance-components ${theme}`));
        } else {
            text.add_child(new St.Label({text: statusText(device, threshold),
                style_class: `blueglance-menu-status ${theme}`}));
        }
        this.add_child(text);

        this.add_child(new St.Label({
            text: levelText(device),
            style_class: `blueglance-menu-pct level-${cls} ${theme}`,
            y_align: Clutter.ActorAlign.CENTER,
        }));
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
        this.menu.addMenuItem(this._devices);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._widgetSwitch = new PopupMenu.PopupSwitchMenuItem('Desktop Widget', false);
        this._widgetSwitch.connect('toggled', (_item, state) => this._client.setWidgetEnabled(state));
        this.menu.addMenuItem(this._widgetSwitch);
        this.menu.addAction('Open BlueGlance', () => this._client.showWindow());
        this.menu.addAction('Preferences', () => this._client.showPreferences());
    }

    setState(state, theme) {
        const devices = state.devices ?? [];
        const threshold = state.lowThreshold ?? 20;
        this._devices.removeAll();
        for (const device of devices) {
            const item = new DeviceItem(this._path, device, threshold, theme);
            item.connect('activate', () => this._client.showDevice(device.id));
            this._devices.addMenuItem(item);
        }
        if (!devices.length) {
            const off = state.bluetooth && state.bluetooth.available && !state.bluetooth.powered;
            this._devices.addMenuItem(new PopupMenu.PopupMenuItem(
                off ? 'Bluetooth is off' : 'No devices connected', {reactive: false}));
        }
        this._widgetSwitch.setToggleState(Boolean(state.widget?.enabled));

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
