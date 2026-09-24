// Circular battery gauge for GNOME Shell (St), mirroring the app's GTK ring.

import Cairo from 'gi://cairo';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GObject from 'gi://GObject';
import St from 'gi://St';

const COLORS = {
    dark: {
        normal: [0x30, 0xd1, 0x58],
        low: [0xff, 0x9f, 0x0a],
        critical: [0xff, 0x45, 0x3a],
        track: [1, 1, 1, 0.16],
    },
    light: {
        normal: [0x25, 0xb3, 0x43],
        low: [0xf0, 0x8c, 0x00],
        critical: [0xe5, 0x37, 0x2c],
        track: [0, 0, 0, 0.12],
    },
};
const CRITICAL_LEVEL = 10;

export function levelClass(level, threshold) {
    if (level === null || level === undefined)
        return 'unknown';
    if (level <= Math.min(CRITICAL_LEVEL, threshold))
        return 'critical';
    if (level <= threshold)
        return 'low';
    return 'normal';
}

export function iconFor(extensionPath, name) {
    return Gio.FileIcon.new(Gio.File.new_for_path(`${extensionPath}/icons/${name}.svg`));
}

const KIND_ICONS = {
    earbuds: 'blueglance-earbuds-symbolic',
    headphones: 'blueglance-headphones-symbolic',
    headset: 'blueglance-headset-symbolic',
    speaker: 'blueglance-speaker-symbolic',
    mouse: 'blueglance-mouse-symbolic',
    keyboard: 'blueglance-keyboard-symbolic',
    touchpad: 'blueglance-touchpad-symbolic',
    gamepad: 'blueglance-gamepad-symbolic',
    pen: 'blueglance-pen-symbolic',
    tablet: 'blueglance-tablet-symbolic',
    phone: 'blueglance-phone-symbolic',
    watch: 'blueglance-watch-symbolic',
    remote: 'blueglance-remote-symbolic',
    computer: 'blueglance-laptop-symbolic',
    other: 'blueglance-bluetooth-symbolic',
};

const COMPONENT_ICONS = {
    left: 'blueglance-earbud-left-symbolic',
    right: 'blueglance-earbud-right-symbolic',
    case: 'blueglance-case-symbolic',
};

export function kindIconName(kind) {
    return KIND_ICONS[kind] ?? KIND_ICONS.other;
}

export function componentIconName(key) {
    return COMPONENT_ICONS[key] ?? KIND_ICONS.earbuds;
}

const RingArea = GObject.registerClass({
    Properties: {
        'fraction': GObject.ParamSpec.double('fraction', null, null,
            GObject.ParamFlags.READWRITE, 0, 1, 0),
    },
}, class RingArea extends St.DrawingArea {
    _init(thickness) {
        super._init({x_expand: true, y_expand: true});
        this._thickness = thickness;
        this._fraction = 0;
        this._hasValue = false;
        this._fill = [0.19, 0.82, 0.35, 1];
        this._track = [1, 1, 1, 0.16];
    }

    get fraction() {
        return this._fraction;
    }

    set fraction(value) {
        this._fraction = value;
        this.queue_repaint();
    }

    setColors(fill, track, hasValue) {
        this._fill = fill;
        this._track = track;
        this._hasValue = hasValue;
        this.queue_repaint();
    }

    vfunc_repaint() {
        const cr = this.get_context();
        const [width, height] = this.get_surface_size();
        const size = Math.min(width, height);
        if (size > 2) {
            const line = Math.max(2, size * this._thickness);
            const radius = (size - line) / 2;
            cr.setLineWidth(line);
            cr.setLineCap(Cairo.LineCap.ROUND);
            cr.setSourceRGBA(...this._track);
            cr.arc(width / 2, height / 2, radius, 0, 2 * Math.PI);
            cr.stroke();
            if (this._hasValue && this._fraction > 0.004) {
                const start = -Math.PI / 2;
                cr.setSourceRGBA(...this._fill);
                cr.arc(width / 2, height / 2, radius, start, start + 2 * Math.PI * Math.min(1, this._fraction));
                cr.stroke();
            }
        }
        cr.$dispose();
    }
});

export const BatteryRing = GObject.registerClass(
class BatteryRing extends St.Widget {
    _init(extensionPath, {size = 56, iconSize = null, thickness = 0.1, badge = true} = {}) {
        super._init({
            layout_manager: new Clutter.BinLayout(),
            style_class: 'blueglance-ring',
            style: `width: ${size}px; height: ${size}px;`,
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._path = extensionPath;
        this._area = new RingArea(thickness);
        this.add_child(this._area);

        this._icon = new St.Icon({
            style_class: 'blueglance-ring-icon',
            icon_size: iconSize ?? Math.max(12, Math.round(size * 0.42)),
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
            x_expand: true,
            y_expand: true,
        });
        this.add_child(this._icon);
        this._iconName = null;

        this._badge = null;
        if (badge) {
            const badgeSize = Math.max(12, Math.round(size * 0.34));
            this._badge = new St.Bin({
                style_class: 'blueglance-badge',
                style: `width: ${badgeSize}px; height: ${badgeSize}px; border-radius: ${badgeSize}px;`,
                x_align: Clutter.ActorAlign.END,
                y_align: Clutter.ActorAlign.END,
                x_expand: true,
                y_expand: true,
                visible: false,
                child: new St.Icon({
                    gicon: iconFor(extensionPath, 'blueglance-bolt-symbolic'),
                    icon_size: Math.max(8, Math.round(badgeSize * 0.62)),
                    x_align: Clutter.ActorAlign.CENTER,
                    y_align: Clutter.ActorAlign.CENTER,
                    x_expand: true,
                    y_expand: true,
                }),
            });
            this.add_child(this._badge);
        }
        this._fraction = null;
    }

    update({level = null, cls = 'unknown', theme = 'dark', iconName = null, charging = false, animate = true}) {
        const palette = COLORS[theme] ?? COLORS.dark;
        const rgb = palette[cls] ?? null;
        const fill = rgb ? [rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, 1] : [0, 0, 0, 0];
        this._area.setColors(fill, palette.track, level !== null && level !== undefined);

        const target = level === null || level === undefined ? 0 : Math.max(0, Math.min(1, level / 100));
        this._area.remove_all_transitions();
        if (animate && this._fraction !== target && this.mapped) {
            this._area.ease_property('fraction', target, {
                duration: 650,
                mode: Clutter.AnimationMode.EASE_OUT_CUBIC,
            });
        } else {
            this._area.fraction = target;
        }
        this._fraction = target;

        if (iconName !== this._iconName) {
            this._iconName = iconName;
            this._icon.gicon = iconName ? iconFor(this._path, iconName) : null;
        }
        this._icon.visible = Boolean(iconName);
        if (this._badge) {
            this._badge.visible = charging;
            this._badge.set_style_class_name(`blueglance-badge ${theme}`);
        }
    }
});
