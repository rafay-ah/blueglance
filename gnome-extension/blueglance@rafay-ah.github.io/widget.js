// The desktop widget: sits above the wallpaper (and above Desktop Icons),
// below every normal window, on all workspaces. Drag to move, click to open
// BlueGlance, right-click for options.

import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Meta from 'gi://Meta';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {BatteryRing, componentIconName, iconFor, kindIconName, levelClass} from './ring.js';

const SLOTS = {small: 4, medium: 4, large: 6};
const DRAG_THRESHOLD = 6;
const DEFAULT_MARGIN = [40, 28];
const SHORT_COMPONENT = {left: 'L', right: 'R', case: 'Case'};

export function vbox(params = {}) {
    const box = new St.BoxLayout(params);
    // Portable across GNOME 45-51 (St.BoxLayout:vertical was removed in 51,
    // :orientation only exists since 48).
    box.layout_manager.orientation = Clutter.Orientation.VERTICAL;
    return box;
}

export function levelText(device) {
    return device.level === null || device.level === undefined ? '—' : `${device.level}%`;
}

export function statusText(device, threshold) {
    if (device.state === 'full')
        return 'Fully charged';
    if (device.charging)
        return 'Charging';
    const cls = levelClass(device.level, threshold);
    if (cls === 'critical')
        return 'Critically low';
    if (cls === 'low')
        return 'Low battery';
    return device.kindLabel ?? '';
}

export function componentSummary(extensionPath, device, styleClass) {
    const box = new St.BoxLayout({style_class: styleClass});
    for (const comp of device.components ?? []) {
        const part = new St.BoxLayout({style_class: 'blueglance-component'});
        part.add_child(new St.Icon({gicon: iconFor(extensionPath, componentIconName(comp.key)), icon_size: 12,
            y_align: Clutter.ActorAlign.CENTER}));
        part.add_child(new St.Label({
            text: comp.level === null || comp.level === undefined ? '—' : `${comp.level}%`,
            y_align: Clutter.ActorAlign.CENTER,
            accessible_name: `${SHORT_COMPONENT[comp.key] ?? comp.label}`,
        }));
        if (comp.charging) {
            part.add_child(new St.Icon({gicon: iconFor(extensionPath, 'blueglance-bolt-symbolic'), icon_size: 10,
                style_class: 'blueglance-bolt', y_align: Clutter.ActorAlign.CENTER}));
        }
        box.add_child(part);
    }
    return box;
}

