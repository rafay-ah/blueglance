#!/usr/bin/env bash
# Run a throwaway headless GNOME Shell with the BlueGlance extension and the app
# in demo mode, then save a screenshot. Handy for testing the extension without
# logging out, and used to render README screenshots.
#
#   dbus-run-session -- scripts/gnome-shell-headless.sh [out.png] [size] [theme]
#
# size: small|medium|large (default medium); theme: dark|light (default dark)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UUID="blueglance@rafay-ah.github.io"
OUT="$(realpath -m "${1:-$ROOT/shell-shot.png}")"
SIZE="${2:-medium}"
THEME="${3:-dark}"
PYTHON="${PYTHON:-python3}"

WORK="$(mktemp -d)"
export HOME="$WORK/home"
export XDG_RUNTIME_DIR="$WORK/run"
export XDG_CONFIG_HOME="$HOME/.config"
export XDG_DATA_HOME="$HOME/.local/share"
export XDG_CURRENT_DESKTOP="${XDG_CURRENT_DESKTOP:-ubuntu:GNOME}"
mkdir -p "$XDG_RUNTIME_DIR" "$XDG_DATA_HOME/gnome-shell/extensions" "$XDG_CONFIG_HOME/blueglance" \
    "$HOME/Desktop/Projects"
printf 'Remember to charge the mouse\n' > "$HOME/Desktop/notes.txt"  # something for Desktop Icons to show
chmod 700 "$XDG_RUNTIME_DIR"
if [ -z "${NO_BLUEGLANCE_EXTENSION:-}" ]; then
    cp -r "$ROOT/gnome-extension/$UUID" "$XDG_DATA_HOME/gnome-shell/extensions/"
fi

# A soft gradient wallpaper.
WALL="$WORK/wallpaper.png"
"$PYTHON" - "$WALL" <<'EOF'
import sys, cairo
w, h = 1600, 1000
s = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
cr = cairo.Context(s)
g = cairo.LinearGradient(0, 0, w, h)
for off, (r, gg, b) in ((0, (0x1d, 0x2b, 0x53)), (0.5, (0x3b, 0x2a, 0x63)), (1, (0x7a, 0x2c, 0x5a))):
    g.add_color_stop_rgb(off, r / 255, gg / 255, b / 255)
cr.set_source(g); cr.paint()
s.write_to_png(sys.argv[1])
EOF

# Make D-Bus activated services (dconf) see the same HOME as us.
dbus-update-activation-environment HOME XDG_RUNTIME_DIR XDG_CONFIG_HOME XDG_DATA_HOME XDG_CURRENT_DESKTOP \
    2>/dev/null || true

# gnome-shell needs a system bus with logind; give it a private one with mocks.
SYSBUS_ADDR="$(dbus-daemon --session --print-address --fork --print-pid 3 3>"$WORK/sysbus.pid")"
export DBUS_SYSTEM_BUS_ADDRESS="$SYSBUS_ADDR"
"$PYTHON" -m dbusmock --template logind >/dev/null 2>&1 &
MOCK1=$!
"$PYTHON" -m dbusmock --template upower >/dev/null 2>&1 &
MOCK2=$!
sleep 1

EXTENSIONS="'$UUID'"
for extra in ${EXTRA_EXTENSIONS:-}; do EXTENSIONS="$EXTENSIONS, '$extra'"; done
gsettings set org.gnome.shell enabled-extensions "[$EXTENSIONS]"
gsettings set org.gnome.shell disable-user-extensions false
gsettings set org.gnome.desktop.background picture-uri "file://$WALL"
gsettings set org.gnome.desktop.background picture-uri-dark "file://$WALL"
gsettings set org.gnome.desktop.interface color-scheme "$([ "$THEME" = dark ] && echo prefer-dark || echo prefer-light)"

cat > "$XDG_CONFIG_HOME/blueglance/config.json" <<EOF
{"onboarded": true, "autostart": false, "widget_size": "$SIZE", "widget_theme": "$THEME",
 "tray_icon": true, "tray_label": true}
EOF

gnome-shell --headless --virtual-monitor 1600x1000 --wayland --no-x11 --unsafe-mode \
    >"$WORK/shell.log" 2>&1 &
SHELL_PID=$!
cleanup() {
    kill "$APP_PID" 2>/dev/null || true
    kill "$SHELL_PID" 2>/dev/null || true
    kill "$MOCK1" "$MOCK2" 2>/dev/null || true
    kill "$(cat "$WORK/sysbus.pid" 2>/dev/null)" 2>/dev/null || true
    wait 2>/dev/null || true
    rm -rf "$WORK"
}
APP_PID=
trap cleanup EXIT

for _ in $(seq 60); do
    gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell \
        --method org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1 && break
    sleep 0.5
done
sleep 3

shell_eval() {
    gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell \
        --method org.gnome.Shell.Eval "$1"
}
shell_eval "Main.overview.hide(); Main.messageTray._bannerBin?.hide(); true" >/dev/null || true
sleep 1

BLUEGLANCE_DEMO=1 BLUEGLANCE_DEMO_STATIC=1 BLUEGLANCE_DEMO_CONFIG=persist BLUEGLANCE_SCREENSHOT=1 GTK_A11Y=none ADW_DEBUG_COLOR_SCHEME="prefer-$THEME" \
    PYTHONPATH="$ROOT/src" "$PYTHON" -m blueglance "$([ -n "${APP_WINDOW:-}" ] && echo --demo || echo --background)" \
    >"$WORK/app.log" 2>&1 &
APP_PID=$!
sleep 6

gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell \
    --method org.gnome.Shell.Extensions.GetExtensionInfo "$UUID" | tr ',' '\n' | grep -E "'state'|'error'" || true

gdbus call --session --dest org.gnome.Shell.Screenshot --object-path /org/gnome/Shell/Screenshot \
    --method org.gnome.Shell.Screenshot.Screenshot false false "$OUT" >/dev/null
echo "screenshot: $OUT"

# Optional extra steps: JS snippets evaluated in the shell, separated by ";;;".
# A screenshot is taken after each one, e.g. to open the indicator menu.
if [ -n "${EXTRA_EVAL:-}" ]; then
    n=0
    while IFS= read -r -d $'\x1e' snippet; do
        n=$((n + 1))
        echo "eval $n: $(shell_eval "$snippet" 2>&1)"
        sleep "${EXTRA_SLEEP:-1.5}"
        gdbus call --session --dest org.gnome.Shell.Screenshot --object-path /org/gnome/Shell/Screenshot \
            --method org.gnome.Shell.Screenshot.Screenshot false false "${OUT%.png}-$n.png" >/dev/null
        echo "screenshot: ${OUT%.png}-$n.png"
    done < <(printf '%s\x1e' "${EXTRA_EVAL//;;;/$'\x1e'}")
fi
if [ -n "${SHOW_CONFIG:-}" ]; then
    echo "--- app config"; cat "$XDG_CONFIG_HOME/blueglance/config.json"; echo
fi
if [ -n "${SHOW_LOGS:-}" ]; then
    echo "--- shell log"; grep -iE "blueglance|error|warn" "$WORK/shell.log" | grep -v "fd limit" | tail -40 || true
    echo "--- app log"; tail -20 "$WORK/app.log" || true
fi