export const DesktopWidget = GObject.registerClass(
class DesktopWidget extends St.BoxLayout {
    _init(extensionPath, client) {
        super._init({
            reactive: true,
            track_hover: true,
            style_class: 'blueglance-widget medium dark',
        });
        this.layout_manager.orientation = Clutter.Orientation.VERTICAL;
        this._path = extensionPath;
        this._client = client;
        this._size = 'medium';
        this._theme = 'dark';
        this._threshold = 20;
        this._signature = null;
        this._slots = new Map();
        this._position = null;
        this._placed = false;
        this._press = null;
        this._grab = null;
        this._timeouts = new Set();

        this._buildMenu();

        global.window_group.add_child(this);
        this._signals = [
            [global.display, global.display.connect('restacked', () => this._ensureStacking())],
            [global.display, global.display.connect('window-created', () => this._queueStackCheck())],
            [global.workspace_manager,
                global.workspace_manager.connect('active-workspace-changed', () => this._queueStackCheck(450))],
            [Main.layoutManager, Main.layoutManager.connect('monitors-changed', () => this._restorePosition())],
        ];
        this._ensureStacking();
        this.connect('destroy', () => this._onDestroy());
    }

    // ---- stacking ---------------------------------------------------------
    _ensureStacking() {
        const group = global.window_group;
        if (this.get_parent() !== group)
            return;
        // Sit right above the wallpaper and any desktop-icons window (DING),
        // so the widget stays clickable, but below every normal window.
        let anchor = Main.layoutManager._backgroundGroup;
        for (const actor of global.get_window_actors()) {
            const win = actor.meta_window;
            if (actor.get_parent() === group && win && win.get_window_type() === Meta.WindowType.DESKTOP)
                anchor = actor;
        }
        if (anchor && anchor.get_parent() === group) {
            if (this.get_previous_sibling() !== anchor)
                group.set_child_above_sibling(this, anchor);
        } else if (group.get_first_child() !== this) {
            group.set_child_below_sibling(this, null);
        }
    }

    _queueStackCheck(delay = 0) {
        const id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, delay, () => {
            this._timeouts.delete(id);
            this._ensureStacking();
            return GLib.SOURCE_REMOVE;
        });
        this._timeouts.add(id);
    }

    // ---- menu ---------------------------------------------------------------
    _buildMenu() {
        this._menuManager = new PopupMenu.PopupMenuManager(this);
        this._menu = new PopupMenu.PopupMenu(this, 0.5, St.Side.TOP);
        this._menu.actor.add_style_class_name('blueglance-widget-menu');
        Main.uiGroup.add_child(this._menu.actor);
        this._menu.actor.hide();
        this._menuManager.addMenu(this._menu);

        this._menu.addAction('Open BlueGlance', () => this._client.showWindow());
        this._menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem('Size'));
        this._sizeItems = {};
        for (const [size, label] of [['small', 'Small'], ['medium', 'Medium'], ['large', 'Large']]) {
            const item = this._menu.addAction(label, () => this._client.setWidgetSize(size));
            this._sizeItems[size] = item;
        }
        this._menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._menu.addAction('Preferences…', () => this._client.showPreferences());
        this._menu.addAction('Hide Widget', () => this._client.setWidgetEnabled(false));
    }

    _syncMenu() {
        for (const [size, item] of Object.entries(this._sizeItems))
            item.setOrnament(size === this._size ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NONE);
    }

    // ---- content ------------------------------------------------------------
    setState(state, theme) {
        const size = ['small', 'medium', 'large'].includes(state.widget?.size) ? state.widget.size : 'medium';
        const devices = state.devices ?? [];
        const shown = devices.slice(0, SLOTS[size]);
        let message = null;
        if (!devices.length && state.bluetooth && state.bluetooth.available && !state.bluetooth.powered)
            message = 'Bluetooth is off';

        this._threshold = state.lowThreshold ?? 20;
        this._theme = theme;
        this._size = size;
        this.set_style_class_name(`blueglance-widget ${size} ${theme}`);
        this._syncMenu();

        const signature = JSON.stringify([size, shown.map(d => d.id), shown.length ? null : message]);
        let animate = true;
        if (signature !== this._signature) {
            this._rebuild(size, shown, message);
            this._signature = signature;
        }
        for (const device of shown)
            this._updateSlot(this._slots.get(device.id), device, animate);

        const position = state.widget?.position ?? null;
        if (!this._placed || JSON.stringify(position) !== JSON.stringify(this._position)) {
            this._position = position;
            this._placed = true;
            // Wait for the new layout to be allocated before clamping to the monitor.
            this._queueRestore();
        }
    }

    _queueRestore() {
        const id = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._timeouts.delete(id);
            this._restorePosition();
            return GLib.SOURCE_REMOVE;
        });
        this._timeouts.add(id);
    }

    _rebuild(size, shown, message) {
        this.destroy_all_children();
        this._slots.clear();
        if (size === 'small')
            this._buildSmall(shown);
        else if (size === 'medium')
            this._buildMedium(shown);
        else
            this._buildLarge(shown, message);
    }

    _ring(size, iconSize, thickness = 0.1) {
        return new BatteryRing(this._path, {size, iconSize, thickness});
    }

    _placeholder(ring) {
        ring.update({level: null, cls: 'unknown', theme: this._theme, iconName: null, animate: false});
        ring.add_style_class_name('placeholder');
    }

    _buildSmall(shown) {
        if (shown.length === 1) {
            const box = vbox({style_class: 'blueglance-single', x_expand: true, y_expand: true,
                y_align: Clutter.ActorAlign.CENTER});
            const ring = this._ring(84, 34, 0.095);
            const pct = new St.Label({style_class: 'blueglance-pct large', x_align: Clutter.ActorAlign.CENTER});
            const name = new St.Label({style_class: 'blueglance-name small', x_align: Clutter.ActorAlign.CENTER});
            box.add_child(ring);
            box.add_child(pct);
            box.add_child(name);
            this.add_child(box);
            this._slots.set(shown[0].id, {ring, pct, name});
            return;
        }
        const grid = vbox({style_class: 'blueglance-grid', x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER, y_expand: true});
        for (let row = 0; row < 2; row++) {
            const line = new St.BoxLayout({style_class: 'blueglance-grid-row'});
            for (let col = 0; col < 2; col++) {
                const index = row * 2 + col;
                const ring = this._ring(58, 24);
                line.add_child(ring);
                if (index < shown.length)
                    this._slots.set(shown[index].id, {ring});
                else
                    this._placeholder(ring);
            }
            grid.add_child(line);
        }
        this.add_child(grid);
    }

    _buildMedium(shown) {
        const row = new St.BoxLayout({style_class: 'blueglance-columns', x_expand: true, y_expand: true,
            y_align: Clutter.ActorAlign.CENTER});
        for (let index = 0; index < 4; index++) {
            const column = vbox({style_class: 'blueglance-column', x_expand: true});
            const ring = this._ring(62, 26);
            const pct = new St.Label({style_class: 'blueglance-pct', x_align: Clutter.ActorAlign.CENTER, text: ' '});
            column.add_child(ring);
            column.add_child(pct);
            row.add_child(column);
            if (index < shown.length)
                this._slots.set(shown[index].id, {ring, pct});
            else
                this._placeholder(ring);
        }
        this.add_child(row);
    }

    _buildLarge(shown, message) {
        const header = new St.BoxLayout({style_class: 'blueglance-header'});
        header.add_child(new St.Icon({gicon: iconFor(this._path, 'blueglance-bluetooth-symbolic'), icon_size: 14,
            style_class: 'blueglance-header-icon', y_align: Clutter.ActorAlign.CENTER}));
        header.add_child(new St.Label({text: 'Batteries', style_class: 'blueglance-header-title',
            y_align: Clutter.ActorAlign.CENTER}));
        this.add_child(header);

        if (!shown.length) {
            const empty = vbox({style_class: 'blueglance-empty', x_expand: true, y_expand: true,
                y_align: Clutter.ActorAlign.CENTER});
            empty.add_child(new St.Icon({gicon: iconFor(this._path, 'blueglance-bluetooth-symbolic'), icon_size: 40,
                style_class: 'blueglance-empty-icon', x_align: Clutter.ActorAlign.CENTER}));
            empty.add_child(new St.Label({text: message ?? 'No devices connected',
                style_class: 'blueglance-empty-label', x_align: Clutter.ActorAlign.CENTER}));
            this.add_child(empty);
            return;
        }
        const rows = vbox({style_class: 'blueglance-rows'});
        for (const device of shown) {
            const row = new St.BoxLayout({style_class: 'blueglance-row'});
            const ring = this._ring(40, 18);
            const text = vbox({x_expand: true, y_align: Clutter.ActorAlign.CENTER, style_class: 'blueglance-row-text'});
            const name = new St.Label({style_class: 'blueglance-name'});
            const status = new St.Label({style_class: 'blueglance-status'});
            const summary = new St.Bin({x_align: Clutter.ActorAlign.START});
            text.add_child(name);
            text.add_child(status);
            text.add_child(summary);
            const pct = new St.Label({style_class: 'blueglance-pct', y_align: Clutter.ActorAlign.CENTER});
            row.add_child(ring);
            row.add_child(text);
            row.add_child(pct);
            rows.add_child(row);
            this._slots.set(device.id, {ring, pct, name, status, summary});
        }
        this.add_child(rows);
    }

    _updateSlot(slot, device, animate) {
        if (!slot)
            return;
        const cls = levelClass(device.level, this._threshold);
        slot.ring.update({
            level: device.level, cls, theme: this._theme, iconName: kindIconName(device.kind),
            charging: Boolean(device.charging), animate,
        });
        if (slot.pct) {
            slot.pct.text = levelText(device);
            slot.pct.set_style_class_name(`blueglance-pct level-${cls}${slot.name && !slot.status ? ' large' : ''}`);
        }
        if (slot.name)
            slot.name.text = device.name;
        if (slot.status) {
            const hasComponents = (device.components ?? []).length > 0;
            slot.status.text = statusText(device, this._threshold);
            slot.status.visible = !hasComponents;
            slot.summary.child = hasComponents
                ? componentSummary(this._path, device, 'blueglance-status blueglance-components') : null;
            slot.summary.visible = hasComponents;
        }
    }

    // ---- position -----------------------------------------------------------
    _monitorIndexAt(x, y) {
        const monitors = Main.layoutManager.monitors;
        for (let i = 0; i < monitors.length; i++) {
            const m = monitors[i];
            if (x >= m.x && x < m.x + m.width && y >= m.y && y < m.y + m.height)
                return i;
        }
        return Main.layoutManager.primaryIndex;
    }

    _restorePosition() {
        const monitors = Main.layoutManager.monitors;
        if (!monitors.length)
            return;
        const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
        const [, width] = this.get_preferred_width(-1);
        const [, height] = this.get_preferred_height(width);
        const pos = this._position;
        let index = Main.layoutManager.primaryIndex;
        let x, y;
        if (pos && Number.isInteger(pos.monitor) && pos.monitor >= 0 && pos.monitor < monitors.length &&
            Number.isFinite(pos.x) && Number.isFinite(pos.y)) {
            index = pos.monitor;
            x = monitors[index].x + pos.x;
            y = monitors[index].y + pos.y;
        } else {
            const area = Main.layoutManager.getWorkAreaForMonitor(index);
            x = area.x + area.width - width - DEFAULT_MARGIN[0] * scale;
            y = area.y + DEFAULT_MARGIN[1] * scale;
        }
        const m = monitors[index];
        x = Math.max(m.x, Math.min(x, m.x + m.width - width));
        y = Math.max(m.y, Math.min(y, m.y + m.height - height));
        this.set_position(Math.round(x), Math.round(y));
    }

    _savePosition() {
        const [cx, cy] = [this.x + this.width / 2, this.y + this.height / 2];
        const index = this._monitorIndexAt(cx, cy);
        const m = Main.layoutManager.monitors[index];
        this._position = {monitor: index, x: Math.round(this.x - m.x), y: Math.round(this.y - m.y)};
        this._client.setWidgetPosition(this._position);
    }

    // ---- input --------------------------------------------------------------
    vfunc_button_press_event(event) {
        const button = event.get_button();
        if (button === Clutter.BUTTON_SECONDARY) {
            this._menu.toggle();
            return Clutter.EVENT_STOP;
        }
        if (button !== Clutter.BUTTON_PRIMARY)
            return Clutter.EVENT_PROPAGATE;
        const [x, y] = event.get_coords();
        this._press = {x, y, ax: this.x, ay: this.y, moved: false};
        this._grab = global.stage.grab(this);
        return Clutter.EVENT_STOP;
    }

    vfunc_motion_event(event) {
        if (!this._press)
            return Clutter.EVENT_PROPAGATE;
        const [x, y] = event.get_coords();
        const dx = x - this._press.x;
        const dy = y - this._press.y;
        if (!this._press.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD)
            return Clutter.EVENT_STOP;
        if (!this._press.moved) {
            this._press.moved = true;
            this.add_style_pseudo_class('dragging');
        }
        this.set_position(Math.round(this._press.ax + dx), Math.round(this._press.ay + dy));
        return Clutter.EVENT_STOP;
    }

    vfunc_button_release_event(event) {
        if (!this._press || event.get_button() !== Clutter.BUTTON_PRIMARY)
            return Clutter.EVENT_PROPAGATE;
        const moved = this._press.moved;
        this._endDrag();
        if (moved) {
            this._restoreClamp();
            this._savePosition();
        } else {
            this._client.showWindow();
        }
        return Clutter.EVENT_STOP;
    }

    _restoreClamp() {
        const index = this._monitorIndexAt(this.x + this.width / 2, this.y + this.height / 2);
        const m = Main.layoutManager.monitors[index];
        const x = Math.max(m.x, Math.min(this.x, m.x + m.width - this.width));
        const y = Math.max(m.y, Math.min(this.y, m.y + m.height - this.height));
        this.set_position(Math.round(x), Math.round(y));
    }

    _endDrag() {
        this._press = null;
        this.remove_style_pseudo_class('dragging');
        if (this._grab) {
            this._grab.dismiss();
            this._grab = null;
        }
    }

    _onDestroy() {
        this._endDrag();
        for (const id of this._timeouts)
            GLib.source_remove(id);
        this._timeouts.clear();
        for (const [obj, id] of this._signals)
            obj.disconnect(id);
        this._signals = [];
        this._menu?.destroy();
        this._menu = null;
    }
});
